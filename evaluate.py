"""evaluate.py - measures accuracy on a question set, for three versions of the system.

Run:  python evaluate.py
It compares each generated result to the result of the correct SQL in examples.py.
IMPORTANT: with LLM_PROVIDER=mock the score is meaningless (mock reads the answer key).
Use gemini or groq, and extend examples.py to ~40 questions, to get numbers you can quote.
"""
import pandas as pd
import config, db, agents
from examples import EXAMPLES

VARIANTS = {                                                # which features are on in each version
    "1. basic (no schema selection, no glossary, no critic)": dict(USE_SCHEMA_SELECTION=False, USE_GLOSSARY=False, USE_CRITIC=False),
    "2. + schema selection + glossary":                        dict(USE_SCHEMA_SELECTION=True,  USE_GLOSSARY=True,  USE_CRITIC=False),
    "3. + critic retry loop (full system)":                    dict(USE_SCHEMA_SELECTION=True,  USE_GLOSSARY=True,  USE_CRITIC=True),
}


def _normalize(df: pd.DataFrame):
    """Turn a result into a sorted list of rounded tuples so column names/order don't matter."""
    rows = [tuple(round(v, 2) if isinstance(v, float) else v for v in r) for r in df.itertuples(index=False)]
    return sorted(rows, key=str)


def same_result(a: pd.DataFrame, b: pd.DataFrame) -> bool:
    return _normalize(a) == _normalize(b)


def main():
    for name, flags in VARIANTS.items():
        for k, v in flags.items():                          # switch features on/off
            setattr(config, k, v)
        stats = {}                                          # level -> [correct, total]
        for ex in EXAMPLES:
            state = agents.answer(ex["question"], "admin")
            gold = db.run_select(ex["sql"])                 # the answer key
            ok = (not state.get("error")) and same_result(state["df"], gold)
            c, t = stats.setdefault(ex["level"], [0, 0])
            stats[ex["level"]] = [c + ok, t + 1]
            if not ok:
                print(f"   MISS [{ex['level']}] {ex['question']}")
        total_c = sum(c for c, _ in stats.values()); total_t = sum(t for _, t in stats.values())
        per_level = ", ".join(f"{lvl}: {c}/{t}" for lvl, (c, t) in stats.items())
        print(f"{name}\n   overall {total_c}/{total_t} = {100 * total_c / total_t:.0f}%   ({per_level})\n")


if __name__ == "__main__":
    main()
