import os
import re
import time
import random
import logging
import json
from pathlib import Path

# ---------------- CONFIG ----------------

ZOHO_EMAIL        = os.environ.get("ZOHO_EMAIL", "your_zoho_email@company.com")
ZOHO_PASSWORD     = os.environ.get("ZOHO_PASSWORD", "your_zoho_password")

OUTLOOK_EMAIL     = os.environ.get("OUTLOOK_EMAIL", "your_outlook_email@outlook.com")
OUTLOOK_APP_PASS  = os.environ.get("OUTLOOK_APP_PASSWORD", "your_app_password")

SESSION_FILE      = Path(__file__).parent / "session.json"
LOG_FILE          = Path(__file__).parent / "bot.log"

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


def fetch_otp_from_outlook(timeout=60):
    """Poll Outlook inbox for a 6-digit OTP email."""
    try:
        from imapclient import IMAPClient
        import pyzmail
    except ImportError:
        logging.error("Missing packages: pip install imapclient pyzmail36")
        return None

    logging.info("Connecting to Outlook to fetch OTP...")
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            with IMAPClient("outlook.office365.com", ssl=True) as server:
                server.login(OUTLOOK_EMAIL, OUTLOOK_APP_PASS)
                server.select_folder("INBOX")
                messages = server.search(["UNSEEN"])

                for uid, data in server.fetch(messages, ["RFC822"]).items():
                    msg = pyzmail.PyzMessage.factory(data[b"RFC822"])
                    body = ""
                    if msg.text_part:
                        body = msg.text_part.get_payload().decode(errors="ignore")
                    elif msg.html_part:
                        body = msg.html_part.get_payload().decode(errors="ignore")

                    otp_match = re.search(r"\b(\d{6})\b", body)
                    if otp_match:
                        logging.info(f"OTP found: {otp_match.group(1)}")
                        return otp_match.group(1)
        except Exception as e:
            logging.warning(f"OTP fetch error: {e}")

        logging.info("OTP not yet received, retrying in 5s...")
        time.sleep(5)

    logging.error("OTP not received within timeout.")
    return None


def login_and_save_session(playwright):
    """Full login flow with OTP. Returns browser context or None."""
    from playwright.sync_api import sync_playwright

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
        page.fill("input[name='login_id']", ZOHO_EMAIL)
        page.wait_for_timeout(random.randint(500, 1000))
        page.click("#nextbtn")
        page.wait_for_timeout(random.randint(2000, 3000))

        # Enter password
        page.fill("input[name='password']", ZOHO_PASSWORD)
        page.wait_for_timeout(random.randint(500, 1000))
        page.click("#nextbtn")
        page.wait_for_timeout(random.randint(3000, 5000))

        # Check if OTP page appeared
        if page.is_visible("input[type='tel']") or "tfa" in page.url or "mfa" in page.url:
            logging.info("OTP prompt detected, fetching OTP...")
            otp = fetch_otp_from_outlook()

            if not otp:
                logging.error("Could not get OTP. Login aborted.")
                browser.close()
                return None, None

            page.fill("input[type='tel']", otp)
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
        page.goto("https://people.zoho.com/home", timeout=30000)
        page.wait_for_timeout(random.randint(4000, 6000))

        # Take screenshot for proof
        screenshot_path = Path(__file__).parent / f"{action}_{int(time.time())}.png"
        page.screenshot(path=str(screenshot_path))
        logging.info(f"Screenshot saved: {screenshot_path}")

        if action == "checkin":
            # Try common selectors
            selectors = [
                "text=Check In",
                "button:has-text('Check In')",
                "[data-action='checkin']",
                ".checkin-btn",
            ]
            for sel in selectors:
                if page.is_visible(sel):
                    page.click(sel)
                    logging.info("✅ Check-In clicked successfully")
                    page.wait_for_timeout(random.randint(2000, 3000))
                    return True

        elif action == "checkout":
            selectors = [
                "text=Check Out",
                "button:has-text('Check Out')",
                "[data-action='checkout']",
                ".checkout-btn",
            ]
            for sel in selectors:
                if page.is_visible(sel):
                    page.click(sel)
                    logging.info("✅ Check-Out clicked successfully")
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
        page.goto("https://people.zoho.com/home", timeout=30000)
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
