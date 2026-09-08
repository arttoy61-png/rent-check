import base64
import copy
import importlib.util
import json
from datetime import datetime
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location("monitor", Path(__file__).resolve().parents[1] / "automation/morning_data_check.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
DAY = "2026-09-09"
NOW = datetime(2026, 9, 9, 7, 15, tzinfo=m.KST)
GOOD = "시도 144/144 · 성공 144 · 실패 0 · 소요 90초"
BAD = "시도 12/144 · 성공 0 · 실패 12 · 소요 227초"

def run(**overrides):
    return {"id": 10, "head_branch": "main", "created_at": "2026-09-08T20:15:00Z",
            "status": "completed", "conclusion": "success", **overrides}

class Fake:
    def __init__(self, log=BAD, runs=None):
        self.record = None
        self.log = log
        self.runs = [run()] if runs is None else runs
        self.calls = []
        self.post_error = False
        self.on_put = None
    def __call__(self, path, method="GET", payload=None, raw=False):
        self.calls.append((path, method, copy.deepcopy(payload)))
        if method == "POST":
            if self.post_error:
                raise RuntimeError("timeout")
            return {}
        if "contents/" in path:
            if method == "PUT":
                self.record = json.loads(base64.b64decode(payload["content"]))
                if self.on_put:
                    self.on_put()
                return {}
            if self.record is None:
                raise FileNotFoundError(path)
            return {"sha": "abc", "content": base64.b64encode(json.dumps(self.record).encode()).decode()}
        if path.endswith("/logs"):
            return self.log
        if "/jobs?" in path:
            return {"jobs": [{"id": 100, "steps": [{"name": "Collect MOLIT data (강서)"}]}]}
        return {"workflow_runs": self.runs}
    def posts(self):
        return [c for c in self.calls if c[1] == "POST"]

class Tests(unittest.TestCase):
    def setUp(self):
        sleep = patch.object(m.time, "sleep")
        sleep.start()
        self.addCleanup(sleep.stop)
    def test_real_summary(self):
        self.assertEqual(m.collection_health(BAD)["state"], "failed")
        self.assertEqual(m.collection_health(GOOD)["state"], "healthy")
    def test_new_trades_zero_is_not_failure(self):
        self.assertEqual(m.collection_health(GOOD + "\n신규 거래 0건")["state"], "healthy")
    def test_partial_and_missing_logs(self):
        self.assertEqual(m.collection_health("시도 144/144 · 성공 143 · 실패 1")["state"], "failed")
        self.assertEqual(m.collection_health("success")["state"], "unverified")
    def test_kst_day_boundary(self):
        self.assertTrue(m.today_run(run(created_at="2026-09-08T15:01:00Z"), DAY))
        self.assertFalse(m.today_run(run(created_at="2026-09-08T14:59:00Z"), DAY))
    def test_running_and_queued_never_dispatch(self):
        for status in ["queued", "in_progress", "waiting", "pending"]:
            f = Fake(runs=[run(status=status)])
            self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "running")
            self.assertFalse(f.posts())
    def test_healthy_skips(self):
        f = Fake(log=GOOD)
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "healthy")
        self.assertFalse(f.posts())
    def test_failed_collect_green_workflow_dispatches_once(self):
        f = Fake()
        monitor = m.Monitor(f)
        self.assertEqual(monitor.check(NOW, dry_run=False)["state"], "retry_requested")
        self.assertEqual(monitor.check(NOW, dry_run=False)["state"], "retry_exhausted")
        self.assertEqual(len(f.posts()), 1)
        self.assertEqual(f.posts()[0][2], {"ref": "main", "inputs": {"recovery_day": DAY}})
    def test_missed_schedule_dispatches(self):
        f = Fake(runs=[])
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "retry_requested")
    def test_dispatch_timeout_does_not_repeat(self):
        f = Fake()
        f.post_error = True
        monitor = m.Monitor(f)
        self.assertEqual(monitor.check(NOW, dry_run=False)["state"], "dispatch_unconfirmed")
        monitor.check(NOW, dry_run=False)
        self.assertEqual(len(f.posts()), 1)
    def test_started_during_check_is_not_dispatched(self):
        f = Fake()
        f.on_put = lambda: setattr(f, "runs", [run(status="in_progress")])
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "running")
        self.assertFalse(f.posts())
    def test_dry_run_never_writes(self):
        f = Fake()
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=True)["state"], "retry_needed")
        self.assertTrue(all(c[1] == "GET" for c in f.calls))
    def test_recovered(self):
        f = Fake(log=GOOD)
        f.record = {"retry": {"day": DAY}}
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "recovered")
    def test_previous_day_claim_does_not_block(self):
        f = Fake()
        f.record = {"retry": {"day": "2026-09-08"}}
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "retry_requested")
    def test_404_is_not_healthy_or_retry(self):
        f = Fake(log="")
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "unverified")
        self.assertFalse(f.posts())
    def test_already_identified_retry_does_not_repeat(self):
        f = Fake(runs=[run(display_title=f"Morning recovery {DAY}")])
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False)["state"], "retry_exhausted")
        self.assertFalse(f.posts())
    def test_completion_event_only_reports(self):
        f = Fake()
        self.assertEqual(m.Monitor(f).check(NOW, dry_run=False, allow_retry=False)["state"], "needs_retry")
        self.assertFalse(f.posts())
    def test_gh_log_reader(self):
        with patch.object(m.subprocess, "run", return_value=m.subprocess.CompletedProcess([], 0, GOOD, "")) as call:
            self.assertEqual(m.api("repos/arttoy61-png/rent-check/actions/jobs/100/logs", raw=True), GOOD)
            self.assertEqual(call.call_args.args[0], ["gh", "run", "view", "--repo", m.REPO, "--job", "100", "--log"])
    def test_only_main_is_target(self):
        self.assertEqual(m.decide([run(head_branch="test", status="in_progress")], DAY, {}, {}), "retry_needed")

if __name__ == "__main__":
    unittest.main()
