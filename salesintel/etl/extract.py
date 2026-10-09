"""Stage 1: stream the raw Shodan zstd NDJSON dump into slim Parquet parts.

One pass over ~79 GB uncompressed. The main process decompresses and cuts the
stream into newline-aligned chunks; a worker pool parses each chunk with orjson,
keeps only the fields we use downstream, and writes one Parquet file per chunk.

Usage:
    python -m salesintel.etl.extract <raw.zst> data/slim [--limit-chunks N]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import orjson
import pyarrow as pa
import pyarrow.parquet as pq
import zstandard

CHUNK_BYTES = 256 * 1024 * 1024

SCHEMA = pa.schema([
    ("ip", pa.string()), ("port", pa.int32()), ("transport", pa.string()),
    ("ts", pa.string()), ("module", pa.string()),
    ("org", pa.string()), ("isp", pa.string()), ("asn", pa.string()),
    ("country_code", pa.string()), ("country", pa.string()),
    ("region_code", pa.string()), ("city", pa.string()),
    ("hostnames", pa.list_(pa.string())), ("domains", pa.list_(pa.string())),
    ("tags", pa.list_(pa.string())), ("product", pa.string()), ("version", pa.string()),
    ("cpe23", pa.list_(pa.string())), ("os", pa.string()), ("info", pa.string()),
    ("cloud_provider", pa.string()),
    ("http_title", pa.string()), ("http_host", pa.string()), ("http_server", pa.string()),
    ("http_status", pa.int32()), ("http_waf", pa.string()), ("http_securitytxt", pa.bool_()),
    ("ssl_cn", pa.string()), ("ssl_subject_o", pa.string()), ("ssl_issuer_cn", pa.string()),
    ("ssl_expired", pa.bool_()), ("ssl_expires", pa.string()), ("ssl_versions", pa.list_(pa.string())),
    ("vuln_ids", pa.list_(pa.string())), ("vuln_max_cvss", pa.float32()),
    ("vuln_max_epss", pa.float32()), ("vuln_kev", pa.int32()),
    ("db_noauth", pa.bool_()), ("vnc_noauth", pa.bool_()), ("ftp_anon", pa.bool_()),
    ("banner", pa.string()),
])


def _s(v, n=300):
    if v is None:
        return None
    if not isinstance(v, str):
        v = str(v)
    return v[:n]


def _first(v):
    if isinstance(v, list):
        return v[0] if v else None
    return v


def slim(d: dict) -> dict:
    loc = d.get("location") or {}
    sh = d.get("_shodan") or {}
    http = d.get("http") or {}
    ssl = d.get("ssl") or {}
    cert = ssl.get("cert") or {}
    subj = cert.get("subject") or {}
    iss = cert.get("issuer") or {}
    cloud = d.get("cloud") or {}
    vulns = d.get("vulns") or {}

    cvss = [v.get("cvss") for v in vulns.values() if isinstance(v, dict) and v.get("cvss") is not None]
    epss = [v.get("epss") for v in vulns.values() if isinstance(v, dict) and v.get("epss") is not None]
    kev = sum(1 for v in vulns.values() if isinstance(v, dict) and v.get("kev"))

    # Unauthenticated data stores: the classic "your database is on the internet" signal.
    db_noauth = False
    mongo = d.get("mongodb")
    if isinstance(mongo, dict) and mongo.get("authentication") is False:
        db_noauth = True
    for k in ("redis", "elastic", "couchdb", "memcache", "cassandra"):
        if d.get(k):
            db_noauth = True  # banner was retrievable without credentials
    vnc = d.get("vnc") or {}
    vnc_noauth = isinstance(vnc, dict) and any(
        "None" == str(t) for t in (vnc.get("security_types") or {}).values())
    ftp = d.get("ftp") or {}
    ftp_anon = bool(isinstance(ftp, dict) and ftp.get("anonymous"))

    return {
        "ip": d.get("ip_str") or (d.get("ipv6")), "port": d.get("port"),
        "transport": d.get("transport"), "ts": d.get("timestamp"), "module": sh.get("module"),
        "org": _s(d.get("org")), "isp": _s(d.get("isp")), "asn": d.get("asn"),
        "country_code": loc.get("country_code"), "country": loc.get("country_name"),
        "region_code": loc.get("region_code"), "city": loc.get("city"),
        "hostnames": [_s(h, 200) for h in (d.get("hostnames") or [])[:20]],
        "domains": [_s(h, 200) for h in (d.get("domains") or [])[:20]],
        "tags": d.get("tags") or [], "product": _s(d.get("product")), "version": _s(d.get("version")),
        "cpe23": (d.get("cpe23") or [])[:10], "os": _s(d.get("os")), "info": _s(d.get("info")),
        "cloud_provider": cloud.get("provider"),
        "http_title": _s(http.get("title")), "http_host": _s(http.get("host")),
        "http_server": _s(http.get("server")), "http_status": http.get("status") if isinstance(http.get("status"), int) else None,
        "http_waf": _s(http.get("waf")), "http_securitytxt": bool(http.get("securitytxt")) if http else None,
        "ssl_cn": _s(_first(subj.get("CN"))), "ssl_subject_o": _s(_first(subj.get("O"))),
        "ssl_issuer_cn": _s(_first(iss.get("CN"))),
        "ssl_expired": cert.get("expired") if cert else None, "ssl_expires": cert.get("expires"),
        "ssl_versions": [v for v in (ssl.get("versions") or []) if isinstance(v, str) and not v.startswith("-")],
        "vuln_ids": list(vulns.keys())[:200],
        "vuln_max_cvss": max(cvss) if cvss else None, "vuln_max_epss": max(epss) if epss else None,
        "vuln_kev": kev,
        "db_noauth": db_noauth, "vnc_noauth": vnc_noauth, "ftp_anon": ftp_anon,
        "banner": _s(d.get("data"), 200),
    }


def process_chunk(args):
    idx, buf, out_dir = args
    rows, bad = [], 0
    for line in buf.split(b"\n"):
        if not line:
            continue
        try:
            rows.append(slim(orjson.loads(line)))
        except Exception:
            bad += 1
    table = pa.Table.from_pylist(rows, schema=SCHEMA)
    pq.write_table(table, os.path.join(out_dir, f"part-{idx:05d}.parquet"), compression="zstd")
    return idx, len(rows), bad


def chunks(path, limit=None):
    with open(path, "rb") as f:
        reader = zstandard.ZstdDecompressor().stream_reader(f, read_size=1 << 22)
        tail, idx = b"", 0
        while True:
            block = reader.read(CHUNK_BYTES)
            if not block:
                break
            block = tail + block
            cut = block.rfind(b"\n") + 1
            tail = block[cut:]
            yield idx, block[:cut]
            idx += 1
            if limit and idx >= limit:
                return
        if tail.strip():
            yield idx, tail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw")
    ap.add_argument("out_dir")
    ap.add_argument("--limit-chunks", type=int, default=None)
    ap.add_argument("--workers", type=int, default=max(2, (os.cpu_count() or 4) - 4))
    a = ap.parse_args()
    os.makedirs(a.out_dir, exist_ok=True)

    t0, total, bad, pending = time.time(), 0, 0, []
    with ProcessPoolExecutor(a.workers) as ex:
        for idx, buf in chunks(a.raw, a.limit_chunks):
            pending.append(ex.submit(process_chunk, (idx, buf, a.out_dir)))
            # Bound memory: never hold more than ~2x workers chunks in flight.
            while len(pending) >= a.workers * 2:
                done = pending.pop(0).result()
                total += done[1]; bad += done[2]
                print(f"chunk {done[0]} rows={total:,} bad={bad} {time.time()-t0:.0f}s", flush=True)
        for p in pending:
            done = p.result()
            total += done[1]; bad += done[2]
    print(f"DONE rows={total:,} bad={bad} secs={time.time()-t0:.0f}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
