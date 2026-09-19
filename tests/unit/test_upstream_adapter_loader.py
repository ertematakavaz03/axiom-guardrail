"""Regression tests for dynamic loading of the pinned upstream adapter.

Phase 3.5 pilot defect: ``_load_adapter`` built the module with
``importlib.util.module_from_spec`` and executed it without registering it in
``sys.modules`` first. ``benchmarks/external/langgraph-support-v1/adapter.py`` uses
``from __future__ import annotations``, so on Python 3.12 ``dataclasses._is_type``
resolves its string field annotations through ``sys.modules[cls.__module__].__dict__``
during class creation. With no entry that lookup returns ``None`` and the import dies
with ``AttributeError: 'NoneType' object has no attribute '__dict__'`` before any
benchmark case runs.

These tests exercise the real upstream file, not a stand-in, and pin the failure mode
itself so the guard cannot be silently removed.
"""

from __future__ import annotations

import dataclasses
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from demos.security_real_agent import runner

ADAPTER_PATH = runner.UPSTREAM_DIR / "adapter.py"


@pytest.fixture(autouse=True)
def clean_module_registry() -> Iterator[None]:
    """Each test starts and ends with an empty registration for the adapter."""
    sys.modules.pop(runner.UPSTREAM_ADAPTER_MODULE, None)
    yield
    sys.modules.pop(runner.UPSTREAM_ADAPTER_MODULE, None)


def test_the_pinned_adapter_file_is_present_and_uses_postponed_annotations() -> None:
    """Preconditions for the regression. If either changes, the guard needs rechecking."""
    assert ADAPTER_PATH.is_file()
    source = ADAPTER_PATH.read_text(encoding="utf-8")
    assert "from __future__ import annotations" in source
    assert "@dataclass" in source


def test_upstream_adapter_loads_through_the_real_loader() -> None:
    """The exact path that failed in the pilot, against the real upstream file."""
    module = runner.load_upstream_adapter_module()
    assert sys.modules[runner.UPSTREAM_ADAPTER_MODULE] is module
    # The dataclass that aborted the import must now be fully constructed.
    assert dataclasses.is_dataclass(module.HttpResult)
    assert [field.name for field in dataclasses.fields(module.HttpResult)] == [
        "body",
        "headers",
        "elapsed_ms",
    ]
    assert hasattr(module, "LangGraphHttpAdapter")
    assert hasattr(module, "ExternalAgentError")


def test_load_adapter_constructs_the_upstream_client() -> None:
    """``_load_adapter`` itself, the frame named in the pilot traceback. No network."""
    adapter = runner._load_adapter("http://127.0.0.1:8123", "agent", 5.0)
    assert type(adapter).__name__ == "LangGraphHttpAdapter"
    assert adapter.base_url == "http://127.0.0.1:8123"
    assert adapter.assistant_id == "agent"
    assert adapter.timeout_seconds == 5.0


def test_omitting_sys_modules_registration_reproduces_the_regression() -> None:
    """Negative control: pin the defect, so the guard cannot be quietly dropped.

    If this ever stops raising, the ``sys.modules`` registration is no longer
    load-bearing for this file on this interpreter, and that should be a deliberate,
    reviewed change rather than a silent one.
    """
    spec = importlib.util.spec_from_file_location("axiom_unregistered_probe", ADAPTER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert "axiom_unregistered_probe" not in sys.modules
    with pytest.raises(AttributeError, match="__dict__"):
        spec.loader.exec_module(module)


def test_loader_leaves_no_half_executed_module_registered(tmp_path: Path) -> None:
    """A failing import must not poison ``sys.modules`` for the next caller."""
    broken_dir = tmp_path / "broken-upstream"
    broken_dir.mkdir()
    (broken_dir / "adapter.py").write_text(
        "raise RuntimeError('synthetic upstream import failure')\n", encoding="utf-8"
    )
    original = runner.UPSTREAM_DIR
    runner.UPSTREAM_DIR = broken_dir
    try:
        with pytest.raises(RuntimeError, match="synthetic upstream import failure"):
            runner.load_upstream_adapter_module()
    finally:
        runner.UPSTREAM_DIR = original
    assert runner.UPSTREAM_ADAPTER_MODULE not in sys.modules


def test_loader_is_idempotent_and_does_not_re_execute() -> None:
    first = runner.load_upstream_adapter_module()
    marker = object()
    first.__dict__["_axiom_probe_marker"] = marker
    second = runner.load_upstream_adapter_module()
    assert second is first
    assert second.__dict__["_axiom_probe_marker"] is marker


def test_loader_does_not_modify_the_pinned_upstream_file() -> None:
    before = ADAPTER_PATH.read_bytes()
    runner.load_upstream_adapter_module()
    assert ADAPTER_PATH.read_bytes() == before
