"""SQLite-basierte Nutzerdatenbank mit JWT-Auth."""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import sqlite3
import string
import time
import uuid
from contextlib import contextmanager
from typing import Any

from config import UserDBConfig

try:
    from jose import jwt, JWTError
except ImportError:
    from jose import jwt  # type: ignore[no-redef]
    JWTError = Exception  # type: ignore[assignment,misc]

try:
    import bcrypt as _bcrypt
except ImportError:
    _bcrypt = None  # type: ignore[assignment]


# ── Password Hashing ────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt."""
    # Truncate to 72 bytes (bcrypt limit)
    pw_bytes = plain.encode("utf-8")[:72]
    if _bcrypt is not None:
        return _bcrypt.hashpw(pw_bytes, _bcrypt.gensalt()).decode("utf-8")
    # Fallback: SHA-256 (less secure, but works without bcrypt)
    return "sha256:" + hashlib.sha256(pw_bytes).hexdigest()


def verify_password(plain: str, hashed: str) -> bool:
    """Verify a plaintext password against its hash."""
    pw_bytes = plain.encode("utf-8")[:72]
    if _bcrypt is not None and not hashed.startswith("sha256:"):
        try:
            return _bcrypt.checkpw(pw_bytes, hashed.encode("utf-8"))
        except Exception:
            return False
    if hashed.startswith("sha256:"):
        return hashed == "sha256:" + hashlib.sha256(pw_bytes).hexdigest()
    return False


# ── JWT ──────────────────────────────────────────────────────────

JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 15
REFRESH_TOKEN_EXPIRE_DAYS = 7


def _get_jwt_secret() -> str:
    secret = os.getenv("JWT_SECRET", "")
    if not secret:
        # Auto-generate a secret for development (will change on restart)
        secret = secrets.token_hex(32)
        os.environ["JWT_SECRET"] = secret
    return secret


