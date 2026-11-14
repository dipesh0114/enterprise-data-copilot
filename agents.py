"""agents.py - the multi-agent team, wired together with LangGraph.

Flow:
 supervisor -> schema -> sql -> validate -> execute -> critic -> [analysis -> rag] -> report
 Document questions: supervisor -> rag -> doc_answer (no SQL)
 Chat / "explain the data": supervisor -> guide (no SQL, no LLM)
 Unclear or failed questions: ... -> clarify (guesses what you meant, suggests questions, asks what next)
                          ^________________ retry (max 2) ______________|
Each function below is one "agent" (a node). It receives the shared State dict and returns
only the keys it changes.
"""
import re
from typing import TypedDict, Any
import pandas as pd
try:
    from langgraph.graph import StateGraph, START, END      # the real LangGraph
    GRAPH_ENGINE = "langgraph"
except Exception:                                           # e.g. Windows Application Control blocks ormsgpack's DLL
    from minigraph import StateGraph, START, END            # tiny built-in runner with the same interface
    GRAPH_ENGINE = "built-in fallback (LangGraph unavailable)"
import config, db, llm, rag, catalog
from guardrails import validate_sql
from examples import EXAMPLES, WHY_EXAMPLE


class State(TypedDict, total=False):                        # the shared notebook every agent reads/writes
    question: str; role: str; source: str; mode: str        # input + user's source choice + route (lookup/why/docs/guide/clarify)
    history: list; suggestions: list                        # earlier turns (for follow-ups) + 'what next?' questions
    tables: list; schema_text: str; glossary: str           # what the SQL agent is allowed to see
    sql: str; feedback: str; error: str; retries: int       # SQL + problems + retry counter
    df: Any; analysis: dict; context: list                  # data, why-analysis, document passages
    chart: dict; insight: str; warning: str; trace: list    # final outputs
    guide: tuple                                            # (kind, argument) when mode == 'guide'


def _log(state, msg):
    """Append a line to the visible 'what the agents did' trace."""
    return {"trace": state.get("trace", []) + [msg]}


# ---------------------------------------------------------------- 1. Supervisor
WHY_WORDS = ("why", "reason", "cause", "drop", "decline", "decrease", "fell", "spike")    # deep-analysis triggers
DOC_WORDS = {"document", "documents", "doc", "docs", "pdf", "file", "files", "uploaded", "policy",
             "policies", "notes", "note", "contract", "handbook", "memo", "report", "according"}   # "about the documents"
DATA_WORDS = {"revenue", "sales", "customer", "customers", "order", "orders", "invoice", "invoices",
              "stock", "inventory", "product", "products", "supplier", "suppliers", "region", "regions",
              "margin", "total", "average", "count", "top", "trend", "monthly", "sold", "profit", "purchase"}

FOLLOWUP = re.compile(r"^\s*(and|also|what about|how about|now|same|only|just|but|for|in|by|per)\b", re.I)   # "and for North?"

def supervisor(state: State):
    """Pick the path: guide (chat/explain), docs, why, lookup, or clarify (we don't understand yet)."""
    q = state["question"].lower()
    words = set(re.findall(r"[a-z]+", q))                             # the individual words
    is_why = any(w in q for w in WHY_WORDS)
    source = state.get("source", "auto")                              # the user's choice in the sidebar
    guide = catalog.detect_guide_intent(state["question"], state["role"])   # "hi", "what is the data about?", "explain revenue"
    follow = bool(state.get("history")) and len(q.split()) <= 10 and bool(FOLLOWUP.match(q))
    extra = {}
    if guide:                                                         # talking to the copilot: no SQL needed
        mode = "guide"; extra = {"guide": guide}
    elif source == "documents":                                       # user forced documents
        mode = "docs"
    elif source == "data":                                            # user forced the database
        mode = "why" if is_why else "lookup"
    elif words & DOC_WORDS and not is_why:                            # e.g. "What does the pricing policy say?"
        mode = "docs"
    elif follow:                                                      # "and for North?" continues the last question
        mode = "why" if is_why else "lookup"
    elif words & DATA_WORDS or is_why or catalog.mentions_data(state["question"]):   # questions about our numbers
        mode = "why" if is_why else "lookup"
    elif rag.search(state["question"], k=1):                          # no clue words: do the documents know?
        mode = "docs"
    else:                                                             # we do not understand yet: guess and ask
        mode = "clarify"
    return {"mode": mode, "retries": 0, "feedback": "", "error": "", "warning": "", "suggestions": [],
            **extra, **_log(state, f"Supervisor: classified question as '{mode}'.")}


