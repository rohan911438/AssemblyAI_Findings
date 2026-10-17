# Evidence — Bug 1: HTTP 500 on valid short audio (= 16 ms)

Reproduction command:

```
AAI_KEY=<key> python3 repro.py
```

Captured HTTP run:

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
```

Same bug through the official Python SDK (`assemblyai==1.6.1`):

```
== Official Python SDK ==
  256 samples ( 16.00 ms) -> DictationError: inference failed
  257 samples ( 16.06 ms) -> OK text=''
```

Raw response bodies at the one-sample boundary:

```
256 samples / 16.00 ms:
HTTP 500
{"status":500,"title":"Inference Error","detail":"inference failed"}

257 samples / 16.06 ms:
HTTP 200
{"text":"","words":[],"confidence":0.0,"audio_duration_ms":16,"session_id":"...","llm_response":null,"llm_error":null}
```

Scope confirmed: both API keys, both `/live` and `/stream`, silence and real
truncated speech, raw HTTP and the Python SDK. Pathological-but-valid audio
(full-scale DC, Nyquist square wave, white noise, impulse trains) at a normal
length of 1 s all return HTTP 200, isolating the cause to audio *length* rather
than signal content.
