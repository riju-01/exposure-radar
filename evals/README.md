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

## Current results (measured on Gemini free tier, 2026-10-12)
Measured on Google's free tier because no paid Claude key was available: classify on
`gemini-3.5-flash-lite` (cheap tier), outreach on `gemini-3-flash-preview` / `gemini-3.8-flash` (strong tier).

**account-classify**, 30 examples, two live runs (the model is not deterministic, so both are shown):

| version | prospect precision | recall | F1 | entity-type acc | industry acc | hard-case acc |
|---|---|---|---|---|---|---|
| rules baseline | 0.56 | 0.93 | 0.70 | 0.43 | 0.53 | 0.3 |
| v1 | 0.63 / 0.71 | 1.00 / 1.00 | 0.77 / 0.83 | 0.80 / 0.77 | 0.87 / 0.90 | 0.5 / 0.5 |
| **v2 (active)** | **0.94 / 1.00** | **1.00 / 1.00** | **0.97 / 1.00** | 0.83 / 0.93 | 0.93 / 1.00 | **1.0 / 1.0** |

- **v1's characteristic failure:** it labels an ISP correctly as `telecom_isp` and still says `is_prospect: true`
  (3–5 cases per run; see `consistency_violations`), because v1 never defines what a prospect is. v2's explicit
  decision procedure removes it entirely.
- **v2's remaining misses (run 1):** `zonevs.eu`, a VPS host read as a self-hosting business; `liquidnetlimited.com`,
  entity "unknown" (prospect decision still correct). With 30 examples, one example = 3 points: v2's 0.94 vs 1.00
  across runs is noise, while v1 → v2 (+0.23 to +0.31 precision) is not.
- p50 latency ~1.1 s per classification (run 2, rate-limit pacing excluded).

**outreach-draft**, 9 examples (run 1; run 2 hit the free-tier daily quota, see `REPORT.md`):

| version | guardrail pass | CVE grounding | supported points | fallback rate |
|---|---|---|---|---|
| template baseline | 1.00 | 1.00 | 1.00 | 0 |
| v1 | 0.67 (3 failures in 8 live drafts) | 1.00 | 1.00 | 0.11 |
| **v2 (active)** | **1.00 (0 failures in 8 live drafts)** | 1.00 | 1.00 | 0.11 |

v1's failures are exactly what v2 targets: "hacked" in a compromise-indicator email, a malformed evidence id
(`desert E4`), and an over-long subject. The template baseline passes trivially because it never names a CVE
(safe but bland), which is why guardrails alone don't measure quality; see the human rubric above.
Run 1 latency (~21 s p50) includes free-tier rate-limit pacing and 503 retries, so it is not model latency.

**Caveats.** These are Gemini numbers; the production target is Claude (Haiku 5.5 / Sonnet 5.5), so re-run
before promoting on Claude. The labels are still `reviewed: false`.
```bash
python -m evals.run --versions baseline v1 v2      # re-run all versions (needs GEMINI_API_KEY or ANTHROPIC_API_KEY)
```
