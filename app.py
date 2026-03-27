#!/usr/bin/env python3
"""Klotimer: modern toilet-time tracker with installable PWA support."""

import csv
import hashlib
import hmac
import html
import io
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http import cookies
from urllib.parse import parse_qs, quote_plus
from wsgiref.simple_server import make_server

DB_PATH = "klotimer.db"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14

FUN_QUOTES = [
    "Progress is made one flush at a time.",
    "Small habits, big dashboards.",
    "Track honestly, improve steadily.",
    "Every minute tells a story.",
]


def now_ts() -> int:
    return int(time.time())


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            total_seconds INTEGER NOT NULL DEFAULT 0,
            sessions_count INTEGER NOT NULL DEFAULT 0,
            daily_goal_seconds INTEGER NOT NULL DEFAULT 900
        )
        """
    )
    cur.execute("PRAGMA table_info(users)")
    u_cols = {row[1] for row in cur.fetchall()}
    if "daily_goal_seconds" not in u_cols:
        cur.execute("ALTER TABLE users ADD COLUMN daily_goal_seconds INTEGER NOT NULL DEFAULT 900")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS auth_sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            expires_at INTEGER NOT NULL,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS toilet_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            start_ts INTEGER NOT NULL,
            end_ts INTEGER,
            duration_seconds INTEGER,
            mood TEXT,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
    cur.execute("PRAGMA table_info(toilet_sessions)")
    s_cols = {row[1] for row in cur.fetchall()}
    if "mood" not in s_cols:
        cur.execute("ALTER TABLE toilet_sessions ADD COLUMN mood TEXT")

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    salt_hex, digest_hex = stored_hash.split("$", 1)
    got = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000
    )
    return hmac.compare_digest(bytes.fromhex(digest_hex), got)


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def fmt_datetime(ts: int | None) -> str:
    if ts is None:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def esc(v) -> str:
    return html.escape(str(v), quote=True)


def parse_cookies(environ):
    jar = cookies.SimpleCookie()
    raw = environ.get("HTTP_COOKIE", "")
    if raw:
        jar.load(raw)
    return jar


def read_post_data(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        size = 0
    body = environ["wsgi.input"].read(size).decode("utf-8")
    return parse_qs(body)


def get_query_params(environ):
    return parse_qs(environ.get("QUERY_STRING", ""))


def get_current_user(environ):
    token = parse_cookies(environ).get("klotimer_session")
    if token is None:
        return None
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT users.* FROM auth_sessions
        JOIN users ON users.id = auth_sessions.user_id
        WHERE auth_sessions.token = ? AND auth_sessions.expires_at > ?
        """,
        (token.value, now_ts()),
    )
    user = cur.fetchone()
    conn.close()
    return user


def get_active_session(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT * FROM toilet_sessions WHERE user_id = ? AND end_ts IS NULL ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def create_session(user_id: int):
    token = secrets.token_urlsafe(32)
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO auth_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, now_ts() + SESSION_TTL_SECONDS),
    )
    conn.commit()
    conn.close()
    return token


def clear_session(token: str):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM auth_sessions WHERE token = ?", (token,))
    conn.commit()
    conn.close()


def base_headers(content_type="text/html; charset=utf-8"):
    return [
        ("Content-Type", content_type),
        ("X-Frame-Options", "DENY"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "same-origin"),
    ]


def respond(start_response, status="200 OK", body="", headers=None):
    hdrs = base_headers()
    if headers:
        hdrs.extend(headers)
    start_response(status, hdrs)
    return [body.encode("utf-8")]


def respond_bytes(start_response, body: bytes, content_type: str, headers=None, status="200 OK"):
    hdrs = base_headers(content_type)
    if headers:
        hdrs.extend(headers)
    start_response(status, hdrs)
    return [body]


def redirect(start_response, location, headers=None):
    hdrs = [("Location", location)]
    if headers:
        hdrs.extend(headers)
    start_response("302 Found", hdrs)
    return [b""]


def redirect_with_message(start_response, path, msg, lvl="ok"):
    sep = "&" if "?" in path else "?"
    return redirect(start_response, f"{path}{sep}msg={quote_plus(msg)}&lvl={quote_plus(lvl)}")


