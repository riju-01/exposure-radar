"""HTTP API + static UI.

    uvicorn salesintel.api.main:app --reload            (dev)
    python -m salesintel.api                             (serve API + built UI on :8000)
"""
from __future__ import annotations

import csv
import io
import json
import sqlite3
import statistics
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from salesintel import config, db, services
from salesintel.api.queries import AccountFilter, account_detail, export_rows, facets, list_accounts
from salesintel.llm import cost_model, registry
from salesintel.llm.providers import active_provider_name, live_available
from salesintel.llm.tracing import default_tracer

_con: sqlite3.Connection | None = None
# Facet counts scan ~100k accounts (~2.5 s). They only change when a classification flips
# prospect/industry, so they are cached in-process and invalidated by the classify endpoint.
_facets_cache: dict | None = None


def cached_facets(con: sqlite3.Connection) -> dict:
    global _facets_cache
    if _facets_cache is None:
        _facets_cache = facets(con)
    return _facets_cache


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _con
    if not config.APP_DB.exists():
        raise RuntimeError(f"{config.APP_DB} not found: run `python -m salesintel.pipeline` first")
    _con = db.connect()
    db.init(_con)  # idempotent: ensures shortlist/llm_outputs exist on older builds
    cached_facets(_con)  # warm the cache at startup, not on the first page view
    yield
    _con.close()


app = FastAPI(title="Exposure-led prospecting API", version="1.0.0", lifespan=lifespan)


def get_con() -> sqlite3.Connection:
    assert _con is not None
    return _con


def account_filter(
    q: str | None = None,
    region: list[str] = Query(default=[]),
    country: list[str] = Query(default=[]),
    industry: list[str] = Query(default=[]),
    segment: list[str] = Query(default=[]),
    hosting: list[str] = Query(default=[]),
    tier: list[str] = Query(default=[]),
    signal: list[str] = Query(default=[]),
    min_need: int | None = Query(default=None, ge=0, le=100),
    prospects_only: bool = True,
    shortlisted: bool = False,
) -> AccountFilter:
    return AccountFilter(q=q, regions=region, countries=country, industries=industry, segments=segment,
                         hosting=hosting, tiers=tier, signals=signal, min_need=min_need,
                         prospects_only=prospects_only, shortlisted=shortlisted)


# ------------------------------------------------------------------------------ read
@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/meta")
def meta(con=Depends(get_con)):
    return {
        "dataset": db.get_meta(con, "dataset"), "build": db.get_meta(con, "build"),
        "resolution": db.get_meta(con, "resolution"), "facets": cached_facets(con),
        "llm": {"mode": "live" if live_available() else "offline", "provider": active_provider_name(),
                "prompts": {f: registry.active_version(f) for f in registry.features()},
                "daily_budget_usd": config.LLM_DAILY_BUDGET_USD,
                "spent_today_usd": round(default_tracer().spend_today("app"), 4)},
    }


@app.get("/api/accounts")
def accounts(f: AccountFilter = Depends(account_filter), sort: str = "priority",
             page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), con=Depends(get_con)):
    return list_accounts(con, f, sort=sort, page=page, page_size=page_size)


@app.get("/api/accounts/{account_id}")
def account(account_id: int, con=Depends(get_con)):
    d = account_detail(con, account_id)
    if d is None:
        raise HTTPException(404, "account not found")
    return d


@app.get("/api/export.csv")
def export(f: AccountFilter = Depends(account_filter), sort: str = "priority", con=Depends(get_con)):
    rows = export_rows(con, f, sort=sort)
    buf = io.StringIO()
    if rows:
        w = csv.DictWriter(buf, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv",
                             headers={"Content-Disposition": f"attachment; filename=prospects-{stamp}.csv"})


# ------------------------------------------------------------------------------ AI actions
def _require_account(con, account_id: int) -> None:
    if not con.execute("select 1 from accounts where id = ?", (account_id,)).fetchone():
        raise HTTPException(404, "account not found")


@app.post("/api/accounts/{account_id}/classify")
def classify(account_id: int, refresh: bool = False, con=Depends(get_con)):
    global _facets_cache
    _require_account(con, account_id)
    result = services.classify_account(con, account_id, refresh=refresh)
    _facets_cache = None
    return result


@app.post("/api/accounts/{account_id}/outreach")
def outreach(account_id: int, refresh: bool = False, con=Depends(get_con)):
    _require_account(con, account_id)
    return services.draft_outreach(con, account_id, refresh=refresh)


# ------------------------------------------------------------------------------ shortlist
@app.put("/api/shortlist/{account_id}")
def shortlist_add(account_id: int, con=Depends(get_con)):
    _require_account(con, account_id)
    con.execute("insert or ignore into shortlist(account_id, added_at) values (?, ?)",
                (account_id, datetime.now(timezone.utc).isoformat(timespec="seconds")))
    con.commit()
    return {"shortlisted": True}


@app.delete("/api/shortlist/{account_id}")
def shortlist_remove(account_id: int, con=Depends(get_con)):
    con.execute("delete from shortlist where account_id = ?", (account_id,))
    con.commit()
    return {"shortlisted": False}


# ------------------------------------------------------------------------------ ops
def _percentile(values: list[int], p: float) -> int | None:
    if not values:
        return None
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p))]


@app.get("/api/ops")
def ops():
    t = default_tracer()
    since = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    by_feature = t.query(
        """select env, feature, prompt_version, provider, model, status, count(*) calls,
                  round(sum(cost_usd), 5) cost_usd, sum(input_tokens) input_tokens, sum(output_tokens) output_tokens,
                  round(avg(latency_ms)) avg_latency_ms
           from llm_calls where ts >= ? group by 1,2,3,4,5,6 order by calls desc""", (since,))
    lat = [r["latency_ms"] for r in t.query(
        "select latency_ms from llm_calls where provider <> 'offline' and latency_ms is not null and ts >= ?", (since,))]
    recent = t.query(
        """select call_id, ts, env, feature, prompt_version, provider, model, account_id, status, decision,
                  input_tokens, output_tokens, latency_ms, cost_usd, error
           from llm_calls order by ts desc limit 50""")
    # The cost model is priced in Claude tokens; other providers' tokenizers aren't comparable.
    measured = {}
    for r in t.query("""select feature, avg(input_tokens) i, avg(output_tokens) o from llm_calls
                        where provider = 'anthropic' and status = 'ok' group by feature"""):
        measured[r["feature"]] = (int(r["i"]), int(r["o"]))
    evals = {}
    for f in registry.features():
        p = config.EVALS_DIR / "results" / f / "latest.json"
        if p.exists():
            evals[f] = json.loads(p.read_text(encoding="utf-8"))["summaries"]
    return {
        "llm_mode": "live" if live_available() else "offline",
        "llm_provider": active_provider_name(),
        "budget": {"daily_usd": config.LLM_DAILY_BUDGET_USD, "spent_today_usd": round(t.spend_today("app"), 4)},
        "usage_30d": by_feature,
        "latency_ms": {"p50": _percentile(lat, 0.5), "p95": _percentile(lat, 0.95),
                       "mean": round(statistics.mean(lat)) if lat else None},
        "recent_calls": recent,
        "cost_model": {"rows": cost_model.table(measured), "ceilings": cost_model.CEILINGS},
        "evals": evals,
        "prompts": {f: {"active": registry.active_version(f), "versions": registry.versions(f)}
                    for f in registry.features()},
    }


# ------------------------------------------------------------------------------ UI
if config.WEB_DIST.exists():
    app.mount("/assets", StaticFiles(directory=config.WEB_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = config.WEB_DIST / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(config.WEB_DIST / "index.html")
