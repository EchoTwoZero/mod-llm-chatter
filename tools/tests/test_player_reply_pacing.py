"""Replies a player is waiting on scale with length, not a fixed clock."""

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

import chatter_db  # noqa: E402
import chatter_group  # noqa: E402
from chatter_shared import calculate_dynamic_delay  # noqa: E402

MODULE_DIR = TOOLS_DIR.parent
PREFIX = 'LLMChatter.PlayerChat.DynamicPacing.'
NO_JITTER = {PREFIX + 'JitterPercent': 0}


def _reply_delay(length, config=None, elapsed=0.0):
    return calculate_dynamic_delay(
        length, NO_JITTER if config is None else config,
        responsive=True, elapsed_seconds=elapsed,
    )


class _Cursor:
    def __init__(self, row=None, error=None):
        self.row = row
        self.error = error
        self.queries = []

    def execute(self, query, params=None):
        if self.error:
            raise self.error
        self.queries.append((' '.join(query.split()), params))

    def fetchone(self):
        return self.row

    def close(self):
        pass


class _DB:
    def __init__(self, row=None, error=None):
        self.cursor_obj = _Cursor(row, error)

    def cursor(self, dictionary=False):
        return self.cursor_obj


class PlayerReplyPacingTests(unittest.TestCase):
    def test_longer_replies_take_longer(self):
        short = _reply_delay(4)
        medium = _reply_delay(40)
        long = _reply_delay(200)
        self.assertLess(short, medium)
        self.assertLess(medium, long)
        self.assertEqual(short, 1.4)
        self.assertEqual(medium, 5.0)
        self.assertEqual(long, 8.0)

    def test_time_already_spent_counts_towards_the_delay(self):
        self.assertEqual(_reply_delay(60), 7.0)
        self.assertEqual(_reply_delay(60, elapsed=4), 3.0)
        # A slow provider never adds more than the floor.
        self.assertEqual(_reply_delay(200, elapsed=60), 1.0)

    def test_previous_message_length_is_ignored(self):
        self.assertEqual(
            calculate_dynamic_delay(
                40, NO_JITTER, prev_message_length=250,
                responsive=True,
            ),
            _reply_delay(40),
        )

    def test_bounds_hold_for_every_length_and_latency(self):
        for length in (0, 3, 25, 80, 150, 255):
            for elapsed in (0, 2, 6, 30):
                for _ in range(30):
                    delay = _reply_delay(length, {}, elapsed)
                    self.assertTrue(1 <= delay <= 8, delay)

    def test_jitter_survives_both_limits(self):
        for length, elapsed in ((250, 0), (2, 30)):
            with patch('chatter_shared.random.uniform',
                       side_effect=lambda low, high: low):
                fast = _reply_delay(length, {}, elapsed)
            with patch('chatter_shared.random.uniform',
                       side_effect=lambda low, high: high):
                slow = _reply_delay(length, {}, elapsed)
            self.assertLess(fast, slow)

    def test_disabled_keeps_the_legacy_window(self):
        config = {PREFIX + 'Enable': 0}
        for length in (2, 250):
            for _ in range(30):
                delay = _reply_delay(length, config, elapsed=30)
                self.assertTrue(4 <= delay <= 8, delay)

    def test_invalid_settings_fall_back_to_defaults(self):
        config = {PREFIX + key: 'invalid' for key in (
            'MinSeconds', 'MaxSeconds', 'CharsPerSecond',
        )}
        config[PREFIX + 'JitterPercent'] = 0
        config[PREFIX + 'CharsPerSecond'] = float('nan')
        self.assertEqual(_reply_delay(40, config), 5.0)
        config[PREFIX + 'CharsPerSecond'] = 0
        self.assertEqual(_reply_delay(40, config), 5.0)
        for elapsed in ('invalid', float('nan'), -5, None):
            self.assertEqual(
                _reply_delay(40, config, elapsed), 5.0,
            )
        inverted = {
            PREFIX + 'MinSeconds': 5,
            PREFIX + 'MaxSeconds': 2,
            PREFIX + 'JitterPercent': 0,
        }
        self.assertEqual(_reply_delay(200, inverted), 5.0)

    def test_ambient_timing_is_unchanged(self):
        for _ in range(30):
            delay = calculate_dynamic_delay(
                40, {}, elapsed_seconds=30,
            )
            self.assertGreaterEqual(delay, 4.0 * 0.85)


