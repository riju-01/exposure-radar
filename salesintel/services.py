"""Application services: the use cases the API (and batch jobs) invoke.

Kept separate from HTTP so the same functions power the API, the batch classifier and tests.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from salesintel.llm import registry
from salesintel.llm.context import load_context
from salesintel.llm.grounding import check_outreach
from salesintel.llm.runner import FeatureResult, run_feature
from salesintel.scoring import score_account

CLASSIFY, OUTREACH = "account-classify", "outreach-draft"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def cached_output(con: sqlite3.Connection, account_id: int, feature: str, version: str | None = None) -> dict | None:
    version = version or registry.active_version(feature)
    row = con.execute("select * from llm_outputs where account_id=? and feature=? and prompt_version=?",
                      (account_id, feature, version)).fetchone()
    if not row:
        return None
    d = dict(row)
    d["output"] = json.loads(d.pop("output_json"))
    d["checks"] = json.loads(d.pop("checks_json")) if d.get("checks_json") else None
    return d


def _store(con, account_id: int, feature: str, res: FeatureResult, checks: dict | None = None) -> None:
    con.execute(
        """insert or replace into llm_outputs(account_id, feature, prompt_version, model, provider,
               output_json, checks_json, call_id, created_at) values (?,?,?,?,?,?,?,?,?)""",
        (account_id, feature, res.prompt_version, res.model, res.provider, json.dumps(res.output),
         json.dumps(checks) if checks else None, res.call_id, _now()))


def rescore(con: sqlite3.Connection, account_id: int) -> None:
    a = con.execute("select * from accounts where id = ?", (account_id,)).fetchone()
    counts = json.loads(a["signals_json"] or "{}")
    # cloud_share isn't stored; recover the hosting bucket's representative value
    cloud_share = {"cloud": 1.0, "hybrid": 0.5, "self_hosted": 0.0}[a["hosting"]]
    res = score_account(signal_counts=counts, n_ips=a["n_ips"], country_code=a["country_code"],
                        industry=a["industry"], cloud_share=cloud_share)
    con.execute("update accounts set fit=?, priority=?, tier=?, fit_json=? where id=?",
                (res.fit, res.priority, res.tier, json.dumps(res.fit_breakdown), account_id))


def classify_account(con: sqlite3.Connection, account_id: int, *, refresh: bool = False, apply: bool = True,
                     env: str = "app", run_id: str | None = None) -> dict:
    """Classify with the active prompt; apply prospect/industry to the account and rescore its fit."""
    if not refresh and (hit := cached_output(con, account_id, CLASSIFY)):
        return {**hit, "cached": True}
    ctx = load_context(con, account_id)
    res = run_feature(CLASSIFY, ctx, env=env, run_id=run_id)
    _store(con, account_id, CLASSIFY, res)
    out = res.output
    # Offline rules are only a fallback: they never overwrite a rule-derived industry with a weaker guess.
    if apply and res.provider != "offline":
        industry = out["industry"] if out["industry"] != "unknown" else ctx.account["industry"]
        con.execute("""update accounts set entity_type=?, is_prospect=?, prospect_source='llm', industry=?,
                       industry_source=case when ?='unknown' then industry_source else 'llm' end where id=?""",
                    (out["entity_type"], int(out["is_prospect"]), industry, out["industry"], account_id))
        rescore(con, account_id)
    con.commit()
    return {**cached_output(con, account_id, CLASSIFY), "cached": False, "status": res.status, "note": res.note}


def draft_outreach(con: sqlite3.Connection, account_id: int, *, refresh: bool = False,
                   env: str = "app", run_id: str | None = None) -> dict:
    if not refresh and (hit := cached_output(con, account_id, OUTREACH)):
        return {**hit, "cached": True}
    ctx = load_context(con, account_id)
    res = run_feature(OUTREACH, ctx, env=env, run_id=run_id)
    checks = check_outreach(res.output, ctx)
    _store(con, account_id, OUTREACH, res, checks)
    con.commit()
    return {**cached_output(con, account_id, OUTREACH), "cached": False, "status": res.status, "note": res.note}
