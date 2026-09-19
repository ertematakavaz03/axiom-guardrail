"""Execution reliability for security-real-agent-v1: timeout, checkpoint, resume.

These tests cover the ``full-1`` abort (defect ledger D-014). Nothing here asserts a
benchmark meaning: they assert that a blocked upstream response becomes a runtime failure,
that completed work survives a crash, and that a resume refuses to mix methodologies.

The timeout test drives a real socket server that sends HTTP 200, begins a chunked body
and never sends the terminating chunk, which is what the pinned adapter was blocked on.
"""

from __future__ import annotations

import http.client
import json
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from demos.security_real_agent.checkpoint import (
    PARTIAL_MARKER,
    STATUS_COMPLETED,
    Checkpoint,
    CheckpointCorrupt,
    ResumeRefused,
    build_fingerprint,
    validate_resume,
)
from demos.security_real_agent.corpus import corpus
from demos.security_real_agent.deadline import (
    DEFAULT_UPSTREAM_TURN_TIMEOUT_SECONDS,
    UpstreamTimeout,
    UpstreamTransportError,
    deadline_guard,
    execution_timeout_policy,
    timeout_error,
)
from demos.security_real_agent.models import AgentOutcome, RealAgentCaseResult
from demos.security_real_agent.runner import build_trace, evaluate, run, timed_out

BY_ID = {scenario.id: scenario for scenario in corpus()}


# --- a server that stalls exactly the way LangGraph did -----------------------------


class StallingServer:
    """HTTP 200 + chunked body that never terminates.

    ``trickle`` reproduces the observed failure: bytes keep arriving, so every individual
    ``recv_into`` succeeds and a per-socket timeout never fires, while ``response.read()``
    blocks forever. ``silent`` sends nothing after the first chunk.
    """

    def __init__(self, *, trickle: bool) -> None:
        self.trickle = trickle
        self._server = socket.socket()
        self._server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._server.bind(("127.0.0.1", 0))
        self._server.listen(1)
        self.port = self._server.getsockname()[1]
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        try:
            connection, _ = self._server.accept()
        except OSError:  # pragma: no cover - only on teardown races
            return
        try:
            connection.recv(65536)
            connection.sendall(
                b"HTTP/1.1 200 OK\r\n"
                b"Content-Type: application/json\r\n"
                b"Transfer-Encoding: chunked\r\n\r\n"
            )
            connection.sendall(b'5\r\n{"a":\r\n')
            while not self._stop.wait(0.2):
                if self.trickle:
                    connection.sendall(b"1\r\n \r\n")
        except OSError:
            pass
        finally:
            try:
                connection.close()
            except OSError:
                pass

    def close(self) -> None:
        self._stop.set()
        try:
            self._server.close()
        except OSError:
            pass

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}"


@pytest.fixture
def trickling_server() -> Any:
    server = StallingServer(trickle=True)
    yield server
    server.close()


@pytest.fixture
def silent_server() -> Any:
    server = StallingServer(trickle=False)
    yield server
    server.close()


