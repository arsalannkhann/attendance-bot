from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

import app
from fakes import REPO, FakeResponse, make_run

WED = datetime(2026, 9, 16, tzinfo=app.IST)  # a Wednesday
SCHEDULE = {"checkin": (12, 0), "checkout": (22, 0)}
TWO_HOURS = timedelta(hours=2)


def at(day: datetime, hour: int, minute: int = 0) -> datetime:
    return day.replace(hour=hour, minute=minute)


def dispatched(github) -> list[tuple[str, str]]:
    return [(d["inputs"]["action"], d["inputs"]["source"]) for d in github.dispatches]


@pytest.fixture
def bot(monkeypatch, github, cfg):
    """app wired to FakeGitHub; the in-process browser and sleeps are recorded, not run."""
    browser: list[dict] = []
    slept: list[float] = []

    def fake_run_attendance(action, *, jitter_seconds, skip_weekend_check):
        browser.append({
            "action": action,
            "jitter_seconds": jitter_seconds,
            "skip_weekend_check": skip_weekend_check,
        })
        return "checked-in"

    monkeypatch.setattr(app, "GITHUB", cfg)
    monkeypatch.setattr(app, "SCHEDULE", SCHEDULE)
    monkeypatch.setattr(app, "CATCHUP_WINDOW", TWO_HOURS)
    monkeypatch.setattr(app, "run_attendance", fake_run_attendance)
    monkeypatch.setattr(app.time, "sleep", slept.append)
    return SimpleNamespace(github=github, browser=browser, slept=slept)


@pytest.fixture
def threads(monkeypatch) -> list[tuple]:
    started: list[tuple] = []

    class RecordingThread:
        def __init__(self, target=None, kwargs=None, daemon=None, name=None):
            self.target, self.kwargs = target, kwargs or {}

        def start(self):
            started.append((self.target, self.kwargs))

    monkeypatch.setattr(app.threading, "Thread", RecordingThread)
    return started


# ── which scheduled fires are due for catch-up ───────────────────────────────

@pytest.mark.parametrize("now, due", [
    (at(WED, 12, 30), [("checkin", at(WED, 12))]),
    (at(WED, 22, 10), [("checkout", at(WED, 22))]),
    (at(WED, 11, 59), []),                       # not fired yet: the scheduler has it
    (at(WED, 14, 0), []),                        # window closed
    (at(WED + timedelta(days=3), 12, 30), []),   # Saturday
])
def test_due_catchups_finds_weekday_fires_inside_the_window(now, due):
    assert app.due_catchups(now, SCHEDULE, TWO_HOURS) == due


def test_due_catchups_reaches_back_past_midnight():
    friday = WED + timedelta(days=2)
    just_after_midnight = at(friday + timedelta(days=1), 0, 15)

    assert app.due_catchups(just_after_midnight, {"checkout": (23, 30)}, TWO_HOURS) == [
        ("checkout", at(friday, 23, 30)),
    ]


# ── catch-up on startup ──────────────────────────────────────────────────────

def test_catch_up_dispatches_a_fire_that_never_succeeded(bot):
    bot.github.history = [
        make_run(501, "checkin", conclusion="failure", created_at="2026-09-16T06:30:28Z"),
    ]

    app._catch_up(now=at(WED, 12, 40))

    assert dispatched(bot.github) == [("checkin", "catchup")]
    assert bot.slept == []    # already late, so no humanising jitter
    assert bot.browser == []  # the browser runs on GitHub, never in-process


def test_catch_up_leaves_a_fire_that_already_succeeded(bot):
    bot.github.history = [make_run(501, "checkin", created_at="2026-09-16T06:30:28Z")]

    app._catch_up(now=at(WED, 12, 40))

    assert bot.github.dispatches == []


def test_catch_up_only_counts_runs_since_the_scheduled_time(bot):
    bot.github.history = [make_run(480, "checkin", created_at="2026-09-16T06:00:00Z")]  # 11:30 IST

    app._catch_up(now=at(WED, 12, 40))

    assert dispatched(bot.github) == [("checkin", "catchup")]


def test_catch_up_still_dispatches_when_run_history_is_unreadable(bot):
    bot.github.list_replies = [FakeResponse(500, {"message": "Server Error"})]

    app._catch_up(now=at(WED, 12, 40))

    assert dispatched(bot.github) == [("checkin", "catchup")]


def test_catch_up_does_nothing_outside_the_window(bot):
    app._catch_up(now=at(WED, 15, 0))

    assert bot.github.requests == []


def test_catch_up_needs_github_as_its_record_of_past_runs(monkeypatch, bot):
    monkeypatch.setattr(app, "GITHUB", None)

    app._catch_up(now=at(WED, 12, 40))

    assert bot.github.requests == []
    assert bot.browser == []


