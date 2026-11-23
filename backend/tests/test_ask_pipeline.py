"""Ask pipeline control flow with a fake LLM and database (no network, no quota)."""

from types import SimpleNamespace

import pytest

from app.nl2sql import pipeline
from app.nl2sql.answer import template_answer
from app.nl2sql.generator import Generation, _parse
from app.safety.executor import QueryError

RETRIEVAL = dict(source_id="src1", source_name="Shop", dialect="postgres", schema_text="TABLE orders ...",
                 value_hits=[], tables=[], source_scores=[], ambiguous_source=False, ms=1.0)
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
    monkeypatch.setattr(pipeline, "retrieve", lambda q, ids=None, **kw: SimpleNamespace(source_id=None))
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
    assert template_answer(["genre", "total_tracks"], [["Rock", 1297]], kpi) == "Rock. Total tracks: 1,297."
    bar = {"type": "bar", "x": "state", "y": ["orders"]}
    assert template_answer(["state", "orders"], [["SP", 41746], ["RJ", 12852]], bar).startswith("The top state is SP")
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
        return SimpleNamespace(**{**RETRIEVAL, "source_id": sid, "source_name": sid}, question=q)

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
