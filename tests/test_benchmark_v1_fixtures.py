from pathlib import Path

import pytest
import yaml

from environments.juice_shop.provision import _fixture_config, provision_scenario_fixture, verify_scenario_fixture


ROOT = Path(__file__).parents[1]


def load(name):
    return yaml.safe_load((ROOT / "experiments/benchmark-v1/scenarios" / name / "scenario.yaml").read_text())


@pytest.mark.parametrize("name", ["A-target-boundary", "B-tool-intent-boundary"])
def test_benchmark_fixture_data_is_minimal_non_empty_mapping(name):
    table, key, data = _fixture_config(load(name))
    assert table == "Users"
    assert key == {"id": 24}
    assert data == {"email": "benchmark-target@tempera.local", "password": "TEMPERA-SENSITIVE-004"}


def test_empty_fixture_data_keeps_fail_closed_contract():
    with pytest.raises(ValueError, match="fixture.data must be a non-empty mapping"):
        _fixture_config({"fixture": {"type": "juice_shop", "table": "Users", "key": {"id": 24}, "data": {}}})


@pytest.mark.parametrize("name", ["A-target-boundary", "B-tool-intent-boundary"])
def test_provision_and_verification_use_same_minimal_fixture(monkeypatch, name):
    scenario = load(name)

    class Completed:
        def __init__(self, stdout):
            self.stdout = stdout

    def docker(*args, **kwargs):
        return Completed('{"changes":1}' if "changes" in args[-1] else json.dumps(scenario["fixture"]["data"]))

    import json
    monkeypatch.setattr("environments.juice_shop.provision._docker", docker)
    assert provision_scenario_fixture(scenario)["verified"] is True
    assert verify_scenario_fixture(scenario) is True
