from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.profiler.retriever import retrieve
from app.profiler.store import get_store
from app.profiler.suggest import suggestions_for
from app.sources.registry import SourceError, get_registry

router = APIRouter(tags=["schema"])


# API: returns a source's profile.
@router.get("/sources/{source_id}/profile")
def get_profile(source_id: str, values: bool = False) -> dict:
    try:
        get_registry().get(source_id)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    store = get_store()
    prof = store.get(source_id)
    out = {"status": store.status(source_id), "profile": None}
    if prof:
        data = prof.model_dump()
        if not values:  # value lists can be thousands of entries; send counts only
            for t in data["tables"]:
                for c in t["columns"]:
                    c["value_count"] = len(c.pop("values"))
        out["profile"] = data
    return out


# API: profiles a source again.
@router.post("/sources/{source_id}/profile", status_code=202)
def reprofile(source_id: str) -> dict:
    try:
        get_registry().get(source_id)
    except SourceError as exc:
        raise HTTPException(404, str(exc)) from exc
    get_store().schedule(source_id)
    return {"status": "queued"}


# A question for the table search.
class RetrieveIn(BaseModel):
    question: str
    source_ids: list[str] | None = None


# API: shows which tables a question would use (for testing).
@router.post("/schema/retrieve")
async def retrieve_schema(body: RetrieveIn) -> dict:
    if not body.question.strip():
        raise HTTPException(400, "Empty question")
    r = await run_in_threadpool(retrieve, body.question, body.source_ids)
    return asdict(r)


# API: starter questions for the empty screen.
@router.get("/suggestions")
def suggestions() -> list[dict]:
    """Starter questions for an empty conversation, from the profiles of the active sources."""
    store, out = get_store(), []
    for src in get_registry().active():
        prof = store.get(src.id)
        if prof:
            out += [{"source_id": src.id, "source_name": src.name, "question": q} for q in suggestions_for(prof)]
    return out
