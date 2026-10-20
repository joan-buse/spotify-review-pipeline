"""Stage 4 GROUP: issue buckets are code-assigned (topic.subtopic); a small model names each from a bounded evidence pack."""
import json, time
from pathlib import Path
from collections import defaultdict
import anthropic
from .common import *
from .prompts import GROUP_SYSTEM
from .rank import issue_id


PACK_STATS = {}


def evidence_pack(records_path, per_issue=12):
    texts_by_issue = defaultdict(list)
    for line in open(records_path, encoding="utf-8"):
        r = json.loads(line)
        if r["status"] == "completed" and r["intent"] in ("complaint", "cancellation") and "cache_source_id" not in r:
            texts_by_issue[issue_id(r)].append(r)
    pack = {}
    for iid, recs in texts_by_issue.items():
        by_hash = sorted(recs, key=lambda r: sample_key(r["review_id"]))  # deterministic pseudo-random = representative of the bucket
        severe = sorted(recs, key=lambda r: (-r["severity"], sample_key(r["review_id"])))[:4]
        chosen = {r["review_id"]: r for r in by_hash[:per_issue - 4] + severe}
        sev_dist = {}
        for r in recs: sev_dist[r["severity"]] = sev_dist.get(r["severity"], 0) + 1
        pack[iid] = [{"review_id": r["review_id"], "severity": r["severity"], "quote": r["evidence_quote"][:240]} for r in chosen.values()]
        PACK_STATS[iid] = {"members_distinct_texts": len(recs), "severity_distribution": dict(sorted(sev_dist.items()))}
    return pack


def run_group(records_path, out_dir, log_path, client=None):
    load_env(); client = client or anthropic.Anthropic(max_retries=0, timeout=90)
    log = JsonlLog(log_path); out_dir = Path(out_dir)
    pack = evidence_pack(records_path); issues = {}
    for iid, ex in sorted(pack.items()):
        topic, sub = iid.split(".")
        user = json.dumps({"issue_id": iid, "topic": topic, "definition": SUBTOPICS[topic][sub], "bucket_stats": PACK_STATS.get(iid), "examples": ex}, ensure_ascii=False)
        t0 = time.time(); title, summary, ids = SUBTOPICS[topic][sub], "", [e["review_id"] for e in ex[:3]]
        try:
            m = client.messages.create(model=GROUP_MODEL, max_tokens=400, thinking={"type": "disabled"}, system=GROUP_SYSTEM,
                                       messages=[{"role": "user", "content": user}])
            txt = "".join(b.text for b in m.content if b.type == "text"); obj = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
            valid = [i for i in obj.get("example_ids", []) if i in {e["review_id"] for e in ex}]
            title, summary, ids = str(obj["title"])[:80], str(obj.get("summary", ""))[:240], valid or ids
            u = m.usage; usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
            log.write({"request_id": m.id, "role": "group", "review_ids": [e["review_id"] for e in ex], "model": GROUP_MODEL,
                       "phase": "initial", "outcome": "succeeded", "label_config": "group-p1", "input_tokens": u.input_tokens,
                       "output_tokens": u.output_tokens, "usage": usage, "cost_usd": round(cost_usd(GROUP_MODEL, usage), 6),
                       "latency_s": round(time.time() - t0, 3), "ts": time.time()})
        except Exception as e:
            log.write({"request_id": f"local-group-{iid}-{time.time_ns()}", "role": "group", "review_ids": [x["review_id"] for x in ex],
                       "model": GROUP_MODEL, "phase": "initial", "outcome": "failed", "label_config": "group-p1",
                       "input_tokens": 0, "output_tokens": 0, "error": str(e)[:200], "ts": time.time()})
        issues[iid] = {"issue_id": iid, "topic": topic, "subtopic": sub, "default_name": SUBTOPICS[topic][sub],
                       "title": title, "summary": summary, "example_review_ids": ids}
    write_json(out_dir / "issues.json", issues)
    return issues
