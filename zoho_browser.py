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
  FORCE                           (CLI only: "true" skips the weekend gate)
  DRY_RUN                         (CLI only: "true" logs in and reads the
                                   widget but never clicks)
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
from urllib.parse import urlparse

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


_HINT_JS = """
() => {
    const re = /(check|att|in_out|inout|ZPAtt|punch|clock|shift|continue|got\\s*it|proceed|skip|dismiss|remind|later|announce|session)/i;
    const out = [];
    const seen = new Set();
    document.querySelectorAll('[id], button, a, [role="button"], [type="submit"]').forEach(el => {
        const id = el.id || '';
        const cls = (typeof el.className === 'string' ? el.className : '') || '';
        const txt = ((el.innerText || el.textContent || '').trim()).slice(0, 80);
        if (!re.test(id) && !re.test(cls) && !re.test(txt)) return;
        const key = `${el.tagName}|${id}|${cls}|${txt}`;
        if (seen.has(key)) return;
        seen.add(key);
        out.push({tag: el.tagName, id, cls: cls.slice(0, 80), txt});
    });
    return out.slice(0, 50);
}
"""


# Common dismiss-button selectors on Zoho announcement / interstitial pages.
# Order matters: we try ID-anchored selectors first since they're stable
# across UI text changes, then fall back to text-matched selectors.
_DISMISS_SELECTORS = [
    # accounts.zoho.in/preannouncement/block-sessions — Zoho's "you have too
    # many active sessions" page. The visible text on `#continue_button` is
    # literally "Terminate All Sessions" because clicking it ends all your
    # other Zoho sessions on this account (phone, laptop, etc.) and then
    # proceeds. There is NO other dismiss option. We click it because the
    # alternative is the bot being permanently blocked.
    "#continue_button",
    "a.continue_button",
    "a.blue_btn.continue_button",
    # accounts.zoho.in/announcement/tfa-banner — "set up two-factor auth" nag
    # shown to accounts without MFA configured. The proceed control is a
    # <button> labelled "Continue to Sign In" (classes cont-signin /
    # no-backup-cancel). It renders a beat after the banner, so the dismiss
    # loop now settles + retries before giving up.
    "button.cont-signin",
    "button.no-backup-cancel",
    "button:has-text('Continue to Sign In')",
    # accounts.zoho.in/announcement/timezone-update — "Update your time zone",
    # shown when the sign-in IP is in another time zone (GitHub's US runners).
    # "Update" / "Edit & Update" would rewrite the account's time zone, so
    # snooze instead. The snooze is a <span>, which the button/link scans miss.
    "span.remind_later_link",
    # Standard acknowledgement buttons on various Zoho announcement pages.
    "button:has-text('Remind me later')",
    "button:has-text('Remind Me Later')",
    "text=/^\\s*remind me later\\s*$/i",
    "button:has-text('Maybe later')",
    "button:has-text('Maybe Later')",
    "button:has-text('Got it')",
    "button:has-text('Got It')",
    "button:has-text('Continue')",
    "button:has-text('Proceed')",
    "button:has-text('Skip')",
    "button:has-text('Dismiss')",
    "button:has-text('Close')",
    "button:has-text('OK')",
    "a:has-text('Continue')",
    "a:has-text('Skip')",
    "a:has-text('Got it')",
    "[role='button']:has-text('Continue')",
    "[role='button']:has-text('Got it')",
    "[role='button']:has-text('Skip')",
]


_INTERSTITIAL_HINT_JS = """
() => {
    const out = [];
    document.querySelectorAll(
        'button, a, [role="button"], input[type="submit"], input[type="button"]'
    ).forEach(el => {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return;
        const txt = ((el.innerText || el.value || el.textContent || '').trim()).slice(0, 80);
        if (!txt && !el.id) return;
        out.push({
            tag: el.tagName,
            id: el.id || '',
            cls: ((typeof el.className === 'string') ? el.className : '').slice(0, 80),
            txt,
            href: (el.href || '').slice(0, 100),
        });
    });
    return out.slice(0, 40);
}
"""

