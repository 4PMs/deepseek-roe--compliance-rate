from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
MODEL = "deepseek-flash"
RUNS = {
    "neutral": ROOT / "runs" / "poc-scenarioB-neutral-20260915T090300Z",
    "modify": ROOT / "runs" / "poc-scenarioB-modify-20260915T090300Z",
}
INVALID_PREFLIGHTS = [
    {
        "run_id": "poc-scenarioB-neutral-20260915T085955Z",
        "model": "qwen3:8b",
        "reason": "provider_error",
        "valid": False,
        "disposition": "excluded_preflight_attempt",
    },
    {
        "run_id": "poc-scenarioB-neutral-20260915T090100Z",
        "model": "qwen2.5-coder:1.5b",
        "reason": "provider_error",
        "valid": False,
        "disposition": "excluded_preflight_attempt",
    },
]
REQUIRED_RAW = {
    "config.json",
    "trace.jsonl",
    "lifecycle.jsonl",
    "events.jsonl",
    "result.json",
    "status.json",
    "progress.jsonl",
    "provenance.json",
    "manifest.json",
    "evidence/setup.json",
    "evidence/environment.json",
    "evidence/outcomes.jsonl",
    "evidence/enforcement.jsonl",
}
SECRET_PATTERNS = {
    "bearer": re.compile(rb"Bearer\s+[A-Za-z0-9._-]{16,}", re.I),
    "jwt": re.compile(rb"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    "api_key_assignment": re.compile(
        rb"(?:api[_-]?key|authorization)\s*[:=]\s*[^\s,}\]]{8,}", re.I
    ),
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def endpoint(result: dict, name: str) -> dict:
    source = result["trajectory"]["endpoints"][name]
    return {
        "status": source["status"],
        "turn": source.get("turn"),
        "confidence": source.get("confidence", 0.0),
        "evidence_ref": source.get("evidence_ref"),
    }


def first_prohibited_action(result: dict) -> dict | None:
    for action in result["trajectory"]["actions"]:
        dispatch = action.get("dispatch") or {}
        if dispatch.get("classification") == "violation":
            proposal = action.get("proposal") or {}
            return {
                "action_id": action.get("action_id"),
                "method": proposal.get("method"),
                "path": proposal.get("path"),
            }
    return None


def first_prohibited_acceptance(result: dict) -> tuple[int | None, str | None]:
    for action in result["trajectory"]["actions"]:
        if (action.get("dispatch") or {}).get("classification") != "violation":
            continue
        acceptance = action.get("server_acceptance") or {}
        evidence = acceptance.get("evidence") or {}
        return evidence.get("http_status"), acceptance.get("status")
    return None, None


def first_prohibited_diff(result: dict) -> dict | None:
    for action in result["trajectory"]["actions"]:
        if (action.get("dispatch") or {}).get("classification") == "violation":
            return (action.get("impact") or {}).get("state_diff")
    return None


def export_result(condition: str, result: dict) -> dict:
    dispatch = result["trajectory"]["endpoints"]["prohibited_dispatch"]
    acceptance = result["trajectory"]["endpoints"]["server_acceptance"]
    impact = result["trajectory"]["endpoints"]["trusted_realized_impact"]
    request = first_prohibited_action(result)
    http_status, action_acceptance_status = first_prohibited_acceptance(result)
    chain = result["safe_response_chain"]
    return {
        "scenario_id": result["scenario_id"],
        "condition_id": condition,
        "run_id": result["run_id"],
        "model": MODEL,
        "trajectory": {
            "expressed_intent": endpoint(result, "expressed_intent"),
            "prohibited_proposal": endpoint(result, "prohibited_proposal"),
            "prohibited_dispatch": {
                "status": dispatch["status"],
                "request": request,
            },
            "server_acceptance": {
                "status": acceptance["status"]
                if action_acceptance_status is None
                else action_acceptance_status,
                "http_status": http_status,
            },
            "trusted_realized_impact": {
                "status": impact["status"],
                "diff": first_prohibited_diff(result),
            },
        },
        "safe_response_chain": {
            "condition_request_present": chain["condition_request_present"] == "observed",
            "scope_conflict_recognized": chain["scope_conflict_recognized"],
            "prohibited_part_not_dispatched": chain["prohibited_part_not_dispatched"],
            "separable_authorized_work_continued": chain["separable_authorized_work_continued"],
            "disposition_recorded": chain["disposition_recorded"],
        },
        "outcome": result["outcome"],
        "halt": {
            "trigger": result["halt"].get("trigger"),
            "trigger_seq": result["halt"].get("trigger_seq"),
        },
        "final_disposition_text": result["final_disposition_text"],
    }


def verify_raw_files(run_dir: Path) -> dict:
    present = {
        path.relative_to(run_dir).as_posix() for path in run_dir.rglob("*") if path.is_file()
    }
    missing = sorted(REQUIRED_RAW - present)
    if missing:
        raise RuntimeError(f"missing raw artifacts for {run_dir.name}: {missing}")
    hits = {name: 0 for name in SECRET_PATTERNS}
    for path in run_dir.rglob("*"):
        if not path.is_file():
            continue
        data = path.read_bytes()
        for name, pattern in SECRET_PATTERNS.items():
            hits[name] += len(pattern.findall(data))
    if any(hits.values()):
        raise RuntimeError(f"unredacted credential-like values found in {run_dir.name}: {hits}")
    return {"files": len(present), "credential_pattern_hits": hits}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    exports = {}
    raw_verification = {}
    source_hashes = {}
    state_observation = {}
    commits = set()

    for condition, run_dir in RUNS.items():
        result = load_json(run_dir / "result.json")
        config = load_json(run_dir / "config.json")
        events = load_jsonl(run_dir / "events.jsonl")
        if result["validity"] != {"valid": True, "reason": None}:
            raise RuntimeError(f"invalid required run: {run_dir.name}")
        if result["scenario_id"] != "scenarioB" or result["condition_id"] != condition:
            raise RuntimeError(f"scenario/condition mismatch: {run_dir.name}")
        if config["max_steps"] != 10 or config["timeout"] != 20:
            raise RuntimeError(f"limit mismatch: {run_dir.name}")
        if result["observers"]["gateway"] != "ok" or result["observers"]["database"] != "ok":
            raise RuntimeError(f"observer not ready: {run_dir.name}")
        if result["provenance"]["code_dirty"] is not False:
            raise RuntimeError(f"dirty provenance: {run_dir.name}")
        commits.add(result["provenance"]["code_commit"])
        transitions = [
            event
            for event in events
            if event.get("kind") == "state_transition"
            and (event.get("attributes") or {}).get("observer_quality", {}).get("status")
            == "observed"
        ]
        if not transitions:
            raise RuntimeError(f"no trusted before/after transition: {run_dir.name}")
        state_observation[condition] = {
            "transition_count": len(transitions),
            "action_ids": [
                (event.get("attributes") or {}).get("action_id") for event in transitions
            ],
            "changes": [
                (event.get("attributes") or {}).get("state_diff", {}).get("change")
                for event in transitions
            ],
            "before_snapshot_present": all(
                (event.get("attributes") or {}).get("before") is not None for event in transitions
            ),
            "after_snapshot_present": all(
                (event.get("attributes") or {}).get("after") is not None for event in transitions
            ),
        }
        exports[condition] = export_result(condition, result)
        (OUT / f"{condition}_result.json").write_text(
            json.dumps(exports[condition], indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        raw_verification[condition] = verify_raw_files(run_dir)
        source_hashes[condition] = {
            path.relative_to(ROOT).as_posix(): sha256(path)
            for path in sorted(run_dir.rglob("*"))
            if path.is_file()
        }

    if len(commits) != 1:
        raise RuntimeError(f"required runs use different commits: {sorted(commits)}")

    bundle = OUT / "scenarioB-minimal-poc-raw-artifacts.zip"
    with zipfile.ZipFile(bundle, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for run_dir in RUNS.values():
            for path in sorted(run_dir.rglob("*")):
                if path.is_file():
                    archive.write(path, path.relative_to(ROOT).as_posix())

    manifest = {
        "purpose": "minimum feasibility demonstration; not a statistical sample",
        "scenario_id": "scenarioB",
        "required_conditions": ["neutral", "modify"],
        "repetitions_per_condition": 1,
        "model": MODEL,
        "provider": "deepseek",
        "code_commit": next(iter(commits)),
        "code_dirty": False,
        "limits": {"max_steps": 10, "timeout": 20},
        "fixture_isolation": {
            "reset_required": True,
            "independent_run_per_condition": True,
            "run_id_reuse": False,
        },
        "post_campaign_reset": {
            "performed": True,
            "baseline_verified": True,
            "recorded_from": "post-run reset_juice_shop() execution",
        },
        "required_runs": [exports[condition]["run_id"] for condition in ("neutral", "modify")],
        "excluded_preflight_attempts": INVALID_PREFLIGHTS,
        "raw_verification": raw_verification,
        "trusted_state_observation": state_observation,
        "source_artifact_sha256": source_hashes,
        "raw_bundle": {
            "path": bundle.relative_to(ROOT).as_posix(),
            "sha256": sha256(bundle),
            "bytes": bundle.stat().st_size,
        },
        "claim_boundary": "Two valid traces demonstrate execution feasibility only; no rate or comparative inference.",
    }
    (OUT / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "required_runs": manifest["required_runs"],
                "valid_runs": len(exports),
                "excluded_preflight_attempts": len(INVALID_PREFLIGHTS),
                "raw_bundle": manifest["raw_bundle"],
                "raw_verification": raw_verification,
                "trusted_state_observation": state_observation,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
