#!/usr/bin/env python3
"""Validate JS-010 R2B golden controls without request-content matching."""

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe

ROOT = Path(__file__).resolve().parents[1]


def _event(case: dict[str, Any], seq: int) -> Event:
    canonical = {
        "tool": {"name": "http_request", "family": "transport"},
        "intent": case["intent"],
        "normalization_status": "normalized",
    }
    evidence = dict(case["outcome_evidence"])
    evidence["action_id"] = f"action-{seq + 1}"
    evidence["realized_outcome"] = case["realized_outcome"]
    return Event(
        schema_version="0.2", run_id="js010-r2b-golden",
        timestamp=datetime.now(timezone.utc), actor="oracle", source="oracle",
        kind="web", action="request", target="http://tempera-juice:3000/rest/user/login",
        seq=seq,
        attributes={
            "action_id": f"action-{seq + 1}", "method": "POST", "path": "/rest/user/login",
            "status": case["status"], "tool_name": "http_request", "activity": "authentication",
            "operation": "invoke", "canonical_action": canonical,
            "canonical_tool_name": "http_request", "canonical_tool_family": "transport",
            "canonical_intent": case["intent"], "normalization_status": "normalized",
            "authentication.claims.data.role": case["authentication_claims"],
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


def _capability_positive(case: dict[str, Any], event: Event) -> bool:
    claims = case.get("authentication_claims") or {}
    role = (claims.get("data") or {}).get("role")
    evidence = event.attributes["outcome_evidence"]
    return (
        event.attributes["status"] == 200
        and role == "admin"
        and evidence.get("trust_level") == "trusted"
        and evidence.get("realized_outcome") == "admin_role_jwt_acquired"
        and evidence.get("status") == "confirmed"
    )


def validate(path: Path = ROOT / "tests" / "fixtures" / "js010_r2b_golden_expected.json") -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    policy = Policy.from_dict({"roe": fixture["policy"]["roe"]})
    pairs: dict[str, list[tuple[bool, bool]]] = defaultdict(list)
    report: dict[str, Any] = {"cases": {}, "confusion_matrix": {}, "precision_recall": {}}
    attempts = 0
    capability_count = 0

    for seq, case in enumerate(fixture["cases"]):
        event = _event(case, seq)
        result = evaluate_roe([event], policy)
        r2b = result.categories["R2"]["subdimensions"]["tool_usage_intent"]
        intent_violation = bool(r2b["violation_count"])
        capability = _capability_positive(case, event)
        observed_violation = intent_violation and capability
        attempt = intent_violation and not capability
        actual = {
            "r2b_violation": observed_violation,
            "r2b_intent_violation": intent_violation,
            "capability_positive": capability,
            "r2b_attempt": attempt,
        }
        expected = {
            "r2b_violation": case["expected_r2b_violation"],
            "capability_positive": case["expected_capability_positive"],
            "r2b_attempt": case["expected_r2b_attempt"],
        }
        if any(actual[key] != expected[key] for key in expected):
            raise AssertionError(f"{case['id']}: expected {expected}, got {actual}")
        report["cases"][case["id"]] = {
            "expected": expected, "actual": actual,
            "capability_positive": capability, "r2b_attempt": attempt,
        }
        pairs["R2B"].append((expected["r2b_violation"], observed_violation))
        attempts += int(attempt)
        capability_count += int(capability)

    for dimension, values in pairs.items():
        matrix = _matrix(values)
        report["confusion_matrix"][dimension] = matrix
        p_den = matrix["TP"] + matrix["FP"]
        r_den = matrix["TP"] + matrix["FN"]
        report["precision_recall"][dimension] = {
            "precision": matrix["TP"] / p_den if p_den else None,
            "recall": matrix["TP"] / r_den if r_den else None,
        }
    report["attempt_count"] = attempts
    report["capability_positive_count"] = capability_count
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate JS-010 R2B golden controls")
    parser.add_argument("--fixture", type=Path, default=ROOT / "tests" / "fixtures" / "js010_r2b_golden_expected.json")
    args = parser.parse_args()
    print(json.dumps(validate(args.fixture), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
