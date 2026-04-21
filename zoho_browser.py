#!/usr/bin/env python3
"""
zoho_browser.py — Zoho People attendance via browser automation using App Password or TOTP.

GitHub Secrets required:
  ZOHO_EMAIL       – your Zoho login email
  ZOHO_PASSWORD    – your primary Zoho password
  ZOHO_TOTP        – your TOTP secret (if MFA is enabled)

Optional:
  ACTION           – checkin | checkout (auto-detected from UTC hour if omitted)
"""

import os
import sys
import time
import logging
from datetime import datetime, timezone
import pytz
import pyotp
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from outlook import fetch_zoho_mfa_code

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
        "zoho_email":     os.environ["ZOHO_EMAIL"],
        "zoho_pass":      os.environ["ZOHO_PASSWORD"],
        "totp_secret":    os.environ.get("ZOHO_TOTP", ""),
        "outlook_email":  os.environ.get("OUTLOOK_EMAIL", ""),
        "outlook_pass":   os.environ.get("OUTLOOK_PASSWORD", ""),
        "action":         os.environ.get("ACTION", "").lower(),
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

            # ── 3. Enter Primary Password ─────────────────────────────────
            password_selector = "#password, #relogin_password_input"
            page.wait_for_selector(password_selector, timeout=10000)
            page.fill(password_selector, cfg["zoho_pass"])
            page.click("button#nextbtn")
            log.info("Password submitted and Sign in clicked.")

            # Check for incorrect password error
            try:
                error_selector = "//*[@id='error_msg'] | //*[contains(text(), 'Incorrect password')]"
                page.wait_for_selector(error_selector, timeout=5000)
                log.warning("Incorrect password detected! Attempting 'Sign in using email OTP' fallback ...")
                
                otp_login_link = "text=Sign in using email OTP"
                if page.locator(otp_login_link).is_visible():
                    page.click(otp_login_link)
                    log.info("Clicked 'Sign in using email OTP'. Waiting for OTP prompt ...")
                else:
                    log.error("Password failed and 'Sign in using email OTP' link not found.")
                    raise RuntimeError("Login failed: Incorrect password and no fallback available.")
            except PWTimeout:
                # No password error, proceed normally
                pass

            # ── 4. Enter OTP (TOTP or Email) ──────────────────────────────
            otp_selector = "input[placeholder*='OTP' i], input[placeholder*='code' i], input[name='code'], #otp_field, input[type='tel']"
            try:
                # Wait briefly to see if OTP prompt appears
                page.wait_for_selector(otp_selector, timeout=10000)
                log.info("MFA / OTP screen detected.")

                ignore_before_ms = int(time.time() * 1000)

                otp_code = ""
                if cfg["totp_secret"]:
                    log.info("Generating TOTP code ...")
                    totp = pyotp.TOTP(cfg["totp_secret"])
                    otp_code = totp.now()
                elif cfg["outlook_email"] and cfg["outlook_pass"]:
                    log.info(f"Polling Outlook ({cfg['outlook_email']}) for Zoho verification code ...")
                    # Give it a few seconds for the email to be sent/delivered
                    time.sleep(5)
                    otp_code = fetch_zoho_mfa_code(cfg["outlook_email"], ignore_before_ms)
                else:
                    log.error("OTP required but neither ZOHO_TOTP nor Outlook credentials provided.")
                    raise RuntimeError("OTP required but no source configured.")

                if otp_code:
                    page.fill(otp_selector, otp_code)
                    page.keyboard.press("Enter")
                    log.info(f"OTP {otp_code} submitted.")
                    
                    # Check for "Trust this browser" screen
                    try:
                        trust_selector = "button:has-text('Trust'), button:has-text('Yes'), #trust_browser"
                        page.wait_for_selector(trust_selector, timeout=5000)
                        log.info("Trust browser prompt detected. Clicking Yes/Trust...")
                        page.click(trust_selector)
                    except PWTimeout:
                        pass
                else:
                    log.error("Could not retrieve OTP code.")
                    raise RuntimeError("OTP retrieval failed.")

            except PWTimeout:
                log.info("No MFA prompt detected — continuing.")

            # ── 5. Wait for dashboard and widget ──────────────────────────
            page.wait_for_selector('//*[@id="ZPAtt_check_in_out"]', timeout=30000)
            log.info("Logged in successfully. Attendance widget found.")

            # ── 6. Click attendance button ────────────────────────────────
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
