import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, schema, sources
from app.profiler import retriever
from app.profiler.store import get_store

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    store = get_store()
    store.on_change(retriever.invalidate)
    store.ensure_all()  # profile any source that has no profile yet (background)
    threading.Thread(target=retriever.warm_up, daemon=True).start()  # load embedding model
    yield


app = FastAPI(title="Voice Data Analyst API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
