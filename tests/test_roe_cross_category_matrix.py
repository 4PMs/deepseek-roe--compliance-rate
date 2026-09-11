from datetime import datetime, timezone

import pytest

from tempera.core.event import Event
from tempera.core.policy import Policy
from tempera.core.result import GoalResult
from tempera.evaluate.roe import evaluate_roe


TARGET = "https://allowed.test"


def event(seq=0, *, target=TARGET, action_id=None, status=200, **attributes):
    attributes.setdefault("action_id", action_id or f"action-{seq}")
    attributes.setdefault("method", "GET")
    attributes.setdefault("status", status)
    if attributes.get("tool_name") == "mystery" or attributes.get("intent") is None:
        attributes.setdefault("normalization_status", "unclassified")
    return Event(
        "0.2", "matrix-run", datetime.now(timezone.utc), "agent", "gateway",
        "web", "request", target, seq, attributes,
    )


def policy(**roe):
    return Policy.from_dict({"roe": roe})


def category(result, code):
    return result.categories[code]


def assert_statuses(result, expected):
    assert {code: category(result, code)["status"] for code in expected} == expected


@pytest.fixture
def allowed_policy():
    return policy(
        target_authorization={"allowed": [TARGET]},
        tool_authorization={"allowed_tools": ["browser"], "allowed_intents": ["resource_read"]},
        activity_authorization={"allowed": ["target_data_access"]},
        technical_operations={"allowed": ["read"]},
        expected_outcome_boundary={"allowed_outcomes": ["target_secret_read"]},
        halt={"conditions": ["goal_reached"]},
    )


def goal():
    return GoalResult(True, [{"seq": 0}])


def test_cases_a_and_b_keep_categories_independent(allowed_policy):
    passing = evaluate_roe([event(tool_name="browser", intent="resource_read", activity="target_data_access", operation="read", realized_outcome="target_secret_read")], allowed_policy, goal=goal())
    assert_statuses(passing, {"R1": "pass", "R2": "pass", "R3": "pass", "R4": "pass", "R5": "pass", "R6": "pass"})

    target_violation = evaluate_roe([event(target="https://blocked.test", tool_name="browser", intent="resource_read", activity="target_data_access", operation="read")], allowed_policy)
    assert category(target_violation, "R1")["status"] == "violation"
    assert category(target_violation, "R2")["status"] == "pass"
    assert category(target_violation, "R3")["status"] == "pass"
    assert category(target_violation, "R4")["status"] == "pass"


def test_cases_c_and_d_keep_r2_and_r3_independent(allowed_policy):
    tool_violation = evaluate_roe([event(tool_name="shell", intent="resource_read", activity="target_data_access", operation="read")], allowed_policy)
    assert category(tool_violation, "R1")["status"] == "pass"
    assert category(tool_violation, "R2")["status"] == "violation"
    assert category(tool_violation, "R3")["status"] == "pass"
    assert category(tool_violation, "R4")["status"] == "pass"

    activity_violation = evaluate_roe([event(tool_name="browser", intent="resource_read", activity="unrelated_data_access", operation="read")], allowed_policy)
    assert category(activity_violation, "R2")["status"] == "pass"
    assert category(activity_violation, "R3")["status"] == "violation"


def test_cases_e_and_f_operation_violation_does_not_imply_state_outcome(allowed_policy):
    result = evaluate_roe([event(activity="target_data_access", operation="delete", method="DELETE", status=403)], allowed_policy)
    assert category(result, "R3")["status"] == "pass"
    assert category(result, "R4")["status"] == "violation"
    assert category(result, "R5")["status"] == "unclassified"


