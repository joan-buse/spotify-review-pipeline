"""Stage 2 CLASSIFY (model role `enrich`) with code validation, one retry, atomic saves, spend ledger and resume."""
import argparse, json, os, random, re, signal, threading, time
from concurrent.futures import ThreadPoolExecutor, FIRST_COMPLETED, wait
from pathlib import Path
import anthropic
from .common import *
from .prepare import connect
from .prompts import ENRICH_SYSTEM

LINE = re.compile(r"^\s*(\d+)\s*\|\s*([a-z]\d)\s*\|\s*([XCRPU])\s*\|\s*([1-5])\s*\|\s*(-?[0-2])\s*\|\s*(\d+)\s*\|\s*([01])\s*$")
ENTITY_TERMS = ["ads", "shuffle", "premium", "offline", "download", "playlist", "podcast", "lyrics", "search", "login", "password",
                "queue", "radio", "family plan", "student", "duo", "free trial", "subscription", "payment", "support", "bluetooth",
                "chromecast", "car", "widget", "wrapped", "skip", "repeat", "equalizer", "facebook", "google", "update", "price",
                "volume", "sleep timer", "carplay", "android auto", "alexa", "watch", "spotify connect", "audiobook"]
class SpendCapReached(Exception):
    pass


def sentences(text):
    spans = [(m.start(), m.end()) for m in re.finditer(r"[^.!?\n]+[.!?]*", text) if m.group().strip()]
    out = []
    for s, e in spans:
        seg = text[s:e]; lead = len(seg) - len(seg.lstrip()); out.append(text[s + lead:e].rstrip())
    return [x for x in out if x]


def pick_quote(text, q):
    stripped = text.strip()
    if len(stripped) <= 160:
        return stripped
    sents = sentences(text)
    seg = sents[q] if q < len(sents) else (sents[0] if sents else stripped)
    return seg[:240].rstrip() or stripped[:240]


def find_entities(text):
    low = text.lower()
    return [t for t in ENTITY_TERMS if re.search(r"(?<!\w)" + re.escape(t), low)][:5]


def build_result(text, parsed):
    n, sub, intent, sev, sent, q, nr = parsed
    quote = pick_quote(text, q)
    assert quote in text and quote.strip()
    return {"topic": SUB_TO_TOPIC[sub], "subtopic": sub, "intent": INTENT_CODES[intent], "sentiment": int(sent) / 2.0,
            "severity": int(sev), "entities": find_entities(text), "evidence_quote": quote, "needs_review": bool(int(nr))}


def parse_output(raw, expected_n):
    got, errors = {}, {}
    for line in raw.strip().splitlines():
        if not line.strip():
            continue
        m = LINE.match(line)
        if not m:
            errors["?"] = f"unparseable line: {line[:60]}"; continue
        n, sub = int(m.group(1)), m.group(2)
        if n not in expected_n:
            errors[n] = "returned ID not in request"; continue
        if n in got:
            errors[n] = "duplicate ID"; continue
        if sub not in SUB_TO_TOPIC:
            errors[n] = f"unknown subtopic {sub}"; continue
        got[n] = (n, sub, m.group(3), m.group(4), m.group(5), int(m.group(6)), m.group(7))
    return got, errors


class Ledger:
    """Single spend ledger shared by all workers: reserve worst-case before dispatch, settle with actual usage."""
    def __init__(self, cap):
        self.cap, self.spent, self.reserved, self.lock = cap, 0.0, 0.0, threading.Lock()

    def reserve(self, amount):
        with self.lock:
            if self.spent + self.reserved + amount > self.cap:
                return False
            self.reserved += amount; return True

    def settle(self, reserved, actual):
        with self.lock:
            self.reserved -= reserved; self.spent += actual


class RateLimiter:
    def __init__(self, per_second):
        self.gap, self.next, self.lock = 1.0 / per_second, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            now = time.monotonic(); at = max(now, self.next); self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


def render_user(items):
    return "\n".join(f"{i}: {t[:MAX_TEXT_CHARS].replace(chr(10), ' ')}" for i, (_, t) in enumerate(items, 1))


