"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useMemo, useState } from "react";
import { api } from "@/lib/api";
import type { CaseResult, Evaluation, Trace } from "@/lib/types";
import { Breadcrumbs, Loading, PageHeader } from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";

interface TraceBundle {
  case: CaseResult;
  traces: Trace[];
  evaluations: Evaluation[];
}

export default function CaseTracePage() {
  const { caseId } = useParams<{ caseId: string }>();
  const [bundle, setBundle] = useState<TraceBundle | null>(null);
  useEffect(() => {
    api<TraceBundle>(`/cases/${caseId}/trace`).then(setBundle);
  }, [caseId]);
  const elapsed = useMemo(
    () =>
      (bundle?.traces ?? []).reduce<{
        total: number;
        values: Record<string, number>;
      }>(
        (accumulator, trace) => {
          const total = accumulator.total + (trace.duration_ms ?? 0);
          return {
            total,
            values: { ...accumulator.values, [trace.id]: total },
          };
        },
        { total: 0, values: {} },
      ).values,
    [bundle],
  );
  if (!bundle) return <Loading />;
  const primaryReason = bundle.case.reason_codes[0];
  const benchmarkExpectation = bundle.traces.find(
    (trace) => trace.event_type === "external_benchmark_expectation",
  );
  const evidenceTrace = bundle.traces.find((trace) =>
    ["retrieval_evidence_selected", "external_retrieval_observed"].includes(trace.event_type),
  );
  const isExternalCase = bundle.traces.some(
    (trace) => trace.event_type === "external_turn_completed",
  );
  const isExternalEvidence = evidenceTrace?.event_type === "external_retrieval_observed";
  const retrievalFailure = bundle.traces.find((trace) => trace.event_type === "retrieval_failed");
  const claims = bundle.traces.filter((trace) => trace.event_type === "claim_extracted");
  const citations = bundle.traces.filter((trace) => trace.event_type === "citation_emitted");
  const rawEvidenceHits = evidenceTrace?.payload.hits ?? evidenceTrace?.payload.items;
  const evidenceHits = Array.isArray(rawEvidenceHits)
    ? rawEvidenceHits.filter(
        (hit): hit is Record<string, unknown> => typeof hit === "object" && hit !== null,
      )
    : [];
  const ragEvaluations = bundle.evaluations.filter((evaluation) =>
    evaluation.metric.startsWith("retrieval_") ||
    evaluation.metric.startsWith("citation_") ||
    ["groundedness", "unsupported_claim_rate", "tenant_isolation", "source_freshness"].includes(evaluation.metric),
  );
  const isRagCase = Boolean(evidenceTrace || retrievalFailure || ragEvaluations.length);

  return (
    <>
      <Breadcrumbs
        items={[
          { label: "Runs", href: "/runs" },
          {
            label: bundle.case.run_id.slice(0, 8),
            href: `/runs/${bundle.case.run_id}`,
          },
          { label: "Case trace" },
        ]}
      />
      <PageHeader
        eyebrow="CASE EVIDENCE"
        title={`Case ${bundle.case.id.slice(0, 8)}`}
        description="A complete, redacted reconstruction of agent decisions and policy outcomes."
        action={
          <StatusBadge value={bundle.case.verdict ?? bundle.case.status} />
        }
      />
      {benchmarkExpectation && (
        <section className="panel spaced">
          <div className="panel-title">
            <div><span className="eyebrow">EXTERNAL BENCHMARK CASE</span><h2>Expected vs observed</h2></div>
            <StatusBadge value={bundle.case.verdict ?? bundle.case.status} />
          </div>
          <div className="eval-list">
            <article><header><strong>Actual input</strong></header><p>{String(benchmarkExpectation.payload.actual_input ?? "N/A")}</p></article>
            <article><header><strong>Expected behavior</strong></header><pre>{JSON.stringify({ expected_tools: benchmarkExpectation.payload.expected_tools, expected_tool_arguments: benchmarkExpectation.payload.expected_tool_arguments, expected_facts: benchmarkExpectation.payload.expected_facts, expected_unknown: benchmarkExpectation.payload.expected_unknown }, null, 2)}</pre></article>
            <article><header><strong>Actual final response</strong></header><p>{bundle.case.final_response || "N/A"}</p></article>
            <article><header><strong>Audit classification</strong></header><code>{String(benchmarkExpectation.payload.audit_classification ?? "N/A")}</code><p>{bundle.case.reason_codes.length ? bundle.case.reason_codes.join(" · ") : "No findings"}</p></article>
          </div>
        </section>
      )}
      {isRagCase && (
        <section className="rag-evidence-flow panel">
          <div className="panel-title"><div><span className="eyebrow">{isExternalCase ? "EXTERNAL EVIDENCE FLOW" : "RAG EVIDENCE FLOW"}</span><h2>Retrieval → answer → citation → verdict</h2></div></div>
          <div className="evidence-flow-grid">
            <article><span>01 · RETRIEVAL</span><strong>{retrievalFailure ? "Failed" : `${evidenceHits.length} evidence chunks`}</strong>{retrievalFailure ? <pre>{JSON.stringify(retrievalFailure.payload, null, 2)}</pre> : <div className="source-links">{evidenceHits.map((hit, index) => isExternalEvidence ? <div key={`${String(hit.document_id)}-${index}`}><b>#{index + 1} {String(hit.document_id || "Unmapped source")}</b><code>rank {String(hit.rank ?? index + 1)} · benchmark-derived ID</code></div> : <Link href={`/documents/${String(hit.document_id)}`} key={String(hit.chunk_id)}><b>#{index + 1} {String(hit.document_name || "Source document")}</b><code>chunk {String(hit.chunk_id).slice(0, 8)} · inspect source →</code></Link>)}</div>}</article>
            <i>→</i>
            <article><span>02 · AGENT ANSWER</span><strong>{claims.length} extracted claims</strong><p>{bundle.case.final_response || "No final response"}</p><pre>{JSON.stringify(claims.map((claim) => claim.payload), null, 2)}</pre></article>
            <i>→</i>
            <article><span>03 · CITATIONS</span><strong>{citations.length} emitted</strong><pre>{JSON.stringify(citations.map((citation) => citation.payload), null, 2)}</pre></article>
            <i>→</i>
            <article className={bundle.case.verdict === "pass" ? "flow-pass" : "flow-fail"}><span>04 · EVALUATION</span><strong>{primaryReason || "All checks passed"}</strong><div>{ragEvaluations.map((evaluation) => <code key={evaluation.id}>{evaluation.metric}: {evaluation.passed ? "PASS" : "FAIL"}</code>)}</div></article>
          </div>
        </section>
      )}
      <div className="trace-layout">
        <section className="panel timeline-panel">
          <div className="panel-title">
            <div>
              <span className="eyebrow">EXECUTION TRACE</span>
              <h2>Timeline</h2>
            </div>
            <span>{bundle.traces.length} events</span>
          </div>
          <div className="timeline">
            {bundle.traces.map((trace) => {
              const decision =
                typeof trace.payload.decision === "string"
                  ? trace.payload.decision
                  : null;
              return (
                <article
                  key={trace.id}
                  className={decision === "deny" ? "denied" : ""}
                >
                  <div className="time">{elapsed[trace.id] ?? 0} ms</div>
                  <i />
                  <div className="event">
                    <header>
                      <span>{trace.event_type.replaceAll("_", " ")}</span>
                      {trace.name && <strong>{trace.name}</strong>}
                      {decision && <StatusBadge value={decision} />}
                    </header>
                    <pre>{JSON.stringify(trace.payload, null, 2)}</pre>
                    {trace.duration_ms !== null && (
                      <small>duration {trace.duration_ms} ms</small>
                    )}
                  </div>
                </article>
              );
            })}
          </div>
        </section>
        <aside className="panel evidence-panel">
          <div className="panel-title">
            <div>
              <span className="eyebrow">EVALUATORS</span>
              <h2>Evidence</h2>
            </div>
          </div>
          {primaryReason && (
            <div className="primary-reason">
              <span>PRIMARY REASON</span>
              <code>{primaryReason}</code>
              {bundle.case.reason_codes.length > 1 && (
                <small>
                  Secondary findings: {bundle.case.reason_codes.slice(1).join(" · ")}
                </small>
              )}
            </div>
          )}
          <div className="eval-list">
            {bundle.evaluations.map((evaluation) => {
              const isPrimary =
                !evaluation.passed && evaluation.reason_code === primaryReason;
              return (
                <article
                  key={evaluation.id}
                  className={`${evaluation.passed ? "passed" : "failed"}${isPrimary ? " primary" : ""}`}
                >
                  <header>
                    <div>
                      <i />
                      <strong>
                        {evaluation.metric.replaceAll("_", " ")}
                        {isPrimary && <em>PRIMARY</em>}
                      </strong>
                    </div>
                    <StatusBadge
                      value={evaluation.passed ? "pass" : "block"}
                    />
                  </header>
                  <p>{evaluation.explanation}</p>
                  {evaluation.reason_code && <code>{evaluation.reason_code}</code>}
                  <details open={!evaluation.passed}>
                    <summary>Expected vs actual</summary>
                    <label>
                      EXPECTED
                      <pre>{JSON.stringify(evaluation.expected, null, 2)}</pre>
                    </label>
                    <label>
                      ACTUAL
                      <pre>{JSON.stringify(evaluation.actual, null, 2)}</pre>
                    </label>
                    {Object.keys(evaluation.evidence).length > 0 && (
                      <label>
                        EVIDENCE
                        <pre>{JSON.stringify(evaluation.evidence, null, 2)}</pre>
                      </label>
                    )}
                  </details>
                </article>
              );
            })}
          </div>
          <div className={`verdict-box ${bundle.case.verdict}`}>
            <span>CASE VERDICT</span>
            <strong>{bundle.case.verdict?.toUpperCase()}</strong>
            <p>
              {primaryReason
                ? `Primary: ${primaryReason}`
                : "All deterministic checks passed."}
            </p>
          </div>
        </aside>
      </div>
    </>
  );
}
