"""DB-Factory – gibt je nach CACHE_BACKEND / DB_BACKEND die richtige Impl zurück.

Verwendung in api.py / orchestrator.py / telegram_bot.py:

    from tools.db.factory import create_cache, create_user_db, create_archive, create_graph

    cache   = create_cache(config)       # ValkeyClaimCache oder ClaimCache
    user_db = create_user_db(config)     # PgUserDB oder UserDB
    archive = create_archive(config)     # PgAnalysisArchive oder AnalysisArchive
    graph   = create_graph(config)       # PgCrossReferenceGraph oder CrossReferenceGraph

SQLite bleibt als Dev-Fallback erhalten.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import AppConfig


def create_cache(config: "AppConfig"):
    """ClaimCache: Valkey wenn CACHE_BACKEND=valkey, sonst SQLite."""
    if config.valkey.enabled:
        try:
            from tools.db.valkey_cache import ValkeyClaimCache
            return ValkeyClaimCache(config.valkey, config.cache)
        except ImportError as exc:
            print(
                f"  ⚠ Valkey-Cache nicht verfügbar ({exc}), Fallback auf SQLite.",
                file=sys.stderr,
            )
    from tools.cache import ClaimCache
    return ClaimCache(config.cache)


def create_user_db(config: "AppConfig"):
    """UserDB: PostgreSQL wenn DB_BACKEND=postgres, sonst SQLite."""
    if config.postgres.enabled:
        try:
            from tools.db.pg_store import PgUserDB
            return PgUserDB(config.postgres)
        except ImportError as exc:
            print(
                f"  ⚠ PostgreSQL-UserDB nicht verfügbar ({exc}), Fallback auf SQLite.",
                file=sys.stderr,
            )
    from tools.user_db import UserDB
    return UserDB(config.user_db)


def create_archive(config: "AppConfig"):
    """AnalysisArchive: PostgreSQL wenn DB_BACKEND=postgres, sonst SQLite."""
    if config.postgres.enabled:
        try:
            from tools.db.pg_store import PgAnalysisArchive
            return PgAnalysisArchive(config.postgres, config.archive)
        except ImportError as exc:
            print(
                f"  ⚠ PostgreSQL-Archive nicht verfügbar ({exc}), Fallback auf SQLite.",
                file=sys.stderr,
            )
    from tools.archive import AnalysisArchive
    return AnalysisArchive(config.archive)


def create_graph(config: "AppConfig"):
    """CrossReferenceGraph: PostgreSQL wenn DB_BACKEND=postgres, sonst SQLite."""
    if config.postgres.enabled:
        try:
            from tools.db.pg_store import PgCrossReferenceGraph
            return PgCrossReferenceGraph(config.postgres)
        except ImportError as exc:
            print(
                f"  ⚠ PostgreSQL-Graph nicht verfügbar ({exc}), Fallback auf SQLite.",
                file=sys.stderr,
            )
    from tools.cross_reference import CrossReferenceGraph
    return CrossReferenceGraph(config.graph.db_path)
