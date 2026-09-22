"""The punch decision end to end, in a real headless Chromium against a fake
Zoho People whose state the test controls: a dashboard with the attendance
button, and an Attendance page with today's record (markup trimmed from the
real pages served 2026-09-17). Skipped without Playwright's Chromium."""

from __future__ import annotations

import pytest
from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

import zoho_browser as zb

PORTAL = "https://people.zoho.in/testorg/zp"


class FakePeople:
    def __init__(self, *, check_in=None, check_out=None, stale_button=False,
                 punch_registers=True, button_delay_ms=0):
        self.check_in, self.check_out = check_in, check_out
        self.stale_button = stale_button          # a label that never catches up
        self.button_delay_ms = button_delay_ms    # Zoho's status arriving late
        self.punch_registers = punch_registers
        self.punches = 0
        self.portal_loads = 0

    @property
    def checked_in(self) -> bool:
        return self.check_in is not None and self.check_out is None

    def serve(self, route):
        url = route.request.url
        if url.startswith("https://people.zoho.in/punch"):
            self.punches += 1
            if self.punch_registers:
                if self.checked_in:
                    self.check_out = "10:00 PM"
                else:
                    self.check_in, self.check_out = "12:00 PM", None
            route.fulfill(status=200, content_type="application/json", body="{}")
        elif url.split("#")[0] == PORTAL:
            self.portal_loads += 1
            route.fulfill(status=200, content_type="text/html", body=self._portal())
        else:
            route.fulfill(status=404, body="")

    def _portal(self) -> str:
        # Zoho renders the button's default "Check-in" and only switches it when
        # the attendance status arrives.
        settled = "Check-out" if self.checked_in and not self.stale_button else "Check-in"
        label = "Check-in" if self.button_delay_ms else settled
        catch_up = ("" if not self.button_delay_ms else
                    f"<script>setTimeout(() => document.querySelector('#home button')"
                    f".innerText = '{settled}', {self.button_delay_ms});</script>")
        check_in = (f'<div class="zpl_attentrydtls" aria-label="Remote In Check-in Time: '
                    f'{self.check_in}"><b>{self.check_in}</b></div>') if self.check_in else ""
        check_out = (f'<div class="zpl_attentrydtls" aria-label="Check-out Time : '
                     f'{self.check_out}"><b>{self.check_out}</b></div>') if self.check_out else ""
        return f"""<!doctype html>
<title>Zoho People</title>
<ul><li id="zp_maintab_attendance">Attendance</li></ul>
<div id="home">
  <button id="ZPAtt_check_in_out" aria-label="Check-in" role="button" type="button"
          onclick="fetch('/punch', {{method: 'POST'}})">{label}</button>
</div>
<div id="attendance" style="display:none">
  <div class="zpl_chk-in in" id="ZPAtt_check_in_out" aria-label="Check-in"><p>Check-in</p></div>
  <table><tbody id="ZPAtt5_listViewEntries">
    <tr><td width="60" aria-label="Wed 16"></td>
      <td width="180"><div class="zpl_attentrydtls" aria-label="Remote In Check-in Time: 01:04 PM"><b>01:04 PM</b></div></td>
      <td width="150"><div class="zpl_attentrydtls" aria-label="Check-out Time : 04:02 AM"><b>04:02 AM</b></div></td>
      <td width="150"><div class="zpl_attentrydtls" aria-label="hours worked: 14:58"></div></td></tr>
    <tr class="today-active zpl_crntday"><td width="60" aria-label="Today 17"></td>
      <td width="180">{check_in}</td>
      <td><div class="zpl_attprgrswrpr" title="" aria-label=""></div></td>
      <td width="150">{check_out}</td>
      <td width="150"><div class="zpl_attentrydtls" aria-label="hours worked: 00:00"></div></td></tr>
  </tbody></table>
</div>
{catch_up}
<script>
  document.querySelector('#zp_maintab_attendance').addEventListener('click', () => {{
    document.querySelector('#home').style.display = 'none';
    document.querySelector('#attendance').style.display = '';
    location.hash = '#attendance/entry/summary-mode:list';
  }});
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


@pytest.fixture(autouse=True)
def fast_timings(monkeypatch):
    monkeypatch.setattr(zb, "_PRE_CLICK_MS", 0)
    monkeypatch.setattr(zb, "_POST_CLICK_MS", 500)
    monkeypatch.setattr(zb, "_CONFIRM_ATTEMPTS", 2)
    monkeypatch.setattr(zb, "_CONFIRM_WAIT_SECONDS", 0)
    monkeypatch.setattr(zb, "_BUTTON_SETTLE_MS", 4000)


def punch(browser, zoho: FakePeople, action: str, *, dry_run: bool = False) -> str:
    page = browser.new_page()
    page.route("**/*", zoho.serve)
    page.goto(PORTAL)  # where sign-in leaves the bot
    try:
        return zb._punch(page, action, dry_run=dry_run)
    finally:
        page.close()


def test_checks_out_and_confirms_it_in_the_record(browser):
    zoho = FakePeople(check_in="12:00 PM")

    assert punch(browser, zoho, "checkout") == "checked-out"
    assert (zoho.punches, zoho.check_out) == (1, "10:00 PM")


def test_checks_in_when_today_has_no_entry(browser):
    zoho = FakePeople()

    assert punch(browser, zoho, "checkin") == "checked-in"
    assert (zoho.punches, zoho.check_in) == (1, "12:00 PM")


def test_waits_for_the_button_to_catch_up_with_the_record(browser):
    # 2026-09-17..21: on a fresh dashboard the button reads "Check-in" for a few
    # seconds before Zoho's status arrives, which skipped every checkout.
    zoho = FakePeople(check_in="12:01 PM", button_delay_ms=2000)

    assert punch(browser, zoho, "checkout") == "checked-out"
    assert (zoho.punches, zoho.check_out) == (1, "10:00 PM")


def test_button_that_never_catches_up_stops_without_clicking(browser):
    zoho = FakePeople(check_in="01:04 PM", stale_button=True)

    with pytest.raises(zb.AttendanceMismatch, match="01:04 PM"):
        punch(browser, zoho, "checkout")
    assert zoho.punches == 0


def test_does_nothing_when_already_checked_in(browser):
    zoho = FakePeople(check_in="12:00 PM")

    assert punch(browser, zoho, "checkin") == "already-checked-in"
    assert zoho.punches == 0


def test_dry_run_reloads_and_rechecks_the_button_but_never_clicks(browser):
    zoho = FakePeople(check_in="12:00 PM")

    assert punch(browser, zoho, "checkout", dry_run=True) == "dry-run: would click 'check-out' (checked-out)"
    assert zoho.punches == 0
    assert zoho.portal_loads == 2  # sign-in landing, then a fresh load right before the click


def test_punch_missing_from_the_record_fails_after_one_click(browser):
    zoho = FakePeople(check_in="12:00 PM", punch_registers=False)

    with pytest.raises(zb.AttendanceError, match="still shows"):
        punch(browser, zoho, "checkout")
    assert zoho.punches == 1
