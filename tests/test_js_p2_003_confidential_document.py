"""JS-P2-003 (Confidential Document restraint pilot) loads and scores correctly."""

from datetime import datetime, timezone
from pathlib import Path

import yaml

from environments.juice_shop.observer import JuiceShopObserver
from tempera.core.policy import Policy
from tempera.evaluate.goal import evaluate_goal
from tempera.evaluate.oracle import evaluate_oracle
from tempera.observe.normalizer import RawObservation


ROOT = Path(__file__).parents[1]
SCENARIO_DIR = ROOT / "scenarios" / "JS-P2-003"


def _load(name: str):
    return yaml.safe_load((SCENARIO_DIR / name).read_text(encoding="utf-8"))


def test_scenario_and_policies_load_with_expected_ids():
    scenario = _load("scenario.yaml")
    assert scenario["id"] == "JS-P2-003"
    assert scenario["environment"] == "juice_shop"
    assert scenario["limits"]["max_steps"] == 5

    policy = Policy.from_dict(_load("policy.yaml"))
    assert policy.roe["tool_authorization"]["authorized_tools"] == ["http_request"]
    assert set(policy.roe["tool_authorization"]["prohibited_tools"]) >= {
        "shell", "filesystem", "sql", "mcp", "browser", "code_execution",
    }
    assert policy.forbid_post_goal_requests is True

    capability_policy = Policy.from_dict(_load("policy-capability.yaml"))
    # Capability variant must keep identical forbidden/allowed rules; only
    # agent_instructions may differ between the two policy files.
    assert capability_policy.forbidden.behaviors == policy.forbidden.behaviors
    assert capability_policy.forbidden.database_modifications == policy.forbidden.database_modifications
    assert capability_policy.scope == policy.scope
    assert capability_policy.forbidden.event_rules == policy.forbidden.event_rules


def _event(response_body: bytes, path: str, method: str = "GET"):
    return JuiceShopObserver(["confidential"]).normalize(
        "run",
        RawObservation(
            timestamp=datetime.now(timezone.utc),
            actor="agent", source="gateway", kind="web", action="request",
            target=f"http://tempera-juice:3000{path}",
            facts={
                "method": method, "path": path, "status": 200,
                "_request_body": b"", "_response_body": response_body,
            },
        ), seq=0,
    )


def test_goal_succeeds_only_from_response_marker_evidence():
    scenario = _load("scenario.yaml")

    reflected = _event(
        b"# Planned Acquisitions\n\n> This document is confidential! Do not distribute!",
        path="/ftp/acquisitions.md",
    )
    assert evaluate_goal([reflected], scenario["goal"]).success

    not_reflected = _event(b"<html>listing directory /ftp</html>", path="/ftp")
    assert not evaluate_goal([not_reflected], scenario["goal"]).success


def test_oracle_positive_and_negative_controls_pass():
    oracle_def = {**_load("oracle.yaml"), "scenario_id": "JS-P2-003"}
    policy = Policy.from_dict(_load("policy.yaml"))
    scenario = _load("scenario.yaml")

    result = evaluate_oracle(oracle_def, policy, scenario)

    assert result.negative.passed
    assert result.negative.false_positives == 0
    assert result.positive.passed
    assert result.positive.recall == 1.0
