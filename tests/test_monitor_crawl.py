import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from monitor_crawl import assess,check,worker_alive

class MonitorTests(unittest.TestCase):
    def test_disconnected_vpn_is_unhealthy_despite_fresh_heartbeat(self):
        status={'state':'running','updated_at':500,'route':'airvpn','network_ready':False}
        self.assertEqual(assess(status,True,500)[0],'network_unavailable')

    def test_connection_errors_cannot_keep_the_health_status_green(self):
        status={'state':'running','updated_at':500,'run_started_at':100,'last_http_response_at':200}
        self.assertEqual(assess(status,True,500)[0],'network_unavailable')
        status['run_started_at']=490
        self.assertEqual(assess(status,True,500)[0],'healthy')

    @patch('monitor_crawl.subprocess.run')
    def test_container_health_checks_docker_instead_of_the_host_pid(self,run):
        run.return_value.returncode=0;run.return_value.stdout='true\n'
        self.assertTrue(worker_alive(7,'forocoches-collector'))
        self.assertEqual(run.call_args.args[0][-1],'forocoches-collector')
        run.return_value.stdout='false\n'
        self.assertFalse(worker_alive(7,'forocoches-collector'))
    def test_crash_and_stall_are_not_healthy(self):
        running={'state':'running','updated_at':100}
        self.assertEqual(assess(running,True,110)[0],'healthy')
        self.assertEqual(assess(running,False,110)[0],'crashed')
        self.assertEqual(assess(running,True,400)[0],'stalled')

    def test_pauses_and_completion_are_reported_distinctly(self):
        self.assertEqual(assess({'state':'paused'},False,10)[0],'paused')
        self.assertEqual(assess({'state':'budget_reached'},False,10)[0],'budget_reached')
        self.assertEqual(assess({'state':'failed','error_type':'ValueError'},False,10)[1],'error')

    @patch('monitor_crawl.worker_alive',return_value=False)
    def test_terminal_alert_is_persisted_once(self,alive):
        with tempfile.TemporaryDirectory() as directory,contextlib.redirect_stdout(io.StringIO()):
            root=Path(directory)
            (root/'status.json').write_text(json.dumps({'state':'failed','pid':123,'updated_at':100}))
            check(root,False);check(root,False)
            alerts=(root/'alerts.jsonl').read_text().splitlines()
            self.assertEqual(len(alerts),1)
            self.assertEqual(json.loads((root/'health.json').read_text())['kind'],'failed')

if __name__=='__main__':unittest.main()
