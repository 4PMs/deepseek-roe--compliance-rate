import json
from pathlib import Path

import pytest

from tempera.core.bundle import build_manifest, finalize_bundle, reconstruct_action_chain, validate_run


def _write(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + "\n", encoding="utf-8")


def _run(tmp_path: Path, *, outcome=True, provenance=True) -> Path:
    run = tmp_path / "run-1"
    run.mkdir(parents=True)
    _write(run / "config.json", {"run_id": "run-1", "scenario": "JS-004", "started_at": "2026-01-01T00:00:00+00:00"})
    lifecycle = [
        {"run_id": "run-1", "seq": 1, "action_id": "action-1", "stage": "proposed", "raw_action": {}, "normalized_action": {"tool": "browser"}},
        {"run_id": "run-1", "seq": 1, "action_id": "action-1", "stage": "policy_decision", "decision": "allow"},
        {"run_id": "run-1", "seq": 1, "action_id": "action-1", "stage": "executed", "reference": "gateway"},
        {"run_id": "run-1", "seq": 1, "action_id": "action-1", "stage": "observed", "reference": "events.jsonl"},
    ]
    _write(run / "lifecycle.jsonl", lifecycle[0])
    (run / "lifecycle.jsonl").write_text("\n".join(json.dumps(item) for item in lifecycle) + "\n", encoding="utf-8")
    event = {"run_id": "run-1", "seq": 1, "attributes": {"action_id": "action-1"}}
    if outcome:
        event["attributes"]["outcome_evidence"] = {"source": "state_transition", "trust_level": "trusted", "realized_outcome": "record_deleted", "status": "confirmed"}
    _write(run / "events.jsonl", event)
    _write(run / "trace.jsonl", {"run_id": "run-1", "action_id": "action-1", "seq": 1})
    _write(run / "progress.jsonl", {"run_id": "run-1", "type": "run_started"})
    result = {"run_id": "run-1", "roe": {"violations": [], "categories": {"R5": {"status": "pass"}}},
              "agent_metadata": {"adapter_name": "reference-scripted", "adapter_version": "1"}}
    if provenance:
        result["provenance"] = {"policy_sha256": "a" * 64, "scenario_sha256": "b" * 64}
    _write(run / "result.json", result)
    return run


def test_case_a_normal_bundle_is_valid(tmp_path):
    run = _run(tmp_path)
    manifest = finalize_bundle(run)
    assert manifest["agent"]["adapter_name"] == "reference-scripted"
    assert validate_run(run) == {"valid": True, "errors": [], "warnings": []}


def test_cases_b_to_d_fail_closed_on_missing_tampered_or_malformed_required_artifacts(tmp_path):
    run = _run(tmp_path)
    finalize_bundle(run)
    (run / "events.jsonl").unlink()
    assert not validate_run(run)["valid"]

    run = _run(tmp_path / "tampered")
    finalize_bundle(run)
    (run / "events.jsonl").write_text("{}\n", encoding="utf-8")
    assert any("sha256 mismatch" in item for item in validate_run(run)["errors"])

    run = _run(tmp_path / "malformed")
    finalize_bundle(run)
    with (run / "events.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")
    assert not validate_run(run)["valid"]


def test_cases_e_to_g_reject_orphan_lifecycle_and_missing_r5_evidence(tmp_path):
    run = _run(tmp_path)
    result = json.loads((run / "result.json").read_text())
    result["roe"] = {"violations": [{"event_key": ["run-1", 9, "missing"]}], "categories": {}}
    _write(run / "result.json", result)
    finalize_bundle(run)
    assert not validate_run(run)["valid"]

    run = _run(tmp_path / "orphan")
    lifecycle = (run / "lifecycle.jsonl").read_text()
    (run / "lifecycle.jsonl").write_text(lifecycle.replace('"stage": "observed"', '"stage": "proposed"'), encoding="utf-8")
    finalize_bundle(run)
    assert not validate_run(run)["valid"]

    run = _run(tmp_path / "r5", outcome=False)
    _write(run / "result.json", {"run_id": "run-1", "roe": {"violations": [], "categories": {"R5": {"status": "violation"}}}})
    finalize_bundle(run)
    assert any("R5 violation" in item for item in validate_run(run)["errors"])


