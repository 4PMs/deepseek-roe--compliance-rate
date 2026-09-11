from __future__ import annotations

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]
PRESERVED_PREFIXES = (
    Path("runs"),
    Path("analysis/outputs"),
    Path("experiments/benchmark-v1/readiness-runs"),
)
PRESERVED_FILES = {
    Path("experiments/benchmark-v1/aggregate-summary.json"),
}
SKIP_PARTS = {".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache"}
RETIRED_BRAND = "tem" + "pera"
RETIRED_PATTERN = re.compile(RETIRED_BRAND + r"(?!ture)", re.IGNORECASE)


def _preserved(relative: Path) -> bool:
    return relative in PRESERVED_FILES or any(
        relative == prefix or prefix in relative.parents for prefix in PRESERVED_PREFIXES
    )


def test_active_repository_has_no_retired_branding() -> None:
    matches: list[str] = []
    for path in ROOT.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(ROOT)
        if any(part in SKIP_PARTS for part in relative.parts) or _preserved(relative):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if RETIRED_PATTERN.search(text):
            matches.append(str(relative))
    assert matches == [], "active files still contain retired branding:\n" + "\n".join(matches)


def test_active_repository_paths_have_no_retired_branding() -> None:
    matches = [
        str(path.relative_to(ROOT))
        for path in ROOT.rglob("*")
        if not _preserved(path.relative_to(ROOT))
        and RETIRED_PATTERN.search(path.name)
        and not any(part in SKIP_PARTS for part in path.relative_to(ROOT).parts)
    ]
    assert matches == [], "active paths still contain retired branding:\n" + "\n".join(matches)


def test_runner_console_entrypoint_is_neutral() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["scripts"]["runner"] == "benchmark_core.runner:main"
