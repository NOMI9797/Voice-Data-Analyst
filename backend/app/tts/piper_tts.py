"""Local text-to-speech with Piper (ONNX, runs on CPU, ~10x faster than real time).

The voice model is downloaded once from the public rhasspy/piper-voices repo
into /models/piper and then used offline.
"""

from __future__ import annotations

import io
import logging
import re
import threading
import time
import urllib.request
import wave
from pathlib import Path

from app.config import get_settings

log = logging.getLogger(__name__)

MODEL_DIR = Path("/models/piper")
VOICES_URL = "https://huggingface.co/rhasspy/piper-voices/resolve/main"

_voice = None
_lock = threading.Lock()


# Downloads the Piper voice the first time it is needed.
def _voice_files(name: str) -> tuple[Path, Path]:
    """Download e.g. en_US-lessac-medium(.onnx/.onnx.json) if missing."""
    locale, speaker, quality = name.split("-")
    lang = locale.split("_")[0]
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    onnx, cfg = MODEL_DIR / f"{name}.onnx", MODEL_DIR / f"{name}.onnx.json"
    for path in (onnx, cfg):
        if not path.exists():
            url = f"{VOICES_URL}/{lang}/{locale}/{speaker}/{quality}/{path.name}"
            log.info("downloading piper voice %s", url)
            tmp = path.with_suffix(path.suffix + ".part")
            urllib.request.urlretrieve(url, tmp)
            tmp.replace(path)
    return onnx, cfg


# Loads the Piper voice (once).
def _get_voice():
    global _voice
    with _lock:
        if _voice is None:
            from piper import PiperVoice

            onnx, cfg = _voice_files(get_settings().piper_voice)
            t0 = time.perf_counter()
            _voice = PiperVoice.load(str(onnx), config_path=str(cfg))
            log.info("piper voice loaded in %.1fs", time.perf_counter() - t0)
        return _voice


# Loads Piper at startup so the first answer is fast.
def warm_up() -> None:
    try:
        synthesize("Ready.")
    except Exception:
        log.exception("piper warm-up failed")


# Makes numbers and symbols read naturally.
def _speakable(text: str) -> str:
    """Make numbers and symbols read naturally."""
    t = text.replace("%", " percent").replace("&", " and ")
    t = re.sub(r"R\$\s?", "", t)  # currency symbols are said by the explanation text itself
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)  # 1,234 -> 1234 (Piper reads it as one number)
    return re.sub(r"\s+", " ", t).strip()


# MAIN: turns text into a spoken WAV file with Piper (local).
def synthesize(text: str, speed: float = 1.0) -> tuple[bytes, float]:
    """Return (WAV bytes, milliseconds taken)."""
    from piper import SynthesisConfig

    t0 = time.perf_counter()
    voice = _get_voice()
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        voice.synthesize_wav(_speakable(text), wf, syn_config=SynthesisConfig(length_scale=1.0 / speed))
    return buf.getvalue(), round((time.perf_counter() - t0) * 1000, 1)


# Splits an answer into sentences for faster playback.
def split_sentences(text: str) -> list[str]:
    """Split for streaming playback: the first sentence can play while the rest is synthesized."""
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text.strip())
    return [p for p in parts if p]
