"""Wall-clock deadline for a single upstream turn.

This module is **operational only**. It changes nothing about what the benchmark means:
no scenario, gold label, detector rule, threshold or outcome definition is involved. Its
sole job is to stop the harness blocking forever on a third-party HTTP response, and to
turn that block into the runtime-failure representation the frozen reporting semantics
already exclude from robustness denominators.

Why a socket timeout is not enough
----------------------------------
``benchmarks/external/langgraph-support-v1/adapter.py`` already passes
``urllib.request.urlopen(request, timeout=...)``. That timeout is applied with
``socket.settimeout``, so it bounds **each individual socket operation**, not the total
time spent in ``response.read()``. Measured against a local fake server:

* server sends headers then goes silent  -> ``TimeoutError`` at exactly the timeout. Good.
* server sends a chunked body and keeps
  trickling one byte every two seconds    -> ``response.read()`` blocks **indefinitely**;
  every ``recv_into`` returns in time, so the timer never fires.

The second case is the observed ``full-1`` failure: the traceback ends in
``http.client._read_chunked`` -> ``socket.recv_into``, with LangGraph health still 200.

Mechanism
---------
A ``threading.Timer`` in the *calling* thread's process shuts down the live socket when
the deadline passes. A blocked ``recv_into`` returns immediately on
``socket.shutdown(SHUT_RDWR)``, on Windows as well as POSIX, so:

* no ``SIGALRM`` (Unix-only, and unavailable off the main thread);
* no worker thread wrapping the call, therefore **no orphan thread** that could keep
  blocking after the timeout — the timer thread only calls ``shutdown`` and exits;
* the pinned adapter is **not modified**. ``urllib.request.urlopen`` with no explicit
  opener dispatches through the module-global opener, so installing one for the duration
  of a turn is enough to observe and own the connection.

The deadline is per upstream turn: it restarts whenever a new request begins, which the
connection wrapper observes directly from the HTTP protocol rather than inferring.
"""

from __future__ import annotations

import http.client
import socket
import threading
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

#: Default wall-clock budget for one upstream turn.
#:
#: A successful real run against a local 8B model takes minutes, so this is deliberately
#: generous: it exists to bound an indefinite block, not to police latency. The observed
#: hang persisted far beyond this.
DEFAULT_UPSTREAM_TURN_TIMEOUT_SECONDS = 600.0

#: Requests whose start restarts the per-turn deadline. A turn is one graph run.
TURN_REQUEST_MARKER = "/runs/wait"


class UpstreamTimeout(TimeoutError):
    """One upstream turn exceeded its wall-clock budget.

    Carries the evidence the runtime-failure record needs. It is never a defence, never a
    safe behaviour and never prevention: the runner maps it to ``runtime_failure`` and the
    frozen reporting semantics exclude runtime failures from robustness denominators.
    """

    def __init__(self, *, seconds: float, turn_index: int | None, requests_started: int) -> None:
        super().__init__(
            f"upstream turn exceeded the {seconds:g}s wall-clock budget while reading the "
            "response body"
        )
        self.seconds = seconds
        self.turn_index = turn_index
        self.requests_started = requests_started


class UpstreamTransportError(RuntimeError):
    """A transport-layer failure raised by the pinned adapter, normalised for the runner.

    The adapter defines its own ``ExternalAgentError`` inside a module loaded by file
    path, so the runner cannot name that class in an ``except`` clause without importing
    the upstream module at class-definition time. The transport closure re-raises through
    this type instead, which keeps the runner's exception boundary narrow: a genuine
    programming error in this harness still propagates and fails loudly.
    """


def execution_timeout_policy(seconds: float, socket_read_timeout: float) -> dict[str, Any]:
    """The pre-registered timeout policy, recorded verbatim in ``run.json``."""
    return {
        "name": "upstream-turn-wall-clock-timeout-1",
        "seconds": seconds,
        "socket_read_timeout_seconds": socket_read_timeout,
        "timeout_is_runtime_failure": True,
        "retry_policy": "none",
        "scoring_effect": (
            "runtime failures are excluded from robustness denominators according to the "
            "already-frozen reporting semantics and never count as defence"
        ),
        "mechanism": (
            "threading.Timer shuts down the live socket at the deadline; no SIGALRM and no "
            "worker thread, so a timed-out turn leaves no thread still blocking"
        ),
    }


