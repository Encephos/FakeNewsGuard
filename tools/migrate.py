"""Migration: SQLite → PostgreSQL + Valkey.

Liest alle bestehenden SQLite-Daten und schreibt sie in die Produktions-DBs.
Idempotent – bereits vorhandene Einträge werden übersprungen (kein Duplicate).

Verwendung:
    # Direkt (Dev):
    DB_BACKEND=postgres CACHE_BACKEND=valkey python tools/migrate.py

    # Via Docker Compose (empfohlen):
    docker compose run --rm migrate

Env-Vars für SQLite-Quellpfade (Defaults passen zu Docker-Volume /app/data):
    SQLITE_CACHE_PATH   – Default: .fakeguard_cache.db
    SQLITE_USERS_PATH   – Default: .fakeguard_users.db
    SQLITE_ARCHIVE_PATH – Default: .fakeguard_archive.db
    SQLITE_GRAPH_PATH   – Default: .fakeguard_graph.db
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import time

# Projekt-Root ins sys.path damit Imports funktionieren
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import AppConfig


# ── Quellpfade ────────────────────────────────────────────────────────────────

def _src(env_key: str, default: str) -> str:
    return os.getenv(env_key, default)


SQLITE_CACHE   = _src("SQLITE_CACHE_PATH",   ".fakeguard_cache.db")
SQLITE_USERS   = _src("SQLITE_USERS_PATH",   ".fakeguard_users.db")
SQLITE_ARCHIVE = _src("SQLITE_ARCHIVE_PATH", ".fakeguard_archive.db")
SQLITE_GRAPH   = _src("SQLITE_GRAPH_PATH",   ".fakeguard_graph.db")


# ── Hilfsfunktionen ───────────────────────────────────────────────────────────

def _sqlite_rows(db_path: str, query: str, params: tuple = ()) -> list[sqlite3.Row]:
    """Liest Zeilen aus einer SQLite-DB. Gibt [] wenn Datei nicht existiert."""
    if not os.path.exists(db_path):
        print(f"  ⚠ SQLite-Datei nicht gefunden: {db_path} – überspringe.")
        return []
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(query, params).fetchall()
    except Exception as exc:
        print(f"  ⚠ Lesefehler {db_path}: {exc}")
        return []
    finally:
        conn.close()


def _ok(label: str, n: int) -> None:
    print(f"  ✓ {label}: {n} Einträge migriert")


# ── Migrations-Routinen ───────────────────────────────────────────────────────

def migrate_cache(config: AppConfig) -> int:
    """ClaimCache SQLite → Valkey (SETEX mit ursprünglicher TTL)."""
    if not config.valkey.enabled:
        print("  ℹ CACHE_BACKEND != valkey – Cache-Migration übersprungen.")
        return 0

    rows = _sqlite_rows(
        SQLITE_CACHE,
        "SELECT cache_key, agent_name, result_json, created_at FROM claim_cache",
    )
    if not rows:
        return 0

    try:
        import redis
    except ImportError:
        print("  ✗ redis-Paket fehlt – Cache-Migration übersprungen.")
        return 0

    client = redis.Redis.from_url(config.valkey.url, db=config.valkey.db, decode_responses=True)
    ttl_s = config.cache.ttl_hours * 3600
    now = time.time()
    count = 0

    for row in rows:
        age = now - row["created_at"]
        remaining_ttl = int(ttl_s - age)
        if remaining_ttl <= 0:
            continue  # Bereits abgelaufen – nicht migrieren
        key = f"fng:cache:{row['cache_key']}"
        if client.exists(key):
            continue  # Bereits vorhanden
        client.setex(key, remaining_ttl, row["result_json"])
        count += 1

    _ok("ClaimCache → Valkey", count)
    return count


def migrate_users(config: AppConfig) -> int:
    """UserDB SQLite → PostgreSQL."""
    if not config.postgres.enabled:
        print("  ℹ DB_BACKEND != postgres – UserDB-Migration übersprungen.")
        return 0

    rows = _sqlite_rows(
        SQLITE_USERS,
        "SELECT id, email, password_hash, display_name, tier, admin, "
        "telegram_id, consent, created_at, last_login FROM users",
    )
    if not rows:
        return 0

    try:
        import psycopg
    except ImportError:
        print("  ✗ psycopg-Paket fehlt – UserDB-Migration übersprungen.")
        return 0

    count = 0
    with psycopg.connect(config.postgres.dsn) as conn:
        conn.autocommit = False
        for row in rows:
            try:
                conn.execute(
                    """
                    INSERT INTO users
                        (id, email, password_hash, display_name, tier, admin,
                         telegram_id, consent, created_at, last_login)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row["id"], row["email"], row["password_hash"],
                        row["display_name"] or "", row["tier"], row["admin"],
                        row["telegram_id"], row["consent"],
                        row["created_at"], row["last_login"],
                    ),
                )
                count += 1
            except Exception as exc:
                print(f"  ⚠ User {row['id']}: {exc}")
        conn.commit()

    # usage_log migrieren
    usage_rows = _sqlite_rows(
        SQLITE_USERS,
        "SELECT user_id, tier_used, created_at, claims, rating, source FROM usage_log",
    )
    usage_count = 0
    if usage_rows:
        with psycopg.connect(config.postgres.dsn) as conn:
            conn.autocommit = False
            for row in usage_rows:
                try:
                    conn.execute(
                        """
                        INSERT INTO usage_log (user_id, tier_used, created_at, claims, rating, source)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (row["user_id"], row["tier_used"], row["created_at"],
                         row["claims"], row["rating"], row["source"]),
                    )
                    usage_count += 1
                except Exception:
                    pass  # FK-Fehler wenn User nicht migriert wurde – OK
            conn.commit()

    _ok("UserDB → PostgreSQL", count)
    print(f"  ✓ UsageLog: {usage_count} Einträge migriert")
    return count


def migrate_archive(config: AppConfig) -> int:
    """AnalysisArchive SQLite → PostgreSQL."""
    if not config.postgres.enabled:
        print("  ℹ DB_BACKEND != postgres – Archive-Migration übersprungen.")
        return 0

    rows = _sqlite_rows(
        SQLITE_ARCHIVE,
        """
        SELECT id, created_at, input_text, source_url, platform,
               overall_rating, confidence, summary, result_json,
               title, claims_count, techniques_count, input_hash
        FROM analysis_archive
        ORDER BY created_at ASC
        """,
    )
    if not rows:
        return 0

    try:
        import psycopg
    except ImportError:
        print("  ✗ psycopg-Paket fehlt – Archive-Migration übersprungen.")
        return 0

    count = 0
    with psycopg.connect(config.postgres.dsn) as conn:
        conn.autocommit = False
        for row in rows:
            fts_text = " ".join(filter(None, [
                row["title"] or "", row["summary"] or "",
                row["source_url"] or "", row["input_text"] or "",
            ]))
            try:
                conn.execute(
                    """
                    INSERT INTO analysis_archive
                        (id, created_at, input_text, source_url, platform,
                         overall_rating, confidence, summary, result_json,
                         title, claims_count, techniques_count, input_hash,
                         search_vector)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                            to_tsvector('simple', %s))
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (
                        row["id"], row["created_at"],
                        row["input_text"] or "", row["source_url"], row["platform"],
                        row["overall_rating"], row["confidence"],
                        row["summary"] or "", row["result_json"],
                        row["title"], row["claims_count"] or 0,
                        row["techniques_count"] or 0, row["input_hash"],
                        fts_text,
                    ),
                )
                count += 1
            except Exception as exc:
                print(f"  ⚠ Archive {row['id']}: {exc}")
        conn.commit()

    _ok("AnalysisArchive → PostgreSQL", count)
    return count


