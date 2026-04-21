"""Outlook / Microsoft Graph MFA code retrieval.

Polls the configured Outlook mailbox for a Zoho verification email 
and extracts the 6 or 7-digit OTP.
"""

from __future__ import annotations

import logging
import os
import re
import time
import quopri
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_DEFAULT_CLIENT_ID = "d3590ed6-52b3-4102-aeff-aad2292ab01c"

_OTP_REGEXES = [
    re.compile(r"verification\s+code[:\s]+(\d{6,7})", re.IGNORECASE),
    re.compile(r"one-time\s+password[:\s]+(\d{6,7})", re.IGNORECASE),
    re.compile(r"otp[:\s]+(\d{6,7})", re.IGNORECASE),
    re.compile(r"code[:\s]+(\d{6,7})", re.IGNORECASE),
    re.compile(r"<b>(\d{6,7})</b>", re.IGNORECASE),
    re.compile(r">(\d{6,7})<"),
    re.compile(r"(?<!\d)(\d{6,7})(?!\d)"),
]

_ZOHO_SENDERS = ["noreply@zohoaccounts.in"]
_ZOHO_SUBJECT_KEYWORDS = ["verification", "zoho", "otp", "code", "sign-in"]


def _normalize_email(email: Optional[str]) -> str:
    return (email or "").strip().lower()


def _get_tenant_id(email: str) -> str:
    """Resolve the tenant ID from the email domain via OpenID discovery."""
    domain = email.split("@")[-1]
    resp = requests.get(
        f"https://login.microsoftonline.com/{domain}/.well-known/openid-configuration",
        timeout=5,
    )
    resp.raise_for_status()
    return resp.json()["token_endpoint"].split("/")[3]


def _get_access_token(
    username: str,
    password: str,
    client_id: str,
    client_secret: Optional[str],
    tenant_id: Optional[str],
) -> str:
    """Obtain an OAuth2 access token for the Outlook mailbox."""
    resolved_tenant_id = tenant_id or _get_tenant_id(username)
    params = {
        "client_id": client_id,
        "scope": "https://outlook.office365.com/.default",
        "username": username,
        "password": password,
        "grant_type": "password",
    }
    if client_secret:
        params["client_secret"] = client_secret

    resp = requests.post(
        f"https://login.microsoftonline.com/{resolved_tenant_id}/oauth2/v2.0/token",
        data=params,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=10,
    )
    if resp.status_code != 200:
        try:
            err = resp.json()
            error_code = err.get("error_codes", [None])[0]
            if error_code == 50076:
                logger.error("[outlook] MFA is REQUIRED on this account. ROPC (password grant) will not work without an App Password.")
            elif error_code == 50126:
                logger.error("[outlook] Invalid username or password.")
            logger.debug("[outlook] Auth error response: %s", resp.text)
        except Exception:
            pass
    resp.raise_for_status()
    return resp.json()["access_token"]


def _messages_api_url(username: str, client_secret: Optional[str]) -> str:
    if client_secret:
        return f"https://graph.microsoft.com/v1.0/users/{username}/messages"
    return "https://outlook.office365.com/api/v2.0/me/messages"


def _delete_message(msg_id: str, token: str, api_url: str):
    """Move a message to the 'Deleted Items' folder."""
    try:
        resp = requests.delete(
            f"{api_url}/{msg_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=10,
        )
        if resp.status_code in (200, 204):
            logger.info("[outlook] Message %s successfully deleted.", msg_id)
        else:
            logger.warning("[outlook] Failed to delete message %s: Status %d", msg_id, resp.status_code)
    except Exception as e:
        logger.warning("[outlook] Error deleting message %s: %s", msg_id, e)


def _extract_mime_part(content: str, mime_type: str) -> str:
    """Extract a specific MIME part body from raw MIME content."""
    pattern = re.compile(
        rf"Content-Type:\s*{re.escape(mime_type)}[\s\S]*?\r?\n\r?\n([\s\S]*?)(?=\r?\n--|$)",
        re.IGNORECASE,
    )
    match = pattern.search(content)
    if not match:
        return ""
    body = match.group(1).strip()
    header_block = match.group(0)
    
    if re.search(r"Content-Transfer-Encoding:\s*base64", header_block, re.IGNORECASE):
        import base64
        try:
            body = base64.b64decode(body.replace("\r\n", "").replace("\n", "")).decode("utf-8", errors="ignore")
        except Exception:
            pass
    elif re.search(r"Content-Transfer-Encoding:\s*quoted-printable", header_block, re.IGNORECASE):
        try:
            body = quopri.decodestring(body).decode("utf-8", errors="ignore")
        except Exception:
            pass
            
    return body


def _extract_otp(text: str) -> Optional[str]:
    """Find a 6 or 7-digit OTP in plain text, using keyword-proximity heuristics."""
    for regex in _OTP_REGEXES:
        match = regex.search(text)
        if match:
            code = match.group(1)
            if code not in {"000000", "646464", "212121"} and not re.match(r"^(\d)\1{5,6}$", code):
                return code
    return None


