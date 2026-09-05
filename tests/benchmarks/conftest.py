from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

BENCHMARK_ROOT = (
    Path(__file__).resolve().parents[2] / "benchmarks" / "external" / "langgraph-support-v1"
)
sys.path.insert(0, str(BENCHMARK_ROOT))


@pytest.fixture
def manifest() -> dict[str, Any]:
    return json.loads((BENCHMARK_ROOT / "manifest.yaml").read_text(encoding="utf-8"))


@pytest.fixture
def cases() -> list[dict[str, Any]]:
    return json.loads((BENCHMARK_ROOT / "cases.json").read_text(encoding="utf-8"))
