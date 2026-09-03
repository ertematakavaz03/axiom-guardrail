"use client";

import Link from "next/link";
import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams } from "next/navigation";
import { StatusBadge } from "@/components/status-badge";
import { Breadcrumbs, Empty, ErrorNotice, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { Corpus, DocumentRecord, RagConfig, RetrievalHit, RetrievalResult } from "@/lib/types";

function score(value: number | null) {
  return value === null ? "—" : value.toFixed(4);
}

function HitColumn({ title, detail, hits, scoreKey }: { title: string; detail: string; hits: RetrievalHit[]; scoreKey: "dense_score" | "sparse_score" | "hybrid_score" | "rerank_score" }) {
  return <section className="retrieval-stage"><header><span>{title}</span><small>{detail}</small></header>{hits.length === 0 ? <p className="muted-copy">No candidates</p> : hits.map((hit, index) => <article key={`${title}-${hit.chunk_id}`}><div><b>#{index + 1}</b><code>{score(hit[scoreKey])}</code></div><strong>{hit.document_name || hit.document_id.slice(0, 8)}</strong><p>{hit.content}</p><small>chunk {hit.chunk_id.slice(0, 8)} · v{String(hit.metadata.version ?? "—")}</small></article>)}</section>;
}

export default function CorpusDetailPage() {
  const { corpusId } = useParams<{ corpusId: string }>();
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [documents, setDocuments] = useState<DocumentRecord[] | null>(null);
  const [configs, setConfigs] = useState<RagConfig[]>([]);
  const [result, setResult] = useState<RetrievalResult | null>(null);
  const [showIngest, setShowIngest] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      const nextCorpus = await api<Corpus>(`/corpora/${corpusId}`);
      const [nextDocuments, nextConfigs] = await Promise.all([
        api<DocumentRecord[]>(`/corpora/${corpusId}/documents`),
        api<RagConfig[]>(`/projects/${nextCorpus.project_id}/rag-configs`),
      ]);
      setCorpus(nextCorpus);
      setDocuments(nextDocuments);
      setConfigs(nextConfigs);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load corpus");
    }
  }, [corpusId]);

  useEffect(() => {
    // Loading remote application state is the synchronization purpose of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function ingest(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const form = event.currentTarget;
    const data = new FormData(form);
    const upload = data.get("file");
    const sourceType = String(data.get("source_type"));
    try {
      if (upload instanceof File && upload.size > 0) {
        const body = new FormData();
        body.set("file", upload);
        body.set("source_type", sourceType);
        body.set("trust_level", String(data.get("trust_level")));
        body.set("metadata", JSON.stringify({ locale: data.get("locale") || "en" }));
        if (data.get("effective_date")) body.set("effective_date", String(data.get("effective_date")));
        await api(`/corpora/${corpusId}/upload`, { method: "POST", body });
      } else {
        await api(`/corpora/${corpusId}/ingest`, {
          method: "POST",
          body: JSON.stringify({
            name: data.get("name"),
            source_type: sourceType,
            content: data.get("content"),
            mime_type: sourceType === "markdown" ? "text/markdown" : "text/plain",
            effective_date: data.get("effective_date") ? new Date(String(data.get("effective_date"))).toISOString() : null,
            trust_level: data.get("trust_level"),
            metadata: { locale: data.get("locale") || "en" },
          }),
        });
      }
      form.reset();
      setShowIngest(false);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to ingest document");
    } finally {
      setBusy(false);
    }
  }

  async function retrieve(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    const data = new FormData(event.currentTarget);
    try {
      const filters = String(data.get("filters") || "{}");
      const parsed = JSON.parse(filters) as Record<string, unknown>;
      const next = await api<RetrievalResult>(`/corpora/${corpusId}/retrieve`, {
        method: "POST",
        body: JSON.stringify({ query: data.get("query"), filters: parsed, top_k: Number(data.get("top_k")), rag_config_id: data.get("rag_config_id") || null }),
      });
      setResult(next);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to retrieve evidence");
    } finally {
      setBusy(false);
    }
  }

  if (!corpus || !documents) return <Loading />;

  const hybridHits = result ? [...result.hits].sort((a, b) => (b.hybrid_score ?? 0) - (a.hybrid_score ?? 0)) : [];
  return <>
    <Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: "RAG corpora", href: `/projects/${corpus.project_id}/corpora` }, { label: corpus.name }]} />
    <PageHeader eyebrow={`CORPUS · VERSION ${corpus.version}`} title={corpus.name} description={corpus.description || "Versioned retrieval evidence"} action={<div className="button-row"><StatusBadge value={corpus.status} /><button className="button primary" onClick={() => setShowIngest(!showIngest)}>+ Ingest</button></div>} />
    <ErrorNotice message={error} />
    <div className="summary-strip"><div><strong>{documents.length}</strong><span>Documents</span></div><div><strong>{documents.reduce((total, document) => total + (document.current_version ? 1 : 0), 0)}</strong><span>Indexed versions</span></div><div><strong>{configs.length}</strong><span>Retrieval configs</span></div><div><strong>{corpus.version}</strong><span>Corpus version</span></div></div>
    {showIngest && <form className="panel form-grid ingest-form" onSubmit={ingest}><label>Name for pasted content<input name="name" placeholder="refund_policy.md" /></label><label>Source type<select name="source_type" defaultValue="markdown"><option value="text">Plain text</option><option value="markdown">Markdown</option><option value="pdf">Text-based PDF</option></select></label><label>Effective date<input name="effective_date" type="datetime-local" /></label><label>Trust level<select name="trust_level" defaultValue="standard"><option value="low">Low</option><option value="standard">Standard</option><option value="trusted">Trusted</option></select></label><label>Locale<input name="locale" defaultValue="en" /></label><label>Upload .txt, .md, or .pdf<input name="file" type="file" accept=".txt,.md,.pdf,text/plain,text/markdown,application/pdf" /></label><label className="wide">Or paste text / Markdown<textarea name="content" placeholder="Refund requests must be submitted within 14 days." /></label><p className="form-hint wide">Choose a file or provide a name and pasted body. Scanned PDFs without extractable text are rejected.</p><button className="button primary" disabled={busy}>{busy ? "Indexing…" : "Parse, chunk & index"}</button></form>}
    <section className="panel">
      <div className="panel-title"><div><span className="eyebrow">DOCUMENT INVENTORY</span><h2>{documents.length} sources</h2></div></div>
      {documents.length === 0 ? <Empty title="No evidence yet" detail="Ingest a text, Markdown, or text-based PDF document." /> : <div className="document-list">{documents.map((document) => <Link href={`/documents/${document.id}`} key={document.id}><div><strong>{document.name}</strong><small>{document.source_type} · {document.mime_type || "unknown MIME"}</small></div><span>v{document.current_version}</span><StatusBadge value={document.status} /><b>Inspect chunks →</b></Link>)}</div>}
    </section>
    <section className="panel spaced retrieval-debugger">
      <div className="panel-title"><div><span className="eyebrow">RETRIEVAL DEBUGGER</span><h2>Dense + sparse → RRF → rerank</h2></div>{result && <span>{result.candidate_count} candidates · {result.timings.total_ms} ms</span>}</div>
      <form className="debug-form" onSubmit={retrieve}><label>Question<input name="query" placeholder="Can customers request a refund after 30 days?" required /></label><label>Config<select name="rag_config_id" defaultValue=""><option value="">Default project config</option>{configs.map((config) => <option key={config.id} value={config.id}>{config.name}</option>)}</select></label><label>Top K<input name="top_k" type="number" min="1" max="50" defaultValue="5" /></label><label>Metadata filters (JSON)<input name="filters" defaultValue="{}" /></label><button className="button primary" disabled={busy}>{busy ? "Retrieving…" : "Retrieve evidence"}</button></form>
      {result && <><div className="timing-strip">{Object.entries(result.timings).map(([name, value]) => <div key={name}><span>{name.replaceAll("_", " ")}</span><b>{value} ms</b></div>)}</div><div className="retrieval-pipeline"><HitColumn title="Dense" detail={`${result.dense_hits.length} vector hits`} hits={result.dense_hits} scoreKey="dense_score" /><HitColumn title="Sparse" detail={`${result.sparse_hits.length} lexical hits`} hits={result.sparse_hits} scoreKey="sparse_score" /><HitColumn title="Hybrid / RRF" detail={`${hybridHits.length} fused hits`} hits={hybridHits} scoreKey="hybrid_score" /><HitColumn title="Reranked" detail={`${result.hits.length} final evidence`} hits={result.hits} scoreKey="rerank_score" /></div></>}
    </section>
  </>;
}
