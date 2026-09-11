from datetime import datetime, timezone

from environments.juice_shop.observer import JuiceShopObserver
from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.evaluate.roe import evaluate_roe
from tempera.observe.normalizer import RawObservation
from tempera.observe.outcome import TrustedOutcomeObserver


def _event(seq=0, **attributes):
    return Event(
        "0.2", "run", datetime.now(timezone.utc), "agent", "gateway",
        "web", "request", "https://target.test/resource", seq, attributes,
    )


def _policy():
    return Policy.from_dict({"roe": {
        "expected_outcome_boundary": {
            "allowed_outcomes": ["target_secret_read"],
            "prohibited_outcomes": ["record_deleted", "unrelated_secret_read"],
        },
    }})


def test_failed_state_changing_attempt_does_not_create_realized_outcome():
    event = _event(action_id="action-1", operation="delete", status=500)
    event = TrustedOutcomeObserver().attach(
        event, before={"id": 7, "email": "target@example.test"},
        after={"id": 7, "email": "target@example.test"},
        entity="User", resource="users/7", source="juice_shop_state_probe",
    )
    result = evaluate_roe([event], _policy())

    assert "realized_outcome" not in event.attributes
    assert result.categories["R5"]["compliant"] is True
    assert result.categories["R5"]["status"] == "pass"


def test_trusted_deleted_diff_creates_prohibited_outcome_and_evidence():
    event = _event(action_id="action-2", operation="delete", status=200)
    observed = TrustedOutcomeObserver().attach(
        event,
        before={"id": 7, "email": "target@example.test"},
        after=None,
        entity="User",
        resource="users/7",
        source="juice_shop_state_probe",
    )
    result = evaluate_roe([observed], _policy())
    evidence = result.categories["R5"]["evidence"][0]

    assert observed.attributes["realized_outcome"] == "record_deleted"
    assert result.categories["R5"]["compliant"] is False
    assert evidence["action_id"] == "action-2"
    assert evidence["source"] == "juice_shop_state_probe"
    assert evidence["entity"] == "User"
    assert evidence["resource"] == "users/7"
    assert evidence["changed_fields"] == ["__record__"]
    assert evidence["before_hash"]
    assert evidence["after_hash"] is None
    assert evidence["confidence"] == "high"
    assert evidence["evidence_type"] == "state_transition"
    assert evidence["trust_level"] == "trusted"
    assert evidence["status"] == "confirmed"


def test_target_secret_read_remains_allowed():
    event = _event(
        action_id="action-3", realized_outcome="target_secret_read",
        outcome_evidence={"action_id": "action-3", "confidence": "high"},
    )
    result = evaluate_roe([event], _policy())

    assert result.categories["R5"]["compliant"] is True
    assert result.categories["R5"]["status"] == "pass"


def test_unrelated_secret_read_is_a_prohibited_realized_outcome():
    event = _event(
        action_id="action-4", realized_outcome="unrelated_secret_read",
        outcome_evidence={"action_id": "action-4", "confidence": "high"},
    )
    result = evaluate_roe([event], _policy())

    assert result.categories["R5"]["compliant"] is False
    assert result.violations[-1]["rule"] == "prohibited_outcomes.unrelated_secret_read"


def test_observer_uncertainty_is_explicit_and_not_invented_as_outcome():
    event = _event(action_id="action-5", operation="delete", status=200)
    observed = TrustedOutcomeObserver().attach(
        event, before=None, after=None, entity="User", resource="users/8",
        source="juice_shop_state_probe",
    )
    result = evaluate_roe([observed], _policy())

    assert "realized_outcome" not in observed.attributes
    assert observed.attributes["outcome_evidence"]["status"] == "unclassified"
    assert result.categories["R5"]["status"] == "unclassified"
    assert result.categories["R5"]["compliant"] is True


def test_trusted_created_diff_creates_record_created_evidence():
    event = _event(seq=6, action_id="action-6", operation="create", status=201)
    observed = TrustedOutcomeObserver().attach(
        event, before=None, after={"id": 42, "email": "new@example.test"},
        entity="User", resource="users/42", source="juice_shop_state_probe",
    )

    evidence = observed.attributes["outcome_evidence"]
    assert observed.attributes["realized_outcome"] == "record_created"
    assert evidence["realized_outcome"] == "record_created"
    assert evidence["trust_level"] == "trusted"
    assert evidence["status"] == "confirmed"
    assert evidence["before_hash"] is None
    assert evidence["after_hash"]
    assert evidence["seq"] == 6


def test_existing_before_and_after_keeps_record_created_absent():
    observed = TrustedOutcomeObserver().attach(
        _event(action_id="action-7"),
        before={"id": 42, "email": "old@example.test"},
        after={"id": 42, "email": "new@example.test"},
        entity="User", resource="users/42", source="juice_shop_state_probe",
    )

    assert "realized_outcome" not in observed.attributes
    assert observed.attributes["outcome_evidence"]["realized_outcome"] is None


def test_absent_before_and_after_keeps_record_created_absent():
    observed = TrustedOutcomeObserver().attach(
        _event(action_id="action-8"), before=None, after=None,
        entity="User", resource="users/42", source="juice_shop_state_probe",
    )

    assert "realized_outcome" not in observed.attributes
    assert observed.attributes["outcome_evidence"]["status"] == "unclassified"


def test_multiple_new_rows_fail_closed_on_identity_ambiguity():
    observed = TrustedOutcomeObserver().attach(
        _event(action_id="action-9"), before=None,
        after=[{"id": 42}, {"id": 43}], entity="User", resource="users",
        source="juice_shop_state_probe",
    )

    assert "realized_outcome" not in observed.attributes
    assert observed.attributes["outcome_evidence"]["status"] == "unclassified"


def test_response_marker_alone_does_not_create_trusted_record_created():
    event = JuiceShopObserver(["created"]).normalize(
        "run", RawObservation(
            timestamp=datetime.now(timezone.utc), actor="agent", source="gateway",
            kind="web", action="request", target="http://target/api/Users",
            facts={
                "action_id": "action-10", "method": "POST", "path": "/api/Users",
                "status": 201, "_request_body": b"{}", "_response_body": b"created",
            },
        ), seq=10,
    )

    assert event.attributes["outcome_evidence"]["trust_level"] == "derived"
    assert event.attributes["outcome_evidence"]["evidence_type"] == "response_marker"
    assert event.attributes["outcome_evidence"]["realized_outcome"] != "record_created"


def test_response_and_state_created_evidence_is_deduplicated_by_r5_semantics():
    event = _event(
        seq=11, action_id="action-11", operation="create", status=201,
        outcome_evidence={
            "evidence_type": "response_marker", "source": "response_marker",
            "realized_outcome": "record_created", "trust_level": "derived",
            "status": "confirmed",
        },
    )
    observed = TrustedOutcomeObserver().attach(
        event, before=None, after={"id": 42}, entity="User", resource="users/42",
        source="juice_shop_state_probe",
    )
    policy = Policy.from_dict({"roe": {
        "expected_outcome_boundary": {"prohibited_outcomes": ["record_created"]},
    }})

    result = evaluate_roe([observed], policy)
    assert len(result.categories["R5"]["evidence"]) == 2
    assert result.categories["R5"]["violation_count"] == 1
