"""Stage 2: entity resolution. Attach an account key to every scan record.

The scan data is per IP:port, but we sell to companies. A record's `org` is usually
the hosting provider and its `domains` are usually provider reverse DNS, so the customer's
identity has to come from the HTTP Host or TLS certificate it serves. Priority:

    1. http_host registered domain   (the site actually served)
    2. ssl_cn registered domain      (who the certificate was issued to)
    3. reverse-DNS registered domain (only if it is not provider rDNS)
    4. org name                      (only if the org is not infrastructure)

Any candidate that is a provider, CDN, dynamic-DNS, placeholder or "fan-out" domain
(seen across too many unrelated networks to be one company) is skipped.
"""
from __future__ import annotations

import ipaddress
import re
from functools import lru_cache
from pathlib import Path

import duckdb
import pandas as pd
import tldextract

from salesintel import config

# A single company rarely spans this many unrelated networks; above it, a domain is a
# platform (CDN, SaaS, device-vendor default certificate) rather than an account.
ORG_FANOUT_LIMIT = 60
# Reverse-DNS domains whose hostnames mostly embed the IP are ISP/hosting rDNS.
RDNS_IP_EMBED_SHARE = 0.3
# A reverse-DNS domain mostly seen on infrastructure networks is the provider's naming scheme.
RDNS_INFRA_SHARE = 0.5
# An org announcing this many scanned IPs is an ISP/hoster, whatever its name says.
ORG_IP_LIMIT = 200

# Scanner modules that fingerprint attacker tooling. Those hosts are threat infrastructure,
# not prospects, so they are excluded alongside honeypots.
MALICIOUS_MODULE_RE = r"(-rat$|^c2-|cobalt|sliver|metasploit|havoc|mythic|covenant|brute-ratel)"

GENERIC_NAMES = {
    "localhost", "localhost.localdomain", "plesk", "vpn", "router", "default", "unknown",
    "sni-support-required-for-valid-ssl", "invalid2.invalid", "traefik default cert",
}

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None)


def load_list(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]


@lru_cache(maxsize=1)
def provider_domains() -> frozenset[str]:
    return frozenset(load_list(config.CONFIG_DIR / "provider_domains.txt"))


@lru_cache(maxsize=1)
def infra_org_regex() -> re.Pattern:
    return re.compile("|".join(load_list(config.CONFIG_DIR / "infra_org_patterns.txt")), re.I)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value.strip("[]"))
        return True
    except ValueError:
        return False


def registered_domain(host: str | None) -> str | None:
    """eTLD+1 of a hostname/CN, or None when it is not a usable public domain."""
    if not host:
        return None
    h = host.strip().lower().lstrip("*.").split(":")[0]
    if not h or h in GENERIC_NAMES or "." not in h or " " in h or _is_ip(h):
        return None
    e = _extract(h)
    if not e.suffix or not e.domain:
        return None
    return e.top_domain_under_public_suffix


def is_infra_org(org: str | None) -> bool:
    return bool(org) and bool(infra_org_regex().search(org))


def _base_view(con: duckdb.DuckDBPyConnection) -> None:
    con.sql(f"""
        create or replace view r as
        select * from read_parquet('{config.SLIM_DIR.as_posix()}/*.parquet')
        where not list_contains(tags, 'honeypot')
          and not list_contains(tags, 'c2')
          and not regexp_matches(coalesce(module, ''), '{MALICIOUS_MODULE_RE}')
    """)


def _host_map(con) -> None:
    """Map every distinct http_host / ssl_cn to its registered domain (Python, distinct values only)."""
    hosts = con.sql("""
        select distinct lower(h) h from (
            select http_host h from r union all select ssl_cn from r) where h is not null
    """).fetchnumpy()["h"]
    df = pd.DataFrame({"h": hosts, "reg": [registered_domain(h) for h in hosts]})
    con.register("host_map_df", df)
    con.sql("create or replace table host_map as select * from host_map_df")


def _org_flags(con) -> None:
    orgs = con.sql("""
        select org,
               avg((cloud_provider is not null)::int) cloud_share,
               avg(list_contains(tags, 'cdn')::int) cdn_share,
               count(distinct ip) n_ips
        from r where org is not null group by org
    """).df()
    orgs["infra"] = ((orgs.cloud_share > 0.5) | (orgs.cdn_share > 0.5)
                     | (orgs.n_ips >= ORG_IP_LIMIT) | orgs.org.map(is_infra_org))
    con.register("org_df", orgs[["org", "infra"]])
    con.sql("create or replace table org_flags as select * from org_df")


