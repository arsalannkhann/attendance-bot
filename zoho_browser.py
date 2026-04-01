#!/usr/bin/env python3
"""
zoho_browser.py — Zoho People attendance via browser automation.
Fetches login OTP from Microsoft 365 inbox using Microsoft Graph API.

GitHub Secrets required:
  ZOHO_EMAIL       – your Zoho login email
  ZOHO_PASSWORD    – your Zoho password
  MS_TENANT_ID     – Azure Directory (tenant) ID
  MS_CLIENT_ID     – Azure Application (client) ID
  MS_CLIENT_SECRET – Azure app client secret value
  M365_EMAIL       – your work email that receives the Zoho OTP

Optional:
  ACTION           – checkin | checkout (auto-detected from UTC hour if omitted)
"""

import os
import sys
import re
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
import pytz
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


# ── Config ────────────────────────────────────────────────────────────────────
def load_config() -> dict:
    required = [
        "ZOHO_EMAIL", "ZOHO_PASSWORD",
        "MS_TENANT_ID", "MS_CLIENT_ID", "MS_CLIENT_SECRET",
        "M365_EMAIL",
    ]
    missing = [k for k in required if not os.environ.get(k, "").strip()]
    if missing:
        log.error(f"Missing env vars: {', '.join(missing)}")
        sys.exit(1)
    return {
        "zoho_email":  os.environ["ZOHO_EMAIL"],
        "zoho_pass":   os.environ["ZOHO_PASSWORD"],
        "tenant_id":   os.environ["MS_TENANT_ID"],
        "client_id":   os.environ["MS_CLIENT_ID"],
        "client_secret": os.environ["MS_CLIENT_SECRET"],
        "m365_email":  os.environ["M365_EMAIL"],
        "action":      os.environ.get("ACTION", "").lower(),
    }


# ── Microsoft Graph: get access token ────────────────────────────────────────
def get_graph_token(tenant_id: str, client_id: str, client_secret: str) -> str:
    url  = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type":    "client_credentials",
        "client_id":     client_id,
        "client_secret": client_secret,
        "scope":         "https://graph.microsoft.com/.default",
    }
    resp = requests.post(url, data=data, timeout=15)
    resp.raise_for_status()
    body = resp.json()
    if "access_token" not in body:
        raise RuntimeError(f"Graph token error: {body}")
    log.info("Microsoft Graph token obtained.")
    return body["access_token"]


# ── Microsoft Graph: poll inbox for OTP ──────────────────────────────────────
def fetch_zoho_otp_graph(cfg: dict, triggered_at: datetime,
                          max_wait: int = 90) -> str:
    """
    Poll M365 inbox via Microsoft Graph for a Zoho OTP email
    received after triggered_at. Returns the 6-digit OTP string.
    """
    token    = get_graph_token(cfg["tenant_id"], cfg["client_id"], cfg["client_secret"])
    headers  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    mailbox  = cfg["m365_email"]

    # ISO 8601 filter — only emails received after login was triggered
    since    = (triggered_at - timedelta(seconds=30)).strftime("%Y-%m-%dT%H:%M:%SZ")

    deadline = time.time() + max_wait
    attempt  = 0

    while time.time() < deadline:
        attempt += 1
        try:
            # Search inbox for unread messages from Zoho after triggered_at
            url = (
                f"{GRAPH_BASE}/users/{mailbox}/messages"
                f"?$filter=isRead eq false"
                f" and receivedDateTime ge {since}"
                f" and contains(from/emailAddress/address,'zoho')"
                f"&$select=id,subject,body,receivedDateTime,from"
                f"&$orderby=receivedDateTime desc"
                f"&$top=5"
            )
            resp = requests.get(url, headers=headers, timeout=15)

            if resp.status_code == 401:
                log.warning("Graph token expired — refreshing …")
                token   = get_graph_token(cfg["tenant_id"], cfg["client_id"], cfg["client_secret"])
                headers["Authorization"] = f"Bearer {token}"
                continue

            resp.raise_for_status()
            messages = resp.json().get("value", [])
            log.info(f"Attempt {attempt}: {len(messages)} unread Zoho email(s) found.")

            for msg in messages:
                body_content = msg.get("body", {}).get("content", "")
                subject      = msg.get("subject", "")
                log.info(f"  Checking: '{subject}'")

                # Extract 6-digit OTP
                otp_match = re.search(r"\b(\d{6})\b", body_content)
                if otp_match:
                    otp = otp_match.group(1)
                    log.info(f"OTP found: {otp}")

                    # Mark message as read
                    patch_url = f"{GRAPH_BASE}/users/{mailbox}/messages/{msg['id']}"
                    requests.patch(
                        patch_url,
                        headers=headers,
                        json={"isRead": True},
                        timeout=10,
                    )
                    return otp

        except requests.RequestException as e:
            log.warning(f"Graph API error on attempt {attempt}: {e}")

        remaining = int(deadline - time.time())
        if remaining > 0:
            log.info(f"OTP not found yet — retrying in 5s … ({remaining}s left)")
            time.sleep(5)

    raise RuntimeError(f"Zoho OTP email not received within {max_wait} seconds.")


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
            page.wait_for_selector(
                "#login_id",
                timeout=15000,
            )
            page.fill(
                "#login_id",
                cfg["zoho_email"],
            )
            page.click("button#nextbtn")
            log.info("Email entered and Next clicked.")

            # ── 3. Enter password ─────────────────────────────────────────
            page.wait_for_selector("#password", timeout=10000)
            page.fill("#password", cfg["zoho_pass"])

            login_triggered_at = datetime.now(timezone.utc)
            page.click("button#nextbtn")
            log.info("Password submitted and Sign in clicked.")

            # ── 4. Handle OTP screen ──────────────────────────────────────
            try:
                page.wait_for_selector(
                    "input[placeholder*='OTP' i], input[placeholder*='code' i], "
                    "input[name='code'], #otp_field, input[type='tel']",
                    timeout=12000,
                )
                log.info("OTP screen detected — fetching from Microsoft 365 inbox …")

                otp = fetch_zoho_otp_graph(
                    cfg          = cfg,
                    triggered_at = login_triggered_at,
                    max_wait     = 90,
                )

                page.fill(
                    "input[placeholder*='OTP' i], input[placeholder*='code' i], "
                    "input[name='code'], #otp_field, input[type='tel']",
                    otp,
                )
                page.keyboard.press("Enter")
                log.info("OTP submitted.")

            except PWTimeout:
                log.info("No OTP screen — proceeding without OTP.")

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
