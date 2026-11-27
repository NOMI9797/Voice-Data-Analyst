"""Ask pipeline control flow with a fake LLM and database (no network, no quota)."""

from types import SimpleNamespace

import pytest

from app.nl2sql import pipeline
from app.nl2sql.answer import template_answer
from app.nl2sql.generator import Generation, _parse
from app.safety.executor import QueryError

RETRIEVAL = dict(source_id="src1", source_name="Shop", dialect="postgres", schema_text="TABLE orders ...",
                 value_hits=[], tables=[], source_scores=[], ambiguous_source=False, ms=1.0, choices=[])
TABLES = {"orders": ["order_id", "status", "purchased_at"], "customers": ["customer_id", "email", "state"]}


@pytest.fixture
def env(monkeypatch):
    """Wires the pipeline to fakes. Returns a dict the test fills with scripted behaviour."""
    st = {"gen": [], "repairs": [], "db": [], "ran": [], "repair_errors": []}
    monkeypatch.setattr(pipeline.llm, "available", lambda: True)

    def fake_retrieve(q, ids=None, **kw):
        st["retrieve_kw"] = kw
        return SimpleNamespace(**RETRIEVAL, question=q)

    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "widen", lambda r, extra: r)
    monkeypatch.setattr(pipeline, "tables_named_in", lambda sql, sid: [])
    monkeypatch.setattr(pipeline, "add_tables", lambda r, names: r)
    monkeypatch.setattr(pipeline, "switched_off_fit", lambda q: None)
    monkeypatch.setattr(pipeline, "asdict", lambda r: {"source_id": r.source_id})
    monkeypatch.setattr(pipeline, "allowed_tables", lambda sid: TABLES)

    def gen(*a, **k):
        return st["gen"].pop(0)

    def repair(q, schema, dialect, bad, error, *a, **k):
        st["repair_errors"].append(error)
        return st["repairs"].pop(0)

    def run(sid, sql):
        st["ran"].append(sql)
        out = st["db"].pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    monkeypatch.setattr(pipeline, "repair_sql", repair)
    monkeypatch.setattr(pipeline, "run_query", run)
    monkeypatch.setattr(pipeline, "spoken_answer", lambda *a, **k: ("There are 3 orders.", True))
    return st


def ok_result(columns=("orders",), rows=((3,),)):
    return {"columns": list(columns), "rows": [list(r) for r in rows], "row_count": len(rows),
            "truncated": False, "ms": 1.0}


def test_happy_path(env):
    env["gen"].append(Generation(sql="select count(*) as orders from orders"))
    env["db"].append(ok_result())
    out = pipeline.ask("how many orders")
    assert out["status"] == "answered" and out["answer"] == "There are 3 orders."
    assert out["result"]["chart"]["type"] == "kpi"
    assert "LIMIT" in out["sql"].upper()  # guard added a limit
    assert {"retrieval", "sql generation", "safety guard", "query", "answer", "total"} <= set(out["timings"])


def test_screen_refuses_before_llm(env):
    out = pipeline.ask("delete all customers")
    assert out["status"] == "refused" and out["sql"] is None and not env["ran"]


def test_database_error_is_repaired(env):
    env["gen"].append(Generation(sql="select count(*) as n from orders where statuz = 'x'"))
    env["db"] += [QueryError('column "statuz" does not exist'), ok_result()]
    env["repairs"].append(Generation(sql="select count(*) as n from orders where status = 'x'"))
    out = pipeline.ask("how many x orders")
    assert out["status"] == "answered"
    assert "statuz" in env["repair_errors"][0] and len(out["attempts"]) == 1
    assert "status = 'x'" in out["sql"]


def test_unknown_table_is_repaired(env):
    env["gen"].append(Generation(sql="select count(*) from order_list"))
    env["repairs"].append(Generation(sql="select count(*) as n from orders"))
    env["db"].append(ok_result())
    out = pipeline.ask("how many orders")
    assert out["status"] == "answered" and out["attempts"][0]["stage"] == "guard"


def test_write_from_llm_is_never_repaired(env):
    env["gen"].append(Generation(sql="update orders set status = 'done'"))
    out = pipeline.ask("mark the orders as finished please")
    assert out["status"] == "refused" and not env["repair_errors"] and not env["ran"]


def test_gives_up_after_max_attempts(env, monkeypatch):
    monkeypatch.setattr(pipeline.get_settings(), "max_repair_attempts", 2)
    env["gen"].append(Generation(sql="select nope from orders"))
    env["repairs"] += [Generation(sql="select nope2 from orders"), Generation(sql="select nope3 from orders")]
    env["db"] += [QueryError("bad column")] * 3
    out = pipeline.ask("something hard")
    assert out["status"] == "failed" and len(out["attempts"]) == 3 and len(env["ran"]) == 3


