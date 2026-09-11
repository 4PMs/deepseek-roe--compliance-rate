"""Audit JS-001 run artifacts without dashboard compatibility defaults."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping


RUN_LEVEL_FIELDS = [
    "artifact_path", "artifact_sha256", "run_id", "config_run_id", "result_run_id",
    "config_scenario", "config_policy", "condition", "config_model", "config_model_version",
    "config_provider", "config_temperature", "config_seed", "config_repetition",
    "config_max_steps", "config_timeout", "config_started_at", "enforcement_enabled",
    "provenance_code_commit", "provenance_code_dirty", "provenance_policy_sha256",
    "provenance_scenario_sha256", "provenance_environment_sha256",
    "provenance_target_image_digest", "provenance_started_at", "provenance_finished_at",
    "status", "termination_reason", "termination_step", "termination_detail",
    "validity_state", "validity_reason", "observer_gateway", "observer_database",
    "goal_success", "goal_achieved_step", "roe_compliant", "safe_success",
    "roe_violation_event_count", "violation_methods", "violation_paths", "violation_types",
    "metrics_steps", "events_count", "lifecycle_count", "performance_eligible",
    "protocol_comparable", "guardrail_evaluable", "exclusion_reason", "audit_flags",
]

FATAL_INTEGRITY_FLAGS = {
    "missing_config_json", "corrupt_config_json", "missing_result_json", "corrupt_result_json",
    "missing_events_jsonl", "empty_events_jsonl", "corrupt_events_jsonl",
    "config_result_run_id_mismatch", "event_run_id_mismatch", "condition_unknown",
    "missing_required_outcome", "duplicate_run_id", "duplicate_artifact",
}

PROTOCOL_FLAGS = {
    "provenance_missing", "code_commit_missing", "code_dirty", "model_version_unknown",
    "policy_hash_mismatch_current_arm", "scenario_hash_mismatch_current",
    "environment_hash_mismatch_current",
}


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    if not path.is_file():
        return None, f"missing_{path.name.replace('.', '_')}"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, f"corrupt_{path.name.replace('.', '_')}"
    if not isinstance(value, dict):
        return None, f"corrupt_{path.name.replace('.', '_')}"
    return value, None


def _get(document: Mapping[str, Any] | None, *keys: str) -> Any:
    value: Any = document
    for key in keys:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _arm_for(config: Mapping[str, Any] | None, spec: Mapping[str, Any]) -> str | None:
    if config is None:
        return None
    policy = config.get("policy")
    for arm, definition in spec.get("arms", {}).items():
        if policy in definition.get("policy_ids", []):
            return str(arm)
    return None


def _candidate_dirs(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    names = ("config.json", "result.json", "events.jsonl")
    return sorted({path.parent for name in names for path in root.rglob(name)})


def _inspect_jsonl(path: Path, expected_run_id: Any) -> tuple[list[dict[str, Any]], list[str]]:
    if not path.is_file():
        return [], [f"missing_{path.name.replace('.', '_')}"]
    flags: list[str] = []
    records: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeError):
        return [], [f"corrupt_{path.name.replace('.', '_')}"]
    for line in lines:
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            flags.append(f"corrupt_{path.name.replace('.', '_')}")
            continue
        if not isinstance(record, dict):
            flags.append(f"corrupt_{path.name.replace('.', '_')}")
            continue
        records.append(record)
        if expected_run_id is not None and record.get("run_id") not in (None, expected_run_id):
            flags.append("event_run_id_mismatch" if path.name == "events.jsonl"
                         else "lifecycle_run_id_mismatch")
    if not records:
        flags.append(f"empty_{path.name.replace('.', '_')}")
    return records, sorted(set(flags))


def _lifecycle_flags(records: Iterable[Mapping[str, Any]]) -> list[str]:
    stages: dict[str, set[str]] = defaultdict(set)
    for record in records:
        action_id = record.get("action_id")
        stage = record.get("stage")
        if isinstance(action_id, str) and isinstance(stage, str):
            stages[action_id].add(stage)
    for action_stages in stages.values():
        if action_stages & {"policy_decision", "executed", "observed"} and "proposed" not in action_stages:
            return ["lifecycle_correlation_failure"]
        if "observed" in action_stages and "executed" not in action_stages:
            return ["lifecycle_correlation_failure"]
    return []


def _first(mapping: Mapping[str, Any], paths: Iterable[tuple[str, ...]]) -> Any:
    for path in paths:
        value = _get(mapping, *path)
        if value is not None:
            return value
    return None


def _violation_details(violations: Any) -> tuple[int, list[str], list[str], list[str]]:
    if not isinstance(violations, list):
        return 0, [], [], []
    methods: set[str] = set()
    paths: set[str] = set()
    types: set[str] = set()
    for item in violations:
        if not isinstance(item, Mapping):
            continue
        method = _first(item, (("method",), ("attributes", "method"),
                               ("event", "method"), ("event", "attributes", "method")))
        path = _first(item, (("path",), ("attributes", "path"),
                             ("event", "path"), ("event", "attributes", "path")))
        kind = _first(item, (("type",), ("category",), ("dimension",), ("code",),
                             ("attributes", "behavior"),
                             ("event", "attributes", "behavior")))
        if method is not None:
            methods.add(str(method).upper())
        if path is not None:
            paths.add(str(path))
        if kind is not None:
            types.add(str(kind))
    return len(violations), sorted(methods), sorted(paths), sorted(types)


def _artifact_digest(run_dir: Path) -> str:
    digest = hashlib.sha256()
    for name in ("config.json", "events.jsonl", "result.json"):
        path = run_dir / name
        digest.update(name.encode())
        if path.is_file():
            digest.update(path.read_bytes())
    return digest.hexdigest()


def _run_row(run_dir: Path, spec: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    config, config_error = _read_json(run_dir / "config.json")
    result, result_error = _read_json(run_dir / "result.json")
    scenario = _get(config, "scenario")
    target_scenario = spec.get("scenario")
    belongs_to_scenario = scenario == target_scenario
    flags = [error for error in (config_error, result_error) if error]
    config_run_id = _get(config, "run_id")
    result_run_id = _get(result, "run_id")
    run_id = config_run_id or result_run_id or run_dir.name

    events, event_flags = _inspect_jsonl(run_dir / "events.jsonl", config_run_id)
    flags.extend(event_flags)
    lifecycle_path = run_dir / "lifecycle.jsonl"
    if lifecycle_path.is_file():
        lifecycle, lifecycle_read_flags = _inspect_jsonl(lifecycle_path, config_run_id)
        flags.extend(lifecycle_read_flags)
        flags.extend(_lifecycle_flags(lifecycle))
    else:
        lifecycle = []
        flags.append("missing_lifecycle_jsonl")

    condition = _arm_for(config, spec)
    if config is not None and belongs_to_scenario and condition is None:
        flags.append("condition_unknown")
    if config_run_id is not None and result_run_id is not None and config_run_id != result_run_id:
        flags.append("config_result_run_id_mismatch")

    validity = _get(result, "validity")
    if not isinstance(validity, Mapping) or not isinstance(validity.get("valid"), bool):
        validity_state = "legacy_unknown"
        validity_reason = None
    elif validity["valid"]:
        validity_state = "valid"
        validity_reason = validity.get("reason")
    else:
        validity_state = "invalid"
        validity_reason = validity.get("reason")

    goal_success = _get(result, "goal", "success")
    roe_compliant = _get(result, "roe", "compliant")
    if not isinstance(goal_success, bool) or not isinstance(roe_compliant, bool):
        flags.append("missing_required_outcome")
    violations = _get(result, "roe", "violations")
    violation_count, methods, paths, types = _violation_details(violations)
    provenance = _get(result, "provenance")
    if not isinstance(provenance, Mapping):
        flags.append("provenance_missing")
    else:
        if not provenance.get("code_commit"):
            flags.append("code_commit_missing")
        if provenance.get("code_dirty") is True:
            flags.append("code_dirty")
        expected_policy_hash = _get(spec, "arms", str(condition), "current_policy_sha256")
        if expected_policy_hash and provenance.get("policy_sha256") != expected_policy_hash:
            flags.append("policy_hash_mismatch_current_arm")
        current_hashes = spec.get("current_artifact_hashes", {})
        if (current_hashes.get("scenario_sha256")
                and provenance.get("scenario_sha256") != current_hashes["scenario_sha256"]):
            flags.append("scenario_hash_mismatch_current")
        if (current_hashes.get("environment_sha256")
                and provenance.get("environment_sha256") != current_hashes["environment_sha256"]):
            flags.append("environment_hash_mismatch_current")
    if _get(config, "model_version") in (None, "", "unknown"):
        flags.append("model_version_unknown")
    flags = sorted(set(flags))
    termination_reason = _get(result, "termination", "reason")
    enforcement_enabled = bool(_get(config, "enforcement_enabled"))
    guardrail_evaluable = termination_reason == "policy_denied" and enforcement_enabled
    fatal_integrity = bool(FATAL_INTEGRITY_FLAGS.intersection(flags))
    performance_eligible = (
        validity_state == "valid" and not fatal_integrity
        and termination_reason != "policy_denied"
    )
    protocol_comparable = performance_eligible and not PROTOCOL_FLAGS.intersection(flags)
    if fatal_integrity:
        exclusion_reason = "artifact_integrity_failure"
    elif termination_reason == "policy_denied":
        exclusion_reason = "policy_denied_guardrail_only"
    elif validity_state == "legacy_unknown":
        exclusion_reason = "legacy_unknown"
    elif validity_state == "invalid":
        exclusion_reason = validity_reason or "invalid_unspecified"
    else:
        exclusion_reason = None
    safe_success = (
        goal_success and roe_compliant
        if isinstance(goal_success, bool) and isinstance(roe_compliant, bool) else None
    )
    row = {
        "artifact_path": run_dir.as_posix(), "artifact_sha256": _artifact_digest(run_dir),
        "run_id": run_id, "config_run_id": config_run_id, "result_run_id": result_run_id,
        "config_scenario": scenario, "config_policy": _get(config, "policy"),
        "condition": condition, "config_model": _get(config, "model"),
        "config_model_version": _get(config, "model_version"),
        "config_provider": _get(config, "provider"),
        "config_temperature": _get(config, "temperature"), "config_seed": _get(config, "seed"),
        "config_repetition": _get(config, "repetition"),
        "config_max_steps": _get(config, "max_steps"), "config_timeout": _get(config, "timeout"),
        "config_started_at": _get(config, "started_at"),
        "enforcement_enabled": enforcement_enabled,
        "provenance_code_commit": _get(provenance, "code_commit"),
        "provenance_code_dirty": _get(provenance, "code_dirty"),
        "provenance_policy_sha256": _get(provenance, "policy_sha256"),
        "provenance_scenario_sha256": _get(provenance, "scenario_sha256"),
        "provenance_environment_sha256": _get(provenance, "environment_sha256"),
        "provenance_target_image_digest": _get(provenance, "target_image_digest"),
        "provenance_started_at": _get(provenance, "started_at"),
        "provenance_finished_at": _get(provenance, "finished_at"),
        "status": _get(result, "status"), "termination_reason": termination_reason,
        "termination_step": _get(result, "termination", "step"),
        "termination_detail": _get(result, "termination", "detail"),
        "validity_state": validity_state, "validity_reason": validity_reason,
        "observer_gateway": _get(result, "observers", "gateway"),
        "observer_database": _get(result, "observers", "database"),
        "goal_success": goal_success, "goal_achieved_step": _get(result, "goal", "achieved_step"),
        "roe_compliant": roe_compliant, "safe_success": safe_success,
        "roe_violation_event_count": violation_count, "violation_methods": methods,
        "violation_paths": paths, "violation_types": types,
        "metrics_steps": _first(result or {}, (("metrics", "steps"), ("metrics", "total_steps"))),
        "events_count": len(events), "lifecycle_count": len(lifecycle),
        "performance_eligible": performance_eligible,
        "protocol_comparable": protocol_comparable,
        "guardrail_evaluable": guardrail_evaluable,
        "exclusion_reason": exclusion_reason, "audit_flags": flags,
    }
    return row, belongs_to_scenario


def _mark_duplicates(rows: list[dict[str, Any]]) -> None:
    for field, flag in (("run_id", "duplicate_run_id"),
                        ("artifact_sha256", "duplicate_artifact")):
        counts = Counter(row[field] for row in rows if row.get(field))
        duplicates = {value for value, count in counts.items() if count > 1}
        for row in rows:
            if row.get(field) in duplicates:
                row["audit_flags"] = sorted(set(row["audit_flags"] + [flag]))
                row["performance_eligible"] = False
                row["protocol_comparable"] = False
                row["exclusion_reason"] = "artifact_integrity_failure"


def _condition_summary(rows: list[dict[str, Any]], spec: Mapping[str, Any]) -> dict[str, Any]:
    summaries: dict[str, Any] = {}
    expected = spec.get("expected_document_counts", {})
    for condition in spec.get("arms", {}):
        attempted = [row for row in rows if row["condition"] == condition]
        eligible = [row for row in attempted if row["performance_eligible"]]
        actual = {
            "attempted": len(attempted), "eligible": len(eligible),
            "goal_success": sum(row["goal_success"] is True for row in eligible),
            "roe_violation": sum(row["roe_compliant"] is False for row in eligible),
            "safe_success": sum(row["safe_success"] is True for row in eligible),
        }
        target = expected.get(condition, {})
        reproduced = bool(target) and all(actual.get(key) == value for key, value in target.items())
        summaries[str(condition)] = {"actual": actual, "documented": target,
                                     "documented_counts_reproduced": reproduced}
    return summaries


def _protocol_variation(rows: list[dict[str, Any]], spec: Mapping[str, Any]) -> dict[str, list[Any]]:
    def ordered(values: set[Any]) -> list[Any]:
        return sorted(
            values,
            key=lambda value: (
                value is None, type(value).__name__,
                value if isinstance(value, (bool, int, float, str)) else repr(value),
            ),
        )

    fields = (
        "config_model", "config_model_version", "config_provider", "config_temperature",
        "config_seed", "config_max_steps", "config_timeout", "provenance_code_commit",
        "provenance_scenario_sha256", "provenance_environment_sha256",
        "provenance_target_image_digest",
    )
    variation = {field: ordered({row.get(field) for row in rows}) for field in fields}
    for condition in spec.get("arms", {}):
        field = f"{condition}.provenance_policy_sha256"
        variation[field] = ordered(
            {row.get("provenance_policy_sha256") for row in rows
             if row.get("condition") == condition},
        )
    return variation


def audit_runs(runs_dir: Path, spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return run-level rows and a conservative eligibility summary."""
    rows: list[dict[str, Any]] = []
    unclassified: list[str] = []
    candidates = _candidate_dirs(runs_dir)
    for run_dir in candidates:
        row, belongs = _run_row(run_dir, spec)
        if belongs:
            rows.append(row)
        elif row["config_scenario"] is None:
            unclassified.append(run_dir.as_posix())
    _mark_duplicates(rows)
    conditions = _condition_summary(rows, spec)
    protocol_variation = _protocol_variation(rows, spec)
    fixed_protocol_fields = {
        "config_model", "config_model_version", "config_provider", "config_temperature",
        "config_max_steps", "config_timeout", "provenance_code_commit",
        "provenance_scenario_sha256", "provenance_environment_sha256",
        "provenance_target_image_digest",
        *(f"{condition}.provenance_policy_sha256" for condition in spec.get("arms", {})),
    }
    mixed_protocol_fields = sorted(
        field for field in fixed_protocol_fields if len(protocol_variation[field]) > 1
    )
    summary = {
        "runs_dir": runs_dir.as_posix(), "runs_dir_exists": runs_dir.is_dir(),
        "candidate_artifact_dirs": len(candidates), "scenario_runs": len(rows),
        "unclassified_artifact_dirs": len(unclassified), "attempted_runs": len(rows),
        "valid_performance_runs": sum(row["performance_eligible"] for row in rows),
        "protocol_comparable_runs": sum(row["protocol_comparable"] for row in rows),
        "invalid_runs": sum(row["validity_state"] == "invalid" for row in rows),
        "legacy_unknown_runs": sum(row["validity_state"] == "legacy_unknown" for row in rows),
        "excluded_runs": sum(not row["performance_eligible"] for row in rows),
        "exclusion_reasons": dict(Counter(
            row["exclusion_reason"] or "none" for row in rows if not row["performance_eligible"]
        )),
        "documented_counts_reproduced": bool(conditions) and all(
            item["documented_counts_reproduced"] for item in conditions.values()
        ),
    }
    return {
        "schema_version": "js001-audit-v1", "summary": summary,
        "conditions": conditions, "protocol_variation": protocol_variation,
        "mixed_protocol_fields": mixed_protocol_fields,
        "unclassified_artifact_paths": unclassified, "rows": rows,
    }


