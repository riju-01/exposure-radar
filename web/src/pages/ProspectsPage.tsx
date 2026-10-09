import { useEffect, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { api, type AccountList, type Meta } from "../api";
import { FilterPanel } from "../components/FilterPanel";
import { Chip, Empty, ErrorNote, ScoreCell, Star, TierBadge } from "../components/ui";
import { label, num } from "../format";

const SORTS = [
  ["priority", "Priority"], ["need", "Need (urgency)"], ["fit", "ICP fit"], ["size", "Footprint size"], ["name", "Name"],
] as const;

export function ProspectsPage({ meta, shortlistOnly = false }: { meta: Meta; shortlistOnly?: boolean }) {
  const [params, setParams] = useSearchParams();
  const [data, setData] = useState<AccountList | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(false);
  const navigate = useNavigate();

  const query = new URLSearchParams(params);
  if (shortlistOnly) query.set("shortlisted", "true");
  if (!query.get("page_size")) query.set("page_size", "50");
  const queryKey = query.toString();

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.accounts(new URLSearchParams(queryKey))
      .then((d) => { if (!cancelled) { setData(d); setError(null); } })
      .catch((e) => !cancelled && setError(e))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [queryKey]);

  const page = Number(params.get("page") ?? 1);
  const pages = data ? Math.max(1, Math.ceil(data.total / data.page_size)) : 1;
  const signalByKey = Object.fromEntries(meta.facets.signals.map((s) => [s.key, s]));

  const setPage = (p: number) => { const n = new URLSearchParams(params); n.set("page", String(p)); setParams(n); };
  const toggleShortlist = async (id: number, on: boolean) => {
    await api.shortlist(id, on);
    setData((d) => d && { ...d, items: shortlistOnly && !on ? d.items.filter((i) => i.id !== id)
      : d.items.map((i) => (i.id === id ? { ...i, shortlisted: on } : i)) });
  };

  return (
    <div className="layout">
      <FilterPanel meta={meta} params={params} onChange={setParams} />
      <main className="main">
        <div className="toolbar">
          <div>
            <h1>{shortlistOnly ? "My shortlist" : "Prospects"}</h1>
            <p className="sub">
              {data ? <><strong>{num(data.total)}</strong> accounts</> : "Loading…"}
              {data && (["A", "B", "C"] as const).map((t) => data.tier_counts[t] ? (
                <span key={t} className="tier-count"><TierBadge tier={t} /> {num(data.tier_counts[t]!)}</span>) : null)}
            </p>
          </div>
          <div className="toolbar-actions">
            <label className="sort">Sort
              <select value={params.get("sort") ?? "priority"}
                onChange={(e) => { const n = new URLSearchParams(params); n.set("sort", e.target.value); n.delete("page"); setParams(n); }}>
                {SORTS.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <a className="btn" href={api.exportUrl(query)} download>Export CSV</a>
          </div>
        </div>

        {error ? <ErrorNote error={error} /> : null}
        {data && data.items.length === 0 && (
          <Empty>{shortlistOnly ? "No shortlisted accounts yet. Star accounts in the prospect list." : "No accounts match these filters."}</Empty>
        )}

        {data && data.items.length > 0 && (
          <div className={`table-wrap ${loading ? "loading" : ""}`}>
            <table className="accounts">
              <thead>
                <tr>
                  <th aria-label="Shortlist" />
                  <th>Tier</th>
                  <th>Account</th>
                  <th>Why now</th>
                  <th>Industry</th>
                  <th className="num">Footprint</th>
                  <th>Priority</th>
                </tr>
              </thead>
              <tbody>
                {data.items.map((a) => (
                  <tr key={a.id} onClick={() => navigate(`/accounts/${a.id}`)} className="clickable">
                    <td><Star on={a.shortlisted} onClick={() => toggleShortlist(a.id, !a.shortlisted)} /></td>
                    <td><TierBadge tier={a.tier} /></td>
                    <td>
                      <div className="acct-name">{a.name}</div>
                      <div className="acct-meta">{a.country ?? a.country_code} · {label(a.hosting)}</div>
                    </td>
                    <td className="why">
                      <div>{a.top_reason}</div>
                      <div className="chips">
                        {Object.entries(a.signals).sort((x, y) => (signalByKey[y[0]]?.weight ?? 0) - (signalByKey[x[0]]?.weight ?? 0))
                          .slice(0, 4).map(([k, n]) => (
                            <Chip key={k} category={signalByKey[k]?.category} title={`${n} affected`}>{signalByKey[k]?.label ?? k}</Chip>
                          ))}
                      </div>
                    </td>
                    <td>
                      {label(a.industry)}
                      {a.industry_source === "llm" && <span className="ai-tag" title="Classified by AI">AI</span>}
                    </td>
                    <td className="num">{num(a.n_ips)} IPs<div className="acct-meta">{label(a.segment)}</div></td>
                    <td><ScoreCell priority={a.priority} need={a.need} fit={a.fit} /></td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {data && pages > 1 && (
          <nav className="pager">
            <button className="btn ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>← Prev</button>
            <span>Page {page} of {num(pages)}</span>
            <button className="btn ghost" disabled={page >= pages} onClick={() => setPage(page + 1)}>Next →</button>
          </nav>
        )}
        <p className="legend">
          <span className="legend-need" /> need (how urgent) <span className="legend-fit" /> fit (ICP match).
          Priority = 0.6 × need + 0.4 × fit. Scores are rule-based and deterministic.
        </p>
      </main>
    </div>
  );
}
