"""Independent parsers. Read data only; no subprocess/network calls."""

import hashlib, json, pathlib
from .core import Call, BUCKETS, normalize, timestamp, object_field


def objects(path, ledger, as_of):
    try:
        with path.open(encoding="utf8", errors="replace") as f:
            for line in f:
                try:
                    o = json.loads(line)
                    if not isinstance(o, dict):
                        raise ValueError()
                    ts = timestamp(o.get("timestamp"))
                    if ts <= as_of:
                        yield o, ts
                except (ValueError, TypeError, OverflowError):
                    ledger.count("invalid_json_or_timestamp")
    except OSError:
        ledger.count("unreadable_files")


def codex_files(home):
    home = pathlib.Path(home).expanduser()
    active = home / "sessions"
    archive = home / "archived_sessions"
    if active.is_dir() or archive.is_dir():
        found = {}
        for base in (active, archive):
            if base.is_dir():
                for p in sorted(base.rglob("*.jsonl")):
                    found.setdefault(str(p.relative_to(base)), p)
        return list(found.values())
    return sorted(home.rglob("*.jsonl")) if home.is_dir() else []


def parse_codex(path, ledger, as_of):
    meta = {}
    model = "unknown"
    tier = "unrecorded"
    turn = None
    explicit = []
    cumulative = []
    previous = None
    is_child = False
    for o, ts in objects(path, ledger, as_of):
        typ = o.get("type")
        v = o.get("payload") or {}
        if not isinstance(v, dict):
            continue
        if typ == "session_meta":
            meta = v
            src = v.get("source") or {}
            spawn = (
                src.get("subagent", {}).get("thread_spawn", {})
                if isinstance(src, dict) and isinstance(src.get("subagent"), dict)
                else {}
            )
            if not isinstance(spawn, dict):
                ledger.count("invalid_session_metadata")
                spawn = {}
            parent = (
                v.get("parent_thread_id")
                or v.get("forked_from_id")
                or spawn.get("parent_thread_id")
            )
            is_child = bool(parent or spawn or isinstance(src, dict) and "subagent" in src)
            if parent:
                ledger.parents["codex", str(v.get("id") or v.get("session_id"))] = str(parent)
            continue
        session = str(
            meta.get("id")
            or meta.get("session_id")
            or hashlib.sha256(str(path).encode()).hexdigest()
        )
        # Child replay formats vary. Explicit response IDs are globally deduped.
        # Cumulative child counters are deliberately withheld in this alpha.
        if typ == "turn_context":
            model = str(v.get("model") or "unknown")
            turn = v.get("turn_id") or turn
        if typ == "thread_settings_applied":
            tier = str(v.get("service_tier") or "unrecorded")
        if typ == "event_msg" and v.get("type") == "thread_settings_applied":
            settings = v.get("settings", v)
            if isinstance(settings, dict):
                tier = str(settings.get("service_tier") or "unrecorded")
        if typ == "event_msg" and v.get("type") == "task_started":
            turn = v.get("turn_id") or turn
            if turn:
                ledger.starts.setdefault(("codex", str(turn)), ts)
        if typ == "event_msg" and v.get("type") == "task_complete" and v.get("turn_id"):
            key = ("codex", str(v["turn_id"]))
            ledger.ends[key] = max(ts, ledger.ends.get(key, ts))
        if typ == "token_usage_record":
            rid = v.get("response_id")
            if not rid:
                ledger.count("missing_response_id")
                continue
            try:
                b, flags = normalize(v.get("usage"), "openai")
                explicit.append(
                    Call(
                        "codex",
                        str(v.get("thread_id") or session),
                        str(v.get("root_turn_id") or v.get("turn_id") or turn or session),
                        str(rid),
                        ts,
                        str(v.get("model") or model),
                        b,
                        "provider_response",
                        str(v.get("service_tier") or tier),
                        flags,
                    )
                )
            except (ValueError, TypeError):
                ledger.count("invalid_usage")
        if typ == "event_msg" and v.get("type") == "token_count":
            info = v.get("info") or {}
            if not isinstance(info, dict):
                ledger.count("invalid_usage")
                continue
            total = info.get("total_token_usage")
            if not isinstance(total, dict):
                continue
            if is_child:
                ledger.count("withheld_child_cumulative_snapshots")
                continue
            try:
                current, _ = normalize(total, "openai")
                last = info.get("last_token_usage")
                # Inclusive totals must advance. Repeated snapshots add nothing.
                now = sum(current.values())
                before = sum(previous.values()) if previous else 0
                if previous and now <= before:
                    if now == before:
                        ledger.count("repeated_cumulative_snapshot")
                        continue
                    ledger.count("counter_resets")
                    if not isinstance(last, dict):
                        previous = current
                        continue
                    b, flags = normalize(last, "openai")
                else:
                    b = {k: current[k] - (previous[k] if previous else 0) for k in BUCKETS}
                    flags = []
                    # Bucket composition can be revised while the total advances.
                    if any(n < 0 for n in b.values()):
                        if not isinstance(last, dict):
                            ledger.count("unresolved_counter_revision")
                            previous = current
                            continue
                        b, flags = normalize(last, "openai")
                        flags.append("counter_revision_last_usage")
                previous = current
                if not sum(b.values()):
                    continue
                fingerprint = hashlib.sha256(
                    json.dumps([session, ts, current], sort_keys=True).encode()
                ).hexdigest()
                cumulative.append(
                    Call(
                        "codex",
                        session,
                        str(turn or session),
                        fingerprint,
                        ts,
                        model,
                        b,
                        "cumulative_delta",
                        tier,
                        flags,
                    )
                )
            except (ValueError, TypeError):
                ledger.count("invalid_usage")
    if explicit:
        for c in explicit:
            ledger.add(c)
        ledger.count("files_explicit_usage")
        ledger.count("suppressed_cumulative_records", len(cumulative))
    else:
        for c in cumulative:
            ledger.add(c)
        ledger.count("files_cumulative_only")
    if not explicit and not cumulative:
        ledger.count("files_without_accepted_usage")


