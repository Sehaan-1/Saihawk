"""
orchestrator.py  — Internshala Pipeline Orchestrator  [v2]
============================================================
Wires all four agents into one end-to-end pipeline:

    Phase 0: GitHub Context  — fetch once, reuse for every job
    Phase 1: Login           — authenticate with Internshala
    Phase 2: Scrape          — listing pages → job detail pages → questions
    Phase 3: Answer          — LLM generates JSON answers per job
    Phase 4: Submit          — Playwright fills + (optionally) submits form

Architecture notes:
    - GitHub context is fetched ONCE at startup (Phase 0), then injected
      into every per-job prompt — no redundant API calls.
    - The LLM engine is src.llm (github_fetcher + prompt_builder + answerer).
      The platform layer does NOT contain its own LLM logic.
    - dry_run=True by default — forms are filled but NOT submitted.
      Set SAIHAWK_DRY_RUN=false in .env to enable live submissions.
"""

from __future__ import annotations

import os
from typing import Any

from dotenv import load_dotenv
from loguru import logger
from playwright.async_api import BrowserContext, TimeoutError as PlaywrightTimeout

from ..base_bot import BasePlatformBot
from .scraper import extract_job_details, extract_job_links
from .selectors import SELECTORS
from .submitter import complete_application

# --- Real LLM engine (src/llm/) ---
from src.llm.github_fetcher import fetch_github_context
from src.llm.prompt_builder import build_prompt, job_context_from_dict
from src.llm.answerer import SaihawkAnswerer

load_dotenv()


