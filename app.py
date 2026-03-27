#!/usr/bin/env python3
import hashlib
import hmac
import os
import secrets
import sqlite3
import time
from http import cookies
from urllib.parse import parse_qs
from wsgiref.simple_server import make_server

DB_PATH = "klotimer.db"
SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # 14 days


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
            sessions_count INTEGER NOT NULL DEFAULT 0
        )
        """
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
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """
    )
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
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def parse_cookies(environ):
    jar = cookies.SimpleCookie()
    raw = environ.get("HTTP_COOKIE", "")
    if raw:
        jar.load(raw)
    return jar


def get_current_user(environ):
    jar = parse_cookies(environ)
    token = jar.get("klotimer_session")
    if token is None:
        return None

    now = int(time.time())
    conn = get_db()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT users.* FROM auth_sessions
        JOIN users ON users.id = auth_sessions.user_id
        WHERE auth_sessions.token = ? AND auth_sessions.expires_at > ?
        """,
        (token.value, now),
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
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    )
    row = cur.fetchone()
    conn.close()
    return row


def create_session(user_id: int):
    token = secrets.token_urlsafe(32)
    expires_at = int(time.time()) + SESSION_TTL_SECONDS
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


def read_post_data(environ):
    try:
        size = int(environ.get("CONTENT_LENGTH", "0"))
    except ValueError:
        size = 0
    body = environ["wsgi.input"].read(size).decode("utf-8")
    return parse_qs(body)


def html_page(title: str, content: str, user=None, flash=""):
    auth_links = "<a href='/login'>Login</a> · <a href='/register'>Register</a>"
    if user:
        auth_links = (
            f"Logged in as <b>{user['username']}</b> · <a href='/profile'>Profile</a> "
            "· <a href='/leaderboard'>Leaderboard</a> "
            "· <form style='display:inline' method='POST' action='/logout'><button>Logout</button></form>"
        )

    flash_html = f"<p class='flash'>{flash}</p>" if flash else ""
    return f"""<!doctype html>
