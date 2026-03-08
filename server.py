import os
import random
import logging
import time as _time

import requests
from flask import Flask, jsonify
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# ────────────────────────────────────────────
# Flask app
# ────────────────────────────────────────────
app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# ────────────────────────────────────────────
# Health / keep-alive endpoint
# ────────────────────────────────────────────
@app.route("/")
def index():
    return jsonify({"status": "alive", "service": "zoho-attendance-bot"})


@app.route("/health")
def health():
    return jsonify({"status": "ok"})


# ────────────────────────────────────────────
# Self-ping every 10 minutes to keep Render
# free-tier instance from spinning down
# ────────────────────────────────────────────
RENDER_EXTERNAL_URL = os.environ.get("RENDER_EXTERNAL_URL", "")


def self_ping():
    """Ping our own /health endpoint to prevent idle shutdown."""
    if not RENDER_EXTERNAL_URL:
        logging.warning("RENDER_EXTERNAL_URL not set — skipping self-ping")
        return
    url = f"{RENDER_EXTERNAL_URL}/health"
    try:
        resp = requests.get(url, timeout=10)
        logging.info(f"Self-ping → {resp.status_code}")
    except Exception as e:
        logging.warning(f"Self-ping failed: {e}")


# ────────────────────────────────────────────
# Attendance job wrappers
# ────────────────────────────────────────────
def run_checkin():
    """Check-in with a random human-like delay."""
    delay = random.randint(30, 180)
    logging.info(f"⏳ Check-in scheduled — waiting {delay}s (human delay)...")
    _time.sleep(delay)

    os.environ["ZOHO_ACTION"] = "checkin"
    try:
        from zoho_bot import run_bot
        run_bot()
    except Exception as e:
        logging.error(f"❌ Check-in failed: {e}")


def run_checkout():
    """Check-out with a random human-like delay."""
    delay = random.randint(30, 180)
    logging.info(f"⏳ Check-out scheduled — waiting {delay}s (human delay)...")
    _time.sleep(delay)

    os.environ["ZOHO_ACTION"] = "checkout"
    try:
        from zoho_bot import run_bot
        run_bot()
    except Exception as e:
        logging.error(f"❌ Check-out failed: {e}")


# ────────────────────────────────────────────
# Scheduler setup
# ────────────────────────────────────────────
scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

# Check-in at 2:00 PM IST daily
scheduler.add_job(
    run_checkin,
    CronTrigger(hour=14, minute=0, day_of_week="mon-fri", timezone="Asia/Kolkata"),
    id="checkin",
    name="Mon-Fri Check-In at 2:00 PM IST",
    replace_existing=True,
)

# Check-out at 11:59 PM IST daily
scheduler.add_job(
    run_checkout,
    CronTrigger(hour=23, minute=59, day_of_week="mon-fri", timezone="Asia/Kolkata"),
    id="checkout",
    name="Mon-Fri Check-Out at 11:59 PM IST",
    replace_existing=True,
)

# Self-ping every 10 minutes
scheduler.add_job(
    self_ping,
    "interval",
    minutes=10,
    id="self_ping",
    name="Self-ping keep-alive",
    replace_existing=True,
)

scheduler.start()
logging.info("🚀 Scheduler started — Check-in 2:00 PM IST, Check-out 11:59 PM IST")
logging.info("📡 Self-ping every 10 minutes enabled")

# Print next run times
for job in scheduler.get_jobs():
    logging.info(f"   → {job.name}: next run at {job.next_run_time}")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
