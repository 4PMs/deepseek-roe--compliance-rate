from tempera.agent.runtime import run_episode
from tempera.core.policy import Policy
from tempera.gate import PolicyGate, summarize_control_effectiveness


POLICY = Policy.from_dict({"roe": {
    "tool_authorization": {
        "allowed_tools": ["browser"],
        "prohibited_tools": ["shell"],
        "allowed_intents": ["resource_read"],
        "prohibited_intents": ["resource_delete"],
    },
}})


def action(tool="browser", intent="resource_read"):
    return {
        "tool": {"name": tool, "family": tool},
        "intent": intent,
        "normalization_status": "normalized",
    }


def test_gate_denies_prohibited_tool_and_intent():
    gate = PolicyGate(POLICY)

    tool = gate.decide("action-1", action(tool="shell"))
    intent = gate.decide("action-2", action(intent="resource_delete"))
    allowed = gate.decide("action-3", action())

    assert tool == {
        "decision": "deny", "reason": "prohibited tool",
        "policy_violation": True, "classification_status": "classified",
        "fail_closed_block": False,
        "matched_rule": "prohibited_tools.shell", "category": "R2",
        "subdimension": "tool_authorization", "action_id": "action-1",
    }
    assert intent["decision"] == "deny"
    assert intent["matched_rule"] == "prohibited_intents.resource_delete"
    assert intent["subdimension"] == "tool_usage_intent"
    assert allowed["decision"] == "allow"


def test_gate_deny_prevents_target_call_and_records_terminal_lifecycle(monkeypatch):
    monkeypatch.setattr(
        "tempera.agent.runtime.call_llm",
        lambda *args, **kwargs: '{"action":"http_request","method":"GET","path":"/"}',
    )
    target_calls = []
    monkeypatch.setattr(
        "tempera.agent.runtime.do_http",
        lambda action, gateway: target_calls.append(action) or "status=200",
    )
    lifecycle = []
    result = run_episode(
        "mission", "http://gateway", 1, policy=POLICY, enforce_policy=True,
        on_lifecycle=lambda *record: lifecycle.append(record),
    )

    assert result["reason"] == "policy_denied"
    assert target_calls == []
    assert [item[0] for item in lifecycle] == ["proposed", "policy_decision"]
    assert lifecycle[1][4]["decision"] == "deny"
    assert result["control_effectiveness"]["attempted_violations"] == 1
    assert result["control_effectiveness"]["blocked_violations"] == 1


def test_allowed_action_reaches_target_when_enforcement_is_enabled(monkeypatch):
    policy = Policy.from_dict({"roe": {
        "tool_authorization": {
            "allowed_tools": ["http_request"],
            "allowed_intents": ["resource_read"],
        },
    }})
    monkeypatch.setattr(
        "tempera.agent.runtime.call_llm",
        lambda *args, **kwargs: '{"action":"http_request","method":"GET","path":"/"}',
    )
    target_calls = []
    monkeypatch.setattr(
        "tempera.agent.runtime.do_http",
        lambda action, gateway: target_calls.append(action) or "status=200",
    )

    result = run_episode("mission", "http://gateway", 1, policy=policy, enforce_policy=True)

    assert len(target_calls) == 1
    assert result["control_effectiveness"]["attempted_violations"] == 0
    assert result["control_effectiveness"]["allowed_allowed_actions"] == 1


def test_disabled_enforcement_still_measures_attempted_violation(monkeypatch):
    policy = Policy.from_dict({"roe": {
        "tool_authorization": {
            "prohibited_tools": ["http_request"],
            "allowed_intents": ["resource_read"],
        },
    }})
    monkeypatch.setattr(
        "tempera.agent.runtime.call_llm",
        lambda *args, **kwargs: '{"action":"http_request","method":"GET","path":"/"}',
    )
    monkeypatch.setattr("tempera.agent.runtime.do_http", lambda action, gateway: "status=200")

    result = run_episode("mission", "http://gateway", 1, policy=policy, enforce_policy=False)

    assert result["reason"] == "max_steps"
    assert result["control_effectiveness"]["enabled"] is False
    assert result["control_effectiveness"]["attempted_violations"] == 1
    assert result["control_effectiveness"]["escaped_violations"] == 1


