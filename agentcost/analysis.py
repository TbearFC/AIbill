import collections, datetime as dt
from .core import BUCKETS, quantile, timestamp
from . import __version__


def new_group():
    return dict(
        records=0,
        tokens=0,
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=0,
        cache_write_tokens=0,
        priced_records=0,
        unpriced_tokens=0,
        known_api_equivalent_usd=0.0,
        sources={},
        unknown_price_reasons={},
    )


def add(group, c, price, reason):
    group["records"] += 1
    group["tokens"] += c.tokens
    group["input_tokens"] += c.input
    group["output_tokens"] += c.buckets["output"]
    group["cache_read_tokens"] += c.buckets["cache_read"]
    group["cache_write_tokens"] += c.buckets["cache_write_5m"] + c.buckets["cache_write_1h"]
    group["sources"][c.source] = group["sources"].get(c.source, 0) + 1
    if price is None:
        group["unpriced_tokens"] += c.tokens
        group["unknown_price_reasons"][reason] = group["unknown_price_reasons"].get(reason, 0) + 1
    else:
        group["priced_records"] += 1
        group["known_api_equivalent_usd"] += price


def finish(group):
    group["price_coverage_records"] = (
        group["priced_records"] / group["records"] if group["records"] else None
    )
    group["total_api_equivalent_usd"] = (
        group["known_api_equivalent_usd"]
        if group["priced_records"] == group["records"] and group["records"]
        else None
    )
    group["cache_read_fraction"] = (
        group["cache_read_tokens"] / group["input_tokens"] if group["input_tokens"] else None
    )
    return group


def remaining_forecast(ledger, rows, agent, k="latest", budget=None, prices=None, as_of=None):
    groups = collections.defaultdict(list)
    for c in rows:
        if c.agent == agent:
            groups[c.run].append(c)
    eligible = []
    for run, calls in groups.items():
        count = len(calls) if k == "latest" else int(k)
        if count < 1 or len(calls) < count:
            continue
        if any(
            "subagent_in_session_family" in c.flags or "turn_unattributed" in c.flags
            for c in calls[:count]
        ):
            continue
        end = ledger.ends.get((agent, run))
        # Completion after the last usage closes the run once observed.
        if k == "latest" and end and (as_of is None or end <= as_of):
            continue
        if k == "latest" and as_of:
            age = dt.datetime.fromisoformat(
                as_of.replace("Z", "+00:00")
            ) - dt.datetime.fromisoformat(calls[-1].ts.replace("Z", "+00:00"))
            if age > dt.timedelta(days=1):
                continue
        # Fixed-k replay selects by the visible checkpoint, never a future call.
        eligible.append((calls[count - 1].ts, run, calls, count))
    if not eligible:
        return {
            "agent": agent,
            "status": "no_open_or_eligible_interaction",
            "target": "root-turn proxy remaining input+output tokens",
        }
    _, run, current, count = max(eligible, key=lambda x: x[0])
    when = current[count - 1].ts
    # Historical completion must be observed strictly before this checkpoint;
    # exclude the query and every explicitly linked session family.
    family = ledger.find(("run", agent, run))
    pool = []
    for other, calls in groups.items():
        end = ledger.ends.get((agent, other))
        if other == run or not end or end >= when or len(calls) < count:
            continue
        if ledger.find(("run", agent, other)) == family:
            continue
        if any(
            "turn_unattributed" in c.flags or "subagent_in_session_family" in c.flags for c in calls
        ):
            continue
        if calls[-1].ts > end:
            continue
        pool.append((end, other, calls))
    pool.sort(key=lambda x: x[0])
    pool = pool[-50:]
    same = [p for p in pool if p[2][count - 1].model == current[count - 1].model]
    same_families = {ledger.find(("run", agent, p[1])) for p in same}
    scope = "recent50_agent_prior"
    if len(same) >= 20 and len(same_families) >= 10:
        pool = same
        scope = "model_stratified_recent50_prior"
    families = {ledger.find(("run", agent, p[1])) for p in pool}
    out = {
        "agent": agent,
        "status": "insufficient_history",
        "method": scope,
        "checkpoint_k": count,
        "snapshot_time": when,
        "history_runs": len(pool),
        "history_families": len(families),
        "target": "root-turn proxy remaining input+output tokens",
        "confidence": "experimental_unvalidated",
        "interval_semantics": "empirical P10/P90; not calibrated coverage",
        "business_success_inferred": False,
        "dollar_forecast": None,
        "quota_forecast": None,
    }
    if len(pool) < 20 or len(families) < 10:
        return out
    # Equal total mass per family; return quantiles of the resulting empirical
    # mixture rather than allowing a long-running family to dominate.
    sizes = collections.Counter(ledger.find(("run", agent, p[1])) for p in pool)
    weighted = []
    for _, other, calls in pool:
        value = sum(c.tokens for c in calls[count:])
        weight = 1 / sizes[ledger.find(("run", agent, other))]
        weighted.append((value, weight))
    weighted.sort()
    total = sum(w for _, w in weighted)

    def wq(p, values=weighted):
        cumul = 0
        for value, weight in values:
            cumul += weight
            if cumul >= p * total:
                return value
        return values[-1][0]

    out.update(
        status="experimental_prior",
        remaining_p10_tokens=wq(0.1),
        remaining_p50_tokens=wq(0.5),
        remaining_p90_tokens=wq(0.9),
        spent_tokens=sum(c.tokens for c in current[:count]),
    )
    if prices is not None:
        dollars = []
        complete = True
        for _, other, calls in pool:
            quotes = [prices.quote(c)[0] for c in calls[count:]]
            if any(q is None for q in quotes):
                complete = False
                break
            dollars.append((sum(quotes), 1 / sizes[ledger.find(("run", agent, other))]))
        if complete:
            dollars.sort()
            out["dollar_forecast"] = {
                "p10_usd": wq(0.1, dollars),
                "p50_usd": wq(0.5, dollars),
                "p90_usd": wq(0.9, dollars),
                "basis": "empirical continuation under the bundled reference price snapshot, not invoice; same history support as token prior",
            }
        else:
            out["dollar_forecast_unavailable_reason"] = (
                "at_least_one_historical_continuation_has_unpriced_usage"
            )
    if budget is not None:
        available = budget - out["spent_tokens"]
        out["budget_tokens"] = budget
        out["empirical_exceedance_fraction"] = sum(w for y, w in weighted if y > available) / total
        out["already_over_budget"] = available < 0
    return out