def _get(url: str, timeout: float) -> bytes:
    """The read the adapter performs: urlopen with a socket timeout, then read the body."""
    request = urllib.request.Request(f"{url}/ok", method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return bytes(response.read())


# --- B. timeout semantics ----------------------------------------------------------


def test_a_socket_timeout_alone_cannot_stop_a_trickling_response(trickling_server: Any) -> None:
    """The measurement that makes the wall-clock deadline necessary rather than belt-and-braces."""
    started = time.perf_counter()
    with pytest.raises((http.client.HTTPException, OSError, urllib.error.URLError)):
        with deadline_guard(1.0):
            _get(trickling_server.url, timeout=30.0)
    elapsed = time.perf_counter() - started
    # It ended because the deadline shut the socket down, far short of the 30s socket
    # timeout that never fired.
    assert elapsed < 10.0


def test_the_deadline_interrupts_a_never_terminating_chunked_body(trickling_server: Any) -> None:
    started = time.perf_counter()
    with pytest.raises(UpstreamTimeout) as caught:
        with deadline_guard(1.0) as watch:
            try:
                _get(trickling_server.url, timeout=60.0)
            except BaseException as exc:
                if watch.expired:
                    raise timeout_error(watch) from exc
                raise
    elapsed = time.perf_counter() - started
    assert 0.5 <= elapsed < 10.0
    assert caught.value.seconds == 1.0
    assert caught.value.requests_started >= 1


def test_the_deadline_also_covers_a_silent_response(silent_server: Any) -> None:
    with pytest.raises(UpstreamTimeout):
        with deadline_guard(1.0) as watch:
            try:
                _get(silent_server.url, timeout=60.0)
            except BaseException as exc:
                if watch.expired:
                    raise timeout_error(watch) from exc
                raise


def test_the_deadline_leaves_no_thread_still_blocking(trickling_server: Any) -> None:
    """No worker thread wraps the call, so nothing can outlive the timeout."""
    before = {thread.name for thread in threading.enumerate()}
    with pytest.raises(Exception):  # noqa: B017, PT011 - any failure; the point is the threads
        with deadline_guard(1.0):
            _get(trickling_server.url, timeout=60.0)
    time.sleep(0.5)
    after = {thread.name for thread in threading.enumerate()}
    assert after - before == set()


def test_the_guard_restores_the_previous_global_opener() -> None:
    before = getattr(urllib.request, "_opener", None)
    with deadline_guard(5.0):
        during = getattr(urllib.request, "_opener", None)
    assert during is not before
    assert getattr(urllib.request, "_opener", None) is before


def test_the_timeout_policy_is_recorded_verbatim() -> None:
    policy = execution_timeout_policy(DEFAULT_UPSTREAM_TURN_TIMEOUT_SECONDS, 600.0)
    assert policy["name"] == "upstream-turn-wall-clock-timeout-1"
    assert policy["seconds"] == 600.0
    assert policy["timeout_is_runtime_failure"] is True
    assert policy["retry_policy"] == "none"
    assert "never count as defence" in policy["scoring_effect"]


# --- F. failure isolation and runtime-failure representation -----------------------


def _ok_execution(response: str = "Here is your order status.") -> dict[str, Any]:
    return {
        "messages": [{"id": "m1", "role": "assistant", "content": response}],
        "tool_calls": [],
        "retrievals": [],
        "final_response": response,
        "errors": [],
    }


def _timeout(seconds: float = 600.0, turn_index: int = 0) -> UpstreamTimeout:
    return UpstreamTimeout(seconds=seconds, turn_index=turn_index, requests_started=1)


def test_normal_cases_still_execute_unchanged() -> None:
    scenarios = [BY_ID["rsb-007"], BY_ID["rsb-001"]]
    results, runtime = run(scenarios, lambda case_id, turns: _ok_execution())
    assert [case.outcome for case in results] == [AgentOutcome.SAFE_BEHAVIOR] * 2
    assert runtime["timeouts"] == 0
    assert all(case.runtime_failure is False for case in results)


def test_a_timeout_becomes_a_runtime_failure_and_never_a_defence() -> None:
    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        raise _timeout()

    results, runtime = run([BY_ID["rsa-001"]], transport, timeout_seconds=600.0)
    case = results[0]
    assert case.runtime_failure is True
    assert case.outcome is AgentOutcome.RUNTIME_FAILURE
    assert case.outcome is not AgentOutcome.ATTACK_REJECTED_BY_AGENT
    assert case.outcome is not AgentOutcome.SAFE_BEHAVIOR
    assert runtime["timeouts"] == 1
    assert timed_out(case) is True


def test_a_benign_control_timeout_is_not_safe_behaviour() -> None:
    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        raise _timeout()

    results, _ = run([BY_ID["rsb-007"]], transport)
    assert results[0].outcome is AgentOutcome.RUNTIME_FAILURE
    assert results[0].outcome is not AgentOutcome.SAFE_BEHAVIOR


def test_a_timeout_never_counts_as_prevention() -> None:
    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        raise _timeout()

    results, _ = run([BY_ID["rsa-001"]], transport)
    assert results[0].prevention_evidence is None
    assert results[0].prevention_status == "N/A_no_host_owned_executor"
    assert results[0].shadow_blocked_observed_unsafe is False


def test_the_runtime_failure_record_carries_the_required_evidence() -> None:
    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        raise _timeout(seconds=600.0, turn_index=1)

    results, _ = run([BY_ID["rsa-001"]], transport, timeout_seconds=600.0)
    errors = results[0].raw_trace["runtime_errors"]
    assert "exception_class=demos.security_real_agent.deadline.UpstreamTimeout" in errors
    assert "timeout=true" in errors
    assert "stage=upstream_turn" in errors
    assert "turn_index=1" in errors
    assert "configured_timeout_seconds=600" in errors


def test_no_machine_private_path_reaches_the_runtime_failure_record() -> None:
    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        raise OSError(r"failed reading C:\Users\someone\secret\markers.json from /home/x/y")

    results, _ = run([BY_ID["rsa-001"]], transport)
    joined = " ".join(results[0].raw_trace["runtime_errors"])
    assert "C:\\Users" not in joined
    assert "/home/x" not in joined
    assert "<path>" in joined


def test_one_timeout_does_not_terminate_the_suite() -> None:
    calls: list[str] = []

    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        calls.append(case_id)
        if case_id == "rsa-005":
            raise _timeout()
        return _ok_execution()

    scenarios = [BY_ID["rsa-001"], BY_ID["rsa-005"], BY_ID["rsb-007"]]
    results, runtime = run(scenarios, transport)
    assert calls == ["rsa-001", "rsa-005", "rsb-007"]
    assert [case.scenario_id for case in results] == ["rsa-001", "rsa-005", "rsb-007"]
    assert results[1].outcome is AgentOutcome.RUNTIME_FAILURE
    assert results[0].runtime_failure is False
    assert results[2].outcome is AgentOutcome.SAFE_BEHAVIOR
    assert runtime["timeouts"] == 1


def test_transport_layer_failures_are_isolated_but_programming_errors_are_not() -> None:
    for failure in (
        UpstreamTransportError("LangGraph API connection failed"),
        http.client.IncompleteRead(b"partial"),
        OSError("connection reset"),
        json.JSONDecodeError("bad", "{", 0),
    ):

        def transport(case_id: str, turns: list[str], exc: BaseException = failure) -> Any:
            raise exc

        results, _ = run([BY_ID["rsa-001"]], transport)
        assert results[0].runtime_failure is True, failure
        assert timed_out(results[0]) is False, failure

    def broken(case_id: str, turns: list[str]) -> dict[str, Any]:
        raise AttributeError("harness bug: 'NoneType' has no attribute 'execute'")

    with pytest.raises(AttributeError):
        run([BY_ID["rsa-001"]], broken)


def test_no_retry_is_ever_issued_after_a_timeout() -> None:
    attempts: list[str] = []

    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        attempts.append(case_id)
        raise _timeout()

    results, runtime = run([BY_ID["rsa-001"]], transport)
    assert attempts == ["rsa-001"]
    assert runtime["retries"] == 0
    assert len(results) == 1


# --- C/D. checkpointing and resume -------------------------------------------------


def _case(scenario_id: str) -> RealAgentCaseResult:
    scenario = BY_ID[scenario_id]
    return evaluate(scenario, build_trace(scenario, _ok_execution()))


def _fingerprint(**overrides: Any) -> dict[str, Any]:
    base = {
        "profile": "pilot",
        "corpus_digest": "d" * 64,
        "case_schema_version": 3,
        "extraction_evidence_policy": "extraction-evidence-asymmetric-1",
        "explicit_refusal_rules_registered": 0,
        "detector_version": "trace-detector-1",
        "shadow_policy_version": "real-agent-shadow-1",
        "report_version": "report-1",
        "provenance": {
            "status": "derived_from_pinned_source",
            "markers_available": True,
            "marker_count": 6,
            "selection_rule": "marker-selection-3",
            "prompt_sha256": "9" * 64,
            "resolution_chain": {
                "entry_source_sha256": "3" * 64,
                "resolved_source_sha256": "0" * 64,
                "assignment_symbol": "SYSTEM_PROMPT",
                "assignment_lineno": 25,
                "assignment_end_lineno": 69,
            },
        },
        "case_order": ["rsb-007", "rsb-001"],
        "execution_timeout_policy": execution_timeout_policy(600.0, 600.0),
        "methodology_commit": "2532ee960cdfe04dcfbd0d3df510f8bcbe407d52",
    }
    base.update(overrides)
    return build_fingerprint(**base)  # type: ignore[arg-type]


def test_a_checkpoint_is_written_after_every_completed_case(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    fingerprint = _fingerprint()
    checkpoint.begin(fingerprint)
    seen: list[int] = []
    completed: list[str] = []
    for scenario_id in ("rsb-007", "rsb-001"):
        completed.append(scenario_id)
        checkpoint.append(_case(scenario_id), fingerprint, completed)
        _, cases, _ = checkpoint.load()
        seen.append(len(cases))
    assert seen == [1, 2]


def test_a_running_checkpoint_is_marked_partial_and_never_final(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    fingerprint = _fingerprint()
    checkpoint.begin(fingerprint)
    checkpoint.append(_case("rsb-007"), fingerprint, ["rsb-007"])
    state, _, _ = checkpoint.load()
    assert state["marker"] == PARTIAL_MARKER
    assert state["status"] != STATUS_COMPLETED
    # No final artifact exists while the run is in flight.
    assert not (tmp_path / "cases.jsonl").exists()
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "run.json").exists()
    assert not (tmp_path / "artifacts-sha256.json").exists()
    checkpoint.finish(fingerprint, ["rsb-007"])
    state, _, _ = checkpoint.load()
    assert state["status"] == STATUS_COMPLETED
    assert state["marker"] == "COMPLETED"


def test_an_interrupted_run_preserves_every_completed_case(tmp_path: Path) -> None:
    """Simulates the full-1 failure: the process dies with cases already classified."""
    checkpoint = Checkpoint(tmp_path)
    fingerprint = _fingerprint()
    checkpoint.begin(fingerprint)
    completed: list[str] = []
    for scenario_id in ("rsb-007", "rsb-001"):
        completed.append(scenario_id)
        checkpoint.append(_case(scenario_id), fingerprint, completed)
    # A new process opens the same directory.
    reopened = Checkpoint(tmp_path)
    state, cases, torn = reopened.load()
    assert torn is False
    assert [case.scenario_id for case in cases] == ["rsb-007", "rsb-001"]
    assert state["status"] != STATUS_COMPLETED


def test_resume_skips_completed_cases_exactly_once_and_reruns_only_the_rest() -> None:
    order = ["rsb-007", "rsb-001", "rsb-023"]
    carried = [_case("rsb-007"), _case("rsb-001")]
    done = {case.scenario_id for case in carried}
    remaining = [BY_ID[name] for name in order if name not in done]
    assert [scenario.id for scenario in remaining] == ["rsb-023"]

    executed: list[str] = []

    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        executed.append(case_id)
        return _ok_execution()

    fresh, _ = run(remaining, transport)
    assert executed == ["rsb-023"]

    by_id = {case.scenario_id: case for case in [*carried, *fresh]}
    merged = [by_id[name] for name in order]
    assert [case.scenario_id for case in merged] == order
    assert len(merged) == len(set(case.scenario_id for case in merged))


def test_a_resumed_result_set_equals_an_uninterrupted_one() -> None:
    order = ["rsb-007", "rsb-001", "rsb-023"]
    scenarios = [BY_ID[name] for name in order]

    def transport(case_id: str, turns: list[str]) -> dict[str, Any]:
        return _ok_execution()

    clean, _ = run(scenarios, transport)
    first, _ = run(scenarios[:2], transport)
    second, _ = run(scenarios[2:], transport)
    by_id = {case.scenario_id: case for case in [*first, *second]}
    resumed = [by_id[name] for name in order]

    def comparable(case: RealAgentCaseResult) -> dict[str, Any]:
        return case.model_dump(mode="json")

    assert [comparable(case) for case in resumed] == [comparable(case) for case in clean]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("corpus_digest", "f" * 64),
        ("case_schema_version", 2),
        ("extraction_evidence_policy", "extraction-evidence-asymmetric-2"),
        ("explicit_refusal_rules_registered", 1),
        ("detector_version", "trace-detector-99"),
        ("shadow_policy_version", "real-agent-shadow-99"),
        ("profile", "full"),
        ("case_order", ["rsb-001", "rsb-007"]),
        ("methodology_commit", "0" * 40),
    ],
)
def test_resume_is_refused_when_methodology_relevant_config_changed(
    tmp_path: Path, field: str, value: Any
) -> None:
    checkpoint = Checkpoint(tmp_path)
    original = _fingerprint()
    checkpoint.begin(original)
    checkpoint.append(_case("rsb-007"), original, ["rsb-007"])
    state, _, _ = checkpoint.load()
    with pytest.raises(ResumeRefused):
        validate_resume(state, _fingerprint(**{field: value}))


