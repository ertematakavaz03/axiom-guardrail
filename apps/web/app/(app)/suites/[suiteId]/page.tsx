"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { Breadcrumbs, Empty, ErrorNotice, Loading, PageHeader } from "@/components/ui";
import { api } from "@/lib/api";
import type { Agent, AgentVersion, Corpus, RagConfig, Run, Scenario, Suite } from "@/lib/types";

type VersionOption = AgentVersion & { agentName: string };

export default function SuiteDetailPage() {
  const { suiteId } = useParams<{ suiteId: string }>();
  const router = useRouter();
  const [suite, setSuite] = useState<Suite | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[] | null>(null);
  const [versions, setVersions] = useState<VersionOption[]>([]);
  const [corpora, setCorpora] = useState<Corpus[]>([]);
  const [configs, setConfigs] = useState<RagConfig[]>([]);
  const [open, setOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const load = useCallback(async () => {
    try {
      const nextSuite = await api<Suite>(`/suites/${suiteId}`);
      const [nextScenarios, agents, nextCorpora, nextConfigs] = await Promise.all([
        api<Scenario[]>(`/suites/${suiteId}/scenarios`),
        api<Agent[]>(`/projects/${nextSuite.project_id}/agents`),
        api<Corpus[]>(`/projects/${nextSuite.project_id}/corpora`),
        api<RagConfig[]>(`/projects/${nextSuite.project_id}/rag-configs`),
      ]);
      const allVersions = (await Promise.all(agents.map(async (agent) =>
        (await api<AgentVersion[]>(`/agents/${agent.id}/versions`)).map((version) => ({ ...version, agentName: agent.name })),
      ))).flat();
      setSuite(nextSuite);
      setScenarios(nextScenarios);
      setVersions(allVersions);
      setCorpora(nextCorpora);
      setConfigs(nextConfigs);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to load suite");
    }
  }, [suiteId]);

  useEffect(() => {
    // Loading remote application state is the synchronization purpose of this effect.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    void load();
  }, [load]);

  async function addScenario(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const data = new FormData(event.currentTarget);
    const csv = (name: string) => String(data.get(name) ?? "").split(",").map((value) => value.trim()).filter(Boolean);
    try {
      const ragEnabled = data.get("rag_enabled") === "on";
      const filters = String(data.get("retrieval_filters") || "{}");
      await api(`/suites/${suiteId}/scenarios`, {
        method: "POST",
        body: JSON.stringify({
          name: data.get("name"), input: data.get("input"), expected_output: data.get("expected_output") || null,
          expected_tools: csv("expected_tools"), forbidden_tools: csv("forbidden_tools"), expected_tool_arguments: null,
          tags: csv("tags"), severity: data.get("severity"), timeout_seconds: 30,
          metadata: { output_match: "contains", rag_enabled: ragEnabled, retrieval_filters: ragEnabled ? JSON.parse(filters) : {} },
        }),
      });
      setOpen(false);
      await load();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to add scenario");
    }
  }

  async function startRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!suite) return;
    setRunning(true);
    const data = new FormData(event.currentTarget);
    const corpusId = String(data.get("corpus_id") || "");
    const ragConfigId = String(data.get("rag_config_id") || "");
    try {
      const run = await api<Run>("/runs", {
        method: "POST",
        body: JSON.stringify({
          project_id: suite.project_id,
          test_suite_id: suite.id,
          agent_version_id: data.get("agent_version_id"),
          corpus_id: corpusId || null,
          rag_config_id: ragConfigId || null,
        }),
      });
      router.push(`/runs/${run.id}`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Unable to queue run");
      setRunning(false);
    }
  }

  if (!suite || !scenarios) return <Loading />;
  const hasRagCases = scenarios.some((scenario) => scenario.metadata.rag_enabled === true);

  return <>
    <Breadcrumbs items={[{ label: "Projects", href: "/projects" }, { label: "Suites", href: `/projects/${suite.project_id}/suites` }, { label: suite.name }]} />
    <PageHeader eyebrow={`SUITE · VERSION ${suite.version}`} title={suite.name} description={suite.description || "Golden deterministic evaluation suite"} action={<button className="button" onClick={() => setOpen(!open)}>+ Add scenario</button>} />
    <ErrorNotice message={error} />
    <section className="run-launch panel"><div><span className="eyebrow">EVALUATE IMMUTABLE VERSION</span><h2>Start a new run</h2><p>{scenarios.length} scenarios will be snapshotted before queueing.{hasRagCases ? " This suite requires a corpus and retrieval config." : ""}</p></div><form className={hasRagCases ? "rag-run-form" : ""} onSubmit={startRun}><select name="agent_version_id" required defaultValue=""><option value="" disabled>Select agent version</option>{versions.map((version) => <option value={version.id} key={version.id}>{version.agentName} · {version.version} · {version.adapter_type}</option>)}</select>{hasRagCases && <><select name="corpus_id" required defaultValue=""><option value="" disabled>Select corpus snapshot</option>{corpora.map((corpus) => <option value={corpus.id} key={corpus.id}>{corpus.name} · v{corpus.version}</option>)}</select><select name="rag_config_id" required defaultValue=""><option value="" disabled>Select retrieval config</option>{configs.map((config) => <option value={config.id} key={config.id}>{config.name}</option>)}</select></>}<button className="button primary" disabled={running || versions.length === 0}>{running ? "Queueing…" : "Run evaluation →"}</button></form></section>
    {open && <form className="panel form-grid" onSubmit={addScenario}><label>Name<input name="name" placeholder="Refund requires confirmation" required /></label><label>Severity<select name="severity" defaultValue="medium"><option>low</option><option>medium</option><option>high</option><option>critical</option></select></label><label className="wide">User input<textarea name="input" placeholder="Can customers request a refund after 30 days?" required /></label><label>Expected tools<input name="expected_tools" placeholder="get_order" /></label><label>Forbidden tools<input name="forbidden_tools" placeholder="refund_order" /></label><label>Expected response contains<input name="expected_output" placeholder="Optional deterministic text" /></label><label>Tags<input name="tags" placeholder="rag, citation" /></label><label className="checkbox-label"><input name="rag_enabled" type="checkbox" />Enable RAG evaluation</label><label className="wide">Retrieval filters (JSON)<input name="retrieval_filters" defaultValue="{}" /></label><button className="button primary">Add golden scenario</button></form>}
    <section className="panel spaced"><div className="panel-title"><div><span className="eyebrow">SCENARIO INVENTORY</span><h2>{scenarios.length} scenarios</h2></div></div>{scenarios.length === 0 ? <Empty title="No scenarios" detail="Add a golden case before starting a run." /> : <div className="scenario-list">{scenarios.map((scenario, index) => <article key={scenario.id}><span className={`severity severity-${scenario.severity}`}>{scenario.severity}</span><div><small>CASE {String(index + 1).padStart(2, "0")}</small><h3>{scenario.name}</h3><p>{scenario.input}</p><footer><span>Expected: {scenario.expected_tools.join(", ") || "no required tool"}</span>{scenario.metadata.rag_enabled === true && <span>RAG evidence required</span>}{scenario.forbidden_tools.length > 0 && <span className="danger">Forbidden: {scenario.forbidden_tools.join(", ")}</span>}</footer></div></article>)}</div>}</section>
  </>;
}
