# Voice Data Analyst

Talk to your databases. Ask a business question out loud ("which products brought in the most revenue last quarter?") and get back a chart, a table, the SQL that was run, and a short spoken explanation. Speech recognition and text-to-speech run on your machine; no SQL knowledge is needed.

**Course project:** Project 4, Voice Data Analyst (NLP course, Complex Computing Problem).
**Report:** [docs/REPORT.md](docs/REPORT.md) · **Architecture diagram:** [docs/architecture.svg](docs/architecture.svg)

## Group members

| Name | Registration number |
|------|---------------------|
|      |                     |
|      |                     |
|      |                     |

## What it does

- **Voice in, voice out.** Speak a question; the app detects when you stop, transcribes it locally, answers, and speaks a one- or two-sentence explanation of what matters ("Orders peaked in November 2017, about twice the monthly average"), not the table.
- **Fixes misheard names** (cities, products, artists, categories) against the real values in the database before any SQL is written: "how many customers in Cartiba" → Curitiba.
- **Understands any database** from a connection string or a file: it profiles tables, columns, values and relationships automatically and sends only the relevant tables to the language model.
- **Several sources at once** (PostgreSQL, MySQL, SQLite, uploaded CSV). It works out which source holds the answer and writes SQL in that dialect. When a question fits more than one source equally well, it asks which one by voice, and remembers the choice for the rest of the conversation.
- **Asks when a question is vague** ("show me the top customers" → "by total spending, or by number of orders?"), then listens for the spoken answer.
- **Follow-ups edit the previous query:** "now break it down by month", "same for last year", "what about France?".
- **Repairs itself** when a query fails or the result looks suspicious (no rows, all NULL, negative counts), with a limit of 3 attempts.
- **Never runs a dangerous query.** Four layers: question screen → SQL guard (one read-only SELECT, known tables, no personal or secret columns, row limit) → read-only database connection → time limit.
- Shows a suitable **chart and table**, and the **SQL** (editable, re-runnable through the same guard).

## Models, APIs, and what runs locally

| Part | Model / library | Runs |
|---|---|---|
| Speech-to-text | faster-whisper `base.en` (CTranslate2, int8, CPU); looped transcripts are rejected | **Local** |
| Text-to-speech | Piper `en_US-lessac-medium` | **Local** |
| Voice activity detection | energy-based detector in the browser (AudioWorklet) | **Local** (browser) |
| Domain word correction | rapidfuzz spelling + phonetic matching against database values | **Local** |
| Schema retrieval and routing | fastembed `BAAI/bge-small-en-v1.5` embeddings + BM25 + value matching | **Local** |
| SQL safety guard | sqlglot syntax tree checks | **Local** |
| Chart choice, key facts, sanity checks | rule-based Python | **Local** |
| SQL writing, repair, spoken wording, table descriptions | Google Gemini API, `gemini-3.5-flash-lite` with fallbacks (`gemini-flash-lite-latest`, `gemini-3.8-flash`, `gemini-3.5-flash`) via the OpenAI-compatible endpoint | **Cloud** (allowed by the project rules) |

Gemini only receives text: the question, the schema of the few relevant tables, matched values, and at most 30 result rows for the spoken wording. Audio, credentials and personal-data columns never leave the machine.

## Architecture

![Architecture](docs/architecture.svg)

A question goes through these stages (numbers match the diagram):

1. **Speech-to-text.** The browser records until 1.1 s of silence and sends 16 kHz WAV; faster-whisper transcribes it. If Whisper loops on a few words instead of transcribing, the result is rejected and the user is asked to repeat.
2. **Domain word correction.** Each 1–4 word span is matched by spelling and sound against ~9,000 values collected by the profiler; confident matches are replaced (1–5 ms).
3. **Question screen.** Rule-based, instant refusals of data changes, secrets, bulk personal data, system access and prompt injection.
4. **Schema retrieval and routing.** Hybrid search (embeddings, BM25, value hits) picks the relevant tables and join paths. With several sources, each source's *coverage* of the question decides; when it's a genuine tie, the app asks which database, once per topic. A question that only a switched-off source can answer is reported as such.
5. **Question → SQL.** Gemini writes one SELECT for the source's dialect, using the retrieved schema, matched values and the last turns (follow-ups). For vague questions it returns a clarifying question instead.
6. **SQL guard.** The query's syntax tree is checked; anything unparseable is rejected.
7. **Read-only executor** with per-engine time limits and a row cap. Errors and suspicious results go back to step 5 (self repair, max 3).
8. **Chart and key facts.** Rules choose the chart; the peak, share, trend and gaps are computed exactly over all rows.
9. **Spoken answer.** Gemini words the facts in one or two sentences (6 s deadline, otherwise a template sentence).
10. **Text-to-speech.** Piper speaks sentence 1 while the rest is synthesized; clicking the mic interrupts it to ask something else.

