"""Profiler + retriever tests on a small throw-away SQLite database
with NO declared foreign keys (like many real databases)."""

import sqlite3

import pytest

from app.profiler.profile import profile_source
from app.profiler.retriever import render_schema
from app.sources.registry import SourceRegistry


@pytest.fixture()
def shop(tmp_path):
    db = tmp_path / "shop.db"
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE customers (id INTEGER PRIMARY KEY, full_name TEXT, city TEXT);
        CREATE TABLE sales (sale_id INTEGER PRIMARY KEY, customer_id INTEGER, amount REAL, sold_on TEXT);
        CREATE TABLE "Product Lines" (code TEXT PRIMARY KEY, label TEXT);
    """)
    cities = ["Sao Paulo", "Rio de Janeiro", "Curitiba"]
    con.executemany("INSERT INTO customers VALUES (?, ?, ?)",
                    [(i, f"Customer {i}", cities[i % 3]) for i in range(1, 51)])
    con.executemany("INSERT INTO sales VALUES (?, ?, ?, ?)",
                    [(i, (i % 50) + 1, i * 1.5, f"2024-0{(i % 9) + 1}-15") for i in range(1, 201)])
    con.execute("""INSERT INTO "Product Lines" VALUES ('A', 'Alpha'), ('B', 'Beta')""")
    con.commit()
    con.close()
    reg = SourceRegistry(str(tmp_path / "data"))
    s = reg.add_sqlite_upload("shop", "shop.db", db)
    return profile_source(s, reg.engine(s.id))


def test_tables_and_counts(shop):
    assert {t.name for t in shop.tables} == {"customers", "sales", "Product Lines"}
    assert shop.table("sales").row_count == 200
    assert shop.table("Product Lines").row_count == 2  # name with a space is quoted correctly


def test_column_kinds(shop):
    sales = shop.table("sales")
    assert sales.column("sale_id").kind == "id"
    assert sales.column("customer_id").kind == "id"
    assert sales.column("amount").kind == "number"
    assert sales.column("sold_on").kind == "date"  # date stored as TEXT is detected
    assert sales.column("amount").min == "1.5" and float(sales.column("amount").max) == 300


def test_categorical_values(shop):
    city = shop.table("customers").column("city")
    assert city.categorical
    assert set(city.values) == {"Sao Paulo", "Rio de Janeiro", "Curitiba"}


def test_inferred_relationship(shop):
    rels = [(r.from_table, r.from_column, r.to_table, r.to_column, r.source) for r in shop.relationships]
    assert ("sales", "customer_id", "customers", "id", "inferred") in rels
    # two tables keyed by "id"-like PKs must not be joined to each other
    assert not any(r[1] == "sale_id" for r in rels)


def test_render_schema_mentions_join_and_values(shop):
    text = render_schema(shop, ["sales", "customers"])
    assert "sales.customer_id = customers.id" in text
    assert "Sao Paulo" in text
    assert "SQL dialect: sqlite" in text
