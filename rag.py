"""rag.py - finds relevant passages in company documents (txt, md, pdf).

Analogy: a librarian who has read every internal document and hands you the right paragraph
(plus the file name, so you can check the source).
Used in two places: (1) the "why" path adds document evidence, (2) the "docs" path answers
questions about the documents themselves.
"""
import re
from pathlib import Path
import config

DOC_DIR = Path(__file__).parent / "docs" / "sample_documents"     # folder holding the documents
SUPPORTED = {".txt", ".md", ".pdf"}                               # file types we can read
MAX_UPLOAD_BYTES = 10 * 1024 * 1024                               # refuse uploads over 10 MB
STOPWORDS = {"the", "and", "for", "what", "does", "did", "say", "says", "about", "are", "was", "were",
             "how", "who", "when", "where", "which", "with", "that", "this", "from", "have", "has",
             "any", "all", "can", "you", "your", "our", "tell", "give", "show", "list", "there", "their"}
_index = None                                                     # built once, rebuilt after uploads


def _read_file(f: Path) -> str:
    """Return the text of one document."""
    if f.suffix.lower() == ".pdf":
        from pypdf import PdfReader                               # lazy import: only needed for PDFs
        return "\n\n".join((p.extract_text() or "") for p in PdfReader(str(f)).pages)
    return f.read_text(encoding="utf-8", errors="ignore")


def _split(text: str, max_chars: int = 800) -> list[str]:
    """Split text into paragraph-sized chunks; cut very long paragraphs at a sentence end."""
    chunks = []
    for para in re.split(r"\n\s*\n", text):                       # blank line = new paragraph
        para = " ".join(para.split())                             # collapse whitespace/newlines
        while len(para) > max_chars:                              # too long: cut near a sentence end
            cut = para.rfind(". ", 0, max_chars)
            cut = cut + 1 if cut > 200 else max_chars
            chunks.append(para[:cut].strip())
            para = para[cut:].strip()
        if len(para) > 40:                                        # ignore tiny fragments / titles
            chunks.append(para)
    return chunks


def _load_chunks() -> list[dict]:
    """Read every supported file and return [{source, text}, ...]."""
    chunks = []
    for f in sorted(DOC_DIR.iterdir()):
        if f.suffix.lower() not in SUPPORTED:
            continue
        try:
            for c in _split(_read_file(f)):
                chunks.append({"source": f.name, "text": c})
        except Exception as e:                                    # one bad file must not break the app
            print(f"[rag] could not read {f.name}: {e}")
    return chunks


def list_documents() -> list[str]:
    """Names of the documents currently in the knowledge base."""
    return sorted(f.name for f in DOC_DIR.iterdir() if f.suffix.lower() in SUPPORTED)


def save_upload(name: str, data: bytes) -> str:
    """Save an uploaded document. Returns '' on success or a reason it was refused."""
    safe = Path(name).name                                        # strip any folder parts (blocks ../ tricks)
    if Path(safe).suffix.lower() not in SUPPORTED:
        return f"{safe}: only txt, md and pdf files are supported."
    if len(data) > MAX_UPLOAD_BYTES:
        return f"{safe}: file is larger than 10 MB."
    (DOC_DIR / safe).write_bytes(data)
    return ""


def reload():
    """Forget the index so the next search rebuilds it (call after uploads)."""
    global _index
    _index = None


def _title(source: str) -> str:
    """'pricing_policy.txt' -> 'pricing policy' (the file name is a strong hint about the topic)."""
    return Path(source).stem.replace("_", " ").replace("-", " ")


def _words(text: str) -> set[str]:
    """Lower-case words of 3+ letters, minus common filler words (used by keyword search)."""
    return set(re.findall(r"[a-z0-9]{3,}", text.lower())) - STOPWORDS


class KeywordIndex:
    """Fallback search: score = number of shared words. Needs no downloads."""
    def __init__(self, chunks): self.chunks = chunks
    def search(self, query: str, k: int = 2):
        q = _words(query)                                         # meaningful words in the question
        need = 2 if len(q) >= 3 else 1                            # short questions need only 1 shared word
        scored = [(len(q & _words(_title(c["source"]) + " " + c["text"])), c) for c in self.chunks]   # file name counts too
        scored.sort(key=lambda x: x[0], reverse=True)             # best first
        return [c for s, c in scored[:k] if s >= need]


class FaissIndex:
    """Real vector search: text -> embedding numbers -> nearest neighbours in FAISS."""
    def __init__(self, chunks):
        from langchain_community.vectorstores import FAISS        # lazy: only needed for this backend
        from langchain_huggingface import HuggingFaceEmbeddings
        emb = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
        self.store = FAISS.from_texts([_title(c["source"]) + ": " + c["text"] for c in chunks], emb,   # embed title + text
                                      metadatas=[{"source": c["source"], "text": c["text"]} for c in chunks])
    def search(self, query: str, k: int = 2):
        docs = self.store.similarity_search(query, k=k)           # k closest chunks by meaning
        return [{"source": d.metadata["source"], "text": d.metadata["text"]} for d in docs]   # return the original text


def search(query: str, k: int = 2) -> list[dict]:
    """Public function: returns [{source, text}, ...]."""
    global _index
    if _index is None:                                            # first call (or after reload): build
        chunks = _load_chunks()
        if not chunks:
            return []
        if config.RAG_BACKEND == "faiss":
            try:
                _index = FaissIndex(chunks)
            except Exception as e:                                # missing package / no internet => degrade
                print(f"[rag] FAISS unavailable ({e}); using keyword search.")
                _index = KeywordIndex(chunks)
        else:
            _index = KeywordIndex(chunks)
    return _index.search(query, k)