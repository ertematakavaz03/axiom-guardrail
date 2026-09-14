"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/status-badge";
import { Breadcrumbs, ErrorNotice, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { Agent, Corpus, Project, Run, Suite } from "@/lib/types";

interface ProjectData {
  project: Project;
  agents: Agent[];
  suites: Suite[];
  runs: Run[];
  corpora: Corpus[];
}

export default function ProjectDetailPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [data, setData] = useState<ProjectData | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api<Project>(`/projects/${projectId}`),
      api<Agent[]>(`/projects/${projectId}/agents`),
      api<Suite[]>(`/projects/${projectId}/suites`),
      api<Run[]>(`/runs?project_id=${projectId}`),
      api<Corpus[]>(`/projects/${projectId}/corpora`),
    ])
      .then(([project, agents, suites, runs, corpora]) =>
        setData({ project, agents, suites, runs, corpora }),
      )
      .catch((caught: unknown) =>
        setError(caught instanceof Error ? caught.message : "Unable to load project"),
      );
  }, [projectId]);

  if (!data && !error) return <Loading />;
  if (!data) return <ErrorNotice message={error} />;

  return (
    <>
      <Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: data.project.name }]} />
      <PageHeader
        eyebrow="PROJECT"
        title={data.project.name}
        description={data.project.description || "Agent evaluation workspace"}
      />
      <ErrorNotice message={error} />
      <div className="summary-strip five">
        <div><strong>{data.agents.length}</strong><span>Agents</span></div>
        <div><strong>{data.suites.length}</strong><span>Suites</span></div>
        <div><strong>{data.corpora.length}</strong><span>Corpora</span></div>
        <div><strong>{data.runs.length}</strong><span>Runs</span></div>
        <div><strong>{data.runs.filter((run) => run.verdict === "block").length}</strong><span>Blocked</span></div>
      </div>
      <div className="card-grid three">
        <Link className="action-card" href={`/projects/${projectId}/security`}>
          <span>SEC</span><div><h2>Security & red team</h2><p>Manage policies, inspect MCP trust, and run controlled attacks.</p></div><b>Open →</b>
        </Link>
        <Link className="action-card" href={`/projects/${projectId}/agents`}>
          <span>AG</span><div><h2>Agent registry</h2><p>Register immutable versions and adapter contracts.</p></div><b>Manage →</b>
        </Link>
        <Link className="action-card" href={`/projects/${projectId}/suites`}>
          <span>TS</span><div><h2>Test suites</h2><p>Define golden scenarios, policies, and evidence gates.</p></div><b>Manage →</b>
        </Link>
        <Link className="action-card" href={`/projects/${projectId}/corpora`}>
          <span>RG</span><div><h2>RAG corpora</h2><p>Ingest sources, inspect chunks, and debug retrieval.</p></div><b>Manage →</b>
        </Link>
      </div>
      <section className="panel spaced">
        <div className="panel-title"><h2>Project runs</h2><Link href="/runs">All runs</Link></div>
        {data.runs.map((run) => (
          <Link className="table-row" href={`/runs/${run.id}`} key={run.id}>
            <code>{run.id.slice(0, 8)}</code>
            <span>{String(run.snapshot.agent?.version ?? "Agent")}</span>
            <span>{run.completed_cases}/{run.total_cases} cases</span>
            <StatusBadge value={run.verdict ?? run.status} />
            <b>{run.overall_score ?? "—"}</b>
          </Link>
        ))}
      </section>
    </>
  );
}