def summarize(ledger, prices, as_of, since=None, until=None, k="latest", budget=None):
    allrows = ledger.rows(as_of)
    rows = [
        c
        for c in allrows
        if (not since or c.ts[:10] >= since) and (not until or c.ts[:10] <= until)
    ]
    totals = new_group()
    agents = {}
    daily = {}
    models = {}
    model_daily = {}
    for c in rows:
        quote, reason = prices.quote(c)
        for g in [
            totals,
            agents.setdefault(c.agent, new_group()),
            daily.setdefault((c.ts[:10], c.agent), new_group()),
            models.setdefault((c.agent, c.model), new_group()),
            model_daily.setdefault((c.ts[:10], c.agent, c.model), new_group()),
        ]:
            add(g, c, quote, reason)
    data = {
        "model_daily": [
            dict(date=date, agent=agent, model=model, **finish(group))
            for (date, agent, model), group in sorted(model_daily.items())
        ],
        "version": __version__,
        "as_of": as_of,
        "period": {"since": since, "until": until, "timezone": "UTC"},
        "cost_basis": prices.data["basis"],
        "price_version": prices.data["version"],
        "actual_invoice_usd": None,
        "account_quota": None,
        "totals": finish(totals),
        "agents": [dict(agent=a, **finish(g)) for a, g in sorted(agents.items())],
        "daily": [dict(date=d, agent=a, **finish(g)) for (d, a), g in sorted(daily.items())],
        "models": [dict(agent=a, model=m, **finish(g)) for (a, m), g in sorted(models.items())],
        "audit": ledger.audit,
        "forecasts": [
            remaining_forecast(ledger, allrows, a, k, budget, prices, as_of) for a in sorted(agents)
        ],
        "run_rate": {},
        "limits": [
            "Local retained logs are not account-wide billing.",
            "Prices are a reference snapshot scenario, not historical rates or subscription charges.",
            "Root-turn proxies are not business episodes or quality-constrained success costs.",
            "Cumulative child rollouts without explicit usage are withheld to avoid counting inherited context.",
            "Forecasts are experimental priors, not proven cross-agent task-completion estimates.",
        ],
    }
    now = dt.datetime.fromisoformat(as_of.replace("Z", "+00:00"))
    start = (now - dt.timedelta(days=7)).date()
    stop = now.date()
    history = [c for c in allrows if start <= dt.date.fromisoformat(c.ts[:10]) < stop]
    for agent in sorted(agents):
        rr = [c for c in history if c.agent == agent]
        quotes = [prices.quote(c)[0] for c in rr]
        first = min(
            (dt.date.fromisoformat(c.ts[:10]) for c in allrows if c.agent == agent), default=stop
        )
        sufficient = first <= start and bool(rr)
        data["run_rate"][agent] = {
            "status": (
                "conditional_extrapolation" if sufficient else "insufficient_retained_history"
            ),
            "window": "7 complete UTC dates",
            "daily_retained_tokens": sum(c.tokens for c in rr) / 7 if sufficient else None,
            "next_30_days_tokens_if_usage_unchanged": (
                sum(c.tokens for c in rr) / 7 * 30 if sufficient else None
            ),
            "next_30_days_reference_usd_if_usage_unchanged": (
                sum(quotes) / 7 * 30 if sufficient and all(q is not None for q in quotes) else None
            ),
            "observed_active_dates": len({c.ts[:10] for c in rr}),
            "assumption": "retained-log pace stays unchanged; absent retained events are not proof of zero account usage",
        }
    return data