<html>
<head>
  <meta charset='utf-8'>
  <meta name='viewport' content='width=device-width,initial-scale=1'>
  <title>{title} · Klotimer</title>
  <style>
    body {{ font-family: Arial, sans-serif; max-width: 820px; margin: 32px auto; padding: 0 12px; }}
    .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 16px; margin: 14px 0; }}
    .flash {{ background: #ecfdf5; border: 1px solid #34d399; padding: 10px; border-radius: 8px; }}
    .stats {{ display: flex; gap: 14px; flex-wrap: wrap; }}
    .stat {{ background:#f9fafb; border:1px solid #e5e7eb; border-radius:8px; padding:10px 14px; min-width:170px; }}
    input, button {{ font-size: 1rem; padding: 8px; margin: 4px 0; }}
    table {{ width: 100%; border-collapse: collapse; }}
    td, th {{ border-bottom: 1px solid #eee; padding: 8px; text-align: left; }}
  </style>
</head>
<body>
  <h1>🚽 Klotimer</h1>
  <p>{auth_links}</p>
  {flash_html}
  {content}
</body>
</html>
"""


def respond(start_response, status="200 OK", body="", headers=None):
    hdrs = [("Content-Type", "text/html; charset=utf-8")]
    if headers:
        hdrs.extend(headers)
    start_response(status, hdrs)
    return [body.encode("utf-8")]


def redirect(start_response, location, headers=None):
    hdrs = [("Location", location)]
    if headers:
        hdrs.extend(headers)
    start_response("302 Found", hdrs)
    return [b""]


def app(environ, start_response):
    init_db()
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    user = get_current_user(environ)

    if path == "/":
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COALESCE(SUM(total_seconds), 0) AS total FROM users")
        total_all = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) AS users_count FROM users")
        users_count = cur.fetchone()["users_count"]
        conn.close()

        content = (
            "<div class='card'><h2>All-time toilet time</h2>"
            f"<p><b>{format_seconds(total_all)}</b> total, across <b>{users_count}</b> profiles.</p>"
            "<p>Create an account, start a session when you go, and stop when you're done.</p></div>"
        )
        if user:
            active = get_active_session(user["id"])
            if active:
                live_seconds = int(time.time()) - active["start_ts"]
                content += (
                    "<div class='card'><h3>Your active session</h3>"
                    f"<p>Running for: <b>{format_seconds(live_seconds)}</b></p>"
                    "<form method='POST' action='/stop'><button>Stop session</button></form></div>"
                )
            else:
                content += (
                    "<div class='card'><h3>Ready?</h3>"
                    "<form method='POST' action='/start'><button>Start session</button></form></div>"
                )
        else:
            content += "<div class='card'><p><a href='/register'>Register</a> or <a href='/login'>login</a> to start tracking.</p></div>"

        return respond(start_response, body=html_page("Home", content, user=user))

    if path == "/register" and method == "GET":
        form = """
        <div class='card'><h2>Create profile</h2>
          <form method='POST' action='/register'>
            <div><input required name='username' minlength='3' maxlength='30' placeholder='Username'></div>
            <div><input required type='password' name='password' minlength='6' placeholder='Password'></div>
            <button>Create account</button>
          </form>
        </div>
        """
        return respond(start_response, body=html_page("Register", form, user=user))

    if path == "/register" and method == "POST":
        data = read_post_data(environ)
        username = (data.get("username", [""])[0]).strip()
        password = data.get("password", [""])[0]
        if len(username) < 3 or len(password) < 6:
            return respond(start_response, body=html_page("Register", "<p>Invalid username/password length.</p>", flash="Please use at least 3/6 characters."))

        conn = get_db()
        cur = conn.cursor()
        try:
            cur.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (username, hash_password(password), int(time.time())),
            )
            conn.commit()
            user_id = cur.lastrowid
        except sqlite3.IntegrityError:
            conn.close()
            return respond(start_response, body=html_page("Register", "<p>Username already exists.</p>", flash="Choose another username."))
        conn.close()

        token = create_session(user_id)
        cookie = f"klotimer_session={token}; HttpOnly; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax"
        return redirect(start_response, "/profile", headers=[("Set-Cookie", cookie)])

    if path == "/login" and method == "GET":
        form = """
        <div class='card'><h2>Login</h2>
          <form method='POST' action='/login'>
            <div><input required name='username' placeholder='Username'></div>
            <div><input required type='password' name='password' placeholder='Password'></div>
            <button>Login</button>
          </form>
        </div>
        """
        return respond(start_response, body=html_page("Login", form, user=user))

    if path == "/login" and method == "POST":
        data = read_post_data(environ)
        username = (data.get("username", [""])[0]).strip()
        password = data.get("password", [""])[0]
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM users WHERE username = ?", (username,))
        found = cur.fetchone()
        conn.close()

        if not found or not verify_password(password, found["password_hash"]):
            return respond(start_response, body=html_page("Login", "<p>Invalid credentials.</p>", flash="Wrong username or password."))

        token = create_session(found["id"])
        cookie = f"klotimer_session={token}; HttpOnly; Path=/; Max-Age={SESSION_TTL_SECONDS}; SameSite=Lax"
        return redirect(start_response, "/profile", headers=[("Set-Cookie", cookie)])

    if path == "/logout" and method == "POST":
        jar = parse_cookies(environ)
        token = jar.get("klotimer_session")
        if token:
            clear_session(token.value)
        cookie = "klotimer_session=; HttpOnly; Path=/; Max-Age=0; SameSite=Lax"
        return redirect(start_response, "/", headers=[("Set-Cookie", cookie)])

    if path == "/profile":
        if not user:
            return redirect(start_response, "/login")
        active = get_active_session(user["id"])
        active_html = "<p>No active toilet session.</p>"
        action_html = "<form method='POST' action='/start'><button>Start session</button></form>"
        if active:
            running = int(time.time()) - active["start_ts"]
            active_html = f"<p>Active for <b>{format_seconds(running)}</b></p>"
            action_html = "<form method='POST' action='/stop'><button>Stop session</button></form>"

        body = f"""
        <div class='card'>
          <h2>{user['username']}'s profile</h2>
          <div class='stats'>
            <div class='stat'><small>All-time total</small><div><b>{format_seconds(user['total_seconds'])}</b></div></div>
            <div class='stat'><small>Completed sessions</small><div><b>{user['sessions_count']}</b></div></div>
          </div>
          <h3>Current status</h3>
          {active_html}
          {action_html}
        </div>
        """
        return respond(start_response, body=html_page("Profile", body, user=user))

    if path == "/start" and method == "POST":
        if not user:
            return redirect(start_response, "/login")
        if get_active_session(user["id"]):
            return redirect(start_response, "/profile")

        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO toilet_sessions (user_id, start_ts) VALUES (?, ?)",
            (user["id"], int(time.time())),
        )
        conn.commit()
        conn.close()
        return redirect(start_response, "/profile")

    if path == "/stop" and method == "POST":
        if not user:
            return redirect(start_response, "/login")
        active = get_active_session(user["id"])
        if not active:
            return redirect(start_response, "/profile")

        now = int(time.time())
        duration = max(0, now - active["start_ts"])
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "UPDATE toilet_sessions SET end_ts = ?, duration_seconds = ? WHERE id = ?",
            (now, duration, active["id"]),
        )
        cur.execute(
            """
            UPDATE users
            SET total_seconds = total_seconds + ?, sessions_count = sessions_count + 1
            WHERE id = ?
            """,
            (duration, user["id"]),
        )
        conn.commit()
        conn.close()
        return redirect(start_response, "/profile")

    if path == "/leaderboard":
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            "SELECT username, total_seconds, sessions_count FROM users ORDER BY total_seconds DESC, username ASC"
        )
        rows = cur.fetchall()
        conn.close()
        body_rows = "".join(
            f"<tr><td>{i}</td><td>{r['username']}</td><td>{format_seconds(r['total_seconds'])}</td><td>{r['sessions_count']}</td></tr>"
            for i, r in enumerate(rows, start=1)
        )
        body = (
            "<div class='card'><h2>Leaderboard</h2>"
            "<table><thead><tr><th>#</th><th>User</th><th>All-time</th><th>Sessions</th></tr></thead>"
            f"<tbody>{body_rows}</tbody></table></div>"
        )
        return respond(start_response, body=html_page("Leaderboard", body, user=user))

    return respond(start_response, status="404 Not Found", body=html_page("404", "<p>Page not found.</p>", user=user))


if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", "8000"))
    with make_server("0.0.0.0", port, app) as server:
        print(f"Klotimer running at http://localhost:{port}")
        server.serve_forever()
