#!/usr/bin/env python3
"""Klotimer: professional/fun toilet-time tracker with profiles and leaderboard."""

import csv
import hashlib
import hmac
import html
import io
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from http import cookies
from urllib.parse import parse_qs, quote_plus
from wsgiref.simple_server import make_server

DB_PATH = "klotimer.db"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days

FUN_QUOTES = [
    "Progress is made one flush at a time.",
    "Great dashboards are built on honest data.",
    "Timing today makes habits better tomorrow.",
    "Small sessions, big insights.",
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
    existing_columns = {row[1] for row in cur.fetchall()}
    if "daily_goal_seconds" not in existing_columns:
        cur.execute(
            "ALTER TABLE users ADD COLUMN daily_goal_seconds INTEGER NOT NULL DEFAULT 900"
        )

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
    sess_cols = {row[1] for row in cur.fetchall()}
    if "mood" not in sess_cols:
        cur.execute("ALTER TABLE toilet_sessions ADD COLUMN mood TEXT")

    conn.commit()
    conn.close()


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}${digest.hex()}"


def verify_password(password: str, stored_hash: str) -> bool:
    salt_hex, digest_hex = stored_hash.split("$", 1)
    salt = bytes.fromhex(salt_hex)
    expected = bytes.fromhex(digest_hex)
    got = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(expected, got)


