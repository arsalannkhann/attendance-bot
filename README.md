# Zoho People Attendance — GitHub Actions

Automatically checks in at **12:00 PM IST** and checks out at **10:00 PM IST**, Monday–Friday. No laptop required.

## Repository structure

```
├── run_once.py                        # The script (reads env vars)
├── requirements.txt
└── .github/
    └── workflows/
        └── attendance.yml             # Cron workflow
```

## Setup (5 minutes)

### 1. Create a private GitHub repository

Keep it private — your Zoho credentials live here as Secrets.

### 2. Add your files

Push all files from this folder to the repo root.

### 3. Add GitHub Secrets

Go to: **Repo → Settings → Secrets and variables → Actions → New repository secret**

Add these four secrets:

| Secret name           | Value                              |
|-----------------------|------------------------------------|
| `ZOHO_CLIENT_ID`      | `1000.3K4NRW...`                   |
| `ZOHO_CLIENT_SECRET`  | `44141658...`                      |
| `ZOHO_REFRESH_TOKEN`  | `1000.xxxx.yyyy` (from token exchange) |
| `ZOHO_EMAIL_ID`       | `you@company.com`                  |

### 4. Enable Actions

Go to **Repo → Actions** and click **"I understand my workflows, go ahead and enable them"** if prompted.

### 5. Test manually

Go to **Actions → Zoho People Attendance → Run workflow** → choose `checkin` or `checkout` → click **Run workflow**.

Watch the logs — you should see `CHECK-IN successful!` or `CHECK-OUT successful!`.

## Schedule

| Job       | IST time  | UTC cron       |
|-----------|-----------|----------------|
| Check-in  | 12:00 PM  | `30 6 * * 1-5` |
| Check-out | 10:00 PM  | `30 16 * * 1-5`|

> GitHub Actions cron may run up to ~5 minutes late under high load — this is normal and acceptable.

## Monitoring

- Each run appears in **Actions** tab with full logs
- Failed runs are shown in red — GitHub emails you by default if a scheduled job fails (check your GitHub notification settings)
- Logs show the exact timestamp posted to Zoho

## Troubleshooting

| Error | Fix |
|-------|-----|
| `Missing required environment variables` | Check all 4 secrets are added correctly |
| `Token refresh failed` | Refresh token expired or wrong — redo the token exchange |
| `401 Unauthorized` | Wrong client ID/secret, or scope missing |
| `Attendance API error` | Check Zoho People portal — duplicate entry or policy issue |
| Workflow not triggering | Check Actions is enabled; GitHub may skip crons on inactive repos — push a commit to wake it up |

## Keeping the repo active

GitHub disables scheduled workflows on repos with **no activity for 60 days**. To prevent this, either:
- Push a trivial commit every month, or
- Star your own repo (doesn't count), or
- Add a `keep-alive` workflow that runs weekly and commits a timestamp file

```yaml
# .github/workflows/keep-alive.yml
name: Keep alive
on:
  schedule:
    - cron: '0 0 * * 0'   # Every Sunday midnight UTC
jobs:
  ping:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: |
          git config user.email "actions@github.com"
          git config user.name "GitHub Actions"
          echo "$(date -u)" > .keepalive
          git add .keepalive
          git commit -m "chore: keep-alive [skip ci]" || echo "Nothing to commit"
          git push
```