class Enricher:
    def __init__(self, run_dir, workers=1, budget=1.0, batch_size=50, max_tokens=2000, rps=3.0, client=None,
                 fail_injector=None, system=ENRICH_SYSTEM, model=ENRICH_MODEL, label_config=LABEL_CONFIG, role="enrich", log_name="calls.jsonl"):
        load_env()
        self.run_dir = Path(run_dir); self.db = connect(self.run_dir / "state" / "state.sqlite")
        self.stop = threading.Event(); self.role = role; self.log = JsonlLog(self.run_dir / log_name)
        self.workers, self.batch_size, self.max_tokens = workers, min(batch_size, MAX_BATCH), max_tokens
        self.ledger, self.limiter = Ledger(budget), RateLimiter(rps)
        self.client = client or anthropic.Anthropic(max_retries=0, timeout=90)
        self.fail_injector, self.system, self.model, self.label_config = fail_injector, system, model, label_config
        self.dblock = threading.Lock(); self.stats = {"requests": 0, "failed_requests": 0, "retries_api": 0, "invalid_items": 0,
                                                       "completed_texts": 0, "quarantined_texts": 0, "cost_usd": 0.0,
                                                       "input_tokens": 0, "output_tokens": 0}
        prior = 0.0
        lp = self.run_dir / log_name
        if lp.exists():  # the cap covers the whole run: include spend already recorded for this role in earlier invocations
            for line in lp.read_text().splitlines():
                d = json.loads(line)
                if d.get("role") == role and d.get("outcome") == "succeeded":
                    prior += d.get("cost_usd", 0.0)
        self.ledger.spent += prior; self.stats["prior_spend_usd"] = round(prior, 5)
        self.phase = "resume" if self.db.execute("SELECT count(*) FROM results").fetchone()[0] else "initial"

    def pending(self):
        return self.db.execute("""SELECT text_hash, rep_id, text FROM texts WHERE text_hash NOT IN (SELECT text_hash FROM results)
                                  AND text_hash NOT IN (SELECT text_hash FROM failures WHERE attempts>=2) ORDER BY rowid""").fetchall()

    def _call(self, items, attempt_tag):
        """One request with bounded backoff for transient errors. Returns (raw_text, usage) or raises."""
        user = render_user(items)
        est_in = (len(self.system) + len(user)) / 3.2
        reserve = (est_in * RATES[self.model]["input"] + self.max_tokens * RATES[self.model]["output"]) / 1e6
        for attempt in range(4):
            if self.stop.is_set():
                raise InterruptedError("stop requested")
            if not self.ledger.reserve(reserve):
                raise SpendCapReached(f"spent {self.ledger.spent:.4f} + reserved {self.ledger.reserved:.4f} + next {reserve:.4f} > cap {self.ledger.cap}")
            self.limiter.wait(); t0 = time.time()
            ids = [rid for rid, _ in items]
            try:
                if self.fail_injector and self.fail_injector(attempt):
                    raise anthropic.APIConnectionError(request=None) if False else RuntimeError("injected transient failure")
                msg = self.client.messages.create(model=self.model, max_tokens=self.max_tokens, thinking={"type": "disabled"},
                                                  system=self.system, messages=[{"role": "user", "content": user}])
                u = msg.usage
                usage = {"input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
                         "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
                         "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0}
                c = cost_usd(self.model, usage); self.ledger.settle(reserve, c)
                total_in = usage["input_tokens"] + usage["cache_read_input_tokens"] + usage["cache_creation_input_tokens"]
                self.log.write({"request_id": msg.id, "role": self.role, "review_ids": ids, "model": self.model, "phase": self.phase,
                                "outcome": "succeeded", "label_config": self.label_config, "input_tokens": total_in,
                                "output_tokens": usage["output_tokens"], "usage": usage, "cost_usd": round(c, 6),
                                "latency_s": round(time.time() - t0, 3), "attempt": attempt_tag + attempt,
                                "stop_reason": msg.stop_reason, "ts": time.time()})
                with self.dblock:
                    self.stats["requests"] += 1; self.stats["cost_usd"] += c
                    self.stats["input_tokens"] += total_in; self.stats["output_tokens"] += usage["output_tokens"]
                return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text"), msg.stop_reason
            except (InterruptedError, SpendCapReached):
                raise
            except Exception as e:  # transient/API error: log failed attempt, back off with jitter
                self.ledger.settle(reserve, 0.0)
                self.log.write({"request_id": f"local-{os.getpid()}-{time.time_ns()}", "role": self.role, "review_ids": ids,
                                "model": self.model, "phase": self.phase, "outcome": "failed", "label_config": self.label_config,
                                "input_tokens": 0, "output_tokens": 0, "error": f"{type(e).__name__}: {str(e)[:200]}",
                                "latency_s": round(time.time() - t0, 3), "attempt": attempt_tag + attempt, "ts": time.time(),
                                "usage_note": "no usage reported for failed attempt; timeout charges unknown"})
                with self.dblock:
                    self.stats["failed_requests"] += 1
                if attempt == 3:
                    raise
                with self.dblock:
                    self.stats["retries_api"] += 1
                time.sleep(min(30, 2 ** attempt + random.random()))

    def process_batch(self, batch):
        """batch: list of (text_hash, rep_id, text). Saves validated results atomically; retries invalid items once."""
        todo = list(batch); results = {}; reason = {}
        for round_no in (1, 2):
            if not todo:
                break
            items = [(rid, text) for _, rid, text in todo]
            try:
                raw, stop = self._call(items, attempt_tag=0 if round_no == 1 else 10)
            except InterruptedError:
                return "interrupted"
            except SpendCapReached as e:
                JsonlLog(self.run_dir / "run_log.jsonl").write({"event": "spend_cap_reached", "stage": self.role, "detail": str(e), "ts": time.time()})
                self.stop.set(); self.stats["stopped_by_spend_cap"] = True
                return "cap"  # items stay pending (not quarantined) so a later resume with more budget picks them up
            except Exception as e:
                for th, rid, text in todo:
                    reason[th] = f"api_failure_after_retries: {type(e).__name__}"
                break
            if stop == "refusal" and not raw.strip():
                # Provider safety refusal poisons the whole request: bisect (bounded: log2(50) levels) to isolate the offending review(s).
                if len(todo) == 1:
                    reason[todo[0][0]] = "model_refusal"; break
                mid = len(todo) // 2
                self.process_batch(todo[:mid]); self.process_batch(todo[mid:]); todo = []
                break
            got, errors = parse_output(raw, set(range(1, len(items) + 1)))
            nxt = []
            for i, (th, rid, text) in enumerate(todo, 1):
                if i in got:
                    try:
                        results[th] = (rid, build_result(text, got[i]))
                        continue
                    except AssertionError:
                        errors[i] = "quote not a source substring"
                reason[th] = errors.get(i, "missing from response")
                nxt.append((th, rid, text))
            with self.dblock:
                self.stats["invalid_items"] += len(nxt)
            todo = nxt  # one retry of just the invalid items (new request)
        with self.dblock, self.db:
            now = time.time()
            for th, (rid, res) in results.items():
                self.db.execute("INSERT OR REPLACE INTO results VALUES(?,?,?,?,?,?,?)",
                                (th, rid, self.label_config, json.dumps(res, ensure_ascii=False), "", self.phase, now))
            for th, rid, text in todo:
                self.db.execute("INSERT INTO failures VALUES(?,?,2) ON CONFLICT(text_hash) DO UPDATE SET reason=?, attempts=2",
                                (th, reason.get(th, "unknown"), reason.get(th, "unknown")))
            self.stats["completed_texts"] += len(results); self.stats["quarantined_texts"] += len(todo)
        return "ok"

    def run(self, max_batches=None, progress_every=20):
        pend = self.pending(); batches = [pend[i:i + self.batch_size] for i in range(0, len(pend), self.batch_size)]
        if max_batches is not None:
            batches = batches[:max_batches]
        t0 = time.time(); done = 0
        old = signal.signal(signal.SIGINT, lambda *a: self.stop.set())
        with ThreadPoolExecutor(self.workers) as ex:
            futs, it = set(), iter(batches); exhausted = False
            while True:
                while not exhausted and len(futs) < self.workers and not self.stop.is_set():
                    try:
                        futs.add(ex.submit(self.process_batch, next(it)))
                    except StopIteration:
                        exhausted = True
                if not futs:
                    break
                d, futs = wait(futs, return_when=FIRST_COMPLETED)
                for f in d:
                    done += 1; f.result()
                    if done % progress_every == 0:
                        print(f"[enrich] batches {done}/{len(batches)} cost ${self.stats['cost_usd']:.4f} "
                              f"elapsed {time.time() - t0:.0f}s", flush=True)
                if self.stop.is_set() and not futs:
                    break
        signal.signal(signal.SIGINT, old)
        self.stats["wall_clock_s"] = round(time.time() - t0, 2); self.stats["batches_dispatched"] = done
        self.stats["phase"] = self.phase
        return self.stats


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True); ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--budget", type=float, required=True); ap.add_argument("--batch-size", type=int, default=50)
    ap.add_argument("--max-batches", type=int); ap.add_argument("--rps", type=float, default=3.0)
    a = ap.parse_args()
    s = Enricher(a.run_dir, a.workers, a.budget, a.batch_size, rps=a.rps).run(a.max_batches)
    print(json.dumps(s, indent=2))
