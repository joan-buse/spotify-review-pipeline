"""Enrichment through the provider's asynchronous Message Batches API (queue; billed at a batch discount).
Same prompt, schema, validation and <=50 reviews per request as the synchronous path; results are saved per request.
  python -m pipeline.batch_enrich submit  --run-dir D --max-texts N     # queue pending texts
  python -m pipeline.batch_enrich collect --run-dir D                   # poll + save results (idempotent)
Invalid/failed items simply stay pending; the synchronous path retries them once afterwards."""
import argparse, json, sys, time
from pathlib import Path
import anthropic
from .common import *
from .prepare import connect
from .enrich import render_user, parse_output, build_result
from .prompts import ENRICH_SYSTEM

BATCH_MULTIPLIER = 0.5  # ASSUMED provider batch discount (cost/rates.csv, item batch_multiplier); verify against the console


def submit(run_dir, max_texts, per_request=50, est_cost_per_text=0.000118):
    load_env(); run_dir = Path(run_dir); db = connect(run_dir / "state" / "state.sqlite"); client = anthropic.Anthropic(max_retries=2)
    pend = db.execute("""SELECT text_hash, rep_id, text FROM texts WHERE text_hash NOT IN (SELECT text_hash FROM results)
                         AND text_hash NOT IN (SELECT text_hash FROM failures) ORDER BY rowid LIMIT ?""", (max_texts,)).fetchall()
    chunks = [pend[i:i + per_request] for i in range(0, len(pend), per_request)]
    reqs, mapping = [], {}
    for n, ch in enumerate(chunks):
        cid = f"b{int(time.time())}-{n}"
        mapping[cid] = [(th, rid) for th, rid, _ in ch]
        reqs.append({"custom_id": cid, "params": {"model": ENRICH_MODEL, "max_tokens": 2000, "thinking": {"type": "disabled"},
                     "system": ENRICH_SYSTEM, "messages": [{"role": "user", "content": render_user([(rid, t) for _, rid, t in ch])}]}})
    est = len(pend) * est_cost_per_text * BATCH_MULTIPLIER
    print(f"submitting {len(reqs)} requests / {len(pend)} texts; estimated cost ${est:.2f} at assumed batch discount x{BATCH_MULTIPLIER}", flush=True)
    b = client.messages.batches.create(requests=reqs)
    write_json(run_dir / "state" / f"batch_{b.id}.json", {"batch_id": b.id, "mapping": mapping, "submitted_at": time.time(), "texts": len(pend),
                                                         "estimated_cost_usd": est, "collected": False})
    JsonlLog(run_dir / "run_log.jsonl").write({"event": "batch_submitted", "stage": "enrich", "batch_id": b.id, "requests": len(reqs), "texts": len(pend), "ts": time.time()})
    print("batch id", b.id)
    return b.id


def collect(run_dir, poll_s=30, wait=True):
    load_env(); run_dir = Path(run_dir); db = connect(run_dir / "state" / "state.sqlite"); client = anthropic.Anthropic(max_retries=2)
    log = JsonlLog(run_dir / "calls.jsonl"); stats = {"requests_ok": 0, "requests_failed": 0, "texts_saved": 0, "invalid_items": 0, "cost_usd": 0.0}
    for f in sorted((run_dir / "state").glob("batch_*.json")):
        meta = json.load(open(f))
        if meta["collected"]:
            continue
        while True:
            b = client.messages.batches.retrieve(meta["batch_id"])
            print(f"{meta['batch_id']}: {b.processing_status} {b.request_counts}", flush=True)
            if b.processing_status == "ended" or not wait:
                break
            time.sleep(poll_s)
        if b.processing_status != "ended":
            continue
        texts = {th: t for th, t in db.execute("SELECT text_hash, text FROM texts")}
        phase = "resume"
        for res in client.messages.batches.results(meta["batch_id"]):
            items = meta["mapping"][res.custom_id]; ids = [rid for _, rid in items]
            if res.result.type != "succeeded":
                log.write({"request_id": f"{meta['batch_id']}:{res.custom_id}", "role": "enrich", "review_ids": ids, "model": ENRICH_MODEL, "phase": phase,
                           "outcome": "failed", "label_config": LABEL_CONFIG, "input_tokens": 0, "output_tokens": 0,
                           "error": res.result.type, "processing_tier": "batch", "ts": time.time()})
                stats["requests_failed"] += 1; continue
            m = res.result.message; u = m.usage
            usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                     "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0, "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
            cost = cost_usd(ENRICH_MODEL, usage) * BATCH_MULTIPLIER
            total_in = usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]
            log.write({"request_id": m.id, "role": "enrich", "review_ids": ids, "model": ENRICH_MODEL, "phase": phase, "outcome": "succeeded",
                       "label_config": LABEL_CONFIG, "input_tokens": total_in, "output_tokens": usage["output_tokens"], "usage": usage,
                       "cost_usd": round(cost, 6), "processing_tier": "batch", "batch_multiplier_assumed": BATCH_MULTIPLIER,
                       "custom_id": res.custom_id, "stop_reason": m.stop_reason, "ts": time.time()})
            stats["requests_ok"] += 1; stats["cost_usd"] += cost
            raw = "".join(x.text for x in m.content if x.type == "text")
            got, errors = parse_output(raw, set(range(1, len(items) + 1)))
            with db:
                for i, (th, rid) in enumerate(items, 1):
                    if i in got:
                        try:
                            r = build_result(texts[th], got[i])
                            db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?)", (th, rid, LABEL_CONFIG, json.dumps(r, ensure_ascii=False), m.id, phase, time.time()))
                            stats["texts_saved"] += 1; continue
                        except AssertionError:
                            pass
                    stats["invalid_items"] += 1
        meta["collected"] = True; meta["stats"] = stats; write_json(f, meta)
    print(json.dumps(stats)); return stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); ap.add_argument("cmd", choices=["submit", "collect"]); ap.add_argument("--run-dir", required=True)
    ap.add_argument("--max-texts", type=int, default=1000); ap.add_argument("--no-wait", action="store_true")
    a = ap.parse_args(); submit(a.run_dir, a.max_texts) if a.cmd == "submit" else collect(a.run_dir, wait=not a.no_wait)
