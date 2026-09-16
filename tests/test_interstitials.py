"""Zoho sign-in interstitials, dismissed in a real headless Chromium against
their served markup (trimmed to the controls). Needs Playwright's Chromium
(`playwright install chromium`); skipped without it."""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import zoho_browser as zb

TIMEZONE_UPDATE = (
    "https://accounts.zoho.in/announcement/timezone-update"
    "?servicename=zohopeople&serviceurl=https%3A%2F%2Fpeople.zoho.in%2Fpeople"
)
DASHBOARD = "https://people.zoho.in/people/"

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
<script>
  document.querySelectorAll('[data-go]').forEach(el =>
    el.addEventListener('click', () => {{ location.href = el.dataset.go; }}));
</script>
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


@pytest.mark.parametrize("snooze", [
    # as served: a <span>, which button/link scans never see
    f'<span class="grey_text remind_later_link" data-go="{DASHBOARD}">Remind me later</span>',
    # if Zoho renames the class, the text alone should still find it
    f'<div class="later" data-go="{DASHBOARD}">Remind me later</div>',
])
def test_timezone_update_prompt_is_snoozed_not_accepted(browser, snooze):
    page = browser.new_page()
    visited: list[str] = []

    def serve(route):
        url = route.request.url
        visited.append(url)
        body = TIMEZONE_PAGE.format(snooze=snooze) if url == TIMEZONE_UPDATE else "<title>stub</title>"
        route.fulfill(status=200, content_type="text/html", body=body)

    page.route("**/*", serve)
    page.goto(TIMEZONE_UPDATE)

    zb._dismiss_announcements_and_open_dashboard(page)

    assert page.url == DASHBOARD
    assert [u for u in visited if "/timezone/" in u or "/logout" in u] == []
    page.close()
