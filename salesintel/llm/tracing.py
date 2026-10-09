"""Trace store: one row per LLM call (live, offline fallback, blocked or failed).

Written to SQLite (queryable: the Ops page and budget guard read it) and mirrored to an
append-only JSONL file (greppable, easy to ship to any log pipeline). The schema below is
the contract; docs/ARCHITECTURE.md documents each column.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from salesintel import config

TRACE_SCHEMA = """
create table if not exists llm_calls (
    call_id           text primary key,
    ts                text not null,      -- UTC ISO-8601
    env               text not null,      -- app | eval | batch
    run_id            text,               -- groups calls of one eval run / batch job
    feature           text not null,      -- account-classify | outreach-draft
    prompt_version    text not null,
    prompt_sha        text not null,      -- hash of model+system+user template
    provider          text not null,      -- anthropic | offline
    model             text not null,
    account_id        integer,
    request_json      text not null,      -- rendered system/user + params actually sent
    response_text     text,               -- raw model text (before parsing)
    output_json       text,               -- parsed + schema-validated output
    decision          text,               -- compact business outcome, e.g. "business/prospect/finance"
    status            text not null,      -- ok | fallback | refused | invalid_output | error | budget_blocked
    error             text,
    stop_reason       text,
    input_tokens      integer default 0,
    output_tokens     integer default 0,
    cache_read_tokens integer default 0,
    latency_ms        integer,
    cost_usd          real default 0,
    request_id        text                -- provider request id for support tickets
);
create index if not exists ix_calls_ts on llm_calls(ts);
create index if not exists ix_calls_feature on llm_calls(feature, prompt_version);
create index if not exists ix_calls_run on llm_calls(run_id);
"""


@dataclass
class TraceRecord:
    env: str
    feature: str
    prompt_version: str
    prompt_sha: str
    provider: str
    model: str
    request_json: str
    status: str
    account_id: int | None = None
    run_id: str | None = None
    response_text: str | None = None
    output_json: str | None = None
    decision: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    latency_ms: int | None = None
    cost_usd: float = 0.0
    request_id: str | None = None
    call_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat(timespec="milliseconds"))


class Tracer:
    def __init__(self, db_path=None, jsonl_path=None):
        self.db_path = db_path or config.TRACES_DB
        self.jsonl_path = jsonl_path or config.TRACES_JSONL
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._connect() as con:
            con.executescript(TRACE_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.db_path)
        con.row_factory = sqlite3.Row
        return con

    def log(self, rec: TraceRecord) -> str:
        row = asdict(rec)
        with self._lock:
            with self._connect() as con:
                con.execute(f"insert into llm_calls({','.join(row)}) values ({','.join('?' * len(row))})",
                            tuple(row.values()))
            with open(self.jsonl_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        return rec.call_id

    def spend_today(self, env: str = "app") -> float:
        day = datetime.now(timezone.utc).date().isoformat()
        with self._connect() as con:
            row = con.execute("select coalesce(sum(cost_usd), 0) from llm_calls where env = ? and ts >= ?",
                              (env, day)).fetchone()
        return float(row[0])

    def query(self, sql: str, params: tuple = ()) -> list[dict]:
        with self._connect() as con:
            return [dict(r) for r in con.execute(sql, params).fetchall()]


_default: Tracer | None = None


def default_tracer() -> Tracer:
    global _default
    if _default is None:
        _default = Tracer()
    return _default
