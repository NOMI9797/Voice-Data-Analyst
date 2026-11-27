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
import re
import time
from dataclasses import asdict

from app.config import get_settings
from app.llm import client as llm
from app.nl2sql.sanity import suspicious
from app.nl2sql.answer import spoken_answer, template_answer
from app.nl2sql.generator import Generation, Turn, generate_sql, repair_sql
from app.profiler.retriever import add_tables, retrieve, switched_off_fit, tables_named_in, widen
from app.profiler.routing import resolve_choice, resolve_intent_reply
from app.profiler.store import get_store
from app.safety.executor import QueryError, run_query
from app.safety.guard import check_sql
from app.safety.intent import screen_question
from app.sources.registry import get_registry
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


def _same_sql(a: str, b: str) -> bool:
    # whitespace only: case matters inside quoted values ('Delivered' vs 'delivered')
    norm = lambda q: re.sub(r"\s+", " ", q.strip().rstrip(";"))  # noqa: E731
    return norm(a) == norm(b)


def _spoken_intent(question: str, options: list[str]) -> str:
    """'How should I rank them?' + options -> 'How should I rank them: by total spending, or by
    number of orders?' A listener can't see the buttons, so the options are said out loud."""
    opts = [re.sub(r"\s*\(.*?\)", "", o).strip().rstrip(".?") for o in options if o.strip()]
    opts = [o[0].lower() + o[1:] if o[:1].isupper() and not o[:2].isupper() else o for o in opts]
    q = question.strip().rstrip("?").rstrip()
    if len(opts) < 2:
        return q + "?"
    return f"{q}: {', '.join(opts[:-1])}, or {opts[-1]}?"


