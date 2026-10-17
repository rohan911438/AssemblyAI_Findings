# AssemblyAI Dictation API — Findings

Submission for the AssemblyAI "Hack into Speech" voice hackathon, covering three
tracks: Bug Report, API feedback, and Security review.

This repository documents three independent, validated defects in the Dictation
API (`dictation.assemblyai.com`), each with a one-command reproduction and
captured evidence. Every finding was reproduced against the live API on two
independent keys. No product was built; the submission is the findings.

## Contents

| Path | Description |
|---|---|
| `README.md` | This report: three bug tickets, API feedback, security review |
| `repro.py` | Reproduces Bug 1 (HTTP 500 on short audio); HTTP + optional SDK |
| `repro_duration.py` | Reproduces Bug 2 (WAV trailing-chunk duration inflation) |
| `repro_truncation.py` | Reproduces Bug 3 (silent discard of cleaned output) |
| `fixtures/speech_short.wav` | ~3.7 s speech clip used by `repro_truncation.py` |
| `evidence/bug-01-short-audio-500.md` | Captured output and raw bodies for Bug 1 |
| `evidence/bug-02-wav-duration-inflation.md` | Captured output and raw bodies for Bug 2 |
| `evidence/bug-03-llm-truncation.md` | Captured output and raw bodies for Bug 3 |
| `evidence/additional-feedback-and-security.md` | API feedback and security test results |

## Quick start

```bash
AAI_KEY=<your_key> python3 repro_duration.py      # Bug 2
AAI_KEY=<your_key> python3 repro.py               # Bug 1 (add `pip install assemblyai` for the SDK path)
AAI_KEY=<your_key> python3 repro_truncation.py    # Bug 3
```

All three scripts use the Python standard library only (the SDK path in
`repro.py` is optional).

## Summary of findings

| # | Finding | Class | Severity |
|---|---|---|---|
| 1 | HTTP 500 "inference failed" on valid audio of 0 < duration <= 16 ms | Reliability / crash | High |
| 2 | WAV reader counts bytes after the `data` chunk as audio: wrong `audio_duration_ms` and spurious `413` | Correctness / billing integrity | High |
| 3 | Custom-`llm_instruction` output silently discarded (`llm_response: null`) with undocumented `llm_error: "truncated"` | Product correctness | Medium |

---

# Bug 2 (primary) — WAV reader decodes trailing chunks as audio

| | |
|---|---|
| **Title** | WAV reader ignores `data`/RIFF sizes and decodes to end-of-stream, counting trailing chunks as audio |
| **Component** | `dictation.assemblyai.com` audio ingestion (WAV demuxer) |
| **Endpoints** | `POST /v1/transcribe/live`, legacy `POST /v1/transcribe/stream` |
| **Severity** | **High** — wrong reported duration (billing-integrity risk) and spurious rejection of valid files |
| **Status** | Reproduced on 2 keys, 3/3 per case |

## 1. Summary

After locating the `fmt ` and `data` chunk headers, the WAV reader decodes every
byte from the start of the `data` payload to end-of-stream as 16-bit PCM. It
honors neither the `data` chunk's declared size nor the RIFF size. Standard,
spec-compliant chunks that follow `data` — `JUNK` alignment padding,
`LIST`/`INFO` metadata, `id3`, `cue`, Broadcast-WAV `bext`/`iXML` — are therefore
counted as audio samples.

## 2. Impact

1. **Wrong `audio_duration_ms`.** The reported duration includes non-audio
   trailing bytes. 1000 ms of real speech plus a 1 MB `JUNK` chunk is reported as
   32250 ms. If duration drives billing (list pricing is per hour of audio), this
   is a billing-integrity and potential overcharge issue; independently, the
   returned field is simply incorrect.
2. **Spurious `413` on valid files.** A real sub-120 s WAV can be rejected because
   its trailing metadata or padding is miscounted toward the 120000 ms limit. A
   119000 ms clip plus 200 KB of padding returns `413 audio_too_large`, though the
   true audio is well under the limit.
3. **Transcript-integrity risk.** Bytes after `data` that happen to encode
   speech-like PCM would be transcribed as if part of the recording.

These trailing chunks are produced by common tooling (alignment padding,
metadata tags, Broadcast-WAV), so ordinary files — not just synthetic ones — are
affected.

## 3. Steps to reproduce

```bash
AAI_KEY=<your_key> python3 repro_duration.py
```

Observed (`evidence/bug-02-wav-duration-inflation.md`):

```
== 1000 ms of real PCM, with trailing chunks appended after `data` ==
data only (control)                -> HTTP 200, audio_duration_ms=1000
+ JUNK chunk of 32,000 bytes       -> HTTP 200, audio_duration_ms=2000    (true audio is 1000 ms)
+ JUNK chunk of 1,000,000 bytes    -> HTTP 200, audio_duration_ms=32250   (true audio is 1000 ms)

== spurious 413: a valid 119,000 ms clip + 200 KB of trailing JUNK ==
119000 ms audio + 200 KB JUNK -> HTTP 413 {"error":"audio exceeds 120000 ms","error_code":"audio_too_large"}
```

