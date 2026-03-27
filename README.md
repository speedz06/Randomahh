# Klotimer

Klotimer is a lightweight, self-hosted web app for tracking all-time toilet time with user profiles.

## What’s included

- Account registration, login, logout.
- Personal dashboard with:
  - all-time total,
  - session count,
  - average and best session,
  - today and 7-day totals,
  - current/best streaks,
  - 7-day day-by-day activity view,
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

Healthcheck endpoint: <http://localhost:8000/health>

## Einfach hosten (für Freunde)

Am schnellsten geht es mit einem kleinen PaaS-Anbieter:

### Option A: Render / Railway (einfachster Start)

1. Repo zu GitHub pushen.
2. Bei Render oder Railway ein neues **Web Service** Projekt aus dem Repo erstellen.
3. Start command setzen:

   ```bash
   python3 app.py
   ```

4. Environment variable setzen:
   - `PORT` wird i.d.R. automatisch gesetzt.
5. Deploy starten und den öffentlichen Link mit Freunden teilen.

Hinweis: Die SQLite-Datei (`klotimer.db`) ist auf vielen Free-Tiers nicht dauerhaft persistent. Für ernsthafte Nutzung:
- persistentes Volume aktivieren **oder**
- auf Postgres migrieren.

### Option B: Selbst hosten auf einem VPS (mehr Kontrolle)

1. Ubuntu-Server (z. B. Hetzner, Netcup, DigitalOcean) erstellen.
2. Python installieren, Repo klonen, App mit `screen`/`tmux` testen.
3. Mit `systemd` als Dienst laufen lassen.
4. Nginx als Reverse-Proxy vor die App setzen.
5. TLS per Let's Encrypt (`certbot`) aktivieren.

Damit bekommen deine Freunde eine sichere URL wie `https://deinedomain.de`.

### Option C: Im Heimnetz selbst hosten

Für "selber Host sein":
- App auf einem Raspberry Pi oder Mini-PC starten.
- Router-Portweiterleitung + DynDNS einrichten.
- Unbedingt TLS (z. B. über Cloudflare Tunnel oder Caddy) nutzen.

Das ist günstig, aber mehr Wartung (Strom, Internet-Ausfälle, Sicherheit).

## Notes

- Uses SQLite (`klotimer.db`) for persistence.
- Passwords are hashed with PBKDF2-HMAC-SHA256 + random salts.
- Auth uses HTTP-only session cookies.
- Includes security headers: `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy`.
