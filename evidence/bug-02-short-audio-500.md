# Evidence — Bug 2: HTTP 500 crash on valid short audio

## What this bug is, in one paragraph

Speech models do not look at audio one sample at a time. They slice the audio
into short overlapping windows called frames — for this model, one frame is 16 ms
of sound — and compute features (a spectrogram) from each frame. The crash
happens when the audio is shorter than a single frame. There are not enough
samples to form even one frame, so the feature step produces nothing, and the
model is then run on empty input and throws. That internal error is returned to
the caller as a generic `HTTP 500 "inference failed"`. The giveaway that this is
a bug rather than a deliberate rule is that an **empty** file (0 ms) is handled
gracefully, a slightly longer file (17 ms) is handled gracefully, but everything
*between* them crashes.

## How to reproduce

```
AAI_KEY=<key> python3 repro.py
```

The script sends a series of valid WAV files of increasing length and prints the
HTTP status for each. If the `assemblyai` package is installed it also drives the
official SDK so you can see the same failure there.

## Walkthrough of what the script shows

The audio is valid in every case — a well-formed mono 16-bit PCM WAV, differing
only in how many samples it contains:

```
    0 samples @ 16000 Hz =   0.00 ms -> HTTP 200    empty file: handled, empty transcript
    1 samples @ 16000 Hz =   0.06 ms -> HTTP 500    <-- crash begins
  128 samples @ 16000 Hz =   8.00 ms -> HTTP 500
  256 samples @ 16000 Hz =  16.00 ms -> HTTP 500    <-- last failing length
  257 samples @ 16000 Hz =  16.06 ms -> HTTP 200    <-- first working length
  300 samples @ 16000 Hz =  18.75 ms -> HTTP 200
```

Read down the column: the result goes 200, then 500, then back to 200. A client
sending **less** data (an empty clip) succeeds; a client sending a little
**more** (8 ms) crashes; a client sending **even more** (17 ms) succeeds again.
No input-validation policy carves a hole in the middle of a range like that — an
unguarded code path does.

### The boundary is exactly one frame, at every sample rate

If the cause were a byte count or a parsing quirk, the failing size would differ
between sample rates. It does not — it is always 16 ms of *time*:

```
  256 samples @ 16000 Hz = 16.00 ms -> HTTP 500     706 samples @ 44100 Hz = 16.01 ms -> HTTP 500
  257 samples @ 16000 Hz = 16.06 ms -> HTTP 200     707 samples @ 44100 Hz = 16.03 ms -> HTTP 200
```

256 samples at 16 kHz and 706 samples at 44.1 kHz are both 16 ms, and both are the
last failing length at their rate. A fixed *time* threshold that is independent of
sample rate is the fingerprint of the analysis frame, which is why we can say with
confidence the crash is in the feature-extraction step, not in request parsing.

### It also breaks the official SDK

```
Python SDK (assemblyai==1.6.1):
  256 samples ( 16.00 ms) -> DictationError: inference failed
  257 samples ( 16.06 ms) -> OK text=''
```

So this is not an artifact of hand-built HTTP requests. A developer using
AssemblyAI's own SDK gets an unhandled `DictationError` for a short clip instead
of an empty transcript.

## The raw response bodies at the boundary

Failing (256 samples / 16.00 ms):

```json
HTTP 500
{"status":500,"title":"Inference Error","detail":"inference failed"}
```

Working (257 samples / 16.06 ms):

```json
HTTP 200
{"text":"","words":[],"confidence":0.0,"audio_duration_ms":16,"llm_response":null,"llm_error":null}
```

One sample of difference flips a server error into a clean empty transcript.

## How we ruled out the obvious alternative explanation

The natural question is whether the crash is really about length, or about some
property of the signal. We sent pathological but perfectly valid audio at a normal
length of 1 second: full-scale DC (every sample at the maximum value), a Nyquist
square wave (alternating max/min), white noise at full amplitude, and an impulse
train. Every one returned HTTP 200. The feature extractor is robust to extreme
content; it is fragile only to audio shorter than one frame. The cause is length.

## Why it matters to AssemblyAI

16 ms sounds too small to care about, but real dictation clients produce sub-16 ms
clips all the time:

- a user taps the mic button and releases it almost instantly (a false start);
- a push-to-talk key is pressed and released in a single keystroke;
- a client-side voice-activity detector emits a tiny leading or trailing segment.

Each of those becomes a hard `500` (or an uncaught `DictationError` in SDK apps)
instead of a harmless empty result. And because the response is a `5xx`, default
retry logic in clients and SDKs will keep retrying input that can never succeed,
turning one stray frame into repeated, pointless load on the service.

## Suggested fix

Immediately after decoding, extend the handling the empty-file case already gets
to cover sub-frame input:

```python
if samples == 0:
    return ok_empty_transcript()          # already the behavior today
if samples * 1000 / sample_rate <= 16:    # one 16 ms frame
    return ok_empty_transcript()          # treat sub-frame like empty, OR
    # raise BadRequest(error_code="audio_too_short", ...)
```

Then document the lower bound next to the existing 120 s upper bound.
