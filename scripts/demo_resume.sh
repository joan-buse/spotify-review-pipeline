#!/usr/bin/env bash
# PAID demo (~$0.15): interruption + resume on a fresh 1,000-review input. Record your screen while running it.
# Usage: ANTHROPIC_API_KEY in .env, then:  bash scripts/demo_resume.sh path/to/spotify_reviews_18months.csv
set -euo pipefail
CSV="${1:?path to input csv}"; RUN=runs/demo_resume; rm -rf "$RUN"
summarize() { python - <<PY
import json
s=json.load(open("$RUN/run_summary.json")); e=s["enrich_stats"]
print(f"   invocation $1: phase={e['phase']}  API requests this invocation={e['requests']}  texts newly classified={e['completed_texts']}  cost=\${e['cost_usd']:.4f}")
print(f"   records now: completed={s['records']['completed']}  still pending (quarantined as pending_not_attempted)={s['records']['quarantined'].get('pending_not_attempted',0)}")
PY
}
echo "== 1. start run, stop after 8 saved batches (simulated interruption) =="
python -m pipeline.run --input "$CSV" --run-dir "$RUN" --target-rows 1000 --budget 1 --max-batches 8 --skip-model-downstream --snapshot "$RUN/checkpoint_before.json" > "$RUN.log1" 2>&1; summarize 1
echo "saved IDs before interruption: $(python -c "import json;print(json.load(open('$RUN/checkpoint_before.json'))['count'])")"
echo "== 2. resume: only pending IDs are sent; completed IDs are NOT relabeled =="
python -m pipeline.run --input "$CSV" --run-dir "$RUN" --target-rows 1000 --budget 1 --skip-model-downstream --snapshot "$RUN/checkpoint_after.json" > "$RUN.log2" 2>&1; summarize 2
python - <<PY
import json
b=set(json.load(open("$RUN/checkpoint_before.json"))["completed_ids"]); a=set(json.load(open("$RUN/checkpoint_after.json"))["completed_ids"])
calls=[json.loads(l) for l in open("$RUN/calls.jsonl")]
resent=[c for c in calls if c["phase"]=="resume" and b & set(c["review_ids"])]
print(f"before={len(b)} after={len(a)} new={len(a-b)} resume-calls re-sending already-completed IDs: {len(resent)}")
PY
echo "(real Ctrl-C also works: SIGINT stops admitting work, in-flight batches finish and are saved)"
