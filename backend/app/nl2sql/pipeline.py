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
from app.nl2sql import smalltalk
from app.nl2sql.sanity import suspicious
from app.nl2sql.answer import spoken_answer, template_answer
from app.nl2sql.generator import Generation, Turn, generate_sql, repair_sql
from app.profiler.retriever import add_tables, retrieve, switched_off_fit, tables_named_in, widen
from app.profiler.routing import ALL, OFF_TOPIC_SCORE, resolve_choice, resolve_intent_reply
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


# The real tables and columns of a source (what the guard allows).
def allowed_tables(source_id: str) -> dict[str, list[str]] | None:
    prof = get_store().get(source_id)
    if prof is None:
        return None
    return {t.name: [c.name for c in t.columns] for t in prof.tables}


# Measures how long each step takes.
class _Clock:
    def __init__(self):
        self.timings: dict[str, float] = {}
        self._t0 = time.perf_counter()

    # Adds the time a step took.
    def lap(self, name: str, since: float) -> None:
        self.timings[name] = round(self.timings.get(name, 0) + (time.perf_counter() - since) * 1000, 1)

    # Total time so far.
    def total(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000, 1)


# True when two SQL queries are the same (ignoring spaces).
def _same_sql(a: str, b: str) -> bool:
    # whitespace only: case matters inside quoted values ('Delivered' vs 'delivered')
    norm = lambda q: re.sub(r"\s+", " ", q.strip().rstrip(";"))  # noqa: E731
    return norm(a) == norm(b)


# The spoken 'which database do you mean?' question.
def _spoken_choices(names: list[str]) -> str:
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + " or " + names[-1]
    tail = ", or all of them?" if len(names) > 1 else "?"
    return f"That could be about the {listed} data. Which one do you mean{tail}"


# Finds the databases the user named in the question ('on Olist', 'Olist and Chinook').
def _named_sources(question: str) -> list[str]:
    """'... on Olist', 'in the Chinook data': the user named databases. Returns their ids
    (a source counts when its distinctive first name word appears, not common words like 'test')."""
    from wordfreq import zipf_frequency

    words = set(re.findall(r"[a-z0-9]+", question.lower()))
    hits = []
    for s in get_registry().list():
        name = re.findall(r"[a-z0-9]+", s.name.lower())
        if name and len(name[0]) >= 4 and zipf_frequency(name[0], "en") < 3.0 and name[0] in words:
            hits.append(s.id)
    return hits


# The spoken clarifying question with its options said out loud.
def _spoken_intent(question: str, options: list[str]) -> str:
    """'How should I rank them?' + options -> 'How should I rank them: by total spending, or by
    number of orders?' A listener can't see the buttons, so the options are said out loud."""
    opts = [re.sub(r"\s*\(.*?\)", "", o).strip().rstrip(".?") for o in options if o.strip()]
    opts = [o[0].lower() + o[1:] if o[:1].isupper() and not o[:2].isupper() else o for o in opts]
    q = question.strip().rstrip("?").rstrip()
    if len(opts) < 2:
        return q + "?"
    return f"{q}: {', '.join(opts[:-1])}, or {opts[-1]}?"


_COMPARE = re.compile(r"\b(compare|comparison|versus|vs|more|fewer|less|bigger|biggest|larger|largest|"
                      r"smaller|higher|highest|lower|which (one )?has|who has)\b", re.I)
_ADDITIVE = re.compile(r"^\s*select\s+(count|sum)\s*\(", re.I)