def _csv_value(value: Any) -> Any:
    return json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, (list, dict)) else value


def _write_csv(path: Path, rows: Iterable[Mapping[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=RUN_LEVEL_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: _csv_value(row.get(field)) for field in RUN_LEVEL_FIELDS})


def _markdown_report(audit: Mapping[str, Any]) -> str:
    summary = audit["summary"]
    lines = [
        "# JS-001 Run Artifact Audit", "",
        "## Audit status", "",
        f"- Runs directory exists: `{summary['runs_dir_exists']}` (`{summary['runs_dir']}`)",
        f"- JS-001 artifact directories found: **{summary['scenario_runs']}**",
        f"- Performance-eligible runs: **{summary['valid_performance_runs']}**",
        f"- Excluded runs: **{summary['excluded_runs']}**",
        f"- Legacy unknown runs: **{summary['legacy_unknown_runs']}**",
        f"- Historical 44/49 counts reproduced: **{summary['documented_counts_reproduced']}**", "",
        "## Condition recount", "",
        "| Condition | Attempted | Eligible | Goal | ROE violation | Safe success | Historical reproduced |",
        "|---|---:|---:|---:|---:|---:|:---:|",
    ]
    for condition, item in audit["conditions"].items():
        actual = item["actual"]
        lines.append(
            f"| {condition} | {actual['attempted']} | {actual['eligible']} | "
            f"{actual['goal_success']}/{actual['eligible']} | "
            f"{actual['roe_violation']}/{actual['eligible']} | "
            f"{actual['safe_success']}/{actual['eligible']} | "
            f"{item['documented_counts_reproduced']} |"
        )
    lines.extend(["", "## Interpretation boundary", ""])
    if summary["scenario_runs"] == 0:
        lines.append(
            "No JS-001 raw Run artifacts were available. Documented aggregate values are therefore "
            "historical claims, not verified raw results, and must not be used as final paper results."
        )
    else:
        lines.append(
            "Only rows with explicit `validity.valid == true`, a preregistered condition mapping, "
            "complete binary outcomes, and intact core artifacts enter model-performance denominators."
        )
    lines.extend(["", "`policy_denied` is excluded from intrinsic model-performance denominators and "
                  "retained only as guardrail evidence when enforcement was enabled.", ""])
    return "\n".join(lines)


def write_outputs(audit: Mapping[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "js001_audit.json").write_text(
        json.dumps(audit, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    _write_csv(output_dir / "js001_run_level.csv", audit["rows"])
    _write_csv(
        output_dir / "js001_exclusions.csv",
        (row for row in audit["rows"] if not row["performance_eligible"]),
    )
    (output_dir / "js001_audit_report.md").write_text(
        _markdown_report(audit), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit raw JS-001 Run artifacts")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--spec", type=Path, default=Path("analysis/js001_audit_spec.json"))
    parser.add_argument("--output-dir", type=Path, default=Path("analysis/outputs"))
    args = parser.parse_args()
    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    audit = audit_runs(args.runs_dir, spec)
    write_outputs(audit, args.output_dir)
    print(json.dumps(audit["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())