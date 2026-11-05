"""NL analytics (SPEC §7.10): text-to-SQL over curated read-only views, validated with sqlglot."""
from __future__ import annotations

import re
import threading
from pathlib import Path

import duckdb
import pandas as pd
import sqlglot
from sqlglot import exp
from sklearn.feature_extraction.text import TfidfVectorizer

from ..db import Data, clean, create_views, register_tables

PROMPT = (Path(__file__).resolve().parents[1] / "agent" / "prompts" / "nlq.md").read_text()
BASE_VIEWS = {"v_incident", "v_store", "v_change", "v_open_incident", "problem", "kb_knowledge"}
SENSITIVE = {"v_store_network_sensitive"}
FORBIDDEN = re.compile(r"\b(read_csv\w*|read_parquet|read_json\w*|read_text|read_blob|glob|copy|attach|detach|pragma|"
                       r"install|load|export|import|sniff_csv|parquet_scan|csv_scan|getenv|set|call)\b\s*\(?", re.I)
FORBIDDEN_STMT = re.compile(r"^\s*(copy|attach|detach|pragma|install|load|export|import|set|call|create|drop|insert|"
                            r"update|delete|alter)\b", re.I)


class SQLRejected(ValueError):
    pass


def examples() -> list[tuple[str, str]]:
    return re.findall(r"^Q: (.+)\nSQL: (.+)$", PROMPT, re.M)


def allowed_views(scope) -> set[str]:
    return BASE_VIEWS | (SENSITIVE if scope.can_view_network else set())


def validate_sql(sql: str, scope) -> str:
    sql = sql.strip().strip(";").strip()
    sql = re.sub(r"^```(?:sql)?|```$", "", sql, flags=re.I).strip()
    if FORBIDDEN_STMT.search(sql):
        raise SQLRejected("Only SELECT statements are allowed.")
    try:
        stmts = [s for s in sqlglot.parse(sql, read="duckdb") if s is not None]
    except sqlglot.errors.ParseError as e:
        raise SQLRejected(f"SQL parse error: {str(e)[:200]}")
    if len(stmts) != 1:
        raise SQLRejected("Exactly one statement is allowed.")
    st = stmts[0]
    if not isinstance(st, (exp.Select, exp.Union, exp.Intersect, exp.Except)) and not (
            isinstance(st, exp.Subquery)):
        raise SQLRejected("Only SELECT statements are allowed.")
    for node in st.walk():
        if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop, exp.Command, exp.Alter)):
            raise SQLRejected("Only read-only SELECT is allowed.")
    ctes = {c.alias_or_name.lower() for c in st.find_all(exp.CTE)}
    allowed = allowed_views(scope)
    for t in st.find_all(exp.Table):
        name = t.name.lower()
        if not name:
            raise SQLRejected("Table functions are not allowed.")
        if name in ctes:
            continue
        if name not in allowed:
            raise SQLRejected(f"Table '{t.name}' is not allowed. Use: {', '.join(sorted(allowed))}.")
    for f in st.find_all(exp.Anonymous, exp.Func):
        fname = (f.name if isinstance(f, exp.Anonymous) else f.sql_name()).lower()
        if FORBIDDEN.fullmatch(fname + "(") or fname.startswith("read_") or fname in ("glob", "getenv"):
            raise SQLRejected(f"Function '{fname}' is not allowed.")
    if FORBIDDEN.search(re.sub(r"'[^']*'", "''", sql)) and re.search(r"\b(read_\w+|glob|getenv)\s*\(", sql, re.I):
        raise SQLRejected("File functions are not allowed.")
    if isinstance(st, exp.Select) and not st.args.get("limit"):
        st = st.limit(500)
    elif not isinstance(st, exp.Select) and "limit" not in sql.lower():
        return f"SELECT * FROM ({st.sql(dialect='duckdb')}) LIMIT 500"
    return st.sql(dialect="duckdb")


def scoped_connection(data: Data, scope):
    con = duckdb.connect(":memory:")
    from ..db import _TS
    con.create_function("clock_now", lambda: data.now().floor("us").to_pydatetime(), [], _TS, side_effects=True)
    register_tables(con, data, scope)
    create_views(con, include_sensitive=scope.can_view_network)
    if not scope.can_view_financials:
        pass  # est_lost_sales_usd already dropped from the scoped incident frame
    return con


def run_sql(data: Data, scope, sql: str, timeout_s: float = 5.0) -> pd.DataFrame:
    con = scoped_connection(data, scope)
    result: dict = {}

    def work():
        try:
            result["df"] = con.execute(sql).fetchdf()
        except Exception as e:  # noqa: BLE001
            result["err"] = e
    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        con.interrupt()
        th.join(1)
        raise SQLRejected("Query timed out (5s).")
    con.close()
    if "err" in result:
        raise SQLRejected(f"Query failed: {str(result['err'])[:200]}")
    return result["df"]


def chart_spec(df: pd.DataFrame) -> dict:
    if df.empty or len(df.columns) < 2:
        return dict(type="table")
    num = [c for c in df.columns if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])]
    tim = [c for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    cat = [c for c in df.columns if c not in num and c not in tim]
    if tim and num:
        return dict(type="line", x=tim[0], y=num[-1])
    if len(df.columns) == 2 and cat and num:
        return dict(type="bar", x=cat[0], y=num[0])
    if cat and num and len(df) <= 30:
        return dict(type="bar", x=cat[0], y=num[-1])
    return dict(type="table")


_EX: dict = {}


def template_sql(question: str) -> tuple[str, str]:
    """Offline NLQ: pick the closest few-shot example (no LLM)."""
    ex = examples()
    if not _EX:
        v = TfidfVectorizer(ngram_range=(1, 2)).fit([q for q, _ in ex] + [question])
        _EX.update(v=v, X=v.transform([q for q, _ in ex]))
    v = _EX["v"]
    sims = (_EX["X"] @ v.transform([question]).T).toarray().ravel()
    i = int(sims.argmax())
    return ex[i][1], ex[i][0]


def answer(data: Data, scope, question: str, llm_sql=None) -> dict:
    """llm_sql: callable(question, error|None) -> sql. Falls back to template_sql."""
    attempts, sql_raw, matched = [], None, None
    for attempt in range(2):
        err = attempts[-1]["error"] if attempts else None
        if llm_sql is not None:
            try:
                sql_raw = llm_sql(question, err)
            except Exception as e:  # noqa: BLE001
                sql_raw, llm_sql = None, None
                attempts.append(dict(sql=None, error=f"LLM unavailable: {e}"))
        if sql_raw is None:
            sql_raw, matched = template_sql(question)
        try:
            sql = validate_sql(sql_raw, scope)
            df = run_sql(data, scope, sql)
            rows = [[clean(v) for v in row] for row in df.itertuples(index=False)]
            return dict(question=question, sql=sql, columns=list(df.columns), rows=rows, row_count=len(rows),
                        chart_spec=chart_spec(df), matched_example=matched, attempts=attempts, ok=True,
                        mode="llm" if llm_sql else "template")
        except SQLRejected as e:
            attempts.append(dict(sql=sql_raw, error=str(e)))
            sql_raw = None
    return dict(question=question, ok=False, sql=None, columns=[], rows=[], chart_spec=dict(type="table"),
                attempts=attempts, error="Sorry — I couldn't build a safe query for that. Try rephrasing.")
