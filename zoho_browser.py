#!/usr/bin/env python3
"""
zoho_browser.py — Zoho People attendance via browser automation using App Password.

GitHub Secrets required:
  ZOHO_EMAIL       – your Zoho login email
  ZOHO_PASSWORD    – your Zoho App Password (bypasses OTP)

Optional:
  ACTION           – checkin | checkout (auto-detected from UTC hour if omitted)
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone
import pytz
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# ── Config ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    required = ["ZOHO_EMAIL", "ZOHO_PASSWORD"]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        log.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    
    return {
        "zoho_email":  os.environ["ZOHO_EMAIL"],
        "zoho_pass":   os.environ["ZOHO_PASSWORD"],
        "action":      os.environ.get("ACTION", "").lower(),
    }

# ── Browser Automation ────────────────────────────────────────────────────────
def run(cfg: dict, action: str):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx     = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()

        try:
            # ── 1. Navigate ───────────────────────────────────────────────
            log.info("Navigating to Zoho Sign In …")
            page.goto("https://accounts.zoho.in/signin?servicename=zohopeople", wait_until="networkidle", timeout=30000)

            # ── 2. Enter email ────────────────────────────────────────────
            page.wait_for_selector("#login_id", timeout=15000)
            page.fill("#login_id", cfg["zoho_email"])
            page.click("button#nextbtn")
            log.info("Email entered and Next clicked.")

            # ── 3. Enter App Password ──────────────────────────────────────
            # Since this is an App Password, Zoho trusts it and skips OTP/Device Verification!
            page.wait_for_selector("#password", timeout=10000)
            page.fill("#password", cfg["zoho_pass"])
            page.click("button#nextbtn")
            log.info("App Password submitted and Sign in clicked.")

            # ── 4. Wait for dashboard and widget ──────────────────────────
            page.wait_for_selector('//*[@id="ZPAtt_check_in_out"]', timeout=30000)
            log.info("Logged in successfully. Attendance widget found.")

            # ── 5. Click attendance button ────────────────────────────────
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
            try:
                page.screenshot(path="debug_screenshot.png", full_page=True)
                log.error("Debug screenshot saved.")
            except Exception:
                pass
            log.error(f"Automation failed: {e}")
            browser.close()
            sys.exit(1)

        browser.close()


# ── Entry point ───────────────────────────────────────────────────────────────
def main():
    cfg = load_config()

    action = cfg["action"]
    if action not in ("checkin", "checkout"):
        utc_h  = datetime.utcnow().hour
        action = "checkin" if 6 <= utc_h <= 7 else "checkout" if 16 <= utc_h <= 17 else ""
        if not action:
            log.error(f"Cannot auto-detect action at UTC hour {utc_h}. Set ACTION explicitly.")
            sys.exit(1)
        log.info(f"Auto-detected action: {action}")

    if datetime.now(IST).weekday() >= 5:
        log.info("Weekend — skipping.")
        sys.exit(0)

    log.info(f"Action : {action}")
    log.info(f"Email  : {cfg['zoho_email']}")
    run(cfg, action)


if __name__ == "__main__":
    main()
