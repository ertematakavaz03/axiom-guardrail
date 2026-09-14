import type { Evaluation, SecurityEvaluation, SecurityMetrics, SecurityRate, Trace } from "@/lib/types";
import { MetricCard } from "@/components/ui";

function rate(value: SecurityRate) {
  return value.rate === null ? "N/A" : `${(value.rate * 100).toFixed(1)}%`;
}

export function SecuritySummary({ metrics }: { metrics: SecurityMetrics }) {
  return <section className="panel spaced security-summary">
    <div className="panel-title"><div><span className="eyebrow">SECURITY / RED TEAM</span><h2>Attack outcomes</h2></div><span>{metrics.attacks_executed} attacks · {metrics.benign_controls} controls</span></div>
    <div className="metric-grid compact">
      <MetricCard label="Attack success" value={rate(metrics.attack_success_rate)} detail={`${metrics.attack_success_rate.numerator}/${metrics.attack_success_rate.denominator} resolved attacks`} tone="block" />
      <MetricCard label="Attack detection" value={rate(metrics.attack_detection_rate)} detail={`${metrics.detected_cases} attack cases with findings`} />
      <MetricCard label="Prevention" value={rate(metrics.prevention_rate)} detail={`${metrics.prevented_cases} cases with execution-path blocks`} />
      <MetricCard label="Manual review" value={metrics.manual_review} detail="Incomplete or inconclusive evidence" />
    </div>
    <p>{metrics.denominator_notes}</p>
    <p>Benign control false positives: {rate(metrics.benign_control_false_positive_rate)} ({metrics.benign_control_false_positive_rate.numerator}/{metrics.benign_control_false_positive_rate.denominator} controls).</p>
    {!!metrics.execution_failures && <p role="alert">{metrics.execution_failures} cases failed during execution and require review. Cases without a security evaluation are excluded from the attack rates above.</p>}
    <div className="summary-strip"><div><strong>{metrics.outcomes.ATTACK_SUCCEEDED ?? 0}</strong><span>Succeeded</span></div><div><strong>{metrics.outcomes.ATTACK_BLOCKED ?? 0}</strong><span>Blocked</span></div><div><strong>{metrics.outcomes.ATTACK_FAILED ?? 0}</strong><span>Failed</span></div><div><strong>{metrics.detected_only_cases}</strong><span>Detected only</span></div></div>
    <div className="security-columns"><div><h3>Category coverage</h3><table><thead><tr><th>Category</th><th>Attacks</th><th>Success</th><th>Blocked</th></tr></thead><tbody>{Object.entries(metrics.category_breakdown).map(([name, item]) => <tr key={name}><td>{name.replaceAll("_", " ")}</td><td>{item.executed}</td><td>{rate(item.success)}</td><td>{item.blocked}</td></tr>)}</tbody></table></div><div><h3>Finding severity</h3>{Object.entries(metrics.severity_distribution).map(([name, count]) => <p key={name}><strong>{name}</strong> · {count}</p>)}<h3>Top reason codes</h3>{Object.entries(metrics.reason_codes).sort((a, b) => b[1] - a[1]).slice(0, 8).map(([name, count]) => <p key={name}><code>{name}</code> · {count}</p>)}</div></div>
    <p className="muted">A gate verdict does not prove that an attack succeeded. These tests do not certify security or guarantee production safety.</p>
  </section>;
}

export function SecurityCaseEvidence({ evaluations, traces }: { evaluations: Evaluation[]; traces: Trace[] }) {
  const summary = evaluations.find(item => item.metric === "security_summary");
  if (!summary) return null;
  const result = summary.evidence.evaluation as unknown as SecurityEvaluation;
  const scenario = summary.evidence.scenario as Record<string, unknown>;
  return <section className="panel spaced security-case">
    <div className="panel-title"><div><span className="eyebrow">ATTACK EVIDENCE</span><h2>{String(scenario.name)}</h2></div><strong>{result.outcome}</strong></div>
    <p>{result.category.replaceAll("_", " ")} · {result.mode} · {result.evidence_complete ? "Complete evidence" : "Manual review required"}</p>
    <div className="security-columns"><div><h3>Attack input</h3><pre>{String(scenario.input)}</pre></div><div><h3>Expected safe behavior</h3><p>{String(scenario.expected_safe_behavior)}</p><h3>Protected synthetic values</h3><pre>{JSON.stringify(scenario.synthetic_sensitive_data, null, 2)}</pre></div></div>
    <p>Evidence SHA-256: <code>{result.evidence_hash}</code></p>
    {result.findings.length === 0 && <p>No deterministic security finding in this case.</p>}
    {result.findings.map((finding, index) => <article className="security-finding" key={`${finding.reason_code}-${index}`}>
      <div><strong>{finding.reason_code}</strong><span>{finding.severity} · {finding.handling}</span></div>
      <p>{finding.description}</p><p>Unsafe consequence observed: {finding.consequence ? "Yes" : "No"}</p>
      <p><strong>Remediation:</strong> {finding.remediation}</p>
      <details><summary>Trace evidence · {finding.event_ids.join(", ")}</summary><pre>{JSON.stringify(finding.evidence, null, 2)}</pre>{traces.filter(trace => finding.event_ids.includes(trace.name ?? "")).map(trace => <pre key={trace.id}>{JSON.stringify(trace.payload, null, 2)}</pre>)}</details>
    </article>)}
    <details><summary>Policy and trusted context</summary><pre>{JSON.stringify(summary.evidence.policy, null, 2)}</pre>{traces.filter(t => t.name === "application_scope").map(t => <pre key={t.id}>{JSON.stringify(t.payload, null, 2)}</pre>)}</details>
  </section>;
}
