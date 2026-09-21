"""Target cleanup and readiness between cases.

A client-side deadline stops *us* waiting. It does not stop the target working. The
LangGraph runs API defaults ``on_disconnect`` to ``"continue"``, and ``langgraph dev``
pins ``N_JOBS_PER_WORKER`` to 1, so a single run that outlives our budget keeps the only
worker slot and every later case queues behind it, never gets scheduled, and burns its
whole budget waiting. That is how one slow case turned into seven consecutive
600-second failures in ``20260920-phase4-hardened-2-permissive-2``.

This module makes the runner clean up after itself:

1. cancel the work it abandoned, by id, through the target's own API;
2. confirm the target is actually free before handing it the next case;
3. fail loudly and stop if it is not, instead of spending ten minutes per case
   discovering the same thing 50 more times.

What it deliberately does **not** do:

* It never changes a verdict. A case that timed out stays a runtime failure whether or
  not cleanup succeeds — a crash is not a defence, and tidying up afterwards does not
  make it one.
* It never retries a case.
* It knows nothing about scenarios, families, prompts or expected outcomes. It asks the
  target which threads are busy and cancels those; the answer would be identical for any
  workload.

Raising worker concurrency would widen the queue but not stop the leak, so it is not
relied on here. Cancellation is the fix; concurrency is at most defence in depth.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from pydantic import Field

from services.security.models import StrictModel

#: How long to wait for the target to become free after cleanup, before giving up. Bounded
#: on purpose: an unbounded wait is the same outage as an unbounded timeout, just quieter.
DEFAULT_READINESS_BUDGET_SECONDS = 180.0
#: Gap between readiness polls.
DEFAULT_POLL_SECONDS = 2.0
#: Per-request timeout for the control-plane calls below. These are cheap metadata calls;
#: if one of them blocks, the target is unhealthy in a way cleanup cannot fix.
DEFAULT_CONTROL_TIMEOUT_SECONDS = 15.0

#: Run states that still hold a worker slot.
ACTIVE_RUN_STATUSES = frozenset({"pending", "running"})
#: Thread states that mean work is still in flight.
BUSY_THREAD_STATUSES = frozenset({"busy"})

#: Recorded in the run artifact so an operator can see what cleanup did.
RECOVERY_POLICY = "upstream-run-cancellation-1"


class TargetUnavailable(RuntimeError):
    """The target did not become free within the readiness budget.

    Raised so the runner stops. The alternative — carrying on — produces a long tail of
    identical full-budget timeouts that look like agent behaviour and are not.
    """


class CleanupReport(StrictModel):
    """What cleanup found and did after one abandoned turn."""

    busy_threads: list[str] = Field(default_factory=list)
    cancelled_runs: list[str] = Field(default_factory=list)
    failed_cancellations: list[str] = Field(default_factory=list)
    ready: bool = False
    waited_seconds: float = 0.0
    errors: list[str] = Field(default_factory=list)

    @property
    def clean(self) -> bool:
        return self.ready and not self.failed_cancellations and not self.errors


def _http(method: str, url: str, payload: dict[str, Any] | None, timeout: float) -> Any:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(  # noqa: S310 - fixed scheme, operator-supplied host
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        raw = response.read()
    return json.loads(raw.decode("utf-8")) if raw else None


class TargetControl:
    """Control-plane client for the agent target: what is busy, and stop it.

    Transport is injected so the whole recovery path is testable against a fake target,
    which is the only way to prove the cascade is gone without a wedged model to hand.
    """

    def __init__(
        self,
        base_url: str,
        *,
        request: Callable[[str, str, dict[str, Any] | None, float], Any] = _http,
        control_timeout: float = DEFAULT_CONTROL_TIMEOUT_SECONDS,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request = request
        self.control_timeout = control_timeout
        self.sleep = sleep
        self.clock = clock

    # -- observation ---------------------------------------------------------------
    def busy_threads(self) -> list[str]:
        """Thread ids the target reports as still working.

        ``/ok`` is not used and is not sufficient: it is a plain route on the main event
        loop, while runs execute on separate worker threads, so it answers 200 while the
        only worker is wedged. Liveness is not readiness — the same distinction the API's
        own ``/livez`` and ``/readyz`` draw.
        """
        found: list[str] = []
        for status in sorted(BUSY_THREAD_STATUSES):
            body = self.request(
                "POST",
                f"{self.base_url}/threads/search",
                {"status": status, "limit": 100},
                self.control_timeout,
            )
            for thread in body or []:
                thread_id = thread.get("thread_id") if isinstance(thread, dict) else None
                if thread_id:
                    found.append(str(thread_id))
        return sorted(set(found))

    def active_runs(self, thread_id: str) -> list[str]:
        body = self.request(
            "GET", f"{self.base_url}/threads/{thread_id}/runs", None, self.control_timeout
        )
        return [
            str(run["run_id"])
            for run in (body or [])
            if isinstance(run, dict)
            and run.get("run_id")
            and str(run.get("status")) in ACTIVE_RUN_STATUSES
        ]

    # -- action --------------------------------------------------------------------
    def cancel(self, thread_id: str, run_id: str) -> None:
        """Cancel one run by its exact id. ``action=interrupt`` stops it where it is."""
        self.request(
            "POST",
            f"{self.base_url}/threads/{thread_id}/runs/{run_id}/cancel?action=interrupt&wait=true",
            None,
            self.control_timeout,
        )

    def recover(
        self,
        *,
        budget_seconds: float = DEFAULT_READINESS_BUDGET_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
    ) -> CleanupReport:
        """Cancel whatever is still running, then wait — bounded — until nothing is.

        Cancellation failures are recorded rather than raised: the readiness check below
        is the thing that actually decides whether it is safe to continue, and a cancel
        that returned an error on a run that then finished by itself is not a problem.
        """
        report = CleanupReport()
        started = self.clock()

        try:
            threads = self.busy_threads()
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            report.errors.append(f"discovery_failed:{type(exc).__name__}:{exc}")
            threads = []
        report.busy_threads = threads

        for thread_id in threads:
            try:
                runs = self.active_runs(thread_id)
            except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
                report.errors.append(f"list_runs_failed:{thread_id}:{type(exc).__name__}")
                continue
            for run_id in runs:
                try:
                    self.cancel(thread_id, run_id)
                    report.cancelled_runs.append(run_id)
                except (urllib.error.URLError, OSError, ValueError) as exc:
                    report.failed_cancellations.append(run_id)
                    report.errors.append(f"cancel_failed:{run_id}:{type(exc).__name__}")

        report.ready = self.await_ready(
            budget_seconds=budget_seconds, poll_seconds=poll_seconds, started=started
        )
        report.waited_seconds = round(self.clock() - started, 3)
        return report

    def await_ready(
        self,
        *,
        budget_seconds: float = DEFAULT_READINESS_BUDGET_SECONDS,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        started: float | None = None,
    ) -> bool:
        """Poll until no thread is busy, or the budget runs out. Never blocks forever."""
        begin = self.clock() if started is None else started
        while True:
            try:
                if not self.busy_threads():
                    return True
            except (urllib.error.URLError, OSError, ValueError, KeyError):
                # An unreachable control plane is not readiness. Keep polling inside the
                # budget: a target mid-restart recovers, and a dead one hits the bound.
                pass
            if self.clock() - begin >= budget_seconds:
                return False
            self.sleep(poll_seconds)
