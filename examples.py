"""examples.py - example questions with their correct SQL.

Used in three places:
  1. few-shot examples inside the SQL agent prompt (teaches the LLM our style)
  2. the offline "mock" LLM (answers these without an API key)
  3. evaluate.py (the correct SQL is the answer key to measure accuracy)
"""

# Reusable join: order_items -> sales_orders -> customers -> regions
_REV_JOIN = ("FROM order_items oi JOIN sales_orders so ON so.order_id = oi.order_id "
             "JOIN customers c ON c.customer_id = so.customer_id "
             "JOIN regions r ON r.region_id = c.region_id")

EXAMPLES = [
    # ---------------- easy: one table ----------------
    {"level": "easy", "question": "How many customers do we have?",
     "sql": "SELECT COUNT(*) AS customer_count FROM customers"},
    {"level": "easy", "question": "How many products are in each category?",
     "sql": "SELECT category, COUNT(*) AS product_count FROM products GROUP BY category"},
    {"level": "easy", "question": "List all region names",
     "sql": "SELECT region_name FROM regions"},
    # ---------------- medium: joins ----------------
    {"level": "medium", "question": "What is the total revenue by region?",
     "sql": f"SELECT r.region_name, SUM(oi.quantity * oi.unit_price) AS revenue {_REV_JOIN} "
            "WHERE so.status <> 'Cancelled' GROUP BY r.region_name ORDER BY revenue DESC"},
    {"level": "medium", "question": "Show the top 5 products by revenue",
     "sql": "SELECT p.name, SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi "
            "JOIN products p ON p.product_id = oi.product_id JOIN sales_orders so ON so.order_id = oi.order_id "
            "WHERE so.status <> 'Cancelled' GROUP BY p.name ORDER BY revenue DESC LIMIT 5"},
    {"level": "medium", "question": "How many invoices are unpaid?",
     "sql": "SELECT COUNT(*) AS unpaid_invoices FROM invoices WHERE paid_on IS NULL"},
    {"level": "medium", "question": "Which 3 customers have the most orders?",
     "sql": "SELECT c.name, COUNT(*) AS order_count FROM sales_orders so JOIN customers c "
            "ON c.customer_id = so.customer_id GROUP BY c.name ORDER BY order_count DESC LIMIT 3"},
    {"level": "medium", "question": "What is the monthly revenue trend?",
     "sql": "SELECT SUBSTR(CAST(so.order_date AS CHAR), 1, 7) AS month, SUM(oi.quantity * oi.unit_price) AS revenue "
            "FROM order_items oi JOIN sales_orders so ON so.order_id = oi.order_id "
            "WHERE so.status <> 'Cancelled' GROUP BY month ORDER BY month"},
    # ---------------- hard: multi-table / date logic ----------------
    {"level": "hard", "question": "Which products are below their reorder level?",
     "sql": "SELECT p.name, i.warehouse, i.stock_qty, i.reorder_level FROM inventory i "
            "JOIN products p ON p.product_id = i.product_id WHERE i.stock_qty < i.reorder_level"},
    {"level": "hard", "question": "What was the total revenue in 2025?",
     "sql": "SELECT SUM(oi.quantity * oi.unit_price) AS revenue FROM order_items oi "
            "JOIN sales_orders so ON so.order_id = oi.order_id WHERE so.status <> 'Cancelled' "
            "AND so.order_date >= '2025-01-01' AND so.order_date < '2026-01-01'"},
    {"level": "hard", "question": "Which customers have overdue invoices?",
     "sql": "SELECT c.name, COUNT(*) AS overdue_invoices, SUM(inv.amount) AS overdue_amount FROM invoices inv "
            "JOIN sales_orders so ON so.order_id = inv.order_id JOIN customers c ON c.customer_id = so.customer_id "
            "WHERE inv.paid_on IS NULL AND inv.due_date < '2026-06-30' GROUP BY c.name ORDER BY overdue_amount DESC"},
]

# "Why" questions need a long-format result: entity, period, value (see agents.py).
WHY_EXAMPLE = {
    "question": "Why did South region sales drop in Q3 2025?",
    "sql": "SELECT c.name AS entity, "
           "CASE WHEN so.order_date >= '2025-04-01' AND so.order_date < '2025-07-01' THEN 'Q2 2025' ELSE 'Q3 2025' END AS period, "
           "SUM(oi.quantity * oi.unit_price) AS value "
           f"{_REV_JOIN} WHERE r.region_name = 'South' AND so.status <> 'Cancelled' "
           "AND so.order_date >= '2025-04-01' AND so.order_date < '2025-10-01' GROUP BY c.name, period",
}
