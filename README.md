# Klotimer

Klotimer is a lightweight, self-hosted web app for tracking all-time toilet time with user profiles.

## What’s included

- Account registration/login/logout.
- Personal dashboard with:
  - total time,
  - session count,
  - average and best session,
  - today and 7-day totals,
  - daily goal progress.
- Start/stop timer for each session.
- Session mood tagging (`quick`, `normal`, `deep-think`).
- Badge system for milestones.
- Personal session history.
- Community leaderboard.
- CSV export for personal data.

## Run locally

```bash
python3 app.py
```

Open: <http://localhost:8000>

## Data & security notes

- Uses SQLite (`klotimer.db`) for persistence.
- Passwords are stored as PBKDF2-HMAC-SHA256 hashes with random salts.
- Auth uses HTTP-only cookie sessions.
- Adds basic secure response headers (`X-Frame-Options`, `nosniff`, `Referrer-Policy`).
