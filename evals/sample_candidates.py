"""Print account cards for a stratified sample of candidate eval examples (for human labelling).

    python -m evals.sample_candidates > candidates.txt

Strata cover the ways classification goes wrong: top-tier accounts (high stakes), org-keyed accounts
(no domain; often ISPs), domain-keyed accounts, rDNS-keyed accounts, and keyword traps.
"""
from __future__ import annotations

import random

from salesintel import db
from salesintel.llm.context import load_context

FEW_SHOT_EXCLUDED = ("rose-hulman.edu", "ReadyIDC Co.,Ltd.", "watchguard.com", "leadpops.com")

STRATA = {
    "top_tier": "select id from accounts where tier = 'A' order by priority desc limit 60",
    "org_keyed": "select id from accounts where key_source = 'org' and need >= 50 order by random() limit 40",
    "domain_keyed": "select id from accounts where key_source in ('http_host','ssl_cn') and need >= 50 order by random() limit 40",
    "rdns_keyed": "select id from accounts where key_source = 'rdns' and need >= 50 order by random() limit 20",
    "keyword_traps": """select id from accounts where need >= 50 and (name like '%bank%' or name like '%clinic%'
                        or name like '%dental%' or name like '%health%') order by priority desc limit 20""",
}


def main(per_stratum: int = 9, seed: int = 7) -> None:
    con = db.connect()
    rng = random.Random(seed)
    excluded = {r[0] for r in con.execute(
        f"select id from accounts where name in ({','.join('?' * len(FEW_SHOT_EXCLUDED))})", FEW_SHOT_EXCLUDED)}
    seen: set[int] = set()
    for stratum, sql in STRATA.items():
        ids = [r[0] for r in con.execute(sql) if r[0] not in excluded]
        rng.shuffle(ids)
        for account_id in ids[:per_stratum]:
            if account_id in seen:
                continue
            seen.add(account_id)
            ctx = load_context(con, account_id)
            print(f"=== account_key={ctx.account['account_key']} stratum={stratum}")
            print(ctx.account_card())


if __name__ == "__main__":
    main()
