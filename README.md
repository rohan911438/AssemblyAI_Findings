# AssemblyAI Dictation API — Findings

Submission for the AssemblyAI "Hack into Speech" voice hackathon, covering three
tracks: **Bug Report**, **API feedback**, and **Security review**.

This repository documents **three independent, validated defects** in the
Dictation API (`dictation.assemblyai.com`), each with a one-command reproduction
you can run yourself and a detailed written evidence file. Every finding was
reproduced against the live API on two independent API keys. No product was
built — the submission is the findings.

Bugs are numbered by priority: **Bug 1** is the most impactful.

## Summary of findings

| # | Finding | Class | Severity | Reproduce with | Full evidence |
|---|---|---|---|---|---|
| 1 | WAV reader counts bytes after the `data` chunk as audio — wrong `audio_duration_ms` and spurious `413` on valid files | Correctness / billing integrity | High | `repro_duration.py` | [bug-01-wav-duration-inflation.md](evidence/bug-01-wav-duration-inflation.md) |
| 2 | `HTTP 500 "inference failed"` on valid audio of 0 < duration ≤ 16 ms | Reliability / crash | High | `repro.py` | [bug-02-short-audio-500.md](evidence/bug-02-short-audio-500.md) |
| 3 | Custom-`llm_instruction` output silently discarded (`llm_response: null`) with undocumented `llm_error: "truncated"` | Product correctness | Medium | `repro_truncation.py` | [bug-03-llm-truncation.md](evidence/bug-03-llm-truncation.md) |

> The repro scripts are named by what they do, so Bug 2 (the crash) is
> `repro.py`; the table's "Reproduce with" column is the authoritative mapping.

## How to test these yourself

**Prerequisites:** Python 3 and an AssemblyAI API key. The scripts use only the
Python standard library — no `pip install` is needed, except the optional SDK
check in `repro.py` (`pip install assemblyai`).

**Set your key once:**

```bash
export AAI_KEY=<your_assemblyai_api_key>
```

**Run each reproduction (about 5–10 seconds each):**

```bash
python3 repro_duration.py      # Bug 1 — WAV trailing-chunk duration inflation
python3 repro.py               # Bug 2 — HTTP 500 on short audio (and the SDK, if installed)
python3 repro_truncation.py    # Bug 3 — cleaned output silently discarded
```

Each script prints a labelled table of requests and results; the inflated
durations, the `500`s, and the `null`/`truncated` responses are flagged inline.
The matching `evidence/*.md` file walks through exactly what each line means.

## Repository contents

| Path | Description |
|---|---|
| `README.md` | This report: three bug tickets, API feedback, security review |
| `repro_duration.py` | Reproduces **Bug 1** (WAV trailing-chunk duration inflation) |
| `repro.py` | Reproduces **Bug 2** (HTTP 500 on short audio); HTTP + optional SDK |
| `repro_truncation.py` | Reproduces **Bug 3** (silent discard of cleaned output) |
| `fixtures/speech_short.wav` | ~3.7 s speech clip used by `repro_truncation.py` |
| `evidence/bug-01-wav-duration-inflation.md` | Detailed walkthrough + raw bodies — Bug 1 |
| `evidence/bug-02-short-audio-500.md` | Detailed walkthrough + raw bodies — Bug 2 |
| `evidence/bug-03-llm-truncation.md` | Detailed walkthrough + raw bodies — Bug 3 |
| `evidence/additional-feedback-and-security.md` | API feedback + security test results |

---

# Bug 1 (primary) — WAV reader decodes trailing chunks as audio

| | |
|---|---|
| **Title** | WAV reader ignores `data`/RIFF sizes and decodes to end-of-stream, counting trailing chunks as audio |
| **Component** | `dictation.assemblyai.com` audio ingestion (WAV reader) |
| **Endpoints** | `POST /v1/transcribe/live`, legacy `POST /v1/transcribe/stream` |
| **Severity** | **High** — wrong reported duration (billing-integrity risk) and spurious rejection of valid files |
| **Reproduce** | `AAI_KEY=<key> python3 repro_duration.py` |
| **Full evidence** | [`evidence/bug-01-wav-duration-inflation.md`](evidence/bug-01-wav-duration-inflation.md) |

## Summary

A WAV file is a container of labelled chunks: `fmt ` (format), `data` (the audio),
and optionally standard extras such as `JUNK` (padding), `LIST`/`INFO` (metadata),
or Broadcast-WAV `bext`. Each chunk declares its own length, so a correct reader
knows exactly where the audio ends. The Dictation API's reader does not use the
`data` chunk's length: once it finds `data`, it decodes everything to the end of
the file as PCM. Any standard chunk placed after `data` is therefore misread as
audio samples.

