#!/usr/bin/env python3
"""
zoho_browser.py — Zoho People attendance via browser automation.
Logs in with email/password and clicks Check-in or Check-out button.

Usage:
  ACTION=checkin  python zoho_browser.py
  ACTION=checkout python zoho_browser.py
"""

import os
import sys
import time
import logging
from datetime import datetime
import pytz
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ── Config from env ───────────────────────────────────────────────────────────
def cfg():
    missing = [k for k in ("ZOHO_EMAIL", "ZOHO_PASSWORD") if not os.environ.get(k)]
    if missing:
        log.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    return {
        "email":    os.environ["ZOHO_EMAIL"],
        "password": os.environ["ZOHO_PASSWORD"],
        "action":   os.environ.get("ACTION", "").lower(),
        "totp":     os.environ.get("ZOHO_TOTP", ""),   # optional: TOTP secret if MFA enabled
    }


# ── Main automation ───────────────────────────────────────────────────────────
def run(action: str, email: str, password: str, totp_secret: str = ""):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        ctx     = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        )
        page = ctx.new_page()

        try:
            # ── 1. Login ──────────────────────────────────────────────────
            log.info("Navigating to Zoho Sign In …")
            page.goto("https://accounts.zoho.in/signin?servicename=zohopeople", wait_until="networkidle", timeout=30000)

            # Fill email
            page.wait_for_selector("#login_id", timeout=15000)
            page.fill("#login_id", email)
            page.click("button#nextbtn")
            log.info("Email entered and Next clicked.")

            # Fill password
            page.wait_for_selector("#password", timeout=10000)
            page.fill("#password", password)
            page.click("button#nextbtn")
            log.info("Password entered and Sign in clicked.")

            # Handle MFA/TOTP if present
            if totp_secret:
                try:
                    page.wait_for_selector("input[name='code'], input[placeholder*='code' i]",
                                           timeout=8000)
                    import pyotp
                    totp_code = pyotp.TOTP(totp_secret).now()
                    page.fill("input[name='code'], input[placeholder*='code' i]", totp_code)
                    page.keyboard.press("Enter")
                    log.info("TOTP entered.")
                except PWTimeout:
                    log.info("No MFA prompt detected — continuing.")

            # Wait for People dashboard and the attendance widget
            page.wait_for_selector('//*[@id="ZPAtt_check_in_out"]', timeout=30000)
            log.info("Logged in successfully. Attendance widget found.")

            # ── 3. Perform check-in or check-out ─────────────────────────
            log.info(f"Checking current state for: {action} …")
            
            button_locator = page.locator('//*[@id="ZPAtt_check_in_out"]')
            btn_text = button_locator.inner_text().strip().lower()
            log.info(f"Widget text: {btn_text.replace(chr(10), ' ')}")

            if action == "checkin":
                if "check-out" in btn_text or "checkout" in btn_text:
                    log.info("Already checked-in (Check-out button is visible). Skipping.")
                    sys.exit(0)
                else:
                    log.info("Waiting for JS to attach before clicking …")
                    time.sleep(3)
                    log.info("Clicking Check-in button …")
                    button_locator.click(delay=200)
                    time.sleep(5)
                    log.info("Check-in completed successfully!")
            else:
                if "check-in" in btn_text or "checkin" in btn_text:
                    log.info("Already checked-out (Check-in button is visible). Skipping.")
                    sys.exit(0)
                else:
                    log.info("Waiting for JS to attach before clicking …")
                    time.sleep(3)
                    log.info("Clicking Check-out button …")
                    button_locator.click(delay=200)
                    time.sleep(5)
                    log.info("Check-out completed successfully!")

        except Exception as e:
            # Save screenshot for debugging
            try:
                page.screenshot(path="debug_screenshot.png", full_page=True)
                log.error("Screenshot saved to debug_screenshot.png")
            except Exception:
                pass
            log.error(f"Automation failed: {e}")
            browser.close()
            sys.exit(1)

        browser.close()

# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    c = cfg()

    # Validate action
    action = c["action"]
    if action not in ("checkin", "checkout"):
        # Auto-detect from UTC hour
        utc_h = datetime.utcnow().hour
        action = "checkin" if 6 <= utc_h <= 7 else "checkout" if 16 <= utc_h <= 17 else ""
        if not action:
            log.error(f"Cannot auto-detect action at UTC hour {utc_h}. Set ACTION=checkin or checkout.")
            sys.exit(1)
        log.info(f"Auto-detected action: {action}")

    # Check weekend
    now_ist = datetime.now(IST)
    if now_ist.weekday() >= 5:
        log.info(f"Today is {now_ist.strftime('%A')} — skipping (weekend).")
        sys.exit(0)

    log.info(f"Action : {action}")
    log.info(f"Email  : {c['email']}")
    run(action, c["email"], c["password"], c["totp"])


if __name__ == "__main__":
    main()
