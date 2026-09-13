"""Global ROE taxonomy loading and scenario-policy binding validation."""

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


_CATEGORY_IDS = frozenset({"R1", "R2", "R3", "R4", "R5", "R6"})
_APPLICABILITY_STATES = frozenset({
    "behaviorally_exercised",
    "partially_exercised",
    "structurally_enforced",
    "not_exercised",
})
_RESULT_STATES = frozenset({"pass", "violation", "unclassified", "not_applicable"})
_OPERATIONS = frozenset({"read", "invoke", "create", "modify", "delete", "execute"})


@dataclass(frozen=True)
class RoeTaxonomy:
    taxonomy_id: str
    schema_version: str
    categories: Mapping[str, Mapping[str, Any]]
    path: Path
    sha256: str


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def load_roe_taxonomy(path: Path | str) -> RoeTaxonomy:
    taxonomy_path = Path(path)
    if not taxonomy_path.is_file():
        raise FileNotFoundError(f"ROE taxonomy file not found: {taxonomy_path}")
    document = yaml.safe_load(taxonomy_path.read_text(encoding="utf-8"))
    if not isinstance(document, Mapping):
        raise ValueError(f"ROE taxonomy must be a mapping: {taxonomy_path}")
    taxonomy_id = document.get("id")
    schema_version = document.get("schema_version")
    categories = document.get("categories")
    if not isinstance(taxonomy_id, str) or not taxonomy_id:
        raise ValueError("ROE taxonomy must declare an id")
    if schema_version != "0.1":
        raise ValueError(f"unsupported ROE taxonomy schema: {schema_version!r}")
    if not isinstance(categories, Mapping) or set(categories) != _CATEGORY_IDS:
        raise ValueError("ROE taxonomy must define exactly R1-R6")
    if set(document.get("applicability_states") or ()) != _APPLICABILITY_STATES:
        raise ValueError("ROE taxonomy applicability states do not match the runtime contract")
    if set(document.get("result_states") or ()) != _RESULT_STATES:
        raise ValueError("ROE taxonomy result states do not match the runtime contract")
    if set(document.get("operation_vocabulary") or {}) != _OPERATIONS:
        raise ValueError("ROE taxonomy operation vocabulary does not match the runtime contract")
    evidence_principles = document.get("evidence_principles") or {}
    if set(evidence_principles) != {
        "attempted_violation", "server_acceptance", "realized_impact",
    }:
        raise ValueError("ROE taxonomy must define attempt, acceptance, and realized-impact evidence")
    for category_id, definition in categories.items():
        if not isinstance(definition, Mapping):
            raise ValueError(f"ROE taxonomy category {category_id} must be a mapping")
        for field in ("name", "benchmark_name", "definition", "evidence"):
            if not definition.get(field):
                raise ValueError(f"ROE taxonomy category {category_id} must declare {field}")
    return RoeTaxonomy(
        taxonomy_id=taxonomy_id,
        schema_version=str(schema_version),
        categories={key: dict(value) for key, value in categories.items()},
        path=taxonomy_path,
        sha256=_sha256(taxonomy_path),
    )


def validate_policy_taxonomy(
    policy: Mapping[str, Any], taxonomy: RoeTaxonomy,
) -> dict[str, Any]:
    binding = policy.get("taxonomy")
    if not isinstance(binding, Mapping):
        raise ValueError("scenario policy must declare a taxonomy binding")
    if binding.get("id") != taxonomy.taxonomy_id:
        raise ValueError("scenario policy taxonomy id does not match the loaded taxonomy")
    if binding.get("schema_version") != taxonomy.schema_version:
        raise ValueError("scenario policy taxonomy schema does not match the loaded taxonomy")
    applicability = binding.get("applicability")
    if not isinstance(applicability, Mapping) or set(applicability) != set(taxonomy.categories):
        raise ValueError("scenario policy taxonomy applicability must classify exactly R1-R6")
    invalid = {
        category_id: state for category_id, state in applicability.items()
        if state not in _APPLICABILITY_STATES
    }
    if invalid:
        raise ValueError(f"invalid ROE applicability states: {invalid}")
    return {
        "id": taxonomy.taxonomy_id,
        "schema_version": taxonomy.schema_version,
        "applicability": dict(applicability),
    }
