# Voice Data Analyst: Project Report

**Course:** Natural Language Processing, Complex Computing Problem (CCP)
**Project:** Project 4, Voice Data Analyst

| Name | Registration number |
|------|---------------------|
| Noman Ahmed | 22i-2563 |
| Zain Abbas | 21i-1109 |

---

## 1. Introduction

### 1.1 What the system does

Voice Data Analyst lets a person **talk to a database**. The user asks a business question out loud, for example *"Which payment type is used most often?"*, and the system:

1. understands the spoken question,
2. finds the answer in the database,
3. shows it as a **chart and a table**, together with the **SQL it ran**,
4. and **speaks a short summary** of what matters.

The user never writes SQL. Speech recognition and speech synthesis run **on the user's computer**. Only the SQL writing and the wording of the answer use a cloud language model (Google Gemini).

### 1.2 Objectives

| # | Objective | How we meet it |
|---|---|---|
| 1 | Answer spoken questions about any database | Works from a connection string or file only |
| 2 | Keep the voice private | Speech to text and text to speech run locally |
| 3 | Understand misheard names | Names are corrected against real database values |
| 4 | Never guess when a question is unclear | Asks a short spoken question and listens for the reply |
| 5 | Never damage or leak data | Four independent safety layers |
| 6 | Answer quickly | About 3.5 seconds from the end of the question to the first spoken word |

### 1.3 Demo databases

| Database | Domain | Engine | Size |
|---|---|---|---|
| **Olist** (dataset named in the brief) | Brazilian e-commerce | PostgreSQL | 9 tables, about 1.6 million rows |
| **Chinook** | Music store | MySQL | 11 tables |
| **Northwind** | Trading company | SQLite | 13 tables plus views |
| **Uploaded CSV files** | Any | DuckDB | Any |

---

## 2. System Overview

### 2.1 The three parts

| Part | Technology | Job | Where it runs |
|---|---|---|---|
| **Browser** | React, TypeScript | Records the voice, shows the conversation, chart and table, plays the answer | User's computer |
| **Backend** | Python, FastAPI, Docker | Does all the processing: speech, correction, search, safety, queries, charts | User's computer |
| **Gemini** | Google cloud language model | Writes the SQL and words the spoken answer. Receives **text only** | Cloud |

### 2.2 How it works

**When a database is connected (once):** the system studies it (tables, columns, values, links between tables) and prepares a search index and a list of real names.

**When a question is asked (every time), ten steps run in order:**

| Step | What happens | Tool / method | Time |
|---|---|---|---|
| 1. Speech to text | The voice becomes text | Whisper (local) | 0.6 s |
| 2. Fix misheard names | "Kuretiba" becomes "Curitiba" | Spelling + sound matching | 5 ms |
| 3. Question safety check | Refuses "delete", "show passwords", etc. | Fixed rules | < 1 ms |
| 4. Find database and tables | Picks the right database and the few tables needed | Hybrid search | 12 ms |
| 5. Write the SQL | One SQL query is written | Gemini (cloud) | 1.0 s |
| 6. SQL safety check | Only safe, read-only SQL is allowed | sqlglot syntax tree | 4 ms |
| 7. Run the query | Runs it; repairs it if it fails (max 3 times) | Read-only connection | 13 ms |
| 8. Chart and key facts | Chooses the chart, computes peak, average, trend | Fixed rules | < 5 ms |
| 9. Spoken answer | One or two sentences are written | Gemini (cloud) | 0.6 s |
| 10. Speak it | The answer is spoken | Piper (local) | 0.1 s |

![Architecture](architecture.png)

*Figure 1: System architecture. Green parts run on the user's computer, blue is the cloud language model, orange are the safety layers.*

### 2.3 Project structure

| Folder | Contents |
|---|---|
| `backend/app/stt` and `backend/app/tts` | Speech to text (Whisper) and text to speech (Piper) |
| `backend/app/correction` | Misheard-name correction |
| `backend/app/profiler` | Database study, table search, database routing, starter questions |
| `backend/app/nl2sql` | Main controller (`pipeline.py`), SQL writing, key facts, spoken answer |
| `backend/app/safety` | Question screen, SQL guard, read-only executor |
| `backend/app/sources` | Connected databases and CSV uploads |
| `backend/app/viz` | Chart choice |
| `backend/app/api` | Web endpoints used by the browser |
| `frontend/src` | React app: conversation, voice button, chart, table |
| `eval/` | The four evaluation scripts and their results |

