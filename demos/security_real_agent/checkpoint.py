"""Durable incremental checkpointing and resume for security-real-agent-v1.

Execution reliability only. Nothing here reads a gold label, a detector output or an
outcome: it persists whatever the classifier produced and refuses to resume across any
methodology change.

The ``full-1`` attempt lost every completed case because ``cases.jsonl`` was written once
at the end. A checkpoint is written after each classified case instead, and the final
artifacts are produced only when the run reaches its terminal state, so a partial
checkpoint can never be mistaken for a benchmark result.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from demos.security_real_agent import BENCHMARK_ID
from demos.security_real_agent.models import RealAgentCaseResult

CHECKPOINT_DIRNAME = "checkpoint"
CASES_FILENAME = "completed-cases.jsonl"
STATE_FILENAME = "state.json"
CHECKPOINT_SCHEMA_VERSION = 1

#: Written into every checkpoint file. A reader that finds this must not treat the
#: directory as a benchmark result, whatever else it contains.
PARTIAL_MARKER = "PARTIAL_IN_PROGRESS_NOT_A_BENCHMARK_RESULT"

#: Terminal states. Final artifacts are produced only from ``completed``.
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"

#: Keys of the fingerprint that must match exactly for a resume to be allowed. Each one
#: would change what a number means if it drifted mid-run.
RESUME_CRITICAL_KEYS = (
    "benchmark_id",
    "profile",
    "corpus_digest",
    "case_schema_version",
    "extraction_evidence_policy",
    "explicit_refusal_rules_registered",
    "detector_version",
    "shadow_policy_version",
    "report_version",
    "marker_fingerprint",
    "case_order",
    "execution_timeout_policy",
)


class ResumeRefused(RuntimeError):
    """A checkpoint exists but cannot be resumed without changing what the run means."""


class CheckpointCorrupt(RuntimeError):
    """The checkpoint cannot be read with confidence. Never silently repaired."""


def marker_fingerprint(provenance: dict[str, Any]) -> dict[str, Any]:
    """The marker facts a resumed run must still agree with.

    The full resolution chain is included rather than just a hash so a refusal message can
    say which link changed.
    """
    chain = provenance.get("resolution_chain")
    return {
        "status": provenance.get("status"),
        "markers_available": provenance.get("markers_available"),
        "marker_count": provenance.get("marker_count"),
        "selection_rule": provenance.get("selection_rule"),
        "prompt_sha256": provenance.get("prompt_sha256"),
        "entry_source_sha256": (chain or {}).get("entry_source_sha256"),
        "resolved_source_sha256": (chain or {}).get("resolved_source_sha256"),
        "assignment_symbol": (chain or {}).get("assignment_symbol"),
        "assignment_lineno": (chain or {}).get("assignment_lineno"),
        "assignment_end_lineno": (chain or {}).get("assignment_end_lineno"),
    }


def _fsync_file(handle: Any) -> None:
    handle.flush()
    try:
        os.fsync(handle.fileno())
    except (OSError, AttributeError):  # pragma: no cover - filesystem dependent
        pass


class Checkpoint:
    """Append-only record of completed cases plus the run's methodology fingerprint."""

    def __init__(self, output: Path) -> None:
        self.root = Path(output) / CHECKPOINT_DIRNAME
        self.cases_path = self.root / CASES_FILENAME
        self.state_path = self.root / STATE_FILENAME

    # -- writing -----------------------------------------------------------------
    def begin(self, fingerprint: dict[str, Any], *, completed: list[str] | None = None) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.cases_path.touch(exist_ok=True)
        self._write_state(fingerprint, completed or [], STATUS_RUNNING)

    def append(
        self, case: RealAgentCaseResult, fingerprint: dict[str, Any], completed: list[str]
    ) -> None:
        """Persist one classified case, then update the state. Order matters.

        The case line is durable before the state names it, so a crash between the two
        leaves a case that is recorded but not yet claimed — recoverable and never
        double-counted, because ``load`` derives completion from the case lines.
        """
        line = json.dumps(case.model_dump(mode="json"), sort_keys=True) + "\n"
        with self.cases_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(line)
            _fsync_file(handle)
        self._write_state(fingerprint, completed, STATUS_RUNNING)

    def finish(self, fingerprint: dict[str, Any], completed: list[str]) -> None:
        self._write_state(fingerprint, completed, STATUS_COMPLETED)

    def _write_state(self, fingerprint: dict[str, Any], completed: list[str], status: str) -> None:
        payload = {
            "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
            "marker": PARTIAL_MARKER if status != STATUS_COMPLETED else "COMPLETED",
            "status": status,
            "completed_scenario_ids": list(completed),
            "completed_count": len(completed),
            "fingerprint": fingerprint,
        }
        temporary = self.state_path.with_suffix(".json.tmp")
        with temporary.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
            _fsync_file(handle)
        os.replace(temporary, self.state_path)

    # -- reading -----------------------------------------------------------------
    def exists(self) -> bool:
        return self.state_path.exists() and self.cases_path.exists()

    def load(self) -> tuple[dict[str, Any], list[RealAgentCaseResult], bool]:
        """Return ``(state, completed_results, discarded_torn_tail)``.

        A final line without its terminating newline is a torn write: the process died
        mid-append. It is dropped, because a half-written line must never be read as a
        completed case. Any *other* unparseable line means the file is not trustworthy and
        raises rather than guessing which cases are real.
        """
        try:
            state = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointCorrupt(f"checkpoint state unreadable: {exc}") from exc
        if not isinstance(state, dict) or "fingerprint" not in state:
            raise CheckpointCorrupt("checkpoint state has no fingerprint")

        raw = self.cases_path.read_text(encoding="utf-8")
        torn = bool(raw) and not raw.endswith("\n")
        lines = [line for line in raw.split("\n") if line.strip()]
        if torn and lines:
            lines = lines[:-1]

        results: list[RealAgentCaseResult] = []
        seen: set[str] = set()
        for index, line in enumerate(lines):
            try:
                case = RealAgentCaseResult.model_validate_json(line)
            except Exception as exc:  # noqa: BLE001 - any failure here is untrustworthy
                raise CheckpointCorrupt(
                    f"checkpoint case line {index + 1} is not a valid case record: {exc}"
                ) from exc
            if case.scenario_id in seen:
                raise CheckpointCorrupt(f"checkpoint records {case.scenario_id} twice")
            seen.add(case.scenario_id)
            results.append(case)
        return state, results, torn


