"""Tests for the data source registry (no external DB needed)."""

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import text

from app.sources.registry import SourceError, SourceRegistry, normalize_url, safe_identifier


def test_normalize_postgres_and_mysql_drivers():
    url, eng = normalize_url("postgres://u:p@db.example.com:5432/shop")
    assert eng == "postgresql" and url.startswith("postgresql+psycopg://")
    url, eng = normalize_url("mysql://u:p@db.example.com/shop")
    assert eng == "mysql" and url.startswith("mysql+pymysql://")


def test_normalize_rejects_unknown_and_missing_files():
    with pytest.raises(SourceError):
        normalize_url("oracle://u:p@host/db")
    with pytest.raises(SourceError):
        normalize_url("sqlite:////nope/missing.db")
    with pytest.raises(SourceError):
        normalize_url("not a url at all")


def test_safe_identifier():
    assert safe_identifier("Sales Report 2024.csv") == "sales_report_2024"
    assert safe_identifier("2024-orders.csv") == "t_2024_orders"


def _make_sqlite(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE people (id INTEGER PRIMARY KEY, name TEXT)")
    con.execute("INSERT INTO people (name) VALUES ('Ana'), ('Bruno')")
    con.commit()
    con.close()


def test_sqlite_upload_is_read_only(tmp_path):
    reg = SourceRegistry(str(tmp_path / "data"))
    db = tmp_path / "upload.db"
    _make_sqlite(db)
    s = reg.add_sqlite_upload("", "My People.db", db)
    assert s.engine == "sqlite" and s.origin == "upload"
    assert reg.tables(s.id) == ["people"]
    with reg.engine(s.id).connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM people")).scalar() == 2
        with pytest.raises(Exception):
            conn.execute(text("DELETE FROM people"))


def test_sqlite_upload_rejects_non_sqlite(tmp_path):
    reg = SourceRegistry(str(tmp_path / "data"))
    fake = tmp_path / "fake.db"
    fake.write_text("hello")
    with pytest.raises(SourceError):
        reg.add_sqlite_upload("", "fake.db", fake)


def test_csv_upload_becomes_read_only_duckdb(tmp_path):
    reg = SourceRegistry(str(tmp_path / "data"))
    a = tmp_path / "Monthly Sales.csv"
    a.write_text("month,revenue\n2024-01,100\n2024-02,150\n")
    b = tmp_path / "regions.csv"
    b.write_text("code,name\nN,North\nS,South\n")
    s = reg.add_csv_upload("", [(a.name, a), (b.name, b)])
    assert s.engine == "duckdb"
    assert sorted(reg.tables(s.id)) == ["monthly_sales", "regions"]
    with reg.engine(s.id).connect() as conn:
        assert conn.execute(text("SELECT sum(revenue) FROM monthly_sales")).scalar() == 250
        with pytest.raises(Exception):
            conn.execute(text("DROP TABLE regions"))


def test_registry_persists_and_removes(tmp_path):
    data = tmp_path / "data"
    reg = SourceRegistry(str(data))
    db = tmp_path / "x.db"
    _make_sqlite(db)
    s = reg.add_sqlite_upload("people", "x.db", db)
    reg.set_active(s.id, False)

    reloaded = SourceRegistry(str(data))
    assert reloaded.get(s.id).active is False
    assert "password" not in str(reloaded.get(s.id).public())

    reloaded.remove(s.id)
    assert not (data / "uploads" / s.id).exists()
    assert SourceRegistry(str(data)).list() == []
