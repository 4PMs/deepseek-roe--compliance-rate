"""Juice Shop environment lifecycle adapter."""

from typing import Any, Mapping

from .provision import provision_scenario_fixture, scenario_agent_context
from .reset import recreate_juice_shop, verify_baseline
from .state_observer import JuiceShopStateObserver


class JuiceShopAdapter:
    def reset(self) -> dict[str, Any]:
        return recreate_juice_shop()

    def verify(self) -> dict[str, Any]:
        return verify_baseline()

    def provision(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        return provision_scenario_fixture(scenario)

    def agent_context(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        return scenario_agent_context(scenario)

    def action_observer(self, scenario: Mapping[str, Any]) -> JuiceShopStateObserver:
        return JuiceShopStateObserver(scenario)
