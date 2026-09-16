"""
app.py — Long-running FastAPI service that fires Zoho check-in/out at exact
IST times via APScheduler. Designed for Render Web Service deployment.

Endpoints:
  GET  /healthz           liveness probe
  GET  /                  next-run summary (browser-friendly)
  GET  /status            JSON: scheduler state + next fire times
  POST /run?action=...    manual trigger (header `X-Trigger-Token` required
                          when TRIGGER_TOKEN env is set)

Self-ping keeps Render's free instance from sleeping between fires.

With GH_DISPATCH_TOKEN set, each fire dispatches the `attendance.yml` GitHub
Actions workflow instead of launching Chromium here: Render's free instance
(512 MB) runs out of memory on the Zoho People app. On startup, a catch-up
re-fires a run missed in the last CATCHUP_WINDOW_MINUTES (restart, deploy, OOM).
"""

from __future__ import annotations

import logging
import os
import random
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException, Query

from github_dispatch import (
    DispatchConfig,
    dispatch,
    handled_run,
    list_runs_since,
    wait_for_completion,
)
from zoho_browser import run_attendance

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
)
log = logging.getLogger("app")

IST = ZoneInfo("Asia/Kolkata")

CHECKIN_HOUR = int(os.environ.get("CHECKIN_HOUR", "12"))
CHECKIN_MIN = int(os.environ.get("CHECKIN_MIN", "0"))
CHECKOUT_HOUR = int(os.environ.get("CHECKOUT_HOUR", "22"))
CHECKOUT_MIN = int(os.environ.get("CHECKOUT_MIN", "0"))

TRIGGER_TOKEN = os.environ.get("TRIGGER_TOKEN", "").strip()
# Render injects RENDER_EXTERNAL_URL automatically — use it so the service
# self-pings without any manual config after the first deploy.
SELF_PING_URL = (
    os.environ.get("SELF_PING_URL", "").strip()
    or (os.environ.get("RENDER_EXTERNAL_URL", "").rstrip("/") + "/healthz"
        if os.environ.get("RENDER_EXTERNAL_URL") else "")
)
SELF_PING_INTERVAL = int(os.environ.get("SELF_PING_INTERVAL", "600"))  # 10 min

# None → launch the browser in-process (the pre-GitHub behaviour).
GITHUB = DispatchConfig.from_env()
SCHEDULE = {
    "checkin": (CHECKIN_HOUR, CHECKIN_MIN),
    "checkout": (CHECKOUT_HOUR, CHECKOUT_MIN),
}
CATCHUP_WINDOW = timedelta(minutes=int(os.environ.get("CATCHUP_WINDOW_MINUTES", "120")))

scheduler = BackgroundScheduler(timezone=IST)


def _scheduled_job(
    action: str,
    *,
    force: bool = False,
    jitter: bool = True,
    source: str = "scheduler",
) -> None:
    log.info("Scheduler firing: %s (force=%s, source=%s)", action, force, source)
    try:
        # Forced runs skip the weekend gate; forced and catch-up runs skip the
        # humanising jitter so they happen immediately.
        if GITHUB is None:
            result = run_attendance(  # type: ignore[arg-type]
                action,
                jitter_seconds=60 if jitter else 0,
                skip_weekend_check=force,
            )
            log.info("Scheduler job %s done: %s", action, result)
        else:
            _run_on_github(action, force=force, jitter=jitter, source=source)
    except Exception as exc:
        log.exception("Scheduler job %s failed: %s", action, exc)


def _run_on_github(action: str, *, force: bool, jitter: bool, source: str) -> None:
    if jitter:
        delay = random.randint(0, 60)
        log.info("Humanising jitter: sleeping %ds before dispatching %s", delay, action)
        time.sleep(delay)
    run = dispatch(GITHUB, action, force=force, source=source)
    log.info("Dispatched %s to GitHub Actions: %s", action, run.html_url)
    final = wait_for_completion(GITHUB, run.id)
    if final.get("status") != "completed":
        log.warning(
            "Scheduler job %s: GitHub run still %s when we stopped watching — %s",
            action, final.get("status"), run.html_url,
        )
    elif final.get("conclusion") == "success":
        log.info("Scheduler job %s done: GitHub run succeeded — %s", action, run.html_url)
    else:
        log.error(
            "Scheduler job %s failed: GitHub run concluded %s — %s",
            action, final.get("conclusion"), run.html_url,
        )


def due_catchups(
    now: datetime,
    schedule: dict[str, tuple[int, int]],
    window: timedelta,
) -> list[tuple[str, datetime]]:
    """Weekday fires of `schedule` that happened less than `window` before `now`.

    Checks yesterday too, so a window that crosses midnight still finds a
    late-evening fire.
    """
    now = now.astimezone(IST)
    due: list[tuple[str, datetime]] = []
    for days_back in (0, 1):
        day = (now - timedelta(days=days_back)).date()
        if day.weekday() >= 5:
            continue
        for action, (hour, minute) in schedule.items():
            fired = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
            if fired <= now < fired + window:
                due.append((action, fired))
    return due


