"""
answerer.py  — Internshala Platform Layer  [re-export shim]
=============================================================
The real answerer lives in src/llm/answerer.py.
This shim re-exports SaihawkAnswerer so any code that imports from
the platform path continues to work without changes.

For direct use, prefer:
    from src.llm.answerer import SaihawkAnswerer
"""
from src.llm.answerer import SaihawkAnswerer  # noqa: F401  (re-export)