# "All of them": asks the question on every chosen source and combines the answers.
def _ask_all(question: str, ids: list[str], out: dict, clock, done) -> dict:
    """Counts and sums are added up ("59 + 93 + 96,096"); other single values (averages)
    are listed side by side, never added; longer results are stacked with a source column."""
    from concurrent.futures import ThreadPoolExecutor

    reg = {s.id: s for s in get_registry().list()}
    off = [reg[i].name for i in ids if i in reg and not reg[i].active]
    ids = [i for i in ids if i in reg and reg[i].active]
    if not ids:
        return done("no_source", f"The {_join(off)} data {'is' if len(off) == 1 else 'are'} switched off. "
                                 "Turn it on under Your data to ask it." if off else "No data source is switched on.")
    if off:
        out["switched_off_names"] = off
    t = time.perf_counter()
    with ThreadPoolExecutor(max_workers=len(ids) or 1) as pool:
        # each one is told which database it answers for: "compare Olist and Chinook" must not make
        # the Chinook writer reply "Olist isn't here"
        parts = list(pool.map(lambda sid: ask(f"{question} (answer this for the {reg[sid].name} data only)",
                                              None, [sid], None, speak=False), ids))
    clock.lap("all sources", t)
    out["parts"] = [{"source_id": sid, "source_name": reg[sid].name, "status": p["status"], "answer": p["answer"],
                     "sql": p.get("sql")} for sid, p in zip(ids, parts)]
    ok = [(reg[sid].name, p) for sid, p in zip(ids, parts) if p["status"] == "answered" and p.get("result")]
    missing = [reg[sid].name for sid, p in zip(ids, parts) if not (p["status"] == "answered" and p.get("result"))]
    if not ok:
        refused = next((p for p in parts if p["status"] == "refused"), None)
        if refused:
            return done("refused", refused["answer"])
        return done("cannot_answer", "None of the databases could answer that.")
    out["source_name"] = f"All {len(ids)} databases" if len(ids) > 2 else "Both databases"
    out["sql"] = "\n\n".join(f"-- {name}\n{p['sql']}" for name, p in ok)
    note = f" {_join(missing)} couldn't answer it." if missing else ""
    if off:
        note += f" {_join(off)} {'is' if len(off) == 1 else 'are'} switched off, so not included."

    def single(p):
        res = p["result"]
        nums = [i for i, v in enumerate(res["rows"][0]) if isinstance(v, (int, float)) and not isinstance(v, bool)] \
            if res["row_count"] == 1 else []
        return res["rows"][0][nums[0]] if len(nums) == 1 and len(res["columns"]) == 1 else None

    values = [single(p) for _, p in ok]
    if all(v is not None for v in values):
        measure = ok[0][1]["result"]["columns"][0]
        rows = [[name, v] for (name, _), v in zip(ok, values)]
        each = ", ".join(f"{_fmt(v)} in {name}" for name, v in rows)
        if _COMPARE.search(question) and len(rows) > 1:
            ranked = sorted(rows, key=lambda x: x[1], reverse=True)
            if ranked[0][1] == ranked[1][1]:
                answer = f"They are level: {each}.{note}"
            else:
                rest = ", ".join(f"{_fmt(v)} in {n}" for n, v in ranked[1:])
                answer = f"{ranked[0][0]} has the most, with {_fmt(ranked[0][1])}, against {rest}.{note}"
        elif all(_ADDITIVE.match(p["sql"] or "") for _, p in ok):
            total = sum(values)
            out["total"] = total
            answer = f"{_fmt(total)} in total: {each}.{note}"
        else:
            answer = f"Each database separately: {each}. These can't simply be added together.{note}"
        result = {"columns": ["database", measure], "rows": rows, "row_count": len(rows), "truncated": False, "ms": 0.0}
    else:
        cols = ok[0][1]["result"]["columns"]
        same = all(len(p["result"]["columns"]) == len(cols) for _, p in ok)
        if not same:
            return done("cannot_answer", "The databases answered that in different shapes, so I can't combine them. "
                                         "Ask each one separately.")
        rows = [[name, *r] for name, p in ok for r in p["result"]["rows"]]
        result = {"columns": ["database", *cols], "rows": rows, "row_count": len(rows),
                  "truncated": any(p["result"]["truncated"] for _, p in ok), "ms": 0.0}
        answer = " ".join(f"{name}: {p['answer']}" for name, p in ok) + note
    chart = choose_chart(result["columns"], result["rows"])
    out["result"] = {"ok": True, "sql": out["sql"], **result, "chart": chart}
    out["answer_by_llm"] = False
    return done("answered", answer)


