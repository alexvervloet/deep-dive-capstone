"""The dozen lines around the model call: cache, budget, retries, logs.

The model call is one line. Production is everything that makes it cheap,
safe, observable, and reliable — adapted from ai-in-production-deep-dive/prod/
(cache.py, cost.py, reliability.py, observability.py).

One adaptation the server-oriented dive doesn't need: the answer cache is
**disk-backed**. askrepo is a CLI — one question per process — so an
in-memory cache would never hit across invocations. A local JSON file is the
offline equivalent of the dive's "back it with Redis so it survives a
restart": same get/set interface, persistent store.

Everything here is pure standard library, so the whole ops layer — and its
tests — run on the mock with no key. That's the v00 promise, kept to the end.
"""

import hashlib
import json
import os
import random
import sys
import time
import uuid
from contextlib import contextmanager

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_PATH = os.path.join(HERE, "index", "answer_cache.json")


# --- Cache: don't pay twice for the same answer ---------------------------


def cache_key(*parts):
    """A stable key over everything that shaped the answer.

    Change any input — the question, the model, the prompt-contract version,
    the retrieval mode/knobs — and the key changes, so a change never serves a
    stale answer. (Same discipline as the RAG index cache: the embedding model
    was part of its key.)
    """
    payload = "\x1f".join(str(p) for p in parts)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


class ResponseCache:
    """A TTL cache of answers, persisted to a JSON file so CLI runs share it.

    Real deployments use Redis; the interface is identical — get / set on a
    key, entries age out past the TTL.
    """

    def __init__(self, path=CACHE_PATH, ttl_s=86400.0):
        self.path = path
        self.ttl_s = ttl_s
        self.hits = 0
        self.misses = 0
        self._store = {}
        if path and os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self._store = json.load(f)
            except (json.JSONDecodeError, OSError):
                self._store = {}  # a corrupt cache is a miss, never a crash

    def get(self, key):
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        if time.time() - entry["stored_at"] > self.ttl_s:
            del self._store[key]  # expired: age it out, count a miss
            self.misses += 1
            return None
        self.hits += 1
        return entry["value"]

    def set(self, key, value):
        self._store[key] = {"value": value, "stored_at": time.time()}
        if self.path:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self._store, f)

    @property
    def hit_rate(self):
        total = self.hits + self.misses
        return self.hits / total if total else 0.0


# --- Budget: stop before you overspend ------------------------------------


class BudgetExceeded(RuntimeError):
    """Raised when a call would push spend past the configured ceiling."""


class Budget:
    """A running spend ceiling for one session. `check()` before a call so the
    app refuses instead of spending; `record()` after, to advance the meter.

    limit_usd <= 0 means unlimited (the default) — enforcement is opt-in.
    """

    def __init__(self, limit_usd=0.0):
        self.limit_usd = limit_usd
        self.spent_usd = 0.0
        self.calls = 0

    def check(self, estimated_usd=0.0):
        if self.limit_usd > 0 and self.spent_usd + estimated_usd > self.limit_usd:
            raise BudgetExceeded(
                f"budget ${self.limit_usd:.4f} would be exceeded "
                f"(spent ${self.spent_usd:.6f}, this call ~${estimated_usd:.6f})"
            )

    def record(self, usd):
        self.spent_usd += usd
        self.calls += 1

    @property
    def remaining_usd(self):
        return max(0.0, self.limit_usd - self.spent_usd) if self.limit_usd > 0 else float("inf")


# --- Reliability: survive a provider that flaps ---------------------------


class TransientError(RuntimeError):
    """A failure worth retrying (rate limit, timeout, 5xx, connection blip)."""


# Provider-SDK exceptions are matched by name so this module never imports the
# SDKs (keeping the mock path dependency-free). A 400 is your bug — not retried.
_TRANSIENT_NAMES = {
    "RateLimitError", "APITimeoutError", "APIConnectionError",
    "InternalServerError", "APIStatusError", "TransientError",
}


def is_transient(exc):
    if isinstance(exc, (TransientError, TimeoutError, ConnectionError)):
        return True
    name = type(exc).__name__
    if name in _TRANSIENT_NAMES:
        status = getattr(exc, "status_code", None)
        return status is None or status == 429 or status >= 500
    return False


def with_retry(fn, *, max_attempts=4, base_delay=0.5, sleep=time.sleep, on_retry=None):
    """Call `fn`, retrying transient failures with exponential backoff + jitter.

    Raises the last error if every attempt fails. `sleep` is injectable so
    tests run instantly. `on_retry(attempt, exc, delay)` hooks each retry into
    a trace.
    """
    last = None
    for attempt in range(1, max_attempts + 1):
        try:
            return fn()
        except Exception as exc:
            if not is_transient(exc) or attempt == max_attempts:
                raise
            last = exc
            delay = base_delay * (2 ** (attempt - 1))
            delay += random.uniform(0, delay * 0.5)
            if on_retry:
                on_retry(attempt, exc, delay)
            sleep(delay)
    raise last  # unreachable, but explicit


# --- Observability: one structured line per request ------------------------

_LEVELS = {"debug": 10, "info": 20, "warning": 30, "error": 40}


def _enabled_level():
    return os.getenv("ASKREPO_LOG", "").strip().lower()


def log(level, event, **fields):
    """Emit one JSON log line to stderr, iff ASKREPO_LOG selects this level.

    Off by default so the CLI's human output stays clean; `ASKREPO_LOG=info`
    turns on the structured trace that reconstructs a request after the fact.
    """
    minimum = _LEVELS.get(_enabled_level())
    if minimum is None or _LEVELS.get(level, 20) < minimum:
        return
    record = {"ts": round(time.time(), 3), "level": level, "event": event, **fields}
    print(json.dumps(record, default=str), file=sys.stderr)


class Trace:
    """One request's record: a trace_id, timed spans, and attributes."""

    def __init__(self, name):
        self.trace_id = uuid.uuid4().hex[:12]
        self.name = name
        self.attributes = {}
        self.spans = {}

    def set(self, **attrs):
        self.attributes.update(attrs)

    @contextmanager
    def span(self, name):
        start = time.perf_counter()
        try:
            yield
        finally:
            self.spans[name] = round((time.perf_counter() - start) * 1000, 1)

    def summary(self):
        return {"trace_id": self.trace_id, "request": self.name,
                **self.attributes, "spans": self.spans}


@contextmanager
def start_trace(name):
    trace = Trace(name)
    log("info", "request.start", trace_id=trace.trace_id, request=name)
    try:
        yield trace
    except Exception as exc:
        trace.set(error=type(exc).__name__, error_message=str(exc))
        log("error", "request.error", **trace.summary())
        raise
    else:
        log("info", "request.end", **trace.summary())
