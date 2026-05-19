"""
submitter.py
Playwright Form Submission Agent for Internshala.

Receives generated answers and maps them to live DOM text areas.
Implements human-like interaction patterns to reduce bot-detection risk.
"""

from __future__ import annotations

import asyncio
import random

from loguru import logger
from playwright.async_api import Page, TimeoutError as PlaywrightTimeout

from .selectors import SELECTORS


async def complete_application(
    page: Page,
    answers: list[str],
    *,
    dry_run: bool = True,
) -> bool:
    """
    Fill all assessment text areas and (optionally) submit the application.

    Args:
        page:     Live Playwright Page with the application wizard open.
        answers:  Ordered list of strings from the answerer agent.
        dry_run:  If True (default), fill forms but DON'T click submit.
                  Set to False only in production once you have verified
                  the selectors and answer quality manually.

    Returns:
        True on success, False on graceful failure.
    """
    try:
        await page.wait_for_selector(SELECTORS["text_area_inputs"], timeout=10_000)
    except PlaywrightTimeout:
        logger.error("Application form text areas not found. Cannot submit.")
        return False

    text_areas = await page.locator(SELECTORS["text_area_inputs"]).all()

    if len(text_areas) != len(answers):
        logger.warning(
            f"Answer/field count mismatch — "
            f"generated {len(answers)} answers but found {len(text_areas)} field(s). "
            f"Attempting best-effort fill."
        )

    fill_count = min(len(text_areas), len(answers))

    for idx in range(fill_count):
        area = text_areas[idx]
        answer_text = answers[idx]

        try:
            await area.scroll_into_view_if_needed()
            await area.focus()

            # Clear any pre-filled content
            await area.triple_click()
            await area.fill("")

            # Type character-by-character with jitter for stealth
            await _human_type(page, area, answer_text)

            delay = random.uniform(0.3, 0.8)
            await asyncio.sleep(delay)
            logger.debug(f"Filled field {idx + 1}/{fill_count}.")

        except Exception as exc:
            logger.error(f"Error filling field {idx + 1}: {exc}")
            return False

    logger.success(
        f"All {fill_count} field(s) populated. "
        f"dry_run={'ON — submission skipped' if dry_run else 'OFF — submitting now'}."
    )

    if not dry_run:
        return await _click_submit(page)

    return True


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

async def _human_type(page: Page, locator, text: str) -> None:
    """
    Type text with randomised per-character delays to mimic human typing.
    Uses fill() for speed on long answers, keyboard simulation for short ones.
    """
    if len(text) > 80:
        # For long answers, use fill() + small post-fill delay
        await locator.fill(text)
        await asyncio.sleep(random.uniform(0.1, 0.3))
    else:
        # For short answers, type character-by-character
        await locator.fill("")
        for char in text:
            await locator.type(char, delay=random.uniform(30, 80))


async def _click_submit(page: Page) -> bool:
    """Click the final submit button and wait for a success indicator."""
    try:
        submit_btn = page.locator(SELECTORS["submit_final"])
        if await submit_btn.count() == 0:
            logger.error("Submit button not found on page.")
            return False

        await submit_btn.scroll_into_view_if_needed()
        await asyncio.sleep(random.uniform(0.5, 1.2))
        await submit_btn.click()

        # Wait for either page navigation or success modal (adjust selector as needed)
        await page.wait_for_load_state("networkidle", timeout=15_000)
        logger.success("Application submitted successfully.")
        return True

    except PlaywrightTimeout:
        logger.error("Timed out waiting for submission confirmation.")
        return False
    except Exception as exc:
        logger.error(f"Submission error: {exc}")
        return False
