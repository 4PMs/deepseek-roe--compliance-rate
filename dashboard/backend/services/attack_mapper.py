from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..domain.models import TechniqueEvidence


class AttackMapper:
    """Map factual events to ATT&CK annotations without making RoE decisions."""

    def __init__(self, mapping_path: Path | None = None) -> None:
        if mapping_path is None:
            mapping_path = Path(__file__).parents[2] / "mappings" / "attack" / "juice_shop_v1.json"
        document = json.loads(mapping_path.read_text(encoding="utf-8"))
        self.version = str(document["mapping_version"])
        self.rules = list(document.get("rules", []))

    def map_event(self, event: dict[str, Any]) -> list[TechniqueEvidence]:
        matches: list[TechniqueEvidence] = []
        for rule in self.rules:
            if self._matches(event, rule.get("when", {})):
                matches.append(
                    TechniqueEvidence(
                        technique_id=str(rule["technique_id"]),
                        name=str(rule["name"]),
                        tactic=str(rule["tactic"]),
                        status=self._status(event),
                        confidence=str(rule.get("confidence", "unknown")),
                        reason=str(rule.get("reason", "")),
                    )
                )
        return matches

    @staticmethod
    def _matches(event: dict[str, Any], conditions: dict[str, Any]) -> bool:
        for dotted_path, expected in conditions.items():
            value: Any = event
            for part in dotted_path.split("."):
                if not isinstance(value, dict) or part not in value:
                    return False
                value = value[part]
            if isinstance(expected, list):
                if str(value).upper() not in {str(item).upper() for item in expected}:
                    return False
            elif value != expected:
                return False
        return True

    @staticmethod
    def _status(event: dict[str, Any]) -> str:
        attributes = event.get("attributes", {})
        if event.get("kind") == "database":
            return "verified"
        status = attributes.get("status")
        if isinstance(status, int) and 200 <= status < 300:
            return "verified"
        return "attempted"
