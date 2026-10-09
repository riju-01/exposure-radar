"""SQLite application database: schema and small helpers shared by the pipeline and the API."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from salesintel import config

SCHEMA = """
create table if not exists accounts (
    id               integer primary key,
    account_key      text unique not null,
    name             text not null,
    domain           text,
    key_source       text,
    country_code     text,
    country          text,
    region           text,
    segment          text,
    hosting          text,
    industry         text,
    industry_source  text,           -- rule | llm | none
    industry_clue    text,
    entity_type      text,           -- from the classifier (null until classified)
    is_prospect      integer,        -- 1/0; rules default, classifier overrides
    prospect_source  text,           -- rule | llm
    n_ips            integer,
    n_services       integer,
    n_ports          integer,
    n_cves           integer,
    max_cvss         real,
    max_epss         real,
    need             integer,
    fit              integer,
    priority         integer,
    tier             text,
    top_reason       text,
    reasons_json     text,
    fit_json         text,
    signals_json     text,
    titles_json      text,
    products_json    text,
    hosts_json       text,
    hosting_orgs_json text,
    cert_orgs_json   text,
    primary_org      text,
    waf_share        real,
    has_security_txt integer,
    last_seen        text
);
create index if not exists ix_accounts_rank on accounts(is_prospect, priority desc);
create index if not exists ix_accounts_region on accounts(region, country_code);
create index if not exists ix_accounts_industry on accounts(industry);
create index if not exists ix_accounts_tier on accounts(tier);

create table if not exists account_signals (
    account_id integer not null references accounts(id),
    signal     text not null,
    count      integer not null,
    primary key (account_id, signal)
);
create index if not exists ix_signals_signal on account_signals(signal, account_id);

create table if not exists evidence (
    id          integer primary key,
    account_id  integer not null references accounts(id),
    ip text, port integer, transport text, module text, product text, version text,
    http_title text, http_host text, ssl_cn text, org text, country_code text, seen_at text,
    cves_json text, n_cves integer, max_cvss real, max_epss real, kev_count integer,
    flags_json text, severity real
);
create index if not exists ix_evidence_account on evidence(account_id, severity desc);

create table if not exists llm_outputs (
    account_id     integer not null references accounts(id),
    feature        text not null,
    prompt_version text not null,
    model          text,
    provider       text,
    output_json    text not null,
    checks_json    text,
    call_id        text,
    created_at     text not null,
    primary key (account_id, feature, prompt_version)
);

create table if not exists shortlist (
    account_id integer primary key references accounts(id),
    added_at   text not null,
    note       text
);

create table if not exists meta (key text primary key, value text);
"""


def connect(path: Path | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(path or config.APP_DB, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("pragma journal_mode=wal")
    con.execute("pragma foreign_keys=on")
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA)


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("insert or replace into meta(key, value) values (?, ?)", (key, json.dumps(value)))


def get_meta(con: sqlite3.Connection, key: str, default=None):
    row = con.execute("select value from meta where key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default
