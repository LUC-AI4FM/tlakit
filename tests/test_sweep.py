"""Issues #19, #20, #8: sweeps, bounded parallelism, and flattened frames."""
import shutil

import pytest

from tlakit.result import (
    Action, CheckResult, Outcome, RawOutput, Stats, Trace, flatten_state,
)
from tlakit.sweep import Run, SweepResult, grid_points, run_sweep, summarize

RAW = RawOutput(argv=[], exit_code=0, stdout="", stderr="")


def fake(outcome=Outcome.OK, distinct=3):
    return CheckResult(outcome, [], None, Stats(distinct=distinct, generated=4), RAW)


# --- grid ----------------------------------------------------------------


def test_grid_is_the_cartesian_product():
    points = grid_points({"a": [1, 2], "b": ["x", "y"]})
    assert len(points) == 4
    assert {"a": 1, "b": "x"} in points and {"a": 2, "b": "y"} in points


def test_grid_keeps_the_callers_key_order_and_varies_the_last_fastest():
    points = grid_points({"outer": [1, 2], "inner": [10, 20]})
    assert [p["inner"] for p in points] == [10, 20, 10, 20]


def test_an_empty_grid_is_a_single_run_with_no_constants():
    assert grid_points({}) == [{}]


# --- running -------------------------------------------------------------


def test_sweep_calls_check_once_per_point():
    seen = []

    def check(constants, **kw):
        seen.append(constants)
        return fake()

    sweep = run_sweep(check, {"N": [1, 2, 3]})
    assert len(sweep) == 3
    assert [s["N"] for s in seen] == [1, 2, 3]


def test_sweep_forwards_check_kwargs():
    got = {}

    def check(constants, **kw):
        got.update(kw)
        return fake()

    run_sweep(check, {"N": [1]}, invariants=["Inv"], timeout=5)
    assert got["invariants"] == ["Inv"] and got["timeout"] == 5


def test_parallel_results_keep_grid_order():
    def check(constants, **kw):
        # Reverse-ordered sleeps: finish order will not match grid order.
        import time

        time.sleep(0.05 * (4 - constants["N"]))
        return fake(distinct=constants["N"])

    sweep = run_sweep(check, {"N": [1, 2, 3]}, workers=3)
    assert [r.constants["N"] for r in sweep] == [1, 2, 3]
    assert [r.result.stats.distinct for r in sweep] == [1, 2, 3]


def test_workers_must_be_at_least_one():
    with pytest.raises(ValueError, match="at least 1"):
        run_sweep(lambda constants, **kw: fake(), {"N": [1]}, workers=0)


def test_a_worker_failure_reaps_running_children(monkeypatch):
    """A worker thread never sees KeyboardInterrupt, so nothing else would."""
    reaped = []
    monkeypatch.setattr(
        "tlakit.cli.terminate_all", lambda: reaped.append(True) or 0
    )

    def check(constants, **kw):
        if constants["N"] == 2:
            raise RuntimeError("boom")
        return fake()

    with pytest.raises(RuntimeError):
        run_sweep(check, {"N": [1, 2, 3]}, workers=3)
    assert reaped, "terminate_all was not called"


# --- results -------------------------------------------------------------


def make_sweep():
    return SweepResult(runs=[
        Run({"N": 2}, fake()),
        Run({"N": 3}, fake(Outcome.INVARIANT_VIOLATION)),
        Run({"N": 4}, fake(Outcome.DEADLOCK)),
    ])


def test_ok_is_false_when_any_point_fails():
    assert make_sweep().ok is False
    assert SweepResult(runs=[Run({"N": 1}, fake())]).ok is True


def test_failures_and_first_failure_are_in_grid_order():
    sweep = make_sweep()
    assert [r.constants["N"] for r in sweep.failures] == [3, 4]
    assert sweep.first_failure().constants["N"] == 3


def test_first_failure_is_none_when_everything_passes():
    assert SweepResult(runs=[Run({}, fake())]).first_failure() is None


def test_run_label_reads_as_the_configuration():
    assert Run({"N": 3, "M": "a"}, fake()).label == "M='a', N=3"


