#!/usr/bin/env python3
"""100-review cost & runtime calculator.

  python3 cost/calculator.py replay            # DEFAULT, offline: recompute everything from saved usage + editable rates (no API key, no calls)
  python3 cost/calculator.py replay --rate-multiplier 2 --budget 15 --workers 4
  python3 cost/calculator.py pilot --confirm-paid   # EXPLICIT paid command: runs real cold + warm 100-review pilot (needs ANTHROPIC_API_KEY)

Importing this module never makes a paid call.  Cost = billed_units x price_per_unit, per mutually exclusive item.
"""
import argparse, csv, json, subprocess, sys, shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent; ROOT = HERE.parent
FULL = {"source_rows": 660622, "nonempty": 660609, "empty_quarantine": 13, "distinct_nonempty_texts": 484189}


def read_rates(path=HERE / "rates.csv"):
    return {(r["model"], r["item"]): float(r["usd_per_million_tokens"]) for r in csv.DictReader(open(path))}


def item_costs(usage_rows, rates, mult=1.0):
    """Mutually exclusive items: uncached input, cache read, cache write, output (reasoning already inside output)."""
    out = []
    for u in usage_rows:
        m = u["model"]
        items = {"input": int(u["input_tokens_uncached"]), "cache_read": int(u["cache_read_tokens"]),
                 "cache_write": int(u["cache_write_tokens"]), "output": int(u["output_tokens"])}
        out.append({**u, "cost_usd": sum(n * rates[(m, k)] * mult / 1e6 for k, n in items.items())})
    return out


def stage_table(costed, run):
    by = {}
    for r in costed:
        if r["run_id"] != run:
            continue
        s = by.setdefault(r["role"], {"requests": 0, "failed": 0, "in": 0, "out": 0, "cost": 0.0, "reviews": 0, "model": r["model"]})
        s["requests"] += 1; s["failed"] += r["outcome"] == "failed"
        s["in"] += int(r["input_tokens_uncached"]) + int(r["cache_read_tokens"]) + int(r["cache_write_tokens"])
        s["out"] += int(r["output_tokens"]); s["cost"] += r["cost_usd"]; s["reviews"] += int(r["n_review_ids"])
    return by


def project(measured, scen, rates, mult, reuse=True):
    """Extrapolate each stage by its own work count and measured per-unit usage; one-time overhead counted once."""
    n_texts_pilot = measured["pilot_texts"]
    e = measured["stages"]["enrich"]
    texts = FULL["distinct_nonempty_texts"] if reuse else FULL["nonempty"]
    if scen.get("target_rows"):  # distinct fraction of a hash-ordered 100k-row sample, measured in the real run (78,100 / 100,000)
        texts = scen["target_rows"] * (scen["sample_distinct_fraction"] if reuse else 1)
    ipt, opt = e["in"] / n_texts_pilot, min(e["out"] / n_texts_pilot, scen["output_token_cap_per_review"])
    r = rates[(e["model"], "input")] * mult / 1e6, rates[(e["model"], "output")] * mult / 1e6
    base_enrich = texts * (ipt * r[0] + opt * r[1])
    retry = scen["retry_rate"]; enrich = base_enrich * (1 + retry)
    v = measured["stages"].get("verify", {"in": 0, "out": 0, "reviews": 1, "model": e["model"]})
    vn = scen["verify_sample"]; vcost = vn * ((v["in"] / max(1, v["reviews"])) * rates[(v["model"], "input")] + (v["out"] / max(1, v["reviews"])) * rates[(v["model"], "output")]) * mult / 1e6
    fb_model = scen["fallback_model"]; fb_n = texts * scen["fallback_fraction"]
    fcost = fb_n * (ipt * rates[(fb_model, "input")] + opt * 2 * rates[(fb_model, "output")]) * mult / 1e6  # fallback assumed 2x output (assumption)
    g, mm = measured["stages"].get("group", {"cost": 0}), measured["stages"].get("memo", {"cost": 0})
    group_cost = measured["fixed_group_cost_per_rate"] * mult * scen["group_scale"]; memo_cost = measured["fixed_memo_cost_per_rate"] * mult
    total = enrich + vcost + fcost + group_cost + memo_cost
    thr = measured["throughput_texts_per_s_1worker"]
    workers = min(scen["max_workers"], scen["rate_limit_cap_workers"])
    speed = thr * (1 + (workers - 1) * scen["parallel_efficiency"])
    secs = texts / speed * (1 + retry) + measured["fixed_stage_seconds"]
    return {"texts": texts, "enrich": enrich, "verify": vcost, "fallback": fcost, "group": group_cost, "memo": memo_cost, "total": total,
            "hours": secs / 3600, "workers": workers, "exceeds_budget": total > scen["budget_usd"]}


