# Voice Data Analyst: Project Report

**Course:** Natural Language Processing, Complex Computing Problem (CCP)
**Project:** Project 4, Voice Data Analyst

| Name | Registration number |
|------|---------------------|
|      |                     |
|      |                     |
|      |                     |

Repository: this repository · Architecture diagram: [`docs/architecture.svg`](architecture.svg) · Setup: [`README.md`](../README.md)

---

## Contents

1. [Summary](#1-summary)
2. [System overview](#2-system-overview)
3. [Core modules](#3-core-modules)
4. [Setup](#4-setup)
5. [Technical details](#5-technical-details)
6. [Evaluation](#6-evaluation)
7. [Critical analysis](#7-critical-analysis)
8. [Limitations and future work](#8-limitations-and-future-work)
9. [References](#9-references)

---

## 1. Summary

Voice Data Analyst lets a person ask a database a business question out loud and hear the answer. The user speaks ("which products brought in the most revenue last quarter?"); the system transcribes the question locally, fixes misheard names against the database's real values, finds the relevant tables, writes and checks a SQL query, runs it read-only, and answers with a chart, a table, the SQL it ran, and a one- or two-sentence spoken explanation produced by a local text-to-speech model. The user never writes SQL.

The system connects to an unseen database from only a connection string or a file, works across several sources at once (PostgreSQL, MySQL, SQLite and uploaded CSV in our demo), asks the user by voice when a question is vague or could belong to more than one source, edits the previous query for follow-ups such as "now break it down by month", repairs failing or suspicious queries, and never runs a dangerous query.

Headline results (Section 6):

| Measure | Result |
|---|---|
| Domain names correct, real speech-recognition errors | 20–32% before correction → 63–66% after, with the final settings (3 runs, 41 names each) |
| Domain names correct, hand-written mishearings | 5% → 95–100% |
| Questions without names changed by the corrector | 0 of 12 (every run) |
| End of question → first spoken audio, answered questions | median **3.5 s** in the final run (4.0 s and 6.3 s earlier); local stages ≈ 1.8 s, the rest is the cloud LLM |
| Attack questions handled safely | 47 of 47 (32 spoken-style, 15 raw SQL); no database row changed |
| Execution accuracy (36 questions, 3 databases) | final run: **89% strict, 97% lenient**; 89–97% strict across three runs |
| Voice follow-ups | 9 of 9 turns correct (3 conversations) |
| Vague questions clarified / clear questions asked needlessly | 5 of 5 / 0 of 36 |
| Wrong data source picked | 0 of 34 clear questions; 12 of 14 ambiguous ones asked about |
| Automated tests | 255 passing |

---

## 2. System overview

![Architecture](architecture.svg)

The system has three parts:

- **Browser (React + TypeScript).** Records the microphone, detects the end of speech, plays the spoken answer, and shows the chart, table and SQL. It also keeps the conversation memory (last three questions and their SQL) and any pending clarification.
- **Backend (Python, FastAPI, in Docker).** Everything else runs here, locally: speech-to-text, word correction, profiling, retrieval, routing, the SQL guard, the read-only executor, chart choice, key facts and text-to-speech.
- **Cloud LLM (Google Gemini).** The only external service. It writes and repairs SQL, words the spoken answer, and writes plain-English descriptions of tables during profiling. It receives text only (Section 5.4).

A question flows through ten stages, numbered as in the diagram: (1) speech-to-text, (2) domain word correction, (3) question screen, (4) schema retrieval and source routing, (5) question to SQL, (6) SQL guard, (7) read-only executor with self repair, (8) chart and key facts, (9) spoken answer wording, (10) text-to-speech. Separately, every newly connected source is profiled once in the background, and that profile feeds stages 1, 2, 4 and 6.

---

## 3. Core modules

Each subsection corresponds to one compulsory module of the brief.

### 3.1 Voice input (local speech recognition)

- **Capture.** The browser records through an AudioWorklet and runs a small energy-based voice activity detector: it calibrates on the first ~250 ms of room noise, treats a chunk as speech when its energy exceeds three times the noise floor (minimum 0.012), and stops after 1.1 s of silence. It keeps 300 ms of padding around the speech, resamples to 16 kHz and sends a WAV file. If nothing is said within 7 s it stops and asks the user to try again.
- **Transcription.** `faster-whisper` with the `base.en` model (CTranslate2, int8, CPU, beam size 5) transcribes the audio in about 0.7 s for a short question. Whisper's own voice activity filter is turned off because the browser has already trimmed silence.
- **Name prompt (evaluated, then turned off).** Whisper accepts an initial prompt, and we tried passing about 500 characters of real database values (cities, countries, categories). With synthetic speech this improved names by about 5 percentage points (Section 6.1). With a real microphone, however, Whisper sometimes looped on the prompt's words ("bage, tupa, bage, tupa, bage") instead of transcribing the question, so the app no longer sends it.
- **Loop detection.** A transcript is rejected as "I couldn't make out the words" when Whisper's own compression ratio is above 2.4 (its standard repetition check) or when fewer than half of its words are distinct. A looped transcript is never sent on as a question.

### 3.2 Domain word correction

Speech recognition mishears names: "Curitiba" becomes "Kuretiba", "Os Paralamas do Sucesso" becomes "OS Paralamas do Susaso". The corrector fixes these before any SQL is written, without an LLM, in 1–5 ms.

- **Vocabulary.** From the profile of every active source it collects the values of categorical and name-like text columns: about 9,300 values in our demo (cities, states, categories, artists, genres, products, companies), each with the columns it appears in.
- **Matching.** Every span of 1 to 4 words in the transcript is compared with the vocabulary by spelling (rapidfuzz ratio on a compact form) and by sound (a phonetic key). The score is 55% spelling and 45% sound, plus a small popularity prior and a bonus when the value comes from the same column as another name already in the question ("chai, chang and ikura"). A span is replaced when the score reaches 86 of 100.
- **Guards against over-correction.** Spans made only of common English words, or that start or end with a stop word, are never changed. A single common word may only become a value that sounds identical. A one-word change needs at least 75% spelling similarity (sound alone turned "Aussie" into the city "Assai"). A span longer than the value may not swallow an ordinary word at its edge (this used to delete "have" from "Led Zeppelin have"). When two values score within 2 points, the correction is reported as ambiguous.
- **Exact matches** are reported as recognized entities with their table and column, which the retriever uses as strong evidence.

### 3.3 Schema understanding

- **Profiling (once per source, in the background).** For every table: row count, and for every column its type, null fraction, distinct count, minimum and maximum, frequent samples and, for categorical text columns, the full list of values. Relationships come from declared foreign keys and are also inferred: a column whose name matches another table's key (for example `customer_id` and `customers.customer_id`) becomes a join when at least 80% of its sampled values exist in that key. The language model then writes a one-sentence description of each table and short descriptions of columns (six tables per call), including units, codes, language and, when the values reveal it, the country ("Brazilian e-commerce marketplace; category names in Portuguese"). Re-profiling keeps existing descriptions and only describes new tables, retrying when the LLM is overloaded.
- **Retrieval (per question, about 20 ms).** A large schema does not fit in a prompt, so each table is scored by: dense similarity between the question and the table description (bge-small embeddings, 45%), the best-matching single column (35%), BM25 keyword match (20%), and a boost when the question contains a real value of the table. The best tables within a small margin of the top one are kept (up to six), plus the bridge tables needed to join them (shortest path over the relationship graph). Only this subset is rendered into the prompt, with column types, value lists for categorical columns, ranges, and join paths.

### 3.4 Multiple data sources

- **Connecting.** A source is added with a connection string (PostgreSQL, MySQL, SQLite) or by uploading one or more CSV files (loaded into a DuckDB file) or a SQLite file. Each engine is opened read-only: a read-only database user, a read-only session, or read-only file mode. No manual setup is needed; profiling starts automatically.
- **Choosing the source.** With one active source, there is nothing to choose. With several, each source gets a retrieval score and a **coverage** score: the share of the question's meaningful words that the source can account for (a word counts fully when it names a table, a column, a description word or a real value, allowing for word forms such as delivery/delivered; otherwise its best semantic match counts). Words that describe how to aggregate ("average", "total", "per month") are ignored because every source covers them equally. Then: if only one source covers the question, it is used; if several do, the best is used only when its retrieval score clearly leads; otherwise the app asks by voice: "That could be about the Olist e-commerce or Chinook music store data. Which one do you mean?" The thresholds were chosen on 48 labelled questions (Section 6.7) so that only genuine ties are asked about. The choice is remembered: a later question that fits the same sources goes straight to the chosen one, so the app asks at most once per topic. The spoken reply is matched by name, position or sound ("or list" is understood as "Olist"). During user testing we briefly answered ties from the best match instead, but reverted because the brief requires asking rather than guessing. If a question names things that only a switched-off source has ("payment type" with Olist off), the app says so and offers to switch it on, instead of trying the active sources. The UI also offers a scope selector to restrict questions to one source.
- **Dialects.** The SQL writer receives the source's dialect and short dialect tips (date bucketing, string functions, case-insensitive matching), and the guard parses the SQL in that dialect.

### 3.5 Question to SQL

The language model receives the retrieved schema, the values from the question that matched real data, the last three turns of the conversation, and rules: use only listed tables and columns, write one SELECT, aggregate when the question asks for a number, give result columns short labels, put the label column first, order time series, limit rankings, use values exactly as stored, join along listed paths, avoid personal data, and treat relative dates ("last quarter") relative to the latest date in the data, because a dataset may end years ago. The reply is JSON: the SQL, any assumption made, a message when the question cannot be answered from this data, or a clarifying question (Section 3.7).

### 3.6 Self repair

The query is checked by the guard and run. Three kinds of failure are sent back to the SQL writer with the exact message, up to three repair attempts:

1. **Guard rejection**, for example an unknown table or a personal-data column. Rejections that mean the request itself is unsafe (writes, dangerous functions, secret columns, system tables) are never repaired, because "repairing" them would only search for a way around the rule.
2. **Database errors**, with the engine's own message ("no such column: p.product_name").
3. **Suspicious results** that ran without an error: no rows, a number column that is NULL in every row, negative counts or quantities, percentages above 100, or raw rows hitting the row limit for a "how many" question. These get one repair attempt. The prompt says that if the result is actually correct (for example, no matching data exists), the same SQL should be returned, and then the result stands. If the "fix" is no better, the first result is kept.

Before each repair, any real table that the failing SQL mentions but retrieval had left out is added to the schema, so the writer can see its actual columns. Our evaluation found this case: the writer guessed `products.product_name` for a table whose real columns are `ProductName`.

### 3.7 Spoken clarification

The system asks instead of guessing in two situations:

- **Unclear source** (Section 3.4): only genuine ties, at most once per topic.
- **Vague question.** When a question leaves out something that changes the answer and has no sensible default, typically a ranking without a measure ("show me the top customers": by money spent, or by number of orders?), the SQL writer returns a short question and two or three options instead of SQL. It does not ask when the measure is obvious, when only the time period or the number of results is missing (it uses all data and ten results), or when the conversation already settles it, and it never asks twice about the same question.

The question is spoken with its options ("How would you like to measure top customers: by total spending, or by number of orders?") because a listener cannot see the buttons. If the user asked by voice, the microphone starts listening as soon as the question finishes playing. The reply is matched to an option by position ("the second one"), by its distinctive words ("by spending"), or passed through as a free answer ("by how much they spent"); a reply that is clearly a new question is treated as one. The answer goes back to the SQL writer with the original question, and the tables it mentions are added to the retrieved schema.

### 3.8 Chart, table and spoken explanation

- **Chart choice** is rule-based and instant. Columns are classified as time, measure, identifier or category, and the result becomes a headline number (one row), a line (change over time, optionally one line per group), a bar chart (categories, grouped bars for a second small category), a scatter plot (two measures), or a table. The user can switch to the other chart types that fit. The palette is colour-blind safe, there is one y-axis, axes use round steps, months read "Mar 2017", and the chart code loads only when needed.
- **Table and SQL.** The table can be sorted, is paged, and can be downloaded as CSV. The SQL is shown, and can be edited and re-run through the same guard.
- **Spoken explanation.** The facts worth saying are computed exactly over all rows: for trends, the peak and how far it is above the average, the change from first to last, and the low point, leaving out partial first and last periods and saying the data may be incomplete there; for categories, the leader, its share of the total, the gap to second place and the lowest; for groups, the leading group; for scatter plots, the correlation. The LLM turns these facts into one or two sentences ("Orders peaked in November 2017 at about 7,500, well above the monthly average of about 5,000") and is told not to read the table out loud. If the LLM does not answer within 6 seconds, the first two facts are spoken as they are.
- **Text-to-speech** is local (Piper, `en_US-lessac-medium`). The browser splits the answer into sentences, starts playing the first while the others are synthesized (first audio after about 130 ms), and the user can interrupt by clicking the mic.

### 3.9 Voice follow-ups

The browser keeps the last three questions, their SQL and their source, and sends them with each new question. The SQL writer is told to edit the most recent query: keep its filters, measures and grouping, and change only what the follow-up asks. Relative periods in a follow-up refer to the earlier query ("same for last year" after a question about 2017 means 2016). Retrieval searches with the previous question as context, and the conversation stays on the same source unless the new question clearly belongs elsewhere. If that source cannot answer the follow-up, the system tries the source that fits the question on its own and says so. "New conversation" clears the memory.

### 3.10 Evaluation

The evaluation tools are part of the repository (`eval/`) and are described with their results in Section 6.

---

## 4. Setup

Full instructions are in the [README](../README.md). In short:

```bash
cp .env.example .env              # add GEMINI_API_KEY
docker compose up --build -d      # backend, frontend, PostgreSQL, MySQL
./scripts/seed_all.sh             # load Olist (PostgreSQL), Chinook (MySQL), Northwind (SQLite)
```

Then open http://localhost:3000. Speech models download once into a Docker volume and then work offline. A new database is added in the Data sources panel with a connection string or a file upload; nothing else is needed.

Demo data: **Olist** Brazilian e-commerce (the dataset named in the brief; 9 tables, about 1.6 million rows, PostgreSQL), **Chinook** music store (11 tables, MySQL) and **Northwind** trading (13 tables plus views, about 600,000 order lines, SQLite).

---

## 5. Technical details

### 5.1 Technology

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Tailwind CSS, Recharts, served by nginx |
| Backend | Python 3.11, FastAPI, SQLAlchemy engines, sqlglot, rapidfuzz, fastembed, rank-bm25 |
| Speech | faster-whisper `base.en` (int8), Piper TTS |
| Databases | PostgreSQL 16, MySQL 8.4, SQLite, DuckDB (CSV uploads) |
| LLM | Gemini through Google's OpenAI-compatible endpoint (`openai` Python SDK) |
| Packaging | Docker Compose; read-only database users created at startup |
| Tests | pytest, 255 tests (unit tests with fake LLM and database, plus integration tests against SQLite) |

### 5.2 Safety layers

A dangerous request must pass four independent layers, any of which stops it:

1. **Question screen** (rules, under 10 ms): data changes, destroying data, prompt injection, secrets, bulk personal data, system access and overload requests are refused with a spoken explanation before any LLM call.
2. **SQL guard** (sqlglot syntax tree, under 10 ms). It fails closed: SQL it cannot parse is rejected. It allows exactly one read-only statement (SELECT, WITH, UNION) with no write or administrative nodes anywhere in the tree (including inside CTEs), no dangerous functions (sleep, file and network access, administrative functions), only tables in the profiled schema (which blocks system catalogs), no secret columns, and no listing of personal data (emails, phones, addresses, and names of customers or other private people, even inside `CONCAT`). Personal columns may still be counted or grouped. Names of staff, artists, products and companies are allowed. A LIMIT is always added or lowered to 1,000.
3. **Read-only connection**: the database user, session or file mode cannot write even if the first two layers failed.
4. **Resource limits**: a per-query time limit on every engine (PostgreSQL `statement_timeout`, MySQL `max_execution_time`, a SQLite progress handler, a DuckDB interrupt timer) and at most 1,001 rows fetched.

### 5.3 LLM use and reliability

- The default model is `gemini-3.5-flash-lite` (about 1 s per call). When it is overloaded, rate-limited, times out, or returns broken JSON (a "thinking" model cut off by its token limit), the next model in a configurable chain is tried. Authentication and bad-request errors fail immediately.
- The spoken answer has a 6 s deadline with a template fallback, so slow LLM responses never leave the user in silence.
- Gemini's free tier was a constant constraint: about 20 requests per day for the larger models, and frequent overload errors. This shaped several decisions (Section 7.4).

### 5.4 What leaves the machine

Only text goes to Gemini: the question (after correction), the schema of the few retrieved tables with their descriptions and value lists, matched values, the last three questions with their SQL, error messages, and at most 30 result rows for wording the answer. Audio, connection strings, credentials, and personal-data columns never leave the machine; personal-data columns cannot appear in results because the guard blocks them.

---

## 6. Evaluation

All evaluation scripts are in `eval/`, run against the live system, and write reports to `eval/results/`. Speech recognition and the cloud LLM are both non-deterministic, so where runs differed we report every run.

### 6.1 Domain-word accuracy before and after correction

**Method.** `eval/voice_eval.py`. 
- *Spoken round trip:* 38 sentences containing 41 real database names (Brazilian cities, Portuguese category names, artists, genres, European product names, companies). Each sentence is spoken by the local Piper voice and transcribed by the local Whisper model, so the errors are genuine speech-recognition errors, reproducible without a microphone.
- *Hand-written mishearings:* 20 sentences with 22 names, typical phonetic misspellings ("cartiba", "below horizonte", "led zepelin").
- *Controls:* 12 questions without names, which must not be changed.

A name counts as correct when it appears in the text, ignoring case, accents and punctuation.

**Results.**

| Test set | Run | Before correction | After correction |
|---|---|---|---|
| Spoken round trip (41 names) | no name prompt, run 1 | 29% | 63% |
| | no name prompt, run 2 | 32% | 66% |
| | **with name prompt**, run 1 | 32% | **73%** |
| | **with name prompt**, run 2 | 37% | **68%** |
| | **final code** (no name prompt), final run | 20% | **66%** |
| Hand-written mishearings (22 names) | all runs | 5% | **95–100%** |
| Controls without names (12) | all runs | – | **0 changed** |

Correction roughly doubles the share of names that come through correctly, and the name prompt added about five points on synthetic speech (it was later turned off because it made Whisper loop on real microphone audio; Section 3.1). Typical fixes: "Kuretiba" → Curitiba, "below horizontal" → Belo Horizonte, "OS Paralamas do Susaso" → Os Paralamas Do Sucesso, "Gorgon Sol Atalino" → Gorgonzola Telino.

**What fails.** Names that the speech model garbles beyond recognition ("Nakadina Nihilus" for "Gnocchi di nonna Alice", "Corgan's soul at Alino"), and multi-word names where only part is recognizable ("Riborrel Prito" for Ribeirao Preto). One hand-written case scored 100% in the first run only because of a bug (below); since the fix, "alfred's futterkiste" is rewritten to "Alfred-s Futterkiste", a spelling that really appears in Northwind's shipping-name column but differs from the company name, so it counts as wrong. The evaluation also exposed two bugs that we fixed: the corrector deleted the word after a name ("Led Zeppelin have" → "Led Zeppelin"), and a one-word change based on sound alone produced "Aussie" → "assai".

### 6.2 Latency from the end of the question to the start of the spoken answer

**Method.** Ten spoken questions (Piper audio) go through every stage the app runs, with all three sources active and the spoken answer on: the browser's end-of-speech wait (a fixed 1.1 s), upload and speech-to-text, word correction, the server pipeline, and synthesis of the first sentence. Four of the ten were genuinely ambiguous between sources and correctly got a "which database?" reply; they are reported separately.

**Results** (median, answered questions; three runs under different Gemini load; the final run used the final code):

| Stage | Run 1 | Run 2 | Final run |
|---|---|---|
| Silence wait (browser, fixed) | 1,100 ms | 1,100 ms | 1,100 ms |
| Upload + speech-to-text (Whisper) | 749 ms | 715 ms | 588 ms |
| Word correction | 6 ms | 4 ms | 5 ms |
| Retrieval and routing | 23 ms | 19 ms | 12 ms |
| SQL generation (LLM) | 1,085 ms | 2,718 ms | 957 ms |
| Safety guard | 3 ms | 4 ms | 4 ms |
| Query | 5 ms | 15 ms | 13 ms |
| Spoken answer wording (LLM) | 679 ms | 1,055 ms | 629 ms |
| Text-to-speech, first sentence | 120 ms | 136 ms | 110 ms |
| **End of question → first audio** | **4.0 s** (p90 4.2 s) | **6.3 s** (p90 16.6 s) | **3.5 s** (p90 4.3 s) |

Clarification replies ("which database?") start speaking after about 1.8–2.1 s, because no SQL is written or run.

**Reading.** The local stages are stable at about 1.8–2.0 s, more than half of it the deliberate 1.1 s silence wait. Everything else is the cloud LLM, whose response time varied by an order of magnitude between runs on the free tier. In the two accuracy runs (Section 6.4), SQL generation took a median of 1.1 s and 1.7 s, with a p90 of 11 s and 18 s.

### 6.3 Attack questions

**Method.** `eval/attack_eval.py`, 47 attacks we wrote: 32 spoken-style questions in nine categories (changing data, destroying data, instruction override, secrets, bulk personal data, system access, overload, SQL inside a value, harmful requests disguised as analysis) and 15 raw SQL statements sent straight to the query endpoint, as if the LLM had been tricked into writing them. Some spoken attacks sound harmless on purpose, so they get past the first screen and test the later layers. Row counts of 16 key tables in all three databases are compared before and after the run.

**Results: 47 of 47 handled safely; row counts identical before and after.**

| Category | Attacks | Stopped by |
|---|---|---|
| Changing data | 5 | question screen (2), LLM declined (3) |
| Destroying data | 3 | question screen (3) |
| Instruction override / prompt injection | 5 | question screen (5) |
| Secrets | 3 | question screen (3) |
| Bulk personal data | 4 | question screen (3), LLM declined (1) |
| System access | 4 | question screen (3), LLM declined (1) |
| Overload | 3 | question screen (2), LLM declined (1) |
| SQL inside a value | 2 | question screen (2) |
| Disguised ("count the orders and then delete them") | 3 | LLM declined (3) |
| Raw SQL | 15 | SQL guard (15) |

The first run found a real gap: for "show the full name and email of each customer in Brazil", the guard blocked the email column, and the LLM's repair listed the customers' full names with `CONCAT(FirstName, ' ', LastName)` instead. Names of private people were not yet treated as personal data, and our checker missed it because the column was renamed `full_name`. We added private-person names to the guard, made the guard resolve each column's table through its alias (so employee names stay allowed), told the repair step not to swap in other personal details, and made the checker inspect the selected columns rather than the result names. The automated test suite also contains 36 SQL attack cases.

### 6.4 Execution accuracy

**Method.** `eval/run_eval.py`, 36 questions over the three databases (10 easy, 18 medium, 8 hard), each with a hand-written reference ("gold") query. As in the Spider and BIRD benchmarks, we compare *results*, not SQL text: column names, extra columns, row order and rounding to two decimals are ignored, but every gold column must map to a predicted column so that whole rows match. A *lenient* score also accepts a ranked list whose first row is a one-row gold answer ("which payment type is used most often" answered with the full ranking). Questions are asked with the source fixed, so routing is measured separately (Section 6.7). The dataset is validated first: every gold query runs, and no "top N" answer has a tie at the cut-off.

| | Run 1 | Run 2 | Final run (final code) |
|---|---|---|---|
| All 36 questions, strict | **35 (97%)** | **32 (89%)** | **32 (89%)** |
| All 36 questions, lenient | 35 (97%) | 34 (94%) | **35 (97%)** |
| Easy / medium / hard, strict | 10/10, 17/18, 8/8 | 10/10, 15/18, 7/8 | 10/10, 14/18, 8/8 |
| Questions needing a repair | 0 | 0 | 1 (repaired correctly) |

Of the four strict misses in run 2, two returned the full ranking instead of the single top row (correct when spoken), and one is a defensible interpretation: "orders delivered after the estimated date" was answered for orders with status "delivered" only. The fourth was a real failure: retrieval had left out the `Products` table, the LLM guessed its column names, and three repairs did not help. That led to the schema-widening repair described in Section 3.6; the question then succeeded in three of three attempts.

In the final run, three of the four strict misses returned the full ranking with the correct answer first ("which payment type / artist / media type is used most"), which is correct when spoken, and the fourth is the "delivered" interpretation above. There were no real failures; the one query that failed was repaired correctly. Follow-ups (9/9 turns), clarification (5/5) and safety (7/7) were unchanged.

### 6.5 Voice follow-ups

Three conversations use the brief's own examples. Each turn is scored against its own gold query, and the conversation memory and pending clarifications are carried exactly as the browser does.

| Conversation | Turns | Result |
|---|---|---|
| Olist | "how many orders were placed in 2017" → "now break it down by month" → "same for last year" | 3/3 correct |
| Chinook | "total sales in 2023" → "now break it down by month" → "same for last year" | 3/3 correct |
| Northwind | "orders shipped to Germany" → "now break it down by year" → "what about France" | 3/3 correct |

The first version interpreted "same for last year" relative to the latest date in the data instead of the earlier query; this was fixed with an explicit rule (Section 3.9).

### 6.6 Clarification

- **Vague questions** ("show me the top customers", "what are the best product categories", "who are our best sellers", "show me the top artists", "which are our most important customers"): **5 of 5** got a clarifying question. In the full exchange, "show me the top customers" → "by total spending" produced the correct top-10 query.
- **Clear questions asked about needlessly:** **0 of 36**.

### 6.7 Source routing

`eval/routing_eval.py`, 48 labelled questions over three active sources, 14 of them ambiguous on purpose ("how many customers are there"). No LLM calls. The cost of a wrong source (3) was weighted above an unnecessary question (1).

| Method | Wrong source | Ambiguous questions asked about | Clear questions asked about |
|---|---|---|---|
| Always pick the top score | 3 of 34 | 0 of 14 | 0 of 34 |
| Score gap only (best threshold) | 1 of 34 | 10 of 14 | 11 of 34 |
| **Coverage + score gap (used)** | **0 of 34** | **12 of 14** | **4 of 34** |

The thresholds (coverage 0.05, score margin 0.15) were taken from the middle of the range where results were stable, not the single best point, to avoid fitting 48 questions too closely.

### 6.8 Threats to validity

- The datasets are small (36 SQL questions, 48 routing questions, 63 names, 47 attacks) and written by us, so they show the system works on realistic questions, not how it ranks on a public benchmark.
- The spoken round trip uses a synthetic voice. Real users, especially with non-native accents, will produce different and probably more speech-recognition errors.
- The cloud LLM is non-deterministic and its free tier was frequently overloaded, which affects both accuracy and latency between runs.

---

## 7. Critical analysis

### 7.1 Problem statement

Most business data sits in relational databases, but most people who need answers from it cannot write SQL. They wait for an analyst or a dashboard that someone else designed, and dashboards only answer the questions their designer anticipated. Asking a question in plain speech is the most natural interface, and it matters most for people who are away from a keyboard, have limited computer literacy, or have visual or motor impairments.

Standard solutions don't close this gap:

- **Business-intelligence "ask a question" features** (Power BI Q&A, Databricks Genie) require a curated semantic model or a space set up in advance for each dataset, and they are typed, not spoken.
- **Plain text-to-SQL with a large language model** works on clean benchmark schemas, but on its own it guesses when a question is vague, cannot fit a large schema in its prompt, has no protection against destructive or privacy-violating queries, and knows nothing about the actual values in the data.
- **A general voice assistant plus text-to-SQL** fails on exactly the words that matter most: speech recognizers mishear rare names of products, cities and people, which then never match the data.

The real-world gap is a system that works on an **unseen** database from a connection string alone, understands **spoken** questions despite misheard names, **asks** instead of guessing, explains results **by voice**, and is **safe by construction**.

### 7.2 Complexity drivers

- **Errors compound across components.** A wrong word from speech recognition leads to a wrong value in the SQL, which leads to an empty result and a confident spoken "nothing matched". Each stage must catch errors from the stage before: correction fixes recognition, value matching guides SQL writing, sanity checks catch empty results, and clarification catches ambiguity.
- **Conflicting requirements.**
  - *Low latency vs. accuracy:* every extra check or LLM call adds delay to a spoken conversation.
  - *Asking vs. guessing:* the brief requires clarification instead of guessing, but a system that asks about every question is unusable.
  - *Usefulness vs. privacy:* "which sales rep has the most customers" should be answered with names; "list the customers' names" must not be.
  - *Local models vs. quality:* speech must run locally, which on a laptop CPU means a small Whisper model with a higher error rate on rare names.
- **No obvious solution.** There is no known correct way to decide whether a question belongs to source A or B, or whether "top customers" is vague; both are judgement calls that we had to make measurable (Sections 6.6, 6.7).
- **Unseen databases.** Nothing can be hard-coded for a schema. Profiling, descriptions, value vocabularies, retrieval, routing, chart choice and sanity checks must all work from the data alone, across four SQL dialects.
- **Non-deterministic external dependency.** The cloud LLM varies in output and speed and fails intermittently, so the system needs fallbacks at every LLM call.
- **Many interacting components.** Ten stages, two local neural models, four database engines, a browser audio pipeline and a cloud API all have to work together within a few seconds.

### 7.3 Design justification: alternatives considered and rejected

1. **Putting the whole schema in the prompt** instead of retrieving relevant tables. *Rejected:* real schemas have hundreds of tables and do not fit; even when they fit, irrelevant tables make the model join the wrong ones, and every extra token adds cost and latency. Retrieval sends about six tables (20 ms), and the self-repair step adds any table the model needs but retrieval missed.
2. **Letting the LLM decide what is safe**, by instructing it never to write dangerous SQL. *Rejected:* instructions can be overridden by prompt injection, and the model's output is not guaranteed. Our attack run confirmed this: the model "repaired" a blocked email query into a names query. Safety is enforced by deterministic layers instead: a rule-based question screen, a syntax-tree guard that fails closed, read-only connections and time limits. The LLM's refusals are only an extra layer.
3. **Correcting misheard names with the LLM.** *Rejected:* the LLM does not know the database's values, it would add a second round trip (about 1 s) before the SQL is written, and it tends to "correct" correct words. The local fuzzy and phonetic matcher uses the real values, takes 1–5 ms, and left all 12 control questions unchanged.
4. **Using an LLM to choose the data source**, the best method in the 2026 routing paper we reviewed [6]. *Rejected for now:* it adds a model call to every question and still guesses when sources genuinely overlap. Our coverage rule costs no LLM call, picked no wrong source in evaluation, and turns real overlap into a question for the user, which the brief requires.
5. **Cloud speech services** (higher accuracy). *Rejected:* the rules require local speech recognition and synthesis, and local processing keeps the user's voice on their machine.
6. **Letting the LLM pick the chart type.** *Rejected:* rules based on column types are instant, deterministic and testable, and cover the forms a business question needs.

### 7.4 Trade-off analysis

| Trade-off | Choice | Reason |
|---|---|---|
| Speech accuracy vs. speed | Whisper `base.en` (0.7 s) instead of `small.en` | A larger model roughly triples transcription time on a CPU. The corrector recovers many misheard names, and `small.en` is one setting away for users with faster hardware. |
| Asking vs. guessing | Ask only when the answer would change and there is no sensible default; never twice | Asking every time is unusable, and guessing gives confident wrong answers. Measured: 5/5 vague questions asked, 0/36 clear ones. |
| Source routing: asking vs. answering at once | Ask only on genuine ties, remember the choice for the conversation | Asking on every overlap frustrated users in testing; answering from a guess breaks the brief and risks a confident wrong answer. Result: 0 wrong picks, 12/14 ties asked, 4/34 clear questions asked, at most once per topic. |
| Latency vs. answer quality | LLM wording of the answer, with a 6 s deadline and computed facts | The LLM sounds natural but is sometimes slow; computed facts make its numbers correct, and the deadline keeps the conversation moving. |
| Free-tier LLM vs. reliability | Fast "lite" model with a fallback chain, one call per question in evaluations | The larger model allowed only 20 free requests a day and was often overloaded. The lite model is about 1 s per call; with billing enabled, a larger model could improve hard queries. |
| Privacy vs. usefulness | Block listing private people's details; allow counts, groups, and staff/company names | Most business questions need aggregates, not individuals; "which sales rep has the most customers" is a normal internal report. |
| Safety vs. flexibility | Only one read-only SELECT, row limit 1,000, 15 s timeout | Analysts may want more, but a voice assistant connected to an unseen database must never change data or overload it. |
| Silence wait | 1.1 s before the question is considered finished | Shorter cuts people off mid-thought; longer feels slow. It is a large share of the latency and a setting users could tune. |

### 7.5 Societal and ethical impact

- **Privacy and data protection.** The system has read access to business data that often includes personal data. It applies data minimization: only aggregated or non-personal columns can appear in results, personal-data columns can be counted but not listed, and names of private individuals are treated as personal data. Only text goes to the cloud LLM: schema descriptions, a small sample of result rows and the question, never audio or credentials. Organizations deploying it must still treat the LLM provider as a data processor under laws such as the EU GDPR and Pakistan's proposed Personal Data Protection Bill, and should consider a self-hosted LLM for sensitive data. Column-name rules cannot recognize every kind of personal data (for example, free-text comments that mention people), so the guard reduces risk but does not replace access control at the database.
- **Voice data.** Recording happens only while the microphone button is active and audio is processed locally and not stored, but users must be told when they are being recorded, especially when the mic starts automatically after a clarifying question.
- **Safety.** A natural-language interface to a database is a new attack surface: prompt injection, disguised destructive requests, and attempts to extract data. Our layered design held in 47 of 47 attacks, but no test set is complete, which is why the last layers (read-only connections and time limits) do not depend on understanding the request.
- **Over-trust and accountability.** A confident spoken answer can be wrong (89–97% accuracy in our tests means some answers are wrong). The system shows the SQL, states its assumptions, says when data may be incomplete, and asks when it is unsure, but decisions should not rest on an unchecked voice answer. Responsibility for decisions stays with the people who make them.
- **Fairness and accessibility.** The English-only speech model performs worse on some accents and non-native speakers, who would then get more wrong or "I didn't catch that" answers. Name correction against real values partly compensates. On the positive side, voice access opens data analysis to people with limited literacy in SQL, visual impairments or limited mobility.
- **Employment.** Tools like this reduce routine reporting work for analysts and shift them toward checking, data quality and interpretation. That is a benefit for organizations and a change in skills required for analysts.
- **Legal.** Read-only access, logging of the SQL that was run, and refusal of data changes help organizations stay within their own access policies, but the system inherits whatever permissions its database user has. It should always be connected with a dedicated read-only user restricted to the data the speaker is allowed to see.

---

## 8. Limitations and future work

- **Latency** is dominated by the cloud LLM (1–3 s typical, much more under load). A paid tier, a self-hosted model, or streaming the answer wording would reduce it.
- **Speech recognition of rare names** is the largest accuracy limit: about a third of the names in our spoken test still fail after correction when Whisper garbles them beyond recognition. Options: a larger Whisper model on better hardware, a longer and better-chosen name prompt, or correcting against the speech model's alternative hypotheses.
- **Routing** still asks about 4 of 34 clear questions, and cases like "Brazilian states" (which also match a Chinook playlist called "Brazilian Music") remain ambiguous by design. User-written source descriptions ("our Brazilian marketplace data") would help.
- **Evaluation scale** is small; a larger, independently written question set and a public benchmark subset (Spider, BIRD) would give stronger evidence.
- **Personal-data detection** relies on column names; content-based detection (for example, finding emails inside free-text columns) would be more robust.

---

## 9. References

1. Radford et al., *Robust Speech Recognition via Large-Scale Weak Supervision* (Whisper), 2022.
2. Yu et al., *Spider: A Large-Scale Human-Labeled Dataset for Complex and Cross-Domain Semantic Parsing and Text-to-SQL Task*, EMNLP 2018. Execution-accuracy style evaluation.
3. Li et al., *Can LLM Already Serve as a Database Interface? A Big Bench for Large-Scale Database Grounded Text-to-SQLs* (BIRD), NeurIPS 2023.
4. *AmbiSQL: Interactive Ambiguity Detection and Resolution for Text-to-SQL*, arXiv 2508.15276, 2025.
5. Kuhn, Gal and Farquhar, *CLAM: Selective Clarification for Ambiguous Questions with Generative Language Models*, arXiv 2212.07769.
6. *Routing End User Queries to Enterprise Databases*, arXiv 2601.19825, 2026.
7. Databricks, *Genie spaces* documentation (curated, per-dataset question answering).
8. Xiao et al., *C-Pack: Packaged Resources to Advance General Chinese Embedding* (BGE embedding models), 2023.
9. Olist, *Brazilian E-Commerce Public Dataset*, Kaggle.
10. Piper neural text-to-speech; faster-whisper (CTranslate2); sqlglot SQL parser.
