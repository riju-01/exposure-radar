# Evals

```bash
python -m evals.run                    # all features: baseline + active + candidate prompt
python -m evals.run account-classify   # one feature
python -m evals.run outreach-draft --versions v2 --limit 3
```
Results: [`results/REPORT.md`](results/REPORT.md) (regenerated each run), `results/<feature>/<run_id>.json`
(every prediction with its trace `call_id`), `results/history.jsonl` (one summary line per version per run, used
for the "vs. previous run" deltas). LLM calls made by evals are traced with `env = 'eval'` and the run id.

## Layout
| path | what | edited by |
|---|---|---|
| `labels/account-classify.yaml` | gold labels + labelling policy + provenance | humans |
| `sample_candidates.py` | prints stratified candidate cards for labelling | – |
| `freeze.py` | labels + exact account context → `datasets/*.jsonl` | – |
| `datasets/*.jsonl` | frozen inputs (eval doesn't drift when the pipeline changes) | generated |
| `run.py` | the harness | – |

## account-classify: what we measure and why
The product risk is **calling a hosting company or ISP as if it were a buyer** (wasted rep time, an odd email)
and **dropping real buyers**. So the headline metrics are:
- **prospect precision**: of the accounts we keep as prospects, how many really are (protects rep time);
- **prospect recall**: of the real prospects, how many we keep (protects pipeline);
- entity-type accuracy, industry accuracy (with `industry_ok` for honestly ambiguous cases), accuracy by difficulty.

30 examples, 15/15 balanced, stratified: top-tier accounts, org-keyed (no domain), domain-keyed, rDNS-keyed and
deliberate keyword traps ("Korbank", "SoftBank", "bankruptcyattorney…"). The 4 accounts used as few-shot
examples in prompt v2 are excluded.

**Defending the weaknesses of this set:** 30 examples give wide confidence intervals (one flip ≈ 3 points of
accuracy), so treat differences under ~10 points as noise. Labels encode a policy (telcos/hosters are never
prospects). Several org-keyed labels rely on public knowledge of the organisation, which the model may or may not
share. The set over-represents hard cases relative to production on purpose.

**Label provenance:** drafted by the author with an AI assistant from each card plus public knowledge, marked
`reviewed: false`. A second human pass is required before the numbers are used for a promotion decision.

## outreach-draft: what we measure
Deterministic checks from `salesintel/llm/grounding.py`, run on 9 strong prospects (one per signal family):
guardrail pass rate, CVE grounding rate (cited CVEs ⊆ evidence), supported why-now points (each cites evidence
ids), average email length. These catch the dangerous failures (hallucinated CVEs, breach language, leaked IPs).
They do **not** measure persuasiveness. For that, score a sample by hand on this rubric (1–3 each): *specific*
(names the real issue), *helpful* (a reader learns something), *calm* (no fear-selling), *clear ask*.

## Promotion rule
A candidate replaces the active prompt (edit `prompts/registry.yaml`) only if, on the latest run,
it is no worse on precision **and** recall by more than one example, and it is better on F1 (classify) or
guardrail pass rate (outreach). Record the decision in the prompt's `changelog`.

## Current status
No API key was available while building, so only the baselines have numbers so far (see `REPORT.md`):
rules baseline for classify: precision 0.56, recall 0.93, F1 0.70; template baseline for outreach: 100%
guardrail pass (it never names a CVE, which is safe but bland). To produce v1-vs-v2:
```bash
set ANTHROPIC_API_KEY=sk-ant-...      # PowerShell: $env:ANTHROPIC_API_KEY="sk-ant-..."
python -m evals.run                   # ≈ 30×2 Haiku + 9×2 Sonnet calls ≈ $0.35
```
