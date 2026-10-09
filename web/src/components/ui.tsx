import type { ReactNode } from "react";
import type { Tier } from "../api";

export function TierBadge({ tier }: { tier: Tier }) {
  const title = { A: "Work this week", B: "Nurture / sequence", C: "Monitor" }[tier];
  return <span className={`tier tier-${tier}`} title={title}>{tier}</span>;
}

/** Priority bar with need and fit shown as two thin bars underneath. */
export function ScoreCell({ priority, need, fit }: { priority: number; need: number; fit: number }) {
  return (
    <div className="score" title={`Priority ${priority} = 0.6 × need ${need} + 0.4 × fit ${fit}`}>
      <div className="score-num">{priority}</div>
      <div className="score-bars">
        <div className="bar"><span className="bar-need" style={{ width: `${need}%` }} /></div>
        <div className="bar"><span className="bar-fit" style={{ width: `${fit}%` }} /></div>
      </div>
    </div>
  );
}

const CATEGORY_CLASS: Record<string, string> = {
  sensitive: "chip-sensitive", vulnerability: "chip-vuln", exposure: "chip-exposure", hygiene: "chip-hygiene",
};

export function Chip({ children, category, title }: { children: ReactNode; category?: string; title?: string }) {
  return <span className={`chip ${category ? CATEGORY_CLASS[category] ?? "" : ""}`} title={title}>{children}</span>;
}

export function Meter({ value, label }: { value: number; label: string }) {
  return (
    <div className="meter">
      <svg viewBox="0 0 36 36" aria-hidden="true">
        <circle cx="18" cy="18" r="15.9" className="meter-track" />
        <circle cx="18" cy="18" r="15.9" className="meter-value" strokeDasharray={`${value} ${100 - value}`} />
      </svg>
      <div className="meter-text"><strong>{value}</strong><span>{label}</span></div>
    </div>
  );
}

export function Star({ on, onClick }: { on: boolean; onClick: () => void }) {
  return (
    <button className={`star ${on ? "on" : ""}`} onClick={(e) => { e.stopPropagation(); onClick(); }}
      aria-label={on ? "Remove from shortlist" : "Add to shortlist"} title={on ? "Shortlisted" : "Add to shortlist"}>
      {on ? "★" : "☆"}
    </button>
  );
}

export function Section({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <header className="card-head"><h2>{title}</h2>{aside}</header>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function ErrorNote({ error }: { error: unknown }) {
  return <div className="error">Something went wrong: {String(error)}</div>;
}
