import type { Verdict } from "@/lib/types";

export function StatusBadge({ value }: { value: Verdict | string }) {
  const normalized = value === "completed" ? "pass" : (value ?? "queued");
  return <span className={`badge badge-${normalized}`}>{String(value ?? "pending").toUpperCase()}</span>;
}

