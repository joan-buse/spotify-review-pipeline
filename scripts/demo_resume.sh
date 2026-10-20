#!/usr/bin/env bash
# PAID demo (~$0.15): interruption + resume on a fresh 1,000-review input. Record your screen while running it.
# Usage: ANTHROPIC_API_KEY in .env, then:  bash scripts/demo_resume.sh path/to/spotify_reviews_18months.csv
set -euo pipefail
CSV="${1:?path to input csv}"; RUN=runs/demo_resume; rm -rf "$RUN"
echo "== 1. start run, stop after 8 saved batches (simulated interruption) =="
python -m pipeline.run --input "$CSV" --run-dir "$RUN" --target-rows 1000 --budget 1 --max-batches 8 --skip-model-downstream --snapshot "$RUN/checkpoint_before.json" | tail -5
echo "saved IDs before interruption: $(python -c "import json;print(json.load(open('$RUN/checkpoint_before.json'))['count'])")"
echo "== 2. resume: only pending IDs are sent; completed IDs are NOT relabeled =="
python -m pipeline.run --input "$CSV" --run-dir "$RUN" --target-rows 1000 --budget 1 --skip-model-downstream --snapshot "$RUN/checkpoint_after.json" | tail -5
python - <<PY
import json
b=set(json.load(open("$RUN/checkpoint_before.json"))["completed_ids"]); a=set(json.load(open("$RUN/checkpoint_after.json"))["completed_ids"])
calls=[json.loads(l) for l in open("$RUN/calls.jsonl")]
resent=[c for c in calls if c["phase"]=="resume" and b & set(c["review_ids"])]
print(f"before={len(b)} after={len(a)} new={len(a-b)} resume-calls re-sending already-completed IDs: {len(resent)}")
PY
echo "(real Ctrl-C also works: SIGINT stops admitting work, in-flight batches finish and are saved)"
