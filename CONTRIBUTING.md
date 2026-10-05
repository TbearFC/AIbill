# Contributing

Use Python 3.9 or later. Runtime code must remain local-only and dependency-free.

Run `python3 -m unittest discover -s tests -v` and `python3 scripts/check_release.py`
before committing. If a formatter is available, use Black with the settings in
`pyproject.toml`.

Use synthetic fixtures to reproduce adapter bugs. Do not submit actual session
logs, prompts, response IDs, user paths, credentials, billing exports, or generated
reports. Minimize fixtures to the metadata necessary to demonstrate a problem.

The public-data study under `research/open-data-2026-10-05/` is a separate,
explicitly attributed research package. Only its exact reviewed source files,
protocols and aggregate snapshots are release-allowlisted. Never add its `data/`
or `outputs/` directories, raw trajectories, per-task exports, or completed user
record templates. Research dependencies are optional and do not enter the runtime.
To verify that study, install its separate dependencies and run
`python -m unittest -v test_integrity` from its directory.

Measurement changes need tests for bucket exclusivity, stream deduplication,
unknown prices, and time visibility. Forecast changes must retain zero remaining
usage, sparse-history refusal, and task-family isolation. Historical checkpoint
selection must not depend on future observations.