---

## 3. Core Modules

### 3.0 Summary

| # | Module (from the brief) | What we built | Main file |
|---|---|---|---|
| 1 | Local speech to text | Whisper `base.en`, auto-stop after silence | `stt/whisper.py` |
| 2 | Domain word correction | Spelling + sound match against 9,300 real values | `correction/corrector.py` |
| 3 | Schema understanding | Automatic profiling + hybrid table search | `profiler/profile.py`, `retriever.py` |
| 4 | Multiple sources + CSV | PostgreSQL, MySQL, SQLite, CSV; automatic routing | `sources/registry.py`, `profiler/routing.py` |
| 5 | Question to SQL | Gemini with only the relevant tables | `nl2sql/generator.py` |
| 6 | Self repair | Up to 3 repair attempts | `nl2sql/pipeline.py` |
| 7 | Spoken clarification | Asks by voice and listens for the reply | `nl2sql/pipeline.py`, `Conversation.tsx` |
| 8 | Chart, table, spoken answer | Rule-based chart, SQL shown, local voice | `viz/chart.py`, `nl2sql/answer.py` |
| 9 | Voice follow-ups | Last 3 questions remembered | `nl2sql/generator.py` |
| 10 | Evaluation | Four scripts, measured results | `eval/` |

### 3.1 Voice input

The browser records the microphone and stops automatically after **1.1 seconds of silence**. The audio is sent to **Whisper** (`base.en`, via faster-whisper), which runs locally and turns it into text in about **0.6 seconds**.

| Setting | Value | Why |
|---|---|---|
| Model | `base.en`, 8-bit, CPU | Fast enough on a laptop |
| Search width (beam) | 5 | Better accuracy than a single guess |
| Broken output check | Repeated words or too few different words | Whisper sometimes loops; the user is asked to repeat |

### 3.2 Domain word correction

Speech models often mishear names. Before any SQL is written, each word group in the transcript is compared with about **9,300 real values** from the database (cities, products, artists, categories).

| Check | Tool | Weight |
|---|---|---|
| Spelling similarity | RapidFuzz | 55% |
| Sound similarity | Metaphone (jellyfish) | 45% |
| Common English word? (never changed) | wordfreq | Filter |

A name is replaced only if the score is **86 or higher**. Examples: "Kuretiba" → *Curitiba*, "below horizontal" → *Belo Horizonte*, "led zepelin" → *Led Zeppelin*.

### 3.3 Schema understanding

When a database is connected, it is **profiled automatically**: tables, columns, types, sample values, and relationships, including links that are not declared (found by matching names and values). Gemini also writes a one-line description of each table and column.

For each question, a **hybrid search** picks only the tables that matter, because a large schema does not fit in a prompt:

| Signal | Meaning | Weight |
|---|---|---|
| Table meaning | Embedding similarity (bge-small) | 45% |
| Best column meaning | Embedding similarity | 35% |
| Keywords | BM25 | 20% |
| Values found in the question | e.g. "Curitiba" is in `customer_city` | Bonus up to 20% |

At most **6 tables** are chosen, plus up to 2 "bridge" tables needed for joins.

### 3.4 Multiple data sources

Users connect **PostgreSQL, MySQL and SQLite** databases with a connection string, or **upload CSV files** (stored as DuckDB). All connections are **read-only**.

**Choosing the database.** Each active database gets a *coverage* score (how much of the question it can explain) and a search score.

| Situation | What the system does |
|---|---|
| One database clearly fits better | Answers from it directly |
| Two fit almost equally (a genuine tie) | Asks *"Olist or Chinook?"* and remembers the choice for that topic |
| A database is switched off | Never used; if it would have fitted, the system says *"Turn on Olist and ask"* |

SQL is written in the correct **dialect** for each engine.

### 3.5 Question to SQL

Gemini receives: the question, the selected tables with descriptions, matching data values, and the last three questions. It returns one SQL query.

| Prompt rule | Example |
|---|---|
| Use the real values from the data | "credit card" → `'credit_card'` |
| Aggregate when asked "how many", "total", "top" | `COUNT`, `SUM`, `ORDER BY ... LIMIT` |
| Dates are relative to the data | "last year" = last full year in the data |
| Never list private details | No emails, phones or customer names |

