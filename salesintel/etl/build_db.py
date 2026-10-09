"""Stage 4: enrich + score every signal-bearing account and write the SQLite app database."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone

import duckdb
import numpy as np

from salesintel import config, db
from salesintel.enrich import rule_industry
from salesintel.scoring import score_account
from salesintel.signals import SIGNALS


def _jsonable(v):
    if isinstance(v, np.ndarray):
        return [_jsonable(x) for x in v.tolist()]
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (np.floating,)):
        return None if np.isnan(v) else float(v)
    if isinstance(v, (np.integer,)):
        return int(v)
    return v


def _j(v) -> str:
    return json.dumps(_jsonable(v) if v is not None else [], ensure_ascii=False)


def _num(v, digits: int | None = None):
    v = _jsonable(v)
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    # float32 Parquet columns carry noise (9.800000190734863); store what a human would read
    return round(v, digits) if digits is not None and isinstance(v, float) else v


def account_row(r: dict) -> tuple[dict, list[tuple[str, int]]]:
    key = r["account_key"]
    is_org = key.startswith("org:")
    name = key[4:] if is_org else key
    domain = None if is_org else key
    titles = _jsonable(r["titles"]) or []
    cert_orgs = _jsonable(r["cert_orgs"]) or []
    # Page titles are deliberately excluded: on shared infrastructure they often describe a
    # *hosted* site rather than the account, so they are left for the LLM to weigh.
    industry, clue = rule_industry(domain, name if is_org else None, " ; ".join(cert_orgs))
    counts = {s.key: int(r[f"sig_{s.key}"] or 0) for s in SIGNALS}
    res = score_account(signal_counts=counts, n_ips=int(r["n_ips"]), country_code=r["country_code"],
                        industry=industry, cloud_share=float(r["cloud_share"] or 0))
    row = {
        "account_key": key, "name": name, "domain": domain, "key_source": r["key_source"],
        "country_code": r["country_code"], "country": r["country"], "region": res.region,
        "segment": res.segment, "hosting": res.hosting,
        "industry": industry, "industry_source": "rule" if industry != "unknown" else "none",
        "industry_clue": clue,
        "is_prospect": 0 if industry == "hosting_telecom" else 1, "prospect_source": "rule",
        "n_ips": int(r["n_ips"]), "n_services": int(r["n_services"]), "n_ports": int(r["n_ports"]),
        "n_cves": int(r["n_cves"] or 0), "max_cvss": _num(r["max_cvss"], 1), "max_epss": _num(r["max_epss"], 4),
        "need": res.need, "fit": res.fit, "priority": res.priority, "tier": res.tier,
        "top_reason": res.reasons[0]["text"] if res.reasons else None,
        "reasons_json": json.dumps(res.reasons), "fit_json": json.dumps(res.fit_breakdown),
        "signals_json": json.dumps({k: v for k, v in counts.items() if v}),
        "titles_json": _j(titles), "products_json": _j(r["products"]), "hosts_json": _j(r["hosts"]),
        "hosting_orgs_json": _j(r["hosting_orgs"]), "cert_orgs_json": _j(cert_orgs),
        "primary_org": r["primary_org"], "waf_share": _num(r["waf_share"], 2),
        "has_security_txt": int(bool(r["has_security_txt"])), "last_seen": r["last_seen"],
    }
    return row, [(k, v) for k, v in counts.items() if v]


def run(con: duckdb.DuckDBPyConnection) -> dict:
    t0 = time.time()
    feats = con.sql("select * from account_features where max_record_severity > 0").df()
    out = db.connect()
    db.init(out)
    out.executescript("delete from account_signals; delete from evidence; delete from llm_outputs; "
                      "delete from shortlist; delete from accounts;")

    rows, sigs = [], []
    for rec in feats.to_dict("records"):
        row, s = account_row(rec)
        rows.append(row)
        sigs.append(s)
    cols = list(rows[0])
    out.executemany(f"insert into accounts({','.join(cols)}) values ({','.join('?' * len(cols))})",
                    [tuple(r[c] for c in cols) for r in rows])
    ids = dict(out.execute("select account_key, id from accounts").fetchall())
    out.executemany("insert into account_signals(account_id, signal, count) values (?,?,?)",
                    [(ids[r["account_key"]], k, v) for r, s in zip(rows, sigs) for k, v in s])

    ev = con.sql("select * from evidence").df()
    ev = ev[ev.account_key.isin(ids.keys())]
    out.executemany(
        """insert into evidence(account_id, ip, port, transport, module, product, version, http_title, http_host,
               ssl_cn, org, country_code, seen_at, cves_json, n_cves, max_cvss, max_epss, kev_count, flags_json, severity)
           values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [(ids[e["account_key"]], e["ip"], int(e["port"]), e["transport"], e["module"], e["product"], e["version"],
          e["http_title"], e["http_host"], e["ssl_cn"], e["org"], e["country_code"], e["ts"], _j(e["cves"]),
          int(e["n_cves"]), _num(e["max_cvss"], 1), _num(e["max_epss"], 4), int(e["kev_count"]), _j(e["flags"]),
          float(e["severity"]))
         for e in ev.to_dict("records")])

    stats = {
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "accounts_total": int(con.sql("select count(*) from account_features").fetchone()[0]),
        "accounts_with_signals": len(rows),
        "evidence_rows": len(ev),
        "seconds": round(time.time() - t0, 1),
    }
    db.set_meta(out, "build", stats)
    out.commit()
    out.execute("analyze")
    out.close()
    return stats
