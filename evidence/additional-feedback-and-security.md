# Evidence — API feedback and security review

## API feedback (lower-severity)

### `llm_instruction` length limit not enforced
Docs state a 2048-character maximum. The server accepts values of at least
20,000 characters with HTTP 200; only a generic `config part too large` (400)
triggers near ~100 KB. Sibling fields are strictly enforced:

```
stt_prompt > 6000 chars  -> 400 "String should have at most 6000 characters"
keyterms_prompt > 100    -> 400 "keyterms_prompt exceeds 100 terms (got 101)"
```

### Validation-ordering on `sample_rate`
The 120000 ms duration guard is computed from the client-supplied `sample_rate`
before that rate is validated against the allowlist, so an invalid rate can
produce a misleading `413` instead of `415`:

```
sr=1, n=100   implied   100000 ms (<120000) -> 415 "unsupported sample rate 1"   (correct)
sr=1, n=1000  implied  1000000 ms (>120000) -> 413 "audio exceeds 120000 ms"      (misleading)
sr=7999       implied    ~4000 ms            -> 415 "unsupported sample rate 7999" (correct)
```

### Inconsistent error envelopes
Two shapes are returned by different validation layers:
`{"status","title","detail"}` and `{"error","error_code"}`.

### Misleading message on overstated WAV `data` size
A complete, valid WAV header whose `data` chunk size is overstated returns
`400 "stream ended before a complete WAV header arrived"`, though the header is
in fact complete.

### Documentation: supported-language count is inconsistent across sources
The hackathon form says 18, launch coverage says 19; the live API enumerates 32
codes in its `400` for an invalid `language_codes` value.

## Security review (tested, no vulnerability found)

Tested only against our own two accounts, read-only, with modest request volume
and no denial-of-service.

- **Transcript retrieval / data retention:** every plausible
  `GET .../{session_id}` returns `404` — transcripts are not exposed behind a
  fetchable URL.
- **Broken access control:** a `session_id` created under key A returns `404`
  from key B and from no key.
- **Secret leakage:** error bodies do not echo the API key; no internal details
  are leaked in errors.
- **Config injection / type confusion:** malformed or hostile config (wrong
  types, nesting, nulls, null bytes in keyterms) is rejected with clean `400`s,
  never a crash.
- **Cross-request contamination:** 18 simultaneous distinct requests on one key,
  each carrying a unique sentinel, showed zero leakage between responses.
- **Prompt disclosure:** a benign instruction asking for any preceding
  instructions returned only the instruction text itself; no hidden system
  prompt or internal config was revealed.

### Design suggestion (not a vulnerability)
Authentication is a single long-lived API key sent as the raw `Authorization`
value, and CORS is `access-control-allow-origin: *`. Browser-based dictation
apps therefore have to embed the long-lived key client-side, where it is
extractable. Consider offering short-lived or scoped tokens so web clients do
not ship a permanent key.

## Surfaces tested and found robust (no bug)

- Config type-fuzzing (wrong JSON types, nesting, nulls) — clean 400s.
- Pathological-but-valid audio (DC, Nyquist square, white noise, impulses) —
  200.
- 15 concurrent valid requests — all 200.
- Trickle / slow upload — 200, genuinely streams.
- 8/24/32-bit and compressed audio — clean 415.
- WAV chunk structure: multiple `data` chunks (summed), pre-`data` chunks
  (skipped), `WAVE_FORMAT_EXTENSIBLE`, non-PCM format tags (clean 415).
- Lower crash zone mapped at every allowed sample rate — single zone only, no
  shift or second zone; stereo threshold is per-channel.
- Upper boundary 119999/120000/120001 ms — consistent `floor(dur) > 120000 ->
  413`, no off-by-one crash.
- Data integrity of default cleanup: a 70 s, 10-distinct-fact dictation was
  cleaned faithfully (all numbers, names, units, and a negation preserved);
  self-corrections resolved to the corrected value.
