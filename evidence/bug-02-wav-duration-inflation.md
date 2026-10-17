# Evidence — Bug 2: the WAV reader counts trailing chunks as audio

## What this bug is, in one paragraph

A WAV file is not just a block of audio samples. It is a container: a short
`RIFF` header, then a sequence of labelled "chunks". The `fmt ` chunk describes
the format (sample rate, bit depth), the `data` chunk holds the actual PCM audio,
and a file may carry other standard chunks such as `JUNK` (zero-byte padding used
to align the audio to a disk boundary), `LIST`/`INFO` (metadata like artist or
software name), `id3`, `cue`, or the Broadcast-WAV `bext` chunk. Each chunk begins
with its own 4-byte name and a 4-byte length, so a correct reader always knows
exactly where a chunk ends and the next begins. **The Dictation API's reader does
not use the `data` chunk's length.** Once it finds the `data` chunk it treats
everything from there to the end of the file as audio samples. Any chunk that
happens to sit *after* `data` is therefore misread as sound.

## How to reproduce

```
AAI_KEY=<key> python3 repro_duration.py
```

The script builds a normal mono 16 kHz WAV and then appends one extra, perfectly
legal chunk after the `data` chunk, and compares what the API reports.

## Walkthrough of what the script shows

### Step 1 — a clean file, to establish the baseline

We send exactly 1000 ms of 16 kHz audio (16,000 samples), with no trailing
chunks. The API reports the duration correctly:

```json
{"audio_duration_ms":1000, "text":"", ...}
```

`audio_duration_ms = 1000` is right. This proves the request shape is valid and
the reader handles an ordinary file correctly.

### Step 2 — the same audio, plus a 32 KB padding chunk

We send the **identical** 1000 ms of audio, but append a `JUNK` chunk of 32,000
zero bytes after `data`. `JUNK` is padding — it is defined by the WAV spec to be
ignored. A correct reader would still report 1000 ms. Instead:

```json
{"audio_duration_ms":2000, ...}
```

The reported duration **doubled to 2000 ms**. The 32,000 padding bytes were read
as 16,000 extra samples (32,000 bytes / 2 bytes-per-sample) = 1000 ms of phantom
audio, added on top of the real 1000 ms. The padding was counted as sound.

### Step 3 — the same audio, plus a 1 MB padding chunk

To show the effect scales linearly, we append 1,000,000 bytes of `JUNK` instead:

```json
{"audio_duration_ms":32250, ...}
```

1 second of real speech is now reported as **32.25 seconds**. The formula is
exact: 1,000,000 bytes / 2 / 16,000 samples-per-second = 31.25 s of phantom
audio, plus the real 1 s. An attacker — or simply a file from a verbose encoder —
controls the reported duration almost arbitrarily.

### Step 4 — a legitimate file is wrongly rejected

This is the harm that hits honest users. We send 119,000 ms of real audio (safely
under the 120,000 ms limit) with 200 KB of trailing `JUNK`:

```json
{"error":"audio exceeds 120000 ms","error_code":"audio_too_large"}
```

HTTP 413. The true audio is 119 s and well within the limit, but the 200 KB of
padding was miscounted as ~6.25 s of extra audio, pushing the total over 120 s.
A valid file is refused because of metadata it is allowed to contain.

## The raw before/after bodies

Control (1000 ms, `data` chunk only):

```json
{"text":"","audio_duration_ms":1000,"session_id":"b69b2f7d-...","llm_error":null}
```

Same audio + 32 KB `JUNK` after `data`:

```json
{"text":"","audio_duration_ms":2000,"session_id":"ccb1a4ff-...","llm_error":null}
```

Only the trailing bytes changed; the audio is byte-for-byte identical. The
duration still doubled.

## How we confirmed the mechanism (not just the symptom)

The reader ignores the two fields that are supposed to bound the audio:

- Setting the `data` chunk's declared size to `200` while 32,000 bytes are
  actually present — the reader still consumed all 32,000 bytes. So it does not
  trust the `data` size.
- Setting the outer `RIFF` size to `4` — the reader still consumed the whole
  stream. So it does not trust the RIFF size either.

It parses chunks that appear *before* `data` correctly (a `LIST` or `fact` chunk
placed before `data` is skipped properly). The defect is strictly about chunks
that follow `data`: the reader stops walking the chunk list and reads to EOF.

A real-world confirmation on the provided `hello.wav`: rebuilding that genuine
speech file with a 4 KB `LIST/INFO` metadata chunk after `data` changed the
reported duration from 9817 ms to 9942 ms (+125 ms), while the transcript came
back identical. So this is not a synthetic-only problem — an ordinary tagged file
already reports the wrong duration.

## Why it matters to AssemblyAI

1. **Billing integrity.** List pricing is per hour of processed audio. If
   `audio_duration_ms` is what gets metered, a file's billed duration can be
   inflated far beyond the real audio — intentionally or just by an encoder that
   pads. Even if billing is measured elsewhere, the field returned to the customer
   is wrong.
2. **Rejecting valid input.** Users whose files carry normal metadata or padding
   (very common from professional and broadcast tooling) can get spurious 413s on
   recordings that are actually within limits.
3. **Transcript integrity.** If trailing bytes ever encode speech-like PCM, they
   would be transcribed as though they were part of the recording.

## Suggested fix

When reading the `data` chunk, read exactly `min(declared_data_size, riff_bound)`
bytes, and continue the existing chunk walk past `data` instead of treating the
remainder of the file as samples. Derive `audio_duration_ms` and apply the 120 s
cap from that bounded sample count.
