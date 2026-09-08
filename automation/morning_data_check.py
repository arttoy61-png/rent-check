"""07:15 KST safety check: keep existing data; dispatch latest main at most once.

Read the real collection summary, not the latest contract date. No collector,
API timeout, CSV, widget or UI changes are made by this script. Writes are limited
to the public status record and the existing update-data workflow dispatch.
"""
from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = "arttoy61-png/rent-check"
WORKFLOW = "update-data.yml"
REPORT = "molit_morning_check.json"
KST = ZoneInfo("Asia/Seoul")
SUMMARY = re.compile(r"시도\s+(\d+)/(\d+)\s*·\s*성공\s+(\d+)\s*·\s*실패\s+(\d+)")


def api(path: str, method: str = "GET", payload: dict | None = None, *, raw: bool = False):
    """Use runner's gh client; never put tokens in URLs or publish raw logs."""
    cmd = ["gh", "api", "--method", method, "-H", "Accept: application/vnd.github+json", path]
    if raw:
        job = re.fullmatch(r"repos/arttoy61-png/rent-check/actions/jobs/(\d+)/logs", path)
        if not job or method != "GET":
            raise ValueError("Unsupported raw log request")
        # gh run view handles the signed-log redirect/archive download.
        cmd = ["gh", "run", "view", "--repo", REPO, "--job", job.group(1), "--log"]
    if payload is not None:
        cmd += ["--input", "-"]
    result = subprocess.run(cmd, input=json.dumps(payload) if payload is not None else None,
                            capture_output=True, text=True, timeout=90, check=False)
    if result.returncode:
        if method == "GET" and "(HTTP 404)" in result.stderr:
            raise FileNotFoundError(path)
        raise RuntimeError(f"GitHub {method} request failed: {path}")
    return result.stdout if raw else (json.loads(result.stdout) if result.stdout.strip() else {})


def today_run(run: dict, day: str) -> bool:
    try:
        return datetime.fromisoformat(run["created_at"].replace("Z", "+00:00")).astimezone(KST).date().isoformat() == day
    except (KeyError, TypeError, ValueError):
        return False


def collection_health(log: str) -> dict:
    matches = list(SUMMARY.finditer(log))
    if not matches:
        return {"state": "unverified", "reason": "Collection summary unavailable"}
    attempted, expected, succeeded, failed = map(int, matches[-1].groups())
    complete = expected > 0 and attempted == expected == succeeded and failed == 0
    return {"state": "healthy" if complete else "failed", "attempted": attempted,
            "expected": expected, "succeeded": succeeded, "failed": failed}


def decide(runs: list[dict], day: str, previous: dict, health: dict) -> str:
    runs = [r for r in runs if r.get("head_branch") == "main"]
    # An older queued run also blocks a duplicate. Never cancel an active run.
    if any(r.get("status") != "completed" for r in runs):
        return "running"
    current = sorted((r for r in runs if today_run(r, day)), key=lambda r: r["created_at"], reverse=True)
    claimed = previous.get("retry", {}).get("day") == day
    if current:
        last = current[0]
        if last.get("conclusion") == "success" and health.get("state") == "healthy":
            return "recovered" if claimed else "healthy"
        if last.get("conclusion") == "success" and health.get("state") != "failed":
            return "unverified"  # Missing logs are not evidence of failed collection.
    if claimed or any(r.get("display_title") == f"Morning recovery {day}" for r in current):
        return "retry_exhausted"
    return "retry_needed"  # Failed collection/build, or no collection started today.


