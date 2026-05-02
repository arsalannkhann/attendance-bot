<div align="center">
  <img src="https://img.icons8.com/color/144/000000/zoho.png" alt="Zoho Logo" width="100"/>
  <h1>Zoho Attendance Bot</h1>
  <p><strong>Long-running Playwright automation that fires Zoho People check-in / check-out at exact IST times.</strong></p>

  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-blue.svg" />
    <img alt="Playwright" src="https://img.shields.io/badge/Playwright-Chromium-orange.svg" />
    <img alt="Render" src="https://img.shields.io/badge/Render-Web%20Service-46E3B7.svg" />
  </p>
</div>

---

## Why Render and not GitHub Actions?

GitHub Actions cron is best-effort — schedules can drift 5–30+ minutes during peak hours and sometimes skip outright. The previous workaround (sleeping inside the workflow until the IST target) wasted runner minutes and still failed when the dispatch never arrived.

This version runs as a **Render Web Service** with an in-process `APScheduler` cron in `Asia/Kolkata` time. The job fires within a second of the configured time, every weekday, with no scheduler drift.

### Schedule (configurable via env)
- **Check-in:** `12:00 IST` Mon–Fri (`CHECKIN_HOUR` / `CHECKIN_MIN`)
- **Check-out:** `22:00 IST` Mon–Fri (`CHECKOUT_HOUR` / `CHECKOUT_MIN`)

A small randomised 0–60s jitter is added before each run so it doesn't look mechanical. Failures retry 3× with exponential backoff (10s → 20s → 40s).

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Render Web Service (Docker, Playwright base image)        │
│                                                           │
│   FastAPI ── /healthz  /status  POST /run                 │
│       │                                                   │
│       └── APScheduler (BackgroundScheduler, IST tz)       │
│              ├── cron 12:00 Mon-Fri  → run_attendance(in) │
│              └── cron 22:00 Mon-Fri  → run_attendance(out)│
│                       │                                   │
│                       └── Playwright Chromium → Zoho      │
└──────────────────────────────────────────────────────────┘
```

---

## Deploy to Render

### 1. Push the repo

```bash
git add .
git commit -m "Migrate to Render web service with APScheduler"
git push
```

### 2. Create the service from the blueprint

1. Render dashboard → **New** → **Blueprint**
2. Pick this repo. Render reads `render.yaml` and proposes a Docker web service named `zoho-attendance-bot`.
3. Fill in the secret env vars when prompted:
   - `ZOHO_EMAIL`, `ZOHO_PASSWORD` *(required)*
   - `ZOHO_TOTP` *(preferred)*  **— or —**  `OUTLOOK_EMAIL` + `OUTLOOK_PASSWORD`
   - `TRIGGER_TOKEN` is auto-generated; copy it for manual triggers.
4. Hit **Apply**. First build pulls the Playwright image (~3 min) then deploys.

### 3. Self-ping (automatic)

The container auto-derives its own URL from Render's built-in `RENDER_EXTERNAL_URL` env var and pings `/healthz` every **10 min** (override with `SELF_PING_INTERVAL`). Free instances stay warm 24/7 with no manual config.

### 4. Disable the old GitHub Actions workflow

Once Render is verified working, disable double-firing:

```bash
git mv .github/workflows/attendance.yml .github/workflows/attendance.yml.disabled
git commit -m "Disable GH Actions cron — Render is now primary"
git push
```

---

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `GET`  | `/`        | JSON: schedule + next fire times (browser-friendly) |
| `GET`  | `/healthz` | Liveness probe used by Render and self-ping |
| `GET`  | `/status`  | Scheduler running state + next runs |
| `POST` | `/run?action=checkin\|checkout` | Manual trigger (header `X-Trigger-Token: <token>`) |

Manual trigger example:

```bash
curl -X POST "https://zoho-attendance-bot.onrender.com/run?action=checkin" \
     -H "X-Trigger-Token: $TRIGGER_TOKEN"
```

---

## Local development

```bash
pip install -r requirements.txt
playwright install chromium

# one-off CLI run:
ACTION=checkin python zoho_browser.py

# full service (scheduler + endpoints):
uvicorn app:app --reload --port 8000
```

---

## Debugging failures

On any failure the script saves a full-page screenshot to `/tmp/attendance-debug/` (override with `DEBUG_DIR`). On Render, open the service shell from the dashboard to retrieve them:

```bash
ls /tmp/attendance-debug/
```

Logs (including each retry and the captured widget text) stream to the Render **Logs** tab.

---

<div align="center">
  <b>Built for hassle-free, on-time attendance tracking.</b>
</div>