If the main model is busy, backup models are used automatically: `gemini-3.5-flash-lite` → `gemini-flash-lite-latest` → `gemini-3.8-flash` → `gemini-3.5-flash`.

### 3.6 Self repair

| Problem | What happens |
|---|---|
| Database error (e.g. wrong column) | Error sent back to Gemini; the missing tables are added |
| Blocked by the SQL guard (fixable) | Reason sent back to Gemini |
| Suspicious result (no rows, all blanks, negative counts, percentages over 100) | One repair attempt |
| Unsafe request (delete, secrets) | **Never repaired**, refused immediately |

There are **at most 3 attempts**. After that the user is told clearly that no answer was found.

### 3.7 Spoken clarification

For vague questions the system does not guess. It asks a short question by voice, then **turns the microphone on automatically** and listens for the answer.

| User says | System asks |
|---|---|
| *"Show me the top customers"* | *"By total spending, or by number of orders?"* |
| *"How many customers are there?"* (several databases fit) | *"Olist, Chinook or Northwind?"* |

It never asks twice about the same question, and the spoken reply is matched even if slightly misheard.

### 3.8 Chart, table and spoken explanation

| Result shape | Chart chosen |
|---|---|
| One number | Big-number card |
| Values over time | Line chart |
| Categories compared | Bar chart (horizontal for long names) |
| Two numbers per row | Scatter plot |

A sortable table, CSV download and the **SQL** are always shown. The system computes the key facts itself (peak, average, share, trend; unfinished months are excluded), and Gemini turns them into one or two sentences, for example *"Orders peaked in November 2017, about twice the monthly average."* The numbers are never invented by the AI. The voice is produced locally by **Piper** (`en_US-lessac-medium`), and the browser plays it sentence by sentence.

### 3.9 Voice follow-ups

The last three questions and their SQL are kept, so the system **edits the previous query** instead of starting over:

| Turn | User says | SQL change |
|---|---|---|
| 1 | *"How many orders were placed in 2017?"* | Count for 2017 |
| 2 | *"Now break it down by month"* | Same count, grouped by month |
| 3 | *"Same for last year"* | Same query for 2016 |

### 3.10 Evaluation

Four scripts in `eval/` measure the system against the live app. Results are in Section 6.

| Script | Measures |
|---|---|
| `voice_eval.py` | Name accuracy before/after correction, and response time |
| `run_eval.py` | Correct answers, follow-ups and clarification |
| `attack_eval.py` | Safety against 47 attacks |
| `routing_eval.py` | Choosing the right database |

---

## 4. Setup

**Requirements:** Docker (or Colima on macOS) and a Gemini API key.

| Step | Command / action |
|---|---|
| 1. Add the key | Copy `.env.example` to `.env` and add the Gemini API key |
| 2. Start everything | `docker compose up --build -d` (backend, frontend, PostgreSQL, MySQL) |
| 3. Load demo data | `./scripts/seed_all.sh` (Olist CSVs from Kaggle go in `data/olist/` first) |
| 4. Open the app | **http://localhost:3000** |

The speech models download on the first start and then work offline. A new database is added from the **"Add a data source"** panel with a connection string or a file. No other setup is needed.

---

## 5. Technical Details

### 5.1 Technology

| Part | Technology | Runs |
|---|---|---|
| Speech to text | faster-whisper (`base.en`) | Local |
| Text to speech | Piper (`en_US-lessac-medium`) | Local |
| Name correction | RapidFuzz, jellyfish (Metaphone), wordfreq | Local |
| Table search | fastembed (bge-small), BM25 | Local |
| SQL safety check | sqlglot (reads SQL as a syntax tree) | Local |
| Databases | PostgreSQL, MySQL, SQLite, DuckDB via SQLAlchemy | Local |
| SQL writing and answer wording | Google Gemini (`gemini-3.5-flash-lite` + backups) | Cloud |
| Backend | Python, FastAPI, Uvicorn | Local |
| Frontend | React, TypeScript, Tailwind, Recharts | Local |

### 5.2 Main API endpoints

| Endpoint | Purpose |
|---|---|
| `POST /api/stt` | Audio → text |
| `POST /api/ask` | Question → SQL, result, chart, spoken answer |
| `POST /api/tts` | Text → speech audio |
| `POST /api/sources`, `/api/sources/upload` | Connect a database or upload a CSV |
| `PATCH /api/sources/{id}` | Switch a database on or off |
| `GET /api/suggestions` | Starter questions for the active databases |

