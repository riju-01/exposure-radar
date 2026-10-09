"""Cost model: tokens x volume x frequency, per feature, with the production ceilings we enforce.

Token sizes start as estimates (measured by rendering the real prompts for real accounts:
~4 chars/token) and are replaced automatically by measured averages from the trace store once
live calls exist. `python -m salesintel.llm.cost_model` prints the table used in ARCHITECTURE.md.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from salesintel.llm.pricing import PRICES_PER_MTOK


@dataclass
class FeatureCostModel:
    feature: str
    model: str
    why_this_model: str
    est_input_tokens: int
    est_output_tokens: int          # includes adaptive-thinking tokens, billed as output
    volume_note: str
    calls_per_month: int
    batch_discount: bool = False     # Message Batches API: 50% off, async (fine for backfills)

    def cost_per_call(self, input_tokens: int | None = None, output_tokens: int | None = None) -> float:
        p_in, p_out = PRICES_PER_MTOK[self.model]
        i = input_tokens or self.est_input_tokens
        o = output_tokens or self.est_output_tokens
        c = (i * p_in + o * p_out) / 1_000_000
        return c * (0.5 if self.batch_discount else 1.0)

    def monthly(self, **kw) -> float:
        return self.cost_per_call(**kw) * self.calls_per_month


# Volumes for a 10-rep team working ~13.5k A/B-tier prospects (the current pipeline output).
MODELS = [
    FeatureCostModel(
        feature="account-classify (backfill)", model="claude-haiku-5-5",
        why_this_model="High-volume, narrow classification with a strict enum schema: the cheapest tier is enough; "
                       "the eval decides whether it holds.",
        est_input_tokens=950, est_output_tokens=200,
        volume_note="one-off: every A/B-tier account (~13.5k), via Batches API", calls_per_month=13_500,
        batch_discount=True),
    FeatureCostModel(
        feature="account-classify (weekly refresh)", model="claude-haiku-5-5",
        why_this_model="Same as backfill; only new/changed accounts (cache keyed on account + prompt version).",
        est_input_tokens=950, est_output_tokens=200,
        volume_note="~10% weekly churn of A/B accounts = ~1.35k/week", calls_per_month=5_850, batch_discount=True),
    FeatureCostModel(
        feature="outreach-draft (on demand)", model="claude-sonnet-5-5",
        why_this_model="Judgement + writing that a prospect reads: quality matters more than cost; "
                       "Opus would be ~2x the price for marginal gain on a 120-word email.",
        est_input_tokens=1_100, est_output_tokens=1_500,
        volume_note="10 reps x 25 drafts/day x 22 days (cached: regenerate only on request)", calls_per_month=5_500),
    FeatureCostModel(
        feature="outreach-draft (weekly pre-generation)", model="claude-sonnet-5-5",
        why_this_model="Pre-warm the top 500 A-tier accounts so reps open a ready draft.",
        est_input_tokens=1_100, est_output_tokens=1_500,
        volume_note="top 500 A-tier x 4.3 weeks, via Batches API", calls_per_month=2_150, batch_discount=True),
]

# Production guardrails (enforced: SI_LLM_DAILY_BUDGET_USD in the runner; the rest are the proposed policy).
CEILINGS = {
    "daily_budget_usd (production proposal; runner enforces SI_LLM_DAILY_BUDGET_USD, demo = 2)": 10.0,
    "monthly_ceiling_usd (alert at 80%)": 200.0,
    "per_rep_drafts_per_day": 40,
    "max_tokens_classify": 1_500,
    "max_tokens_outreach": 4_000,
}


def table(measured: dict[str, tuple[int, int]] | None = None) -> list[dict]:
    """measured: feature-prefix -> (avg_input_tokens, avg_output_tokens) from traces."""
    rows = []
    for m in MODELS:
        tok = next((v for k, v in (measured or {}).items() if m.feature.startswith(k)), None)
        kw = {"input_tokens": tok[0], "output_tokens": tok[1]} if tok else {}
        rows.append({**asdict(m), "tokens_source": "measured" if tok else "estimate",
                     "cost_per_call_usd": round(m.cost_per_call(**kw), 6),
                     "monthly_usd": round(m.monthly(**kw), 2)})
    return rows


def what_if() -> list[dict]:
    """The same workloads on other models: the case for per-task model choice."""
    out = []
    for m in MODELS:
        for alt in PRICES_PER_MTOK:
            alt_m = FeatureCostModel(**{**asdict(m), "model": alt})
            out.append({"feature": m.feature, "model": alt, "monthly_usd": round(alt_m.monthly(), 2)})
    return out


if __name__ == "__main__":
    total = 0.0
    print(f"{'feature':<42}{'model':<20}{'$/call':>10}{'calls/mo':>10}{'$/month':>10}")
    for r in table():
        total += r["monthly_usd"]
        print(f"{r['feature']:<42}{r['model']:<20}{r['cost_per_call_usd']:>10.5f}{r['calls_per_month']:>10,}{r['monthly_usd']:>10.2f}")
    print(f"{'TOTAL':<82}{total:>10.2f}")
    print("\nwhat-if (monthly USD):")
    for r in what_if():
        print(f"  {r['feature']:<42}{r['model']:<20}{r['monthly_usd']:>10.2f}")
