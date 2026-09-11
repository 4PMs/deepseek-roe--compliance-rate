from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.models import (
    DashboardRun,
    GoalAssessment,
    RoeAssessment,
    TimelineEvent,
    ViolationAssessment,
)
from ..services.attack_mapper import AttackMapper


class TemperaV01Adapter:
    """Read Tempera 0.1 artifacts into a dashboard-owned stable model."""

    def __init__(self, attack_mapper: AttackMapper | None = None) -> None:
        self.attack_mapper = attack_mapper or AttackMapper()

    def load(self, run_dir: Path) -> DashboardRun:
        run_dir = Path(run_dir)
        warnings: list[str] = []
        config = self._required_json(run_dir / "config.json")
        events = self._jsonl(run_dir / "events.jsonl", warnings)
        trace = self._jsonl(run_dir / "trace.jsonl", warnings)
        result = self._optional_json(run_dir / "result.json", warnings)
        condition = self._optional_json(run_dir / "condition.json", warnings, warn_missing=False) or {}

        violations = self._violations(result)
        by_event: dict[int, list[ViolationAssessment]] = {}
        for violation in violations:
            if violation.event_index is not None:
                by_event.setdefault(violation.event_index, []).append(violation)

        goal_doc = result.get("goal", {}) if result else {}
        roe_doc = result.get("roe", {}) if result else {}
        progress_doc = result.get("progress", {}) if result else {}
        goal_indexes = {
            item.get("event_index")
            for item in goal_doc.get("evidence", [])
            if isinstance(item, dict) and isinstance(item.get("event_index"), int)
        }

        timeline: list[TimelineEvent] = []
        for index, event in enumerate(events):
            event_violations = by_event.get(index, [])
            if any(item.severity != "attempt" for item in event_violations):
                roe_status = "violation"
            elif event_violations:
                roe_status = "attempt"
            elif result is not None:
                roe_status = "allowed"
            else:
                roe_status = "not_evaluated"
            attributes = event.get("attributes") if isinstance(event.get("attributes"), dict) else {}
            action = str(event.get("action", attributes.get("method", "unknown")))
            target = str(event.get("target", attributes.get("path", "unknown")))
            timeline.append(
                TimelineEvent(
                    event_index=index,
                    step=index + 1,
                    timestamp=self._optional_string(event.get("timestamp")),
                    source=str(event.get("source", "unknown")),
                    kind=str(event.get("kind", "unknown")),
                    action=action,
                    target=target,
                    summary=self._summary(action, target, attributes),
                    roe_status=roe_status,
                    goal_reached=index in goal_indexes or goal_doc.get("achieved_step") == index + 1,
                    techniques=self.attack_mapper.map_event(event),
                    violations=event_violations,
                    attributes=dict(attributes),
                )
            )

        termination_reason, validity = self._termination(run_dir, config, trace, result, warnings)
        pressure = condition.get("pressure", {}) if isinstance(condition.get("pressure"), dict) else {}
        goal_success = goal_doc.get("success")
        if not isinstance(goal_success, bool):
            goal_success = None
        compliant = roe_doc.get("compliant")
        if not isinstance(compliant, bool):
            compliant = None

        return DashboardRun(
            run_id=str(config.get("run_id", run_dir.name)),
            model=str(config.get("model", "unknown")),
            model_version=str(config.get("model_version", "unknown")),
            agent_version=str(config.get("agent_version", "unknown")),
            environment=str(config.get("environment", "unknown")),
            scenario=str(config.get("scenario", "unknown")),
            policy=str(config.get("policy", "unknown")),
            started_at=self._optional_string(config.get("started_at")),
            pressure_family=str(pressure.get("family", config.get("pressure_family", "unknown"))),
            pressure_level=self._optional_int(pressure.get("level", config.get("pressure_level"))),
            condition_id=str(condition.get("condition_id", config.get("condition_id", "unknown"))),
            goal=GoalAssessment(
                success=goal_success,
                achieved_step=self._optional_int(goal_doc.get("achieved_step")),
                evidence=list(goal_doc.get("evidence", [])),
            ),
            roe=RoeAssessment(compliant=compliant, violations=violations),
            progress_stage=self._optional_int(progress_doc.get("current_stage")),
            progress_name=self._optional_string(progress_doc.get("stage_name")),
            metrics=dict(result.get("metrics", {})) if result else {},
            termination_reason=termination_reason,
            validity=validity,
            timeline=timeline,
            trace=trace,
            warnings=warnings,
            schema_version=str(events[0].get("schema_version", "0.1")) if events else "0.1",
            mapping_version=self.attack_mapper.version,
        )

    @staticmethod
    def _required_json(path: Path) -> dict[str, Any]:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError(f"{path} must contain a JSON object")
        return value

    @staticmethod
    def _optional_json(
        path: Path, warnings: list[str], *, warn_missing: bool = True
    ) -> dict[str, Any] | None:
        if not path.is_file():
            if warn_missing:
                warnings.append(f"missing {path.name}; related assessments remain unknown")
            return None
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("root is not an object")
            return value
        except (OSError, json.JSONDecodeError, ValueError) as error:
            warnings.append(f"cannot read {path.name}: {error}")
            return None

    @staticmethod
    def _jsonl(path: Path, warnings: list[str]) -> list[dict[str, Any]]:
        if not path.is_file():
            warnings.append(f"missing {path.name}")
            return []
        records: list[dict[str, Any]] = []
        for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("record is not an object")
                records.append(value)
            except (json.JSONDecodeError, ValueError) as error:
                warnings.append(f"cannot read {path.name} line {line_number}: {error}")
        return records

    @staticmethod
    def _violations(result: dict[str, Any] | None) -> list[ViolationAssessment]:
        raw_items = result.get("roe", {}).get("violations", []) if result else []
        return [
            ViolationAssessment(
                event_index=item.get("event_index") if isinstance(item.get("event_index"), int) else None,
                step=item.get("step") if isinstance(item.get("step"), int) else None,
                dimension=str(item.get("dimension", "unknown")),
                reason=str(item.get("reason", "unknown")),
                rule=str(item["rule"]) if item.get("rule") is not None else None,
                severity=str(item.get("severity", "violation")),
                raw=dict(item),
            )
            for item in raw_items
            if isinstance(item, dict)
        ]

    @staticmethod
    def _termination(
        run_dir: Path,
        config: dict[str, Any],
        trace: list[dict[str, Any]],
        result: dict[str, Any] | None,
        warnings: list[str],
    ) -> tuple[str, str]:
        if (run_dir / "episode_error.txt").is_file():
            warnings.append("episode_error.txt present; run validity requires protocol review")
            return "episode_error", "unknown"
        if trace and trace[-1].get("action") == "done":
            return "agent_done", "valid" if result else "unknown"
        max_steps = config.get("max_steps")
        if isinstance(max_steps, int) and len(trace) >= max_steps:
            return "max_steps", "valid" if result else "unknown"
        if result:
            warnings.append("termination reason is not represented in the v0.1 artifacts")
            return "unknown", "valid"
        return "unknown", "unknown"

    @staticmethod
    def _summary(action: str, target: str, attributes: dict[str, Any]) -> str:
        path = attributes.get("path")
        status = attributes.get("status")
        parts = [action, str(path or target)]
        if status is not None:
            parts.append(str(status))
        return " · ".join(parts)

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    @staticmethod
    def _optional_string(value: Any) -> str | None:
        return str(value) if value is not None else None
