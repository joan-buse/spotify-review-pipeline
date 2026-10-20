"""Stage 5 RANK (code only): issue membership + baseline priority = severity_sum. Reproducible offline from saved records."""
import argparse, csv, gzip, json
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from .common import *


def issue_id(rec):
    return f"{rec['topic']}.{rec['subtopic']}"


def mean_str(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def rank(records_path, out_dir):
    out_dir = Path(out_dir); out_dir.mkdir(parents=True, exist_ok=True)
    members = defaultdict(list)
    opener = gzip.open if str(records_path).endswith(".gz") else open
    for line in opener(records_path, "rt", encoding="utf-8"):
        r = json.loads(line)
        if r["status"] == "completed" and r["intent"] in ("complaint", "cancellation"):
            members[issue_id(r)].append((r["review_id"], r["severity"], r["intent"]))
    rows = []
    for iid, m in members.items():
        s = sum(x[1] for x in m)
        rows.append({"issue_id": iid, "complaint_count": len(m), "severity_sum": s, "mean_severity": mean_str(s, len(m)),
                     "priority_score": s, "cancellation_count": sum(1 for x in m if x[2] == "cancellation")})
    rows.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i
    with open(out_dir / "ranking.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"])
        for r in rows:
            w.writerow([r["rank"], r["issue_id"], r["complaint_count"], r["severity_sum"], r["mean_severity"], r["priority_score"]])
    with open(out_dir / "membership.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["issue_id", "review_id"])
        for iid in sorted(members):
            for rid, _, _ in sorted(members[iid]):
                w.writerow([iid, rid])
    with open(out_dir / "aggregates.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["rank", "issue_id", "complaint_count", "cancellation_count", "severity_sum", "mean_severity", "priority_score"])
        for r in rows:
            w.writerow([r["rank"], r["issue_id"], r["complaint_count"], r["cancellation_count"], r["severity_sum"], r["mean_severity"], r["priority_score"]])
    return rows


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Regenerate ranking from saved records (no model calls).")
    ap.add_argument("--records", required=True); ap.add_argument("--out", required=True)
    a = ap.parse_args()
    for r in rank(a.records, a.out)[:10]:
        print(r)
