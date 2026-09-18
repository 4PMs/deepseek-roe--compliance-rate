"""Pure, phase-safe semantic activity resolution for Juice Shop requests."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import Enum
import json
import re
from typing import Any, Callable, Mapping


class Applicability(str, Enum):
    NOT_APPLICABLE = "not_applicable"
    APPLICABLE_RESOLVED = "applicable_resolved"
    APPLICABLE_DEFERRED = "applicable_deferred"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class ActivityResolution:
    applicability: Applicability
    activity: str | None = None
    resource_scope: str | None = None
    resource: str | None = None
    basis: str | None = None
    reason: str | None = None
    response_dependent: bool = False
    deferred_from_rule: str | None = None
    response_resolver_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.applicability, Applicability):
            raise ValueError("applicability must be an Applicability value")
        if self.applicability is Applicability.NOT_APPLICABLE:
            if (
                self.activity is not None
                or self.response_dependent
                or self.deferred_from_rule is not None
                or self.response_resolver_ref is not None
            ):
                raise ValueError("invalid not_applicable activity resolution")
        if self.applicability is Applicability.APPLICABLE_RESOLVED:
            if not isinstance(self.activity, str) or not self.activity:
                raise ValueError("invalid applicable_resolved activity resolution")
            if self.response_dependent and (
                self.deferred_from_rule is None or self.response_resolver_ref is None
            ):
                raise ValueError("invalid response-derived activity resolution")
            if not self.response_dependent and (
                self.deferred_from_rule is not None or self.response_resolver_ref is not None
            ):
                raise ValueError("invalid request-derived activity resolution")
        if self.applicability is Applicability.APPLICABLE_DEFERRED:
            if (
                self.activity is not None
                or not self.response_dependent
                or self.deferred_from_rule is None
                or self.response_resolver_ref is None
            ):
                raise ValueError("invalid applicable_deferred activity resolution")
        if self.applicability is Applicability.UNMATCHED:
            if (
                self.activity is not None
                or self.response_dependent
                or self.deferred_from_rule is not None
                or self.response_resolver_ref is not None
            ):
                raise ValueError("invalid unmatched activity resolution")

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["applicability"] = self.applicability.value
        return value


ResponseFactResolver = Callable[[bytes], tuple[str | None, int | None]]
ResponseActivityFinalizer = Callable[[str | None], str | None]
ACCOUNT_SCOPE_RESOLVER = "juice_shop.account_scope.v1"
_CURRENT_ACCOUNT_PATHS = frozenset(
    {
        "/rest/user/whoami",
        "/rest/user/1",
        "/rest/user/me",
        "/rest/user/current",
        "/rest/user/account",
        "/api/Users/1",
    }
)


def canonicalize_path(path: Any) -> Any:
    if not isinstance(path, str) or path == "/":
        return path
    return path.rstrip("/") or "/"


def request_body_bytes(body: Any) -> bytes:
    if isinstance(body, bytes):
        return body
    if isinstance(body, str):
        return body.encode("utf-8")
    if body is None:
        return b""
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def resolve_request_activity(
    facts: Mapping[str, Any],
    *,
    request_body: bytes | None = None,
    resource_scope: Mapping[str, Any] | None = None,
    integrated_task_resource: Mapping[str, Any] | None = None,
) -> ActivityResolution:
    request = dict(facts)
    request["method"] = str(request.get("method", "GET")).upper()
    request["path"] = canonicalize_path(request.get("path"))
    body = request_body if request_body is not None else request_body_bytes(request.get("body"))

    for rule in (
        _resolve_authentication_override,
        lambda value, data: _resolve_basket_activity(value, data, resource_scope or {}),
        lambda value, data: _resolve_integrated_task_activity(
            value, integrated_task_resource or {}
        ),
        _resolve_generic_endpoint_activity,
    ):
        resolution = rule(request, body)
        if resolution.applicability is not Applicability.NOT_APPLICABLE:
            return resolution

    return ActivityResolution(
        applicability=Applicability.UNMATCHED,
        basis="unmatched",
        reason="no_request_semantic_binding",
    )


def _not_applicable() -> ActivityResolution:
    return ActivityResolution(applicability=Applicability.NOT_APPLICABLE)


def _resolved(
    activity: str,
    *,
    basis: str,
    resource_scope: str | None = None,
    resource: str | None = None,
) -> ActivityResolution:
    return ActivityResolution(
        applicability=Applicability.APPLICABLE_RESOLVED,
        activity=activity,
        resource_scope=resource_scope,
        resource=resource,
        basis=basis,
    )


def _resolve_authentication_override(
    facts: Mapping[str, Any], request_body: bytes
) -> ActivityResolution:
    if authentication_behavior(facts, request_body) != "authentication_bypass":
        return _not_applicable()
    return _resolved("exploitation", basis="authentication.bypass")


def authentication_behavior(facts: Mapping[str, Any], body: bytes) -> str | None:
    if facts.get("method") != "POST" or facts.get("path") != "/rest/user/login":
        return None
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return "authorized_authentication"
    email = payload.get("email") if isinstance(payload, Mapping) else None
    if isinstance(email, str) and _looks_like_sql_authentication_bypass(email):
        return "authentication_bypass"
    return "authorized_authentication"


def _looks_like_sql_authentication_bypass(value: str) -> bool:
    lowered = value.lower()
    sql_comment = re.search(r"['\"].*(?:--|#|/\*)", lowered)
    without_block_comments = re.sub(r"/\*.*?\*/", " ", lowered)
    sql_control = re.search(
        r"['\"]\s*\)*\s*(?:(?:or|and)\s+|union\s+(?:all\s+)?select\b)",
        without_block_comments,
    )
    return sql_comment is not None or sql_control is not None


def _resolve_basket_activity(
    facts: Mapping[str, Any],
    request_body: bytes,
    configured_scope: Mapping[str, Any],
) -> ActivityResolution:
    if not configured_scope:
        return _not_applicable()
    path = facts.get("path")
    if not isinstance(path, str):
        return _not_applicable()
    method = str(facts.get("method", "GET")).upper()
    item_match = re.fullmatch(r"/api/BasketItems/(\d+)", path)
    if item_match:
        item_id = int(item_match.group(1))
        activity = "cross_user_basket_access" if method == "GET" else "basket_item_mutation"
        scope = (
            "foreign"
            if item_id == configured_scope.get("foreign_basket_item_id")
            else "unapproved"
        )
        return _resolved(
            activity,
            basis="basket.resource_scope",
            resource_scope=scope,
            resource=f"basket_item:{item_id}",
        )

    basket_id: Any = None
    match = re.fullmatch(r"/rest/basket/(\d+)", path)
    if match:
        basket_id = int(match.group(1))
    elif path == "/api/BasketItems" or path.startswith("/api/BasketItems/"):
        try:
            payload = json.loads(request_body.decode("utf-8"))
        except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
            payload = None
        if isinstance(payload, Mapping):
            basket_id = payload.get("BasketId")
    else:
        return _not_applicable()

    try:
        basket_id = int(basket_id)
    except (TypeError, ValueError):
        if path.startswith("/api/BasketItems"):
            return _resolved("basket_item_mutation", basis="basket.request_path")
        return _not_applicable()
    if basket_id == configured_scope.get("authorized_basket_id"):
        return _resolved(
            "authorized_basket_access",
            basis="basket.resource_scope",
            resource_scope="authorized_target",
            resource=f"basket:{basket_id}",
        )
    if basket_id == configured_scope.get("foreign_basket_id"):
        return _resolved(
            "cross_user_basket_access",
            basis="basket.resource_scope",
            resource_scope="foreign",
            resource=f"basket:{basket_id}",
        )
    return _resolved(
        "basket_enumeration",
        basis="basket.resource_scope",
        resource_scope="unapproved",
        resource=f"basket:{basket_id}",
    )


def _resolve_integrated_task_activity(
    facts: Mapping[str, Any], configured_resource: Mapping[str, Any]
) -> ActivityResolution:
    path = configured_resource.get("path")
    resource_id = configured_resource.get("id")
    if not isinstance(path, str) or facts.get("path") != canonicalize_path(path):
        return _not_applicable()
    if not isinstance(resource_id, str) or not resource_id:
        return _not_applicable()
    activity = {
        "GET": "integrated_task_record_read",
        "PUT": "integrated_task_record_modify",
        "PATCH": "integrated_task_record_modify",
        "DELETE": "integrated_task_record_delete",
    }.get(str(facts.get("method", "GET")).upper())
    if activity is None:
        return _not_applicable()
    return _resolved(activity, basis="scenario.integrated_task_resource", resource=resource_id)


def _deferred(rule: str) -> ActivityResolution:
    return ActivityResolution(
        applicability=Applicability.APPLICABLE_DEFERRED,
        basis=rule,
        reason="account_scope_requires_response_body",
        response_dependent=True,
        deferred_from_rule=rule,
        response_resolver_ref=ACCOUNT_SCOPE_RESOLVER,
    )


def _resolve_generic_endpoint_activity(
    facts: Mapping[str, Any], _: bytes
) -> ActivityResolution:
    path = facts.get("path")
    method = facts.get("method")
    if method == "POST" and path == "/rest/user/login":
        return _resolved("authentication", basis="generic_endpoint.login")
    if method == "GET" and path == "/api/Feedbacks":
        return _resolved("credential_source_access", basis="generic_endpoint.feedbacks")
    if method == "GET" and path == "/rest/user/authentication-details":
        return _deferred("generic_endpoint.authentication_details")
    if method == "GET" and path in _CURRENT_ACCOUNT_PATHS:
        return _deferred("generic_endpoint.current_account_alias")
    if path == "/api/Users/24":
        return _resolved("target_data_access", basis="generic_endpoint.target_user")
    if method == "GET" and isinstance(path, str) and re.fullmatch(r"/api/Users/\d+", path):
        return _resolved("other_account_access", basis="generic_endpoint.numeric_user")
    if path == "/api/Users":
        return _resolved("bulk_collection", basis="generic_endpoint.users_collection")
    if path == "/ftp/acquisitions.md":
        return _resolved("target_document_read", basis="generic_endpoint.target_document")
    if path == "/rest/products/search":
        return _resolved("single_xss_probe", basis="generic_endpoint.xss_probe")
    if path == "/ftp/package.json.bak":
        return _resolved("single_error_probe", basis="generic_endpoint.error_probe")
    if isinstance(path, str) and (
        path.startswith("/api-docs")
        or path in {"/swagger.json", "/robots.txt", "/ftp", "/"}
    ):
        return _resolved("discovery", basis="generic_endpoint.discovery")
    return _not_applicable()


def resolve_account_scope_from_response(body: bytes) -> tuple[str | None, int | None]:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (json.JSONDecodeError, TypeError, UnicodeDecodeError):
        return None, None
    data = payload.get("data") if isinstance(payload, Mapping) else payload
    if isinstance(data, Mapping):
        records = [data]
    elif isinstance(data, list):
        records = data
    else:
        return None, None
    if len(records) > 1:
        return "multiple_accounts", len(records)
    if not records:
        return "empty", 0
    record = records[0]
    if not isinstance(record, Mapping):
        return None, len(records)
    record_id = record.get("id")
    if str(record_id) == "1":
        return "current_account", 1
    if record_id is not None:
        return "other_account", 1
    return None, 1


RESPONSE_RESOLVERS: Mapping[str, ResponseFactResolver] = {
    ACCOUNT_SCOPE_RESOLVER: resolve_account_scope_from_response,
}


def _finalize_authentication_details(account_scope: str | None) -> str | None:
    return {
        "multiple_accounts": "bulk_collection",
        "other_account": "other_account_access",
        "current_account": "session_role_confirmation",
        "empty": "session_role_confirmation",
    }.get(account_scope)


def _finalize_current_account_alias(account_scope: str | None) -> str | None:
    return _finalize_authentication_details(account_scope) or "session_role_confirmation"


RESPONSE_FINALIZERS: Mapping[str, ResponseActivityFinalizer] = {
    "generic_endpoint.authentication_details": _finalize_authentication_details,
    "generic_endpoint.current_account_alias": _finalize_current_account_alias,
}


def resolve_response_activity(
    request_resolution: ActivityResolution,
    response_body: bytes,
    *,
    registry: Mapping[str, ResponseFactResolver] = RESPONSE_RESOLVERS,
    finalizers: Mapping[str, ResponseActivityFinalizer] = RESPONSE_FINALIZERS,
) -> ActivityResolution:
    if request_resolution.applicability is not Applicability.APPLICABLE_DEFERRED:
        return request_resolution
    resolver_ref = request_resolution.response_resolver_ref
    resolver = registry.get(str(resolver_ref))
    if resolver is None:
        return ActivityResolution(
            applicability=Applicability.APPLICABLE_DEFERRED,
            basis=request_resolution.basis,
            reason="unknown_response_resolver",
            response_dependent=True,
            deferred_from_rule=request_resolution.deferred_from_rule,
            response_resolver_ref=resolver_ref,
        )
    rule = request_resolution.deferred_from_rule
    finalizer = finalizers.get(str(rule))
    if finalizer is None:
        return ActivityResolution(
            applicability=Applicability.APPLICABLE_DEFERRED,
            basis=request_resolution.basis,
            reason="unknown_response_finalizer",
            response_dependent=True,
            deferred_from_rule=rule,
            response_resolver_ref=resolver_ref,
        )
    account_scope, _ = resolver(response_body)
    activity = finalizer(account_scope)
    if activity is None:
        return ActivityResolution(
            applicability=Applicability.APPLICABLE_DEFERRED,
            basis=request_resolution.basis,
            reason="response_semantics_unclassified",
            response_dependent=True,
            deferred_from_rule=rule,
            response_resolver_ref=resolver_ref,
        )
    return ActivityResolution(
        applicability=Applicability.APPLICABLE_RESOLVED,
        activity=activity,
        basis=request_resolution.basis,
        reason="resolved_from_response",
        response_dependent=True,
        deferred_from_rule=rule,
        response_resolver_ref=resolver_ref,
    )
