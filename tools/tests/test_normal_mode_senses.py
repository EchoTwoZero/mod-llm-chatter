"""Normal-mode playerbots perceive the game world only by sight."""

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
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import chatter_group_prompts as group_prompts  # noqa: E402
from chatter_constants import (  # noqa: E402
    AMBIENT_CHAT_TOPICS,
    AMBIENT_CHAT_TOPICS_RP,
    MESSAGE_CATEGORIES,
    PERSONALITY_SPICES,
    PROXIMITY_PLAYER_CHAT_TOPICS,
)
from chatter_mode import (  # noqa: E402
    NORMAL_MODE_SENSES_RULE,
    build_npc_chat_guidance,
    build_player_chat_guidance,
)
from chatter_prompts import build_event_conversation_prompt  # noqa: E402
from chatter_shared import (  # noqa: E402
    get_dungeon_flavor,
    get_subzone_lore,
    get_subzone_name,
    get_zone_flavor,
)

ZONE, AREA, DUNGEON = 12, 62, 36
BOT = {
    'name': 'Aliss',
    'bot_name': 'Aliss',
    'race': 'Human',
    'class': 'Mage',
    'level': 32,
    'gender': 'female',
    'guid': 42,
    'zone': 'Elwynn Forest',
}
OTHER = {**BOT, 'name': 'Borin', 'bot_name': 'Borin', 'guid': 43}


def _text(prompt):
    return (
        str(getattr(prompt, 'system_prompt', '') or '')
        + '\n'
        + str(getattr(prompt, 'user_prompt', prompt))
    )


def _lore_samples():
    """Distinctive slices of each lore source, so a leak is visible."""
    return {
        'zone': get_zone_flavor(ZONE)[:60],
        'subzone': get_subzone_lore(ZONE, AREA)[:60],
        'dungeon': get_dungeon_flavor(DUNGEON)[:60],
    }


class SightOnlyContractTests(unittest.TestCase):
    def test_normal_voice_contract_is_sight_only(self):
        for channel in (
            'party', 'general', 'guild', 'say', 'raid', 'battleground',
        ):
            text = build_player_chat_guidance('normal', channel)
            self.assertEqual(text.count(NORMAL_MODE_SENSES_RULE), 1)
        for phrase in (
            'You only see the game world',
            'never claim to hear, smell, taste, touch or physically feel',
            'background lore, never as something you perceive',
        ):
            self.assertIn(phrase, NORMAL_MODE_SENSES_RULE)

    def test_roleplay_and_npc_voices_are_unchanged(self):
        self.assertNotIn(
            NORMAL_MODE_SENSES_RULE,
            build_player_chat_guidance('roleplay', 'party'),
        )
        self.assertNotIn(NORMAL_MODE_SENSES_RULE, build_npc_chat_guidance())


