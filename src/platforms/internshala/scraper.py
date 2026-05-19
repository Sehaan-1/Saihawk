"""
scraper.py  — Internshala Scraper Agent  [v2]
===============================================
Responsibilities:
  1. Paginate listing pages and collect individual job URLs.
  2. Navigate each posting and extract: title, company, skills, stipend.
  3. Click Apply and extract every assessment question label — in DOM order.

Key reliability improvements over v1:
  - Fallback selector chains: tries multiple selectors before giving up.
  - Smart question extraction: handles both old-style (.form_group) and
    new-style (.form-group.additional_question) Internshala wizard layouts.
  - Graceful already-applied detection across multiple indicator selectors.
  - All timeouts and delays are constants at the top — easy to tune.

All methods accept an already-authenticated Playwright Page so the session
cookie is preserved across the full pipeline run.
"""

from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .selectors import FALLBACK_SELECTORS, SELECTORS

# ---------------------------------------------------------------------------
# Tuning constants
# ---------------------------------------------------------------------------

_NAV_TIMEOUT     = 30_000   # ms: page.goto timeout
_SELECTOR_WAIT   = 12_000   # ms: wait_for_selector timeout
_APPLY_WAIT      = 12_000   # ms: wait for wizard to open after Apply click
_CRAWL_DELAY     = 1.5      # seconds: polite inter-page delay


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _first_matching(page: Page, key: str, timeout: int = 5_000) -> str | None:
    """
    Return the first selector from FALLBACK_SELECTORS[key] that has
    a visible element on the page within `timeout` ms.
    Falls back to SELECTORS[key] if the key has no fallback list.
    Returns None if nothing matches.
    """
    candidates = FALLBACK_SELECTORS.get(key, [SELECTORS.get(key, "")])
    for selector in candidates:
        if not selector:
            continue
        try:
            await page.wait_for_selector(selector, timeout=timeout, state="visible")
            return selector
        except PlaywrightTimeout:
            continue
    return None


async def _safe_inner_text(page: Page, selector: str) -> str:
    """Return inner text of first matching element, or '' on failure."""
    try:
        return (await page.locator(selector).first.inner_text()).strip()
    except Exception:
        return ""


async def _any_visible(page: Page, key: str) -> bool:
    """Return True if any fallback selector for key has a visible match."""
    sel = await _first_matching(page, key, timeout=3_000)
    return sel is not None


# ---------------------------------------------------------------------------
# Listing-level extraction
# ---------------------------------------------------------------------------

