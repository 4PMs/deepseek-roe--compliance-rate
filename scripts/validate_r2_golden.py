#!/usr/bin/env python3
"""Validate deterministic R2A/R2B golden controls and confusion matrices."""

import json
from collections import defaultdict
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe

ROOT = Path(__file__).resolve().parents[1]


def _event(case: dict[str, Any], seq: int) -> Event:
    tool = case["tool"]
    intent = case.get("intent")
    canonical = {
        "tool": {"name": tool, "family": case["family"]},
        "intent": intent,
        "normalization_status": case.get("normalization_status", "normalized"),
    }
    return Event(
        schema_version="0.2", run_id="r2-golden", timestamp=datetime.now(timezone.utc),
        actor="oracle", source="oracle", kind="web", action="request",
        target="https://allowed.test", seq=seq,
        attributes={
            "action_id": f"action-{seq + 1}", "tool_name": tool,
            "tool_family": case["family"], "intent": intent,
            "activity": case["activity"], "canonical_action": canonical,
        },
    )


def _actual(result: Any) -> dict[str, bool | None]:
    r2 = result.categories["R2"]
    r3 = result.categories["R3"]
    def sub(name: str) -> bool | None:
        item = r2["subdimensions"][name]
        if item["unclassified_count"]:
            return None
        return not bool(item["violation_count"])
    r2a, r2b = sub("tool_authorization"), sub("tool_usage_intent")
    return {
        "R2A": None if r2a is None else not r2a,
        "R2B": None if r2b is None else not r2b,
        "R2": None if r2a is None or r2b is None else not r2["compliant"],
        "R3": not r3["compliant"],
    }


def _matrix(pairs: list[tuple[bool, bool]]) -> dict[str, int]:
    counts = {key: 0 for key in ("TP", "FP", "FN", "TN")}
    for expected, actual in pairs:
        key = "TP" if expected and actual else "FP" if not expected and actual else "FN" if expected else "TN"
        counts[key] += 1
    return counts


def validate(path: Path = ROOT / "tests" / "fixtures" / "r2_golden_expected.json") -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    policy_data = fixture["policy"]
    policy = Policy.from_dict({"roe": policy_data["roe"]})
    report: dict[str, Any] = {"cases": {}, "confusion_matrix": {}, "precision_recall": {}}
    pairs: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    for seq, case in enumerate(fixture["cases"]):
        result = evaluate_roe([_event(case, seq)], policy)
        actual = _actual(result)
        expected = case["expected"]
        if actual != expected:
            raise AssertionError(f"{case['id']}: expected {expected}, got {actual}")
        evidence = result.categories["R2"]["evidence"][0]
        if evidence["action_id"] != f"action-{seq + 1}":
            raise AssertionError(f"{case['id']}: lifecycle correlation missing")
        report["cases"][case["id"]] = {"expected": expected, "actual": actual}
        for dimension in ("R2A", "R2B", "R2"):
            if expected[dimension] is not None:
                pairs[dimension].append((expected[dimension], bool(actual[dimension])))
    for dimension, values in pairs.items():
        matrix = _matrix(values)
        report["confusion_matrix"][dimension] = matrix
        denominator = matrix["TP"] + matrix["FP"]
        recall_denominator = matrix["TP"] + matrix["FN"]
        report["precision_recall"][dimension] = {
            "precision": matrix["TP"] / denominator if denominator else None,
            "recall": matrix["TP"] / recall_denominator if recall_denominator else None,
        }
    return report


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
