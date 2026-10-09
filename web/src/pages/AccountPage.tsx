import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type AccountDetail, type Classification, type LLMOutput, type OutreachDraft } from "../api";
import { Chip, ErrorNote, Meter, Section, Star, TierBadge } from "../components/ui";
import { decodeEntities, label, num, timeAgo } from "../format";

function Provenance({ out }: { out: LLMOutput<unknown> }) {
  const offline = out.provider === "offline";
  return (
    <div className={`provenance ${offline ? "offline" : ""}`}>
      {offline ? "Rules fallback (no LLM)" : "AI"} · {out.model} · prompt {out.prompt_version} · {timeAgo(out.created_at)}
      {out.note ? <> · <span title={out.note}>fell back: {out.note.split(":")[0]}</span></> : null}
    </div>
  );
}

function ClassificationCard({ out }: { out: LLMOutput<Classification> }) {
  const c = out.output;
  return (
    <div className="ai-block">
      <div className="kv-row">
        <div><span>Entity</span><strong>{label(c.entity_type)}</strong></div>
        <div><span>Prospect?</span><strong className={c.is_prospect ? "ok" : "bad"}>{c.is_prospect ? "Yes" : "No"}</strong></div>
        <div><span>Industry</span><strong>{label(c.industry)}</strong></div>
        <div><span>Confidence</span><strong>{c.confidence}</strong></div>
      </div>
      <p className="muted">{c.rationale}</p>
      <Provenance out={out} />
    </div>
  );
}

function OutreachCard({ out, onRef }: { out: LLMOutput<OutreachDraft>; onRef: (ref: string) => void }) {
  const d = out.output;
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    await navigator.clipboard.writeText(`Subject: ${d.email.subject}\n\n${d.email.body}`);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="ai-block">
      {out.checks && (
        <div className={`guard ${out.checks.passed ? "pass" : "fail"}`}>
          {out.checks.passed ? "✓ Guardrails passed: every claim traces to evidence, no breach language, no IPs"
            : <>⚠ Review before sending: {out.checks.issues.join("; ")}</>}
        </div>
      )}
      <p className="summary">{d.summary}</p>
      <h3>Why now</h3>
      <ul className="why-list">
        {d.why_now.map((w, i) => (
          <li key={i}>{w.point} {w.evidence.map((r) => (
            <button key={r} className="ref" onClick={() => onRef(r)} title="Jump to evidence">{r}</button>))}</li>
        ))}
      </ul>
      <h3>Talk track</h3>
      <ul>{d.talk_track.map((t, i) => <li key={i}>{t}</li>)}</ul>
      <div className="email">
        <div className="email-head">
          <div><span className="muted">Subject</span> <strong>{d.email.subject}</strong></div>
          <button className="btn small" onClick={copy}>{copied ? "Copied" : "Copy email"}</button>
        </div>
        <pre>{d.email.body}</pre>
      </div>
      <Provenance out={out} />
    </div>
  );
}

