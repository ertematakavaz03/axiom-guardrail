"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

export default function Home() {
  const router = useRouter();
  useEffect(() => {
    router.replace(localStorage.getItem("agentarena_token") ? "/dashboard" : "/login");
  }, [router]);
  return <main className="center-screen muted">Opening AgentArena…</main>;
}