def test_summarize_has_one_line_per_point():
    text = summarize(make_sweep())
    assert len(text.splitlines()) == 3
    assert "invariant_violation" in text


# --- #8 flattening -------------------------------------------------------


def test_flatten_expands_nested_records_into_dotted_keys():
    flat = flatten_state({"progress": {"s1": {"term": 2}}, "n": 1})
    assert flat == {"progress.s1.term": 2, "n": 1}


def test_flatten_leaves_sequences_and_sets_whole():
    """A tuple is a value, not a namespace."""
    flat = flatten_state({"log": [1, 2, 3], "peers": {"a", "b"}})
    assert flat["log"] == [1, 2, 3]
    assert flat["peers"] == {"a", "b"}


def test_flatten_keeps_an_empty_record_as_a_value():
    assert flatten_state({"m": {}}) == {"m": {}}


def test_to_dataframe_flatten_produces_scalar_columns():
    pd = pytest.importorskip("pandas")
    trace = Trace(
        states=[{"p": {"s1": 0}}, {"p": {"s1": 1}}],
        actions=[Action("Next")],
    )
    plain = trace.to_dataframe()
    flat = trace.to_dataframe(flatten=True)
    assert isinstance(plain["p"][0], dict)
    assert "p.s1" in flat.columns
    assert list(flat["p.s1"]) == [0, 1]


# --- end to end ----------------------------------------------------------

SPEC = """---- MODULE Bounded ----
EXTENDS Naturals
CONSTANT Limit
VARIABLE x
Init == x = 0
Next == IF x < Limit THEN x' = x + 1 ELSE x' = x
Spec == Init /\\ [][Next]_x
Inv == x < 3
====
"""


@pytest.fixture
def ready():
    import tlakit
    from tlakit.jar import JarNotFound

    if shutil.which("java") is None:
        pytest.skip("java not on PATH")
    try:
        tlakit.CliRunner()
    except JarNotFound as exc:
        pytest.skip(str(exc))


@pytest.mark.java
def test_a_real_sweep_finds_the_smallest_failing_constant(ready):
    import tlakit

    spec = tlakit.Spec(source=SPEC, name="Bounded")
    sweep = spec.sweep({"Limit": [1, 2, 3, 4]}, invariants=["Inv"])
    assert len(sweep) == 4
    assert sweep.ok is False
    # Inv is x < 3, so Limit 1 and 2 pass and 3 is the smallest break.
    assert sweep.first_failure().constants == {"Limit": 3}
    assert [r.result.ok for r in sweep] == [True, True, False, False]


@pytest.mark.java
def test_a_real_sweep_runs_in_parallel_with_a_heap_cap(ready):
    import tlakit

    spec = tlakit.Spec(source=SPEC, name="Bounded")
    sweep = spec.sweep(
        {"Limit": [1, 2]}, invariants=["Inv"], workers=2, heap="512M"
    )
    assert [r.constants["Limit"] for r in sweep] == [1, 2]
    assert all("-Xmx512M" in r.result.raw.argv for r in sweep)


@pytest.mark.java
def test_sweep_to_dataframe_has_a_row_per_configuration(ready):
    pytest.importorskip("pandas")
    import tlakit

    spec = tlakit.Spec(source=SPEC, name="Bounded")
    df = spec.sweep({"Limit": [1, 2, 3]}, invariants=["Inv"]).to_dataframe()
    assert len(df) == 3
    assert list(df["Limit"]) == [1, 2, 3]
    assert set(df["outcome"]) == {"ok", "invariant_violation"}


# --- printing a sweep (#81) ----------------------------------------------


def sweep_of(*outcomes, trace=None):
    """A sweep over `Servers`, one point per outcome."""
    runs = []
    for index, outcome in enumerate(outcomes, start=3):
        result = CheckResult(
            outcome, [], trace if outcome is not Outcome.OK else None,
            Stats(distinct=8 * index, generated=10 * index, depth=index,
                  duration_ms=100 * index),
            RAW,
        )
        runs.append(Run(constants={"Servers": index}, result=result))
    return SweepResult(runs=runs)


