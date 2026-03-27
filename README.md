# Klotimer

Klotimer is a simple web app to track all-time time spent on the toilet.

## Features

- Account registration and login.
- Personal profile with all-time total time and number of sessions.
- Start/stop toilet sessions.
- Global all-time counter across all users.
- Leaderboard with ranked profiles.

## Run locally

```bash
python3 app.py
```

Then open <http://localhost:8000>.

## Notes

- Data is stored in `klotimer.db` (SQLite).
- Sessions are stored in an HTTP-only cookie.
