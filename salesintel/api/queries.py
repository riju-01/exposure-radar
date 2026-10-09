"""Read-side queries for the API: filtering, facets, account detail. All SQL is parameterised."""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field

from salesintel.llm.context import load_context
from salesintel.services import CLASSIFY, OUTREACH, cached_output
from salesintel.signals import BY_KEY

SORTS = {
    "priority": "a.priority desc, a.need desc",
    "need": "a.need desc, a.priority desc",
    "fit": "a.fit desc, a.priority desc",
    "size": "a.n_ips desc",
    "name": "a.name collate nocase asc",
}
LIST_COLUMNS = """a.id, a.name, a.domain, a.country_code, a.country, a.region, a.industry, a.industry_source,
    a.segment, a.hosting, a.entity_type, a.is_prospect, a.prospect_source, a.n_ips, a.need, a.fit, a.priority,
    a.tier, a.top_reason, a.signals_json, a.max_cvss, a.max_epss, a.n_cves,
    (s.account_id is not null) as shortlisted"""


@dataclass
class AccountFilter:
    q: str | None = None
    regions: list[str] = field(default_factory=list)
    countries: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    segments: list[str] = field(default_factory=list)
    hosting: list[str] = field(default_factory=list)
    tiers: list[str] = field(default_factory=list)
    signals: list[str] = field(default_factory=list)   # account must have ALL of these
    min_need: int | None = None
    prospects_only: bool = True
    shortlisted: bool = False

    def where(self) -> tuple[str, list]:
        clauses, params = [], []

        def any_of(col: str, values: list[str]) -> None:
            if values:
                clauses.append(f"{col} in ({','.join('?' * len(values))})")
                params.extend(values)

        if self.q:
            clauses.append("(a.name like ? or a.domain like ?)")
            params += [f"%{self.q}%", f"%{self.q}%"]
        any_of("a.region", self.regions)
        any_of("a.country_code", self.countries)
        any_of("a.industry", self.industries)
        any_of("a.segment", self.segments)
        any_of("a.hosting", self.hosting)
        any_of("a.tier", self.tiers)
        for sig in self.signals:
            clauses.append("exists (select 1 from account_signals x where x.account_id = a.id and x.signal = ?)")
            params.append(sig)
        if self.min_need is not None:
            clauses.append("a.need >= ?")
            params.append(self.min_need)
        if self.prospects_only:
            clauses.append("a.is_prospect = 1")
        if self.shortlisted:
            clauses.append("s.account_id is not null")
        return (" where " + " and ".join(clauses)) if clauses else "", params


def _row_to_list_item(r: sqlite3.Row) -> dict:
    d = dict(r)
    d["signals"] = json.loads(d.pop("signals_json") or "{}")
    d["shortlisted"] = bool(d["shortlisted"])
    return d


def list_accounts(con, f: AccountFilter, *, sort: str, page: int, page_size: int) -> dict:
    where, params = f.where()
    base = f"from accounts a left join shortlist s on s.account_id = a.id{where}"
    total = con.execute(f"select count(*) {base}", params).fetchone()[0]
    rows = con.execute(f"select {LIST_COLUMNS} {base} order by {SORTS.get(sort, SORTS['priority'])} "
                       f"limit ? offset ?", [*params, page_size, (page - 1) * page_size]).fetchall()
    tiers = dict(con.execute(f"select a.tier, count(*) {base} group by a.tier", params).fetchall())
    return {"total": total, "page": page, "page_size": page_size, "tier_counts": tiers,
            "items": [_row_to_list_item(r) for r in rows]}


def export_rows(con, f: AccountFilter, *, sort: str, limit: int = 5000) -> list[dict]:
    where, params = f.where()
    rows = con.execute(
        f"""select a.name, a.domain, a.tier, a.priority, a.need, a.fit, a.industry, a.segment, a.region,
                   a.country, a.n_ips, a.top_reason, a.reasons_json
            from accounts a left join shortlist s on s.account_id = a.id{where}
            order by {SORTS.get(sort, SORTS['priority'])} limit ?""", [*params, limit]).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        reasons = json.loads(d.pop("reasons_json") or "[]")
        d["all_reasons"] = " | ".join(x["text"] for x in reasons)
        out.append(d)
    return out


def facets(con) -> dict:
    def counts(col: str) -> list[dict]:
        return [{"value": v, "count": n} for v, n in con.execute(
            f"select {col}, count(*) from accounts where is_prospect = 1 and {col} is not null "
            f"group by {col} order by count(*) desc")]

    signal_counts = dict(con.execute(
        "select s.signal, count(*) from account_signals s join accounts a on a.id = s.account_id "
        "where a.is_prospect = 1 group by s.signal").fetchall())
    countries = [{"value": cc, "label": name, "region": reg, "count": n} for cc, name, reg, n in con.execute(
        "select country_code, max(country), max(region), count(*) from accounts where is_prospect = 1 "
        "and country_code is not null group by country_code order by count(*) desc")]
    return {
        "regions": counts("region"), "industries": counts("industry"), "segments": counts("segment"),
        "hosting": counts("hosting"), "tiers": counts("tier"), "countries": countries,
        "signals": [{"key": s.key, "label": s.label, "category": s.category, "weight": s.weight,
                     "count": signal_counts.get(s.key, 0)} for s in BY_KEY.values()],
    }


def account_detail(con, account_id: int) -> dict | None:
    try:
        ctx = load_context(con, account_id)
    except KeyError:
        return None
    a = ctx.account
    a["fit_breakdown"] = json.loads(a.pop("fit_json") or "{}")
    a["signals"] = json.loads(a.pop("signals_json") or "{}")
    for e in ctx.evidence:
        e["flag_labels"] = [BY_KEY[f].label for f in e["flags"] if f in BY_KEY]
    all_evidence = con.execute("select count(*) from evidence where account_id = ?", (account_id,)).fetchone()[0]
    shortlisted = con.execute("select 1 from shortlist where account_id = ?", (account_id,)).fetchone() is not None
    return {
        "account": a, "reasons": ctx.reasons, "evidence": ctx.evidence, "evidence_total": all_evidence,
        "shortlisted": shortlisted,
        "classification": cached_output(con, account_id, CLASSIFY),
        "outreach": cached_output(con, account_id, OUTREACH),
    }