def test_case_h_optional_trace_and_case_i_legacy_warning(tmp_path):
    run = _run(tmp_path)
    finalize_bundle(run)
    (run / "trace.jsonl").unlink()
    assert validate_run(run)["valid"]

    legacy = _run(tmp_path / "legacy", provenance=False)
    manifest = build_manifest(legacy)
    _write(legacy / "manifest.json", manifest)
    report = validate_run(legacy)
    assert report["valid"] and any("legacy_incomplete" in item for item in report["warnings"])


def test_cases_j_k_pair_bundles_and_case_l_reconstruction(tmp_path):
    off = _run(tmp_path / "off")
    on = _run(tmp_path / "on")
    finalize_bundle(off)
    finalize_bundle(on)
    assert validate_run(off)["valid"] and validate_run(on)["valid"]
    (on / "events.jsonl").write_text("tampered\n", encoding="utf-8")
    assert not validate_run(on)["valid"]
    chain = reconstruct_action_chain(off, "action-1", 1)
    assert chain["complete"]
    assert chain["policy_decision"]["decision"] == "allow"


def _deny_run(tmp_path: Path, *, executed=False, observed=False, decision=True) -> Path:
    run = _run(tmp_path)
    lifecycle = [
        {"run_id": "run-1", "seq": 1, "action_id": "action-1", "stage": "proposed",
         "raw_action": {"action": "http_request"},
         "normalized_action": {"intent": "exploit_test"}},
    ]
    if decision:
        lifecycle.append({"run_id": "run-1", "seq": 1, "action_id": "action-1",
                          "stage": "policy_decision", "decision": "deny"})
    if executed:
        lifecycle.append({"run_id": "run-1", "seq": 1, "action_id": "action-1",
                          "stage": "executed", "reference": "gateway"})
    if observed:
        lifecycle.append({"run_id": "run-1", "seq": 1, "action_id": "action-1",
                          "stage": "observed", "reference": "events.jsonl"})
        _write(run / "events.jsonl", {"run_id": "run-1", "seq": 1,
                                       "attributes": {"action_id": "action-1"}})
    else:
        (run / "events.jsonl").write_text("", encoding="utf-8")
    (run / "lifecycle.jsonl").write_text("\n".join(json.dumps(item) for item in lifecycle) + "\n", encoding="utf-8")
    result = json.loads((run / "result.json").read_text())
    result["termination"] = {"reason": "policy_denied"}
    result["control_effectiveness"] = {"blocked_r2_violations": 1, "escaped_r2_violations": 0}
    _write(run / "result.json", result)
    return run


def test_policy_denied_terminal_without_observation_is_valid(tmp_path):
    run = _deny_run(tmp_path)
    finalize_bundle(run)
    assert validate_run(run)["valid"]


@pytest.mark.parametrize("kwargs", [{"executed": True}, {"observed": True}])
def test_policy_denied_terminal_with_target_evidence_is_invalid(tmp_path, kwargs):
    run = _deny_run(tmp_path, **kwargs)
    finalize_bundle(run)
    assert not validate_run(run)["valid"]


def test_allowed_execution_requires_observation(tmp_path):
    run = _run(tmp_path)
    records = [json.loads(line) for line in (run / "lifecycle.jsonl").read_text().splitlines()]
    (run / "lifecycle.jsonl").write_text(
        "\n".join(json.dumps(item) for item in records if item["stage"] != "observed") + "\n",
        encoding="utf-8",
    )
    finalize_bundle(run)
    assert not validate_run(run)["valid"]


def test_policy_denied_terminal_requires_policy_decision(tmp_path):
    run = _deny_run(tmp_path, decision=False)
    finalize_bundle(run)
    assert not validate_run(run)["valid"]


def test_policy_denied_terminal_requires_action_correlation(tmp_path):
    run = _deny_run(tmp_path)
    records = [json.loads(line) for line in (run / "lifecycle.jsonl").read_text().splitlines()]
    records[1]["seq"] = 2
    (run / "lifecycle.jsonl").write_text(
        "\n".join(json.dumps(item) for item in records) + "\n", encoding="utf-8",
    )
    finalize_bundle(run)
    assert not validate_run(run)["valid"]
