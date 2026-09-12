# Scripts Directory

This directory is reserved for live-provider utilities (e.g., live model matrix tests, provider compatibility checks).

**Current status:** No live-provider utilities exist in this repository.

- There is no `run_tests.py` script.
- There is no `live_model_matrix.py` or similar utility.
- Pytest discovery is clean and only collects unit/integration tests under `tests/`.

## Future Guidance

If a live-provider utility is needed later:

1. Place it under `scripts/` (this directory).
2. Make it import-safe: importing the module must not execute network code or require credentials.
3. Make it explicitly opt-in: execution should require a deliberate flag (e.g., `--live`, `--provider=...`).
4. Keep it outside pytest discovery: do not name it `test_*.py` or place it under `tests/`.
5. Document the actual invocation and network requirements in this README.