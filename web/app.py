"""Flask backend: serves saved pipeline results from the database (no model calls, no secrets in the browser)."""
import json, os
from pathlib import Path
from flask import Flask, jsonify, request, send_from_directory
from sqlalchemy import create_engine, text

PUBLIC = Path(__file__).resolve().parent / "public"
app = Flask(__name__)
_url = os.environ.get("DATABASE_URL") or f"sqlite:///{Path(__file__).resolve().parent / 'local.db'}"
if _url.startswith("postgres://"): _url = _url.replace("postgres://", "postgresql+psycopg://", 1)
elif _url.startswith("postgresql://"): _url = _url.replace("postgresql://", "postgresql+psycopg://", 1)
engine = create_engine(_url, pool_pre_ping=True)


def rows(sql, **p):
    with engine.connect() as c:
        return [dict(r._mapping) for r in c.execute(text(sql), p)]


def meta(key):
    r = rows("SELECT value FROM meta WHERE key=:k", k=key)
    return json.loads(r[0]["value"]) if r else None


@app.get("/api/health")
def health():
    return jsonify(ok=True, reviews=rows("SELECT count(*) AS n FROM reviews")[0]["n"])


@app.get("/api/overview")
def overview():
    cov, ing = meta("coverage"), meta("ingestion")
    by_topic = rows("SELECT topic, count(*) AS n, sum(CASE WHEN intent IN ('complaint','cancellation') THEN 1 ELSE 0 END) AS complaints, avg(severity) AS mean_severity FROM reviews GROUP BY topic ORDER BY n DESC")
    by_intent = rows("SELECT intent, count(*) AS n FROM reviews GROUP BY intent ORDER BY n DESC")
    by_sev = rows("SELECT severity, count(*) AS n FROM reviews GROUP BY severity ORDER BY severity")
    classified = sum(r["n"] for r in by_intent)
    return jsonify(source_rows=ing["rows"], nonempty_rows=ing["nonempty_rows"], empty_rows=ing["empty_text_rows"],
                   distinct_nonempty_texts=ing["distinct_nonempty_texts"], classified=classified, coverage=cov,
                   cache_reuse=cov.get("exact_text_cache_reuse_records"), by_topic=by_topic, by_intent=by_intent, by_severity=by_sev,
                   spend_usd=meta("run_spend_usd"), golden=meta("golden_eval"), verify=meta("verify_report"),
                   needs_review=rows("SELECT count(*) AS n FROM reviews WHERE needs_review")[0]["n"])


@app.get("/api/ranking")
def ranking():
    return jsonify(rows("SELECT * FROM issues ORDER BY rank"))


@app.get("/api/issues/<issue_id>")
def issue(issue_id):
    topic, sub = issue_id.split(".")
    meta_row = rows("SELECT * FROM issues WHERE issue_id=:i", i=issue_id)
    ev = rows("""SELECT review_id, month, severity, intent, evidence_quote, rating FROM reviews WHERE topic=:t AND subtopic=:s
                 AND intent IN ('complaint','cancellation') ORDER BY severity DESC, review_id LIMIT 15""", t=topic, s=sub)
    return jsonify(issue=meta_row[0] if meta_row else None, evidence=ev)


@app.get("/api/trend")
def trend():
    return jsonify(rows("""SELECT month, topic, count(*) AS n, sum(CASE WHEN intent IN ('complaint','cancellation') THEN 1 ELSE 0 END) AS complaints
                           FROM reviews GROUP BY month, topic ORDER BY month"""),)


@app.get("/api/recommendation")
def recommendation():
    return jsonify(memo=meta("memo_markdown"), claims=meta("claims"))


@app.get("/api/review/<rid>")
def review(rid):
    r = rows("SELECT * FROM reviews WHERE review_id=:i", i=rid)
    return jsonify(r[0] if r else None), (200 if r else 404)


@app.get("/")
def index():
    return send_from_directory(PUBLIC, "index.html")


if __name__ == "__main__":
    app.run(port=int(os.environ.get("PORT", 5057)), debug=False)