Each new source is profiled once in the background (tables, columns, samples, value lists, declared and inferred relationships, LLM descriptions). The profile feeds the retrieval index, the correction vocabulary, the guard's allowed tables and the speech-to-text hint.

## Setup

Requirements:
- Docker (Docker Desktop, or Colima: `colima start --cpu 4 --memory 5`), about 10 GB of free disk
- A Gemini API key from https://aistudio.google.com/apikey

```bash
cp .env.example .env        # then put your key in GEMINI_API_KEY
docker compose up --build -d
./scripts/seed_all.sh       # loads the demo databases (one time)
```
Before seeding, download the Olist dataset from https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce and unzip the CSVs into `data/olist/`. Chinook and Northwind are downloaded automatically. The speech models download on first start into a Docker volume, then work offline.

Open http://localhost:3000 (API docs: http://localhost:8000/docs).

| Service  | Host port | Purpose |
|----------|-----------|---------|
| frontend | 3000 | React UI (served by nginx) |
| backend  | 8000 | FastAPI |
| postgres | 5433 | Demo source 1: Olist e-commerce (9 tables, about 1.6M rows) |
| mysql    | 3307 | Demo source 2: Chinook music store (11 tables) |
| (file)   | –    | Demo source 3: Northwind SQLite, `data/northwind.db` |

### Connecting a new database
In the **Data sources** panel:
- **Connect** with a connection string: `postgresql://user:pass@host:5432/db`, `mysql://user:pass@host:3306/db`, or `sqlite:////data/file.db` (`localhost` is mapped to your machine automatically).
- **Upload** one or more CSV files (each becomes a table) or a SQLite `.db` file.
- **Toggle** sources on or off. With several active, the selector next to the Ask box can limit a question to one source.

No other setup is needed: the source is profiled automatically and is ready in seconds to a few minutes depending on size. All connections are read-only (read-only database user, read-only session, or read-only file mode).

## Project layout
```
backend/app/
  api/         HTTP endpoints (ask, sources, schema, voice, correction, query)
  stt/ tts/    local Whisper and Piper
  correction/  domain word correction
  sources/     source registry, read-only engines
  profiler/    schema profiling, LLM descriptions, retrieval, source routing
  nl2sql/      pipeline, SQL writer, sanity checks, key facts, spoken answer
  safety/      question screen, SQL guard, read-only executor
  viz/         chart choice
  evaluation/  result comparison for execution accuracy
  llm/         Gemini client with model fallback
backend/tests/ unit and integration tests (pytest)
frontend/      React + Vite + TypeScript UI
eval/          evaluation scripts and datasets
docs/          report and architecture diagram
docker/ scripts/  database setup and seeding
```

## Tests
```bash
docker compose exec backend python -m pytest -q tests
```

## Evaluation
Run from the project folder while the app is up (plain Python 3, no extra packages). Results go to `eval/results/` (not committed). Full numbers and discussion are in the [report](docs/REPORT.md#6-evaluation).

```bash
python3 eval/voice_eval.py              # domain-word accuracy before/after correction, and per-stage latency (--hint: with a Whisper name prompt)
python3 eval/attack_eval.py             # 47 attack questions and raw SQL attacks
python3 eval/run_eval.py --check-gold   # validate the answer dataset (no LLM)
python3 eval/run_eval.py                # execution accuracy, follow-ups, clarification
python3 eval/routing_eval.py            # which database a question goes to (no LLM)
```

Tests: 255 automated tests (`pytest`).

| Evaluation | Dataset | Result |
|---|---|---|
| Domain words, spoken round trip (Piper → Whisper) | 38 sentences, 41 names | about 33% → 68% of names correct before → after correction (4 runs); 0 of 12 name-free questions changed |
| Domain words, hand-written mishearings | 20 sentences, 22 names | 5% → 95–100% |
| End of question → first audio (median) | 10 spoken questions | 4.0 s and 6.3 s in two runs (local stages ≈ 2.0 s incl. 1.1 s silence wait; the rest is the cloud LLM) |
| Attack questions | 32 spoken-style + 15 raw SQL | 47/47 handled safely; no row changed in 16 checked tables |
| Execution accuracy | 36 questions, 3 databases | 89–97% strict, 94–97% lenient (two runs) |
| Voice follow-ups | 3 conversations, 9 turns | 9/9 turns correct |
| Clarification of vague questions | 5 vague questions | 5/5 asked; 0/36 clear questions asked needlessly |
| Source routing | 48 questions (14 ambiguous) | 0 wrong sources; 12/14 ambiguous asked; 4/34 clear asked |
