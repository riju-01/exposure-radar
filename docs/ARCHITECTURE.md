# Architecture

> Draft. Sections are filled in as each stage is built.

## Pipeline overview
```
raw Shodan dump (11.9 GB zstd, ~79 GB NDJSON, 10.66M records)
  └─ etl/extract.py   one parallel pass → data/slim/*.parquet (576 MB, ~45 fields)      [~3.5 min]
      └─ etl/resolve  record → account key (entity resolution)
          └─ etl/signals + score   per-account features, rule-based score + tier
              └─ app.db (SQLite)   shipped with the app
                  ├─ FastAPI       list / filter / account detail / export
                  ├─ LLM layer     classify (cheap model), brief + outreach (strong model)
                  └─ React UI
```
Re-reading 79 GB per experiment would make iteration slow, so stage 1 runs once and everything
downstream iterates on the 576 MB Parquet copy in seconds with DuckDB.

## Dataset profile (full pass)
| Measure | Value |
|---|---|
| Records / distinct IPs | 10,660,937 / 3,374,094 |
| Distinct `org` / ASN | 51,497 / 30,584 |
| Records tagged CDN or cloud-hosted | 7.04M (66%) |
| Records with ≥1 CVE | 148k (98.6k CVSS ≥ 9, 135k EPSS ≥ 0.1, 70k on CISA KEV) |
| End-of-life product | 61.7k |
| Weak TLS (SSLv2/3, TLS 1.0) | 120.7k |
| Honeypots (excluded) | 60k |
| Signal-bearing, non-honeypot records | 313.6k across 217k IPs |

## Entity resolution (record → account)
**Finding:** neither `org` nor `domains` identifies the customer for most records.
- `org`/ASN is usually the **hosting provider**. On signal-bearing records the top orgs are Cloudflare,
  Aliyun, AWS, Google, Hetzner, DigitalOcean.
- `domains`/`hostnames` are mostly **reverse DNS of the provider** (`amazonaws.com`, `your-server.de`,
  `spectrum.com`, `contaboserver.net`).
- The customer's identity is in the **TLS certificate subject CN** and **HTTP Host**, e.g.
  `*.inksoft.com` on Microsoft IPs or `delotrans-software.com` on Contabo.

**Account key, first match wins:**
1. Registered domain (eTLD+1) of `ssl_cn`, unless it is generic (`localhost`, `Plesk`, `VPN`, IP-like) or a
   provider domain.
2. Registered domain of `http_host`, unless it is an IP address or provider domain.
3. Registered domain from `hostnames`/`domains`, unless it is a provider/rDNS domain.
4. `org`, but only when the org is not infrastructure (e.g. *Massachusetts Institute of Technology*, which
   owns its address space).
5. Otherwise unresolved: excluded from prospects and counted in a coverage metric.

**Provider/rDNS domain detection** is data-driven, not hand-listed: a domain counts as a provider domain
when its IP footprint is very large (hundreds of IPs) or spans many orgs, when its hostnames embed the IP
address, or when it maps to an org flagged as infrastructure. Infrastructure orgs are those with
`cloud_provider` set, a CDN tag, hosting/telecom keywords in the name, or a very large fan-out of distinct
customer domains. A small seed list covers the obvious giants.

**Where rules stop and the LLM starts:** these rules resolve the bulk cheaply. The leftover ambiguity
("is *Data Communication Business Group* a prospect or a telco?", "what industry is `craftysystems.com`?")
goes to the cheap-model classifier, which is the feature we hand-label and evaluate.
