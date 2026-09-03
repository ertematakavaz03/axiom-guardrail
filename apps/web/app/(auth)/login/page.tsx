"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api, saveSession } from "@/lib/api";
import { ErrorNotice } from "@/components/ui";

export default function LoginPage() {
  const router = useRouter(); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(null);
    const data = new FormData(event.currentTarget);
    try { const response = await api<{ access_token: string }>("/auth/login", { method: "POST", body: JSON.stringify({ email: data.get("email"), password: data.get("password") }) }); saveSession(response.access_token); router.push("/dashboard"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to sign in"); } finally { setBusy(false); }
  }
  return <section className="auth-card"><div className="eyebrow">WELCOME BACK</div><h2>Sign in to your workspace</h2><p>Continue investigating agent quality, safety, and release readiness.</p><ErrorNotice message={error} /><form onSubmit={submit}><label>Email<input name="email" type="email" placeholder="you@company.com" required autoComplete="email" /></label><label>Password<input name="password" type="password" placeholder="At least 10 characters" required autoComplete="current-password" /></label><button className="button primary" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button></form><footer>New to Axiom Guardrail? <Link href="/register">Create workspace</Link></footer></section>;
}
