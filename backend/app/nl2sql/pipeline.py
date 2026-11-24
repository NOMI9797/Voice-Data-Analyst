"""The full question -> answer pipeline.

  screen question -> retrieve schema -> LLM writes SQL -> safety guard -> run
        ^                                     |                |         |
        |                                     +--- repair <----+---------+  (error fed back, up to N times)
  spoken answer + chart  <--------------------------------------------------+

Every stage is timed; the result says which stage stopped it and why, in a
sentence that can be spoken.
"""

from __future__ import annotations

import logging
import time
from dataclasses import asdict

from app.config import get_settings
from app.llm import client as llm
from app.nl2sql.answer import spoken_answer, template_answer
from app.nl2sql.generator import Generation, Turn, generate_sql, repair_sql
from app.profiler.retriever import retrieve
from app.profiler.routing import resolve_choice
from app.profiler.store import get_store
from app.safety.executor import QueryError, run_query
from app.safety.guard import check_sql
from app.safety.intent import screen_question
from app.viz.chart import choose_chart

log = logging.getLogger(__name__)

# Guard rejections that are never sent back for repair: the question asked for
# something unsafe, and "repairing" it would only search for a way around the rule.
# (parse errors, unknown tables, personal-data columns etc. ARE repaired: the LLM
# can fix a typo or aggregate instead of listing emails.)
HARD_STOP = {"write_operation", "not_select", "dangerous_function", "secret_column",
             "system_table", "table_function"}


def allowed_tables(source_id: str) -> dict[str, list[str]] | None:
    prof = get_store().get(source_id)
    if prof is None:
        return None
    return {t.name: [c.name for c in t.columns] for t in prof.tables}


class _Clock:
    def __init__(self):
        self.timings: dict[str, float] = {}
        self._t0 = time.perf_counter()

    def lap(self, name: str, since: float) -> None:
        self.timings[name] = round(self.timings.get(name, 0) + (time.perf_counter() - since) * 1000, 1)

    def total(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)


def _spoken_choices(names: list[str]) -> str:
    names = [f"{n}" for n in names]
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    return f"That could be about the {listed} data. Which one do you mean?"


