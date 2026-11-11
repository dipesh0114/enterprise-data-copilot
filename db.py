"""db.py - every database operation the copilot needs, in one place."""
import pandas as pd                                         # query results come back as DataFrames
from sqlalchemy import create_engine, text, inspect, insert, select
import config                                               # our settings
from models import (metadata, query_audit_log, business_glossary, role_table_access)

engine = create_engine(config.DB_URL)                       # ADMIN connection: seeding + audit log writes
ro_engine = create_engine(config.READONLY_DB_URL)           # READ-ONLY connection: runs AI-written SQL
DIALECT = engine.dialect.name                               # "mysql" or "sqlite" - tells the LLM which SQL flavour to write

# Tables that exist only for the copilot's own bookkeeping. The AI may never query these.
INTERNAL_TABLES = {"query_audit_log", "business_glossary", "role_table_access"}


def create_all_tables():
    """Create every table if it does not exist yet."""
    metadata.create_all(engine)


def allowed_tables(role: str) -> list[str]:
    """Which tables may this role query? Read from the role_table_access table."""
    with engine.connect() as conn:                          # open a connection, auto-close afterwards
        rows = conn.execute(select(role_table_access.c.table_name)
                            .where(role_table_access.c.role == role)).fetchall()
    return [r[0] for r in rows]                             # turn rows into a plain list of names


def list_roles() -> list[str]:
    """All roles that exist (used by the UI dropdown)."""
    with engine.connect() as conn:
        rows = conn.execute(select(role_table_access.c.role).distinct()).fetchall()
    return sorted(r[0] for r in rows)


def schema_text(tables: list[str]) -> str:
    """Describe the chosen tables (columns + foreign keys) as text the LLM can read."""
    insp = inspect(engine)                                  # SQLAlchemy's schema reader
    lines = []                                              # we build the description line by line
    for t in tables:
        cols = ", ".join(f"{c['name']} ({str(c['type']).split('(')[0]})" for c in insp.get_columns(t))
        lines.append(f"TABLE {t}: {cols}")                  # e.g. TABLE customers: customer_id (INTEGER), ...
        for fk in insp.get_foreign_keys(t):                 # also list how tables connect
            lines.append(f"  JOIN HINT: {t}.{fk['constrained_columns'][0]} = "
                         f"{fk['referred_table']}.{fk['referred_columns'][0]}")
    return "\n".join(lines)


def glossary_text() -> str:
    """The business glossary as text: 'revenue = SUM(quantity * unit_price)'."""
    with engine.connect() as conn:
        rows = conn.execute(select(business_glossary)).fetchall()
    return "\n".join(f"- {r.term}: {r.definition} => {r.sql_expression}" for r in rows)


def run_select(sql: str) -> pd.DataFrame:
    """Run an ALREADY VALIDATED select on the read-only connection and return a DataFrame."""
    with ro_engine.connect() as conn:
        if ro_engine.dialect.name == "mysql":               # MySQL only: stop queries that run too long
            conn.execute(text("SET SESSION MAX_EXECUTION_TIME=10000"))  # 10 seconds
        return pd.read_sql(text(sql), conn)                 # execute and load into pandas


def write_audit(role: str, question: str, sql: str, status: str, rows: int, retries: int):
    """Save one line in the audit log: who asked what, which SQL ran, what happened."""
    with engine.begin() as conn:                            # begin() auto-commits on success
        conn.execute(insert(query_audit_log).values(
            user_role=role, question=question, generated_sql=sql,
            status=status, rows_returned=rows, retries=retries))


def recent_audit(limit: int = 20) -> pd.DataFrame:
    """Latest audit log entries (shown in the UI)."""
    return pd.read_sql(text(f"SELECT * FROM query_audit_log ORDER BY log_id DESC LIMIT {int(limit)}"), engine)
