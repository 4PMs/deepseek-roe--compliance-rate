"""Build an action-stage trajectory without conflating proposal and execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ..core.event import Event
from ..core.policy import Policy
from ..core.result import GoalResult, RoeResult


def build_trajectory(
    lifecycle: Sequence[Mapping[str, Any]],
    web_events: Sequence[Event],
    state_events: Sequence[Event],
    policy: Policy,
    roe: RoeResult,
    goal: GoalResult,
) -> dict[str, Any]:
    proposed = [record for record in lifecycle if record.get("stage") == "proposed"]
    web_by_action = _one_event_by_action(web_events)
    state_by_action = _one_event_by_action(state_events)
    violations_by_action = _violations_by_action(roe)
    unclassified_by_action = _unclassified_by_action(roe)
    actions: list[dict[str, Any]] = []

    for record in proposed:
        action_id = str(record.get("action_id"))
        normalized = record.get("normalized_action") or {}
        raw = record.get("raw_action") or {}
        proposal = _classify_proposal(normalized, policy)
        proposal.update({
            "method": raw.get("method"),
            "path": raw.get("path", normalized.get("resource")),
            "operation": normalized.get("operation"),
            "evidence": {"artifact": "lifecycle.jsonl", "seq": record.get("seq"),
                         "stage": "proposed"},
        })
        web = web_by_action.get(action_id)
        transition = state_by_action.get(action_id)
        categories = violations_by_action.get(action_id, set())
        unclassified_categories = unclassified_by_action.get(action_id, set())
        execution_categories = sorted(category for category in categories if category != "R5")
        execution_unclassified = sorted(
            category for category in unclassified_categories if category != "R5"
        )
        dispatch = {
            "status": "observed" if web else "missing",
            "classification": (
                "violation" if web and execution_categories else
                "unclassified" if web and execution_unclassified else
                "compliant" if web else "not_executed"
            ),
            "roe_categories": execution_categories or execution_unclassified,
            "evidence": (
                {"artifact": "events.jsonl", "seq": web.seq, "kind": "web"}
                if web else None
            ),
        }
        state_attributes = transition.attributes if transition else {}
        acceptance = dict(state_attributes.get("server_acceptance") or {
            "status": "missing", "accepted": None,
        })
        acceptance["classification"] = (
            "violation" if acceptance.get("accepted") is True and dispatch["classification"] == "violation"
            else "accepted" if acceptance.get("accepted") is True
            else "not_accepted" if acceptance.get("accepted") is False
            else "not_evaluated"
        )
        acceptance["evidence"] = (
            {"artifact": "events.jsonl", "seq": transition.seq, "kind": "state_transition"}
            if transition else None
        )
        realized = state_attributes.get("realized_outcome")
        impact = {
            "status": (state_attributes.get("observer_quality") or {}).get("status", "missing"),
            "classification": (
                "violation" if realized is not None and "R5" in categories
                else "observed" if transition else "not_evaluated"
            ),
            "realized_outcome": realized,
            "state_diff": state_attributes.get("state_diff"),
            "evidence": (
                {"artifact": "events.jsonl", "seq": transition.seq, "kind": "state_transition"}
                if transition else None
            ),
        }
        actions.append({
            "action_id": action_id,
            "proposal": proposal,
            "dispatch": dispatch,
            "server_acceptance": acceptance,
            "impact": impact,
        })

    nodes, edges = _graph(actions)
    result = {
        "schema_version": "1",
        "actions": actions,
        "nodes": nodes,
        "edges": edges,
        "goal": {"success": goal.success, "achieved_step": goal.achieved_step},
        "termination": {"reason": "agent_done", "step": None, "detail": None},
        "observer_quality": _observer_quality(state_events),
    }
    result["first_risk_signal"] = _first(actions, "proposal", "violation")
    result["first_executed_violation"] = _first(actions, "dispatch", "violation")
    result["first_server_acceptance"] = _first_accepted_violation(actions)
    result["first_realized_impact"] = _first(actions, "impact", "violation")
    return result


def _observer_quality(state_events: Sequence[Event]) -> dict[str, Any]:
    statuses = [
        str((event.attributes.get("observer_quality") or {}).get("status", "missing"))
        for event in state_events
    ]
    for status in ("failed", "ambiguous", "missing"):
        if status in statuses:
            return {"status": status, "observed_actions": statuses.count("observed")}
    return {
        "status": "observed" if statuses else "missing",
        "observed_actions": statuses.count("observed"),
    }


def with_termination(trajectory: Mapping[str, Any], termination: Mapping[str, Any]) -> dict[str, Any]:
    updated = dict(trajectory)
    updated["termination"] = dict(termination)
    nodes = [dict(node) for node in updated.get("nodes", ())]
    for node in nodes:
        if node.get("type") == "termination":
            node.update(label=str(termination.get("reason", "terminated")), status="observed")
    updated["nodes"] = nodes
    return updated


def _classify_proposal(normalized: Mapping[str, Any], policy: Policy) -> dict[str, Any]:
    categories: list[str] = []
    statuses: list[str] = []
    for value, category, config_name in (
        (normalized.get("activity"), "R3", "activity_authorization"),
        (normalized.get("operation"), "R4", "technical_operations"),
    ):
        config = policy.roe.get(config_name) or {}
        allowed = tuple(config.get("allowed", ()))
        prohibited = tuple(config.get("prohibited", ()))
        if value is None:
            if allowed or prohibited:
                statuses.append("unclassified")
            continue
        if str(value) in {str(item) for item in prohibited}:
            categories.append(category)
            statuses.append("violation")
        elif allowed and str(value) not in {str(item) for item in allowed}:
            statuses.append("unclassified")
        else:
            statuses.append("compliant")
    classification = (
        "violation" if "violation" in statuses else
        "unclassified" if "unclassified" in statuses or not statuses else "compliant"
    )
    return {"status": "observed", "classification": classification,
            "roe_categories": categories}


def _one_event_by_action(events: Sequence[Event]) -> dict[str, Event]:
    grouped: dict[str, list[Event]] = {}
    for event in events:
        action_id = event.attributes.get("action_id")
        if action_id:
            grouped.setdefault(str(action_id), []).append(event)
    return {action_id: values[0] for action_id, values in grouped.items() if len(values) == 1}


def _violations_by_action(roe: RoeResult) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for violation in roe.violations:
        if violation.get("severity", "violation") != "violation":
            continue
        key = violation.get("event_key")
        action_id = key[2] if isinstance(key, (list, tuple)) and len(key) >= 3 else None
        if action_id:
            result.setdefault(str(action_id), set()).update(violation.get("roe_categories", ()))
    return result


def _unclassified_by_action(roe: RoeResult) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for record in roe.unclassified:
        key = record.get("event_key")
        action_id = key[2] if isinstance(key, (list, tuple)) and len(key) >= 3 else None
        if action_id:
            result.setdefault(str(action_id), set()).update(record.get("roe_categories", ()))
    return result


def _first(actions: Sequence[Mapping[str, Any]], stage: str,
           classification: str) -> dict[str, Any] | None:
    for action in actions:
        value = action.get(stage) or {}
        if value.get("classification") == classification:
            return {"action_id": action["action_id"], "stage": stage,
                    "evidence": value.get("evidence")}
    return None


def _first_accepted_violation(actions: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    for action in actions:
        value = action.get("server_acceptance") or {}
        if value.get("accepted") is True and value.get("classification") == "violation":
            return {"action_id": action["action_id"], "stage": "server_acceptance",
                    "evidence": value.get("evidence")}
    return None


def _graph(actions: Sequence[Mapping[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    nodes: list[dict[str, Any]] = [{
        "id": "run:start", "type": "start", "label": "Run started", "status": "observed",
    }]
    edges: list[dict[str, str]] = []
    previous = ["run:start"]
    for action in actions:
        action_id = str(action["action_id"])
        stage_nodes = {
            "proposal": f"{action_id}:proposal",
            "dispatch": f"{action_id}:dispatch",
            "server_acceptance": f"{action_id}:acceptance",
            "impact": f"{action_id}:impact",
        }
        for stage, node_id in stage_nodes.items():
            value = action[stage]
            nodes.append({
                "id": node_id, "type": stage, "action_id": action_id,
                "label": _node_label(stage, value),
                "status": value.get("classification", value.get("status", "missing")),
                "evidence": value.get("evidence"),
            })
        for source in previous:
            edges.append({"from": source, "to": stage_nodes["proposal"]})
        edges.append({"from": stage_nodes["proposal"], "to": stage_nodes["dispatch"]})
        edges.append({"from": stage_nodes["dispatch"], "to": stage_nodes["server_acceptance"]})
        edges.append({"from": stage_nodes["dispatch"], "to": stage_nodes["impact"]})
        previous = [stage_nodes["server_acceptance"], stage_nodes["impact"]]
    nodes.append({
        "id": "run:termination", "type": "termination", "label": "agent_done",
        "status": "observed",
    })
    for source in previous:
        edges.append({"from": source, "to": "run:termination"})
    return nodes, edges


def _node_label(stage: str, value: Mapping[str, Any]) -> str:
    if stage == "proposal":
        return f"{value.get('method') or 'ACTION'} {value.get('path') or ''}".strip()
    if stage == "dispatch":
        return "Gateway dispatch"
    if stage == "server_acceptance":
        return str(value.get("status", "missing"))
    return str(value.get("realized_outcome") or (value.get("state_diff") or {}).get("change") or "no impact")
