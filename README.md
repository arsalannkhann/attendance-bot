# Zoho Attendance Bot 🤖

Automated check-in / check-out for Zoho People — runs on **Render** (or locally via cron).

## Schedule

| Action      | Time (IST)  | Daily |
|-------------|-------------|-------|
| ✅ Check-In  | 2:00 PM     | Yes   |
| 🚪 Check-Out | 11:59 PM    | Yes   |

A random 30s–3min human-like delay is added before each action.

---

## Deploy to Render (Recommended)

### 1. Push to GitHub

```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/YOUR_USERNAME/attendance-bot.git
git push -u origin main
```

### 2. Create Render Web Service

1. Go to [render.com](https://render.com) → **New → Web Service**
2. Connect your GitHub repo
3. Settings:
   - **Environment**: Docker
   - **Plan**: Free
   - **Region**: Oregon (or closest to you)

### 3. Set Environment Variables

In Render dashboard → **Environment** tab, add:

| Key                    | Value                              |
|------------------------|------------------------------------|
| `ZOHO_EMAIL`           | your_zoho_email@company.com        |
| `ZOHO_PASSWORD`        | your_zoho_password                 |
| `OUTLOOK_EMAIL`        | your_outlook_email@outlook.com     |
| `OUTLOOK_APP_PASSWORD` | your_outlook_app_password          |
| `RENDER_EXTERNAL_URL`  | *(auto-set by Render after deploy)*|

> **Important:** After first deploy, copy the Render URL (e.g. `https://zoho-attendance-bot.onrender.com`) and set it as `RENDER_EXTERNAL_URL`. This enables the 10-min self-ping keep-alive.

### 4. Deploy

Render auto-deploys on `git push`. The bot will:
- Start a web server on port `5000`
- Schedule check-in at 2:00 PM IST & check-out at 11:59 PM IST
- Self-ping every 10 minutes to prevent idle shutdown

---

## Run Locally (Mac)

### Setup

```bash
bash setup.sh        # Install dependencies
```

### Option A: Cron (recommended for local)

```bash
bash setup_cron.sh   # Installs cron jobs
```

### Option B: Run server locally

```bash
pip3 install -r requirements.txt
python3 server.py
```

---

## Files

| File               | Purpose                                    |
|--------------------|--------------------------------------------|
| `server.py`        | Flask server + scheduler (for Render)      |
| `zoho_bot.py`      | Core bot logic (login, OTP, attendance)    |
| `Dockerfile`       | Docker config for Render deployment        |
| `render.yaml`      | Render blueprint for one-click deploy      |
| `setup.sh`         | Local Mac setup script                     |
| `setup_cron.sh`    | Local cron job setup                       |
| `requirements.txt` | Python dependencies                        |
| `.env`             | Local credentials (not committed)          |

---

## Logs

- **Render**: View logs in the Render dashboard
- **Local**: Check `bot.log` and `cron.log`
