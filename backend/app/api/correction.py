from dataclasses import asdict

from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.correction.corrector import correct, get_vocabulary

router = APIRouter(tags=["correction"])


class TextIn(BaseModel):
    text: str


@router.post("/correct")
async def correct_text(body: TextIn) -> dict:
    """Fix misheard database names in a transcript (no LLM involved)."""
    return asdict(await run_in_threadpool(correct, body.text))


@router.get("/correct/vocabulary")
async def vocabulary_stats() -> dict:
    v = await run_in_threadpool(get_vocabulary)
    return {"entries": len(v.entries), "built_ms": v.built_ms,
            "examples": [e.value for e in v.entries[:20]]}
