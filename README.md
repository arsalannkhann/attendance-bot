<div align="center">

# 🤖 Zoho Attendance Bot

**Automated check-in & check-out for Zoho People — runs 24/7 on Render**

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

---

`2:00 PM IST → Check-In` · `11:59 PM IST → Check-Out` · `Mon–Fri` · `Fully Automated`

</div>

---

## ✨ Features

| Feature | Description |
|:---:|---|
| 🕐 | **Scheduled attendance** — Check-in at 2:00 PM, check-out at 11:59 PM IST |
| 📅 | **Weekdays only** — Runs Monday through Friday |
| 🧠 | **Smart toggle** — Detects button state, won't double check-in/out |
| 🔄 | **Session management** — Saves login session, auto-refreshes on expiry |
| 🫀 | **Self-ping keep-alive** — 10-min ping prevents Render free-tier shutdown |
| 🕵️ | **Human-like delays** — Random 30s–3min delay before each action |
| 📸 | **Screenshot proof** — Captures page state before every action |

---

## 🚀 Deploy to Render

### 1. Push to GitHub

```bash
git init && git add . && git commit -m "init"
git remote add origin https://github.com/YOUR_USERNAME/zoho-bot.git
git push -u origin main
```

### 2. Create Web Service on Render

> **[render.com](https://render.com)** → New → Web Service → Connect repo

| Setting | Value |
|---|---|
| **Runtime** | Docker |
| **Plan** | Free |
| **Branch** | `main` |
| **Health Check** | `/health` |

### 3. Set Environment Variables

| Variable | Value |
|---|---|
| `ZOHO_EMAIL` | Your Zoho email |
| `ZOHO_PASSWORD` | Your Zoho password |
| `RENDER_EXTERNAL_URL` | `https://your-app.onrender.com` *(set after first deploy)* |

> ⚠️ **Important:** Set `RENDER_EXTERNAL_URL` to your Render URL after the first deploy. This enables the self-ping keep-alive.

### 4. Deploy 🎉

Render auto-deploys on every `git push`. The bot will:
- Start a web server on port `5000`
- Schedule check-in/check-out (Mon–Fri, IST)
- Self-ping every 10 minutes

---

## 🏗️ Architecture

```
┌─────────────────────────────────────────────┐
│              Render (Docker)                │
│                                             │
│  ┌──────────┐    ┌────────────────────────┐ │
│  │ Flask    │    │ APScheduler            │ │
│  │ /health  │◄───│                        │ │
│  │ /        │    │ 2:00 PM  → check-in    │ │
│  └──────────┘    │ 11:59 PM → check-out   │ │
│       ▲          │ Every 10m → self-ping   │ │
│       │          └────────┬───────────────┘ │
│       │                   │                 │
│  Self-ping           ┌────▼─────┐           │
│  (keep alive)        │ zoho_bot │           │
│                      │ Playwright│          │
│                      └────┬─────┘           │
└───────────────────────────┼─────────────────┘
                            │
                   ┌────────▼────────┐
                   │  Zoho People    │
                   │  people.zoho.in │
                   └─────────────────┘
```

---

## 📁 Project Structure

```
attendance_bot/
├── server.py          # Flask + APScheduler (entry point for Render)
├── zoho_bot.py        # Core bot — login, session, attendance
├── Dockerfile         # Python 3.11 + Chromium
├── render.yaml        # Render blueprint
├── requirements.txt   # Python dependencies
├── .gitignore         # Excludes secrets, logs, screenshots
└── .env               # Local credentials (not committed)
```

---

## 🛠️ Local Development

```bash
# Install dependencies
pip3 install -r requirements.txt
python3 -m playwright install chromium

# Run directly
ZOHO_ACTION=checkin python3 zoho_bot.py

# Run server locally
python3 server.py
```

---

## 📝 Logs

- **Render** → Dashboard → Logs tab
- **Local** → `bot.log`

---

<div align="center">

Made with ❤️ for automating the mundane

</div>