def test_printing_a_sweep_gives_a_grid_not_one_line():
    """#61 gave CheckResult a __str__ for this reason; a sweep nests one per
    grid point, so the unreadable line is multiplied rather than merely long."""
    text = str(sweep_of(Outcome.OK, Outcome.INVARIANT_VIOLATION))

    assert "CheckResult(" not in text
    assert "RawOutput(" not in text
    assert "outcome=<Outcome." not in text
    lines = text.splitlines()
    assert len(lines) > 3
    for line in lines:
        assert len(line) <= 100, line


def test_the_grid_shows_a_column_per_constant_and_a_row_per_point():
    text = str(sweep_of(Outcome.OK, Outcome.INVARIANT_VIOLATION))

    header, *_ = [line for line in text.splitlines() if "outcome" in line]
    for column in ("Servers", "outcome", "states", "distinct", "depth", "ms"):
        assert column in header
    assert "OK" in text and "INVARIANT_VIOLATION" in text
    assert "24" in text  # distinct at Servers=3
    assert "32" in text  # distinct at Servers=4


def test_the_summary_counts_the_configurations_and_the_failures():
    text = str(sweep_of(Outcome.OK, Outcome.INVARIANT_VIOLATION, Outcome.OK))
    assert "3 configurations" in text
    assert "1 failed" in text


def test_the_summary_says_where_it_broke():
    """`first_failure()` is what the README leads with; printing the sweep
    should make the same point without a second call."""
    text = str(sweep_of(Outcome.OK, Outcome.DEADLOCK))
    assert "first_failure()" in text
    assert "Servers=4" in text


def test_a_clean_sweep_says_so_and_names_no_failure():
    text = str(sweep_of(Outcome.OK, Outcome.OK))
    assert "2 configurations" in text
    assert "failed" not in text
    assert "first_failure()" not in text


def test_an_empty_sweep_prints_something_rather_than_nothing():
    assert "no configurations" in str(SweepResult())


def test_the_counterexamples_are_not_inlined():
    """One trace per failing point is the wall of text this removes. The
    summary points at `sweep.first_failure().result`, which prints well."""
    trace = Trace(
        states=[flatten_state({"x": 0}), flatten_state({"x": 1})],
        actions=[Action(name="Increment")],
    )
    text = str(sweep_of(Outcome.OK, Outcome.INVARIANT_VIOLATION, trace=trace))

    assert "Increment" not in text
    assert "2" in text.splitlines()[-1] or "trace" in text  # the length, not the trace


def test_a_long_grid_is_truncated_with_a_count_of_what_was_omitted():
    """`render.trace_text` already does this for states; same pattern."""
    sweep = sweep_of(*([Outcome.OK] * 40))
    text = str(sweep)

    assert len(text.splitlines()) < 40
    assert "omitted" in text
    assert "40 configurations" in text


def test_the_rows_are_what_to_dataframe_builds():
    """Two column sets that drift is the failure this avoids."""
    pd = pytest.importorskip("pandas")
    sweep = sweep_of(Outcome.OK, Outcome.INVARIANT_VIOLATION)

    frame = sweep.to_dataframe()

    assert list(frame.columns) == list(sweep.rows()[0])
    assert frame.to_dict("records") == sweep.rows()


def test_a_notebook_gets_a_table_and_needs_no_pandas(monkeypatch):
    """`to_dataframe()` was the only readable view, and pandas is not a
    dependency."""
    import builtins

    real_import = builtins.__import__

    def no_pandas(name, *args, **kwargs):
        if name == "pandas":
            raise ImportError("pandas is not installed")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_pandas)
    sweep = sweep_of(Outcome.OK, Outcome.INVARIANT_VIOLATION)

    html = sweep._repr_html_()

    assert "<table" in html
    assert "INVARIANT_VIOLATION" in html
    assert "Servers" in html
    assert str(sweep)  # and the text view is pandas-free too


def test_repr_is_left_alone():
    """`repr` stays the faithful dataclass form -- what a debugger and a test
    failure want. Same reasoning as #61."""
    sweep = sweep_of(Outcome.OK)
    assert repr(sweep).startswith("SweepResult(runs=[Run(")
    assert "CheckResult(" in repr(sweep)
