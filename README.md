<div align="center">
  <img src="https://img.icons8.com/color/144/000000/zoho.png" alt="Zoho Logo" width="100"/>
  <h1>Zoho Attendance Bot</h1>
  <p><strong>Always-on Playwright service that checks you in/out of Zoho People at exact IST times — no GitHub Actions cron drift, no missed days.</strong></p>

  <p>
    <img alt="Python" src="https://img.shields.io/badge/Python-3.11+-blue.svg" />
    <img alt="Playwright" src="https://img.shields.io/badge/Playwright-1.59-orange.svg" />
    <img alt="Render" src="https://img.shields.io/badge/Render-Blueprint-46E3B7.svg" />
    <img alt="Free tier" src="https://img.shields.io/badge/cost-$0%2Fmo-success.svg" />
  </p>
</div>

---

## Why this exists

GitHub Actions cron is best-effort — schedules drift by 5–30+ min during peak hours and sometimes skip outright. Sleeping inside the workflow until the IST target burns runner minutes and still fails when the dispatch never arrives.

This version runs as a **Render Web Service** with an in-process `APScheduler` cron pinned to `Asia/Kolkata`. The job fires within ~1 second of the configured time, every weekday, with no scheduler drift. A 10-minute self-ping keeps the free instance from sleeping. Total cost: **$0/month**.

### Schedule (configurable)
| Action | Time (IST) | Days |
|--------|------------|------|
| Check-in | `12:00` | Mon–Fri |
| Check-out | `22:00` | Mon–Fri |

A small randomised 0–60 s jitter is added so it doesn't look mechanical. Failures retry 3× with exponential backoff (10 s → 20 s → 40 s).

---

## Architecture

```
┌──────────────────────────────────────────────────────────┐
│ Render Web Service (Docker, Playwright base image)        │
│                                                           │
│   FastAPI ── /healthz  /status  POST /run                 │
│       │                                                   │
│       └── APScheduler (BackgroundScheduler, IST tz)       │
│              ├── cron Mon-Fri 12:00 → run_attendance(in)  │
│              └── cron Mon-Fri 22:00 → run_attendance(out) │
│                       │                                   │
│                       └── Playwright Chromium → Zoho      │
│                                  │                        │
│                                  └── MFA (TOTP or Outlook)│
└──────────────────────────────────────────────────────────┘
```

---

## Setup — fork and deploy

### Prerequisites
- A **Zoho People** account with attendance widget enabled
- A **Render** account (free) — sign up at https://render.com
- A **GitHub** account (to fork this repo so Render can autodeploy on push)
- Either:
  - A **TOTP secret** for your Zoho MFA *(recommended — most reliable)*, **OR**
  - An **Outlook/M365 mailbox** the Zoho OTP emails get delivered to *(falls back if TOTP not set)*

### Step 1 — Fork

Click **Fork** at the top right of this repo. You'll get a copy at `https://github.com/<your-user>/zoho-bot`.

### Step 2 — Get your secrets ready

Have these values handy before deploying — Render asks for them during Blueprint setup.

#### `ZOHO_EMAIL` *(required)*
Your Zoho login email, e.g. `you@company.com`.

#### `ZOHO_PASSWORD` *(required)*
Your Zoho password.

#### `ZOHO_TOTP` *(recommended)*
Your TOTP secret string (NOT the 6-digit code — the long secret behind the QR code).

To extract it:
1. Zoho → **My Account** → **Multi-Factor Authentication** → **OneAuth/TOTP**
2. When setting up, click **Can't scan? Show secret key**
3. Copy the long alphanumeric string (looks like `JBSWY3DPEHPK3PXP...`)

If you already have TOTP enabled and can't see the secret, disable & re-enable it to see it again.

