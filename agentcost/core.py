import dataclasses, datetime as dt, json, math, pathlib

BUCKETS = ("input_uncached", "cache_read", "cache_write_5m", "cache_write_1h", "output")


def object_field(value, name):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(name + " must be an object")
    return value


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("missing timestamp")
    x = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if x.tzinfo is None:
        raise ValueError("timestamp must contain timezone")
    return x.astimezone(dt.timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def integer(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or value < 0
        or (isinstance(value, float) and (not math.isfinite(value) or int(value) != value))
    ):
        raise ValueError("usage must be nonnegative integer")
    return int(value)


def normalize(usage, schema):
    if not isinstance(usage, dict):
        raise ValueError("usage must be an object")
    b = dict.fromkeys(BUCKETS, 0)
    flags = []
    if schema == "openai":
        details = object_field(
            usage.get("input_tokens_details", usage.get("prompt_tokens_details")),
            "input_tokens_details",
        )
        inp = integer(usage.get("input_tokens", usage.get("prompt_tokens")))
        b["output"] = integer(usage.get("output_tokens", usage.get("completion_tokens")))
        b["cache_read"] = integer(usage.get("cached_input_tokens", details.get("cached_tokens", 0)))
        b["cache_write_5m"] = integer(
            usage.get("cache_write_input_tokens", details.get("cache_write_tokens", 0))
        )
        b["input_uncached"] = inp - b["cache_read"] - b["cache_write_5m"]
        if b["input_uncached"] < 0:
            raise ValueError("overlapping cache buckets exceed input")
    elif schema == "anthropic":
        b["input_uncached"] = integer(usage.get("input_tokens"))
        b["output"] = integer(usage.get("output_tokens"))
        b["cache_read"] = integer(usage.get("cache_read_input_tokens", 0))
        creation = integer(usage.get("cache_creation_input_tokens", 0))
        detail = object_field(usage.get("cache_creation"), "cache_creation")
        if "ephemeral_5m_input_tokens" in detail or "ephemeral_1h_input_tokens" in detail:
            b["cache_write_5m"] = integer(detail.get("ephemeral_5m_input_tokens", 0))
            b["cache_write_1h"] = integer(detail.get("ephemeral_1h_input_tokens", 0))
            if creation != b["cache_write_5m"] + b["cache_write_1h"]:
                raise ValueError("cache creation detail mismatch")
        else:
            b["cache_write_5m"] = creation
            if creation:
                flags.append("cache_ttl_assumed_5m")
    elif schema == "exclusive":
        for key in BUCKETS:
            b[key] = integer(usage.get(key, 0))
        if not any(k in usage for k in BUCKETS):
            raise ValueError("no usage buckets")
    else:
        raise ValueError("unsupported usage schema")
    return b, flags


@dataclasses.dataclass
class Call:
    agent: str
    session: str
    run: str
    request: str
    ts: str
    model: str
    buckets: dict
    source: str
    tier: str = "unrecorded"
    flags: list = dataclasses.field(default_factory=list)
    geo: str = "unrecorded"
    tool_usage: bool = False

    @property
    def tokens(self):
        return sum(self.buckets.values())

    @property
    def input(self):
        return self.tokens - self.buckets["output"]


class Ledger:
    def __init__(self):
        self.calls = {}
        self.starts = {}
        self.ends = {}
        self.parents = {}
        self.audit = {}
        self.uf = {}

    def count(self, key, n=1):
        self.audit[key] = self.audit.get(key, 0) + n

    def find(self, key):
        self.uf.setdefault(key, key)
        if self.uf[key] != key:
            self.uf[key] = self.find(self.uf[key])
        return self.uf[key]

    def join(self, a, b):
        self.uf[self.find(a)] = self.find(b)

    def add(self, c, replace=False):
        key = (c.agent, c.request)
        if key in self.calls:
            self.count("duplicate_or_stream_updates")
            if replace:
                old = self.calls[key]
                # One response may appear in multiple streaming records. The
                # merged usage is observable at the latest update, never earlier.
                if any(c.buckets[k] != old.buckets[k] for k in BUCKETS[:-1]):
                    self.count("stream_input_revision")
                if c.ts >= old.ts:
                    c.buckets["output"] = max(old.buckets["output"], c.buckets["output"])
                    c.run = old.run
                    c.session = old.session
                    self.calls[key] = c
            return
        self.calls[key] = c
        self.join(("run", c.agent, c.run), ("session", c.agent, c.session))

    def finalize(self):
        explicit_sessions = {
            c.session
            for c in self.calls.values()
            if c.agent == "codex" and c.source == "provider_response"
        }
        drop = [
            k
            for k, c in self.calls.items()
            if c.agent == "codex"
            and c.source == "cumulative_delta"
            and c.session in explicit_sessions
        ]
        for k in drop:
            del self.calls[k]
        self.count("suppressed_overlapping_cumulative_records", len(drop))
        for (agent, child), parent in self.parents.items():
            self.join(("session", agent, child), ("session", agent, parent))

    def rows(self, as_of):
        return sorted((c for c in self.calls.values() if c.ts <= as_of), key=lambda c: c.ts)


class Prices:
    def __init__(self, path=None):
        p = (
            pathlib.Path(path).expanduser()
            if path
            else pathlib.Path(__file__).with_name("prices.json")
        )
        self.data = json.loads(p.read_text())
        if not isinstance(self.data, dict):
            raise ValueError("price catalog must be an object")
        self.models = self.data["models"]
        if not isinstance(self.models, dict):
            raise ValueError("price models must be an object")
        if not isinstance(self.data.get("version"), str) or not isinstance(
            self.data.get("basis"), str
        ):
            raise ValueError("price version and basis must be strings")
        aliases = self.data.get("aliases", {})
        if not isinstance(aliases, dict) or any(
            not isinstance(k, str) or not isinstance(v, str) or v not in self.models
            for k, v in aliases.items()
        ):
            raise ValueError("price aliases must refer to catalog models")
        for model, spec in self.models.items():
            if not isinstance(spec, dict):
                raise ValueError("model price must be an object")
            if not isinstance(spec.get("usd_per_million"), dict):
                raise ValueError("usd_per_million must be an object")
            for b in BUCKETS:
                rate = spec["usd_per_million"].get(b)
                if rate is not None and (
                    isinstance(rate, bool)
                    or not isinstance(rate, (int, float))
                    or not math.isfinite(rate)
                    or rate < 0
                ):
                    raise ValueError("invalid price")
            for field in ("tier_multipliers", "geo_multipliers", "long_context_multipliers"):
                values = object_field(spec.get(field), field)
                if any(
                    isinstance(v, bool)
                    or not isinstance(v, (int, float))
                    or not math.isfinite(v)
                    or v < 0
                    for v in values.values()
                ):
                    raise ValueError("invalid " + field)
            for field in ("long_context_threshold", "unverified_context_above"):
                if field in spec and integer(spec[field]) == 0:
                    raise ValueError(field + " must be positive")

    def quote(self, call):
        spec = self.models.get(call.model)
        if spec is None:
            spec = self.models.get(self.data.get("aliases", {}).get(call.model))
        if spec is None:
            return None, "unknown_model_price"
        if spec.get("status") == "source_conflict":
            return None, "conflicting_official_price_sources"
        if spec.get("unverified_context_above") and call.input > spec["unverified_context_above"]:
            return None, "unverified_long_context_price"
        if "cache_ttl_assumed_5m" in call.flags:
            return None, "unknown_cache_ttl"
        if call.tool_usage:
            return None, "unpriced_tool_or_iteration_usage"
        tier = call.tier
        if tier not in ("unrecorded", "default", "standard", "auto") and tier not in spec.get(
            "tier_multipliers", {}
        ):
            return None, "unknown_service_tier"
        mult = spec.get("tier_multipliers", {}).get(tier, 1.0)
        if call.geo not in ("unrecorded", "global", "not_available"):
            if call.geo not in spec.get("geo_multipliers", {}):
                return None, "unknown_processing_geo"
            mult *= spec["geo_multipliers"][call.geo]
        rates = spec["usd_per_million"]
        cost = 0.0
        threshold = spec.get("long_context_threshold")
        for b, n in call.buckets.items():
            if n and rates.get(b) is None:
                return None, "unknown_bucket_price"
            scale = (
                spec.get("long_context_multipliers", {}).get(b, 1.0)
                if threshold and call.input > threshold
                else 1.0
            )
            cost += n * (rates.get(b) or 0) * mult * scale / 1e6
        return cost, "catalog_api_equivalent"


def quantile(xs, p):
    values = sorted(xs)
    if not values:
        return None
    a = (len(values) - 1) * p
    i = int(a)
    j = min(i + 1, len(values) - 1)
    return values[i] + (values[j] - values[i]) * (a - i)
