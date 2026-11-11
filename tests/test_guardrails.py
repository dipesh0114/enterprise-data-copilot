"""Tests for the SQL security guard. Run: pytest -q"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))   # let tests import project files
from guardrails import validate_sql

ALLOWED = ["customers", "sales_orders"]

def test_plain_select_passes_and_gets_limit():
    ok, sql = validate_sql("SELECT name FROM customers", ALLOWED, "sqlite")
    assert ok and "LIMIT" in sql.upper()

def test_delete_blocked():
    assert not validate_sql("DELETE FROM customers", ALLOWED, "sqlite")[0]

def test_drop_blocked():
    assert not validate_sql("DROP TABLE customers", ALLOWED, "sqlite")[0]

def test_two_statements_blocked():
    assert not validate_sql("SELECT 1 FROM customers; DROP TABLE customers", ALLOWED, "sqlite")[0]

def test_disallowed_table_blocked():
    ok, reason = validate_sql("SELECT * FROM invoices", ALLOWED, "sqlite")
    assert not ok and "invoices" in reason

def test_hidden_table_in_subquery_blocked():
    sql = "SELECT name FROM customers WHERE customer_id IN (SELECT customer_id FROM invoices)"
    assert not validate_sql(sql, ALLOWED, "sqlite")[0]

def test_sleep_function_blocked():
    assert not validate_sql("SELECT SLEEP(10) FROM customers", ALLOWED, "mysql")[0]

def test_existing_limit_kept():
    ok, sql = validate_sql("SELECT name FROM customers LIMIT 5", ALLOWED, "sqlite")
    assert ok and "LIMIT 5" in sql.upper()
