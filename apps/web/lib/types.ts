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
  retrieval_recall_at_1?: number | null; retrieval_recall_at_3?: number | null;
  retrieval_recall_at_5?: number | null;
  mrr?: number | null; ndcg?: number | null; citation_precision?: number | null;
  citation_recall?: number | null; groundedness?: number | null; unsupported_claim_rate?: number | null;
  embedding_latency?: number | null; reranking_latency?: number | null;
  rag_average_latency?: number | null; rag_p95_latency?: number | null;
}
export interface Run {
  id: string; project_id: string; test_suite_id: string; agent_version_id: string; status: RunStatus;
  verdict: Verdict; snapshot: { agent?: Record<string, unknown>; suite?: Record<string, unknown>; scenarios?: Scenario[]; rag?: RagSnapshot };
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

export type CorpusStatus = "empty" | "indexing" | "ready" | "failed";
export type DocumentSourceType = "text" | "markdown" | "pdf";

export interface Corpus {
  id: string; project_id: string; name: string; description: string | null; version: string;
  status: CorpusStatus; document_count?: number; chunk_count?: number; embedding_model?: string | null;
  last_ingestion?: string | null; created_at: string; updated_at: string;
}

export interface DocumentRecord {
  id: string; corpus_id: string; project_id: string; name: string; source_type: DocumentSourceType;
  mime_type: string | null; source_uri: string | null; current_version: number; status: string;
  metadata: Record<string, unknown>; created_at: string; updated_at: string;
}

export interface DocumentChunk {
  id: string; document_version_id: string; document_id: string; corpus_id: string; project_id: string;
  organization_id: string; chunk_index: number; text: string; token_count: number | null;
  page_number: number | null; section_title: string | null; qdrant_point_id: string | null;
  metadata: Record<string, unknown>; created_at: string;
}

export interface RagConfig {
  id: string; project_id: string; name: string; embedding_provider: string; embedding_model: string;
  dense_enabled: boolean; sparse_enabled: boolean; top_k_dense: number; top_k_sparse: number;
  hybrid_top_k: number; reranker_type: string; reranker_model: string | null; rerank_top_n: number;
  metadata_filter_policy: Record<string, unknown>; created_at: string; updated_at: string;
}

export interface RetrievalHit {
  chunk_id: string; document_id: string; document_version_id: string; content: string; document_name: string;
  dense_score: number | null; sparse_score: number | null; hybrid_score: number | null;
  rerank_score: number | null; dense_rank: number | null; sparse_rank: number | null; rank: number;
  metadata: Record<string, unknown>;
}

export interface RetrievalTimings {
  embedding_ms: number; dense_ms: number; sparse_ms: number; fusion_ms: number;
  reranking_ms: number; total_ms: number;
}

export interface RetrievalResult {
  query: string; hits: RetrievalHit[]; dense_hits: RetrievalHit[]; sparse_hits: RetrievalHit[];
  candidate_count: number; timings: RetrievalTimings;
}

export interface RagSnapshot {
  corpus_id: string; corpus_version: string; rag_config_id: string; organization_id: string;
  project_id: string; embedding_provider: string; embedding_model: string; dense_enabled: boolean;
  sparse_enabled: boolean; retrieval_parameters: Record<string, unknown>; reranker_type: string;
  reranker_model: string | null; gold_evidence_version: string; qdrant_collection_name: string;
}
