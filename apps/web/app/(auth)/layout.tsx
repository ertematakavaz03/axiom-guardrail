export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return <main className="auth-shell"><section className="auth-story"><div className="brand auth-brand"><span className="brand-mark">A</span><span><strong>AgentArena</strong><small>QUALITY &amp; SECURITY</small></span></div><div><p className="eyebrow">SHIP WITH EVIDENCE</p><h1>Know exactly how your agent behaves before your users do.</h1><p>Trace every decision, enforce tool policy, and turn deterministic evidence into release verdicts.</p></div><blockquote>“Ship agents with evidence, not hope.”</blockquote></section>{children}</main>;
}

