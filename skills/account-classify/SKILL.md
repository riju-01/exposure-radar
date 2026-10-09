---
name: account-classify
version: 2.0.0
description: >
  Decide whether an internet-scan "account" is a real prospect for a cybersecurity vendor
  (vs. a hosting provider, ISP, platform or hobby site) and infer its industry. Use when an
  account's prospect status or industry is unknown or disputed, before it is worked by a rep,
  or when re-classifying a batch after a prompt change.
prompt: prompts/account-classify/v2.md        # candidate; registry.yaml says which version the app serves
schema: account_classification                 # salesintel/llm/schemas.py
model: claude-haiku-5-5
owner: sales-intel
---

# account-classify

## When to use (triggers)
- A rep asks "is X actually a prospect?" / "what industry is X?"
- An account's `industry` is `unknown` or `is_prospect` came from rules only (`prospect_source = rule`).
- Before pre-generating outreach for a territory (don't write emails to hosting companies).
- After editing a classify prompt: run the eval first (`python -m evals.run account-classify`).

Do **not** use it to score need/urgency. That is deterministic rules (`salesintel/scoring.py`) by design.

## Inputs
| name | type | source |
|---|---|---|
| `account` | account key or name (e.g. `mit.edu`, `org:Logix`) | the prospect list / API `GET /api/accounts` |
| `account_card` | text, rendered by `AccountContext.account_card()` | name, domain, how it was identified, network owners, certificate orgs, country, footprint, page titles, hostnames, products |

## Output (JSON, schema-enforced)
```json
{"entity_type": "business|education|government|healthcare_provider|nonprofit|hosting_provider|telecom_isp|cdn_saas_platform|individual_or_hobby|unknown",
 "is_prospect": true,
 "industry": "healthcare|finance|manufacturing|education|retail_ecommerce|professional_services|technology|media_entertainment|government|nonprofit|hosting_telecom|unknown",
 "confidence": "high|medium|low",
 "rationale": "one sentence naming the decisive clue"}
```
Invariant: `is_prospect` is true exactly for business, education, government, healthcare_provider, nonprofit.

## Dependent prompts and files
- `prompts/account-classify/v2.md`: system prompt (decision procedure, field-reliability rules, 4 few-shot examples) + user template `{{account_card}}`
- `prompts/account-classify/v1.md`: previous version, kept for comparison
- `prompts/registry.yaml`: active vs candidate version
- `salesintel/llm/schemas.py`: `ACCOUNT_CLASSIFICATION`
- `salesintel/llm/offline.py::classify_rules`: fallback + eval baseline

## How to run

### A. With this repo (preferred: traced, budgeted, validated, cached)
```bash
python -m salesintel.cli classify mit.edu               # active prompt, applies result + rescores fit
python -m salesintel.cli classify mit.edu --version v2  # try a specific prompt version, no side effects
python -m salesintel.cli classify-batch --tier A --limit 200
```
HTTP: `POST /api/accounts/{id}/classify?refresh=true`.

### B. From any agent or the raw API (no repo code)
1. Get the card: `python -m salesintel.cli card <account>` (or build the same fields yourself).
2. Read `prompts/account-classify/v2.md`; split at `# system` / `# user`; substitute `{{account_card}}`.
3. Call Claude with `model` from the front matter, `max_tokens: 1500`,
   `output_config: {effort: "low", format: {type: "json_schema", schema: ACCOUNT_CLASSIFICATION}}`.
4. Check `stop_reason` (`refusal`/`max_tokens` → treat as failed), `json.loads` the text block, validate it.
5. If anything fails, fall back to `unknown` / `is_prospect=false` / `confidence=low` and flag for review.
6. Log the call (request, response, model, prompt version + sha, tokens, latency, cost, decision). The schema is in `salesintel/llm/tracing.py`.

## Worked example
```bash
$ python -m salesintel.cli classify "org:Korbank S. A." --version v2
```
Card excerpt the model sees:
```
name: Korbank S. A.
identified_via: org
network_owners (ASN org of the IPs): Korbank S. A.
sample_page_titles: 301 Moved Permanently | Planner Pomiarow — Dispatch | Backup | Dead Host | Apache2 Ubuntu Default Page
detected_products: nginx, Apache httpd, OpenSSH, Remote Desktop Protocol
```
Expected output (gold label in `evals/labels/account-classify.yaml`):
```json
{"entity_type": "telecom_isp", "is_prospect": false, "industry": "hosting_telecom",
 "confidence": "medium", "rationale": "Network-registration name of a Polish ISP; titles are customer sites."}
```
The keyword baseline gets this wrong (`"bank"` → finance prospect). This is the trap v2's field-reliability guidance is aimed at.

## Quality and known weaknesses
- Measured on 30 hand-labelled accounts (`evals/results/REPORT.md`); baseline rules: prospect precision 0.56 / recall 0.93.
- Org-keyed cards (no domain) carry very little text; expect `low` confidence there and route to a human.
- Labels encode a policy: telcos/hosters are never prospects, even for their corporate IT. Change the policy → relabel.
