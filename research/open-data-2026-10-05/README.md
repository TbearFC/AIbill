# Open-data agent cost research — 2026-10-05

This directory contains reproducible research code and aggregate findings from
15,639 public agent runs. It uses no personal Codex or Claude Code logs.
The report records both positive findings and a failed early-token-prediction
experiment; it does not establish production savings or a multi-user data advantage.

- [Research report](research.html): methods, evidence, limits and next experiments.
- [Numerical results](results.json): audits, repeated-run variation, frozen heldout
  predictions and exploratory paired comparisons.
- [Original protocol](protocol.json) and [target amendment](protocol-amendment.json):
  USD was absent in common-scaffold trajectories, so the target was changed to
  reported tokens before fitting any predictor.
- [Paired comparison CLI](compare_configs.py): dependency-free cost/quality comparison.

## Compare two configurations

The CLI needs Python 3.9+ and the standard library only. Supply your own UTF-8 CSV
or TSV with `benchmark`, `model`, `harness` (or `config`), `task_id`, `cost_usd`,
and `reward` (or `quality_score`, between 0 and 1). Repeated measurements require
unique `run_id` values and equal run counts per paired task.

```bash
python compare_configs.py --data my-task-records.csv \
  --benchmark my-task-batch --model my-model \
  --baseline current --candidate proposed --quality-margin 0.05
```

Use [the empty record template](task-record-template.csv). Do not commit completed
user records: put them in `data/` or outside the checkout. Set a common cost basis,
pricing revision and quality definition before comparing. A zero observed cost
differs from unknown cost; missing selected costs are rejected. Intervals resample
paired tasks, keeping repeated runs together. At least eight paired tasks are
required, but this minimum does not guarantee statistical power. Quality margins
are user choices, not benchmark-validated tolerances.

Only paired tasks are compared; unmatched coverage is explicitly counted. The
CLI compares configurations of one model at a time. It does not run agents,
contact model APIs, inspect local logs, interrupt sessions, or make forecasts.

## Reproduce the public-data study

The numerical study was validated with Python 3.12. Its optional scientific
dependencies do not change AIbill's dependency-free runtime.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-research.txt
.venv/bin/python fetch_sources.py
.venv/bin/python collect.py
.venv/bin/python analyze.py
.venv/bin/python dig_deeper.py
.venv/bin/python -m unittest -v test_integrity
```

On Windows use `.venv\Scripts\python` instead. Run commands from this directory.
`fetch_sources.py` downloads seven small pinned metadata/score files and verifies
their size and SHA256. `collect.py` downloads approximately 481 MB of compressed
public trajectories, validates all 85 uncompressed content hashes and writes
scalar-only features. It never executes downloaded code or unpickles data.
`data/` and `outputs/` are ignored and excluded from release inputs.

Generate the two examples and the local report after the analysis:

```bash
.venv/bin/python compare_configs.py \
  --data data/sources/right_fit/results/task_level.tsv \
  --benchmark ALE-CLI --model 'GPT-6 Astra' --baseline DSH --candidate PI \
  --output outputs/configuration-regression-example.json
.venv/bin/python compare_configs.py \
  --data data/sources/right_fit/results/task_level.tsv \
  --benchmark 'Terminal-Bench 4' --model 'Claude Opus 5' \
  --baseline OpenHands --candidate PI --output outputs/quality-regression-example.json
.venv/bin/python build_report.py
```

The published HTML, SVG figures and aggregate JSON/CSV files are a reviewed
snapshot. Reproduction writes separate local outputs. Per-task exports and
trajectory metadata are generated locally and are not redistributed here.
`dig_deeper.py` runs post-analysis exploratory diagnostics; it is not another
blind validation. All model names are source configuration labels, not current
provider recommendations. Neither dataset verifies actual customer invoices.

## Attribution and data terms

- **SWE-rebench July 2026 Trajectories**, published by `ibragim-bad`:
  [dataset](https://huggingface.co/datasets/ibragim-bad/swe_rebench_07_2026_trajectories),
  revision `cdae27cdd16673f0c682871ad55d325f24cc7020`.
  Task/code/output content retains underlying repository and provider terms;
  the source does not relicense that content. No raw trajectories are shipped.
- **Finding the Right Fit: Model–Harness Interactions across Agent Tasks**, Yixuan
  Li et al., 2026:
  [paper](https://arxiv.org/abs/2610.00917),
  [dataset](https://huggingface.co/datasets/yixuanli97/finding-the-right-fit),
  revision `92ffbd7daf5ce7bc4b1e996e962a46ca52876820`.
  The source is [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)
  and asks that data be used for analysis, not training, fine-tuning or distillation.
  Right Fit-derived numerical aggregates and figures here retain attribution and
  noncommercial research conditions. Changes: independent aggregation, paired-task
  bootstrap intervals and exploratory task-domain transfer diagnostics. Right Fit
  data was not used to fit the token predictor. Raw score tables are downloaded
  locally rather than bundled.

Source URLs, revisions and checksums are recorded in
[source-manifest.json](source-manifest.json). Aggregate outputs are research
evidence, not personal usage snapshots, and are separate from AIbill's synthetic
runtime examples and user-generated reports.