def ask(question: str, history: list[Turn] | None = None, source_ids: list[str] | None = None,
        pending: dict | None = None, speak: bool = True) -> dict:
    """pending: the clarification we asked last time, {"question": ..., "options": [{source_id, name}]}.
    If this question is an answer to it ("Olist", "the second one"), the ORIGINAL
    question is answered on the chosen source; otherwise it is a new question."""
    s = get_settings()
    clock = _Clock()
    out: dict = {"question": question, "status": "", "answer": "", "sql": None, "result": None,
                 "attempts": [], "assumption": "", "retrieval": None, "timings": clock.timings}

    def done(status: str, answer: str) -> dict:
        out["status"], out["answer"] = status, answer
        out["timings"]["total"] = clock.total()
        log.info("ask %s in %.0f ms: %s", status, out["timings"]["total"], question[:80])
        return out

    if pending and pending.get("options"):
        chosen = resolve_choice(question, pending["options"])
        if chosen:
            out["chosen_source"] = chosen
            question = out["question"] = pending["question"]
            source_ids = [chosen]

    # 1. screen
    t = time.perf_counter()
    screen = screen_question(question)
    clock.lap("screen", t)
    if not screen.allowed:
        out["refusal"] = {"category": screen.category, "matched": screen.matched}
        return done("refused", screen.message)
    if not llm.available():
        return done("failed", "The language model key is not set, so I can't answer questions yet.")

    # 2. retrieve
    hist = (history or [])[-3:]
    prev = hist[-1] if hist else None
    t = time.perf_counter()
    r = retrieve(question, source_ids, prefer_source=prev.source_id if prev else None,
                 context=prev.question if prev else "")
    clock.lap("retrieval", t)
    out["retrieval"] = asdict(r)
    if not r.source_id:
        return done("no_source", "No data source is active. Turn one on in the sources panel first.")
    if r.choices:
        out["choices"] = r.choices
        return done("clarify", _spoken_choices([c["name"] for c in r.choices]))
    tables = allowed_tables(r.source_id)
    if tables is None:
        return done("not_ready", f"The {r.source_name} database is still being analysed. Try again in a moment.")
    out["source_id"], out["source_name"] = r.source_id, r.source_name

    # 3. generate (earlier turns on another source would only confuse the SQL writer)
    hist = [h for h in hist if not h.source_id or h.source_id == r.source_id]
    t = time.perf_counter()
    try:
        gen: Generation = generate_sql(question, r.schema_text, r.dialect, r.value_hits, hist)
    except llm.LLMError as exc:
        clock.lap("sql generation", t)
        log.warning("generation failed: %s", exc)
        return done("failed", "I couldn't reach the language model just now. Please try again in a moment.")
    clock.lap("sql generation", t)
    out["assumption"] = gen.assumption
    if not gen.sql and prev and r.source_id == prev.source_id:
        # We stayed on the previous question's source, but it can't answer this one.
        # Maybe the user changed topic: retry once on the source that fits the
        # question by itself.
        t = time.perf_counter()
        alt = retrieve(question, source_ids)
        clock.lap("retrieval", t)
        if alt.source_id and not alt.choices and alt.source_id != r.source_id \
                and (alt_tables := allowed_tables(alt.source_id)):
            t = time.perf_counter()
            try:
                alt_gen = generate_sql(question, alt.schema_text, alt.dialect, alt.value_hits, [])
            except llm.LLMError:
                alt_gen = None
            clock.lap("sql generation", t)
            if alt_gen and alt_gen.sql:
                log.info("follow-up switched source %s -> %s", r.source_name, alt.source_name)
                r, tables, gen, hist = alt, alt_tables, alt_gen, []
                out["retrieval"] = asdict(r)
                out["source_id"], out["source_name"] = r.source_id, r.source_name
                out["assumption"] = gen.assumption
                out["switched_source"] = True
    if not gen.sql:
        return done("cannot_answer", gen.message or f"I can't answer that from the {r.source_name} data.")

    # 4. guard + run, repairing on failure
    sql = gen.sql
    for attempt in range(s.max_repair_attempts + 1):
        t = time.perf_counter()
        guard = check_sql(sql, r.dialect, tables, row_limit=s.query_row_limit)
        clock.lap("safety guard", t)
        error = ""
        if not guard.ok:
            error = f"Rejected by the safety checker: {guard.reason}"
            out["attempts"].append({"sql": sql, "stage": "guard", "error": guard.reason, "code": guard.code})
            if guard.code in HARD_STOP:
                # a write or a secret is never "repaired" into something else; stop here
                out["sql"] = sql
                return done("refused", f"I can't run that. {guard.reason}")
        else:
            t = time.perf_counter()
            try:
                result = run_query(r.source_id, guard.sql)
            except QueryError as exc:
                error = f"Database error: {exc}"
                out["attempts"].append({"sql": guard.sql, "stage": "database", "error": str(exc)})
            clock.lap("query", t)
            if not error:
                out["sql"] = guard.sql
                break
        if attempt == s.max_repair_attempts:
            out["sql"] = sql
            return done("failed", "I wrote a query for that but couldn't get it to run. "
                                  "You can see what I tried in the SQL tab.")
        t = time.perf_counter()
        try:
            fix = repair_sql(question, r.schema_text, r.dialect, sql, error, r.value_hits, hist)
        except llm.LLMError:
            clock.lap("sql repair", t)
            out["sql"] = sql
            return done("failed", "I couldn't reach the language model to fix the query. Please try again.")
        clock.lap("sql repair", t)
        if not fix.sql:
            out["sql"] = sql
            return done("cannot_answer", fix.message or "I couldn't find a way to answer that from this data.")
        sql = fix.sql

    # 5. chart + spoken answer
    t = time.perf_counter()
    chart = choose_chart(result["columns"], result["rows"])
    out["result"] = {"ok": True, "sql": out["sql"], **result, "chart": chart}
    clock.lap("chart", t)
    t = time.perf_counter()
    if not speak:  # evaluation runs: skip the answer LLM call (half the quota per question)
        answer, by_llm = template_answer(result["columns"], result["rows"], chart), False
    else:
        answer, by_llm = spoken_answer(question, result["columns"], result["rows"], result["row_count"],
                                       result["truncated"], chart, gen.assumption)
    clock.lap("answer", t)
    out["answer_by_llm"] = by_llm
    if out.get("switched_source"):
        answer = f"That's not in the earlier data, so I looked in {r.source_name}. {answer}"
    return done("answered", answer)
