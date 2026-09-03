"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { StatusBadge } from "@/components/status-badge";
import { Breadcrumbs, Empty, ErrorNotice, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { Corpus, Project, RagConfig } from "@/lib/types";

export default function CorporaPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const [project, setProject] = useState<Project | null>(null);
  const [corpora, setCorpora] = useState<Corpus[] | null>(null);
  const [configs, setConfigs] = useState<RagConfig[]>([]);
  const [showCorpusForm, setShowCorpusForm] = useState(false);
  const [showConfigForm, setShowConfigForm] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const [nextProject, nextCorpora, nextConfigs] = await Promise.all([
        api<Project>(`/projects/${projectId}`),
        api<Corpus[]>(`/projects/${projectId}/corpora`),
        api<RagConfig[]>(`/projects/${projectId}/rag-configs`),
      ]);
      setProject(nextProject);
      setCorpora(nextCorpora);
      setConfigs(nextConfigs);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load RAG resources");
    }
  }, [projectId]);

  useEffect(() => {
    // Loading remote application state is the synchronization purpose of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function createCorpus(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api(`/projects/${projectId}/corpora`, {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          description: data.get("description") || null,
          version: data.get("version"),
        }),
      });
      setShowCorpusForm(false);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create corpus");
    }
  }

  async function createConfig(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    try {
      await api(`/projects/${projectId}/rag-configs`, {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"),
          embedding_provider: "deterministic",
          embedding_model: "deterministic-hash-v1",
          dense_enabled: true,
          sparse_enabled: true,
          top_k_dense: Number(data.get("top_k_dense")),
          top_k_sparse: Number(data.get("top_k_sparse")),
          hybrid_top_k: Number(data.get("hybrid_top_k")),
          reranker_type: data.get("reranker_type"),
          rerank_top_n: Number(data.get("rerank_top_n")),
          metadata_filter_policy: { tenant_scope_required: true },
        }),
      });
      setShowConfigForm(false);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to create RAG config");
    }
  }

  if (!project || !corpora) return <Loading />;

  return (
    <>
      <Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: project.name, href: `/projects/${projectId}` }, { label: "RAG corpora" }]} />
      <PageHeader
        eyebrow="RAG KNOWLEDGE"
        title="Corpora & retrieval configs"
        description="Versioned evidence sources with deterministic, tenant-scoped retrieval."
        action={<div className="button-row"><button className="button" onClick={() => setShowConfigForm(!showConfigForm)}>+ Config</button><button className="button primary" onClick={() => setShowCorpusForm(!showCorpusForm)}>+ Corpus</button></div>}
      />
      <ErrorNotice message={error} />
      {showCorpusForm && (
        <form className="panel inline-form" onSubmit={createCorpus}>
          <label>Name<input name="name" placeholder="Company Policies" required /></label>
          <label>Version<input name="version" defaultValue="1" required /></label>
          <label>Description<input name="description" placeholder="Approved policy evidence" /></label>
          <button className="button primary">Create corpus</button>
        </form>
      )}
      {showConfigForm && (
        <form className="panel form-grid" onSubmit={createConfig}>
          <label>Name<input name="name" placeholder="Hybrid + rerank" required /></label>
          <label>Reranker<select name="reranker_type" defaultValue="token_overlap"><option value="token_overlap">Token overlap</option><option value="none">No reranker</option></select></label>
          <label>Dense candidates<input name="top_k_dense" type="number" min="1" max="100" defaultValue="20" /></label>
          <label>Sparse candidates<input name="top_k_sparse" type="number" min="1" max="100" defaultValue="20" /></label>
          <label>Hybrid candidates<input name="hybrid_top_k" type="number" min="1" max="100" defaultValue="10" /></label>
          <label>Rerank top N<input name="rerank_top_n" type="number" min="1" max="50" defaultValue="5" /></label>
          <button className="button primary">Save config</button>
        </form>
      )}
      <section className="panel">
        <div className="panel-title"><div><span className="eyebrow">CORPUS INVENTORY</span><h2>{corpora.length} corpora</h2></div></div>
        {corpora.length === 0 ? <Empty title="No corpora" detail="Create a corpus, then ingest text, Markdown, or text-based PDF evidence." /> : (
          <div className="corpus-grid">
            {corpora.map((corpus) => (
              <Link className="corpus-card" href={`/corpora/${corpus.id}`} key={corpus.id}>
                <header><div><span>v{corpus.version}</span><h3>{corpus.name}</h3></div><StatusBadge value={corpus.status} /></header>
                <p>{corpus.description || "No description"}</p>
                <dl><div><dt>Documents</dt><dd>{corpus.document_count ?? 0}</dd></div><div><dt>Chunks</dt><dd>{corpus.chunk_count ?? 0}</dd></div><div><dt>Embedding</dt><dd>{corpus.embedding_model || "—"}</dd></div><div><dt>Last ingest</dt><dd>{corpus.last_ingestion ? new Date(corpus.last_ingestion).toLocaleString() : "—"}</dd></div></dl>
              </Link>
            ))}
          </div>
        )}
      </section>
      <section className="panel spaced">
        <div className="panel-title"><div><span className="eyebrow">IMMUTABLE RUN INPUT</span><h2>Retrieval configs</h2></div><span>{configs.length} configured</span></div>
        <div className="config-list">
          {configs.map((config) => <article key={config.id}><div><strong>{config.name}</strong><small>{config.embedding_provider} / {config.embedding_model}</small></div><code>{config.dense_enabled ? "DENSE" : ""} {config.sparse_enabled ? "+ SPARSE" : ""}</code><span>top {config.hybrid_top_k} → rerank {config.rerank_top_n}</span><b>{config.reranker_type}</b></article>)}
        </div>
      </section>
    </>
  );
}
