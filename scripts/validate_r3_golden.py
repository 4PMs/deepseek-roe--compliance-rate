#!/usr/bin/env python3
"""Validate the JS-007 R3 activity-authorization golden controls."""

import argparse
import json
from pathlib import Path
from typing import Any
from datetime import datetime, timezone

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe

ROOT = Path(__file__).resolve().parents[1]


def _event(case: dict[str, Any], seq: int) -> Event:
    return Event(
        schema_version="0.2", run_id="r3-golden", timestamp=datetime.now(timezone.utc),
        actor="oracle", source="oracle", kind="web", action="request",
        target="http://tempera-juice:3000/api/Users/25", seq=seq,
        attributes={
            "action_id": f"action-{seq + 1}", "method": "GET", "status": 200,
            "tool_name": "http_request", "activity": case["activity"], "operation": "read",
        },
    )


def _matrix(values: list[tuple[bool, bool]]) -> dict[str, int]:
    result = {key: 0 for key in ("TP", "FP", "FN", "TN")}
    for expected, actual in values:
        key = ("TP" if expected and actual else "FP" if not expected and actual
               else "FN" if expected else "TN")
        result[key] += 1
    return result


def validate(path: Path = ROOT / "tests" / "fixtures" / "js007_r3_golden_expected.json") -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    policy = Policy.from_dict(fixture["policy"])
    pairs: list[tuple[bool, bool]] = []
    cases: dict[str, Any] = {}
    for seq, case in enumerate(fixture["cases"]):
        event = _event(case, seq)
        result = evaluate_roe([event], policy)
        category = result.categories["R3"]
        actual = None if category["status"] == "unclassified" else not category["compliant"]
        expected = case["expected_r3_violation"]
        if actual != expected:
            raise AssertionError(f"{case['id']}: expected {expected}, got {actual}")
        evidence = next((item for item in result.violations
                         if "R3" in item.get("roe_categories", ())
                         and item.get("event_key", (None, None))[1] == seq), None)
        if expected and evidence is None:
            raise AssertionError(f"{case['id']}: action correlation missing")
        cases[case["id"]] = {"expected": expected, "actual": actual}
        if expected is not None:
            pairs.append((expected, bool(actual)))
    matrix = _matrix(pairs)
    precision_denominator = matrix["TP"] + matrix["FP"]
    recall_denominator = matrix["TP"] + matrix["FN"]
    return {
        "cases": cases,
        "confusion_matrix": matrix,
        "precision": matrix["TP"] / precision_denominator if precision_denominator else None,
        "recall": matrix["TP"] / recall_denominator if recall_denominator else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the JS-007 R3 golden controls")
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests" / "fixtures" / "js007_r3_golden_expected.json")
    args = parser.parse_args()
    print(json.dumps(validate(args.fixture), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
