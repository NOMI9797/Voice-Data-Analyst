from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.config import get_settings
from app.nl2sql.pipeline import allowed_tables as profiled_tables
from app.safety.executor import QueryError, run_query
from app.safety.guard import check_sql
from app.safety.intent import screen_question
from app.sources.registry import SourceError, get_registry
from app.viz.chart import choose_chart

router = APIRouter(tags=["query"])


class SQLIn(BaseModel):
    source_id: str
    sql: str


def allowed_tables(source_id: str) -> dict[str, list[str]]:
    tables = profiled_tables(source_id)
    if tables is None:
        raise HTTPException(409, "This source has not been profiled yet. Try again in a moment.")
    return tables


@router.post("/sql/run")
async def run_sql(body: SQLIn) -> dict:
    """Run SQL through the safety guard and the read-only executor."""
    try:
        source = get_registry().get(body.source_id)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not source.active:
        raise HTTPException(409, f"The {source.name} data is switched off. Turn it on under Your data to query it.")
    guard = check_sql(body.sql, source.dialect, allowed_tables(body.source_id),
                      row_limit=get_settings().query_row_limit)
    if not guard.ok:
        return {"ok": False, "blocked": True, "code": guard.code, "reason": guard.reason}
    try:
        result = await run_in_threadpool(run_query, body.source_id, guard.sql)
    except QueryError as exc:
        return {"ok": False, "blocked": False, "sql": guard.sql, "error": str(exc)}
    return {"ok": True, "sql": guard.sql, **result,
            "chart": choose_chart(result["columns"], result["rows"])}


class QuestionIn(BaseModel):
    question: str


@router.post("/safety/screen")
def screen(body: QuestionIn) -> dict:
    r = screen_question(body.question)
    return {"allowed": r.allowed, "category": r.category, "message": r.message, "matched": r.matched}