# JS-based dismiss: finds the first visible button/link whose text matches an
# acknowledgement keyword and is NOT a destructive action. Returns the matched
# text so we can log it. More resilient than CSS :has-text selectors because
# Zoho uses generic <div role=button> elements without semantic markup.
_JS_DISMISS_CLICK = """
() => {
    const good = /^(proceed|continue|got\\s*it|acknowledge|skip|dismiss|close|ok|i\\s*understand|maybe\\s*later|remind\\s*me\\s*later|next|done|agree|accept)$/i;
    const bad  = /cancel|logout|sign\\s*out|delete|remove|deactivate|revoke|terminate/i;
    const candidates = document.querySelectorAll(
        'button, a, [role="button"], input[type="submit"], input[type="button"]'
    );
    for (const el of candidates) {
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) continue;
        const txt = ((el.innerText || el.value || el.textContent || '').trim());
        if (!txt || bad.test(txt)) continue;
        if (good.test(txt)) {
            el.click();
            return {clicked: true, txt: txt.slice(0, 80), tag: el.tagName, id: el.id || ''};
        }
    }
    return {clicked: false};
}
"""

# Targeted proceed-click for the tfa-banner (and similar) interstitials, where
# the "Continue to Sign In" control is present in the DOM but hidden behind a
# collapsed sub-dialog — so the visibility-filtered scans above never match it.
# A direct JS .click() fires the element's handler regardless of visibility.
# Scoped to specific proceed classes / exact text so we never click a
# destructive control (Sign Out, etc.).
_JS_FORCE_PROCEED_CLICK = """
() => {
    const byClass = document.querySelector(
        'button.cont-signin, button.no-backup-cancel, a.continue_button'
    );
    let el = byClass;
    if (!el) {
        el = [...document.querySelectorAll('button, a, [role="button"]')].find(b => {
            const t = ((b.innerText || b.textContent || '').trim());
            return /^continue\\s*to\\s*sign\\s*in$/i.test(t);
        });
    }
    if (el) {
        el.click();
        return {
            clicked: true,
            txt: ((el.innerText || el.textContent || '').trim()).slice(0, 80),
            cls: (typeof el.className === 'string' ? el.className : '').slice(0, 80),
        };
    }
    return {clicked: false};
}
"""


def _dump_interstitial_hints(page) -> None:
    """Log every visible clickable element so we can identify the dismiss
    button when Zoho changes the interstitial markup."""
    try:
        hints = page.evaluate(_INTERSTITIAL_HINT_JS)
        log.info("Interstitial clickable elements (%d):", len(hints))
        for i, h in enumerate(hints):
            log.info(
                "  [%02d] %s id=%r cls=%r txt=%r",
                i, h.get("tag"), h.get("id"), h.get("cls"), h.get("txt"),
            )
    except Exception as exc:
        log.warning("Interstitial hint scan failed: %s", exc)


