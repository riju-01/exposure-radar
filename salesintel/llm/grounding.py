"""Deterministic guardrails for generated outreach.

An LLM-written email that cites a CVE the prospect does not have, or reads like a breach
notification, damages trust far more than no email at all. Every draft is checked against
the evidence pack it was generated from, and the result is stored and shown to the rep
next to the draft. These checks are also the outreach eval's metrics.
"""
from __future__ import annotations

import re

from salesintel.llm.context import AccountContext

CVE_RE = re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I)
REF_RE = re.compile(r"\bE\d{1,3}\b")
PORT_RE = re.compile(r"\bport\s+(\d{1,5})\b", re.I)
IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")

# Phrases a first-touch security email must never use: they assert a breach or sound like a threat.
FORBIDDEN = [
    r"\bbreach(ed)?\b", r"\bhacked\b", r"\bhas been compromised\b", r"\bwere compromised\b",
    r"\byou are compromised\b", r"\bexploit code\b", r"\bproof[- ]of[- ]concept\b",
    r"\bwe (?:broke|got) in\b", r"\bguarantee", r"\b100% secure\b", r"\burgent(ly)?\b.*\bor else\b",
]
FORBIDDEN_RE = [re.compile(p, re.I) for p in FORBIDDEN]

MAX_EMAIL_WORDS = 180
MAX_SUBJECT_CHARS = 80


def _all_text(draft: dict) -> str:
    parts = [draft.get("summary", ""), draft["email"]["subject"], draft["email"]["body"]]
    parts += [w["point"] for w in draft.get("why_now", [])]
    parts += draft.get("talk_track", [])
    return "\n".join(parts)


def check_outreach(draft: dict, ctx: AccountContext) -> dict:
    issues: list[str] = []
    text = _all_text(draft)
    email = f"{draft['email']['subject']}\n{draft['email']['body']}"

    mentioned = {c.upper() for c in CVE_RE.findall(text)} | {c.upper() for c in draft.get("cited_cves", [])}
    unknown_cves = sorted(mentioned - ctx.cves)
    if unknown_cves:
        issues.append(f"cites CVEs not in evidence: {', '.join(unknown_cves)}")

    used_refs = {r for w in draft.get("why_now", []) for r in w.get("evidence", [])}
    unknown_refs = sorted(used_refs - ctx.refs)
    if unknown_refs:
        issues.append(f"references unknown evidence ids: {', '.join(unknown_refs)}")
    unsupported_points = [w["point"][:60] for w in draft.get("why_now", []) if not w.get("evidence")]
    if unsupported_points:
        issues.append(f"{len(unsupported_points)} why-now point(s) without evidence")

    unknown_ports = sorted({int(p) for p in PORT_RE.findall(text)} - ctx.ports)
    if unknown_ports:
        issues.append(f"mentions ports not in evidence: {unknown_ports}")

    if IPV4_RE.search(email):
        issues.append("email exposes raw IP addresses")
    hits = sorted({m.group(0).lower() for rx in FORBIDDEN_RE for m in rx.finditer(email)})
    if hits:
        issues.append(f"forbidden phrasing in email: {hits}")

    words = len(draft["email"]["body"].split())
    if words > MAX_EMAIL_WORDS:
        issues.append(f"email body too long ({words} words > {MAX_EMAIL_WORDS})")
    if len(draft["email"]["subject"]) > MAX_SUBJECT_CHARS:
        issues.append("subject line too long")
    if not draft.get("why_now"):
        issues.append("no why-now points")

    return {
        "passed": not issues,
        "issues": issues,
        "metrics": {
            "cves_mentioned": len(mentioned), "cves_grounded": len(mentioned) - len(unknown_cves),
            "why_now_points": len(draft.get("why_now", [])),
            "why_now_supported": len(draft.get("why_now", [])) - len(unsupported_points),
            "email_words": words,
        },
    }
