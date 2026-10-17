# Evidence — Bug 3: cleaned output silently discarded with undocumented `llm_error`

Reproduction command:

```
AAI_KEY=<key> python3 repro_truncation.py
```

Fixture: `fixtures/speech_short.wav` (~3.67 s, transcript
"Send the quarterly report to Margaret by Friday.").

Captured run:

```
== natural use: dictate a short note, ask the model to expand/format it ==
instruction: short formal email (fits budget)                  -> HTTP 200, llm_error=None, llm_response=213 chars
instruction: 'roughly three short paragraphs' (exceeds budget) -> HTTP 200, llm_error='truncated', llm_response=null   <-- discarded

== deterministic length cliff: force the model to emit N copies of a line ==
instruction: output a sentence exactly 5 times   -> HTTP 200, llm_error=None, llm_response=119 chars
instruction: output a sentence exactly 6 times   -> HTTP 200, llm_error=None, llm_response=143 chars
instruction: output a sentence exactly 7 times   -> HTTP 200, llm_error=None, llm_response=167 chars
instruction: output a sentence exactly 8 times   -> HTTP 200, llm_error=None, llm_response=191 chars
instruction: output a sentence exactly 10 times  -> HTTP 200, llm_error='truncated', llm_response=null   <-- discarded
instruction: output a sentence exactly 15 times  -> HTTP 200, llm_error='truncated', llm_response=null   <-- discarded
```

## Raw response bodies (same audio, same transcript)

Succeeds — `llm_instruction` = "Rewrite the dictated note as a short, polite
formal business email.":

```json
{"text":"Send the quarterly report to Margaret by Friday.",
 "confidence":0.993,"audio_duration_ms":3672,"session_id":"5fa07171-...",
 "llm_response":"Subject: Quarterly Report Submission\n\nDear Margaret,\n\nPlease find the quarterly report attached. Kindly review it at your earliest convenience and let me know if you have any questions.\n\nBest regards,\n\n[Your Name]",
 "llm_error":null}
```

Fails — `llm_instruction` = "Rewrite the dictated note in expanded professional
prose, roughly three short paragraphs.":

```json
{"text":"Send the quarterly report to Margaret by Friday.",
 "confidence":0.993,"audio_duration_ms":3672,"session_id":"7382cff3-...",
 "llm_response":null,"llm_error":"truncated"}
```

## Observations

- The failure is `HTTP 200` with `llm_response: null` and `llm_error:
  "truncated"`. The verbatim `text` field is still present and correct.
- `"truncated"` is not in the documented `llm_error` enum
  (`null | "timeout" | "upstream_error"`).
- On truncation the entire cleaned output is discarded, not returned partial.
- The budget is proportional to transcript length: for this ~8-word transcript
  the cap is roughly 200 characters of cleaned output. The identical
  forced-length instruction that fails here succeeds on a longer (~130-word)
  transcript, confirming the cap scales with transcript length and is not a
  property of the instruction.
- Plain default cleanup (no `llm_instruction`) was not observed to hit this,
  because default cleanup output is bounded by the transcript length and audio
  is hard-capped at 120 s. The issue is specific to the custom-`llm_instruction`
  path, whose most natural use (expand or reformat a short note) is exactly what
  exceeds the budget.
