"""Command-line entry points used by the skills in skills/ (and handy for humans).

    python -m salesintel.cli top --region Europe --industry healthcare --n 10
    python -m salesintel.cli card mit.edu
    python -m salesintel.cli classify mit.edu [--version v2] [--no-apply]
    python -m salesintel.cli outreach ooda-health.io [--refresh]
    python -m salesintel.cli classify-batch --tier A --limit 200

Every command prints JSON so agents can consume it directly.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid

from salesintel import db, services
from salesintel.api.queries import AccountFilter, list_accounts
from salesintel.llm.context import load_context
from salesintel.llm.grounding import check_outreach
from salesintel.llm.runner import run_feature


def _account_id(con, key: str) -> int:
    row = con.execute("select id from accounts where account_key = ? or name = ?", (key, key)).fetchone()
    if not row:
        sys.exit(f"no account with key or name '{key}'")
    return row[0]


def cmd_top(con, a) -> dict:
    f = AccountFilter(regions=a.region or [], countries=a.country or [], industries=a.industry or [],
                      tiers=a.tier or [], signals=a.signal or [])
    res = list_accounts(con, f, sort="priority", page=1, page_size=a.n)
    return {"total": res["total"], "accounts": [
        {k: i[k] for k in ("id", "name", "tier", "priority", "need", "fit", "industry", "country", "n_ips", "top_reason")}
        for i in res["items"]]}


def cmd_card(con, a) -> dict:
    ctx = load_context(con, _account_id(con, a.account))
    return {"account_card": ctx.account_card(), "evidence_pack": ctx.evidence_pack()}


def cmd_classify(con, a) -> dict:
    aid = _account_id(con, a.account)
    if a.version:  # explicit version: evaluate without touching the account
        res = run_feature("account-classify", load_context(con, aid), version=a.version, env="batch")
        return {"output": res.output, "provider": res.provider, "model": res.model, "prompt_version": res.prompt_version,
                "status": res.status, "cost_usd": res.cost_usd}
    return services.classify_account(con, aid, refresh=True, apply=not a.no_apply, env="batch")


def cmd_outreach(con, a) -> dict:
    aid = _account_id(con, a.account)
    if a.version:
        ctx = load_context(con, aid)
        res = run_feature("outreach-draft", ctx, version=a.version, env="batch")
        return {"output": res.output, "checks": check_outreach(res.output, ctx), "provider": res.provider,
                "model": res.model, "prompt_version": res.prompt_version, "status": res.status, "cost_usd": res.cost_usd}
    return services.draft_outreach(con, aid, refresh=a.refresh, env="batch")


def cmd_classify_batch(con, a) -> dict:
    run_id = f"batch-{uuid.uuid4().hex[:8]}"
    clause = f"and tier in ({','.join('?' * len(a.tier))})" if a.tier else ""
    ids = [r[0] for r in con.execute(
        f"""select id from accounts where id not in (select account_id from llm_outputs where feature = 'account-classify')
            {clause} order by priority desc limit ?""", (*(a.tier or []), a.limit))]
    flipped = 0
    for aid in ids:
        before = con.execute("select is_prospect from accounts where id = ?", (aid,)).fetchone()[0]
        services.classify_account(con, aid, env="batch", run_id=run_id)
        after = con.execute("select is_prospect from accounts where id = ?", (aid,)).fetchone()[0]
        flipped += before != after
    return {"run_id": run_id, "classified": len(ids), "prospect_flag_changed": flipped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("top")
    for flag in ("region", "country", "industry", "tier", "signal"):
        t.add_argument(f"--{flag}", action="append")
    t.add_argument("--n", type=int, default=10)
    sub.add_parser("card").add_argument("account")
    c = sub.add_parser("classify")
    c.add_argument("account")
    c.add_argument("--version")
    c.add_argument("--no-apply", action="store_true")
    o = sub.add_parser("outreach")
    o.add_argument("account")
    o.add_argument("--version")
    o.add_argument("--refresh", action="store_true")
    b = sub.add_parser("classify-batch")
    b.add_argument("--tier", action="append")
    b.add_argument("--limit", type=int, default=100)
    a = ap.parse_args()

    con = db.connect()
    handler = {"top": cmd_top, "card": cmd_card, "classify": cmd_classify, "outreach": cmd_outreach,
               "classify-batch": cmd_classify_batch}[a.cmd]
    print(json.dumps(handler(con, a), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
