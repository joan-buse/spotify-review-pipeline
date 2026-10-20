"""Compare saved model predictions with the human-reviewed golden-50 labels. Offline; no model calls.
Predeclared sentiment tolerance: |pred - human| <= 0.5.  Golden labels were never used in prompts, examples or routing."""
import csv, json, sys
from collections import Counter, defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
run = Path(sys.argv[1] if len(sys.argv) > 1 else ROOT / "runs_evidence/golden")
gold = {r["review_id"]: r for r in csv.DictReader(open(ROOT / "evals/golden_50_labeled.csv", encoding="utf-8"))}
pred = {}
for line in open(run / "records.jsonl", encoding="utf-8"):
    r = json.loads(line)
    if r["review_id"] in gold:
        pred[r["review_id"]] = r
rows, conf_t, conf_i = [], Counter(), Counter()
per_topic = defaultdict(lambda: [0, 0])
for rid, g in gold.items():
    p = pred.get(rid); ok = p and p["status"] == "completed"
    rec = {"review_id": rid, "text": g["review_text"][:120].replace("\n", " "), "human_topic": g["topic"], "human_intent": g["intent"],
           "human_severity": int(g["severity"]), "human_sentiment": float(g["sentiment"]), "human_ambiguous": g["needs_review"] == "true"}
    if ok:
        rec.update(pred_topic=p["topic"], pred_intent=p["intent"], pred_severity=p["severity"], pred_sentiment=p["sentiment"],
                   pred_needs_review=p["needs_review"], quote_is_substring=p["evidence_quote"] in g["review_text"],
                   quote_text=p["evidence_quote"][:120], pred_entities=p["entities"])
        rec["topic_ok"] = p["topic"] == g["topic"]; rec["intent_ok"] = p["intent"] == g["intent"]
        rec["sev_exact"] = p["severity"] == int(g["severity"]); rec["sev_err"] = abs(p["severity"] - int(g["severity"]))
        rec["sent_err"] = abs(p["sentiment"] - float(g["sentiment"])); rec["sent_ok"] = rec["sent_err"] <= 0.5
        conf_t[(g["topic"], p["topic"])] += 1; conf_i[(g["intent"], p["intent"])] += 1
        per_topic[g["topic"]][0] += 1; per_topic[g["topic"]][1] += rec["topic_ok"]
    else:  # missing/quarantined predictions count as incorrect
        rec.update(topic_ok=False, intent_ok=False, sev_exact=False, sev_err=4, sent_ok=False, sent_err=2.0, quote_is_substring=False)
    rows.append(rec)
n = len(rows); amb = [r for r in rows if r["human_ambiguous"]]
def f(x): return round(x / n, 4)
def macro_f1(key_h, key_p):
    labels = sorted({r[key_h] for r in rows}); fs = []
    for L in labels:
        tp = sum(1 for r in rows if r[key_h] == L and r.get(key_p) == L); fp = sum(1 for r in rows if r[key_h] != L and r.get(key_p) == L)
        fn = sum(1 for r in rows if r[key_h] == L and r.get(key_p) != L)
        fs.append(0 if tp == 0 else 2 * tp / (2 * tp + fp + fn))
    return round(sum(fs) / len(fs), 4)
rep = {"n": n, "topic_agreement": f(sum(r["topic_ok"] for r in rows)), "intent_agreement": f(sum(r["intent_ok"] for r in rows)),
       "severity_exact": f(sum(r["sev_exact"] for r in rows)), "severity_mae": round(sum(r["sev_err"] for r in rows) / n, 3),
       "sentiment_mae": round(sum(r["sent_err"] for r in rows) / n, 3), "sentiment_within_0.5": f(sum(r["sent_ok"] for r in rows)),
       "topic_macro_f1": macro_f1("human_topic", "pred_topic"), "intent_macro_f1": macro_f1("human_intent", "pred_intent"),
       "quote_exact_substring": f(sum(bool(r["quote_is_substring"]) for r in rows)),
       "ambiguous_cases_human_flagged": len(amb), "topic_agreement_on_nonambiguous": round(sum(r["topic_ok"] for r in rows if not r["human_ambiguous"]) / max(1, n - len(amb)), 4),
       "needs_review_as_prediction": {"model_flagged": sum(bool(r.get("pred_needs_review")) for r in rows),
            "flagged_and_human_ambiguous": sum(bool(r.get("pred_needs_review")) and r["human_ambiguous"] for r in rows),
            "human_ambiguous_total": len(amb), "disagreements_with_nr_flag": sum(1 for r in rows if not (r["topic_ok"] and r["intent_ok"]) and r.get("pred_needs_review"))},
       "per_topic_counts_and_topic_accuracy": {t: {"n": v[0], "correct": v[1]} for t, v in sorted(per_topic.items())},
       "topic_confusion": {f"{a}->{b}": c for (a, b), c in sorted(conf_t.items()) if a != b},
       "intent_confusion": {f"{a}->{b}": c for (a, b), c in sorted(conf_i.items()) if a != b}}
out = ROOT / "evals"; (out / "golden_eval_report.json").write_text(json.dumps(rep, indent=2) + "\n")
with open(out / "golden_predictions_vs_labels.csv", "w", newline="", encoding="utf-8") as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) + [k for k in rows[0] if False]); keys = sorted({k for r in rows for k in r}, key=lambda k: list(rows[0]).index(k) if k in rows[0] else 99)
    w = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore"); w.writeheader(); w.writerows(rows)
bad = [r for r in rows if not (r["topic_ok"] and r["intent_ok"] and r["sev_err"] <= 1)]
(out / "golden_disagreements.json").write_text(json.dumps(bad, indent=1, ensure_ascii=False) + "\n")
print(json.dumps(rep, indent=1)); print(f"{len(bad)} disagreement rows saved")
