# Generic HTTP agent contract

The Generic HTTP adapter lets AgentArena evaluate a framework-neutral external agent. It makes one `POST` request per scenario with `Content-Type: application/json`. When `AGENTARENA_GENERIC_HTTP_SECRET` is configured, the API—not the browser—adds `Authorization: Bearer …`.

## Request

```json
{
  "messages": [{"role": "user", "content": "Where is order ORD-1001?"}],
  "context": {
    "run_id": "26d2138d-3e28-4a63-ac0a-3dde222fe89d",
    "case_id": "54f2df9a-ed25-4d19-9a75-71ce8cf59da1",
    "sandbox": true
  }
}
```

## Response

```json
{
  "final_response": "Order ORD-1001 was delivered.",
  "messages": [{"role": "assistant", "content": "Order ORD-1001 was delivered."}],
  "tool_calls": [{
    "tool_call_id": "call-1",
    "name": "get_order",
    "arguments": {"order_id": "ORD-1001"},
    "result": null
  }],
  "usage": {"input_tokens": 30, "output_tokens": 12},
  "metadata": {"deployment": "candidate-18"}
}
```

`messages`, `tool_calls`, `usage`, and `metadata` may be empty but must have the documented types. Unknown top-level and nested fields are rejected. Invalid JSON or a schema mismatch becomes `AGENT_RESPONSE_VALIDATION_ERROR` evidence instead of crashing a worker.

`result` is accepted as diagnostic metadata but does not authorize a side effect. AgentArena validates the tool against the snapshotted registry and scenario policy, validates arguments, applies confirmation and risk rules, and executes only the local sandbox implementation.

## Retry behavior

AgentArena retries only HTTP 429, temporary 5xx, network interruption, and timeout failures, at most twice. HTTP 4xx, invalid responses, invalid tool arguments, incorrect behavior, and policy violations never retry.