async def extract_job_links(page: Page, search_url: str) -> list[str]:
    """
    Navigate to a search URL and collect all individual job-posting links.
    Handles pagination automatically.

    Args:
        page:       An authenticated Playwright Page.
        search_url: Internshala search results URL.

    Returns:
        List of absolute job-posting URLs.
    """
    job_links: list[str] = []
    current_url: str | None = search_url

    while current_url:
        logger.debug(f"[Scraper] Listing page: {current_url}")
        try:
            await page.goto(current_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
            await page.wait_for_selector(SELECTORS["job_card"], timeout=_SELECTOR_WAIT)
        except PlaywrightTimeout:
            logger.warning(
                f"[Scraper] Timeout waiting for job cards at {current_url}. "
                "Stopping pagination."
            )
            break

        # Collect all href attributes — try both link patterns Internshala uses
        card_locator = page.locator(
            f"{SELECTORS['job_card']} a.view_detail_button,"
            f"{SELECTORS['job_card']} a[href*='/internship/detail/'],"
            f"{SELECTORS['job_card']} a[href*='/jobs/detail/']"
        )
        cards = await card_locator.all()

        page_links: list[str] = []
        for card in cards:
            href = await card.get_attribute("href")
            if href:
                full_url = (
                    f"https://internshala.com{href}"
                    if href.startswith("/")
                    else href
                )
                if full_url not in job_links:
                    page_links.append(full_url)

        job_links.extend(page_links)
        logger.info(f"[Scraper] Found {len(page_links)} job link(s) on this page.")

        # Pagination
        next_btn = page.locator(SELECTORS["next_page_button"])
        if await next_btn.count() > 0:
            next_href = await next_btn.get_attribute("href")
            current_url = (
                f"https://internshala.com{next_href}"
                if next_href
                else None
            )
            await asyncio.sleep(_CRAWL_DELAY)
        else:
            current_url = None

    logger.info(f"[Scraper] Total unique job links collected: {len(job_links)}")
    return job_links


# ---------------------------------------------------------------------------
# Individual posting extraction
# ---------------------------------------------------------------------------

async def extract_job_details(page: Page, job_url: str) -> dict[str, Any] | None:
    """
    Navigate to a single job posting, extract metadata and assessment questions.

    The page is left open with the application wizard visible so the
    submitter can write directly into the form fields without re-navigating.

    Args:
        page:    An authenticated Playwright Page.
        job_url: Direct URL to the internship/job posting.

    Returns:
        Structured dict with job metadata + questions, or None on failure.
        Returned dict keys:
            url        (str)       – the job URL
            title      (str)       – job/internship title
            company    (str)       – company name
            skills     (list[str]) – mandatory skill tokens
            stipend    (str)       – stipend/salary string
            questions  (list[str]) – assessment question labels in DOM order
            page       (Page)      – live Playwright Page (wizard is open)
    """
    logger.debug(f"[Scraper] Extracting details from: {job_url}")

    try:
        await page.goto(job_url, wait_until="domcontentloaded", timeout=_NAV_TIMEOUT)
        title_sel = await _first_matching(page, "job_title", timeout=_SELECTOR_WAIT)
        if not title_sel:
            logger.error(f"[Scraper] No title selector matched — skipping: {job_url}")
            return None
    except PlaywrightTimeout:
        logger.error(f"[Scraper] Timeout loading job detail page: {job_url}")
        return None

    # ----------------------------------------------------------------
    # Core metadata (all gracefully fallback to safe defaults)
    # ----------------------------------------------------------------

    title = await _safe_inner_text(page, title_sel) or "Unknown Role"

    company_sel = await _first_matching(page, "company_name", timeout=3_000)
    company = (
        await _safe_inner_text(page, company_sel) if company_sel else "Unknown Company"
    )

    skills_sel = await _first_matching(page, "skills_container", timeout=3_000)
    if skills_sel:
        try:
            skills = [
                s.strip()
                for s in await page.locator(skills_sel).all_inner_texts()
                if s.strip()
            ]
        except Exception:
            skills = []
    else:
        skills = []

    stipend_sel = await _first_matching(page, "stipend", timeout=3_000)
    stipend = (
        await _safe_inner_text(page, stipend_sel) if stipend_sel else "Not specified"
    )

    # ----------------------------------------------------------------
    # Already-applied guard (check BEFORE clicking Apply)
    # ----------------------------------------------------------------

    if await _any_visible(page, "already_applied_indicator"):
        logger.info(f"[Scraper] Already applied to '{title}' @ '{company}'. Skipping.")
        return None

    # ----------------------------------------------------------------
    # Click Apply and extract assessment questions
    # ----------------------------------------------------------------

    questions: list[str] = []

    apply_sel = await _first_matching(page, "apply_button", timeout=5_000)
    if not apply_sel:
        logger.warning(f"[Scraper] No Apply button found: {job_url}. Skipping.")
        return None

    try:
        apply_btn = page.locator(apply_sel).first
        await apply_btn.scroll_into_view_if_needed()
        await apply_btn.click()

        # Wait for the wizard to open — try both textarea selector variants
        wizard_sel = await _first_matching(page, "text_area_inputs", timeout=_APPLY_WAIT)

        if wizard_sel:
            # Questions are the label immediately above each textarea
            question_sel = await _first_matching(page, "assessment_questions", timeout=3_000)
            if question_sel:
                raw_labels = await page.locator(question_sel).all_inner_texts()
                questions = [q.strip() for q in raw_labels if q.strip()]
                logger.debug(
                    f"[Scraper] Extracted {len(questions)} question(s) "
                    f"via selector '{question_sel}'."
                )
            else:
                # Fallback: try to infer question text from placeholder attributes
                textareas = await page.locator(wizard_sel).all()
                for ta in textareas:
                    placeholder = await ta.get_attribute("placeholder") or ""
                    if placeholder.strip():
                        questions.append(placeholder.strip())
                logger.debug(
                    f"[Scraper] Inferred {len(questions)} question(s) from placeholders."
                )
        else:
            # No text areas — possibly a direct-apply job (no assessment)
            logger.info(
                f"[Scraper] No assessment wizard for '{title}' @ '{company}'. "
                "Will proceed with no-question submission."
            )

    except PlaywrightTimeout:
        logger.warning(
            f"[Scraper] Assessment wizard did not load within {_APPLY_WAIT}ms "
            f"for: {job_url}"
        )
    except Exception as exc:
        logger.error(
            f"[Scraper] Error during Apply flow for {job_url}: {exc}"
        )

    job_data: dict[str, Any] = {
        "url"      : job_url,
        "title"    : title,
        "company"  : company,
        "skills"   : skills,
        "stipend"  : stipend,
        "questions": questions,
        "page"     : page,   # submitter uses this live reference to fill forms
    }

    logger.info(
        f"[Scraper] Extracted: '{title}' @ '{company}' | "
        f"skills={len(skills)} | questions={len(questions)}"
    )
    return job_data
