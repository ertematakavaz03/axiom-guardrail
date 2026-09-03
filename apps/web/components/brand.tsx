import Image from "next/image";
import Link from "next/link";

export function Brand({ className = "" }: { className?: string }) {
  return (
    <Link className={`brand ${className}`.trim()} href="/dashboard" aria-label="Axiom Guardrail dashboard">
      <span className="brand-mark" aria-hidden="true">
        <Image src="/brand/axiom-mark.png" alt="" width={48} height={48} priority />
      </span>
      <span className="brand-copy">
        <strong className="brand-name"><b>AXIOM</b> <span>GUARDRAIL</span></strong>
        <small>AI AGENT RELEASE CONTROL</small>
      </span>
    </Link>
  );
}
