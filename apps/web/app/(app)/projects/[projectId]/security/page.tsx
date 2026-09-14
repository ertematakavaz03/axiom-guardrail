"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { api } from "@/lib/api";
import type { Run, Agent, AgentVersion, Suite } from "@/lib/types";
import { Breadcrumbs, ErrorNotice, PageHeader } from "@/components/ui";
import { SecuritySummary } from "@/components/security-evidence";

interface Policy { id: string; name: string; policy_hash: string; policy: Record<string, unknown> }
interface Inventory { id: string; server: string; approved: boolean; inventory_hash: string; inventory: Record<string, unknown> }

export default function SecurityProjectPage() {
  const { projectId } = useParams<{ projectId: string }>();
  const router = useRouter();
  const [policies, setPolicies] = useState<Policy[]>([]);
  const [inventories, setInventories] = useState<Inventory[]>([]);
  const [runs, setRuns] = useState<Run[]>([]);
  const [suites, setSuites] = useState<Suite[]>([]);
  const [versions, setVersions] = useState<AgentVersion[]>([]);
  const [policyId, setPolicyId] = useState("");
  const [suiteId, setSuiteId] = useState("");
  const [versionId, setVersionId] = useState("");
  const [mode, setMode] = useState("observational");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    const [ps, inv, rs, ss, agents] = await Promise.all([
      api<Policy[]>(`/projects/${projectId}/security/policies`), api<Inventory[]>(`/projects/${projectId}/security/mcp`),
      api<Run[]>(`/runs?project_id=${projectId}`), api<Suite[]>(`/projects/${projectId}/suites`), api<Agent[]>(`/projects/${projectId}/agents`),
    ]);
    const vs = (await Promise.all(agents.map(agent => api<AgentVersion[]>(`/agents/${agent.id}/versions`)))).flat();
    setPolicies(ps); setInventories(inv); setRuns(rs); setSuites(ss); setVersions(vs);
    setPolicyId(current => current || ps[0]?.id || ""); setSuiteId(current => current || ss.find(s => s.name.includes("Security"))?.id || ""); setVersionId(current => current || vs.find(v => v.adapter_type === "demo_security_agent")?.id || "");
  }, [projectId]);
  useEffect(() => { const timer = setTimeout(() => { load().catch(err => setError(String(err))); }, 0); return () => clearTimeout(timer); }, [load]);
  async function install() {
    setBusy(true); setError(null);
    try { await api(`/projects/${projectId}/security/demo`, { method: "POST" }); await load(); }
    catch (err) { setError(String(err)); } finally { setBusy(false); }
  }
  async function launch() {
    setBusy(true); setError(null);
    try { const run = await api<Run>("/runs", { method: "POST", body: JSON.stringify({ project_id: projectId, test_suite_id: suiteId, agent_version_id: versionId, security_policy_id: policyId, security_mode: mode }) }); router.push(`/runs/${run.id}`); }
    catch (err) { setError(String(err)); } finally { setBusy(false); }
  }
  const latest = runs.find(run => run.metrics.security);
  return <>
    <Breadcrumbs items={[{ label: "Project", href: `/projects/${projectId}` }, { label: "Security" }]} />
    <PageHeader eyebrow="SECURITY / RED TEAM / MCP" title="Security evidence" description="Test agent boundaries, inspect findings, and distinguish detection from execution-path prevention." />
    <ErrorNotice message={error} />
    <section className="panel spaced"><div className="panel-title"><h2>Run a security suite</h2><button className="button" disabled={busy || suites.some(s => s.name === "Phase 3 Security Lab")} onClick={install}>Install local security demo</button></div>
      <p>The demo contains 65 attacks and 13 benign controls. All data and tool effects are synthetic and local.</p>
      <div className="security-launch">
        <label>Suite<select value={suiteId} onChange={e => setSuiteId(e.target.value)}><option value="">Select suite</option>{suites.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
        <label>Agent version<select value={versionId} onChange={e => { setVersionId(e.target.value); setMode("observational"); }}><option value="">Select version</option>{versions.map(v => <option key={v.id} value={v.id}>{v.version} · {v.adapter_type}</option>)}</select></label>
        <label>Security policy<select value={policyId} onChange={e => setPolicyId(e.target.value)}><option value="">Select policy</option>{policies.map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
        <label>Mode<select value={mode} onChange={e => setMode(e.target.value)}><option value="observational">Observational · detect only</option><option value="preventive" disabled={versions.find(v => v.id === versionId)?.adapter_type !== "demo_security_agent"}>Preventive · execution gateway</option></select></label>
      </div><button className="button primary" disabled={busy || !suiteId || !versionId || !policyId} onClick={launch}>{busy ? "Working…" : "Queue security run"}</button>
    </section>
    {latest?.metrics.security && <><p><Link href={`/runs/${latest.id}`}>Latest security run →</Link></p><SecuritySummary metrics={latest.metrics.security} /></>}
    <section className="panel spaced"><div className="panel-title"><h2>Policy snapshots</h2></div>{policies.map(p => <details key={p.id}><summary>{p.name}</summary><p>SHA-256: <code>{p.policy_hash}</code></p><pre>{JSON.stringify(p.policy, null, 2)}</pre></details>)}{!policies.length && <p>No security policies registered.</p>}</section>
    <section className="panel spaced"><div className="panel-title"><h2>MCP inventory history</h2></div>{inventories.map(item => <details key={item.id}><summary>{item.server} · {item.approved ? "Approved snapshot" : "Observed only"}</summary><p>SHA-256: <code>{item.inventory_hash}</code></p><pre>{JSON.stringify(item.inventory, null, 2)}</pre></details>)}{!inventories.length && <p>No MCP inventories registered.</p>}<p>New observations do not automatically replace approved inventories.</p></section>
  </>;
}
