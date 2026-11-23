"""Re-profiling keeps earlier LLM descriptions; only undescribed tables cost calls."""

from app.profiler import describe
from app.profiler.models import ColumnProfile, SourceProfile, TableProfile


def table(name, desc="", col_desc=""):
    return TableProfile(name=name, description=desc,
                        columns=[ColumnProfile(name="id", type="INT", kind="id", description=col_desc)])


def profile(*tables, desc=""):
    return SourceProfile(source_id="s", source_name="Shop", engine="sqlite", dialect="sqlite",
                         description=desc, tables=list(tables), profiled_at="now", seconds=0.1)


def test_carry_over_keeps_old_descriptions():
    old = profile(table("orders", "one row per order", "order id"), table("gone", "x"), desc="A shop")
    new = profile(table("orders"), table("customers"))
    describe.carry_over_descriptions(old, new)
    assert new.description == "A shop"
    assert new.tables[0].description == "one row per order" and new.tables[0].columns[0].description == "order id"
    assert new.tables[1].description == ""


def test_only_missing_tables_are_described(monkeypatch):
    sent = []

    def fake_chat_json(msgs, **kw):
        sent.append(msgs[1]["content"])
        return {"tables": [{"name": "customers", "description": "one row per customer", "columns": {"id": "customer id"}}]}

    monkeypatch.setattr(describe, "chat_json", fake_chat_json)
    p = describe.describe_profile(profile(table("orders", "one row per order"), table("customers"), desc="A shop"))
    assert len(sent) == 1 and "TABLE customers" in sent[0] and "TABLE orders" not in sent[0]
    assert p.tables[1].description == "one row per customer" and p.described


def test_failed_batch_leaves_profile_undescribed(monkeypatch):
    monkeypatch.setattr(describe, "RETRY_WAITS", ())

    def boom(*a, **k):
        raise describe.LLMError("503")

    monkeypatch.setattr(describe, "chat_json", boom)
    p = describe.describe_profile(profile(table("orders", "kept"), table("customers")))
    assert not p.described and p.tables[0].description == "kept"
