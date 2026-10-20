# Label definitions and applied examples (enrich-p1 / schema-v1)

Exact shared labels from `assignment/GRADING_CONTRACT.md`.

**Topics** `access · usability · playback · downloads · catalog · billing · support · other`
**Intents (precedence)** `cancellation > complaint > request > praise > unclear`
**Severity** 1 no problem/praise/unclear/pure request · 2 dislike/minor/cosmetic · 3 degraded or restricted function · 4 core task blocked · 5 explicit serious financial/privacy/data harm.
Stars are never an input; the model only sees `review_text` (first 600 chars).

## Optional subtopics (fixed list, `subtopic` field; issue id = `topic.subtopic`)
| code | meaning | code | meaning |
|---|---|---|---|
| a1 | cannot log in / login loop | c1 | missing songs or artists |
| a2 | password or reset problems | c2 | search or discovery |
| a3 | account banned, locked or lost | c3 | recommendations or algorithm |
| a4 | signup / linked-account problems | c4 | lyrics, podcasts or other content |
| u1 | too many or intrusive ads | b1 | price too high |
| u2 | shuffle / forced order / playback controls | b2 | unwanted charge or cannot cancel |
| u3 | UI, navigation, queue or playlist management | b3 | features locked behind premium |
| u4 | other usability | b4 | premium/trial not activating, payment failure |
| p1 | crashes or freezes | s1 / s2 | no support response / other support |
| p2 | lag, buffering, connection failure | d1 | downloads disappear |
| p3 | songs skipping/pausing by themselves | d2 | cannot download |
| p4 | audio quality, battery, resource use | d3 | offline mode problems |
| p5 | other playback | o1 / o2 / o3 | general criticism / general praise / unrelated or unclear |

## Worked examples used in the prompt (invented or non-golden)
| text | label (topic.sub, intent, severity) | why |
|---|---|---|
| "Ads every two songs, unbearable now." | usability.u1, complaint, 2 | annoyance, function intact |
| "Can't log in since the update… reset it twice." | access.a1, complaint, 4 | core task blocked |
| "Downloaded songs disappear when I go offline. Uninstalling." | downloads.d1, **cancellation**, 3 | explicit departure outranks complaint; severity not raised by cancellation |
| "Charged twice for Premium and support never answered." | billing.b2, complaint, 3 | highest-severity problem is the charge |
| "Premium isn't activated even though I paid…" | billing.b4, complaint, 4 | entitlement failure |
| "Free Palestine!!" | other.o3, unclear, 1 (needs_review) | bare slogan |
| "Please add a sleep timer." | usability.u3, request, 1 | wish, no failure |

## Handling rules
* Ambiguous / garbled / unsupported-language → `needs_review=true` (flag, not auto-correct).
* `evidence_quote`: whole text if ≤160 chars, else the model-chosen sentence index (code extracts the exact substring; ≤240 chars). Code asserts substring membership.
* `entities`: deterministic keyword match (code, no model). `sentiment`: model integer −2…2 → /2.
* Validation failure → retry only the invalid items once → else quarantine with reason. Provider safety refusals → bounded bisection (≤log2(50) levels) to isolate the offending review.
