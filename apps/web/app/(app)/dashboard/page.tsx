"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Run } from "@/lib/types";
import { StatusBadge } from "@/components/status-badge";
import { Empty, Loading, MetricCard, PageHeader } from "@/components/ui";

export default function DashboardPage() {
  const [runs, setRuns] = useState<Run[] | null>(null);
  useEffect(() => { api<Run[]>("/runs").then(setRuns).catch(() => setRuns([])); }, []);
  if (!runs) return <Loading />;
  const latest = runs.find((run) => run.status === "completed") ?? runs[0]; const metrics = latest?.metrics ?? {};
  const reasons = typeof metrics.top_failure_reasons === "object" ? metrics.top_failure_reasons : {};
  return <><PageHeader eyebrow="RELEASE CONTROL" title="Evidence overview" description="The latest measurable picture of agent quality, safety, and efficiency." action={<Link className="button primary" href="/projects">New evaluation</Link>} />
    <section className="metric-grid"><MetricCard label="Release Score" value={latest?.overall_score ?? "—"} detail={latest ? `Run ${latest.id.slice(0, 8)}` : "No completed runs"} tone={latest?.verdict ?? "neutral"} /><MetricCard label="Task Success" value={metrics.task_success !== undefined ? `${metrics.task_success}%` : "—"} detail="Deterministic checks" /><MetricCard label="Security Blockers" value={metrics.security_violations ?? 0} detail="Hard policy violations" tone={Number(metrics.security_violations) > 0 ? "block" : "pass"} /><MetricCard label="Average Cost" value={metrics.average_estimated_cost !== undefined ? `$${Number(metrics.average_estimated_cost).toFixed(4)}` : "—"} detail="Configured pricing map" /><MetricCard label="Average Latency" value={metrics.average_latency_ms !== undefined ? `${metrics.average_latency_ms} ms` : "—"} detail="End-to-end case" /></section>
    <div className="dashboard-grid"><section className="panel"><div className="panel-title"><div><span className="eyebrow">RECENT ACTIVITY</span><h2>Recent runs</h2></div><Link href="/runs">View all</Link></div>{runs.length === 0 ? <Empty title="No runs yet" detail="Create a project, agent, suite, and start your first evaluation." /> : <div className="run-list">{runs.slice(0, 6).map((run) => <Link href={`/runs/${run.id}`} key={run.id} className="run-row"><div className="run-icon">{run.snapshot.agent?.version?.toString().slice(0, 2).toUpperCase() ?? "RN"}</div><div><strong>{String(run.snapshot.agent?.version ?? "Agent run")}</strong><span>{new Date(run.created_at).toLocaleString()} · {run.completed_cases}/{run.total_cases} cases</span></div><StatusBadge value={run.verdict ?? run.status} /><b>{run.overall_score ?? "—"}</b></Link>)}</div>}</section>
      <section className="panel"><div className="panel-title"><div><span className="eyebrow">ROOT CAUSES</span><h2>Top failure reasons</h2></div></div>{Object.keys(reasons).length === 0 ? <Empty title="No failures recorded" detail="Failure reason codes will appear here with evidence." /> : <div className="reason-list">{Object.entries(reasons).map(([reason, count], i) => <div key={reason}><span>{i + 1}</span><div><strong>{reason.replaceAll("_", " ")}</strong><small>{count} occurrence{Number(count) === 1 ? "" : "s"}</small></div><i style={{ width: `${Math.min(100, Number(count) * 20)}%` }} /></div>)}</div>}</section></div></>;
}