def create_access_token(user_id: str, tier: str, admin: bool) -> str:
    """Create a short-lived JWT access token."""
    payload = {
        "sub": user_id,
        "tier": tier,
        "admin": admin,
        "type": "access",
        "exp": time.time() + ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "iat": time.time(),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def create_refresh_token(user_id: str, expire_days: int = REFRESH_TOKEN_EXPIRE_DAYS) -> str:
    """Create a long-lived JWT refresh token."""
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": time.time() + expire_days * 86400,
        "iat": time.time(),
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT token. Raises JWTError on failure."""
    return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])


# ── User Database ────────────────────────────────────────────────

class UserDB:
    """Thread-safe SQLite user database following the ClaimCache pattern."""

    def __init__(self, config: UserDBConfig) -> None:
        self.config = config
        self._db_path = config.db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id           TEXT PRIMARY KEY,
                    email        TEXT UNIQUE,
                    password_hash TEXT,
                    display_name TEXT NOT NULL DEFAULT '',
                    tier         TEXT NOT NULL DEFAULT 'lite',
                    admin        INTEGER NOT NULL DEFAULT 0,
                    telegram_id  TEXT UNIQUE,
                    consent      INTEGER NOT NULL DEFAULT 0,
                    created_at   REAL NOT NULL,
                    last_login   REAL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS usage_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id    TEXT NOT NULL,
                    tier_used  TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    claims     INTEGER NOT NULL DEFAULT 0,
                    rating     TEXT,
                    source     TEXT NOT NULL DEFAULT 'web',
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_user ON usage_log(user_id, created_at DESC)"
            )
            # Auto-migrate: add consent column if missing
            cols = {r[1] for r in conn.execute("PRAGMA table_info(users)").fetchall()}
            if "consent" not in cols:
                conn.execute("ALTER TABLE users ADD COLUMN consent INTEGER NOT NULL DEFAULT 0")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS telegram_link_codes (
                    code        TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL,
                    created_at  REAL NOT NULL,
                    expires_at  REAL NOT NULL,
                    FOREIGN KEY (user_id) REFERENCES users(id)
                )
                """
            )
            self._init_registration_codes(conn)

    @contextmanager
    def _connect(self):
        conn = sqlite3.connect(self._db_path, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    # ── CRUD ─────────────────────────────────────────────────────

    def create_user(
        self,
        email: str | None = None,
        password: str | None = None,
        display_name: str = "",
        tier: str = "lite",
        admin: int = 0,
        telegram_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Create a new user. Returns the user dict or None if email/telegram_id exists."""
        user_id = str(uuid.uuid4())
        now = time.time()
        pw_hash = hash_password(password) if password else None

        try:
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO users (id, email, password_hash, display_name, tier, admin, telegram_id, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, email, pw_hash, display_name, tier, admin, telegram_id, now),
                )
        except sqlite3.IntegrityError:
            return None

        return self.get_by_id(user_id)

    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        """Find a user by ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_by_email(self, email: str) -> dict[str, Any] | None:
        """Find a user by email."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
        return dict(row) if row else None

    def get_by_telegram_id(self, telegram_id: str) -> dict[str, Any] | None:
        """Find a user by Telegram ID."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM users WHERE telegram_id = ?", (str(telegram_id),)
            ).fetchone()
        return dict(row) if row else None

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        """Verify email + password. Returns user dict or None."""
        user = self.get_by_email(email)
        if user is None or not user.get("password_hash"):
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        self.update_last_login(user["id"])
        return user

    def set_credentials(self, user_id: str, email: str, password: str) -> bool:
        """Set email + password on an existing user (e.g. Telegram-only account)."""
        pw_hash = hash_password(password)
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE users SET email = ?, password_hash = ? WHERE id = ?",
                    (email, pw_hash, user_id),
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False  # Email already taken

    def update_last_login(self, user_id: str) -> None:
        """Update the last_login timestamp."""
        with self._connect() as conn:
            conn.execute("UPDATE users SET last_login = ? WHERE id = ?", (time.time(), user_id))

    def update_tier(self, user_id: str, tier: str) -> bool:
        """Update a user's tier. Returns True if the user existed."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET tier = ? WHERE id = ?", (tier, user_id)
            )
            return cursor.rowcount > 0

    def update_admin(self, user_id: str, admin: int) -> bool:
        """Update a user's admin status."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET admin = ? WHERE id = ?", (admin, user_id)
            )
            return cursor.rowcount > 0

    def link_telegram(self, user_id: str, telegram_id: str) -> bool:
        """Link a Telegram ID to an existing user."""
        try:
            with self._connect() as conn:
                cursor = conn.execute(
                    "UPDATE users SET telegram_id = ? WHERE id = ?",
                    (str(telegram_id), user_id),
                )
                return cursor.rowcount > 0
        except sqlite3.IntegrityError:
            return False

    def update_display_name(self, user_id: str, display_name: str) -> bool:
        """Update a user's display name. Returns True if the user existed."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET display_name = ? WHERE id = ?", (display_name, user_id)
            )
            return cursor.rowcount > 0

    def set_consent(self, user_id: str, consent: bool = True) -> bool:
        """Set the logging consent flag for a user."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET consent = ? WHERE id = ?", (1 if consent else 0, user_id)
            )
            return cursor.rowcount > 0

    def has_consent(self, user_id: str) -> bool:
        """Check if a user has given logging consent."""
        with self._connect() as conn:
            row = conn.execute("SELECT consent FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row and row["consent"])

    def unlink_telegram(self, user_id: str) -> bool:
        """Remove the Telegram link from a user."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE users SET telegram_id = NULL WHERE id = ?", (user_id,)
            )
            return cursor.rowcount > 0

    # ── Registration / invite codes ────────────────────────────────

    def _init_registration_codes(self, conn) -> None:
        """Create registration_codes table if missing (called from _init_db)."""
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS registration_codes (
                id          TEXT PRIMARY KEY,
                code        TEXT UNIQUE NOT NULL,
                created_by  TEXT NOT NULL,
                label       TEXT NOT NULL DEFAULT '',
                max_uses    INTEGER NOT NULL DEFAULT 1,
                used_count  INTEGER NOT NULL DEFAULT 0,
                is_active   INTEGER NOT NULL DEFAULT 1,
                created_at  REAL NOT NULL,
                expires_at  REAL,
                FOREIGN KEY (created_by) REFERENCES users(id)
            )
            """
        )

    def create_registration_code(
        self,
        admin_user_id: str,
        label: str = "",
        max_uses: int = 1,
        expires_days: int | None = None,
    ) -> dict[str, Any]:
        """Generate a unique invite code (FNG-XXXXXXXX). Returns the code record."""
        code_id = str(uuid.uuid4())
        raw = secrets.token_hex(4).upper()
        code = f"FNG-{raw}"
        now = time.time()
        expires_at = (now + expires_days * 86400) if expires_days else None

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO registration_codes
                    (id, code, created_by, label, max_uses, used_count, is_active, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, 0, 1, ?, ?)
                """,
                (code_id, code, admin_user_id, label, max_uses, now, expires_at),
            )
        return {
            "id": code_id,
            "code": code,
            "created_by": admin_user_id,
            "label": label,
            "max_uses": max_uses,
            "used_count": 0,
            "is_active": 1,
            "created_at": now,
            "expires_at": expires_at,
        }

    def validate_and_consume_registration_code(self, code: str) -> bool:
        """Atomically validate and consume an invite code.

        Returns True if the code was valid and consumed, False otherwise.
        Uses a single UPDATE with WHERE guards to avoid race conditions.
        """
        now = time.time()
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE registration_codes
                SET used_count = used_count + 1
                WHERE code = ?
                  AND is_active = 1
                  AND used_count < max_uses
                  AND (expires_at IS NULL OR expires_at > ?)
                """,
                (code, now),
            )
            return cursor.rowcount > 0

    def list_registration_codes(self) -> list[dict[str, Any]]:
        """Return all registration codes ordered by creation date (newest first)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM registration_codes ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_registration_code(self, code_id: str) -> bool:
        """Deactivate a registration code. Returns True if found and updated."""
        with self._connect() as conn:
            cursor = conn.execute(
                "UPDATE registration_codes SET is_active = 0 WHERE id = ?",
                (code_id,),
            )
            return cursor.rowcount > 0

    # ── Telegram link codes ───────────────────────────────────────

    def create_link_code(self, user_id: str, ttl: int = 600) -> str:
        """Generate a 6-char alphanumeric code for Telegram linking.

        TTL defaults to 10 minutes. Cleans up expired codes and any
        existing codes for this user before creating a new one.
        """
        now = time.time()
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))

        with self._connect() as conn:
            # Cleanup: remove expired codes and existing codes for this user
            conn.execute("DELETE FROM telegram_link_codes WHERE expires_at < ?", (now,))
            conn.execute("DELETE FROM telegram_link_codes WHERE user_id = ?", (user_id,))
            conn.execute(
                "INSERT INTO telegram_link_codes (code, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (code, user_id, now, now + ttl),
            )
        return code

    def verify_link_code(self, code: str, telegram_id: str) -> dict[str, Any] | None:
        """Verify a link code and bind the Telegram ID to the user.

        Returns the user dict on success, None if the code is invalid/expired
        or the telegram_id is already linked to another account.
        """
        now = time.time()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT user_id FROM telegram_link_codes WHERE code = ? AND expires_at > ?",
                (code, now),
            ).fetchone()
            if not row:
                return None
            user_id = row["user_id"]
            # Delete the used code
            conn.execute("DELETE FROM telegram_link_codes WHERE code = ?", (code,))

        # Link the telegram_id to the user
        if not self.link_telegram(user_id, telegram_id):
            return None  # telegram_id already taken

        return self.get_by_id(user_id)

    def delete_user(self, user_id: str) -> bool:
        """Delete a user by ID."""
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
            return cursor.rowcount > 0

    def list_users(self) -> list[dict[str, Any]]:
        """List all users with usage stats (admin function)."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.id, u.email, u.display_name, u.tier, u.admin,
                       u.telegram_id, u.created_at, u.last_login,
                       COALESCE(s.total, 0) AS analyses_total,
                       COALESCE(s.month, 0) AS analyses_month,
                       s.last_analysis
                FROM users u
                LEFT JOIN (
                    SELECT user_id,
                           COUNT(*) AS total,
                           SUM(CASE WHEN created_at > ? THEN 1 ELSE 0 END) AS month,
                           MAX(created_at) AS last_analysis
                    FROM usage_log GROUP BY user_id
                ) s ON s.user_id = u.id
                ORDER BY u.created_at DESC
                """,
                (time.time() - 30 * 86400,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        """Return total user count."""
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]

    # ── Usage tracking ───────────────────────────────────────────

    def log_usage(
        self,
        user_id: str,
        tier_used: str,
        claims: int = 0,
        rating: str | None = None,
        source: str = "web",
    ) -> None:
        """Log a completed analysis for usage tracking."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO usage_log (user_id, tier_used, created_at, claims, rating, source)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, tier_used, time.time(), claims, rating, source),
            )

    def get_user_usage(self, user_id: str, days: int = 30) -> list[dict[str, Any]]:
        """Get usage log for a specific user."""
        cutoff = time.time() - days * 86400
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT tier_used, created_at, claims, rating, source
                FROM usage_log WHERE user_id = ? AND created_at > ?
                ORDER BY created_at DESC
                """,
                (user_id, cutoff),
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Migration helper ─────────────────────────────────────────

    def migrate_from_json(self, json_path: str) -> int:
        """Import users from the old users.json file. Returns count of imported users."""
        try:
            with open(json_path, encoding="utf-8") as f:
                data = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return 0

        imported = 0
        for u in data.get("users", []):
            telegram_id = str(u.get("user_id", ""))
            if not telegram_id:
                continue
            # Skip if already migrated
            if self.get_by_telegram_id(telegram_id) is not None:
                continue
            tier = u.get("tier", "lite")
            admin = u.get("admin", 0)
            result = self.create_user(
                telegram_id=telegram_id,
                tier=tier,
                admin=admin,
                display_name=f"Telegram {telegram_id}",
            )
            if result:
                imported += 1

        return imported