def _catch_up(now: datetime | None = None) -> None:
    """Re-fire a scheduled run this process missed, e.g. when the instance
    restarted (deploy, crash, out-of-memory kill) around fire time.

    GitHub's run history is the record of what already ran, so this needs
    dispatch configured. In-process mode has no durable record, and re-running
    Chromium right after an OOM restart would just loop.
    """
    if GITHUB is None:
        log.info("Catch-up off: needs GH_DISPATCH_TOKEN to read past runs from GitHub")
        return
    now = now or datetime.now(IST)
    for action, fired in due_catchups(now, SCHEDULE, CATCHUP_WINDOW):
        try:
            done = handled_run(list_runs_since(GITHUB, fired), action)
        except Exception as exc:
            log.warning(
                "Catch-up: couldn't read GitHub run history (%s); dispatching %s to be safe",
                exc, action,
            )
        else:
            if done:
                log.info(
                    "Catch-up: %s at %s already handled by GitHub run %s (%s)",
                    action, fired.strftime("%H:%M"), done["id"],
                    done.get("conclusion") or done.get("status"),
                )
                continue
            log.warning(
                "Catch-up: %s scheduled for %s IST has no successful run; dispatching now",
                action, fired.strftime("%a %H:%M"),
            )
        _scheduled_job(action, jitter=False, source="catchup")


def _start_scheduler() -> None:
    scheduler.add_job(
        _scheduled_job,
        CronTrigger(day_of_week="mon-fri", hour=CHECKIN_HOUR, minute=CHECKIN_MIN, timezone=IST),
        kwargs={"action": "checkin"},
        id="checkin",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        _scheduled_job,
        CronTrigger(day_of_week="mon-fri", hour=CHECKOUT_HOUR, minute=CHECKOUT_MIN, timezone=IST),
        kwargs={"action": "checkout"},
        id="checkout",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "Scheduler started — checkin %02d:%02d IST, checkout %02d:%02d IST (Mon-Fri)",
        CHECKIN_HOUR, CHECKIN_MIN, CHECKOUT_HOUR, CHECKOUT_MIN,
    )


def _self_ping_loop() -> None:
    if not SELF_PING_URL:
        return
    log.info("Self-ping loop active: %s every %ds", SELF_PING_URL, SELF_PING_INTERVAL)
    while True:
        time.sleep(SELF_PING_INTERVAL)
        try:
            requests.get(SELF_PING_URL, timeout=10)
        except Exception as exc:
            log.warning("Self-ping failed: %s", exc)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _start_scheduler()
    if GITHUB:
        log.info(
            "Browser runs on GitHub Actions (%s, %s@%s)",
            GITHUB.repo, GITHUB.workflow, GITHUB.ref,
        )
    else:
        log.info("Browser runs in-process; set GH_DISPATCH_TOKEN to run it on GitHub Actions")
    threading.Thread(target=_catch_up, daemon=True).start()
    if SELF_PING_URL:
        threading.Thread(target=_self_ping_loop, daemon=True).start()
    yield
    scheduler.shutdown(wait=False)


app = FastAPI(lifespan=lifespan)


def _next_runs() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for job in scheduler.get_jobs():
        out[job.id] = job.next_run_time.isoformat() if job.next_run_time else None
    return out


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, Any]:
    now = datetime.now(IST)
    return {
        "service": "zoho-attendance-bot",
        "now_ist": now.isoformat(),
        "schedule": {
            "checkin": f"{CHECKIN_HOUR:02d}:{CHECKIN_MIN:02d} IST Mon-Fri",
            "checkout": f"{CHECKOUT_HOUR:02d}:{CHECKOUT_MIN:02d} IST Mon-Fri",
        },
        "next_runs": _next_runs(),
    }


@app.get("/status")
def status() -> dict[str, Any]:
    return {
        "running": scheduler.running,
        "now_ist": datetime.now(IST).isoformat(),
        "next_runs": _next_runs(),
    }


@app.post("/run")
def manual_run(
    action: str = Query(..., pattern="^(checkin|checkout)$"),
    force: bool = Query(False, description="Bypass weekend check and jitter (smoke tests)"),
    x_trigger_token: str | None = Header(default=None, alias="X-Trigger-Token"),
) -> dict[str, str | bool]:
    if TRIGGER_TOKEN and x_trigger_token != TRIGGER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid trigger token")
    threading.Thread(
        target=_scheduled_job,
        kwargs={"action": action, "force": force, "jitter": not force, "source": "api"},
        daemon=True,
    ).start()
    return {"status": "started", "action": action, "force": force}
