# Enterprise Data Copilot

Ask business questions in plain English. A team of AI agents (built with LangGraph) writes SQL, checks it for safety, runs it on a **read-only** database connection, reviews its own result, explains *why* numbers changed using pandas and company documents (RAG), and shows a chart.

> Analogy: every company has a data room that only two people know how to use. This is a trained analyst team that anyone can talk to.

---------------------------------------------------------------------

## 1. Quick start (5 minutes, no API key, no MySQL)

```bash
cd enterprise-data-copilot
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install langgraph sqlglot pandas sqlalchemy pymysql python-dotenv streamlit plotly pytest pypdf
cp .env.example .env                # Windows: copy .env.example .env
python seed.py                      # creates fake ERP data in a local SQLite file
streamlit run app.py                # opens http://localhost:8501
```

This uses **mock mode**: no AI is called; the app answers the example questions below from `examples.py`. It proves the pipeline, safety layer, charts and UI work. For real AI, do section 3.

Run the tests: `python -m pytest -q`  (expected: 20 passed)

---------------------------------------------------------------------

## 2. Real setup with MySQL

1. Install MySQL 8 and open a MySQL shell as root. Run:
```sql
CREATE DATABASE enterprise_demo;
CREATE USER 'copilot_admin'@'localhost' IDENTIFIED BY 'admin_pass';   -- seeds data, writes audit log
GRANT ALL PRIVILEGES ON enterprise_demo.* TO 'copilot_admin'@'localhost';
CREATE USER 'copilot_ro'@'localhost' IDENTIFIED BY 'ro_pass';         -- runs the AI's SQL: SELECT only
GRANT SELECT ON enterprise_demo.* TO 'copilot_ro'@'localhost';
FLUSH PRIVILEGES;
```
2. In `.env`, comment out the SQLite `DB_URL` and enable:
```
DB_URL=mysql+pymysql://copilot_admin:admin_pass@localhost:3306/enterprise_demo
READONLY_DB_URL=mysql+pymysql://copilot_ro:ro_pass@localhost:3306/enterprise_demo
```
3. `python seed.py` then `streamlit run app.py`.

Why two users? Even if every other safety layer failed, the database itself refuses INSERT/UPDATE/DELETE from `copilot_ro`.

---------------------------------------------------------------------

## 3. Turn on a real LLM

```bash
pip install langchain-google-genai langchain-groq
```
In `.env` set **one** of:
```
LLM_PROVIDER=gemini
GOOGLE_API_KEY=your_key        # from Google AI Studio
```
or
```
LLM_PROVIDER=groq
GROQ_API_KEY=your_key          # from console.groq.com
```
Model names change over time: if you get a "model not found" error, update `GEMINI_MODEL` / `GROQ_MODEL` in `.env` from the provider's current model list.

Optional real vector search (FAISS): `pip install langchain-community langchain-huggingface sentence-transformers faiss-cpu`, then set `RAG_BACKEND=faiss` in `.env`. The first run downloads a ~90 MB embedding model. If anything is missing, it falls back to keyword search automatically.

---------------------------------------------------------------------

## 4. Example questions and what you should see

| Question | Result |
|---|---|
| What is the total revenue by region? | Bar chart + table, 5 rows, SQL shown |
| Show the top 5 products by revenue | Bar chart of 5 products |
| What is the monthly revenue trend? | Line chart, 18 months (Jan 2025 to Jun 2026) |
| Which products are below their reorder level? | Table of low-stock items |
| Which customers have overdue invoices? | Table with count and amount per customer |
| **Why did South region sales drop in Q3 2025?** | Metric card showing about **-67%** (Q2 to Q3 2025), bar chart of changes per customer with **Apex Retail** (about -176k) and **Bluewave Traders** (about -99k) as top decliners, plus passages from `south_region_notes.txt` and `pricing_policy.txt` |

### Ask about documents (new)

The copilot also answers questions about your **documents** (txt, md, pdf), using only what the documents say, with the file name as the source. If the documents do not contain the answer, it says so instead of guessing.

| Question | What happens |
|---|---|
| What does the pricing policy say about Electronics? | Answered from `pricing_policy.txt` with the passage shown as the source |
| What did the account team say about Bluewave Traders? | Answered from `south_region_notes.txt` |
| What does the policy say about space tourism rockets? | "I could not find that in the documents." |

- **Add your own documents:** in the sidebar, use *Add documents*, choose files, click *Add to knowledge base*. They are searchable immediately.
- **Answer from:** *Auto* decides for you (words like document, policy, notes, contract, PDF send a question to the documents). Choose *Documents* to force a document answer, or *Data (database)* to force SQL. Use this when the wording is ambiguous, for example "Which suppliers had shipping delays?".
- Document questions skip SQL completely: supervisor, then RAG search, then a document-answer agent.

Try the role check: choose role **warehouse**, ask "How many invoices are unpaid?". It is refused because that role cannot access `invoices`.
Try a failure: ask "tell me a joke". After 2 retries the app says it cannot answer reliably instead of guessing.

---------------------------------------------------------------------

## 5. How it works (the flow)

```
Question -> supervisor -> schema agent -> SQL agent -> validator -> executor -> critic -> report -> answer
                                              ^            |           |          |
                                              +---- retry (max 2) with feedback --+
              ("why" questions also pass through: critic -> analysis agent -> RAG agent -> report)
```