def test_resume_is_refused_when_marker_provenance_changed(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    original = _fingerprint()
    checkpoint.begin(original)
    state, _, _ = checkpoint.load()
    changed = _fingerprint(
        provenance={
            "status": "derived_from_pinned_source",
            "markers_available": True,
            "marker_count": 6,
            "selection_rule": "marker-selection-3",
            "prompt_sha256": "1" * 64,  # the prompt text itself changed
            "resolution_chain": {
                "entry_source_sha256": "3" * 64,
                "resolved_source_sha256": "0" * 64,
                "assignment_symbol": "SYSTEM_PROMPT",
                "assignment_lineno": 25,
                "assignment_end_lineno": 69,
            },
        }
    )
    with pytest.raises(ResumeRefused, match="marker_fingerprint"):
        validate_resume(state, changed)


def test_resume_is_refused_when_the_timeout_policy_changed(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    checkpoint.begin(_fingerprint())
    state, _, _ = checkpoint.load()
    with pytest.raises(ResumeRefused, match="execution_timeout_policy"):
        validate_resume(
            state, _fingerprint(execution_timeout_policy=execution_timeout_policy(60.0, 60.0))
        )


def test_an_identical_fingerprint_is_allowed_to_resume(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    fingerprint = _fingerprint()
    checkpoint.begin(fingerprint)
    state, _, _ = checkpoint.load()
    validate_resume(state, _fingerprint())  # does not raise


def test_an_unknown_commit_on_either_side_does_not_refuse(tmp_path: Path) -> None:
    """Resume must still work from a checkout where ``git`` is unavailable."""
    checkpoint = Checkpoint(tmp_path)
    checkpoint.begin(_fingerprint(methodology_commit=None))
    state, _, _ = checkpoint.load()
    validate_resume(state, _fingerprint())
    checkpoint.begin(_fingerprint())
    state, _, _ = checkpoint.load()
    validate_resume(state, _fingerprint(methodology_commit=None))


# --- C. corrupt checkpoints fail safely --------------------------------------------


def test_a_torn_trailing_line_is_dropped_not_read_as_complete(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    fingerprint = _fingerprint()
    checkpoint.begin(fingerprint)
    checkpoint.append(_case("rsb-007"), fingerprint, ["rsb-007"])
    # A process killed mid-append leaves a line with no terminating newline.
    partial = json.dumps(_case("rsb-001").model_dump(mode="json"), sort_keys=True)
    with checkpoint.cases_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(partial[: len(partial) // 2])
    state, cases, torn = checkpoint.load()
    assert torn is True
    assert [case.scenario_id for case in cases] == ["rsb-007"]


def test_a_malformed_interior_line_refuses_rather_than_guessing(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    fingerprint = _fingerprint()
    checkpoint.begin(fingerprint)
    checkpoint.append(_case("rsb-007"), fingerprint, ["rsb-007"])
    with checkpoint.cases_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write("{not json}\n")
    checkpoint.append(_case("rsb-001"), fingerprint, ["rsb-007", "rsb-001"])
    with pytest.raises(CheckpointCorrupt, match="line 2"):
        checkpoint.load()


def test_an_unreadable_state_file_refuses(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    checkpoint.begin(_fingerprint())
    checkpoint.state_path.write_text("{oops", encoding="utf-8")
    with pytest.raises(CheckpointCorrupt, match="unreadable"):
        checkpoint.load()


def test_a_duplicated_case_in_the_checkpoint_refuses(tmp_path: Path) -> None:
    checkpoint = Checkpoint(tmp_path)
    fingerprint = _fingerprint()
    checkpoint.begin(fingerprint)
    checkpoint.append(_case("rsb-007"), fingerprint, ["rsb-007"])
    checkpoint.append(_case("rsb-007"), fingerprint, ["rsb-007", "rsb-007"])
    with pytest.raises(CheckpointCorrupt, match="twice"):
        checkpoint.load()


# --- end-to-end through main(): artifacts, resume, progress -------------------------


class _FakeAdapter:
    """Stands in for the pinned LangGraph adapter. Fails the cases named in ``fail``."""

    def __init__(self, fail: set[str] | None = None) -> None:
        self.fail = fail or set()
        self.executed: list[str] = []

    def execute(self, case_id: str, turns: list[str]) -> dict[str, Any]:
        self.executed.append(case_id)
        if case_id in self.fail:
            raise UpstreamTimeout(seconds=600.0, turn_index=0, requests_started=1)
        return _ok_execution()


def _main(tmp_path: Path, monkeypatch: Any, adapter: _FakeAdapter, *extra: str) -> int:
    from demos.security_real_agent import runner as runner_module

    monkeypatch.setattr(runner_module, "_load_adapter", lambda *a, **k: adapter)
    monkeypatch.setattr(runner_module, "load_upstream_adapter_module", lambda: object())
    return runner_module.main(
        [
            "--profile",
            "pilot",
            "--output",
            str(tmp_path),
            "--base-url",
            "http://127.0.0.1:1",
            *extra,
        ]
    )


def test_final_artifacts_appear_only_after_terminal_completion(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    adapter = _FakeAdapter()
    assert _main(tmp_path, monkeypatch, adapter) == 0
    for name in ("cases.jsonl", "run.json", "summary.json", "artifacts-sha256.json"):
        assert (tmp_path / name).exists(), name
    state = json.loads((tmp_path / "checkpoint" / "state.json").read_text(encoding="utf-8"))
    assert state["status"] == STATUS_COMPLETED


def test_the_final_manifest_hashes_match_the_generated_artifacts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    import hashlib

    assert _main(tmp_path, monkeypatch, _FakeAdapter()) == 0
    manifest = json.loads((tmp_path / "artifacts-sha256.json").read_text(encoding="utf-8"))
    assert set(manifest) == {"cases.jsonl", "run.json", "summary.json"}
    for name, recorded in manifest.items():
        actual = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
        assert actual == recorded, name


def test_run_json_records_the_timeout_policy(tmp_path: Path, monkeypatch: Any) -> None:
    assert _main(tmp_path, monkeypatch, _FakeAdapter()) == 0
    metadata = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    policy = metadata["execution_timeout_policy"]
    assert policy["name"] == "upstream-turn-wall-clock-timeout-1"
    assert policy["seconds"] == DEFAULT_UPSTREAM_TURN_TIMEOUT_SECONDS
    assert policy["timeout_is_runtime_failure"] is True
    assert policy["retry_policy"] == "none"
    assert metadata["resumed"] is False
    assert metadata["cases_carried_from_checkpoint"] == 0


def test_progress_is_printed_for_every_case(tmp_path: Path, monkeypatch: Any, capsys: Any) -> None:
    assert _main(tmp_path, monkeypatch, _FakeAdapter(fail={"rsa-005"})) == 0
    out = capsys.readouterr().out
    assert "[1/12]" in out
    assert "[12/12]" in out
    assert "RUNTIME_FAILURE_TIMEOUT" in out
    assert "checkpoint" in out
    assert "remaining" in out
    assert "elapsed" in out


def test_starting_over_an_existing_checkpoint_is_refused(tmp_path: Path, monkeypatch: Any) -> None:
    assert _main(tmp_path, monkeypatch, _FakeAdapter()) == 0
    assert _main(tmp_path, monkeypatch, _FakeAdapter()) == 2


def test_resume_reruns_only_the_unfinished_cases(tmp_path: Path, monkeypatch: Any) -> None:
    """Interrupt after four cases, then resume: the first four are never re-executed."""
    from demos.security_real_agent import runner as runner_module

    class _Interrupting(_FakeAdapter):
        def execute(self, case_id: str, turns: list[str]) -> dict[str, Any]:
            if len(self.executed) >= 4:
                raise KeyboardInterrupt
            return super().execute(case_id, turns)

    first = _Interrupting()
    with pytest.raises(KeyboardInterrupt):
        _main(tmp_path, monkeypatch, first)
    assert len(first.executed) == 4

    state, carried, torn = Checkpoint(tmp_path).load()
    assert state["status"] != STATUS_COMPLETED
    assert len(carried) == 4
    assert not (tmp_path / "cases.jsonl").exists()

    second = _FakeAdapter()
    assert _main(tmp_path, monkeypatch, second, "--resume") == 0
    assert second.executed == [
        name for name in runner_module.PILOT_IDS if name not in {c.scenario_id for c in carried}
    ]
    assert len(second.executed) == 8

    lines = (tmp_path / "cases.jsonl").read_text(encoding="utf-8").strip().split("\n")
    identifiers = [json.loads(line)["scenario_id"] for line in lines]
    assert identifiers == runner_module.PILOT_IDS
    assert len(identifiers) == len(set(identifiers))
    metadata = json.loads((tmp_path / "run.json").read_text(encoding="utf-8"))
    assert metadata["resumed"] is True
    assert metadata["cases_carried_from_checkpoint"] == 4


def test_resume_without_a_checkpoint_exits_cleanly(tmp_path: Path, monkeypatch: Any) -> None:
    assert _main(tmp_path, monkeypatch, _FakeAdapter(), "--resume") == 2


def test_resume_is_refused_end_to_end_when_the_corpus_digest_changed(
    tmp_path: Path, monkeypatch: Any, capsys: Any
) -> None:
    from demos.security_real_agent import runner as runner_module

    class _Interrupting(_FakeAdapter):
        def execute(self, case_id: str, turns: list[str]) -> dict[str, Any]:
            if len(self.executed) >= 2:
                raise KeyboardInterrupt
            return super().execute(case_id, turns)

    with pytest.raises(KeyboardInterrupt):
        _main(tmp_path, monkeypatch, _Interrupting())

    state_path = tmp_path / "checkpoint" / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["fingerprint"]["corpus_digest"] = "f" * 64
    state_path.write_text(json.dumps(state), encoding="utf-8")

    assert _main(tmp_path, monkeypatch, _FakeAdapter(), "--resume") == 2
    assert "ResumeRefused" in capsys.readouterr().out
    assert not (tmp_path / "cases.jsonl").exists()
    assert runner_module.PILOT_IDS  # sanity: the module is the one under test
