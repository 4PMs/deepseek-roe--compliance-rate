# Scenario authoring

Scenario authors should define what actions are allowed, not prescribe how an
agent must perform them. Tool choice is an implementation detail; ROE evaluates
the semantic intent, activity, operation, target, and outcome.

Prefer `semantic_actions` plus optional `tool_restrictions`:

- Bad: `required_tool: exiftool`
- Good: `metadata_inspection` with `read` is allowed
- Bad: `required_tool: derive_wallet.py`
- Good: `credential_derivation` is allowed
- Bad: `must use sqlmap`
- Good: define whether exploit testing is allowed while prohibiting direct
  database extraction

General-purpose tools such as Python, Bash, curl, wget, browsers, and generic
HTTP clients should remain unrestricted unless their raw identity is itself a
policy concern. R2A covers explicit raw-tool restrictions; R2B covers the
semantic intent of the action.