# Years the question asks about that a repaired query no longer filters on.
def _dropped_years(question: str, old_sql: str, new_sql: str) -> list[str]:
    years = dict.fromkeys(re.findall(r"\b((?:19|20)\d{2})\b", question))
    return [y for y in years if y in old_sql and y not in new_sql]


# "What tables do you have?": the real tables of each database (or of the one named).
def _catalog(question: str, out: dict, done) -> dict:
    reg = get_registry()
    named = set(_named_sources(question))
    srcs = [s for s in reg.list() if (s.id in named if named else s.active)]
    rows, said = [], []
    for s in srcs:
        try:
            tables = reg.tables(s.id)
        except Exception:  # an unreachable source is simply left out
            continue
        rows += [[s.name, t] for t in tables]
        examples = ", ".join(tables[:3])
        said.append(f"{s.name} has {len(tables)} tables, such as {examples}" if tables else f"{s.name} has no tables")
    if not srcs:
        return done("no_source", "No data source is switched on. Turn one on under Your data first.")
    result = {"columns": ["database", "table"], "rows": rows, "row_count": len(rows), "truncated": False, "ms": 0.0}
    out["result"] = {"ok": True, "sql": "", **result, "chart": choose_chart(result["columns"], rows)}
    out["source_name"] = srcs[0].name if len(srcs) == 1 else f"All {len(srcs)} databases"
    return done("answered", ". ".join(said) + ". The full list is in the table.")


# "a", "a and b", "a, b and c"
def _join(names: list[str]) -> str:
    return names[0] if len(names) == 1 else ", ".join(names[:-1]) + " and " + names[-1]


# 96096 -> "96,096"; 4.0864 -> "4.09"
def _fmt(v) -> str:
    return f"{v:,}" if isinstance(v, int) else f"{v:,.2f}"