## Impact

1. **Wrong `audio_duration_ms` (billing-integrity risk).** The reported duration
   includes non-audio trailing bytes. 1000 ms of speech plus a 1 MB `JUNK` chunk
   is reported as 32250 ms. If duration drives the per-hour billing, this inflates
   the billed amount; regardless, the returned field is incorrect.
2. **Spurious `413` on valid files.** A real sub-120 s WAV can be rejected because
   trailing metadata/padding is miscounted toward the 120000 ms limit. A 119000 ms
   clip plus 200 KB of padding returns `413 audio_too_large`.
3. **Transcript-integrity risk.** Trailing bytes that encode speech-like PCM would
   be transcribed as if part of the recording.

These trailing chunks are produced by common, conformant tooling, so ordinary
real-world files — not only synthetic ones — are affected.

## Steps to reproduce

```bash
AAI_KEY=<your_key> python3 repro_duration.py
```

Observed:

```
data only (control)                -> HTTP 200, audio_duration_ms=1000
+ JUNK chunk of 32,000 bytes       -> HTTP 200, audio_duration_ms=2000    (true audio is 1000 ms)
+ JUNK chunk of 1,000,000 bytes    -> HTTP 200, audio_duration_ms=32250   (true audio is 1000 ms)
119000 ms audio + 200 KB JUNK      -> HTTP 413 {"error":"audio exceeds 120000 ms","error_code":"audio_too_large"}
```

## Root cause — where this lives

In the WAV reader, in the step that decides how many PCM bytes to read from the
`data` chunk. Chunks *before* `data` are parsed and skipped correctly, so the
chunk walker exists; the defect is that once `data` is reached the reader consumes
to end-of-stream instead of stopping at `data_offset + data_size` (and instead of
trusting the RIFF size as an outer bound). Confirmed by setting the `data` size
field to `200` while 32000 bytes are present (all 32000 still decoded) and the
RIFF size to `4` (whole stream still decoded). The full confirmation is in the
evidence file.

## Suggested fix

Read exactly `min(declared_data_size, riff_bound)` bytes for the `data` chunk and
skip any trailing chunks (continue the existing chunk walk past `data`). Compute
`audio_duration_ms` and apply the 120 s cap from that bounded sample count.

---

# Bug 2 — HTTP 500 crash on valid short audio

| | |
|---|---|
| **Title** | Dictation API returns `500 "inference failed"` for valid audio of 0 < duration ≤ 16 ms |
| **Component** | `dictation.assemblyai.com` inference service (Universal-3.5 Pro, Dictation) |
| **Endpoints** | `POST /v1/transcribe/live`, legacy `POST /v1/transcribe/stream` |
| **Also affects** | Official Python SDK `assemblyai==1.6.1` (`DictationTranscriber.transcribe_live`) |
| **Severity** | **High** — unhandled 5xx on valid input in a path real clients hit |
| **Reproduce** | `AAI_KEY=<key> python3 repro.py` |
| **Full evidence** | [`evidence/bug-02-short-audio-500.md`](evidence/bug-02-short-audio-500.md) |

## Summary

The service returns `HTTP 500 {"status":500,"title":"Inference Error","detail":"inference failed"}`
for any valid WAV/PCM audio whose duration is greater than 0 and at most 16 ms.
This is a server fault (`5xx`), not a client-input rejection — it is surfaced as
neither `400`, `413`, nor `415`.

## The behavior is non-monotonic

| Audio duration | Result |
|---|---|
| `0 ms` (empty) | `200 OK` (empty transcript) — handled |
| `0 < d ≤ 16 ms` | `500 Inference Error` — crashes |
| `d > 16 ms` | `200 OK` — handled |

Less data (0 ms) succeeds, slightly more (8 ms) crashes, even more (17 ms)
succeeds again. No input-validation policy produces a hole in the middle of a
range; an unguarded code path does.

## Steps to reproduce

```bash
AAI_KEY=<your_key> python3 repro.py        # HTTP path, standard library only
pip install assemblyai && AAI_KEY=<key> python3 repro.py   # also drives the official SDK
```

Observed:

```
    0 samples @ 16000 Hz =   0.00 ms -> HTTP 200
  256 samples @ 16000 Hz =  16.00 ms -> HTTP 500
  257 samples @ 16000 Hz =  16.06 ms -> HTTP 200
  706 samples @ 44100 Hz =  16.01 ms -> HTTP 500
  707 samples @ 44100 Hz =  16.03 ms -> HTTP 200

Python SDK: 256 samples -> DictationError: inference failed;  257 samples -> OK
```

