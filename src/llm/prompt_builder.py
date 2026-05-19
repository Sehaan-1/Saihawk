"""
prompt_builder.py — Saihawk LLM Engine  [v2 — Dynamic Routing Mode]
======================================================================
Fuses the live GitHub portfolio metadata with scraped job context into a
single, deterministic prompt block ready for the LLM answerer.

Pipeline position:
    github_fetcher.py  ─►  prompt_builder.py  ─►  answerer.py
                              (this file)

v2 Changes vs v1:
    REMOVED — _ML_SIGNALS / _BACKEND_SIGNALS keyword tables
    REMOVED — _XAI_ANCHOR_BLOCK / _TENDERPULSE_ANCHOR_BLOCK / _BOTH_ANCHOR_BLOCK
    REMOVED — _detect_anchor() Python-side routing function
    ADDED   — EXECUTION STRATEGY block: the LLM dynamically selects which
              of your portfolio projects are most relevant to the job, using
              the full metadata list from github_fetcher.py.

Why this is strictly better:
    - Zero maintenance: push a new project → agent uses it immediately
    - LLM routing > keyword matching: understands semantic relationships
      (e.g. "data pipeline" maps to scraping AND ML projects)
    - Portfolio always reflects your real GitHub state, not a .env whitelist
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Dict, List, Optional

from loguru import logger


# ---------------------------------------------------------------------------
# Data Structures
# ---------------------------------------------------------------------------

@dataclass
class JobContext:
    """Structured representation of a scraped Internshala job."""
    title: str
    company: str
    skills: List[str]       # mandatory requirement tokens from job page
    questions: List[str]    # literal text-area label strings, in DOM order


@dataclass
class PromptPackage:
    """
    Output of build_prompt().
    Consumed directly by answerer.SaihawkAnswerer.answer().
    """
    system_prompt: str
    user_prompt: str
    question_count: int
    job_title: str
    company: str


# ---------------------------------------------------------------------------
# System Prompt — The LLM's Constitution (constant across all jobs)
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are the personal job application assistant for a software engineering \
student with a baseline technical stack of: Python, Java, C++, Docker, \
AWS, GCP, and ML/DevOps. Your sole purpose is to generate authentic, \
first-person answers to internship and job application assessment questions.

STRICT RULES — NON-NEGOTIABLE:
1. NEVER invent skills, certifications, projects, or work experience that \
are not present in the GITHUB PORTFOLIO provided to you.
2. NEVER mention companies, internships, or job titles not evidenced in \
the portfolio.
3. Write EXCLUSIVELY in the first person: "I built...", \
"My architecture utilises...", "I designed...".
4. Ground every answer ONLY in:
   a) The GITHUB PORTFOLIO block (primary source).
   b) The BASELINE STACK: Python, Java, C++, Docker, AWS, GCP, ML, DevOps.
5. Your ENTIRE response MUST be a valid JSON array of strings — one string \
per question, in the exact same order as the QUESTIONS block. No prose, no \
markdown fences, no explanation outside the JSON array.
6. Each answer must be 3–6 sentences. Concise, confident, and technical.
7. If a question cannot be answered from the portfolio, write a credible \
answer using the baseline stack — but do NOT fabricate project names or \
credentials absent from the portfolio.
"""


# ---------------------------------------------------------------------------
# Core Builder Function
# ---------------------------------------------------------------------------

