"""Per-client rate limiting at the origin.

Cloudflare's own rate limiting would be the natural place for this, but it needs
dashboard access this deployment does not have. Doing it here is not merely a
fallback: the origin is the thing with finite CPU, so the limit belongs where
the cost is.

Client identity comes from `CF-Connecting-IP`, which Cloudflare sets and
overwrites on every request. Since the tunnel is the only route to this process,
a client cannot forge it. `X-Forwarded-For` is deliberately ignored — it is
client-supplied and appending to it is trivial.
"""
from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Rule:
    """`limit` requests per `window` seconds."""

    limit: int
    window: float

    def __post_init__(self) -> None:
        if self.limit < 1 or self.window <= 0:
            raise ValueError("limit must be >= 1 and window > 0")


#: Both must pass: a burst allowance per minute, and an hourly ceiling.
#:
#: These were 6/minute and 60/hour, which the browser tutorial hit *within its
#: own page* -- it runs four checks, and re-running a cell after an edit is the
#: entire point of a notebook. A limit a visitor trips by following the
#: instructions is not protecting the machine, it is the product failing.
#:
#: Raising it is safe because the per-minute rule was never what bounded load.
#: `serve/app.py` holds a semaphore of `MAX_CONCURRENCY` (2) and returns 503
#: immediately when it is full rather than queueing, so at most two checks ever
#: run at once no matter what any single client is allowed to ask for. With a
#: 30-second cap per check, that ceiling is ~4 checks/minute of actual CPU
#: whether this number is 6 or 60. What the per-minute rule actually does is
#: stop one address monopolising those two slots, and 30 leaves room for a
#: person working while still refusing a script.
#:
#: The hourly ceiling is the one that matters for sustained abuse, so it stays
#: proportionally tighter: 300/hour is ten minutes of continuous work, not ten
#: hours of it.
CHECK_RULES = (Rule(limit=30, window=60), Rule(limit=300, window=3600))
#: The page and health endpoint are cheap, but not free, and an open redirect of
#: traffic through them would still cost bandwidth.
CHEAP_RULES = (Rule(limit=120, window=60),)


@dataclass
class RateLimiter:
    """Sliding-window counter, in memory.

    In memory is the right scope here: one process on one machine. A restart
    forgets history, which is acceptable — the worst case is one extra burst.
    """

    rules: tuple[Rule, ...]
    #: Stop tracking a client after the longest window has elapsed, so a scan
    #: across many addresses cannot grow this without bound.
    _hits: dict[str, deque[float]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def horizon(self) -> float:
        return max(rule.window for rule in self.rules)

    def _prune(self, now: float) -> None:
        cutoff = now - self.horizon
        for key in [k for k, hits in self._hits.items() if not hits or hits[-1] < cutoff]:
            del self._hits[key]

    def check(self, key: str, now: float | None = None) -> float | None:
        """Record a request. Returns None if allowed, else seconds to wait."""
        now = time.monotonic() if now is None else now
        with self._lock:
            if len(self._hits) > 10_000:
                self._prune(now)
            hits = self._hits.setdefault(key, deque())
            while hits and hits[0] < now - self.horizon:
                hits.popleft()

            for rule in self.rules:
                start = now - rule.window
                recent = sum(1 for stamp in hits if stamp >= start)
                if recent >= rule.limit:
                    # The oldest request inside this window decides when a slot
                    # frees up.
                    oldest = next(stamp for stamp in hits if stamp >= start)
                    return max(0.5, round(oldest + rule.window - now, 1))

            hits.append(now)
            return None

    def refund(self, key: str) -> None:
        """Give back the most recent request's slot.

        For a request the server then refuses to do any work for. `/check`
        rate-limits before it checks the concurrency gate -- deliberately, so
        that a flood is cheap to reject -- which means a caller turned away
        with "busy" had already paid for a check that never ran. Charging for
        that is punishing someone twice for the server being busy, and during a
        burst it is exactly the caller who most needs their next attempt to
        land.
        """
        with self._lock:
            hits = self._hits.get(key)
            if hits:
                hits.pop()

    def forget(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def client_key(cf_connecting_ip: str | None, peer: str | None) -> str:
    """Identify the client.

    Only `CF-Connecting-IP` is trusted, because only Cloudflare can set it on
    the path into this process. Falling back to the socket peer means a direct
    localhost caller is limited as itself rather than sharing one bucket with
    every proxied visitor.
    """
    if cf_connecting_ip:
        return cf_connecting_ip.strip()
    return peer or "unknown"
