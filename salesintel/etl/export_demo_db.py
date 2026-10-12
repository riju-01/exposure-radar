"""Export a small, deployable copy of app.db for hosting.

The full app.db (~137 MB, 96k accounts) exceeds GitHub's 100 MB file limit and is heavy for free hosting
tiers. Reps work A/B accounts, so the demo database keeps those (plus everything already classified or
drafted by the LLM) with their signals, evidence, LLM outputs and build metadata.

    python -m salesintel.etl.export_demo_db            # -> data/app_demo.db
"""
from __future__ import annotations

import sqlite3
import sys

from salesintel import config, db

DEMO_DB = config.DATA_DIR / "app_demo.db"


def export(src_path=config.APP_DB, dst_path=DEMO_DB) -> dict:
    if dst_path.exists():
        dst_path.unlink()
    dst = db.connect(dst_path)
    db.init(dst)
    dst.close()

    con = sqlite3.connect(dst_path)
    con.execute("attach database ? as src", (str(src_path),))
    con.executescript("""
        create temp table keep as
            select id from src.accounts where tier in ('A', 'B')
            union select account_id from src.llm_outputs;
        insert into accounts select * from src.accounts where id in (select id from keep);
        insert into account_signals select * from src.account_signals where account_id in (select id from keep);
        insert into evidence select * from src.evidence where account_id in (select id from keep);
        insert into llm_outputs select * from src.llm_outputs where account_id in (select id from keep);
        insert into meta select * from src.meta;
    """)
    con.commit()
    stats = {t: con.execute(f"select count(*) from {t}").fetchone()[0]
             for t in ("accounts", "account_signals", "evidence", "llm_outputs")}
    con.execute("detach database src")
    con.execute("vacuum")
    con.close()
    stats["size_mb"] = round(dst_path.stat().st_size / 1e6, 1)
    return stats


if __name__ == "__main__":
    print(export())
    sys.exit(0)