# MAIN CONTROLLER: screen -> find tables/source -> write SQL -> check -> run -> repair -> chart -> facts -> spoken answer.
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

    # 0. not a data question: greetings, thanks, "never mind", "help", "what tables do you have"
    talk = smalltalk.classify(question)
    if talk == "catalog":
        return _catalog(question, out, done)
    if talk:
        names = [s.name for s in get_registry().active()]
        return done("cancelled" if talk == "cancel" else "chat", smalltalk.reply(talk, names))

    clarification = None
    if pending and pending.get("kind", "source") == "source" and pending.get("options"):
        chosen = resolve_choice(question, pending["options"])
        if chosen:
            out["chosen_source"] = chosen
            question = out["question"] = pending["question"]
            source_ids = [ALL] + [o["source_id"] for o in pending["options"]] if chosen == ALL else [chosen]
    elif pending and pending.get("kind") == "intent":
        answer = resolve_intent_reply(question, pending.get("options") or [])
        if answer:
            clarification = {"asked": pending.get("asked", ""), "answer": answer}
            out["clarification"] = clarification
            question = pending["question"]
            out["question"] = f"{question} ({answer})"  # what history and the UI show
            if pending.get("source_id"):
                source_ids = [pending["source_id"]]

    if source_ids and source_ids[0] == ALL:
        return _ask_all(question, source_ids[1:], out, clock, done)

    # 1. screen
    t = time.perf_counter()
    screen = screen_question(question)
    clock.lap("screen", t)
    if not screen.allowed:
        out["refusal"] = {"category": screen.category, "matched": screen.matched}
        return done("refused", screen.message)
    if not llm.available():
        return done("failed", "The language model key is not set, so I can't answer questions yet.")

    # 2. retrieve (a database the user named, "... on Olist", is used directly)
    if source_ids is None and (named := _named_sources(question)):
        # one name: use that database; two or more ("compare Olist and Chinook"): ask each, combined
        source_ids = named if len(named) == 1 else [ALL, *named]
        out["named_source"] = named
        if source_ids[0] == ALL:
            return _ask_all(question, source_ids[1:], out, clock, done)
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
        busy = [s.name for s in get_registry().active() if not source_ids or s.id in source_ids]
        if busy:  # switched on, but its analysis (run once after connecting) hasn't finished yet
            return done("not_ready", f"The {busy[0]} data is still being analysed. Try again in a moment.")
        return done("no_source", "No data source is switched on. Turn one on under Your data first.")
    if r.choices and source_ids is None:
        # a tie among the active sources, but the words fit a switched-off one: say so
        off = switched_off_fit(question)
        if off:
            out["switched_off_source"] = off
            return done("cannot_answer", f"That isn't in the data that's switched on. It looks like it's in the "
                                         f"{off['name']} data, which is switched off. Turn it on under Your data to ask it.")
    if r.choices and max((sc["score"] for sc in r.source_scores), default=1.0) < OFF_TOPIC_SCORE:
        # nothing matches well anywhere: not a question about the data ("tell me a joke")
        return done("cannot_answer", "I can only answer questions about the data that's connected, "
                                     "such as orders, customers, products or sales.")
    if r.choices:
        # Several sources fit about equally well: ask by voice rather than guess (the brief:
        # "ask for clarification instead of guessing"). Only real ties get here, and the
        # choice is then remembered through the conversation history.
        out["choices"] = r.choices
        spoken = _spoken_choices([c["name"] for c in r.choices])
        out["clarify"] = {"kind": "source", "asked": spoken, "options": r.choices}
        return done("clarify", spoken)
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
        # First: the same source, searched for this question alone. The previous question's
        # words can keep the right tables out ("how many customers" after "tracks per genre").
        t = time.perf_counter()
        fresh = retrieve(question, [r.source_id])
        clock.lap("retrieval", t)
        if fresh.source_id == r.source_id and fresh.schema_text != r.schema_text:
            t = time.perf_counter()
            try:
                fresh_gen = generate_sql(question, fresh.schema_text, fresh.dialect, fresh.value_hits, [])
            except llm.LLMError:
                fresh_gen = None
            clock.lap("sql generation", t)
            if fresh_gen and fresh_gen.sql:
                r, gen, hist = fresh, fresh_gen, []
                out["retrieval"] = asdict(r)
                out["assumption"] = gen.assumption
    if not gen.sql and not gen.clarify_question and prev and r.source_id == prev.source_id and has_subject:
        # Still nothing: maybe the user changed topic. Retry once on the source that fits
        # the question by itself.
        t = time.perf_counter()
        alt = retrieve(question, source_ids)
        clock.lap("retrieval", t)
        if alt.choices:
            # the new topic fits several sources equally: ask, as for any other tie
            # (minus the previous source, which we already know can't answer it)
            opts = [c for c in alt.choices if c["source_id"] != r.source_id]
            if len(opts) > 1:
                spoken = f"That isn't in the {r.source_name} data. " + _spoken_choices([c["name"] for c in opts])
                out["choices"] = opts
                out["clarify"] = {"kind": "source", "asked": spoken, "options": opts}
                return done("clarify", spoken)
            if opts:
                t = time.perf_counter()
                alt = retrieve(question, [opts[0]["source_id"]])
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
        if kept is not None and (lost := _dropped_years(question, sql, fix.sql)):
            # "revenue for 2017" came back empty and the "fix" swapped in another year: that
            # answers a different question. The empty result is the true answer.
            out["sql"], result = kept
            out["no_data_for"] = lost
            out["attempts"][-1]["rejected_fix"] = fix.sql
            break
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
    if out.get("no_data_for") and not result["rows"]:
        answer = f"There's no data for {_join(out['no_data_for'])} in the {r.source_name} data."
    if out.get("switched_source"):
        answer = f"That's not in the earlier data, so I looked in {r.source_name}. {answer}"
    return done("answered", answer)
