from fastapi import APIRouter
from sqlalchemy import create_engine, text

from app.config import get_settings

router = APIRouter(tags=["health"])


def _ping(url: str) -> str:
    if not url:
        return "not configured"
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        engine.dispose()
        return "ok"
    except Exception as exc:  # report, don't crash the health check
        return f"error: {type(exc).__name__}"


@router.get("/health")
def health() -> dict:
    s = get_settings()
    return {
        "status": "ok",
        "llm_provider": s.llm_provider,
        "llm_key_set": bool(s.openai_api_key if s.llm_provider == "openai" else s.anthropic_api_key),
        "databases": {
            "postgres": _ping(s.demo_postgres_url),
            "mysql": _ping(s.demo_mysql_url),
            "sqlite": _ping(s.demo_sqlite_url),
        },
    }
