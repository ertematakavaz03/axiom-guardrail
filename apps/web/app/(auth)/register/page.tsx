"use client";

import Link from "next/link";
import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import { api, saveSession } from "@/lib/api";
import { ErrorNotice } from "@/components/ui";

export default function RegisterPage() {
  const router = useRouter(); const [error, setError] = useState<string | null>(null); const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError(null); const data = new FormData(event.currentTarget);
    try { const response = await api<{ access_token: string }>("/auth/register", { method: "POST", body: JSON.stringify({ organization_name: data.get("organization"), email: data.get("email"), password: data.get("password") }) }); saveSession(response.access_token); router.push("/projects"); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Unable to create workspace"); } finally { setBusy(false); }
  }
  return <section className="auth-card"><div className="eyebrow">START EVALUATING</div><h2>Create your workspace</h2><p>Your first organization and owner account are created together.</p><ErrorNotice message={error} /><form onSubmit={submit}><label>Organization<input name="organization" placeholder="Acme Engineering" required minLength={2} /></label><label>Email<input name="email" type="email" placeholder="you@company.com" required autoComplete="email" /></label><label>Password<input name="password" type="password" placeholder="At least 10 characters" required minLength={10} autoComplete="new-password" /></label><button className="button primary" disabled={busy}>{busy ? "Creating…" : "Create workspace"}</button></form><footer>Already have an account? <Link href="/login">Sign in</Link></footer></section>;
}

