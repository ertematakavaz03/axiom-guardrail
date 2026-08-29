"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Project } from "@/lib/types";
import { Empty, ErrorNotice, Loading, PageHeader } from "@/components/ui";

export default function ProjectsPage() {
  const [projects, setProjects] = useState<Project[] | null>(null); const [open, setOpen] = useState(false); const [error, setError] = useState<string | null>(null);
  const load = () => api<Project[]>("/projects").then(setProjects).catch((e) => setError(e.message)); useEffect(() => { void load(); }, []);
  async function submit(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const data = new FormData(event.currentTarget); try { await api("/projects", { method: "POST", body: JSON.stringify({ name: data.get("name"), description: data.get("description") }) }); setOpen(false); load(); } catch (e) { setError(e instanceof Error ? e.message : "Could not create project"); } }
  if (!projects) return <Loading />;
  return <><PageHeader eyebrow="WORKSPACE" title="Projects" description="Organize agents and evaluation suites by product boundary." action={<button className="button primary" onClick={() => setOpen(!open)}>+ New project</button>} /><ErrorNotice message={error} />{open && <form className="inline-form panel" onSubmit={submit}><label>Project name<input name="name" placeholder="Customer Support Evaluation" required /></label><label>Description<input name="description" placeholder="Release quality gate for support automation" /></label><button className="button primary">Create project</button></form>}{projects.length === 0 ? <Empty title="Create your first project" detail="A project contains agent versions, suites, scenarios, and runs." /> : <div className="card-grid">{projects.map((project) => <Link className="project-card" href={`/projects/${project.id}`} key={project.id}><div className="project-mark">{project.name.slice(0, 2).toUpperCase()}</div><h2>{project.name}</h2><p>{project.description || "No description"}</p><footer><span>Created {new Date(project.created_at).toLocaleDateString()}</span><b>Open →</b></footer></Link>)}</div>}</>;
}
