import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import ask, correction, health, query, schema, sources, voice
from app.profiler import retriever
from app.profiler.store import get_store
from app.stt import whisper
from app.tts import piper_tts

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


# On startup: load profiles, profile new databases in the background, and warm up Whisper, Piper and the embedding model.
@asynccontextmanager
async def lifespan(_app: FastAPI):
    store = get_store()
    store.on_change(retriever.invalidate)
    store.ensure_all()  # profile any source that has no profile yet (background)
    # load local models in the background so the first question is fast
    for fn in (retriever.warm_up, piper_tts.warm_up):
        threading.Thread(target=fn, daemon=True).start()
    whisper.EXECUTOR.submit(whisper.warm_up)  # same thread that will serve requests
    yield


app = FastAPI(title="Voice Data Analyst API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-TTS-ms"],
)

app.include_router(health.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
app.include_router(schema.router, prefix="/api")
app.include_router(voice.router, prefix="/api")
app.include_router(query.router, prefix="/api")
app.include_router(correction.router, prefix="/api")
app.include_router(ask.router, prefix="/api")
