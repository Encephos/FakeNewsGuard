"""PostgreSQL-Backends für UserDB, AnalysisArchive und CrossReferenceGraph.

Drop-in-Ersatz für die jeweiligen SQLite-Implementierungen – gleiche
öffentliche Schnittstelle, produktionstaugliche Persistenz.

Vorteile gegenüber SQLite:
  - ACID + echte Concurrent Writes (kein WAL-Limit)
  - pg_tsvector für Volltextsuche (statt FTS5)
  - Connection Pooling via psycopg3-pool
  - Multi-Prozess-fähig (Backend + Telegram-Bot teilen eine DB)

Aktivierung: DB_BACKEND=postgres (Fallback: sqlite)

Benötigt: psycopg[binary,pool] >= 3.1
    pip install "psycopg[binary,pool]"
"""

from __future__ import annotations

import json
import random
import secrets
import string
import time
import uuid
from contextlib import contextmanager
from typing import Any

from config import PostgreSQLConfig, UserDBConfig, ArchiveConfig, GraphConfig


def _get_conn(pg_cfg: PostgreSQLConfig):
    """Gibt eine psycopg3-Verbindung zurück."""
    try:
        import psycopg
    except ImportError as exc:
        raise ImportError(
            "Das 'psycopg[binary]'-Paket fehlt. "
            "Bitte 'pip install psycopg[binary,pool]' ausführen."
        ) from exc
    return psycopg.connect(pg_cfg.dsn, autocommit=False)


@contextmanager
def _pg(pg_cfg: PostgreSQLConfig):
    """Kontext-Manager: öffnet Verbindung, committed oder rollback."""
    conn = _get_conn(pg_cfg)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── PostgreSQL UserDB ─────────────────────────────────────────────────────────


