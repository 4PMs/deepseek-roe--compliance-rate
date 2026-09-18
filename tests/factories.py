"""Shared constructors for test transport envelopes and runner inputs."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from benchmark_core.observe.normalizer import RawObservation
from benchmark_core.runner import add_run_parser


def raw_web_observation(
    path: str,
    *,
    method: str = "GET",
    status: int = 200,
    request_body: bytes = b"",
    response_body: bytes = b"{}",
    action_id: str | None = None,
    target_base: str = "http://target",
    facts: Mapping[str, Any] | None = None,
) -> RawObservation:
    """Build a gateway observation while leaving semantic resolution to the observer."""

    request_facts: dict[str, Any] = {
        "method": method,
        "path": path,
        "status": status,
        "_request_body": request_body,
        "_response_body": response_body,
    }
    if action_id is not None:
        request_facts["action_id"] = action_id
    request_facts.update(facts or {})
    return RawObservation(
        timestamp=datetime.now(timezone.utc),
        actor="agent",
        source="gateway",
        kind="web",
        action="request",
        target=f"{target_base}{path}",
        facts=request_facts,
    )


def run_args(
    directory: str | Path,
    run_id: str,
    *,
    scenario: str = "scenarioA",
    model: str = "test",
    **overrides: Any,
) -> argparse.Namespace:
    """Build run-command arguments from the real parser, then apply test-specific overrides."""

    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    add_run_parser(subparsers)
    args = parser.parse_args(
        [
            "run",
            "--scenario",
            scenario,
            "--model",
            model,
            "--runs-dir",
            str(directory),
            "--run",
            run_id,
        ]
    )
    for name, value in overrides.items():
        if not hasattr(args, name):
            raise TypeError(f"unknown run argument override: {name}")
        setattr(args, name, value)
    return args