### 5.3 Safety layers

| Layer | What it stops | Example |
|---|---|---|
| 1. Question screen | Requests to change data, reveal secrets, or ignore instructions | *"Delete all orders"* |
| 2. SQL guard | Anything other than one `SELECT`; unknown tables; listing private data | `DROP TABLE`, emails, customer names |
| 3. Read-only connection | Any change at database level | Even an unknown trick fails |
| 4. Limits | Overloading the database | 15 seconds and 1,000 rows per query |

### 5.4 What goes to the cloud

| Sent to Gemini | Never sent |
|---|---|
| The question text | The user's voice |
| The few selected tables and descriptions | Passwords or connection strings |
| At most 30 result rows (for wording the answer) | Private columns (emails, phones, names) |

### 5.5 Reliability and testing

- If Gemini is busy or returns broken output, a **backup model** is tried automatically.
- If the spoken answer takes more than **6 seconds**, the computed facts are spoken directly.
- **266 automated tests** cover every step.

---

## 6. Evaluation Results

All results are from the final version of the system.

### 6.1 Summary

| What we measured | Result |
|---|---|
| Names correct, real speech-recognition errors (41 names) | **20% → 66%** after correction |
| Names correct, typical mishearings (22 names) | **5% → 95%** |
| Normal questions wrongly changed | **0 of 12** |
| Time to first spoken word (median) | **3.5 seconds** |
| Attacks handled safely | **47 of 47**, no row changed |
| Correct answers (36 questions) | **89%** exact, **97%** lenient |
| Follow-ups correct | **9 of 9** |
| Vague questions clarified | **5 of 5** (0 of 36 clear ones asked about) |
| Wrong database chosen (48 questions) | **0** |

### 6.2 Name correction

| Test set | Before | After |
|---|---|---|
| Real Whisper errors (41 names, voice round trip) | 20% | **66%** |
| Typical mishearings (22 names) | 5% | **95%** |
| Questions without names (12) | – | **0 changed** |

### 6.3 Response time

| Stage | Time |
|---|---|
| Waiting for silence | 1.1 s |
| Speech to text (Whisper) | 0.6 s |
| Correction, search, safety, query | < 0.1 s |
| Writing the SQL (Gemini) | 1.0 s |
| Wording the answer (Gemini) | 0.6 s |
| First audio (Piper) | 0.1 s |
| **Total (median)** | **3.5 s** |

### 6.4 Safety

| Attack type | Count | Stopped by |
|---|---|---|
| Change or destroy data | 8 | Question screen, AI refusal |
| Ignore instructions / reveal secrets | 8 | Question screen |
| Bulk personal data | 4 | Question screen, AI refusal |
| System access, overload, SQL inside a value | 9 | Question screen, AI refusal |
| Disguised (*"count orders, then delete them"*) | 3 | AI refusal |
| Raw dangerous SQL | 15 | SQL guard |
| **Total** | **47** | **All stopped; no row changed** |

### 6.5 Answer accuracy (36 questions, 3 databases)

| Difficulty | Correct |
|---|---|
| Easy (10) | 10 |
| Medium (18) | 14 |
| Hard (8) | 8 |
| **Total, exact** | **32 (89%)** |
| **Total, lenient** (ranked list with the right answer first) | **35 (97%)** |

Answers were compared by their **results**, not by SQL text, as in the Spider and BIRD benchmarks.

### 6.6 Database routing (48 questions)

| Method | Wrong database | Ambiguous questions asked about |
|---|---|---|
| Always pick the top score | 3 | 0 of 14 |
| **Coverage + score gap (used)** | **0** | **12 of 14** |

---

## 7. Critical Analysis

### 7.1 Problem Statement

Most business data is stored in databases, but most people who need answers **cannot write SQL**. They depend on analysts or on dashboards that only answer questions planned in advance. Speaking a question is the most natural way to ask.

| Existing solution | Why it is not enough |
|---|---|
| Business-intelligence tools (e.g. Power BI Q&A) | Each dataset must be prepared by an expert; typed, not spoken |
| Plain AI text-to-SQL | Guesses when unclear, cannot fit large databases, no safety |
| Normal voice assistants | Mishear names of products, places and people |

