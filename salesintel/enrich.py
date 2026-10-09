"""Cheap deterministic enrichment: region from country, industry and segment from text clues.

Rule-based industry is intentionally conservative: it only fires on unambiguous clues
(TLDs like .edu/.gov, strong keywords). Everything else is `unknown` and is left for the
LLM classifier, whose quality we measure in evals/.
"""
from __future__ import annotations

import re

_REGIONS = {
    "North America": "US CA MX PR",
    "Oceania": "AU NZ FJ PG",
    "Europe": ("GB IE FR DE NL BE LU CH AT IT ES PT DK SE NO FI IS PL CZ SK HU RO BG GR HR SI RS BA "
               "ME MK AL EE LV LT UA BY MD RU CY MT LI MC AD SM GI JE GG IM FO XK"),
    "APAC": ("CN HK MO TW JP KR KP SG MY TH VN PH ID IN PK BD LK NP BT MM KH LA BN MN KZ UZ KG TJ TM "
             "AF MV TL"),
    "Middle East & Africa": ("AE SA QA KW BH OM YE IL PS JO LB SY IQ IR TR EG LY TN DZ MA ZA NG KE GH "
                             "ET TZ UG RW CI SN CM AO ZM ZW MZ NA BW MU SD SS SO DJ ER GA CG CD BJ TG "
                             "BF ML NE TD MR GM GN SL LR MG MW LS SZ RE SC CV"),
    "Latin America": ("BR AR CL CO PE VE EC BO PY UY GY SR GT HN SV NI CR PA CU DO HT JM TT BS BB BZ "
                      "AW CW KY VG VI AG DM GD KN LC VC"),
}
COUNTRY_REGION = {cc: region for region, ccs in _REGIONS.items() for cc in ccs.split()}


def region_of(country_code: str | None) -> str:
    return COUNTRY_REGION.get((country_code or "").upper(), "Other")


def segment_of(n_ips: int, segments: dict) -> str:
    for name, spec in segments.items():  # ordered smallest -> largest in icp.yaml
        if n_ips <= spec["max_ips"]:
            return name
    return list(segments)[-1]


def hosting_of(cloud_share: float) -> str:
    if cloud_share >= 0.7:
        return "cloud"
    if cloud_share <= 0.2:
        return "self_hosted"
    return "hybrid"


# (industry, regex over "domain | org-name | page titles"), first match wins.
_INDUSTRY_RULES: list[tuple[str, re.Pattern]] = [
    ("education", re.compile(
        r"(\.edu(\.[a-z]{2})?\b|\.ac\.[a-z]{2}\b|\.k12\.|\buniv|universit|college|school|academy|"
        r"hochschule|campus|\.sch\.[a-z]{2}\b)", re.I)),
    ("government", re.compile(
        r"(\.gov(\.[a-z]{2})?\b|\.gob\.|\.gouv\.|\.go\.[a-z]{2}\b|\.mil\b|\bcity of\b|\bcounty\b|"
        r"municipal|ministry|ministerio|\bstate of\b|government)", re.I)),
    ("healthcare", re.compile(
        r"(hospital|health|clinic|medical|medic|pharma|dental|\bcare\b|healthcare|patient|"
        r"klinik|surgery|radiology|\bnhs\b|biotech|laborator)", re.I)),
    ("finance", re.compile(
        r"(\bbank|banco|banque|credit union|\bcredit\b|capital|finance|financial|insurance|"
        r"versicherung|invest|securities|\bpay(ment)?s?\b|fintech|lending|mortgage|wealth)", re.I)),
    ("hosting_telecom", re.compile(
        r"(hosting|\bhost\b|datacenter|data center|\bvps\b|telecom|broadband|\bisp\b|"
        r"\bcloud\b|internet service)", re.I)),
    ("manufacturing", re.compile(
        r"(manufactur|industrial|industries|factory|automotive|engineering|\bsteel\b|chemical|"
        r"machinery|\bplant\b|electronics|semiconductor)", re.I)),
    ("retail_ecommerce", re.compile(
        r"(\bshop\b|store|retail|e-?commerce|boutique|marketplace|\bcart\b|fashion|grocery)", re.I)),
    ("nonprofit", re.compile(r"(foundation|charity|non-?profit|\bngo\b|association|\.org\b)", re.I)),
    ("media_entertainment", re.compile(
        r"(\bmedia\b|news|radio|\btv\b|television|magazine|publishing|entertainment|music|"
        r"gaming|\bgames?\b|studio)", re.I)),
    ("professional_services", re.compile(
        r"(\blaw\b|legal|attorney|accounting|consult|advisory|\bcpa\b|architects?|agency)", re.I)),
    ("technology", re.compile(
        r"(software|\btech\b|technolog|\bsaas\b|\.io\b|\.dev\b|\bapi\b|digital|solutions|systems)", re.I)),
]


def rule_industry(*texts: str | None) -> tuple[str, str | None]:
    """Return (industry, matched_text) or ('unknown', None)."""
    blob = " | ".join(t for t in texts if t)
    for industry, rx in _INDUSTRY_RULES:
        m = rx.search(blob)
        if m:
            return industry, m.group(0)
    return "unknown", None
