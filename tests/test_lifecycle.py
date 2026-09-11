import pytest

from tempera.agent.runtime import run_episode
from tempera.core.lifecycle import LifecycleEvent, validate_lifecycle


def test_lifecycle_schema_correlation_and_order(monkeypatch):
    monkeypatch.setattr(
        "tempera.agent.runtime.call_llm",
        lambda *args, **kwargs: '{"action":"http_request","method":"GET","path":"/"}',
    )
    monkeypatch.setattr(
        "tempera.agent.runtime.do_http",
        lambda action, gateway: "status=200",
    )
    lifecycle = []

    def collect(stage, action_id, step, raw_action, details, normalized_action):
        lifecycle.append(LifecycleEvent.now(
            run_id="run-1", seq=step - 1, action_id=action_id,
            actor="agent", source="runner", stage=stage,
            raw_action=raw_action if stage == "proposed" else None,
            reference=None if stage == "proposed" else {"action_step": step},
            normalized_action=normalized_action,
            decision=(details or {}).get("decision"),
            reason=(details or {}).get("reason"),
        ))

    run_episode("mission", "http://gateway", 1, on_lifecycle=collect)
    lifecycle.append(LifecycleEvent.now(
        run_id="run-1", seq=0, action_id="action-1", actor="agent",
        source="gateway", stage="observed", reference={"event_seq": 0},
        normalized_action={"operation": "read"},
    ))

    records = [event.to_dict() for event in lifecycle]
    assert [record["stage"] for record in records] == [
        "proposed", "policy_decision", "executed", "observed",
    ]
    assert {record["action_id"] for record in records} == {"action-1"}
    assert records[1]["decision"] == "allow"
    assert records[1]["reason"] == "enforcement_not_enabled"
    validate_lifecycle(records)


@pytest.mark.parametrize(
    "stages",
    [
        ["proposed", "policy_decision", "executed"],
        ["proposed", "policy_decision", "executed", "executed", "observed"],
        ["proposed", "executed", "policy_decision", "observed"],
    ],
)
def test_lifecycle_missing_duplicate_or_reordered_stage_fails_closed(stages):
    records = [
        {"action_id": "action-1", "stage": stage}
        for stage in stages
    ]
    with pytest.raises(ValueError):
        validate_lifecycle(records)


def test_denied_action_is_a_valid_terminal_lifecycle_sequence():
    records = [
        {"action_id": "action-1", "stage": "proposed"},
        {"action_id": "action-1", "stage": "policy_decision", "decision": "deny"},
    ]

    validate_lifecycle(records)


def test_synchronous_gateway_observation_before_runner_execution_is_valid():
    records = [
        {"action_id": "action-1", "stage": "proposed"},
        {"action_id": "action-1", "stage": "policy_decision", "decision": "allow"},
        {"action_id": "action-1", "stage": "observed"},
        {"action_id": "action-1", "stage": "executed"},
    ]

    validate_lifecycle(records)
