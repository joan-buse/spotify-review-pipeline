"""Stage 3 VERIFY: an independent model task re-labels a declared random sample WITHOUT seeing the first labels; code compares."""
import csv, json, re, sqlite3
from pathlib import Path
from .common import *
from .enrich import Enricher, render_user
from .prompts import VERIFY_SYSTEM
from .prepare import connect

VLINE = re.compile(r"^\s*(\d+)\s*\|\s*([a-z]+)\s*\|\s*([a-z]+)\s*\|\s*([1-5])\s*$")
SIBLING = {"access", "usability", "playback", "downloads", "catalog", "billing", "support", "other"}


def verify_sample(run_dir, n=1000, budget=1.0, workers=2, planted_n=25):
    run_dir = Path(run_dir); db = connect(run_dir / "state" / "state.sqlite")
    rows = db.execute("SELECT t.text_hash, t.rep_id, t.text, r.result_json FROM texts t JOIN results r USING(text_hash)").fetchall()
    rows.sort(key=lambda r: sample_key("verify:" + r[1])); rows = rows[:n]  # declared rule: lowest SHA-256('verify:'+id)
    en = Enricher(run_dir, workers=workers, budget=budget, system=VERIFY_SYSTEM, model=VERIFY_MODEL, role="verify",
                  label_config="verify-p1+think-off", log_name="calls.jsonl", max_tokens=1200)
    out = []
    from concurrent.futures import ThreadPoolExecutor
    def work(chunk):
        items = [(rid, text) for _, rid, text, _ in chunk]
        try:
            raw, _ = en._call(items, 0)
        except Exception as e:
            return [(c, None, str(e)) for c in chunk]
        parsed = {}
        for line in raw.splitlines():
            m = VLINE.match(line)
            if m:
                parsed[int(m.group(1))] = (m.group(2), m.group(3), int(m.group(4)))
        return [(c, parsed.get(i), None if i in parsed else "unparsed") for i, c in enumerate(chunk, 1)]
    chunks = [rows[i:i + 50] for i in range(0, len(rows), 50)]
    with ThreadPoolExecutor(workers) as ex:
        for res in ex.map(work, chunks):
            out.extend(res)
    recs, dis = [], []
    for (th, rid, text, rj), v, err in out:
        e = json.loads(rj)
        if v is None:
            recs.append({"review_id": rid, "status": "verifier_failed", "error": err}); continue
        rec = {"review_id": rid, "enrich": {k: e[k] for k in ("topic", "intent", "severity")},
               "verify": {"topic": v[0], "intent": v[1], "severity": v[2]},
               "topic_agree": e["topic"] == v[0], "intent_agree": e["intent"] == v[1],
               "severity_exact": e["severity"] == v[2], "severity_abs_err": abs(e["severity"] - v[2]), "text": text[:300]}
        recs.append(rec)
        if not (rec["topic_agree"] and rec["intent_agree"] and rec["severity_abs_err"] <= 1):
            dis.append(rec)
    ok = [r for r in recs if "enrich" in r]
    rep = {"declared_sample_rule": "lowest SHA-256('verify:'+review_id) among completed original records", "sample_size": len(recs),
           "verifier_failed": len(recs) - len(ok),
           "topic_agreement": round(sum(r["topic_agree"] for r in ok) / max(1, len(ok)), 4),
           "intent_agreement": round(sum(r["intent_agree"] for r in ok) / max(1, len(ok)), 4),
           "severity_exact_agreement": round(sum(r["severity_exact"] for r in ok) / max(1, len(ok)), 4),
           "severity_mean_abs_diff": round(sum(r["severity_abs_err"] for r in ok) / max(1, len(ok)), 4),
           "disagreements_flagged": len(dis), "verifier_model": VERIFY_MODEL, "cost_usd": round(en.stats["cost_usd"], 5)}
    # planted-error test (SYNTHETIC; excluded from business aggregates): corrupt first labels, check the comparison flags them
    planted = []
    rot = {t: TOPICS[(TOPICS.index(t) + 3) % len(TOPICS)] for t in TOPICS}
    for r in ok[:planted_n]:
        bad = {"topic": rot[r["enrich"]["topic"]], "intent": "praise" if r["enrich"]["intent"] != "praise" else "complaint",
               "severity": min(5, r["enrich"]["severity"] + 3) if r["enrich"]["severity"] < 3 else 1}
        flagged = bad["topic"] != r["verify"]["topic"] or bad["intent"] != r["verify"]["intent"] or abs(bad["severity"] - r["verify"]["severity"]) > 1
        planted.append({"review_id": r["review_id"], "synthetic": True, "planted_wrong_label": bad, "verifier_label": r["verify"], "flagged": flagged})
    rep["planted_error_test"] = {"synthetic": True, "cases": len(planted), "flagged": sum(p["flagged"] for p in planted)}
    d = run_dir / "verify"; d.mkdir(exist_ok=True)
    (d / "verifier_predictions.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")
    (d / "planted_error_test.jsonl").write_text("\n".join(json.dumps(p) for p in planted) + "\n", encoding="utf-8")
    with open(d / "disagreements.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["review_id", "enrich_topic", "verify_topic", "enrich_intent", "verify_intent", "enrich_sev", "verify_sev", "text"])
        for r in dis:
            w.writerow([r["review_id"], r["enrich"]["topic"], r["verify"]["topic"], r["enrich"]["intent"], r["verify"]["intent"],
                        r["enrich"]["severity"], r["verify"]["severity"], r["text"].replace("\n", " ")])
    write_json(d / "verify_report.json", rep)
    return rep
