# AIbill

AIbill includes the AgentCost CLI, Python library, and local research dashboard.

A local CLI and Python library for retained agent usage, catalog-based cost estimates,
and experimental remaining-token priors. Supports Codex JSONL, Claude Code JSONL,
and a generic event contract. Python 3.9+, with no runtime dependencies.

## Quick start

```bash
python3 -m agentcost demo --as-of 2026-10-03T13:35:00Z --out reports/demo
python3 -m unittest discover -s tests -v
```

Install a command-line entry point:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/agentcost --help
```

On Windows use `.venv\Scripts\python` and `.venv\Scripts\agentcost`.
Without installation, `python3 agentcost_cli.py ...` also works.

## Research workspace and one-command local deployment

On macOS, double-click `start.command` (Python 3.9+ required), or run:

```bash
./start.sh
# after installation, from any working directory:
agentcost dashboard
# synthetic demo is an explicit opt-in:
agentcost dashboard --demo
```

**The dashboard now defaults to retained local logs, not demo data.** It discovers
Codex active and archived sessions under `CODEX_HOME` or the current user's
`~/.codex`, and Claude Code project/subagent JSONL under `CLAUDE_CONFIG_DIR` or
`~/.claude`. No personal path is embedded in the source distribution.

Use the **Log directories** dialog to set your own Codex / Claude Code roots and
an optional verified price catalog. Existing environment variables and explicit
CLI roots take priority at startup; otherwise the last local settings are reused.
`--codex-home` also accepts multiple roots separated by commas. A Claude root can
be its configuration directory or its `projects` directory.

```bash
agentcost dashboard --codex-home ./my-codex-root --claude-home ./my-claude-root
agentcost dashboard --prices ./my-prices.json --port 8766
agentcost dashboard --agent generic --input examples/events.jsonl
```

The loopback URL is printed after collection. Port 8765 is the default; `--port 0`
selects a free port. Stop with Ctrl+C. The launcher does not open a browser.

Click **Read logs again** to discover and reread logs. Optional 60-second automatic
refresh is off by default. Refresh runs in the background; the page retains the
last complete snapshot until success. New files and appended usage are included.
Failures retain the last report and show a diagnostic message. A supplied `--as-of`
fixes historical replay; omit it for the current observation boundary.

Overview source cards distinguish accepted usage, missing directories, empty roots,
and files with no accepted usage. An absent source is never replaced by synthetic
records. Tokens and mutually exclusive cache/output buckets come from accepted
log records; forecasts are separate experimental priors. Actual invoices and quotas
remain unavailable. Unknown model prices remain unknown, not zero or fictional rates.

Agent/date filters affect overview, trends, and model details consistently. Forecast
and audit panels retain their stated historical evidence scope. Other interactions
include day selection, budget comparison, dark mode, and aggregate JSON export.

By default live output and settings stay in private per-user application storage:
macOS `~/Library/Application Support/AIbill/reports/dashboard`, Linux's XDG data
location, or Windows' local application data. `--out` overrides the output directory.
Directory settings are stored locally in `sources.json` (owner-only file permissions
on Unix); they
are not embedded in the HTML, included in aggregate JSON, or served as a file.
The settings API is accessible only to the local dashboard. Raw prompts, request
IDs, session IDs, and log contents are never served or exported.

The server binds only to `127.0.0.1`, serves an explicit set of aggregate/UI routes,
and requires a matching Origin and custom header for scan requests. It provides no
filesystem browser or remote log upload. Standalone exported reports work offline;
refresh is available only through the local Python server. Public hosting is a
separate step and cannot read someone else's local logs from a web browser.

## Real logs

```bash
agentcost report --out reports/local
agentcost report --agent codex --k 5 --out reports/checkpoint
agentcost forecast --agent codex --k latest --budget-tokens 10000000 --out reports/forecast
agentcost report --agent generic --input examples/events.jsonl --out reports/generic
agentcost report --prices my-prices.json --out reports/priced
```

By default the CLI reads `CODEX_HOME` or `~/.codex`, and `CLAUDE_CONFIG_DIR` or
`~/.claude`. Choose roots with `--codex-home` and `--claude-home`. Generic `--input`
may be repeated. Explicit missing paths fail before report generation.

Each output directory contains `usage.json` and a standalone, offline `report.html`.
The CLI does not call model APIs, upload logs, or write input files. Its console
output includes destination paths and aggregates; keep those outputs private too.

## Prices

**The bundled catalog contains fictional example models and rates only.** It is
for tests and demos, not a provider price list. Real model names remain unpriced
unless supplied in a verified local catalog using `--prices`.

Use `examples/prices.json` as a schema example; replace model names, rates, terms,
version, and basis with your applicable price conditions. Set `synthetic_example`
to `false` for an actual catalog. Unknown models, missing bucket rates, unknown
cache TTLs, or unsupported service conditions retain tokens with unknown costs.

The known-price subtotal is not an invoice, subscription deduction, or account
total. The tool does not connect to billing or quota APIs.

## Python API

```python
from pathlib import Path
from agentcost.api import AnalysisOptions, LogSource, analyze_sources

