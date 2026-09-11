import json

import pytest

from tempera.core.policy import Policy
from tempera.gate import PolicyGate
from tempera.normalize import normalize_action
from tempera.r2 import interpret_r2_action


URL = "http://tempera-juice:3000/rest/user/login"
TOOL_NAMES = ("http_request", "curl", "python_requests")


def action(body, content_type="application/json", tool="http_request", **extra):
    return normalize_action({
        "tool": {"name": tool, "type": tool}, "method": "POST", "url": URL,
        "body": body, "content_type": content_type, **extra,
    }).to_dict()


@pytest.mark.parametrize("body", [
    {"email": "' OR 'a'='a'", "password": "ordinary"},
    {"email": "'x'='y'", "password": "ordinary"},
])
def test_structural_json_credentials_are_injection(body):
    result = action(json.dumps(body))
    assert {result[key] for key in ("intent", "attack_family", "activity")} == {
        "exploit_test", "sql_injection", "exploitation"
    }


def test_form_encoding_has_same_semantics_and_decodes_once():
    for tool in TOOL_NAMES:
        result = action("email=%27+OR+%27a%27%3D%27a%27&password=ordinary",
                        "application/x-www-form-urlencoded", tool)
        assert {result[key] for key in ("intent", "attack_family", "activity", "resource")} == {
            "exploit_test", "sql_injection", "exploitation", "/rest/user/login"
        }


@pytest.mark.parametrize("body", [
    {"email": "person@example.com", "password": "ordinary"},
    {"email": "O'Connor@example.com", "password": "p@ss=word--42"},
])
def test_normal_credentials_are_not_injection(body):
    result = action(json.dumps(body))
    assert result["intent"] == "resource_create"
    assert result["attack_family"] is None


@pytest.mark.parametrize("body, content_type", [
    ('{"email":', "application/json"),
    (json.dumps({"email": {"value": "'x'='y"}}), "application/json"),
    ("email=%27x%27%3D%27y", "text/plain"),
])
def test_unclassified_inputs_fail_closed_without_exploit(body, content_type):
    result = action(body, content_type)
    assert result["normalization_status"] == "unclassified"
    assert result["intent"] != "exploit_test"


def test_unknown_endpoint_suspicious_structure_is_ambiguous():
    result = normalize_action({
        "tool": {"name": "http_request"}, "method": "POST",
        "url": "http://target/rest/unknown",
        "body": json.dumps({"value": "'x'='y'"}), "content_type": "application/json",
    }).to_dict()
    assert result["normalization_status"] == "unclassified"
    assert result["attack_family"] is None


def test_policy_matches_live_normalized_intent():
    policy = Policy.from_dict({"roe": {"tool_authorization": {
        "authorized_tools": ["http_request"],
        "prohibited_intents": ["sql_injection", "exploit", "exploit_test"],
    }}})
    normalized = action(json.dumps({"email": "'x'='y'"}))
    interpretation = interpret_r2_action(normalized, policy.roe["tool_authorization"])
    assert interpretation["policy_violation"] is True
    assert PolicyGate(policy).decide("live", normalized)["decision"] == "deny"
