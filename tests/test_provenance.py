from datetime import datetime, timezone
import hashlib
from pathlib import Path
from unittest.mock import patch

from benchmark_core.core.result import BenchmarkResult, Provenance
from benchmark_core.runner import (
    _image_digests,
    _use_verified_reset_image,
    collect_provenance,
    sha256_file,
)


def test_old_result_without_provenance_loads():
    result = BenchmarkResult.from_dict({
        "run_id": "old", "goal": {"success": False},
        "progress": {"current_stage": 0}, "roe": {"compliant": True},
        "metrics": {"steps": 0, "duration_sec": 0.0},
    })
    assert result.provenance is None


def test_same_policy_bytes_have_same_sha256(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    policy.write_bytes(b"allowed:\n  targets: [example]\n")

    assert sha256_file(policy) == sha256_file(policy)
    assert sha256_file(policy) == hashlib.sha256(policy.read_bytes()).hexdigest()


def test_git_unavailable_returns_unknown_without_raising(tmp_path: Path):
    policy = tmp_path / "policy.yaml"
    scenario = tmp_path / "scenario.yaml"
    condition = tmp_path / "neutral.yaml"
    taxonomy = tmp_path / "taxonomy.yaml"
    policy.write_bytes(b"policy")
    scenario.write_bytes(b"scenario")
    condition.write_bytes(b"condition")
    taxonomy.write_bytes(b"taxonomy")

    with patch("benchmark_core.runner.subprocess.run", side_effect=FileNotFoundError):
        provenance = collect_provenance(
            policy, scenario, "test-model", "test-version", 7,
            datetime.now(timezone.utc), condition_path=condition, taxonomy_path=taxonomy,
        )

    assert provenance.code_commit == "unknown"
    assert provenance.code_dirty is False
    assert provenance.policy_sha256 == sha256_file(policy)
    assert provenance.scenario_sha256 == sha256_file(scenario)
    assert provenance.condition_sha256 == sha256_file(condition)
    assert provenance.taxonomy_sha256 == sha256_file(taxonomy)


def test_verified_reset_image_fills_early_docker_probe_gap():
    provenance = Provenance(
        "commit", False, "policy", "scenario", "model", "version", None,
        {}, datetime.now(timezone.utc), datetime.now(timezone.utc),
    )
    result = _use_verified_reset_image(provenance, {
        "image": "juice-shop:latest",
        "image_id": "sha256:" + "a" * 64,
        "baseline_verified": True,
    })
    assert result.image_digests == {"juice-shop:latest": "sha256:" + "a" * 64}
    assert result.image_digests_status == "verified_reset_image_id"


def test_docker_available_records_repo_digest():
    from subprocess import CompletedProcess

    calls = [
        CompletedProcess([], 0, stdout="container-1\n", stderr=""),
        CompletedProcess([], 0, stdout="sha256:image-id\n", stderr=""),
        CompletedProcess([], 0, stdout='["juice@sha256:' + "b" * 64 + '"]', stderr=""),
    ]
    with patch("benchmark_core.runner.subprocess.run", side_effect=calls) as run:
        assert _image_digests() == {"container-1": "juice@sha256:" + "b" * 64}
    assert run.call_args_list[1].args[0] == [
        "docker", "inspect", "--format", "{{.Image}}", "container-1",
    ]
    assert run.call_args_list[2].args[0] == [
        "docker", "image", "inspect", "--format", "{{json .RepoDigests}}", "sha256:image-id",
    ]


def test_unverified_reset_image_is_not_invented():
    provenance = Provenance(
        "commit", False, "policy", "scenario", "model", "version", None,
        {}, datetime.now(timezone.utc), datetime.now(timezone.utc),
    )
    result = _use_verified_reset_image(provenance, {"image_id": "sha256:" + "a" * 64})
    assert result.image_digests == {}
    assert result.image_digests_status == "unavailable"
