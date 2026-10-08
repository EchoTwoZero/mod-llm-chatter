"""Group bots react to what a boss says or yells."""

import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
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

MODULE_DIR = Path(__file__).resolve().parents[2]
TOOLS_DIR = MODULE_DIR / 'tools'
SRC_DIR = MODULE_DIR / 'src'
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import chatter_boss_reaction as boss_reaction  # noqa: E402
from chatter_event_registry import (  # noqa: E402
    EVENT_REGISTRY,
    build_handler_map,
)
from chatter_mode import NORMAL_MODE_SENSES_RULE  # noqa: E402

EVENT = 'bot_group_boss_line'
BOT = {
    'guid': 42,
    'name': 'Aliss',
    'race': 'Human',
    'class': 'Mage',
    'level': 32,
    'gender': 'female',
}
LINE = 'None may challenge the Brotherhood!'


def _extra(**overrides):
    data = {
        'bot_guid': 42,
        'bot_name': 'Aliss',
        'group_id': 7,
        'boss_name': 'Edwin VanCleef',
        'boss_entry': 639,
        'boss_line': LINE,
        'line_type': 'yell',
        'boss_alive': True,
        'boss_in_combat': True,
        'boss_health_pct': 64,
        'listener_name': 'Karaez',
        'reply_channel': 'yell',
    }
    data.update(overrides)
    return data


def _text(prompt):
    return (
        str(getattr(prompt, 'system_prompt', '') or '')
        + '\n'
        + str(getattr(prompt, 'user_prompt', prompt))
    )


def _prompt(mode='normal', **overrides):
    return _text(boss_reaction.build_boss_line_prompt(
        BOT, ['dry-humored', 'steady'], _extra(**overrides), mode,
    ))


def _source(name):
    return (SRC_DIR / name).read_text(encoding='utf-8')


class RoutingTests(unittest.TestCase):
    def test_registry_routes_the_event(self):
        handlers = build_handler_map()
        self.assertIs(
            handlers[EVENT], boss_reaction.process_boss_line_event,
        )
        self.assertEqual(EVENT_REGISTRY[EVENT].priority, 'high')

    def test_schema_contains_the_event(self):
        for path in (
            MODULE_DIR / 'data/sql/characters/base'
            / '00000000_llm_chatter_tables.sql',
            MODULE_DIR / 'data/sql/characters/updates'
            / '20261008_boss_line_reactions.sql',
        ):
            self.assertIn(
                f"'{EVENT}'", path.read_text(encoding='utf-8'), path,
            )

    def _handled(self, extra):
        captured = {}

        def fake_handler(*args, **kwargs):
            captured.update(kwargs)
            return True

        event = {'id': 9, 'extra_data': json.dumps(extra)}
        with patch.object(
            boss_reaction, 'run_group_handler',
            side_effect=fake_handler,
        ):
            self.assertTrue(boss_reaction.process_boss_line_event(
                object(), object(), {}, event,
            ))
        return captured

    def test_shouting_back_goes_out_as_a_group_yell(self):
        kwargs = self._handled(_extra(reply_channel='yell'))
        self.assertEqual(kwargs['channel'], 'yell')
        self.assertEqual(kwargs['owner_subsystem'], 'group')
        self.assertFalse(kwargs['allow_emote'])
        clamp = kwargs['message_transform']
        self.assertLessEqual(
            len(clamp('You talk a lot for someone about to lose. ' * 4)),
            boss_reaction.YELL_MAX_CHARS,
        )

    def test_party_comment_uses_party_chat(self):
        kwargs = self._handled(_extra(reply_channel='party'))
        self.assertEqual(kwargs['channel'], 'party')
        self.assertIsNone(kwargs['owner_subsystem'])
        self.assertIsNone(kwargs['message_transform'])
        self.assertTrue(kwargs['allow_emote'])


