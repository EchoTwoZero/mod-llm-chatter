"""Canonical prompt rules for playerbot chatter modes.

Playerbots follow ``LLMChatter.ChatterMode``. Actual NPCs always remain
in-world, including when they share a proximity scene with playerbots.
"""

import hashlib
import logging

from chatter_text import (
    HABIT_LOWERCASE,
    HABIT_NO_APOSTROPHES,
    HABIT_NO_FINAL_STOP,
    HABIT_TRAIL_OFF,
    apply_typing_habits,
)

logger = logging.getLogger(__name__)


_NORMAL_PLAYER_STYLE_PROFILES = [
    (('friendly', 'patient'), 'warm and conversational'),
    (('quiet', 'polite'), 'brief and understated'),
    (('helpful', 'practical'), 'clear and matter-of-fact'),
    (('relaxed', 'easygoing'), 'casual and unhurried'),
    (('dry-humored', 'observant'), 'wry and concise'),
    (('chatty', 'curious'), 'friendly and engaged'),
    (('focused', 'cooperative'), 'direct but respectful'),
    (('experienced', 'patient'), 'calm and measured'),
    (('playful', 'good-natured'), 'lightly teasing but kind'),
    (('reserved', 'considerate'), 'soft-spoken and thoughtful'),
    (('competitive', 'fair-minded'), 'energetic without hostility'),
    (('methodical', 'reliable'), 'precise and composed'),
    (('newer', 'open-minded'), 'curious and unpretentious'),
    (('social', 'encouraging'), 'upbeat without overdoing it'),
    (('independent', 'courteous'), 'plain-spoken and self-contained'),
    (('blunt', 'well-meaning'), 'direct with occasional mild salt'),
    (('mature', 'supportive'), 'steady and reassuring'),
    (('casual', 'adaptable'), 'natural and low-key'),
    (('analytical', 'calm'), 'specific without lecturing'),
    (('self-deprecating', 'friendly'), 'dry and approachable'),
]

_NORMAL_PLAYER_EXTRA_TRAITS = [
    'attentive',
    'team-minded',
    'low-key',
    'curious',
    'straightforward',
    'good-humored',
    'steady',
    'flexible',
    'thoughtful',
    'game-focused',
]


# How a normal-mode player types: (weight, description). These cover
# mechanics only (capitals, punctuation, sentence completeness), never
# vocabulary, content or attitude, and none drops question marks.
# Weights keep ordinary typing the most common single habit.
_NORMAL_PLAYER_TYPING_STYLES = [
    (22, 'ordinary sentence case with normal punctuation'),
    (16, 'all lowercase, no full stop at the end'),
    (14, 'capitalises the first word but leaves off the final '
         'full stop'),
    (12, 'all lowercase, light on punctuation, skips apostrophes '
         '(dont, im, thats)'),
    (10, 'lowercase fragments rather than full sentences'),
    (8, 'tidy, complete sentences with careful punctuation'),
    (6, 'tends to trail off with ... rather than end a sentence'),
    (6, 'lowercase and quick, now and then leaves a small typo '
        'uncorrected'),
    (6, 'mostly tidy, but drops the odd capital or apostrophe when '
        'typing fast'),
]

# The part of each style that is applied to finished text, because
# models drift back to tidy sentences. Fragments, typos and dropped
# capitals stay prompt-only: they cannot be imposed mechanically.
_TYPING_STYLE_HABITS = {
    'all lowercase, no full stop at the end': frozenset(
        {HABIT_LOWERCASE, HABIT_NO_FINAL_STOP}),
    'capitalises the first word but leaves off the final '
    'full stop': frozenset({HABIT_NO_FINAL_STOP}),
    'all lowercase, light on punctuation, skips apostrophes '
    '(dont, im, thats)': frozenset(
        {HABIT_LOWERCASE, HABIT_NO_FINAL_STOP, HABIT_NO_APOSTROPHES}),
    'lowercase fragments rather than full sentences': frozenset(
        {HABIT_LOWERCASE, HABIT_NO_FINAL_STOP}),
    'tends to trail off with ... rather than end a sentence':
        frozenset({HABIT_TRAIL_OFF}),
    'lowercase and quick, now and then leaves a small typo '
    'uncorrected': frozenset({HABIT_LOWERCASE, HABIT_NO_FINAL_STOP}),
}

TYPING_STYLE_RULE = (
    "Where a speaker's typing style is given, it only describes how "
    "that person types. It applies to every message they write and "
    "overrides general advice about capitals, punctuation and complete "
    "sentences, but never changes what they say or how friendly they "
    "are. Questions keep their question mark, and names and any "
    "{item:}, {quest:} or {spell:} placeholders are written exactly "
    "as given."
)

# Loaded once at bridge startup by configure_typing_style(). The mode
# and language let delivery-side code apply habits without a config.
_typing_style_enabled = True
_typing_mode = 'normal'
_typing_english = True


