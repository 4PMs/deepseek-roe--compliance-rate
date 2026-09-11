# Docker E2E readiness

This document describes the prerequisites for the optional live Juice Shop E2E run. Unit and replay tests do not require Docker.

## Required inputs

- Python 3.11+ with the package installed: `python -m pip install -e ".[test,dev]"`
- Docker Desktop (Windows) or Docker Engine (Linux), running and reachable by `docker info`
- `TEMPERA_DB_OBSERVER_TOKEN`: the same non-empty local token passed to the target container and observer configuration
- For DeepSeek runs only: `DEEPSEEK_API_KEY`; Ollama runs require a locally running model instead

Do not commit either token or API key. Use a process-local environment variable or a local `.env` file.

## Build and target startup

From the repository root:

```text
docker network create target-net
docker build -f docker/juice-shop.Dockerfile -t tempera-juice-shop .
```

Windows CMD:

```bat
set TEMPERA_DB_OBSERVER_TOKEN=secret-local-token
docker run -d --name tempera-juice --network target-net -e NODE_ENV=ctf -e CTF_KEY=tempera-test-001 -e TEMPERA_DB_OBSERVER_TOKEN=%TEMPERA_DB_OBSERVER_TOKEN% -p 127.0.0.1:3001:3000 tempera-juice-shop
```

Linux/macOS shell:

```sh
export TEMPERA_DB_OBSERVER_TOKEN=secret-local-token
docker run -d --name tempera-juice --network target-net -e NODE_ENV=ctf -e CTF_KEY=tempera-test-001 -e TEMPERA_DB_OBSERVER_TOKEN="$TEMPERA_DB_OBSERVER_TOKEN" -p 127.0.0.1:3001:3000 tempera-juice-shop
```

Verify `http://127.0.0.1:3001` before starting the runner. When `--reset-target` is used, the target must be reachable and the adapter's reset/provision prerequisites must be available; reset verification is intentionally performed before the agent starts.

## Expected command and skip reasons

```text
python -B -m tempera.runner run --scenario JS-001 --model qwen2.5:3b --provider ollama --upstream http://127.0.0.1:3001 --reset-target
```

The live E2E is skipped when Docker is unavailable, the target image is not built, port 3001 is occupied/unreachable, the observer token is missing, or the selected provider is not configured. A skip is an environment prerequisite result, not a benchmark pass or a policy verdict.

Windows uses Docker Desktop's Linux containers and may require allowing the repository directory in Docker file sharing. Linux requires the invoking user to access the Docker socket (or an appropriate rootless Docker configuration). Do not delete `runs/` during test cleanup: it contains real experiment artifacts. Disposable tests belong under `.test-tmp*/` or `runs/test-generated/`.

## Evidence bundle validation

Completed runs write `manifest.json` plus derived evidence under `evidence/`.
Validate one run without rewriting its existing artifacts:

```text
python -B -m tempera.runner validate-run --run <run_id>
```

The command exits non-zero on missing or modified required artifacts,
malformed JSONL, lifecycle/action mismatches, orphan verdict evidence, or
missing R5 outcome evidence. Legacy runs can be inspected with
`build_manifest(run_dir)`; unavailable evidence is reported as
`legacy_incomplete` and is never fabricated.
