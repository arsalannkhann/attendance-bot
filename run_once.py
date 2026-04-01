#!/usr/bin/env python3
"""
run_once.py — GitHub Actions entrypoint for Zoho People attendance.

Reads credentials from environment variables (set as GitHub Secrets).
Determines whether to check-in or check-out based on the ACTION env var
set by the workflow (or auto-detects from current IST hour if not set).

Env vars required:
  CLIENT_ID      – Zoho OAuth client ID
  CLIENT_SECRET  – Zoho OAuth client secret
  REFRESH_TOKEN  – Zoho OAuth refresh token (permanent)
  EMAIL_ID       – Your Zoho People email address

Optional:
  ACTION         – "checkin" or "checkout" (set by workflow; auto-detected if missing)
  ACCOUNTS_URL   – defaults to https://accounts.zoho.in
  API_BASE       – defaults to https://people.zoho.in
"""

import os
import sys
import logging
import requests
from datetime import datetime
import pytz

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    stream=sys.stdout,
)
log = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
IST          = pytz.timezone("Asia/Kolkata")
ZOHO_FMT     = "dd/MM/yyyy HH:mm:ss"
PY_FMT       = "%d/%m/%Y %H:%M:%S"


# ── Config from env ───────────────────────────────────────────────────────────
def load_config() -> dict:
    missing = []
    cfg = {}
    for key in ("CLIENT_ID", "CLIENT_SECRET", "REFRESH_TOKEN", "EMAIL_ID"):
        val = os.environ.get(key, "").strip()
        if not val:
            missing.append(key)
        cfg[key.lower()] = val

    if missing:
        log.error(f"Missing required environment variables: {', '.join(missing)}")
        log.error("Add them as GitHub Secrets (repo → Settings → Secrets → Actions).")
        sys.exit(1)

    cfg["accounts_url"] = os.environ.get("ACCOUNTS_URL", "https://accounts.zoho.in").rstrip("/")
    cfg["api_base"]     = os.environ.get("API_BASE",     "https://people.zoho.in").rstrip("/")
    return cfg


# ── OAuth ─────────────────────────────────────────────────────────────────────
def get_access_token(cfg: dict) -> str:
    url  = f"{cfg['accounts_url']}/oauth/v2/token"
    data = {
        "grant_type":    "refresh_token",
        "client_id":     cfg["client_id"],
        "client_secret": cfg["client_secret"],
        "refresh_token": cfg["refresh_token"],
    }
    resp = requests.post(url, data=data, timeout=30)
    resp.raise_for_status()
    body = resp.json()
    if "access_token" not in body:
        log.error(f"Token refresh failed: {body}")
        sys.exit(1)
    log.info("Access token obtained.")
    return body["access_token"]


# ── Attendance ────────────────────────────────────────────────────────────────
def build_timestamp(hour: int, minute: int) -> str:
    now = datetime.now(IST).replace(hour=hour, minute=minute, second=0, microsecond=0)
    return now.strftime(PY_FMT)


def post_attendance(cfg: dict, action: str):
    token = get_access_token(cfg)
    url   = f"{cfg['api_base']}/people/api/attendance"

    if action == "checkin":
        ts     = build_timestamp(12, 0)
        params = {"dateFormat": ZOHO_FMT, "emailId": cfg["email_id"], "checkIn": ts}
        label  = "CHECK-IN"
    else:
        ts     = build_timestamp(22, 0)
        params = {"dateFormat": ZOHO_FMT, "emailId": cfg["email_id"], "checkOut": ts}
        label  = "CHECK-OUT"

    log.info(f"Posting {label} → {ts} IST")
    headers = {"Authorization": f"Zoho-oauthtoken {token}"}
    resp    = requests.post(url, params=params, headers=headers, timeout=30)

    try:
        body = resp.json()
    except Exception:
        body = resp.text

    if resp.status_code == 200 and isinstance(body, dict) \
            and body.get("response", {}).get("status") == "success":
        log.info(f"{label} successful! Response: {body}")
    else:
        log.error(f"{label} failed [{resp.status_code}]: {body}")
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    cfg = load_config()
    log.info(f"Email    : {cfg['email_id']}")
    log.info(f"API base : {cfg['api_base']}")

    # Check weekday in IST
    now_ist = datetime.now(IST)
    if now_ist.weekday() >= 5:
        log.info(f"Today is {now_ist.strftime('%A')} IST — skipping (weekend).")
        sys.exit(0)

    # Determine action: explicit env var → or auto-detect from UTC hour
    action = os.environ.get("ACTION", "").strip().lower()
    if action not in ("checkin", "checkout"):
        utc_hour = datetime.utcnow().hour
        # 12:00 IST = 06:30 UTC  → hours 6–7 → checkin
        # 22:00 IST = 16:30 UTC  → hours 16–17 → checkout
        if 6 <= utc_hour <= 7:
            action = "checkin"
        elif 16 <= utc_hour <= 17:
            action = "checkout"
        else:
            log.error(
                f"Cannot auto-detect action from UTC hour {utc_hour}. "
                "Set ACTION=checkin or ACTION=checkout explicitly."
            )
            sys.exit(1)
        log.info(f"Auto-detected action: {action} (UTC hour={utc_hour})")
    else:
        log.info(f"Action from env: {action}")

    post_attendance(cfg, action)


if __name__ == "__main__":
    main()
