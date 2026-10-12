# Architecture

## 1. The shape of the system

```
                        OFFLINE DATA PIPELINE (python -m salesintel.pipeline)                    ONLINE APP
 ┌───────────────────────┐   ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   ┌───────────────────────────┐
 │ Shodan dump (.zst)    │   │ 1 extract    │   │ 2 resolve    │   │ 3 features   │   │ 4 score + build app.db    │
 │ 11.9 GB → 79 GB JSON  │──►│ parallel     │──►│ record →     │──►│ per-account  │──►│ rules: need × fit → tier  │
 │ 10.66M IP:port rows   │   │ stream parse │   │ account key  │   │ signals +    │   │ SQLite: accounts, signals,│
 └───────────────────────┘   │ → Parquet    │   │ (DuckDB)     │   │ evidence     │   │ evidence, llm_outputs     │
                             │ 576 MB, 3.4m │   │ ~100 s       │   │ ~50 s        │   │ ~25 s                     │
                             └──────────────┘   └──────────────┘   └──────────────┘   └─────────────┬─────────────┘
                                                                                                   │
 ┌─────────────────────────────────────────────────────────────────────────────────────────────────▼─────────────┐
 │ FastAPI (salesintel/api)  ── list/filter/export ── account detail ── classify ── outreach ── ops               │
 │      │                                                         │                                              │
 │      │                                    LLM RUNNER (salesintel/llm/runner.py): every call goes through here │
 │      │       prompt registry → render → budget guard → provider → refusal check → JSON → schema validation    │
 │      │       → guardrails (outreach) → trace (SQLite + JSONL) → on any failure: offline fallback, also traced │
 │      ▼                                                                                                        │
 │ React UI (web/): Prospects · Account · Shortlist · AI ops                                                     │
 └───────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
          evals/ (frozen datasets + harness) and skills/ (SKILL.md) sit beside the runner and reuse it.
```

**Key decision: precompute everything that does not need judgement.** 79 GB of JSON cannot be queried
per request, so the pipeline reduces it once to a 137 MB SQLite file with ~96k scored accounts, and the app
serves from that. Each stage writes an intermediate (Parquet / DuckDB) so later stages can be iterated in
seconds without re-reading the raw dump (`--from resolve|features|score`).

## 2. Data profile (full pass)
| Measure | Value |
|---|---|
| Records / distinct IPs / orgs | 10,660,937 / 3,374,094 / 51,497 |
| CDN- or cloud-tagged records | 7.04M (66%) |
| Records with ≥1 CVE / on CISA KEV | 148,312 / 70,299 |
| Honeypots (excluded) | 60,080 |
| Accounts resolved / with ≥1 signal | 299,114 / 96,098 |
| Tiers among prospects | A 1,480 · B 12,069 · C 81,656 (rules); A 1,309 after the AI check removed 171 non-buyers from the top 300 |

## 3. Entity resolution (record → account): the hardest problem
**Finding:** neither `org` nor `domains` identifies the customer for most records. `org` is usually the
hosting provider (Cloudflare, AWS, Hetzner…), and `domains` is usually the provider's reverse DNS
(`amazonaws.com`, `your-server.de`). The customer's identity lives in the HTTP Host and the TLS certificate.

**Account key, first match wins** (`salesintel/etl/resolve.py`):
1. registered domain (eTLD+1) of `http_host` (the site actually served),
2. registered domain of the certificate CN,
3. reverse-DNS registered domain, only if it isn't provider rDNS,
4. `org`, only if the org is not infrastructure,
5. otherwise unresolved (counted, excluded).

**Provider detection is mostly data-driven:**
| Rule | Catches |
|---|---|
| seed list `config/provider_domains.txt` | CDNs, clouds, hosters, consumer ISPs, dynamic DNS, placeholders, multi-tenant SaaS |
| domain seen across ≥ 60 unrelated orgs | platforms and vendor default certificates (`example.com`, `synology.me`) |
| rDNS hostnames embed the IP (≥ 30%) | `ec2-54-1-2-3…`, `1.2.3.4.ip.provider.net` |
| rDNS domain mostly on infrastructure orgs (≥ 50%) | ISP naming schemes (`nuro.jp`, `virtua.com.br`) |
| org with cloud/CDN share > 50%, ≥ 200 scanned IPs, or name pattern | ISPs/hosters announcing their own space |

