export type Verdict = "pass" | "warn" | "block" | null;
export type RunStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface Project {
  id: string; organization_id: string; name: string; description: string; created_at: string; updated_at: string;
}
export interface Agent {
  id: string; project_id: string; name: string; description: string; created_at: string; updated_at: string;
}
export interface AgentVersion {
  id: string; agent_id: string; version: string; adapter_type: string; endpoint_url: string | null;
  model_provider: string; model_name: string; tool_registry: Record<string, unknown>[]; created_at: string;
}
export interface Suite {
  id: string; project_id: string; name: string; description: string; version: string;
  gate_policy: Record<string, unknown>; created_at: string; updated_at: string;
}
export interface Scenario {
  id: string; test_suite_id: string; name: string; input: string; expected_output: string | null;
  expected_tools: string[]; forbidden_tools: string[]; expected_tool_arguments: Record<string, Record<string, unknown>> | null;
  tags: string[]; severity: "low" | "medium" | "high" | "critical"; timeout_seconds: number;
  metadata: Record<string, unknown>; created_at: string; updated_at: string;
}
export interface RunMetrics {
  task_success?: number; tool_selection_accuracy?: number; tool_argument_accuracy?: number;
  security_violations?: number; average_latency_ms?: number; p95_latency_ms?: number;
  average_estimated_cost?: number; total_tokens?: number; top_failure_reasons?: Record<string, number>;
  quality_score?: number; tool_correctness_score?: number; security_score?: number; efficiency_score?: number;
}
export interface Run {
  id: string; project_id: string; test_suite_id: string; agent_version_id: string; status: RunStatus;
  verdict: Verdict; snapshot: { agent?: Record<string, unknown>; suite?: Record<string, unknown>; scenarios?: Scenario[] };
  metrics: RunMetrics; overall_score: string | null; total_cases: number;
  completed_cases: number; passed_cases: number; failed_cases: number; started_at: string | null;
  finished_at: string | null; created_at: string;
}
export interface CaseResult {
  id: string; run_id: string; scenario_id: string; status: string; verdict: Verdict; score: string | null;
  reason_codes: string[]; final_response: string | null; latency_ms: number | null; input_tokens: number | null;
  output_tokens: number | null; total_tokens: number | null; estimated_cost: string | null;
  started_at: string | null; finished_at: string | null;
}
export interface Trace {
  id: string; case_result_id: string; sequence_number: number; event_type: string; name: string | null;
  payload: Record<string, unknown>; duration_ms: number | null; created_at: string;
}
export interface Evaluation {
  id: string; metric: string; value: string | null; passed: boolean; reason_code: string | null;
  explanation: string; expected: unknown; actual: unknown; evidence: Record<string, unknown>;
}
