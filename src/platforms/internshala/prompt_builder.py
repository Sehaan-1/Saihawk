"""
prompt_builder.py  — Internshala Platform Layer  [re-export shim]
===================================================================
The real prompt builder lives in src/llm/prompt_builder.py.
This shim re-exports everything from there so any code that still
imports from the platform path continues to work without changes.
"""
from src.llm.prompt_builder import (  # noqa: F401  (re-export)
    JobContext,
    PromptPackage,
    build_prompt,
    job_context_from_dict,
)