def build_prompt(
    live_github_context: str,
    job_context: JobContext,
) -> PromptPackage:
    """
    Fuse live GitHub portfolio metadata with scraped job context into a
    compiled PromptPackage ready for answerer.py.

    The LLM itself selects which portfolio projects to anchor its answers
    on, guided by the EXECUTION STRATEGY block injected into the prompt.

    Parameters
    ----------
    live_github_context : str
        Compact metadata string from github_fetcher.fetch_github_context().
        Contains repo names, descriptions, languages, topics — no READMEs.

    job_context : JobContext
        Structured job data: title, company, skills, questions.

    Returns
    -------
    PromptPackage
        system_prompt, user_prompt, question_count, job_title, company.

    Raises
    ------
    ValueError
        If job_context.questions is empty.
    """
    if not job_context.questions:
        raise ValueError(
            f"[PromptBuilder] No questions for '{job_context.title}' "
            f"at '{job_context.company}'. Scraper must supply ≥ 1 question."
        )

    logger.info(
        f"[PromptBuilder] Building prompt — "
        f"job='{job_context.title}' @ '{job_context.company}' | "
        f"skills={len(job_context.skills)} | "
        f"questions={len(job_context.questions)}"
    )

    # --- Format skills list ---
    skills_formatted = (
        "\n  • " + "\n  • ".join(job_context.skills)
        if job_context.skills
        else "  (not specified)"
    )

    # --- Format questions with 1-based index ---
    questions_formatted = "\n".join(
        f"  Q{i + 1}: {q}" for i, q in enumerate(job_context.questions)
    )

    # --- Dynamic output schema (count injected at runtime) ---
    n = len(job_context.questions)
    example_array = json.dumps(
        [f"<answer to Q{i + 1}>" for i in range(min(n, 2))],
        indent=2,
    )
    output_schema_block = (
        f"OUTPUT FORMAT — MANDATORY:\n"
        f"Return ONLY a valid JSON array of exactly {n} string(s) — "
        f"one per question, preserving Q1…Q{n} order.\n"
        f"Example shape:\n{example_array}"
        + (" ..." if n > 2 else "") +
        f"\nDO NOT include markdown fences, prose, or any text outside "
        f"the JSON array."
    )

    # --- EXECUTION STRATEGY — LLM-directed dynamic routing ---
    execution_strategy = f"""\
EXECUTION STRATEGY — FOLLOW IN ORDER:
1. Read the CANDIDATE'S ACTIVE GITHUB PORTFOLIO above carefully.
2. Read the REQUIRED SKILLS for this specific job.
3. DYNAMIC PROJECT SELECTION: Identify the 1–2 portfolio projects that \
best match the job's required skills.
   • Backend / Cloud / DevOps / Scraping jobs → look for infrastructure, \
automation, or pipeline projects in the portfolio.
   • ML / Data Science / AI jobs → look for model-building, data \
processing, or explainability projects in the portfolio.
   • Full-stack / General → pick whichever projects demonstrate the \
broadest alignment with the required skills.
4. Anchor your answers to those selected projects. Reference them by \
their exact repository name as listed in the portfolio.
5. Strictly IGNORE portfolio projects that do not align with this job's \
requirements — do not shoehorn irrelevant work into answers.
6. If no portfolio project strongly matches a question, answer using the \
BASELINE STACK (Python, Java, C++, Docker, AWS, GCP, ML, DevOps) only.\
"""

    # --- Assemble user prompt ---
    user_prompt = f"""\
JOB CONTEXT:
  Title   : {job_context.title}
  Company : {job_context.company}

REQUIRED SKILLS (mandatory, scraped from job page):
{skills_formatted}

CANDIDATE'S ACTIVE GITHUB PORTFOLIO (live data — primary source of truth):
{live_github_context}

{execution_strategy}

QUESTIONS TO ANSWER (answer each in the order shown):
{questions_formatted}

{output_schema_block}
"""

    logger.debug(
        f"[PromptBuilder] Prompt compiled — "
        f"{len(user_prompt)} chars in user_prompt."
    )

    return PromptPackage(
        system_prompt  = _SYSTEM_PROMPT,
        user_prompt    = user_prompt,
        question_count = n,
        job_title      = job_context.title,
        company        = job_context.company,
    )


# ---------------------------------------------------------------------------
# Convenience Factory — scraper dict → JobContext
# ---------------------------------------------------------------------------

def job_context_from_dict(raw: Dict) -> JobContext:
    """
    Convert a raw scraper output dict into a typed JobContext.

    Expected keys:
        title     (str)       – job title
        company   (str)       – company name
        skills    (List[str]) – mandatory skill tokens
        questions (List[str]) – literal text-area label strings, DOM order

    All keys are optional and default gracefully.
    """
    title     = raw.get("title",     "Unknown Role")
    company   = raw.get("company",   "Unknown Company")
    skills    = [s.strip() for s in raw.get("skills",    []) if str(s).strip()]
    questions = [q.strip() for q in raw.get("questions", []) if str(q).strip()]

    logger.debug(
        f"[PromptBuilder] JobContext — title='{title}', "
        f"company='{company}', skills={len(skills)}, "
        f"questions={len(questions)}"
    )
    return JobContext(
        title=title, company=company, skills=skills, questions=questions
    )
