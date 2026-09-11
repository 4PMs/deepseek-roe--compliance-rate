"""Convert raw tool calls into a small canonical action vocabulary."""

from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

from .detectors.injection import detect_injection

Adapter = Callable[[Mapping[str, Any]], "CanonicalAction"]


@dataclass(frozen=True)
class CanonicalAction:
    tool: dict[str, Any]
    protocol: str | None = None
    intent: str | None = None
    attack_family: str | None = None
    activity: str | None = None
    operation: str | None = None
    target: dict[str, Any] | None = None
    resource: str | None = None
    normalization_status: str = "normalized"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_ADAPTERS: dict[str, Adapter] = {}
_SUPPORTED_OPERATIONS = {"read", "create", "modify", "delete", "execute", "invoke"}


def register_adapter(*identities: str) -> Callable[[Adapter], Adapter]:
    def decorator(adapter: Adapter) -> Adapter:
        for identity in identities:
            _ADAPTERS[identity.casefold()] = adapter
        return adapter
    return decorator


def _tool(raw: Mapping[str, Any]) -> dict[str, Any]:
    value = raw.get("tool")
    if isinstance(value, Mapping):
        return {
            "name": str(value.get("name", "unknown")),
            "type": str(value.get("type", "other")),
            "family": str(value.get("family", "other")),
        }
    if isinstance(value, str):
        return {"name": value, "type": "other", "family": "other"}
    return {"name": "unknown", "type": "other", "family": "other"}


def _url_parts(raw: Mapping[str, Any]) -> tuple[str | None, str | None, int | None, str | None]:
    url = raw.get("url") or raw.get("target_url")
    if not isinstance(url, str):
        target = raw.get("target")
        url = target if isinstance(target, str) else None
    if not url:
        return None, None, None, None
    parsed = urlsplit(url)
    return parsed.scheme or None, parsed.hostname, parsed.port, parsed.path or "/"


def _target(raw: Mapping[str, Any], host: str | None = None) -> dict[str, Any]:
    value = raw.get("target") if isinstance(raw.get("target"), Mapping) else {}
    return {
        "host": value.get("host", raw.get("destination_host", host)),
        "port": value.get("port", raw.get("destination_port")),
        "application": value.get("application", raw.get("application")),
    }


def _operation(raw: Mapping[str, Any]) -> str | None:
    if raw.get("operation") is not None:
        return str(raw["operation"])
    method = str(raw.get("method", "")).upper()
    return {"GET": "read", "POST": "create", "PUT": "modify",
            "PATCH": "modify", "DELETE": "delete"}.get(method)


def _http(raw: Mapping[str, Any]) -> CanonicalAction:
    tool = _tool(raw)
    protocol, parsed_host, parsed_port, parsed_resource = _url_parts(raw)
    resource = raw.get("path") or raw.get("resource") or parsed_resource
    operation = _operation(raw)
    target = _target(raw, parsed_host)
    if target["port"] is None:
        target["port"] = parsed_port
    status = (
        "normalized" if resource and operation in _SUPPORTED_OPERATIONS and target.get("host")
        else "unclassified"
    )
    intent = {
        "read": "resource_read", "create": "resource_create",
        "modify": "resource_modify", "delete": "resource_delete",
    }.get(operation)
    activity = raw.get("activity", "target_data_access" if operation == "read" else None)
    attack_family = None
    injection = detect_injection(
        method=str(raw.get("method", "")).upper(),
        path=resource,
        content_type=(raw.get("content_type") or raw.get("Content-Type")
                      or (raw.get("headers", {}).get("Content-Type")
                          if isinstance(raw.get("headers"), Mapping) else None)),
        body=raw.get("body"),
    )
    if injection["status"] == "ambiguous":
        status = "unclassified"
    elif injection["classified"]:
        intent = injection["intent"]
        activity = "exploitation"
        attack_family = injection["attack_family"]
    else:
        attack_family = None
    return CanonicalAction(
        tool=tool, protocol=protocol or "http", intent=intent,
        activity=activity,
        operation=operation, target=target, resource=resource,
        normalization_status=status,
        attack_family=attack_family,
    )


@register_adapter("http_request", "curl", "browser", "python_requests")
def _http_adapter(raw: Mapping[str, Any]) -> CanonicalAction:
    return _http(raw)


@register_adapter("sqlmap")
def _sqlmap(raw: Mapping[str, Any]) -> CanonicalAction:
    tool = _tool(raw)
    if tool["family"] == "other":
        tool["family"] = "security_scanner"
    target = _target(raw)
    target_value = raw.get("target")
    if isinstance(target_value, str):
        target["application"] = target["application"] or target_value
    return CanonicalAction(
        tool=tool, protocol="http", intent="exploit_test",
        activity="exploitation", operation=None, target=target,
        resource=raw.get("resource"),
        normalization_status="normalized" if target["application"] else "unclassified",
    )


def normalize_action(raw: Mapping[str, Any] | Any) -> CanonicalAction:
    if not isinstance(raw, Mapping):
        return CanonicalAction(
            tool={"name": "unknown", "type": "other", "family": "other"},
            normalization_status="unclassified",
        )
    tool = _tool(raw)
    adapter = _ADAPTERS.get(tool["name"].casefold()) or _ADAPTERS.get(tool["type"].casefold())
    if adapter is None:
        return CanonicalAction(tool=tool, normalization_status="unclassified")
    try:
        return adapter(raw)
    except (KeyError, TypeError, ValueError):
        return CanonicalAction(tool=tool, normalization_status="unclassified")
