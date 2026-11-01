"""Users, passwords and server-side sessions.

Passwords: scrypt (stdlib). Sessions: a random token in an httpOnly cookie; only its sha256 is stored, so
revoking a session or deactivating a user takes effect on the very next request.
"""
import base64
import hashlib
import hmac
import os
import secrets
import time
from contextvars import ContextVar
from datetime import datetime, timedelta, timezone

from shared import db
from identity.perms import ROLES

current_user: ContextVar[dict | None] = ContextVar("current_user", default=None)
COOKIE = "session"
MIN_PASSWORD = 10
_N, _R, _P = 2 ** 14, 8, 1


class AuthError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(h).decode()}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(h))
    except (ValueError, TypeError):
        return False


_DUMMY = hash_password(secrets.token_hex(8))  # verify against this for unknown emails: same timing as a real miss


def _public(u: dict) -> dict:
    return {k: u[k] for k in ("id", "email", "name", "role", "active", "created_at", "last_login") if k in u}


def _check_password_policy(pw: str) -> None:
    if len(pw) < MIN_PASSWORD:
        raise AuthError(422, f"Password must be at least {MIN_PASSWORD} characters")


# ---------- login throttle ----------
# ponytail: in-process counter; move to Postgres when there are several backend replicas.
_FAILS: dict[str, list[float]] = {}
MAX_FAILS, WINDOW = 5, 15 * 60


def _throttled(email: str) -> bool:
    now = time.time()
    _FAILS[email] = [t for t in _FAILS.get(email, []) if now - t < WINDOW]
    return len(_FAILS[email]) >= MAX_FAILS


# ---------- sessions ----------

def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def login(email: str, password: str) -> tuple[dict, str]:
    """Returns (user, raw session token). Raises AuthError(401|429)."""
    email = email.strip().lower()
    if _throttled(email):
        raise AuthError(429, "Too many attempts. Try again in 15 minutes.")
    u = db.fetch1("SELECT * FROM users WHERE email=%s", (email,))
    ok = verify_password(password, u["pw_hash"] if u else _DUMMY) and u is not None and u["active"]
    import audit
    if not ok:
        _FAILS.setdefault(email, []).append(time.time())
        audit.record_audit("auth.login_failed", "user", email, f"Failed sign-in for {email}", actor="system")
        raise AuthError(401, "Wrong email or password")
    _FAILS.pop(email, None)
    token = secrets.token_urlsafe(32)
    days = int(os.getenv("SESSION_DAYS", "7"))
    with db.tx() as c:
        c.execute("INSERT INTO sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
                  (_token_hash(token), u["id"], datetime.now(timezone.utc) + timedelta(days=days)))
        c.execute("UPDATE users SET last_login=now() WHERE id=%s", (u["id"],))
        audit.record_audit("auth.login", "user", str(u["id"]), f"{u['email']} signed in", actor=u["email"],
                           actor_role=u["role"], conn=c)
    return _public(u), token


def user_for_token(token: str | None) -> dict | None:
    if not token:
        return None
    return db.fetch1("""SELECT u.id, u.email, u.name, u.role::text AS role FROM sessions s JOIN users u ON u.id = s.user_id
                        WHERE s.token_hash=%s AND s.revoked_at IS NULL AND s.expires_at > now() AND u.active""",
                     (_token_hash(token),))


def logout(token: str | None) -> None:
    if token:
        db.execute("UPDATE sessions SET revoked_at=now() WHERE token_hash=%s AND revoked_at IS NULL", (_token_hash(token),))


def change_password(user: dict, current: str, new: str, keep_token: str | None) -> None:
    _check_password_policy(new)
    u = db.fetch1("SELECT * FROM users WHERE id=%s", (user["id"],))
    if not verify_password(current, u["pw_hash"]):
        raise AuthError(401, "Current password is wrong")
    with db.tx() as c:
        c.execute("UPDATE users SET pw_hash=%s WHERE id=%s", (hash_password(new), u["id"]))
        c.execute("UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL AND token_hash<>%s",
                  (u["id"], _token_hash(keep_token or "")))
        import audit
        audit.record_audit("auth.password_changed", "user", str(u["id"]), f"{u['email']} changed their password", conn=c)


# ---------- user admin ----------

def list_users() -> list[dict]:
    return db.fetch("""SELECT id, email, name, role::text AS role, active, created_at, last_login FROM users
                       ORDER BY active DESC, role, name""")


def create_user(email: str, name: str, role: str, password: str, actor: str | None = None, conn=None) -> dict:
    email = email.strip().lower()
    if role not in ROLES:
        raise AuthError(422, f"Unknown role {role}")
    _check_password_policy(password)
    row = db.fetch1("""INSERT INTO users (email, name, role, pw_hash) VALUES (%s, %s, %s, %s)
                       ON CONFLICT (email) DO NOTHING
                       RETURNING id, email, name, role::text AS role, active, created_at, last_login""",
                    (email, name.strip(), role, hash_password(password)), conn=conn)
    if not row:
        raise AuthError(409, "A user with that email already exists")
    import audit
    audit.record_audit("user.created", "user", str(row["id"]), f"Created {role} user {email}",
                       **({"actor": actor} if actor else {}), conn=conn)
    return row


def _active_admins(conn, excluding: int) -> int:
    return conn.execute("SELECT count(*) AS n FROM users WHERE role='admin' AND active AND id<>%s", (excluding,)).fetchone()["n"]


def update_user(uid: int, name: str | None = None, role: str | None = None, active: bool | None = None,
                password: str | None = None) -> dict:
    with db.tx() as c:
        u = c.execute("SELECT * FROM users WHERE id=%s FOR UPDATE", (uid,)).fetchone()
        if not u:
            raise AuthError(404, "User not found")
        c.execute("SELECT pg_advisory_xact_lock(7243003)")  # serialise admin-count checks
        losing_admin = u["role"] == "admin" and u["active"] and (
            (role is not None and role != "admin") or active is False)
        if losing_admin and _active_admins(c, uid) == 0:
            raise AuthError(409, "This is the last active admin. Make someone else an admin first.")
        if role is not None and role not in ROLES:
            raise AuthError(422, f"Unknown role {role}")
        changes, sets, params = [], [], []
        for col, val in (("name", name), ("role", role), ("active", active)):
            if val is not None and val != u[col]:
                sets.append(f"{col}=%s")
                params.append(val)
                changes.append(f"{col} {u[col]} -> {val}")
        if password:
            _check_password_policy(password)
            sets.append("pw_hash=%s")
            params.append(hash_password(password))
            changes.append("password reset")
        if sets:
            c.execute(f"UPDATE users SET {', '.join(sets)} WHERE id=%s", (*params, uid))
        if active is False or password or (role is not None and role != u["role"]):
            c.execute("UPDATE sessions SET revoked_at=now() WHERE user_id=%s AND revoked_at IS NULL", (uid,))
        if changes:
            import audit
            audit.record_audit("user.updated", "user", str(uid), f"Updated {u['email']}: {'; '.join(changes)}", conn=c)
        return c.execute("""SELECT id, email, name, role::text AS role, active, created_at, last_login
                            FROM users WHERE id=%s""", (uid,)).fetchone()


def ensure_user(email: str, name: str, role: str, password: str) -> bool:
    """Create the user if missing. Never touches an existing user's password. Returns True if created."""
    if db.fetch1("SELECT 1 AS x FROM users WHERE email=%s", (email.lower(),)):
        return False
    try:
        create_user(email, name, role, password, actor="system")
        return True
    except AuthError as e:
        if e.status == 409:  # another replica created it first
            return False
        raise
