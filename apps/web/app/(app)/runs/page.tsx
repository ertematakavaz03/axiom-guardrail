"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Run } from "@/lib/types";
import { Empty, Loading, PageHeader } from "@/components/ui";
import { StatusBadge } from "@/components/status-badge";

export default function RunsPage() {
  const [runs, setRuns] = useState<Run[] | null>(null); useEffect(() => { api<Run[]>("/runs").then(setRuns); }, []); if (!runs) return <Loading />;
  return <><PageHeader eyebrow="EVALUATION HISTORY" title="Runs" description="Every run is backed by an immutable configuration snapshot and trace evidence." />{runs.length === 0 ? <Empty title="No evaluations yet" detail="Open a suite to queue your first run." /> : <section className="panel run-table"><div className="table-head"><span>Run</span><span>Agent / Suite</span><span>Progress</span><span>Verdict</span><span>Score</span><span>Created</span></div>{runs.map((run) => <Link href={`/runs/${run.id}`} className="table-row six" key={run.id}><code>{run.id.slice(0, 8)}</code><span><strong>{String(run.snapshot.agent?.version ?? "—")}</strong><small>Suite v{String(run.snapshot.suite?.version ?? "—")}</small></span><span>{run.completed_cases}/{run.total_cases}</span><StatusBadge value={run.verdict ?? run.status} /><b>{run.overall_score ?? "—"}</b><span>{new Date(run.created_at).toLocaleString()}</span></Link>)}</section>}</>;
}

