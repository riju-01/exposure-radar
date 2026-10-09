"""Freeze eval inputs: labels (hand-written YAML) + the exact account context -> datasets/*.jsonl.

Evals must not drift when the data pipeline is re-run, so each example stores the full context
(account card fields, evidence pack, reasons) it was labelled against. Re-freeze deliberately,
and re-check the labels, when the pipeline changes what a card contains.

    python -m evals.freeze
"""
from __future__ import annotations

import json

import yaml

from salesintel import config, db
from salesintel.llm.context import load_context

LABELS = config.EVALS_DIR / "labels"
DATASETS = config.EVALS_DIR / "datasets"

# outreach-draft examples: one strong prospect per major signal family, so the drafts must handle
# KEV CVEs, open databases, remote access, ICS, compromise indicators, EOL software and AI exposure.
OUTREACH_SIGNALS = ["kev_vuln", "db_unauth", "remote_unauth", "ics_exposed", "compromise_indicator",
                    "remote_access", "eol_software", "ai_devops_exposed", "likely_exploited", "db_exposed"]


def _account_id(con, key: str) -> int:
    row = con.execute("select id from accounts where account_key = ?", (key,)).fetchone()
    if not row:
        raise SystemExit(f"labelled account not in app.db: {key}")
    return row[0]


def freeze_classify(con) -> int:
    spec = yaml.safe_load((LABELS / "account-classify.yaml").read_text(encoding="utf-8"))
    out = DATASETS / "account-classify.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for ex in spec["examples"]:
            ctx = load_context(con, _account_id(con, ex["account_key"]))
            gold = {k: ex[k] for k in ("entity_type", "is_prospect", "industry")}
            gold["industry_ok"] = ex.get("industry_ok", [ex["industry"]])
            fh.write(json.dumps({"id": ex["account_key"], "gold": gold, "difficulty": ex["difficulty"],
                                 "note": ex.get("note", ""), "context": ctx.to_dict()}, ensure_ascii=False) + "\n")
    return len(spec["examples"])


def freeze_outreach(con) -> int:
    picked: list[tuple[int, str]] = []
    for sig in OUTREACH_SIGNALS:
        taken = [aid for aid, _ in picked] or [0]
        row = con.execute(
            f"""select a.id from accounts a join account_signals s on s.account_id = a.id
                where s.signal = ? and a.is_prospect = 1 and a.domain is not null
                  and a.industry <> 'hosting_telecom' and a.id not in ({",".join("?" * len(taken))})
                order by a.priority desc limit 1""", (sig, *taken)).fetchone()
        if row:
            picked.append((row[0], sig))
    with open(DATASETS / "outreach-draft.jsonl", "w", encoding="utf-8") as fh:
        for account_id, sig in picked:
            ctx = load_context(con, account_id)
            fh.write(json.dumps({"id": ctx.account["account_key"], "focus_signal": sig,
                                 "context": ctx.to_dict()}, ensure_ascii=False) + "\n")
    return len(picked)


def main() -> None:
    DATASETS.mkdir(parents=True, exist_ok=True)
    con = db.connect()
    print(f"account-classify: {freeze_classify(con)} examples")
    print(f"outreach-draft:   {freeze_outreach(con)} examples")


if __name__ == "__main__":
    main()
