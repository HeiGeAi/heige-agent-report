"""Synthetic dispatch acknowledgement and Claude turn-boundary regressions."""
import concurrent.futures
import io
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import claude_stop_hook as claude
import codex_notify as notify


class DispatchRegressions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = {'quiet_seconds': 0, 'open_id': 'synthetic-recipient'}
        for target, name, kwargs in (
            (notify, 'PENDING', {'new': str(Path(self.tmp.name) / 'pending')}),
            (notify.notify_lib, 'load_config', {'return_value': self.cfg}),
            (notify.notify_lib, 'log', {}),
        ):
            patcher = patch.object(target, name, **kwargs)
            result = patcher.start()
            self.addCleanup(patcher.stop)
            if name == 'log':
                self.log = result
        self.event = {'thread-id': 'synthetic-session', 'last-assistant-message': 'older report'}
        self.key = notify.enqueue(self.event)
        self.path = Path(notify.pending_path(self.key))

    def test_failed_dispatch_is_retained_and_later_acknowledged(self):
        before = self.path.read_bytes()
        with patch.object(notify.notify_lib, 'send_markdown', side_effect=[False, True]) as send:
            notify.flush(self.key)
            self.assertTrue(self.path.exists(), 'unaccepted report must remain pending')
            self.assertEqual(self.path.read_bytes(), before)
            self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
            self.assertIn('retained', self.log.call_args.args[1])
            self.assertNotIn('sent', self.log.call_args.args[1])
            notify.flush(self.key)
        self.assertEqual(send.call_count, 2)
        self.assertFalse(self.path.exists())
        self.assertIn('dispatch accepted', self.log.call_args.args[1])
        self.assertNotIn('sent', self.log.call_args.args[1])

    def test_dispatch_exception_retains_pending_for_retry(self):
        before = self.path.read_bytes()
        with patch.object(notify.notify_lib, 'send_markdown', side_effect=OSError('synthetic startup failure')):
            notify.flush(self.key)
        self.assertTrue(self.path.exists())
        self.assertEqual(self.path.read_bytes(), before)

    def test_real_dispatch_start_failure_paths_retain_pending(self):
        original_mkstemp = tempfile.mkstemp
        for reason in ('missing recipient', 'temporary file failure', 'worker startup failure'):
            with self.subTest(reason=reason):
                if not self.path.exists():
                    notify.enqueue(self.event)
                self.cfg['open_id'] = '' if reason == 'missing recipient' else 'synthetic-recipient'
                tempfile_effect = OSError('synthetic temporary failure') if reason == 'temporary file failure' else (
                    lambda **kwargs: original_mkstemp(dir=self.tmp.name, **kwargs))
                with patch.object(notify.notify_lib.tempfile, 'mkstemp', side_effect=tempfile_effect), patch.object(
                    notify.notify_lib.subprocess, 'Popen', side_effect=OSError('synthetic worker failure')
                ) as start:
                    notify.flush(self.key)
                self.assertTrue(self.path.exists(), reason)
                self.assertEqual(json.loads(self.path.read_text())['data'], self.event)
                self.assertEqual(start.call_count, int(reason == 'worker startup failure'))

    def test_enqueue_during_failed_start_keeps_newest_data_and_all_counts(self):
        started, release, enqueue_started = threading.Event(), threading.Event(), threading.Event()
        def dispatch(*args):
            started.set()
            if not release.wait(5):
                raise AssertionError('test dispatch was not released')
            return False
        newer = {**self.event, 'last-assistant-message': 'newer report'}
        def enqueue():
            enqueue_started.set()
            return notify.enqueue(newer)
        with patch.object(notify.notify_lib, 'send_markdown', side_effect=dispatch):
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                flushing = pool.submit(notify.flush, self.key)
                try:
                    self.assertTrue(started.wait(5))
                    queued = pool.submit(enqueue)
                    self.assertTrue(enqueue_started.wait(5))
                finally:
                    release.set()
                flushing.result(timeout=5)
                queued.result(timeout=5)
        item = json.loads(self.path.read_text())
        self.assertEqual(item['count'], 2)
        self.assertEqual(item['data'], newer)
        with patch.object(notify.notify_lib, 'send_markdown', return_value=True) as send:
            notify.flush(self.key)
        self.assertIn('newer report', send.call_args.args[0])
        self.assertIn('2', send.call_args.args[0])
        self.assertFalse(self.path.exists())

    def test_enqueue_during_success_is_not_removed_with_old_report(self):
        started, release = threading.Event(), threading.Event()
        def dispatch(*args):
            started.set()
            if not release.wait(5):
                raise AssertionError('test dispatch was not released')
            return True
        newer = {**self.event, 'last-assistant-message': 'newer report'}
        with patch.object(notify.notify_lib, 'send_markdown', side_effect=dispatch):
            with concurrent.futures.ThreadPoolExecutor(2) as pool:
                flushing = pool.submit(notify.flush, self.key)
                try:
                    self.assertTrue(started.wait(5))
                    queued = pool.submit(notify.enqueue, newer)
                finally:
                    release.set()
                flushing.result(timeout=5)
                queued.result(timeout=5)
        item = json.loads(self.path.read_text())
        self.assertEqual(item['count'], 1)
        self.assertEqual(item['data'], newer)

    def test_competing_flushers_retry_failed_start_without_duplicate_acceptance(self):
        with patch.object(notify.notify_lib, 'send_markdown', side_effect=[False, True]) as send:
            with concurrent.futures.ThreadPoolExecutor(8) as pool:
                list(pool.map(notify.flush, [self.key] * 8))
        self.assertEqual(send.call_count, 2)
        self.assertFalse(self.path.exists())
        accepted = [call for call in self.log.call_args_list if 'dispatch accepted' in call.args[1]]
        self.assertEqual(len(accepted), 1)