## 4. Actual vs. expected

- **Actual:** reported duration = `(data_payload .. EOF) / 2 / sample_rate`; the
  120 s cap is applied to this inflated count.
- **Expected:** read exactly the `data` chunk's declared number of bytes (bounded
  by the RIFF size), and skip any subsequent chunks, as the reader already does
  for chunks that precede `data`.

## 5. Root cause — where this lives

In the WAV demuxer, in the step that determines how many PCM bytes to read from
the `data` chunk. Pre-`data` chunks are parsed and skipped correctly, so the
chunk walker exists; the defect is that once `data` is reached, the reader
consumes to end-of-stream instead of stopping at `data_offset + data_size` (and
instead of trusting the RIFF size as an outer bound). Confirmed: setting the
`data` size field to `200` while 32000 bytes are present still decodes all 32000
bytes, and setting the RIFF size to `4` still decodes everything.

## 6. Suggested fix

When reading the `data` chunk, read exactly `min(data_chunk_size, riff_bound)`
bytes and ignore trailing chunks (continue the existing chunk walk past `data`
rather than treating the remainder as samples). Compute `audio_duration_ms` and
apply the 120 s cap from that bounded sample count.

---

# Bug 1 — HTTP 500 crash on valid short audio

| | |
|---|---|
| **Title** | Dictation API returns `500 "inference failed"` for valid audio of 0 < duration <= 16 ms |
| **Component** | `dictation.assemblyai.com` inference service (Universal-3.5 Pro, Dictation) |
| **Endpoints** | `POST /v1/transcribe/live`, legacy `POST /v1/transcribe/stream` |
| **Also affects** | Official Python SDK `assemblyai==1.6.1` (`DictationTranscriber.transcribe_live`) |
| **Severity** | **High** — unhandled 5xx on valid input in a path real clients hit |
| **Status** | Reproduced on 2 keys, both endpoints, HTTP + SDK |

## 1. Summary

The service returns `HTTP 500 {"status":500,"title":"Inference Error","detail":"inference failed"}`
for any valid WAV/PCM audio whose duration is greater than 0 and at most 16 ms.
This is a server fault (`5xx`), not a client-input rejection — it is surfaced as
neither `400`, `413`, nor `415`.

## 2. The behavior is non-monotonic

| Audio duration | Result |
|---|---|
| `0 ms` (empty) | `200 OK` (empty transcript) — handled |
| `0 < d <= 16 ms` | `500 Inference Error` — crashes |
| `d > 16 ms` | `200 OK` — handled |

A client sending less data (0 ms) succeeds, a client sending slightly more (8 ms)
gets a 500, and a client sending even more (17 ms) succeeds again. No
input-validation policy produces a hole in the middle of the range; an unguarded
code path does.

## 3. Steps to reproduce

```bash
AAI_KEY=<your_key> python3 repro.py        # HTTP path, standard library only
pip install assemblyai && AAI_KEY=<key> python3 repro.py   # also drives the official SDK
```

Observed (`evidence/bug-01-short-audio-500.md`):

```
    0 samples @ 16000 Hz =   0.00 ms -> HTTP 200
  256 samples @ 16000 Hz =  16.00 ms -> HTTP 500
  257 samples @ 16000 Hz =  16.06 ms -> HTTP 200
  706 samples @ 44100 Hz =  16.01 ms -> HTTP 500
  707 samples @ 44100 Hz =  16.03 ms -> HTTP 200

Python SDK: 256 samples -> DictationError: inference failed;  257 samples -> OK
```

## 4. Actual vs. expected

- **Actual:** `500 "inference failed"` (HTTP) / `DictationError` (SDK).
- **Expected:** the graceful handling the 0 ms case already receives — `200` with
  an empty transcript — or a documented `4xx` (for example `audio_too_short`),
  mirroring the existing `413 audio_too_large`.

## 5. Root cause — where this lives

The failing boundary is exactly 16 ms and is identical across sample rates
(256 samples at 16 kHz, 706 at 44.1 kHz). A fixed time threshold invariant to
sample rate points at the front-end feature extractor: 16 ms is one analysis
frame. When decoded audio is shorter than a single frame, framing produces zero
complete frames and the model is invoked on an empty feature tensor, which raises
inside inference and surfaces as the generic `500`. The `0 ms` case is already
special-cased (returns `200`, empty transcript); the defect is that
`0 < d <= 16 ms` falls through that early return into the model. Pathological-but-
valid audio at normal length returns `200`, isolating the cause to length, not
content.

## 6. Suggested fix