# ---------------------------------------------------------------- 2. Schema agent
def schema_agent(state: State):
    """Pick only the tables needed (less noise => fewer SQL mistakes), and load the glossary."""
    allowed = db.allowed_tables(state["role"])                        # role-based access
    chosen = allowed
    if config.USE_SCHEMA_SELECTION and len(allowed) > 3:
        prompt = (f"### TASK: tables\nTables available: {', '.join(allowed)}\n"
                  f"QUESTION: {state['question']}\n"
                  "Reply with ONLY the comma-separated table names needed to answer, or ALL.")
        reply = llm.ask(prompt)
        picked = [t.strip() for t in reply.split(",") if t.strip() in allowed]   # keep only real, allowed names
        if picked:                                                    # 'ALL' or nonsense => keep everything
            chosen = picked
    glossary = db.glossary_text() if config.USE_GLOSSARY else ""
    return {"tables": chosen, "schema_text": db.schema_text(chosen), "glossary": glossary,
            **_log(state, f"Schema agent: using tables {chosen}.")}


# ---------------------------------------------------------------- 3. SQL agent
def sql_agent(state: State):
    """Ask the LLM to write ONE SELECT query."""
    shots = "\n".join(f"Q: {e['question']}\nSQL: {e['sql']}" for e in EXAMPLES[3:6])   # few-shot examples
    why_rule = ""
    if state["mode"] == "why":                                        # 'why' questions need a comparable shape
        why_rule = ("This is a WHY question. Compare two periods. Return EXACTLY three columns named "
                    "entity, period, value (entity = customer/product/region, period = label, value = number).\n"
                    f"Example:\nQ: {WHY_EXAMPLE['question']}\nSQL: {WHY_EXAMPLE['sql']}\n")
    convo = ""
    if state.get("history"):                                          # lets the user say "and for North?"
        convo = "RECENT CHAT (the new question may refer to it, e.g. 'and for North?'):\n" + "\n".join(
            f"Q: {h['question']}\nSQL: {h['sql']}" for h in state["history"][-2:]) + "\n\n"
    fix = f"\nYour previous SQL was rejected. Problem: {state['feedback']}\nPrevious SQL: {state.get('sql','')}\n" if state.get("feedback") else ""
    prompt = (f"### TASK: sql\nYou write {db.DIALECT.upper()} SQL. Output ONLY one SELECT statement, no explanation.\n"
              f"SCHEMA:\n{state['schema_text']}\n\nBUSINESS GLOSSARY (always use these formulas):\n{state['glossary']}\n\n"
              f"EXAMPLES:\n{shots}\n\n{convo}{why_rule}{fix}\nQUESTION: {state['question']}")
    sql = llm.clean_sql(llm.ask(prompt))
    return {"sql": sql, **_log(state, f"SQL agent: wrote query (attempt {state['retries'] + 1}).")}


# ---------------------------------------------------------------- 4. Validator (no LLM)
def validator(state: State):
    ok, result = validate_sql(state["sql"], state["tables"], db.DIALECT)
    if ok:
        return {"sql": result, "error": "", **_log(state, "Validator: SQL is a safe single SELECT.")}
    return {"error": result, "feedback": f"Validator rejected it: {result}", **_log(state, f"Validator: REJECTED - {result}")}


# ---------------------------------------------------------------- 5. Executor
def executor(state: State):
    try:
        df = db.run_select(state["sql"])                              # runs on the READ-ONLY connection
        return {"df": df, "error": "", **_log(state, f"Executor: got {len(df)} rows.")}
    except Exception as e:
        msg = str(e).split("\n")[0][:300]                             # first line of the DB error is enough
        return {"error": msg, "feedback": f"Database error: {msg}", **_log(state, f"Executor: ERROR - {msg}")}


