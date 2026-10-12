# Exposure Radar: exposure-led prospecting for a cybersecurity sales team

Turns a 10.66M-record internet scan snapshot into a ranked, filterable list of companies that need security
help **now**, with the evidence that proves it and an AI-drafted, guardrail-checked first touch.

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

| Key in `.env` | Provider | Models |
|---|---|---|
| `ANTHROPIC_API_KEY` | Claude (production design target) | Haiku 5.5 (classify) · Sonnet 5.5 (outreach) |
| `GEMINI_API_KEY` | Gemini free tier (how the evals were measured) | gemini-3.5-flash-lite (classify) · gemini-3.8-flash → 3.6-flash → 3-flash-preview (outreach) |
| none | offline | clearly labelled rule/template fallbacks |

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