# ── the scheduled job ────────────────────────────────────────────────────────

def test_scheduled_job_hands_the_browser_run_to_github(bot):
    app._scheduled_job("checkout")

    assert dispatched(bot.github) == [("checkout", "scheduler")]
    assert bot.browser == []
    assert len(bot.slept) == 1 and 0 <= bot.slept[0] <= 60  # humanising jitter before dispatch


def test_scheduled_job_reports_a_failed_github_run(bot, caplog):
    bot.github.run_states[9001] = [("completed", "failure")]

    app._scheduled_job("checkin", jitter=False)

    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("failure" in m and f"https://github.com/{REPO}/actions/runs/9001" in m for m in errors)


def test_scheduled_job_logs_dispatch_errors_instead_of_raising(bot, caplog):
    bot.github.dispatch_replies = [FakeResponse(401, {"message": "Bad credentials"})]

    app._scheduled_job("checkin", jitter=False)  # runs on a scheduler thread: must not raise

    assert any(r.levelno == logging.ERROR and "Bad credentials" in r.getMessage()
               for r in caplog.records)


@pytest.mark.parametrize("kwargs, expected", [
    ({}, {"jitter_seconds": 60, "skip_weekend_check": False}),
    ({"force": True, "jitter": False}, {"jitter_seconds": 0, "skip_weekend_check": True}),
])
def test_scheduled_job_runs_the_browser_in_process_without_github(monkeypatch, bot, kwargs, expected):
    monkeypatch.setattr(app, "GITHUB", None)

    app._scheduled_job("checkin", **kwargs)

    assert bot.browser == [{"action": "checkin", **expected}]
    assert bot.github.requests == []


# ── entry points ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("force, jitter", [(True, False), (False, True)])
def test_manual_run_skips_jitter_only_when_forced(monkeypatch, threads, force, jitter):
    monkeypatch.setattr(app, "TRIGGER_TOKEN", "")

    app.manual_run(action="checkout", force=force, x_trigger_token=None)

    assert threads == [(app._scheduled_job, {
        "action": "checkout", "force": force, "jitter": jitter, "source": "api",
    })]


def test_startup_runs_catch_up_in_the_background(monkeypatch, threads, cfg, caplog):
    monkeypatch.setattr(app, "GITHUB", cfg)
    monkeypatch.setattr(app, "_start_scheduler", lambda: None)
    monkeypatch.setattr(app, "SELF_PING_URL", "")
    monkeypatch.setattr(app.scheduler, "shutdown", lambda wait=True: None)

    async def boot():
        async with app.lifespan(app.app):
            pass

    with caplog.at_level(logging.INFO):
        asyncio.run(boot())

    assert (app._catch_up, {}) in threads
    assert f"GitHub Actions ({REPO}" in caplog.text


# ── Render fallback when GitHub can't do the run ────────────────────────────

@pytest.fixture
def portal_login(monkeypatch):
    monkeypatch.setenv("ZOHO_EMAIL", "user@example.com")
    monkeypatch.setenv("ZOHO_PASSWORD", "not-a-real-password")
    monkeypatch.delenv("FALLBACK_TO_RENDER", raising=False)


def test_failed_github_run_falls_back_to_the_browser_here(bot, portal_login):
    bot.github.run_states[9001] = [("completed", "failure")]

    app._scheduled_job("checkin", jitter=False)

    assert dispatched(bot.github) == [("checkin", "scheduler")]
    assert bot.browser == [{"action": "checkin", "jitter_seconds": 0, "skip_weekend_check": False}]


def test_dispatch_error_falls_back_to_the_browser_here(bot, portal_login):
    bot.github.dispatch_replies = [FakeResponse(403, {"message": "Actions disabled"})]

    app._scheduled_job("checkout", jitter=False)

    assert [b["action"] for b in bot.browser] == ["checkout"]


def test_successful_github_run_does_not_start_a_second_browser(bot, portal_login):
    app._scheduled_job("checkin", jitter=False)

    assert bot.browser == []


def test_fallback_can_be_switched_off(monkeypatch, bot, portal_login):
    monkeypatch.setenv("FALLBACK_TO_RENDER", "false")
    bot.github.run_states[9001] = [("completed", "failure")]

    app._scheduled_job("checkin", jitter=False)

    assert bot.browser == []


def test_fallback_needs_the_portal_login_on_this_service(monkeypatch, bot):
    monkeypatch.delenv("ZOHO_EMAIL", raising=False)
    monkeypatch.delenv("ZOHO_PASSWORD", raising=False)
    bot.github.run_states[9001] = [("completed", "failure")]

    app._scheduled_job("checkin", jitter=False)

    assert bot.browser == []
