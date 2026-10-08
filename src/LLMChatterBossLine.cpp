/*
 * mod-llm-chatter - boss line reactions
 *
 * Owns:
 *   - LLMChatterBossLineServerScript (captures creature say and
 *     yell packets sent to real players)
 *   - ProcessCapturedBossLines() (world thread)
 *   - bot_group_boss_line events
 *
 * AzerothCore has no script hook for creature speech, so the
 * module watches the SMSG_MESSAGECHAT packets the server sends
 * to real players. This covers scripted boss lines and the
 * module's own boss dialogue alike: a group bot reacts only to
 * a line its real player actually received.
 *
 * CanPacketSend runs on whichever thread sends the packet
 * (often a map worker), so the hook only parses and records the
 * line. Every game-object lookup happens later on the world
 * thread. The hook never blocks, alters or delays a packet.
 */

#include "LLMChatterBossLine.h"

#include "LLMChatterBossLineParse.h"
#include "LLMChatterConfig.h"
#include "LLMChatterGroupInternal.h"
#include "LLMChatterShared.h"

#include "Creature.h"
#include "Group.h"
#include "ObjectAccessor.h"
#include "Opcodes.h"
#include "Player.h"
#include "ScriptMgr.h"
#include "WorldPacket.h"
#include "WorldSession.h"

#include <ctime>
#include <mutex>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

namespace
{

// Lines waiting for the world thread. A boss rarely says more
// than one line a second, so the cap only matters if the world
// thread stalls; overflow drops lines rather than growing.
constexpr size_t kMaxPendingLines = 64;
// One line reaches every real player nearby; copies of the same
// line for the same group within this window are one moment.
constexpr time_t kDuplicateWindowSeconds = 10;
// A reaction to a line more than this old would be out of place.
constexpr uint32 kEventExpirySeconds = 20;
constexpr size_t kMaxLineChars = 255;
constexpr size_t kMaxBossNameChars = 64;
// Creature /say carries about 25 yards; allow some slack.
constexpr float kSayHearingYards = 40.0f;

struct CapturedLine
{
    ObjectGuid listener;
    ObjectGuid speaker;
    uint8 type{0};
    std::string text;
};

std::mutex _captureMutex;
std::vector<CapturedLine> _pendingLines;

// World thread only.
std::unordered_map<uint32, time_t> _groupCooldowns;
std::unordered_map<std::string, time_t> _recentLines;

bool IsBossLineChatterEnabled()
{
    return sLLMChatterConfig
        && sLLMChatterConfig->IsEnabled()
        && sLLMChatterConfig->_useGroupChatter
        && sLLMChatterConfig->_bossLineChatterEnable;
}

void PruneHistory(time_t now)
{
    time_t cooldown = static_cast<time_t>(
        sLLMChatterConfig->_bossLineCooldown);
    for (auto it = _groupCooldowns.begin();
         it != _groupCooldowns.end(); )
    {
        if (now - it->second >= cooldown)
            it = _groupCooldowns.erase(it);
        else
            ++it;
    }
    for (auto it = _recentLines.begin();
         it != _recentLines.end(); )
    {
        if (now - it->second >= kDuplicateWindowSeconds)
            it = _recentLines.erase(it);
        else
            ++it;
    }
}

// True the first time a group hears this line in the window.
bool TryRecordLine(
    uint32 groupId, CapturedLine const& line, time_t now)
{
    std::string key = std::to_string(groupId) + ":"
        + std::to_string(line.speaker.GetRawValue()) + ":"
        + line.text;
    auto it = _recentLines.find(key);
    if (it != _recentLines.end()
        && now - it->second < kDuplicateWindowSeconds)
        return false;
    _recentLines[key] = now;
    return true;
}

bool TryConsumeGroupCooldown(uint32 groupId, time_t now)
{
    auto it = _groupCooldowns.find(groupId);
    if (it != _groupCooldowns.end()
        && now - it->second < static_cast<time_t>(
            sLLMChatterConfig->_bossLineCooldown))
        return false;
    _groupCooldowns[groupId] = now;
    return true;
}

// A living group bot that heard the line: anywhere in the same
// instance for a yell, near the player for a /say.
Player* SelectBossLineReactor(
    Group* group, Player* listener, bool isYell)
{
    std::vector<Player*> candidates;
    for (GroupReference* itr = group->GetFirstMember();
         itr != nullptr; itr = itr->next())
    {
        Player* member = itr->GetSource();
        if (!member || !IsPlayerBot(member)
            || !member->IsInWorld() || !member->IsAlive()
            || member->GetMap() != listener->GetMap())
            continue;
        if (!isYell && !member->IsWithinDistInMap(
                listener, kSayHearingYards))
            continue;
        candidates.push_back(member);
    }
    if (candidates.empty())
        return nullptr;
    return candidates[urand(0, candidates.size() - 1)];
}

// Control characters would break the JSON payload; scripted
// lines never need them.
std::string CleanLine(std::string const& text, size_t maxChars)
{
    std::string cleaned = NormalizeChatTextForDb(text, maxChars);
    for (char& c : cleaned)
    {
        if (static_cast<unsigned char>(c) < 0x20)
            c = ' ';
    }
    return cleaned;
}

void QueueBossLineEvent(
    Player* listener, Group* group, Creature* boss,
    Player* reactor, CapturedLine const& line)
{
    bool isYell =
        line.type == LLMChatterBossLine::kChatMsgMonsterYell;
    bool replyByYell =
        urand(1, 100) <= sLLMChatterConfig->_bossLineYellChance;
    uint32 groupId = group->GetGUID().GetCounter();
    std::string bossName =
        CleanLine(boss->GetName(), kMaxBossNameChars);

    std::string extraData = "{"
        + BuildBotIdentityFields(reactor) + ","
        "\"group_id\":" + std::to_string(groupId) + ","
        "\"boss_name\":\"" + JsonEscape(bossName) + "\","
        "\"boss_entry\":"
            + std::to_string(boss->GetEntry()) + ","
        "\"boss_line\":\""
            + JsonEscape(CleanLine(line.text, kMaxLineChars))
            + "\","
        "\"line_type\":\""
            + std::string(isYell ? "yell" : "say") + "\","
        "\"boss_alive\":"
            + std::string(boss->IsAlive() ? "true" : "false")
            + ","
        "\"boss_in_combat\":"
            + std::string(
                boss->IsInCombat() ? "true" : "false")
            + ","
        "\"boss_health_pct\":"
            + std::to_string(static_cast<uint32>(
                boss->GetHealthPct() + 0.5f))
            + ","
        "\"listener_name\":\""
            + JsonEscape(listener->GetName()) + "\","
        "\"reply_channel\":\""
            + std::string(replyByYell ? "yell" : "party")
            + "\","
        + BuildBotStateJson(reactor) + "}";

    extraData = EscapeString(extraData);

    QueueChatterEvent(
        "bot_group_boss_line",
        "player",
        reactor->GetZoneId(),
        reactor->GetMapId(),
        GetChatterEventPriority("bot_group_boss_line"),
        "",
        reactor->GetGUID().GetCounter(),
        reactor->GetName(),
        0,
        bossName,
        boss->GetEntry(),
        extraData,
        GetReactionDelaySeconds("bot_group_boss_line"),
        kEventExpirySeconds,
        false
    );
}

void HandleCapturedLine(CapturedLine const& line, time_t now)
{
    Player* listener = ObjectAccessor::FindPlayer(line.listener);
    if (!listener || !listener->IsInWorld()
        || IsPlayerBot(listener)
        || listener->InBattleground() || listener->InArena())
        return;

    Group* group = listener->GetGroup();
    if (!group || !GroupHasRealPlayer(group))
        return;

    Creature* boss =
        ObjectAccessor::GetCreature(*listener, line.speaker);
    if (!boss || !IsLLMChatterBoss(boss)
        || IsLLMChatterInternalCreature(boss))
        return;

    uint32 groupId = group->GetGUID().GetCounter();
    // Record before rolling, so the copy of this line sent to a
    // second real player is not a second chance.
    if (!TryRecordLine(groupId, line, now))
        return;
    if (urand(1, 100) > sLLMChatterConfig->_bossLineChance)
        return;

    bool isYell =
        line.type == LLMChatterBossLine::kChatMsgMonsterYell;
    Player* reactor =
        SelectBossLineReactor(group, listener, isYell);
    if (!reactor || !TryConsumeGroupCooldown(groupId, now))
        return;

    QueueBossLineEvent(listener, group, boss, reactor, line);
}

} // namespace

