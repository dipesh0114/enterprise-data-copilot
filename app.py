"""app.py - the Streamlit chat UI. Run:  streamlit run app.py"""
import streamlit as st                                      # the web UI framework
import plotly.express as px                                 # charts
import db, agents, config, rag

st.set_page_config(page_title="Enterprise Data Copilot", layout="wide")   # browser tab title + wide layout
st.title("Enterprise Data Copilot")
st.caption("Ask about your business data or your documents in plain English. AI agents find the answer, check their work, and show their sources.")

# ---------------- sidebar: role, documents, example questions ----------------
with st.sidebar:
    st.header("Settings")
    role = st.selectbox("Your role", db.list_roles() or ["admin"], key="role",
                        help="Each role can only query certain tables.")
    st.write(f"**LLM:** {config.LLM_PROVIDER}  |  **DB:** {db.DIALECT}")
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
    examples = ["What is the total revenue by region?", "Show the top 5 products by revenue",
                "What is the monthly revenue trend?", "Why did South region sales drop in Q3 2025?",
                "Which customers have overdue invoices?", "Which products are below their reorder level?",
                "What does the pricing policy say about Electronics?",
                "What did the account team say about Bluewave Traders?"]
    clicked = None
    for i, q in enumerate(examples):
        if st.button(q, key=f"example_{i}", width="stretch"):    # one button per example question
            clicked = q
    show_log = st.checkbox("Show audit log", key="show_log")

if "history" not in st.session_state:                       # chat memory that survives reruns
    st.session_state.history = []

typed = st.chat_input("Ask about your data or your documents...")
question = clicked or typed                                 # a button click or typed text
if question:
    with st.spinner("Agents are working..."):
        state = agents.answer(question, role, source)       # run the whole LangGraph team
    st.session_state.history.append((question, role, state))

# ---------------- render the conversation ----------------
# Every element gets a key that includes the message number n. Without unique keys Streamlit raises
# StreamlitDuplicateElementId when the same question (so the same chart) is asked twice.
for n, (q, r, s) in enumerate(st.session_state.history):
    with st.chat_message("user"):
        st.write(q)
    with st.chat_message("assistant"):
        st.markdown(s["insight"])                           # written insight
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
            st.dataframe(df, width="stretch", key=f"table_{n}")
        for j, c in enumerate(s.get("context", [])):        # document passages used as evidence
            st.info(f"Source: {c['source']}\n\n{c['text']}")
        if s.get("sql"):                                    # document answers have no SQL
            with st.expander("SQL that was run (verify the numbers)"):
                st.code(s["sql"], language="sql")
        with st.expander("What the agents did"):
            st.write("\n".join(f"{i + 1}. {t}" for i, t in enumerate(s["trace"])))

if show_log:
    st.subheader("Audit log (latest 20)")
    st.dataframe(db.recent_audit(), width="stretch", key="audit_table")
