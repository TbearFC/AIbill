"""Reusable local analysis API; callers receive aggregates, not raw log records."""

import dataclasses
import pathlib
from typing import Iterable, Optional

from .adapters import codex_files, parse_claude, parse_codex, parse_generic
from .analysis import summarize
from .core import Ledger, Prices, timestamp


@dataclasses.dataclass(frozen=True)
class LogSource:
    """An explicitly selected JSONL file and its supported adapter."""

    adapter: str
    path: pathlib.Path


@dataclasses.dataclass(frozen=True)
class AnalysisOptions:
    """Observation boundary, reporting period, and experimental checkpoint."""

    as_of: str
    since: Optional[str] = None
    until: Optional[str] = None
    checkpoint: str = "latest"
    budget_tokens: Optional[int] = None


ADAPTERS = {"codex": parse_codex, "claude": parse_claude, "generic": parse_generic}


def discover_sources(agent="all", codex_homes=(), claude_home=None, inputs=()):
    """Discover files beneath caller-selected roots without reading their contents."""
    if agent not in {"all", "codex", "claude", "generic"}:
        raise ValueError("unsupported agent selection")
    sources = []
    if agent in {"all", "codex"}:
        for home in codex_homes:
            sources.extend(LogSource("codex", path) for path in codex_files(home))
    if agent in {"all", "claude"} and claude_home is not None:
        folder = pathlib.Path(claude_home).expanduser()
        if (folder / "projects").is_dir():
            folder = folder / "projects"
        if folder.is_dir():
            sources.extend(LogSource("claude", path) for path in sorted(folder.rglob("*.jsonl")))
    sources.extend(LogSource("generic", pathlib.Path(path).expanduser()) for path in inputs)
    # Repeated or overlapping configured roots must not inflate source counts.
    unique = {}
    for source in sources:
        unique.setdefault((source.adapter, source.path.resolve()), source)
    return list(unique.values())


def analyze_sources(sources: Iterable[LogSource], options: AnalysisOptions, prices=None):
    """Read selected inputs and return a metadata-only aggregate report.

    Does not write files, request a model, or transmit logs. Usage gaps are recorded
    in ``audit``. Model and Agent labels remain in aggregates and may be sensitive.
    """
    import datetime as dt

    as_of = timestamp(options.as_of)
    for date in (options.since, options.until):
        if date:
            dt.date.fromisoformat(date)
    if options.since and options.until and options.since > options.until:
        raise ValueError("since must not exceed until")
    if options.until:
        as_of = min(as_of, timestamp(options.until + "T23:59:59.999999Z"))
    checkpoint = str(options.checkpoint)
    if checkpoint != "latest" and (not checkpoint.isdigit() or int(checkpoint) < 1):
        raise ValueError("checkpoint must be latest or a positive integer")
    budget = options.budget_tokens
    if budget is not None and (
        isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0
    ):
        raise ValueError("budget must be a positive integer")
    prices = prices or Prices()
    ledger = Ledger()
    inventory = dict(codex_files=0, claude_files=0, generic_files=0)
    for source in sources:
        if source.adapter not in ADAPTERS:
            raise ValueError("unsupported log adapter")
        ADAPTERS[source.adapter](pathlib.Path(source.path).expanduser(), ledger, as_of)
        ledger.count("files_scanned")
        inventory[source.adapter + "_files"] += 1
    ledger.finalize()
    data = summarize(ledger, prices, as_of, options.since, options.until, checkpoint, budget)
    observations = {}
    for agent, kinds in (
        ("codex", {"provider_response", "cumulative_delta"}),
        ("claude", {"assistant_message"}),
    ):
        rows = [
            c
            for c in ledger.rows(as_of)
            if c.agent == agent
            and c.source in kinds
            and (not options.since or c.ts[:10] >= options.since)
            and (not options.until or c.ts[:10] <= options.until)
        ]
        observations[agent] = {
            "records": len(rows),
            "tokens": sum(c.tokens for c in rows),
            "first_observed_at": rows[0].ts if rows else None,
            "last_observed_at": rows[-1].ts if rows else None,
        }
    data["log_observations"] = observations
    data["scan_status"] = "accepted_usage" if ledger.calls else "no_accepted_usage"
    data["source_inventory"] = inventory
    data["price_catalog_kind"] = (
        "synthetic_example" if prices.data.get("synthetic_example") else "user_supplied"
    )
    return data
