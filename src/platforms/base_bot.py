"""
base_bot.py
Abstract base class for all platform-specific bots.
Every platform bot (Internshala, Hirist, Wellfound, Cuvette) inherits from this.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger
from playwright.async_api import BrowserContext, Page


class BasePlatformBot(ABC):
    """
    Abstract base for all Saihawk platform bots.

    Subclasses must implement:
        - login()
        - search_jobs()
        - process_single_job()
    """

    def __init__(self, browser_context: BrowserContext, config: dict[str, Any]) -> None:
        self.ctx = browser_context
        self.config = config
        self.page: Page | None = None

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def new_page(self) -> Page:
        """Open a fresh page in the shared browser context."""
        self.page = await self.ctx.new_page()
        logger.debug(f"[{self.__class__.__name__}] New page opened.")
        return self.page

    async def close_page(self) -> None:
        if self.page and not self.page.is_closed():
            await self.page.close()
            logger.debug(f"[{self.__class__.__name__}] Page closed.")

    # ------------------------------------------------------------------
    # Human-like interaction helpers
    # ------------------------------------------------------------------

    @staticmethod
    async def human_sleep(min_s: float = 0.4, max_s: float = 1.2) -> None:
        """Randomised sleep to reduce heuristic bot detection."""
        import random
        delay = random.uniform(min_s, max_s)
        await asyncio.sleep(delay)

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def login(self) -> None:
        """Authenticate with the platform."""

    @abstractmethod
    async def search_jobs(self, search_urls: list[str]) -> list[dict[str, Any]]:
        """Return a list of raw job-detail dicts from the given search URLs."""

    @abstractmethod
    async def process_single_job(self, job_url: str) -> bool:
        """
        Full end-to-end pipeline for one job posting.
        Returns True on successful submission, False on graceful failure.
        """

    # ------------------------------------------------------------------
    # Entrypoint
    # ------------------------------------------------------------------

    async def run(self, search_urls: list[str]) -> None:
        """Main orchestration loop."""
        logger.info(f"[{self.__class__.__name__}] Starting run loop.")
        await self.login()
        jobs = await self.search_jobs(search_urls)
        logger.info(f"[{self.__class__.__name__}] Found {len(jobs)} job(s).")

        for job in jobs:
            job_url: str = job.get("url", "")
            if not job_url:
                logger.warning("Skipping job with no URL.")
                continue
            try:
                success = await self.process_single_job(job_url)
                status = "✓ Applied" if success else "✗ Skipped"
                logger.info(f"{status}: {job_url}")
            except Exception as exc:  # noqa: BLE001
                logger.error(f"Unhandled error on {job_url}: {exc}")
            await self.human_sleep(2.0, 5.0)

        logger.info(f"[{self.__class__.__name__}] Run loop complete.")
