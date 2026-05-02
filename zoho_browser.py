#!/usr/bin/env python3
"""
zoho_browser.py — Zoho People attendance via Playwright.

Exposes `run_attendance(action)` for the scheduler in app.py and keeps
a CLI entry point for one-off local runs / manual debugging.

Env vars:
  ZOHO_EMAIL, ZOHO_PASSWORD       (required)
  ZOHO_TOTP                       (preferred MFA source)
  OUTLOOK_EMAIL, OUTLOOK_PASSWORD (fallback MFA source via inbox poll)
  ACTION                          (CLI only: checkin | checkout)
"""

from __future__ import annotations

import logging
import os
import random
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

import pyotp
import pytz
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from outlook import fetch_zoho_mfa_code

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")
Action = Literal["checkin", "checkout"]

DEBUG_DIR = Path(os.environ.get("DEBUG_DIR", "/tmp/attendance-debug"))


def _load_config() -> dict:
    missing = [k for k in ("ZOHO_EMAIL", "ZOHO_PASSWORD") if not os.environ.get(k, "").strip()]
    if missing:
        raise RuntimeError(f"Missing env vars: {', '.join(missing)}")
    return {
        "zoho_email": os.environ["ZOHO_EMAIL"],
        "zoho_pass": os.environ["ZOHO_PASSWORD"],
        "totp_secret": os.environ.get("ZOHO_TOTP", "").strip(),
        "outlook_email": os.environ.get("OUTLOOK_EMAIL", "").strip(),
        "outlook_pass": os.environ.get("OUTLOOK_PASSWORD", "").strip(),
    }


def _save_debug(page, label: str) -> None:
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        path = DEBUG_DIR / f"{ts}-{label}.png"
        page.screenshot(path=str(path), full_page=True)
        log.error("Debug screenshot saved: %s", path)
    except Exception as exc:
        log.warning("Could not save debug screenshot: %s", exc)


def _do_attendance(cfg: dict, action: Action) -> str:
    """Single attempt. Returns a human-readable result string. Raises on failure."""
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        )
        page = ctx.new_page()
        try:
            log.info("Navigating to Zoho sign-in")
            page.goto(
                "https://accounts.zoho.in/signin?servicename=zohopeople",
                wait_until="networkidle",
                timeout=30_000,
            )

            page.wait_for_selector("#login_id", timeout=15_000)
            page.fill("#login_id", cfg["zoho_email"])
            page.click("button#nextbtn")

            password_selector = "#password, #relogin_password_input"
            page.wait_for_selector(password_selector, timeout=10_000)
            page.fill(password_selector, cfg["zoho_pass"])
            page.click("button#nextbtn")

            try:
                page.wait_for_selector(
                    "//*[@id='error_msg'] | //*[contains(text(), 'Incorrect password')]",
                    timeout=5_000,
                )
                log.warning("Password rejected; trying 'Sign in using email OTP' fallback")
                fallback = page.locator("text=Sign in using email OTP")
                if fallback.is_visible():
                    fallback.click()
                else:
                    raise RuntimeError("Login failed: incorrect password and no email-OTP fallback")
            except PWTimeout:
                pass

            otp_selector = (
                "input[placeholder*='OTP' i], input[placeholder*='code' i], "
                "input[name='code'], #otp_field, input[type='tel']"
            )
            try:
                page.wait_for_selector(otp_selector, timeout=10_000)
                log.info("MFA prompt detected")
                ignore_before_ms = int(time.time() * 1000)

                otp_code = ""
                if cfg["totp_secret"]:
                    otp_code = pyotp.TOTP(cfg["totp_secret"]).now()
                    log.info("Generated TOTP")
                elif cfg["outlook_email"] and cfg["outlook_pass"]:
                    log.info("Polling Outlook for Zoho code")
                    time.sleep(5)
                    otp_code = fetch_zoho_mfa_code(cfg["outlook_email"], ignore_before_ms)
                else:
                    raise RuntimeError("OTP required but no source configured")

                if not otp_code:
                    raise RuntimeError("OTP source returned empty code")
                page.fill(otp_selector, otp_code)
                page.keyboard.press("Enter")
                log.info("OTP submitted")

                try:
                    trust = "button:has-text('Trust'), button:has-text('Yes'), #trust_browser"
                    page.wait_for_selector(trust, timeout=5_000)
                    page.click(trust)
                    log.info("Trusted browser")
                except PWTimeout:
                    pass
            except PWTimeout:
                log.info("No MFA prompt")

            try:
                remind = "text='Remind me later', text='Remind Me Later'"
                page.wait_for_selector(remind, timeout=8_000)
                page.click(remind)
                log.info("Dismissed interstitial")
            except PWTimeout:
                pass

            page.wait_for_selector('//*[@id="ZPAtt_check_in_out"]', timeout=30_000)
            button = page.locator('//*[@id="ZPAtt_check_in_out"]')
            text = button.inner_text().strip().lower()
            log.info("Widget text: %s", text.replace("\n", " "))

            if action == "checkin":
                if "check-out" in text or "checkout" in text:
                    return "already-checked-in"
                time.sleep(3)
                button.click(delay=200)
                time.sleep(5)
                return "checked-in"
            else:
                if "check-in" in text or "checkin" in text:
                    return "already-checked-out"
                time.sleep(3)
                button.click(delay=200)
                time.sleep(5)
                return "checked-out"

        except Exception as exc:
            _save_debug(page, action)
            raise
        finally:
            try:
                browser.close()
            except Exception:
                pass


def run_attendance(
    action: Action,
    *,
    retries: int = 3,
    jitter_seconds: int = 60,
    skip_weekend_check: bool = False,
) -> str:
    """Public entry point used by the scheduler.

    Skips on weekends, applies a small humanising jitter, retries transient
    failures with exponential backoff. Returns the result string from the
    underlying attempt. Pass ``skip_weekend_check=True`` for forced manual
    smoke tests on Sat/Sun.
    """
    if action not in ("checkin", "checkout"):
        raise ValueError(f"Invalid action: {action}")

    if not skip_weekend_check and datetime.now(IST).weekday() >= 5:
        log.info("Weekend — skipping %s", action)
        return "weekend-skip"

    cfg = _load_config()

    if jitter_seconds > 0:
        delay = random.randint(0, jitter_seconds)
        log.info("Humanising jitter: sleeping %ds before %s", delay, action)
        time.sleep(delay)

    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            log.info("Attendance attempt %d/%d for %s", attempt, retries, action)
            result = _do_attendance(cfg, action)
            log.info("Result: %s", result)
            return result
        except Exception as exc:
            last_error = exc
            log.error("Attempt %d failed: %s", attempt, exc)
            if attempt < retries:
                backoff = 10 * (2 ** (attempt - 1))
                log.info("Retrying in %ds", backoff)
                time.sleep(backoff)

    raise RuntimeError(f"All {retries} attempts failed: {last_error}") from last_error


def _cli() -> None:
    action = os.environ.get("ACTION", "").lower()
    if action not in ("checkin", "checkout"):
        hour = datetime.now(IST).hour
        action = "checkin" if 6 <= hour < 17 else "checkout"
        log.info("Auto-detected action from IST hour %d: %s", hour, action)
    try:
        run_attendance(action, jitter_seconds=0)
    except Exception as exc:
        log.error("Run failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
