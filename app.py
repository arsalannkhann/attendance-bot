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
"""

from __future__ import annotations

import logging
import os
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Header, HTTPException, Query

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

scheduler = BackgroundScheduler(timezone=IST)


def _scheduled_job(action: str, *, force: bool = False) -> None:
    log.info("Scheduler firing: %s (force=%s)", action, force)
    try:
        # Manual/forced runs skip the weekend gate AND the humanising jitter
        # so smoke tests give immediate, deterministic feedback.
        result = run_attendance(  # type: ignore[arg-type]
            action,
            jitter_seconds=0 if force else 60,
            skip_weekend_check=force,
        )
        log.info("Scheduler job %s done: %s", action, result)
    except Exception as exc:
        log.exception("Scheduler job %s failed: %s", action, exc)


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
        kwargs={"action": action, "force": force},
        daemon=True,
    ).start()
    return {"status": "started", "action": action, "force": force}
