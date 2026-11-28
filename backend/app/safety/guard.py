"""SQL safety guard: the gate every query must pass before it reaches a database.

Works on the parsed syntax tree (sqlglot), never on string matching, and fails
CLOSED: anything it cannot parse or understand is rejected.

Rules
  1. exactly one statement
  2. the statement is a read-only query: SELECT / WITH ... SELECT / UNION ...
  3. no write or admin nodes anywhere in the tree (INSERT, UPDATE, DELETE, MERGE,
     CREATE, DROP, ALTER, TRUNCATE, GRANT, SET, PRAGMA, ATTACH, COPY, SELECT INTO,
     SELECT ... FOR UPDATE, raw commands...)
  4. no dangerous functions (sleep / file access / network / admin)
  5. only tables that exist in the profiled schema (+ the query's own CTEs);
     this also blocks system catalogs (pg_catalog, information_schema, mysql.user,
     sqlite_master ...) and table functions like read_csv('/etc/passwd')
  6. secret columns (passwords, tokens, card numbers) are never selected;
     personal data columns (email, phone, address, and the names of customers or
     other private people, even inside CONCAT) may be counted or used in
     filters, but not returned as raw values
  7. a row LIMIT is always present (added or lowered)

The database connection is read-only as well; this guard is the first line of
defence, the read-only session is the last.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

# ---------------------------------------------------------------- policies

FORBIDDEN_NODES = tuple(
    getattr(exp, n) for n in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "AlterTable",
        "TruncateTable", "Command", "Into", "Lock", "Set", "Pragma", "Use", "Transaction",
        "Commit", "Rollback", "Copy", "Grant", "Revoke", "Attach", "Detach", "Describe", "Show",
        "LoadData", "Kill", "Analyze", "Cache", "Uncache", "Refresh", "Install",
    ) if hasattr(exp, n)
)

DANGEROUS_FUNCTIONS = {
    # time / denial of service
    "sleep", "pg_sleep", "pg_sleep_for", "pg_sleep_until", "benchmark", "waitfor",
    # file system / server access
    "load_file", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "lo_import", "lo_export", "lo_get", "lo_put", "readfile", "writefile", "load_extension",
    "read_csv", "read_csv_auto", "read_parquet", "read_json", "read_json_auto", "read_text",
    "read_blob", "read_ndjson", "glob", "parquet_scan", "csv_scan", "sniff_csv", "httpfs",
    "readcsv", "readparquet", "readjson",
    # network / remote databases
    "dblink", "dblink_exec", "postgres_scan", "mysql_scan", "sqlite_scan", "query_table",
    # admin / session
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile",
    "set_config", "pg_advisory_lock", "get_lock", "sys_exec", "sys_eval", "xp_cmdshell",
    "current_setting", "version", "pg_postmaster_start_time", "user", "current_user",
    "session_user", "system_user", "database",
    "duckdb_settings", "duckdb_extensions", "duckdb_secrets", "getenv",
}

SECRET_COLUMN = re.compile(
    r"(pass(word|wd)?|pwd|secret|token|api_?key|private_?key|salt|ssn|social_?security"
    r"|credit_?card|card_?(number|num|no)|cvv|cvc|iban|pin_?code)", re.I)
PII_COLUMN = re.compile(
    r"(e_?mail|phone|mobile|fax|cpf|cnpj|birth|dob|passport|national_?id|tax_?id"
    r"|(^|_)address|address$|street|ip_?addr)", re.I)
# Names of PRIVATE people (customers, users ...) are personal data too. Names of
# employees, artists, products and companies are not blocked: staff names are normal
# in internal reports ("which sales rep has the most customers").
PERSON_NAME_COLUMN = re.compile(
    r"^((first|last|full|given|family|middle|sur|fore|contact|customer|client|user|member|patient)_?name"
    r"|surname|forename)$", re.I)
PRIVATE_PERSON_TABLE = re.compile(
    r"(customer|client|user|member|patient|contact|person|people|subscriber|guest|student|account)", re.I)


# Whether SQL is allowed, the safe SQL, or the reason it was blocked.
@dataclass
class GuardResult:
    ok: bool
    sql: str = ""  # rewritten, safe SQL (dialect-specific) when ok
    reason: str = ""  # short user-facing explanation when rejected
    code: str = ""  # machine-readable category
    tables: list[str] = field(default_factory=list)
    limited: bool = False  # a LIMIT was added or lowered


# Builds a 'blocked, because ...' result.
def _reject(code: str, reason: str) -> GuardResult:
    return GuardResult(ok=False, code=code, reason=reason)


# The name of a SQL function in the syntax tree.
def _func_name(node: exp.Expression) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.name).lower()
    return type(node).__name__.lower()


# All names a SQL function can be known by.
def _sql_func_names(node: exp.Expression) -> set[str]:
    """All names a function node may be known by (class name, SQL name)."""
    names = {_func_name(node)}
    try:
        names.add(node.sql_name().lower())
    except Exception:
        pass
    return names


# Lowercase schema and table name of a table in the query.
def _norm_table(t: exp.Table) -> tuple[str, str]:
    return (t.db or "").lower(), t.name.lower()


# ---------------------------------------------------------------- main entry

# MAIN: checks the SQL's syntax tree: read-only, one statement, known tables, no dangerous functions, no private data, adds LIMIT.
def check_sql(
    sql: str,
    dialect: str,
    allowed_tables: dict[str, list[str]],
    row_limit: int = 1000,
    pii_policy: str = "block",
) -> GuardResult:
    """
    allowed_tables: {table name as profiled (maybe 'schema.table'): [column names]}
    """
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        return _reject("empty", "No SQL to run.")

    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except ParseError as exc:
        return _reject("parse_error", f"The SQL could not be parsed safely: {str(exc).splitlines()[0][:150]}")
    except Exception as exc:  # unknown parser failure -> fail closed
        return _reject("parse_error", f"The SQL could not be parsed safely: {exc}")

    if len(statements) != 1:
        return _reject("multiple_statements", "Only a single query is allowed.")
    root = statements[0]

    if not isinstance(root, (exp.Select, exp.SetOperation)):
        return _reject("not_select", f"Only read-only SELECT queries are allowed (got {root.key.upper()}).")

    for node in root.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return _reject("write_operation",
                           f"The query contains a forbidden operation ({type(node).__name__.upper()}).")
        if isinstance(node, exp.Func):
            bad = _sql_func_names(node) & DANGEROUS_FUNCTIONS
            if bad:
                return _reject("dangerous_function", f"The function {sorted(bad)[0]}() is not allowed.")

    # ---- tables: must be known tables (or the query's own CTEs)
    cte_names = {c.alias_or_name.lower() for c in root.find_all(exp.CTE)}
    known: dict[str, str] = {}  # lookup key -> profiled name
    for name in allowed_tables:
        low = name.lower()
        known[low] = name
        if "." in low:
            known.setdefault(low.split(".", 1)[1], name)  # allow unqualified use if unique
    used: list[str] = []
    for t in root.find_all(exp.Table):
        if isinstance(t.this, exp.Func) or not t.name:
            return _reject("table_function", "Reading from table functions or files is not allowed.")
        db, name = _norm_table(t)
        if not db and name in cte_names:
            continue
        candidates = [f"{db}.{name}" if db else name]
        if db in ("public", "main", "dbo"):
            candidates.append(name)  # default schema written explicitly
        match = next((known[c] for c in candidates if c in known), None)
        if match is None:
            full = f"{db}.{name}" if db else name
            if db in ("information_schema", "pg_catalog", "mysql", "performance_schema", "sys") \
                    or name.startswith(("pg_", "sqlite_", "duckdb_")):
                return _reject("system_table", f"System table {full} is not accessible.")
            return _reject("unknown_table", f"Table {full} does not exist in this data source.")
        used.append(match)

    # ---- sensitive columns
    for col in root.find_all(exp.Column):
        if col.name and SECRET_COLUMN.search(col.name):
            return _reject("secret_column", f"Column {col.name} holds secrets and cannot be queried.")
    if pii_policy == "block":
        reason = _pii_in_output(root, allowed_tables, used)
        if reason:
            return _reject("personal_data", reason)

    # ---- row limit
    limited = False
    if isinstance(root, exp.Select):
        lim = root.args.get("limit")
        current = None
        if lim is not None:
            expr = lim.expression if isinstance(lim, exp.Limit) else None
            if isinstance(expr, exp.Literal) and expr.is_int:
                current = int(expr.this)
        if current is None or current > row_limit:
            root = root.limit(row_limit, copy=False)
            limited = True
    else:  # UNION etc.: wrap
        root = exp.select("*").from_(root.subquery("q")).limit(row_limit)
        limited = True

    try:
        safe_sql = root.sql(dialect=dialect)
    except Exception as exc:
        return _reject("render_error", f"Could not render SQL: {exc}")
    return GuardResult(ok=True, sql=safe_sql, tables=sorted(set(used)), limited=limited)


# The SELECTs whose results are actually returned.
def _outer_selects(root: exp.Expression) -> list[exp.Select]:
    if isinstance(root, exp.SetOperation):
        return _outer_selects(root.left) + _outer_selects(root.right)
    return [root] if isinstance(root, exp.Select) else []


# Finds customer-like name columns (but allows staff names).
def _private_name_check(root: exp.Expression, allowed: dict[str, list[str]], used: list[str]):
    """Returns is_private(column) for person-name columns of customer-like tables. The
    column's table is resolved through its alias, so Employee.FirstName stays allowed
    next to Customer.FirstName; an unqualified column counts as private if any
    customer-like table in the query has it."""
    private = {t: {c.lower() for c in allowed.get(t, []) if PERSON_NAME_COLUMN.search(c)}
               for t in used if PRIVATE_PERSON_TABLE.search(t.rsplit(".", 1)[-1])}
    private = {t: cols for t, cols in private.items() if cols}
    if not private:
        return lambda col: False
    by_short = {t.rsplit(".", 1)[-1].lower(): t for t in used}
    aliases = {}
    for tbl in root.find_all(exp.Table):
        target = by_short.get(tbl.name.lower())
        if target:
            aliases[(tbl.alias or tbl.name).lower()] = target

    def is_private(col: exp.Column) -> bool:
        name = col.name.lower()
        if col.table:
            return name in private.get(aliases.get(col.table.lower(), ""), set())
        return any(name in cols for cols in private.values())
    return is_private


# Blocks listing private data (emails, phones, addresses, customer names).
def _pii_in_output(root: exp.Expression, allowed: dict[str, list[str]], used: list[str]) -> str:
    """Reject raw personal-data values in the final output. Aggregates (COUNT(email)) are fine."""
    is_private_name = _private_name_check(root, allowed, used)
    for sel in _outer_selects(root):
        for proj in sel.expressions:
            node = proj.this if isinstance(proj, exp.Alias) else proj
            if isinstance(node, exp.Star) or (isinstance(node, exp.Column) and isinstance(node.this, exp.Star)):
                for t in used:
                    cols = [c for c in allowed.get(t, []) if SECRET_COLUMN.search(c) or PII_COLUMN.search(c)
                            or (PRIVATE_PERSON_TABLE.search(t.rsplit(".", 1)[-1]) and PERSON_NAME_COLUMN.search(c))]
                    if cols:
                        return (f"SELECT * would return personal data ({', '.join(cols[:3])}). "
                                "Ask for specific, non-personal columns instead.")
                continue
            if node.find(exp.AggFunc):
                continue  # COUNT(email), COUNT(DISTINCT phone) ... are fine
            for col in node.find_all(exp.Column):
                if col.name and (PII_COLUMN.search(col.name) or is_private_name(col)):
                    return (f"Listing {col.name} would expose personal data. "
                            "I can count or group by it, but not show individual values.")
    return ""
