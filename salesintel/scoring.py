"""Rule-based account scoring: Need x Fit -> Priority -> Tier, with plain-English reasons.

Scoring is deliberately NOT an LLM task. A rep has to be able to answer "why is this an A?"
and get the same answer tomorrow; finance has to be able to audit why territory X got more
A-accounts than territory Y. Rules are deterministic, explainable, free, and testable.

need (0-100)     How urgently the account needs security help, from the signal registry.
                 Each signal contributes weight * volume_multiplier(count); the sum saturates
                 so that one KEV finding already scores high, and 40 hygiene issues cannot
                 outrank a single open database.
fit (0-100)      ICP match from config/icp.yaml: segment x region x industry x hosting.
priority         0.6 * need + 0.4 * fit.
tier             A = work this week, B = nurture / sequence, C = monitor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from functools import lru_cache

import yaml

from salesintel import config
from salesintel.enrich import hosting_of, region_of, segment_of
from salesintel.signals import SIGNALS

NEED_SATURATION = 45.0  # need = 100 * (1 - exp(-raw / NEED_SATURATION))
NEED_WEIGHT, FIT_WEIGHT = 0.6, 0.4

# Tier gates. A-tier requires real need, not just a great-looking ICP match.
TIER_A = {"priority": 68, "need": 70}
TIER_B = {"priority": 50, "need": 35}


@lru_cache(maxsize=1)
def load_icp() -> dict:
    with open(config.CONFIG_DIR / "icp.yaml", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def volume_multiplier(count: int) -> float:
    """1 asset -> 1.0x, 4 -> 1.5x, 16 -> 2.0x, capped at 2.0x: more exposure matters, with diminishing returns."""
    if count <= 0:
        return 0.0
    return min(2.0, 1.0 + 0.25 * math.log2(count))


@dataclass
class ScoreResult:
    need: int
    fit: int
    priority: int
    tier: str
    segment: str
    region: str
    hosting: str
    reasons: list[dict]          # [{signal, label, category, count, points, text}]
    fit_breakdown: dict[str, float]


def score_need(signal_counts: dict[str, int]) -> tuple[int, list[dict]]:
    contributions = []
    for s in SIGNALS:
        n = int(signal_counts.get(s.key) or 0)
        if n <= 0:
            continue
        pts = s.weight * volume_multiplier(n)
        contributions.append({"signal": s.key, "label": s.label, "category": s.category,
                              "count": n, "points": round(pts, 1), "text": s.sentence(n)})
    contributions.sort(key=lambda c: c["points"], reverse=True)
    raw = sum(c["points"] for c in contributions)
    need = round(100 * (1 - math.exp(-raw / NEED_SATURATION)))
    return need, contributions


def score_fit(*, n_ips: int, country_code: str | None, industry: str, cloud_share: float) -> tuple[int, dict, str, str, str]:
    icp = load_icp()
    segment = segment_of(n_ips, icp["segments"])
    region = region_of(country_code)
    hosting = hosting_of(cloud_share or 0.0)
    parts = {
        "segment": icp["segments"][segment]["weight"],
        "region": icp["regions"].get(region, icp["regions"]["Other"]),
        "industry": icp["industries"].get(industry, icp["industries"]["unknown"]),
        "hosting": icp["hosting"][hosting],
    }
    fit = round(100 * math.prod(parts.values()))
    return fit, parts, segment, region, hosting


def tier_of(priority: int, need: int) -> str:
    if priority >= TIER_A["priority"] and need >= TIER_A["need"]:
        return "A"
    if priority >= TIER_B["priority"] and need >= TIER_B["need"]:
        return "B"
    return "C"


def score_account(*, signal_counts: dict[str, int], n_ips: int, country_code: str | None,
                  industry: str, cloud_share: float) -> ScoreResult:
    need, reasons = score_need(signal_counts)
    fit, parts, segment, region, hosting = score_fit(
        n_ips=n_ips, country_code=country_code, industry=industry, cloud_share=cloud_share)
    priority = round(NEED_WEIGHT * need + FIT_WEIGHT * fit)
    return ScoreResult(need, fit, priority, tier_of(priority, need), segment, region, hosting,
                       reasons, parts)
