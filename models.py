"""models.py - the database tables, written once, working on both MySQL and SQLite."""
from sqlalchemy import (MetaData, Table, Column, Integer, String, Date, Numeric,
                        Text, DateTime, ForeignKey, func)

metadata = MetaData()                       # a container that remembers all tables defined below

# ---------- ERP-style business tables ----------
regions = Table("regions", metadata,
    Column("region_id", Integer, primary_key=True),          # unique id of the region
    Column("region_name", String(50), nullable=False))       # North, South, East ...

customers = Table("customers", metadata,
    Column("customer_id", Integer, primary_key=True),
    Column("name", String(100), nullable=False),             # company name
    Column("region_id", Integer, ForeignKey("regions.region_id")),  # link to regions
    Column("segment", String(30)),                           # Enterprise / SMB / Retail
    Column("created_on", Date))

products = Table("products", metadata,
    Column("product_id", Integer, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("category", String(50)),                          # Electronics, Furniture ...
    Column("unit_price", Numeric(10, 2)),                    # list price
    Column("unit_cost", Numeric(10, 2)))                     # what it costs us (for margin)

suppliers = Table("suppliers", metadata,
    Column("supplier_id", Integer, primary_key=True),
    Column("name", String(100), nullable=False),
    Column("country", String(50)))

sales_orders = Table("sales_orders", metadata,
    Column("order_id", Integer, primary_key=True),
    Column("customer_id", Integer, ForeignKey("customers.customer_id")),
    Column("order_date", Date),
    Column("status", String(20)),                            # Delivered / Shipped / Cancelled
    Column("total_amount", Numeric(12, 2)))                  # sum of the order's items

order_items = Table("order_items", metadata,
    Column("item_id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("sales_orders.order_id")),
    Column("product_id", Integer, ForeignKey("products.product_id")),
    Column("quantity", Integer),
    Column("unit_price", Numeric(10, 2)))                    # price actually charged on this order

invoices = Table("invoices", metadata,
    Column("invoice_id", Integer, primary_key=True),
    Column("order_id", Integer, ForeignKey("sales_orders.order_id")),
    Column("invoice_date", Date),
    Column("due_date", Date),
    Column("amount", Numeric(12, 2)),
    Column("paid_on", Date, nullable=True))                  # NULL means not paid yet

inventory = Table("inventory", metadata,
    Column("product_id", Integer, ForeignKey("products.product_id")),
    Column("warehouse", String(50)),
    Column("stock_qty", Integer),
    Column("reorder_level", Integer))                        # below this => needs restocking

purchase_orders = Table("purchase_orders", metadata,
    Column("po_id", Integer, primary_key=True),
    Column("supplier_id", Integer, ForeignKey("suppliers.supplier_id")),
    Column("product_id", Integer, ForeignKey("products.product_id")),
    Column("order_date", Date),
    Column("quantity", Integer),
    Column("unit_cost", Numeric(10, 2)))

# ---------- Tables used by the copilot itself ----------
query_audit_log = Table("query_audit_log", metadata,
    Column("log_id", Integer, primary_key=True, autoincrement=True),
    Column("user_role", String(30)),
    Column("question", Text),
    Column("generated_sql", Text),
    Column("status", String(20)),                            # ok / failed
    Column("rows_returned", Integer),
    Column("retries", Integer),
    Column("created_at", DateTime, server_default=func.now()))  # filled automatically

business_glossary = Table("business_glossary", metadata,
    Column("term", String(50), primary_key=True),            # e.g. revenue
    Column("definition", Text),                              # plain-English meaning
    Column("sql_expression", Text))                          # the exact formula to use

role_table_access = Table("role_table_access", metadata,
    Column("role", String(30)),                              # e.g. finance
    Column("table_name", String(50)))                        # a table that role may query