def measured_from(costed, summary_cold, summary_warm):
    st = stage_table(costed, "cold"); sw = stage_table(costed, "warm")
    enrich_requests = [r for r in costed if r["run_id"] == "cold" and r["role"] == "enrich" and r["outcome"] == "succeeded"]
    pilot_texts = sum(int(r["n_review_ids"]) for r in enrich_requests)
    enrich_sec = summary_cold["stage_seconds"].get("enrich", 1)
    return {"stages": st, "warm_stages": sw, "pilot_texts": pilot_texts, "throughput_texts_per_s_1worker": pilot_texts / enrich_sec,
            "fixed_group_cost_per_rate": st.get("group", {"cost": 0})["cost"], "fixed_memo_cost_per_rate": st.get("memo", {"cost": 0})["cost"],
            "fixed_stage_seconds": sum(v for k, v in summary_cold["stage_seconds"].items() if k in ("group", "memo")),
            "cold_wall_s": summary_cold["total_wall_clock_s"], "warm_wall_s": summary_warm["total_wall_clock_s"],
            "cold_stage_s": summary_cold["stage_seconds"], "warm_stage_s": summary_warm["stage_seconds"]}


def replay(a):
    rates = read_rates(a.rates); scen = json.load(open(a.scenario))
    if a.budget is not None: scen["budget_usd"] = a.budget
    if a.workers is not None: scen["max_workers"] = a.workers
    if a.fallback_fraction is not None: scen["fallback_fraction"] = a.fallback_fraction
    if a.output_cap is not None: scen["output_token_cap_per_review"] = a.output_cap
    usage = list(csv.DictReader(open(HERE / "usage.csv")))
    costed = item_costs(usage, rates, a.rate_multiplier)
    sc, sw = json.load(open(HERE / "summary_cold.json")), json.load(open(HERE / "summary_warm.json"))
    m = measured_from(costed, sc, sw)
    cold_total = sum(r["cost_usd"] for r in costed if r["run_id"] == "cold"); warm_total = sum(r["cost_usd"] for r in costed if r["run_id"] == "warm")
    warm_enrich = sum(1 for r in costed if r["run_id"] == "warm" and r["role"] == "enrich")
    n = m["pilot_texts"]; lines = []
    P = lines.append
    P("# 100-review cost & runtime report (offline replay)\n")
    P(f"Rates file: `cost/rates.csv` (editable). Rate multiplier: x{a.rate_multiplier}. Usage file: `cost/usage.csv` (raw provider usage).")
    P("\n## Measured: real 100-review pilot (API spend only)\n")
    P("| stage | model | requests | failed | reviews sent | input tok | output tok | cost USD |"); P("|---|---|---|---|---|---|---|---|")
    for k, s in m["stages"].items():
        P(f"| {k} | {s['model']} | {s['requests']} | {s['failed']} | {s['reviews']} | {s['in']} | {s['out']} | {s['cost']:.6f} |")
    P(f"\n**Cold run total API cost: ${cold_total:.6f}**  ·  cost per 1,000 input rows: ${cold_total / 100 * 1000:.4f}  ·  per completed record: ${cold_total / max(1, n):.6f}")
    P(f"\n**Warm run (saved result cache): {warm_enrich} enrichment calls; incremental API cost ${warm_total:.6f}** (downstream stages cached by input fingerprint).")
    P(f"\nWall-clock (not summed call time): cold {m['cold_wall_s']:.1f}s, warm {m['warm_wall_s']:.1f}s. Stage seconds cold: {m['cold_stage_s']}; warm: {m['warm_stage_s']}.")
    P(f"Enrichment throughput (1 worker): {m['throughput_texts_per_s_1worker']:.2f} texts/s.")
    P("\n### Configuration by stage (pilot)\n")
    P("| stage | provider | model | effort/thinking | prompt/schema version | batch size | workers | unique texts | result-cache hits (cold / warm) |"); P("|---|---|---|---|---|---|---|---|---|")
    cfg = {"enrich": ("claude-haiku-5-5", "thinking disabled", "enrich-p1 / schema-v1", "<=50 reviews/request (2 requests)", "1"),
           "verify": ("claude-haiku-5-5", "thinking disabled", "verify-p1", "<=50 (1 request, 20-review declared sample)", "1"),
           "group": ("claude-haiku-5-5", "thinking disabled", "group-p1", "1 issue bucket per request", "1"),
           "memo": ("claude-sonnet-5-5", "no thinking between tools (provider default for this model)", "memo-p1", "1 request (+1 regeneration if checks fail)", "1")}
    for k, (mod, eff, ver, bs, w) in cfg.items():
        hits = f"0 / {n}" if k == "enrich" else "n/a (downstream cached by input fingerprint: 0 calls warm)"
        P(f"| {k} | Anthropic | {mod} | {eff} | {ver} | {bs} | {w} | {n if k == 'enrich' else '-'} | {hits} |")
    P("\nLocal compute / electricity / hardware: **unknown, not measured** (orchestration is CPU-light; not included in API spend).")
    P("\n## Projection: full corpus\n")
    P(f"Source rows {FULL['source_rows']:,} = {FULL['nonempty']:,} nonempty to classify + {FULL['empty_quarantine']} empty quarantines. Distinct nonempty texts with exact-text reuse: {FULL['distinct_nonempty_texts']:,}.")
    P(f"Controls: budget ${scen['budget_usd']}, output cap {scen['output_token_cap_per_review']} tok/review, max workers {scen['max_workers']}, fallback fraction {scen['fallback_fraction']} ({scen['fallback_model']}).\n")
    P("| scenario | texts classified | enrich | verify | fallback | group | memo | **total USD** | hours | workers | vs budget |"); P("|---|---|---|---|---|---|---|---|---|---|---|")
    for name, ov, reuse in (("base, exact-text reuse", {}, True), ("base, NO reuse", {}, False),
                            ("conservative (more retries/fallback, slower), reuse", scen["conservative"], True),
                            ("base, reuse, target_rows=100,000 (this project's run)", {"target_rows": 100000}, True)):
        sc2 = {**scen, **ov}; p = project(m, sc2, rates, a.rate_multiplier, reuse)
        P(f"| {name} | {p['texts']:,.0f} | {p['enrich']:.2f} | {p['verify']:.2f} | {p['fallback']:.2f} | {p['group']:.2f} | {p['memo']:.2f} | **{p['total']:.2f}** | {p['hours']:.1f} | {p['workers']} | {'**EXCEEDS BUDGET**' if p['exceeds_budget'] else 'within'} |")
    ms = HERE / "measurements" / "summary.json"
    if ms.exists():
        P("\n## Measurement refresh at 100 -> 500 -> 10,000 -> actual runs incl. Message Batches (enrichment, from saved call logs)\n")
        P("| checkpoint | reviews sent | input tok/review | output tok/review | failed API attempts | enrich cost USD at current rates | USD per 1,000 reviews |"); P("|---|---|---|---|---|---|---|")
        for k, v in json.load(open(ms)).items():
            disc = rates[("claude-haiku-5-5", "batch_multiplier")] if v.get("tier") == "batch" else 1.0
            e = disc * (rates[("claude-haiku-5-5", "input")] * v["input_tokens"] + rates[("claude-haiku-5-5", "output")] * v["output_tokens"]) * a.rate_multiplier / 1e6
            P(f"| {k} | {v['reviews_sent']:,} | {v['input_tokens_per_review']} | {v['output_tokens_per_review']} | {v['enrich_failed_attempts']} | {e:.4f} | {1000 * e / max(1, v['reviews_sent']):.4f} |")
        P("\nPer-review usage is stable from 100 to 78,631 reviews, so the 100-review estimate extrapolates well for this prompt; rare failure modes (provider refusals on ~0.1% of texts) only appeared at scale and are handled by bounded bisection.")
    P("\nAssumptions are in `cost/scenario.json` (retry rate, fallback fraction, parallel efficiency, verify sample size). The first 100 reviews are an initial estimate; see the measurement-refresh table above (`cost/measurements/summary.json`).")
    text = "\n".join(lines) + "\n"
    (HERE / "report.md").write_text(text) if a.write else None
    print(text)