def validate_resume(state: dict[str, Any], fingerprint: dict[str, Any]) -> None:
    """Refuse a resume that would silently mix two different methodologies."""
    if state.get("checkpoint_schema_version") != CHECKPOINT_SCHEMA_VERSION:
        raise ResumeRefused(
            "checkpoint schema version "
            f"{state.get('checkpoint_schema_version')!r} != {CHECKPOINT_SCHEMA_VERSION}"
        )
    recorded = state.get("fingerprint")
    if not isinstance(recorded, dict):
        raise ResumeRefused("checkpoint has no fingerprint to compare against")

    differences: list[str] = []
    for key in RESUME_CRITICAL_KEYS:
        if recorded.get(key) != fingerprint.get(key):
            differences.append(key)
    # A commit is compared only when both sides know one. Unknown on either side is not
    # evidence of a change, and refusing on it would make resume unusable off a checkout.
    old_commit = recorded.get("methodology_commit")
    new_commit = fingerprint.get("methodology_commit")
    if old_commit and new_commit and old_commit != new_commit:
        differences.append("methodology_commit")

    if differences:
        detail = ", ".join(
            f"{key}: {recorded.get(key)!r} -> {fingerprint.get(key)!r}" for key in differences
        )
        raise ResumeRefused(
            "refusing to resume: the run's methodology-relevant configuration changed "
            f"({detail}). Start a new output directory instead."
        )


def build_fingerprint(
    *,
    profile: str,
    corpus_digest: str,
    case_schema_version: int,
    extraction_evidence_policy: str,
    explicit_refusal_rules_registered: int,
    detector_version: str,
    shadow_policy_version: str,
    report_version: str,
    provenance: dict[str, Any],
    case_order: list[str],
    execution_timeout_policy: dict[str, Any],
    methodology_commit: str | None,
) -> dict[str, Any]:
    return {
        "benchmark_id": BENCHMARK_ID,
        "profile": profile,
        "corpus_digest": corpus_digest,
        "case_schema_version": case_schema_version,
        "extraction_evidence_policy": extraction_evidence_policy,
        "explicit_refusal_rules_registered": explicit_refusal_rules_registered,
        "detector_version": detector_version,
        "shadow_policy_version": shadow_policy_version,
        "report_version": report_version,
        "marker_fingerprint": marker_fingerprint(provenance),
        "case_order": list(case_order),
        "execution_timeout_policy": execution_timeout_policy,
        "methodology_commit": methodology_commit,
    }