class Monitor:
    def __init__(self, request=api):
        self.request = request
        self.base = f"repos/{REPO}"

    def read_report(self) -> dict:
        try:
            data = self.request(f"{self.base}/contents/{REPORT}?ref=main")
        except FileNotFoundError:
            return {}
        return json.loads(base64.b64decode(data["content"]))

    def write_report(self, report: dict) -> None:
        path = f"{self.base}/contents/{REPORT}"
        try:
            existing = self.request(path + "?ref=main")
            sha = existing["sha"]
        except FileNotFoundError:
            sha = None
        body = {"branch": "main", "message": "ops: morning collection check",
                "content": base64.b64encode((json.dumps(report, ensure_ascii=False, indent=2) + "\n").encode()).decode()}
        if sha:
            body["sha"] = sha
        self.request(path, "PUT", body)

    def runs(self) -> list[dict]:
        return self.request(f"{self.base}/actions/workflows/{WORKFLOW}/runs?branch=main&per_page=100")["workflow_runs"]

    def health(self, runs: list[dict], day: str) -> dict:
        candidates = sorted((r for r in runs if r.get("head_branch") == "main" and today_run(r, day)),
                            key=lambda r: r["created_at"], reverse=True)
        if not candidates or candidates[0].get("status") != "completed":
            return {"state": "unverified"}
        last = candidates[0]
        if last.get("conclusion") != "success":
            return {"state": "failed", "reason": "Workflow did not complete successfully", "run_id": last["id"]}
        for attempt in range(3):
            try:
                jobs = self.request(f"{self.base}/actions/runs/{last['id']}/jobs?per_page=100")["jobs"]
                job = next(j for j in jobs if any(s.get("name") == "Collect MOLIT data (강서)" for s in j.get("steps", [])))
                log = self.request(f"{self.base}/actions/jobs/{job['id']}/logs", raw=True)
                result = collection_health(log)
                if result["state"] != "unverified":
                    return {**result, "run_id": last["id"]}
            except (RuntimeError, FileNotFoundError, StopIteration, KeyError, subprocess.TimeoutExpired):
                pass
            if attempt < 2:
                time.sleep(5)
        return {"state": "unverified", "reason": "Could not verify collection log", "run_id": last["id"]}

    def check(self, now: datetime, *, dry_run: bool = True, allow_retry: bool = True) -> dict:
        day = now.astimezone(KST).date().isoformat()
        previous = self.read_report()
        runs = self.runs()
        health = self.health(runs, day)
        state = decide(runs, day, previous, health)
        if state == "retry_needed" and not allow_retry:
            state = "needs_retry"
        report = {"schema_version": 1, "checked_at": now.astimezone(KST).isoformat(),
                  "day": day, "state": state, "collection": health,
                  "retry": previous.get("retry", {}), "dry_run": dry_run, "allow_retry": allow_retry}
        if dry_run:
            return report
        if state == "retry_needed":
            # Durable claim BEFORE dispatch: an uncertain POST response must not
            # produce a second attempt. Workflow concurrency serializes checkers.
            report["retry"] = {"day": day, "requested_at": now.isoformat(),
                               "checker_run_id": os.getenv("GITHUB_RUN_ID", ""), "status": "claimed"}
            report["state"] = "retry_reserved"
            self.write_report(report)
            # Recheck immediately: a normal run may have started meanwhile.
            latest_runs = self.runs()
            latest_health = self.health(latest_runs, day)
            latest_state = decide(latest_runs, day, previous, latest_health)
            if latest_state != "retry_needed":
                report["state"] = latest_state
                report["retry"]["status"] = "not_dispatched"
                report["collection"] = latest_health
            else:
                try:
                    self.request(f"{self.base}/actions/workflows/{WORKFLOW}/dispatches", "POST",
                                 {"ref": "main", "inputs": {"recovery_day": day}})
                    report["state"] = "retry_requested"
                    report["retry"]["status"] = "requested"
                except RuntimeError:
                    report["state"] = "dispatch_unconfirmed"
                    report["retry"]["status"] = "unconfirmed"
                    # Keep claim even on timeout: GitHub may have accepted it.
        self.write_report(report)
        return report


def main() -> int:
    if os.getenv("GITHUB_REPOSITORY", REPO) != REPO:
        raise SystemExit("Unexpected repository; refusing to run")
    dry_run = os.getenv("MONITOR_DRY_RUN", "true").lower() != "false"
    allow_retry = os.getenv("MONITOR_ALLOW_RETRY", "false").lower() == "true"
    result = Monitor().check(datetime.now(KST), dry_run=dry_run, allow_retry=allow_retry)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if os.getenv("GITHUB_STEP_SUMMARY"):
        with Path(os.environ["GITHUB_STEP_SUMMARY"]).open("a", encoding="utf-8") as out:
            out.write("## 07:15 KST morning collection check\n\n```json\n" + text + "\n```\n")
    return 1 if result["state"] in {"unverified", "retry_exhausted", "dispatch_unconfirmed"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