def migrate_graph(config: AppConfig) -> int:
    """CrossReferenceGraph SQLite → PostgreSQL."""
    if not config.postgres.enabled:
        print("  ℹ DB_BACKEND != postgres – Graph-Migration übersprungen.")
        return 0

    node_rows = _sqlite_rows(
        SQLITE_GRAPH,
        "SELECT id, type, label, properties, created_at, updated_at FROM nodes",
    )
    edge_rows = _sqlite_rows(
        SQLITE_GRAPH,
        "SELECT source_id, target_id, relation, properties, created_at FROM edges",
    )

    if not node_rows and not edge_rows:
        return 0

    try:
        import psycopg
    except ImportError:
        print("  ✗ psycopg-Paket fehlt – Graph-Migration übersprungen.")
        return 0

    node_count = 0
    edge_count = 0
    with psycopg.connect(config.postgres.dsn) as conn:
        conn.autocommit = False
        for row in node_rows:
            props = row["properties"] or "{}"
            try:
                conn.execute(
                    """
                    INSERT INTO graph_nodes (id, type, label, properties, created_at, updated_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s, %s)
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (row["id"], row["type"], row["label"], props,
                     row["created_at"], row["updated_at"]),
                )
                node_count += 1
            except Exception as exc:
                print(f"  ⚠ Node {row['id']}: {exc}")

        for row in edge_rows:
            props = row["properties"] or "{}"
            try:
                conn.execute(
                    """
                    INSERT INTO graph_edges (source_id, target_id, relation, properties, created_at)
                    VALUES (%s, %s, %s, %s::jsonb, %s)
                    ON CONFLICT (source_id, target_id, relation) DO NOTHING
                    """,
                    (row["source_id"], row["target_id"], row["relation"],
                     props, row["created_at"]),
                )
                edge_count += 1
            except Exception as exc:
                print(f"  ⚠ Edge {row['source_id']}→{row['target_id']}: {exc}")
        conn.commit()

    _ok("Graph-Nodes → PostgreSQL", node_count)
    _ok("Graph-Edges → PostgreSQL", edge_count)
    return node_count + edge_count


# ── Einstiegspunkt ────────────────────────────────────────────────────────────

def main() -> None:
    print("FakeNewsGuard – Datenmigration SQLite → PostgreSQL + Valkey")
    print("=" * 60)

    config = AppConfig()

    if not config.postgres.enabled and not config.valkey.enabled:
        print(
            "\n✗ Weder DB_BACKEND=postgres noch CACHE_BACKEND=valkey gesetzt.\n"
            "  Setze diese Variablen in .env und starte neu.\n"
            "  Beispiel:\n"
            "    DB_BACKEND=postgres CACHE_BACKEND=valkey python tools/migrate.py"
        )
        sys.exit(1)

    # Sicherstellen, dass Ziel-Tabellen existieren (idempotent)
    if config.postgres.enabled:
        print("\n── PostgreSQL: Tabellen initialisieren ──────────────────")
        try:
            from tools.db.pg_store import PgUserDB, PgAnalysisArchive, PgCrossReferenceGraph
            PgUserDB(config.postgres)._init_db()
            PgAnalysisArchive(config.postgres, config.archive)._init_db()
            PgCrossReferenceGraph(config.postgres)._init_db()
            print("  ✓ Tabellen OK")
        except Exception as exc:
            print(f"  ✗ Tabellen-Init fehlgeschlagen: {exc}")
            sys.exit(1)

    print("\n── Migration ────────────────────────────────────────────────")
    total = 0
    total += migrate_cache(config)
    total += migrate_users(config)
    total += migrate_archive(config)
    total += migrate_graph(config)

    print(f"\n✓ Migration abgeschlossen – {total} Einträge insgesamt übertragen.")


if __name__ == "__main__":
    main()
