from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

BATCH_ID = "scenarioA-n30-20260915T025311Z"
CONDITIONS = ("neutral", "read", "modify", "delete")
REPETITIONS = 30
ROOT = Path(__file__).resolve().parents[2]
OUTPUT = Path(__file__).resolve().parent
RUNS_DIR = OUTPUT / "runs"
LOGS_DIR = OUTPUT / "logs"
MANIFEST_PATH = OUTPUT / "batch_manifest.json"

RUNS_DIR.mkdir(parents=True, exist_ok=True)
LOGS_DIR.mkdir(parents=True, exist_ok=True)

records: list[dict[str, object]] = []
started_at = datetime.now(timezone.utc).isoformat()
subprocess_environment = os.environ.copy()
existing_pythonpath = subprocess_environment.get("PYTHONPATH")
subprocess_environment["PYTHONPATH"] = (
    str(ROOT) if not existing_pythonpath else str(ROOT) + os.pathsep + existing_pythonpath
)
subprocess_environment["DB_OBSERVER"] = "db-observer-relay:8765"

for condition in CONDITIONS:
    for repetition in range(1, REPETITIONS + 1):
        run_id = f"{BATCH_ID}-{condition}-r{repetition:02d}"
        result_path = RUNS_DIR / run_id / "result.json"
        log_path = LOGS_DIR / f"{run_id}.log"
        if result_path.is_file():
            print(f"SKIP {run_id}: result already exists", flush=True)
            records.append({"run_id": run_id, "condition": condition, "repetition": repetition,
                            "exit_code": 0, "elapsed_seconds": 0.0, "status": "existing"})
            continue

        command = [
            "uv", "run", "runner", "run",
            "--scenario", "scenarioA",
            "--condition", condition,
            "--provider", "deepseek",
            "--model", "deepseek-chat",
            "--upstream", "http://127.0.0.1:3001",
            "--reset-target",
            "--progress", "quiet",
            "--repetition", str(repetition),
            "--run", run_id,
            "--runs-dir", str(RUNS_DIR),
        ]
        print(f"START {run_id}", flush=True)
        run_started = time.monotonic()
        completed = subprocess.run(
            command, cwd=ROOT, env=subprocess_environment, text=True, capture_output=True
        )
        elapsed = time.monotonic() - run_started
        log_path.write_text(
            "$ " + " ".join(command) + "\n\nSTDOUT\n" + completed.stdout
            + "\nSTDERR\n" + completed.stderr,
            encoding="utf-8",
        )
        status = "completed" if completed.returncode == 0 and result_path.is_file() else "failed"
        records.append({
            "run_id": run_id,
            "condition": condition,
            "repetition": repetition,
            "exit_code": completed.returncode,
            "elapsed_seconds": round(elapsed, 3),
            "status": status,
            "result_exists": result_path.is_file(),
        })
        MANIFEST_PATH.write_text(json.dumps({
            "batch_id": BATCH_ID,
            "scenario": "scenarioA",
            "provider": "deepseek",
            "model": "deepseek-chat",
            "conditions": list(CONDITIONS),
            "repetitions_per_condition": REPETITIONS,
            "reset_target_each_run": True,
            "enforcement_enabled": False,
            "started_at": started_at,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "records": records,
        }, indent=2), encoding="utf-8")
        print(f"END {run_id} status={status} exit={completed.returncode} elapsed={elapsed:.1f}s", flush=True)

failed = [record for record in records if record["status"] == "failed"]
manifest = {
    "batch_id": BATCH_ID,
    "scenario": "scenarioA",
    "provider": "deepseek",
    "model": "deepseek-chat",
    "conditions": list(CONDITIONS),
    "repetitions_per_condition": REPETITIONS,
    "attempted_runs": len(records),
    "failed_processes": len(failed),
    "reset_target_each_run": True,
    "enforcement_enabled": False,
    "started_at": started_at,
    "completed_at": datetime.now(timezone.utc).isoformat(),
    "records": records,
}
MANIFEST_PATH.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
print(json.dumps({"attempted_runs": len(records), "failed_processes": len(failed)}), flush=True)
sys.exit(1 if failed else 0)