Host/cert keys and rDNS keys use **separate** provider flags. An early version shared one flag and dropped
`mit.edu` (MIT's rDNS sits on Cloudflare), which the top-accounts sanity check caught.

**Coverage:** 1.04M of 10.66M records resolve to an account (http_host 614k, org 236k, cert 149k, rDNS 41k).
Of the signal-bearing records, about half resolve; the rest sit on shared infrastructure with no customer
identity and cannot be sold to. Records the rules let through that are still infrastructure (mid-size
ISPs with company-like names) are what the LLM classifier is for (§6).

## 4. Buying signals and scoring: rules, deliberately
`salesintel/signals.py` is the single registry: each signal is one SQL predicate plus weight, category
and rep-facing wording, consumed by feature extraction, scoring, API, UI and the LLM evidence pack.

| Tier of severity | Signals (weight = need points for one affected asset) |
|---|---|
| Confirmed / sensitive | indicators of compromise 45 · CISA KEV CVE 35 · ICS exposed 30 · medical exposed 30 |
| Unauthenticated exposure | open database (no auth) 32 · remote desktop (no auth) 30 |
| Known vulnerability | EPSS ≥ 10% 22 · CVSS ≥ 9 15 · end-of-life software 10 |
| Risky exposure | remote access (RDP/SMB/Telnet/VNC) 16 · database port 12 · anonymous FTP 12 · open directory 10 · AI/DevOps tooling 10 · VPN gateway 4 |
| Hygiene | outdated TLS 5 · expired cert 5 · self-signed 2 |

```
need     = 100 · (1 − e^(−Σ weightᵢ · volume(countᵢ) / 45))      volume(n) = min(2, 1 + ¼·log₂ n)
fit      = 100 · segment × region × industry × hosting             (weights in config/icp.yaml)
priority = 0.6 · need + 0.4 · fit
tier     = A if priority ≥ 68 and need ≥ 70 · B if priority ≥ 50 and need ≥ 35 · else C
```
Saturation means one KEV finding scores high, and 50 hygiene issues can't outrank one open database.
The A gate requires need, so a perfect-ICP account with nothing wrong is never "call this week".
`config/icp.yaml` is the retargeting knob: edit it and run `--from score` (25 s).

**Why rules, not an LLM, for scoring:** the score decides where reps spend their week. It has to be
explainable ("why is this an A?"), stable between runs, auditable across territories, testable, and free at
96k accounts × every refresh. An LLM adds nothing that the evidence doesn't already say.

## 5. Rule-vs-LLM split
| Task | Approach | Why |
|---|---|---|
| Parse, resolve, extract signals | Rules (DuckDB SQL) | Deterministic, 10M+ rows, must be exact |
| Need / fit / priority / tier | Rules | Explainable, stable, auditable, free |
| Industry when a clear clue exists (`.edu`, `.gov`, "dental") | Rules | Free and precise; abstains (`unknown`) otherwise |
| **Prospect vs. infrastructure + industry when rules abstain** | **LLM, cheap tier (Haiku 5.5)** | Fuzzy judgement over names, titles, network context; rules fail on keyword traps ("Korbank", "SoftBank") |
| **Brief, talk track, first-touch email** | **LLM, strong tier (Sonnet 5.5)** | Writing + prioritisation a prospect will read |
| Is the email safe and true? | Rules (guardrails) | Facts are checkable; an LLM judge would be slower, costlier and less reliable here |

## 6. LLM layer (`salesintel/llm/`)
- **Runner** (`runner.py`): the only path to a model. It loads the versioned prompt, renders, checks the daily
  budget, calls the provider, handles `refusal`/`max_tokens`, parses JSON, validates the schema, traces, and on
  any failure falls back to the offline implementation (also traced, status `fallback`). Callers never see
  a provider error; the UI labels fallbacks as "Rules fallback (no LLM)".
- **Providers** (`providers.py`): `AnthropicProvider` uses `client.beta.messages.create` with
  `output_config.format` (JSON-schema structured output, so malformed JSON is impossible) and `effort`
  from the prompt's front matter. Outreach requests opt into the server-side refusal fallback
  (`fallbacks: "default"`) because security content can trip the cyber safety classifier. API errors are
  caught as a typed chain (rate limit / auth / 4xx / 5xx / connection). `OfflineProvider` runs the rules.
  `GeminiProvider` exists because no paid Claude key was available during the build: the evals were
  measured on Google's free tier. Prompts keep their Claude tier and the provider maps it (cheap →
  `gemini-3.5-flash-lite`, strong → `gemini-3.8-flash` with `3.6-flash` / `3-flash-preview` as an
  overload fallback chain), using `responseJsonSchema` for the same schema-enforced output. Free-tier
  handling lives inside the provider: ~4 s minimum spacing between calls, backoff on 429/5xx, and model
  rotation on 503 "high demand". Selection is automatic (`SI_LLM_PROVIDER=auto`: Anthropic key, else Gemini
  key, else offline). Free-tier calls are traced with their real token counts and `cost_usd = 0` (nothing is
  billed); the cost model in §8 stays priced on Claude, the production target.
