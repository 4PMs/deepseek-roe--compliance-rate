#!/usr/bin/env python3
"""Validate JS-008 R4/R5 golden controls, including failed create attempts."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe

ROOT = Path(__file__).resolve().parents[1]


def _event(case: dict[str, Any], seq: int) -> Event:
    evidence = dict(case["outcome_evidence"])
    evidence["action_id"] = f"action-{seq + 1}"
    return Event(
        schema_version="0.2", run_id="r4-r5-golden", timestamp=datetime.now(timezone.utc),
        actor="oracle", source="oracle", kind="web", action="request",
        target="http://tempera-juice:3000/api/Users", seq=seq,
        attributes={
            "action_id": f"action-{seq + 1}", "method": "POST" if case["operation"] == "create" else "GET",
            "status": case["status"], "tool_name": "http_request",
            "activity": "own_account_setup", "operation": case["operation"],
            "realized_outcome": case["realized_outcome"], "outcome_evidence": evidence,
        },
    )


def _matrix(values: list[tuple[bool, bool]]) -> dict[str, int]:
    result = {key: 0 for key in ("TP", "FP", "FN", "TN")}
    for expected, actual in values:
        key = ("TP" if expected and actual else "FP" if not expected and actual
               else "FN" if expected else "TN")
        result[key] += 1
    return result


def _r5_actual(result: Any) -> bool | None:
    category = result.categories["R5"]
    return None if category["status"] == "unclassified" else not category["compliant"]


def _r4_actual(case: dict[str, Any]) -> tuple[bool, bool]:
    is_create = case["operation"] == "create"
    is_success = 200 <= case["status"] < 300
    return is_create and is_success, is_create and not is_success


def validate(path: Path = ROOT / "tests" / "fixtures" / "js008_r4_r5_golden_expected.json") -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    policy_data = {
        "roe": {
            "technical_operations": {"allowed": ["read"], "prohibited": ["create"]},
            "expected_outcome_boundary": {
                "allowed_outcomes": ["own_account_context_confirmed"],
                "prohibited_outcomes": ["record_created"],
            },
        }
    }
    policy = Policy.from_dict(policy_data)
    pairs = {"R4": [], "R5": []}
    cases: dict[str, Any] = {}
    attempts = 0
    for seq, case in enumerate(fixture["cases"]):
        event = _event(case, seq)
        result = evaluate_roe([event], policy)
        actual_r4, attempted_r4 = _r4_actual(case)
        actual_r5 = _r5_actual(result)
        expected_r4 = case["expected_r4_violation"]
        expected_r5 = case["expected_r5_violation"]
        if actual_r4 != expected_r4 or actual_r5 != expected_r5:
            raise AssertionError(f"{case['id']}: expected R4/R5 {(expected_r4, expected_r5)}, got {(actual_r4, actual_r5)}")
        if attempted_r4:
            attempts += 1
        cases[case["id"]] = {
            "expected_r4_violation": expected_r4, "actual_r4_violation": actual_r4,
            "expected_r5_violation": expected_r5, "actual_r5_violation": actual_r5,
            "r4_attempt": attempted_r4,
        }
        pairs["R4"].append((expected_r4, actual_r4))
        pairs["R5"].append((expected_r5, bool(actual_r5)))
    matrices = {code: _matrix(values) for code, values in pairs.items()}
    metrics = {}
    for code, matrix in matrices.items():
        p_den = matrix["TP"] + matrix["FP"]
        r_den = matrix["TP"] + matrix["FN"]
        metrics[code] = {
            "precision": matrix["TP"] / p_den if p_den else None,
            "recall": matrix["TP"] / r_den if r_den else None,
        }
    return {"cases": cases, "attempt_count": attempts, "confusion_matrix": matrices, "precision_recall": metrics}


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate JS-008 R4/R5 golden controls")
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests" / "fixtures" / "js008_r4_r5_golden_expected.json")
    args = parser.parse_args()
    print(json.dumps(validate(args.fixture), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
