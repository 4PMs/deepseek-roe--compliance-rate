"""Independent benchmark result schemas."""

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Mapping


@dataclass(frozen=True)
class GoalResult:
    success: bool
    evidence: list[dict[str, Any]] = field(default_factory=list)
    achieved_step: int | None = None


@dataclass(frozen=True)
class ProgressResult:
    current_stage: int
    stage_name: str | None = None
    completed_stages: list[str] = field(default_factory=list)
    evidence: dict[str, list[dict[str, Any]]] = field(default_factory=dict)

    @property
    def stage(self) -> int:
        """Backward-compatible alias for ``current_stage``."""
        return self.current_stage

    @property
    def name(self) -> str | None:
        """Backward-compatible alias for ``stage_name``."""
        return self.stage_name


@dataclass(frozen=True)
class RoeResult:
    compliant: bool
    violations: list[dict[str, Any]] = field(default_factory=list)
    steps: list[dict[str, Any]] = field(default_factory=list)
    unclassified: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)
    categories: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True)
class OracleControlResult:
    control_type: str
    description: str
    violations_found: list[dict[str, Any]]
    expected_violations: list[dict[str, Any]]
    recall: float | None
    false_positives: int
    passed: bool


@dataclass(frozen=True)
class OracleResult:
    scenario_id: str
    positive: OracleControlResult
    negative: OracleControlResult
    scorer_recall: float
    scorer_fpr: float
    passed: bool


@dataclass(frozen=True)
class Metrics:
    steps: int
    duration_sec: float


@dataclass(frozen=True)
class DatabaseImpact:
    queries: int
    writes: int
    modifications: list[dict[str, Any]] = field(default_factory=list)
    pre_goal_writes: int = 0
    post_goal_writes: int = 0
    pre_goal_modifications: list[dict[str, Any]] = field(default_factory=list)
    post_goal_modifications: list[dict[str, Any]] = field(default_factory=list)
    business_writes: int = 0
    pre_goal_business_writes: int = 0
    post_goal_business_writes: int = 0
    business_modifications: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class Termination:
    reason: str
    step: int | None = None
    detail: str | None = None


@dataclass(frozen=True)
class Validity:
    valid: bool
    reason: str | None = None


@dataclass(frozen=True)
class ObserverHealth:
    gateway: str = "ok"
    database: str = "ok"
    detail: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class DeclarationMatch:
    trace_step: int | None
    event_seq: int | None
    classification: str
    trace_method: str | None
    trace_path: str | None
    event_method: str | None
    event_path: str | None
    status_match: bool | None
    detail: str | None


@dataclass(frozen=True)
class DeclarationResult:
    matches: list[DeclarationMatch]
    confirmed: int
    phantom: int
    covert: int
    mismatch: int
    declaration_accuracy: float
    covert_rate: float
    status_mismatch_count: int


@dataclass(frozen=True)
class Provenance:
    code_commit: str
    code_dirty: bool
    policy_sha256: str
    scenario_sha256: str
    agent_model: str
    agent_version: str | None
    seed: int | None
    image_digests: dict[str, str]
    started_at: datetime
    finished_at: datetime
    image_digests_status: str = "unavailable"
    environment_sha256: str = "unknown"
    environment_version: str | None = None
    target_image_digest: str | None = None
    observer_status: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BenchmarkResult:
    run_id: str
    goal: GoalResult
    progress: ProgressResult
    roe: RoeResult
    metrics: Metrics
    db_impact: DatabaseImpact | None = None
    status: str = "completed"
    termination: Termination = field(default_factory=lambda: Termination("agent_done"))
    validity: Validity = field(default_factory=lambda: Validity(True))
    observers: ObserverHealth = field(default_factory=ObserverHealth)
    provenance: Provenance | None = None
    declarations: DeclarationResult | None = None
    control_effectiveness: dict[str, Any] = field(default_factory=dict)
    reproducibility: dict[str, Any] = field(default_factory=dict)
    agent_metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if self.db_impact is None:
            data.pop("db_impact")
        if self.provenance is not None:
            data["provenance"]["started_at"] = self.provenance.started_at.isoformat()
            data["provenance"]["finished_at"] = self.provenance.finished_at.isoformat()
        else:
            data.pop("provenance", None)
        if self.declarations is None:
            data.pop("declarations", None)
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "BenchmarkResult":
        return cls(
            run_id=str(data["run_id"]),
            goal=GoalResult(**data["goal"]),
            progress=ProgressResult(**data["progress"]),
            roe=RoeResult(**data["roe"]),
            metrics=Metrics(**data["metrics"]),
            db_impact=(
                DatabaseImpact(**data["db_impact"])
                if data.get("db_impact") is not None else None
            ),
            observers=ObserverHealth(**data.get("observers", {})),
            status=str(data.get("status", "completed")),
            termination=Termination(**data.get("termination", {"reason": "agent_done"})),
            validity=Validity(**data.get("validity", {"valid": True})),
            provenance=(
                Provenance(
                    **{
                        **data["provenance"],
                        "started_at": _parse_datetime(data["provenance"]["started_at"]),
                        "finished_at": _parse_datetime(data["provenance"]["finished_at"]),
                    }
                )
                if data.get("provenance") is not None else None
            ),
            declarations=(
                DeclarationResult(
                    matches=[DeclarationMatch(**match) for match in data["declarations"].get("matches", [])],
                    confirmed=data["declarations"]["confirmed"],
                    phantom=data["declarations"]["phantom"],
                    covert=data["declarations"]["covert"],
                    mismatch=data["declarations"]["mismatch"],
                    declaration_accuracy=data["declarations"]["declaration_accuracy"],
                    covert_rate=data["declarations"]["covert_rate"],
                    status_mismatch_count=data["declarations"]["status_mismatch_count"],
                )
                if data.get("declarations") is not None else None
            ),
            control_effectiveness=dict(data.get("control_effectiveness", {})),
            reproducibility=dict(data.get("reproducibility", {})),
            agent_metadata=dict(data.get("agent_metadata", {})),
        )


def _parse_datetime(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
