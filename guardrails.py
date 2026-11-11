"""guardrails.py - plain Python (NOT an LLM) that decides whether AI-written SQL may run.

Analogy: the LLM is a clerk who writes a request slip; this file is the security guard
who reads every slip and only lets safe ones through.
"""
import sqlglot                                              # SQL parser
from sqlglot import exp                                     # node types of the parsed SQL tree
import config

# Statement types that change data or the database. None of these may appear anywhere.
FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create,
             exp.Alter, exp.Command, exp.Into, exp.Merge)

# Dangerous functions (sleep to hog the server, read server files).
FORBIDDEN_FUNCS = {"sleep", "benchmark", "load_file", "pg_sleep"}


def validate_sql(sql: str, allowed: list[str], dialect: str) -> tuple[bool, str]:
    """Return (True, cleaned_sql) if safe, or (False, reason) if rejected."""
    if not sql or not sql.strip():                          # empty text is useless
        return False, "Empty SQL."
    try:
        statements = sqlglot.parse(sql, read=dialect)       # split into statements and parse each
    except Exception as e:                                  # unparseable => reject
        return False, f"SQL could not be parsed: {e}"
    statements = [s for s in statements if s is not None]   # drop blanks from trailing ';'
    if len(statements) != 1:                                # "SELECT 1; DROP TABLE x" => 2 statements
        return False, "Exactly one statement is allowed."
    tree = statements[0]                                    # the single parsed statement
    if not isinstance(tree, (exp.Select, exp.Union)):       # must be a SELECT (or UNION of SELECTs)
        return False, "Only SELECT statements are allowed."
    for node in tree.walk():                                # visit every part of the query tree
        if isinstance(node, FORBIDDEN):                     # e.g. a hidden INSERT or INTO OUTFILE
            return False, f"Forbidden operation: {type(node).__name__}."
        if isinstance(node, exp.Func):                      # check function names
            name = (node.name or node.key).lower()
            if name in FORBIDDEN_FUNCS:
                return False, f"Forbidden function: {name}."
    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}   # WITH-clause names are not real tables
    used = {t.name.lower() for t in tree.find_all(exp.Table)} - cte_names   # real tables the query touches
    allowed_lower = {a.lower() for a in allowed}            # compare in lowercase
    blocked = sorted(used - allowed_lower)                  # tables used but not permitted for this role
    if blocked:
        return False, f"Role may not access table(s): {', '.join(blocked)}."
    if not tree.args.get("limit"):                          # no LIMIT written by the model?
        tree = tree.limit(config.MAX_ROWS)                  # add one so results stay small
    return True, tree.sql(dialect=dialect)                  # return the cleaned, re-generated SQL
