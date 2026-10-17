# AssemblyAI Dictation API — Hackathon Submission (Hack into Speech)

Three tracks in one repo: **Bug Report**, **API feedback**, **Security review**.
All findings are reproduced against the live API on two independent keys; see `evidence/`.

### Where everything is

| Path | What it is |
|---|---|
| `README.md` | This report — the primary bug (ticket), API feedback, and security review |
| [`repro.py`](repro.py) | One-command reproduction of the primary bug (stdlib HTTP + optional SDK path) |
| [`evidence/repro_output.txt`](evidence/repro_output.txt) | Captured run: the non-monotonic 200/500/200 table |
| [`evidence/raw_responses.txt`](evidence/raw_responses.txt) | Raw `500` vs `200` bodies at the one-sample boundary |
| [`evidence/sdk_repro.txt`](evidence/sdk_repro.txt) | Same bug through the official Python SDK |
| [`evidence/secondary_findings.txt`](evidence/secondary_findings.txt) | API-feedback details + security-test results |

**Quick start:**

```bash
AAI_KEY=<your_key> python3 repro.py        # HTTP path, standard library only
```

The primary bug's root cause and a suggested code fix are in **§5–§6** of the Bug Report below.

---

# 🔴 Bug Report (primary) — HTTP 500 crash on valid short audio

| | |
|---|---|
| **Title** | Dictation API returns `500 "inference failed"` for valid audio of 0 < duration ≤ 16 ms |
| **Component** | `dictation.assemblyai.com` inference service (Universal-3.5 Pro, Dictation) |
| **Endpoints** | `POST /v1/transcribe/live` and legacy `POST /v1/transcribe/stream` (same handler) |
| **Also affects** | Official Python SDK `assemblyai==1.6.1` (`DictationTranscriber.transcribe_live`) |
| **Severity** | **High** — unhandled 5xx on valid input, in a code path real clients hit |
| **Status** | Reproduced on 2 independent keys, both endpoints, HTTP + SDK |

## 1. Summary

The service returns **HTTP 500**

```json
{"status":500,"title":"Inference Error","detail":"inference failed"}
```

for **any valid WAV/PCM audio whose duration is greater than 0 and at most 16 ms.** The response is
a server fault (`5xx`), not a client-input rejection — it is surfaced as neither `400`, `413`, nor
`415`, so by the API's own contract the client sent valid input and the server failed on it.

## 2. The behavior is non-monotonic — this is the proof it is a defect

| Audio duration       | Result |
|----------------------|--------|
| `0 ms` (empty)       | **200 OK** (empty transcript) — handled |
| `0 < d ≤ 16 ms`      | **500 Inference Error** — crashes |
| `d > 16 ms`          | **200 OK** — handled |

A client sending **less** data (0 ms) succeeds, a client sending **slightly more** (8 ms) gets a
500, and a client sending **even more** (17 ms) succeeds again. No input-validation policy produces
a hole in the middle of the range; an unguarded code path does.

## 3. Steps to reproduce

```bash
AAI_KEY=<your_key> python3 repro.py        # HTTP path, standard library only
pip install assemblyai && AAI_KEY=<key> python3 repro.py   # also drives the official SDK
```

Observed (`evidence/repro_output.txt`):

```
== Raw HTTP ==
    0 samples @ 16000 Hz =   0.00 ms -> HTTP 200
    1 samples @ 16000 Hz =   0.06 ms -> HTTP 500  <-- HTTP 500 on VALID input
  128 samples @ 16000 Hz =   8.00 ms -> HTTP 500  <-- HTTP 500 on VALID input
  256 samples @ 16000 Hz =  16.00 ms -> HTTP 500  <-- HTTP 500 on VALID input
  257 samples @ 16000 Hz =  16.06 ms -> HTTP 200
  300 samples @ 16000 Hz =  18.75 ms -> HTTP 200
  706 samples @ 44100 Hz =  16.01 ms -> HTTP 500  <-- HTTP 500 on VALID input
  707 samples @ 44100 Hz =  16.03 ms -> HTTP 200

== Official Python SDK (assemblyai==1.6.1) ==
  256 samples ( 16.00 ms) -> DictationError: inference failed
  257 samples ( 16.06 ms) -> OK text=''
```

## 4. Actual vs. expected

- **Actual:** `500 "inference failed"` (HTTP) / `DictationError: inference failed` (SDK).
- **Expected:** the same graceful handling the `0 ms` case already gets — **`200` with an empty
  transcript** — or, if sub-frame audio must be rejected, a deterministic **`400`/`422` with a
  documented `error_code`** (e.g. `audio_too_short`), mirroring the existing `413 audio_too_large`.

## 5. Root cause — where this lives

The failing boundary is **exactly 16 ms, and it is identical across sample rates**:

```
256 samples @ 16000 Hz = 16.00 ms  -> 500      706 samples @ 44100 Hz = 16.01 ms -> 500
257 samples @ 16000 Hz = 16.06 ms  -> 200      707 samples @ 44100 Hz = 16.03 ms -> 200
```

