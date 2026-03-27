# Klotimer

Klotimer is a lightweight, self-hosted web app for tracking all-time toilet time with user profiles.

## What’s included

- Account registration, login, logout.
- Personal dashboard with:
  - all-time total,
  - session count,
  - average and best session,
  - today and 7-day totals,
  - daily goal progress.
- Start/stop session timer.
- Mood tagging (`quick`, `normal`, `deep`).
- Badge milestones.
- Personal session history.
- Community leaderboard.
- CSV export.
- **PWA support** (install as app):
  - web manifest,
  - service worker,
  - install button (`beforeinstallprompt`),
  - custom app icon.

## Run locally

```bash
python3 app.py
```

Open: <http://localhost:8000>

## Notes

- Uses SQLite (`klotimer.db`) for persistence.
- Passwords are hashed with PBKDF2-HMAC-SHA256 + random salts.
- Auth uses HTTP-only session cookies.
- Includes security headers: `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`.
