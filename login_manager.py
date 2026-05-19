"""
login_manager.py
==================================================
Utility script to authenticate with Internshala manually and
save the browser session (cookies, local storage) to a JSON file.

Run this script ONCE before running main.py to avoid bot-detection
captchas during the automated pipeline.

Usage:
    python login_manager.py
"""

import asyncio
from playwright.async_api import async_playwright

SESSION_FILE = "internshala_session.json"

async def main():
    print("=========================================================")
    print("Internshala Session Manager")
    print("=========================================================")
    print("A browser will open shortly.")
    print("1. Log in to Internshala manually.")
    print("2. Solve any captchas if required.")
    print("3. Once you see your dashboard, close the browser window.")
    print("=========================================================\n")

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=False)
        context = await browser.new_context()
        page = await context.new_page()

        await page.goto("https://internshala.com/login")

        print("Waiting for you to log in and close the browser...")
        
        # Wait until the page is closed by the user
        try:
            await page.wait_for_event("close", timeout=0)  # Wait indefinitely
        except Exception as e:
            print(f"Browser closed or interrupted: {e}")

        # Save the session state
        await context.storage_state(path=SESSION_FILE)
        print(f"\n[SUCCESS] Session saved to {SESSION_FILE}")
        print("You can now run main.py!")

if __name__ == "__main__":
    asyncio.run(main())