class LoreProseTests(unittest.TestCase):
    def setUp(self):
        self.lore = _lore_samples()

    def _assert_no_lore(self, text, *kinds):
        for kind in kinds:
            self.assertNotIn(self.lore[kind], text, kind)

    def test_zone_arrival_keeps_lore_in_roleplay_only(self):
        def build(mode):
            return _text(group_prompts.build_zone_transition_prompt(
                BOT, ['patient'], 'Elwynn Forest', ZONE, mode,
                area_id=AREA, is_subzone=True,
            ))

        normal = build('normal')
        self._assert_no_lore(normal, 'zone', 'subzone')
        self.assertNotIn('Zone atmosphere', normal)
        self.assertIn(get_subzone_name(ZONE, AREA), normal)
        roleplay = build('roleplay')
        self.assertIn(self.lore['zone'], roleplay)
        self.assertIn(self.lore['subzone'], roleplay)

    def test_party_reply_names_the_subzone_without_its_lore(self):
        def build(mode):
            return _text(group_prompts.build_player_response_prompt(
                BOT, ['patient'], 'Karaez', 'where are we?', mode,
                zone_id=ZONE, area_id=AREA, allow_action=False,
            ))

        normal = build('normal')
        self._assert_no_lore(normal, 'zone', 'subzone')
        self.assertIn(f'Subzone: {get_subzone_name(ZONE, AREA)}', normal)
        self.assertIn(self.lore['subzone'], build('roleplay'))

    def test_dungeon_entry_keeps_bosses_but_not_atmosphere(self):
        def build(mode):
            with patch.object(
                group_prompts, 'get_dungeon_bosses',
                return_value=['Edwin VanCleef'],
            ):
                return _text(group_prompts.build_dungeon_entry_prompt(
                    None, BOT, ['patient'], 'The Deadmines', False,
                    DUNGEON, mode,
                ))

        normal = build('normal')
        self._assert_no_lore(normal, 'dungeon')
        self.assertNotIn('Dungeon atmosphere', normal)
        self.assertIn('Edwin VanCleef', normal)
        roleplay = build('roleplay')
        self.assertIn(self.lore['dungeon'], roleplay)
        self.assertIn('Edwin VanCleef', roleplay)

    def test_world_event_conversation_narration_is_roleplay_only(self):
        def build(mode):
            return _text(build_event_conversation_prompt(
                [BOT, OTHER], 'The Lunar Festival has begun.',
                zone_id=ZONE, area_id=AREA,
                config={'LLMChatter.ChatterMode': mode},
                current_weather='rain',
            ))

        normal = build('normal')
        for line in ('Time of day:', 'Season:', 'Current weather:'):
            self.assertNotIn(line, normal)
        self._assert_no_lore(normal, 'zone', 'subzone')
        self.assertIn('Time of day:', build('roleplay'))


class TopicPoolTests(unittest.TestCase):
    def test_normal_pools_no_longer_prompt_other_senses(self):
        removed = {
            'message categories': (MESSAGE_CATEGORIES, (
                "noting the game's ambient sounds",
                'mentioning being tired or hungry',
                'commenting on the mood the zone creates',
            )),
            'spices': (PERSONALITY_SPICES, (
                'the ambient sound is making the area feel eerie',
                'your music is a little too loud for the game audio',
            )),
            'ambient topics': (AMBIENT_CHAT_TOPICS, (
                'complaining about being hungry or thirsty',
                'mentioning that the game music fits the current area well',
                'wondering whether a sound cue came from the game or voice '
                'chat',
                'commenting on the weather',
                'commenting on the scenery or surroundings',
            )),
            'proximity topics': (PROXIMITY_PLAYER_CHAT_TOPICS, (
                'commenting on the music changing in this area',
                'asking whether the game sound gave away something nearby',
            )),
        }
        for label, (pool, entries) in removed.items():
            for entry in entries:
                self.assertNotIn(entry, pool, label)

    def test_scenery_and_sensation_topics_stay_in_roleplay(self):
        for entry in (
            'commenting on the scenery or surroundings',
            'observing the landscape or terrain',
            'commenting on the weather',
            'noticing the time of day',
            'mentioning how the light looks',
            'complaining about being hungry or thirsty',
        ):
            self.assertIn(entry, AMBIENT_CHAT_TOPICS_RP)
            self.assertNotIn(entry, AMBIENT_CHAT_TOPICS)
        self.assertEqual(
            len(set(AMBIENT_CHAT_TOPICS_RP)), len(AMBIENT_CHAT_TOPICS_RP),
        )

    def test_visible_scenery_remains_available_but_occasional(self):
        scenery = [
            category for category in MESSAGE_CATEGORIES
            if category in (
                'noticing something interesting nearby',
                'remarking on how empty or busy the area is',
                'noting something weird or unexpected',
                "commenting on a zone's visual design",
            )
        ]
        self.assertEqual(len(scenery), 4)
        self.assertLess(len(scenery) / len(MESSAGE_CATEGORIES), 0.06)


if __name__ == '__main__':
    unittest.main()
