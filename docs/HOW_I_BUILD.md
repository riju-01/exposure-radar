# How I build

## The dev loop
I built this in **Claude Code** (desktop app). My role was to set direction, make the product calls and
review every result; Claude Code did most of the typing, querying and first drafts. Every stage followed the
same loop: **profile → hypothesis → small sample → full run → sanity-check the top of the output → fix → commit**.

1. **Look before planning.** I asked for a plan only after inspecting the file. The first 400 KB showed the
   "company dataset" was really an 11.9 GB Shodan scan dump (one row per exposed server), which turned the
   product from "filter companies" into "turn attack-surface evidence into who to call and why".
2. **Decide, document, move on.** I chose not to email Firmable about the per-server data or the odd-looking
   CVE IDs. Instead the assumptions are written down in `PLANNING.md`, and the outreach prompt is told never to
   claim a breach. I picked FastAPI + React + Render, kept all work local until it was ready, and only then
   published the repo.
3. **One expensive pass, then cheap iteration.** A parallel extractor turned 79 GB of JSON into 576 MB of
   Parquet in 3.4 minutes, so every later experiment (entity resolution, signals, scoring) ran in seconds.
4. **Eyeball the top of the list after every change.** The highest-leverage habit. It caught ISPs posing as
   accounts (*Ziply Fiber*, *Private Customer*), a rule that wrongly dropped `mit.edu`, page titles turning a
   telecom into "healthcare", and `bank.in` merging several banks into one account.
5. **Measure before tuning.** The eval harness ran on the rules baseline first, and immediately exposed a product
   bug: some "why now" points had no evidence behind them. Fixing evidence selection took supported points
   from 89% to 100%.
6. **No paid key, so find a free one and measure for real.** I had no Anthropic key, so the LLM layer was built
   provider-agnostic with labelled offline fallbacks. An xAI key turned out to need paid credits; a free Gemini
   key worked. Live evals showed v2 beating v1 (classify precision 0.63 → 0.94, outreach guardrail failures
   3 → 0), so I promoted v2 and ran it on the top 300 A-tier accounts: **57% were hosting companies, ISPs or
   hobby sites** and dropped out of the reps' list.
7. **Audit against the brief before calling it done.** Checking each "what we're looking for" point against the
   repo surfaced stale cost figures and a missing hosted link, which were fixed before submission.

Tools: Claude Code for code, data work and docs; its in-app browser to check the UI; DuckDB for analysis; the
Claude API reference skill for current model IDs, pricing and API parameters; 54 pytest tests to pin behaviour.

## Where AI saved the most time
- **Data archaeology at scale.** Profiling 10.66M nested records and iterating on entity-resolution rules took
  hours instead of days: query, read the result, propose the next rule, repeat.
- **Breadth of scaffolding.** Prompt registry, tracer, runner with fallbacks, eval harness, API, three-page UI
  and tests each came out in one focused pass with consistent patterns.
- **Adversarial cases.** Once I stated the policy, it readily produced the traps the evals and guardrail tests
  need: *Korbank* and *SoftBank* (ISPs with "bank" in the name), fake CVEs, IPs in emails, breach language.

## Where AI cost more than doing it by hand
- **Shell-escaped edits on Windows.** Patching files through heredocs mangled a `"\n"` into a real newline and
  ate a backslash. Each was caught at once, but a plain editor edit would have been faster.
- **A first-draft SQL join on an `OR` condition** became a nested-loop join over 10M rows and hung for 10
  minutes; two joins and a `UNION ALL` fixed it.
- **Labels.** The model drafts gold labels quickly, but each label is a claim about a real organisation, and
  checking them is human work. The set is still marked `reviewed: false`.

## One known weakness to flag to a teammate
**The AI numbers come from Gemini's free tier, not the Claude models the design targets, and they don't hold
up in the hosted app.** The evals (v2 classify precision 0.94–1.00, recall 1.00; 0 guardrail failures in 8 live
drafts) were measured from my machine. On Render, Google answers the same key with `403 project denied access`,
so the hosted demo serves the 300 pre-computed AI classifications plus clearly labelled rule fallbacks for any
new request. The runner, tracing and fallback design are what kept that from becoming an outage, and the
failure is visible on the AI ops page. Before trusting this in production: have someone else review the eval
labels, set `ANTHROPIC_API_KEY`, and re-run `python -m evals.run --versions baseline v1 v2` (~$0.35).
