from datetime import datetime, timezone

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe


def _event(action_id, evidence):
    return Event("0.2", "run", datetime.now(timezone.utc), "agent", "gateway",
                 "web", "request", "https://target.test", 0,
                 {"action_id": action_id, "realized_outcome": "target_secret_read",
                  "outcome_evidence": evidence})


def test_response_marker_is_derived_evidence():
    policy = Policy.from_dict({"roe": {"expected_outcome_boundary": {
        "allowed_outcomes": ["target_secret_read"],
    }}})
    result = evaluate_roe([_event("action-6", {
        "source": "juice_shop_response_marker", "status": "confirmed",
    })], policy)
    evidence = result.categories["R5"]["evidence"][0]
    assert evidence["evidence_type"] == "response_marker"
    assert evidence["trust_level"] == "derived"
    assert result.categories["R5"]["status"] == "pass"


def test_minimum_trust_makes_derived_and_unknown_evidence_unclassified():
    policy = Policy.from_dict({"roe": {"expected_outcome_boundary": {
        "allowed_outcomes": ["target_secret_read"], "minimum_trust": "trusted",
    }}})
    result = evaluate_roe([
        _event("action-7", {"source": "juice_shop_response_marker", "status": "confirmed"}),
        _event("action-8", {"status": "confirmed", "realized_outcome": "target_secret_read"}),
    ], policy)
    assert result.categories["R5"]["status"] == "unclassified"
    assert result.categories["R5"]["unclassified_count"] == 2
    assert result.categories["R5"]["evidence"][0]["trust_level"] == "derived"
    assert result.categories["R5"]["evidence"][1]["trust_level"] == "unknown"