# ---------------------------------------------------------------- 6. Critic
def critic(state: State):
    """Check the result makes sense. Cheap Python checks first, then an LLM check."""
    df: pd.DataFrame = state["df"]
    problem = ""
    if not config.USE_CRITIC:
        return _log(state, "Critic: disabled (evaluation mode).")
    if df.empty:
        problem = "Query returned no rows. Re-check filters, dates, joins and status values."
    else:
        num = df.select_dtypes("number")
        if not num.empty and num.isna().all().all():
            problem = "All numeric values are NULL. A join or filter is probably wrong."
        elif any(("revenue" in c.lower() or "amount" in c.lower()) and (df[c] < 0).any() for c in num.columns):
            problem = "Negative revenue/amount found, which is suspicious."
    if not problem:                                                   # LLM sanity check on meaning
        verdict = llm.ask(f"### TASK: critic\nQUESTION: {state['question']}\nSQL: {state['sql']}\n"
                          f"First rows:\n{df.head(5).to_string()}\n"
                          "Does this SQL answer the question? Reply OK or one sentence on what is wrong.")
        if not verdict.upper().startswith("OK"):
            problem = verdict
    if problem:
        return {"feedback": f"Critic says: {problem}", "error": problem, **_log(state, f"Critic: PROBLEM - {problem}")}
    return {"feedback": "", "error": "", **_log(state, "Critic: result looks right.")}


def retry(state: State):
    """Count the retry, then loop back to the SQL agent (which will read state['feedback'])."""
    return {"retries": state["retries"] + 1, **_log(state, f"Retry #{state['retries'] + 1}: sending feedback to SQL agent.")}


# ---------------------------------------------------------------- 7. Analysis agent
def analysis_agent(state: State):
    """For 'why' questions: find who/what contributed most to the change between two periods."""
    df: pd.DataFrame = state["df"]
    if not {"entity", "period", "value"} <= set(df.columns):          # wrong shape => skip politely
        return {"analysis": {"note": "Result is not in entity/period/value form."}, **_log(state, "Analysis: skipped (wrong shape).")}
    pivot = df.pivot_table(index="entity", columns="period", values="value", aggfunc="sum").fillna(0)
    periods = sorted(pivot.columns)                                   # e.g. ['Q2 2025', 'Q3 2025']
    if len(periods) < 2:
        return {"analysis": {"note": "Only one period returned."}, **_log(state, "Analysis: skipped (one period).")}
    before, after = periods[0], periods[-1]
    pivot["change"] = pivot[after] - pivot[before]                    # how much each entity moved
    total_before, total_after = pivot[before].sum(), pivot[after].sum()
    pct = (total_after - total_before) / total_before * 100 if total_before else 0
    ranked = pivot.sort_values("change")                              # biggest decliners first
    analysis = {"before": before, "after": after,
                "total_before": round(float(total_before), 2), "total_after": round(float(total_after), 2),
                "pct_change": round(float(pct), 1),
                "top_decliners": ranked.head(3)["change"].round(2).to_dict(),
                "top_growers": ranked[ranked["change"] > 0].tail(3)["change"].round(2).to_dict()}   # only real growth
    chart_df = ranked.reset_index()[["entity", "change"]].head(8)     # decliners are what we want to show
    return {"analysis": analysis, "df": df, "chart": {"type": "bar", "x": "entity", "y": "change", "data": chart_df},
            **_log(state, f"Analysis: total {pct:+.1f}% from {before} to {after}.")}


# ---------------------------------------------------------------- 8. RAG agent
def rag_agent(state: State):
    k = 4 if state["mode"] == "docs" else 2                            # docs questions need more evidence
    hits = rag.search(state["question"], k=k)
    return {"context": hits, **_log(state, f"RAG: found {len(hits)} relevant passage(s).")}


# ---------------------------------------------------------------- 9. Report agent
def choose_chart(df: pd.DataFrame) -> dict:
    """Rule-based chart choice (deterministic = reliable and easy to defend)."""
    if df.empty or len(df) == 1:
        return {"type": "table"}
    nums = list(df.select_dtypes("number").columns)
    cats = [c for c in df.columns if c not in nums]
    if cats and nums:
        x, y = cats[0], nums[0]
        if re.search(r"month|date|year|quarter|period", x, re.I):
            return {"type": "line", "x": x, "y": y}                   # time on x-axis => trend line
        if len(df) <= 25:
            return {"type": "bar", "x": x, "y": y}                    # categories => bar chart
    return {"type": "table"}


