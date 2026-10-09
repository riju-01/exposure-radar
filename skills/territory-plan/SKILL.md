---
name: territory-plan
version: 1.0.0
description: >
  Build a rep's weekly call list for a territory: pull the highest-priority accounts, verify each is a
  real prospect with account-classify, pre-draft outreach for the survivors, and return a ranked plan.
  Use for "plan my week in <region/country/industry>" or a Monday pre-generation job.
depends_on: [account-classify, outreach-draft]
owner: sales-intel
---

# territory-plan

## When to use
- "Give me my top 10 accounts in DACH healthcare this week."
- A scheduled weekly job that pre-warms drafts for each rep's territory (cost: see the cost model).

## Inputs
| name | example | notes |
|---|---|---|
| territory filters | `--region Europe --country DE --industry healthcare` | any mix of region, country, industry, tier, signal |
| `n` | 10 | accounts to return |
| `overfetch` | 2x | fetch extra so accounts dropped by classification can be backfilled |

## Procedure
1. **Rank (rules, free):** `python -m salesintel.cli top --region Europe --industry healthcare --tier A --tier B --n 20`
2. **Verify (cheap model):** for each account without an LLM classification:
   `python -m salesintel.cli classify <account>`. Drop results with `is_prospect=false`; flag `confidence=low` for a human.
3. **Re-rank:** classification can change industry → fit → priority. Re-run step 1 and take the top `n` verified accounts.
4. **Draft (strong model):** `python -m salesintel.cli outreach <account>` for each of the top `n`.
   Keep drafts whose `checks.passed` is true; list the rest under "needs review" with their issues.
5. **Report:** a table of rank, account, tier, priority, the one-line "why now", draft status, and a link
   `/accounts/<id>`.

## Outputs
Markdown table + a JSON list `[{account, id, tier, priority, why_now, classification, draft_status}]`.

## Cost guardrails
- Classification ≈ $0.0001 per account (Haiku), drafts ≈ $0.017 (Sonnet), so a 10-account plan costs about $0.20.
- All calls go through the runner, so the daily budget (`SI_LLM_DAILY_BUDGET_USD`) applies; on budget exhaustion
  the runner returns rule/template fallbacks, clearly labelled, instead of failing.

## Worked example
```bash
python -m salesintel.cli top --region Europe --industry healthcare --n 3
# -> ooda-health.io (A, 91), pharmagest.com (B, 78), "LNPUT657 Puteaux" (B, 78)
python -m salesintel.cli classify "LNPUT657 Puteaux"   # org-keyed name: verify before drafting
python -m salesintel.cli outreach ooda-health.io
```
