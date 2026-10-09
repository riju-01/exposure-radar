import { useEffect, useState } from "react";
import { api, type Ops } from "../api";
import { Empty, ErrorNote, Section } from "../components/ui";
import { num, timeAgo, usd } from "../format";

/** The "is the AI working, and what does it cost?" page: traces, budget, evals, cost model. */
export function OpsPage() {
  const [ops, setOps] = useState<Ops | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { api.ops().then(setOps).catch(setError); }, []);
  if (error) return <main className="page"><ErrorNote error={error} /></main>;
  if (!ops) return <main className="page"><p className="muted">Loading…</p></main>;

  const totalMonthly = ops.cost_model.rows.reduce((s, r) => s + r.monthly_usd, 0);
  const spentPct = Math.min(100, (ops.budget.spent_today_usd / ops.budget.daily_usd) * 100);

  return (
    <main className="page">
      <h1>AI operations</h1>
      <p className="sub">Every LLM call is traced (request, response, model, prompt version, tokens, latency, cost, decision).</p>

      <div className="stat-row">
        <div className="stat"><span>LLM mode</span><strong className={ops.llm_mode === "live" ? "ok" : "warn"}>{ops.llm_mode}</strong>
          <em>{ops.llm_mode === "live" ? "Anthropic API" : "no API key: rules/template fallbacks"}</em></div>
        <div className="stat"><span>Spend today</span><strong>{usd(ops.budget.spent_today_usd, 2)}</strong>
          <div className="bar"><span className="bar-need" style={{ width: `${spentPct}%` }} /></div>
          <em>of {usd(ops.budget.daily_usd, 2)} daily cap (enforced)</em></div>
        <div className="stat"><span>Latency p50 / p95</span>
          <strong>{ops.latency_ms.p50 ?? "—"} / {ops.latency_ms.p95 ?? "—"} ms</strong><em>live calls, last 30 days</em></div>
        <div className="stat"><span>Projected monthly</span><strong>{usd(totalMonthly, 0)}</strong><em>10-rep team (see cost model)</em></div>
      </div>

      <Section title="Prompt versions & evals" aside={<code>python -m evals.run</code>}>
        {Object.entries(ops.evals).map(([feature, runs]) => (
          <div key={feature} className="eval-block">
            <h3>{feature} <span className="muted">active {ops.prompts[feature]?.active} · versions {ops.prompts[feature]?.versions.join(", ")}</span></h3>
            <div className="table-wrap">
              <table>
                <thead><tr><th>version</th><th>model</th>
                  {Object.keys(runs.find((r) => r.headline)?.headline ?? {}).map((h) => <th key={h}>{h.replace(/_/g, " ")}</th>)}
                  <th>cost / example</th></tr></thead>
                <tbody>
                  {runs.map((r) => (
                    <tr key={r.version}>
                      <td><strong>{r.version}</strong>{r.version === ops.prompts[feature]?.active && <span className="ai-tag">active</span>}</td>
                      {r.status === "ran" ? (
                        <>
                          <td className="mono small">{r.ops?.model}</td>
                          {Object.entries(r.headline ?? {}).map(([k, v]) => <td key={k} className="num">{v == null ? "—" : Number(v).toFixed(2)}</td>)}
                          <td className="num">{usd(r.ops?.cost_per_example_usd ?? 0, 5)}</td>
                        </>
                      ) : <td colSpan={8} className="muted">skipped: {r.reason}</td>}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}
        {Object.keys(ops.evals).length === 0 && <Empty>No eval runs yet.</Empty>}
      </Section>

      <Section title="Cost model" aside={<span className="muted">tokens × volume × frequency</span>}>
        <div className="table-wrap">
          <table>
            <thead><tr><th>Workload</th><th>Model</th><th>Why this model</th><th className="num">$/call</th><th className="num">calls/mo</th><th className="num">$/month</th></tr></thead>
            <tbody>
              {ops.cost_model.rows.map((r) => (
                <tr key={r.feature}>
                  <td><strong>{r.feature}</strong><div className="acct-meta">{r.volume_note}</div></td>
                  <td className="mono small">{r.model}{r.batch_discount && <div className="acct-meta">Batches API −50%</div>}</td>
                  <td className="small">{r.why_this_model}</td>
                  <td className="num">{usd(r.cost_per_call_usd, 5)}<div className="acct-meta">{r.tokens_source}</div></td>
                  <td className="num">{num(r.calls_per_month)}</td>
                  <td className="num"><strong>{usd(r.monthly_usd, 2)}</strong></td>
                </tr>
              ))}
              <tr><td colSpan={5}><strong>Total</strong></td><td className="num"><strong>{usd(totalMonthly, 2)}</strong></td></tr>
            </tbody>
          </table>
        </div>
        <h3>Production ceilings</h3>
        <ul className="ceilings">{Object.entries(ops.cost_model.ceilings).map(([k, v]) => <li key={k}><code>{k}</code>: {v}</li>)}</ul>
      </Section>

      <Section title="Usage by feature (30 days)">
        {ops.usage_30d.length === 0 ? <Empty>No LLM calls yet.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>env</th><th>feature</th><th>prompt</th><th>provider / model</th><th>status</th><th className="num">calls</th><th className="num">tokens in / out</th><th className="num">avg latency</th><th className="num">cost</th></tr></thead>
              <tbody>
                {ops.usage_30d.map((u, i) => (
                  <tr key={i}>
                    <td>{u.env}</td><td>{u.feature}</td><td>{u.prompt_version}</td>
                    <td className="mono small">{u.provider} / {u.model}</td>
                    <td><span className={`status s-${u.status}`}>{u.status}</span></td>
                    <td className="num">{num(u.calls)}</td>
                    <td className="num">{num(u.input_tokens)} / {num(u.output_tokens)}</td>
                    <td className="num">{u.avg_latency_ms ?? "—"} ms</td>
                    <td className="num">{usd(u.cost_usd ?? 0, 4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>

      <Section title="Recent calls">
        {ops.recent_calls.length === 0 ? <Empty>No calls yet.</Empty> : (
          <div className="table-wrap">
            <table>
              <thead><tr><th>when</th><th>feature</th><th>model</th><th>status</th><th>decision</th><th className="num">tokens</th><th className="num">latency</th><th className="num">cost</th></tr></thead>
              <tbody>
                {ops.recent_calls.map((c) => (
                  <tr key={c.call_id} title={c.error ?? c.call_id}>
                    <td className="small">{timeAgo(c.ts)}<div className="acct-meta">{c.env}</div></td>
                    <td>{c.feature} <span className="muted">{c.prompt_version}</span></td>
                    <td className="mono small">{c.model}</td>
                    <td><span className={`status s-${c.status}`}>{c.status}</span></td>
                    <td className="mono small">{c.decision ?? "—"}</td>
                    <td className="num">{num(c.input_tokens)} / {num(c.output_tokens)}</td>
                    <td className="num">{c.latency_ms ?? "—"} ms</td>
                    <td className="num">{usd(c.cost_usd ?? 0, 5)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </main>
  );
}
