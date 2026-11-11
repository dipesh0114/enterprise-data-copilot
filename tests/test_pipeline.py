"""End-to-end tests using the mock LLM and the seeded SQLite database."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import config
config.LLM_PROVIDER = "mock"                                    # force offline mode for tests
import agents

def test_simple_lookup():
    s = agents.answer("How many customers do we have?", "admin")
    assert int(s["df"].iloc[0, 0]) == 20 and not s["error"]

def test_why_question_finds_apex_as_top_decliner():
    s = agents.answer("Why did South region sales drop in Q3 2025?", "admin")
    assert s["analysis"]["pct_change"] < -30
    assert "Apex Retail Pvt Ltd" in s["analysis"]["top_decliners"]
    assert s["context"], "RAG should return at least one document passage"

def test_role_blocks_forbidden_table():
    s = agents.answer("How many invoices are unpaid?", "warehouse")   # warehouse role has no invoices access
    assert s["error"] and "invoices" in s["error"]

def test_unknown_question_fails_gracefully():
    s = agents.answer("tell me a joke", "admin")
    assert s["error"] and "could not produce" in s["insight"]


# ---------------- document Q&A mode ----------------
def test_document_question_is_answered_from_documents():
    s = agents.answer("What does the pricing policy say about Electronics?", "admin")
    assert s["mode"] == "docs" and not s["sql"]
    assert any(c["source"] == "pricing_policy.txt" for c in s["context"])
    assert "pricing_policy.txt" in s["insight"]

def test_document_question_without_keywords_uses_documents():
    s = agents.answer("What did the account team say about Bluewave Traders?", "admin")
    assert s["mode"] == "docs" and s["context"]

def test_question_not_in_documents_admits_it():
    s = agents.answer("What does the policy say about space tourism rockets?", "admin")
    assert s["mode"] == "docs" and "could not find" in s["insight"] and not s["context"]

def test_user_can_force_documents_even_with_data_words():
    s = agents.answer("Which suppliers had shipping delays?", "admin", source="documents")
    assert s["mode"] == "docs" and any(c["source"] == "supplier_delays.txt" for c in s["context"])

def test_user_can_force_data_mode():
    s = agents.answer("How many customers do we have?", "admin", source="data")
    assert s["mode"] == "lookup" and not s["error"]