class PgUserDB:
    """PostgreSQL-Implementierung der UserDB.

    Identische öffentliche Schnittstelle wie tools/user_db.UserDB.
    Erstellt Tabellen automatisch beim ersten Start (idempotent).
    """

    def __init__(self, pg_cfg: PostgreSQLConfig) -> None:
        self._cfg = pg_cfg
        self._init_db()

    def _init_db(self) -> None:
        with _pg(self._cfg) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id            TEXT PRIMARY KEY,
                    email         TEXT UNIQUE,
                    password_hash TEXT,
                    display_name  TEXT NOT NULL DEFAULT '',
                    tier          TEXT NOT NULL DEFAULT 'lite',
                    admin         INTEGER NOT NULL DEFAULT 0,
                    telegram_id   TEXT UNIQUE,
                    consent       INTEGER NOT NULL DEFAULT 0,
                    created_at    DOUBLE PRECISION NOT NULL,
                    last_login    DOUBLE PRECISION
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS usage_log (
                    id         BIGSERIAL PRIMARY KEY,
                    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    tier_used  TEXT NOT NULL,
                    created_at DOUBLE PRECISION NOT NULL,
                    claims     INTEGER NOT NULL DEFAULT 0,
                    rating     TEXT,
                    source     TEXT NOT NULL DEFAULT 'web'
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_usage_user "
                "ON usage_log(user_id, created_at DESC)"
            )
            conn.execute("ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS total_tokens INTEGER DEFAULT 0")
            conn.execute("ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS estimated_cost_usd DOUBLE PRECISION DEFAULT 0.0")
            conn.execute("ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS estimated_co2_grams DOUBLE PRECISION DEFAULT 0.0")
            conn.execute("ALTER TABLE usage_log ADD COLUMN IF NOT EXISTS analysis_id TEXT")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS telegram_link_codes (
                    code        TEXT PRIMARY KEY,
                    user_id     TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    created_at  DOUBLE PRECISION NOT NULL,
                    expires_at  DOUBLE PRECISION NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS registration_codes (
                    id          TEXT PRIMARY KEY,
                    code        TEXT UNIQUE NOT NULL,
                    created_by  TEXT NOT NULL REFERENCES users(id),
                    label       TEXT NOT NULL DEFAULT '',
                    max_uses    INTEGER NOT NULL DEFAULT 1,
                    used_count  INTEGER NOT NULL DEFAULT 0,
                    is_active   INTEGER NOT NULL DEFAULT 1,
                    created_at  DOUBLE PRECISION NOT NULL,
                    expires_at  DOUBLE PRECISION
                )
            """)

    @contextmanager
    def _conn(self):
        with _pg(self._cfg) as conn:
            yield conn

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create_user(
        self,
        email: str | None = None,
        password: str | None = None,
        display_name: str = "",
        tier: str = "lite",
        admin: int = 0,
        telegram_id: str | None = None,
    ) -> dict[str, Any] | None:
        from tools.user_db import hash_password
        user_id = str(uuid.uuid4())
        now = time.time()
        pw_hash = hash_password(password) if password else None
        try:
            with self._conn() as conn:
                conn.execute(
                    """
                    INSERT INTO users
                        (id, email, password_hash, display_name, tier, admin, telegram_id, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (user_id, email, pw_hash, display_name, tier, admin, telegram_id, now),
                )
        except Exception:
            return None
        return self.get_by_id(user_id)

    def get_by_id(self, user_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_by_email(self, email: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            cur = conn.execute("SELECT * FROM users WHERE email = %s", (email,))
            row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def get_by_telegram_id(self, telegram_id: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM users WHERE telegram_id = %s", (str(telegram_id),)
            )
            row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        return dict(zip(cols, row))

    def authenticate(self, email: str, password: str) -> dict[str, Any] | None:
        from tools.user_db import verify_password
        user = self.get_by_email(email)
        if user is None or not user.get("password_hash"):
            return None
        if not verify_password(password, user["password_hash"]):
            return None
        self.update_last_login(user["id"])
        return user

    def set_credentials(self, user_id: str, email: str, password: str) -> bool:
        from tools.user_db import hash_password
        pw_hash = hash_password(password)
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "UPDATE users SET email = %s, password_hash = %s WHERE id = %s",
                    (email, pw_hash, user_id),
                )
                return cur.rowcount > 0
        except Exception:
            return False

    def update_last_login(self, user_id: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE users SET last_login = %s WHERE id = %s", (time.time(), user_id)
            )

    def update_tier(self, user_id: str, tier: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET tier = %s WHERE id = %s", (tier, user_id)
            )
            return cur.rowcount > 0

    def update_admin(self, user_id: str, admin: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET admin = %s WHERE id = %s", (admin, user_id)
            )
            return cur.rowcount > 0

    def link_telegram(self, user_id: str, telegram_id: str) -> bool:
        try:
            with self._conn() as conn:
                cur = conn.execute(
                    "UPDATE users SET telegram_id = %s WHERE id = %s",
                    (str(telegram_id), user_id),
                )
                return cur.rowcount > 0
        except Exception:
            return False

    def update_display_name(self, user_id: str, display_name: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET display_name = %s WHERE id = %s",
                (display_name, user_id),
            )
            return cur.rowcount > 0

    def set_consent(self, user_id: str, consent: bool = True) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET consent = %s WHERE id = %s",
                (1 if consent else 0, user_id),
            )
            return cur.rowcount > 0

    def has_consent(self, user_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("SELECT consent FROM users WHERE id = %s", (user_id,))
            row = cur.fetchone()
        return bool(row and row[0])

    def unlink_telegram(self, user_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE users SET telegram_id = NULL WHERE id = %s", (user_id,)
            )
            return cur.rowcount > 0

    def create_link_code(self, user_id: str, ttl: int = 600) -> str:
        now = time.time()
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM telegram_link_codes WHERE expires_at < %s", (now,)
            )
            conn.execute(
                "DELETE FROM telegram_link_codes WHERE user_id = %s", (user_id,)
            )
            conn.execute(
                "INSERT INTO telegram_link_codes (code, user_id, created_at, expires_at) "
                "VALUES (%s, %s, %s, %s)",
                (code, user_id, now, now + ttl),
            )
        return code

    def verify_link_code(self, code: str, telegram_id: str) -> dict[str, Any] | None:
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT user_id FROM telegram_link_codes WHERE code = %s AND expires_at > %s",
                (code, now),
            )
            row = cur.fetchone()
            if not row:
                return None
            user_id = row[0]
            conn.execute(
                "DELETE FROM telegram_link_codes WHERE code = %s", (code,)
            )
        if not self.link_telegram(user_id, telegram_id):
            return None
        return self.get_by_id(user_id)

    def delete_user(self, user_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM users WHERE id = %s", (user_id,))
            return cur.rowcount > 0

    def list_users(self) -> list[dict[str, Any]]:
        cutoff = time.time() - 30 * 86400
        with self._conn() as conn:
            cur = conn.execute(
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
                           SUM(CASE WHEN created_at > %s THEN 1 ELSE 0 END) AS month,
                           MAX(created_at) AS last_analysis
                    FROM usage_log GROUP BY user_id
                ) s ON s.user_id = u.id
                ORDER BY u.created_at DESC
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def count(self) -> int:
        with self._conn() as conn:
            cur = conn.execute("SELECT COUNT(*) FROM users")
            return cur.fetchone()[0]

    def log_usage(
        self,
        user_id: str,
        tier_used: str,
        claims: int = 0,
        rating: str | None = None,
        source: str = "web",
        total_tokens: int = 0,
        estimated_cost_usd: float = 0.0,
        estimated_co2_grams: float = 0.0,
        analysis_id: str | None = None,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO usage_log (user_id, tier_used, created_at, claims, rating, source, "
                "total_tokens, estimated_cost_usd, estimated_co2_grams, analysis_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (user_id, tier_used, time.time(), claims, rating, source,
                 total_tokens, estimated_cost_usd, estimated_co2_grams, analysis_id),
            )

    def get_user_usage(self, user_id: str, days: int = 30) -> list[dict[str, Any]]:
        cutoff = time.time() - days * 86400
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT tier_used, created_at, claims, rating, source "
                "FROM usage_log WHERE user_id = %s AND created_at > %s "
                "ORDER BY created_at DESC",
                (user_id, cutoff),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_cost_stats(self, days: int = 30) -> dict:
        """Aggregierte Kosten-, Token- und CO2-Statistiken ueber alle Nutzer."""
        cutoff = time.time() - days * 86400
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT
                    tier_used,
                    COUNT(*) AS analyses,
                    COALESCE(SUM(total_tokens), 0) AS total_tokens,
                    COALESCE(SUM(estimated_cost_usd), 0.0) AS total_cost_usd,
                    COALESCE(AVG(estimated_cost_usd), 0.0) AS avg_cost_per_analysis,
                    COALESCE(SUM(estimated_co2_grams), 0.0) AS total_co2_grams,
                    COALESCE(AVG(estimated_co2_grams), 0.0) AS avg_co2_per_analysis
                FROM usage_log
                WHERE created_at > %s
                GROUP BY tier_used
                ORDER BY tier_used
                """,
                (cutoff,),
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return {"period_days": days, "by_tier": [dict(zip(cols, r)) for r in rows]}

    # ── Registration / invite codes ──────────────────────────────────────────

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

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO registration_codes
                    (id, code, created_by, label, max_uses, used_count, is_active, created_at, expires_at)
                VALUES (%s, %s, %s, %s, %s, 0, 1, %s, %s)
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
        """Atomically validate and consume an invite code."""
        now = time.time()
        with self._conn() as conn:
            cur = conn.execute(
                """
                UPDATE registration_codes
                SET used_count = used_count + 1
                WHERE code = %s
                  AND is_active = 1
                  AND used_count < max_uses
                  AND (expires_at IS NULL OR expires_at > %s)
                """,
                (code, now),
            )
            return cur.rowcount > 0

    def list_registration_codes(self) -> list[dict[str, Any]]:
        """Return all registration codes ordered by creation date (newest first)."""
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM registration_codes ORDER BY created_at DESC"
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in rows]

    def revoke_registration_code(self, code_id: str) -> bool:
        """Deactivate a registration code. Returns True if found and updated."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE registration_codes SET is_active = 0 WHERE id = %s",
                (code_id,),
            )
            return cur.rowcount > 0

    def migrate_from_json(self, json_path: str) -> int:
        """Import aus altem users.json – identisch mit SQLite-Version."""
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
            if self.get_by_telegram_id(telegram_id) is not None:
                continue
            result = self.create_user(
                telegram_id=telegram_id,
                tier=u.get("tier", "lite"),
                admin=u.get("admin", 0),
                display_name=f"Telegram {telegram_id}",
            )
            if result:
                imported += 1
        return imported


