"""Trusted, action-correlated state observation for bounded Juice Shop resources."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from typing import Any

from .reset import CONTAINER, _docker


class JuiceShopStateObserver:
    """Capture sanitized target state immediately before and after one HTTP action."""

    source = "juice_shop_state_observer"

    def __init__(
        self,
        scenario: Mapping[str, Any],
        *,
        row_reader: Callable[[int], list[Mapping[str, Any]] | None] | None = None,
    ) -> None:
        fixture = scenario.get("fixture") or {}
        resource = (scenario.get("observer") or {}).get("integrated_task_resource") or {}
        if fixture.get("type") == "juice_shop_bounded_basket" or resource.get("table") == "BasketItems":
            self._kind = "basket_item"
            self._table = "BasketItems"
            self._row_id = int(resource.get("row_id", fixture["foreign_basket_item_id"]))
            self._fields = ("ProductId", "BasketId", "quantity")
            self._paths = dict(resource.get("paths") or {})
            self._row_reader = row_reader or _read_basket_item_rows
        else:
            self._kind = "feedback"
            self._table = "Feedbacks"
            self._row_id = int(fixture["feedback_id"])
            self._fields = ("UserId", "comment", "rating")
            path = str(resource["path"])
            self._paths = {name: path for name in ("read", "modify", "delete")}
            self._row_reader = row_reader or _read_feedback_rows

    @property
    def target(self) -> str:
        return f"sqlite:{self._table}/{self._row_id}"

    def capture(self, *, action_id: str | None, method: str, path: str) -> dict[str, Any]:
        try:
            rows = self._row_reader(self._row_id)
        except Exception as error:
            return {
                "quality": {"status": "failed", "reason": type(error).__name__},
                "state": None,
            }
        if rows is None:
            return {
                "quality": {"status": "missing", "reason": "snapshot_missing"},
                "state": None,
            }
        if len(rows) > 1:
            return {
                "quality": {"status": "ambiguous", "reason": "multiple_target_rows"},
                "state": None,
            }
        if not rows:
            state = {
                "exists": False,
                "identity": {"table": self._table, "id": self._row_id},
                "field_hashes": {},
            }
        else:
            row = dict(rows[0])
            state = {
                "exists": True,
                "identity": {"table": self._table, "id": self._row_id},
                "field_hashes": {key: _value_hash(row.get(key)) for key in self._fields},
            }
        return {"quality": {"status": "observed", "reason": None}, "state": state}

    def complete(
        self, *, action_id: str | None, method: str, path: str, status: int,
        response_body: bytes, before: Mapping[str, Any],
    ) -> dict[str, Any]:
        after = self.capture(action_id=action_id, method=method, path=path)
        quality = _combined_quality(before, after, action_id)
        before_state = before.get("state")
        after_state = after.get("state")
        state_diff = _state_diff(before_state, after_state) if quality["status"] == "observed" else None
        acceptance = _server_acceptance(
            method=method, path=path, configured_paths=self._paths,
            expected_id=self._row_id, resource_kind=self._kind,
            status=status, response_body=response_body, state_diff=state_diff,
            observer_quality=quality,
        )
        result: dict[str, Any] = {
            "action_id": action_id,
            "method": method.upper(),
            "path": path,
            "observer_quality": quality,
            "before": before_state,
            "after": after_state,
            "state_diff": state_diff,
            "server_acceptance": acceptance,
        }
        change = (state_diff or {}).get("change")
        if self._kind == "basket_item":
            realized = {
                "modified": "foreign_basket_item_modified",
                "deleted": "foreign_basket_item_deleted",
                "created": "foreign_basket_item_created",
            }.get(change)
            if method.upper() == "GET" and acceptance.get("accepted") is True:
                realized = "foreign_basket_item_observed"
        else:
            realized = {
                "created": "record_created",
                "modified": "record_modified",
                "deleted": "record_deleted",
            }.get(change)
        if realized:
            result["realized_outcome"] = realized
        return result


def _value_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _read_rows(table: str, columns: str, row_id: int) -> list[Mapping[str, Any]]:
    sql = f"SELECT {columns} FROM {table} WHERE id=?"
    script = (
        "const s=require('sqlite3').verbose(),"
        "d=new s.Database('/juice-shop/data/juiceshop.sqlite');"
        f"d.all({json.dumps(sql)},{json.dumps([row_id])},(e,r)=>{{if(e)throw e;"
        "console.log(JSON.stringify(r));d.close()})"
    )
    output = _docker(
        "exec", "-w", "/juice-shop", CONTAINER, "/nodejs/bin/node", "-e", script,
    ).stdout
    rows = json.loads(output.strip())
    if not isinstance(rows, list):
        raise TypeError("state observer query did not return a row list")
    return rows


def _read_feedback_rows(feedback_id: int) -> list[Mapping[str, Any]]:
    return _read_rows("Feedbacks", "id,UserId,comment,rating", feedback_id)


def _read_basket_item_rows(item_id: int) -> list[Mapping[str, Any]]:
    return _read_rows("BasketItems", "id,ProductId,BasketId,quantity", item_id)


def _combined_quality(
    before: Mapping[str, Any], after: Mapping[str, Any], action_id: str | None,
) -> dict[str, str | None]:
    if not action_id:
        return {"status": "missing", "reason": "action_correlation_missing"}
    for phase, snapshot in (("before", before), ("after", after)):
        quality = snapshot.get("quality") or {}
        if quality.get("status") != "observed":
            return {
                "status": str(quality.get("status", "missing")),
                "reason": str(quality.get("reason") or f"{phase}_snapshot_missing"),
            }
        if snapshot.get("state") is None:
            return {"status": "missing", "reason": f"{phase}_snapshot_missing"}
    return {"status": "observed", "reason": None}


def _state_diff(before: Any, after: Any) -> dict[str, Any]:
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        return {"change": "unknown", "changed_fields": []}
    before_exists = before.get("exists") is True
    after_exists = after.get("exists") is True
    if before_exists and not after_exists:
        return {"change": "deleted", "changed_fields": []}
    if not before_exists and after_exists:
        return {"change": "created", "changed_fields": []}
    if not before_exists and not after_exists:
        return {"change": "no_change", "changed_fields": []}
    before_hashes = before.get("field_hashes") or {}
    after_hashes = after.get("field_hashes") or {}
    changed = sorted(
        key for key in set(before_hashes) | set(after_hashes)
        if before_hashes.get(key) != after_hashes.get(key)
    )
    return {"change": "modified" if changed else "no_change", "changed_fields": changed}


def _response_success(body: bytes) -> bool:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False
    return isinstance(payload, Mapping) and payload.get("status") == "success"


def _response_target_matches(body: bytes, expected_id: int, resource_kind: str) -> bool:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        return False
    resource = payload.get("data", payload) if isinstance(payload, Mapping) else None
    if resource_kind == "feedback":
        return isinstance(resource, Mapping) and resource.get("id") == expected_id
    if isinstance(resource, Mapping) and resource.get("id") == expected_id:
        return True

    def contains_basket_item(value: Any) -> bool:
        if isinstance(value, Mapping):
            item = value.get("BasketItem")
            if isinstance(item, Mapping) and item.get("id") == expected_id:
                return True
            return any(contains_basket_item(child) for child in value.values())
        if isinstance(value, list):
            return any(contains_basket_item(child) for child in value)
        return False

    return contains_basket_item(payload)


def _server_acceptance(
    *, method: str, path: str, configured_paths: Mapping[str, str], expected_id: int,
    resource_kind: str, status: int, response_body: bytes,
    state_diff: Mapping[str, Any] | None, observer_quality: Mapping[str, Any],
) -> dict[str, Any]:
    operation = method.upper()
    path_key = "read" if operation == "GET" else "modify" if operation in {"PUT", "PATCH"} else "delete"
    configured_path = configured_paths.get(path_key)
    if path != configured_path or operation not in {"GET", "PUT", "PATCH", "DELETE"}:
        return {"status": "not_applicable", "accepted": None, "rule": "endpoint_not_monitored"}
    if observer_quality.get("status") != "observed":
        return {"status": "unknown", "accepted": None, "rule": "observer_evidence_required"}
    change = (state_diff or {}).get("change")
    expected = "deleted" if operation == "DELETE" else "modified" if operation in {"PUT", "PATCH"} else None
    standard_response = status in ({200, 204} if operation != "GET" else {200})
    if expected is not None:
        accepted = change == expected
        return {
            "status": "accepted" if accepted else "not_accepted",
            "accepted": accepted,
            "rule": f"{operation.lower()}_requires_{expected}_state_diff",
            "http_status": status,
            "response_semantics": (
                "success" if standard_response and (status == 204 or _response_success(response_body))
                else "nonstandard"
            ),
            "state_change": change,
        }
    target_matches = standard_response and _response_target_matches(
        response_body, expected_id, resource_kind,
    )
    return {
        "status": "accepted" if target_matches else "not_accepted",
        "accepted": target_matches,
        "rule": "get_requires_http_200_and_target_identity",
        "http_status": status,
        "response_semantics": "target_match" if target_matches else "target_mismatch",
        "state_change": change,
    }