def format_seconds(seconds: int) -> str:
    seconds = max(0, int(seconds or 0))
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def fmt_datetime(ts: int | None) -> str:
    if ts is None:
        return "—"
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def esc(value) -> str:
    return html.escape(str(value), quote=True)


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
    jar = parse_cookies(environ)
    token = jar.get("klotimer_session")
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
        """
        SELECT * FROM toilet_sessions
        WHERE user_id = ? AND end_ts IS NULL
        ORDER BY id DESC LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def create_session(user_id: int):
    token = secrets.token_urlsafe(32)
    expires_at = now_ts() + SESSION_TTL_SECONDS
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO auth_sessions (token, user_id, expires_at) VALUES (?, ?, ?)",
        (token, user_id, expires_at),
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


def respond(start_response, status="200 OK", body="", headers=None):
    hdrs = [
        ("Content-Type", "text/html; charset=utf-8"),
        ("X-Frame-Options", "DENY"),
        ("X-Content-Type-Options", "nosniff"),
        ("Referrer-Policy", "same-origin"),
    ]
    if headers:
        hdrs.extend(headers)
    start_response(status, hdrs)
    return [body.encode("utf-8")]


def respond_bytes(start_response, status="200 OK", body=b"", headers=None):
    hdrs = headers[:] if headers else []
    start_response(status, hdrs)
    return [body]


def redirect(start_response, location, headers=None):
    hdrs = [("Location", location)]
    if headers:
        hdrs.extend(headers)
    start_response("302 Found", hdrs)
    return [b""]


def redirect_with_message(start_response, location: str, message: str, level: str = "ok"):
    sep = "&" if "?" in location else "?"
    return redirect(start_response, f"{location}{sep}msg={quote_plus(message)}&lvl={quote_plus(level)}")


def mood_badge(mood: str | None) -> str:
    emojis = {
        "quick": "⚡ Quick",
        "normal": "🙂 Normal",
        "deep": "🧠 Deep-think",
    }
    return emojis.get((mood or "").lower(), "—")


def user_stats(user_id: int):
    conn = get_db()
    cur = conn.cursor()
    day_start = now_ts() - 86400
    week_start = now_ts() - 7 * 86400

    cur.execute(
        "SELECT COALESCE(SUM(duration_seconds),0) FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL AND end_ts >= ?",
        (user_id, day_start),
    )
    today = cur.fetchone()[0]
    cur.execute(
        "SELECT COALESCE(SUM(duration_seconds),0) FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL AND end_ts >= ?",
        (user_id, week_start),
    )
    week = cur.fetchone()[0]
    cur.execute(
        "SELECT COALESCE(MAX(duration_seconds),0) FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL",
        (user_id,),
    )
    best = cur.fetchone()[0]
    cur.execute(
        "SELECT COALESCE(AVG(duration_seconds),0) FROM toilet_sessions WHERE user_id = ? AND end_ts IS NOT NULL",
        (user_id,),
    )
    avg = int(cur.fetchone()[0] or 0)
    conn.close()
    return {"today": today, "week": week, "best": best, "avg": avg}


def badges(total_seconds: int, sessions_count: int, best_session: int):
    out = []
    if sessions_count >= 1:
        out.append("🆕 First Flush")
    if sessions_count >= 25:
        out.append("🥉 Habit Builder (25 sessions)")
    if sessions_count >= 100:
        out.append("🥇 Throne Veteran (100 sessions)")
    if total_seconds >= 3600:
        out.append("🕐 One Hour Club")
    if total_seconds >= 10 * 3600:
        out.append("🏆 Platinum Plopper")
    if best_session >= 30 * 60:
        out.append("🚨 Marathon Moment")
    return out


def page_layout(title: str, content: str, user=None, flash="", level="ok"):
    nav = "<a href='/'>Home</a> · <a href='/login'>Login</a> · <a href='/register'>Register</a>"
    if user:
        nav = (
            "<a href='/'>Home</a> · <a href='/profile'>Profile</a> · <a href='/history'>History</a> "
            "· <a href='/leaderboard'>Leaderboard</a> · <a href='/settings'>Settings</a> "
            "· <a href='/export.csv'>Export CSV</a>"
            " · <form style='display:inline' method='POST' action='/logout'><button class='link-btn'>Logout</button></form>"
        )

    flash_html = ""
    if flash:
        cls = "flash-ok" if level == "ok" else "flash-warn"
        flash_html = f"<div class='flash {cls}'>{esc(flash)}</div>"

    return f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width,initial-scale=1'>
  <title>{esc(title)} · Klotimer</title>
  <style>
    :root {{ --bg:#f4f8fb; --card:#fff; --ink:#1f2937; --accent:#2563eb; --muted:#6b7280; --border:#e5e7eb; }}
    body {{ font-family: Inter, Segoe UI, Arial, sans-serif; max-width: 980px; margin: 24px auto; padding: 0 14px; background:var(--bg); color:var(--ink); }}
    .brand {{ display:flex; justify-content:space-between; gap:10px; align-items:center; margin-bottom:16px; }}
    .brand h1 {{ margin:0; }}
    .card {{ background:var(--card); border:1px solid var(--border); border-radius:14px; padding:16px; margin:14px 0; box-shadow:0 4px 14px rgba(15,23,42,.04); }}
    .stats {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(170px,1fr)); gap:10px; }}
    .stat {{ border:1px solid var(--border); border-radius:10px; padding:10px; background:#fbfdff; }}
    .kicker {{ color:var(--muted); font-size:.85rem; }}
    a {{ color:var(--accent); text-decoration:none; }}
    input, select, button {{ font-size:1rem; padding:8px 10px; border:1px solid #cfd4dc; border-radius:9px; }}
    button {{ cursor:pointer; background:#111827; color:#fff; border:none; }}
    button.secondary {{ background:#e5e7eb; color:#111827; }}
    .link-btn {{ background:none; color:var(--accent); padding:0; border:none; text-decoration:underline; }}
    table {{ width:100%; border-collapse:collapse; }}
    th, td {{ text-align:left; padding:9px; border-bottom:1px solid var(--border); }}
    .flash {{ border-radius:10px; padding:10px; margin:10px 0; }}
    .flash-ok {{ background:#ecfdf5; border:1px solid #86efac; }}
    .flash-warn {{ background:#fff7ed; border:1px solid #fdba74; }}
    .badge {{ display:inline-block; margin:4px 6px 0 0; padding:5px 9px; border-radius:999px; background:#eef2ff; border:1px solid #c7d2fe; font-size:.85rem; }}
  </style>
</head>
<body>
  <div class='brand'>
    <h1>🚽 Klotimer</h1>
    <div>{nav}</div>
  </div>
  {flash_html}
  {content}
</body>
</html>"""


