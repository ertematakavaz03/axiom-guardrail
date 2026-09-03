"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { StatusBadge } from "@/components/status-badge";
import { Breadcrumbs, ErrorNotice, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { Corpus, DocumentChunk, DocumentRecord } from "@/lib/types";

export default function DocumentDetailPage() {
  const { documentId } = useParams<{ documentId: string }>();
  const [document, setDocument] = useState<DocumentRecord | null>(null);
  const [corpus, setCorpus] = useState<Corpus | null>(null);
  const [chunks, setChunks] = useState<DocumentChunk[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<DocumentRecord>(`/documents/${documentId}`)
      .then(async (nextDocument) => {
        const [nextCorpus, nextChunks] = await Promise.all([
          api<Corpus>(`/corpora/${nextDocument.corpus_id}`),
          api<DocumentChunk[]>(`/documents/${documentId}/chunks`),
        ]);
        setDocument(nextDocument);
        setCorpus(nextCorpus);
        setChunks(nextChunks);
      })
      .catch((caught: unknown) => setError(caught instanceof Error ? caught.message : "Unable to load document"));
  }, [documentId]);

  if (!document || !corpus || !chunks) return <Loading />;
  const versions = new Set(chunks.map((chunk) => chunk.document_version_id)).size;

  return <>
    <Breadcrumbs items={[{ label: "RAG corpora", href: `/projects/${document.project_id}/corpora` }, { label: corpus.name, href: `/corpora/${corpus.id}` }, { label: document.name }]} />
    <PageHeader eyebrow={`${document.source_type.toUpperCase()} DOCUMENT`} title={document.name} description="Deterministic chunk inventory and vector-store point mapping." action={<StatusBadge value={document.status} />} />
    <ErrorNotice message={error} />
    <div className="summary-strip"><div><strong>v{document.current_version}</strong><span>Current version</span></div><div><strong>{versions}</strong><span>Stored versions</span></div><div><strong>{chunks.length}</strong><span>Chunks</span></div><div><strong>{chunks.reduce((sum, chunk) => sum + (chunk.token_count ?? 0), 0)}</strong><span>Approx. tokens</span></div></div>
    <section className="panel">
      <div className="panel-title"><div><span className="eyebrow">EXTRACTED EVIDENCE</span><h2>Chunks & metadata</h2></div><code>{document.mime_type || "unknown MIME"}</code></div>
      <div className="chunk-list">{chunks.map((chunk) => <article key={chunk.id}><header><div><b>CHUNK {String(chunk.chunk_index + 1).padStart(2, "0")}</b><span>{chunk.section_title || "Untitled section"}</span></div><code>{chunk.id.slice(0, 8)}</code></header><p>{chunk.text}</p><footer><span>page {chunk.page_number ?? "—"}</span><span>{chunk.token_count ?? "—"} tokens</span><span>version {chunk.document_version_id.slice(0, 8)}</span><span>point {chunk.qdrant_point_id?.slice(0, 8) ?? "—"}</span></footer></article>)}</div>
    </section>
  </>;
}