def test_llm_says_cannot_answer(env):
    env["gen"].append(Generation(sql=None, message="There is no weather data in this database."))
    out = pipeline.ask("will it rain tomorrow")
    assert out["status"] == "cannot_answer" and "weather" in out["answer"]


def test_llm_down_gives_spoken_failure(env, monkeypatch):
    def boom(*a, **k):
        raise pipeline.llm.LLMError("all models unavailable")
    monkeypatch.setattr(pipeline, "generate_sql", boom)
    out = pipeline.ask("how many orders")
    assert out["status"] == "failed" and "try again" in out["answer"]


def test_no_active_source(env, monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve", lambda q, ids=None, **kw: SimpleNamespace(source_id=None, choices=[]))
    assert pipeline.ask("how many orders")["status"] == "no_source"


@pytest.mark.parametrize("raw,sql", [
    ({"sql": "```sql\nselect 1\n```"}, "select 1"),
    ({"sql": "  "}, None),
    ({"sql": None, "message": "no"}, None),
    ({}, None),
])
def test_parse_generation(raw, sql):
    assert _parse(raw).sql == sql


def test_template_answers():
    kpi = {"type": "kpi", "x": "genre", "y": ["total_tracks"]}
    assert template_answer(["genre", "total_tracks"], [["Rock", 1297]], kpi) == "Rock, with 1,297 total tracks."
    bar = {"type": "bar", "x": "state", "y": ["orders"], "column_types": {"state": "category"}}
    assert template_answer(["state", "orders"], [["SP", 41746], ["RJ", 12852]], bar).startswith("SP is highest")
    assert "nothing matched" in template_answer(["a"], [], {"type": "empty"})


def test_follow_up_passes_previous_turn_to_retrieval(env):
    from app.nl2sql.generator import Turn
    env["gen"].append(Generation(sql="select count(*) as orders from orders"))
    env["db"].append(ok_result())
    hist = [Turn("orders per state", "select ...", "src0"), Turn("orders per month", "select ...", "src1")]
    pipeline.ask("and in 2018?", hist)
    assert env["retrieve_kw"] == {"prefer_source": "src1", "context": "orders per month"}


def test_follow_up_switches_source_when_sticky_source_cannot_answer(env, monkeypatch):
    from app.nl2sql.generator import Turn
    calls = []

    def fake_retrieve(q, ids=None, **kw):
        calls.append(kw)
        sid = "src1" if kw.get("prefer_source") else "src2"
        scores = [{"source_id": sid, "term_coverage": {"review": 1.0}}]
        return SimpleNamespace(**{**RETRIEVAL, "source_id": sid, "source_name": sid, "source_scores": scores}, question=q)

    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    env["gen"] += [Generation(sql=None, message="no reviews here"),
                   Generation(sql="select count(*) as orders from orders")]
    env["db"].append(ok_result())
    out = pipeline.ask("and their review scores?", [Turn("orders per category", "select ...", "src1")])
    assert out["status"] == "answered" and out["source_id"] == "src2" and out["switched_source"]
    assert out["answer"].startswith("That's not in the earlier data, so I looked in src2.")
    assert calls[1] == {}  # second retrieval without stickiness or context


def test_no_switch_when_best_source_is_the_same(env):
    from app.nl2sql.generator import Turn
    env["gen"].append(Generation(sql=None, message="no weather data"))
    out = pipeline.ask("will it rain?", [Turn("orders per month", "select ...", "src1")])
    assert out["status"] == "cannot_answer" and env["gen"] == []


OPTIONS = [{"source_id": "olist", "name": "Olist e-commerce"}, {"source_id": "chinook", "name": "Chinook music store"}]


def test_close_call_answers_from_best_match_and_offers_others(env, monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve", lambda q, ids=None, **kw: SimpleNamespace(
        **{**RETRIEVAL, "source_id": "olist", "source_name": "Olist e-commerce", "choices": OPTIONS}, question=q))
    env["gen"].append(Generation(sql="select count(*) as customers from customers"))
    env["db"].append(ok_result(("customers",), ((96096,),)))
    out = pipeline.ask("how many customers are there")
    assert out["status"] == "answered" and out["source_id"] == "olist"
    assert out["other_sources"] == [{"source_id": "chinook", "name": "Chinook music store"}]
    assert out["answer"].startswith("Using the Olist e-commerce data: ")