#### `OUTLOOK_EMAIL` + `OUTLOOK_PASSWORD` *(only if no TOTP)*
The mailbox that receives Zoho's OTP emails. **Important:**
- If your M365 tenant requires MFA, the password-grant flow this bot uses is blocked. You'll need an **App Password**: account.microsoft.com → Security → Advanced → App passwords.
- Personal Outlook (`@outlook.com`, `@hotmail.com`) usually works directly.
- TOTP is more reliable; prefer it if you can.

#### `TRIGGER_TOKEN` *(auto-generated)*
Render generates this for you from `render.yaml`. You'll grab it from the dashboard after deploy to use the manual `/run` endpoint.

### Step 3 — Deploy via Blueprint

1. Go to https://dashboard.render.com → **New** → **Blueprint**
2. Connect your GitHub account if you haven't already
3. Select your forked **zoho-bot** repo
4. Render reads `render.yaml` and proposes a Docker web service named `zoho-attendance-bot` in Singapore
5. Fill in the secret env vars when prompted (see Step 2). Required: `ZOHO_EMAIL`, `ZOHO_PASSWORD`. Optional: `ZOHO_TOTP` *or* `OUTLOOK_EMAIL`+`OUTLOOK_PASSWORD`
6. Click **Apply**

First build pulls the Playwright base image (~600 MB, ~3–5 min). Subsequent deploys are much faster.

When you see `Your service is live 🎉` in the deploy logs, hit your service URL — you should get JSON like:

```json
{
  "service": "zoho-attendance-bot",
  "now_ist": "2026-05-04T11:58:42+05:30",
  "schedule": {
    "checkin":  "12:00 IST Mon-Fri",
    "checkout": "22:00 IST Mon-Fri"
  },
  "next_runs": {
    "checkin":  "2026-05-04T12:00:00+05:30",
    "checkout": "2026-05-04T22:00:00+05:30"
  }
}
```

### Step 4 — Smoke-test before relying on it

Grab the `TRIGGER_TOKEN` from your Render dashboard → service → **Environment** tab → click the eye icon next to `TRIGGER_TOKEN` to reveal, then **Copy**.

Fire a forced check-in (the `force=true` bypasses the weekend gate so you can test on any day):

```bash
curl -X POST "https://<your-service>.onrender.com/run?action=checkin&force=true" \
     -H "X-Trigger-Token: <paste-token>"
```

Expected reply: `{"status":"started","action":"checkin","force":true}`.

Then watch the Render **Logs** tab — within ~70 seconds you should see:

```
Scheduler firing: checkin (force=True)
Attendance attempt 1/3 for checkin
Navigating to Zoho sign-in
No MFA prompt           # or: MFA prompt detected → Generated TOTP / Polling Outlook
Widget text: check-in
Result: checked-in
Scheduler job checkin done: checked-in
```

Verify in Zoho People that you're checked in. To undo the test, fire `action=checkout` the same way.

### Step 5 — Disable the old GitHub Actions workflow (if you used the previous version)

If you migrated from the GH Actions–based version, disable it so it doesn't double-fire:

```bash
git mv .github/workflows/attendance.yml .github/workflows/attendance.yml.disabled
git mv .github/workflows/keep-alive.yml .github/workflows/keep-alive.yml.disabled
git commit -m "Disable GH Actions — Render is primary now"
git push
```

You're done. Monday 12:00 IST you check in, Monday 22:00 IST you check out, and so on.

---

## Endpoints

| Method | Path | Purpose | Auth |
|--------|------|---------|------|
| `GET`  | `/`        | Service info + next fire times | — |
| `GET`  | `/healthz` | Liveness probe (used by Render and the self-ping) | — |
| `GET`  | `/status`  | Scheduler running state + next runs | — |
| `POST` | `/run?action=checkin\|checkout[&force=true]` | Manual trigger | `X-Trigger-Token` header |

`force=true` skips both the weekend gate and the humanising jitter — use it for smoke tests on Sat/Sun.

---

## Configuration knobs

All optional env vars (set in Render Environment tab):