void ProcessCapturedBossLines()
{
    std::vector<CapturedLine> lines;
    {
        std::lock_guard<std::mutex> lock(_captureMutex);
        if (_pendingLines.empty())
            return;
        lines.swap(_pendingLines);
    }

    if (!IsBossLineChatterEnabled())
        return;

    time_t now = time(nullptr);
    PruneHistory(now);
    for (CapturedLine const& line : lines)
        HandleCapturedLine(line, now);
}

class LLMChatterBossLineServerScript : public ServerScript
{
public:
    LLMChatterBossLineServerScript()
        : ServerScript(
              "LLMChatterBossLineServerScript",
              {SERVERHOOK_CAN_PACKET_SEND}) {}

    // Observation only: always lets the packet through.
    bool CanPacketSend(
        WorldSession* session, WorldPacket const& packet) override
    {
        if (packet.GetOpcode() != SMSG_MESSAGECHAT
            || packet.empty() || !session
            || !IsBossLineChatterEnabled())
            return true;

        // Cheap type check before parsing anything else.
        uint8 type = packet.contents()[0];
        if (type != LLMChatterBossLine::kChatMsgMonsterSay
            && type != LLMChatterBossLine::kChatMsgMonsterYell)
            return true;

        Player* listener = session->GetPlayer();
        if (!listener)
            return true;

        LLMChatterBossLine::MonsterChat chat;
        if (!LLMChatterBossLine::ParseMonsterChat(
                packet.contents(), packet.size(), chat))
            return true;

        CapturedLine line;
        line.listener = listener->GetGUID();
        line.speaker = ObjectGuid(chat.senderGuid);
        line.type = chat.type;
        line.text = std::move(chat.text);

        std::lock_guard<std::mutex> lock(_captureMutex);
        if (_pendingLines.size() < kMaxPendingLines)
            _pendingLines.push_back(std::move(line));
        return true;
    }
};

void AddLLMChatterBossLineScripts()
{
    new LLMChatterBossLineServerScript();
}
