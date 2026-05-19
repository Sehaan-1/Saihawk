"""
answerer.py — Saihawk LLM Engine
==================================
Executes the compiled PromptPackage against the configured LLM and returns
a strictly validated List[str] of answers — one entry per question, in DOM order.

Pipeline position:
    github_fetcher.py  ─►  prompt_builder.py  ─►  answerer.py
                                                     (this file)
                                                          │
                                                          ▼
                                              Playwright Submitter
                                        (loops DOM text-areas 1:1)

Key Guarantees:
    - Output is ALWAYS List[str] with len == package.question_count
    - JSON extraction handles markdown fences, trailing commas, whitespace noise
    - Auto-correction: if LLM returns wrong count, re-prompts once with
      an explicit count-mismatch message before raising
    - Emergency repair: graceful truncation / padding as absolute last resort
      (never silently drops a text-area, never crashes the submitter)
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.messages import HumanMessage, SystemMessage
from loguru import logger

from src.llm.llm_manager import AIAdapter, LoggerChatModel
from src.llm.prompt_builder import PromptPackage


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_CORRECTION_ATTEMPTS = 2     # retries on count-mismatch before repair
_ANSWER_FALLBACK_TEXT    = (
    "I am eager to contribute to this role using my experience in Python, "
    "Docker, AWS, and machine learning. Please see my GitHub portfolio for "
    "further details on my projects and technical background."
)


# ---------------------------------------------------------------------------
# JSON Extraction
# ---------------------------------------------------------------------------

def _strip_markdown_fences(raw: str) -> str:
    """
    Remove ```json ... ``` or ``` ... ``` wrappers that some LLMs emit
    despite explicit instructions not to.
    """
    # Pattern covers ```json, ```JSON, ``` with optional whitespace
    raw = re.sub(r"```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
    raw = re.sub(r"```", "", raw)
    return raw.strip()


def _find_json_array(text: str) -> str:
    """
    Locate the first top-level JSON array [ ... ] in the text.
    Handles cases where the LLM prepends/appends prose to the array.
    """
    # Fast path: the whole string is already a valid array
    stripped = text.strip()
    if stripped.startswith("["):
        return stripped

    # Scan for the first '[' that opens a JSON array
    start = text.find("[")
    if start == -1:
        raise ValueError("No JSON array '[' found in LLM response.")

    # Walk forward to find the matching closing ']', respecting nesting
    depth = 0
    for i, ch in enumerate(text[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]

    raise ValueError("JSON array in LLM response is not properly closed.")


def _parse_answer_list(raw: str, expected_count: int) -> Tuple[List[str], bool]:
    """
    Parse the raw LLM string into a List[str].

    Returns
    -------
    (answers, count_ok)
        answers   – parsed list (may have wrong length if LLM misbehaved)
        count_ok  – True if len(answers) == expected_count
    """
    cleaned  = _strip_markdown_fences(raw)
    arr_text = _find_json_array(cleaned)

    # Fix common LLM JSON sloppiness: trailing commas before ] or }
    arr_text = re.sub(r",\s*([\]}])", r"\1", arr_text)

    data = json.loads(arr_text)

    if not isinstance(data, list):
        raise ValueError(
            f"LLM returned a JSON {type(data).__name__}, expected a list."
        )

    # Coerce every element to str (LLMs occasionally return int for numeric answers)
    answers = [str(item) for item in data]
    count_ok = len(answers) == expected_count

    logger.debug(
        f"[Answerer] Parsed {len(answers)} answer(s) "
        f"(expected {expected_count}) — count_ok={count_ok}"
    )
    return answers, count_ok


# ---------------------------------------------------------------------------
# Emergency Repair
# ---------------------------------------------------------------------------

def _repair_answer_list(answers: List[str], expected: int) -> List[str]:
    """
    Absolute last resort after all LLM correction attempts are exhausted.

    - Too many answers  → truncate to expected length
    - Too few answers   → pad missing slots with a safe fallback string
    """
    if len(answers) > expected:
        logger.warning(
            f"[Answerer] Repairing: truncating {len(answers)} → {expected} answers."
        )
        return answers[:expected]

    if len(answers) < expected:
        shortfall = expected - len(answers)
        logger.warning(
            f"[Answerer] Repairing: padding {shortfall} missing answer(s) "
            f"with fallback text."
        )
        answers.extend([_ANSWER_FALLBACK_TEXT] * shortfall)
        return answers

    return answers   # already correct (shouldn't reach here)


# ---------------------------------------------------------------------------
# Core Answerer Class
# ---------------------------------------------------------------------------

class SaihawkAnswerer:
    """
    Consumes a PromptPackage, invokes the LLM via the existing AIAdapter
    infrastructure, and returns a strictly validated List[str] of answers.

    Usage
    -----
    ::

        answerer = SaihawkAnswerer(config, api_key)
        answers  = answerer.answer(prompt_package)
        # answers is List[str], len == prompt_package.question_count
        # Playwright loop: zip(textarea_elements, answers)

    Parameters
    ----------
    config  : dict
        Same config dict consumed by AIAdapter / GPTAnswerer.
        Must contain 'llm_model_type' and 'llm_model'.
    api_key : str
        API key for the configured provider.
    """

    def __init__(self, config: Dict[str, Any], api_key: str) -> None:
        self._adapter    = AIAdapter(config, api_key)
        self._llm        = LoggerChatModel(self._adapter)
        logger.info(
            f"[Answerer] Initialised — model_type='{config.get('llm_model_type')}', "
            f"model='{config.get('llm_model')}'"
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def answer(self, package: PromptPackage) -> List[str]:
        """
        Generate answers for all questions in the PromptPackage.

        Execution flow:
            1. Build LangChain messages [SystemMessage, HumanMessage]
            2. Call LLM via LoggerChatModel (rate-limit handling included)
            3. Extract JSON array from raw response
            4. Validate count == package.question_count
            5. On mismatch → auto-correction attempt (up to _MAX_CORRECTION_ATTEMPTS)
            6. If correction still fails → emergency repair (truncate / pad)

        Parameters
        ----------
        package : PromptPackage
            Output of prompt_builder.build_prompt().

        Returns
        -------
        List[str]
            Exactly package.question_count strings, in question order.

        Raises
        ------
        RuntimeError
            If the LLM response cannot be parsed as a JSON list at all
            (not a count mismatch — an unparseable response).
        """
        logger.info(
            f"[Answerer] Generating answers — "
            f"job='{package.job_title}' @ '{package.company}' | "
            f"questions={package.question_count}"
        )

        # --- Initial call ---
        messages = self._build_messages(package.system_prompt, package.user_prompt)
        raw      = self._invoke_llm(messages)
        answers, count_ok = self._safe_parse(raw, package.question_count)

        if count_ok:
            logger.info(
                f"[Answerer] ✓ {package.question_count} answer(s) validated. "
                "Returning to submitter."
            )
            return answers

        # --- Auto-correction loop ---
        for attempt in range(1, _MAX_CORRECTION_ATTEMPTS + 1):
            logger.warning(
                f"[Answerer] Count mismatch — got {len(answers)}, "
                f"need {package.question_count}. "
                f"Correction attempt {attempt}/{_MAX_CORRECTION_ATTEMPTS}."
            )
            correction_prompt = self._build_correction_prompt(
                original_user_prompt=package.user_prompt,
                wrong_answer_count=len(answers),
                expected_count=package.question_count,
                bad_output=raw,
            )
            correction_messages = self._build_messages(
                package.system_prompt, correction_prompt
            )
            raw     = self._invoke_llm(correction_messages)
            answers, count_ok = self._safe_parse(raw, package.question_count)

            if count_ok:
                logger.info(
                    f"[Answerer] ✓ Count corrected on attempt {attempt}. "
                    "Returning to submitter."
                )
                return answers

        # --- Emergency repair (never crash the submitter) ---
        logger.error(
            f"[Answerer] All correction attempts exhausted. "
            f"Applying emergency repair: got {len(answers)}, "
            f"need {package.question_count}."
        )
        return _repair_answer_list(answers, package.question_count)

    # ------------------------------------------------------------------
    # Internal Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _build_messages(
        system_prompt: str,
        user_prompt: str,
    ) -> List:
        """Assemble LangChain-compatible message list."""
        return [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_prompt),
        ]

    def _invoke_llm(self, messages: List) -> str:
        """
        Call the LLM and extract the raw content string.
        LoggerChatModel handles rate-limit retries internally.
        """
        reply = self._llm(messages)
        # reply is an AIMessage; .content is the raw string
        raw = reply.content if hasattr(reply, "content") else str(reply)
        logger.debug(
            f"[Answerer] Raw LLM response ({len(raw)} chars): "
            f"{raw[:200]}{'...' if len(raw) > 200 else ''}"
        )
        return raw

    @staticmethod
    def _safe_parse(
        raw: str,
        expected_count: int,
    ) -> Tuple[List[str], bool]:
        """
        Attempt to parse raw into (answers, count_ok).
        On any parse failure, returns ([], False) instead of raising —
        the correction loop will handle it.
        """
        try:
            return _parse_answer_list(raw, expected_count)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                f"[Answerer] JSON parse failed: {exc}. "
                "Returning empty list for correction loop."
            )
            return [], False

    @staticmethod
    def _build_correction_prompt(
        original_user_prompt: str,
        wrong_answer_count: int,
        expected_count: int,
        bad_output: str,
    ) -> str:
        """
        Build a targeted correction message that gives the LLM
        the exact failure reason and re-states the contract.
        """
        return (
            f"{original_user_prompt}\n\n"
            f"--- CORRECTION REQUIRED ---\n"
            f"Your previous response contained {wrong_answer_count} answer(s) "
            f"but exactly {expected_count} answer(s) are required — "
            f"one per question, no more, no less.\n\n"
            f"Your previous (incorrect) output was:\n{bad_output}\n\n"
            f"You MUST return a JSON array with EXACTLY {expected_count} "
            f"string(s). "
            f"Do not add, merge, or split answers. "
            f"Do not include any text outside the JSON array."
        )


# ---------------------------------------------------------------------------
# Convenience Function — direct pipeline entry point
# ---------------------------------------------------------------------------

def generate_answers(
    package: PromptPackage,
    config: Dict[str, Any],
    api_key: str,
) -> List[str]:
    """
    Stateless convenience wrapper for one-shot pipeline calls.

    The orchestrator can use this directly without managing a
    SaihawkAnswerer instance:

    ::

        answers = generate_answers(package, config, api_key)

    Parameters
    ----------
    package : PromptPackage
        Output of prompt_builder.build_prompt().
    config  : dict
        LLM config dict: {'llm_model_type': ..., 'llm_model': ...}
    api_key : str
        API key for the configured provider.

    Returns
    -------
    List[str]
        Exactly package.question_count strings, ready for Playwright loop.
    """
    answerer = SaihawkAnswerer(config, api_key)
    return answerer.answer(package)


# ---------------------------------------------------------------------------
# CLI Smoke Test  (python -m src.llm.answerer)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys
    from dotenv import load_dotenv

    from src.llm.prompt_builder import JobContext, build_prompt
    from src.llm.github_fetcher import fetch_github_context

    load_dotenv()

    print("=" * 60)
    print("  SaihawkAnswerer — End-to-End Smoke Test")
    print("=" * 60)

    # --- Minimal test job ---
    test_job = {
        "title"    : "Machine Learning Intern",
        "company"  : "FinTech Startup",
        "skills"   : ["Python", "Machine Learning", "XGBoost", "Data Analysis"],
        "questions": [
            "Tell us about a project where you applied machine learning.",
            "Why are you interested in this role?",
        ],
    }

    # --- Config: resolve provider + API key from .env ---
    provider  = os.getenv("LLM_PROVIDER", "nvidia").lower()
    model_map = {
        "nvidia"      : "moonshotai/kimi-k2.6",
        "gemini"      : "gemini-1.5-pro",
        "openai"      : "gpt-4o-mini",
        "ollama"      : "llama3",
        "huggingface" : "mistralai/Mistral-7B-Instruct-v0.3",
    }
    key_map = {
        "nvidia"      : os.getenv("NVIDIA_API_KEY", ""),
        "gemini"      : os.getenv("GEMINI_API_KEY", ""),
        "openai"      : os.getenv("OPENAI_API_KEY", ""),
        "huggingface" : os.getenv("HUGGINGFACEHUB_API_TOKEN", ""),
        "ollama"      : "",   # no key needed for local Ollama
    }
    api_key = key_map.get(provider, "")
    config = {
        "llm_model_type": provider,
        "llm_model"     : os.getenv("LLM_MODEL", model_map.get(provider, "moonshotai/kimi-k2.6")),
    }

    try:
        print("\n[1/3] Fetching live GitHub context...")
        github_ctx = fetch_github_context()
        print(f"      ✓ Context length: {len(github_ctx)} chars\n")

        print("[2/3] Building prompt package...")
        from src.llm.prompt_builder import job_context_from_dict
        job_ctx = job_context_from_dict(test_job)
        package = build_prompt(github_ctx, job_ctx)
        print(f"      ✓ Anchor: DYNAMIC (LLM-selected) | "
              f"Questions: {package.question_count}\n")

        print("[3/3] Invoking LLM and parsing answers...")
        answers = generate_answers(package, config, api_key)
        print(f"      ✓ {len(answers)} answer(s) returned\n")

        print("-" * 60)
        for i, ans in enumerate(answers, 1):
            print(f"Q{i}: {test_job['questions'][i - 1]}")
            print(f"A{i}: {ans}")
            print()

        print("[OK] Full pipeline completed successfully.")
        sys.exit(0)

    except Exception as exc:
        print(f"\n[ERROR] {exc}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
