"""Build the LLM input context for an account: an 'account card' and an 'evidence pack'.

The context is assembled once, as structured data, and rendered to text for the prompt.
The offline fallback and the grounding checker read the same structured data, so all three
agree on exactly what evidence the model was shown.
"""
from __future__ import annotations

import html
import json
import re
import sqlite3
from dataclasses import asdict, dataclass

from salesintel.signals import BY_KEY

MAX_EVIDENCE = 12
_IP_RE = re.compile(r"^[\d.:\[\]]+$")


@dataclass
class AccountContext:
    account: dict
    evidence: list[dict]   # each has an `ref` like "E1"
    reasons: list[dict]

    # -- serialisation: eval datasets freeze the exact context an example was labelled on ----
    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AccountContext":
        return cls(account=d["account"], evidence=d["evidence"], reasons=d["reasons"])

    # -- facts the grounding check uses ------------------------------------------------
    @property
    def cves(self) -> set[str]:
        return {c.upper() for e in self.evidence for c in e["cves"]}

    @property
    def ports(self) -> set[int]:
        return {int(e["port"]) for e in self.evidence}

    @property
    def refs(self) -> set[str]:
        return {e["ref"] for e in self.evidence}

    @property
    def signal_keys(self) -> set[str]:
        return {r["signal"] for r in self.reasons}

    # -- prompt renderings ---------------------------------------------------------------
    def account_card(self) -> str:
        a = self.account
        hostnames = [h for h in a["hosts"] if not _IP_RE.match(h)]  # bare IPs say nothing about identity
        lines = [
            f"name: {a['name']}",
            f"domain: {a['domain'] or '(none - identified by network owner name)'}",
            f"identified_via: {a['key_source']}",
            f"network_owners (ASN org of the IPs): {', '.join(a['hosting_orgs'][:4]) or 'n/a'}",
            f"certificate_organisations: {', '.join(a['cert_orgs']) or 'n/a'}",
            f"country: {a['country'] or a['country_code']} ({a['region']})",
            f"footprint: {a['n_ips']} IPs, {a['n_services']} services, {a['n_ports']} distinct ports",
            f"hosting_profile: {a['hosting']}",
            f"sample_page_titles: {' | '.join(html.unescape(t).strip()[:80] for t in a['titles'][:5]) or 'n/a'}",
            f"sample_hostnames: {', '.join(hostnames[:5]) or 'n/a'}",
            f"detected_products: {', '.join(a['products'][:8]) or 'n/a'}",
        ]
        return "\n".join(lines)

    def evidence_pack(self) -> str:
        a = self.account
        out = [f"need_score: {a['need']}/100  fit_score: {a['fit']}/100  tier: {a['tier']}",
               f"industry: {a['industry']}  segment: {a['segment']}", "", "SIGNALS (rule-detected):"]
        out += [f"- {r['signal']}: {r['text']}" for r in self.reasons]
        out += ["", "EVIDENCE (worst assets first):"]
        for e in self.evidence:
            cve_part = ""
            if e["cves"]:
                cve_part = (f" | CVEs: {', '.join(e['cves'][:6])}"
                            f"{' (+' + str(e['n_cves'] - 6) + ' more)' if e['n_cves'] > 6 else ''}"
                            f" | max CVSS {e['max_cvss']}, max EPSS {e['max_epss']}"
                            f"{', on CISA KEV' if e['kev_count'] else ''}")
            title = f' | title "{html.unescape(e["http_title"]).strip()[:60]}"' if e["http_title"] else ""
            prod = " ".join(p for p in (e["product"], e["version"]) if p) or e["module"] or "unknown service"
            out.append(f"[{e['ref']}] port {e['port']}/{e['transport']} {prod}{title}{cve_part}"
                       f" | flags: {', '.join(BY_KEY[f].label for f in e['flags'] if f in BY_KEY)}")
        return "\n".join(out)


def select_evidence(rows: list[dict], signals: list[str], limit: int) -> list[dict]:
    """Coverage first: the worst asset for each signal (in signal-priority order), then fill by severity."""
    chosen: list[dict] = []
    for sig in signals:
        hit = next((r for r in rows if sig in r["flags"] and r not in chosen), None)
        if hit and len(chosen) < limit:
            chosen.append(hit)
    for r in rows:
        if len(chosen) >= limit:
            break
        if r not in chosen:
            chosen.append(r)
    return sorted(chosen, key=lambda r: -r["severity"])


def _loads(v, default):
    return json.loads(v) if v else default


def load_context(con: sqlite3.Connection, account_id: int) -> AccountContext:
    row = con.execute("select * from accounts where id = ?", (account_id,)).fetchone()
    if row is None:
        raise KeyError(f"account {account_id} not found")
    a = dict(row)
    for col, key in [("titles_json", "titles"), ("products_json", "products"), ("hosts_json", "hosts"),
                     ("hosting_orgs_json", "hosting_orgs"), ("cert_orgs_json", "cert_orgs")]:
        a[key] = _loads(a.pop(col), [])
    reasons = _loads(a.pop("reasons_json"), [])
    rows = [dict(e) for e in con.execute(
        "select * from evidence where account_id = ? order by severity desc, n_cves desc", (account_id,))]
    for d in rows:
        d["cves"] = _loads(d.pop("cves_json"), [])
        d["flags"] = _loads(d.pop("flags_json"), [])
    evidence = select_evidence(rows, [r["signal"] for r in reasons], MAX_EVIDENCE)
    for i, d in enumerate(evidence, 1):
        d["ref"] = f"E{i}"
    return AccountContext(account=a, evidence=evidence, reasons=reasons)
