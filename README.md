# Voice Data Analyst

Talk to your databases. Ask a business question out loud, and get back a chart, a table, the SQL that was run, and a short spoken explanation. Speech recognition and text-to-speech run locally.

## Status
Work in progress. Done so far: Step 0 (repo skeleton), Step 1 (Docker skeleton), Step 2 (demo data loaded), Step 3 (data source manager), Step 4 (schema profiler + retrieval), Step 5 (question to SQL to spoken answer), Step 6 (local voice in/out), safety layer, Step 7 (domain word correction), Step 8 (chart, table and SQL display), source routing with clarification, Step 10 (evaluation tooling).

## Requirements
- Docker (Docker Desktop, or Colima: `colima start --cpu 4 --memory 5`)
- A free Gemini API key from https://aistudio.google.com/apikey (set `GEMINI_API_KEY` in `.env`)

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

## Evaluation
Run from the project folder while the app is up (plain Python 3, no extra packages):

```bash
python3 eval/run_eval.py --check-gold   # validate the dataset: every gold query runs, no ties at "top N" cut-offs
python3 eval/run_eval.py                # end-to-end run; writes eval/results/run-<time>.jsonl and .md
python3 eval/run_eval.py --resume eval/results/run-<time>.jsonl   # continue after a quota stop
python3 eval/routing_eval.py            # which database a question goes to; no LLM calls
```

- **Execution accuracy** (`eval/datasets/nl2sql.jsonl`, 36 questions over the three demo databases, plus 7 safety questions). Each question has a hand-written gold query; the app's result and the gold result are compared row by row, ignoring column names, extra columns, row order and rounding to 2 decimals. *Strict* needs the same rows; *lenient* also accepts a ranked list whose first row is a one-row gold answer ("which X has the most Y"). Questions are asked with the source fixed, so routing errors don't count here, and with `speak=false` (template answer) to halve Gemini calls.
- **Routing** (`eval/datasets/routing.jsonl`, 48 labelled questions, 14 of them ambiguous on purpose). Measures wrong-source picks, ambiguous questions caught, and unnecessary "which database?" questions, and sweeps the two thresholds in `backend/app/profiler/routing.py`.

Results go to `eval/results/` (not committed).

## Local speech models
Downloaded automatically on first start into a Docker volume (`models`), then used offline:
- Speech-to-text: faster-whisper `base.en` (int8, CPU). Set `WHISPER_MODEL=small.en` in `.env` for better accuracy (slower).
- Text-to-speech: Piper `en_US-lessac-medium`.

The browser records the microphone, detects when you stop speaking, and sends a 16 kHz WAV to the backend. Audio never leaves your machine.
