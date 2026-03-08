import os
import time
import random
import logging
from pathlib import Path

# ---------------- CONFIG ----------------

ZOHO_EMAIL    = os.environ.get("ZOHO_EMAIL", "your_zoho_email@company.com")
ZOHO_PASSWORD = os.environ.get("ZOHO_PASSWORD", "your_zoho_password")

SESSION_FILE  = Path(__file__).parent / "session.json"
LOG_FILE      = Path(__file__).parent / "bot.log"

ZOHO_PEOPLE_URL = "https://people.zoho.in"


def get_action():
    """Read action at runtime so server.py can override it dynamically."""
    return os.environ.get("ZOHO_ACTION", "checkin")

# ----------------------------------------

logging.basicConfig(
    filename=str(LOG_FILE),
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# Also log to terminal
console = logging.StreamHandler()
console.setLevel(logging.INFO)
logging.getLogger().addHandler(console)


def login_and_save_session(playwright):
    """Full login flow. Returns (browser, context) or (None, None)."""
    logging.info("Starting fresh login...")
    browser = playwright.chromium.launch(headless=True)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/121.0.0.0 Safari/537.36"
    )
    page = context.new_page()

    try:
        page.goto("https://accounts.zoho.com/signin", timeout=30000)
        page.wait_for_timeout(random.randint(1000, 2000))

        # Enter email
        page.fill("input#login_id", ZOHO_EMAIL)
        page.wait_for_timeout(random.randint(500, 1000))
        page.click("#nextbtn")
        page.wait_for_timeout(random.randint(2000, 3000))

        # Enter password
        page.fill("input#password", ZOHO_PASSWORD)
        page.wait_for_timeout(random.randint(500, 1000))
        page.click("#nextbtn")
        page.wait_for_timeout(random.randint(5000, 8000))

        # Save session
        context.storage_state(path=str(SESSION_FILE))
        logging.info(f"Session saved to {SESSION_FILE}")
        return browser, context

    except Exception as e:
        logging.error(f"Login failed: {e}")
        browser.close()
        return None, None


def perform_attendance(page):
    """Navigate to Zoho People and click check-in or check-out."""
    action = get_action()
    try:
        logging.info(f"Navigating to Zoho People for action: {action}")
        page.goto(ZOHO_PEOPLE_URL, timeout=30000)
        page.wait_for_timeout(random.randint(4000, 6000))

        # Take screenshot for proof (before clicking)
        screenshot_path = Path(__file__).parent / f"{action}_{int(time.time())}.png"
        page.screenshot(path=str(screenshot_path))
        logging.info(f"Screenshot saved: {screenshot_path}")

        # The check-in / check-out is a single toggle button with ID ZPAtt_check_in_out
        btn_selector = "#ZPAtt_check_in_out"

        if page.is_visible(btn_selector):
            btn_text = page.inner_text(btn_selector).strip().lower()
            logging.info(f"Attendance button found — text: '{btn_text}'")

            if action == "checkin" and "check-in" in btn_text:
                page.click(btn_selector)
                logging.info("✅ Check-In clicked successfully")
                page.wait_for_timeout(random.randint(2000, 3000))
                return True
            elif action == "checkout" and "check-out" in btn_text:
                page.click(btn_selector)
                logging.info("✅ Check-Out clicked successfully")
                page.wait_for_timeout(random.randint(2000, 3000))
                return True
            else:
                logging.warning(
                    f"Button says '{btn_text}' but action is '{action}'. "
                    f"Already {'checked in' if action == 'checkin' else 'checked out'}?"
                )
                return False
        else:
            # Fallback selectors
            fallback_selectors = [
                "text=Check-in",
                "text=Check-out",
                "text=Check In",
                "text=Check Out",
                "button:has-text('Check')",
            ]
            for sel in fallback_selectors:
                if page.is_visible(sel):
                    sel_text = page.inner_text(sel).strip().lower()
                    if action == "checkin" and "in" in sel_text:
                        page.click(sel)
                        logging.info(f"✅ Check-In clicked via fallback: {sel}")
                        page.wait_for_timeout(random.randint(2000, 3000))
                        return True
                    elif action == "checkout" and "out" in sel_text:
                        page.click(sel)
                        logging.info(f"✅ Check-Out clicked via fallback: {sel}")
                        page.wait_for_timeout(random.randint(2000, 3000))
                        return True

        logging.warning(f"Could not find {action} button. Check screenshot.")
        return False

    except Exception as e:
        logging.error(f"Attendance action failed: {e}")
        return False


def run_bot():
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = None
        context = None

        # Try loading saved session
        if SESSION_FILE.exists():
            logging.info("Loading saved session...")
            try:
                browser = p.chromium.launch(headless=True)
                context = browser.new_context(
                    storage_state=str(SESSION_FILE),
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                               "AppleWebKit/537.36 (KHTML, like Gecko) "
                               "Chrome/121.0.0.0 Safari/537.36"
                )
            except Exception as e:
                logging.warning(f"Could not load session: {e}")
                if browser:
                    browser.close()
                browser, context = login_and_save_session(p)
        else:
            logging.info("No saved session. Fresh login required.")
            browser, context = login_and_save_session(p)

        if context is None:
            logging.error("❌ Could not establish session. Exiting.")
            return

        page = context.new_page()

        # Check if session is still valid
        page.goto(ZOHO_PEOPLE_URL, timeout=30000)
        page.wait_for_timeout(4000)

        if "signin" in page.url or "accounts.zoho" in page.url:
            logging.info("Session expired. Re-logging in...")
            browser.close()
            browser, context = login_and_save_session(p)
            if context is None:
                logging.error("❌ Re-login failed.")
                return
            page = context.new_page()

        success = perform_attendance(page)

        if browser:
            browser.close()

        if success:
            logging.info(f"🎯 Bot completed: {get_action().upper()}")
        else:
            logging.error(f"❌ Bot failed: {get_action().upper()}")


if __name__ == "__main__":
    # Random human-like delay (30s–3min)
    delay = random.randint(30, 180)
    logging.info(f"⏳ Waiting {delay}s before starting (human delay)...")
    time.sleep(delay)
    run_bot()
