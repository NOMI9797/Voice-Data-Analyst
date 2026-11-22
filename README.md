# Voice Data Analyst

Talk to your databases. Ask a business question out loud, and get back a chart, a table, the SQL that was run, and a short spoken explanation. Speech recognition and text-to-speech run locally.

## Status
Work in progress. Done so far: Step 0 (repo skeleton) and Step 1 (Docker skeleton).

## Requirements
- Docker Desktop (with at least 6 GB RAM in Settings → Resources)
- An LLM API key (Anthropic by default)

## Quick start
```bash
cp .env.example .env        # then put your API key in .env
docker compose up --build
```
Open http://localhost:3000. The backend API docs are at http://localhost:8000/docs.

| Service  | Host port | Purpose                     |
|----------|-----------|-----------------------------|
| frontend | 3000      | React UI (served by nginx)  |
| backend  | 8000      | FastAPI                     |
| postgres | 5433      | Demo DB #1 (Olist)          |
| mysql    | 3307      | Demo DB #2                  |

## Project layout
```
backend/   FastAPI app (stt, tts, sources, profiler, correction, nl2sql, safety, viz, session, llm)
frontend/  React + Vite + TypeScript UI
docker/    DB init scripts (read-only users)
scripts/   data download / seeding
eval/      evaluation datasets, attack questions, results
docs/      report and diagrams
```
