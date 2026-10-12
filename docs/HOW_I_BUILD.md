# How I build

## The dev loop
I worked in **Claude Code** (desktop app) as the main loop, with me directing and reviewing. The loop
for every stage was the same: **profile → hypothesis → small sample → full run → sanity-check the top of the
output → fix → commit**.

1. **Look before building.** Before any plan, Claude streamed the first 400 KB of the "dataset" and found
   it wasn't a company list but an 11.9 GB zstd Shodan banner dump. That one finding reshaped the whole product
   from "filter companies" to "turn attack-surface evidence into who to call and why".
2. **One expensive pass, then cheap iteration.** A parallel extractor turned 79 GB of JSON into 576 MB of
   Parquet in 3.4 minutes. Every later experiment (entity resolution, signals, scoring) ran in DuckDB in seconds.
3. **Eyeball the top-N after every change.** The highest-leverage habit. It caught: ISPs leaking in as
   accounts (*Ziply Fiber*, *Private Customer*), a provider flag that wrongly dropped `mit.edu`, page titles
   making a telecom "healthcare", and `bank.in` lumping banks together.
4. **Measure, then fix.** The eval harness ran against the rules baseline before any prompt was tuned, and
   immediately found a real product bug: some why-now points had no evidence row behind them, because the evidence
   pack was "top 12 by severity". Coverage-first evidence selection took supported points from 89% to 100%.
5. **Measure on a real model, then promote.** With a free Gemini key, `python -m evals.run` compared baseline,
   v1 and v2 on live calls. v2 won (classify precision 0.63 → 0.94, outreach guardrail failures 3 → 0), was promoted
   in `prompts/registry.yaml` with the numbers in its changelog, and then classified the top 300 A-tier accounts for real:
   57% turned out to be hosting companies, ISPs or hobby sites, and dropped out of the reps' list.
6. **Tests pin the behaviour** (54 tests: resolution helpers, scoring invariants, every guardrail, the runner's
   refusal / invalid-JSON / budget paths with a fake provider, API smoke tests on a fixture DB).

Tools: Claude Code for code, data exploration and docs; the in-app browser for checking the UI;
DuckDB for analysis; the Claude API reference skill for current model IDs, pricing and structured-output and
refusal-fallback parameters (so none of it came from memory).

## Where AI saved the most time
- **Data archaeology at scale.** Profiling 10.66M nested records, designing entity resolution and iterating
  on the provider heuristics took hours instead of days. Claude wrote the DuckDB queries, read the results
  and proposed the next rule.
- **Breadth of scaffolding.** Prompt registry, tracer, runner with fallbacks, eval harness, typed API client,
  three-page UI and tests were each a single focused pass, with consistent patterns across files.
- **Writing the adversarial cases.** Guardrail tests (fake CVEs, IPs in email, breach language) and eval
  traps (*Korbank*, *SoftBank*, `bankruptcyattorney…`) came out of the model readily once the policy was stated.

## Where AI cost more than doing it by hand
- **Shell-escaped edits.** Python-in-heredoc patching on Windows mangled a `"\n"` into a real newline once, and
  a convoluted f-string slipped in once. Both were caught immediately, but a plain editor edit would have been faster.
- **A first-draft SQL join on an `OR` condition** turned into a nested-loop join over 10M rows and hung for 10
  minutes. A human who knows DuckDB's planner writes two joins and a `UNION ALL` first time.
- **Labels.** The model can draft gold labels quickly, but every draft label is a claim about a real organisation.
  Checking them is human work, and the labels are marked unreviewed until that's done.

## One known weakness to flag to a teammate
**The eval numbers are Gemini numbers on 30 self-drafted labels, not Claude numbers on reviewed labels.**
No paid Claude key was available, so the prompts were measured on Google's free tier (v2 classify: precision
0.94–1.00, recall 1.00; v2 outreach: 0 guardrail failures in 8 live drafts). That proves the harness and the
v1 → v2 improvement, but the production design targets Claude. First jobs for whoever picks this up: have a second person
review `evals/labels/account-classify.yaml` (labels are marked `reviewed: false`), then set `ANTHROPIC_API_KEY`
and re-run `python -m evals.run --versions baseline v1 v2` (~$0.35) before trusting the promotion on Claude.
Free-tier quotas also make Gemini unfit for production traffic: one eval run exhausted the daily quota of
the writing models, and that run is why `fallback_rate` is now part of every eval headline.
A close second: entity resolution is heuristic, and roughly half of signal-bearing records can't be attributed
to any company at all.