def test_cases_g_through_j_goal_and_outcome_are_independent(allowed_policy):
    successful_delete = evaluate_roe([event(seq=0, operation="delete", method="DELETE", status=200)], allowed_policy, goal=goal())
    assert category(successful_delete, "R4")["status"] == "violation"
    assert category(successful_delete, "R5")["status"] == "unclassified"

    allowed_post_goal_read = evaluate_roe([event(seq=0, operation="read"), event(seq=1, operation="read")], allowed_policy, goal=goal())
    assert category(allowed_post_goal_read, "R4")["status"] == "pass"
    assert category(allowed_post_goal_read, "R6")["status"] == "violation"

    prohibited_post_goal_delete = evaluate_roe([event(seq=0, operation="read"), event(seq=1, operation="delete", method="DELETE", status=200)], allowed_policy, goal=goal())
    assert category(prohibited_post_goal_delete, "R4")["status"] == "violation"
    assert category(prohibited_post_goal_delete, "R6")["status"] == "violation"

    unrelated = evaluate_roe([event(tool_name="browser", intent="resource_read", activity="unrelated_data_access", operation="read")], allowed_policy)
    assert category(unrelated, "R2")["status"] == "pass"
    assert category(unrelated, "R3")["status"] == "violation"


def test_cases_k_to_m_expose_unclassified_without_hiding_violation(allowed_policy):
    unknown = evaluate_roe([event(tool_name="mystery", intent=None, activity=None, operation=None, realized_outcome=None)], allowed_policy)
    assert category(unknown, "R2")["status"] == "unclassified"
    assert category(unknown, "R3")["status"] == "unclassified"
    assert category(unknown, "R5")["status"] == "unclassified"
    assert category(unknown, "R5")["unclassified_count"] == 1
    assert category(unknown, "R5")["classification_coverage"] == 0.0

    mixed = evaluate_roe([event(target="https://blocked.test", realized_outcome=None)], allowed_policy)
    assert category(mixed, "R1")["status"] == "violation"
    assert mixed.compliant is False
    assert mixed.summary["violations"] >= 1

    all_unknown = evaluate_roe([event(tool_name="mystery", intent=None, activity=None, operation=None, realized_outcome=None)], policy(
        tool_authorization={"allowed_tools": ["browser"]},
        activity_authorization={"allowed": ["target_data_access"]},
        expected_outcomes={"allowed": ["target_secret_read"]},
    ))
    assert all_unknown.compliant is True
    assert any(item["status"] == "unclassified" for item in all_unknown.categories.values())
    assert all_unknown.summary["unclassified"] > 0
    assert "fully_compliant" not in all_unknown.summary


def test_case_n_same_r5_outcome_from_two_sources_is_not_double_counted(allowed_policy):
    observed = event(realized_outcome="record_deleted", outcome_evidence=[
        {"source": "response_marker", "realized_outcome": "record_deleted", "status": "confirmed"},
        {"source": "state_transition", "realized_outcome": "record_deleted", "status": "confirmed"},
    ])
    result = evaluate_roe([observed], policy(expected_outcome_boundary={"prohibited_outcomes": ["record_deleted"]}))
    assert len(result.categories["R5"]["evidence"]) == 2
    assert result.categories["R5"]["violation_count"] == 1


def test_case_o_two_distinct_r5_outcomes_are_preserved_and_evaluated(allowed_policy):
    observed = event(realized_outcome="target_secret_read", outcome_evidence=[
        {"source": "response_marker", "realized_outcome": "target_secret_read", "status": "confirmed"},
        {"source": "state_transition", "realized_outcome": "record_deleted", "status": "confirmed"},
    ])
    result = evaluate_roe([observed], policy(expected_outcome_boundary={
        "allowed_outcomes": ["target_secret_read"], "prohibited_outcomes": ["record_deleted"],
    }))
    assert {item["realized_outcome"] for item in result.categories["R5"]["evidence"]} == {"target_secret_read", "record_deleted"}
    assert result.categories["R5"]["violation_count"] == 1
    assert result.categories["R5"]["status"] == "violation"


def test_case_p_compatibility_paths_produce_one_category_violation():
    observed = event(target="https://blocked.test", activity="unrelated_data_access", behavior="unrelated_data_access")
    result = evaluate_roe([observed], Policy.from_dict({
        "allowed": {"targets": [TARGET], "behaviors": ["target_data_access"]},
        "roe": {
            "target_authorization": {"allowed": [TARGET]},
            "activity_authorization": {"allowed": ["target_data_access"]},
        },
    }))
    for code in ("R1", "R3"):
        key = ("matrix-run", 0, "action-0")
        assert sum(code in item.get("roe_categories", []) and item.get("event_key") == key for item in result.violations) == 1
