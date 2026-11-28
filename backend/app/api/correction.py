from dataclasses import asdict

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.correction.corrector import correct, get_vocabulary, whisper_hint

router = APIRouter(tags=["correction"])


# The text to correct.
class TextIn(BaseModel):
    text: str


# API /api/correct: fixes misheard names in a transcript.
@router.post("/correct")
async def correct_text(body: TextIn) -> dict:
    """Fix misheard database names in a transcript (no LLM involved)."""
    return asdict(await run_in_threadpool(correct, body.text))


# API: how many real values the corrector knows.
@router.get("/correct/vocabulary")
async def vocabulary_stats() -> dict:
    v = await run_in_threadpool(get_vocabulary)
    return {"entries": len(v.entries), "built_ms": v.built_ms,
            "examples": [e.value for e in v.entries[:20]]}


# API: list of database names for Whisper's prompt (only used by the evaluation).
@router.get("/correct/hint")
async def stt_hint() -> dict:
    """Real database names to pass to speech-to-text as a prompt (biases Whisper toward them)."""
    return {"hint": await run_in_threadpool(whisper_hint)}