def pilot(a):
    if not a.confirm_paid:
        sys.exit("Refusing: pilot makes PAID API calls. Re-run with --confirm-paid (needs ANTHROPIC_API_KEY).")
    run = ROOT / "runs" / "pilot_cold"; shutil.rmtree(run, ignore_errors=True)
    base = [sys.executable, "-m", "pipeline.run", "--input", str(ROOT / "assignment/cost_100.csv"), "--run-dir", str(run),
            "--budget", "0.5", "--workers", "1", "--verify-n", "20"]
    subprocess.run(base, cwd=ROOT, check=True)
    shutil.copy(run / "run_summary.json", HERE / "summary_cold.json")
    calls_cold = (run / "calls.jsonl").read_text().splitlines(); n_cold = len(calls_cold)
    subprocess.run(base, cwd=ROOT, check=True)  # warm: same saved state => zero new enrichment calls
    shutil.copy(run / "run_summary.json", HERE / "summary_warm.json")
    calls = [json.loads(l) for l in (run / "calls.jsonl").read_text().splitlines()]
    with open(HERE / "pilot_calls.jsonl", "w") as f:
        for i, c in enumerate(calls):
            f.write(json.dumps({**c, "run_id": "cold" if i < n_cold else "warm"}) + "\n")
    shutil.copy(run / "records.jsonl", HERE / "pilot_records.jsonl")
    with open(HERE / "usage.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["run_id", "request_id", "role", "model", "outcome", "n_review_ids", "input_tokens_uncached", "cache_read_tokens", "cache_write_tokens", "output_tokens", "latency_s", "attempt"])
        for i, c in enumerate(calls):
            u = c.get("usage", {}); w.writerow(["cold" if i < n_cold else "warm", c["request_id"], c["role"], c["model"], c["outcome"], len(c["review_ids"]),
                                              u.get("input_tokens", 0), u.get("cache_read_input_tokens", 0), u.get("cache_creation_input_tokens", 0),
                                              u.get("output_tokens", 0), c.get("latency_s", ""), c.get("attempt", "")])
    print("pilot saved; now run: python3 cost/calculator.py replay --write")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd")
    r = sub.add_parser("replay"); r.add_argument("--rates", default=str(HERE / "rates.csv")); r.add_argument("--scenario", default=str(HERE / "scenario.json"))
    r.add_argument("--rate-multiplier", type=float, default=1.0); r.add_argument("--budget", type=float); r.add_argument("--workers", type=int)
    r.add_argument("--fallback-fraction", type=float); r.add_argument("--output-cap", type=int); r.add_argument("--write", action="store_true")
    p = sub.add_parser("pilot"); p.add_argument("--confirm-paid", action="store_true")
    a = ap.parse_args()
    if a.cmd == "replay": replay(a)
    elif a.cmd == "pilot": pilot(a)
    else: ap.print_help()
