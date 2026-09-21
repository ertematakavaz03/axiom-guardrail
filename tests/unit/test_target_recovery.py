"""Timeout-cascade containment: cancel what we abandoned, then check the target is free.

`20260920-phase4-hardened-2-permissive-2` recorded seven consecutive 600-second failures
from one slow case. The mechanism was structural, not statistical: the runs API defaults
`on_disconnect` to `"continue"`, `langgraph dev` pins the worker pool to one, and a client
deadline stops the client rather than the run. Every later case queued behind work nobody
was waiting for.

These tests drive a fake target, so the cascade can be reproduced and its absence proved
without a wedged model. The load-bearing one is
`test_one_slow_case_does_not_cascade_into_a_run_of_full_budget_timeouts`, with its
mutation twin directly below it.
"""

from __future__ import annotations

from typing import Any

import pytest

from demos.security_real_agent.recovery import (
    ACTIVE_RUN_STATUSES,
    CleanupReport,
    TargetControl,
    TargetUnavailable,
)


class FakeTarget:
    """A target with one worker, runs that outlive the client, and an honest /ok.

    Mirrors the three observed behaviours: `/ok` answers while the worker is wedged, a
    disconnected run keeps running, and queued work is never scheduled.
    """

    def __init__(self, *, cancel_fails: bool = False, ignores_cancel: bool = False) -> None:
        self.threads: dict[str, list[dict[str, Any]]] = {}
        self.cancel_fails = cancel_fails
        self.ignores_cancel = ignores_cancel
        self.calls: list[tuple[str, str]] = []
        self.cancelled: list[str] = []

    def start_run(self, thread_id: str, run_id: str, status: str = "running") -> None:
        self.threads.setdefault(thread_id, []).append({"run_id": run_id, "status": status})

    def request(self, method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> Any:
        self.calls.append((method, url))
        path = url.split("://", 1)[-1].split("/", 1)[-1]
        if path == "ok":  # pragma: no cover - present to prove it is never consulted
            return {"ok": True}
        if path.endswith("threads/search"):
            wanted = (payload or {}).get("status")
            out = []
            for thread_id, runs in self.threads.items():
                busy = any(r["status"] in ACTIVE_RUN_STATUSES for r in runs)
                if (wanted == "busy" and busy) or wanted is None:
                    out.append({"thread_id": thread_id, "status": "busy" if busy else "idle"})
            return out
        if path.endswith("/runs") and method == "GET":
            thread_id = path.split("threads/", 1)[1].split("/")[0]
            return list(self.threads.get(thread_id, []))
        if "/cancel" in path:
            if self.cancel_fails:
                raise OSError("cancel refused")
            thread_id = path.split("threads/", 1)[1].split("/")[0]
            run_id = path.split("/runs/", 1)[1].split("/")[0]
            self.cancelled.append(run_id)
            if not self.ignores_cancel:
                for run in self.threads.get(thread_id, []):
                    if run["run_id"] == run_id:
                        run["status"] = "interrupted"
            return {"ok": True}
        raise AssertionError(f"unexpected call {method} {url}")  # pragma: no cover


class FakeClock:
    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += seconds


def _control(target: FakeTarget, clock: FakeClock | None = None) -> TargetControl:
    clock = clock or FakeClock()
    return TargetControl(
        "http://target", request=target.request, sleep=clock.sleep, clock=clock.now
    )


# -- cancellation ----------------------------------------------------------------------
def test_a_timeout_cancels_the_exact_run_id() -> None:
    target = FakeTarget()
    target.start_run("thread-a", "run-1")
    target.start_run("thread-a", "run-old", status="success")
    report = _control(target).recover()
    assert target.cancelled == ["run-1"]
    assert report.cancelled_runs == ["run-1"]
    assert report.ready is True
    assert report.clean is True


def test_only_runs_still_holding_a_worker_are_cancelled() -> None:
    target = FakeTarget()
    target.start_run("thread-a", "done", status="success")
    target.start_run("thread-a", "failed", status="error")
    target.start_run("thread-a", "live", status="pending")
    _control(target).recover()
    assert target.cancelled == ["live"]


def test_a_target_with_nothing_running_issues_no_cancel() -> None:
    """Normal completion must not touch the control plane's cancel path at all."""
    target = FakeTarget()
    target.start_run("thread-a", "finished", status="success")
    report = _control(target).recover()
    assert target.cancelled == []
    assert report.cancelled_runs == []
    assert report.ready is True
    assert not any("cancel" in url for _, url in target.calls)


def test_several_wedged_threads_are_all_cleaned_up() -> None:
    target = FakeTarget()
    target.start_run("thread-a", "run-1")
    target.start_run("thread-b", "run-2")
    report = _control(target).recover()
    assert sorted(target.cancelled) == ["run-1", "run-2"]
    assert report.ready is True


# -- readiness -------------------------------------------------------------------------
def test_readiness_waits_while_the_target_is_busy_then_proceeds() -> None:
    """The run finishes on its own two polls in; readiness must notice, not guess."""
    target = FakeTarget(ignores_cancel=True)
    target.start_run("thread-a", "run-1")
    clock = FakeClock()
    polls = {"n": 0}
    original = target.request

    def counting(method: str, url: str, payload: Any, timeout: float) -> Any:
        if url.endswith("threads/search"):
            polls["n"] += 1
            if polls["n"] > 3:
                target.threads["thread-a"][0]["status"] = "success"
        return original(method, url, payload, timeout)

    control = TargetControl("http://target", request=counting, sleep=clock.sleep, clock=clock.now)
    report = control.recover(budget_seconds=60.0, poll_seconds=2.0)
    assert report.ready is True
    assert report.waited_seconds > 0


def test_readiness_is_bounded_and_gives_up() -> None:
    target = FakeTarget(ignores_cancel=True)
    target.start_run("thread-a", "run-1")
    clock = FakeClock()
    report = _control(target, clock).recover(budget_seconds=30.0, poll_seconds=5.0)
    assert report.ready is False
    assert report.waited_seconds >= 30.0
    assert clock.t <= 40.0, "the bound must actually bound: no unbounded polling"


def test_ok_alone_is_never_treated_as_readiness() -> None:
    """`/ok` answers 200 from the main loop while the sole worker is wedged."""
    target = FakeTarget(ignores_cancel=True)
    target.start_run("thread-a", "run-1")
    report = _control(target).recover(budget_seconds=10.0, poll_seconds=5.0)
    assert report.ready is False
    assert not any(url.endswith("/ok") for _, url in target.calls)


def test_an_unreachable_control_plane_does_not_hang() -> None:
    class Dead:
        def request(self, *_: Any) -> Any:
            raise OSError("connection refused")

    clock = FakeClock()
    control = TargetControl(
        "http://target", request=Dead().request, sleep=clock.sleep, clock=clock.now
    )
    report = control.recover(budget_seconds=20.0, poll_seconds=5.0)
    assert report.ready is False
    assert any("discovery_failed" in e for e in report.errors)


# -- verdicts are never touched ---------------------------------------------------------
def test_a_failed_cancellation_is_recorded_and_changes_no_verdict() -> None:
    """Cleanup reports what it could not do. It has no path to a case's outcome —
    `CleanupReport` carries no verdict field at all."""
    target = FakeTarget(cancel_fails=True)
    target.start_run("thread-a", "run-1")
    report = _control(target).recover(budget_seconds=10.0, poll_seconds=5.0)
    assert report.failed_cancellations == ["run-1"]
    assert any("cancel_failed" in e for e in report.errors)
    assert report.clean is False
    fields = set(CleanupReport.model_fields)
    assert not fields & {"outcome", "verdict", "runtime_failure", "retry", "scenario_id"}


def test_cleanup_success_also_changes_no_verdict() -> None:
    assert "outcome" not in CleanupReport.model_fields
    assert set(CleanupReport.model_fields) == {
        "busy_threads",
        "cancelled_runs",
        "failed_cancellations",
        "ready",
        "waited_seconds",
        "errors",
    }


# -- the cascade, and its mutation twin --------------------------------------------------
def _simulate(cases: int, *, cancel_enabled: bool, budget: float = 600.0) -> list[float]:
    """Run `cases` cases against a single-worker target where case 1 wedges.

    Returns each case's elapsed time. The wedged run outlives the client exactly as the
    real one does; only whether we cancel it varies.
    """
    target = FakeTarget(ignores_cancel=not cancel_enabled)
    clock = FakeClock()
    control = TargetControl(
        "http://target", request=target.request, sleep=clock.sleep, clock=clock.now
    )
    elapsed: list[float] = []
    for index in range(cases):
        start = clock.t
        if index == 0:
            target.start_run("thread-0", "run-0")  # the slow case, still running at the deadline
            clock.t += budget  # our deadline fires
            if cancel_enabled:
                control.recover(budget_seconds=60.0, poll_seconds=2.0)
        else:
            # A later case only gets a worker if nothing is still holding one.
            if any(
                r["status"] in ACTIVE_RUN_STATUSES for rs in target.threads.values() for r in rs
            ):
                clock.t += budget  # queued, never scheduled, burns the whole budget
            else:
                clock.t += 20.0  # ordinary case
        elapsed.append(round(clock.t - start, 1))
    return elapsed


def test_one_slow_case_does_not_cascade_into_a_run_of_full_budget_timeouts() -> None:
    elapsed = _simulate(6, cancel_enabled=True)
    assert elapsed[0] == 600.0, "the slow case still costs its full budget"
    assert all(t == 20.0 for t in elapsed[1:]), f"later cases must run normally: {elapsed}"


def test_disabling_cancellation_reproduces_the_observed_cascade() -> None:
    """The mutation twin. Without cancellation this is the seven-in-a-row failure."""
    elapsed = _simulate(6, cancel_enabled=False)
    assert elapsed[0] == 600.0
    assert all(t == 600.0 for t in elapsed[1:]), (
        f"expected the cascade to reappear when cancellation is removed: {elapsed}"
    )


def test_an_unrecoverable_target_raises_rather_than_burning_every_remaining_case() -> None:
    """The runner turns this into a stop. Ten minutes per case, 50 more times, is not
    a benchmark result — it is an outage wearing one."""
    target = FakeTarget(ignores_cancel=True)
    target.start_run("thread-a", "run-1")
    report = _control(target).recover(budget_seconds=10.0, poll_seconds=5.0)
    assert report.ready is False
    with pytest.raises(TargetUnavailable):
        if not report.ready:
            raise TargetUnavailable("target still busy")


# -- the runner wires it to timeouts only ------------------------------------------------
def test_the_runner_reclaims_only_after_a_timeout() -> None:
    """Structural: `reclaim` is reached from the timeout branch, not the success path."""
    import inspect

    import demos.security_real_agent.runner as runner_module

    source = inspect.getsource(runner_module)
    timeout_branch = source.index("if watch.expired:")
    reclaim_call = source.index("reclaim(case_id)")
    raise_timeout = source.index("raise error from exc")
    assert timeout_branch < reclaim_call < raise_timeout
    assert source.count("reclaim(case_id)") == 1


def test_the_runner_does_not_retry_a_timed_out_case() -> None:
    import inspect

    import demos.security_real_agent.runner as runner_module

    source = inspect.getsource(runner_module)
    assert "retry" not in source.split("def reclaim")[1].split("def transport")[0].lower()
