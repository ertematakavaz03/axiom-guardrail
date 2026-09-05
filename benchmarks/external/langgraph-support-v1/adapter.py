from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

SECRET_PATTERNS = [
    re.compile(r"(?i)(?<!return )(authorization\s*[:=]\s*)(?:bearer\s+)?([^\s,;]+)"),
    re.compile(r"(?i)(bearer\s+)([A-Za-z0-9._~+/=-]+)"),
    re.compile(r"(?i)((?:api[_-]?key|password|cookie|secret)\s*[:=]\s*)([^\s,;]+)"),
]


class ExternalAgentError(RuntimeError):
    def __init__(self, message: str, *, transient: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.transient = transient
        self.status_code = status_code


@dataclass(frozen=True)
class HttpResult:
    body: Any
    headers: dict[str, str]
    elapsed_ms: int


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if key not in {"input_tokens", "output_tokens", "total_tokens"}
            and re.search(r"(?i)(authorization|token|api.?key|password|cookie|secret)", str(key))
            else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        output = value
        for pattern in SECRET_PATTERNS:
            output = pattern.sub(r"\1[REDACTED]", output)
        return output
    return value


def _message_id(message: dict[str, Any], fallback: int) -> str:
    return str(message.get("id") or f"message-{fallback}")


def normalize_messages(raw_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, message in enumerate(raw_messages):
        message_id = _message_id(message, index)
        if message_id in seen:
            continue
        seen.add(message_id)
        item: dict[str, Any] = {
            "id": message_id,
            "role": {"human": "user", "ai": "assistant"}.get(
                str(message.get("type")), str(message.get("type", "unknown"))
            ),
            "content": str(message.get("content") or ""),
        }
        if message.get("name") is not None:
            item["name"] = str(message["name"])
        if message.get("tool_call_id") is not None:
            item["tool_call_id"] = str(message["tool_call_id"])
        if message.get("status") is not None:
            item["status"] = str(message["status"])
        if message.get("tool_calls"):
            item["tool_calls"] = [
                {
                    "tool_call_id": str(call.get("id", "")),
                    "name": str(call.get("name", "")),
                    "arguments": call.get("args") if isinstance(call.get("args"), dict) else {},
                }
                for call in message["tool_calls"]
            ]
        usage = message.get("usage_metadata")
        if isinstance(usage, dict):
            item["usage"] = {
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
            }
        metadata = message.get("response_metadata")
        if isinstance(metadata, dict) and metadata:
            item["model_metadata"] = {
                key: metadata.get(key)
                for key in (
                    "model",
                    "model_name",
                    "model_provider",
                    "done_reason",
                    "total_duration",
                    "load_duration",
                    "prompt_eval_count",
                    "prompt_eval_duration",
                    "eval_count",
                    "eval_duration",
                )
                if metadata.get(key) is not None
            }
        normalized.append(item)
    return normalized


def extract_tool_calls(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results_by_id = {
        str(message.get("tool_call_id")): message
        for message in messages
        if message.get("role") == "tool" and message.get("tool_call_id")
    }
    calls: list[dict[str, Any]] = []
    for message in messages:
        for call in message.get("tool_calls", []):
            result = results_by_id.get(str(call["tool_call_id"]))
            calls.append(
                {
                    **call,
                    "result": result.get("content") if result else None,
                    "status": result.get("status") if result else None,
                    "tool_latency_ms": None,
                    "tool_latency_status": "N/A",
                }
            )
    return calls


def _strip_box_prefix(line: str) -> str:
    return re.sub(r"^[\s│┃┌└├─━]+", "", line).strip()


def parse_retrieval_result(tool_result: str, document_signatures: dict[str, str]) -> dict[str, Any]:
    query_match = re.search(r"Query:\s*['\"]([^'\"]+)['\"]", tool_result)
    categories_match = re.search(r"Categories:\s*([^\r\n]+)", tool_result)
    minimum_match = re.search(r"Min Similarity:\s*([0-9.]+)", tool_result)
    items: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    capture_content = False
    for line in tool_result.splitlines():
        rank_match = re.search(r"Result #(\d+)", line)
        if rank_match:
            if current:
                current["content"] = "\n".join(current.pop("content_lines", [])).strip()
                current["document_id"] = next(
                    (
                        document_id
                        for document_id, signature in document_signatures.items()
                        if signature.lower() in current["content"].lower()
                    ),
                    None,
                )
                items.append(current)
            current = {"rank": int(rank_match.group(1)), "content_lines": []}
            capture_content = False
            continue
        if current is None:
            continue
        score_match = re.search(r"\((-?[0-9.]+)\)", line) if "Relevance:" in line else None
        if score_match:
            current["reported_similarity"] = float(score_match.group(1))
            continue
        if "Category:" in line:
            current["category"] = _strip_box_prefix(line.split("Category:", 1)[1])
            continue
        if "Type:" in line:
            current["type"] = _strip_box_prefix(line.split("Type:", 1)[1])
            continue
        if "├" in line:
            capture_content = True
            continue
        if "└" in line:
            capture_content = False
            continue
        if capture_content and "│" in line:
            content = _strip_box_prefix(line)
            if content:
                current["content_lines"].append(content)
    if current:
        current["content"] = "\n".join(current.pop("content_lines", [])).strip()
        current["document_id"] = next(
            (
                document_id
                for document_id, signature in document_signatures.items()
                if signature.lower() in current["content"].lower()
            ),
            None,
        )
        items.append(current)
    categories = []
    if categories_match:
        rendered = categories_match.group(1).strip()
        if rendered.lower() not in {"all", "none", ""}:
            categories = [item.strip().lower() for item in rendered.split(",") if item.strip()]
    return {
        "query": query_match.group(1) if query_match else None,
        "categories": categories,
        "min_similarity": float(minimum_match.group(1)) if minimum_match else None,
        "items": items,
        "document_ids_are_benchmark_derived": True,
        "retrieval_latency_ms": None,
        "retrieval_latency_status": "N/A",
    }


class LangGraphHttpAdapter:
    """Protocol-only adapter for a local LangGraph API.

    The adapter observes tools already executed by the external graph. It never invokes
    an Axiom tool and contains no customer-support correctness rules.
    """

    def __init__(
        self,
        base_url: str,
        *,
        assistant_id: str = "agent",
        timeout_seconds: float = 180.0,
        document_signatures: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.assistant_id = assistant_id
        self.timeout_seconds = timeout_seconds
        self.document_signatures = document_signatures or {}

    def _request(self, method: str, path: str, payload: Any | None = None) -> HttpResult:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=body,
            method=method,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read()
                elapsed_ms = round((time.perf_counter() - started) * 1000)
                parsed = json.loads(raw.decode("utf-8")) if raw else None
                return HttpResult(parsed, dict(response.headers.items()), elapsed_ms)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ExternalAgentError(
                f"LangGraph API returned HTTP {exc.code}: {detail[:500]}",
                transient=exc.code == 429 or exc.code >= 500,
                status_code=exc.code,
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ExternalAgentError(
                f"LangGraph API connection failed: {exc}", transient=True
            ) from exc

    def health(self) -> dict[str, Any]:
        info = self._request("GET", "/info")
        ok = self._request("GET", "/ok")
        return {"info": info.body, "ok": ok.body}

    def create_thread(self, metadata: dict[str, Any]) -> str:
        result = self._request("POST", "/threads", {"metadata": metadata})
        return str(result.body["thread_id"])

    def run_turn(self, thread_id: str, prompt: str) -> dict[str, Any]:
        result = self._request(
            "POST",
            f"/threads/{thread_id}/runs/wait",
            {
                "assistant_id": self.assistant_id,
                "input": {"messages": [{"role": "user", "content": prompt}]},
                "stream_mode": "values",
                "durability": "sync",
            },
        )
        location = next(
            (value for key, value in result.headers.items() if key.lower() == "content-location"),
            "",
        )
        run_id = location.rstrip("/").split("/")[-1] if location else None
        raw_messages = result.body.get("messages", []) if isinstance(result.body, dict) else []
        if not raw_messages:
            raise ExternalAgentError(
                "LangGraph run returned no messages; the graph may have failed before producing output",
                transient=True,
            )
        return {
            "run_id": run_id,
            "latency_ms": result.elapsed_ms,
            "messages": raw_messages,
        }

    def execute(self, case_id: str, turns: list[str]) -> dict[str, Any]:
        case_started = time.perf_counter()
        thread_id = self.create_thread({"benchmark": "langgraph-support-v1", "case_id": case_id})
        turn_results: list[dict[str, Any]] = []
        all_raw: list[dict[str, Any]] = []
        for prompt in turns:
            turn = self.run_turn(thread_id, prompt)
            turn_results.append({"run_id": turn["run_id"], "latency_ms": turn["latency_ms"]})
            all_raw.extend(turn["messages"])
        return self._finalize(
            case_id,
            [thread_id],
            all_raw,
            turn_results,
            round((time.perf_counter() - case_started) * 1000),
        )

    def execute_isolation(
        self, case_id: str, *, seed_prompt: str, probe_prompt: str, canary: str
    ) -> dict[str, Any]:
        case_started = time.perf_counter()
        seed_thread = self.create_thread(
            {"benchmark": "langgraph-support-v1", "case_id": case_id, "role": "seed"}
        )
        seed = self.run_turn(seed_thread, seed_prompt)
        probe_thread = self.create_thread(
            {"benchmark": "langgraph-support-v1", "case_id": case_id, "role": "probe"}
        )
        probe = self.run_turn(probe_thread, probe_prompt)
        result = self._finalize(
            case_id,
            [seed_thread, probe_thread],
            probe["messages"],
            [
                {"role": "seed", "run_id": seed["run_id"], "latency_ms": seed["latency_ms"]},
                {"role": "probe", "run_id": probe["run_id"], "latency_ms": probe["latency_ms"]},
            ],
            round((time.perf_counter() - case_started) * 1000),
        )
        probe_text = "\n".join(str(message.get("content", "")) for message in probe["messages"])
        result["isolation"] = {
            "canary": canary,
            "seed_thread_id": seed_thread,
            "probe_thread_id": probe_thread,
            "threads_are_distinct": seed_thread != probe_thread,
            "canary_observed_in_probe": canary in probe_text,
            "seed_trace": redact(normalize_messages(seed["messages"])),
        }
        return result

    def _finalize(
        self,
        case_id: str,
        thread_ids: list[str],
        raw_messages: list[dict[str, Any]],
        turn_results: list[dict[str, Any]],
        total_case_latency_ms: int,
    ) -> dict[str, Any]:
        messages = normalize_messages(raw_messages)
        calls = extract_tool_calls(messages)
        retrievals = [
            parse_retrieval_result(str(call["result"]), self.document_signatures)
            for call in calls
            if call["name"] == "search_vector_knowledge_base" and call.get("result")
        ]
        final_response = next(
            (
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "assistant" and not message.get("tool_calls")
            ),
            "",
        )
        usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
        usage_available = False
        for message in messages:
            item = message.get("usage")
            if not isinstance(item, dict):
                continue
            usage_available = True
            for key in usage:
                if isinstance(item.get(key), int):
                    usage[key] += item[key]
        external_latency = sum(int(turn["latency_ms"]) for turn in turn_results)
        model_duration_ns = 0
        model_latency_available = False
        for message in messages:
            metadata = message.get("model_metadata")
            if not isinstance(metadata, dict):
                continue
            duration = metadata.get("total_duration")
            if isinstance(duration, (int, float)):
                model_latency_available = True
                model_duration_ns += duration
        return redact(
            {
                "case_id": case_id,
                "thread_ids": thread_ids,
                "turns": turn_results,
                "messages": messages,
                "tool_calls": calls,
                "retrievals": retrievals,
                "final_response": final_response,
                "usage": usage if usage_available else None,
                "performance": {
                    "queue_wait_ms": None,
                    "queue_wait_status": "N/A",
                    "external_agent_latency_ms": external_latency,
                    "model_latency_ms": (
                        round(model_duration_ns / 1_000_000, 3) if model_latency_available else None
                    ),
                    "model_latency_status": (
                        "measured_from_ollama_response_metadata"
                        if model_latency_available
                        else "N/A_not_exposed"
                    ),
                    "tool_latency_ms": None,
                    "tool_latency_status": "N/A",
                    "retrieval_latency_ms": None,
                    "retrieval_latency_status": "N/A",
                    "total_case_latency_ms": total_case_latency_ms,
                    "time_to_first_token_ms": None,
                    "time_to_first_token_status": "N/A",
                    "retry_count": 0,
                    "tool_call_count": len(calls),
                },
                "errors": [],
            }
        )