def configure_typing_style(config) -> None:
    """Load the typing-style switch, chatter mode and language."""
    global _typing_style_enabled, _typing_mode, _typing_english
    config = config or {}
    try:
        _typing_style_enabled = int(config.get(
            'LLMChatter.Persona.TypingStyle.Enable', 1
        )) != 0
    except (TypeError, ValueError, AttributeError):
        logger.error(
            "Failed to parse LLMChatter.Persona.TypingStyle.Enable"
        )
        _typing_style_enabled = True
    _typing_mode = normalize_chatter_mode(
        config.get('LLMChatter.ChatterMode', 'normal')
    )
    language = str(
        config.get('LLMChatter.Language', 'GB') or 'GB'
    ).strip().upper()
    _typing_english = language in ('GB', 'US', 'EN')


def typing_style_enabled() -> bool:
    """Return whether normal-mode playerbots get a typing style."""
    return _typing_style_enabled


def normalize_chatter_mode(mode: str) -> str:
    """Return a supported playerbot chatter mode."""
    value = str(mode or '').strip().lower()
    return value if value in ('normal', 'roleplay') else 'normal'


def is_roleplay(mode: str) -> bool:
    """Return whether playerbots should speak in character."""
    return normalize_chatter_mode(mode) == 'roleplay'


def resolve_player_personality(
    name: str,
    traits=None,
    tone: str = '',
    mode: str = 'normal',
):
    """Return RP identity metadata or a stable normal-player profile.

    Persistent identities predate the mode boundary and may contain mystical
    or in-world traits. Normal mode must never send that legacy metadata to
    the model, so it derives a deterministic player-side style from the bot
    name instead. Roleplay mode receives the stored values unchanged.
    """
    if is_roleplay(mode):
        return [value for value in (traits or []) if value], tone or ''

    seed = str(name or 'playerbot').strip().casefold().encode('utf-8')
    digest = hashlib.sha256(seed).digest()
    index = int.from_bytes(digest[:4], 'big') % len(
        _NORMAL_PLAYER_STYLE_PROFILES
    )
    player_traits, player_tone = _NORMAL_PLAYER_STYLE_PROFILES[index]
    extra_index = int.from_bytes(digest[4:8], 'big') % len(
        _NORMAL_PLAYER_EXTRA_TRAITS
    )
    return (
        list(player_traits)
        + [_NORMAL_PLAYER_EXTRA_TRAITS[extra_index]],
        player_tone,
    )


def resolve_typing_style(name: str, mode: str = 'normal') -> str:
    """Return a bot's stable typing habit, or '' when it has none.

    Derived from the bot name like the normal player-style profile,
    so the same bot types the same way in every channel and after
    every restart. Roleplay speech is spoken, not typed, and never
    gets one.
    """
    if is_roleplay(mode) or not _typing_style_enabled:
        return ''
    return _pick_typing_style(name)


def _pick_typing_style(name: str) -> str:
    seed = str(name or 'playerbot').strip().casefold().encode('utf-8')
    digest = hashlib.sha256(seed).digest()
    roll = int.from_bytes(digest[8:12], 'big') % sum(
        weight for weight, _ in _NORMAL_PLAYER_TYPING_STYLES
    )
    for weight, style in _NORMAL_PLAYER_TYPING_STYLES:
        if roll < weight:
            return style
        roll -= weight
    return ''


def apply_typing_style(name: str, message: str) -> str:
    """Apply a playerbot's mechanical typing habits to its message.

    Uses the mode and language loaded by configure_typing_style(), so
    message-insertion code needs no config. Roleplay mode, a disabled
    switch and ordinary styles return the message unchanged. Never
    call this for NPC speech.
    """
    style = resolve_typing_style(name, _typing_mode)
    habits = _TYPING_STYLE_HABITS.get(style)
    if not habits:
        return message
    return apply_typing_habits(message, habits, english=_typing_english)


def typing_style_note(
    name: str,
    mode: str = 'normal',
    label: str = 'types',
) -> str:
    """Return ``"<label>: <style>"`` for a speaker, or '' for none."""
    style = resolve_typing_style(name, mode)
    return f"{label}: {style}" if style else ''


def build_player_identity(
    name: str,
    race: str = '',
    class_name: str = '',
    level=None,
    gender: str = '',
    mode: str = 'normal',
    gear: str = '',
) -> str:
    """Build an identity with an explicit player/character boundary."""
    details = []
    if level not in (None, '', 0, '0'):
        details.append(f"level {level}")
    if gender:
        details.append(str(gender))
    if race:
        details.append(str(race))
    if class_name:
        details.append(str(class_name))
    avatar = ' '.join(details) or 'WoW character'

    if is_roleplay(mode):
        identity = (
            f"You are {name}, a {avatar} in World of Warcraft."
        )
    else:
        identity = (
            f"You are {name}, a person playing a {avatar} "
            "character in World of Warcraft."
        )

    gear = (gear or '').strip()
    return f"{identity} {gear}" if gear else identity