def _dismiss_announcements_and_open_dashboard(page) -> None:
    """Click through any Zoho announcement / preannouncement interstitial so
    the session redirect can complete to the People dashboard.

    Strategy:
      1. Dump every visible clickable element on the interstitial (for
         diagnostics if the click flow fails — Zoho changes button text often).
      2. Try our hard-coded `_DISMISS_SELECTORS` (Playwright `:has-text(...)`).
      3. Fall back to in-page JS that scans for buttons whose text matches an
         acknowledgement keyword (handles `<div role=button>` and other markup
         that Playwright's CSS selector engine misses).
      4. NEVER navigate away from accounts.zoho.in unauthenticated — doing
         so loses the session ticket and Zoho redirects to the public
         marketing site `www.zoho.com/people/`, which has no dashboard.
    """
    for attempt in range(6):
        url = page.url
        host = (urlparse(url).hostname or "").lower()

        # We've arrived once the *host* is people.zoho.* — match on hostname,
        # NOT a substring of the whole URL. The accounts.zoho.in interstitials
        # carry "people.zoho.in" inside their `serviceurl=...` query param
        # (the host isn't percent-encoded), so a naive `"people.zoho." in url`
        # check falsely fired on the interstitial and skipped the dismiss step.
        if host.startswith("people.zoho."):
            break

        if not host.startswith("accounts.zoho."):
            log.warning("Unexpected URL during interstitial handling: %s", url)
            break

        log.info("Interstitial detected (attempt %d): %s", attempt + 1, url)
        # Some banners (e.g. tfa-banner) paint their primary button a beat
        # after the page first appears. Let the DOM settle before scanning so
        # the dismiss control actually exists when we look for it.
        page.wait_for_timeout(1500)
        _dump_interstitial_hints(page)

        clicked = False
        # Pass 1: Playwright :has-text selectors (works for <button> and <a>).
        for sel in _DISMISS_SELECTORS:
            try:
                btn = page.locator(sel).first
                if btn.is_visible(timeout=300):
                    log.info("Dismissing via Playwright selector: %s", sel)
                    btn.click()
                    try:
                        page.wait_for_load_state("networkidle", timeout=10_000)
                    except PWTimeout:
                        pass
                    clicked = True
                    break
            except Exception:
                continue

        # Pass 2: in-page JS click. Handles generic divs / spans with role=button.
        if not clicked:
            try:
                result = page.evaluate(_JS_DISMISS_CLICK)
                if result and result.get("clicked"):
                    log.info(
                        "Dismissed via JS click: tag=%s id=%r txt=%r",
                        result.get("tag"), result.get("id"), result.get("txt"),
                    )
                    try:
                        page.wait_for_load_state("networkidle", timeout=10_000)
                    except PWTimeout:
                        pass
                    clicked = True
            except Exception as exc:
                log.warning("JS dismiss click failed: %s", exc)

        # Pass 3: targeted force-click for proceed controls that live in the DOM
        # but are hidden (tfa-banner's "Continue to Sign In"). Visibility-
        # filtered passes above miss these; a scoped JS click fires the handler.
        if not clicked:
            try:
                result = page.evaluate(_JS_FORCE_PROCEED_CLICK)
                if result and result.get("clicked"):
                    log.info(
                        "Dismissed via forced proceed-click: txt=%r cls=%r",
                        result.get("txt"), result.get("cls"),
                    )
                    try:
                        page.wait_for_load_state("networkidle", timeout=10_000)
                    except PWTimeout:
                        pass
                    clicked = True
            except Exception as exc:
                log.warning("Forced proceed-click failed: %s", exc)

        if not clicked:
            # Don't give up after one miss: a late-rendering button may appear
            # within the next iteration. We never navigate away (that loses the
            # session) — just wait and let the bounded loop re-scan.
            log.warning(
                "No dismiss button matched on interstitial (attempt %d); "
                "waiting and retrying.", attempt + 1,
            )
            page.wait_for_timeout(2000)
            continue

    final_host = (urlparse(page.url).hostname or "").lower()
    if final_host.startswith("accounts.zoho."):
        log.error(
            "Still stuck on interstitial after retries: %s — the hint dumps "
            "above show every visible clickable element; extend "
            "_DISMISS_SELECTORS or the JS regex in zoho_browser.py to match one.",
            page.url,
        )
    log.info("Post-interstitial URL: %s", page.url)


