# Strix external adapter PoC

Tempera supports one external agent proposal bridge: Strix.

Selection rationale:

- Apache-2.0 open-source project with a local CLI (`strix-agent` / `strix`).
- Intended for live web/API security testing and exposes structured tool calls.
- Supports HTTP interception/proxy and headless execution.
- The repository has no Strix installation or running Docker/LLM provider, so the
  real Strix process cannot be used as a verified run in this environment.

## Candidate comparison and selection

| Candidate | Local/CLI | Structured action visibility | Web/API fit | Tempera boundary fit | Decision |
|---|---|---|---|---|---|
| Strix (`usestrix/strix`) | Yes; Apache-2.0 CLI/PyPI package | Tool-oriented agent runtime, but no stable Tempera proposal-stream contract | Strong: HTTP interception, browser, API testing | Best available only through a proposal-stream interception adapter; native CLI normally executes inside its own runtime | Selected |
| PentestGPT (`GreyDGL/PentestGPT`) | Yes; CLI/repository workflow | Primarily an interactive planning/execution workflow; no stable machine-readable proposal contract found | Broad pentest use, but less deterministic for a small HTTP PoC | Would require deeper process interception and its own execution path could bypass the gate | Not selected |

Strix was selected because it has the clearest local CLI/package boundary, explicit web/API testing capability, and Apache-2.0 licensing. The adapter deliberately does **not** claim that the normal Strix CLI is gate-safe: the supported PoC input is a JSON/JSONL proposal stream, and the normal Strix sandbox execution path is reported as a limitation below.

## Boundary

`ExternalStrixAgentAdapter` consumes a JSON or JSONL proposal stream. Accepted
common envelopes include:

```json
{"tool_call":{"name":"http_request","arguments":{"method":"GET","path":"/api/users"}}}
```

and:

```json
{"tool":"http_request","arguments":{"method":"GET","path":"/api/users"}}
```

The adapter converts these records to `AgentProposal`; the runtime then calls
the existing action normalizer, Policy Gate, gateway, observers, evaluator, and
bundle finalizer. The adapter never calls the target itself.

The upstream Strix CLI normally executes tools in its own Docker/Caido runtime
and does not provide a stable Tempera proposal-stream contract. Passing a raw
Strix scan command is therefore an integration limitation, not a supported
way to bypass the gate. A future Strix bridge must emit proposal records only.

## CLI

```text
python -m tempera.runner run --scenario JS-004 --model unused \
  --agent external:strix --external-command <proposal-stream-command> \
  --upstream <authorized-target> --enforce-policy
```

The external command must write one JSON proposal object per stdout line and
must not execute the target. Without `--external-command`, the adapter can be
loaded for metadata inspection but has no proposals and terminates with an
adapter parse failure.

Metadata records `adapter_name`, adapter and Strix versions,
`installation_source_version`, provider/model, and declared capabilities in
`result.json`, `provenance.json`, and `manifest.json`.
