"""Orchestrator: accepts an input CSV and runs ingest -> enrich -> verify -> group -> rank -> recommend.
Code decides stage order, batching, budgets, retries, validation and arithmetic; models only read messy language.
Every stage saves its handoff under the run dir; completed stages are skipped unless their inputs change."""
import argparse, hashlib, json, os, shutil, sqlite3, sys, time
from pathlib import Path
from .common import *
from .prepare import prepare, connect
from .enrich import Enricher
from .records import build_records
from .rank import rank
from .group import run_group
from .memo import run_memo
from .verify import verify_sample


class Stages:
    def __init__(self, run_dir):
        self.run_dir = Path(run_dir); self.log = JsonlLog(self.run_dir / "run_log.jsonl"); self.timings = {}

    def timed(self, name, fn, **meta):
        t0 = time.time(); self.log.write({"event": "start", "stage": name, "ts": t0, **meta})
        out = fn(); dt = time.time() - t0; self.timings[name] = round(dt, 3)
        self.log.write({"event": "end", "stage": name, "seconds": round(dt, 3), "ts": time.time()})
        return out


def fingerprint(*paths, extra=""):
    h = hashlib.sha256(extra.encode())
    for p in paths:
        h.update(Path(p).read_bytes() if Path(p).exists() else b"missing")
    return h.hexdigest()


def stage_cached(run_dir, name, fp):
    f = Path(run_dir) / "state" / f"stage_{name}.fp"
    return f.exists() and f.read_text() == fp


def stage_done(run_dir, name, fp):
    (Path(run_dir) / "state" / f"stage_{name}.fp").write_text(fp)


def snapshot(run_dir, out_path):
    db = connect(Path(run_dir) / "state" / "state.sqlite")
    ids = [r[0] for r in db.execute("SELECT s.review_id FROM source s JOIN results r USING(text_hash) WHERE s.selected=1 ORDER BY s.review_id")]
    write_json(out_path, {"completed_ids": ids, "count": len(ids), "saved_at": time.time()})
    return len(ids)


def coverage(run_dir, counts, out_dir):
    db = connect(Path(run_dir) / "state" / "state.sqlite")
    total = db.execute("SELECT count(*) FROM source").fetchone()[0]
    cov = {"source_rows": total, "completed_records": counts["completed"], "exact_text_cache_reuse_records": counts["cache_reuse"],
           "quarantined_by_reason": counts["quarantined"]}
    write_json(Path(out_dir) / "coverage.json", cov)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--input", required=True, help="input CSV (review_id,review_text,review_rating,review_likes,app_version,review_timestamp)")
    ap.add_argument("--run-dir", required=True); ap.add_argument("--target-rows", type=int, default=0, help="0 = all nonempty rows")
    ap.add_argument("--force-ids-csv"); ap.add_argument("--budget", type=float, required=True, help="USD spend cap for the whole run")
    ap.add_argument("--workers", type=int, default=1); ap.add_argument("--rps", type=float, default=3.0)
    ap.add_argument("--max-batches", type=int, help="stop after N enrichment batches (interruption demo / bounded experiments)")
    ap.add_argument("--verify-n", type=int, default=1000); ap.add_argument("--skip-model-downstream", action="store_true")
    ap.add_argument("--snapshot", help="write completed-ID snapshot to this path after enrichment")
    a = ap.parse_args()
    run_dir = Path(a.run_dir); (run_dir / "state").mkdir(parents=True, exist_ok=True)
    st = Stages(run_dir); t_all = time.time()
    force = [r["review_id"] for r in csv_rows(a.force_ids_csv)] if a.force_ids_csv else []
    rep = st.timed("prepare", lambda: prepare(a.input, run_dir, a.target_rows, force))
    if not (run_dir / "ingestion.json").exists() and a.target_rows != 0 or True:
        ing = st.timed("profile", lambda: profile(a.input)); write_json(run_dir / "ingestion.json", ing)
    en = Enricher(run_dir, a.workers, a.budget, rps=a.rps)
    stats = st.timed("enrich", lambda: en.run(a.max_batches), phase=en.phase)
    if a.snapshot:
        snapshot(run_dir, a.snapshot)
    counts = st.timed("records", lambda: build_records(run_dir))
    out = run_dir
    ranking = st.timed("rank", lambda: rank(run_dir / "records.jsonl", out))
    coverage(run_dir, counts, out)
    summary = {"run_id": run_dir.name, "input": a.input, "label_config": LABEL_CONFIG, "enrich_stats": stats, "records": counts,
               "stage_seconds": st.timings, "budget_usd": a.budget, "workers": a.workers, "ingestion_report": rep}
    if not a.skip_model_downstream and counts["completed"]:
        rec_fp = fingerprint(run_dir / "records.jsonl")
        if not stage_cached(run_dir, "verify", rec_fp):
            vr = st.timed("verify", lambda: verify_sample(run_dir, n=a.verify_n, budget=max(0.05, a.budget * 0.1), workers=a.workers))
            stage_done(run_dir, "verify", rec_fp); summary["verify"] = vr
        else:
            summary["verify"] = "cached (inputs unchanged): zero new calls"
        if not stage_cached(run_dir, "group", rec_fp):
            st.timed("group", lambda: run_group(run_dir / "records.jsonl", out, run_dir / "calls.jsonl")); stage_done(run_dir, "group", rec_fp)
        if not stage_cached(run_dir, "memo", rec_fp + fingerprint(run_dir / "issues.json")):
            st.timed("memo", lambda: run_memo(run_dir, out, run_dir / "calls.jsonl")); stage_done(run_dir, "memo", rec_fp + fingerprint(run_dir / "issues.json"))
    summary["total_wall_clock_s"] = round(time.time() - t_all, 2); summary["stage_seconds"] = st.timings
    write_json(run_dir / "run_summary.json", summary)
    print(json.dumps({k: summary[k] for k in ("run_id", "records", "stage_seconds", "total_wall_clock_s")}, indent=1))


if __name__ == "__main__":
    main()
