from contextlib import contextmanager
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import wake_crawl_thread as wake


class WakeThreadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        wake.save(self.root/'thread-wake-config.json',dict(enabled=True,thread_id='same-thread',socket_path='/unused'))
        wake.save(self.root/'status.json',dict(state='paused',run_started_at=100))
        self.rpc = Mock()
        self.rpc.call.side_effect = self.reply
        self.thread_state = 'idle'
        self.start_error = None

    def reply(self, method, params):
        if method == 'thread/read':
            return dict(thread=dict(id='same-thread',status=dict(type=self.thread_state)))
        if method == 'turn/start':
            if self.start_error:
                raise self.start_error
            return dict(turn=dict(id='wake-turn'))
        return {}

    @contextmanager
    def connection(self, config):
        yield self.rpc

    def check(self, kind='paused'):
        with patch.object(wake,'connect',self.connection):
            return wake.check(self.root,dict(kind=kind))

    def test_stop_wakes_same_thread_once_without_model_or_policy_overrides(self):
        wake.save(self.root/'status.json',dict(state='paused',run_started_at=100,limits=dict(delay_seconds=.02)))
        self.assertEqual(self.check(),'sent')
        self.assertEqual(self.check(),'already_sent')
        calls=self.rpc.call.call_args_list
        self.assertEqual([c.args[0] for c in calls],['thread/read','turn/start'])
        request=calls[-1].args[1]
        self.assertEqual(set(request),{'threadId','input'})
        self.assertEqual(request['threadId'],'same-thread')
        self.assertIn('Keep 50 req/s',request['input'][0]['text'])
        self.assertIn('Real login failures, rate limits and challenges must stay stopped',request['input'][0]['text'])

    def test_busy_thread_defers_until_idle(self):
        self.thread_state='active'
        self.assertEqual(self.check(),'thread_busy')
        self.assertFalse((self.root/'thread-wake-state.json').exists())
        self.thread_state='idle'
        self.assertEqual(self.check(),'sent')

    def test_healthy_complete_and_manual_stop_do_not_wake(self):
        for kind in ['healthy','scan_complete','stopped']:
            self.assertEqual(self.check(kind),'not_needed')
        self.rpc.call.assert_not_called()

    def test_resume_restores_only_the_same_unloaded_thread(self):
        self.thread_state='notLoaded'
        self.assertEqual(self.check(),'sent')
        self.assertEqual(self.rpc.call.call_args_list[1].args,
                         ('thread/resume',dict(threadId='same-thread',excludeTurns=True)))

    def test_rejected_dispatch_retries_but_lost_ack_does_not_duplicate(self):
        self.start_error=wake.RpcError('busy')
        self.assertEqual(self.check(),'failed')
        self.start_error=TimeoutError('reply lost')
        self.assertEqual(self.check(),'uncertain')
        self.start_error=None
        self.assertEqual(self.check(),'already_sent')

    def test_new_run_can_wake_for_a_new_incident(self):
        self.assertEqual(self.check(),'sent')
        wake.save(self.root/'status.json',dict(state='paused',run_started_at=200))
        self.assertEqual(self.check(),'sent')


if __name__=='__main__':
    unittest.main()
