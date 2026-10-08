"""Normal-mode playerbots keep one typing habit in every channel."""

import importlib
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def _ensure_module(name):
    module = sys.modules.get(name)
    if module is None:
        module = types.ModuleType(name)
        sys.modules[name] = module
    return module


for dependency in ('anthropic', 'openai'):
    try:
        importlib.import_module(dependency)
    except ModuleNotFoundError:
        module = _ensure_module(dependency)
        attribute = (
            'Anthropic' if dependency == 'anthropic'
            else 'OpenAI'
        )
        setattr(module, attribute, type(attribute, (), {}))

try:
    importlib.import_module('mysql.connector')
except ModuleNotFoundError:
    mysql_module = _ensure_module('mysql')
    connector_module = _ensure_module('mysql.connector')
    setattr(mysql_module, 'connector', connector_module)

TOOLS_DIR = Path(__file__).resolve().parents[1]
MODULE_DIR = TOOLS_DIR.parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import chatter_emote_observer as emote_observer  # noqa: E402
import chatter_mode  # noqa: E402
import chatter_proximity  # noqa: E402
from chatter_general import (  # noqa: E402
    _build_general_response_prompt,
)
from chatter_group import build_idle_chatter_prompt  # noqa: E402
from chatter_group_general_reaction import (  # noqa: E402
    _build_conversation_prompt as _relay_conversation_prompt,
)
from chatter_group_prompts import (  # noqa: E402
    build_player_msg_conversation_prompt,
    build_player_response_prompt,
)
from chatter_guild import _participant_identity_lines  # noqa: E402
from chatter_mode import (  # noqa: E402
    TYPING_STYLE_RULE,
    build_npc_chat_guidance,
    build_player_chat_guidance,
    build_player_prompt_header,
    configure_typing_style,
    resolve_typing_style,
    typing_style_note,
)
from chatter_persona import (  # noqa: E402
    Persona,
    build_cast_lines,
    build_persona_block,
    persona_from_fields,
)
from chatter_prompts import (  # noqa: E402
    build_dynamic_guidelines,
    build_event_statement_prompt,
    build_plain_statement_prompt,
)

KEY = 'LLMChatter.Persona.TypingStyle.Enable'
NORMAL_CONFIG = {'LLMChatter.ChatterMode': 'normal'}
RP_CONFIG = {'LLMChatter.ChatterMode': 'roleplay'}
SINGLE = 'How you type: '
BOT = {
    'name': 'Aliss',
    'bot_name': 'Aliss',
    'race': 'Human',
    'class': 'Mage',
    'level': 32,
    'gender': 'female',
}
OTHER = {**BOT, 'name': 'Rytsen', 'bot_name': 'Rytsen', 'guid': 43}
STYLES = [
    style for _, style in chatter_mode._NORMAL_PLAYER_TYPING_STYLES
]


def _text(prompt):
    return (
        str(getattr(prompt, 'system_prompt', '') or '')
        + '\n'
        + str(getattr(prompt, 'user_prompt', prompt))
    )


class _Cursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def execute(self, *args, **kwargs):
        pass

    def fetchone(self):
        return self.rows.pop(0) if self.rows else None

    def fetchall(self):
        return []

    def close(self):
        pass


class _DB:
    def __init__(self, rows=None):
        self.cursor_value = _Cursor(rows or [])

    def cursor(self, *args, **kwargs):
        return self.cursor_value


class _TypingStyleCase(unittest.TestCase):
    def setUp(self):
        configure_typing_style({})
        self.addCleanup(configure_typing_style, {})


