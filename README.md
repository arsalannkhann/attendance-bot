<div align="center">
  <img src="https://img.icons8.com/color/144/000000/zoho.png" alt="Zoho Logo" width="100"/>
  <h1>Zoho Attendance Bot</h1>
  <p><strong>Checks you in/out of Zoho People at exact IST times. A Render service keeps the clock and a GitHub Actions runner drives the browser, so there's no cron drift, no out-of-memory kills, and no missed days.</strong></p>

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

Running the browser on Render's free instance has the opposite problem: it has 512 MB of memory and 0.15 CPU, and Chromium plus the Zoho People app needs nearly all of it. Runs crawl, time out, and eventually get killed for running out of memory.

So this version splits the work. A **Render Web Service** runs an in-process `APScheduler` cron pinned to `Asia/Kolkata` that fires within ~1 second of the configured time. Each fire **dispatches a GitHub Actions workflow**, and the browser runs on GitHub's runner (7 GB of memory). If the Render service restarts around a fire, a startup catch-up reads GitHub's run history and re-dispatches anything that didn't succeed. A 10-minute self-ping keeps the free instance awake. Total cost: **$0/month** (about 5 runner-minutes a day, well inside GitHub's free allowance).

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
│ Render Web Service (Docker, free tier): the clock        │
│                                                          │
│   FastAPI ── /healthz  /status  POST /run                │
│       │                                                  │
│       ├── APScheduler (BackgroundScheduler, IST tz)      │
│       │      ├── cron Mon-Fri 12:00 → dispatch checkin   │
│       │      └── cron Mon-Fri 22:00 → dispatch checkout  │
│       │                                                  │
│       └── startup catch-up: re-dispatch a fire from the  │
│           last 2 h that has no successful run            │
└─────────────────────────────┬────────────────────────────┘
                              │ workflow_dispatch via GitHub API; Render
                              │ then polls the run and logs its result
┌─────────────────────────────▼────────────────────────────┐
│ GitHub Actions: attendance.yml (7 GB runner)             │
│                                                          │
│   python zoho_browser.py   (one run at a time)           │
│       └── Playwright Chromium → Zoho People              │
│              └── MFA (TOTP or Outlook)                   │
│   failure screenshots → run artifacts (7 days)           │
└──────────────────────────────────────────────────────────┘
```

Without `GH_DISPATCH_TOKEN` the service falls back to running Chromium in-process on Render (the old behaviour, with its memory problems) and the catch-up stays off.

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

### Step 4 — Run the browser on GitHub Actions

1. **Add the Zoho secrets to GitHub.** Open your fork → **Settings** → **Secrets and variables** → **Actions**, and add `ZOHO_EMAIL`, `ZOHO_PASSWORD`, and either `ZOHO_TOTP` or `OUTLOOK_EMAIL` + `OUTLOOK_PASSWORD`. Use the same values you gave Render.
2. **Enable the workflow.** On the **Actions** tab, open *Zoho People Attendance (Browser)* and click **Enable workflow** if it's disabled. It has no schedule of its own and only runs when dispatched.
3. **Create a token.** Go to https://github.com/settings/personal-access-tokens → **Generate new token** (fine-grained). Under repository access pick only your fork, and under repository permissions set **Actions: Read and write**. Pick a long expiry and note the date: when the token expires, dispatches fail until you replace it.
4. **Give the token to Render.** In the service's **Environment** tab, add `GH_DISPATCH_TOKEN`. Render redeploys, and the log should show `Browser runs on GitHub Actions (<you>/zoho-bot, attendance.yml@main)`.

### Step 5 — Smoke-test before relying on it

First, a **dry run**. It logs in and reads the attendance button without clicking it, so it's safe at any time:

```bash
gh workflow run attendance.yml -f action=checkin -f dry_run=true
```

In the run's log (`gh run watch`), look for `Widget text: check-in` followed by `Result: dry-run: would click 'check-in' (checked-in)`.

Then test the Render → GitHub path end to end. Grab the `TRIGGER_TOKEN` from your Render dashboard → service → **Environment** tab → click the eye icon next to `TRIGGER_TOKEN` to reveal, then **Copy**. This fires a **real** check-in (`force=true` bypasses the weekend gate so you can test on any day):

```bash
curl -X POST "https://<your-service>.onrender.com/run?action=checkin&force=true" \
     -H "X-Trigger-Token: <paste-token>"
