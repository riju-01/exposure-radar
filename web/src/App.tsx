import { useEffect, useState } from "react";
import { NavLink, Route, Routes } from "react-router-dom";
import { api, type Meta } from "./api";
import { ErrorNote } from "./components/ui";
import { num } from "./format";
import { AccountPage } from "./pages/AccountPage";
import { OpsPage } from "./pages/OpsPage";
import { ProspectsPage } from "./pages/ProspectsPage";

export function App() {
  const [meta, setMeta] = useState<Meta | null>(null);
  const [error, setError] = useState<unknown>(null);
  useEffect(() => { api.meta().then(setMeta).catch(setError); }, []);

  return (
    <>
      <nav className="topbar">
        <div className="brand"><span className="logo" aria-hidden="true" />Exposure Radar</div>
        <div className="nav-links">
          <NavLink to="/" end>Prospects</NavLink>
          <NavLink to="/shortlist">Shortlist</NavLink>
          <NavLink to="/ops">AI ops</NavLink>
        </div>
        {meta?.dataset && (
          <div className="dataset" title="Internet scan snapshot powering the rankings">
            {num(meta.dataset.records)} scan records · {num(meta.build?.accounts_with_signals)} accounts with signals ·
            snapshot {meta.dataset.last_seen?.slice(0, 10)}
            <span className={`mode ${meta.llm.mode}`}>{meta.llm.mode === "live" ? "AI live" : "AI offline"}</span>
          </div>
        )}
      </nav>
      {error ? <main className="page"><ErrorNote error={error} /></main> : null}
      {meta && (
        <Routes>
          <Route path="/" element={<ProspectsPage meta={meta} />} />
          <Route path="/shortlist" element={<ProspectsPage meta={meta} shortlistOnly />} />
          <Route path="/accounts/:id" element={<AccountPage />} />
          <Route path="/ops" element={<OpsPage />} />
        </Routes>
      )}
    </>
  );
}