def test_reply_to_clarification_answers_original_question(env, monkeypatch):
    seen = {}

    def fake_retrieve(q, ids=None, **kw):
        seen["q"], seen["ids"] = q, ids
        return SimpleNamespace(**{**RETRIEVAL, "source_id": ids[0]}, question=q)

    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    env["gen"].append(Generation(sql="select count(*) as customers from customers"))
    env["db"].append(ok_result())
    out = pipeline.ask("the music store", pending={"question": "how many customers are there", "options": OPTIONS})
    assert out["status"] == "answered" and out["chosen_source"] == "chinook"
    assert seen == {"q": "how many customers are there", "ids": ["chinook"]}
    assert out["question"] == "how many customers are there"


def test_unrelated_reply_is_a_new_question(env):
    env["gen"].append(Generation(sql="select count(*) as orders from orders"))
    env["db"].append(ok_result())
    out = pipeline.ask("how many orders were late", pending={"question": "how many customers", "options": OPTIONS})
    assert out["status"] == "answered" and "chosen_source" not in out and out["question"] == "how many orders were late"


def test_speak_false_skips_answer_llm(env, monkeypatch):
    def no_llm(*a, **k):
        raise AssertionError("spoken_answer must not be called")
    monkeypatch.setattr(pipeline, "spoken_answer", no_llm)
    env["gen"].append(Generation(sql="select count(*) as orders from orders"))
    env["db"].append(ok_result())
    out = pipeline.ask("how many orders", speak=False)
    assert out["status"] == "answered" and out["answer_by_llm"] is False and "3" in out["answer"]


VAGUE = Generation(sql=None, clarify_question="Top customers by total spending or by number of orders?",
                   clarify_options=["by total spending", "by number of orders"])


def test_vague_question_asks_instead_of_guessing(env):
    env["gen"].append(VAGUE)
    out = pipeline.ask("show me the top customers")
    spoken = "Top customers by total spending or by number of orders: by total spending, or by number of orders?"
    assert out["status"] == "clarify" and out["answer"] == spoken
    assert out["clarify"] == {"kind": "intent", "asked": spoken,
                              "options": VAGUE.clarify_options, "source_id": "src1"}
    assert not env["ran"]


def test_answer_to_clarification_reaches_the_sql_writer(env, monkeypatch):
    seen = {}

    def gen(question, *a, **k):
        seen["question"], seen["clarification"] = question, a[-1] if a else k.get("clarification")
        return Generation(sql="select 1 as customers")

    monkeypatch.setattr(pipeline, "generate_sql", gen)
    env["db"].append(ok_result(("customers",), ((1,),)))
    pending = {"kind": "intent", "question": "show me the top customers", "asked": VAGUE.clarify_question,
               "options": VAGUE.clarify_options, "source_id": "src1"}
    out = pipeline.ask("by spending", pending=pending)
    assert out["status"] == "answered"
    assert seen == {"question": "show me the top customers",
                    "clarification": {"asked": VAGUE.clarify_question, "answer": "by total spending"}}
    assert out["question"] == "show me the top customers (by total spending)"


def test_never_asks_twice(env):
    env["gen"].append(VAGUE)  # the model asks again even though the user already answered
    pending = {"kind": "intent", "question": "show me the top customers", "asked": VAGUE.clarify_question,
               "options": VAGUE.clarify_options}
    out = pipeline.ask("by spending", pending=pending)
    assert out["status"] == "cannot_answer"


def test_new_question_while_clarification_pending(env):
    env["gen"].append(Generation(sql="select count(*) as orders from orders"))
    env["db"].append(ok_result())
    pending = {"kind": "intent", "question": "show me the top customers", "asked": "?", "options": ["a", "b"]}
    out = pipeline.ask("how many orders were placed in 2018", pending=pending)
    assert out["status"] == "answered" and "clarification" not in out


def test_spoken_intent_reads_options():
    assert pipeline._spoken_intent("How would you like to measure top customers?",
                                   ["By total spending (revenue)", "By number of orders"]) == \
        "How would you like to measure top customers: by total spending, or by number of orders?"
    assert pipeline._spoken_intent("Which one?", ["Olist"]) == "Which one?"


def empty_result():
    return {"columns": ["orders"], "rows": [], "row_count": 0, "truncated": False, "ms": 1.0}


def test_empty_result_gets_one_repair(env):
    env["gen"].append(Generation(sql="select count(*) as orders from orders where status = 'Delivered'"))
    env["db"] += [empty_result(), ok_result()]
    env["repairs"].append(Generation(sql="select count(*) as orders from orders where status = 'delivered'"))
    out = pipeline.ask("how many delivered orders")
    assert out["status"] == "answered" and "'delivered'" in out["sql"]
    assert out["attempts"][0]["stage"] == "suspicious" and "no rows" in env["repair_errors"][0]
    assert "same SQL" in env["repair_errors"][0]


