"""Re-point a finished run at a different (e.g. the official) copy of the CSV WITHOUT repeating paid work.
Selected IDs and exact-text results are carried over; only texts that differ in the new file become pending again.
  python -m pipeline.remap --old-run runs/full --new-run runs/full_official --csv official.csv"""
import argparse, shutil, sqlite3
from pathlib import Path
from .common import *
from .prepare import prepare, connect


def remap(old_run, new_run, csv_path):
    old_run, new_run = Path(old_run), Path(new_run)
    if new_run.exists():
        raise SystemExit(f"{new_run} exists; refusing to overwrite")
    shutil.copytree(old_run, new_run, ignore=shutil.ignore_patterns("state", "records.jsonl", "membership.csv", "ranking.csv", "aggregates.csv"))
    old = sqlite3.connect(old_run / "state" / "state.sqlite")
    selected = {r[0] for r in old.execute("SELECT review_id FROM source WHERE selected=1")}
    rep = prepare(csv_path, new_run, selected_ids=selected)
    db = connect(new_run / "state" / "state.sqlite")
    kept = lost = 0
    for th, rep_id, cfg, rj, req, phase, at in old.execute("SELECT * FROM results"):
        row = db.execute("SELECT rep_id FROM texts WHERE text_hash=?", (th,)).fetchone()
        if row and row[0] == rep_id:
            with db: db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?)", (th, rep_id, cfg, rj, req, phase, at))
            kept += 1
        else:
            lost += 1
    for th, reason, att in old.execute("SELECT * FROM failures"):
        pass  # failures are retried on the new source (pending)
    out = {"selected_ids": len(selected), "results_carried_over": kept, "results_not_applicable_text_or_rep_changed": lost,
           "pending_after_remap": db.execute("SELECT count(*) FROM texts WHERE text_hash NOT IN (SELECT text_hash FROM results)").fetchone()[0]}
    write_json(new_run / "remap_report.json", out); print(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("--old-run", required=True); ap.add_argument("--new-run", required=True); ap.add_argument("--csv", required=True)
    a = ap.parse_args(); remap(a.old_run, a.new_run, a.csv)