## Root cause — where this lives

The failing boundary is exactly 16 ms and is identical across sample rates
(256 samples at 16 kHz, 706 at 44.1 kHz). A fixed time threshold invariant to
sample rate points at the front-end feature extractor: 16 ms is one analysis
frame. Audio shorter than one frame yields zero frames, the model is invoked on an
empty feature tensor, and that raises inside inference and surfaces as the generic
`500`. The `0 ms` case is already special-cased (returns `200`, empty transcript);
the defect is that `0 < d ≤ 16 ms` falls through that early return into the model.
Pathological-but-valid audio at normal length returns `200`, isolating the cause
to length, not content.

## Suggested fix

After decode, extend the existing empty-audio early return to cover sub-frame
input (`samples * 1000 / sample_rate <= 16` → return an empty transcript, or a
documented `audio_too_short` 4xx), and document the lower bound next to the 120 s
upper bound.

## Impact

Production dictation clients emit sub-16 ms clips routinely — a tap-and-release on
the mic, a push-to-talk key pressed and released in one keystroke, or a VAD
emitting a tiny segment. Each becomes a hard `500` (or an uncaught `DictationError`
in SDK apps), and because it is a `5xx`, default retry logic retries input that can
never succeed.

---

# Bug 3 — Cleaned output silently discarded with an undocumented error

| | |
|---|---|
| **Title** | Custom-`llm_instruction` output silently dropped (`llm_response: null`) with undocumented `llm_error: "truncated"` past a hidden output budget |
| **Component** | `dictation.assemblyai.com` LLM cleanup path |
| **Endpoints** | `POST /v1/transcribe/live` |
| **Severity** | **Medium** — user-facing silent data loss in the core cleanup feature |
| **Reproduce** | `AAI_KEY=<key> python3 repro_truncation.py` |
| **Full evidence** | [`evidence/bug-03-llm-truncation.md`](evidence/bug-03-llm-truncation.md) |

## Summary

The response carries `text` (verbatim) and `llm_response` (the model-cleaned
version, shaped by the `llm_instruction` field). When a custom `llm_instruction`
requests cleaned output longer than a hidden, transcript-length-proportional
budget, the API silently discards the entire cleaned output (`llm_response: null`)
and returns `llm_error: "truncated"` under `HTTP 200`. The verbatim `text` is
still present, so a client that renders `llm_response` shows the user a blank
result. `"truncated"` is not in the documented `llm_error` enum
(`null | "timeout" | "upstream_error"`).

## Steps to reproduce

```bash
AAI_KEY=<your_key> python3 repro_truncation.py
```

Fixture: `fixtures/speech_short.wav` (~3.67 s, "Send the quarterly report to
Margaret by Friday."). Observed:

```
instruction: short formal email (fits budget)                  -> HTTP 200, llm_error=None,        llm_response=213 chars
instruction: 'roughly three short paragraphs' (exceeds budget) -> HTTP 200, llm_error='truncated', llm_response=null

output a sentence exactly  8 times -> HTTP 200, llm_error=None,        llm_response=191 chars
output a sentence exactly 10 times -> HTTP 200, llm_error='truncated', llm_response=null
```

## Why it is a trap

The budget scales with transcript length: the identical forced-length instruction
that fails on this ~8-word transcript (cap ≈ 200 characters) succeeds on a longer
(~130-word) transcript. The cliff is invisible to the caller and depends on how
much was dictated, not on the instruction — so a feature tested with long samples
will silently fail for users who dictate short notes, the most common case. The
most natural use of `llm_instruction` (expand/reformat a short note) is exactly
what exceeds the budget.

## Suggested fix

At minimum, add `"truncated"` to the documented `llm_error` enum and document the
output budget. Better, return the partial output or fall back to `text` with a
flag, so a successful request never renders as a blank.

---

# API feedback (suggestions)

Lower-severity items found during testing; details and captures in
[`evidence/additional-feedback-and-security.md`](evidence/additional-feedback-and-security.md).

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

No vulnerability was found. Tested surfaces and one hardening suggestion are in
[`evidence/additional-feedback-and-security.md`](evidence/additional-feedback-and-security.md).
In summary: transcript retrieval, cross-account access, secret leakage, config
injection, cross-request contamination, and prompt disclosure were all tested
(own accounts only, read-only, no denial-of-service) and showed a solid posture.
The one design suggestion is to offer short-lived or scoped tokens so browser
clients need not embed a long-lived API key.