def user_stats(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    day_start = now_ts() - 86400
    week_start = now_ts() - 7 * 86400
    cur.execute(
        "SELECT COALESCE(SUM(duration_seconds), 0) FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL AND end_ts >= ?",
        (user_id, day_start),
    )
    today = cur.fetchone()[0]
    cur.execute(
        "SELECT COALESCE(SUM(duration_seconds), 0) FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL AND end_ts >= ?",
        (user_id, week_start),
    )
    week = cur.fetchone()[0]
    cur.execute(
        "SELECT COALESCE(MAX(duration_seconds), 0), COALESCE(AVG(duration_seconds), 0) FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL",
        (user_id,),
    )
    best, avg = cur.fetchone()
    conn.close()
    return {"today": today, "week": week, "best": best, "avg": int(avg or 0)}


def badges(total: int, sessions: int, best: int):
    out = []
    if sessions >= 1:
        out.append("🆕 First Flush")
    if sessions >= 25:
        out.append("🥉 Habit Builder")
    if sessions >= 100:
        out.append("🥇 Throne Veteran")
    if total >= 3600:
        out.append("🕐 One Hour Club")
    if total >= 10 * 3600:
        out.append("🏆 Platinum Plopper")
    if best >= 30 * 60:
        out.append("🚨 Marathon Moment")
    return out


def mood_badge(mood: str | None):
    return {"quick": "⚡ Quick", "normal": "🙂 Normal", "deep": "🧠 Deep"}.get(
        (mood or "").lower(), "—"
    )


def pwa_install_js():
    return """
    <script>
    if ('serviceWorker' in navigator) {
      window.addEventListener('load', () => navigator.serviceWorker.register('/sw.js').catch(()=>{}));
    }
    let deferredPrompt;
    window.addEventListener('beforeinstallprompt', (e) => {
      e.preventDefault();
      deferredPrompt = e;
      const btn = document.getElementById('install-app-btn');
      if (btn) btn.style.display = 'inline-flex';
    });
    window.addEventListener('DOMContentLoaded', () => {
      const btn = document.getElementById('install-app-btn');
      if (!btn) return;
      btn.addEventListener('click', async () => {
        if (!deferredPrompt) return;
        deferredPrompt.prompt();
        await deferredPrompt.userChoice;
        deferredPrompt = null;
        btn.style.display = 'none';
      });
    });
    </script>
    """


def page(title: str, content: str, user=None, flash="", level="ok"):
    nav = "<a href='/'>Home</a> · <a href='/login'>Login</a> · <a href='/register'>Register</a>"
    if user:
        nav = (
            "<a href='/'>Home</a> · <a href='/profile'>Profile</a> · <a href='/history'>History</a> · "
            "<a href='/leaderboard'>Leaderboard</a> · <a href='/settings'>Settings</a> · <a href='/export.csv'>Export</a> "
            "· <form style='display:inline' method='POST' action='/logout'><button class='link-btn'>Logout</button></form>"
        )

    alert = ""
    if flash:
        cls = "ok" if level == "ok" else "warn"
        alert = f"<div class='alert {cls}'>{esc(flash)}</div>"

    return f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width,initial-scale=1,viewport-fit=cover'>
  <meta name='theme-color' content='#0f172a'>
  <link rel='manifest' href='/manifest.webmanifest'>
  <link rel='icon' href='/icon.svg' type='image/svg+xml'>
  <title>{esc(title)} · Klotimer</title>
  <style>
    :root {{ --bg:#020617; --glass:rgba(15,23,42,.65); --card:rgba(15,23,42,.84); --ink:#e2e8f0; --muted:#94a3b8; --line:#334155; --acc:#22d3ee; --good:#22c55e; --warn:#f97316; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; background:radial-gradient(circle at 15% 10%,#0f766e 0%,#020617 40%), #020617; color:var(--ink); font-family:Inter,Segoe UI,Arial,sans-serif; min-height:100vh; }}
    .wrap {{ width:min(980px, 94vw); margin:18px auto 44px; }}
    .top {{ background:var(--glass); border:1px solid var(--line); backdrop-filter: blur(12px); border-radius:16px; padding:14px 16px; position:sticky; top:10px; z-index:20; }}
    h1 {{ margin:0; font-size:1.35rem; }}
    .nav {{ margin-top:8px; color:var(--muted); font-size:.95rem; }}
    a {{ color:var(--acc); text-decoration:none; }}
    .grid {{ display:grid; gap:12px; grid-template-columns:repeat(auto-fit,minmax(165px,1fr)); }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:16px; padding:15px; margin-top:12px; box-shadow:0 12px 28px rgba(2,6,23,.35); }}
    .label {{ color:var(--muted); font-size:.83rem; }}
    .value {{ font-size:1.25rem; font-weight:700; margin-top:4px; }}
    .btn, button {{ border:none; border-radius:10px; padding:10px 13px; font-size:.95rem; cursor:pointer; background:linear-gradient(90deg,#06b6d4,#3b82f6); color:white; }}
    .btn.secondary, button.secondary {{ background:#1e293b; border:1px solid #334155; }}
    input, select {{ width:100%; max-width:340px; border-radius:10px; border:1px solid #334155; padding:10px; background:#0f172a; color:#e2e8f0; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ text-align:left; padding:8px; border-bottom:1px solid #334155; }}
    .alert {{ border-radius:12px; padding:9px 12px; margin-top:12px; }}
    .alert.ok {{ border:1px solid rgba(34,197,94,.45); background:rgba(34,197,94,.12); }}
    .alert.warn {{ border:1px solid rgba(249,115,22,.45); background:rgba(249,115,22,.12); }}
    .badge {{ display:inline-block; margin:4px 6px 0 0; padding:5px 9px; border-radius:999px; border:1px solid #334155; background:#0f172a; font-size:.82rem; }}
    .link-btn {{ border:none; padding:0; background:none; color:var(--acc); text-decoration:underline; }}
    .install-btn {{ display:none; margin-left:8px; }}
    @media (max-width:680px) {{ .top {{ position:static; }} }}
  </style>
</head>
<body>
  <div class='wrap'>
    <div class='top'>
      <h1>🚽 Klotimer <button id='install-app-btn' class='btn secondary install-btn'>Als App installieren</button></h1>
      <div class='nav'>{nav}</div>
    </div>
    {alert}
    {content}
  </div>
  {pwa_install_js()}
</body>
</html>"""


def require_user(user, start_response):
    if user:
        return None
    return redirect_with_message(start_response, "/login", "Bitte zuerst einloggen.", "warn")


def home(environ, start_response, user):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(total_seconds),0), COUNT(*) FROM users")
    total, users_count = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM toilet_sessions WHERE end_ts IS NULL")
    live = cur.fetchone()[0]
    cur.execute("SELECT username, total_seconds FROM users ORDER BY total_seconds DESC LIMIT 1")
    top = cur.fetchone()
    conn.close()

    champion = "Noch kein Champion — sei der/die Erste!"
    if top:
        champion = f"🏅 {esc(top['username'])}: {format_seconds(top['total_seconds'])}"

    content = f"""
    <div class='card'>
      <h2>Community Dashboard</h2>
      <div class='grid'>
        <div><div class='label'>All-time Gesamtzeit</div><div class='value'>{format_seconds(total)}</div></div>
        <div><div class='label'>Profile</div><div class='value'>{users_count}</div></div>
        <div><div class='label'>Aktive Sessions jetzt</div><div class='value'>{live}</div></div>
      </div>
      <p style='margin-top:10px'>{champion}</p>
      <p class='label'>💡 {esc(FUN_QUOTES[now_ts() % len(FUN_QUOTES)])}</p>
    </div>
    """

    if user:
        active = get_active_session(user["id"])
        if active:
            elapsed = now_ts() - active["start_ts"]
            content += f"""
            <div class='card'>
              <h3>Deine laufende Session</h3>
              <p><b>{format_seconds(elapsed)}</b> seit Start.</p>
              <form method='POST' action='/stop'>
                <p><select name='mood'>
                  <option value='normal'>🙂 Normal</option>
                  <option value='quick'>⚡ Quick</option>
                  <option value='deep'>🧠 Deep</option>
                </select></p>
                <button>Session stoppen</button>
              </form>
            </div>
            """
        else:
            content += "<div class='card'><h3>Tracken starten</h3><form method='POST' action='/start'><button>Session starten</button></form></div>"
    else:
        content += "<div class='card'><h3>Los geht's</h3><p>Erstelle ein Profil und nutze Klotimer auch als installierbare App.</p><a class='btn' href='/register'>Profil erstellen</a></div>"

    q = get_query_params(environ)
    return respond(start_response, body=page("Home", content, user, q.get("msg", [""])[0], q.get("lvl", ["ok"])[0]))


def register(environ, start_response, user):
    if environ.get("REQUEST_METHOD") == "GET":
        body = """
        <div class='card'><h2>Registrieren</h2>
          <form method='POST' action='/register'>
            <p><input required name='username' minlength='3' maxlength='24' pattern='[A-Za-z0-9_]+' placeholder='Username'></p>
            <p><input required type='password' name='password' minlength='8' placeholder='Passwort (mind. 8 Zeichen)'></p>
            <button>Konto erstellen</button>
          </form>
        </div>
        """
        q = get_query_params(environ)
        return respond(start_response, body=page("Register", body, user, q.get("msg", [""])[0], q.get("lvl", ["ok"])[0]))

    data = read_post_data(environ)
    username = (data.get("username", [""])[0]).strip()
    password = data.get("password", [""])[0]
    if not username.replace("_", "").isalnum() or len(username) < 3:
        return redirect_with_message(start_response, "/register", "Username: nur Buchstaben/Zahlen/_ und min. 3 Zeichen.", "warn")
    if len(password) < 8:
        return redirect_with_message(start_response, "/register", "Passwort muss mindestens 8 Zeichen haben.", "warn")

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)", (username, hash_password(password), now_ts()))
        conn.commit()
        user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return redirect_with_message(start_response, "/register", "Username existiert bereits.", "warn")
    conn.close()

    token = create_session(user_id)
    cookie = f"klotimer_session={token}; HttpOnly; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax"
    return redirect(start_response, "/profile?msg=Willkommen+bei+Klotimer!&lvl=ok", headers=[("Set-Cookie", cookie)])


def login(environ, start_response, user):
    if environ.get("REQUEST_METHOD") == "GET":
        body = """
        <div class='card'><h2>Login</h2>
          <form method='POST' action='/login'>
            <p><input required name='username' placeholder='Username'></p>
            <p><input required type='password' name='password' placeholder='Passwort'></p>
            <button>Einloggen</button>
          </form>
        </div>
        """
        q = get_query_params(environ)
        return respond(start_response, body=page("Login", body, user, q.get("msg", [""])[0], q.get("lvl", ["ok"])[0]))

    data = read_post_data(environ)
    username = (data.get("username", [""])[0]).strip()
    password = data.get("password", [""])[0]
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    found = cur.fetchone()
    conn.close()

    if not found or not verify_password(password, found["password_hash"]):
        return redirect_with_message(start_response, "/login", "Login fehlgeschlagen.", "warn")

    token = create_session(found["id"])
    cookie = f"klotimer_session={token}; HttpOnly; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax"
    return redirect(start_response, "/profile", headers=[("Set-Cookie", cookie)])


def logout(environ, start_response):
    token = parse_cookies(environ).get("klotimer_session")
    if token:
        clear_session(token.value)
    cookie = "klotimer_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax"
    return redirect(start_response, "/?msg=Erfolgreich+ausgeloggt.&lvl=ok", headers=[("Set-Cookie", cookie)])


def profile(environ, start_response, user):
    denied = require_user(user, start_response)
    if denied:
        return denied

    active = get_active_session(user["id"])
    stats = user_stats(user["id"])
    goal_pct = min(100, round((stats["today"] / max(1, user["daily_goal_seconds"])) * 100))
    badge_html = "".join(f"<span class='badge'>{esc(b)}</span>" for b in badges(user["total_seconds"], user["sessions_count"], stats["best"])) or "<span class='label'>Noch keine Badges.</span>"

    current = "<p>Keine aktive Session.</p><form method='POST' action='/start'><button>Session starten</button></form>"
    if active:
        current = f"""
        <p>Aktiv seit <b>{format_seconds(now_ts()-active['start_ts'])}</b> ({fmt_datetime(active['start_ts'])})</p>
        <form method='POST' action='/stop'>
          <p><select name='mood'><option value='normal'>🙂 Normal</option><option value='quick'>⚡ Quick</option><option value='deep'>🧠 Deep</option></select></p>
          <button>Session stoppen</button>
        </form>
        """

    body = f"""
    <div class='card'>
      <h2>{esc(user['username'])}</h2>
      <div class='grid'>
        <div><div class='label'>All-time</div><div class='value'>{format_seconds(user['total_seconds'])}</div></div>
        <div><div class='label'>Sessions</div><div class='value'>{user['sessions_count']}</div></div>
        <div><div class='label'>Durchschnitt</div><div class='value'>{format_seconds(stats['avg'])}</div></div>
        <div><div class='label'>Best Session</div><div class='value'>{format_seconds(stats['best'])}</div></div>
        <div><div class='label'>Heute</div><div class='value'>{format_seconds(stats['today'])}</div></div>
        <div><div class='label'>7 Tage</div><div class='value'>{format_seconds(stats['week'])}</div></div>
      </div>
      <p style='margin-top:10px'>Tagesziel: <b>{goal_pct}%</b> ({format_seconds(stats['today'])} / {format_seconds(user['daily_goal_seconds'])})</p>
    </div>
    <div class='card'><h3>Status</h3>{current}</div>
    <div class='card'><h3>Badges</h3>{badge_html}</div>
    """
    q = get_query_params(environ)
    return respond(start_response, body=page("Profile", body, user, q.get("msg", [""])[0], q.get("lvl", ["ok"])[0]))


def start_session(environ, start_response, user):
    denied = require_user(user, start_response)
    if denied:
        return denied
    if get_active_session(user["id"]):
        return redirect_with_message(start_response, "/profile", "Session läuft bereits.", "warn")

    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO toilet_sessions (user_id, start_ts) VALUES (?, ?)", (user["id"], now_ts()))
    conn.commit()
    conn.close()
    return redirect_with_message(start_response, "/profile", "Session gestartet.")


def stop_session(environ, start_response, user):
    denied = require_user(user, start_response)
    if denied:
        return denied

    active = get_active_session(user["id"])
    if not active:
        return redirect_with_message(start_response, "/profile", "Keine aktive Session.", "warn")

    data = read_post_data(environ)
    mood = (data.get("mood", ["normal"])[0] or "normal").lower()
    if mood not in {"quick", "normal", "deep"}:
        mood = "normal"

    duration = max(0, now_ts() - active["start_ts"])
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE toilet_sessions SET end_ts = ?, duration_seconds = ?, mood = ? WHERE id = ?", (now_ts(), duration, mood, active["id"]))
    cur.execute("UPDATE users SET total_seconds = total_seconds + ?, sessions_count = sessions_count + 1 WHERE id = ?", (duration, user["id"]))
    conn.commit()
    conn.close()
    return redirect_with_message(start_response, "/profile", f"Session gespeichert ({format_seconds(duration)}).")


def leaderboard(environ, start_response, user):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT username, total_seconds, sessions_count,
               CASE WHEN sessions_count>0 THEN CAST(total_seconds/sessions_count AS INTEGER) ELSE 0 END AS avg_seconds
        FROM users
        ORDER BY total_seconds DESC, sessions_count DESC, username ASC
        """
    )
    rows = cur.fetchall()
    conn.close()
    rows_html = "".join(
        f"<tr><td>{i}</td><td>{esc(r['username'])}</td><td>{format_seconds(r['total_seconds'])}</td><td>{r['sessions_count']}</td><td>{format_seconds(r['avg_seconds'])}</td></tr>"
        for i, r in enumerate(rows, start=1)
    )
    body = f"<div class='card'><h2>Leaderboard</h2><table><thead><tr><th>#</th><th>User</th><th>Total</th><th>Sessions</th><th>Avg</th></tr></thead><tbody>{rows_html}</tbody></table></div>"
    return respond(start_response, body=page("Leaderboard", body, user))


def history(environ, start_response, user):
    denied = require_user(user, start_response)
    if denied:
        return denied

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT start_ts, end_ts, duration_seconds, mood FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL ORDER BY id DESC LIMIT 40",
        (user["id"],),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        body = "<div class='card'><h2>History</h2><p>Noch keine abgeschlossenen Sessions.</p></div>"
    else:
        rows_html = "".join(
            f"<tr><td>{fmt_datetime(r['start_ts'])}</td><td>{fmt_datetime(r['end_ts'])}</td><td>{format_seconds(r['duration_seconds'])}</td><td>{mood_badge(r['mood'])}</td></tr>"
            for r in rows
        )
        body = f"<div class='card'><h2>History</h2><table><thead><tr><th>Start</th><th>Ende</th><th>Dauer</th><th>Mood</th></tr></thead><tbody>{rows_html}</tbody></table></div>"
    return respond(start_response, body=page("History", body, user))


def settings(environ, start_response, user):
    denied = require_user(user, start_response)
    if denied:
        return denied

    if environ.get("REQUEST_METHOD") == "GET":
        body = f"""
        <div class='card'><h2>Settings</h2>
          <form method='POST' action='/settings'>
            <p class='label'>Tagesziel in Minuten</p>
            <p><input type='number' name='daily_goal_minutes' min='1' max='600' value='{max(1, user['daily_goal_seconds']//60)}'></p>
            <button>Speichern</button>
          </form>
        </div>
        """
        q = get_query_params(environ)
        return respond(start_response, body=page("Settings", body, user, q.get("msg", [""])[0], q.get("lvl", ["ok"])[0]))

    data = read_post_data(environ)
    try:
        minutes = int(data.get("daily_goal_minutes", ["15"])[0])
    except ValueError:
        return redirect_with_message(start_response, "/settings", "Bitte Zahl eingeben.", "warn")
    minutes = max(1, min(600, minutes))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET daily_goal_seconds = ? WHERE id = ?", (minutes * 60, user["id"]))
    conn.commit()
    conn.close()
    return redirect_with_message(start_response, "/settings", "Gespeichert.")


def export_csv(environ, start_response, user):
    denied = require_user(user, start_response)
    if denied:
        return denied

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "SELECT start_ts, end_ts, duration_seconds, mood FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL ORDER BY id DESC",
        (user["id"],),
    )
    rows = cur.fetchall()
    conn.close()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["start_utc", "end_utc", "duration_seconds", "mood"])
    for r in rows:
        writer.writerow([fmt_datetime(r["start_ts"]), fmt_datetime(r["end_ts"]), r["duration_seconds"], r["mood"] or ""])

    return respond_bytes(
        start_response,
        out.getvalue().encode("utf-8"),
        "text/csv; charset=utf-8",
        headers=[("Content-Disposition", f"attachment; filename=klotimer_{user['username']}.csv")],
    )


def manifest(environ, start_response, _user):
    payload = {
        "name": "Klotimer",
        "short_name": "Klotimer",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#020617",
        "theme_color": "#0f172a",
        "description": "Toilet-time tracker with profile stats and leaderboard.",
        "icons": [
            {"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}
        ],
    }
    return respond_bytes(start_response, json.dumps(payload).encode("utf-8"), "application/manifest+json; charset=utf-8")


def service_worker(environ, start_response, _user):
    script = """
const CACHE = 'klotimer-v1';
const CORE = ['/', '/manifest.webmanifest', '/icon.svg'];
self.addEventListener('install', e => e.waitUntil(caches.open(CACHE).then(c => c.addAll(CORE))));
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => {
  if (e.request.method !== 'GET') return;
  e.respondWith(caches.match(e.request).then(r => r || fetch(e.request).then(res => {
    const copy = res.clone();
    caches.open(CACHE).then(c => c.put(e.request, copy));
    return res;
  }).catch(() => caches.match('/'))));
});
"""
    return respond_bytes(
        start_response,
        script.encode("utf-8"),
        "application/javascript; charset=utf-8",
        headers=[("Cache-Control", "no-cache")],
    )


def icon(environ, start_response, _user):
    svg = """<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 512 512'>
<defs><linearGradient id='g' x1='0' x2='1'><stop stop-color='#06b6d4'/><stop offset='1' stop-color='#2563eb'/></linearGradient></defs>
<rect width='512' height='512' rx='110' fill='#020617'/>
<rect x='130' y='125' width='252' height='120' rx='54' fill='url(#g)'/>
<rect x='170' y='220' width='172' height='90' rx='38' fill='#0f172a' stroke='url(#g)' stroke-width='14'/>
<circle cx='256' cy='360' r='65' fill='url(#g)'/>
</svg>"""
    return respond_bytes(start_response, svg.encode("utf-8"), "image/svg+xml; charset=utf-8")


def app(environ, start_response):
    init_db()
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    user = get_current_user(environ)

    routes = {
        ("GET", "/"): home,
        ("GET", "/register"): register,
        ("POST", "/register"): register,
        ("GET", "/login"): login,
        ("POST", "/login"): login,
        ("POST", "/logout"): lambda e, s, u: logout(e, s),
        ("GET", "/profile"): profile,
        ("POST", "/start"): start_session,
        ("POST", "/stop"): stop_session,
        ("GET", "/leaderboard"): leaderboard,
        ("GET", "/history"): history,
        ("GET", "/settings"): settings,
        ("POST", "/settings"): settings,
        ("GET", "/export.csv"): export_csv,
        ("GET", "/manifest.webmanifest"): manifest,
        ("GET", "/sw.js"): service_worker,
        ("GET", "/icon.svg"): icon,
    }

    handler = routes.get((method, path))
    if handler:
        return handler(environ, start_response, user)

    body = "<div class='card'><h2>404</h2><p>Seite nicht gefunden.</p></div>"
    return respond(start_response, status="404 Not Found", body=page("404", body, user))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    with make_server("0.0.0.0", port, app) as server:
        print(f"Klotimer running at http://localhost:{port}")
        server.serve_forever()
