from fastapi import APIRouter
from fastapi.concurrency import run_in_threadpool

from app.config import get_settings
from app.sources.registry import SourceError, check_connection, get_registry

router = APIRouter(tags=["health"])


def _status() -> dict:
    s = get_settings()
    reg = get_registry()
    dbs = {}
    for src in reg.list():
        try:
            check_connection(reg.engine(src.id))
            dbs[src.name] = "ok"
        except SourceError:
            dbs[src.name] = "error"
    return {
        "status": "ok",
        "llm_provider": s.llm_provider,
        "llm_key_set": bool(s.llm_key),
        "databases": dbs,
    }


@router.get("/health")
async def health() -> dict:
    return await run_in_threadpool(_status)
