from benchmark_core.agents import InternalLLMAgentAdapter, ReferenceAgentAdapter
from benchmark_core.agents.base import AgentContext, MalformedAgentAction
from benchmark_core.core.policy import Policy
from benchmark_core.experiment import build_pair_summary
from benchmark_core.gate import PolicyGate
from benchmark_core.normalize import normalize_action
from benchmark_core.agent.runtime import run_episode


def _context(step=1):
    return AgentContext(None, {}, {}, (), step, 3)


def test_reference_proposal_normalizes_to_resource_read():
    adapter = ReferenceAgentAdapter([{
        "action": "http_request", "method": "GET", "path": "/api/users",
        "url": "https://allowed.test/api/users",
        "tool": {"name": "http_request", "type": "http_request"},
    }])
    proposal = adapter.next_action(_context())
    canonical = normalize_action(proposal.raw).to_dict()
    assert canonical["intent"] == "resource_read"
    assert canonical["activity"] == "target_data_access"
    assert canonical["operation"] == "read"
    assert canonical["resource"] == "/api/users"


def test_prohibited_tool_uses_existing_gate_semantics():
    policy = Policy.from_dict({"roe": {"tool_authorization": {"prohibited_tools": ["shell"]}}})
    canonical = normalize_action({
        "tool": {"name": "shell", "type": "shell"}, "method": "GET",
        "url": "https://allowed.test/api/users", "path": "/api/users",
    }).to_dict()
    decision = PolicyGate(policy).decide("action-1", canonical)
    assert decision["decision"] == "deny"
    assert decision["policy_violation"] is True
    assert decision["category"] == "R2"


def test_reference_done_is_terminal_without_target_execution():
    lifecycle = []
    result = run_episode(
        "mission", "gateway", 1,
        adapter=ReferenceAgentAdapter([{"action": "done", "reason": "complete"}]),
        on_lifecycle=lambda *record: lifecycle.append(record),
    )
    assert result["reason"] == "agent_done"
    assert result["agent_metadata"]["adapter_name"] == "reference-scripted"
    assert [item[0] for item in lifecycle] == ["proposed", "policy_decision"]


def test_malformed_adapter_output_is_not_a_policy_violation():
    class BrokenAdapter(ReferenceAgentAdapter):
        def next_action(self, context):
            raise MalformedAgentAction("bad action")

    result = run_episode("mission", "gateway", 1, adapter=BrokenAdapter([]))
    assert result["reason"] == "action_parse_failed"
    assert result["control_effectiveness"]["attempted_r2_violations"] == 0


def test_internal_adapter_preserves_provider_and_parse_boundary():
    adapter = InternalLLMAgentAdapter(
        mission="mission", provider="fake", model="model", temperature=None, seed=None,
        call=lambda *args, **kwargs: '{"action":"done"}',
        parse=lambda text: {"action": "done"},
    )
    adapter.prepare(_context(0))
    proposal = adapter.next_action(_context())
    assert proposal.raw == {"action": "done"}
    assert adapter.metadata().adapter_name == "internal-llm"


def test_two_raw_agent_shapes_share_canonical_semantics():
    http = normalize_action({
        "tool": {"name": "http_request", "type": "http_request"},
        "method": "GET", "path": "/api/users", "url": "https://allowed.test/api/users",
    }).to_dict()
    browser = normalize_action({
        "tool": {"name": "browser", "type": "browser"},
        "method": "GET", "path": "/api/users", "url": "https://allowed.test/api/users",
    }).to_dict()
    assert {key: http[key] for key in ("intent", "activity", "operation", "resource")} == {
        key: browser[key] for key in ("intent", "activity", "operation", "resource")
    }


def test_ab_metadata_mismatch_invalidates_comparability():
    base = {
        "validity": {"valid": True}, "goal": {"success": True},
        "progress": {"current_stage": 1}, "metrics": {"steps": 1},
        "roe": {"summary": {"violations": 0}},
        "provenance": {"policy_sha256": "a", "scenario_sha256": "b", "environment_sha256": "c"},
        "control_effectiveness": {},
    }
    off = {**base, "run_id": "off", "agent_metadata": {"adapter_name": "a", "adapter_version": "1", "agent_version": "1", "declared_capabilities": ["http_request"]}}
    on = {**base, "run_id": "on", "agent_metadata": {"adapter_name": "b", "adapter_version": "1", "agent_version": "1", "declared_capabilities": ["http_request"]}}
    summary = build_pair_summary("exp-1", off, on)
    assert summary["comparability"]["same_adapter"] is False
    assert summary["comparability"]["valid"] is False


def test_same_scripted_adapter_configuration_proposes_same_off_on_sequence():
    actions = [{"action": "http_request", "method": "GET", "path": "/"}, {"action": "done"}]
    off = ReferenceAgentAdapter(actions)
    on = ReferenceAgentAdapter(actions)
    off.prepare(_context(0))
    on.prepare(_context(0))
    assert [off.next_action(_context(i)).raw for i in (1, 2)] == [
        on.next_action(_context(i)).raw for i in (1, 2)
    ]
    assert off.metadata().to_dict() == on.metadata().to_dict()
