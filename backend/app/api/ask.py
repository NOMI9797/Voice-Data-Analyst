from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel, Field

from app.nl2sql.generator import Turn
from app.nl2sql.pipeline import ask

router = APIRouter(tags=["ask"])


class TurnIn(BaseModel):
    question: str
    sql: str = ""
    source_id: str = ""


class AskIn(BaseModel):
    question: str = Field(max_length=1000)
    history: list[TurnIn] = Field(default_factory=list, max_length=10)
    source_ids: list[str] | None = None


@router.post("/ask")
async def ask_question(body: AskIn) -> dict:
    """Question in, spoken answer + SQL + rows + chart out."""
    if not body.question.strip():
        raise HTTPException(400, "Empty question")
    history = [Turn(t.question, t.sql, t.source_id) for t in body.history]
    return await run_in_threadpool(ask, body.question.strip(), history, body.source_ids)