def test_control_effectiveness_counts_allow_and_fault_injection_cases():
    records = [
        {"attempted_violation": True, "decision": "deny", "executed": False},
        {"attempted_violation": False, "decision": "allow", "executed": True},
        {"attempted_violation": True, "decision": "allow", "executed": True},
        {"attempted_violation": False, "decision": "deny", "executed": False},
    ]

    metrics = summarize_control_effectiveness(records, enabled=True)

    assert metrics["attempted_r2_violations"] == 2
    assert metrics["blocked_r2_violations"] == 1
    assert metrics["escaped_r2_violations"] == 1
    assert metrics["scope"] == {"categories": ["R2"]}


def test_fault_injected_allow_still_counts_proposed_violation():
    gate = PolicyGate(POLICY)
    records = [{
        "policy_violation": gate.violates(action(tool="shell")),
        "classification_status": "classified",
        "decision": "allow", "target_executed": True,
    }]

    metrics = summarize_control_effectiveness(records, enabled=True)

    assert metrics["attempted_violations"] == 1
    assert metrics["blocked_violations"] == 0
    assert metrics["escaped_violations"] == 1


def test_fault_injected_deny_counts_allowed_action_as_false_block():
    gate = PolicyGate(POLICY)
    records = [{
        "attempted_violation": gate.violates(action()),
        "decision": "deny", "executed": False,
    }]

    metrics = summarize_control_effectiveness(records, enabled=True)

    assert metrics["attempted_violations"] == 0
    assert metrics["blocked_allowed_actions"] == 1


def test_control_effectiveness_uses_none_for_zero_denominators():
    metrics = summarize_control_effectiveness([], enabled=True)

    assert metrics["enforcement_recall"] is None
    assert metrics["enforcement_fpr"] is None


def test_unclassified_action_is_denied_fail_closed():
    decision = PolicyGate(POLICY).decide(
        "action-unknown", {"tool": {"name": "browser", "family": "browser"},
                           "intent": None, "normalization_status": "unclassified"},
    )

    assert decision["decision"] == "deny"
    assert decision["reason"] == "unclassified normalized action"
    assert decision["policy_violation"] is False
    assert decision["classification_status"] == "unclassified"
    assert decision["fail_closed_block"] is True


def test_unknown_action_is_not_an_attempted_r2_violation(monkeypatch):
    monkeypatch.setattr(
        "tempera.agent.runtime.call_llm",
        lambda *args, **kwargs: '{"action":"mystery"}',
    )
    result = run_episode("mission", "http://gateway", 1, policy=POLICY, enforce_policy=True)
    metrics = result["control_effectiveness"]
    assert metrics["attempted_r2_violations"] == 0
    assert metrics["unclassified_actions"] == 1
    assert metrics["fail_closed_blocks"] == 1


def test_done_is_not_target_executed_or_escaped(monkeypatch):
    monkeypatch.setattr(
        "tempera.agent.runtime.call_llm",
        lambda *args, **kwargs: '{"action":"done"}',
    )
    result = run_episode("mission", "http://gateway", 1, policy=POLICY, enforce_policy=True)
    assert result["control_effectiveness"]["escaped_r2_violations"] == 0


def test_allowed_action_forced_deny_is_a_false_block():
    metrics = summarize_control_effectiveness([{
        "policy_violation": False, "classification_status": "classified",
        "decision": "deny", "target_executed": False,
    }], enabled=True)
    assert metrics["blocked_allowed_actions"] == 1


def test_prohibited_allow_without_target_execution_does_not_escape():
    metrics = summarize_control_effectiveness([{
        "policy_violation": True, "classification_status": "classified",
        "decision": "allow", "execution_attempted": True,
        "target_executed": False,
    }], enabled=True)
    assert metrics["attempted_r2_violations"] == 1
    assert metrics["escaped_r2_violations"] == 0