class TypingStyleResolutionTests(_TypingStyleCase):
    def test_style_is_stable_per_bot(self):
        style = resolve_typing_style('Aliss')
        self.assertIn(style, STYLES)
        for name in ('Aliss', 'aliss', ' ALISS '):
            self.assertEqual(resolve_typing_style(name), style)
        self.assertEqual(
            typing_style_note('Aliss'), f'types: {style}',
        )
        self.assertEqual(
            typing_style_note('Aliss', label='typing style'),
            f'typing style: {style}',
        )

    def test_roleplay_and_disabled_have_no_style(self):
        self.assertEqual(resolve_typing_style('Aliss', 'roleplay'), '')
        self.assertEqual(typing_style_note('Aliss', 'roleplay'), '')
        configure_typing_style({KEY: '0'})
        self.assertEqual(resolve_typing_style('Aliss'), '')
        self.assertEqual(typing_style_note('Aliss'), '')
        self.assertNotIn(
            TYPING_STYLE_RULE,
            build_player_chat_guidance('normal', 'party'),
        )
        self.assertNotIn(SINGLE, build_player_prompt_header(
            'Aliss', 'Human', 'Mage', 32, 'female', 'normal',
        ))

    def test_invalid_setting_keeps_styles_on(self):
        with self.assertLogs('chatter_mode', level='ERROR'):
            configure_typing_style({KEY: 'sometimes'})
        self.assertTrue(chatter_mode.typing_style_enabled())
        configure_typing_style(None)
        self.assertTrue(chatter_mode.typing_style_enabled())

    def test_every_style_is_used_and_ordinary_typing_leads(self):
        counts = {style: 0 for style in STYLES}
        for index in range(4000):
            counts[resolve_typing_style(f'Bot{index}')] += 1
        self.assertTrue(all(counts.values()), counts)
        self.assertEqual(max(counts, key=counts.get), STYLES[0])
        self.assertEqual(len(set(STYLES)), len(STYLES))

    def test_styles_cover_mechanics_only(self):
        # Bot-initiated questions are validated by their final '?'.
        for style in STYLES:
            self.assertNotIn('question', style)
            self.assertNotIn('no punctuation', style)
        self.assertIn(
            'Questions keep their question mark', TYPING_STYLE_RULE,
        )
        self.assertIn('{item:}', TYPING_STYLE_RULE)
        self.assertIn('never changes what they say', TYPING_STYLE_RULE)


class TypingStyleRenderingTests(_TypingStyleCase):
    def test_voice_contract_carries_the_rule_once(self):
        for channel in ('party', 'general', 'guild', 'say', 'raid'):
            text = build_player_chat_guidance('normal', channel)
            self.assertEqual(text.count(TYPING_STYLE_RULE), 1)
        self.assertNotIn(
            TYPING_STYLE_RULE,
            build_player_chat_guidance('roleplay', 'party'),
        )
        self.assertNotIn('typing style', build_npc_chat_guidance())

    def test_header_states_the_speakers_habit(self):
        normal = build_player_prompt_header(
            'Aliss', 'Human', 'Mage', 32, 'female', 'normal',
        )
        self.assertTrue(normal.endswith(
            SINGLE + resolve_typing_style('Aliss')
        ))
        roleplay = build_player_prompt_header(
            'Aliss', 'Human', 'Mage', 32, 'female', 'roleplay',
        )
        self.assertNotIn(SINGLE, roleplay)

    def test_persona_carries_the_habit_in_normal_mode_only(self):
        normal = persona_from_fields(
            'Aliss', 'normal', traits=['zealous'], tone='mystical',
            with_mood=False,
        )
        self.assertEqual(
            normal.typing_style, resolve_typing_style('Aliss'),
        )
        roleplay = persona_from_fields(
            'Aliss', 'roleplay', traits=['zealous'], tone='mystical',
            with_mood=False,
        )
        self.assertEqual(roleplay.typing_style, '')
        self.assertEqual(
            Persona('Aliss', ('patient',), 'calm').typing_style, '',
        )

    def test_persona_block_and_cast_render_the_habit(self):
        aliss = persona_from_fields('Aliss', 'normal', with_mood=False)
        rytsen = persona_from_fields(
            'Rytsen', 'normal', with_mood=False,
        )
        block = build_persona_block(aliss, 'normal')
        self.assertEqual(block.count(SINGLE + aliss.typing_style), 1)
        self.assertNotIn(SINGLE, build_persona_block(
            aliss, 'normal', include_typing_style=False,
        ))
        cast = '\n'.join(build_cast_lines([aliss, rytsen], 'normal'))
        self.assertIn(f'types: {aliss.typing_style}', cast)
        self.assertIn(f'types: {rytsen.typing_style}', cast)
        rp_cast = '\n'.join(build_cast_lines([
            persona_from_fields(
                'Aliss', 'roleplay', traits=['stern'], tone='terse',
                with_mood=False,
            ),
        ], 'roleplay'))
        self.assertNotIn('types:', rp_cast)


