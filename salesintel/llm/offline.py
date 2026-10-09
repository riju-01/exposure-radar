"""Offline implementations of every LLM feature.

They serve two purposes:
1. Graceful degradation: no API key, budget exhausted, refusal or invalid output -> the
   app still returns a useful, clearly-labelled result instead of an error.
2. Baseline: `rules-v0` is the bar each prompt version must beat in evals. If a prompt
   can't beat keyword rules, it shouldn't ship.
"""
from __future__ import annotations

import re

from salesintel.enrich import rule_industry
from salesintel.etl.resolve import is_infra_org
from salesintel.llm.context import AccountContext
from salesintel.llm.schemas import PROSPECT_ENTITY_TYPES

_HOSTING_HINT = re.compile(
    r"(hosting|\bhost|server|\bvps\b|\bidc\b|datacenter|data center|colo|cloud|layer|proxy|vpn\b|"
    r"\bcdn\b|dedicated|bulletproof|\bnet\b|networks?\b)", re.I)
_TELCO_HINT = re.compile(r"(telecom|broadband|fib(er|re)|cable|\bisp\b|mobile|wireless|dsl)", re.I)
_PLATFORM_HINT = re.compile(r"(\.(myshopify|wixsite|squarespace|herokuapp|netlify|vercel)\.|saas|platform)", re.I)
_PERSONAL_HINT = re.compile(r"(\.(ddns|duckdns|no-ip|synology|myfritz)\.|homelab|my nas|personal)", re.I)

_INDUSTRY_TO_ENTITY = {
    "education": "education", "government": "government", "healthcare": "healthcare_provider",
    "nonprofit": "nonprofit", "hosting_telecom": "hosting_provider",
}


def classify_rules(ctx: AccountContext) -> dict:
    a = ctx.account
    name_blob = " ".join(filter(None, [a["name"], a["domain"], " ".join(a["cert_orgs"])]))
    titles = " ".join(a["titles"][:5])
    industry, clue = rule_industry(a["domain"], a["name"], " ; ".join(a["cert_orgs"]), titles)

    if _PERSONAL_HINT.search(name_blob):
        entity = "individual_or_hobby"
    elif _TELCO_HINT.search(name_blob) and not a["domain"]:
        entity = "telecom_isp"
    elif _HOSTING_HINT.search(name_blob) or (not a["domain"] and is_infra_org(a["name"])):
        entity = "hosting_provider"
    elif _PLATFORM_HINT.search(name_blob):
        entity = "cdn_saas_platform"
    else:
        entity = _INDUSTRY_TO_ENTITY.get(industry, "business")

    if entity in ("hosting_provider", "telecom_isp", "cdn_saas_platform"):
        industry = "hosting_telecom"
    return {
        "entity_type": entity,
        "is_prospect": entity in PROSPECT_ENTITY_TYPES,
        "industry": industry,
        "confidence": "low",
        "rationale": f"keyword rules (matched: {clue or 'no industry keyword'})",
    }


def outreach_template(ctx: AccountContext) -> dict:
    a = ctx.account
    top = ctx.reasons[:3]
    why_now = []
    for r in top:
        refs = [e["ref"] for e in ctx.evidence if r["signal"] in e["flags"]][:3]
        why_now.append({"point": r["text"], "evidence": refs})
    who = a["name"] if not a["domain"] else a["domain"]
    bullets = "\n".join(f"- {r['text']}" for r in top)
    body = (
        f"Hi {{first_name}},\n\n"
        f"Our external attack-surface monitoring picked up a few internet-facing findings for {who} "
        f"that teams usually want to know about early:\n\n{bullets}\n\n"
        f"These come from public scan data, so they may already be on your radar. If useful, I can share "
        f"the full list with remediation priorities. No strings attached.\n\n"
        f"Would a 15-minute call next week be worthwhile?\n\nBest,\n{{sender_name}}"
    )
    return {
        "summary": f"{who}: {a['n_ips']} internet-facing IPs; need {a['need']}/100. "
                   f"Top issue: {top[0]['text'] if top else 'n/a'}.",
        "why_now": why_now,
        "talk_track": [
            "Open with the specific finding, not the product.",
            "Position as an outside-in view they may not have; offer the full findings list.",
            "Ask who owns external exposure / patching today.",
        ],
        "email": {"subject": f"External exposure findings for {who}"[:80], "body": body},
        "cited_cves": [],  # the template never names CVEs; that judgement is left to the LLM
    }


OFFLINE = {
    "account-classify": ("rules-v0", classify_rules),
    "outreach-draft": ("template-v0", outreach_template),
}
