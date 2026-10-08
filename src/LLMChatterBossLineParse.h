#ifndef MOD_LLM_CHATTER_BOSS_LINE_PARSE_H
#define MOD_LLM_CHATTER_BOSS_LINE_PARSE_H

/*
 * mod-llm-chatter - monster chat packet reader
 *
 * Dependency-free (standard library only) so it can be
 * unit-tested outside the server build
 * (tools/tests/cpp/test_boss_line_parse.cpp).
 *
 * Reads the SMSG_MESSAGECHAT layout written by
 * ChatHandler::BuildChatPacket() for creature speech:
 *
 *   uint8  chat type
 *   int32  language
 *   uint64 sender guid
 *   uint32 flags
 *   uint32 sender name length (including the NUL)
 *   string sender name, NUL-terminated
 *   uint64 receiver guid
 *   [uint32 length + NUL-terminated receiver name, only when
 *    the receiver guid is set and is not a player or pet]
 *   uint32 message length (including the NUL)
 *   string message, NUL-terminated
 *   uint8  chat tag
 *
 * Integers are little-endian. Anything malformed or truncated
 * is rejected rather than partially read.
 */

#include <cstddef>
#include <cstdint>
#include <string>
#include <utility>

namespace LLMChatterBossLine
{

// ChatMsg values from SharedDefines.h (3.3.5a).
constexpr std::uint8_t kChatMsgMonsterSay = 0x0C;
constexpr std::uint8_t kChatMsgMonsterYell = 0x0E;

// HighGuid values from ObjectGuid.h, in the top 16 bits.
constexpr std::uint16_t kHighGuidPlayer = 0x0000;
constexpr std::uint16_t kHighGuidUnit = 0xF130;
constexpr std::uint16_t kHighGuidPet = 0xF140;
constexpr std::uint16_t kHighGuidVehicle = 0xF150;

// Longest name or line accepted; real ones are far shorter.
constexpr std::uint32_t kMaxFieldBytes = 4096;

struct MonsterChat
{
    std::uint8_t type{0};
    std::uint64_t senderGuid{0};
    std::string senderName;
    std::string text;
};

inline std::uint16_t HighGuidOf(std::uint64_t raw)
{
    return static_cast<std::uint16_t>((raw >> 48) & 0xFFFF);
}

inline bool IsCreatureGuid(std::uint64_t raw)
{
    std::uint16_t high = HighGuidOf(raw);
    return high == kHighGuidUnit || high == kHighGuidVehicle;
}

inline bool IsPlayerOrPetGuid(std::uint64_t raw)
{
    if (raw == 0)
        return false;
    std::uint16_t high = HighGuidOf(raw);
    return high == kHighGuidPlayer || high == kHighGuidPet;
}

namespace detail
{

class Reader
{
public:
    Reader(std::uint8_t const* data, std::size_t size)
        : _data(data), _size(size) {}

    bool Skip(std::size_t bytes)
    {
        if (bytes > _size - _pos)
            return false;
        _pos += bytes;
        return true;
    }

    template <typename T>
    bool Read(T& out)
    {
        if (sizeof(T) > _size - _pos)
            return false;
        T value = 0;
        for (std::size_t i = 0; i < sizeof(T); ++i)
            value |= static_cast<T>(
                static_cast<T>(_data[_pos + i]) << (8 * i));
        _pos += sizeof(T);
        out = value;
        return true;
    }

    // A uint32 length that counts the trailing NUL, then the
    // bytes. The NUL must be where the length says it is.
    bool ReadLengthPrefixed(std::string& out)
    {
        std::uint32_t length = 0;
        if (!Read(length) || length == 0
            || length > kMaxFieldBytes
            || length > _size - _pos)
            return false;
        char const* begin =
            reinterpret_cast<char const*>(_data + _pos);
        if (begin[length - 1] != '\0')
            return false;
        out.assign(begin, length - 1);
        if (out.find('\0') != std::string::npos)
            return false;
        _pos += length;
        return true;
    }

private:
    std::uint8_t const* _data;
    std::size_t _size;
    std::size_t _pos{0};
};

} // namespace detail

// True when the packet body is a creature's say or yell. Fills
// `out` only on success.
inline bool ParseMonsterChat(
    std::uint8_t const* data, std::size_t size,
    MonsterChat& out)
{
    if (!data || size == 0)
        return false;

    detail::Reader reader(data, size);
    MonsterChat parsed;
    if (!reader.Read(parsed.type))
        return false;
    if (parsed.type != kChatMsgMonsterSay
        && parsed.type != kChatMsgMonsterYell)
        return false;

    std::uint32_t flags = 0;
    std::uint64_t receiverGuid = 0;
    if (!reader.Skip(4)  // language
        || !reader.Read(parsed.senderGuid)
        || !reader.Read(flags)
        || !reader.ReadLengthPrefixed(parsed.senderName)
        || !reader.Read(receiverGuid))
        return false;

    if (receiverGuid != 0 && !IsPlayerOrPetGuid(receiverGuid))
    {
        std::string receiverName;
        if (!reader.ReadLengthPrefixed(receiverName))
            return false;
    }

    std::uint8_t chatTag = 0;
    if (!reader.ReadLengthPrefixed(parsed.text)
        || !reader.Read(chatTag))
        return false;

    if (!IsCreatureGuid(parsed.senderGuid)
        || parsed.senderName.empty() || parsed.text.empty())
        return false;

    out = std::move(parsed);
    return true;
}

} // namespace LLMChatterBossLine

#endif
