"""Tests for document loading, chunking and uploads."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent))
import pytest
import rag

def test_long_paragraph_is_split():
    text = ("This is a sentence about invoices and payment terms. " * 40)
    chunks = rag._split(text, max_chars=300)
    assert len(chunks) > 3 and all(len(c) <= 300 for c in chunks)

def test_upload_rejects_wrong_type_and_path_tricks(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "DOC_DIR", tmp_path)
    assert "only txt" in rag.save_upload("virus.exe", b"x")
    assert rag.save_upload("../../evil.txt", b"hello world this is a safe note about suppliers") == ""
    assert (tmp_path / "evil.txt").exists()                      # saved INSIDE the folder, path stripped

def test_uploaded_text_and_pdf_are_searchable(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "DOC_DIR", tmp_path)
    rag.save_upload("leave_policy.txt", b"Employees receive twenty four days of paid annual leave every calendar year.")
    rag.reload()
    hits = rag.search("How many days of annual leave do employees get?", k=2)
    assert hits and hits[0]["source"] == "leave_policy.txt"
    canvas = pytest.importorskip("reportlab.pdfgen.canvas")
    pdf = tmp_path / "travel.pdf"
    c = canvas.Canvas(str(pdf)); c.drawString(72, 750, "Travel reimbursement is capped at five thousand rupees per trip for domestic flights.")
    c.save()
    rag.reload()
    assert any(h["source"] == "travel.pdf" for h in rag.search("travel reimbursement cap domestic flights", k=3))
    rag.reload()                                                 # leave no stale index behind