# ── PostgreSQL AnalysisArchive ────────────────────────────────────────────────


class PgAnalysisArchive:
    """PostgreSQL-Implementierung des AnalysisArchive.

    FTS via pg_tsvector + GIN-Index (Deutsch + Englisch).
    Identische öffentliche Schnittstelle wie tools/archive.AnalysisArchive.
    """

    _placeholder = "%s"

    def __init__(self, pg_cfg: PostgreSQLConfig, archive_cfg: ArchiveConfig) -> None:
        self._cfg = pg_cfg
        self._archive_cfg = archive_cfg
        if archive_cfg.enabled:
            self._init_db()

    @property
    def config(self) -> ArchiveConfig:
        return self._archive_cfg

    @contextmanager
    def _connect(self):
        """Drop-in equivalent of AnalysisArchive._connect() with dict-row support."""
        from psycopg.rows import dict_row
        conn = _get_conn(self._cfg)
        conn.row_factory = dict_row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self) -> None:
        with _pg(self._cfg) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analysis_archive (
                    id               TEXT PRIMARY KEY,
                    created_at       DOUBLE PRECISION NOT NULL,
                    input_text       TEXT NOT NULL DEFAULT '',
                    source_url       TEXT,
                    platform         TEXT,
                    overall_rating   TEXT NOT NULL,
                    confidence       INTEGER NOT NULL,
                    summary          TEXT NOT NULL DEFAULT '',
                    result_json      TEXT NOT NULL,
                    title            TEXT,
                    claims_count     INTEGER DEFAULT 0,
                    techniques_count INTEGER DEFAULT 0,
                    input_hash       TEXT,
                    search_vector    tsvector
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_created "
                "ON analysis_archive (created_at DESC)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_rating "
                "ON analysis_archive (overall_rating)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_hash "
                "ON analysis_archive (input_hash)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_fts "
                "ON analysis_archive USING gin(search_vector)"
            )
            conn.execute("ALTER TABLE analysis_archive ADD COLUMN IF NOT EXISTS cost_summary JSONB")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS archive_shares (
                    id          TEXT PRIMARY KEY,
                    archive_id  TEXT NOT NULL REFERENCES analysis_archive(id) ON DELETE CASCADE,
                    token       TEXT UNIQUE NOT NULL,
                    created_by  TEXT NOT NULL,
                    created_at  DOUBLE PRECISION NOT NULL,
                    expires_at  DOUBLE PRECISION,
                    view_count  INTEGER DEFAULT 0,
                    allow_embed BOOLEAN DEFAULT FALSE
                )
            """)
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS idx_shares_token ON archive_shares(token)"
            )

    @contextmanager
    def _conn(self):
        with _pg(self._cfg) as conn:
            yield conn

    def _make_tsvector_expr(self, title: str, summary: str, source_url: str, input_text: str) -> str:
        parts = " ".join(filter(None, [title, summary, source_url, input_text]))
        return parts

    def find_duplicate(self, text: str = "", url: str = "") -> dict[str, Any] | None:
        if not self._archive_cfg.enabled or (not text and not url):
            return None
        import hashlib
        raw = (url.strip().lower() if url else text.strip().lower())
        key = hashlib.sha256(raw.encode()).hexdigest()
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT id, created_at, input_text, source_url, platform,
                       overall_rating, confidence, summary, result_json,
                       title, claims_count, techniques_count
                FROM analysis_archive
                WHERE input_hash = %s
                ORDER BY created_at DESC LIMIT 1
                """,
                (key,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))
        d["result"] = json.loads(d.pop("result_json"))
        return d

    def save(
        self,
        result: dict[str, Any],
        input_text: str = "",
        source_url: str | None = None,
        platform: str | None = None,
        title: str | None = None,
        cost_summary: dict | None = None,
    ) -> str:
        if not self._archive_cfg.enabled:
            return ""
        import hashlib

        archive_id = str(uuid.uuid4())
        if not title:
            title = (result.get("summary", "") or input_text)[:120]
        claims_count = len(result.get("claims", []))
        techniques_count = len(result.get("rhetoric", []))
        raw = (source_url or "").strip().lower() if source_url else input_text.strip().lower()
        lookup_hash = hashlib.sha256(raw.encode()).hexdigest()
        input_short = input_text[:500]
        summary_text = result.get("summary", "")

        fts_text = " ".join(filter(None, [title or "", summary_text, source_url or "", input_short]))

        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO analysis_archive
                    (id, created_at, input_text, source_url, platform,
                     overall_rating, confidence, summary, result_json,
                     title, claims_count, techniques_count, input_hash, search_vector,
                     cost_summary)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        to_tsvector('simple', %s), %s)
                """,
                (
                    archive_id, time.time(), input_short, source_url, platform,
                    result.get("overall_rating_key") or result.get("overall_rating", "?"),
                    result.get("confidence", 0),
                    summary_text,
                    json.dumps(result, ensure_ascii=False),
                    title, claims_count, techniques_count, lookup_hash,
                    fts_text,
                    json.dumps(cost_summary) if cost_summary else None,
                ),
            )

        if self._archive_cfg.max_entries > 0:
            self._enforce_max_entries()
        return archive_id

    def delete(self, archive_id: str) -> bool:
        if not self._archive_cfg.enabled:
            return False
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM analysis_archive WHERE id = %s", (archive_id,)
            )
            return cur.rowcount > 0

    def clear_all(self) -> int:
        if not self._archive_cfg.enabled:
            return 0
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM analysis_archive")
            return cur.rowcount

    def list(
        self,
        limit: int = 50,
        offset: int = 0,
        rating_filter: str | None = None,
        search: str | None = None,
    ) -> dict[str, Any]:
        if not self._archive_cfg.enabled:
            return {"items": [], "total": 0, "limit": limit, "offset": offset}
        limit = max(1, min(limit, 100))

        where: list[str] = []
        params: list[Any] = []

        if rating_filter:
            where.append("overall_rating = %s")
            params.append(rating_filter)
        if search:
            where.append("search_vector @@ plainto_tsquery('simple', %s)")
            params.append(search)

        where_sql = f"WHERE {' AND '.join(where)}" if where else ""

        with self._conn() as conn:
            cur = conn.execute(
                f"SELECT COUNT(*) FROM analysis_archive {where_sql}", params
            )
            total = cur.fetchone()[0]

            cur = conn.execute(
                f"""
                SELECT id, created_at, input_text, source_url, platform,
                       overall_rating, confidence, summary, title,
                       claims_count, techniques_count
                FROM analysis_archive
                {where_sql}
                ORDER BY created_at DESC
                LIMIT %s OFFSET %s
                """,
                params + [limit, offset],
            )
            rows = cur.fetchall()
            cols = [d[0] for d in cur.description]

        return {
            "items": [dict(zip(cols, r)) for r in rows],
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    def get(self, archive_id: str) -> dict[str, Any] | None:
        if not self._archive_cfg.enabled:
            return None
        with self._conn() as conn:
            cur = conn.execute(
                """
                SELECT id, created_at, input_text, source_url, platform,
                       overall_rating, confidence, summary, result_json,
                       title, claims_count, techniques_count
                FROM analysis_archive WHERE id = %s
                """,
                (archive_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        cols = [d[0] for d in cur.description]
        d = dict(zip(cols, row))
        d["result"] = json.loads(d.pop("result_json"))
        return d

    def count_analyses(self) -> dict[str, int]:
        if not self._archive_cfg.enabled:
            return {"total": 0, "last_30_days": 0}
        cutoff = time.time() - 30 * 86400
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive"
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive WHERE created_at > %s", (cutoff,)
            ).fetchone()[0]
        return {"total": total, "last_30_days": recent}

    def stats(self) -> dict[str, Any]:
        if not self._archive_cfg.enabled:
            return {"enabled": False}
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive"
            ).fetchone()[0]
            cur = conn.execute(
                "SELECT overall_rating, COUNT(*) FROM analysis_archive GROUP BY overall_rating"
            )
            rating_counts = {r[0]: r[1] for r in cur.fetchall()}
            avg_confidence = conn.execute(
                "SELECT AVG(confidence) FROM analysis_archive"
            ).fetchone()[0]
        return {
            "enabled": True,
            "backend": "postgres",
            "total_entries": total,
            "rating_distribution": rating_counts,
            "average_confidence": round(float(avg_confidence or 0), 1),
            "max_entries": self._archive_cfg.max_entries,
        }

    def analytics_data(self, days: int = 30) -> dict[str, Any]:
        """Aggregated analytics for the admin dashboard."""
        KNOWN_RATINGS = ["TRUE", "MOSTLY_TRUE", "MISLEADING", "MOSTLY_FALSE", "FALSE", "UNVERIFIABLE"]
        empty_histogram = [{"bucket": f"{i/10:.1f}-{(i+1)/10:.1f}", "count": 0} for i in range(10)]

        if not self._archive_cfg.enabled:
            return {
                "verdict_distribution": {k: 0 for k in KNOWN_RATINGS},
                "confidence_histogram": empty_histogram,
                "top_domains": [],
                "analyses_per_day": [],
                "period_days": days,
            }

        import time
        from datetime import date, timedelta
        from urllib.parse import urlparse

        cutoff = time.time() - days * 86400

        with self._conn() as conn:
            # 1. Verdict distribution
            verdict_distribution: dict[str, int] = {k: 0 for k in KNOWN_RATINGS}
            cur = conn.execute(
                "SELECT overall_rating, COUNT(*) AS cnt FROM analysis_archive "
                "WHERE created_at > %s GROUP BY overall_rating",
                (cutoff,),
            )
            for row in cur.fetchall():
                if row[0] in verdict_distribution:
                    verdict_distribution[row[0]] = row[1]

            # 2. Confidence histogram
            buckets = [0] * 10
            cur = conn.execute(
                "SELECT confidence FROM analysis_archive WHERE created_at > %s",
                (cutoff,),
            )
            for row in cur.fetchall():
                idx = min(row[0] // 10, 9)
                buckets[idx] += 1
            confidence_histogram = [
                {"bucket": f"{i/10:.1f}-{(i+1)/10:.1f}", "count": buckets[i]}
                for i in range(10)
            ]

            # 3. Top domains (extract in Python from source_url)
            cur = conn.execute(
                "SELECT source_url FROM analysis_archive "
                "WHERE created_at > %s AND source_url IS NOT NULL",
                (cutoff,),
            )
            domain_counts: dict[str, int] = {}
            for row in cur.fetchall():
                netloc = urlparse(row[0]).netloc
                domain = netloc[4:] if netloc.startswith("www.") else netloc
                if domain:
                    domain_counts[domain] = domain_counts.get(domain, 0) + 1
            top_domains = [
                {"domain": d, "count": c, "avg_tier": 0}
                for d, c in sorted(domain_counts.items(), key=lambda x: -x[1])[:15]
            ]

            # 4. Analyses per day
            cur = conn.execute(
                "SELECT TO_CHAR(TO_TIMESTAMP(created_at), 'YYYY-MM-DD') AS day, COUNT(*) AS cnt "
                "FROM analysis_archive WHERE created_at > %s GROUP BY day ORDER BY day",
                (cutoff,),
            )
            day_map: dict[str, int] = {row[0]: row[1] for row in cur.fetchall()}

        start_date = date.fromtimestamp(cutoff)
        end_date = date.today()
        analyses_per_day: list[dict[str, Any]] = []
        current = start_date
        while current <= end_date:
            analyses_per_day.append({"date": current.isoformat(), "count": day_map.get(current.isoformat(), 0)})
            current += timedelta(days=1)

        return {
            "verdict_distribution": verdict_distribution,
            "confidence_histogram": confidence_histogram,
            "top_domains": top_domains,
            "analyses_per_day": analyses_per_day,
            "period_days": days,
        }

    def _enforce_max_entries(self) -> None:
        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM analysis_archive"
            ).fetchone()[0]
            if count > self._archive_cfg.max_entries:
                excess = count - self._archive_cfg.max_entries
                conn.execute(
                    """
                    DELETE FROM analysis_archive
                    WHERE id IN (
                        SELECT id FROM analysis_archive
                        ORDER BY created_at ASC LIMIT %s
                    )
                    """,
                    (excess,),
                )

    # ── Share-Links ───────────────────────────────────────────────────────────

    def create_share(
        self,
        archive_id: str,
        created_by: str,
        expires_days: int | None = None,
        allow_embed: bool = False,
    ) -> dict[str, Any]:
        import secrets as _secrets
        share_id = str(uuid.uuid4())
        token = _secrets.token_urlsafe(16)
        now = time.time()
        expires_at = (now + expires_days * 86400) if expires_days else None
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO archive_shares
                    (id, archive_id, token, created_by, created_at, expires_at, allow_embed)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (share_id, archive_id, token, created_by, now, expires_at, allow_embed),
            )
        return {
            "id": share_id,
            "archive_id": archive_id,
            "token": token,
            "created_by": created_by,
            "created_at": now,
            "expires_at": expires_at,
            "view_count": 0,
            "allow_embed": allow_embed,
        }

    def get_share_by_token(self, token: str) -> dict[str, Any] | None:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM archive_shares WHERE token = %s", (token,)
            )
            row = cur.fetchone()
            if row is None:
                return None
            d = dict(zip([c[0] for c in cur.description], row))
            if d.get("expires_at") is not None and d["expires_at"] < time.time():
                return None
            conn.execute(
                "UPDATE archive_shares SET view_count = view_count + 1 WHERE token = %s",
                (token,),
            )
            cur = conn.execute(
                "SELECT * FROM archive_shares WHERE token = %s", (token,)
            )
            updated = cur.fetchone()
        return dict(zip([c[0] for c in cur.description], updated)) if updated else d

    def delete_share(self, token: str, user_id: str) -> bool:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT created_by FROM archive_shares WHERE token = %s", (token,)
            )
            row = cur.fetchone()
            if row is None:
                return False
            if row[0] != user_id:
                return False
            conn.execute("DELETE FROM archive_shares WHERE token = %s", (token,))
        return True

    def list_shares_for_archive(self, archive_id: str) -> list[dict[str, Any]]:
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT * FROM archive_shares WHERE archive_id = %s ORDER BY created_at DESC",
                (archive_id,),
            )
            rows = cur.fetchall()
            cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in rows]


# ── PostgreSQL CrossReferenceGraph ────────────────────────────────────────────


class PgCrossReferenceGraph:
    """PostgreSQL-Implementierung des CrossReferenceGraph.

    Gleiche Schnittstelle wie tools/cross_reference.CrossReferenceGraph.
    """

    def __init__(self, pg_cfg: PostgreSQLConfig) -> None:
        self._cfg = pg_cfg
        self._init_db()

    def _init_db(self) -> None:
        with _pg(self._cfg) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_nodes (
                    id          TEXT PRIMARY KEY,
                    type        TEXT NOT NULL,
                    label       TEXT NOT NULL,
                    properties  JSONB NOT NULL DEFAULT '{}',
                    created_at  DOUBLE PRECISION NOT NULL,
                    updated_at  DOUBLE PRECISION NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gnodes_type ON graph_nodes(type)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS graph_edges (
                    id         BIGSERIAL PRIMARY KEY,
                    source_id  TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
                    target_id  TEXT NOT NULL REFERENCES graph_nodes(id) ON DELETE CASCADE,
                    relation   TEXT NOT NULL,
                    properties JSONB NOT NULL DEFAULT '{}',
                    created_at DOUBLE PRECISION NOT NULL,
                    UNIQUE(source_id, target_id, relation)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gedges_src ON graph_edges(source_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gedges_tgt ON graph_edges(target_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_gedges_rel ON graph_edges(relation)"
            )

    @contextmanager
    def _conn(self):
        with _pg(self._cfg) as conn:
            yield conn

    def add_node(self, node) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO graph_nodes (id, type, label, properties, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    label = EXCLUDED.label,
                    properties = EXCLUDED.properties,
                    updated_at = EXCLUDED.updated_at
                """,
                (node.id, node.type, node.label,
                 json.dumps(node.properties), now, now),
            )

    def get_node(self, node_id: str):
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT id, type, label, properties FROM graph_nodes WHERE id = %s",
                (node_id,),
            )
            row = cur.fetchone()
        if row is None:
            return None
        from tools.cross_reference import GraphNode
        return GraphNode(
            id=row[0], type=row[1], label=row[2],
            properties=row[3] if isinstance(row[3], dict) else json.loads(row[3]),
        )

    def add_edge(self, edge) -> None:
        now = time.time()
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO graph_edges
                    (source_id, target_id, relation, properties, created_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (source_id, target_id, relation) DO NOTHING
                """,
                (edge.source_id, edge.target_id, edge.relation,
                 json.dumps(edge.properties), now),
            )

    def get_edges(
        self,
        node_id: str,
        direction: str = "both",
        relation: str | None = None,
    ) -> list:
        """Alle Kanten eines Knotens (outgoing, incoming, oder both)."""
        from tools.cross_reference import GraphEdge

        clauses: list[str] = []
        params: list[Any] = []

        if direction in ("out", "both"):
            clauses.append("source_id = %s")
            params.append(node_id)
        if direction in ("in", "both"):
            clauses.append("target_id = %s")
            params.append(node_id)

        where = " OR ".join(clauses)
        if relation:
            where = f"({where}) AND relation = %s"
            params.append(relation)

        with self._conn() as conn:
            cur = conn.execute(
                f"SELECT source_id, target_id, relation, properties FROM graph_edges WHERE {where} ORDER BY created_at DESC",
                params,
            )
            rows = cur.fetchall()

        return [
            GraphEdge(
                source_id=r[0], target_id=r[1], relation=r[2],
                properties=r[3] if isinstance(r[3], dict) else json.loads(r[3]),
            )
            for r in rows
        ]

    def get_edges_between(self, node_ids: list[str]) -> list:
        """Kanten zwischen einer Menge von Knoten (beide Endpunkte in node_ids)."""
        from tools.cross_reference import GraphEdge
        if not node_ids:
            return []
        with self._conn() as conn:
            cur = conn.execute(
                "SELECT source_id, target_id, relation, properties "
                "FROM graph_edges WHERE source_id = ANY(%s) AND target_id = ANY(%s)",
                (node_ids, node_ids),
            )
            rows = cur.fetchall()
        return [
            GraphEdge(
                source_id=r[0], target_id=r[1], relation=r[2],
                properties=r[3] if isinstance(r[3], dict) else json.loads(r[3]),
            )
            for r in rows
        ]

    def get_neighbors(self, node_id: str, relation: str | None = None) -> list:
        """Alle Nachbarknoten (via beliebige Kante, beide Richtungen)."""
        from tools.cross_reference import GraphNode

        edges = self.get_edges(node_id, relation=relation)
        neighbor_ids = set()
        for e in edges:
            neighbor_ids.add(e.target_id if e.source_id == node_id else e.source_id)

        nodes = []
        with self._conn() as conn:
            for nid in neighbor_ids:
                cur = conn.execute(
                    "SELECT id, type, label, properties FROM graph_nodes WHERE id = %s",
                    (nid,),
                )
                row = cur.fetchone()
                if row:
                    nodes.append(GraphNode(
                        id=row[0], type=row[1], label=row[2],
                        properties=row[3] if isinstance(row[3], dict) else json.loads(row[3]),
                    ))
        return nodes

    def find_nodes(
        self,
        node_type: str | None = None,
        label_search: str | None = None,
        limit: int = 50,
    ) -> list:
        """Suche nach Knoten."""
        from tools.cross_reference import GraphNode

        clauses = []
        params: list[Any] = []

        if node_type:
            clauses.append("type = %s")
            params.append(node_type)
        if label_search:
            clauses.append("label ILIKE %s")
            params.append(f"%{label_search}%")

        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)

        with self._conn() as conn:
            cur = conn.execute(
                f"SELECT id, type, label, properties FROM graph_nodes WHERE {where} ORDER BY updated_at DESC LIMIT %s",
                params,
            )
            rows = cur.fetchall()

        return [
            GraphNode(
                id=r[0], type=r[1], label=r[2],
                properties=r[3] if isinstance(r[3], dict) else json.loads(r[3]),
            )
            for r in rows
        ]

    def find_related_claims(self, claim_id: str) -> list:
        return self.get_neighbors(claim_id, relation="related_to")

    def get_actor_claims(self, actor_name: str) -> list:
        """Alle Claims, in denen ein Akteur erwaehnt wird."""
        actor_nodes = self.find_nodes(node_type="ACTOR", label_search=actor_name)
        if not actor_nodes:
            return []

        claims = []
        for actor in actor_nodes:
            neighbors = self.get_neighbors(actor.id, relation="mentions")
            claims.extend(n for n in neighbors if n.type == "CLAIM")
        return claims

    def get_source_history(self, domain: str) -> dict[str, Any]:
        """Wie oft und in welchem Kontext wurde eine Quelle verwendet?"""
        source_nodes = self.find_nodes(node_type="SOURCE", label_search=domain)
        if not source_nodes:
            return {"domain": domain, "total_references": 0, "claims": []}

        all_claims = []
        for src in source_nodes:
            edges = self.get_edges(src.id, direction="in")
            for edge in edges:
                claim_node = self.get_node(edge.source_id)
                if claim_node and claim_node.type == "CLAIM":
                    all_claims.append({
                        "claim": claim_node.label[:100],
                        "relation": edge.relation,
                        "rating": claim_node.properties.get("rating", ""),
                    })

        return {
            "domain": domain,
            "total_references": len(all_claims),
            "claims": all_claims,
        }

    def populate_from_result(
        self,
        analysis_id: str,
        claims_analysis: list[dict[str, Any]],
        original_text: str = "",
    ) -> None:
        """Trage die Ergebnisse einer Analyse in den Graphen ein."""
        from tools.cross_reference import GraphNode, GraphEdge, _extract_domain, _extract_actors

        claim_node_ids: list[str] = []

        for claim in claims_analysis:
            claim_id = f"{analysis_id}:{claim.get('id', 'C?')}"
            claim_text = claim.get("text", "")
            claim_rating = claim.get("rating", "")

            # Claim-Knoten
            self.add_node(GraphNode(
                id=claim_id, type="CLAIM", label=claim_text,
                properties={
                    "rating": claim_rating,
                    "claim_type": claim.get("type", ""),
                    "analysis_id": analysis_id,
                },
            ))
            claim_node_ids.append(claim_id)

            # Source-Knoten + Kanten
            sources = claim.get("sources", [])
            for url in sources:
                source_id = f"src:{url}"
                domain = _extract_domain(url)
                label = url.replace("https://", "").replace("http://", "").replace("www.", "")
                if len(label) > 60:
                    label = label[:57] + "..."

                self.add_node(GraphNode(
                    id=source_id, type="SOURCE", label=label,
                    properties={"url": url, "domain": domain},
                ))

                relation = "supported_by" if claim_rating in ("TRUE", "MOSTLY_TRUE") else "referenced_by"
                self.add_edge(GraphEdge(
                    source_id=claim_id, target_id=source_id, relation=relation,
                ))

            # Akteur-Extraktion
            actors = _extract_actors(claim_text)
            for actor in actors:
                actor_id = f"actor:{actor.lower().replace(' ', '_')}"
                self.add_node(GraphNode(
                    id=actor_id, type="ACTOR", label=actor,
                ))
                self.add_edge(GraphEdge(
                    source_id=claim_id, target_id=actor_id, relation="mentions",
                ))

        # Beziehungen zwischen Claims der gleichen Analyse
        for i, cid_a in enumerate(claim_node_ids):
            for cid_b in claim_node_ids[i + 1:]:
                self.add_edge(GraphEdge(
                    source_id=cid_a, target_id=cid_b, relation="related_to",
                    properties={"reason": "same_analysis"},
                ))

    def stats(self) -> dict[str, Any]:
        with self._conn() as conn:
            total_nodes = conn.execute("SELECT COUNT(*) FROM graph_nodes").fetchone()[0]
            total_edges = conn.execute("SELECT COUNT(*) FROM graph_edges").fetchone()[0]
            nodes_by_type = dict(conn.execute(
                "SELECT type, COUNT(*) FROM graph_nodes GROUP BY type"
            ).fetchall())
            edges_by_relation = dict(conn.execute(
                "SELECT relation, COUNT(*) FROM graph_edges GROUP BY relation"
            ).fetchall())
        return {
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "nodes_by_type": nodes_by_type,
            "edges_by_relation": edges_by_relation,
        }