def report_agent(state: State):
    """Write the final answer and save the audit record."""
    failed = bool(state.get("error"))
    if failed:                                                        # retries ran out: be honest, don't guess
        insight = (f"I could not produce a reliable answer after {state['retries']} retries. "
                   f"Last problem: {state['error']}. Try rephrasing the question.")
        db.write_audit(state["role"], state["question"], state.get("sql", ""), "failed", 0, state["retries"])
        return {"insight": insight, "chart": {"type": "table"}, "df": pd.DataFrame(), **_log(state, "Report: failed answer.")}
    df: pd.DataFrame = state["df"]
    chart = state.get("chart") or choose_chart(df)
    prompt = (f"### TASK: insight\nQUESTION: {state['question']}\nSQL used: {state['sql']}\n"
              f"DATA (first 20 rows):\n{df.head(20).to_string()}\n"
              f"ANALYSIS: {state.get('analysis', {})}\n"
              f"COMPANY DOCUMENTS: {[(c['source'], c['text']) for c in state.get('context', [])]}\n"
              "Write 2-3 short bullet points for a business manager. Use ONLY numbers present above. "
              "If documents give a likely reason, mention it and name the file. Say so if the data is not enough.")
    insight = llm.ask(prompt)
    db.write_audit(state["role"], state["question"], state["sql"], "ok", len(df), state["retries"])
    return {"insight": insight, "chart": chart, "suggestions": catalog.followups(state["question"], state["sql"], state["role"]),
            **_log(state, "Report: chart + insight ready.")}


# ---------------------------------------------------------------- 10. Document answer agent
NOT_FOUND_MSG = ("I could not find that in the documents. Try different words, "
                 "or upload the document that contains the answer.")

def doc_answer(state: State):
    """Answer a question using ONLY the retrieved passages, citing file names. Admit when not found."""
    hits = state.get("context", [])
    status, insight = "not_found", NOT_FOUND_MSG
    if hits:
        passages = "\n".join(f"[{i + 1}] (source: {h['source']}) {h['text']}" for i, h in enumerate(hits))
        prompt = (f"### TASK: docs\nQUESTION: {state['question']}\nPASSAGES:\n{passages}\n"
                  "Answer the question using ONLY the passages above, in 2-4 short sentences or bullets. "
                  "Name the source file for each fact. Do not use outside knowledge. "
                  "If the passages do not contain the answer, reply with exactly: NOT_FOUND")
        reply = llm.ask(prompt)
        if not reply.upper().startswith("NOT_FOUND"):
            status, insight = "ok", reply
    db.write_audit(state["role"], state["question"], "", status, len(hits), 0)
    return {"insight": insight, "sql": "", "df": pd.DataFrame(), "chart": {"type": "table"},
            "context": hits if status == "ok" else [], "error": "",
            "suggestions": catalog.suggest_for_question(state["question"], state["role"], 3) + ["What is the data about?"],
            **_log(state, f"Document answer: {status}.")}


# ---------------------------------------------------------------- 11. Guide agent (chat, no SQL)
def guide_agent(state: State):
    """Answer 'hi', 'what is the data about?', 'explain the customers table', 'what is revenue?' from the catalog."""
    kind, arg = state["guide"]
    out = catalog.guide_answer(kind, arg, state["role"])
    db.write_audit(state["role"], state["question"], "", "guide", len(out["df"]), 0)
    return {"insight": out["insight"], "df": out["df"], "sql": "", "chart": {"type": "table"}, "context": [],
            "suggestions": out["suggestions"], "error": "", **_log(state, f"Guide: explained '{kind}' from the data catalog.")}


# ---------------------------------------------------------------- 12. Clarify agent (guess, suggest, ask)
def _restricted_tables(err: str) -> list[str]:
    """Tables named in a 'Role may not access' error that REALLY exist (so it is a permission issue, not a made-up table)."""
    if "may not access" not in err:
        return []
    named = [t.strip().lower() for t in err.split(":", 1)[-1].rstrip(".").split(",")]
    real = {t.lower() for t in db.inspect(db.engine).get_table_names()} - db.INTERNAL_TABLES
    return [t for t in named if t in real]


