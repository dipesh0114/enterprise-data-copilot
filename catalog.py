"""catalog.py - "what do we know, and what can the user ask?"

This is the copilot's tour guide. It knows what data exists (tables, row counts, date range, business
terms, documents) and uses that to:
  * explain the data when the user asks ("what is the data about?", "explain the customers table")
  * recognise whether a message is about the data at all
  * guess what a vague message might mean and suggest questions that WILL work
It needs no LLM, so it works in mock mode and costs nothing.
"""
import re, difflib
from functools import lru_cache
import pandas as pd
from sqlalchemy import inspect, text, select
import db, rag
from models import business_glossary
from examples import EXAMPLES, WHY_EXAMPLE

# Plain-English description of each table (shown to the user).
TABLE_INFO = {
    "regions": "Sales regions: North, South, East, West, Central.",
    "customers": "Customer companies, with their region and segment (Enterprise, SMB, Retail).",
    "products": "Products with category, list price and cost.",
    "suppliers": "Suppliers and the country they ship from.",
    "sales_orders": "One row per sales order: customer, date, status and total amount.",
    "order_items": "The products on each order: quantity and the price charged.",
    "invoices": "Invoices for orders: due date, amount and paid date (empty = not paid yet).",
    "inventory": "Stock per product and warehouse, with the reorder level.",
    "purchase_orders": "Purchases from suppliers: product, quantity and unit cost.",
}
# Small "category" columns whose values (North, Electronics...) are words users naturally type.
DIM_COLUMNS = [("regions", "region_name"), ("customers", "segment"), ("products", "category"),
               ("suppliers", "country"), ("inventory", "warehouse"), ("sales_orders", "status")]
# Everyday words that point to tables even though no column has that name ("revenue" is a calculation, not a column).
TOPIC_HINTS = {
    "revenue": ["sales_orders", "order_items"], "sale": ["sales_orders", "order_items"], "money": ["sales_orders", "invoices"],
    "income": ["sales_orders", "order_items"], "profit": ["order_items", "products"], "margin": ["order_items", "products"],
    "buy": ["purchase_orders"], "purchase": ["purchase_orders"], "payment": ["invoices"], "paid": ["invoices"],
    "unpaid": ["invoices"], "overdue": ["invoices"], "bill": ["invoices"], "owe": ["invoices"], "stock": ["inventory"],
    "shipping": ["suppliers", "purchase_orders"], "delay": ["suppliers", "purchase_orders"], "client": ["customers"],
    "item": ["products", "order_items"], "buyer": ["customers"],
}
GENERIC = {"name", "date", "created"}                       # too common to prove a question is about our data


# ----------------------------------------------------------------- small helpers
def _stem(w: str) -> str:
    """Very simple plural handling: customers -> customer."""
    return w[:-1] if len(w) > 3 and w.endswith("s") else w

def _tokens(s: str) -> list[str]:
    """Lower-case word stems of 3+ letters."""
    return [_stem(w) for w in re.findall(r"[a-z]+", s.lower()) if len(w) >= 3]

