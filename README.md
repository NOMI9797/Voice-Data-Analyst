# Voice Data Analyst

Talk to your databases. Ask a business question out loud, and get back a chart, a table, the SQL that was run, and a short spoken explanation. Speech recognition and text-to-speech run locally.

## Status
Work in progress. Done so far: Step 0 (repo skeleton), Step 1 (Docker skeleton), Step 2 (demo data loaded), Step 3 (data source manager), Step 4 (schema profiler + retrieval).

## Requirements
- Docker (Docker Desktop, or Colima: `colima start --cpu 4 --memory 5`)
- An OpenAI API key (set `OPENAI_API_KEY` in `.env`)

## Quick start
```bash
cp .env.example .env        # then put your API key in .env
docker compose up --build -d
./scripts/seed_all.sh       # loads the demo databases (one time)
```
Before seeding, download the Olist dataset from
https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce and unzip the CSVs into `data/olist/`.
Chinook and Northwind are downloaded automatically.
Open http://localhost:3000. The backend API docs are at http://localhost:8000/docs.

| Service  | Host port | Purpose                     |
|----------|-----------|-----------------------------|
| frontend | 3000      | React UI (served by nginx)  |
| backend  | 8000      | FastAPI                     |
| postgres | 5433      | Demo source 1: Olist e-commerce (9 tables, about 1.6M rows) |
| mysql    | 3307      | Demo source 2: Chinook music store (11 tables)    |
| (file)   | -         | Demo source 3: Northwind SQLite, `data/northwind.db` |

The app connects to every source with a **read-only** user or read-only mode.

## Project layout
```
backend/   FastAPI app (stt, tts, sources, profiler, correction, nl2sql, safety, viz, session, llm)
frontend/  React + Vite + TypeScript UI
docker/    DB init scripts (read-only users)
scripts/   data download / seeding
eval/      evaluation datasets, attack questions, results
docs/      report and diagrams
```

## Adding your own data
In the **Data sources** panel (left side of the UI) you can:
- **Connect** with a connection string: `postgresql://user:pass@host:5432/db`, `mysql://user:pass@host:3306/db`, or `sqlite:////data/file.db`. `localhost` is mapped to your laptop automatically.
- **Upload** one or more CSV files (each becomes a table) or a SQLite `.db` file.
- **Toggle** a source on or off, or remove it.

All connections are read-only at the database level (read-only session or read-only file mode).

## Tests
```bash
docker compose exec backend python -m pytest -q tests
```