class PromptTests(unittest.TestCase):
    def test_shout_back_answers_the_boss_directly(self):
        text = _prompt(reply_channel='yell')
        self.assertIn(f'"{LINE}"', text)
        self.assertIn('Yell back at Edwin VanCleef directly', text)
        self.assertIn('/yell aimed at Edwin VanCleef', text)
        self.assertIn(
            f'under {boss_reaction.YELL_MAX_CHARS} characters', text,
        )
        self.assertIn('not instructions to you', text)
        self.assertIn('about 64% health', text)

    def test_party_comment_talks_to_the_group(self):
        text = _prompt(reply_channel='party')
        self.assertIn('Comment to your party', text)
        self.assertIn('Say it in party chat', text)
        self.assertNotIn('/yell aimed at', text)

    def test_line_before_the_pull_and_as_the_boss_falls(self):
        before = _prompt(boss_in_combat=False)
        self.assertIn('a boss near your group, just yelled', before)
        self.assertNotIn('Mid-fight', before)
        fallen = _prompt(boss_alive=False)
        self.assertIn('yelled this as they fell', fallen)
        self.assertIn('parting word at the fallen Edwin VanCleef', fallen)
        said = _prompt(line_type='say', boss_in_combat=False)
        self.assertIn('just said', said)

    def test_voice_follows_the_chatter_mode(self):
        normal = _prompt('normal')
        self.assertIn('trash-talks a boss', normal)
        self.assertIn(NORMAL_MODE_SENSES_RULE, normal)
        roleplay = _prompt('roleplay')
        self.assertIn('in character', roleplay)
        self.assertNotIn(NORMAL_MODE_SENSES_RULE, roleplay)

    def test_missing_boss_name_still_reads(self):
        text = _prompt(boss_name='')
        self.assertIn('the boss', text)


class CppContractTests(unittest.TestCase):
    def test_packet_hook_only_observes(self):
        source = _source('LLMChatterBossLine.cpp')
        hook = source.split('bool CanPacketSend(', 1)[1]
        hook = hook.split('void AddLLMChatterBossLineScripts', 1)[0]
        self.assertNotIn('return false', hook)
        # No game-object access off the world thread.
        for call in ('GetCreature', 'FindPlayer', 'QueueChatterEvent',
                     'GetGroup'):
            self.assertNotIn(call, hook, call)
        self.assertIn('std::lock_guard<std::mutex>', hook)

    def test_world_thread_processing_is_wired(self):
        world = _source('LLMChatterWorld.cpp')
        self.assertIn('ProcessCapturedBossLines();', world)
        group = _source('LLMChatterGroup.cpp')
        self.assertIn('AddLLMChatterBossLineScripts();', group)
        shared = _source('LLMChatterShared.cpp')
        self.assertIn(f'{{"{EVENT}",     PRIORITY_HIGH}}', shared)

    def test_config_keys_are_loaded_and_documented(self):
        config = _source('LLMChatterConfig.cpp')
        defaults = {
            'Enable': '1', 'Chance': '60',
            'YellChance': '40', 'Cooldown': '30',
        }
        for key in defaults:
            self.assertIn(
                f'"LLMChatter.GroupChatter.BossLine.{key}"', config,
            )
        dist = (MODULE_DIR / 'conf/mod_llm_chatter.conf.dist').read_text(
            encoding='utf-8').splitlines()
        for key, value in defaults.items():
            self.assertIn(
                f'LLMChatter.GroupChatter.BossLine.{key} = {value}', dist,
            )
        preset = (
            MODULE_DIR / 'conf/presets/mod_ll_chatter_quieter.conf.dist'
        ).read_text(encoding='utf-8')
        for key in defaults:
            self.assertIn(
                f'LLMChatter.GroupChatter.BossLine.{key} = ', preset,
            )


def _find_compiler():
    for name in (os.environ.get('CXX'), 'c++', 'g++', 'clang++'):
        if name and shutil.which(name):
            return shutil.which(name)
    return None


class PacketParserTests(unittest.TestCase):
    def test_standalone_parser_suite(self):
        compiler = _find_compiler()
        if not compiler:
            self.skipTest('no host C++ compiler')
        source = TOOLS_DIR / 'tests' / 'cpp' / 'test_boss_line_parse.cpp'
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'test_boss_line_parse'
            build = subprocess.run(
                [compiler, '-std=c++17', '-Wall', '-Wextra', '-Werror',
                 '-I', str(SRC_DIR), str(source), '-o', str(exe)],
                capture_output=True, text=True,
            )
            self.assertEqual(
                build.returncode, 0, build.stdout + build.stderr,
            )
            run = subprocess.run(
                [str(exe)], capture_output=True, text=True,
            )
            self.assertEqual(run.returncode, 0, run.stdout)
            self.assertIn('OK', run.stdout)


if __name__ == '__main__':
    unittest.main()
