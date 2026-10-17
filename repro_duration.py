#!/usr/bin/env python3
"""
Repro: the Dictation API's WAV reader ignores the `data` chunk size and the RIFF
size, decoding every byte after the `data` header to end-of-file as PCM audio.

Standard, spec-compliant chunks that follow `data` (JUNK alignment padding,
LIST/INFO metadata, id3, cue, Broadcast-WAV bext/iXML) are therefore counted as
audio. This (a) inflates the reported `audio_duration_ms`, and (b) can push a
genuinely valid <=120 s file over the 120000 ms limit, yielding a spurious 413.

Usage:  AAI_KEY=<your_key> python3 repro_duration.py
Requires: Python 3 standard library only.
"""
import io, os, sys, json, struct, wave, urllib.request, urllib.error

URL = "https://dictation.assemblyai.com/v1/transcribe/live"
KEY = os.environ.get("AAI_KEY")
if not KEY:
    sys.exit("set AAI_KEY env var")


def wav(pcm, sr=16000, trailing=b""):
    """A well-formed mono 16-bit PCM WAV, with optional bytes appended AFTER the
    data chunk (a spec-compliant place for JUNK/LIST/etc.)."""
    fmt = struct.pack("<HHIIHH", 1, 1, sr, sr * 2, 2, 16)
    body = (b"WAVE" + b"fmt " + struct.pack("<I", 16) + fmt
            + b"data" + struct.pack("<I", len(pcm)) + pcm + trailing)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def junk(n):
    """A standard JUNK padding chunk of n zero bytes (header is self-describing;
    a conformant reader skips it)."""
    return b"JUNK" + struct.pack("<I", n) + b"\x00" * n


def call(audio):
    boundary = "----dictbug"
    parts = [("config", None, "application/json", b"{}"),
             ("audio", "a.wav", "audio/wav", audio)]
    body = b""
    for name, filename, ctype, data in parts:
        body += f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"'.encode()
        if filename:
            body += f'; filename="{filename}"'.encode()
        body += f"\r\nContent-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": KEY,
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = urllib.request.urlopen(req, timeout=120)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


one_sec = b"\x00\x00" * 16000  # exactly 1000 ms of 16 kHz mono PCM

print("== 1000 ms of real PCM, with trailing chunks appended after `data` ==")
for label, trailing in [
    ("data only (control)",            b""),
    ("+ JUNK chunk of 32,000 bytes",   junk(32000)),
    ("+ JUNK chunk of 1,000,000 bytes", junk(1_000_000)),
]:
    code, body = call(wav(one_sec, trailing=trailing))
    dur = body.get("audio_duration_ms")
    note = ""
    if code == 200 and dur != 1000:
        note = f"  <-- WRONG: true audio is 1000 ms, reported {dur} ms"
    print(f"{label:34s} -> HTTP {code}, audio_duration_ms={dur}{note}")

print("\n== spurious 413: a valid 119,000 ms clip + 200 KB of trailing JUNK ==")
long_pcm = b"\x00\x00" * (119000 * 16)  # 119,000 ms, genuinely under the 120 s cap
code, body = call(wav(long_pcm, trailing=junk(200_000)))
print(f"119000 ms audio + 200 KB JUNK -> HTTP {code}: {body}")
print("(true audio is 119000 ms < 120000 ms; rejection is caused by miscounting"
      " the 200 KB padding as ~6250 ms of extra audio)")
