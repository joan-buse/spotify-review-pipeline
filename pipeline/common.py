"""Shared constants, source-row hashing, config and small IO helpers."""
import csv, hashlib, json, os, sys, tempfile, threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "assignment"))
from check_submission import row_sha, csv_rows, profile, FIELDS, TOPICS, INTENTS  # noqa: E402  (provided helper)

SEED = "berkeley-fall-2026-assignment-5-v1"
ENRICH_MODEL = "claude-haiku-5-5"
VERIFY_MODEL = "claude-haiku-5-5"
GROUP_MODEL = "claude-haiku-5-5"
MEMO_MODEL = "claude-sonnet-5-5"
PROMPT_VERSION = "enrich-p1"
SCHEMA_VERSION = "schema-v1"
LABEL_CONFIG = f"{ENRICH_MODEL}+think-off+{PROMPT_VERSION}+{SCHEMA_VERSION}"
MAX_BATCH = 50
MAX_TEXT_CHARS = 600  # text shown to the model; quotes are still chosen from the FULL source text in code

# Editable, dated rates (USD per million tokens). See cost/rates.csv for the source/date record.
RATES = {  # USD per million tokens; official list prices checked 2026-10-20 (platform.claude.com/docs/en/about-claude/pricing)
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01, "cache_write": 0.125},   # prompts <= 100k tokens
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.10, "cache_write": 2.50},
}
BATCH_MULTIPLIER = 0.5  # Message Batches API: 50% off input and output (same page)

SUBTOPICS = {
    "access": {"a1": "cannot log in / login loop", "a2": "password or reset problems", "a3": "account banned, locked or lost", "a4": "signup or linked-account (Facebook/Google) problems"},
    "usability": {"u1": "too many or intrusive ads", "u2": "shuffle / forced order / playback controls", "u3": "UI, navigation, queue or playlist management", "u4": "other usability"},
    "playback": {"p1": "crashes or freezes", "p2": "lag, buffering or connection failure", "p3": "songs skipping, pausing or stopping by themselves", "p4": "audio quality, battery or resource use", "p5": "other playback"},
    "downloads": {"d1": "downloads disappear or vanish", "d2": "cannot download", "d3": "offline mode problems"},
    "catalog": {"c1": "missing songs or artists", "c2": "search or discovery", "c3": "recommendations or algorithm", "c4": "lyrics, podcasts or other content"},
    "billing": {"b1": "price too high", "b2": "unwanted charge or cannot cancel subscription", "b3": "features locked behind premium", "b4": "premium/trial not activating or payment failure"},
    "support": {"s1": "no support response", "s2": "other support"},
    "other": {"o1": "general criticism", "o2": "general praise", "o3": "unrelated or unclear"},
}
SUB_TO_TOPIC = {s: t for t, d in SUBTOPICS.items() for s in d}
TOPIC_CODES = {str(i): t for i, t in enumerate(TOPICS)}
INTENT_CODES = {"X": "cancellation", "C": "complaint", "R": "request", "P": "praise", "U": "unclear"}


def sample_key(review_id):
    return hashlib.sha256(f"{SEED}:{review_id}".encode()).hexdigest()


def load_env():
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env")
        load_dotenv(ROOT.parent / ".env")
    except ImportError:
        pass


def atomic_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".")
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
        f.flush(); os.fsync(f.fileno())
    os.replace(tmp, path)


def write_json(path, obj):
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def cost_usd(model, usage):
    r = RATES[model]
    uncached = usage.get("input_tokens", 0)  # Anthropic reports uncached input separately from cache_read/creation
    return (uncached * r["input"] + usage.get("output_tokens", 0) * r["output"]
            + usage.get("cache_read_input_tokens", 0) * r["cache_read"]
            + usage.get("cache_creation_input_tokens", 0) * r["cache_write"]) / 1e6


class JsonlLog:
    """Thread-safe append-only JSONL log."""
    def __init__(self, path):
        self.path = Path(path); self.path.parent.mkdir(parents=True, exist_ok=True); self.lock = threading.Lock()

    def write(self, obj):
        with self.lock, self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(obj, ensure_ascii=False) + "\n"); f.flush(); os.fsync(f.fileno())
