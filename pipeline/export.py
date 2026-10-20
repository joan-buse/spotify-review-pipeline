"""Assemble the grading/ folder from saved run artifacts (no model calls)."""
import csv, gzip, json, shutil
from pathlib import Path
from .common import *


def export_grading(run_dir, input_csv, out_dir, analysis_sha=None, checkpoint_before=None, checkpoint_after=None):
    run_dir, out_dir = Path(run_dir), Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    for name in ("records.jsonl", "calls.jsonl", "membership.csv", "ranking.csv", "claims.csv"):
        shutil.copy(run_dir / name, out_dir / name)
    full_sha = sha_file(input_csv)
    write_json(out_dir / "run.json", {"version": "a5-audit-v1", "analysis_count": sum(1 for _ in csv_rows(input_csv)),
                                      "analysis_sha256": analysis_sha or full_sha, "classification_input_fields": ["review_text"],
                                      "allow_multi_issue": False})
    if (run_dir / "ingestion.json").exists():
        shutil.copy(run_dir / "ingestion.json", out_dir / "ingestion.json")
    for src, name in ((checkpoint_before, "checkpoint_before.json"), (checkpoint_after, "checkpoint_after.json")):
        if src:
            shutil.copy(src, out_dir / name)


def sha_file(p):
    import hashlib
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()
