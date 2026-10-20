"""Build one final record per source ID (completed or quarantined) from saved state. No model calls."""
import json, sqlite3
from pathlib import Path
from .common import *
from .prepare import connect


def build_records(run_dir, out_path=None):
    run_dir = Path(run_dir); db = connect(run_dir / "state" / "state.sqlite")
    res = {th: (rep, cfg, json.loads(rj), rid_call) for th, rep, cfg, rj, rid_call in
           db.execute("SELECT text_hash, rep_id, label_config, result_json, request_id FROM results")}
    fails = dict(db.execute("SELECT text_hash, reason FROM failures"))
    n = {"completed": 0, "cache_reuse": 0, "quarantined": {}}
    out = open(out_path or run_dir / "records.jsonl", "w", encoding="utf-8")
    def q(reason):
        n["quarantined"][reason.split(":")[0]] = n["quarantined"].get(reason.split(":")[0], 0) + 1
    for rid, sha, th, empty, selected in db.execute("SELECT review_id, source_sha256, text_hash, empty, selected FROM source"):
        base = {"review_id": rid, "source_sha256": sha}
        if empty:
            rec = {**base, "status": "quarantined", "reason": "empty_review_text", "attempts": 0}; q("empty_review_text")
        elif not selected:
            rec = {**base, "status": "quarantined", "reason": "not_selected_budget_scope", "attempts": 0}; q("not_selected_budget_scope")
        elif th in res:
            rep, cfg, r, _ = res[th]
            rec = {**base, "status": "completed", "topic": r["topic"], "intent": r["intent"], "sentiment": r["sentiment"],
                   "severity": r["severity"], "entities": r["entities"], "evidence_quote": r["evidence_quote"],
                   "needs_review": r["needs_review"], "label_config": cfg, "subtopic": r["subtopic"]}
            if rid != rep:
                rec["cache_source_id"] = rep; n["cache_reuse"] += 1
            n["completed"] += 1
        elif th in fails:
            rec = {**base, "status": "quarantined", "reason": "model_stage_failure: " + fails[th], "attempts": 2}; q("model_stage_failure")
        else:
            rec = {**base, "status": "quarantined", "reason": "pending_not_attempted", "attempts": 0}; q("pending_not_attempted")
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
    out.close()
    return n
