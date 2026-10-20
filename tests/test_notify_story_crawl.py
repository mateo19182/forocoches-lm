import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from notify_story_crawl import check, observe


def status(state='running', progress=100):
    return dict(state=state, updated_at=2000, pid=7, attempts=progress, posts=50,
                id_scan=dict(start_id=1, end_id=10000, page_states=dict(done=progress, pending=32)),
                limits=dict(deadline=10000), free_disk_bytes=100e9)


class TelegramMonitorTests(unittest.TestCase):
    def test_sustained_slowdown_and_no_progress(self):
        saved = dict(samples=[dict(at=100, resolved=0, attempts=0)])
        self.assertEqual(observe(status(), saved, 2000, True)['kind'], 'slow')
        saved['samples'][0]['attempts'] = 100
        self.assertEqual(observe(status(), saved, 2000, True)['kind'], 'no_progress')
        self.assertEqual(observe(status(), {}, 2000, True)['kind'], 'healthy')

    @patch('notify_story_crawl.subprocess.run')
    @patch('notify_story_crawl.worker_alive', return_value=True)
    @patch('notify_story_crawl.time.time', return_value=2000)
    def test_alert_deduplication_recovery_milestone_and_daily(self, now, alive, send):
        send.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            def run(value):
                (root / 'status.json').write_text(json.dumps(value))
                return check(root)
            self.assertEqual(run(status()), 'monitoring started')
            self.assertIsNone(run(status()))
            self.assertEqual(run(status('paused')), 'crawl alert')
            self.assertIsNone(run(status('paused')))
            self.assertEqual(run(status()), 'recovered')
            large = status(); large['posts'] = 500000
            self.assertIn('milestone', run(large))
            self.assertIsNone(run(large))
            now.return_value += 86401
            large['updated_at'] = now.return_value
            self.assertEqual(run(large), 'daily update')
            self.assertEqual(send.call_count, 5)
            self.assertEqual(run(status('scan_complete')), 'crawl alert')
            self.assertIsNone(run(status('scan_complete')))

    @patch('notify_story_crawl.subprocess.run')
    @patch('notify_story_crawl.worker_alive', return_value=True)
    @patch('notify_story_crawl.time.time', return_value=2000)
    def test_resume_does_not_count_paused_time_in_rate(self, now, alive, send):
        send.return_value.returncode = 0
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'status.json').write_text(json.dumps(status('paused')))
            check(root)
            now.return_value = 4000
            current = status(); current['updated_at'] = 4000
            (root / 'status.json').write_text(json.dumps(current))
            self.assertEqual(check(root), 'recovered')
            saved = json.loads((root / 'telegram-monitor.json').read_text())
            self.assertEqual(len(saved['samples']), 1)
            self.assertNotIn('ETA', saved['last_message'])

    @patch('notify_story_crawl.subprocess.run')
    @patch('notify_story_crawl.worker_alive', return_value=True)
    @patch('notify_story_crawl.time.time', return_value=2000)
    def test_failed_delivery_retries_without_marking_sent(self, now, alive, send):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'status.json').write_text(json.dumps(status('paused')))
            send.return_value.returncode = 1
            with self.assertRaises(RuntimeError):
                check(root)
            self.assertNotIn('delivered_kind', json.loads((root / 'telegram-monitor.json').read_text()))
            send.return_value.returncode = 0
            self.assertEqual(check(root), 'crawl alert')
            self.assertIsNone(check(root))


if __name__ == '__main__':
    unittest.main()
