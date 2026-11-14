"""app.py - the Streamlit chat UI. Run:  streamlit run app.py"""
import streamlit as st                                      # the web UI framework
import plotly.express as px                                 # charts
import db, agents, config, rag, catalog

st.set_page_config(page_title="Enterprise Data Copilot", layout="wide")   # browser tab title + wide layout


@st.cache_resource                                          # runs once per server start
def _boot():
    db.ensure_seeded()                                      # demo SQLite database: build it if it is missing/empty
    return True
_boot()


@st.cache_data(ttl=300, show_spinner=False)                 # table list + row counts, refreshed every 5 minutes
def _overview(role):
    return catalog.overview(role)


def _ask(text):
    """Button callback: remember which question to run on the next rerun."""
    st.session_state["pending"] = text


st.title("Enterprise Data Copilot")
st.caption("Talk to your company's data and documents in plain English. Ask anything: I'll explain, guess what you mean, and suggest what to ask next.")

if "history" not in st.session_state:                       # chat memory that survives reruns
    st.session_state.history = []

# ---------------- sidebar: role, source, documents, example questions ----------------
with st.sidebar:
    st.header("Settings")
    role = st.selectbox("Your role", db.list_roles() or ["admin"], key="role",
                        help="Each role can only see certain tables.")
    st.write(f"**LLM:** {config.LLM_PROVIDER}  |  **DB:** {db.DIALECT}  |  **Graph:** {agents.GRAPH_ENGINE.split(' ')[0]}")
    if agents.GRAPH_ENGINE != "langgraph":                  # be honest when the real LangGraph could not load
        st.warning("LangGraph could not load on this computer (often a Windows security policy), so a built-in "
                   "runner with the same flow is being used.")
    source_label = st.radio("Answer from", ["Auto", "Data (database)", "Documents"], key="source",
                            help="Auto picks for you. Choose Documents to force an answer from your documents.")
    source = {"Auto": "auto", "Data (database)": "data", "Documents": "documents"}[source_label]

    st.header("Documents")
    st.caption("Ask questions about: " + (", ".join(rag.list_documents()) or "no documents yet"))
    uploads = st.file_uploader("Add documents (txt, md, pdf)", type=["txt", "md", "pdf"],
                               accept_multiple_files=True, key="uploader")
    if uploads and st.button("Add to knowledge base", key="add_docs"):
        problems = [msg for f in uploads if (msg := rag.save_upload(f.name, f.getvalue()))]   # '' means saved
        rag.reload()                                        # rebuild the search index with the new files
        if problems:
            st.warning("\n".join(problems))
        else:
            st.success(f"Added {len(uploads)} document(s).")
            st.rerun()

    st.header("Try these")
    examples = ["What is the data about?", "What is the total revenue by region?", "Show the top 5 products by revenue",
                "What is the monthly revenue trend?", "Why did South region sales drop in Q3 2025?",
                "Which customers have overdue invoices?", "Explain the customers table",
                "What does the pricing policy say about Electronics?"]
    clicked = None
    for i, q in enumerate(examples):
        if st.button(q, key=f"example_{i}", width="stretch"):    # one button per example question
            clicked = q
    show_log = st.checkbox("Show audit log", key="show_log")

# ---------------- "what data is in here?" panel (always available) ----------------
with st.expander("What data is in here?", expanded=not st.session_state.history):
    per = catalog.data_period()
    st.write("A demo company's business data (fake, no real customers)."
             + (f" Orders run from **{per[0]}** to **{per[1]}**." if per else ""))
    st.dataframe(_overview(role), width="stretch", key="catalog_table", hide_index=True)
    st.caption("Business terms I know: " + ", ".join(t for t, _, _ in catalog.glossary_terms())
               + ".  Documents: " + (", ".join(rag.list_documents()) or "none yet") + ".")

if not st.session_state.history:
    st.info("**Hi! You can just talk to me.** Try: *What is the data about?*, *Explain the invoices table*, "
            "*What is revenue?*, or a question like *Which customers have overdue invoices?* "
            "If I don't understand, I'll guess what you mean and suggest what to ask next.")

# ---------------- run a question: a button click, a suggestion button, or typed text ----------------
pending = st.session_state.pop("pending", None)
typed = st.chat_input("Ask anything: explain the data, a number question, or a document question...")
question = clicked or pending or typed
if question:
    recent = [{"question": q, "sql": s.get("sql", "")} for q, _, s in st.session_state.history if s.get("sql")][-3:]
    with st.spinner("Agents are working..."):
        state = agents.answer(question, role, source, recent)   # run the whole LangGraph team (recent = follow-up memory)
    st.session_state.history.append((question, role, state))

# ---------------- render the conversation ----------------
# Every element gets a key that includes the message number n. Without unique keys Streamlit raises
# StreamlitDuplicateElementId when the same question (so the same chart) is asked twice.
last = len(st.session_state.history) - 1
for n, (q, r, s) in enumerate(st.session_state.history):
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        st.markdown(s["insight"])                           # written answer
        chart, df = s.get("chart", {"type": "table"}), s["df"]
        if s.get("analysis") and "pct_change" in s["analysis"]:
            a = s["analysis"]
            st.metric(f"Change {a['before']} -> {a['after']}", f"{a['total_after']:,.0f}", f"{a['pct_change']}%")
        if chart["type"] == "bar":
            data = chart.get("data", df)
            st.plotly_chart(px.bar(data, x=chart["x"], y=chart["y"]), width="stretch", key=f"chart_{n}")
        elif chart["type"] == "line":
            st.plotly_chart(px.line(df, x=chart["x"], y=chart["y"], markers=True), width="stretch", key=f"chart_{n}")
        if not df.empty:
            st.dataframe(df, width="stretch", key=f"table_{n}", hide_index=True)
        for c in s.get("context", []):                      # document passages used as evidence
            st.info(f"Source: {c['source']}\n\n{c['text']}")
        if s.get("sql"):                                    # chat, document and clarify answers have no SQL
            with st.expander("SQL that was run (verify the numbers)"):
                st.code(s["sql"], language="sql")
        with st.expander("What the agents did"):
            st.write("\n".join(f"{i + 1}. {t}" for i, t in enumerate(s["trace"])))
        if n == last and s.get("suggestions"):              # "what next?" buttons under the latest answer only
            st.caption("What next? Click a question, or type your own below.")
            for j, text_ in enumerate(dict.fromkeys(s["suggestions"])):
                st.button(text_, key=f"sugg_{n}_{j}", on_click=_ask, args=(text_,))

if show_log:
    st.subheader("Audit log (latest 20)")
    st.dataframe(db.recent_audit(), width="stretch", key="audit_table")