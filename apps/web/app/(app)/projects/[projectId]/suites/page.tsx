"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { api } from "@/lib/api";
import type { Project, Suite } from "@/lib/types";
import { Breadcrumbs, Empty, ErrorNotice, Loading, PageHeader } from "@/components/ui";

export default function SuitesPage() {
  const { projectId } = useParams<{ projectId: string }>(); const [project, setProject] = useState<Project | null>(null); const [suites, setSuites] = useState<Suite[] | null>(null); const [open, setOpen] = useState(false); const [error, setError] = useState<string | null>(null);
  const load = useCallback(() => Promise.all([api<Project>(`/projects/${projectId}`), api<Suite[]>(`/projects/${projectId}/suites`)]).then(([p, s]) => { setProject(p); setSuites(s); }), [projectId]); useEffect(() => { void load(); }, [load]);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); try { await api(`/projects/${projectId}/suites`, { method: "POST", body: JSON.stringify({ name: data.get("name"), description: data.get("description"), version: data.get("version"), gate_policy: { block_severities: ["critical"] } }) }); setOpen(false); load(); } catch (e) { setError(e instanceof Error ? e.message : "Unable to create suite"); } }
  if (!project || !suites) return <Loading />;
  return <><Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: project.name, href: `/projects/${projectId}` }, { label: "Suites" }]} /><PageHeader eyebrow="TEST DESIGN" title="Golden test suites" description="Versioned deterministic scenarios with explicit security gates." action={<button className="button primary" onClick={() => setOpen(!open)}>+ New suite</button>} /><ErrorNotice message={error} />{open && <form className="panel inline-form" onSubmit={submit}><label>Name<input name="name" placeholder="Golden Support Suite" required /></label><label>Version<input name="version" placeholder="1" defaultValue="1" required /></label><label>Description<input name="description" placeholder="Core release scenarios" /></label><button className="button primary">Create suite</button></form>}{suites.length === 0 ? <Empty title="No suites yet" detail="Create a suite, add scenarios, and evaluate an immutable agent version." /> : <div className="card-grid">{suites.map((suite) => <Link href={`/suites/${suite.id}`} className="project-card" key={suite.id}><div className="suite-top"><div className="project-mark">TS</div><code>v{suite.version}</code></div><h2>{suite.name}</h2><p>{suite.description || "No description"}</p><footer><span>Critical failures block</span><b>Open suite →</b></footer></Link>)}</div>}</>;
}