class IntershalaPipeline(BasePlatformBot):
    """
    Full end-to-end Internshala application pipeline.

    Usage example:
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=False)
            ctx = await browser.new_context()
            pipeline = IntershalaPipeline(ctx, dry_run=True)
            await pipeline.run(search_urls=[
                "https://internshala.com/internships/python-internship/",
            ])
    """

    def __init__(
        self,
        browser_context: BrowserContext,
        config: dict[str, Any] | None = None,
        *,
        dry_run: bool | None = None,
    ) -> None:
        super().__init__(browser_context, config or {})

        # dry_run: env var takes precedence over constructor arg
        env_flag = os.getenv("SAIHAWK_DRY_RUN", "true").strip().lower()
        if dry_run is None:
            self.dry_run = env_flag not in ("false", "0", "no")
        else:
            self.dry_run = dry_run

        self._github_context: str = ""
        self._answerer: SaihawkAnswerer | None = None
        self._email: str = os.getenv("INTERNSHALA_EMAIL", "")
        self._password: str = os.getenv("INTERNSHALA_PASSWORD", "")

        logger.info(
            f"[Orchestrator] Initialised — dry_run={self.dry_run}"
        )

    # ------------------------------------------------------------------
    # Phase 0: GitHub Context  (runs once per session)
    # ------------------------------------------------------------------

    def _get_llm_config(self) -> tuple[dict[str, Any], str]:
        """Resolve LLM provider, model, and API key from the environment."""
        provider = os.getenv("LLM_PROVIDER", "nvidia").lower()
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
            "ollama"      : "",
        }
        api_key = key_map.get(provider, "")
        config = {
            "llm_model_type": provider,
            "llm_model"     : os.getenv("LLM_MODEL", model_map.get(provider, "moonshotai/kimi-k2.6")),
        }
        return config, api_key

    async def _fetch_github_context(self) -> None:
        """Fetch live GitHub portfolio once. Reused for all jobs this session."""
        logger.info("[Orchestrator] Phase 0: Fetching GitHub portfolio context...")
        config, api_key = self._get_llm_config()
        try:
            self._github_context = fetch_github_context()
            self._answerer = SaihawkAnswerer(config, api_key)
            logger.success(
                f"[Orchestrator] GitHub context ready — "
                f"{len(self._github_context)} chars."
            )
        except Exception as exc:
            logger.warning(
                f"[Orchestrator] GitHub fetch failed: {exc}. "
                "Answerer will use baseline stack only."
            )
            self._github_context = (
                "GitHub context unavailable. Use baseline stack only: "
                "Python, Java, C++, Docker, AWS, GCP, ML, DevOps."
            )
            self._answerer = SaihawkAnswerer(config, api_key)


    # ------------------------------------------------------------------
    # Phase 1: Login Verification
    # ------------------------------------------------------------------

    async def login(self) -> None:
        """
        Verify that the injected BrowserContext is already authenticated.
        The context should have been loaded with internshala_session.json.
        """
        logger.info("[Orchestrator] Phase 1: Verifying Internshala session...")
        page = await self.new_page()

        try:
            await page.goto(
                "https://internshala.com/student/dashboard",
                wait_until="domcontentloaded",
                timeout=20_000
            )
            
            # Check if we are redirected to the login page or if the dashboard loads
            current_url = page.url
            if "login" in current_url:
                logger.error("[Orchestrator] Redirected to login page. Session is invalid or missing.")
                raise RuntimeError(
                    "Session invalid. Please run 'python login_manager.py' to authenticate "
                    "and generate a valid internshala_session.json file."
                )
                
            # Double check for a logged-in UI element (e.g., profile container)
            await page.wait_for_selector(
                SELECTORS["login_success_indicator"], timeout=10_000
            )
            logger.success("[Orchestrator] Session verification successful. Logged in.")

        except PlaywrightTimeout:
            logger.warning(
                "[Orchestrator] Dashboard verification timed out. The session might be expired, "
                "or Internshala's DOM has changed."
            )
            # We don't hard crash here, as the user might be on a different valid page,
            # but we log a strong warning.
        finally:
            await self.close_page()

    # ------------------------------------------------------------------
    # Phase 2: Search & Collect Links
    # ------------------------------------------------------------------

    async def search_jobs(self, search_urls: list[str]) -> list[dict[str, Any]]:
        """
        Paginate through all search URLs and collect unique job-posting URLs.
        Returns a list of minimal dicts: [{"url": "..."}, ...]
        """
        logger.info(
            f"[Orchestrator] Phase 2: Scraping {len(search_urls)} search URL(s)..."
        )
        page = await self.new_page()
        all_links: list[str] = []

        for url in search_urls:
            links = await extract_job_links(page, url)
            all_links.extend(links)

        # Deduplicate while preserving order
        unique_links = list(dict.fromkeys(all_links))
        logger.info(
            f"[Orchestrator] Collected {len(unique_links)} unique job link(s)."
        )
        return [{"url": link} for link in unique_links]

    # ------------------------------------------------------------------
    # Phase 3 + 4: Per-Job Pipeline
    # ------------------------------------------------------------------

    async def process_single_job(self, job_url: str) -> bool:
        """
        End-to-end pipeline for one job posting:
            Scrape → Build prompt → Generate answers → Fill form → (Submit)

        Returns True on successful fill/submit, False on graceful skip/failure.
        """
        page = await self.new_page()

        try:
            # --- Phase 2b: Scrape individual posting ---
            job_data = await extract_job_details(page, job_url)
            if job_data is None:
                logger.info(
                    f"[Orchestrator] Skipped (no data / already applied): {job_url}"
                )
                return False

            title   = job_data["title"]
            company = job_data["company"]

            # --- Phase 3a: Build prompt ---
            job_ctx = job_context_from_dict(job_data)
            package = build_prompt(self._github_context, job_ctx)
            logger.debug(
                f"[Orchestrator] Prompt ready for '{title}' @ '{company}' — "
                f"{package.question_count} question(s)."
            )

            # --- Phase 3b: Generate LLM answers ---
            if job_data.get("questions"):
                assert self._answerer is not None
                answers = self._answerer.answer(package)
                logger.info(
                    f"[Orchestrator] Generated {len(answers)} answer(s) for "
                    f"'{title}' @ '{company}'."
                )
            else:
                logger.info(
                    f"[Orchestrator] No assessment questions for '{title}'. "
                    "Proceeding with direct submission."
                )
                answers = []

            # --- Phase 4: Fill + (optionally) submit the form ---
            success = await complete_application(
                page=page,
                answers=answers,
                dry_run=self.dry_run,
            )

            status = "Applied" if success else "Submit failed"
            logger.info(
                f"[Orchestrator] {status}: '{title}' @ '{company}'"
            )
            return success

        except Exception as exc:
            logger.error(f"[Orchestrator] Pipeline error for {job_url}: {exc}")
            return False

        finally:
            await self.close_page()

    # ------------------------------------------------------------------
    # Main Entrypoint (injects Phase 0 before base class run loop)
    # ------------------------------------------------------------------

    async def run(self, search_urls: list[str]) -> None:  # type: ignore[override]
        """
        Full pipeline:
            GitHub context (once) → Login → Scrape all → Apply each job
        """
        await self._fetch_github_context()
        await super().run(search_urls)