| Var | Default | Purpose |
|-----|---------|---------|
| `CHECKIN_HOUR` | `12` | Hour-of-day (IST) for check-in |
| `CHECKIN_MIN`  | `0`  | Minute-of-hour for check-in |
| `CHECKOUT_HOUR`| `22` | Hour-of-day (IST) for check-out |
| `CHECKOUT_MIN` | `0`  | Minute-of-hour for check-out |
| `SELF_PING_INTERVAL` | `600` | Self-ping interval in seconds (10 min) |
| `SELF_PING_URL` | *(auto)* | Override the self-ping URL; auto-derived from `RENDER_EXTERNAL_URL` |
| `DEBUG_DIR` | `/tmp/attendance-debug` | Where Playwright failure screenshots are saved |

Change a value → Render auto-redeploys.

---

## Local development

```bash
git clone https://github.com/<your-user>/zoho-bot.git
cd zoho-bot

python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium

# create a .env (gitignored) with ZOHO_EMAIL, ZOHO_PASSWORD, etc.

# one-off CLI run:
ACTION=checkin python zoho_browser.py

# full service (scheduler + endpoints) on http://localhost:8000:
uvicorn app:app --reload --port 8000
```

---

## Troubleshooting

### Build fails with `ZoneInfoNotFoundError: 'No time zone found with key Asia/Kolkata'`
You're on an old commit. Pull latest — the Dockerfile installs `tzdata` via apt and `requirements.txt` includes the pip `tzdata` fallback.

### Build hangs at `Configuring tzdata` / `Please select the geographic area in which you live`
Same — pull latest. The fix sets `DEBIAN_FRONTEND=noninteractive` and pre-seeds `/etc/timezone`.

### `BrowserType.launch: Executable doesn't exist at /ms-playwright/chromium_headless_shell-...`
Mismatch between the Playwright pip version and the Docker base image tag. They must move together — both are pinned to `1.59.0` in this repo. If you bump one, bump the other.

### `RuntimeError: Missing env vars: ZOHO_EMAIL, ZOHO_PASSWORD`
You created the service manually instead of via Blueprint. Add the secrets in **Environment** tab.

### `RuntimeError: OTP required but no source configured`
Zoho is asking for MFA but neither `ZOHO_TOTP` nor `OUTLOOK_EMAIL`+`OUTLOOK_PASSWORD` are set. Add one.

### `MFA is REQUIRED on this account. ROPC (password grant) will not work without an App Password.`
Your M365 tenant blocks password-grant. Either generate an App Password (account.microsoft.com → Security → Advanced) or switch to TOTP.

### Login fails / widget not found
Open the Render **Logs** tab and look for the failure attempt. The script saves a full-page screenshot to `/tmp/attendance-debug/<timestamp>-<action>.png` on every failure — open the Render **Shell** tab to download it:

```bash
ls -lt /tmp/attendance-debug/
```

Common cause: Zoho's login page changed selectors. Open an issue with the screenshot and the captured `Widget text:` line.

---

## Security notes

- **Never commit secrets.** `.env` is gitignored.
- **Rotate `TRIGGER_TOKEN`** if you ever paste it in chat, an issue, or a screenshot. Render → Environment → edit → Save → service redeploys.
- **Rotate Zoho/Outlook passwords** if they're ever exposed.
- **The token-protected `/run` endpoint** is open to the internet. If you don't need manual triggers, set `TRIGGER_TOKEN` to a long random value (Render auto-generates one).

---

## Files in this repo

| File | What it does |
|------|--------------|
| `app.py` | FastAPI service + APScheduler, endpoints, self-ping |
| `zoho_browser.py` | Playwright automation (login, MFA, click widget) |
| `outlook.py` | Microsoft Graph poll for Zoho OTP emails |
| `Dockerfile` | Builds container; installs `tzdata`, pins Playwright |
| `render.yaml` | Blueprint: web service, env vars, health check, region |
| `requirements.txt` | Python deps with version pins |

---

<div align="center">
  <b>Built for hassle-free, on-time attendance tracking.</b>
</div>
