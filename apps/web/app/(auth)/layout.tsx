import { Brand } from "@/components/brand";

export default function AuthLayout({ children }: { children: React.ReactNode }) {
  return <main className="auth-shell"><section className="auth-story"><Brand className="auth-brand" /><div><p className="eyebrow">EVIDENCE-DRIVEN RELEASE CONTROL</p><h1>Know exactly how your agent behaves before your users do.</h1><p>Trace every decision, enforce tool policy, and turn deterministic evidence into release verdicts.</p></div><blockquote>“Ship agents with evidence, not hope.”</blockquote></section>{children}</main>;
}