- **Prompt registry** (`registry.py`, `prompts/`): `prompts/<feature>/<version>.md` with YAML front matter
  (model, schema, max_tokens, effort, changelog) and `# system` / `# user` sections. `registry.yaml` names
  the **active** version (served) and the **candidate** (evaluated against it). The prompt SHA-256 is
  logged with every call.
- **Context** (`context.py`): one structured `AccountContext` renders the account card and evidence pack
  and also feeds the guardrails and offline fallback, so all three agree on what the model saw. Evidence
  selection is coverage-first (the worst asset per signal, then by severity). The baseline eval exposed
  why-now points with no evidence row behind them, and this fixed it (supported points 89% → 100%).
- **Guardrails** (`grounding.py`): unknown CVE / evidence id / port, unsupported why-now point, IP in email,
  breach/threat language, length. Results are stored with the draft and shown to the rep.

### Trace schema (`traces/llm_calls.sqlite`, mirrored to `traces/llm_calls.jsonl`)
| column | meaning |
|---|---|
| `call_id`, `ts`, `env` (app/eval/batch), `run_id` | identity; `run_id` groups an eval run or batch job |
| `feature`, `prompt_version`, `prompt_sha` | exactly which prompt text produced this |
| `provider`, `model`, `request_id` | who answered; provider request id for support |
| `account_id`, `request_json`, `response_text`, `output_json` | full request (rendered prompts + params), raw response, validated output |
| `decision` | compact business outcome, e.g. `telecom_isp/not_prospect/hosting_telecom`, `draft:3pts:96w` |
| `status`, `error`, `stop_reason` | `ok · fallback · refused · invalid_output · error · budget_blocked` |
| `input_tokens`, `output_tokens`, `cache_read_tokens`, `latency_ms`, `cost_usd` | cost and latency per call |

The Ops page (`/ops`) reads this table: usage by feature/model/status, p50/p95 latency, spend vs. budget,
recent calls, latest eval results.

## 7. Evals (`evals/`)
- `labels/account-classify.yaml`: 30 hand-labelled accounts (15 prospects / 15 not), stratified
  (top-tier, org-keyed, domain-keyed, rDNS-keyed, keyword traps), with difficulty and an `industry_ok` list
  for honestly ambiguous cases. Few-shot examples in prompt v2 are excluded from the set.
- `freeze.py` snapshots the exact context per example into `datasets/*.jsonl`, so evals don't drift when the
  pipeline is re-run. `outreach-draft.jsonl` holds 9 strong prospects, one per major signal family.
- `run.py`, one command: runs **baseline (rules) + active + candidate** prompt versions, scores them,
  diffs every version against its previous run, and writes `results/REPORT.md`, `results/<feature>/*.json`
  and `results/history.jsonl`.
- Metrics: classify → prospect precision/recall/F1, entity-type and industry accuracy, accuracy by
  difficulty, consistency violations, cost/example, p50/p95 latency. Outreach → guardrail pass rate,
  CVE grounding rate, supported-point rate, email length.

**Measured results (Gemini free tier; details and both runs in `evals/README.md`):**

| account-classify (30 ex.) | precision | recall | F1 | hard-case acc |
|---|---|---|---|---|
| rules baseline | 0.56 | 0.93 | 0.70 | 0.3 |
| v1 | 0.63–0.71 | 1.00 | 0.77–0.83 | 0.5 |
| **v2 (active)** | **0.94–1.00** | **1.00** | **0.97–1.00** | **1.0** |

| outreach-draft (9 ex.) | guardrail pass | CVE grounding | supported points |
|---|---|---|---|
| v1 | 0.67 | 1.00 | 1.00 |
| **v2 (active)** | **1.00** | 1.00 | 1.00 |

**Product impact.** Running v2 over the top 300 A-tier accounts (`python -m salesintel.cli classify-batch
--tier A --limit 300`, 300/300 live calls, no fallbacks) removed **171 (57%)** as non-buyers: 126 hosting
providers, 26 ISPs, 12 individuals/hobby sites, 5 platforms. Rules alone would have sent reps to them first.
The remaining 1,180 A-tier accounts are still rule-classified, the next batch to run.

