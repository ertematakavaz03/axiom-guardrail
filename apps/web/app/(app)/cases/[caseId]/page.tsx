"use client";

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
