from __future__ import annotations

import shutil
from pathlib import Path

import yaml

from benchmark_core.runner import _integrate_condition_goal, _load_instruction_condition

ROOT = Path(__file__).resolve().parents[2]
BATCH = ROOT / "reports" / "scenarioA-n30-20260915T025311Z"
SOURCE_RUNS = BATCH / "runs"
OUTPUT = BATCH / "offline_rescore_fixed"
OUTPUT_RUNS = OUTPUT / "runs"
SCENARIO_DIR = ROOT / "scenarios" / "scenarioA"


def main() -> None:
    OUTPUT_RUNS.mkdir(parents=True, exist_ok=True)
    base = yaml.safe_load((SCENARIO_DIR / "scenario.yaml").read_text(encoding="utf-8"))
    for condition in ("neutral", "read", "modify", "delete"):
        integrated = _integrate_condition_goal(
            base, _load_instruction_condition(SCENARIO_DIR, condition),
        )
        (OUTPUT / f"scenarioA-{condition}-integrated.yaml").write_text(
            yaml.safe_dump(integrated, sort_keys=False, allow_unicode=True), encoding="utf-8",
        )
        for repetition in range(1, 31):
            run_id = f"scenarioA-n30-20260915T025311Z-{condition}-r{repetition:02d}"
            source = SOURCE_RUNS / run_id
            target = OUTPUT_RUNS / run_id
            target.mkdir(exist_ok=True)
            for name in ("config.json", "events.jsonl", "trace.jsonl"):
                path = source / name
                if path.is_file():
                    shutil.copy2(path, target / name)
    print(OUTPUT)


if __name__ == "__main__":
    main()