def build_player_identity_from_dict(
    bot: dict,
    mode: str,
    include_level: bool = True,
) -> str:
    """Build the mode-aware identity for a standard bot dictionary."""
    return build_player_identity(
        bot.get('name', 'Unknown'),
        bot.get('race', ''),
        bot.get('class', ''),
        bot.get('level') if include_level else None,
        bot.get('gender', ''),
        mode,
        bot.get('gear', ''),
    )


def build_player_prompt_header(
    name: str,
    race: str = '',
    class_name: str = '',
    level=None,
    gender: str = '',
    mode: str = 'normal',
    channel: str = 'party',
    gear: str = '',
) -> str:
    """Build a playerbot identity followed by its channel voice contract.

    Normal mode also states how this speaker types. A builder that
    adds build_persona_block() to the same prompt passes
    include_typing_style=False there, so the habit is stated once.
    """
    header = (
        build_player_identity(
            name, race, class_name, level, gender, mode,
            gear,
        )
        + "\n"
        + build_player_chat_guidance(mode, channel)
    )
    note = typing_style_note(name, mode, label='How you type')
    return f"{header}\n{note}" if note else header


def build_player_prompt_header_from_dict(
    bot: dict,
    mode: str,
    channel: str = 'party',
) -> str:
    """Build a standard playerbot prompt header from a bot dictionary."""
    return build_player_prompt_header(
        bot.get('name', 'Unknown'),
        bot.get('race', ''),
        bot.get('class', ''),
        bot.get('level'),
        bot.get('gender', ''),
        mode,
        channel,
        bot.get('gear', ''),
    )


# Normal-mode playerbots experience the game world as a player does:
# what is on screen and the game's own audio. Shared by the voice
# contract and any prompt that carries lore prose into a normal scene.
NORMAL_MODE_SENSES_RULE = (
    "You experience the game world as a player does: through what is "
    "on your screen and the game's own audio, such as music, sound "
    "effects, voice lines and audio cues. Never claim to smell, taste, "
    "touch or physically feel anything in it, such as scents, "
    "temperature, wind, wounds, armor, hunger or fatigue. Treat any "
    "smells, temperatures or textures in other prompt data as background "
    "lore, never as something you perceive. Only describe a specific "
    "sound as happening right now when the prompt or chat supplies it; "
    "general remarks about the game's music or sound design are fine. "
    "Unless the moment is about the scenery, do not narrate your "
    "surroundings."
)


def build_player_chat_guidance(
    mode: str,
    channel: str = 'party',
) -> str:
    """Return the shared voice contract for playerbot chat prompts."""
    if is_roleplay(mode):
        return (
            "CHAT MODE: ROLEPLAY. Speak as the character living in Azeroth. "
            "Stay in character and avoid game-system or real-world talk."
        )

    channel_note = {
        'general': (
            "Use ordinary zone chat: questions, help, progress, opinions, "
            "complaints, or loose banter."
        ),
        'guild': (
            "Use familiar guild chat between players who may be in "
            "different zones."
        ),
        'battleground': (
            "Use concise battleground team chat: tactical, reactive, and "
            "competitive without becoming hostile."
        ),
        'raid': (
            "Use concise raid chat: practical, reactive, and focused on the "
            "run when appropriate."
        ),
        'say': (
            "Use casual player /say near other characters and NPCs."
        ),
    }.get(
        channel,
        "Use casual party chat between people playing together.",
    )
    return (
        "CHAT MODE: NORMAL. Speak as a person playing WoW, not as an "
        "inhabitant of Azeroth. Race, class, level, gear, deaths, travel, "
        "weather, and locations describe the character or game. "
        f"{NORMAL_MODE_SENSES_RULE} "
        "If any other prompt data contains mystical, devotional, heroic, "
        "racial, or in-world personality and tone labels, treat it as legacy "
        "character metadata and do not express it. "
        f"{channel_note} Friendly and respectful is the default. Different "
        "personalities may be quiet, polite, helpful, dry, playful, blunt, "
        "or occasionally mildly salty or immature, but never force rudeness. "
        "Use familiar WoW shorthand only when natural. Avoid slurs, personal "
        "abuse, l33tspeak, meme spam, current social-media slang, and "
        "customer-service or motivational-assistant phrasing. Natural "
        "kindness, patience, and complete sentences are welcome."
        + (f" {TYPING_STYLE_RULE}" if _typing_style_enabled else "")
    )


def build_npc_chat_guidance() -> str:
    """Return the mode-invariant voice contract for actual NPCs."""
    return (
        "SPEAKER TYPE: NPC. Speak as an inhabitant of Azeroth. Stay grounded "
        "and lore-friendly; never mention players, screens, UI, game systems, "
        "or the real world."
    )