class EventAgeTests(unittest.TestCase):
    def test_age_comes_from_the_database_clock(self):
        db = _DB({'age_seconds': 6})
        self.assertEqual(
            chatter_db.get_event_age_seconds(db, 77), 6.0,
        )
        query, params = db.cursor_obj.queries[0]
        self.assertIn(
            'TIMESTAMPDIFF(SECOND, created_at, NOW())', query,
        )
        self.assertEqual(params, (77,))

    def test_unknown_age_gives_no_latency_credit(self):
        get_age = chatter_db.get_event_age_seconds
        self.assertEqual(get_age(_DB(None), 77), 0.0)
        self.assertEqual(get_age(_DB({'age_seconds': None}), 77), 0.0)
        self.assertEqual(get_age(_DB({'age_seconds': -3}), 77), 0.0)
        self.assertEqual(get_age(_DB({'age_seconds': 6}), None), 0.0)
        with self.assertLogs('chatter_db', level='ERROR'):
            self.assertEqual(
                get_age(_DB(error=RuntimeError('down')), 77), 0.0,
            )


class SecondReplyOrderTests(unittest.TestCase):
    def _second_reply_delay(self, first_reply_due, now):
        inserted = []
        second = {
            'guid': 2, 'name': 'Rytsen',
            'traits': ['dry-humored'], 'tone': 'wry',
        }
        row = {'class': 1, 'race': 1, 'level': 20, 'gender': 0}
        patches = {
            'get_other_group_bot': second,
            'build_gear_context': '',
            '_get_recent_chat': [],
            'format_chat_history': '',
            'get_group_members': ['Aliss', 'Rytsen'],
            'get_group_location': (12, 0, 0),
            '_maybe_talent_context': None,
            'get_character_info_by_name': None,
            'build_player_response_prompt': 'Prompt',
            'render_for_player_reply': '',
            'party_reaction_backstory': '',
            'call_llm': '{"message": "Same here."}',
            '_store_chat': None,
            'calculate_dynamic_delay': 3.0,
        }
        with patch.object(
            chatter_group.time, 'monotonic', return_value=now,
        ), patch.object(
            chatter_group, 'insert_chat_message',
            side_effect=lambda *args, **kwargs: inserted.append(
                kwargs['delay_seconds']
            ),
        ):
            active = [
                patch.object(
                    chatter_group, name, return_value=value,
                )
                for name, value in patches.items()
            ]
            for item in active:
                item.start()
            try:
                chatter_group._try_second_bot_response(
                    _DB(row), object(), {}, 5, 1, 'Calwen',
                    'Anyone else tired?', 'normal', 77,
                    first_reply_due=first_reply_due,
                )
            finally:
                for item in active:
                    item.stop()
        self.assertEqual(len(inserted), 1)
        return inserted[0]

    def test_second_reply_waits_for_the_first(self):
        # First reply still 6s away: wait it out, then add the gap.
        self.assertEqual(
            self._second_reply_delay(106.0, now=100.0), 9.0,
        )

    def test_first_reply_already_visible_adds_only_the_gap(self):
        self.assertEqual(
            self._second_reply_delay(95.0, now=100.0), 3.0,
        )
        self.assertEqual(
            self._second_reply_delay(None, now=100.0), 3.0,
        )


class ConfigTemplateTests(unittest.TestCase):
    def test_templates_carry_the_code_defaults(self):
        defaults = (
            ('Enable', 1), ('MinSeconds', 1), ('MaxSeconds', 8),
            ('CharsPerSecond', 10), ('JitterPercent', 20),
        )
        for relative in (
            'conf/mod_llm_chatter.conf.dist',
            'conf/presets/mod_ll_chatter_quieter.conf.dist',
        ):
            lines = (MODULE_DIR / relative).read_text(
                encoding='utf-8',
            ).splitlines()
            for key, value in defaults:
                self.assertIn(f'{PREFIX}{key} = {value}', lines)
        # The documented defaults are the ones the code falls back to.
        configured = {PREFIX + key: value for key, value in defaults}
        for length in (4, 40, 200):
            with patch('chatter_shared.random.uniform',
                       side_effect=lambda low, high: high):
                self.assertEqual(
                    _reply_delay(length, {}),
                    _reply_delay(length, configured),
                )


if __name__ == '__main__':
    unittest.main()
