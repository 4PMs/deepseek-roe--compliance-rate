from __future__ import annotations

from pathlib import Path

from ..adapters.tempera_v01 import TemperaV01Adapter
from ..domain.models import DashboardRun


class RunRepository:
    def __init__(self, runs_dir: Path, adapter: TemperaV01Adapter | None = None) -> None:
        self.runs_dir = Path(runs_dir)
        self.adapter = adapter or TemperaV01Adapter()

    def load_all(self) -> list[DashboardRun]:
        if not self.runs_dir.is_dir():
            return []
        runs = [
            self.adapter.load(path)
            for path in sorted(self.runs_dir.iterdir())
            if path.is_dir() and (path / "config.json").is_file()
        ]
        return sorted(runs, key=lambda run: run.started_at or "", reverse=True)

    def get(self, run_id: str) -> DashboardRun | None:
        safe_name = Path(run_id).name
        if safe_name != run_id:
            return None
        run_dir = self.runs_dir / safe_name
        if not (run_dir / "config.json").is_file():
            return None
        return self.adapter.load(run_dir)