| Step | File / function | Uses LLM? | Job |
|---|---|---|---|
| Supervisor | `agents.supervisor` | No | Keyword check: "why/drop/decline..." means deep analysis, else simple lookup |
| Schema agent | `agents.schema_agent` | Yes | Picks relevant tables the role may see; loads the business glossary |
| SQL agent | `agents.sql_agent` | Yes | Writes one SELECT using schema, glossary and few-shot examples |
| Validator | `guardrails.validate_sql` | **No** | Parses SQL; allows only one safe SELECT on allowed tables; adds LIMIT |
| Executor | `db.run_select` | No | Runs on the read-only connection with a timeout |
| Critic | `agents.critic` | Partly | Python checks (empty, NULL, negative revenue) + LLM check; triggers retry with feedback |
| Analysis agent | `agents.analysis_agent` | No | pandas: change between two periods, ranks top contributors |
| RAG agent | `rag.search` | No (embeddings only) | Finds relevant document paragraphs, returns file names as sources |
| Report agent | `agents.report_agent` | Yes | Chooses chart by rules, writes 2-3 bullets, saves audit log |

**Key design principle:** the LLM never touches the database. It only *proposes* SQL; plain Python validates and runs it.

---------------------------------------------------------------------

## 6. File-by-file guide (every file is commented line by line)

| File | What it is | Read it for |
|---|---|---|
| `config.py` | Settings from `.env` | Limits (MAX_ROWS, MAX_RETRIES) and the three feature switches |
| `models.py` | Table definitions | The ERP schema + audit log, glossary, role-access tables |
| `seed.py` | Fake data generator | How the Q3 drop and price rise were planted |
| `db.py` | Database helper | Read-only vs admin connection, schema text for the LLM, audit log |
| `guardrails.py` | SQL security guard | How sqlglot proves a query is safe |
| `llm.py` | LLM wrapper + mock | One `ask()` for Gemini, Groq or offline |
| `examples.py` | Question + correct SQL pairs | Few-shot examples and the evaluation answer key |
| `rag.py` | Document search | Reading txt/md/pdf, chunking, uploads, keyword and FAISS search |
| `agents.py` | The LangGraph team | **The heart of the project** |
| `app.py` | Streamlit UI | Chat, charts, SQL and trace panels |
| `evaluate.py` | Accuracy measurement | Compares 3 versions against the answer key |
| `tests/` | Automated tests | Guardrail and pipeline tests |
| `docs/sample_documents/` | Fake company documents | What the RAG agent searches |

---------------------------------------------------------------------

## 7. Measuring accuracy (your key differentiator)

```bash
python evaluate.py
```
It runs every question in `examples.py` through three versions (basic, + schema selection + glossary, + critic loop) and prints accuracy per difficulty level.

**Important:** in mock mode the score is always 100% because the mock reads the answer key. That number means nothing. To get real numbers:
1. Set `LLM_PROVIDER` to gemini or groq.
2. Add more questions to `EXAMPLES` in `examples.py` (aim for about 40, mixed easy/medium/hard). Keep the few-shot examples in `agents.sql_agent` (`EXAMPLES[3:6]`) out of your test set, or accuracy will look inflated: move them to a separate list.
3. Run `python evaluate.py` and quote only the numbers you actually measured, including the questions it still gets wrong.

---------------------------------------------------------------------

## 8. How to extend it

- **New question type:** add a dict to `EXAMPLES` with question, correct SQL, level.
- **New business term:** add a row to `business_glossary` in `seed.py` (for example "gross profit").
- **New role:** add its tables to `role_map` in `seed.py`, re-run `python seed.py`.
- **Different industry:** change the data in `seed.py` and the documents in `docs/sample_documents/`.
- **Smarter routing:** replace the keyword check in `agents.supervisor` with an LLM classifier.
- **Real client database:** point `DB_URL` / `READONLY_DB_URL` at a reporting replica (never the live production database), update the glossary, and test with that client's real questions.

---------------------------------------------------------------------

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| `ModuleNotFoundError` | Activate the venv and re-run the pip install line |
| Everything says "could not produce a reliable answer" | You are in mock mode and asked a question not in `examples.py`. Use a listed question or enable a real LLM |
| `Access denied` MySQL error | Check user/password in `.env` and that the GRANT commands ran |
| Model not found / 404 from LLM | Update the model name in `.env` |
| Empty charts with a real LLM | Open "SQL that was run" and "What the agents did" to see what it wrote and why the critic complained |
| FAISS errors | Set `RAG_BACKEND=keyword` |

---------------------------------------------------------------------

## 10. Known limits (say these honestly)

- Tested in a sandbox with **SQLite + mock LLM** (12 automated tests pass; the Streamlit UI loads and renders). **Not yet tested here:** live Gemini/Groq calls, MySQL, and FAISS. Expect to tune prompts once you run a real model.
- The `overdue invoice` glossary rule uses a fixed date (2026-06-30) because the fake data ends then. A real system would use the current date.
- The SQLite read-only connection is not truly read-only (MySQL's `copilot_ro` user is). The validator still blocks all writes in both cases.
- The "why" analysis compares two periods and ranks contributors. It finds *who* changed, and documents suggest *why*, but it does not prove causation. Say that.
- LLM-written insights can still be wrong. That is why every answer shows its SQL.
#   e n t e r p r i s e - d a t a - c o p i l o t  
 