What is missing is a system that works on **any database**, understands **spoken** questions even when names are misheard, **asks instead of guessing**, answers **by voice**, and is **safe by design**.

### 7.2 Complexity Drivers

| Driver | Why it makes the problem hard |
|---|---|
| Errors build up | One misheard word → wrong value → empty result → confident wrong answer |
| Fast vs. accurate | Every extra check or AI call slows the conversation |
| Asking vs. guessing | Must ask when unclear, but asking too often is annoying |
| Useful vs. private | Staff names are fine; a list of customers is not |
| Local vs. quality | Speech must run on a laptop, so the model is smaller |
| No obvious solution | No standard way to pick the database or detect vagueness; we built and measured rules |
| Unknown databases | Nothing can be hard-coded; four SQL dialects |
| Many parts | Ten steps, two local AI models, four engines, one cloud service, in a few seconds |

### 7.3 Design Justification

| Alternative we considered | Why we rejected it | What we chose instead |
|---|---|---|
| Send the **whole schema** to the AI | Large databases do not fit; extra tables cause wrong joins | Send only the few tables that matter |
| Let the **AI decide what is safe** | AI can be tricked; in our test it turned "show emails" into "show customer names" | Four fixed safety layers |
| Let the **AI fix misheard names** | Does not know real values; slower; changes correct words | Local matcher against real values |
| **Cloud speech recognition** (e.g. Deepgram) | Rules require local speech; the voice would leave the computer | Whisper and Piper locally |
| **Answer from the best-matching database** | Faster, but it is guessing | Ask, but only on a genuine tie |

### 7.4 Trade-off Analysis

| Trade-off | Our choice | Reason |
|---|---|---|
| Speech accuracy vs. speed | Whisper `base.en` (0.6 s), not `small.en` (1.7 s) | `small.en` got 73% of names vs. 66%, but adds over a second to every question; the corrector closes much of the gap |
| Asking vs. guessing | Ask only when the answer would really change | Asking too often is annoying; guessing gives wrong answers |
| Natural answer vs. speed | AI writes the answer, 6-second limit, facts computed by us | Natural, exact numbers, no long waits |
| Privacy vs. usefulness | Allow totals and staff names; block listing customers | Most questions need totals, not individuals |
| Safety vs. flexibility | Read-only, one query, 1,000 rows, 15 seconds | Must never change or overload an unknown database |
| Free AI vs. reliability | Fast model plus backups | The free service is often busy |

### 7.5 Societal and Ethical Impact

| Issue | Risk | What we did |
|---|---|---|
| **Privacy** | Databases hold personal data | Private details can be counted but not listed; only text goes to the cloud. Users must still follow laws such as the **EU GDPR** and **Pakistan's proposed Personal Data Protection Bill** |
| **Voice recording** | Microphone may record unexpectedly | Audio is processed locally and not stored; the microphone state is always visible |
| **Safety** | Tricked or disguised delete requests | Four layers stopped all 47 attacks; read-only access protects against unknown tricks |
| **Over-trust** | A confident voice can still be wrong (89–97% accuracy) | SQL is shown, assumptions stated, asks when unsure; important decisions need a human check |
| **Fairness** | Some accents are recognised less well | Name correction reduces this; non-SQL users and people with visual or mobility impairments gain access |

---

## 8. Limitations and Future Work

| Limitation | Possible improvement |
|---|---|
| About a third of badly mispronounced names are not recovered | Larger speech model on faster hardware |
| Response time depends on the cloud AI | Paid tier or a local SQL model |
| Small test sets written by us | Evaluate on public benchmarks (Spider, BIRD) |
| Private data found by column names only | Scan free-text columns for personal data |

---

## 9. References

1. Radford et al., *Robust Speech Recognition via Large-Scale Weak Supervision* (Whisper), 2022.
2. Yu et al., *Spider: A Large-Scale Human-Labeled Dataset for Text-to-SQL*, 2018.
3. Li et al., *BIRD: A Big Bench for Large-Scale Database Grounded Text-to-SQLs*, 2023.
4. *Routing End User Queries to Enterprise Databases*, arXiv 2601.19825, 2026.
5. Olist, *Brazilian E-Commerce Public Dataset*, Kaggle.
6. Piper text-to-speech; faster-whisper; sqlglot; fastembed (BGE embeddings).