def parse_claude(path, ledger, as_of):
    session = None
    turn = None
    model = "unknown"
    last_user = None
    seen = False
    for o, ts in objects(path, ledger, as_of):
        typ = o.get("type")
        msg = o.get("message") or {}
        if not isinstance(msg, dict):
            continue
        session = str(
            o.get("sessionId") or session or hashlib.sha256(str(path).encode()).hexdigest()
        )
        # Tool-result user records are not new human turns.
        content = msg.get("content")
        human = (
            typ == "user"
            and (
                isinstance(content, str)
                or isinstance(content, list)
                and any(isinstance(x, dict) and x.get("type") == "text" for x in content)
            )
            and not o.get("isMeta")
        )
        if human:
            turn = str(o.get("uuid") or ts)
            last_user = ts
            ledger.starts.setdefault(("claude", session + ":" + turn), ts)
        if typ != "assistant" or not isinstance(msg.get("usage"), dict):
            continue
        if msg.get("model") == "<synthetic>":
            ledger.count("synthetic_assistant_ignored")
            continue
        rid = msg.get("id") or o.get("requestId")
        if not rid:
            ledger.count("missing_response_id")
            continue
        try:
            u = msg["usage"]
            b, flags = normalize(u, "anthropic")
            model = str(msg.get("model") or model)
            if o.get("isSidechain") or o.get("agentId"):
                flags.append("subagent_in_session_family")
            run = session + ":" + str(turn or "unattributed")
            if turn is None:
                flags.append("turn_unattributed")
            server = u.get("server_tool_use") or {}
            tool = bool(
                isinstance(server, dict)
                and any(isinstance(n, (int, float)) and n > 0 for n in server.values())
            ) or bool(u.get("iterations"))
            ledger.add(
                Call(
                    "claude",
                    session,
                    run,
                    str(rid),
                    ts,
                    model,
                    b,
                    "assistant_message",
                    str(u.get("speed") or u.get("service_tier") or "unrecorded"),
                    flags,
                    str(u.get("inference_geo") or "unrecorded"),
                    tool,
                ),
                replace=True,
            )
            # end_turn closes this CLI interaction, not a business success label.
            if turn and msg.get("stop_reason") == "end_turn":
                ledger.ends["claude", run] = max(ts, ledger.ends.get(("claude", run), ts))
            seen = True
        except (ValueError, TypeError):
            ledger.count("invalid_usage")
    if not seen:
        ledger.count("files_without_accepted_usage")


def parse_generic(path, ledger, as_of):
    for o, ts in objects(path, ledger, as_of):
        try:

            def identifier(name):
                value = o[name]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(name + " must be a nonempty string")
                return value

            agent = identifier("agent")
            run = identifier("run_id")
            key = (agent, run)
            if o.get("type") == "task_started":
                ledger.starts.setdefault(key, ts)
                continue
            if o.get("type") == "task_complete":
                ledger.ends[key] = max(ts, ledger.ends.get(key, ts))
                continue
            if o.get("type") != "usage":
                ledger.count("unsupported_generic_events")
                continue
            b, flags = normalize(o.get("usage"), o.get("usage_schema", "exclusive"))
            ledger.add(
                Call(
                    agent,
                    identifier("session_id"),
                    run,
                    identifier("response_id"),
                    ts,
                    str(o.get("model") or "unknown"),
                    b,
                    "generic_contract",
                    str(o.get("service_tier") or "unrecorded"),
                    flags,
                    str(o.get("inference_geo") or "unrecorded"),
                    bool(o.get("unpriced_tool_usage")),
                )
            )
        except (KeyError, ValueError, TypeError):
            ledger.count("invalid_generic_contract")
