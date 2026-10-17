# Evidence — Bug 2: WAV reader counts trailing chunks as audio

Reproduction command:

```
AAI_KEY=<key> python3 repro_duration.py
```

Captured run:

```
== 1000 ms of real PCM, with trailing chunks appended after `data` ==
data only (control)                -> HTTP 200, audio_duration_ms=1000
+ JUNK chunk of 32,000 bytes       -> HTTP 200, audio_duration_ms=2000   <-- WRONG: true audio is 1000 ms
+ JUNK chunk of 1,000,000 bytes    -> HTTP 200, audio_duration_ms=32250  <-- WRONG: true audio is 1000 ms

== spurious 413: a valid 119,000 ms clip + 200 KB of trailing JUNK ==
119000 ms audio + 200 KB JUNK -> HTTP 413: {'error': 'audio exceeds 120000 ms', 'error_code': 'audio_too_large'}
```

## Raw response bodies

Control — 1000 ms of audio, `data` chunk only:

```json
{"text":"","words":[],"confidence":0.0,"audio_duration_ms":1000,"session_id":"b69b2f7d-...","llm_response":null,"llm_error":null}
```

Same 1000 ms of audio with a 32,000-byte `JUNK` chunk appended after `data`:

```json
{"text":"","words":[],"confidence":0.0,"audio_duration_ms":2000,"session_id":"ccb1a4ff-...","llm_response":null,"llm_error":null}
```

A genuinely valid 119,000 ms clip (under the 120 s cap) plus 200 KB of trailing
`JUNK` padding:

```json
{"error":"audio exceeds 120000 ms","error_code":"audio_too_large"}
```

## Mechanism

The reader locates the `fmt ` and `data` chunk headers correctly and correctly
skips chunks that appear *before* `data`. The defect is that it then reads from
the first byte of the `data` payload to end-of-stream as 16-bit PCM — it honors
neither the `data` chunk's declared size nor the RIFF size. Verified:

- `data` size field set to `200` while `32000` bytes are present -> all `32000`
  bytes still decoded.
- RIFF size field set to `4` -> entire stream still decoded.

Inflation is exactly `trailing_bytes / 2 / sample_rate` seconds of phantom audio
(e.g. 1,000,000 bytes / 2 / 16000 = 31.25 s added on top of the real 1 s).

The 120000 ms cap is applied to this miscomputed sample count
(`floor(samples / rate * 1000) > 120000 -> 413`), which is why a real sub-120 s
file can be rejected.

## Why these trailing chunks are legitimate

`JUNK` (alignment padding), `LIST`/`INFO` (metadata tags), `id3`, `cue`, `PEAK`,
and Broadcast-WAV `bext`/`iXML`/`axml` are all spec-compliant chunks that
conformant tools place after `data`. A real-world example confirmed on the
provided `hello.wav`: rebuilding it with a 4 KB `LIST/INFO` chunk after `data`
moved the reported duration from 9817 ms to 9942 ms (+125 ms) while the
transcript was unchanged.