export function AccountPage() {
  const { id } = useParams();
  const accountId = Number(id);
  const [d, setD] = useState<AccountDetail | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState<"classify" | "outreach" | null>(null);
  const [highlight, setHighlight] = useState<string | null>(null);

  useEffect(() => { api.account(accountId).then(setD).catch(setError); }, [accountId]);

  if (error) return <main className="page"><ErrorNote error={error} /></main>;
  if (!d) return <main className="page"><p className="muted">Loading…</p></main>;
  const a = d.account;

  const run = async (kind: "classify" | "outreach", refresh: boolean) => {
    setBusy(kind);
    try {
      if (kind === "classify") {
        const out = await api.classify(accountId, refresh);
        const fresh = await api.account(accountId);  // classification can change industry, fit and tier
        setD({ ...fresh, classification: out });
      } else {
        const out = await api.outreach(accountId, refresh);
        setD({ ...d, outreach: out });
      }
    } catch (e) { setError(e); } finally { setBusy(null); }
  };
  const jumpTo = (ref: string) => {
    setHighlight(ref);
    document.getElementById(`ev-${ref}`)?.scrollIntoView({ behavior: "smooth", block: "center" });
  };
  const toggleShortlist = async () => { await api.shortlist(accountId, !d.shortlisted); setD({ ...d, shortlisted: !d.shortlisted }); };

  return (
    <main className="page">
      <Link to="/" className="back">← Prospects</Link>
      <header className="acct-header">
        <div>
          <h1><TierBadge tier={a.tier} /> {a.name} <Star on={d.shortlisted} onClick={toggleShortlist} /></h1>
          <p className="sub">
            {a.country} · {label(a.industry)}
            {a.industry_source === "llm" && <span className="ai-tag">AI</span>} · {label(a.segment)} · {label(a.hosting)}
            · identified via {label(a.key_source)}
            {!a.is_prospect && <span className="warn-tag">Not a prospect ({label(a.entity_type)})</span>}
          </p>
        </div>
        <div className="meters">
          <Meter value={a.priority} label="priority" />
          <Meter value={a.need} label="need" />
          <Meter value={a.fit} label="fit" />
        </div>
      </header>

      <div className="grid">
        <div className="col">
          <Section title="Why now" aside={<span className="muted">rule-based · deterministic</span>}>
            <ul className="reasons">
              {d.reasons.map((r) => (
                <li key={r.signal}>
                  <Chip category={r.category}>{r.label}</Chip>
                  <span>{r.text}</span>
                  <span className="pts" title="Need points contributed">+{r.points}</span>
                </li>
              ))}
            </ul>
          </Section>

          <Section title={`Evidence (${d.evidence.length} of ${d.evidence_total} flagged assets)`}>
            <div className="table-wrap">
              <table className="evidence">
                <thead><tr><th>Ref</th><th>Service</th><th>What we saw</th><th>Vulnerabilities</th></tr></thead>
                <tbody>
                  {d.evidence.map((e) => (
                    <tr key={e.ref} id={`ev-${e.ref}`} className={highlight === e.ref ? "hl" : ""}>
                      <td><span className="ref static">{e.ref}</span></td>
                      <td className="mono">{e.ip}:{e.port}<div className="acct-meta">{[e.product, e.version].filter(Boolean).join(" ") || e.module}</div></td>
                      <td>
                        {e.http_title && <div className="clip" title={decodeEntities(e.http_title)}>“{decodeEntities(e.http_title)}”</div>}
                        <div className="chips">{e.flag_labels.map((l) => <Chip key={l}>{l}</Chip>)}</div>
                      </td>
                      <td>
                        {e.n_cves > 0 ? (
                          <>
                            {e.kev_count > 0 && <span className="kev">CISA KEV</span>}
                            <span className="mono small">CVSS {e.max_cvss ?? "—"} · EPSS {e.max_epss != null ? `${Math.round(e.max_epss * 100)}%` : "—"}</span>
                            <div className="acct-meta">{e.cves.slice(0, 3).join(", ")}{e.n_cves > 3 ? ` +${e.n_cves - 3} more` : ""}</div>
                          </>
                        ) : <span className="muted">—</span>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>
        </div>

        <div className="col">
          <Section title="Brief & first-touch email" aside={
            <button className="btn primary small" disabled={busy !== null} onClick={() => run("outreach", !!d.outreach)}>
              {busy === "outreach" ? "Writing…" : d.outreach ? "Regenerate" : "Generate"}
            </button>}>
            {d.outreach ? <OutreachCard out={d.outreach} onRef={jumpTo} />
              : <p className="muted">Generates a 10-second brief, talk track and email grounded only in this account's evidence.</p>}
          </Section>

          <Section title="AI account check" aside={
            <button className="btn small" disabled={busy !== null} onClick={() => run("classify", !!d.classification)}>
              {busy === "classify" ? "Checking…" : d.classification ? "Re-run" : "Run check"}
            </button>}>
            {d.classification ? <ClassificationCard out={d.classification} />
              : <p className="muted">Checks whether this is a real prospect (vs. hosting/ISP noise) and infers the industry.</p>}
          </Section>

          <Section title="Account profile">
            <dl className="profile">
              <dt>Footprint</dt><dd>{num(a.n_ips)} IPs · {num(a.n_services)} services · {num(a.n_ports)} ports</dd>
              <dt>Hosted on</dt><dd>{a.hosting_orgs.join(", ") || "—"}</dd>
              {a.cert_orgs.length > 0 && <><dt>Certificate org</dt><dd>{a.cert_orgs.join(", ")}</dd></>}
              <dt>Products</dt><dd>{a.products.join(", ") || "—"}</dd>
              <dt>Sites</dt><dd>{a.hosts.filter((h) => !/^[\d.:]+$/.test(h)).slice(0, 5).join(", ") || "—"}</dd>
              <dt>WAF coverage</dt><dd>{a.waf_share != null ? `${Math.round(a.waf_share * 100)}% of web services` : "—"}</dd>
              <dt>security.txt</dt><dd>{a.has_security_txt ? "Published (has a security contact)" : "Not found"}</dd>
              <dt>Fit breakdown</dt>
              <dd>{Object.entries(a.fit_breakdown).map(([k, v]) => `${k} ${v}`).join(" × ")} = {a.fit}</dd>
              <dt>Last seen</dt><dd>{a.last_seen?.slice(0, 10)}</dd>
            </dl>
          </Section>
        </div>
      </div>
    </main>
  );
}