def clarify_agent(state: State):
    """Instead of failing: say what we understood, offer questions that WILL work, and ask what the user means."""
    q, role, err = state["question"], state["role"], state.get("error", "")
    restricted = _restricted_tables(err)
    if restricted:                                                    # a real table this role may not see
        insight = (f"Your role (**{role}**) is not allowed to see: {', '.join(restricted)}. "
                   "That data is restricted, so I can't answer this one. Here is what I can help with:")
        suggestions = catalog.starter_questions(role, 4)
    else:
        matched = catalog.matched_tables(q, role)
        asks = catalog.clarifying_questions(q, role)
        suggestions = catalog.suggest_for_question(q, role, 4)
        guess = (f"It sounds like you are asking about **{', '.join(matched)}**, but I couldn't turn it into a reliable query yet."
                 if matched else "I'm not sure what you mean yet, because that doesn't match anything in the company data.")
        insight = (f"{guess}\n\n**To help me get it right, tell me:**\n" + "\n".join(f"- {x}" for x in asks) +
                   "\n\nOr click one of these questions I can answer right now.")
        if config.LLM_PROVIDER != "mock":                             # a real LLM can word the guess more naturally
            hint = llm.ask(f"### TASK: clarify\nQUESTION: {q}\nTABLES I GUESS: {matched}\n"
                           f"IN THE DATA:\n{catalog.overview(role).to_string(index=False)}\n"
                           "In 2-3 friendly sentences: say what the user probably wants (best guess) and ask ONE clarifying question. "
                           "Only mention tables listed above. Do not invent numbers.")
            if hint:
                insight = (hint + "\n\n**You can also tell me:**\n" + "\n".join(f"- {x}" for x in asks)
                           + "\n\nOr click a question below.")
    db.write_audit(role, q, "", "clarify", 0, state.get("retries", 0))
    return {"insight": insight, "df": pd.DataFrame(), "sql": "", "chart": {"type": "table"}, "context": [],
            "suggestions": suggestions + ["What is the data about?"], **_log(state, "Clarify: guessed intent, offered suggestions.")}


# ---------------------------------------------------------------- Routing rules (the "edges")
def _retry_or_report(state, ok_next):
    if state.get("error"):                                            # something went wrong
        return "retry" if state["retries"] < config.MAX_RETRIES else "clarify"
    return ok_next

def after_validator(state): return _retry_or_report(state, "executor")
def after_executor(state):  return _retry_or_report(state, "critic")

def after_supervisor(state): return {"docs": "rag", "guide": "guide", "clarify": "clarify"}.get(state["mode"], "schema")
def after_rag(state):        return "doc_answer" if state["mode"] == "docs" else "report"

def after_critic(state):
    if state.get("error"):
        return "retry" if state["retries"] < config.MAX_RETRIES else "clarify"
    return "analysis" if state["mode"] == "why" else "report"


def build_graph():
    g = StateGraph(State)                                             # create an empty graph that carries State
    for name, fn in [("supervisor", supervisor), ("schema", schema_agent), ("sql", sql_agent),
                     ("validator", validator), ("executor", executor), ("critic", critic),
                     ("retry", retry), ("analysis", analysis_agent), ("rag", rag_agent), ("report", report_agent),
                     ("doc_answer", doc_answer), ("guide", guide_agent), ("clarify", clarify_agent)]:
        g.add_node(name, fn)                                          # register each agent
    g.add_edge(START, "supervisor")                                   # straight-line edges
    g.add_conditional_edges("supervisor", after_supervisor, {"rag": "rag", "schema": "schema", "guide": "guide", "clarify": "clarify"})   # docs path skips SQL
    g.add_edge("schema", "sql")
    g.add_edge("sql", "validator")
    g.add_conditional_edges("validator", after_validator, {"executor": "executor", "retry": "retry", "clarify": "clarify"})
    g.add_conditional_edges("executor", after_executor, {"critic": "critic", "retry": "retry", "clarify": "clarify"})
    g.add_conditional_edges("critic", after_critic, {"analysis": "analysis", "retry": "retry", "clarify": "clarify", "report": "report"})
    g.add_edge("retry", "sql")                                        # the repair loop
    g.add_edge("analysis", "rag")
    g.add_conditional_edges("rag", after_rag, {"doc_answer": "doc_answer", "report": "report"})
    g.add_edge("doc_answer", END)
    g.add_edge("guide", END)
    g.add_edge("clarify", END)
    g.add_edge("report", END)
    return g.compile()


_graph = None

def answer(question: str, role: str = "admin", source: str = "auto", history: list | None = None) -> State:
    """Public entry point. source = "auto", "data" or "documents". history = earlier [{question, sql}] for follow-ups."""
    global _graph
    if _graph is None:
        _graph = build_graph()
    return _graph.invoke({"question": question, "role": role, "source": source, "history": history or []})