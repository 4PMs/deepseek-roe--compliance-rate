from __future__ import annotations

from dataclasses import dataclass

from ..domain.models import DashboardRun


@dataclass(frozen=True)
class RunSummary:
    total_runs: int
    valid_runs: int
    invalid_runs: int
    unknown_runs: int
    goal_success_count: int
    violation_run_count: int
    safe_success_count: int
    unsafe_success_count: int
    goal_rate: float | None
    violation_rate: float | None
    safe_success_rate: float | None
    unsafe_success_rate: float | None

    def to_dict(self) -> dict[str, int | float | None]:
        return self.__dict__.copy()


def summarize_runs(runs: list[DashboardRun]) -> RunSummary:
    valid = [run for run in runs if run.validity == "valid"]
    denominator = len(valid)
    goals = sum(run.goal.success is True for run in valid)
    violations = sum(run.roe.compliant is False for run in valid)
    safe = sum(run.goal.success is True and run.roe.compliant is True for run in valid)
    unsafe = sum(run.goal.success is True and run.roe.compliant is False for run in valid)

    def rate(value: int) -> float | None:
        return value / denominator if denominator else None

    return RunSummary(
        total_runs=len(runs),
        valid_runs=denominator,
        invalid_runs=sum(run.validity == "invalid" for run in runs),
        unknown_runs=sum(run.validity == "unknown" for run in runs),
        goal_success_count=goals,
        violation_run_count=violations,
        safe_success_count=safe,
        unsafe_success_count=unsafe,
        goal_rate=rate(goals),
        violation_rate=rate(violations),
        safe_success_rate=rate(safe),
        unsafe_success_rate=rate(unsafe),
    )
