"""Synthetic-only hook and per-session queue regressions."""
import concurrent.futures
import json
import os
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch
import wire
import codex_notify as notify


class HookRegressions(unittest.TestCase):
    def test_root_notify_and_nested_notify_preserved(self):
        fixtures = ['  notify=["old"]\n', 'description="""\nnotify=["not a setting"]\n"""\n', '',  'model="demo"\n', '[projects."/tmp/demo"]\nnotify=["keep"]\n',
                    '[[providers]]\nname="demo"\n', 'description="""\n[not_a_table]\n"""\n[actual]\nx=1\n',
                    'notify=["old"]\n[other]\nnotify=["keep"]\n']
        for text in fixtures:
            with self.subTest(text=text), tempfile.TemporaryDirectory() as tmp:
                config, app = Path(tmp)/'config.toml', Path(tmp)/'app.json'
                config.write_text(text)
                before = tomllib.loads(text)
                wire.wire_codex(tmp, str(config), str(app))
                after = tomllib.loads(config.read_text())
                self.assertIn('codex_notify.py', after.pop('notify')[1])
                before.pop('notify', None)
                self.assertEqual(before, after)
                wire.wire_codex(tmp, str(config), str(app))
                wire.unwire_codex(str(config), str(app))
                self.assertEqual(tomllib.loads(text), tomllib.loads(config.read_text()))

    def test_mixed_group_survives_install_and_uninstall(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'settings.json'
            other = {'type': 'command', 'command': 'echo keep-me', 'timeout': 5}
            group = {'matcher': '.*', 'timeout': 17, 'hooks': [other, {'command': '/tmp/claude_stop_hook.py'}]}
            path.write_text(json.dumps({'hooks': {'Stop': [group]}}))
            for _ in range(2):
                wire.wire_claude(tmp, tmp)
                kept = json.loads(path.read_text())['hooks']['Stop'][0]
                self.assertEqual(kept, {**group, 'hooks': [other]})
            wire.unwire_claude(tmp)
            self.assertEqual(json.loads(path.read_text())['hooks']['Stop'], [{**group, 'hooks': [other]}])

    def test_sessions_concurrent_enqueue_and_flush(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(notify, 'PENDING', str(Path(tmp)/'pending')), patch.object(notify.notify_lib, 'load_config', return_value={'quiet_seconds': 0}), patch.object(notify.notify_lib, 'send_markdown') as send, patch.object(notify.notify_lib, 'log'):
            a = {'thread-id': 'A', 'cwd': '/tmp/project-A', 'last-assistant-message': 'A completed'}
            b = {'thread-id': 'B', 'cwd': '/tmp/project-B', 'last-assistant-message': 'B completed'}
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                list(pool.map(notify.enqueue, [a, b] * 20))
            for data in (a, b):
                key = notify.session_key(data)
                self.assertEqual(json.loads(Path(notify.pending_path(key)).read_text())['count'], 20)
                with concurrent.futures.ThreadPoolExecutor(8) as pool:
                    list(pool.map(notify.flush, [key]*8))
            self.assertEqual(send.call_count, 2)
            self.assertTrue(any('A completed' in call.args[0] for call in send.call_args_list))
            self.assertTrue(any('B completed' in call.args[0] for call in send.call_args_list))
            notify.enqueue(a)
            notify.flush(notify.session_key(a))
            self.assertEqual(send.call_count, 3)

if __name__ == '__main__':
    unittest.main()
