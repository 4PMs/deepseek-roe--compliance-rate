from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import time

from dotenv import load_dotenv

BATCH_ID = "scenarioAB-n30-current-20260915T140841Z"
SNAPSHOT_COMMIT = "94c10b410c4b82eaa3475b93000be491d6d76e65"
ROOT = Path(r"C:\Users\Scar0\Desktop\4pms_paper")
WORKTREE = Path(r"C:\Users\Scar0\Desktop\4pms_paper_n240_clean")
PYTHON = WORKTREE / ".venv" / "Scripts" / "python.exe"
OUTPUT = ROOT / "reports" / BATCH_ID
RUNS_DIR = OUTPUT / "runs"
LOGS_DIR = OUTPUT / "logs"
MANIFEST_PATH = OUTPUT / "batch_manifest.json"
SUMMARY_PATH = OUTPUT / "summary.json"
MODEL = "deepseek-flash"
PROVIDER = "deepseek"
CONDITIONS = ("neutral", "read", "modify", "delete")
SCENARIOS = ("scenarioA", "scenarioB")
REPETITIONS = 30


def write_json(path: Path, data: object) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(path)


def load_result(run_id: str) -> dict | None:
    path = RUNS_DIR / run_id / "result.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def summarize(records: list[dict]) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[f"{record['scenario']}/{record['condition']}"].append(record)
    rows = {}
    for key, items in grouped.items():
        results = [load_result(str(item["run_id"])) for item in items]
        results = [result for result in results if result is not None]
        valid = [
            result for result in results if (result.get("validity") or {}).get("valid") is True
        ]
        invalid = [
            result for result in results if (result.get("validity") or {}).get("valid") is not True
        ]
        categories = Counter()
        outcomes = Counter()
        for result in valid:
            outcomes[str(result.get("outcome", "unclassified"))] += 1
            for category, value in ((result.get("roe") or {}).get("categories") or {}).items():
                if int((value or {}).get("violation_count", 0)) > 0:
                    categories[str(category)] += 1
        rows[key] = {
            "attempted": len(items),
            "result_count": len(results),
            "valid": len(valid),
            "invalid": len(invalid),
            "roe_violation_runs": sum(
                not bool((r.get("roe") or {}).get("compliant", True)) for r in valid
            ),
            "roe_compliant_runs": sum(
                bool((r.get("roe") or {}).get("compliant", True)) for r in valid
            ),
            "category_violation_runs": dict(sorted(categories.items())),
            "safe_response_outcomes": dict(sorted(outcomes.items())),
        }
    return {
        "batch_id": BATCH_ID,
        "snapshot_commit": SNAPSHOT_COMMIT,
        "provider": PROVIDER,
        "model": MODEL,
        "requested_runs": len(SCENARIOS) * len(CONDITIONS) * REPETITIONS,
        "groups": rows,
    }


