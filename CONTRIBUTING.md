# Contributing

Use Python 3.9 or later. Runtime code must remain local-only and dependency-free.

Run `python3 -m unittest discover -s tests -v` and `python3 scripts/check_release.py`
before committing. If a formatter is available, use Black with the settings in
`pyproject.toml`.

Use synthetic fixtures to reproduce adapter bugs. Do not submit actual session
logs, prompts, response IDs, user paths, credentials, billing exports, or generated
reports. Minimize fixtures to the metadata necessary to demonstrate a problem.

Measurement changes need tests for bucket exclusivity, stream deduplication,
unknown prices, and time visibility. Forecast changes must retain zero remaining
usage, sparse-history refusal, and task-family isolation. Historical checkpoint
selection must not depend on future observations.
