from pathlib import Path

import pytest
import yaml

from benchmark_core.core.taxonomy import load_roe_taxonomy, validate_policy_taxonomy
from benchmark_core.core.policy import Policy
from benchmark_core.evaluate.roe import ROE_CATEGORY_DEFINITIONS


ROOT = Path(__file__).resolve().parents[1]
TAXONOMY_PATH = ROOT / "roe" / "taxonomy.yaml"


def test_global_taxonomy_matches_evaluator_category_vocabulary():
    taxonomy = load_roe_taxonomy(TAXONOMY_PATH)

    assert taxonomy.taxonomy_id == "roe-r1-r6"
    assert taxonomy.schema_version == "0.1"
    assert set(taxonomy.categories) == set(ROE_CATEGORY_DEFINITIONS)
    for category_id, evaluator_definition in ROE_CATEGORY_DEFINITIONS.items():
        assert taxonomy.categories[category_id]["name"] == evaluator_definition["name"]
        assert taxonomy.categories[category_id]["benchmark_name"] == evaluator_definition["benchmark_name"]
        assert taxonomy.categories[category_id]["nist_basis"] == evaluator_definition["nist_basis"]


def test_each_scenario_policy_explicitly_binds_to_global_taxonomy():
    taxonomy = load_roe_taxonomy(TAXONOMY_PATH)

    for scenario_name in ("scenarioA", "scenarioB"):
        policy = yaml.safe_load(
            (ROOT / "scenarios" / scenario_name / "policy.yaml").read_text(encoding="utf-8")
        )
        binding = validate_policy_taxonomy(policy, taxonomy)
        parsed_policy = Policy.from_dict(policy)
        assert binding["id"] == taxonomy.taxonomy_id
        assert binding["schema_version"] == taxonomy.schema_version
        assert set(binding["applicability"]) == set(taxonomy.categories)
        assert binding["applicability"]["R1"] == "structurally_enforced"
        assert binding["applicability"]["R2"] == "structurally_enforced"
        assert parsed_policy.taxonomy == binding
        if scenario_name == "scenarioA":
            assert "record_modified" in policy["roe"]["expected_outcome_boundary"]["prohibited_outcomes"]


def test_policy_binding_rejects_a_different_taxonomy():
    taxonomy = load_roe_taxonomy(TAXONOMY_PATH)
    policy = {
        "taxonomy": {
            "id": "different-taxonomy",
            "schema_version": "0.1",
            "applicability": {category_id: "behaviorally_exercised" for category_id in taxonomy.categories},
        }
    }

    with pytest.raises(ValueError, match="taxonomy id"):
        validate_policy_taxonomy(policy, taxonomy)


def test_policy_binding_requires_every_category_applicability():
    taxonomy = load_roe_taxonomy(TAXONOMY_PATH)
    policy = {
        "taxonomy": {
            "id": taxonomy.taxonomy_id,
            "schema_version": taxonomy.schema_version,
            "applicability": {"R1": "structurally_enforced"},
        }
    }

    with pytest.raises(ValueError, match="exactly R1-R6"):
        validate_policy_taxonomy(policy, taxonomy)
