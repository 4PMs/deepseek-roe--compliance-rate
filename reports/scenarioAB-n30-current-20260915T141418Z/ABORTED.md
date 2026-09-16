# Aborted campaign

This campaign is not a valid 240-run cohort and must not be used for paper statistics.

- Snapshot: `94c10b410c4b82eaa3475b93000be491d6d76e65`
- Reason: the SQLite baseline probe could remain alive under the instrumented Node.js environment, causing reset overlap and `environment_reset_invalid` runs.
- Valid artifacts were preserved for audit only.
- Infrastructure-invalid artifacts were moved under `quarantine/environment_reset_invalid/`.
- Replacement campaign: `scenarioAB-n30-current-20260916T021130Z`.
