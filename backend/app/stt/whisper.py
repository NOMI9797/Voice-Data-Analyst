"""Local speech recognition with faster-whisper (CTranslate2, int8 on CPU).

Runs fully offline after the first model download (cached in /models/whisper).
`hint` is passed as Whisper's initial prompt: a list of real database words
(product names, cities...) that biases recognition toward them.
"""

from __future__ import annotations

import io
import re
import os
import logging
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from app.config import get_settings

log = logging.getLogger(__name__)

# All Whisper work runs on ONE dedicated thread. CTranslate2 uses OpenMP, which creates a
# separate busy-waiting thread team per calling thread; calling it from FastAPI's thread
# pool (a different thread per request) piles teams up and made inference ~3x slower.
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="whisper")

MODEL_DIR = "/models/whisper"
BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
_model = None
_lock = threading.Lock()


# Loads the local Whisper model (once).
def _get_model():
    global _model
    with _lock:
        if _model is None:
            from faster_whisper import WhisperModel

            name = get_settings().whisper_model
            t0 = time.perf_counter()
            _model = WhisperModel(name, device="cpu", compute_type="int8", download_root=MODEL_DIR,
                                  cpu_threads=4)
            log.info("whisper %s loaded in %.1fs", name, time.perf_counter() - t0)
        return _model


# Loads Whisper at startup so the first question is fast.
def warm_up() -> None:
    try:
        m = _get_model()
        list(m.transcribe(np.zeros(16000, dtype=np.float32), language="en")[0])
    except Exception:
        log.exception("whisper warm-up failed")


# Reads the browser's WAV audio into numbers for Whisper.
def _decode(audio: bytes) -> np.ndarray | bytes:
    """16 kHz mono WAV from the browser -> float32 array (fast path, no ffmpeg).
    Anything else (webm/ogg/mp3...) is handed to faster-whisper's own decoder."""
    if audio[:4] == b"RIFF":
        with wave.open(io.BytesIO(audio)) as w:
            if w.getframerate() == 16000 and w.getnchannels() == 1 and w.getsampwidth() == 2:
                pcm = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16)
                return pcm.astype(np.float32) / 32768.0
    return audio


# Detects Whisper looping on a few words instead of transcribing.
def looks_looped(text: str, compression_ratios: list[float]) -> bool:
    """Whisper sometimes loops on a few words instead of transcribing ("bage, tupa, bage,
    tupa, bage ..."), on noise or when prompted with a word list. Signs: Whisper's own
    compression ratio is high (its standard check uses 2.4), or the text reuses very few
    different words: a loop has under half distinct words, while a real question, even
    "how many orders, how many customers, how many sellers", has more."""
    if any(r > 2.4 for r in compression_ratios):
        return True
    words = re.findall(r"[a-z0-9']+", text.lower())
    return len(words) >= 6 and len(set(words)) / len(words) < 0.5


# MAIN: turns spoken audio into text with Whisper (local).
def transcribe(audio: bytes, hint: str = "") -> dict:
    t0 = time.perf_counter()
    model = _get_model()
    data = _decode(audio)
    src = data if isinstance(data, np.ndarray) else io.BytesIO(data)
    segments, info = model.transcribe(
        src,
        language="en",
        beam_size=BEAM_SIZE,
        # The browser already trims silence (client-side VAD), so Whisper's own Silero VAD
        # is skipped: it adds an extra ONNX model run that competes for the CPU.
        vad_filter=False,
        initial_prompt=hint[:600] or None,
        condition_on_previous_text=False,
    )
    segs = list(segments)
    text = " ".join(s.text.strip() for s in segs).strip()
    # word-level confidence proxy: average log-prob of segments
    avg_logprob = float(np.mean([s.avg_logprob for s in segs])) if segs else None
    return {
        "text": text,
        "duration_s": round(info.duration, 2),
        "avg_logprob": None if avg_logprob is None else round(avg_logprob, 3),
        "no_speech": bool(segs) and all(s.no_speech_prob > 0.6 for s in segs),
        "looped": looks_looped(text, [s.compression_ratio for s in segs]),
        "ms": round((time.perf_counter() - t0) * 1000, 1),
    }
