"""User authentication and database access for the Telegram bot."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

log = logging.getLogger("fng-telegram")

_user_db = None


def _get_db():
    global _user_db
    if _user_db is None:
        from config import AppConfig as _AppConfig
        from tools.db.factory import create_user_db as _create_user_db
        _cfg = _AppConfig()
        _user_db = _create_user_db(_cfg)
        if hasattr(_user_db, "migrate_from_json"):
            json_path = str(Path(__file__).parent.parent / "users.json")
            imported = _user_db.migrate_from_json(json_path)
            if imported > 0:
                log.info("Migrated %d users from users.json to SQLite", imported)
    return _user_db


class BotAuth:
    """User management and JWT minting for bot handlers."""

    def get_user(self, user_id: int | str) -> dict[str, Any] | None:
        """Find a user by Telegram ID. Returns None if not registered."""
        return _get_db().get_by_telegram_id(str(user_id))

    def is_admin(self, user_id: int | str) -> bool:
        """Check if a user has admin rights."""
        user = self.get_user(user_id)
        return user is not None and user.get("admin", 0) == 1

    def add_user(self, user_id: int | str, tier: str = "lite", admin: int = 0) -> bool:
        """Add a new Telegram user. Returns False if the user already exists."""
        db = _get_db()
        if db.get_by_telegram_id(str(user_id)) is not None:
            return False
        result = db.create_user(
            telegram_id=str(user_id),
            tier=tier,
            admin=admin,
            display_name=f"Telegram {user_id}",
        )
        return result is not None

    def mint_jwt(self, user: dict[str, Any]) -> str:
        """Mint a JWT token for the given user."""
        from tools.user_db import create_access_token
        return create_access_token(user["id"], user["tier"], bool(user.get("admin", 0)))

    def set_consent(self, user_id: int | str, value: bool) -> None:
        """Set the consent flag for a user."""
        user = self.get_user(user_id)
        if user is not None:
            _get_db().set_consent(user["id"], value)
