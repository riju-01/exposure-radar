"""The buying-signal registry: the single definition of every signal.

Each signal is a record-level SQL predicate over the slim scan schema plus the metadata the
rest of the system needs: a scoring weight, a category, and rep-facing wording. Feature
extraction, scoring, the API, the UI and the LLM evidence pack all read from this list,
so adding a signal is a one-place change.

Weights are "points of need" for a single affected asset (see scoring.py for how counts and
weights combine). They encode a simple severity ladder:
    confirmed exploitation / compromise  >  unauthenticated exposure  >  known critical CVE
    >  risky exposed service  >  outdated software  >  hygiene
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Signal:
    key: str
    label: str           # short UI label
    category: str        # vulnerability | exposure | hygiene | sensitive
    weight: float        # need points for one affected asset
    predicate: str       # DuckDB SQL over one slim record
    explain: str         # rep-facing sentence; {n} = affected asset count

    def sentence(self, n: int) -> str:
        return self.explain.format(n=n, s="" if n == 1 else "s")


ICS_MODULES = "('dnp3','s7','modbus','bacnet','iec-104','iec-61850','fox','codesys','ethernetip','opc-ua')"
REMOTE_ACCESS_MODULES = "('rdp','rdpeudp','telnet','smb','vnc','x11','netbios')"
DATABASE_MODULES = "('mysql','postgresql','ms-sql','mongodb','redis','elastic','couchdb','memcache','cassandra','etcd')"

SIGNALS: list[Signal] = [
    Signal("compromise_indicator", "Indicators of compromise", "sensitive", 45,
           "list_contains(tags, 'compromised')",
           "{n} asset{s} flagged by the scanner with indicators of compromise"),
    Signal("kev_vuln", "Known-exploited CVE", "vulnerability", 35,
           "coalesce(vuln_kev, 0) > 0",
           "{n} asset{s} running software with vulnerabilities on CISA's Known Exploited list"),
    Signal("db_unauth", "Open database (no auth)", "exposure", 32,
           "coalesce(db_noauth, false)",
           "{n} database{s} reachable from the internet without authentication"),
    Signal("remote_unauth", "Remote desktop (no auth)", "exposure", 30,
           "coalesce(vnc_noauth, false)",
           "{n} remote-desktop service{s} accepting connections without a password"),
    Signal("ics_exposed", "Industrial control exposed", "sensitive", 30,
           f"(list_contains(tags, 'ics') or module in {ICS_MODULES})",
           "{n} industrial-control system interface{s} exposed to the internet"),
    Signal("medical_exposed", "Medical device exposed", "sensitive", 30,
           "list_contains(tags, 'medical')",
           "{n} medical device/system interface{s} exposed to the internet"),
    Signal("likely_exploited", "High exploit likelihood", "vulnerability", 22,
           "coalesce(vuln_max_epss, 0) >= 0.1 and coalesce(vuln_kev, 0) = 0",
           "{n} asset{s} with vulnerabilities rated likely to be exploited (EPSS >= 10%)"),
    Signal("critical_cve", "Critical CVE (CVSS 9+)", "vulnerability", 15,
           "coalesce(vuln_max_cvss, 0) >= 9 and coalesce(vuln_max_epss, 0) < 0.1 and coalesce(vuln_kev, 0) = 0",
           "{n} asset{s} with critical-severity (CVSS 9+) vulnerabilities"),
    Signal("remote_access", "Remote access exposed", "exposure", 16,
           f"module in {REMOTE_ACCESS_MODULES} and not coalesce(vnc_noauth, false)",
           "{n} remote-access service{s} (RDP/SMB/Telnet/VNC) open to the internet"),
    Signal("db_exposed", "Database exposed", "exposure", 12,
           f"(module in {DATABASE_MODULES} or list_contains(tags, 'database')) and not coalesce(db_noauth, false)",
           "{n} database port{s} open to the internet"),
    Signal("ftp_anonymous", "Anonymous FTP", "exposure", 12,
           "coalesce(ftp_anon, false)",
           "{n} FTP server{s} allowing anonymous login"),
    Signal("open_directory", "Open directory listing", "exposure", 10,
           "list_contains(tags, 'open-dir')",
           "{n} web server{s} publicly listing directory contents"),
    Signal("ai_devops_exposed", "AI / DevOps tooling exposed", "exposure", 10,
           "list_has_any(tags, ['ai', 'mcp', 'devops'])",
           "{n} AI or DevOps tool{s} (e.g. model servers, MCP, CI) exposed to the internet"),
    Signal("eol_software", "End-of-life software", "vulnerability", 10,
           "list_has_any(tags, ['eol-product', 'eol-os'])",
           "{n} asset{s} running end-of-life software that no longer receives security patches"),
    Signal("vpn_gateway", "VPN gateway (watch for patches)", "exposure", 4,
           "list_contains(tags, 'vpn')",
           "{n} VPN gateway{s}, a frequent target for initial access"),
    Signal("weak_tls", "Outdated TLS/SSL", "hygiene", 5,
           "list_has_any(ssl_versions, ['SSLv2', 'SSLv3', 'TLSv1'])",
           "{n} service{s} still accepting outdated encryption (SSLv3/TLS 1.0)"),
    Signal("expired_cert", "Expired certificate", "hygiene", 5,
           "coalesce(ssl_expired, false)",
           "{n} service{s} presenting an expired TLS certificate"),
    Signal("self_signed", "Self-signed certificate", "hygiene", 2,
           "list_contains(tags, 'self-signed')",
           "{n} service{s} using self-signed certificates"),
]

BY_KEY: dict[str, Signal] = {s.key: s for s in SIGNALS}


def record_severity_sql() -> str:
    """SQL expression ranking a single record by its worst signals (used to pick evidence)."""
    return " + ".join(f"(case when {s.predicate} then {s.weight} else 0 end)" for s in SIGNALS)


def record_flags_sql() -> str:
    """SQL expression: list of signal keys a record triggers."""
    items = ", ".join(f"case when {s.predicate} then '{s.key}' end" for s in SIGNALS)
    return f"list_filter([{items}], x -> x is not null)"
