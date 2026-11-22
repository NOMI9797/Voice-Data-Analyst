from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import health, sources

app = FastAPI(title="Voice Data Analyst API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(sources.router, prefix="/api")
