"""Rate limiting at the origin: the machine with finite CPU is the right place."""
import pytest

from tlakit.serve.limiter import CHECK_RULES, RateLimiter, Rule, client_key


def test_requests_under_the_limit_are_allowed():
    rl = RateLimiter(rules=(Rule(limit=3, window=60),))
    assert [rl.check("a", now=t) for t in (0, 1, 2)] == [None, None, None]


def test_the_limit_is_enforced():
    rl = RateLimiter(rules=(Rule(limit=3, window=60),))
    for t in (0, 1, 2):
        rl.check("a", now=t)
    wait = rl.check("a", now=3)
    assert wait is not None and wait > 0


def test_the_window_slides():
    rl = RateLimiter(rules=(Rule(limit=2, window=10),))
    rl.check("a", now=0)
    rl.check("a", now=1)
    assert rl.check("a", now=5) is not None       # still inside the window
    assert rl.check("a", now=11) is None          # the first hit has aged out


def test_retry_after_points_at_when_a_slot_frees():
    rl = RateLimiter(rules=(Rule(limit=1, window=60),))
    rl.check("a", now=0)
    wait = rl.check("a", now=10)
    assert wait == pytest.approx(50, abs=1)


def test_clients_are_independent():
    rl = RateLimiter(rules=(Rule(limit=1, window=60),))
    assert rl.check("a", now=0) is None
    assert rl.check("b", now=0) is None
    assert rl.check("a", now=1) is not None


def test_every_rule_must_pass():
    """A small per-minute allowance plus an hourly ceiling."""
    rl = RateLimiter(rules=(Rule(limit=5, window=60), Rule(limit=6, window=3600)))
    now = 0.0
    allowed = 0
    for i in range(20):
        # Space them a minute apart so the per-minute rule never trips.
        if rl.check("a", now=i * 61.0) is None:
            allowed += 1
    assert allowed == 6, "the hourly ceiling should bind"


def test_a_rule_must_be_sane():
    for bad in ((0, 60), (1, 0), (-1, 60)):
        with pytest.raises(ValueError):
            Rule(limit=bad[0], window=bad[1])


def test_tracking_does_not_grow_without_bound():
    """A scan across many source addresses must not be a memory leak."""
    rl = RateLimiter(rules=(Rule(limit=1, window=10),))
    for i in range(10_050):
        rl.check(f"ip-{i}", now=0)
    # One request far in the future triggers a prune of everything stale.
    rl.check("later", now=10_000)
    assert len(rl._hits) < 10_050


def test_only_cf_connecting_ip_is_trusted():
    """X-Forwarded-For is client-supplied; appending to it is trivial."""
    assert client_key("203.0.113.9", "127.0.0.1") == "203.0.113.9"
    assert client_key(None, "127.0.0.1") == "127.0.0.1"
    assert client_key("  203.0.113.9  ", None) == "203.0.113.9"
    assert client_key(None, None) == "unknown"


def test_the_shipped_check_rules_are_shaped_for_a_person():
    """This used to assert `per_minute.limit <= 10`, on the reasoning that a
    model check costs seconds of shared CPU. The reasoning was right and the
    conclusion was wrong: the per-minute rule is not what pays that cost.

    `serve/app.py` admits at most `MAX_CONCURRENCY` checks at once and returns
    503 rather than queueing, so the CPU ceiling is the same whether this
    number is 6 or 60 -- while 6 was low enough that the published tutorial
    tripped it from inside its own page. What is still worth asserting is the
    shape: a burst allowance, a strictly larger hourly ceiling above it, and
    neither of them unbounded.

    The machine's own protection is pinned separately, in
    `test_serve.py::test_the_machine_is_protected_by_the_gate_not_by_this_limit`.
    """
    per_minute, per_hour = CHECK_RULES
    assert per_minute.window == 60
    assert per_hour.window == 3600
    assert per_minute.limit >= 12, "the browser tutorial alone needs headroom"
    assert per_minute.limit <= 60, "past a check a second this stops being a limit"
    assert per_hour.limit > per_minute.limit, "the ceiling must bind above the burst"
    assert per_hour.limit < per_minute.limit * 60, (
        "an hourly ceiling that a full hour at the per-minute rate cannot reach "
        "is not a ceiling"
    )