After decode and before framing, extend the existing empty-audio early return to
cover sub-frame input:

```python
if samples == 0:
    return ok_empty_transcript()          # existing behavior
if samples * 1000 / sr <= FRAME_MS:       # FRAME_MS == 16
    return ok_empty_transcript()          # treat sub-frame like empty, OR
    # raise BadRequest(error_code="audio_too_short", ...)
```

## 7. Impact

16 ms is below one frame, but production dictation clients emit sub-16 ms clips
routinely: a tap-and-release on the mic, a push-to-talk key pressed and released
in one keystroke, or a client-side VAD emitting a tiny leading/trailing segment.
Each becomes a hard `500` (or an uncaught `DictationError` in SDK apps), and
because it is a `5xx`, default retry logic will retry input that can never
succeed.

---

# Bug 3 — Cleaned output silently discarded with an undocumented error

| | |
|---|---|
| **Title** | Custom-`llm_instruction` output silently dropped (`llm_response: null`) with undocumented `llm_error: "truncated"` past a hidden output budget |
| **Component** | `dictation.assemblyai.com` LLM cleanup path |
| **Endpoints** | `POST /v1/transcribe/live` |
| **Severity** | **Medium** — user-facing silent data loss in the core cleanup feature |
| **Status** | Reproduced on 2 keys, deterministic |

## 1. Summary

When a custom `llm_instruction` requests cleaned output longer than a hidden,
transcript-length-proportional budget, the API silently discards the entire
cleaned output (`llm_response: null`) and returns `llm_error: "truncated"` under
`HTTP 200`. The verbatim `text` field is still present, so a client that renders
`llm_response` as the finished output shows the user a blank result.

`"truncated"` is not in the documented `llm_error` enum
(`null | "timeout" | "upstream_error"`), so client code written to the documented
set will not anticipate it.

## 2. Steps to reproduce

```bash
AAI_KEY=<your_key> python3 repro_truncation.py
```

Fixture: `fixtures/speech_short.wav` (~3.67 s, transcript "Send the quarterly
report to Margaret by Friday."). Observed
(`evidence/bug-03-llm-truncation.md`):

```
instruction: short formal email (fits budget)                  -> HTTP 200, llm_error=None,        llm_response=213 chars
instruction: 'roughly three short paragraphs' (exceeds budget) -> HTTP 200, llm_error='truncated', llm_response=null

output a sentence exactly  8 times -> HTTP 200, llm_error=None,        llm_response=191 chars
output a sentence exactly 10 times -> HTTP 200, llm_error='truncated', llm_response=null
```

## 3. Actual vs. expected

- **Actual:** `200` with `llm_response: null` and the undocumented
  `llm_error: "truncated"`; the whole cleaned output is dropped rather than
  returned partial.
- **Expected:** either return the cleaned output, or document the budget and the
  `"truncated"` error value and consider returning a partial result. At minimum,
  `"truncated"` belongs in the published `llm_error` enum.

## 4. Notes

- The budget scales with transcript length: the identical forced-length
  instruction that fails on this ~8-word transcript (cap roughly 200 characters)
  succeeds on a longer (~130-word) transcript. The cliff is therefore invisible
  to the caller and depends on how much was dictated, not on the instruction.
- The most natural use of `llm_instruction` — dictate a short note and ask the
  model to expand or reformat it — is exactly the case that exceeds the budget.
- Plain default cleanup (no `llm_instruction`) was not observed to hit this.

---

# API feedback (suggestions)

Lower-severity items found during testing; details and captures in
`evidence/additional-feedback-and-security.md`.

1. **`llm_instruction` length limit not enforced.** Docs state 2048 characters;
   the server accepts at least 20,000. Sibling fields (`stt_prompt` 6000,
   `keyterms_prompt` 100) are strictly enforced.
2. **Validation-ordering on `sample_rate`.** The 120 s duration guard is computed
   from the client-supplied sample rate before it is validated, so an invalid rate
   can return a misleading `413` instead of `415`.
3. **Inconsistent error envelopes.** Two shapes are returned by different layers:
   `{"status","title","detail"}` and `{"error","error_code"}`.
4. **Misleading WAV error.** An overstated `data` chunk size returns
   `400 "stream ended before a complete WAV header arrived"` though the header is
   complete.
5. **Documentation.** Supported-language count is stated as 18 / 19 / 32 across
   sources (the API enumerates 32).

---

# Security review

No vulnerability was found. The tested surfaces and one hardening suggestion are
recorded in `evidence/additional-feedback-and-security.md`. In summary: transcript
retrieval, cross-account access, secret leakage, config injection, cross-request
contamination, and prompt disclosure were all tested (own accounts only,
read-only, no denial-of-service) and showed a solid posture. The one design
suggestion is to offer short-lived or scoped tokens so browser clients need not
embed a long-lived API key.
