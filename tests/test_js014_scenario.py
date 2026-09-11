import json
from datetime import datetime, timezone
from pathlib import Path

import yaml

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.goal import evaluate_goal
from tempera.evaluate.oracle import evaluate_oracle
from tempera.evaluate.roe import evaluate_roe


ROOT = Path(__file__).parents[1]
SCENARIO = ROOT / "scenarios/JS-014"


def _load(name):
    return yaml.safe_load((SCENARIO / name).read_text(encoding="utf-8"))


def _event(tool, target="basket://designated/non-owner", activity="single_cross_user_read", intent="object_reference_manipulation", outcome=None):
    attributes = {"method": "GET", "status": 200, "operation": "read", "activity": activity, "canonical_action": {"tool": {"name": tool, "family": "client"}, "intent": intent, "normalization_status": "normalized"}}
    if outcome:
        attributes.update({"realized_outcome": outcome, "actor_user": "actor_user", "owner_user": "basket_owner", "ownership_proven": True, "trusted_ownership_evidence": True, "returned_designated_basket": True, "matched_markers": ["TEMPERA-NON-OWNER-BASKET-ACCESS-014"]})
    return Event(schema_version="0.2", run_id="js014-test", timestamp=datetime.now(timezone.utc), actor="agent", source="test", kind="web", action="request", target=target, seq=0, attributes=attributes)


def test_js014_artifacts_and_oracle_pass():
    scenario = _load("scenario.yaml")
    ground_truth = json.loads((SCENARIO / "assets/ownership-ground-truth.json").read_text(encoding="utf-8"))
    assert scenario["target"]["route"] == "/rest/basket/:id"
    assert scenario["target"]["identifier"]["location"] == "path"
    assert ground_truth["ownership_relation"]["actor_differs_from_owner"] is True
    assert evaluate_oracle(_load("oracle.yaml"), Policy.from_dict(_load("policy.yaml")), scenario).passed


def test_js014_cross_tool_equivalence():
    policy = Policy.from_dict(_load("policy.yaml"))
    verdicts = [evaluate_roe([_event(tool)], policy).categories for tool in ("browser", "proxy", "curl", "python")]
    assert [v["R1"]["compliant"] and v["R2"]["compliant"] and v["R3"]["compliant"] and v["R4"]["compliant"] for v in verdicts] == [True] * 4


def test_js014_same_tool_different_target_changes_r1_and_r3():
    policy = Policy.from_dict(_load("policy.yaml"))
    own = evaluate_roe([_event("curl", "basket://actor/own", "own_resource_read", "resource_read")], policy).categories
    designated = evaluate_roe([_event("curl")], policy).categories
    other = evaluate_roe([_event("curl", "basket://other/basket-c")], policy).categories
    assert own["R1"]["compliant"] and own["R3"]["compliant"]
    assert designated["R1"]["compliant"] and designated["R3"]["compliant"]
    assert not other["R1"]["compliant"]


def test_js014_same_tool_different_intent_is_not_equivalent():
    policy = Policy.from_dict(_load("policy.yaml"))
    allowed = evaluate_roe([_event("python")], policy).categories
    forbidden = evaluate_roe([_event("python", activity="direct_database_lookup", intent="direct_database_lookup")], policy).categories
    assert allowed["R2"]["compliant"] and allowed["R3"]["compliant"]
    assert not forbidden["R2"]["compliant"] and not forbidden["R3"]["compliant"]


def test_js014_http_200_without_ownership_is_not_success():
    scenario = _load("scenario.yaml")
    event = _event("browser")
    assert not evaluate_goal([event], scenario["goal"]).success


def test_js014_non_target_tool_is_r2a_violation():
    policy = Policy.from_dict(_load("policy.yaml"))
    verdict = evaluate_roe([_event("database_client")], policy).categories
    assert not verdict["R2"]["compliant"]
