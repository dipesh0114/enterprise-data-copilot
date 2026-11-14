"""llm.py - one function, ask(prompt), that works with Gemini, Groq or an offline mock."""
import re, difflib                                          # re: text cleanup, difflib: fuzzy question matching
import config
from examples import EXAMPLES, WHY_EXAMPLE

_llm = None                                                 # cached model object (create once, reuse)


class _Msg:                                                 # tiny stand-in for a LangChain message
    def __init__(self, content): self.content = content


class MockLLM:
    """Offline model. Reads the '### TASK:' tag in the prompt and answers from examples.py.
    It exists so you can run the whole pipeline with no API key. It is NOT intelligent."""
    def invoke(self, prompt: str):
        task = re.search(r"### TASK: (\w+)", prompt).group(1)          # which agent is calling?
        if task == "tables":                                           # schema agent: keep every table
            return _Msg("ALL")
        if task == "critic":                                           # critic: always satisfied
            return _Msg("OK")
        if task == "insight":                                          # report agent: generic bullets
            return _Msg("- (mock mode) Numbers above come straight from the SQL shown.\n"
                        "- Switch LLM_PROVIDER to gemini or groq for real written insights.")
        if task == "docs":                                             # document Q&A: quote the best passage
            m = re.search(r"\[1\] \(source: ([^)]+)\) (.*)", prompt)
            if not m:
                return _Msg("NOT_FOUND")
            return _Msg(f"- (mock mode) Most relevant passage, from {m.group(1)}: {m.group(2)}\n"
                        "- Switch LLM_PROVIDER to gemini or groq for answers written for your exact question.")
        if task == "sql":                                              # SQL agent: look the question up
            q = re.search(r"QUESTION: (.*)", prompt).group(1).strip()
            pool = EXAMPLES + [WHY_EXAMPLE]
            hit = difflib.get_close_matches(q, [e["question"] for e in pool], n=1, cutoff=0.55)
            if hit:
                return _Msg(next(e["sql"] for e in pool if e["question"] == hit[0]))
            return _Msg("SELECT * FROM unknown_table_mock_cannot_answer")   # fails on purpose => graceful error path
        return _Msg("")


def _get_llm():
    """Build the model object for the configured provider (imports are lazy so unused packages aren't needed)."""
    global _llm
    if _llm is not None:
        return _llm
    if config.LLM_PROVIDER == "gemini":
        from langchain_google_genai import ChatGoogleGenerativeAI
        _llm = ChatGoogleGenerativeAI(model=config.GEMINI_MODEL, google_api_key=config.GOOGLE_API_KEY, temperature=0)
    elif config.LLM_PROVIDER == "groq":
        from langchain_groq import ChatGroq
        _llm = ChatGroq(model=config.GROQ_MODEL, api_key=config.GROQ_API_KEY, temperature=0)
    else:
        _llm = MockLLM()
    return _llm                                             # temperature=0 => same input gives (almost) same SQL


def ask(prompt: str) -> str:
    """Send a prompt, get plain text back."""
    content = _get_llm().invoke(prompt).content             # call the model
    if isinstance(content, list):                           # some providers return a list of parts
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    return content.strip()


def clean_sql(text: str) -> str:
    """Models often wrap SQL in ```sql fences. Remove them."""
    text = re.sub(r"```(?:sql)?", "", text, flags=re.I)     # delete the fence markers
    return text.strip().rstrip(";").strip()                 # trim spaces and a trailing semicolon