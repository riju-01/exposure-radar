"""One-command eval harness.

    python -m evals.run                      # every feature: baseline + active + candidate prompt
    python -m evals.run account-classify     # one feature
    python -m evals.run outreach-draft --versions v2 --limit 3

For each feature it runs the offline baseline and each requested prompt version over the frozen
dataset (evals/datasets/*.jsonl), scores the outputs, compares every version with the active
version and with that version's previous run, then writes:

    evals/results/<feature>/<run_id>.json   full per-example results
    evals/results/history.jsonl             one summary line per (feature, version, run)
    evals/results/REPORT.md                 the human-readable comparison (regenerated)

Live prompt versions need ANTHROPIC_API_KEY; without it they are reported as skipped and only
the baseline runs, so the harness itself is always runnable.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import uuid
from datetime import datetime, timezone

from salesintel import config
from salesintel.llm import registry
from salesintel.llm.context import AccountContext
from salesintel.llm.grounding import check_outreach
from salesintel.llm.offline import OFFLINE
from salesintel.llm.providers import OfflineProvider, active_provider_name, default_provider, live_available
from salesintel.llm.runner import run_feature
from salesintel.llm.schemas import PROSPECT_ENTITY_TYPES

DATASETS = config.EVALS_DIR / "datasets"
RESULTS = config.EVALS_DIR / "results"
HISTORY = RESULTS / "history.jsonl"
BASELINE = "baseline"


def load_dataset(feature: str) -> list[dict]:
    path = DATASETS / f"{feature}.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- scoring
def _pct(n: float, d: float) -> float | None:
    return round(n / d, 3) if d else None


def score_classify(rows: list[dict]) -> dict:
    tp = sum(r["gold"]["is_prospect"] and r["pred"]["is_prospect"] for r in rows)
    fp = sum(not r["gold"]["is_prospect"] and r["pred"]["is_prospect"] for r in rows)
    fn = sum(r["gold"]["is_prospect"] and not r["pred"]["is_prospect"] for r in rows)
    tn = len(rows) - tp - fp - fn
    precision, recall = _pct(tp, tp + fp), _pct(tp, tp + fn)
    f1 = round(2 * precision * recall / (precision + recall), 3) if precision and recall else 0.0
    by_diff: dict[str, dict] = {}
    for r in rows:
        d = by_diff.setdefault(r["difficulty"], {"n": 0, "prospect_correct": 0})
        d["n"] += 1
        d["prospect_correct"] += r["gold"]["is_prospect"] == r["pred"]["is_prospect"]
    return {
        "n": len(rows),
        "prospect": {"precision": precision, "recall": recall, "f1": f1, "accuracy": _pct(tp + tn, len(rows)),
                     "confusion": {"tp": tp, "fp": fp, "fn": fn, "tn": tn}},
        "entity_type_accuracy": _pct(sum(r["gold"]["entity_type"] == r["pred"]["entity_type"] for r in rows), len(rows)),
        "industry_accuracy": _pct(sum(r["pred"]["industry"] in r["gold"]["industry_ok"] for r in rows), len(rows)),
        "consistency_violations": sum(
            (r["pred"]["entity_type"] in PROSPECT_ENTITY_TYPES) != r["pred"]["is_prospect"] for r in rows),
        "by_difficulty": {k: {**v, "accuracy": _pct(v["prospect_correct"], v["n"])} for k, v in sorted(by_diff.items())},
        "errors": [{"id": r["id"], "gold": {k: r["gold"][k] for k in ("entity_type", "is_prospect", "industry")},
                    "pred": {k: r["pred"][k] for k in ("entity_type", "is_prospect", "industry")},
                    "rationale": r["pred"].get("rationale", "")}
                   for r in rows if r["gold"]["is_prospect"] != r["pred"]["is_prospect"]
                   or r["pred"]["industry"] not in r["gold"]["industry_ok"]],
    }


def score_outreach(rows: list[dict]) -> dict:
    checks = [r["checks"] for r in rows]
    m = [c["metrics"] for c in checks]
    cves_mentioned = sum(x["cves_mentioned"] for x in m)
    points = sum(x["why_now_points"] for x in m)
    return {
        "n": len(rows),
        "guardrail_pass_rate": _pct(sum(c["passed"] for c in checks), len(rows)),
        "cve_grounding_rate": _pct(sum(x["cves_grounded"] for x in m), cves_mentioned) if cves_mentioned else 1.0,
        "cves_mentioned": cves_mentioned,
        "supported_point_rate": _pct(sum(x["why_now_supported"] for x in m), points),
        "avg_email_words": round(statistics.mean(x["email_words"] for x in m), 1) if m else None,
        "failures": [{"id": r["id"], "issues": r["checks"]["issues"]} for r in rows if not r["checks"]["passed"]],
    }


SCORERS = {"account-classify": score_classify, "outreach-draft": score_outreach}


# --------------------------------------------------------------------------- running
def run_version(feature: str, version: str, dataset: list[dict], run_id: str) -> dict:
    is_baseline = version == BASELINE
    provider = OfflineProvider() if is_baseline else default_provider()
    prompt_version = registry.active_version(feature) if is_baseline else version
    rows, costs, latencies, statuses, models = [], [], [], {}, {}
    for ex in dataset:
        ctx = AccountContext.from_dict(ex["context"])
        res = run_feature(feature, ctx, version=prompt_version, provider=provider, env="eval", run_id=run_id)
        statuses[res.status] = statuses.get(res.status, 0) + 1
        costs.append(res.cost_usd)
        latencies.append(res.latency_ms)
        models[res.model] = models.get(res.model, 0) + 1
        row = {"id": ex["id"], "pred": res.output, "status": res.status, "note": res.note, "call_id": res.call_id}
        if feature == "account-classify":
            row.update(gold=ex["gold"], difficulty=ex["difficulty"])
        else:
            row["checks"] = check_outreach(res.output, ctx)
        rows.append(row)
        print(f"  {version:<9} {ex['id'][:40]:<40} {res.status:<9} {res.model}", flush=True)
    metrics = SCORERS[feature](rows)
    lat = sorted(latencies)
    metrics["ops"] = {
        # the model that actually answered most rows (fallback rows show up as rules-v0/template-v0)
        "model": OFFLINE[feature][0] if is_baseline else max(models, key=models.get),
        "provider": "offline" if is_baseline else provider.name,
        "models_used": models,
        "total_cost_usd": round(sum(costs), 5), "cost_per_example_usd": round(sum(costs) / len(costs), 6),
        "latency_p50_ms": lat[len(lat) // 2], "latency_p95_ms": lat[min(len(lat) - 1, int(len(lat) * 0.95))],
        "statuses": statuses,
    }
    return {"version": version, "metrics": metrics, "rows": rows}


def _headline(feature: str, metrics: dict) -> dict:
    # Rows that fell back (provider overloaded / invalid output) were answered by the offline
    # rules/template, not the prompt under test, so the fallback rate is part of every headline.
    st = metrics["ops"]["statuses"]
    fallback_rate = round(st.get("fallback", 0) / max(1, sum(st.values())), 3)
    if feature == "account-classify":
        p = metrics["prospect"]
        return {"prospect_precision": p["precision"], "prospect_recall": p["recall"], "prospect_f1": p["f1"],
                "entity_type_acc": metrics["entity_type_accuracy"], "industry_acc": metrics["industry_accuracy"],
                "fallback_rate": fallback_rate}
    return {"guardrail_pass": metrics["guardrail_pass_rate"], "cve_grounding": metrics["cve_grounding_rate"],
            "supported_points": metrics["supported_point_rate"], "avg_words": metrics["avg_email_words"],
            "fallback_rate": fallback_rate}


def _previous(feature: str, version: str) -> dict | None:
    if not HISTORY.exists():
        return None
    last = None
    for line in HISTORY.read_text(encoding="utf-8").splitlines():
        h = json.loads(line)
        if h["feature"] == feature and h["version"] == version and h.get("status") == "ran":
            last = h
    return last


def evaluate(feature: str, versions: list[str], limit: int | None) -> dict:
    dataset = load_dataset(feature)[:limit] if limit else load_dataset(feature)
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:6]
    print(f"\n== {feature}: {len(dataset)} examples, versions {versions} (run {run_id})")
    results, summaries = {}, []
    for v in versions:
        prev = _previous(feature, v)
        if v != BASELINE and not live_available():
            summaries.append({"feature": feature, "version": v, "run_id": run_id, "status": "skipped",
                              "reason": "no LLM API key set (ANTHROPIC_API_KEY or GEMINI_API_KEY)", "previous": prev and prev["headline"]})
            print(f"  {v:<9} skipped (no LLM API key set)")
            continue
        res = run_version(feature, v, dataset, run_id)
        results[v] = res
        summaries.append({"feature": feature, "version": v, "run_id": run_id, "status": "ran",
                          "n": len(dataset), "headline": _headline(feature, res["metrics"]),
                          "ops": res["metrics"]["ops"], "previous": prev and prev["headline"],
                          "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    out_dir = RESULTS / feature
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{run_id}.json").write_text(json.dumps(
        {"run_id": run_id, "feature": feature, "versions": versions, "results": results, "summaries": summaries},
        indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "latest.json").write_text(json.dumps(
        {"run_id": run_id, "summaries": summaries,
         "metrics": {v: {k: m for k, m in r["metrics"].items()} for v, r in results.items()}},
        indent=2, ensure_ascii=False), encoding="utf-8")
    with open(HISTORY, "a", encoding="utf-8") as fh:
        for s in summaries:
            fh.write(json.dumps(s, ensure_ascii=False) + "\n")
    return {"feature": feature, "summaries": summaries, "results": results}


# --------------------------------------------------------------------------- report
def _fmt(v) -> str:
    if v is None:
        return "–"
    return f"{v:.3f}" if isinstance(v, float) else str(v)


def _delta(cur, prev) -> str:
    if not isinstance(cur, (int, float)) or not isinstance(prev, (int, float)):
        return ""
    d = cur - prev
    return "" if abs(d) < 1e-9 else f" ({'+' if d > 0 else ''}{d:.3f})"


def write_report() -> None:
    lines = ["# Eval report", "",
             "_Generated by `python -m evals.run`. Numbers in brackets are the change vs. that version's previous run._", ""]
    for feature in registry.features():
        latest = RESULTS / feature / "latest.json"
        if not latest.exists():
            continue
        data = json.loads(latest.read_text(encoding="utf-8"))
        active = registry.active_version(feature)
        lines += [f"## {feature}", "", f"Run `{data['run_id']}` · active prompt **{active}** · "
                  f"candidate **{registry._registry()['features'][feature].get('candidate', '–')}**", ""]
        if data.get("note"):
            lines += [f"> **Note:** {data['note']}", ""]
        heads = None
        for s in data["summaries"]:
            if s["status"] != "ran":
                continue
            heads = list(s["headline"])
            break
        if heads:
            lines.append("| version | model | " + " | ".join(heads) + " | cost/example | p50 latency | statuses |")
            lines.append("|---" * (len(heads) + 5) + "|")
        for s in data["summaries"]:
            label = s["version"] + (" (active)" if s["version"] == active else "")
            if s["status"] != "ran":
                lines.append(f"| {label} | – | " + " | ".join("–" for _ in (heads or [])) +
                             f" | – | – | skipped: {s['reason']} |")
                continue
            prev = s.get("previous") or {}
            cells = [_fmt(s["headline"][h]) + _delta(s["headline"][h], prev.get(h)) for h in heads]
            ops = s["ops"]
            lines.append(f"| {label} | {ops['model']} | " + " | ".join(cells) +
                         f" | ${ops['cost_per_example_usd']:.5f} | {ops['latency_p50_ms']} ms | {ops['statuses']} |")
        metrics = data.get("metrics", {})
        for v, m in metrics.items():
            errs = m.get("errors") or m.get("failures") or []
            if errs:
                lines += ["", f"<details><summary>{v}: {len(errs)} error(s)</summary>", "", "```json",
                          json.dumps(errs[:15], indent=1, ensure_ascii=False), "```", "</details>"]
            if "by_difficulty" in m:
                lines += ["", f"{v} prospect accuracy by difficulty: " + ", ".join(
                    f"{k} {d['accuracy']} (n={d['n']})" for k, d in m["by_difficulty"].items())]
        lines.append("")
    (RESULTS / "REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("feature", nargs="?", default="all", choices=["all", *registry.features()])
    ap.add_argument("--versions", nargs="+", help="default: baseline + active + candidate")
    ap.add_argument("--limit", type=int)
    a = ap.parse_args()
    features = registry.features() if a.feature == "all" else [a.feature]
    for f in features:
        spec = registry._registry()["features"][f]
        versions = a.versions or list(dict.fromkeys([BASELINE, spec["active"], spec.get("candidate", spec["active"])]))
        evaluate(f, versions, a.limit)
    write_report()
    print(f"\nreport: {RESULTS / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
