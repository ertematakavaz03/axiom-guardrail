"""Locate, verify and import the pinned upstream agent source.

The upstream checkout lives outside this repository — it is a third-party project pinned
by commit and per-file SHA-256 in ``benchmarks/external/langgraph-support-v1/source.json``.
This module is the only place that touches it, and it does three things:

1. resolves where it is (explicit argument, then ``AXIOM_UPSTREAM_SRC``);
2. verifies every pinned file byte-for-byte before importing anything;
3. imports ``support_agent.tools`` and ``support_agent.prompts`` and hands them back.

Verification is not decoration. The mediated target's whole claim is "same agent, one node
replaced"; if the substrate differs from the one FULL-2 measured, the comparison is void.
A mismatch raises rather than warns, and the digests are recorded in the run evidence.

The manifest is read as *data* — a table of file digests. Nothing here imports benchmark
code, and the manifest carries no scenario, family or label that a decision could branch
on. ``tests/unit/test_runtime_antioverfit.py`` asserts that structurally.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

#: Pin manifest, relative to the repository root. Digests only.
DEFAULT_MANIFEST = Path("benchmarks/external/langgraph-support-v1/source.json")

#: Environment variable naming the upstream checkout root (the directory containing
#: ``src/support_agent``). Kept out of the repository: it is a private host path.
UPSTREAM_ENV = "AXIOM_UPSTREAM_SRC"


class UpstreamIntegrityError(RuntimeError):
    """The upstream checkout does not match the pinned digests, or is not where we look."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_pins(manifest: Path | None = None) -> dict[str, str]:
    """Return ``{relative path: sha256}`` from the pin manifest."""
    path = manifest or (_repo_root() / DEFAULT_MANIFEST)
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pins = data.get("upstream", {}).get("source_sha256", {})
    if not isinstance(pins, dict) or not pins:
        raise UpstreamIntegrityError(f"no source_sha256 pins in {path}")
    return {str(key): str(value) for key, value in pins.items()}


def resolve_root(root: str | os.PathLike[str] | None = None) -> Path:
    """Find the upstream checkout. Explicit argument wins, then the environment."""
    candidate = root or os.environ.get(UPSTREAM_ENV)
    if not candidate:
        raise UpstreamIntegrityError(
            "upstream source root not given; pass root= or set "
            f"{UPSTREAM_ENV} to the directory containing src/support_agent"
        )
    path = Path(candidate).expanduser().resolve()
    if not (path / "src" / "support_agent" / "tools.py").is_file():
        raise UpstreamIntegrityError(f"{path} does not look like the upstream checkout")
    return path


def verify(root: Path, pins: dict[str, str]) -> dict[str, str]:
    """Check every pinned file. Returns the observed digests; raises on any mismatch.

    Fails closed on a missing file as well as a changed one: an absent pinned file means
    the substrate is not the one that was measured, which is the same problem.
    """
    observed: dict[str, str] = {}
    problems: list[str] = []
    for relative, expected in sorted(pins.items()):
        target = root / relative
        if not target.is_file():
            problems.append(f"{relative}: missing")
            continue
        actual = hashlib.sha256(target.read_bytes()).hexdigest()
        observed[relative] = actual
        if actual != expected:
            problems.append(f"{relative}: {actual} != pinned {expected}")
    if problems:
        raise UpstreamIntegrityError(
            "upstream source does not match the pinned digests:\n  " + "\n  ".join(problems)
        )
    return observed


class UpstreamAgent:
    """The imported upstream modules, plus the evidence that they were the pinned ones."""

    def __init__(self, root: Path, digests: dict[str, str], modules: dict[str, ModuleType]) -> None:
        self.root = root
        self.digests = digests
        self.tools_module = modules["tools"]
        self.prompts_module = modules["prompts"]
        self.state_module = modules["state"]

    @property
    def tools(self) -> list[Any]:
        """The upstream's own bound tool list, in its own order."""
        return list(self.tools_module.tools)

    @property
    def system_prompt(self) -> str:
        return str(self.prompts_module.SYSTEM_PROMPT)

    @property
    def state_schema(self) -> Any:
        return self.state_module.SupportState

    def evidence(self) -> dict[str, Any]:
        """Integrity evidence for the run artifact. Deliberately excludes the host path.

        The absolute checkout location is a private path on the operator's machine and has
        no business in a committed artifact; the digests are what make the run reproducible.
        """
        return {
            "upstream_verified": True,
            "upstream_file_digests": dict(sorted(self.digests.items())),
        }


def load(
    root: str | os.PathLike[str] | None = None, *, manifest: Path | None = None
) -> UpstreamAgent:
    """Verify and import the upstream agent. Raises before importing if it does not match."""
    resolved = resolve_root(root)
    digests = verify(resolved, load_pins(manifest))

    source_dir = str(resolved / "src")
    if source_dir not in sys.path:
        # Prepend so the pinned checkout wins over any same-named installed package.
        sys.path.insert(0, source_dir)

    import importlib

    modules = {
        name: importlib.import_module(f"support_agent.{name}")
        for name in ("tools", "prompts", "state")
    }
    return UpstreamAgent(resolved, digests, modules)
