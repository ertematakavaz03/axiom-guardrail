"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { CaseResult, Run } from "@/lib/types";
import { Breadcrumbs, Loading, MetricCard, PageHeader } from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";

export default function RunDetailPage() {
  const { runId } = useParams<{ runId: string }>();
  const [run, setRun] = useState<Run | null>(null);
  const [cases, setCases] = useState<CaseResult[]>([]);
  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      const [nextRun, nextCases] = await Promise.all([
        api<Run>(`/runs/${runId}`),
        api<CaseResult[]>(`/runs/${runId}/cases`),
      ]);
      setRun(nextRun);
      setCases(nextCases);
      if (["queued", "running"].includes(nextRun.status)) {
        timer = setTimeout(load, 1000);
      }
    };
    load();
    return () => clearTimeout(timer);
  }, [runId]);
  const names = useMemo(
    () =>
      Object.fromEntries(
        (run?.snapshot.scenarios ?? []).map((scenario) => [
          scenario.id,
          scenario.name,
        ]),
      ),
    [run],
  );
  if (!run) return <Loading />;
  const metrics = run.metrics;
  const external = metrics.external_benchmark;
  const externalSnapshot = run.snapshot.external_benchmark;
  const percentage = (value: number | null | undefined) =>
    value === null || value === undefined ? "N/A" : `${Math.round(value * 1000) / 10}%`;

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Runs", href: "/runs" },
          { label: run.id.slice(0, 8) },
        ]}
      />
      <PageHeader
        eyebrow="RUN EVIDENCE"
        title={`Run ${run.id.slice(0, 8)}`}
        description={`Agent ${String(run.snapshot.agent?.version ?? "—")} · Suite v${String(run.snapshot.suite?.version ?? "—")}`}
        action={<StatusBadge value={run.verdict ?? run.status} />}
      />
      <div className="run-hero panel">
        <div className={`score-ring score-${run.verdict ?? "queued"}`}>
          <strong>{run.overall_score ?? "—"}</strong>
          <span>OVERALL</span>
        </div>
        <div>
          <span className="eyebrow">RELEASE VERDICT</span>
          <h2>
            {run.status === "completed"
              ? run.verdict === "block"
                ? "Release blocked"
                : run.verdict === "warn"
                  ? "Review required"
                  : "Ready to ship"
              : "Evaluation in progress"}
          </h2>
          <p>
            {run.status === "completed"
              ? `${run.failed_cases} of ${run.total_cases} cases need attention.`
              : `${run.completed_cases} of ${run.total_cases} cases completed.`}
          </p>
        </div>
        <div className="progress-track">
          <i
            style={{
              width: `${run.total_cases ? (run.completed_cases / run.total_cases) * 100 : 0}%`,
            }}
          />
        </div>
      </div>
      <section className="metric-grid compact">
        <MetricCard
          label="Task Success"
          value={metrics.task_success !== undefined ? `${metrics.task_success}%` : "—"}
        />
        <MetricCard
          label="Tool Selection"
          value={
            metrics.tool_selection_accuracy !== undefined
              ? `${metrics.tool_selection_accuracy}%`
              : "—"
          }
        />
        <MetricCard
          label="Security Violations"
          value={metrics.security_violations ?? "—"}
          tone={Number(metrics.security_violations) ? "block" : "pass"}
        />
        <MetricCard
          label="Average / p95"
          value={
            metrics.average_latency_ms !== undefined
              ? `${metrics.average_latency_ms} / ${metrics.p95_latency_ms} ms`
              : "—"
          }
        />
        <MetricCard label="Total tokens" value={metrics.total_tokens ?? "—"} />
      </section>
      {external && externalSnapshot && (
        <>
          <section className="panel rag-run-context">
            <div><span>External benchmark</span><strong>{String(externalSnapshot.benchmark_id)}</strong><code>{external.completed_execution_count} cases · {external.verdicts.pass} PASS / {external.verdicts.warn} WARN / {external.verdicts.block} BLOCK</code></div>
            <div><span>Pinned source</span><strong>{String(externalSnapshot.external_repository)}</strong><code>{String(externalSnapshot.pinned_sha)}</code></div>
            <div><span>Model</span><strong>{String(run.snapshot.agent?.model_name ?? "N/A")}</strong><code>Run → Case → Trace</code></div>
            <div><span>Audit</span><strong>{external.audit.unresolved_case_count === 0 ? "Complete" : "Unresolved"}</strong><code>{external.audit.validated_agent_failure_case_count} validated failures</code></div>
          </section>
          <section className="metric-grid rag-metrics">
            <MetricCard label="Task success" value={percentage(external.quality.task_success_rate)} />
            <MetricCard label="Tool selection" value={percentage(external.quality.tool_selection_accuracy)} />
            <MetricCard label="Required tool recall" value={percentage(external.quality.required_tool_recall)} />
            <MetricCard label="Tool argument accuracy" value={percentage(external.quality.tool_argument_accuracy)} />
            <MetricCard label="Grounded cases" value={percentage(external.hallucination.grounded_case_rate)} tone={(external.hallucination.grounded_case_rate ?? 1) < 1 ? "block" : "pass"} />
            <MetricCard label="Hallucination cases" value={percentage(external.hallucination.hallucination_case_rate)} tone={(external.hallucination.hallucination_case_rate ?? 0) > 0 ? "block" : "pass"} />
            <MetricCard label="Leakage rate" value={percentage(external.privacy_isolation.leakage_rate)} tone={external.privacy_isolation.leakage_cases ? "block" : "pass"} />
            <MetricCard label="Completion rate" value={percentage(external.quality.completion_rate)} />
            <MetricCard label="Malformed output rate" value={percentage(external.quality.malformed_output_rate)} tone={(external.quality.malformed_output_rate ?? 0) > 0 ? "block" : "pass"} />
            <MetricCard label="p50 / p95 latency" value={external.performance.total_case_latency_ms.p95 == null ? "N/A" : `${Math.round(external.performance.total_case_latency_ms.p50 ?? 0)} / ${Math.round(external.performance.total_case_latency_ms.p95)} ms`} detail="Total case latency" />
            <MetricCard label="Token usage" value={external.performance.token_counts.total_tokens ?? "N/A"} detail={`${external.performance.token_counts.input_tokens ?? "N/A"} input · ${external.performance.token_counts.output_tokens ?? "N/A"} output`} />
          </section>
        </>
      )}
      {run.snapshot.rag && (
        <>
          <section className="panel rag-run-context">
            <div><span>Corpus snapshot</span><strong>v{run.snapshot.rag.corpus_version}</strong><code>{run.snapshot.rag.corpus_id.slice(0, 8)}</code></div>
            <div><span>Retrieval</span><strong>{run.snapshot.rag.dense_enabled ? "Dense" : ""}{run.snapshot.rag.sparse_enabled ? " + Sparse" : ""}</strong><code>{run.snapshot.rag.reranker_type}</code></div>
            <div><span>Embedding</span><strong>{run.snapshot.rag.embedding_provider}</strong><code>{run.snapshot.rag.embedding_model}</code></div>
            <div><span>Gold evidence</span><strong>v{run.snapshot.rag.gold_evidence_version}</strong><code>immutable</code></div>
          </section>
          <section className="metric-grid rag-metrics">
            <MetricCard label="Recall@1 / @3 / @5" value={`${percentage(metrics.retrieval_recall_at_1)} / ${percentage(metrics.retrieval_recall_at_3)} / ${percentage(metrics.retrieval_recall_at_5)}`} />
            <MetricCard label="MRR / nDCG" value={`${percentage(metrics.mrr)} / ${percentage(metrics.ndcg)}`} detail="N/A when graded relevance is absent" />
            <MetricCard label="Citation P / R" value={`${percentage(metrics.citation_precision)} / ${percentage(metrics.citation_recall)}`} />
            <MetricCard label="Groundedness" value={percentage(metrics.groundedness)} tone={(metrics.groundedness ?? 1) < 1 ? "block" : "pass"} />
            <MetricCard label="Unsupported claims" value={percentage(metrics.unsupported_claim_rate)} tone={(metrics.unsupported_claim_rate ?? 0) > 0 ? "block" : "pass"} />
            <MetricCard label="RAG average / p95" value={metrics.rag_average_latency == null ? "N/A" : `${metrics.rag_average_latency} / ${metrics.rag_p95_latency} ms`} detail={`embed ${metrics.embedding_latency ?? "N/A"} · rerank ${metrics.reranking_latency ?? "N/A"} ms`} />
          </section>
        </>
      )}
      <section className="panel spaced">
        <div className="panel-title">
          <div>
            <span className="eyebrow">CASE RESULTS</span>
            <h2>Failure-first investigation</h2>
          </div>
          <span>
            {run.passed_cases} passed · {run.failed_cases} need attention
          </span>
        </div>
        <div className="case-table">
          <div className="table-head">
            <span>Scenario</span>
            <span>Verdict</span>
            <span>Primary reason</span>
            <span>Latency</span>
            <span>Tokens</span>
            <span>Score</span>
          </div>
          {[...cases]
            .sort((first, second) =>
              first.verdict === "block" ? -1 : second.verdict === "block" ? 1 : 0,
            )
            .map((item) => (
              <Link
                href={`/cases/${item.id}`}
                className="table-row six"
                key={item.id}
              >
                <span>
                  <strong>
                    {names[item.scenario_id] ?? item.scenario_id.slice(0, 8)}
                  </strong>
                  <small>{item.status}</small>
                </span>
                <StatusBadge value={item.verdict ?? item.status} />
                <code className="reason-code">
                  {item.reason_codes[0] ?? "—"}
                </code>
                <span>{item.latency_ms ?? "—"} ms</span>
                <span>{item.total_tokens ?? "—"}</span>
                <b>{item.score ?? "—"} →</b>
              </Link>
            ))}
        </div>
      </section>
    </>
  );
}