def row(kind, content, timestamp='2026-10-09T00:00:00Z', **extra):
    return {'type': kind, 'timestamp': timestamp, 'message': {'content': content}, **extra}


class SummaryRegressions(unittest.TestCase):
    def message(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'transcript.jsonl'
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            with patch.object(claude.sys, 'stdin', io.StringIO(json.dumps({'transcript_path': str(path), 'cwd': tmp}))), patch.object(
                claude.notify_lib, 'load_config', return_value={'min_seconds': 0}
            ), patch.object(claude.notify_lib, 'send_markdown', return_value=True) as send, patch.dict(os.environ, {}, clear=True):
                claude.main()
            self.assertEqual(send.call_count, 1)
            return send.call_args.args[0]

    def old_turn(self):
        return [row('user', 'old question'), row('assistant', [{'type': 'text', 'text': 'OLD ANSWER'}], '2026-10-09T00:01:00Z')]

    def test_new_human_turn_cannot_borrow_old_answer(self):
        for content in ('new question', [{'type': 'text', 'text': 'new question'}], [{'type': 'image', 'source': {'type': 'base64', 'data': 'synthetic'}}]):
            for timestamp in ('2026-10-09T01:00:00Z', None, 'invalid timestamp'):
                with self.subTest(content=content, timestamp=timestamp):
                    message = self.message(self.old_turn() + [row('user', content, timestamp), row(
                        'assistant', [{'type': 'tool_use', 'name': 'synthetic_tool'}], '2026-10-09T01:01:00Z')])
                    self.assertNotIn('OLD ANSWER', message)
                    self.assertIn('无文本输出', message)
                    if timestamp == '2026-10-09T01:00:00Z':
                        self.assertIn('1分0秒', message)
                    else:
                        self.assertIn('耗时 0秒', message)

    def test_tool_results_sidechains_and_meta_records_are_not_human_boundaries(self):
        non_prompts = [
            row('user', [{'type': 'tool_result', 'content': 'result'}]),
            row('user', [{'type': 'text', 'text': 'result wrapper'}, {'type': 'tool_result', 'content': 'result'}]),
            row('user', 'result wrapper', toolUseResult={}),
            row('user', 'side task', isSidechain=True),
            row('user', 'internal context', isMeta=True),
        ]
        for non_prompt in non_prompts:
            with self.subTest(non_prompt=non_prompt):
                message = self.message(self.old_turn() + [non_prompt])
                self.assertIn('OLD ANSWER', message)

    def test_current_turn_text_survives_later_tool_results_and_tool_use(self):
        message = self.message(self.old_turn() + [
            row('user', 'new question', '2026-10-09T01:00:00Z'),
            row('assistant', [{'type': 'text', 'text': 'CURRENT ANSWER'}], '2026-10-09T01:01:00Z'),
            row('user', [{'type': 'tool_result', 'content': 'result'}], '2026-10-09T01:02:00Z'),
            row('assistant', [{'type': 'tool_use', 'name': 'synthetic_tool'}], '2026-10-09T01:03:00Z'),
            row('assistant', [{'type': 'text', 'text': 'SIDECHAIN ANSWER'}], '2026-10-09T01:04:00Z', isSidechain=True),
        ])
        self.assertIn('CURRENT ANSWER', message)
        self.assertNotIn('OLD ANSWER', message)
        self.assertNotIn('SIDECHAIN ANSWER', message)


if __name__ == '__main__':
    unittest.main()
