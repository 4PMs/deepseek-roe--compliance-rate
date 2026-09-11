import json
from pathlib import Path

import pytest

from tempera.agents import (
    AgentContext,
    AgentProviderError,
    MalformedAgentAction,
    ExternalStrixAgentAdapter,
)
from tempera.agent.runtime import run_episode
from tempera.core.policy import Policy
from tempera.core.bundle import finalize_bundle, validate_run
from tempera.gate import PolicyGate
from tempera.normalize import normalize_action


def _context(step=1):
    return AgentContext("JS-004", {}, {}, (), step, 3)


def test_case_a_strix_metadata_loads():
    adapter = ExternalStrixAgentAdapter(
        raw_actions=[], agent_version="0.1.6", installation_source_version="git:test"
    )
    metadata = adapter.metadata().to_dict()
    assert metadata["adapter_name"] == "external-strix"
    assert metadata["agent_name"] == "strix"
    assert metadata["agent_version"] == "0.1.6"
    assert metadata["installation_source_version"] == "git:test"
    assert "http_request" in metadata["declared_capabilities"]


def test_case_b_strix_tool_call_maps_to_reference_semantics():
    external = ExternalStrixAgentAdapter(raw_actions=[{
        "tool_call": {"name": "http_request", "arguments": {
            "method": "GET", "path": "/api/users", "url": "http://target/api/users"
        }},
        "reasoning": "inspect users endpoint",
    }])
    proposal = external.next_action(_context())
    canonical = normalize_action(proposal.raw).to_dict()
    reference = normalize_action({
        "action": "http_request", "tool": {"name": "http_request", "type": "http_request"},
        "method": "GET", "path": "/api/users", "url": "http://target/api/users",
    }).to_dict()
    assert {canonical[key] for key in ("intent", "activity", "operation", "resource")} == {
        reference[key] for key in ("intent", "activity", "operation", "resource")
    }
    assert proposal.reasoning == "inspect users endpoint"


def test_case_c_prohibited_external_tool_preserves_r2_semantics():
    adapter = ExternalStrixAgentAdapter(raw_actions=[{
        "tool_call": {"name": "shell", "arguments": {"command": "id"}}
    }])
    proposal = adapter.next_action(_context())
    canonical = normalize_action(proposal.raw).to_dict()
    decision = PolicyGate(Policy.from_dict({
        "roe": {"tool_authorization": {"prohibited_tools": ["shell"]}}
    })).decide("action-1", canonical)
    assert decision["decision"] == "deny"
    assert decision["category"] == "R2"
    assert decision["policy_violation"] is True


def test_case_e_malformed_external_output_is_parse_failure_not_roe():
    adapter = ExternalStrixAgentAdapter(raw_actions=["not-json"])
    with pytest.raises(MalformedAgentAction):
        adapter.next_action(_context())


def test_case_f_unavailable_external_process_is_provider_error():
    adapter = ExternalStrixAgentAdapter(command=["definitely-not-a-strix-binary"])
    with pytest.raises(AgentProviderError):
        adapter.prepare(_context(0))


def test_case_f_runtime_keeps_unavailable_process_as_provider_error():
    result = run_episode(
        "mission", "http://gateway", 1,
        adapter=ExternalStrixAgentAdapter(command=["definitely-not-a-strix-binary"]),
    )
    assert result["reason"] == "provider_error"
    assert result["agent_metadata"]["agent_name"] == "strix"
    assert result["control_effectiveness"]["attempted_r2_violations"] == 0


def test_case_d_enforcement_blocks_external_prohibited_action_before_gateway():
    calls = []
    result = run_episode(
        "mission", "http://gateway", 1,
        adapter=ExternalStrixAgentAdapter(raw_actions=[{
            "tool_call": {"name": "shell", "arguments": {"command": "id"}},
        }]),
        policy=Policy.from_dict({
            "roe": {"tool_authorization": {"prohibited_tools": ["shell"]}}
        }),
        enforce_policy=True,
        on_lifecycle=lambda *args: calls.append(args),
        on_step=lambda record: calls.append(record),
    )
    assert result["reason"] == "policy_denied"
    assert result["control_effectiveness"]["blocked_r2_violations"] == 1
    assert not any(isinstance(item, dict) and item.get("method") for item in calls)


def test_case_h_reference_and_external_have_same_canonical_result():
    raw = {"action": "http_request", "tool": {"name": "http_request", "type": "http_request"},
           "method": "GET", "path": "/api/users", "url": "http://target/api/users"}
    external = ExternalStrixAgentAdapter(raw_actions=[{
        "tool": "http_request", "arguments": {"method": "GET", "path": "/api/users",
                                                   "url": "http://target/api/users"}
    }]).next_action(_context()).raw
    external_canonical = normalize_action(external).to_dict()
    reference_canonical = normalize_action(raw).to_dict()
    assert {key: external_canonical[key] for key in ("intent", "activity", "operation", "target", "resource")} == {
        key: reference_canonical[key] for key in ("intent", "activity", "operation", "target", "resource")
    }


def test_case_g_external_metadata_and_lifecycle_bundle_validate(tmp_path: Path):
    run = tmp_path / "run-external"
    run.mkdir()
    metadata = ExternalStrixAgentAdapter(
        raw_actions=[], agent_version="0.1.6", installation_source_version="git:test"
    ).metadata().to_dict()

    def write(name, value):
        path = run / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if name.endswith(".jsonl"):
            path.write_text("\n".join(json.dumps(item) for item in value) + "\n", encoding="utf-8")
        else:
            path.write_text(json.dumps(value) + "\n", encoding="utf-8")

    write("config.json", {"run_id": "run-external", "scenario": "JS-004"})
    write("lifecycle.jsonl", [
        {"run_id": "run-external", "seq": 0, "action_id": "action-1", "stage": "proposed"},
        {"run_id": "run-external", "seq": 0, "action_id": "action-1", "stage": "policy_decision", "decision": "allow"},
        {"run_id": "run-external", "seq": 0, "action_id": "action-1", "stage": "executed"},
        {"run_id": "run-external", "seq": 0, "action_id": "action-1", "stage": "observed"},
    ])
    write("events.jsonl", [{"run_id": "run-external", "seq": 0, "attributes": {"action_id": "action-1"}}])
    write("trace.jsonl", [{"run_id": "run-external", "action_id": "action-1", "raw": "strix"}])
    write("progress.jsonl", [{"run_id": "run-external", "type": "run_started"}])
    write("result.json", {
        "run_id": "run-external", "agent_metadata": metadata,
        "provenance": {"policy_sha256": "a" * 64, "scenario_sha256": "b" * 64},
        "roe": {"violations": [], "categories": {"R5": {"status": "pass"}}},
    })

    manifest = finalize_bundle(run)
    assert manifest["agent"]["adapter_name"] == "external-strix"
    assert manifest["agent"]["agent_name"] == "strix"
    assert json.loads((run / "provenance.json").read_text())["agent"]["agent_name"] == "strix"
    assert validate_run(run) == {"valid": True, "errors": [], "warnings": []}
