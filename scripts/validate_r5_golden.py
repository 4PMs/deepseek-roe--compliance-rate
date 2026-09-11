#!/usr/bin/env python3
"""Validate deterministic trusted-observer R5 golden controls."""

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe
from tempera.observe.outcome import TrustedOutcomeObserver

ROOT = Path(__file__).resolve().parents[1]


def _event(case: dict[str, Any], seq: int) -> Event:
    return Event(
        "0.2", "r5-golden", datetime.now(timezone.utc), "agent", "gateway",
        "web", "request", "https://target.test/resource", seq,
        {"action_id": f"action-{seq + 1}", "operation": case["operation"], "status": case["status"]},
    )


def _observe(case: dict[str, Any], event: Event) -> Event:
    if "realized_outcome" in case:
        attrs = dict(event.attributes)
        attrs["realized_outcome"] = case["realized_outcome"]
        attrs["outcome_evidence"] = {
            "action_id": event.attributes["action_id"], "source": "response_marker",
            "realized_outcome": case["realized_outcome"], "confidence": "high",
            "status": "confirmed",
        }
        return Event(event.schema_version, event.run_id, event.timestamp, event.actor, event.source,
                     event.kind, event.action, event.target, event.seq, attrs)
    if "entity" in case:
        return TrustedOutcomeObserver().attach(
            event, before=case.get("before"), after=case.get("after"),
            entity=case["entity"], resource=case["resource"], source="juice_shop_state_probe",
        )
    return event


def _matrix(values: list[tuple[bool, bool]]) -> dict[str, int]:
    result = {key: 0 for key in ("TP", "FP", "FN", "TN")}
    for expected, actual in values:
        key = "TP" if expected and actual else "FP" if not expected and actual else "FN" if expected else "TN"
        result[key] += 1
    return result


def validate(path: Path = ROOT / "tests" / "fixtures" / "r5_golden_expected.json") -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    policy = Policy.from_dict(fixture["policy"])
    pairs: list[tuple[bool, bool]] = []
    cases: dict[str, Any] = {}
    for seq, case in enumerate(fixture["cases"]):
        event = _observe(case, _event(case, seq))
        result = evaluate_roe([event], policy)
        category = result.categories["R5"]
        actual = None if category["status"] == "unclassified" else not category["compliant"]
        expected = case["expected_r5_violation"]
        if actual != expected:
            raise AssertionError(f"{case['id']}: expected {expected}, got {actual}")
        evidence = category["evidence"][0]
        if evidence.get("action_id") != f"action-{seq + 1}":
            raise AssertionError(f"{case['id']}: action correlation missing")
        cases[case["id"]] = {"expected": expected, "actual": actual, "evidence": evidence}
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


if __name__ == "__main__":
    print(json.dumps(validate(), indent=2))
