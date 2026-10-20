"""Stage 6 RECOMMEND: memo model sees only saved aggregates + bounded evidence pack; code checks claims and cited IDs."""
import csv, json, re, time
from pathlib import Path
import anthropic
from .common import *
from .prompts import MEMO_SYSTEM, MEMO_PLAIN_SYSTEM


def make_claims(ranking_csv, out_csv, top=8):
    rows = list(csv.DictReader(open(ranking_csv, encoding="utf-8"))); claims = []
    for r in rows[:top]:
        for m in ("complaint_count", "severity_sum", "mean_severity", "priority_score"):
            claims.append({"claim_id": f"C{len(claims) + 1:03d}", "issue_id": r["issue_id"], "metric": m, "value": r[m]})
    with open(out_csv, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["claim_id", "issue_id", "metric", "value"]); w.writeheader(); w.writerows(claims)
    return claims


def check_memo(text, claims, issues, pack_ids, payload_text):
    problems = []
    allowed = set(re.findall(r"\d+(?:\.\d+)?", payload_text))
    body = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", "", text)
    body = re.sub(r"\b[a-z]+\.[a-z]\d\b|\[C\d{3}\]", "", body)
    for num in sorted(set(re.findall(r"(?<![\w.])\d[\d,]*(?:\.\d+)?", body))):
        n = num.replace(",", "")
        if n not in allowed and n not in {"1", "2", "3", "4", "5", "6", "7", "8", "600"}:
            problems.append(f"number {num} not found in saved aggregates")
    cids = {c["claim_id"] for c in claims}
    for c in re.findall(r"\[(C\d{3})\]", text):
        if c not in cids:
            problems.append(f"unknown claim id {c}")
    for iid in re.findall(r"\b([a-z]+\.[a-z]\d)\b", text):
        if iid not in issues:
            problems.append(f"unknown issue id {iid}")
    for rid in re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text):
        if rid not in pack_ids:
            problems.append(f"review id not in evidence pack {rid}")
    return problems


def run_memo(run_dir, out_dir, log_path, client=None, top=8):
    load_env(); client = client or anthropic.Anthropic(max_retries=0, timeout=180)
    out_dir = Path(out_dir); issues = json.load(open(out_dir / "issues.json"))
    claims = make_claims(out_dir / "ranking.csv", out_dir / "claims.csv", top)
    ranking = list(csv.DictReader(open(out_dir / "ranking.csv", encoding="utf-8")))
    top_ids = [r["issue_id"] for r in ranking[:top]]
    by_topic = {}
    for r in ranking:
        t = r["issue_id"].split(".")[0]; d = by_topic.setdefault(t, {"complaint_count": 0, "severity_sum": 0}); d["complaint_count"] += int(r["complaint_count"]); d["severity_sum"] += int(r["severity_sum"])
    pack_ids = {i for iid in top_ids for i in issues[iid]["example_review_ids"]}
    cover = json.load(open(out_dir / "coverage.json"))
    payload = {"ranking_top": ranking[:top], "claims_table": claims, "topic_totals_computed_by_code": by_topic,
               "issues": {i: {k: issues[i][k] for k in ("default_name", "title", "summary", "example_review_ids")} for i in top_ids},
               "coverage": cover}
    log = JsonlLog(log_path); text = ""; problems = []
    for attempt in range(2):
        user = json.dumps(payload, ensure_ascii=False, indent=1) + ("\n\nFix these problems from your previous draft: " + "; ".join(problems) if problems else "")
        t0 = time.time()
        m = client.messages.create(model=MEMO_MODEL, max_tokens=1800, system=MEMO_SYSTEM, messages=[{"role": "user", "content": user}],
                                   thinking={"type": "between_tools"})
        text = "".join(b.text for b in m.content if b.type == "text"); u = m.usage
        usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
        problems = check_memo(text, claims, issues, pack_ids, json.dumps(payload))
        log.write({"request_id": m.id, "role": "memo", "review_ids": [], "model": MEMO_MODEL, "phase": "initial",
                   "outcome": "succeeded", "label_config": "memo-p1", "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                   "usage": usage, "cost_usd": round(cost_usd(MEMO_MODEL, usage), 6), "latency_s": round(time.time() - t0, 3),
                   "artifacts": ["ranking.csv", "claims.csv", "issues.json", "coverage.json"], "check_problems": problems, "ts": time.time()})
        if not problems:
            break
    (out_dir / "memo.md").write_text(text + ("\n\n<!-- UNRESOLVED CHECK PROBLEMS: " + "; ".join(problems) + " -->\n" if problems else "\n"), encoding="utf-8")
    return {"problems": problems}


def run_memo_plain(run_dir, out_dir, log_path, client=None, top=8):
    """Plain-language dashboard summary (model role `memo`), same saved aggregates and same code checks as the full memo."""
    load_env(); client = client or anthropic.Anthropic(max_retries=0, timeout=180)
    out_dir = Path(out_dir); issues = json.load(open(out_dir / "issues.json"))
    claims = list(csv.DictReader(open(out_dir / "claims.csv", encoding="utf-8")))
    ranking = list(csv.DictReader(open(out_dir / "ranking.csv", encoding="utf-8"))); top_ids = [r["issue_id"] for r in ranking[:top]]
    pack_ids = {i for iid in top_ids for i in issues[iid]["example_review_ids"]}
    payload = {"ranking_top": ranking[:top], "claims_table": claims,
               "issues": {i: {k: issues[i][k] for k in ("default_name", "title", "summary", "example_review_ids")} for i in top_ids},
               "topic_totals": list(csv.DictReader(open(Path(out_dir) / "topic_totals.csv"))), "coverage": json.load(open(out_dir / "coverage.json")),
               "full_memo": (out_dir / "memo.md").read_text(encoding="utf-8"),
               "display_means": {c["claim_id"]: f"{float(c['value']):.2f}" for c in claims if c["metric"] == "mean_severity"}}
    log = JsonlLog(log_path); problems = []; text = ""
    for attempt in range(2):
        user = json.dumps(payload, ensure_ascii=False) + ("\n\nFix these problems from your previous draft: " + "; ".join(problems) if problems else "")
        t0 = time.time()
        m = client.messages.create(model=MEMO_MODEL, max_tokens=1200, system=MEMO_PLAIN_SYSTEM, messages=[{"role": "user", "content": user}], thinking={"type": "between_tools"})
        text = "".join(b.text for b in m.content if b.type == "text"); u = m.usage; usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens}
        problems = [p for p in check_memo(text, claims, issues, pack_ids, json.dumps(payload)) if not p.startswith("number ")] + \
                   [p for p in check_memo(text, claims, issues, pack_ids, json.dumps(payload)) if p.startswith("number ")]
        log.write({"request_id": m.id, "role": "memo", "review_ids": [], "model": MEMO_MODEL, "phase": "initial", "outcome": "succeeded",
                   "label_config": "memo-plain-p1", "input_tokens": u.input_tokens, "output_tokens": u.output_tokens, "usage": usage,
                   "cost_usd": round(cost_usd(MEMO_MODEL, usage), 7), "rates_version": "official-2026-10-20", "latency_s": round(time.time() - t0, 3),
                   "artifacts": ["ranking.csv", "claims.csv", "issues.json", "coverage.json", "topic_totals.csv"], "check_problems": problems, "ts": time.time()})
        if not problems:
            break
    (out_dir / "memo_plain.md").write_text(text + ("\n\n<!-- UNRESOLVED CHECK PROBLEMS: " + "; ".join(problems) + " -->\n" if problems else "\n"), encoding="utf-8")
    return {"problems": problems}