def home(environ, start_response, user):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(total_seconds), 0) AS total, COUNT(*) AS users_count FROM users")
    totals = cur.fetchone()
    cur.execute("SELECT COUNT(*) FROM toilet_sessions WHERE end_ts IS NULL")
    active_people = cur.fetchone()[0]
    cur.execute(
        "SELECT username, total_seconds FROM users ORDER BY total_seconds DESC, username ASC LIMIT 1"
    )
    champion = cur.fetchone()
    conn.close()

    quote = FUN_QUOTES[now_ts() % len(FUN_QUOTES)]

    champ_html = "No champion yet — be first!"
    if champion:
        champ_html = f"🏅 <b>{esc(champion['username'])}</b> with {format_seconds(champion['total_seconds'])}"

    content = f"""
    <div class='card'>
      <h2>All-time toilet analytics</h2>
      <div class='stats'>
        <div class='stat'><div class='kicker'>Community total</div><div><b>{format_seconds(totals['total'])}</b></div></div>
        <div class='stat'><div class='kicker'>Profiles</div><div><b>{totals['users_count']}</b></div></div>
        <div class='stat'><div class='kicker'>Live sessions right now</div><div><b>{active_people}</b></div></div>
      </div>
      <p style='margin-top:10px'>{champ_html}</p>
      <p class='kicker'>💡 {esc(quote)}</p>
    </div>
    """

    if not user:
        content += """
        <div class='card'>
          <h3>Get started</h3>
          <p>Create a profile and start tracking your routine with leaderboards, history, and badges.</p>
          <a href='/register'><button>Create account</button></a>
        </div>
        """
    else:
        active = get_active_session(user["id"])
        if active:
            elapsed = now_ts() - active["start_ts"]
            content += f"""
            <div class='card'>
              <h3>Current session</h3>
              <p>Running for <b>{format_seconds(elapsed)}</b></p>
              <form method='POST' action='/stop'>
                <label>Mood when done:</label>
                <select name='mood'>
                  <option value='normal'>Normal</option>
                  <option value='quick'>Quick</option>
                  <option value='deep'>Deep-think</option>
                </select>
                <button>Stop session</button>
              </form>
            </div>
            """
        else:
            content += """
            <div class='card'>
              <h3>Ready to track?</h3>
              <form method='POST' action='/start'><button>Start session</button></form>
            </div>
            """

    qp = get_query_params(environ)
    msg = qp.get("msg", [""])[0]
    lvl = qp.get("lvl", ["ok"])[0]
    return respond(start_response, body=page_layout("Home", content, user=user, flash=msg, level=lvl))


def register(environ, start_response, user):
    if environ.get("REQUEST_METHOD") == "GET":
        body = """
        <div class='card'>
          <h2>Create profile</h2>
          <form method='POST' action='/register'>
            <p><input required name='username' minlength='3' maxlength='24' pattern='[A-Za-z0-9_]+' placeholder='Username (letters/numbers/_)'></p>
            <p><input required type='password' name='password' minlength='8' placeholder='Password (min 8 chars)'></p>
            <button>Create account</button>
          </form>
        </div>
        """
        return respond(start_response, body=page_layout("Register", body, user=user))

    data = read_post_data(environ)
    username = (data.get("username", [""])[0]).strip()
    password = data.get("password", [""])[0]

    if not username.replace("_", "").isalnum() or len(username) < 3:
        return redirect_with_message(start_response, "/register", "Username must be at least 3 chars and use letters/numbers/_", "warn")
    if len(password) < 8:
        return redirect_with_message(start_response, "/register", "Password must be at least 8 characters.", "warn")

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
            (username, hash_password(password), now_ts()),
        )
        conn.commit()
        user_id = cur.lastrowid
    except sqlite3.IntegrityError:
        conn.close()
        return redirect_with_message(start_response, "/register", "That username already exists.", "warn")
    conn.close()

    token = create_session(user_id)
    cookie = f"klotimer_session={token}; HttpOnly; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax"
    return redirect(start_response, "/profile?msg=Welcome+to+Klotimer!&lvl=ok", headers=[("Set-Cookie", cookie)])


def login(environ, start_response, user):
    if environ.get("REQUEST_METHOD") == "GET":
        body = """
        <div class='card'>
          <h2>Login</h2>
          <form method='POST' action='/login'>
            <p><input required name='username' placeholder='Username'></p>
            <p><input required type='password' name='password' placeholder='Password'></p>
            <button>Login</button>
          </form>
        </div>
        """
        qp = get_query_params(environ)
        msg = qp.get("msg", [""])[0]
        lvl = qp.get("lvl", ["ok"])[0]
        return respond(start_response, body=page_layout("Login", body, user=user, flash=msg, level=lvl))

    data = read_post_data(environ)
    username = (data.get("username", [""])[0]).strip()
    password = data.get("password", [""])[0]

    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM users WHERE username = ?", (username,))
    found = cur.fetchone()
    conn.close()

    if not found or not verify_password(password, found["password_hash"]):
        return redirect_with_message(start_response, "/login", "Invalid credentials.", "warn")

    token = create_session(found["id"])
    cookie = f"klotimer_session={token}; HttpOnly; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax"
    return redirect(start_response, "/profile", headers=[("Set-Cookie", cookie)])


def require_user(start_response, user):
    if not user:
        return redirect_with_message(start_response, "/login", "Please login first.", "warn")
    return None


def logout(environ, start_response):
    jar = parse_cookies(environ)
    token = jar.get("klotimer_session")
    if token:
        clear_session(token.value)
    cookie = "klotimer_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax"
    return redirect(start_response, "/?msg=Logged+out&lvl=ok", headers=[("Set-Cookie", cookie)])


