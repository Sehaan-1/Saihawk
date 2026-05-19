"""
main.py
==================================================
Saihawk: The Internshala Autonomous Application Bot.
==================================================

Usage:
    # Authenticate first!
    python login_manager.py
    
    # Run the bot
    python main.py

    # Or specify search URLs via CLI:
    python main.py --url "https://internshala.com/internships/python-internship/"
"""

import argparse
import asyncio
import os
import sys

from loguru import logger
from playwright.async_api import async_playwright

from src.platforms.internshala.orchestrator import IntershalaPipeline

SESSION_FILE = "internshala_session.json"

# Replace this with an active Internshala search URL if not using the CLI
DEFAULT_TEST_URL = "https://internshala.com/internships/python-internship/"

async def main():
    parser = argparse.ArgumentParser(description="Saihawk Internshala Bot")
    parser.add_argument(
        "--url", 
        type=str, 
        default=DEFAULT_TEST_URL,
        help="Internshala search URL or specific job URL to apply to."
    )
    args = parser.parse_args()

    # Verify session file exists
    if not os.path.exists(SESSION_FILE):
        logger.error(f"Session file '{SESSION_FILE}' not found.")
        logger.error("Please run 'python login_manager.py' first to authenticate.")
        sys.exit(1)

    # Determine headless mode from .env (PLAYWRIGHT_HEADFUL=true means headless=False)
    # The user specifically requested Headful Mode in the checklist.
    is_headful = os.getenv("PLAYWRIGHT_HEADFUL", "true").strip().lower() == "true"
    
    logger.info("=====================================================")
    logger.info(f"Starting Saihawk Pipeline")
    logger.info(f"Target URL: {args.url}")
    logger.info(f"Headful Mode: {is_headful}")
    logger.info("=====================================================")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=not is_headful)
        
        # Inject the saved authentication state
        context = await browser.new_context(storage_state=SESSION_FILE)
        
        # Initialize the pipeline orchestrator
        pipeline = IntershalaPipeline(context, config={})
        
        try:
            # The run() method handles Phase 0 (GitHub context), Phase 1 (Login verification),
            # Phase 2 (Scraping), Phase 3 (LLM answers), and Phase 4 (Submission).
            await pipeline.run(search_urls=[args.url])
        except Exception as e:
            logger.exception(f"Pipeline crashed: {e}")
        finally:
            await context.close()
            await browser.close()

if __name__ == "__main__":
    # Ensure loguru prints to stdout
    logger.remove()
    logger.add(sys.stdout, format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <level>{message}</level>")
    
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Pipeline interrupted by user.")
