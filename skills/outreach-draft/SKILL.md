---
name: outreach-draft
version: 2.0.0
description: >
  Turn an account's external attack-surface evidence into a 10-second brief, a call talk track and a
  first-touch email that cites only verified evidence. Use when a rep is about to contact an account,
  or to pre-generate drafts for the top of a territory.
prompt: prompts/outreach-draft/v2.md
schema: outreach_draft
model: claude-sonnet-5-5
guardrails: salesintel/llm/grounding.py::check_outreach
owner: sales-intel
---

# outreach-draft

## When to use (triggers)
- "Draft an email to <account>", "what do I say to <account>?", "prep me for a call with <account>".
- Weekly pre-generation for the top A-tier accounts in a territory (see `skills/territory-plan`).
- Never for accounts with `is_prospect = 0` (hosting/ISP). Run `account-classify` first if unsure.

## Inputs
| name | type | source |
|---|---|---|
| `account` | account key or name | prospect list |
| `account_card` | text | `AccountContext.account_card()` |
| `evidence_pack` | text: scores, rule-detected signals, and up to 12 evidence rows `[E1]..[E12]` (coverage-first: at least one asset per signal) | `AccountContext.evidence_pack()` |

## Output (JSON, schema-enforced)
```json
{"summary": "2-3 sentences",
 "why_now": [{"point": "...", "evidence": ["E1", "E3"]}],
 "talk_track": ["opening question", "value", "next step"],
 "email": {"subject": "<= 60 chars", "body": "<= 120 words, {first_name}/{sender_name} placeholders"},
 "cited_cves": ["CVE-..."]}
```

## Guardrails (always run after generation; results shown to the rep)
`check_outreach(draft, ctx)` fails the draft if it:
- cites a CVE or evidence id that is not in the evidence pack, or a port that is not in it;
- has a why-now point without evidence ids;
- puts an IP address in the email;
- uses breach/threat language ("breached", "hacked", "has been compromised", "proof of concept", guarantees);
- exceeds 180 words or an 80-character subject.
A failing draft is still shown, with a "Review before sending" banner listing the issues. It is never sent automatically.

## Dependent prompts and files
- `prompts/outreach-draft/v2.md` (candidate) and `v1.md` (active until v2 wins the eval)
- `salesintel/llm/schemas.py::OUTREACH_DRAFT`, `salesintel/llm/grounding.py`, `salesintel/llm/offline.py::outreach_template` (fallback)

## How to run

### A. With this repo
```bash
python -m salesintel.cli outreach ooda-health.io               # active prompt, cached per account
python -m salesintel.cli outreach ooda-health.io --refresh     # regenerate
python -m salesintel.cli outreach ooda-health.io --version v2  # specific version, no caching
```
HTTP: `POST /api/accounts/{id}/outreach?refresh=true`. UI: account page → "Generate".

### B. From any agent or the raw API
1. `python -m salesintel.cli card <account>` → `account_card`, `evidence_pack`.
2. Render `prompts/outreach-draft/v2.md` (`{{account_card}}`, `{{evidence_pack}}`).
3. Call `claude-sonnet-5-5` with `max_tokens: 4000`, `output_config: {effort: "medium", format: {type: "json_schema", schema: OUTREACH_DRAFT}}`,
   and the server-side refusal fallback (`betas: ["server-side-fallback-2026-07-01"]`, `fallbacks: "default"`),
   because security content can trip the cyber safety classifier.
4. Handle `stop_reason == "refusal"` before reading content; validate JSON; run the guardrail checks above.
5. Log the call with the trace schema in `salesintel/llm/tracing.py`.

## Worked example
```bash
$ python -m salesintel.cli outreach ooda-health.io --version v2
```
Evidence pack excerpt:
```
SIGNALS (rule-detected):
- kev_vuln: 13 assets running software with vulnerabilities on CISA's Known Exploited list
- eol_software: 11 assets running end-of-life software that no longer receives security patches
EVIDENCE (worst assets first):
[E1] port 80/tcp Apache httpd 2.4.6 | ... | max CVSS 9.8, max EPSS 1.0, on CISA KEV | flags: Known-exploited CVE, End-of-life software
```
Shape of a passing draft (illustrative):
```json
{"summary": "ooda-health.io (healthcare payments tech, DE-hosted) exposes 11 servers; 13 run software with known-exploited vulnerabilities, most on end-of-life Apache 2.4.x.",
 "why_now": [{"point": "Known-exploited (CISA KEV) vulnerabilities on internet-facing web servers", "evidence": ["E1", "E2"]},
             {"point": "End-of-life web server builds that no longer receive patches", "evidence": ["E1"]}],
 "talk_track": ["Who owns patching for the prod web tier?", "We map and prioritise external exposure continuously", "Offer the full findings list"],
 "email": {"subject": "Internet-facing findings for ooda-health.io",
           "body": "Hi {first_name},\n\nPublic scan data shows several ooda-health.io web servers running end-of-life Apache builds with vulnerabilities on CISA's Known Exploited list. You may already be on it, and if so, ignore this.\n\nIf useful, I can send the full list with what to fix first. Worth 15 minutes?\n\n{sender_name}"},
 "cited_cves": []}
```

## Quality and known weaknesses
- Metrics: guardrail pass rate, CVE grounding rate, supported-point rate (`python -m evals.run outreach-draft`).
- Guardrails check facts, not persuasiveness. Tone and quality need a human rubric review (see `evals/README.md`).
- CVE IDs in this dataset may be synthetic; v2 caps CVE mentions in emails at 2 and prefers describing the issue.
