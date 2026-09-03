"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { api, clearSession, token } from "@/lib/api";
import { Brand } from "@/components/brand";

const nav = [
  { href: "/dashboard", label: "Overview", code: "OV" },
  { href: "/projects", label: "Projects", code: "PR" },
  { href: "/runs", label: "Runs", code: "RN" },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname(); const router = useRouter();
  const [email, setEmail] = useState("");
  useEffect(() => {
    if (!token()) { router.replace("/login"); return; }
    api<{ email: string }>("/auth/me").then((me) => setEmail(me.email)).catch(() => router.replace("/login"));
  }, [router]);
  return <div className="app-shell">
    <aside className="sidebar">
      <Brand />
      <div className="workspace-label">WORKSPACE</div>
      <nav>{nav.map((item) => <Link key={item.href} href={item.href} className={pathname.startsWith(item.href) ? "active" : ""}><span>{item.code}</span>{item.label}</Link>)}</nav>
      <div className="sidebar-foot"><div className="environment"><i /> Sandbox mode</div><div className="account"><span>{email.slice(0, 2).toUpperCase() || "AA"}</span><div><strong>{email || "Loading…"}</strong><small>Owner</small></div></div><button onClick={() => { clearSession(); router.push("/login"); }}>Sign out</button></div>
    </aside>
    <main className="content">{children}</main>
  </div>;
}
