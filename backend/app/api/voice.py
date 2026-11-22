import asyncio

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.stt import whisper
from app.tts import piper_tts

router = APIRouter(tags=["voice"])

MAX_AUDIO_BYTES = 20 * 1024 * 1024  # ~10 minutes of 16 kHz WAV


@router.post("/stt")
async def speech_to_text(audio: UploadFile = File(...), hint: str = Form("")) -> dict:
    data = await audio.read()
    if not data:
        raise HTTPException(400, "Empty audio")
    if len(data) > MAX_AUDIO_BYTES:
        raise HTTPException(413, "Audio too long")
    try:
        return await asyncio.wrap_future(whisper.EXECUTOR.submit(whisper.transcribe, data, hint))
    except Exception as exc:
        raise HTTPException(400, f"Could not transcribe audio: {exc}") from exc


class TTSIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    speed: float = Field(1.0, ge=0.5, le=2.0)


@router.post("/tts")
async def text_to_speech(body: TTSIn) -> Response:
    wav, ms = await run_in_threadpool(piper_tts.synthesize, body.text, body.speed)
    return Response(content=wav, media_type="audio/wav", headers={"X-TTS-ms": str(ms)})
