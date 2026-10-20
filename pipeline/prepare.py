"""Stage 1 PREPARE (code only, no model calls).

Reads the whole CSV with a multiline-safe parser, preserves IDs/hashes, profiles quality, finds exact duplicate
texts, selects the rows to classify and builds the pending-work queue in a SQLite state DB.
"""
import argparse, hashlib, sqlite3, statistics, time
from collections import Counter
from pathlib import Path
from .common import *

SCHEMA = """
CREATE TABLE IF NOT EXISTS source(review_id TEXT PRIMARY KEY, source_sha256 TEXT, text_hash TEXT, empty INT, selected INT);
CREATE TABLE IF NOT EXISTS texts(text_hash TEXT PRIMARY KEY, text TEXT, rep_id TEXT, n_selected INT);
CREATE TABLE IF NOT EXISTS results(text_hash TEXT PRIMARY KEY, rep_id TEXT, label_config TEXT, result_json TEXT,
                                   request_id TEXT, phase TEXT, completed_at REAL);
CREATE TABLE IF NOT EXISTS failures(text_hash TEXT PRIMARY KEY, reason TEXT, attempts INT);
CREATE INDEX IF NOT EXISTS source_text ON source(text_hash);
"""


def connect(db_path):
    db = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
    db.execute("PRAGMA journal_mode=WAL"); db.execute("PRAGMA synchronous=FULL")
    db.executescript(SCHEMA)
    return db


def prepare(input_csv, run_dir, target_rows=0, force_ids=(), selected_ids=None):
    t0 = time.time()
    run_dir = Path(run_dir); (run_dir / "state").mkdir(parents=True, exist_ok=True)
    rows = []
    stats = Counter(); text_counts = Counter(); lengths = []
    for r in csv_rows(input_csv):
        sha = row_sha(r)
        empty = not r["review_text"].strip()
        th = hashlib.sha256(r["review_text"].encode("utf-8")).hexdigest()
        rows.append((r["review_id"], sha, th, int(empty), sample_key(r["review_id"]), r["review_text"]))
        stats["rows"] += 1
        if empty:
            stats["empty"] += 1
        else:
            text_counts[th] += 1; lengths.append(len(r["review_text"]))
    lengths = lengths or [0]
    nonempty = [r for r in rows if not r[3]]
    if selected_ids is not None:
        chosen = {r[0] for r in nonempty if r[0] in selected_ids}
    elif target_rows and target_rows < len(nonempty):
        chosen = {r[0] for r in sorted(nonempty, key=lambda r: r[4])[:target_rows]} | set(force_ids)
    else:
        chosen = {r[0] for r in nonempty}
    db = connect(run_dir / "state" / "state.sqlite")
    db.execute("DELETE FROM source WHERE 1=1") if False else None
    first_rep, n_sel = {}, Counter()
    for rid, sha, th, empty, key, text in sorted(rows, key=lambda r: r[4]):  # representative = lowest sample key
        if rid in chosen and not empty:
            first_rep.setdefault(th, (rid, text)); n_sel[th] += 1
    with db:
        db.executemany("INSERT OR IGNORE INTO source VALUES(?,?,?,?,?)",
                       [(rid, sha, th, empty, int(rid in chosen)) for rid, sha, th, empty, key, text in rows])
        db.executemany("UPDATE source SET selected=1 WHERE review_id=? AND selected=0", [(rid,) for rid in chosen])  # scope expansion on later runs
        db.executemany("INSERT OR IGNORE INTO texts VALUES(?,?,?,?)",
                       [(th, text, rid, n_sel[th]) for th, (rid, text) in first_rep.items()])
    pending = db.execute("SELECT count(*) FROM texts WHERE text_hash NOT IN (SELECT text_hash FROM results)").fetchone()[0]
    report = {
        "input_csv": str(input_csv), "rows": stats["rows"], "empty_text_rows": stats["empty"],
        "nonempty_rows": len(nonempty), "distinct_nonempty_texts": len(text_counts),
        "rows_in_duplicate_texts": sum(c for c in text_counts.values() if c > 1),
        "most_common_duplicate_text_counts": [c for _, c in text_counts.most_common(10)],
        "selected_rows": len(chosen), "selected_distinct_texts": len(first_rep),
        "selection_rule": f"lowest SHA-256('{SEED}:'+review_id) among nonempty rows, target={target_rows or 'all'}, plus forced ids={len(force_ids)}",
        "text_length_chars": {"median": statistics.median(lengths), "mean": round(statistics.mean(lengths), 1),
                              "p95": sorted(lengths)[int(.95 * len(lengths))], "max": max(lengths)},
        "pending_distinct_texts": pending, "elapsed_s": round(time.time() - t0, 2),
    }
    write_json(run_dir / "ingestion_report.json", report)
    return report


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--input", required=True); ap.add_argument("--run-dir", required=True)
    ap.add_argument("--target-rows", type=int, default=0); ap.add_argument("--force-ids-csv")
    a = ap.parse_args()
    force = [r["review_id"] for r in csv_rows(a.force_ids_csv)] if a.force_ids_csv else []
    print(json.dumps(prepare(a.input, a.run_dir, a.target_rows, force), indent=2))