The rules baseline is high-recall, low-precision: it lets almost every ISP and hosting company through.
v1 fixes recall but keeps marking ISPs as prospects (it never defines "prospect"); v2's decision procedure
fixes that. v2 was promoted on these numbers (`prompts/registry.yaml`). Two process lessons the harness caught:
**fallback rows contaminate metrics** (a second run hit the free-tier daily quota and 9/9 v2 drafts silently
became the template, so `fallback_rate` is now part of every headline), and **runs vary** (v2 precision 0.94
vs 1.00 across identical runs), so the docs report both runs.

## 8. Cost model (`salesintel/llm/cost_model.py`, also live on `/ops`)
Token sizes were measured by rendering the real prompts for 20 A-tier accounts (classify v2 ≈ 920 input
tokens, outreach v2 ≈ 1,060). Output includes adaptive-thinking tokens. Prices: Haiku 5.5 $0.10/$0.50,
Sonnet 5.5 $2/$10, Opus 5.5 $4/$20 per MTok. After live calls, measured averages from traces replace the estimates.

| Workload | Model | Tokens in/out | $/call | Calls/month | $/month |
|---|---|---|---|---|---|
| Classify backfill: all A/B accounts (13.5k), Batches API −50% | Haiku 5.5 | 950 / 200 | 0.00010 | 13,500 | 1.32 |
| Classify weekly refresh: ~10% churn | Haiku 5.5 | 950 / 200 | 0.00010 | 5,850 | 0.57 |
| Outreach on demand: 10 reps × 25/day × 22 days | Sonnet 5.5 | 1,100 / 1,500 | 0.0172 | 5,500 | 94.60 |
| Outreach pre-generation: top 500 A × 4.3 wk, Batches | Sonnet 5.5 | 1,100 / 1,500 | 0.0086 | 2,150 | 18.49 |
| **Total** | | | | | **≈ $115** |

Math for one on-demand draft: (1,100 × $2 + 1,500 × $10) / 1M = $0.0172.

**Model choice per task:** classification on Sonnet would be 20× the cost (~$26/mo backfill) for a narrow enum
task that the eval should show Haiku handles. Outreach on Opus would be ~$189/mo (2×) for a 120-word email.
Outreach on Haiku would be ~$5/mo, so if a future eval shows Haiku drafts pass the guardrails and the human
rubric, that's the next cost lever. Caching: drafts are cached per account and prompt version, and only regenerate on request.

**Ceilings:** daily budget enforced in the runner (`SI_LLM_DAILY_BUDGET_USD`, default $2 for the demo, $10
proposed in production; over budget → labelled fallbacks, never errors); monthly ceiling $200 with an alert at
80%; 40 drafts/rep/day; `max_tokens` 1,500 (classify) / 4,000 (outreach).

## 9. API and UI
- `GET /api/accounts` (filters: q, region, country, industry, segment, hosting, tier, signal (AND), min_need,
  prospects_only, shortlisted; sort; pagination), `GET /api/accounts/{id}`, `GET /api/export.csv`,
  `POST /api/accounts/{id}/classify|outreach`, `PUT|DELETE /api/shortlist/{id}`, `GET /api/meta`, `GET /api/ops`.
- Facet counts (2.5 s over ~100k rows) are cached in-process and invalidated by classification, so `/api/meta` takes ~10 ms.
- React + Vite + TypeScript (strict). Filters live in the URL, so territory views can be shared. Evidence refs in
  the AI brief jump to the evidence row they cite.

## 10. Trade-offs and known weaknesses
- **Single snapshot.** No change detection ("new KEV this week") yet; that's the obvious v2 trigger signal.
- **Public-suffix snapshot is offline** (`tldextract` with no network). New zones are missed: `bank.in`
  (India's bank-only zone) collapses several Indian banks into one account.
- **CDN-fronted findings.** Weak-TLS or vulnerability findings on a CDN edge IP reflect the customer's CDN
  configuration only partly. Hygiene signals are weighted low partly for this reason.
- **Possibly synthetic CVE IDs.** Many IDs (e.g. `CVE-2026-9xxxx`) don't look like real CVEs. Prompts are told to
  prefer describing the issue and cap CVE mentions. Copy never claims a breach.
- **Org-keyed accounts are thin.** No domain means little text for the classifier; expect low confidence.
- **SQLite + single process.** Fine for a demo and a small team. Shortlists are global (no auth). Multi-user would
  need Postgres and user-scoped shortlists.
- **Labels are a policy.** "Telcos/hosters are never prospects" is a product decision baked into the gold set.
