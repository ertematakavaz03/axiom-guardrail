from __future__ import annotations

from pathlib import Path

from runner import append_jsonl, read_jsonl


def test_incremental_jsonl_is_immediately_resumable(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    append_jsonl(path, {"execution_id": "case-1", "value": 1})
    append_jsonl(path, {"execution_id": "case-2", "value": 2})
    assert [record["execution_id"] for record in read_jsonl(path)] == ["case-1", "case-2"]


def test_jsonl_reader_rejects_corrupt_checkpoint(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text('{"execution_id":"ok"}\nnot-json\n', encoding="utf-8")
    try:
        read_jsonl(path)
    except RuntimeError as exc:
        assert ":2:" in str(exc)
    else:
        raise AssertionError("corrupt resumable state was silently accepted")
