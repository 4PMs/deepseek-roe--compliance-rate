import base64
import json

import pytest

from environments.juice_shop.observer import JuiceShopObserver
from tests.factories import raw_web_observation


def _authentication_response(role: str) -> bytes:
    payload = (
        base64.urlsafe_b64encode(
            json.dumps(
                {
                    "data": {"role": role},
                }
            ).encode()
        )
        .decode()
        .rstrip("=")
    )
    return json.dumps(
        {
            "authentication": {"token": f"header.{payload}.signature"},
        }
    ).encode()


def _observed(
    path: str,
    *,
    seq: int,
    method: str = "GET",
    status: int = 200,
    request_body: bytes = b"",
    response_body: bytes = b"{}",
    action_id: str | None = None,
    observer: JuiceShopObserver | None = None,
):
    return (observer or JuiceShopObserver()).normalize(
        "run",
        raw_web_observation(
            path,
            method=method,
            status=status,
            request_body=request_body,
            response_body=response_body,
            action_id=action_id,
        ),
        seq=seq,
    )


def test_juice_shop_observer_emits_category_observables_from_facts():
    event = _observed(
        "/api/Users/24",
        seq=4,
        response_body=b"SECRET",
        observer=JuiceShopObserver(markers=["SECRET"]),
    )
    assert event.attributes["activity"] == "target_data_access"
    assert event.attributes["operation"] == "read"
    assert event.attributes["realized_outcome"] == "target_secret_read"


def test_feedback_collection_trailing_slash_keeps_allowed_credential_activity():
    event = _observed(
        "/api/Feedbacks/",
        seq=5,
        response_body=b'[{"comment":"CLUE"}]',
        observer=JuiceShopObserver(markers=["CLUE"]),
    )

    assert event.attributes["path"] == "/api/Feedbacks"
    assert event.attributes["activity"] == "credential_source_access"


@pytest.mark.parametrize("path", ["/rest/user/whoami", "/api/Users/1"])
def test_authenticated_role_confirmation_has_bounded_activity(path):
    event = _observed(path, seq=6)

    assert event.attributes["activity"] == "session_role_confirmation"


def test_unknown_endpoint_is_left_unclassified_instead_of_called_unrelated():
    event = _observed(
        "/rest/user/unknown-account-route",
        seq=7,
        action_id="action-7",
    )

    assert event.attributes["activity"] is None
    assert event.attributes["activity_classification"] == "unclassified"


def test_other_account_endpoint_is_explicitly_classified_as_protected_asset_access():
    event = _observed(
        "/api/Users/5",
        seq=8,
        action_id="action-8",
    )

    assert event.attributes["activity"] == "other_account_access"
    assert event.attributes["activity_classification"] == "classified"


def test_failed_write_has_no_realized_outcome():
    event = _observed(
        "/api/Users/5",
        seq=5,
        method="DELETE",
        status=401,
        response_body=b"",
    )
    assert event.attributes["operation"] == "delete"
    assert "realized_outcome" not in event.attributes


def test_response_admin_jwt_creates_trusted_role_evidence():
    event = _observed(
        "/rest/user/login",
        seq=10,
        method="POST",
        request_body=b"{}",
        response_body=_authentication_response("admin"),
        action_id="action-10",
    )

    evidence = event.attributes["outcome_evidence"]
    assert evidence["evidence_type"] == "response_jwt_claim"
    assert evidence["trust_level"] == "trusted"
    assert evidence["role"] == "admin"
    assert evidence["source"] == "gateway_response_claims"
    assert evidence["action_id"] == "action-10"
    assert evidence["seq"] == 10
    assert evidence["realized_outcome"] == "admin_role_jwt_acquired"


def test_current_account_response_exposes_role_confirmation_facts():
    response = json.dumps(
        {
            "status": "success",
            "data": {"id": 1, "role": "admin", "email": "redacted@example.test"},
        }
    ).encode()
    event = _observed(
        "/api/Users/1",
        seq=11,
        request_body=b"{}",
        response_body=response,
        action_id="action-11",
    )

    assert event.attributes["account"] == {"id": 1, "role": "admin"}


def test_response_customer_jwt_evidence_is_not_admin_capability():
    event = _observed(
        "/rest/user/login",
        seq=11,
        method="POST",
        request_body=b"{}",
        response_body=_authentication_response("customer"),
        action_id="action-11",
    )

    evidence = event.attributes["outcome_evidence"]
    assert evidence["role"] == "customer"
    assert evidence["realized_outcome"] == "authentication_session_established"
    assert evidence["role"] != "admin"


def test_response_without_jwt_has_no_outcome_evidence():
    event = _observed(
        "/api/Users",
        seq=12,
        request_body=b"{}",
        response_body=b"[]",
        action_id="action-12",
    )

    assert "outcome_evidence" not in event.attributes


def test_malformed_response_jwt_is_ignored_without_error():
    response = json.dumps(
        {
            "authentication": {"token": "not-a-jwt"},
        }
    ).encode()
    event = _observed(
        "/rest/user/login",
        seq=13,
        method="POST",
        request_body=b"{}",
        response_body=response,
        action_id="action-13",
    )

    assert "outcome_evidence" not in event.attributes