```

Expected reply: `{"status":"started","action":"checkin","force":true}`.

Then watch the Render **Logs** tab. Within a few minutes you should see:

```
Scheduler firing: checkin (force=True, source=api)
Dispatched checkin to GitHub Actions: https://github.com/<you>/zoho-bot/actions/runs/<id>
GitHub run <id>: queued
GitHub run <id>: in_progress
GitHub run <id>: completed
Scheduler job checkin done: GitHub run succeeded — https://github.com/<you>/zoho-bot/actions/runs/<id>
```

Verify in Zoho People that you're checked in. To undo the test, fire `action=checkout` the same way.

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
| `GH_DISPATCH_TOKEN` | *(unset)* | Fine-grained GitHub token with **Actions: Read and write**. Set: the browser runs on GitHub Actions. Unset: it runs in-process on Render |
| `GH_DISPATCH_REPO` | *(auto)* | `owner/repo` to dispatch; auto-derived from `RENDER_GIT_REPO_SLUG` |
| `GH_DISPATCH_WORKFLOW` | `attendance.yml` | Workflow file to dispatch |
| `GH_DISPATCH_REF` | *(auto)* | Branch the workflow runs on; defaults to the deployed branch (`RENDER_GIT_BRANCH`) |
| `CATCHUP_WINDOW_MINUTES` | `120` | On startup, re-dispatch a fire from the last N minutes that has no successful run |
| `DEBUG_DIR` | `/tmp/attendance-debug` | Where Playwright failure screenshots are saved (on GitHub, uploaded as run artifacts) |

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

# one-off CLI run (DRY_RUN=true logs in without clicking):
ACTION=checkin DRY_RUN=true python zoho_browser.py

# full service (scheduler + endpoints) on http://localhost:8000:
uvicorn app:app --reload --port 8000

# tests (no browser, no network):
pip install -r requirements-dev.txt
pytest
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

### Render restarts mid-run, or runs time out at `Locator.click` / `wait_for_selector`
The browser is running on Render's free instance and running out of memory: it needs about 500 MB of the 512 MB. Set up [Step 4](#step-4--run-the-browser-on-github-actions) so it runs on GitHub instead.

### `Scheduler job ... failed: GitHub API returned HTTP 4xx`
The log line ends with GitHub's own message. Usual causes:
- **401:** the token expired or was revoked. Create a new one and update `GH_DISPATCH_TOKEN`.
- **403 / 404:** the token can't see the repo or lacks **Actions: Read and write**, or `GH_DISPATCH_REPO` / `GH_DISPATCH_WORKFLOW` is wrong.
- **422:** the workflow is disabled, or the branch's `attendance.yml` doesn't declare the inputs Render sends. Enable it and make sure the branch is up to date.

### Login fails / widget not found
Open the Render **Logs** tab and follow the GitHub run link from the failed job. The run log has the full attempt, and every failure uploads a full-page screenshot as a run artifact:

```bash
gh run download <run-id>
```

Without GitHub dispatch, the screenshot is saved on the Render instance at `/tmp/attendance-debug/<timestamp>-<action>.png` (the free tier has no shell to fetch it).

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
| `app.py` | FastAPI service + APScheduler, endpoints, self-ping, startup catch-up |
| `github_dispatch.py` | Dispatches `attendance.yml`, watches the run, reads run history |
| `.github/workflows/attendance.yml` | The GitHub Actions job that runs the browser |
| `zoho_browser.py` | Playwright automation (login, MFA, click widget) |
| `outlook.py` | Microsoft Graph poll for Zoho OTP emails |
| `Dockerfile` | Builds container; installs `tzdata`, pins Playwright |
| `render.yaml` | Blueprint: web service, env vars, health check, region |
| `requirements.txt` | Python deps with version pins |
| `requirements-dev.txt`, `tests/` | pytest suite (GitHub API faked at the HTTP layer) |

---

<div align="center">
  <b>Built for hassle-free, on-time attendance tracking.</b>
</div>
