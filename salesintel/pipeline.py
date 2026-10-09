"""One command to rebuild the data product.

    python -m salesintel.pipeline                 # resolve -> features -> score/build (uses data/slim)
    python -m salesintel.pipeline --extract RAW   # also run stage 1 from the raw zstd dump first
    python -m salesintel.pipeline --from score    # re-score only (e.g. after editing config/icp.yaml)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time

import duckdb

from salesintel import config, db
from salesintel.etl import build_db, features, resolve


def _dataset_profile(con: duckdb.DuckDBPyConnection) -> dict:
    con.sql(f"create or replace view slim as select * from read_parquet('{config.SLIM_DIR.as_posix()}/*.parquet')")
    row = con.sql("""
        select count(*) records, count(distinct ip) ips, count(distinct org) orgs,
               sum(list_contains(tags, 'honeypot')::int) honeypots,
               sum((len(vuln_ids) > 0)::int) with_cves, sum((coalesce(vuln_kev, 0) > 0)::int) with_kev,
               min(ts) first_seen, max(ts) last_seen
        from slim
    """).fetchone()
    keys = ["records", "ips", "orgs", "honeypots", "with_cves", "with_kev", "first_seen", "last_seen"]
    return {k: (v if isinstance(v, str) else int(v)) for k, v in zip(keys, row)}


def _resolution_stats(con) -> dict:
    rows = con.sql("select coalesce(key_source, 'unresolved') src, count(*) n from resolved group by 1").fetchall()
    return {src: int(n) for src, n in rows}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--extract", metavar="RAW", help="run stage 1 on the raw .zst dump first")
    ap.add_argument("--from", dest="start", choices=["resolve", "features", "score"], default="resolve")
    a = ap.parse_args()

    if a.extract:
        subprocess.run([sys.executable, "-m", "salesintel.etl.extract", a.extract, str(config.SLIM_DIR)],
                       check=True, cwd=config.ROOT)

    t0 = time.time()
    con = duckdb.connect(str(config.DATA_DIR / "work.duckdb"))
    if a.start == "resolve":
        print("[1/3] resolving records -> accounts", flush=True)
        resolve.run(con)
        con.sql(f"copy (select * from resolved where account_key is not null) "
                f"to '{(config.DATA_DIR / 'resolved.parquet').as_posix()}' (compression zstd)")
    if a.start in ("resolve", "features"):
        print("[2/3] building account features + evidence", flush=True)
        features.run(con)
    print("[3/3] scoring + writing app.db", flush=True)
    stats = build_db.run(con)

    app = db.connect()
    db.set_meta(app, "dataset", _dataset_profile(con))
    if a.start == "resolve":
        db.set_meta(app, "resolution", _resolution_stats(con))
    app.commit()
    print(json.dumps(stats, indent=2))
    print(f"pipeline finished in {time.time() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
