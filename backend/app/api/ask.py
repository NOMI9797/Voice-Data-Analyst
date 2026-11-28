from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.nl2sql.generator import Turn
from app.nl2sql.pipeline import ask

router = APIRouter(tags=["ask"])


# One earlier question sent by the browser (for follow-ups).
class TurnIn(BaseModel):
    question: str
    sql: str = ""
    source_id: str = ""


# One option of a 'which database?' question.
class OptionIn(BaseModel):
    source_id: str
    name: str


# The clarifying question the app asked last time.
class PendingIn(BaseModel):
    kind: str = "source"  # source: which database? | intent: what did you mean?
    question: str
    asked: str = ""
    options: list[OptionIn | str] = Field(max_length=5)
    source_id: str | None = None


# What the browser sends: the question, history, pending clarification.
class AskIn(BaseModel):
    question: str = Field(max_length=1000)
    history: list[TurnIn] = Field(default_factory=list, max_length=10)
    source_ids: list[str] | None = None
    pending: PendingIn | None = None  # the "which database?" question we asked last time
    speak: bool = True  # False: template answer instead of an LLM-written one (evaluation runs)


# API /api/ask: runs the whole pipeline (pipeline.ask) for one question.
@router.post("/ask")
async def ask_question(body: AskIn) -> dict:
    """Question in, spoken answer + SQL + rows + chart out."""
    if not body.question.strip():
        raise HTTPException(400, "Empty question")
    history = [Turn(t.question, t.sql, t.source_id) for t in body.history]
    pending = body.pending.model_dump() if body.pending else None
    return await run_in_threadpool(ask, body.question.strip(), history, body.source_ids, pending, body.speak)
