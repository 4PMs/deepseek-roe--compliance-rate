"""Web observer; allocate seq at request start, before the upstream call."""

import argparse
from contextlib import AbstractContextManager, nullcontext
from datetime import datetime, timezone
import importlib
from pathlib import Path
import re
import secrets
import threading
import time
from typing import Any, Callable, Mapping
from urllib.parse import SplitResult, urlsplit, urlunsplit

import requests
from flask import Flask, Response, request

from .normalizer import Observer, RawObservation, normalize_attributes
from ..core.event import Event
from ..core.lifecycle import LifecycleEvent
from ..core.run import RunConfig, RunStore
from ..normalize import normalize_action

HTTP_METHODS = ["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]
DROP_RESPONSE_HEADERS = {"connection", "content-encoding", "content-length", "transfer-encoding"}
EventSink = Callable[[Event], None]


class ActionBindingRegistry:
    """Run-scoped, single-use server-issued action/request bindings."""

    def __init__(self, *, ttl_seconds: float = 60.0):
        self._ttl = ttl_seconds
        self._pending: dict[str, tuple[str, str, str, float]] = {}
        self._lock = threading.Lock()

    def register(self, run_id: str, action_id: str, decision: str) -> str:
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._cleanup()
            self._pending[token] = (run_id, action_id, decision, time.monotonic() + self._ttl)
        return token

    def consume(self, run_id: str, action_id: str, token: str) -> tuple[bool, str | None]:
        with self._lock:
            self._cleanup()
            binding = self._pending.pop(token, None)
        if binding is None:
            return False, "missing_or_replayed_token"
        if binding[:3] != (run_id, action_id, "allow"):
            return False, "invalid_action_binding"
        return True, None

    def close(self, run_id: str, action_id: str) -> None:
        with self._lock:
            self._pending = {
                token: binding
                for token, binding in self._pending.items()
                if binding[:2] != (run_id, action_id)
            }

    def _cleanup(self) -> None:
        now = time.monotonic()
        self._pending = {
            token: binding for token, binding in self._pending.items() if binding[3] > now
        }


class WebObserver(Observer):
    def normalize(self, run_id: str, observation: RawObservation, *, seq: int) -> Event:
        attributes = {
            key: value for key, value in observation.facts.items() if not key.startswith("_")
        }
        return Event(
            schema_version="0.2",
            run_id=run_id,
            timestamp=observation.timestamp,
            actor=observation.actor,
            source="gateway",
            kind="web",
            action="request",
            target=observation.target,
            seq=seq,
            attributes=normalize_attributes(attributes),
        )


def safe_run_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", value)[:100] or "run-adhoc"


def create_app(
    upstream: str,
    run_id: str,
    actor: str,
    event_sink: EventSink,
    observer: Observer | None = None,
    timeout: int = 20,
    tls: Mapping[str, Any] | None = None,
    request_scope: Callable[[str | None], AbstractContextManager[Any]] | None = None,
    sequence_allocator: Any = None,
    lifecycle_sink: Callable[[LifecycleEvent], None] | None = None,
    action_registry: ActionBindingRegistry | None = None,
    enforce_policy: bool = False,
    state_observer: Any = None,
) -> Flask:
    upstream_url = _web_url(upstream)
    if enforce_policy and action_registry is None:
        action_registry = ActionBindingRegistry()
    tls_config = {"mode": "passthrough", "verify": True, **(tls or {})}
    if tls_config["mode"] != "passthrough":
        raise ValueError("only tls.mode=passthrough is currently supported")
    app = Flask(__name__)
    normalizer = observer or WebObserver()

    @app.route("/", defaults={"path": ""}, methods=HTTP_METHODS)
    @app.route("/<path:path>", methods=HTTP_METHODS)
    def forward_request(path: str) -> Response:
        seq = sequence_allocator.next() if sequence_allocator else -1
        request_started_at = datetime.now(timezone.utc)
        request_path = "/" + path
        query = request.query_string.decode("utf-8", "replace")
        target_path = upstream_url.path.rstrip("/") + request_path
        target = urlunsplit(
            (
                upstream_url.scheme,
                upstream_url.netloc,
                target_path,
                query,
                "",
            )
        )
        headers = {key: value for key, value in request.headers if key.lower() != "host"}
        action_id = next(
            (value for key, value in headers.items() if key.lower() == "x-action-id"), None
        )
        headers = {key: value for key, value in headers.items() if key.lower() != "x-action-id"}
        correlation_token = next(
            (
                value
                for key, value in request.headers.items()
                if key.lower() == "x-correlation-token"
            ),
            None,
        )
        bound_action_id = action_id
        if action_registry is not None:
            valid_binding = bool(
                correlation_token
                and action_id
                and action_registry.consume(run_id, action_id, correlation_token)[0]
            )
            if not valid_binding:
                if enforce_policy:
                    return Response("invalid action correlation", 403)
                bound_action_id = None
        status = 502
        body = b"upstream unavailable"
        response_headers: list[tuple[str, str]] = [("Content-Type", "text/plain")]
        facts = {
            "scheme": upstream_url.scheme,
            "host": upstream_url.hostname,
            "port": upstream_url.port or (443 if upstream_url.scheme == "https" else 80),
            "method": request.method,
            "path": target_path,
            "destination_host": upstream_url.hostname,
            "destination_port": upstream_url.port
            or (443 if upstream_url.scheme == "https" else 80),
            "application": "http",
            "resource": target_path,
            "tool_name": "http_request",
            "tool_type": "http_request",
            "query": query,
            "request_size": len(request.get_data()),
            "_request_body": request.get_data(),
        }

        before_snapshot = None
        if state_observer is not None:
            try:
                before_snapshot = state_observer.capture(
                    action_id=bound_action_id,
                    method=request.method,
                    path=target_path,
                )
            except Exception as error:
                before_snapshot = {
                    "quality": {"status": "failed", "reason": type(error).__name__},
                    "state": None,
                }

        try:
            with request_scope(bound_action_id) if request_scope is not None else nullcontext():
                response = requests.request(
                    method=request.method,
                    url=target,
                    headers=headers,
                    data=request.get_data(),
                    cookies=request.cookies,
                    allow_redirects=False,
                    timeout=timeout,
                    verify=bool(tls_config["verify"]),
                )
            status = response.status_code
            body = response.content or b""
            response_headers = [
                (key, value)
                for key, value in response.raw.headers.items()
                if key.lower() not in DROP_RESPONSE_HEADERS
            ]
        except requests.RequestException as error:
            facts["upstream_error"] = type(error).__name__

        facts.update(
            {
                "status": status,
                "response_size": len(body),
                "request_completed_at": datetime.now(timezone.utc).isoformat(),
                "response_content_type": next(
                    (value for key, value in response_headers if key.lower() == "content-type"),
                    None,
                ),
                "_response_body": body,
            }
        )
        canonical_action = normalize_action(
            {
                "tool": {
                    "name": facts.get("tool_name", "http_request"),
                    "type": facts.get("tool_type", "http_request"),
                },
                "method": request.method,
                "path": target_path,
                "url": target,
                "activity": facts.get("activity"),
            }
        ).to_dict()
        facts.update(
            {
                "action_id": bound_action_id,
                "raw_tool_name": facts.get("tool_name"),
                "canonical_tool_name": canonical_action["tool"].get("name"),
                "canonical_tool_family": canonical_action["tool"].get("family"),
                "canonical_intent": canonical_action.get("intent"),
                "normalization_status": canonical_action.get("normalization_status"),
                "canonical_action": canonical_action,
            }
        )
        try:
            event_sink(
                normalizer.normalize(
                    run_id,
                    RawObservation(
                        timestamp=request_started_at,
                        actor=actor,
                        source="gateway",
                        kind="web",
                        action="request",
                        target=target,
                        facts=facts,
                    ),
                    seq=seq,
                )
            )
            if lifecycle_sink and bound_action_id:
                lifecycle_sink(
                    LifecycleEvent.now(
                        run_id=run_id,
                        seq=seq,
                        action_id=bound_action_id,
                        actor=actor,
                        source="gateway",
                        stage="observed",
                        reference={"event_seq": seq},
                        normalized_action=normalize_action(
                            {
                                "tool": {
                                    "name": facts.get("tool_name", "http_request"),
                                    "type": facts.get("tool_type", "http_request"),
                                },
                                "method": request.method,
                                "path": target_path,
                                "url": target,
                                "activity": facts.get("activity"),
                                "operation": facts.get("operation"),
                            }
                        ).to_dict(),
                    )
                )
            if state_observer is not None:
                try:
                    transition_attributes = state_observer.complete(
                        action_id=bound_action_id,
                        method=request.method,
                        path=target_path,
                        status=status,
                        response_body=body,
                        before=before_snapshot,
                    )
                except Exception as error:
                    transition_attributes = {
                        "action_id": bound_action_id,
                        "method": request.method,
                        "path": target_path,
                        "observer_quality": {
                            "status": "failed",
                            "reason": type(error).__name__,
                        },
                        "before": (before_snapshot or {}).get("state"),
                        "after": None,
                        "state_diff": None,
                        "server_acceptance": {
                            "status": "unknown",
                            "accepted": None,
                            "rule": "observer_evidence_required",
                        },
                    }
                event_sink(
                    Event(
                        schema_version="0.2",
                        run_id=run_id,
                        timestamp=datetime.now(timezone.utc),
                        actor="target",
                        source=str(getattr(state_observer, "source", "state_observer")),
                        kind="state_transition",
                        action="state_diff",
                        target=str(getattr(state_observer, "target", target)),
                        seq=seq,
                        attributes=normalize_attributes(transition_attributes),
                    )
                )
        except Exception:
            # Required event/lifecycle persistence owns run validity; never continue silently.
            raise
        return Response(body, status, response_headers)

    return app


def _web_url(value: str) -> SplitResult:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("upstream must be an absolute http(s) URL")
    parsed.port  # Validate a declared port at configuration load time.
    return parsed


# Compatibility for environment adapters importing the old name.
HttpObserver = WebObserver


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ROE Benchmark HTTP/HTTPS gateway observer")
    parser.add_argument("--upstream", required=True)
    parser.add_argument("--run", default="run-adhoc")
    parser.add_argument("--runs-dir", type=Path, default=Path("runs"))
    parser.add_argument("--config", type=Path)
    parser.add_argument(
        "--observer",
        help="optional adapter as module:class (for example environments.juice_shop.observer:JuiceShopObserver)",
    )
    parser.add_argument("--actor", choices=["agent", "human", "unknown"], default="unknown")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    run_id = safe_run_id(args.run)
    if args.config:
        import json

        config = RunConfig.from_dict(json.loads(args.config.read_text(encoding="utf-8")))
        if config.run_id != run_id:
            raise ValueError("--run must match config.json run_id")
    else:
        config = RunConfig(
            run_id=run_id,
            model="unknown",
            model_version="unknown",
            agent_version="poc",
            environment="unknown",
            scenario="adhoc",
            policy="adhoc",
            max_steps=8,
            timeout=20,
            started_at=datetime.now(timezone.utc),
        )
    store = RunStore(args.runs_dir, config)
    store.initialize()
    observer = _load_observer(args.observer) if args.observer else None
    app = create_app(
        args.upstream,
        run_id,
        args.actor,
        store.append_event,
        observer=observer,
        timeout=config.timeout,
    )
    print(f"[benchmark_core] run: {run_id}")
    print(f"[benchmark_core] upstream: {args.upstream}")
    print(f"[benchmark_core] events: {store.events_path}")
    app.run(host="0.0.0.0", port=8080, threaded=True)


def load_observer(reference: str, **options: Any) -> Observer:
    module_name, separator, class_name = reference.partition(":")
    if not separator:
        raise ValueError("observer must use module:class format")
    observer_class = getattr(importlib.import_module(module_name), class_name)
    observer = observer_class(**options)
    if not isinstance(observer, Observer):
        raise TypeError(f"{reference} does not implement Observer")
    return observer


# Backwards-compatible alias for the previous private name.
_load_observer = load_observer


if __name__ == "__main__":
    main()
