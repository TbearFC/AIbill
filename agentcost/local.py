"""Collect retained local logs without exporting paths or raw content."""

import dataclasses
import datetime as dt
import json
import os
from pathlib import Path

from .api import AnalysisOptions, analyze_sources, discover_sources
from .core import Prices
from .report import write_report


def private_output_dir():
    import sys

    if sys.platform == "darwin":
        return Path.home() / "Library/Application Support/AIbill/reports/dashboard"
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "AIbill/reports/dashboard"
    return (
        Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
        / "aibill/reports/dashboard"
    )


@dataclasses.dataclass(frozen=True)
class LocalConfig:
    codex_home: str
    claude_home: str
    price_path: str = ""

    def updated(self, changes, check_prices=True):
        if not isinstance(changes, dict) or set(changes) - {
            "codex_home",
            "claude_home",
            "price_path",
        }:
            raise ValueError("unsupported source settings")
        if any(not isinstance(v, str) or len(v) > 4096 or "\x00" in v for v in changes.values()):
            raise ValueError("source settings must be local path strings")
        config = dataclasses.replace(self, **changes)
        if not config.codex_home.strip() or not config.claude_home.strip():
            raise ValueError("log directories must not be empty")
        homes = [p.strip() for p in config.codex_home.split(",") if p.strip()]
        if not homes:
            raise ValueError("log directories must not be empty")
        config = dataclasses.replace(
            config,
            codex_home=",".join(str(Path(p).expanduser().resolve()) for p in homes),
            claude_home=str(Path(config.claude_home.strip()).expanduser().resolve()),
            price_path=(
                str(Path(config.price_path.strip()).expanduser().resolve())
                if config.price_path.strip()
                else ""
            ),
        )
        for root in [*config.codex_home.split(","), config.claude_home]:
            path = Path(root)
            if path.exists() and not path.is_dir():
                raise ValueError("log roots must be directories")
        if config.price_path and check_prices:
            # Validate before starting a scan; errors must leave the old snapshot intact.
            try:
                Prices(Path(config.price_path).expanduser())
            except (OSError, ValueError, KeyError, TypeError):
                raise ValueError("price catalog is missing or invalid") from None
        return config

    def public_settings(self):
        # These settings are loopback-only, never embedded in report HTML/JSON.
        return dataclasses.asdict(self)


def load_config(path, default):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return default.updated(json.loads(path.read_text(encoding="utf8")), check_prices=False)
    except (OSError, ValueError, TypeError):
        raise ValueError(
            "saved source settings are invalid; override the log directories or price catalog"
        ) from None


class LocalCollector:
    def __init__(self, config, out, *, agent="all", inputs=(), options=None):
        self.config = config
        self.out = Path(out)
        self.agent = agent
        self.inputs = tuple(inputs)
        self.options = options or AnalysisOptions("")

    def collect(self, config=None):
        config = config or self.config
        options = dataclasses.replace(
            self.options,
            as_of=self.options.as_of or dt.datetime.now(dt.timezone.utc).isoformat(),
        )
        homes = [p.strip() for p in config.codex_home.split(",") if p.strip()]
        sources = discover_sources(
            agent=self.agent,
            codex_homes=homes,
            claude_home=config.claude_home,
            inputs=self.inputs,
        )
        data = analyze_sources(
            sources,
            options,
            Prices(Path(config.price_path).expanduser() if config.price_path else None),
        )
        source_states = []
        for agent, roots in (("codex", homes), ("claude", [config.claude_home])):
            selected = self.agent in ("all", agent)
            available = sum(Path(p).expanduser().is_dir() for p in roots)
            count = data["source_inventory"][agent + "_files"]
            observed = data["log_observations"][agent]
            state = (
                "not_selected"
                if not selected
                else (
                    "missing_directory"
                    if not available
                    else (
                        "empty_directory"
                        if not count
                        else "no_accepted_usage" if not observed["records"] else "ready"
                    )
                )
            )
            source_states.append(
                dict(
                    agent=agent,
                    status=state,
                    files=count,
                    available_roots=available,
                    configured_roots=len(roots),
                    **observed,
                )
            )
        data["collection"] = {
            "mode": "local",
            "sources": source_states,
            "collected_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "scope": "retained_local_logs_only",
            "fixed_as_of": self.options.as_of is not None,
            "refresh_supported": True,
        }
        return data

    def publish(self, data, config=None):
        config = config or self.config
        self.out.mkdir(mode=0o700, parents=True, exist_ok=True)
        report = write_report(data, self.out)
        # Local settings stay outside the served file set and aggregate exports.
        settings = self.out / "sources.json"
        temporary = self.out / ".sources.tmp"
        with temporary.open("w", encoding="utf8") as f:
            json.dump(config.public_settings(), f, ensure_ascii=False, indent=2)
        temporary.chmod(0o600)
        temporary.replace(settings)
        self.config = config
        return report