def profile(environ, start_response, user):
    maybe_redirect = require_user(start_response, user)
    if maybe_redirect:
        return maybe_redirect

    active = get_active_session(user["id"])
    stats = user_stats(user["id"])
    medals = badges(user["total_seconds"], user["sessions_count"], stats["best"])
    badges_html = "".join(f"<span class='badge'>{esc(b)}</span>" for b in medals) or "<span class='kicker'>No badges yet — start your first session.</span>"

    current_block = "<p>No active session.</p><form method='POST' action='/start'><button>Start session</button></form>"
    if active:
        running = now_ts() - active["start_ts"]
        current_block = f"""
        <p>Active for <b>{format_seconds(running)}</b> (started {fmt_datetime(active['start_ts'])}).</p>
        <form method='POST' action='/stop'>
          <label>Mood:</label>
          <select name='mood'>
            <option value='normal'>Normal</option>
            <option value='quick'>Quick</option>
            <option value='deep'>Deep-think</option>
          </select>
          <button>Stop session</button>
        </form>
        """

    goal_pct = min(100, round((stats["today"] / max(1, user["daily_goal_seconds"])) * 100))

    content = f"""
    <div class='card'>
      <h2>{esc(user['username'])}'s profile</h2>
      <div class='stats'>
        <div class='stat'><div class='kicker'>All-time total</div><b>{format_seconds(user['total_seconds'])}</b></div>
        <div class='stat'><div class='kicker'>Sessions</div><b>{user['sessions_count']}</b></div>
        <div class='stat'><div class='kicker'>Average session</div><b>{format_seconds(stats['avg'])}</b></div>
        <div class='stat'><div class='kicker'>Best session</div><b>{format_seconds(stats['best'])}</b></div>
        <div class='stat'><div class='kicker'>Today</div><b>{format_seconds(stats['today'])}</b></div>
        <div class='stat'><div class='kicker'>Last 7 days</div><b>{format_seconds(stats['week'])}</b></div>
      </div>
      <p style='margin-top:8px'>Daily goal progress: <b>{goal_pct}%</b> ({format_seconds(stats['today'])} / {format_seconds(user['daily_goal_seconds'])})</p>
    </div>

    <div class='card'>
      <h3>Current status</h3>
      {current_block}
    </div>

    <div class='card'>
      <h3>Badges</h3>
      <div>{badges_html}</div>
    </div>
    """
    qp = get_query_params(environ)
    msg = qp.get("msg", [""])[0]
    lvl = qp.get("lvl", ["ok"])[0]
    return respond(start_response, body=page_layout("Profile", content, user=user, flash=msg, level=lvl))


def start_session(environ, start_response, user):
    maybe_redirect = require_user(start_response, user)
    if maybe_redirect:
        return maybe_redirect

    if get_active_session(user["id"]):
        return redirect_with_message(start_response, "/profile", "You already have an active session.", "warn")

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO toilet_sessions (user_id, start_ts) VALUES (?, ?)",
        (user["id"], now_ts()),
    )
    conn.commit()
    conn.close()
    return redirect_with_message(start_response, "/profile", "Session started.")


def stop_session(environ, start_response, user):
    maybe_redirect = require_user(start_response, user)
    if maybe_redirect:
        return maybe_redirect

    active = get_active_session(user["id"])
    if not active:
        return redirect_with_message(start_response, "/profile", "No active session to stop.", "warn")

    data = read_post_data(environ)
    mood = (data.get("mood", ["normal"])[0] or "normal").lower()
    if mood not in {"quick", "normal", "deep"}:
        mood = "normal"

    duration = max(0, now_ts() - active["start_ts"])
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        "UPDATE toilet_sessions SET end_ts = ?, duration_seconds = ?, mood = ? WHERE id = ?",
        (now_ts(), duration, mood, active["id"]),
    )
    cur.execute(
        "UPDATE users SET total_seconds = total_seconds + ?, sessions_count = sessions_count + 1 WHERE id = ?",
        (duration, user["id"]),
    )
    conn.commit()
    conn.close()
    return redirect_with_message(start_response, "/profile", f"Session saved ({format_seconds(duration)}).")


