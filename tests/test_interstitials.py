"""Zoho sign-in interstitials and sign-out, driven in a real headless Chromium
against Zoho's served markup (trimmed to the controls). Needs Playwright's
Chromium (`playwright install chromium`); skipped without it."""

from __future__ import annotations

import logging

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import zoho_browser as zb

DASHBOARD = "https://people.zoho.in/people/"
SERVICE = "?servicename=zohopeople&serviceurl=https%3A%2F%2Fpeople.zoho.in%2Fpeople"
TIMEZONE_UPDATE = "https://accounts.zoho.in/announcement/timezone-update" + SERVICE
SESSIONS_REMINDER = "https://accounts.zoho.in/announcement/sessions-reminder" + SERVICE

# Controls navigate to their `data-go` URL when clicked, so every click shows up
# as a request the test can check.
_CLICK_THROUGH = """
<script>
  document.querySelectorAll('[data-go]').forEach(el =>
    el.addEventListener('click', () => { location.href = el.dataset.go; }));
</script>
"""

# accounts.zoho.in/announcement/timezone-update as served to a GitHub runner on
# 2026-09-16. "Edit & Update" and "Update" rewrite the account's time zone and
# "Sign Out" ends the session; only the snooze may be clicked.
TIMEZONE_PAGE = """<!doctype html>
<title>Update your time zone</title>
<a href="https://accounts.zoho.in/logout?servicename=AaaServer" class="err-btn">Sign Out</a>
<h1>Update your time zone</h1>
<span class="edit_text" data-go="https://accounts.zoho.in/timezone/edit">Edit &amp; Update</span>
<button class="update_profile" id="update_profile_new"
        data-go="https://accounts.zoho.in/timezone/update"><span></span>Update</button>
<button class="update_profile" id="update_profile" style="display:none"
        data-go="https://accounts.zoho.in/timezone/update"><span></span>Update</button>
{snooze}
"""

# accounts.zoho.in/announcement/sessions-reminder, the variant served on
# 2026-09-16 (the older one had an "I Understand" #continue_button). "Manage
# Sessions" opens session management and "Sign Out" ends the session; only
# "Do it later" moves on.
SESSIONS_REMINDER_PAGE = f"""<!doctype html>
<title>Zoho Accounts</title>
<a href="https://accounts.zoho.in/logout?servicename=AaaServer" class="err-btn">Sign Out</a>
<a id="continueButton" class="blue_btn" data-go="https://accounts.zoho.in/home#sessions">Manage Sessions</a>
<a class="blue_text dolater" data-go="{DASHBOARD}">Do it later</a>
<a href="https://www.zoho.com">Zoho Corporation Pvt. Ltd.</a>
"""


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        try:
            chromium = p.chromium.launch(headless=True)
        except PlaywrightError as exc:
            pytest.skip(f"Playwright Chromium not installed: {exc.message.splitlines()[0]}")
        yield chromium
        chromium.close()


def _dismiss(browser, url: str, html: str) -> tuple[str, list[str]]:
    """Open `url` serving `html`, run the bot's dismiss loop, and return the
    final URL plus every URL requested after the interstitial."""
    page = browser.new_page()
    visited: list[str] = []

    def serve(route):
        visited.append(route.request.url)
        body = html + _CLICK_THROUGH if route.request.url == url else "<title>stub</title>"
        route.fulfill(status=200, content_type="text/html", body=body)

    page.route("**/*", serve)
    page.goto(url)
    zb._dismiss_announcements_and_open_dashboard(page)
    final_url = page.url
    page.close()
    return final_url, visited[1:]


@pytest.mark.parametrize("snooze", [
    # as served: a <span>, which button/link scans never see
    f'<span class="grey_text remind_later_link" data-go="{DASHBOARD}">Remind me later</span>',
    # if Zoho renames the class, the text alone should still find it
    f'<div class="later" data-go="{DASHBOARD}">Remind me later</div>',
], ids=["as-served", "class-renamed"])
def test_timezone_update_prompt_is_snoozed_not_accepted(browser, snooze):
    final_url, visited = _dismiss(browser, TIMEZONE_UPDATE, TIMEZONE_PAGE.format(snooze=snooze))

    assert final_url == DASHBOARD
    assert visited == [DASHBOARD]


@pytest.mark.parametrize("markup", [
    SESSIONS_REMINDER_PAGE,
    # if Zoho renames the class, the text alone should still find it
    SESSIONS_REMINDER_PAGE.replace('class="blue_text dolater"', 'class="later"'),
], ids=["as-served", "class-renamed"])
def test_sessions_reminder_is_put_off_not_managed(browser, markup):
    final_url, visited = _dismiss(browser, SESSIONS_REMINDER, markup)

    assert final_url == DASHBOARD
    assert visited == [DASHBOARD]


# ── sign-out ─────────────────────────────────────────────────────────────────

def test_sign_out_ends_the_session(browser):
    page = browser.new_page()
    visited: list[str] = []

    def serve(route):
        visited.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body="<title>Sign in</title>")

    page.route("**/*", serve)
    zb._sign_out(page)
    page.close()

    assert visited[0].startswith("https://accounts.zoho.in/logout?")


def test_sign_out_failure_is_logged_not_raised(browser, caplog):
    page = browser.new_page()
    page.route("**/*", lambda route: route.abort())

    with caplog.at_level(logging.WARNING):
        zb._sign_out(page)  # runs in _do_attendance's `finally`: must never raise
    page.close()

    assert "sign-out failed" in caplog.text
