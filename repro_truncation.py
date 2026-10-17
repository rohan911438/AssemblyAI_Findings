#!/usr/bin/env python3
"""
Repro: when a custom `llm_instruction` asks for cleaned output longer than a
hidden, transcript-length-proportional budget, the Dictation API silently
discards the ENTIRE cleaned output (`llm_response: null`) and returns an
UNDOCUMENTED `llm_error: "truncated"` under HTTP 200. The verbatim `text` field
is still present, so a client that renders `llm_response` shows a blank result.

The documented `llm_error` enum is: null | "timeout" | "upstream_error".
"truncated" is not in it.

Uses a bundled ~3.7 s speech clip (fixtures/speech_short.wav, transcript:
"Send the quarterly report to Margaret by Friday.") so the result is
deterministic and needs no TTS.

Usage:  AAI_KEY=<your_key> python3 repro_truncation.py
Requires: Python 3 standard library only.
"""
import os, sys, json, urllib.request, urllib.error

URL = "https://dictation.assemblyai.com/v1/transcribe/live"
KEY = os.environ.get("AAI_KEY")
if not KEY:
    sys.exit("set AAI_KEY env var")

FIX = os.path.join(os.path.dirname(__file__), "fixtures", "speech_short.wav")
if not os.path.exists(FIX):
    sys.exit(f"missing fixture: {FIX}")
AUDIO = open(FIX, "rb").read()


def call(config):
    boundary = "----dictbug"
    parts = [("config", None, "application/json", json.dumps(config).encode()),
             ("audio", "a.wav", "audio/wav", AUDIO)]
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
        r = urllib.request.urlopen(req, timeout=90)
        return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def summarize(label, config):
    code, b = call(config)
    resp = b.get("llm_response")
    resp_s = "null" if resp is None else f"{len(resp)} chars"
    err = b.get("llm_error")
    flag = "  <-- output silently discarded" if resp is None and err == "truncated" else ""
    print(f"{label:52s} -> HTTP {code}, llm_error={err!r}, llm_response={resp_s}{flag}")


print("== natural use: dictate a short note, ask the model to expand/format it ==")
summarize("instruction: short formal email (fits budget)",
          {"llm_instruction": "Rewrite the dictated note as a short, polite formal business email."})
summarize("instruction: 'roughly three short paragraphs' (exceeds budget)",
          {"llm_instruction": "Rewrite the dictated note in expanded professional prose, roughly three short paragraphs."})

print("\n== deterministic length cliff: force the model to emit N copies of a line ==")
for n in (5, 6, 7, 8, 10, 15):
    summarize(f"instruction: output a sentence exactly {n} times",
              {"llm_instruction": f'Output "The cat sat on the mat." exactly {n} times.'})
print("(same instruction, same audio: it succeeds up to ~200 chars of output, then"
      " silently returns null with llm_error='truncated')")