def _save_debug(page, label: str) -> None:
    """Dump screenshot + HTML + page hints. Free Render tier has no shell,
    so we also stream the URL/title/element scan to stdout for log retrieval."""
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        png = DEBUG_DIR / f"{ts}-{label}.png"
        html = DEBUG_DIR / f"{ts}-{label}.html"
        try:
            page.screenshot(path=str(png), full_page=True)
            log.error("Debug screenshot saved: %s", png)
        except Exception as exc:
            log.warning("Screenshot failed: %s", exc)
        try:
            html.write_text(page.content(), encoding="utf-8")
        except Exception as exc:
            log.warning("HTML dump failed: %s", exc)
        try:
            log.error("DEBUG page.url    = %s", page.url)
            log.error("DEBUG page.title  = %s", page.title())
            hints = page.evaluate(_HINT_JS)
            log.error("DEBUG hint count  = %d", len(hints))
            for i, h in enumerate(hints):
                log.error("DEBUG hint[%02d]    tag=%s id=%r cls=%r txt=%r",
                          i, h.get("tag"), h.get("id"), h.get("cls"), h.get("txt"))
        except Exception as exc:
            log.warning("DEBUG page-scan failed: %s", exc)
    except Exception as exc:
        log.warning("Could not save debug info: %s", exc)


def _decide(action: Action, label: str, *, dry_run: bool) -> tuple[bool, str]:
    """(click?, result) for the punch button's current label. A label already
    showing the opposite action means the punch happened, so re-runs no-op."""
    if action == "checkin" and ("check-out" in label or "checkout" in label):
        return False, "already-checked-in"
    if action == "checkout" and ("check-in" in label or "checkin" in label):
        return False, "already-checked-out"
    result = "checked-in" if action == "checkin" else "checked-out"
    if dry_run:
        return False, f"dry-run: would click {label!r} ({result})"
    return True, result


def _do_attendance(cfg: dict, action: Action, *, dry_run: bool = False) -> str:
    """Single attempt. Returns a human-readable result string. Raises on failure.
    With `dry_run`, logs in and reads the widget but never clicks."""
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

            # Dismiss any post-login announcement interstitials (timezone reminder,
            # sessions-reminder, OneAuth promo, etc.) and force-navigate to the
            # People dashboard. Zoho's redirect target is set by `serviceurl`, but
            # since May 11 they sometimes park us on accounts.zoho.in/announcement/*
            # indefinitely. Direct goto bypasses that.
            _dismiss_announcements_and_open_dashboard(page)

            # The attendance widget is rendered by Zoho's SPA after the
            # dashboard shell loads; on a cold free dyno this can take a while.
            # NOTE: `zpl_btnloading` / `zbutton--loading` are *permanent*
            # classes on this button (the real spinner is #zpl_loading_icon,
            # which is display:none) — do NOT gate on them. The reliable
            # readiness signal is the button being visible with its label
            # text ("Check-in" / "Check-out") populated.
            page.wait_for_selector("#ZPAtt_check_in_out", state="visible", timeout=45_000)
            page.wait_for_function(
                """() => {
                    const b = document.querySelector('#ZPAtt_check_in_out');
                    return b && /check-?in|check-?out/i.test(
                        (b.innerText || b.getAttribute('aria-label') || '')
                    );
                }""",
                timeout=45_000,
            )
            button = page.locator("#ZPAtt_check_in_out")
            text = button.inner_text().strip().lower()
            log.info("Widget text: %s", text.replace("\n", " "))

            should_click, result = _decide(action, text, dry_run=dry_run)
            if should_click:
                time.sleep(3)
                button.click(delay=200)
                time.sleep(5)
            return result

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
    dry_run: bool = False,
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
            result = _do_attendance(cfg, action, dry_run=dry_run)
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


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")


def _cli() -> None:
    action = os.environ.get("ACTION", "").lower()
    if action not in ("checkin", "checkout"):
        hour = datetime.now(IST).hour
        action = "checkin" if 6 <= hour < 17 else "checkout"
        log.info("Auto-detected action from IST hour %d: %s", hour, action)
    try:
        run_attendance(
            action,
            jitter_seconds=0,
            skip_weekend_check=_env_flag("FORCE"),
            dry_run=_env_flag("DRY_RUN"),
        )
    except Exception as exc:
        log.error("Run failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    _cli()
