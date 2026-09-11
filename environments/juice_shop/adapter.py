"""Juice Shop environment lifecycle adapter."""

from typing import Any, Mapping

from .provision import provision_scenario_fixture
from .reset import recreate_juice_shop, verify_baseline


class JuiceShopAdapter:
    def reset(self) -> dict[str, Any]:
        return recreate_juice_shop()

    def verify(self) -> dict[str, Any]:
        return verify_baseline()

    def provision(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        return provision_scenario_fixture(scenario)
