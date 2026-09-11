import pytest

from tempera.normalize import normalize_action


@pytest.mark.parametrize(
    "raw",
    [
        {"tool": {"name": "http_request", "type": "http_request", "family": "transport"},
         "method": "GET", "url": "http://target-app:3000/api/users"},
        {"tool": {"name": "browser", "type": "browser", "family": "browser"},
         "method": "GET", "url": "http://target-app:3000/api/users"},
        {"tool": {"name": "python_requests", "type": "python", "family": "code"},
         "method": "GET", "url": "http://target-app:3000/api/users"},
    ],
)
def test_http_tool_adapters_have_equivalent_canonical_semantics(raw):
    action = normalize_action(raw).to_dict()
    assert action["normalization_status"] == "normalized"
    assert {key: action[key] for key in (
        "protocol", "intent", "activity", "operation", "target", "resource"
    )} == {
        "protocol": "http", "intent": "resource_read",
        "activity": "target_data_access", "operation": "read",
        "target": {"host": "target-app", "port": 3000, "application": None},
        "resource": "/api/users",
    }


def test_raw_tool_identity_is_preserved():
    actions = [
        normalize_action({"tool": {"name": name, "type": "http_request"},
                          "method": "GET", "url": "http://target/api/users"})
        for name in ("curl", "browser", "python_requests")
    ]
    assert [action.tool["name"] for action in actions] == [
        "curl", "browser", "python_requests"
    ]


def test_sqlmap_fixture_is_explicitly_normalized_without_guessing_operation():
    action = normalize_action({
        "tool": {"name": "sqlmap", "type": "scanner"}, "target": "target-app",
    }).to_dict()
    assert action["tool"] == {"name": "sqlmap", "type": "scanner", "family": "security_scanner"}
    assert action["intent"] == "exploit_test"
    assert action["activity"] == "exploitation"
    assert action["operation"] is None


@pytest.mark.parametrize(
    "raw",
    [
        {"tool": {"name": "mystery", "type": "other"}, "method": "GET",
         "url": "http://target/"},
        "not-an-action",
        {"tool": {"name": "http_request", "type": "http_request"},
         "method": "GET", "path": "/api/users"},
        {"tool": {"name": "http_request", "type": "http_request"},
         "method": "BOGUS", "url": "http://target/api/users"},
    ],
)
def test_unknown_or_malformed_actions_are_unclassified(raw):
    assert normalize_action(raw).normalization_status == "unclassified"
