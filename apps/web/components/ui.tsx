import Link from "next/link";
import type { ReactNode } from "react";

export function PageHeader({ eyebrow, title, description, action }: { eyebrow?: string; title: string; description?: string; action?: ReactNode }) {
  return <header className="page-header"><div>{eyebrow && <div className="eyebrow">{eyebrow}</div>}<h1>{title}</h1>{description && <p>{description}</p>}</div>{action}</header>;
}
export function MetricCard({ label, value, detail, tone = "neutral" }: { label: string; value: string | number; detail?: string; tone?: string }) {
  return <div className={`metric-card tone-${tone}`}><span>{label}</span><strong>{value}</strong>{detail && <small>{detail}</small>}</div>;
}
export function Empty({ title, detail }: { title: string; detail: string }) {
  return <div className="empty"><div className="empty-mark">+</div><h3>{title}</h3><p>{detail}</p></div>;
}
export function Breadcrumbs({ items }: { items: { label: string; href?: string }[] }) {
  return <nav className="breadcrumbs" aria-label="Breadcrumb">{items.map((item, i) => <span key={`${item.label}-${i}`}>{i > 0 && <b>/</b>}{item.href ? <Link href={item.href}>{item.label}</Link> : item.label}</span>)}</nav>;
}
export function ErrorNotice({ message }: { message: string | null }) {
  return message ? <div className="error-notice">{message}</div> : null;
}
export function Loading() { return <div className="loading"><span /><span /><span /> Loading evidence…</div>; }

