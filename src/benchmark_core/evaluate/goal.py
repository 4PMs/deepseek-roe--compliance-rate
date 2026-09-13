"""Final-goal evaluation over normalized events."""

from typing import Any, Mapping, Sequence

from ..core.event import Event
from ..core.result import GoalResult
from .matching import evidence, matches


def evaluate_goal(events: Sequence[Event], goal: Mapping[str, Any]) -> GoalResult:
    conditions = goal.get("success_when_all")
    if not isinstance(conditions, Sequence) or isinstance(conditions, (str, bytes)):
        conditions = [goal.get("success_when", {})]
    marker = goal.get("marker")
    findings = []
    for condition in conditions:
        matches_for_condition = [
            evidence(index, event) for index, event in enumerate(events)
            if condition and matches(event, condition)
            and (not marker or marker in event.attributes.get("matched_markers", ()))
        ]
        if not matches_for_condition:
            return GoalResult(False)
        findings.append(matches_for_condition[0])
    achieved_step = max((item["event_index"] for item in findings), default=-1) + 1
    return GoalResult(
        success=bool(findings),
        evidence=findings,
        achieved_step=achieved_step if findings else None,
    )