report = analyze_sources(
    [LogSource("generic", Path("examples/events.jsonl"))],
    AnalysisOptions(as_of="2026-10-03T13:35:00Z", checkpoint="5"),
)
print(report["totals"]["tokens"])
```

`analyze_sources` reads only the given files and returns aggregates. It does not
generate HTML or write files. Pass a `Prices(path)` object to use a custom catalog.

## Measurement and forecast limits

- Usage buckets are mutually exclusive: uncached input, cache read, 5-minute and
  1-hour cache write, and output. Reasoning already included in output is not added.
- Explicit Codex response IDs take precedence over overlapping cumulative counters.
  Child cumulative snapshots are withheld rather than counted as new usage.
- Deduplication is an adapter-level approximation, not a universal multi-account
  invoice ledger. Mixed or missing source coverage is audited, not filled in.
- An interaction ending does not imply a successful customer delivery.
- History must have ended before the query checkpoint and exclude linked families.
  Forecasts require at least 20 runs and 10 families. Sparse support is refused.
- `latest` selects an observed open interaction within 24 hours. Fixed `--k` is
  historical checkpoint replay, not a current live forecast.
- P10/P50/P90 are empirical priors, not calibrated guarantees or hard budget limits.
- Dates are UTC. `--until` caps ingestion; `--since` filters reported totals while
  earlier history remains available for forecasts.
- Exit 0 means usage was accepted; exit 1 means no accepted usage (a diagnostic report
  is still produced); exit 2 means invalid CLI configuration or report write failure.

## Project structure

| Module | Responsibility |
|---|---|
| `agentcost/api.py` | Source selection and reusable analysis orchestration |
| `agentcost/__main__.py` | CLI options and report output |
| `agentcost/adapters.py` | Codex, Claude, and generic JSONL adapters |
| `agentcost/core.py` | Normalization, deduplication, and catalog pricing |
| `agentcost/analysis.py` | Aggregation, history qualification, and empirical priors |
| `agentcost/report.py`, `agentcost/dashboard.html` | Offline research workspace |
| `agentcost/dashboard.py`, `agentcost/local.py`, `start.sh`, `start.command` | Local collection, private settings, refresh service, and launchers |
| `agentcost/demo.py` | Synthetic input generation |
| `tests/` | Synthetic unit, regression, and CLI integration fixtures |

## Privacy and release preparation

This repository ships code, synthetic fixtures, and fictional pricing. It does not
ship personal logs, machine details, prompts, attachments, usage snapshots, or research
handoff documents. All example identifiers are artificial.

Generated aggregates can still reveal usage, model labels, activity times, and costs;
they are not automatically anonymous. `.gitignore` excludes generated reports and
local inputs. Do not force-add those files.

Before publishing:

```bash
python3 scripts/check_release.py
python3 -m unittest discover -s tests -v
git diff --cached --stat
```

The release check enforces an allowed file set, rejects symlinks and generated
artifacts, and detects common credentials and private-path patterns. It is a
repeatable check, not proof that every possible sensitive value can be recognized.

## Development

All tests create temporary synthetic inputs. They do not scan the user's logs.
Run `python3 -m unittest discover -s tests -v` from the repository root.

Source repository: https://github.com/TbearFC/AIbill
