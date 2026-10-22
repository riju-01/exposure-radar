# Exposure Radar: exposure-led prospecting for a cybersecurity sales team

Turns a 10.66M-record internet scan snapshot into a ranked, filterable list of companies that need security
help **now**, with the evidence that proves it and an AI-drafted, guardrail-checked first touch.

**Live demo:** https://exposure-radar-4bbp.onrender.com (free tier: the first load can take ~30 s to wake up).
The demo serves the 13.4k A/B-tier accounts with the 300 AI classifications pre-computed. Google currently
refuses the Gemini key from Render's servers (403), so new AI requests there return clearly labelled rule
fallbacks. See [docs/HOW_I_BUILD.md](docs/HOW_I_BUILD.md).

| | |
|---|---|
| Planning (use cases, research, assumptions) | [docs/PLANNING.md](docs/PLANNING.md) |
| Architecture (pipeline, entity resolution, scoring, rule-vs-LLM split, tracing, cost model, trade-offs) | [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) |
| How I build (dev loop, where AI helped and hurt, known weakness) | [docs/HOW_I_BUILD.md](docs/HOW_I_BUILD.md) |
| Skills | [skills/account-classify](skills/account-classify/SKILL.md) · [skills/outreach-draft](skills/outreach-draft/SKILL.md) · [skills/territory-plan](skills/territory-plan/SKILL.md) |
| Prompts (versioned) | [prompts/](prompts/) · [registry.yaml](prompts/registry.yaml) |
| Evals | [evals/README.md](evals/README.md) · [latest report](evals/results/REPORT.md) |

## What a rep gets
- **Prospects:** ranked A/B/C list with a one-line "why now", filterable by territory (region/country),
  industry, size, hosting, tier and buying signal (AND), with min-need, search, shortlist and CSV export.
- **Account page:** need/fit/priority, every rule-detected signal with its points, the evidence assets (CVEs,
  CVSS, EPSS, CISA KEV), an **AI account check** (real prospect vs. hosting/ISP noise, industry), and an
  **AI brief + talk track + email** whose every claim links to an evidence row, with a guardrail verdict.
- **AI ops:** traced LLM calls, spend vs. daily budget, latency, eval results per prompt version, cost model.

## Run it locally
Requirements: Python 3.11+, Node 18+.
```bash
pip install -r requirements.txt
cd web && npm install && npm run build && cd ..

# 1) data (once). Needs the raw dump; ~5 min for stages 2-4, +3.5 min for extraction
python -m salesintel.pipeline --extract "C:/path/to/b2_download_file_by_id"
#    or, if data/slim/*.parquet already exists:
python -m salesintel.pipeline

# 2) app: API + UI on http://127.0.0.1:8000
python -m salesintel.api

# 3) quality
python -m pytest -q
python -m evals.run
```
Run every command from the repo root.

**LLM mode.** Copy `.env.example` to `.env` and add a key. The app picks a provider automatically:

| Key in `.env` | Provider | Models | Used in this submission? |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Claude (production design target) | Haiku 5.5 (classify) · Sonnet 5.5 (outreach) | **No.** Supported and tested with a fake provider, never called live |
| `GEMINI_API_KEY` | Gemini free tier | gemini-3.5-flash-lite (classify) · gemini-3.8-flash → 3.6-flash → 3-flash-preview (outreach) | **Yes.** All measured AI results (evals, 300 classifications) |
| none | offline | clearly labelled rule/template fallbacks | Yes, in the hosted demo for new AI requests (Google returns 403 there) |

> **Note:** no Anthropic API key was available for this submission, so no Claude model was called. Every
> measured AI number in this repo (eval results, the 300 pre-computed classifications) comes from **Gemini's
> free tier**. The Claude models and prices appear because the architecture and cost model are designed for
> them; anyone with a key can add `ANTHROPIC_API_KEY` to `.env` and re-run `python -m evals.run`.

Prompts declare a Claude tier (cheap/strong); on Gemini each tier maps to the models above. Every call,
whichever provider, goes through the same runner: tracing, daily budget, schema validation, guardrails, fallbacks.

## Repository map
```
salesintel/
  etl/extract.py      stage 1: zstd NDJSON → slim Parquet (parallel)
  etl/resolve.py      stage 2: record → account key (entity resolution)
  etl/features.py     stage 3: per-account signal counts + evidence
  etl/build_db.py     stage 4: enrich + score → data/app.db
  signals.py          buying-signal registry (one place)
  scoring.py          need × fit → priority → tier, with reasons
  enrich.py           region, segment, conservative rule industry
  llm/                runner · providers · registry · context · schemas · grounding · offline · tracing · pricing · cost_model
  services.py         use cases (classify, draft) shared by API, CLI, batch
  api/                FastAPI app + queries
  cli.py              JSON CLI used by the skills
config/               icp.yaml (retarget the ranking), provider_domains.txt, infra_org_patterns.txt
prompts/ skills/ evals/ tests/ web/ docs/
```