def _extract_otp_from_mime(content: str) -> Optional[str]:
    """Extract OTP from a raw MIME email body."""
    plain = _extract_mime_part(content, "text/plain")
    html = _extract_mime_part(content, "text/html")
    searchable = "\n".join(part for part in [plain, html, content] if part)
    return _extract_otp(searchable)


# ── Zoho MFA ───────────────────────────────────────────────────────────

def fetch_zoho_mfa_code(
    mfa_mailbox: str,
    ignore_before_ms: int,
    max_retries: int = 15,
    check_interval_seconds: float = 5.0,
    email_config: Optional[dict] = None,
) -> str:
    """Poll an Outlook mailbox for the Zoho verification code."""
    _email_cfg = email_config or {}
    username = mfa_mailbox or _email_cfg.get("username") or _strip_quotes(os.getenv("OUTLOOK_USERNAME", ""))
    password = _email_cfg.get("password") or _strip_quotes(os.getenv("OUTLOOK_PASSWORD", ""))
    client_id = _strip_quotes(os.getenv("OUTLOOK_CLIENT_ID")) or _DEFAULT_CLIENT_ID
    client_secret = _strip_quotes(os.getenv("OUTLOOK_CLIENT_SECRET")) or None
    tenant_id = _strip_quotes(os.getenv("OUTLOOK_TENANT_ID")) or None

    if not username or not password:
        raise RuntimeError("OUTLOOK_USERNAME and OUTLOOK_PASSWORD are required for MFA retrieval")

    api_url = _messages_api_url(username, client_secret)
    received_after_iso = __import__("datetime").datetime.utcfromtimestamp(
        ignore_before_ms / 1000
    ).strftime("%Y-%m-%dT%H:%M:%SZ")

    for attempt in range(1, max_retries + 1):
        try:
            token = _get_access_token(username, password, client_id, client_secret, tenant_id)
            resp = requests.get(
                api_url,
                headers={"Authorization": f"Bearer {token}"},
                params={
                    "$filter": f"receivedDateTime ge {received_after_iso}",
                    "$orderby": "receivedDateTime desc",
                    "$top": 25,
                },
                timeout=10,
            )
            resp.raise_for_status()
            messages = resp.json().get("value", [])

            for message in messages:
                received_raw = message.get("receivedDateTime") or message.get("ReceivedDateTime", "")
                try:
                    received_ms = int(
                        __import__("datetime").datetime.fromisoformat(
                            received_raw.replace("Z", "+00:00")
                        ).timestamp() * 1000
                    )
                except Exception:
                    received_ms = 0

                if received_ms and received_ms < ignore_before_ms:
                    continue

                sender_email = (
                    (message.get("from") or {}).get("emailAddress", {}).get("address", "")
                    or (message.get("From") or {}).get("EmailAddress", {}).get("Address", "")
                ).strip().lower()
                
                # Broaden check for zoho senders
                if sender_email not in [s.lower() for s in _ZOHO_SENDERS]:
                    if "zoho" not in sender_email:
                        continue

                subject = str(message.get("subject") or message.get("Subject") or "").lower()
                if not any(kw in subject for kw in _ZOHO_SUBJECT_KEYWORDS):
                    # If sender is correct, try to search anyway if keywords match
                    pass

                msg_id = message.get("id") or message.get("Id")
                if not msg_id:
                    continue

                mime_resp = requests.get(
                    f"{api_url}/{msg_id}/$value",
                    headers={"Authorization": f"Bearer {token}"},
                    timeout=10,
                )
                otp = _extract_otp_from_mime(mime_resp.text)
                if otp:
                    logger.info("[outlook] Zoho MFA code retrieved on attempt %d", attempt)
                    _delete_message(msg_id, token, api_url)
                    return otp

        except Exception as exc:
            if attempt == max_retries:
                raise
            logger.warning("[outlook] Zoho attempt %d failed: %s", attempt, exc)

        time.sleep(check_interval_seconds)

    raise RuntimeError("Zoho MFA verification code not found after maximum retries")


def _strip_quotes(val: Optional[str]) -> str:
    """Strip surrounding double/single quotes that Docker .env files may inject."""
    if not val:
        return ""
    return val.strip().strip('"').strip("'").strip()


if __name__ == "__main__":
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Fetch OTP from Outlook/Microsoft Graph")
    parser.add_argument("--mailbox", help="Email address to poll")
    parser.add_argument(
        "--ignore-before-mins",
        type=int,
        default=5,
        help="Ignore emails older than N minutes",
    )

    args = parser.parse_args()

    # Fetching anything in the last few minutes should be sufficient.
    ignore_before_ms = int((time.time() - (args.ignore_before_mins * 60)) * 1000)

    try:
        print(f"Polling {args.mailbox} for Zoho MFA code...")
        otp = fetch_zoho_mfa_code(args.mailbox, ignore_before_ms)
        print(f"\nSUCCESS! OTP: {otp}")
    except Exception as e:
        print(f"\nERROR: {e}")
        sys.exit(1)
