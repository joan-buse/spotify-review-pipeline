"""Load processed pipeline outputs into the dashboard database (Postgres in production, SQLite locally).
No model calls.   DATABASE_URL=postgresql://...  python3 web/scripts/load_db.py --run-dir runs/full --input spotify_reviews_18months.csv
Without DATABASE_URL a local SQLite file web/local.db is created."""
import argparse, csv, json, os, sys
from pathlib import Path
from sqlalchemy import create_engine, text
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "assignment"))
from check_submission import csv_rows  # noqa

DDL = [
 "DROP TABLE IF EXISTS reviews", "DROP TABLE IF EXISTS issues", "DROP TABLE IF EXISTS meta",
 """CREATE TABLE reviews(review_id TEXT PRIMARY KEY, month TEXT, rating INT, topic TEXT, subtopic TEXT, intent TEXT, severity INT,
    sentiment REAL, evidence_quote TEXT, needs_review BOOLEAN, cache_source_id TEXT)""",
 "CREATE INDEX reviews_issue ON reviews(topic, subtopic, intent, severity)",
 "CREATE INDEX reviews_month ON reviews(month, topic)",
 """CREATE TABLE issues(issue_id TEXT PRIMARY KEY, rank INT, topic TEXT, subtopic TEXT, title TEXT, summary TEXT, complaint_count INT,
    cancellation_count INT, severity_sum INT, mean_severity TEXT, priority_score INT, example_review_ids TEXT)""",
 "CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT)",
]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--run-dir", required=True); ap.add_argument("--input", required=True)
    ap.add_argument("--meta-only", action="store_true", help="reload issues/meta only; keep the reviews table"); ap.add_argument("--evals", default=str(ROOT / "evals")); ap.add_argument("--cost", default=str(ROOT / "cost"))
    a = ap.parse_args(); run = Path(a.run_dir)
    url = os.environ.get("DATABASE_URL") or f"sqlite:///{ROOT / 'web/local.db'}"
    if url.startswith("postgres://"): url = url.replace("postgres://", "postgresql+psycopg://", 1)
    elif url.startswith("postgresql://"): url = url.replace("postgresql://", "postgresql+psycopg://", 1)
    eng = create_engine(url)
    src = {} if a.meta_only else {r["review_id"]: (r["review_timestamp"][:7], int(r["review_rating"])) for r in csv_rows(a.input)}
    with eng.begin() as c:
        for s in DDL:
            if a.meta_only and ("reviews" in s): continue
            c.execute(text(s))
        rows_iter = []
        for line in ([] if a.meta_only else open(run / "records.jsonl", encoding="utf-8")):
            r = json.loads(line)
            if r["status"] != "completed": continue
            m, rating = src[r["review_id"]]
            rows_iter.append((r["review_id"], m, rating, r["topic"], r["subtopic"], r["intent"], r["severity"], r["sentiment"], r["evidence_quote"], bool(r["needs_review"]), r.get("cache_source_id")))
        if a.meta_only: pass
        elif eng.dialect.name == "postgresql":  # bulk COPY: one round trip instead of one per row
            with c.connection.cursor() as cur, cur.copy("COPY reviews FROM STDIN") as cp:
                for row in rows_iter: cp.write_row(row)
        else:
            c.execute(text("INSERT INTO reviews VALUES(:i,:m,:r,:t,:s,:n,:v,:se,:q,:nr,:c)"),
                      [dict(zip("i m r t s n v se q nr c".split(), row)) for row in rows_iter])
        batch = rows_iter
        issues = json.load(open(run / "issues.json")); rows = []
        for r in csv.DictReader(open(run / "aggregates.csv")):
            i = issues.get(r["issue_id"], {})
            rows.append({"id": r["issue_id"], "rk": int(r["rank"]), "t": r["issue_id"].split(".")[0], "s": r["issue_id"].split(".")[1],
                         "ti": i.get("title", ""), "su": i.get("summary", ""), "cc": int(r["complaint_count"]), "ca": int(r["cancellation_count"]),
                         "ss": int(r["severity_sum"]), "ms": r["mean_severity"], "ps": int(r["priority_score"]), "ex": json.dumps(i.get("example_review_ids", []))})
        c.execute(text("INSERT INTO issues VALUES(:id,:rk,:t,:s,:ti,:su,:cc,:ca,:ss,:ms,:ps,:ex)"), rows)
        def put(k, v): c.execute(text("INSERT INTO meta VALUES(:k,:v)"), {"k": k, "v": json.dumps(v)})
        summ = json.load(open(run / "run_summary.json")); put("run_summary", summ)
        put("coverage", json.load(open(run / "coverage.json")))
        put("memo_markdown", (run / "memo.md").read_text(encoding="utf-8"))
        put("claims", list(csv.DictReader(open(run / "claims.csv"))))
        put("ingestion", json.load(open(run / "ingestion_report.json")))
        for name, path in (("golden_eval", Path(a.evals) / "golden_eval_report.json"), ("verify_report", run / "verify/verify_report.json"),
                           ("system_tests", Path(a.evals) / "system_tests.json")):
            if path.exists(): put(name, json.load(open(path)))
        spend = 0.0
        for line in open(run / "calls.jsonl"):
            d = json.loads(line); spend += d.get("cost_usd", 0.0)
        put("run_spend_usd", round(spend, 4))
        print("loaded", len(batch), "last batch;", c.execute(text("SELECT count(*) FROM reviews")).scalar(), "reviews;", len(rows), "issues")


if __name__ == "__main__":
    main()
