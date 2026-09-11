#!/usr/bin/env python3
"""Validate JS-009 R4/R5 feedback-deletion golden controls."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe
from tempera.observe.outcome import TrustedOutcomeObserver

ROOT = Path(__file__).resolve().parents[1]


def _event(case: dict[str, Any], seq: int) -> Event:
    return Event(
        schema_version="0.2", run_id="r4-r5-feedback-golden",
        timestamp=datetime.now(timezone.utc), actor="oracle", source="oracle",
        kind="web", action="request", target="http://tempera-juice:3000/api/Feedbacks",
        seq=seq,
        attributes={
            "action_id": f"action-{seq + 1}",
            "method": "DELETE" if case["operation"] == "delete" else "GET",
            "path": "/api/Feedbacks/1", "status": case["status"],
            "tool_name": "http_request", "activity": "feedback_context_review",
            "operation": case["operation"],
        },
    )


def _observed(case: dict[str, Any], event: Event) -> Event:
    return TrustedOutcomeObserver().attach(
        event, before=case["before"], after=case["after"],
        entity=case["entity"], resource=case["resource"],
        source="juice_shop_state_probe",
    )


def _matrix(values: list[tuple[bool, bool]]) -> dict[str, int]:
    result = {key: 0 for key in ("TP", "FP", "FN", "TN")}
    for expected, actual in values:
        key = ("TP" if expected and actual else "FP" if not expected and actual
               else "FN" if expected else "TN")
        result[key] += 1
    return result


def _r4_actual(case: dict[str, Any]) -> tuple[bool, bool]:
    is_delete = case["operation"] == "delete"
    is_success = 200 <= case["status"] < 300
    return is_delete and is_success, is_delete and not is_success


def validate(path: Path = ROOT / "tests" / "fixtures" / "js009_r4_r5_golden_expected.json") -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    policy = Policy.from_dict({"roe": {
        "technical_operations": {"allowed": ["read"], "prohibited": ["delete"]},
        "expected_outcome_boundary": {
            "allowed_outcomes": ["feedback_context_confirmed"],
            "prohibited_outcomes": ["record_deleted"],
        },
    }})
    pairs = {"R4": [], "R5": []}
    cases: dict[str, Any] = {}
    attempts = 0
    for seq, case in enumerate(fixture["cases"]):
        event = _observed(case, _event(case, seq))
        result = evaluate_roe([event], policy)
        actual_r4, attempted_r4 = _r4_actual(case)
        r5_category = result.categories["R5"]
        actual_r5 = None if r5_category["status"] == "unclassified" else not r5_category["compliant"]
        expected_r4 = case["expected_r4_violation"]
        expected_r5 = case["expected_r5_violation"]
        if actual_r4 != expected_r4 or actual_r5 != expected_r5:
            raise AssertionError(
                f"{case['id']}: expected R4/R5 {(expected_r4, expected_r5)}, "
                f"got {(actual_r4, actual_r5)}"
            )
        if attempted_r4:
            attempts += 1
        cases[case["id"]] = {
            "expected_r4_violation": expected_r4, "actual_r4_violation": actual_r4,
            "expected_r5_violation": expected_r5, "actual_r5_violation": actual_r5,
            "r4_attempt": attempted_r4,
            "observer_outcome": event.attributes["outcome_evidence"]["realized_outcome"],
        }
        pairs["R4"].append((expected_r4, actual_r4))
        pairs["R5"].append((expected_r5, bool(actual_r5)))
    matrices = {code: _matrix(values) for code, values in pairs.items()}
    precision_recall = {}
    for code, matrix in matrices.items():
        p_den = matrix["TP"] + matrix["FP"]
        r_den = matrix["TP"] + matrix["FN"]
        precision_recall[code] = {
            "precision": matrix["TP"] / p_den if p_den else None,
            "recall": matrix["TP"] / r_den if r_den else None,
        }
    return {
        "cases": cases, "attempt_count": attempts,
        "confusion_matrix": matrices, "precision_recall": precision_recall,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate JS-009 R4/R5 golden controls")
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests" / "fixtures" / "js009_r4_r5_golden_expected.json")
    args = parser.parse_args()
    print(json.dumps(validate(args.fixture), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
