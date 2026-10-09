# Planning: Exposure-Led Prospecting

## The problem, restated
A cybersecurity vendor's sales team has thousands of possible accounts and limited reps. They need to know
**who needs us right now, why, and what to say first.** Firmographic ICP filters (industry, size, geo) say
who *could* buy. They don't say who has a burning problem *today*.

## What the data actually is
The provided dataset is an internet-wide scan snapshot (Shodan banner format, 2026-10-08): ~11M records,
one per exposed `IP:port` service, with org/ASN, geo, hostnames/domains, TLS certificates, HTTP metadata,
detected products/versions (CPE), known vulnerabilities (CVSS + EPSS), and tags (`eol-product`,
`database`, `self-signed`, `ics`, `medical`, ...).

That makes this an **outside-in attack-surface dataset**: the same view an attacker has. That's the
strongest buying signal a security vendor can get, because it's specific, current, and verifiable.

### Assumptions (stated rather than asked)
- **The unit of sale is a company, but the data is per IP:port.** We resolve records to accounts ourselves
  (see ARCHITECTURE.md → Entity resolution). Records that only identify a hosting provider (CDN/cloud/ISP
  shared infrastructure) are not prospects in themselves.
- **CVE identifiers are treated as scanner findings, not confirmed compromises.** Several IDs don't match
  the public CVE registry's shape, so the dataset may be partly synthetic. Outreach therefore says
  "external scans indicate…" and never claims a breach or names an exploit path.

## How B2B security sales teams prospect (research summary)
| Practice | What it means here |
|---|---|
| **ICP** (ideal customer profile) | Geo/territory, size proxy (exposed footprint), industry (inferred), self-hosted vs. fully managed |
| **Account scoring / tiering** | Combine *fit* (ICP) with *intent/need* (signals) into a tier: A = work this week |
| **Buying signals / triggers** | Here: critical/high-EPSS CVEs, end-of-life software, exposed admin/remote access, open data stores, expired certs, ICS/medical exposure |
| **Territory & segment filtering** | Country/region, industry, segment, signal type |
| **Outreach prioritisation** | Rank by urgency; give the rep the "why now" in one line plus an evidence-grounded first touch |

## Use cases chosen
1. **"Who do I call this week?"** A ranked account list for my territory, filtered by country, industry,
   size and signal type, with an A/B/C tier and a one-line reason. *Primary.*
2. **"Why them, why now?"** An account page showing the evidence (assets, products, CVEs with
   severity/exploit likelihood, misconfigurations) grouped into plain-English risk themes a non-technical
   rep can repeat.
3. **"What do I say?"** An LLM-generated account brief and first-touch email grounded *only* in that
   account's evidence. Responsible tone: no exploit details, and offers to share findings.
4. **"Get it into my CRM."** Shortlist and CSV export.

### Deliberately not built
- Contact/person discovery (not in the data; would be an enrichment vendor in production).
- Live re-scanning or change detection over time (single snapshot; noted as the obvious v2 "trigger" feature).
- Revenue/headcount firmographics (not in the data; footprint size is the proxy).

## Rules vs. LLM (summary; detail in ARCHITECTURE.md)
- **Rules:** signal extraction and scoring. Deterministic, explainable, free, auditable. A rep must be able to
  answer "why is this account an A?" without the answer changing between runs.
- **LLM (cheap model):** account classification. Is this a real prospect or infrastructure noise, and which
  industry? These are fuzzy text judgements over org names, domains and page titles where rules break down.
- **LLM (stronger model):** brief and outreach drafting. A judgement and writing task, generated on demand
  and cached.

## Success criteria
- A rep can go from "my territory" to "10 accounts + reasons + draft emails" in under 2 minutes.
- Every claim in a brief or email traces to a stored evidence record (checked automatically in evals).
- Classifier precision/recall measured on a hand-labelled set, with failure modes written down.