def ask(question: str, history: list[Turn] | None = None, source_ids: list[str] | None = None,
        pending: dict | None = None, speak: bool = True) -> dict:
    """pending: the clarification we asked last time:
      {"kind": "source", "question": ..., "options": [{source_id, name}]}          which database?
      {"kind": "intent", "question": ..., "asked": ..., "options": [str], "source_id"}  what did you mean?
    If this question answers it ("Olist", "by revenue", "the second one"), the ORIGINAL
    question is answered with that choice; otherwise it is treated as a new question."""
    s = get_settings()
    clock = _Clock()
    out: dict = {"question": question, "status": "", "answer": "", "sql": None, "result": None,
                 "attempts": [], "assumption": "", "retrieval": None, "timings": clock.timings}

    def done(status: str, answer: str) -> dict:
        out["status"], out["answer"] = status, answer
        out["timings"]["total"] = clock.total()
        log.info("ask %s in %.0f ms: %s", status, out["timings"]["total"], question[:80])
        return out

    clarification = None
    if pending and pending.get("kind", "source") == "source" and pending.get("options"):
        chosen = resolve_choice(question, pending["options"])
        if chosen:
            out["chosen_source"] = chosen
            question = out["question"] = pending["question"]
            source_ids = [chosen]
    elif pending and pending.get("kind") == "intent":
        answer = resolve_intent_reply(question, pending.get("options") or [])
        if answer:
            clarification = {"asked": pending.get("asked", ""), "answer": answer}
            out["clarification"] = clarification
            question = pending["question"]
            out["question"] = f"{question} ({answer})"  # what history and the UI show
            if pending.get("source_id"):
                source_ids = [pending["source_id"]]

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
    context = " ".join(x for x in (prev.question if prev else "",
                                    clarification["answer"] if clarification else "") if x)
    r = retrieve(question, source_ids, prefer_source=prev.source_id if prev else None, context=context)
    clock.lap("retrieval", t)
    out["retrieval"] = asdict(r)
    if not r.source_id:
        off = [s.name for s in get_registry().list() if source_ids and s.id in source_ids and not s.active]
        if off:
            return done("no_source", f"The {off[0]} data is switched off. Turn it on under Your data to ask it.")
        return done("no_source", "No data source is switched on. Turn one on under Your data first.")
    if r.choices:
        # Several sources fit about equally. Rather than stop and ask, answer from the best
        # match, say which data was used, and offer the others as one-tap alternatives.
        out["other_sources"] = [c for c in r.choices if c["source_id"] != r.source_id]
    if clarification:  # make sure the tables the answer talks about ("money spent") are included
        t = time.perf_counter()
        r = widen(r, clarification["answer"])
        clock.lap("retrieval", t)
        out["retrieval"] = asdict(r)
    tables = allowed_tables(r.source_id)
    if tables is None:
        return done("not_ready", f"The {r.source_name} database is still being analysed. Try again in a moment.")
    out["source_id"], out["source_name"] = r.source_id, r.source_name

    # 3. generate (earlier turns on another source would only confuse the SQL writer)
    hist = [h for h in hist if not h.source_id or h.source_id == r.source_id]
    t = time.perf_counter()
    try:
        gen: Generation = generate_sql(question, r.schema_text, r.dialect, r.value_hits, hist, clarification)
    except llm.LLMError as exc:
        clock.lap("sql generation", t)
        log.warning("generation failed: %s", exc)
        return done("failed", "I couldn't reach the language model just now. Please try again in a moment.")
    clock.lap("sql generation", t)
    out["assumption"] = gen.assumption
    if not gen.sql and gen.clarify_question and clarification is None:
        # vague question: ask instead of guessing (never twice for the same question)
        spoken = _spoken_intent(gen.clarify_question, gen.clarify_options)
        out["clarify"] = {"kind": "intent", "asked": spoken, "options": gen.clarify_options,
                          "source_id": r.source_id}
        return done("clarify", spoken)
    # a pure follow-up ("now break it down by month") has no subject words of its own: if the
    # conversation's source can't do it, say so rather than answer something else elsewhere
    has_subject = any(sc.get("term_coverage") for sc in (r.source_scores or []))
    if not gen.sql and not gen.clarify_question and source_ids is None and has_subject:
        # the question may be about data in a source that is switched off: say so, rather than
        # trying the other active sources for something they don't have
        off = switched_off_fit(question)
        if off:
            out["switched_off_source"] = off
            out.pop("other_sources", None)  # the active ones don't have it either
            return done("cannot_answer", f"That isn't in the data that's switched on. It looks like it's in the "
                                         f"{off['name']} data, which is switched off. Turn it on under Your data to ask it.")
    if not gen.sql and not gen.clarify_question and prev and r.source_id == prev.source_id and has_subject:
        # We stayed on the previous question's source, but it can't answer this one.
        # Maybe the user changed topic: retry once on the source that fits the
        # question by itself.
        t = time.perf_counter()
        alt = retrieve(question, source_ids)
        clock.lap("retrieval", t)
        if alt.source_id and alt.source_id != r.source_id \
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

    # 4. guard + run, repairing on failure and (once) on a suspicious result
    sql = gen.sql
    kept = None  # (sql, result) of a run that looked suspicious; used if the "fix" is no better
    result = None
    for attempt in range(s.max_repair_attempts + 1):
        t = time.perf_counter()
        guard = check_sql(sql, r.dialect, tables, row_limit=s.query_row_limit)
        clock.lap("safety guard", t)
        error = ""
        if not guard.ok:
            error = f"Rejected by the safety checker: {guard.reason}"
            if guard.code == "personal_data":
                error += (" Do not return other personal details (names, contact data) instead. If the "
                          "question asks for personal details about individuals, set sql to null and say "
                          "in message that you can give counts or summaries instead.")
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
                result = None
            clock.lap("query", t)
            if not error:
                issue = suspicious(question, result["columns"], result["rows"], result["truncated"])
                if issue and kept is None and attempt < s.max_repair_attempts:
                    kept = (guard.sql, result)
                    out["attempts"].append({"sql": guard.sql, "stage": "suspicious", "error": issue})
                    error = (f"The query ran, but the result looks suspicious: {issue}. "
                             "If the result is actually correct, reply with exactly the same SQL.")
                else:
                    if issue and kept is not None:  # the "fix" is no better: keep the first answer
                        guard_sql, result = kept
                        out["sql"] = guard_sql
                    else:
                        out["sql"] = guard.sql
                    break
        if attempt == s.max_repair_attempts:
            if kept is not None:  # repairs after a suspicious result failed: the first result stands
                out["sql"], result = kept
                break
            out["sql"] = sql
            return done("failed", "I wrote a query for that but couldn't get it to run. "
                                  "You can see what I tried in the SQL tab.")
        # The writer may have used a real table that retrieval left out, guessing its columns
        # ("products.product_name"): show it that table's actual schema for the repair.
        wider = add_tables(r, tables_named_in(sql, r.source_id))
        if wider is not r:
            r = wider
            out["retrieval"] = asdict(r)
            error += " The schema above now also lists the tables you used; use their exact column names."
        t = time.perf_counter()
        try:
            fix = repair_sql(question, r.schema_text, r.dialect, sql, error, r.value_hits, hist, clarification)
        except llm.LLMError:
            clock.lap("sql repair", t)
            if kept is not None:
                out["sql"], result = kept
                break
            out["sql"] = sql
            return done("failed", "I couldn't reach the language model to fix the query. Please try again.")
        clock.lap("sql repair", t)
        if kept is not None and fix.sql and _same_sql(fix.sql, sql):
            out["sql"], result = kept  # the model confirms the suspicious-looking result is right
            out["attempts"][-1]["confirmed"] = True
            break
        if not fix.sql:
            if kept is not None:
                out["sql"], result = kept
                break
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
    elif out.get("other_sources"):
        answer = f"Using the {r.source_name} data: {answer}"
    return done("answered", answer)
