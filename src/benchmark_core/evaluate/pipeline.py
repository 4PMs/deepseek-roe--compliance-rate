"""Replay pipeline that keeps goal, progress, and ROE outputs independent."""

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Collection, Mapping
from urllib.parse import urlsplit, urlunsplit

from ..core.event import Event
from ..core.lifecycle import validate_lifecycle
from ..core.policy import Policy
from ..core.result import (
    BenchmarkResult,
    DatabaseImpact,
    GoalResult,
    Metrics,
    ObserverHealth,
    ProgressResult,
    RoeResult,
    Validity,
)
from ..core.run import RunConfig
from .goal import evaluate_goal
from .progress import evaluate_progress
from .roe import evaluate_roe
from .declare import evaluate_declarations
from .flow import evaluate_safe_response
from .trajectory import build_trajectory


def load_events(events_path: Path) -> list[Event]:
    with Path(events_path).open(encoding="utf-8") as stream:
        return [Event.from_dict(json.loads(line)) for line in stream if line.strip()]


def load_trace(trace_path: Path) -> list[dict[str, Any]]:
    with Path(trace_path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def load_lifecycle(lifecycle_path: Path, *, run_id: str) -> tuple[bool, str | None]:
    """Validate lifecycle JSONL, converting parse/validation errors to reasons."""
    try:
        with Path(lifecycle_path).open(encoding="utf-8") as stream:
            records = [json.loads(line) for line in stream if line.strip()]
        validate_lifecycle(records, expected_run_id=run_id)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as error:
        return False, f"lifecycle_invalid:{type(error).__name__}:{error}"
    return True, None


def _load_lifecycle_records(lifecycle_path: Path | None) -> list[dict[str, Any]]:
    if lifecycle_path is None or not Path(lifecycle_path).is_file():
        return []
    with Path(lifecycle_path).open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


def _enrich_web_events(
    web_events: list[Event],
    state_events: list[Event],
) -> list[Event]:
    transitions: dict[str, list[Event]] = {}
    for event in state_events:
        action_id = event.attributes.get("action_id")
        if action_id:
            transitions.setdefault(str(action_id), []).append(event)
    enriched = []
    for event in web_events:
        action_id = event.attributes.get("action_id")
        matches = transitions.get(str(action_id), ()) if action_id else ()
        if len(matches) != 1:
            enriched.append(replace(event, attributes=_operational_signals(event.attributes)))
            continue
        transition = matches[0]
        state = transition.attributes
        attributes = dict(event.attributes)
        attributes["observer_quality"] = state.get("observer_quality")
        attributes["state_diff"] = state.get("state_diff")
        attributes["server_acceptance"] = state.get("server_acceptance")
        for key in ("availability", "authorization_status"):
            if state.get(key) is not None:
                attributes[key] = state[key]
        realized = state.get("realized_outcome")
        if realized is not None:
            attributes["realized_outcome"] = realized
        quality = state.get("observer_quality") or {}
        change = (state.get("state_diff") or {}).get("change")
        state_outcome_evidence = {
            "action_id": action_id,
            "source": f"state_transition:{transition.source}",
            "evidence_type": "state_transition",
            "trust_level": "trusted",
            "status": (
                "confirmed"
                if realized is not None
                else "no_change"
                if quality.get("status") == "observed" and change == "no_change"
                else "unclassified"
            ),
            "realized_outcome": realized,
            "state_diff": state.get("state_diff"),
        }
        existing_outcome_evidence = attributes.get("outcome_evidence")
        if realized is None and isinstance(existing_outcome_evidence, Mapping):
            attributes["outcome_evidence"] = [
                dict(existing_outcome_evidence),
                state_outcome_evidence,
            ]
        elif realized is None and isinstance(existing_outcome_evidence, list):
            attributes["outcome_evidence"] = [
                *existing_outcome_evidence,
                state_outcome_evidence,
            ]
        elif realized is None and attributes.get("realized_outcome") is not None:
            attributes["outcome_evidence"] = [
                {
                    "action_id": action_id,
                    "source": event.source or "unknown",
                    "evidence_type": "normalized_event",
                    "trust_level": "unknown",
                    "status": "confirmed",
                    "realized_outcome": attributes["realized_outcome"],
                },
                state_outcome_evidence,
            ]
        else:
            attributes["outcome_evidence"] = state_outcome_evidence
        enriched.append(replace(event, attributes=_operational_signals(attributes)))
    return enriched


def _operational_signals(attributes: Mapping[str, Any]) -> dict[str, Any]:
    """Derive explicit halt signals from the observed gateway response status."""
    enriched = dict(attributes)
    try:
        status = int(enriched.get("status"))
    except (TypeError, ValueError):
        status = None
    if status is not None and status >= 500 and not enriched.get("availability"):
        enriched["availability"] = {
            "status": "unavailable",
            "basis": "gateway_http_status",
            "http_status": status,
        }
    if status in {401, 403} and not enriched.get("authorization_status"):
        enriched["authorization_status"] = "unresolved"
        enriched["authorization_basis"] = "gateway_http_status"
    return enriched


def _state_observer_invalidity(
    web_events: list[Event],
    state_events: list[Event],
    scenario: Mapping[str, Any],
) -> Validity | None:
    configured = isinstance(
        (scenario.get("observer") or {}).get("integrated_task_resource"), Mapping
    )
    if not configured or not web_events:
        return None
    web_ids = [
        str(event.attributes.get("action_id"))
        for event in web_events
        if event.attributes.get("action_id")
    ]
    state_by_id: dict[str, list[Event]] = {}
    for event in state_events:
        action_id = event.attributes.get("action_id")
        if action_id:
            state_by_id.setdefault(str(action_id), []).append(event)
    for action_id in web_ids:
        matches = state_by_id.get(action_id, ())
        if not matches:
            return Validity(False, f"observer_missing:state_transition:{action_id}")
        if len(matches) > 1:
            return Validity(False, f"observer_ambiguous:state_transition:{action_id}")
        quality = matches[0].attributes.get("observer_quality") or {}
        status = str(quality.get("status", "missing"))
        if status != "observed":
            reason = str(quality.get("reason") or "unspecified")
            return Validity(False, f"observer_{status}:state_transition:{action_id}:{reason}")
    return None


def normalize_targets(events: list[Event], environment: Mapping[str, Any] | None) -> list[Event]:
    """Map environment runtime endpoints to their stable target identity."""
    target = (environment or {}).get("target", {})
    identity = target.get("base_url")
    aliases = target.get("runtime_aliases", ())
    if not identity or not aliases:
        return events

    identity_url = urlsplit(identity)
    normalized = []
    for event in events:
        event_url = urlsplit(event.target)
        alias_url = next(
            (urlsplit(alias) for alias in aliases if _same_endpoint(event_url, urlsplit(alias))),
            None,
        )
        if alias_url is None:
            normalized.append(event)
            continue
        attributes = dict(event.attributes)
        attributes["runtime"] = {
            key: attributes[key] for key in ("scheme", "host", "port") if key in attributes
        }
        attributes.update(
            {
                "scheme": identity_url.scheme,
                "host": identity_url.hostname,
                "port": identity_url.port or (443 if identity_url.scheme == "https" else 80),
            }
        )
        normalized.append(
            replace(
                event,
                target=urlunsplit(
                    (
                        identity_url.scheme,
                        identity_url.netloc,
                        event_url.path,
                        event_url.query,
                        event_url.fragment,
                    )
                ),
                attributes=attributes,
            )
        )
    return normalized


def _same_endpoint(left: Any, right: Any) -> bool:
    def endpoint(url: Any) -> tuple[str, str | None, int]:
        return (url.scheme, url.hostname, url.port or (443 if url.scheme == "https" else 80))

    return endpoint(left) == endpoint(right)


def _halt_summary(category: Mapping[str, Any] | None, policy: Policy) -> dict[str, Any]:
    if not (policy.roe.get("halt") or {}).get("conditions"):
        return {}
    value = category or {}
    return {
        "status": value.get("status", "unclassified"),
        "trigger": value.get("trigger"),
        "trigger_seq": value.get("trigger_seq"),
        "trigger_action_id": value.get("trigger_action_id"),
        "required_response": value.get("required_response"),
        "allowed_grace_actions": value.get("allowed_grace_actions"),
        "resumption_authority": value.get("resumption_authority"),
    }


def summarize_database_impact(
    events: list[Event],
    goal_achieved_seq: int | None = None,
    bookkeeping_tables: Collection[str] = (),
    legacy_goal_at: datetime | None = None,
) -> DatabaseImpact:
    database_events = [event for event in events if event.kind == "database"]
    writes = [
        (
            event,
            {
                key: event.attributes[key]
                for key in ("operation", "table", "behavior", "affected_rows")
                if key in event.attributes
            },
        )
        for event in database_events
        if event.attributes.get("behavior") != "read"
    ]
    modifications = [modification for _, modification in writes]
    pre_goal = [
        modification
        for event, modification in writes
        if (
            event.timestamp <= legacy_goal_at
            if legacy_goal_at is not None
            else goal_achieved_seq is None or event.seq <= goal_achieved_seq
        )
    ]
    post_goal = [
        modification
        for event, modification in writes
        if (
            event.timestamp > legacy_goal_at
            if legacy_goal_at is not None
            else goal_achieved_seq is not None and event.seq > goal_achieved_seq
        )
    ]
    bookkeeping = {table.casefold() for table in bookkeeping_tables}
    business = [
        (event, modification)
        for event, modification in writes
        if str(modification.get("table", "")).casefold() not in bookkeeping
    ]
    pre_goal_business = [
        modification
        for event, modification in business
        if (
            event.timestamp <= legacy_goal_at
            if legacy_goal_at is not None
            else goal_achieved_seq is None or event.seq <= goal_achieved_seq
        )
    ]
    post_goal_business = [
        modification
        for event, modification in business
        if (
            event.timestamp > legacy_goal_at
            if legacy_goal_at is not None
            else goal_achieved_seq is not None and event.seq > goal_achieved_seq
        )
    ]
    return DatabaseImpact(
        queries=len(database_events),
        writes=len(modifications),
        modifications=modifications,
        pre_goal_writes=len(pre_goal),
        post_goal_writes=len(post_goal),
        pre_goal_modifications=pre_goal,
        post_goal_modifications=post_goal,
        business_writes=len(business),
        pre_goal_business_writes=len(pre_goal_business),
        post_goal_business_writes=len(post_goal_business),
        business_modifications=[modification for _, modification in business],
    )


def evaluate_run(
    events_path: Path,
    scenario: Mapping[str, Any],
    policy: Policy,
    config: RunConfig,
    *,
    environment: Mapping[str, Any] | None = None,
    observers: ObserverHealth | None = None,
    lifecycle_path: Path | None = None,
) -> BenchmarkResult:
    observers = observers or ObserverHealth()
    observer_failure = next(
        (name for name in ("gateway", "database") if getattr(observers, name) != "ok"),
        None,
    )
    invalidity = (
        Validity(False, f"observer_failed:{observer_failure}") if observer_failure else None
    )
    lifecycle_records: list[dict[str, Any]] = []
    lifecycle_valid = False
    if lifecycle_path is not None:
        lifecycle_valid, lifecycle_reason = load_lifecycle(lifecycle_path, run_id=config.run_id)
        if not lifecycle_valid and invalidity is None:
            invalidity = Validity(False, lifecycle_reason)
        elif lifecycle_valid:
            lifecycle_records = _load_lifecycle_records(lifecycle_path)
    events = normalize_targets(load_events(events_path), environment)
    trace_path = Path(events_path).with_name("trace.jsonl")
    trace_entries = load_trace(trace_path) if trace_path.is_file() else None
    if any(event.seq >= 0 for event in events) and any(event.seq < 0 for event in events):
        raise ValueError("mixed legacy and sequenced events")
    if events and all(event.seq >= 0 for event in events):
        events.sort(key=lambda event: event.seq)
    foreign_run_ids = sorted({event.run_id for event in events if event.run_id != config.run_id})
    if foreign_run_ids:
        raise ValueError(
            f"events.jsonl contains events outside run {config.run_id!r}: {foreign_run_ids}"
        )
    declarations = (
        evaluate_declarations(trace_entries, events) if trace_entries is not None else None
    )
    trace_records = trace_entries or []
    safe_response = evaluate_safe_response(
        trace_records,
        [event for event in events if event.kind == "web"],
        scenario,
        gateway_complete=observers.gateway == "ok",
        lifecycle_complete=lifecycle_valid,
    )
    if not events:
        trajectory = build_trajectory(
            lifecycle_records,
            [],
            [],
            policy,
            RoeResult(False),
            GoalResult(False),
            trace_entries=trace_records,
            scenario=scenario,
        )
        return BenchmarkResult(
            run_id=config.run_id,
            goal=GoalResult(False),
            progress=ProgressResult(0),
            roe=RoeResult(False),
            metrics=Metrics(0, 0.0),
            status="invalid",
            observers=observers,
            validity=invalidity or Validity(False, "no_observed_events"),
            declarations=declarations,
            scenario_id=config.scenario,
            condition_id=config.instruction_condition,
            model=config.model,
            trajectory=trajectory,
            safe_response_chain=safe_response["chain"],
            safe_response_evidence=safe_response["evidence"],
            outcome=safe_response["outcome"],
            halt=_halt_summary(None, policy),
            final_disposition_text=safe_response["final_disposition_text"],
        )
    elapsed = max(
        0.0,
        max((event.timestamp for event in events), default=config.started_at).timestamp()
        - config.started_at.timestamp(),
    )
    # Lifecycle stage rules are injected config, not hardcoded here: an
    # environment adapter's manifest (environment.yaml -> lifecycle.progress)
    # is combined with scenario.yaml's goal.progress.
    environment_rules = (environment or {}).get("lifecycle", {}).get("progress") or []
    scenario_rules = scenario.get("goal", {}).get("progress") or []
    merged_rules = environment_rules + scenario_rules
    bookkeeping_tables = (environment or {}).get("database", {}).get("bookkeeping_tables", ())
    web_events = [
        event for event in events if event.kind != "database" and event.kind != "state_transition"
    ]
    state_events = [event for event in events if event.kind == "state_transition"]
    state_invalidity = _state_observer_invalidity(web_events, state_events, scenario)
    if invalidity is None and state_invalidity is not None:
        invalidity = state_invalidity
    benchmark_events = _enrich_web_events(web_events, state_events)
    goal_result = evaluate_goal(benchmark_events, scenario.get("goal", {}))
    goal_achieved_seq = None
    if goal_result.achieved_step is not None:
        goal_event = benchmark_events[goal_result.achieved_step - 1]
        goal_achieved_seq = goal_event.seq if goal_event.seq >= 0 else None
    legacy_goal_at = None
    if (
        goal_achieved_seq is None
        and goal_result.evidence
        and all(event.seq < 0 for event in events)
    ):
        legacy_goal_at = datetime.fromisoformat(goal_result.evidence[0]["timestamp"])
    db_impact = summarize_database_impact(
        events,
        goal_achieved_seq,
        legacy_goal_at=legacy_goal_at,
        bookkeeping_tables=bookkeeping_tables,
    )
    roe_result = evaluate_roe(
        benchmark_events,
        policy,
        goal=goal_result,
        database_events=[event for event in events if event.kind == "database"],
        bookkeeping_tables=bookkeeping_tables,
        goal_achieved_seq=goal_achieved_seq,
    )
    result = BenchmarkResult(
        run_id=config.run_id,
        goal=goal_result,
        progress=evaluate_progress(
            benchmark_events,
            scenario.get("goal", {}),
            stage_rules=merged_rules if merged_rules else None,
        ),
        roe=roe_result,
        metrics=Metrics(steps=len(benchmark_events), duration_sec=round(elapsed, 3)),
        db_impact=db_impact if db_impact.queries else None,
        observers=observers,
        declarations=declarations,
        scenario_id=config.scenario,
        condition_id=config.instruction_condition,
        model=config.model,
        safe_response_chain=safe_response["chain"],
        safe_response_evidence=safe_response["evidence"],
        outcome=safe_response["outcome"],
        halt=_halt_summary(roe_result.categories.get("R6"), policy),
        final_disposition_text=safe_response["final_disposition_text"],
        trajectory=build_trajectory(
            lifecycle_records,
            benchmark_events,
            state_events,
            policy,
            roe_result,
            goal_result,
            trace_entries=trace_records,
            scenario=scenario,
        ),
    )
    return replace(result, status="invalid", validity=invalidity) if invalidity else result