class _Watch:
    """Shared state between the calling thread and the deadline timer."""

    def __init__(self, seconds: float) -> None:
        self.seconds = seconds
        self.lock = threading.Lock()
        self.sockets: list[socket.socket] = []
        self.timer: threading.Timer | None = None
        self.expired = False
        self.requests_started = 0
        self.turns_started = 0
        self.closed = False

    # -- socket registry ---------------------------------------------------------
    def register(self, sock: socket.socket) -> None:
        with self.lock:
            if self.closed:
                return
            self.sockets.append(sock)

    def observe_request(self, url: str) -> None:
        """A new request began. Restart the per-turn budget."""
        with self.lock:
            if self.closed:
                return
            self.requests_started += 1
            if TURN_REQUEST_MARKER in url:
                self.turns_started += 1
        self.restart()

    # -- timer -------------------------------------------------------------------
    def restart(self) -> None:
        with self.lock:
            if self.closed:
                return
            if self.timer is not None:
                self.timer.cancel()
            timer = threading.Timer(self.seconds, self._fire)
            timer.daemon = True
            self.timer = timer
        timer.start()

    def _fire(self) -> None:
        with self.lock:
            if self.closed:
                return
            self.expired = True
            targets = list(self.sockets)
        # Outside the lock: shutdown unblocks a recv already in progress in the caller.
        for sock in targets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self) -> None:
        with self.lock:
            self.closed = True
            timer, self.timer = self.timer, None
            self.sockets.clear()
        if timer is not None:
            timer.cancel()

    @property
    def turn_index(self) -> int | None:
        return self.turns_started - 1 if self.turns_started else None


def _connection_classes(watch: _Watch) -> tuple[type[Any], type[Any]]:
    class _HTTPConnection(http.client.HTTPConnection):
        def connect(self) -> None:
            super().connect()
            if self.sock is not None:
                watch.register(self.sock)

        def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> None:
            watch.observe_request(url)
            super().request(method, url, *args, **kwargs)

    class _HTTPSConnection(http.client.HTTPSConnection):
        def connect(self) -> None:
            super().connect()
            if self.sock is not None:
                watch.register(self.sock)

        def request(self, method: str, url: str, *args: Any, **kwargs: Any) -> None:
            watch.observe_request(url)
            super().request(method, url, *args, **kwargs)

    return _HTTPConnection, _HTTPSConnection


_INSTALL_LOCK = threading.Lock()


@contextmanager
def deadline_guard(seconds: float) -> Iterator[_Watch]:
    """Bound every HTTP turn made inside the block by ``seconds`` of wall clock.

    Yields the watch so the caller can read ``turn_index`` and ``requests_started`` when
    building a runtime-failure record. The previous global opener is restored on exit.
    """
    watch = _Watch(seconds)
    http_class, https_class = _connection_classes(watch)

    class _Handler(urllib.request.HTTPHandler):
        def http_open(self, req: Any) -> Any:
            return self.do_open(http_class, req)  # type: ignore[arg-type]

    class _SecureHandler(urllib.request.HTTPSHandler):
        def https_open(self, req: Any) -> Any:
            return self.do_open(https_class, req)  # type: ignore[arg-type]

    with _INSTALL_LOCK:
        previous = getattr(urllib.request, "_opener", None)  # the module-global hook
        urllib.request.install_opener(urllib.request.build_opener(_Handler, _SecureHandler))
        watch.restart()
        try:
            yield watch
        finally:
            watch.close()
            urllib.request._opener = previous  # type: ignore[attr-defined]


def timeout_error(watch: _Watch) -> UpstreamTimeout:
    return UpstreamTimeout(
        seconds=watch.seconds,
        turn_index=watch.turn_index,
        requests_started=watch.requests_started,
    )
