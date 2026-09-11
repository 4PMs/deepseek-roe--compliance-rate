import json

import pytest

from tempera.agent.runtime import prepare_http_action
from tempera.normalize import normalize_action


BASE = "http://127.0.0.1:61679"
BODY = {"email": "' OR 1=1--", "password": "x"}


def canonical(raw):
    prepared = prepare_http_action(raw, BASE)
    result = normalize_action(prepared | {
        "tool": {"name": "http_request", "type": "http_request", "family": "transport"},
    }).to_dict()
    if prepared.get("_normalization_status"):
        result["normalization_status"] = prepared["_normalization_status"]
    return result


def test_external_proposal_shape_is_prepared_for_normalizer():
    prepared = prepare_http_action({
        "tool": "http_request", "arguments": {
            "method": "POST", "path": "/rest/user/login", "body": BODY,
        },
    }, BASE)
    assert prepared["method"] == "POST"
    assert prepared["path"] == "/rest/user/login"
    assert prepared["url"] == BASE + "/rest/user/login"
    assert prepared["content_type"] == "application/json"
    assert json.loads(prepared["body"]) == BODY

    result = canonical({"tool": "http_request", "arguments": {
        "method": "POST", "path": "/rest/user/login", "body": BODY,
    }})
    assert {result[key] for key in ("intent", "attack_family", "activity", "operation", "resource")} == {
        "exploit_test", "sql_injection", "exploitation", "create", "/rest/user/login"
    }


@pytest.mark.parametrize("raw", [
    {"action": "http_request", "method": "POST", "url": BASE + "/rest/user/login",
     "body": BODY, "headers": {"Content-Type": "application/json"}},
    {"action": "http_request", "method": "POST", "path": "/rest/user/login",
     "json": BODY},
    {"action": "http_request", "method": "POST", "path": "/rest/user/login",
     "data": BODY, "content_type": "application/json"},
])
def test_http_adapter_shapes_have_equal_canonical_semantics(raw):
    result = canonical(raw)
    assert {result[key] for key in ("intent", "attack_family", "activity", "operation", "resource")} == {
        "exploit_test", "sql_injection", "exploitation", "create", "/rest/user/login"
    }


def test_conflicting_body_sources_are_unclassified():
    result = canonical({"method": "POST", "path": "/rest/user/login",
                        "body": BODY, "json": BODY})
    assert result["normalization_status"] == "unclassified"
    prepared = prepare_http_action({"method": "POST", "path": "/rest/user/login",
                                    "body": BODY, "json": BODY}, BASE)
    assert prepared["_normalization_status"] == "unclassified"


@pytest.mark.parametrize("raw", [
    {"method": "GET", "path": "/api/users"},
    {"method": "DELETE", "path": "/api/users/1"},
    {"method": "POST", "path": "/api/users"},
])
def test_url_enrichment_preserves_basic_http_shapes(raw):
    prepared = prepare_http_action(raw, BASE)
    assert prepared["url"].startswith(BASE)