def _domain_flags(con) -> None:
    """Flag provider domains: seed list, cross-network fan-out, or IP-embedding reverse DNS."""
    con.register("seed_df", pd.DataFrame({"d": sorted(provider_domains())}))
    con.sql(f"""
        create or replace table domain_flags as
        with cand as (
            select hm.reg d, r.org, r.ip, false is_rdns, false ip_embedded
            from r join host_map hm on hm.h = lower(r.http_host)
            where hm.reg is not null
            union all
            select hm.reg d, r.org, r.ip, false is_rdns, false ip_embedded
            from r join host_map hm on hm.h = lower(r.ssl_cn)
            where hm.reg is not null
            union all
            select unnest(r.domains) d, r.org, r.ip, true,
                   -- reverse-DNS hostnames like ec2-54-1-2-3 or 1.2.3.4.ip.provider.net embed the IP
                   list_has_all(
                       list_transform(list_filter(string_split(regexp_replace(coalesce(r.hostnames[1], ''), '[^0-9]+', ' ', 'g'), ' '), x -> x <> ''), x -> try_cast(x as int)),
                       list_transform(string_split(r.ip, '.'), x -> try_cast(x as int)))
            from r where len(r.domains) > 0 and r.ip like '%.%.%.%'
        )
        , cand_flagged as (
            select c.*, coalesce(o.infra, false) org_infra
            from cand c left join org_flags o on o.org = c.org
        )
        select d,
               count(distinct org) n_orgs,
               count(distinct ip) n_ips,
               avg(ip_embedded::int) filter (where is_rdns) rdns_ip_share,
               -- as a served site / certificate identity
               (d in (select d from seed_df)
                or count(distinct org) >= {ORG_FANOUT_LIMIT}
               ) is_provider,
               -- as a reverse-DNS name: additionally reject the provider's own naming schemes
               (d in (select d from seed_df)
                or count(distinct org) >= {ORG_FANOUT_LIMIT}
                or coalesce(avg(ip_embedded::int) filter (where is_rdns), 0) >= {RDNS_IP_EMBED_SHARE}
                or coalesce(avg(org_infra::int) filter (where is_rdns), 0) >= {RDNS_INFRA_SHARE}
                or d not like '%_._%'
               ) is_provider_rdns
        from cand_flagged group by d
    """)


def _assign_keys(con) -> None:
    con.sql("""
        create or replace table resolved as
        with k as (
            select r.*,
                   case when not coalesce(dh.is_provider, true) then hh.reg end key_host,
                   case when not coalesce(dc.is_provider, true) then hc.reg end key_cn,
                   -- records almost always carry a single rDNS domain; use the first one
                   case when not coalesce(dr.is_provider_rdns, true) then r.domains[1] end key_rdns,
                   case when r.org is not null and not coalesce(o.infra, true) then 'org:' || r.org end key_org
            from r
            left join host_map hh on hh.h = lower(r.http_host)
            left join domain_flags dh on dh.d = hh.reg
            left join host_map hc on hc.h = lower(r.ssl_cn)
            left join domain_flags dc on dc.d = hc.reg
            left join domain_flags dr on dr.d = r.domains[1]
            left join org_flags o on o.org = r.org
        )
        select * exclude (key_host, key_cn, key_rdns, key_org),
               coalesce(key_host, key_cn, key_rdns, key_org) account_key,
               case when key_host is not null then 'http_host'
                    when key_cn is not null then 'ssl_cn'
                    when key_rdns is not null then 'rdns'
                    when key_org is not null then 'org' end key_source
        from k
    """)


def run(con: duckdb.DuckDBPyConnection | None = None) -> duckdb.DuckDBPyConnection:
    con = con or duckdb.connect()
    _base_view(con)
    _host_map(con)
    _org_flags(con)
    _domain_flags(con)
    _assign_keys(con)
    return con


if __name__ == "__main__":
    c = run()
    print(c.sql("""
        select key_source, count(*) records, count(distinct account_key) accounts
        from resolved group by key_source order by records desc
    """))
