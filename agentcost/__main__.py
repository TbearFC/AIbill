import argparse, datetime as dt, json, os, pathlib, sys
from .core import Prices, timestamp
from .api import AnalysisOptions, LogSource, analyze_sources, discover_sources
from .report import write_report


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="AgentCost: local-only coding-agent usage statistics and experimental priors"
    )
    ap.add_argument("command", choices=["report", "forecast", "demo", "dashboard"])
    ap.add_argument("--agent", choices=["all", "codex", "claude", "generic"], default="all")
    ap.add_argument(
        "--codex-home", default=os.environ.get("CODEX_HOME", str(pathlib.Path.home() / ".codex"))
    )
    ap.add_argument(
        "--claude-home",
        default=os.environ.get("CLAUDE_CONFIG_DIR", str(pathlib.Path.home() / ".claude")),
    )
    ap.add_argument(
        "--input",
        action="append",
        default=[],
        help="Generic contract JSONL; repeat for other agents",
    )
    ap.add_argument(
        "--since", help="Inclusive UTC date YYYY-MM-DD; reporting only, history stays available"
    )
    ap.add_argument(
        "--until", help="Inclusive UTC date YYYY-MM-DD; also caps ingestion to that date"
    )
    ap.add_argument(
        "--as-of", help="Timezone-aware timestamp; observations after it are never ingested"
    )
    ap.add_argument(
        "--prices",
        help="Verified local price catalog; bundled default has fictional demo models only",
    )
    ap.add_argument(
        "--k",
        default="latest",
        help="latest for newest open interaction, or fixed positive usage checkpoint",
    )
    ap.add_argument(
        "--budget-tokens",
        type=int,
        help="Optional logical input+output budget for empirical exceedance",
    )
    ap.add_argument(
        "--out", help="Output directory; live dashboard defaults to private app storage"
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--local", action="store_true", help="Dashboard: local logs (the default)")
    mode.add_argument("--demo", action="store_true", help="Dashboard: explicit synthetic demo")
    ap.add_argument(
        "--port", type=int, default=8765, help="Loopback dashboard port; 0 selects a free port"
    )
    args = ap.parse_args(argv)
    if (args.local or args.demo) and args.command != "dashboard":
        ap.error("--local and --demo are only supported by dashboard")
    from .local import LocalCollector, LocalConfig, load_config, private_output_dir

    args.out = args.out or (
        str(private_output_dir()) if args.command == "dashboard" else "agentcost_reports"
    )
    if not 0 <= args.port <= 65535:
        ap.error("port must be between 0 and 65535")
    is_demo = args.command == "demo" or (args.command == "dashboard" and args.demo)
    if args.command == "dashboard" and is_demo and not args.as_of:
        args.as_of = "2026-10-03T13:35:00Z"
    if (
        args.command == "dashboard"
        and is_demo
        and (args.input or args.prices or args.agent != "all")
    ):
        ap.error("remove --demo to analyze log inputs, prices or agent selection")
    try:
        as_of = timestamp(args.as_of or dt.datetime.now(dt.timezone.utc).isoformat())
        for d in (args.since, args.until):
            if d:
                dt.date.fromisoformat(d)
        if args.since and args.until and args.since > args.until:
            raise ValueError("since must not exceed until")
        if args.until:
            as_of = min(as_of, timestamp(args.until + "T23:59:59.999999Z"))
        if args.k != "latest" and (not args.k.isdigit() or int(args.k) < 1):
            raise ValueError("k must be latest or positive integer")
        if args.budget_tokens is not None and args.budget_tokens <= 0:
            raise ValueError("budget must be positive")
        if not is_demo:
            for filename in args.input:
                if not pathlib.Path(filename).expanduser().is_file():
                    raise ValueError("input JSONL file does not exist: " + filename)
            if args.command != "dashboard":
                for option in ("--codex-home", "--claude-home"):
                    if argv is None:
                        requested = any(
                            x == option or x.startswith(option + "=") for x in sys.argv[1:]
                        )
                    else:
                        requested = any(x == option or x.startswith(option + "=") for x in argv)
                    if requested:
                        value = args.codex_home if option == "--codex-home" else args.claude_home
                        for home in value.split(",") if option == "--codex-home" else [value]:
                            if not pathlib.Path(home.strip()).expanduser().is_dir():
                                raise ValueError(option + " directory does not exist: " + home)
        prices = Prices(args.prices)
    except (ValueError, OSError, KeyError) as e:
        ap.error(str(e))
    collector = None
    if args.command == "dashboard" and not is_demo:
        explicit = sys.argv[1:] if argv is None else argv

        def requested(option):
            return any(v == option or v.startswith(option + "=") for v in explicit)

        config = LocalConfig(args.codex_home, args.claude_home, args.prices or "")
        try:
            saved = load_config(pathlib.Path(args.out) / "sources.json", config)
            config = LocalConfig(
                (
                    args.codex_home
                    if requested("--codex-home") or os.environ.get("CODEX_HOME")
                    else saved.codex_home
                ),
                (
                    args.claude_home
                    if requested("--claude-home") or os.environ.get("CLAUDE_CONFIG_DIR")
                    else saved.claude_home
                ),
                (args.prices or "") if requested("--prices") else saved.price_path,
            ).updated({})
            options = AnalysisOptions(
                args.as_of or None, args.since, args.until, args.k, args.budget_tokens
            )
            collector = LocalCollector(
                config, args.out, agent=args.agent, inputs=args.input, options=options
            )
            print("Reading retained local Codex / Claude Code logs...", flush=True)
            data = collector.collect()
            output = collector.publish(data)
        except (OSError, ValueError, KeyError) as e:
            ap.error("cannot collect local logs: " + str(e))
        source_count = sum(data["source_inventory"].values())
    else:
        if is_demo:
            from .demo import make_demo

            folder = pathlib.Path(args.out) / "synthetic_inputs"
            make_demo(folder)
            sources = [LogSource("generic", path) for path in sorted(folder.glob("*.jsonl"))]
        else:
            sources = discover_sources(
                agent=args.agent,
                codex_homes=[home.strip() for home in args.codex_home.split(",")],
                claude_home=args.claude_home,
                inputs=args.input,
            )
        options = AnalysisOptions(as_of, args.since, args.until, args.k, args.budget_tokens)
        data = analyze_sources(sources, options, prices)
        if is_demo:
            data["synthetic_demo"] = True
        try:
            output = write_report(data, args.out)
        except OSError as e:
            ap.error("cannot write report: " + str(e))
        source_count = len(sources)
    print(
        json.dumps(
            {
                "version": data["version"],
                "report": str(output.resolve()),
                "aggregate_json": str(output.with_name("usage.json").resolve()),
                "summary": data["totals"],
                "forecast": data["forecasts"],
                "files_scanned": source_count,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if data["scan_status"] == "no_accepted_usage":
        print(
            "No accepted usage; inspect the report audit and selected source paths.",
            file=sys.stderr,
        )
    if args.command == "dashboard":
        from .dashboard import serve_report

        try:
            serve_report(output, args.port, collector)
        except OSError as e:
            ap.error("cannot start dashboard: " + str(e))
    return 0 if data["scan_status"] == "accepted_usage" else 1


if __name__ == "__main__":
    raise SystemExit(main())
