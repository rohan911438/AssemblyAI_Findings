# Evidence — Bug 3: the cleaned output is silently thrown away

## What this bug is, in one paragraph

The Dictation API returns two versions of what you said. `text` is the verbatim
transcription. `llm_response` is the "cleaned" version produced by a language
model — and with the `llm_instruction` field you can tell that model how to clean
it ("rewrite as an email", "make it three paragraphs", "turn this into bullet
points"). This is the feature that makes the output "ready to send". The bug is
that the cleaned output has a hidden size limit, and when the model's answer would
exceed it, the API does not return a shortened answer or a clear error — it throws
the **entire** cleaned result away, returns `llm_response: null`, and sets
`llm_error: "truncated"`, all under a normal `HTTP 200`. Two things make this
sharp: the whole output is lost (not trimmed), and `"truncated"` is not one of the
documented error values, so apps are not written to expect it.

## How to reproduce

```
AAI_KEY=<key> python3 repro_truncation.py
```

The script uses a bundled 3.7-second clip (`fixtures/speech_short.wav`,
transcript: "Send the quarterly report to Margaret by Friday.") and sends it with
different cleaning instructions, so the only variable is how long the requested
output is.

## Walkthrough of what the script shows

### Two natural instructions, one works and one vanishes

Both of these are ordinary, reasonable requests for the same short note:

```
instruction: "rewrite as a short, polite formal email"        -> HTTP 200, llm_error=None,        llm_response=213 chars
instruction: "rewrite in expanded prose, ~three paragraphs"    -> HTTP 200, llm_error='truncated', llm_response=null
```

The email request succeeds and returns a 213-character email. The "three
paragraphs" request — on the identical audio — comes back with `llm_response:
null`. Nothing tells the user why; a client that displays `llm_response` simply
shows a blank where the cleaned note should be. The user dictated something and
got nothing back, with a `200 OK`.

### The size cliff, made deterministic

To show it is purely about output length, we ask the model to repeat one fixed
sentence N times. As N grows, the output grows, until it falls off the cliff:

```
output a sentence exactly  5 times -> HTTP 200, llm_error=None,        llm_response=119 chars
output a sentence exactly  6 times -> HTTP 200, llm_error=None,        llm_response=143 chars
output a sentence exactly  7 times -> HTTP 200, llm_error=None,        llm_response=167 chars
output a sentence exactly  8 times -> HTTP 200, llm_error=None,        llm_response=191 chars
output a sentence exactly 10 times -> HTTP 200, llm_error='truncated', llm_response=null
output a sentence exactly 15 times -> HTTP 200, llm_error='truncated', llm_response=null
```

Up to about 190–200 characters the output is returned normally. Cross roughly 200
characters and the whole thing is discarded. The cutoff is a hard edge, not a
graceful trim.

## The raw before/after bodies (same audio, same transcript)

Succeeds — "rewrite as a short, polite formal email":

```json
{"text":"Send the quarterly report to Margaret by Friday.",
 "audio_duration_ms":3672,
 "llm_response":"Subject: Quarterly Report Submission\n\nDear Margaret,\n\nPlease find the quarterly report attached. Kindly review it at your earliest convenience and let me know if you have any questions.\n\nBest regards,\n\n[Your Name]",
 "llm_error":null}
```

Fails — "rewrite in expanded professional prose, roughly three short paragraphs":

```json
{"text":"Send the quarterly report to Margaret by Friday.",
 "audio_duration_ms":3672,
 "llm_response":null,
 "llm_error":"truncated"}
```

Note that `text` is present and correct in both. Only the cleaned output differs:
one is an email, the other is gone.

## The part that makes it a genuine trap

The size limit is not fixed — it scales with how much you dictated. The same
"repeat the sentence 10 times" instruction that fails on this ~8-word clip
**succeeds** on a longer (~130-word) transcript. So:

- The limit depends on the length of the *input* audio, not on the instruction.
- A developer who tests their "expand my note" feature with a long sample will see
  it work, ship it, and then have it silently fail for users who dictate short
  notes — the most common case for a dictation product.
- Plain default cleanup (no `llm_instruction`) was not observed to hit this,
  because its output is naturally bounded by the transcript length. The problem is
  specific to the custom-instruction path, whose whole purpose is to reshape or
  expand the text.

## Why it matters to AssemblyAI

`llm_instruction` is a documented, promoted capability, and expanding or
reformatting a short dictated note is its single most natural use — which is
exactly the case that breaks. The failure mode is the worst kind for a product:
silent, total data loss under a `200 OK`, with an error code (`"truncated"`) that
is not in the documentation, so client developers have no reason to handle it.

## Suggested fix

At minimum, add `"truncated"` to the documented `llm_error` enum and document the
output budget. Better, return the partial cleaned output instead of discarding it,
or fall back to returning `text` with a clear flag, so the user never sees a blank
result for a successful request.
