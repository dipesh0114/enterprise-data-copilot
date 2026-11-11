"""seed.py - fills the database with realistic FAKE data (no real client data is used).

Two patterns are planted on purpose so the demo has real answers:
  1. Two big South-region customers order ~75% less in Q3 2025 (Jul-Sep).
  2. Electronics prices rise 10% from July 2025.
Run:  python seed.py
"""
import random                                               # fake data generator
from datetime import date, timedelta
from sqlalchemy import insert, delete
import db                                                   # our database helper (engines)
from models import *                                        # all table objects

random.seed(42)                                             # same data every run => repeatable demo


def month_starts(start: date, months: int):
    """Yield the first day of `months` consecutive months."""
    y, m = start.year, start.month
    for _ in range(months):
        yield date(y, m, 1)
        m += 1
        if m == 13:
            y, m = y + 1, 1


def main():
    db.create_all_tables()                                  # make sure tables exist
    with db.engine.begin() as conn:                         # one transaction for the whole seed
        for t in reversed(metadata.sorted_tables):          # wipe old data (children first)
            conn.execute(delete(t))

        # ---- regions ----
        region_names = ["North", "South", "East", "West", "Central"]
        conn.execute(insert(regions), [{"region_id": i + 1, "region_name": n}
                                       for i, n in enumerate(region_names)])
        south_id = region_names.index("South") + 1

        # ---- customers (ids 1 and 2 are the big South customers) ----
        customer_rows = [
            {"customer_id": 1, "name": "Apex Retail Pvt Ltd", "region_id": south_id, "segment": "Enterprise", "created_on": date(2024, 3, 1)},
            {"customer_id": 2, "name": "Bluewave Traders", "region_id": south_id, "segment": "Enterprise", "created_on": date(2024, 4, 1)},
        ]
        for cid in range(3, 21):                            # 18 more ordinary customers
            customer_rows.append({"customer_id": cid, "name": f"Customer {cid:02d} Co",
                                  "region_id": random.randint(1, 5),
                                  "segment": random.choice(["SMB", "Retail", "Enterprise"]),
                                  "created_on": date(2024, random.randint(1, 12), 1)})
        conn.execute(insert(customers), customer_rows)

        # ---- products ----
        categories = {"Electronics": (200, 1200), "Furniture": (80, 600),
                      "Stationery": (2, 40), "Appliances": (100, 900)}
        product_rows, pid = [], 1
        for cat, (lo, hi) in categories.items():
            for k in range(1, 9):                           # 8 products per category = 32 products
                price = round(random.uniform(lo, hi), 2)
                product_rows.append({"product_id": pid, "name": f"{cat} Item {k}", "category": cat,
                                     "unit_price": price, "unit_cost": round(price * random.uniform(0.55, 0.8), 2)})
                pid += 1
        conn.execute(insert(products), product_rows)
        prod = {p["product_id"]: p for p in product_rows}   # quick lookup by id

        # ---- suppliers + purchase orders ----
        supplier_rows = [{"supplier_id": i, "name": f"Supplier {i}", "country": random.choice(["India", "China", "Vietnam", "Germany"])}
                         for i in range(1, 7)]
        conn.execute(insert(suppliers), supplier_rows)
        po_rows = []
        for po_id in range(1, 201):
            p = random.choice(product_rows)
            po_rows.append({"po_id": po_id, "supplier_id": random.randint(1, 6), "product_id": p["product_id"],
                            "order_date": date(2025, 1, 1) + timedelta(days=random.randint(0, 540)),
                            "quantity": random.randint(20, 200), "unit_cost": p["unit_cost"]})
        conn.execute(insert(purchase_orders), po_rows)

        # ---- inventory (some products deliberately below reorder level) ----
        inv_rows = []
        for p in product_rows:
            for wh in ("Bangalore WH", "Hyderabad WH"):
                reorder = random.randint(20, 60)
                inv_rows.append({"product_id": p["product_id"], "warehouse": wh,
                                 "stock_qty": random.randint(5, 200), "reorder_level": reorder})
        conn.execute(insert(inventory), inv_rows)

        # ---- orders, items, invoices: 18 months, Jan 2025 - Jun 2026 ----
        order_id = item_id = invoice_id = 0
        orders, items, invoices_rows = [], [], []
        for ms in month_starts(date(2025, 1, 1), 18):
            for _ in range(random.randint(150, 185)):       # ~3,000 orders in total
                cust = random.choice(customer_rows)
                in_q3_2025 = date(2025, 7, 1) <= ms <= date(2025, 9, 1)
                if cust["customer_id"] in (1, 2) and in_q3_2025 and random.random() < 0.75:
                    continue                                # PLANTED PATTERN 1: skip 75% of their Q3 orders
                order_id += 1
                odate = ms + timedelta(days=random.randint(0, 27))
                total = 0
                for _ in range(random.randint(1, 4)):       # 1-4 line items per order
                    p = random.choice(product_rows)
                    price = float(p["unit_price"])
                    if p["category"] == "Electronics" and odate >= date(2025, 7, 1):
                        price = round(price * 1.10, 2)      # PLANTED PATTERN 2: +10% from July 2025
                    qty = random.randint(1, 10)
                    item_id += 1
                    items.append({"item_id": item_id, "order_id": order_id, "product_id": p["product_id"],
                                  "quantity": qty, "unit_price": price})
                    total += qty * price
                orders.append({"order_id": order_id, "customer_id": cust["customer_id"], "order_date": odate,
                               "status": random.choices(["Delivered", "Shipped", "Cancelled"], [85, 10, 5])[0],
                               "total_amount": round(total, 2)})
                invoice_id += 1
                due = odate + timedelta(days=30)
                paid = due + timedelta(days=random.randint(-10, 25)) if random.random() < 0.85 else None
                invoices_rows.append({"invoice_id": invoice_id, "order_id": order_id, "invoice_date": odate,
                                      "due_date": due, "amount": round(total, 2), "paid_on": paid})
        conn.execute(insert(sales_orders), orders)          # parents first, then children (foreign keys)
        conn.execute(insert(order_items), items)
        conn.execute(insert(invoices), invoices_rows)

        # ---- business glossary: one agreed formula per business word ----
        conn.execute(insert(business_glossary), [
            {"term": "revenue", "definition": "Money from sales, excluding cancelled orders",
             "sql_expression": "SUM(order_items.quantity * order_items.unit_price) joined to sales_orders WHERE sales_orders.status <> 'Cancelled'"},
            {"term": "margin", "definition": "Revenue minus product cost",
             "sql_expression": "SUM(order_items.quantity * (order_items.unit_price - products.unit_cost))"},
            {"term": "overdue invoice", "definition": "Unpaid invoice past its due date",
             "sql_expression": "invoices.paid_on IS NULL AND invoices.due_date < '2026-06-30'"},
            {"term": "low stock", "definition": "Stock below the reorder level",
             "sql_expression": "inventory.stock_qty < inventory.reorder_level"},
        ])

        # ---- roles: which tables each role may query ----
        business = ["regions", "customers", "products", "sales_orders", "order_items"]
        role_map = {
            "admin": business + ["suppliers", "invoices", "inventory", "purchase_orders"],
            "sales_manager": business,
            "finance": ["regions", "customers", "sales_orders", "order_items", "invoices"],
            "warehouse": ["products", "inventory", "suppliers", "purchase_orders"],
        }
        conn.execute(insert(role_table_access), [{"role": r, "table_name": t}
                                                 for r, tables in role_map.items() for t in tables])
    print(f"Seeded {len(orders)} orders, {len(items)} items, {len(invoices_rows)} invoices.")


if __name__ == "__main__":                                  # only run when called directly: python seed.py
    main()
