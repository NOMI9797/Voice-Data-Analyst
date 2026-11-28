"""Data source registry.

Keeps the list of connected data sources (persisted to <data_dir>/sources.json)
and hands out SQLAlchemy engines that are READ-ONLY at the connection level:

  postgresql -> every session starts with default_transaction_read_only=on + statement_timeout
  mysql      -> SET SESSION TRANSACTION READ ONLY + max_execution_time on every connect
  sqlite     -> opened with ?mode=ro (file cannot be written)
  duckdb     -> opened with read_only=True (used for uploaded CSV files)

This is the last line of defence. The SQL guard (later step) blocks dangerous
statements before they ever reach the database.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine, make_url

from app.config import get_settings

# engine name -> sqlglot dialect name (used by the SQL generator / guard)
SQLGLOT_DIALECT = {"postgresql": "postgres", "mysql": "mysql", "sqlite": "sqlite", "duckdb": "duckdb"}

# schemas that belong to the database system, never to user data
SYSTEM_SCHEMAS = {
    "information_schema", "pg_catalog", "pg_toast", "mysql", "performance_schema", "sys",
}


# One connected database: name, engine, address or file, and its on/off switch.
@dataclass
class Source:
    id: str
    name: str
    engine: str  # postgresql | mysql | sqlite | duckdb
    url: str  # full SQLAlchemy URL, may contain a password: never sent to the client
    origin: str  # builtin | connection | upload
    active: bool = True
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # The SQL dialect name for this engine (postgres, mysql, sqlite, duckdb).
    @property
    def dialect(self) -> str:
        return SQLGLOT_DIALECT[self.engine]

    # Safe version for the browser: password hidden, file paths shortened.
    def public(self) -> dict:
        """Safe-to-show representation (password masked, file paths shortened)."""
        u = make_url(self.url)
        if self.engine in ("sqlite", "duckdb"):
            location = Path(_db_file_path(self.url)).name
        else:
            location = f"{u.host}:{u.port or ''}/{u.database}"
        return {
            "id": self.id,
            "name": self.name,
            "engine": self.engine,
            "dialect": self.dialect,
            "origin": self.origin,
            "active": self.active,
            "location": location,
            "display_url": u.render_as_string(hide_password=True),
            "created_at": self.created_at,
        }


# A user-facing problem with a source (bad address, unreachable, bad file).
class SourceError(ValueError):
    """User-facing error (bad URL, unreachable DB, bad file...)."""


# ---------------------------------------------------------------- URL handling

# Turns a sqlite/duckdb URL into the file path on disk.
def _db_file_path(url: str) -> str:
    db = make_url(url).database or ""
    return db[len("file:"):] if db.startswith("file:") else db


# Cleans a pasted connection string and works out which engine it is.
def normalize_url(raw: str) -> tuple[str, str]:
    """Turn a user-typed connection string into (sqlalchemy_url, engine_name)."""
    raw = raw.strip()
    if raw.startswith("postgres://"):
        raw = "postgresql://" + raw[len("postgres://"):]
    try:
        url = make_url(raw)
    except Exception as exc:
        raise SourceError(f"Not a valid connection string: {exc}") from exc

    backend = url.get_backend_name()
    if backend == "postgresql":
        url = url.set(drivername="postgresql+psycopg")
    elif backend in ("mysql", "mariadb"):
        url = url.set(drivername="mysql+pymysql")
        backend = "mysql"
    elif backend == "sqlite":
        path = _db_file_path(url.render_as_string(hide_password=False))
        if not path or path == ":memory:":
            raise SourceError("SQLite needs a file path, e.g. sqlite:////data/my.db")
        if not Path(path).is_file():
            raise SourceError(f"SQLite file not found: {path}")
        return f"sqlite:///file:{path}?mode=ro&uri=true", "sqlite"
    elif backend == "duckdb":
        path = _db_file_path(url.render_as_string(hide_password=False))
        if not Path(path).is_file():
            raise SourceError(f"DuckDB file not found: {path}")
        return f"duckdb:///{path}", "duckdb"
    else:
        raise SourceError(
            f"Unsupported engine '{backend}'. Use postgresql://, mysql://, sqlite:/// or a file upload."
        )

    # Inside Docker, "localhost" means the container itself, not the user's laptop.
    if os.environ.get("IN_DOCKER") == "1" and url.host in ("localhost", "127.0.0.1"):
        url = url.set(host="host.docker.internal")
    return url.render_as_string(hide_password=False), backend


# Opens the database connection in READ-ONLY mode for its engine.
def build_engine(url: str, engine_name: str) -> Engine:
    timeout_s = get_settings().query_timeout_seconds
    if engine_name == "postgresql":
        return create_engine(
            url,
            pool_pre_ping=True,
            connect_args={
                "connect_timeout": 5,
                "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout_s * 1000}",
            },
        )
    if engine_name == "mysql":
        eng = create_engine(
            url, pool_pre_ping=True, pool_recycle=1800,
            connect_args={"connect_timeout": 5, "read_timeout": timeout_s + 5},
        )

        @event.listens_for(eng, "connect")
        def _readonly(dbapi_conn, _record):  # noqa: ANN001
            cur = dbapi_conn.cursor()
            cur.execute("SET SESSION TRANSACTION READ ONLY")
            cur.execute(f"SET SESSION max_execution_time = {timeout_s * 1000}")
            cur.close()

        return eng
    if engine_name == "sqlite":
        return create_engine(url, connect_args={"check_same_thread": False})
    if engine_name == "duckdb":
        return create_engine(url, connect_args={"read_only": True})
    raise SourceError(f"Unsupported engine {engine_name}")


# Lists all tables and views in a database.
def list_tables(eng: Engine) -> list[str]:
    """Tables + views in all user schemas. Non-default schemas are prefixed."""
    if eng.dialect.name == "duckdb":
        # duckdb-engine reports catalog-qualified schemas ("data.main"); ask DuckDB directly.
        with eng.connect() as conn:
            rows = conn.execute(text(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_catalog = current_database() ORDER BY 1, 2"
            )).all()
        return [t if s == "main" else f"{s}.{t}" for s, t in rows]
    insp = inspect(eng)
    default = insp.default_schema_name
    names: list[str] = []
    for schema in insp.get_schema_names():
        if schema in SYSTEM_SCHEMAS or schema.startswith("pg_temp") or schema.startswith("pg_toast"):
            continue
        prefix = "" if schema == default else f"{schema}."
        names += [prefix + t for t in insp.get_table_names(schema=schema)]
        names += [prefix + v for v in insp.get_view_names(schema=schema)]
    return names


# Tests that a new connection works before it is saved.
def check_connection(eng: Engine) -> list[str]:
    try:
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return list_tables(eng)
    except Exception as exc:
        msg = str(getattr(exc, "orig", exc)).strip().splitlines()[0][:200]
        raise SourceError(f"Could not connect: {msg}") from exc


# ---------------------------------------------------------------- file uploads

_SAFE = re.compile(r"[^a-z0-9_]+")


# Makes a safe table name from a file name.
def safe_identifier(name: str) -> str:
    stem = _SAFE.sub("_", Path(name).stem.lower()).strip("_") or "table"
    return stem if not stem[0].isdigit() else f"t_{stem}"


# Loads uploaded CSV files into a new DuckDB file, one table per CSV.
def csvs_to_duckdb(csv_paths: list[Path], db_path: Path) -> list[str]:
    """Load each CSV as one table in a new DuckDB file. Returns table names."""
    import duckdb

    tables: list[str] = []
    con = duckdb.connect(str(db_path))
    try:
        for p in csv_paths:
            base = safe_identifier(p.name)
            name, i = base, 2
            while name in tables:
                name, i = f"{base}_{i}", i + 1
            lit = str(p).replace("'", "''")
            try:
                con.execute(
                    f'CREATE TABLE "{name}" AS SELECT * FROM '
                    f"read_csv_auto('{lit}', header=true, sample_size=20000)"
                )
            except Exception as exc:
                raise SourceError(f"Could not read {p.name} as CSV: {str(exc).splitlines()[0]}") from exc
            tables.append(name)
    finally:
        con.close()
    return tables


# ---------------------------------------------------------------- registry

# The list of all connected databases, saved to disk.
class SourceRegistry:
    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.uploads_dir = self.data_dir / "uploads"
        self.file = self.data_dir / "sources.json"
        self._lock = threading.Lock()
        self._sources: dict[str, Source] = {}
        self._engines: dict[str, Engine] = {}
        self._load()

    # ---- persistence
    # Reads the saved list of sources from disk.
    def _load(self) -> None:
        if self.file.exists():
            for d in json.loads(self.file.read_text()):
                s = Source(**d)
                self._sources[s.id] = s

    # Writes the list of sources to disk.
    def _save(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        tmp = self.file.with_suffix(".tmp")
        tmp.write_text(json.dumps([asdict(s) for s in self._sources.values()], indent=2))
        tmp.replace(self.file)

    # Adds the three demo databases on the very first start.
    def seed_builtins(self, builtins: list[tuple[str, str]]) -> None:
        """Register demo sources once (first start only), so a user's removals stick."""
        if self.file.exists():
            return
        for name, url in builtins:
            if not url:
                continue
            try:
                self.add_connection(name, url, origin="builtin", verify=False)
            except SourceError:
                pass
        self._save()

    # ---- queries
    # All sources, switched on or off.
    def list(self) -> list[Source]:
        return sorted(self._sources.values(), key=lambda s: s.created_at)

    # Only the sources that are switched on.
    def active(self) -> list[Source]:
        return [s for s in self.list() if s.active]

    # Finds one source by its id.
    def get(self, source_id: str) -> Source:
        try:
            return self._sources[source_id]
        except KeyError:
            raise SourceError(f"Unknown source {source_id}") from None

    # Gives the read-only connection for a source (cached).
    def engine(self, source_id: str) -> Engine:
        with self._lock:
            if source_id not in self._engines:
                s = self.get(source_id)
                self._engines[source_id] = build_engine(s.url, s.engine)
            return self._engines[source_id]

    # Lists the tables of one source.
    def tables(self, source_id: str) -> list[str]:
        return check_connection(self.engine(source_id))

    # ---- mutations
    # Checks a new source and adds it to the list.
    def _register(self, name: str, url: str, engine_name: str, origin: str,
                  verify: bool = True, source_id: str | None = None) -> Source:
        sid = source_id or uuid.uuid4().hex[:8]
        if verify:
            eng = build_engine(url, engine_name)
            try:
                check_connection(eng)
            except SourceError:
                eng.dispose()
                raise
            self._engines[sid] = eng
        s = Source(id=sid, name=name.strip() or engine_name, engine=engine_name, url=url, origin=origin)
        with self._lock:
            self._sources[s.id] = s
            self._save()
        return s

    # Adds a database from a connection string.
    def add_connection(self, name: str, raw_url: str, origin: str = "connection",
                       verify: bool = True) -> Source:
        url, engine_name = normalize_url(raw_url)
        for existing in self._sources.values():
            if existing.url == url:
                raise SourceError(f"This database is already connected as '{existing.name}'.")
        return self._register(name, url, engine_name, origin, verify)

    # Adds an uploaded SQLite file as a source.
    def add_sqlite_upload(self, name: str, filename: str, data_path: Path) -> Source:
        with open(data_path, "rb") as f:
            if f.read(16) != b"SQLite format 3\x00":
                raise SourceError(f"{filename} is not a SQLite database file.")
        sid = uuid.uuid4().hex[:8]
        dest_dir = self.uploads_dir / sid
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / (safe_identifier(filename) + ".db")
        shutil.move(str(data_path), dest)
        try:
            return self._register(name or Path(filename).stem, f"sqlite:///file:{dest}?mode=ro&uri=true",
                                  "sqlite", "upload", source_id=sid)
        except SourceError:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise

    # Adds uploaded CSV files as a source (via DuckDB).
    def add_csv_upload(self, name: str, files: list[tuple[str, Path]]) -> Source:
        sid = uuid.uuid4().hex[:8]
        dest_dir = self.uploads_dir / sid
        dest_dir.mkdir(parents=True, exist_ok=True)
        try:
            csv_paths = []
            for filename, tmp in files:
                dest = dest_dir / (safe_identifier(filename) + ".csv")
                shutil.move(str(tmp), dest)
                csv_paths.append(dest)
            db_path = dest_dir / "data.duckdb"
            csvs_to_duckdb(csv_paths, db_path)
            default_name = Path(files[0][0]).stem if len(files) == 1 else f"{len(files)} CSV files"
            return self._register(name or default_name, f"duckdb:///{db_path}", "duckdb", "upload",
                                  source_id=sid)
        except Exception:
            shutil.rmtree(dest_dir, ignore_errors=True)
            raise

    # The on/off switch for a source.
    def set_active(self, source_id: str, active: bool) -> Source:
        with self._lock:
            s = self.get(source_id)
            s.active = active
            self._save()
        return s

    # Renames a source.
    def rename(self, source_id: str, name: str) -> Source:
        with self._lock:
            s = self.get(source_id)
            s.name = name.strip() or s.name
            self._save()
        return s

    # Removes a source (and its uploaded file).
    def remove(self, source_id: str) -> None:
        with self._lock:
            s = self.get(source_id)
            eng = self._engines.pop(source_id, None)
            if eng is not None:
                eng.dispose()
            del self._sources[source_id]
            self._save()
        if s.origin == "upload":
            shutil.rmtree(self.uploads_dir / source_id, ignore_errors=True)


_registry: SourceRegistry | None = None


# Gives every file the same SourceRegistry (created once).
def get_registry() -> SourceRegistry:
    global _registry
    if _registry is None:
        s = get_settings()
        _registry = SourceRegistry(s.data_dir)
        _registry.seed_builtins([
            ("Olist e-commerce", s.demo_postgres_url),
            ("Chinook music store", s.demo_mysql_url),
            ("Northwind trading", s.demo_sqlite_url),
        ])
    return _registry