def sql_tables(sql: str) -> set[str]:
    """Tables mentioned after FROM / JOIN in a SQL string."""
    return set(re.findall(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", sql, flags=re.I))

def _count(table: str) -> int:
    with db.engine.connect() as c:
        return int(c.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar() or 0)   # table names come from the DB itself


# ----------------------------------------------------------------- what data exists
def overview(role: str) -> pd.DataFrame:
    """A table the user can read: which tables this role can see, what they hold, how many rows."""
    allowed = db.allowed_tables(role)
    ordered = [t for t in TABLE_INFO if t in allowed] + [t for t in allowed if t not in TABLE_INFO]
    return pd.DataFrame([{"Table": t, "What it holds": TABLE_INFO.get(t, ""), "Rows": _count(t)} for t in ordered])

def data_period():
    """(first order date, last order date) as text, or None."""
    try:
        with db.engine.connect() as c:
            lo, hi = c.execute(text("SELECT MIN(order_date), MAX(order_date) FROM sales_orders")).one()
        return (str(lo)[:10], str(hi)[:10]) if lo else None
    except Exception:
        return None

def glossary_terms() -> list[tuple]:
    """[(term, definition, sql_expression)] from the business glossary."""
    with db.engine.connect() as c:
        return [(r.term, r.definition, r.sql_expression) for r in c.execute(select(business_glossary)).fetchall()]

def documents() -> pd.DataFrame:
    """Each loaded document with the start of its first paragraph."""
    first = {}
    for ch in rag._load_chunks():
        first.setdefault(ch["source"], ch["text"])
    return pd.DataFrame([{"Document": n, "Starts with": (first.get(n, "")[:140] + "...")} for n in rag.list_documents()])


# ----------------------------------------------------------------- is this message about our data?
@lru_cache(maxsize=1)
def vocabulary() -> frozenset:
    """Every word that means 'our data': table names, column names, glossary terms, region/category/warehouse values."""
    words = set()
    insp = inspect(db.engine)
    for t in insp.get_table_names():
        if t in db.INTERNAL_TABLES:
            continue
        words |= set(_tokens(t.replace("_", " ")))
        for c in insp.get_columns(t):
            words |= set(_tokens(c["name"].replace("_", " ")))
    with db.engine.connect() as conn:
        for t, col in DIM_COLUMNS:
            try:
                for (v,) in conn.execute(text(f"SELECT DISTINCT {col} FROM {t}")).fetchall():
                    words |= set(_tokens(str(v)))
            except Exception:
                pass
        for (term,) in conn.execute(select(business_glossary.c.term)).fetchall():
            words |= set(_tokens(term))
    return frozenset(words - set(_tokens(" ".join(GENERIC))) - GENERIC)

def mentions_data(question: str) -> bool:
    """True if the message uses at least one word that belongs to our data."""
    return bool(set(_tokens(question)) & vocabulary())

def matched_tables(question: str, role: str) -> list[str]:
    """Best-guess tables the question is about (table-name words count double)."""
    qt = set(_tokens(question)); insp = inspect(db.engine); scored = []
    for t in db.allowed_tables(role):
        name_tokens = set(_tokens(t.replace("_", " ")))
        col_tokens = {tok for c in insp.get_columns(t) for tok in _tokens(c["name"].replace("_", " "))} - GENERIC
        score = 2 * len(qt & name_tokens) + len(qt & col_tokens)
        score += 2 * sum(1 for w in qt if t in TOPIC_HINTS.get(w, []))          # everyday-word hints
        if score:
            scored.append((score, t))
    scored.sort(reverse=True)
    return [t for s, t in scored[:3] if s >= 2] or [t for s, t in scored[:1]]


# ----------------------------------------------------------------- questions we know will work
def runnable_pool(role: str) -> list[dict]:
    """Example questions this role is allowed to run, with the tables each one uses."""
    allowed = set(db.allowed_tables(role)); pool = []
    for e in EXAMPLES + [WHY_EXAMPLE]:
        tabs = sql_tables(e["sql"])
        if tabs <= allowed:
            pool.append({"question": e["question"], "tables": tabs})
    return pool

def starter_questions(role: str, n: int = 6) -> list[str]:
    """A varied set of good first questions (different tables, plus the 'why' question)."""
    pool, chosen, seen = runnable_pool(role), [], set()
    for p in pool:
        key = frozenset(p["tables"])
        if key not in seen:
            seen.add(key); chosen.append(p["question"])
    why = [p["question"] for p in pool if p["question"].lower().startswith("why")]
    if why and why[0] not in chosen:
        chosen.insert(1, why[0])
    return chosen[:n]

def suggest_for_question(question: str, role: str, n: int = 4) -> list[str]:
    """Closest questions we can really answer (by wording similarity). Falls back to starters."""
    qt = set(_tokens(question)); scored = []
    for p in runnable_pool(role):
        pq = p["question"]
        sim = difflib.SequenceMatcher(None, question.lower(), pq.lower()).ratio()
        if sim > 0.97:
            continue                                         # skip the question they already asked
        overlap = len(qt & set(_tokens(pq))) / max(1, len(set(_tokens(pq))))
        scored.append((0.5 * sim + overlap, pq))
    scored.sort(reverse=True)
    if not scored or scored[0][0] < 0.2:                     # nothing close: offer a general menu
        return starter_questions(role, n)
    return [q for _, q in scored[:n]]

def followups(question: str, sql: str, role: str, n: int = 3) -> list[str]:
    """'What next?' ideas after an answer: other questions touching the same tables."""
    used = sql_tables(sql or ""); scored = []
    for p in runnable_pool(role):
        if difflib.SequenceMatcher(None, question.lower(), p["question"].lower()).ratio() > 0.9:
            continue
        scored.append((len(p["tables"] & used) / max(1, len(p["tables"] | used)), p["question"]))
    scored.sort(key=lambda x: -x[0])
    out = [q for _, q in scored[:n]]
    for q in starter_questions(role):                        # top up with general starters
        if len(out) >= n:
            break
        if q not in out and difflib.SequenceMatcher(None, question.lower(), q.lower()).ratio() <= 0.9:
            out.append(q)
    return out

def clarifying_questions(question: str, role: str) -> list[str]:
    """Questions the copilot asks back, depending on the area it guesses the user means."""
    t = set(matched_tables(question, role))
    if t & {"sales_orders", "order_items", "customers", "products", "regions"}:
        return ["Which measure matters: revenue, margin, or number of orders?",
                "Which period: all time, 2025, or a specific quarter?",
                "Should I break it down by region, product category, or customer?"]
    if "invoices" in t:
        return ["Do you mean all invoices, or only unpaid / overdue ones?", "Should I group them by customer?"]
    if t & {"inventory"}:
        return ["Which warehouse do you mean?", "Only items below their reorder level?"]
    if t & {"suppliers", "purchase_orders"}:
        return ["Which supplier or country?", "Purchases by product, or over time?"]
    return ["Is this about sales, invoices, stock, suppliers, or your documents?",
            "Which keywords should I look for (for example region, product, customer)?"]


# ----------------------------------------------------------------- understanding "talk" messages
_GREET = re.compile(r"^\s*(hi+|hello+|hey+|hola|namaste|good (morning|afternoon|evening))\b[\s!.,]*(there|copilot|buddy)?[\s!.?]*$", re.I)
_THANKS = re.compile(r"^\s*(thanks|thank you|thx|ok|okay|cool|great|nice|got it|bye|goodbye)\b[\s!.,]*$", re.I)
_HELP = re.compile(r"\b(help|what can you do|what do you do|how (do|can|does) (i|this|it)\b|get started|how to use|guide me|capabilities|what should i ask)\b", re.I)
_ABOUT = re.compile(r"(what('?s| is| are)?\s+(the |this |your )?(data|dataset|database|tables?|columns?|fields?)\b|what data\b|"
                    r"\bdata (about|overview|summary|dictionary|catalog)\b|\babout (the |this )?(data|dataset|database)\b|"
                    r"\b(explain|describe|summari[sz]e|overview of|tell me about)\s+(the |this |your )?(data|dataset|database|tables)\b|"
                    r"\bwhat do you (have|know)\b|\bavailable data\b|\bwhat('?s| is) (in|inside)\b|\bshow (me )?(the )?(tables|schema)\b|\bschema\b)", re.I)
_DOCS = re.compile(r"^\s*(what (are|is|do you have)|which|list|show( me)?)( all)?( the| these| your| my)?\s*(uploaded )?"
                   r"(documents?|docs|files)( (do )?you have| available| uploaded| loaded| there)?( about)?\s*[?.!]*\s*$", re.I)
_DOCS2 = re.compile(r"^\s*(what|which)\s+(documents?|docs|files)(\s+(do you have|have you got|are (there|available|loaded|uploaded)))?\s*[?.!]*\s*$", re.I)
_TABLE_CUE = re.compile(r"\b(table|columns?|fields?)\b|^\s*(explain|describe|tell me about)\b", re.I)

def detect_guide_intent(question: str, role: str):
    """Return (kind, argument) if the message is chat / help / 'explain the data', else None."""
    q = question.strip()
    ql = re.sub(r"[?!.]+$", "", q.lower()).strip()
    for term, _, _ in glossary_terms():                      # "what is revenue?" / "define margin"
        if re.fullmatch(rf"(what is|what's|what are|define|explain|meaning of)( the)? {re.escape(term)}|what does {re.escape(term)} mean", ql):
            return ("term", term)
    if _TABLE_CUE.search(q):                                 # "explain the customers table"
        norm = " " + " ".join(_tokens(q)) + " "
        for t in db.allowed_tables(role) + [x for x in TABLE_INFO if x not in db.allowed_tables(role)]:
            if " " + " ".join(_tokens(t.replace("_", " "))) + " " in norm:
                return ("table", t)
    if _DOCS.match(q) or _DOCS2.match(q):
        return ("documents", "")
    if _GREET.match(q):
        return ("greeting", "")
    if _THANKS.match(q):
        return ("thanks", "")
    if _ABOUT.search(q):
        return ("about", "")
    if _HELP.search(q):
        return ("help", "")
    return None


def guide_answer(kind: str, arg: str, role: str) -> dict:
    """Build the reply for a guide intent: {'insight': markdown, 'df': DataFrame, 'suggestions': [..]}."""
    empty = pd.DataFrame()
    starters = starter_questions(role)
    if kind == "thanks":
        return {"insight": "You're welcome! Want to explore something else? Pick a question below, or ask me what data I have.",
                "df": empty, "suggestions": starters[:4]}
    if kind == "greeting":
        return {"insight": ("Hi! I'm your **Enterprise Data Copilot**. Ask me about the company's sales, invoices, stock or suppliers "
                            "in plain English, or ask about the documents. Not sure where to start? Ask **\"What is the data about?\"** "
                            "or pick one of the questions below."),
                "df": empty, "suggestions": ["What is the data about?"] + starters[:4]}
    if kind == "documents":
        df = documents()
        txt = "These documents are loaded. You can ask me anything about them." if len(df) else "No documents are loaded yet. Upload some in the sidebar."
        return {"insight": txt, "df": df, "suggestions": ["What does the pricing policy say about Electronics?"] if len(df) else starters[:3]}
    if kind == "term":
        for term, definition, expr in glossary_terms():
            if term == arg:
                return {"insight": f"**{term.title()}** means: {definition}.\n\nHow I calculate it: `{expr}`",
                        "df": empty, "suggestions": [p["question"] for p in runnable_pool(role)][:3] or starters[:3]}
    if kind == "table":
        if arg not in db.allowed_tables(role):
            return {"insight": f"Your role (**{role}**) is not allowed to see the **{arg}** table. Here is what you can see:",
                    "df": overview(role), "suggestions": starters[:4]}
        cols = ", ".join(f"{c['name']} ({str(c['type']).split('(')[0].lower()})" for c in inspect(db.engine).get_columns(arg))
        sample = pd.read_sql(text(f"SELECT * FROM {arg} LIMIT 5"), db.engine)
        related = [p["question"] for p in runnable_pool(role) if arg in p["tables"]][:3]
        return {"insight": f"**{arg}**: {TABLE_INFO.get(arg, '')}\n\n**Columns:** {cols}\n\nHere are the first 5 rows:",
                "df": sample, "suggestions": related or starters[:3]}
    # about / help: the full tour
    df = overview(role)
    per = data_period()
    period = f" The orders run from **{per[0]}** to **{per[1]}**." if per else ""
    terms = ", ".join(t for t, _, _ in glossary_terms())
    docs = ", ".join(rag.list_documents()) or "none yet"
    text_ = (f"This is a demo company's business data (fake data, no real customers). As **{role}** you can see "
             f"**{len(df)} tables** about sales, invoices, stock and suppliers.{period}\n\n"
             f"- **Business terms I understand:** {terms}. Ask \"What is revenue?\" to see exactly how I calculate one.\n"
             f"- **Documents I can answer from:** {docs}.\n"
             "- **You can ask me to:** show numbers and charts, explain *why* something changed, answer from documents, "
             "or explain any table (try \"Explain the customers table\").\n\n"
             "**What next?** Pick a question below, or tell me which area interests you: sales, invoices, stock or suppliers.")
    return {"insight": text_, "df": df, "suggestions": starters[:5]}