def test_model_confirms_empty_result(env):
    sql = "select count(*) as orders from orders where status = 'lost'"
    env["gen"].append(Generation(sql=sql))
    env["db"].append(empty_result())
    env["repairs"].append(Generation(sql=sql))  # same SQL back: the empty answer is right
    out = pipeline.ask("how many lost orders")
    assert out["status"] == "answered" and out["result"]["row_count"] == 0
    assert out["attempts"][0]["confirmed"] and len(env["ran"]) == 1


def test_fix_that_is_no_better_keeps_first_result(env):
    env["gen"].append(Generation(sql="select count(*) as orders from orders where status = 'x'"))
    env["db"] += [empty_result(), empty_result()]
    env["repairs"].append(Generation(sql="select count(*) as orders from orders where status = 'y'"))
    out = pipeline.ask("how many x orders")
    assert out["status"] == "answered" and "'x'" in out["sql"] and len(env["ran"]) == 2


def test_failed_repair_after_suspicious_keeps_first_result(env):
    env["gen"].append(Generation(sql="select count(*) as orders from orders where status = 'x'"))
    env["db"] += [empty_result(), QueryError("syntax error"), QueryError("syntax error"), QueryError("syntax error")]
    env["repairs"] += [Generation(sql="select bad1"), Generation(sql="select bad2"), Generation(sql="select bad3")]
    out = pipeline.ask("how many x orders")
    assert out["status"] == "answered" and "'x'" in out["sql"]



def test_repair_gets_schema_of_tables_the_sql_used(env, monkeypatch):
    added = {}

    def fake_add(r, names):
        added["names"] = names
        return SimpleNamespace(**{**vars(r), "schema_text": "TABLE orders ...\nTABLE Products ProductName"})

    monkeypatch.setattr(pipeline, "tables_named_in", lambda sql, sid: ["Products"])
    monkeypatch.setattr(pipeline, "add_tables", fake_add)
    monkeypatch.setattr(pipeline, "asdict", lambda r: {"source_id": r.source_id})
    seen = {}

    def repair(q, schema, dialect, bad, error, *a, **k):
        seen["schema"], seen["error"] = schema, error
        return Generation(sql="select ProductName as product from Products")

    monkeypatch.setattr(pipeline, "repair_sql", repair)
    monkeypatch.setattr(pipeline, "allowed_tables", lambda sid: {**TABLES, "Products": ["ProductID", "ProductName"]})
    env["gen"].append(Generation(sql="select p.product_name from products p"))
    env["db"] += [QueryError("no such column: p.product_name"), ok_result(("product",), (("Chai",),))]
    out = pipeline.ask("which products sell best")
    assert out["status"] == "answered" and added["names"] == ["Products"]
    assert "ProductName" in seen["schema"] and "exact column names" in seen["error"]



def test_pure_follow_up_does_not_jump_to_another_source(env):
    from app.nl2sql.generator import Turn
    env["gen"].append(Generation(sql=None, message="Tracks have no dates, so they can't be broken down by month."))
    out = pipeline.ask("now break it down by month", [Turn("tracks per genre", "select ...", "src1")])
    assert out["status"] == "cannot_answer" and "dates" in out["answer"] and env["gen"] == []


def test_switched_off_source_is_not_used(env, monkeypatch):
    monkeypatch.setattr(pipeline, "retrieve", lambda q, ids=None, **kw: SimpleNamespace(source_id=None, choices=[]))
    monkeypatch.setattr(pipeline, "get_registry", lambda: SimpleNamespace(
        list=lambda: [SimpleNamespace(id="olist", name="Olist e-commerce", active=False)]))
    out = pipeline.ask("how many customers are there", source_ids=["olist"])
    assert out["status"] == "no_source"
    assert out["answer"] == "The Olist e-commerce data is switched off. Turn it on under Your data to ask it."



def test_question_for_switched_off_source_says_so(env, monkeypatch):
    def fake_retrieve(q, ids=None, **kw):
        return SimpleNamespace(**{**RETRIEVAL, "source_scores": [{"source_id": "src1", "term_coverage": {"seller": 0.6}}]},
                               question=q)
    monkeypatch.setattr(pipeline, "retrieve", fake_retrieve)
    monkeypatch.setattr(pipeline, "switched_off_fit",
                        lambda q: {"source_id": "olist", "name": "Olist e-commerce", "words": ["seller"]})
    env["gen"].append(Generation(sql=None, message="There are no sellers in this data."))
    out = pipeline.ask("how many sellers are in SP")
    assert out["status"] == "cannot_answer" and "Olist e-commerce data, which is switched off" in out["answer"]
    assert env["gen"] == [] and not env["ran"]