class TypingStylePromptTests(_TypingStyleCase):
    def _single_speaker_prompts(self, mode):
        config = NORMAL_CONFIG if mode == 'normal' else RP_CONFIG
        return {
            'party reply': build_player_response_prompt(
                BOT, ['patient'], 'Karaez', 'where is the inn?',
                mode, allow_action=False,
            ),
            'party idle': build_idle_chatter_prompt(
                BOT, ['patient'], mode,
            ),
            'general statement': build_plain_statement_prompt(
                {**BOT, 'zone': 'Elwynn Forest'}, config=config,
                topic='the next quest',
            ),
            'general reply': _build_general_response_prompt(
                'Aliss', 'Human', 'Mage', 32, 'female', ['patient'],
                'Karaez', 'where is the inn?', 'Elwynn Forest', '',
                mode,
            ),
            'world event': build_event_statement_prompt(
                {
                    'bot1_name': 'Aliss', 'bot1_race': 'Human',
                    'bot1_class': 'Mage', 'bot1_level': 32,
                },
                'It started to rain.', 'weather_change',
                'Elwynn Forest', config=config,
            ),
            'emote observer': emote_observer._build_party_bot_prompt(
                'Aliss', 'Human', 'Mage', 'female', 'Karaez', 'hug',
                'Rytsen', 'affection', traits=['patient'],
                mode=mode, bot_guid=42,
            ),
        }

    def test_single_speaker_prompts_state_the_habit_once(self):
        expected = SINGLE + resolve_typing_style('Aliss')
        for label, prompt in self._single_speaker_prompts(
            'normal'
        ).items():
            text = _text(prompt)
            self.assertEqual(text.count(expected), 1, label)
            self.assertEqual(text.count(SINGLE), 1, label)
            self.assertEqual(text.count(TYPING_STYLE_RULE), 1, label)

    def test_roleplay_prompts_never_mention_typing(self):
        for label, prompt in self._single_speaker_prompts(
            'roleplay'
        ).items():
            text = _text(prompt)
            self.assertNotIn(SINGLE, text, label)
            self.assertNotIn(TYPING_STYLE_RULE, text, label)

    def test_disabled_prompts_never_mention_typing(self):
        configure_typing_style({KEY: 0})
        for label, prompt in self._single_speaker_prompts(
            'normal'
        ).items():
            text = _text(prompt)
            self.assertNotIn(SINGLE, text, label)
            self.assertNotIn(TYPING_STYLE_RULE, text, label)

    def test_party_conversation_lists_each_speakers_habit(self):
        prompt = _text(build_player_msg_conversation_prompt(
            [BOT, OTHER],
            {'Aliss': ['patient'], 'Rytsen': ['dry-humored']},
            'Karaez', 'are we ready?', 'normal',
        ))
        for name in ('Aliss', 'Rytsen'):
            note = f'types: {resolve_typing_style(name)}'
            self.assertEqual(prompt.count(note), 1, name)
        self.assertNotIn(SINGLE, prompt)
        self.assertEqual(prompt.count(TYPING_STYLE_RULE), 1)

    def test_general_relay_conversation_lists_each_habit(self):
        location = {
            'dungeon_flavor': '', 'zone_flavor': '',
            'subzone_lore': '',
        }
        source = {'name': 'Borin', 'race': 'Dwarf', 'class': 'Warrior'}
        normal = _text(_relay_conversation_prompt(
            [BOT, OTHER], source, 'anyone need this quest?',
            'Karaez', '', 'normal', location,
        ))
        for name in ('Aliss', 'Rytsen'):
            self.assertIn(
                f'; types: {resolve_typing_style(name)}', normal,
            )
        roleplay = _text(_relay_conversation_prompt(
            [BOT, OTHER], source, 'anyone need this quest?',
            'Karaez', '', 'roleplay', location,
        ))
        self.assertNotIn('types:', roleplay)

    def test_guild_participants_keep_their_habit(self):
        participant = {
            'name': 'Aliss',
            'speaker': {**BOT, 'traits': ['patient']},
        }
        normal = _participant_identity_lines(participant, 'normal')
        self.assertIn(
            f"Aliss typing style: {resolve_typing_style('Aliss')}.",
            normal,
        )
        roleplay = '\n'.join(
            _participant_identity_lines(participant, 'roleplay')
        )
        self.assertNotIn('typing style', roleplay)

    def test_proximity_roster_gives_habits_to_playerbots_only(self):
        participants = [
            {
                'name': 'Innkeeper Allison',
                'is_npc': True,
                'role': 'Innkeeper',
            },
            {'name': 'Aliss', 'is_npc': False, 'bot_guid': 7},
        ]
        db = _DB(rows=[
            {'class': 8, 'race': 1, 'gender': 1, 'level': 32},
            None,
        ])
        prompt = _text(chatter_proximity._conversation_prompt(
            db, {}, participants, NORMAL_CONFIG,
        ))
        note = f"; types: {resolve_typing_style('Aliss')}"
        self.assertEqual(prompt.count(note), 1)
        npc_line = next(
            line for line in prompt.splitlines()
            if line.startswith('- [NPC]')
        )
        self.assertNotIn('types:', npc_line)

    def test_proximity_player_replies_list_playerbot_habits(self):
        participants = [
            {
                'name': 'Innkeeper Allison',
                'is_npc': True,
                'role': 'Innkeeper',
            },
            {
                'name': 'Aliss', 'is_npc': False, 'bot_guid': 7,
                'race': 'Human', 'class': 'Mage',
            },
        ]
        note = f"; types: {resolve_typing_style('Aliss')}"
        say = chatter_proximity._player_say_conversation_prompt
        emote = chatter_proximity._player_emote_conversation_prompt
        extra = {'player_name': 'Karaez', 'participants': participants}
        prompts = {
            'say': lambda config: say(
                _DB(), extra, participants, 'hello', [], config,
            ),
            'emote': lambda config: emote(
                _DB(),
                {**extra, 'addressed_name': 'Aliss'},
                participants, 'wave', config,
            ),
        }
        for label, build in prompts.items():
            normal = _text(build(NORMAL_CONFIG))
            self.assertEqual(normal.count(note), 1, label)
            self.assertEqual(normal.count('; types:'), 1, label)
            self.assertNotIn(
                '; types:', _text(build(RP_CONFIG)), label,
            )

    def test_typos_belong_to_speakers_not_random_lines(self):
        typo = 'Can include a typo for realism'
        with patch('chatter_prompts.random.random', return_value=0.0):
            for _ in range(40):
                self.assertNotIn(typo, build_dynamic_guidelines())
            configure_typing_style({KEY: 0})
            seen = set()
            for _ in range(200):
                seen.update(build_dynamic_guidelines())
        self.assertIn(typo, seen)


class TypingStyleConfigTests(unittest.TestCase):
    def test_templates_document_the_default(self):
        for relative in (
            'conf/mod_llm_chatter.conf.dist',
            'conf/presets/mod_ll_chatter_quieter.conf.dist',
        ):
            lines = (MODULE_DIR / relative).read_text(
                encoding='utf-8',
            ).splitlines()
            self.assertTrue(f'{KEY} = 1' in lines, relative)

    def test_bridge_loads_the_setting_at_startup(self):
        source = (TOOLS_DIR / 'llm_chatter_bridge.py').read_text(
            encoding='utf-8',
        )
        self.assertIn('configure_typing_style(config)', source)


if __name__ == '__main__':
    unittest.main()
