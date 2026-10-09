import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def make_account(**over) -> dict:
    base = {
        "id": 1, "account_key": "acme-dental.com", "name": "acme-dental.com", "domain": "acme-dental.com",
        "key_source": "http_host", "country_code": "US", "country": "United States", "region": "North America",
        "segment": "small", "hosting": "self_hosted", "industry": "healthcare", "industry_source": "rule",
        "industry_clue": "dental", "entity_type": None, "is_prospect": 1, "prospect_source": "rule",
        "n_ips": 3, "n_services": 9, "n_ports": 3, "n_cves": 4, "max_cvss": 9.8, "max_epss": 0.97,
        "need": 80, "fit": 80, "priority": 80, "tier": "A", "top_reason": "x",
        "titles": ["Acme Dental &amp; Implants"], "products": ["Apache httpd"], "hosts": ["www.acme-dental.com", "1.2.3.4"],
        "hosting_orgs": ["Comcast"], "cert_orgs": [], "primary_org": "Comcast", "waf_share": 0.0,
        "has_security_txt": 0, "last_seen": "2026-10-08",
    }
    base.update(over)
    return base


def make_context(**over):
    from salesintel.llm.context import AccountContext

    evidence = [
        {"ref": "E1", "ip": "1.2.3.4", "port": 443, "transport": "tcp", "module": "https", "product": "Apache httpd",
         "version": "2.4.49", "http_title": "Acme", "http_host": "www.acme-dental.com", "ssl_cn": None, "org": "Comcast",
         "country_code": "US", "seen_at": "2026-10-08", "cves": ["CVE-2021-41773", "CVE-2021-42013"], "n_cves": 2,
         "max_cvss": 9.8, "max_epss": 0.97, "kev_count": 1, "flags": ["kev_vuln"], "severity": 35},
        {"ref": "E2", "ip": "1.2.3.5", "port": 3389, "transport": "tcp", "module": "rdp", "product": None,
         "version": None, "http_title": None, "http_host": None, "ssl_cn": None, "org": "Comcast",
         "country_code": "US", "seen_at": "2026-10-08", "cves": [], "n_cves": 0, "max_cvss": None,
         "max_epss": None, "kev_count": 0, "flags": ["remote_access"], "severity": 16},
    ]
    reasons = [
        {"signal": "kev_vuln", "label": "Known-exploited CVE", "category": "vulnerability", "count": 1, "points": 35,
         "text": "1 asset running software with vulnerabilities on CISA's Known Exploited list"},
        {"signal": "remote_access", "label": "Remote access exposed", "category": "exposure", "count": 1, "points": 16,
         "text": "1 remote-access service (RDP/SMB/Telnet/VNC) open to the internet"},
    ]
    return AccountContext(account=make_account(**over), evidence=evidence, reasons=reasons)


@pytest.fixture
def ctx():
    return make_context()


@pytest.fixture
def tracer(tmp_path):
    from salesintel.llm.tracing import Tracer

    return Tracer(db_path=tmp_path / "traces.sqlite", jsonl_path=tmp_path / "traces.jsonl")


@pytest.fixture
def app_db(tmp_path, monkeypatch):
    """A tiny app.db with two accounts, wired into config for API tests."""
    from salesintel import config, db

    path = tmp_path / "app.db"
    monkeypatch.setattr(config, "APP_DB", path)
    monkeypatch.setattr(config, "TRACES_DB", tmp_path / "traces.sqlite")
    monkeypatch.setattr(config, "TRACES_JSONL", tmp_path / "traces.jsonl")
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
    import salesintel.llm.tracing as tracing
    monkeypatch.setattr(tracing, "_default", None)

    con = db.connect(path)
    db.init(con)
    for i, (name, tier, prospect, industry) in enumerate(
            [("acme-dental.com", "A", 1, "healthcare"), ("org:Big Hosting Ltd", "C", 0, "hosting_telecom")], 1):
        a = make_account(id=i, account_key=name, name=name.removeprefix("org:"), tier=tier, is_prospect=prospect,
                         industry=industry, domain=None if name.startswith("org:") else name)
        con.execute(
            """insert into accounts(id, account_key, name, domain, key_source, country_code, country, region, segment,
                 hosting, industry, industry_source, is_prospect, prospect_source, n_ips, n_services, n_ports, n_cves,
                 max_cvss, max_epss, need, fit, priority, tier, top_reason, reasons_json, fit_json, signals_json,
                 titles_json, products_json, hosts_json, hosting_orgs_json, cert_orgs_json, primary_org, last_seen)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (i, a["account_key"], a["name"], a["domain"], "http_host", "US", "United States", "North America",
             "small", "self_hosted", industry, "rule", prospect, "rule", 3, 9, 3, 2, 9.8, 0.97, 80, 80, 80, tier,
             "reason", json.dumps(make_context().reasons), json.dumps({"segment": 0.8}),
             json.dumps({"kev_vuln": 1, "remote_access": 1}), "[]", "[]", "[]", "[]", "[]", "Comcast", "2026-10-08"))
        con.execute("insert into account_signals values (?, 'kev_vuln', 1)", (i,))
        for e in make_context().evidence:
            con.execute(
                """insert into evidence(account_id, ip, port, transport, module, product, version, http_title, http_host,
                     ssl_cn, org, country_code, seen_at, cves_json, n_cves, max_cvss, max_epss, kev_count, flags_json, severity)
                   values (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (i, e["ip"], e["port"], e["transport"], e["module"], e["product"], e["version"], e["http_title"],
                 e["http_host"], e["ssl_cn"], e["org"], e["country_code"], e["seen_at"], json.dumps(e["cves"]),
                 e["n_cves"], e["max_cvss"], e["max_epss"], e["kev_count"], json.dumps(e["flags"]), e["severity"]))
    con.commit()
    con.close()
    os.environ["SI_APP_DB"] = str(path)
    return path
