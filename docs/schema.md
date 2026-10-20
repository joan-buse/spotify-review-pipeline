# Shared output schema (schema-v1)

One record per source `review_id` in `grading/records.jsonl.gz`.

**Completed**
```json
{"review_id":"…","source_sha256":"HASH of the six original field strings","status":"completed",
 "topic":"usability","intent":"complaint","sentiment":-0.5,"severity":3,
 "entities":["ads"],"evidence_quote":"EXACT SOURCE SUBSTRING","needs_review":false,
 "label_config":"claude-haiku-5-5+think-off+enrich-p1+schema-v1","subtopic":"u1",
 "cache_source_id":"ORIGINAL-ID (only for exact-duplicate text reuse)"}
```
**Quarantined**: `{"review_id","source_sha256","status":"quarantined","reason":"empty_review_text | not_selected_budget_scope | model_refusal | …","attempts":n}`; the 13 `empty_review_text` rows are also listed in `quarantine.jsonl`.

Types/ranges: topic ∈ 8 topics; intent ∈ {cancellation, complaint, request, praise, unclear}; severity int 1–5; sentiment ∈ {−1,−0.5,0,0.5,1}; `evidence_quote` is a verified substring of `review_text`; `entities` = deterministic keyword matches (≤5). `subtopic` (optional, see `labels.md`) forms the issue id `topic.subtopic`.
Statuses: `pending` (queue only, never exported as final) · `completed` · `quarantined` (with reason and attempt count).
Model task contracts (versioned prompts live in `pipeline/prompts.py`): enrich → `n|sub|I|sev|sent|q|nr` lines; verify → `n|topic|intent|severity`; group → JSON `{title, summary, example_ids}` (IDs validated); memo → Markdown with `[Cnnn]` claim IDs (claim, issue and review IDs and numbers checked by code).
