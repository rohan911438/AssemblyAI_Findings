#!/usr/bin/env python3
"""
Repro: AssemblyAI Dictation API returns HTTP 500 ("inference failed") for any
valid audio of 0 < duration <= 16 ms, while BOTH shorter (0 ms / empty) and
longer (> 16 ms) audio succeed with HTTP 200.

Usage:  AAI_KEY=<your_key> python3 repro.py
Requires: Python 3 standard library only (no third-party packages).
"""
import io, os, sys, json, wave, urllib.request, urllib.error

URL = "https://dictation.assemblyai.com/v1/transcribe/live"
KEY = os.environ.get("AAI_KEY")
if not KEY:
    sys.exit("set AAI_KEY env var")

def wav_bytes(n_samples, sr=16000):
    """A well-formed mono 16-bit PCM WAV of n_samples of silence."""
    b = io.BytesIO()
    w = wave.open(b, "wb"); w.setnchannels(1); w.setsampwidth(2); w.setframerate(sr)
    w.writeframes(b"\x00\x00" * n_samples); w.close()
    return b.getvalue()

def call(audio):
    boundary = "----dictbug"
    body = b""
    for name, filename, ctype, data in [
        ("config", None, "application/json", b"{}"),
        ("audio", "a.wav", "audio/wav", audio),
    ]:
        body += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"".encode()
        if filename: body += f"; filename=\"{filename}\"".encode()
        body += f"\r\nContent-Type: {ctype}\r\n\r\n".encode() + data + b"\r\n"
    body += f"--{boundary}--\r\n".encode()
    req = urllib.request.Request(URL, data=body, headers={
        "Authorization": KEY,
        "Content-Type": f"multipart/form-data; boundary={boundary}"})
    try:
        r = urllib.request.urlopen(req, timeout=90)
        return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

print("== Raw HTTP ==")
for n, sr in [(0,16000),(1,16000),(128,16000),(256,16000),(257,16000),(300,16000),(706,44100),(707,44100)]:
    ms = n * 1000 / sr
    code, body = call(wav_bytes(n, sr))
    flag = "  <-- HTTP 500 on VALID input" if code == 500 else ""
    print(f"{n:>5} samples @ {sr} Hz = {ms:6.2f} ms -> HTTP {code}{flag}")

# Optional: same bug through the official SDK, if installed (pip install assemblyai)
try:
    import assemblyai as aai
    aai.settings.api_key = KEY
    tr = aai.DictationTranscriber()
    print(f"\n== Official Python SDK (assemblyai=={aai.__version__}) ==")
    for n in (256, 257):
        try:
            r = tr.transcribe_live(wav_bytes(n))
            print(f"{n:>5} samples ({n*1000/16000:6.2f} ms) -> OK text={r.text!r}")
        except Exception as e:
            print(f"{n:>5} samples ({n*1000/16000:6.2f} ms) -> {type(e).__name__}: {e}")
    tr.close()
except ImportError:
    pass
