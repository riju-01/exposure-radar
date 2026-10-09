"""Stage 3: per-account features and the per-account evidence pack.

Input:  data/resolved.parquet (scan records with an account_key)
Output: DuckDB tables `account_features` (one row per account) and `evidence`
        (the worst N records per account, which is what reps and the LLM get to see).
"""
from __future__ import annotations

import duckdb

from salesintel import config
from salesintel.signals import SIGNALS, record_flags_sql, record_severity_sql

EVIDENCE_PER_ACCOUNT = 25


def _records_view(con: duckdb.DuckDBPyConnection) -> None:
    con.sql(f"""
        create or replace view rec as
        select *,
               {record_severity_sql()} as severity,
               {record_flags_sql()} as flags
        from read_parquet('{(config.DATA_DIR / "resolved.parquet").as_posix()}')
    """)


def _account_features(con) -> None:
    signal_cols = ",\n".join(
        f"count(distinct case when {s.predicate} then ip || ':' || port end) as sig_{s.key}"
        for s in SIGNALS)
    con.sql(f"""
        create or replace table account_features as
        select
            account_key,
            mode(key_source) key_source,
            count(distinct ip) n_ips,
            count(*) n_services,
            count(distinct port) n_ports,
            mode(country_code) country_code,
            mode(country) country,
            list_distinct(list(country_code))[1:6] countries,
            list(distinct org order by org)[1:4] hosting_orgs,
            mode(org) primary_org,
            avg((cloud_provider is not null or list_contains(tags, 'cdn'))::int) cloud_share,
            list(distinct http_title) filter (where http_title is not null and length(http_title) > 2)[1:6] titles,
            list(distinct product) filter (where product is not null)[1:10] products,
            list(distinct http_host) filter (where http_host is not null)[1:6] hosts,
            list(distinct ssl_subject_o) filter (where ssl_subject_o is not null)[1:3] cert_orgs,
            count(distinct v) n_cves,
            max(vuln_max_cvss) max_cvss,
            max(vuln_max_epss) max_epss,
            avg((http_waf is not null)::int) filter (where http_title is not null or http_status is not null) waf_share,
            bool_or(coalesce(http_securitytxt, false)) has_security_txt,
            max(ts) last_seen,
            max(severity) max_record_severity,
            {signal_cols}
        from (select *, unnest(case when len(vuln_ids) = 0 then [null] else vuln_ids end) v from rec)
        group by account_key
    """)


def _evidence(con) -> None:
    """Keep the worst N records per account, plus the worst example of every signal it has,
    so each signal shown to a rep (or the LLM) has at least one concrete asset behind it."""
    con.sql(f"""
        create or replace table evidence as
        with ranked as (
            select account_key, ip, port, transport, module, product, version, http_title, http_host,
                   ssl_cn, org, country_code, ts, vuln_ids[1:12] cves, len(vuln_ids) n_cves,
                   vuln_max_cvss max_cvss, vuln_max_epss max_epss, coalesce(vuln_kev, 0) kev_count,
                   flags, severity,
                   row_number() over (partition by account_key order by severity desc, len(vuln_ids) desc, ip, port) rn
            from rec
            where len(flags) > 0
        ),
        per_signal as (
            select account_key, flag, min(rn) rn
            from (select account_key, unnest(flags) flag, rn from ranked)
            group by account_key, flag
        )
        select * exclude (rn), rn as severity_rank from ranked r
        where rn <= {EVIDENCE_PER_ACCOUNT}
           or exists (select 1 from per_signal p where p.account_key = r.account_key and p.rn = r.rn)
    """)


def run(con: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    con = con or duckdb.connect()
    _records_view(con)
    _account_features(con)
    _evidence(con)
    return con


if __name__ == "__main__":
    c = run()
    print(c.sql("select count(*) accounts, sum((max_record_severity > 0)::int) with_signals from account_features"))