A fixed **time** threshold that is invariant to sample rate points at the **front-end feature
extractor**, not at request parsing: 16 ms is one analysis window (a standard 16 ms / 256-sample-at-
16 kHz STFT/mel frame). When the decoded audio is shorter than a single frame, the framing step
produces **zero complete frames**, and the downstream model is invoked on an empty feature tensor,
which raises inside inference and bubbles up as the generic `500 "inference failed"`.

This localizes the defect to the **request handler → audio-decode → framing → model-invoke** path,
specifically **the absence of a minimum-length guard between decode and framing.** The `0 ms` case
is already special-cased earlier (it returns `200` with an empty transcript and never reaches the
model); the bug is that `0 < d ≤ 16 ms` falls *through* that early return and *into* the model with
less than one frame. Evidence that it is length, not signal: pathological-but-valid audio at normal
length (full-scale DC, Nyquist square wave, white noise, impulse trains) all return `200`
(`evidence/secondary_findings.txt`), so the extractor is robust to content and fragile only to
sub-frame length.

## 6. Suggested fix

In the handler, immediately after decode and before framing:

```python
# samples: int, sr: int (already known at this point)
if samples == 0:
    return ok_empty_transcript()          # existing behavior
if samples * 1000 / sr <= FRAME_MS:       # FRAME_MS == 16
    return ok_empty_transcript()          # treat sub-frame like empty, OR:
    # raise BadRequest(error_code="audio_too_short", detail=f"audio shorter than one {FRAME_MS}ms frame")
```

Either collapse sub-frame audio into the existing empty-transcript path, or reject it with a
documented `audio_too_short` 4xx. Then document the lower bound next to the existing 120 s upper
bound. (The `0 ms → 200` path already exists, so extending it to `≤ 16 ms` is the lower-risk fix.)

## 7. Impact

16 ms is below one frame, but production dictation clients emit sub-16 ms clips routinely:

- a user taps and releases the mic almost instantly (false start / fat-finger tap);
- a push-to-talk key is pressed and released in a single keystroke;
- a client-side VAD emits a tiny leading/trailing voiced segment.

Each becomes a hard `500` (or an uncaught `DictationError` in SDK apps) instead of a harmless empty
result — and because it is a `5xx`, default client/SDK retry logic will retry input that can never
succeed, turning one bad frame into repeated load.

---

# 🟡 API feedback (suggestions)

Lower-severity DX issues found during testing (details + captures in `evidence/secondary_findings.txt`):

1. **`llm_instruction` length limit not enforced.** Docs state a 2048-char max; the server accepts
   ≥ 20,000 chars (only a generic `config part too large` triggers near ~100 KB). Sibling fields are
   strictly enforced (`stt_prompt` > 6000 → 400, `keyterms_prompt` > 100 → 400), so this is an
   inconsistent/missing validator or an overstated doc limit.
2. **Validation-ordering bug on `sample_rate`.** The 120 s duration guard is computed from the
   client-supplied `sample_rate` *before* that rate is validated against the allowlist, so an invalid
   rate yields a misleading `413 "audio exceeds 120000 ms"` instead of `415 "unsupported sample rate"`
   (`sr=1,n=100 → 415` but `sr=1,n=1000 → 413`). Validate the rate first.
3. **Inconsistent error envelopes.** Two shapes are returned by different layers:
   `{"status","title","detail"}` vs `{"error","error_code"}`. Picking one simplifies client handling.
4. **Misleading message on overstated WAV data-chunk size** — returns `400 "stream ended before a
   complete WAV header arrived"` though the header is in fact complete.
5. **Docs: supported-language count is inconsistent across sources** (hackathon form says 18, launch
   PR says 19; the live API enumerates **32** in its `400` for an invalid `language_codes`).

---

# 🟢 Security review — tested, posture is solid (one design suggestion)

No vulnerability found. Reporting what was tested so the result is verifiable, plus one hardening
suggestion. (Tested only against our own two accounts; read-only; no DoS.)

**Tested, no issue:**
- **Transcript retrieval / data retention:** every plausible `GET .../{session_id}` returns `404` —
  transcripts aren't exposed behind a fetchable URL.
- **Broken access control:** a `session_id` created under key A is `404` from key B and from no key.
- **Secret leakage:** error bodies do not echo the API key; no internal details leaked in errors.
- **Config injection / type confusion:** malformed/hostile config (wrong types, nesting, nulls,
  null-bytes in keyterms) is rejected with clean `400`s, never a crash.

**Design suggestion (not a vuln):** auth is a single long-lived API key sent as the raw
`Authorization` value, and CORS is `access-control-allow-origin: *`. Browser-based dictation apps
therefore have to embed the long-lived key client-side, where it is extractable. Consider offering
**short-lived / scoped tokens** (as several AssemblyAI streaming products do) so web clients don't
ship a permanent key.

---

## Files

```
README.md                      this report
repro.py                       self-contained repro (HTTP + optional SDK)
evidence/repro_output.txt      captured HTTP run (the non-monotonic table)
evidence/raw_responses.txt     raw 500 vs 200 bodies at the one-sample boundary
evidence/sdk_repro.txt         same bug via the official Python SDK
evidence/secondary_findings.txt  API-feedback + security test details
```
