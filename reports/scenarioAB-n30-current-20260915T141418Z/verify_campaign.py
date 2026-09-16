#!/usr/bin/env python3
"""Verify and independently aggregate the completed Scenario A/B campaign."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from benchmark_core.core.bundle import validate_run

REPORT_DIR = Path(__file__).resolve().parent
RUNS_DIR = REPORT_DIR / "runs"
RUN_ID_RE = re.compile(
    rf"^{re.escape(REPORT_DIR.name)}-(scenarioA|scenarioB)-(neutral|read|modify|delete)-r(\d{{2}})$"
)
SCENARIOS = ("scenarioA", "scenarioB")
CONDITIONS = ("neutral", "read", "modify", "delete")
REQUIRED_FILES = (
    "config.json",
    "provenance.json",
    "result.json",
    "trace.jsonl",
    "events.jsonl",
    "lifecycle.jsonl",
)


def counter_dict(counter: Counter[Any]) -> dict[str, int]:
    return {
        str(key): value for key, value in sorted(counter.items(), key=lambda item: str(item[0]))
    }


def main() -> None:
    expected = {
        (scenario, condition, repetition)
        for scenario in SCENARIOS
        for condition in CONDITIONS
        for repetition in range(1, 31)
    }
    observed: set[tuple[str, str, int]] = set()
    duplicate_slots: list[str] = []
    missing_files: Counter[str] = Counter()
    validity: Counter[str] = Counter()
    observer_states: Counter[str] = Counter()
    source_commits: Counter[str] = Counter()
    groups: dict[str, dict[str, Any]] = {}
    bundle_errors: dict[str, list[str]] = {}

    for scenario in SCENARIOS:
        for condition in CONDITIONS:
            groups[f"{scenario}/{condition}"] = {
                "runs": 0,
                "goal_success": 0,
                "roe_compliant": 0,
                "roe_violation": 0,
                "categories": {
                    category: Counter() for category in ("R1", "R2", "R3", "R4", "R5", "R6")
                },
                "outcomes": Counter(),
                "halt": Counter(),
                "safe_response_chain": {},
                "trajectory_endpoints": {},
                "termination_reasons": Counter(),
            }

    result_paths = sorted(RUNS_DIR.glob("*/result.json"))
    for result_path in result_paths:
        run_dir = result_path.parent
        match = RUN_ID_RE.fullmatch(run_dir.name)
        if match is None:
            raise ValueError(f"unexpected run directory: {run_dir.name}")
        scenario, condition, repetition_text = match.groups()
        slot = (scenario, condition, int(repetition_text))
        if slot in observed:
            duplicate_slots.append(run_dir.name)
        observed.add(slot)

        for filename in REQUIRED_FILES:
            if not (run_dir / filename).is_file():
                missing_files[filename] += 1

        bundle_report = validate_run(run_dir)
        if not bundle_report["valid"]:
            bundle_errors[run_dir.name] = bundle_report["errors"]

        result = json.loads(result_path.read_text(encoding="utf-8"))
        result_validity = result.get("validity") or {}
        validity[f"{result_validity.get('valid')}:{result_validity.get('reason')}"] += 1
        observers = result.get("observers") or {}
        observer_states[
            f"gateway={observers.get('gateway')},database={observers.get('database')}"
        ] += 1
        provenance = result.get("provenance") or {}
        source_commits[str(provenance.get("code_commit"))] += 1

        group = groups[f"{scenario}/{condition}"]
        group["runs"] += 1
        group["goal_success"] += int(bool((result.get("goal") or {}).get("success")))
        compliant = bool((result.get("roe") or {}).get("compliant"))
        group["roe_compliant"] += int(compliant)
        group["roe_violation"] += int(not compliant)
        category_details = (result.get("roe") or {}).get("categories") or {}
        for category in ("R1", "R2", "R3", "R4", "R5", "R6"):
            detail = category_details.get(category)
            status = (detail or {}).get("status") if isinstance(detail, dict) else "missing"
            group["categories"][category][str(status)] += 1
        group["outcomes"][str(result.get("outcome"))] += 1
        group["halt"][str((result.get("halt") or {}).get("status"))] += 1
        group["termination_reasons"][str((result.get("termination") or {}).get("reason"))] += 1
        for field, status in (result.get("safe_response_chain") or {}).items():
            group["safe_response_chain"].setdefault(field, Counter())[str(status)] += 1
        endpoints = (result.get("trajectory") or {}).get("endpoints") or {}
        for endpoint, detail in endpoints.items():
            group["trajectory_endpoints"].setdefault(endpoint, Counter())[
                str((detail or {}).get("status"))
            ] += 1

    for group in groups.values():
        group["categories"] = {
            key: counter_dict(value) for key, value in group["categories"].items()
        }
        group["outcomes"] = counter_dict(group["outcomes"])
        group["halt"] = counter_dict(group["halt"])
        group["termination_reasons"] = counter_dict(group["termination_reasons"])
        group["safe_response_chain"] = {
            key: counter_dict(value) for key, value in group["safe_response_chain"].items()
        }
        group["trajectory_endpoints"] = {
            key: counter_dict(value) for key, value in group["trajectory_endpoints"].items()
        }

    manifest = json.loads((REPORT_DIR / "batch_manifest.json").read_text(encoding="utf-8"))
    summary = json.loads((REPORT_DIR / "summary.json").read_text(encoding="utf-8"))
    manifest_ids = [record.get("run_id") for record in manifest.get("records", [])]
    summary_mismatches: list[str] = []
    for key, group in groups.items():
        persisted = (summary.get("groups") or {}).get(key) or {}
        comparisons = {
            "result_count": group["runs"],
            "valid": group["runs"],
            "roe_violation_runs": group["roe_violation"],
            "roe_compliant_runs": group["roe_compliant"],
            "safe_response_outcomes": group["outcomes"],
        }
        for field, calculated in comparisons.items():
            if persisted.get(field) != calculated:
                summary_mismatches.append(
                    f"{key}.{field}: persisted={persisted.get(field)!r}, calculated={calculated!r}"
                )

    report = {
        "campaign": REPORT_DIR.name,
        "verified_complete": (
            len(result_paths) == 240
            and observed == expected
            and not duplicate_slots
            and not missing_files
            and not bundle_errors
            and validity == Counter({"True:None": 240})
            and observer_states == Counter({"gateway=ok,database=ok": 240})
            and len(manifest_ids) == 240
            and len(set(manifest_ids)) == 240
            and not summary_mismatches
        ),
        "artifact_count": len(result_paths),
        "expected_slots": len(expected),
        "missing_slots": [f"{s}/{c}/r{r:02d}" for s, c, r in sorted(expected - observed)],
        "unexpected_slots": [f"{s}/{c}/r{r:02d}" for s, c, r in sorted(observed - expected)],
        "duplicate_slots": duplicate_slots,
        "missing_required_files": counter_dict(missing_files),
        "bundle_validation": {
            "valid": len(result_paths) - len(bundle_errors),
            "invalid": len(bundle_errors),
        },
        "bundle_errors": bundle_errors,
        "validity": counter_dict(validity),
        "observers": counter_dict(observer_states),
        "source_commits": counter_dict(source_commits),
        "manifest": {
            "requested_runs": manifest.get("requested_runs"),
            "records": len(manifest_ids),
            "unique_run_ids": len(set(manifest_ids)),
            "failed_processes": sum(
                1 for record in manifest.get("records", []) if record.get("returncode") != 0
            ),
        },
        "summary_mismatches": summary_mismatches,
        "groups": groups,
    }
    destination = REPORT_DIR / "verified_summary.json"
    destination.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "verified_complete": report["verified_complete"],
                "artifact_count": report["artifact_count"],
                "bundle_validation": report["bundle_validation"],
                "summary_mismatches": len(summary_mismatches),
                "output": str(destination),
            }
        )
    )


if __name__ == "__main__":
    main()
