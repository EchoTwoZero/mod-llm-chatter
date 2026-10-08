// Standalone test for src/LLMChatterBossLineParse.h (standard
// library only; not part of the server build).
//
// Built and run by tools/tests/test_boss_line_parse_cpp.py when
// a C++ compiler is available:
//   c++ -std=c++17 -I src tools/tests/cpp/test_boss_line_parse.cpp
//
// Packets are assembled the way ChatHandler::BuildChatPacket()
// writes SMSG_MESSAGECHAT for creature speech.

#include "LLMChatterBossLineParse.h"

#include <cstdint>
#include <cstdio>
#include <string>
#include <vector>

namespace
{

using LLMChatterBossLine::MonsterChat;
using LLMChatterBossLine::ParseMonsterChat;

int failures = 0;

void Check(bool condition, char const* what)
{
    if (!condition)
    {
        std::printf("FAIL: %s\n", what);
        ++failures;
    }
}

constexpr std::uint64_t kBossGuid =
    (std::uint64_t{0xF130} << 48) | (std::uint64_t{639} << 24) | 77;
constexpr std::uint64_t kVehicleGuid =
    (std::uint64_t{0xF150} << 48) | 12;
constexpr std::uint64_t kPlayerGuid = 42;
constexpr std::uint64_t kPetGuid = (std::uint64_t{0xF140} << 48) | 5;

struct Writer
{
    std::vector<std::uint8_t> bytes;

    template <typename T>
    Writer& Int(T value)
    {
        for (std::size_t i = 0; i < sizeof(T); ++i)
            bytes.push_back(
                static_cast<std::uint8_t>(value >> (8 * i)));
        return *this;
    }

    Writer& Str(std::string const& value)
    {
        Int<std::uint32_t>(
            static_cast<std::uint32_t>(value.size() + 1));
        bytes.insert(bytes.end(), value.begin(), value.end());
        bytes.push_back(0);
        return *this;
    }
};

std::vector<std::uint8_t> Build(
    std::uint8_t type, std::uint64_t sender,
    std::string const& name, std::uint64_t receiver,
    std::string const& receiverName, std::string const& text)
{
    Writer w;
    w.Int<std::uint8_t>(type)
        .Int<std::int32_t>(0)
        .Int<std::uint64_t>(sender)
        .Int<std::uint32_t>(0)
        .Str(name)
        .Int<std::uint64_t>(receiver);
    bool namedReceiver = receiver
        && !LLMChatterBossLine::IsPlayerOrPetGuid(receiver);
    if (namedReceiver)
        w.Str(receiverName);
    w.Str(text).Int<std::uint8_t>(0);
    return w.bytes;
}

bool Parse(std::vector<std::uint8_t> const& packet, MonsterChat& out)
{
    return ParseMonsterChat(packet.data(), packet.size(), out);
}

} // namespace

int main()
{
    using namespace LLMChatterBossLine;

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kBossGuid,
            "Edwin VanCleef", 0, "",
            "None may challenge the Brotherhood!");
        Check(Parse(packet, out), "yell without receiver parses");
        Check(out.type == kChatMsgMonsterYell, "yell type kept");
        Check(out.senderGuid == kBossGuid, "sender guid kept");
        Check(out.senderName == "Edwin VanCleef", "sender name");
        Check(out.text == "None may challenge the Brotherhood!",
            "yell text");
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterSay, kBossGuid,
            "Hogger", kPlayerGuid, "ignored", "Grrr... fresh meat!");
        Check(Parse(packet, out), "say aimed at a player parses");
        Check(out.text == "Grrr... fresh meat!", "say text");
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kBossGuid,
            "Hogger", kPetGuid, "ignored", "Your pet is next!");
        Check(Parse(packet, out), "pet receiver carries no name");
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kVehicleGuid,
            "Ignis", kBossGuid, "Iron Construct",
            "Let the inferno consume you!");
        Check(Parse(packet, out), "named creature receiver parses");
        Check(out.text == "Let the inferno consume you!",
            "text after receiver name");
    }

    {
        MonsterChat out;
        out.text = "untouched";
        auto packet = Build(0x01, kBossGuid, "Hogger", 0, "",
            "not monster chat");
        Check(!Parse(packet, out), "player chat type rejected");
        Check(out.text == "untouched", "rejection leaves output");
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kPlayerGuid,
            "Someone", 0, "", "Players are not creatures");
        Check(!Parse(packet, out), "player sender rejected");
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kBossGuid,
            "Hogger", 0, "", "");
        Check(!Parse(packet, out), "empty text rejected");
    }

    {
        auto packet = Build(kChatMsgMonsterYell, kBossGuid,
            "Edwin VanCleef", 0, "", "Truncate me");
        for (std::size_t cut = 0; cut < packet.size(); ++cut)
        {
            MonsterChat out;
            std::vector<std::uint8_t> partial(
                packet.begin(), packet.begin() + cut);
            if (ParseMonsterChat(
                    partial.empty() ? nullptr : partial.data(),
                    partial.size(), out))
            {
                Check(false, "truncated packet rejected");
                break;
            }
        }
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kBossGuid,
            "Hogger", 0, "", "Liar");
        // Corrupt the text length so it overruns the packet.
        std::size_t textLengthPos = packet.size() - 1 - 5 - 4;
        packet[textLengthPos + 3] = 0x7F;
        Check(!Parse(packet, out), "overlong length rejected");
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kBossGuid,
            "Hogger", 0, "", "Hidden");
        // Replace the terminating NUL of the text.
        packet[packet.size() - 2] = 'X';
        Check(!Parse(packet, out), "missing NUL rejected");
    }

    {
        MonsterChat out;
        auto packet = Build(kChatMsgMonsterYell, kBossGuid,
            "Hogger", 0, "", std::string("A\0B", 3));
        Check(!Parse(packet, out), "embedded NUL rejected");
    }

    Check(IsCreatureGuid(kBossGuid) && IsCreatureGuid(kVehicleGuid),
        "unit and vehicle guids are creatures");
    Check(!IsCreatureGuid(kPetGuid) && !IsCreatureGuid(kPlayerGuid),
        "pet and player guids are not creatures");
    Check(!IsPlayerOrPetGuid(0), "empty guid is no receiver");

    if (failures)
        std::printf("%d failure(s)\n", failures);
    else
        std::printf("OK\n");
    return failures ? 1 : 0;
}