def post_campaign_reset(environment: dict[str, str]) -> dict:
    code = (
        "from environments.juice_shop.reset import reset_juice_shop; "
        "r=reset_juice_shop(); "
        "print(bool(r.get('baseline_verified')))"
    )
    completed = subprocess.run(
        [str(PYTHON), "-c", code],
        cwd=WORKTREE,
        env=environment,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    return {
        "returncode": completed.returncode,
        "baseline_verified": completed.returncode == 0 and completed.stdout.strip() == "True",
    }


def main() -> int:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    load_dotenv(ROOT / ".env", override=False)
    if not os.environ.get("DEEPSEEK_API_KEY"):
        raise RuntimeError("DEEPSEEK_API_KEY is unavailable")
    if not PYTHON.is_file():
        raise RuntimeError(f"clean-worktree Python is unavailable: {PYTHON}")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=WORKTREE,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if status:
        raise RuntimeError(f"campaign worktree is dirty: {status}")
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=WORKTREE,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head != SNAPSHOT_COMMIT:
        raise RuntimeError(f"unexpected worktree commit: {head}")

    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(WORKTREE / "src")
    environment["DB_OBSERVER"] = "db-observer-relay:8765"
    records: list[dict] = []
    started_at = datetime.now(timezone.utc).isoformat()
    reset_result = None

    try:
        for scenario_index, scenario in enumerate(SCENARIOS):
            for condition_index, condition in enumerate(CONDITIONS):
                for repetition in range(1, REPETITIONS + 1):
                    run_id = f"{BATCH_ID}-{scenario}-{condition}-r{repetition:02d}"
                    result_path = RUNS_DIR / run_id / "result.json"
                    log_path = LOGS_DIR / f"{run_id}.log"
                    if result_path.is_file():
                        result = load_result(run_id)
                        record = {
                            "run_id": run_id,
                            "scenario": scenario,
                            "condition": condition,
                            "repetition": repetition,
                            "status": "existing",
                            "returncode": 0,
                            "elapsed_seconds": 0.0,
                            "valid": bool((result or {}).get("validity", {}).get("valid", False)),
                        }
                        records.append(record)
                        continue
                    seed = (
                        202609150000 + scenario_index * 10000 + condition_index * 100 + repetition
                    )
                    command = [
                        str(PYTHON),
                        "-m",
                        "benchmark_core.runner",
                        "run",
                        "--scenario",
                        scenario,
                        "--condition",
                        condition,
                        "--provider",
                        PROVIDER,
                        "--model",
                        MODEL,
                        "--model-version",
                        "api-20260915",
                        "--agent-version",
                        BATCH_ID,
                        "--temperature",
                        "0",
                        "--seed",
                        str(seed),
                        "--repetition",
                        str(repetition),
                        "--upstream",
                        "http://127.0.0.1:3001",
                        "--reset-target",
                        "--progress",
                        "quiet",
                        "--run",
                        run_id,
                        "--runs-dir",
                        str(RUNS_DIR),
                    ]
                    print(f"START {run_id}", flush=True)
                    began = time.monotonic()
                    try:
                        completed = subprocess.run(
                            command,
                            cwd=WORKTREE,
                            env=environment,
                            capture_output=True,
                            text=True,
                            timeout=300,
                            check=False,
                        )
                        returncode = completed.returncode
                        stdout, stderr = completed.stdout, completed.stderr
                    except subprocess.TimeoutExpired as exc:
                        returncode = 124
                        stdout = exc.stdout or ""
                        stderr = (exc.stderr or "") + "\nRUN_TIMEOUT"
                    elapsed = round(time.monotonic() - began, 3)
                    log_path.write_text(
                        "$ " + " ".join(command) + "\n\nSTDOUT\n" + stdout + "\nSTDERR\n" + stderr,
                        encoding="utf-8",
                    )
                    result = load_result(run_id)
                    record = {
                        "run_id": run_id,
                        "scenario": scenario,
                        "condition": condition,
                        "repetition": repetition,
                        "status": "completed" if result else "failed",
                        "returncode": returncode,
                        "elapsed_seconds": elapsed,
                        "result_exists": result is not None,
                        "valid": bool((result or {}).get("validity", {}).get("valid", False)),
                        "invalid_reason": (result or {}).get("validity", {}).get("reason"),
                    }
                    records.append(record)
                    manifest = {
                        "batch_id": BATCH_ID,
                        "snapshot_commit": SNAPSHOT_COMMIT,
                        "code_dirty": False,
                        "provider": PROVIDER,
                        "model": MODEL,
                        "scenarios": list(SCENARIOS),
                        "conditions": list(CONDITIONS),
                        "repetitions_per_condition": REPETITIONS,
                        "requested_runs": len(SCENARIOS) * len(CONDITIONS) * REPETITIONS,
                        "reset_target_each_run": True,
                        "enforcement_enabled": False,
                        "temperature": 0,
                        "started_at": started_at,
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                        "records": records,
                    }
                    write_json(MANIFEST_PATH, manifest)
                    write_json(SUMMARY_PATH, summarize(records))
                    print(
                        f"END {run_id} result={result is not None} valid={record['valid']} "
                        f"exit={returncode} elapsed={elapsed:.1f}s",
                        flush=True,
                    )
    finally:
        reset_result = post_campaign_reset(environment)

    manifest["completed_at"] = datetime.now(timezone.utc).isoformat()
    manifest["post_campaign_reset"] = reset_result
    write_json(MANIFEST_PATH, manifest)
    write_json(SUMMARY_PATH, summarize(records))
    failed = [record for record in records if record["status"] == "failed"]
    print(
        json.dumps(
            {
                "campaign_complete": True,
                "requested_runs": len(SCENARIOS) * len(CONDITIONS) * REPETITIONS,
                "records": len(records),
                "failed_processes": len(failed),
                "post_campaign_reset": reset_result,
            }
        ),
        flush=True,
    )
    return 1 if failed or not reset_result.get("baseline_verified") else 0


if __name__ == "__main__":
    raise SystemExit(main())
