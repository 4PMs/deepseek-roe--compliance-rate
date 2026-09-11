"""Environment lifecycle contract."""

from typing import Any, Mapping, Protocol


class EnvironmentAdapter(Protocol):
    """Public lifecycle protocol; concrete adapters remain environment-specific."""
    def reset(self) -> dict[str, Any]:
        """환경을 초기 상태로 복원하고 결과를 반환한다."""
        ...

    def provision(self, scenario: Mapping[str, Any]) -> dict[str, Any]:
        """시나리오별 fixture 를 환경에 적용한다 (예: credential 랜덤화)."""
        ...