def leaderboard(environ, start_response, user):
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT username, total_seconds, sessions_count,
               CASE WHEN sessions_count > 0 THEN CAST(total_seconds / sessions_count AS INTEGER) ELSE 0 END AS avg_seconds
        FROM users
        ORDER BY total_seconds DESC, sessions_count DESC, username ASC
        """
    )
    rows = cur.fetchall()
    conn.close()

    table_rows = "".join(
        (
            f"<tr><td>{i}</td><td>{esc(r['username'])}</td><td>{format_seconds(r['total_seconds'])}</td>"
            f"<td>{r['sessions_count']}</td><td>{format_seconds(r['avg_seconds'])}</td></tr>"
        )
        for i, r in enumerate(rows, start=1)
    )

    body = f"""
    <div class='card'>
      <h2>Leaderboard</h2>
      <table>
        <thead><tr><th>#</th><th>User</th><th>Total</th><th>Sessions</th><th>Avg</th></tr></thead>
        <tbody>{table_rows}</tbody>
      </table>
    </div>
    """
    return respond(start_response, body=page_layout("Leaderboard", body, user=user))


def history(environ, start_response, user):
    maybe_redirect = require_user(start_response, user)
    if maybe_redirect:
        return maybe_redirect

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT start_ts, end_ts, duration_seconds, mood
        FROM toilet_sessions
        WHERE user_id = ? AND end_ts IS NOT NULL
        ORDER BY id DESC
        LIMIT 30
        """,
        (user["id"],),
    )
    rows = cur.fetchall()
    conn.close()

    if not rows:
        body = "<div class='card'><h2>History</h2><p>No completed sessions yet.</p></div>"
    else:
        row_html = "".join(
            f"<tr><td>{fmt_datetime(r['start_ts'])}</td><td>{fmt_datetime(r['end_ts'])}</td><td>{format_seconds(r['duration_seconds'])}</td><td>{mood_badge(r['mood'])}</td></tr>"
            for r in rows
        )
        body = (
            "<div class='card'><h2>Recent sessions</h2><table><thead>"
            "<tr><th>Start</th><th>End</th><th>Duration</th><th>Mood</th></tr></thead>"
            f"<tbody>{row_html}</tbody></table></div>"
        )

    return respond(start_response, body=page_layout("History", body, user=user))


def settings(environ, start_response, user):
    maybe_redirect = require_user(start_response, user)
    if maybe_redirect:
        return maybe_redirect

    if environ.get("REQUEST_METHOD") == "GET":
        body = f"""
        <div class='card'>
          <h2>Settings</h2>
          <form method='POST' action='/settings'>
            <label>Daily goal (minutes)</label>
            <p><input type='number' min='1' max='600' name='daily_goal_minutes' value='{max(1, user['daily_goal_seconds'] // 60)}'></p>
            <button>Save settings</button>
          </form>
        </div>
        """
        qp = get_query_params(environ)
        msg = qp.get("msg", [""])[0]
        lvl = qp.get("lvl", ["ok"])[0]
        return respond(start_response, body=page_layout("Settings", body, user=user, flash=msg, level=lvl))

    data = read_post_data(environ)
    try:
        minutes = int(data.get("daily_goal_minutes", ["15"])[0])
    except ValueError:
        return redirect_with_message(start_response, "/settings", "Daily goal must be a number.", "warn")
    minutes = max(1, min(600, minutes))

    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE users SET daily_goal_seconds = ? WHERE id = ?", (minutes * 60, user["id"]))
    conn.commit()
    conn.close()

    return redirect_with_message(start_response, "/settings", "Settings saved.")


def export_csv(environ, start_response, user):
    maybe_redirect = require_user(start_response, user)
    if maybe_redirect:
        return maybe_redirect

    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT start_ts, end_ts, duration_seconds, mood
        FROM toilet_sessions
        WHERE user_id = ? AND end_ts IS NOT NULL
        ORDER BY id DESC
        """,
        (user["id"],),
    )
    rows = cur.fetchall()
    conn.close()

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(["start_utc", "end_utc", "duration_seconds", "mood"])
    for r in rows:
        writer.writerow([fmt_datetime(r["start_ts"]), fmt_datetime(r["end_ts"]), r["duration_seconds"], r["mood"] or ""])

    data = out.getvalue().encode("utf-8")
    headers = [
        ("Content-Type", "text/csv; charset=utf-8"),
        ("Content-Disposition", f"attachment; filename=klotimer_{user['username']}.csv"),
    ]
    return respond_bytes(start_response, body=data, headers=headers)


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
    }

    handler = routes.get((method, path))
    if handler:
        return handler(environ, start_response, user)

    return respond(
        start_response,
        status="404 Not Found",
        body=page_layout("404", "<div class='card'><h2>404</h2><p>Page not found.</p></div>", user=user),
    )


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    with make_server("0.0.0.0", port, app) as server:
        print(f"Klotimer running at http://localhost:{port}")
        server.serve_forever()
