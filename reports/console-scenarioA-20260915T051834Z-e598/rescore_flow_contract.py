from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess

import yaml

from benchmark_core.core.policy import Policy
from benchmark_core.core.run import RunConfig
from benchmark_core.evaluate.pipeline import evaluate_run
from benchmark_core.runner import (
    _integrate_condition_goal,
    _load_instruction_condition,
    _with_execution_status,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "runs" / "console-scenarioA-20260915T051834Z-e598"
OUTPUT = Path(__file__).resolve().parent / "offline_flow_rescore"
SCENARIO_DIR = ROOT / "scenarios" / "scenarioA"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def git_commit() -> str:
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip() if completed.returncode == 0 else "unknown"


def main() -> None:
    config = RunConfig.from_dict(json.loads((SOURCE / "config.json").read_text(encoding="utf-8")))
    source_result_path = SOURCE / "result.json"
    source_result = json.loads(source_result_path.read_text(encoding="utf-8"))
    condition_id = config.instruction_condition or "neutral"
    condition = _load_instruction_condition(SCENARIO_DIR, condition_id)
    base = yaml.safe_load((SCENARIO_DIR / "scenario.yaml").read_text(encoding="utf-8"))
    integrated = _integrate_condition_goal(base, condition)
    policy = Policy.from_dict(
        yaml.safe_load((SCENARIO_DIR / "policy.yaml").read_text(encoding="utf-8"))
    )
    environment = yaml.safe_load(
        (ROOT / "environments" / config.environment / "environment.yaml").read_text(
            encoding="utf-8"
        )
    )

    result = evaluate_run(
        SOURCE / "events.jsonl",
        integrated,
        policy,
        config,
        environment=environment,
        lifecycle_path=SOURCE / "lifecycle.jsonl",
    )
    termination = source_result.get("termination") or {}
    result = _with_execution_status(result, termination)

    payload = result.to_dict()
    payload["offline_rescore"] = {
        "source_run_dir": str(SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "source_result_sha256": sha256(source_result_path),
        "source_events_sha256": sha256(SOURCE / "events.jsonl"),
        "source_trace_sha256": sha256(SOURCE / "trace.jsonl"),
        "source_lifecycle_sha256": sha256(SOURCE / "lifecycle.jsonl"),
        "condition_id": condition_id,
        "configured_condition_sha256": config.instruction_condition_sha256,
        "current_condition_sha256": sha256(SCENARIO_DIR / "conditions" / f"{condition_id}.yaml"),
        "evaluator_code_commit": git_commit(),
        "evaluator_code_dirty": True,
        "rescored_at": datetime.now(timezone.utc).isoformat(),
        "provider_reinvoked": False,
        "source_artifacts_modified": False,
    }

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "scenarioA-integrated.yaml").write_text(
        yaml.safe_dump(integrated, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    (OUTPUT / "result.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    endpoints = payload.get("trajectory", {}).get("endpoints", {})
    print(
        json.dumps(
            {
                "run_id": payload["run_id"],
                "prohibited_proposal": endpoints.get("prohibited_proposal"),
                "roe_compliant": payload["roe"]["compliant"],
                "roe_violation_count": len(payload["roe"]["violations"]),
                "outcome": payload.get("outcome"),
                "output": str(OUTPUT / "result.json"),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
