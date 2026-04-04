"""Tests für das Share-Link-System (tools/archive.py + api/share.py)."""

from __future__ import annotations

import sqlite3
import time
import json
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from config import ArchiveConfig

# ── Helpers ────────────────────────────────────────────────────────────────


def _make_archive(tmp_path) -> "AnalysisArchive":
    """In-memory SQLite archive für Tests."""
    from tools.archive import AnalysisArchive

    cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "test.db"), max_entries=100)
    return AnalysisArchive(cfg)


def _save_entry(archive) -> str:
    """Speichere einen Dummy-Eintrag und gib die ID zurück."""
    result = {
        "overall_rating": "MISLEADING",
        "overall_rating_key": "MISLEADING",
        "confidence": 72,
        "summary": "Test summary",
        "claims": [],
        "rhetoric": [],
        "key_corrections": [],
        "fairness_notes": [],
        "sources": [],
    }
    return archive.save(result, input_text="Test input text", title="Test title")


_FAKE_USER = {"id": "user-1", "email": "test@example.com", "tier": "lite", "admin": False}


# ── Unit Tests: AnalysisArchive share methods ──────────────────────────────


class TestCreateShare:
    def test_token_is_22_chars_urlsafe(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)

        share = archive.create_share(
            archive_id=entry_id, created_by="user-1", expires_days=7, allow_embed=False
        )

        token = share["token"]
        # secrets.token_urlsafe(16) produces 22 base64url chars
        assert len(token) == 22
        assert all(c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_" for c in token)

    def test_expires_at_calculated_correctly(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)

        before = time.time()
        share = archive.create_share(archive_id=entry_id, created_by="user-1", expires_days=7)
        after = time.time()

        expected_low = before + 7 * 86400
        expected_high = after + 7 * 86400
        assert expected_low <= share["expires_at"] <= expected_high

    def test_no_expiry_when_expires_days_is_none(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)

        share = archive.create_share(archive_id=entry_id, created_by="user-1", expires_days=None)
        assert share["expires_at"] is None

    def test_allow_embed_stored_correctly(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)

        share = archive.create_share(
            archive_id=entry_id, created_by="user-1", allow_embed=True
        )
        assert share["allow_embed"] is True

    def test_each_call_produces_unique_token(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)

        tokens = {
            archive.create_share(archive_id=entry_id, created_by="user-1")["token"]
            for _ in range(5)
        }
        assert len(tokens) == 5


class TestGetShareByToken:
    def test_returns_share_dict(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="user-1")

        result = archive.get_share_by_token(share["token"])
        assert result is not None
        assert result["archive_id"] == entry_id

    def test_returns_none_for_unknown_token(self, tmp_path):
        archive = _make_archive(tmp_path)
        assert archive.get_share_by_token("nonexistent-token-xyz") is None

    def test_returns_none_for_expired_token(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)

        # Create share that expires 1 second in the future
        share = archive.create_share(archive_id=entry_id, created_by="user-1", expires_days=1)

        # Patch time so the token appears expired
        with patch("tools.archive.time") as mock_time:
            mock_time.time.return_value = share["expires_at"] + 1
            result = archive.get_share_by_token(share["token"])

        assert result is None

    def test_non_expired_token_returns_data(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="user-1", expires_days=30)

        result = archive.get_share_by_token(share["token"])
        assert result is not None

    def test_increments_view_count(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="user-1")

        assert share["view_count"] == 0

        archive.get_share_by_token(share["token"])
        archive.get_share_by_token(share["token"])
        archive.get_share_by_token(share["token"])

        result = archive.get_share_by_token(share["token"])
        assert result["view_count"] == 4  # 3 previous + this call


class TestDeleteShare:
    def test_owner_can_delete(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="user-1")

        deleted = archive.delete_share(token=share["token"], user_id="user-1")
        assert deleted is True
        assert archive.get_share_by_token(share["token"]) is None

    def test_non_owner_cannot_delete(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="user-1")

        deleted = archive.delete_share(token=share["token"], user_id="user-2")
        assert deleted is False
        # Token still exists
        assert archive.get_share_by_token(share["token"]) is not None

    def test_returns_false_for_nonexistent_token(self, tmp_path):
        archive = _make_archive(tmp_path)
        assert archive.delete_share(token="does-not-exist", user_id="user-1") is False


class TestListSharesForArchive:
    def test_lists_shares_for_archive(self, tmp_path):
        archive = _make_archive(tmp_path)
        entry_id = _save_entry(archive)
        archive.create_share(archive_id=entry_id, created_by="user-1")
        archive.create_share(archive_id=entry_id, created_by="user-1")

        shares = archive.list_shares_for_archive(entry_id)
        assert len(shares) == 2

    def test_returns_empty_for_unknown_archive(self, tmp_path):
        archive = _make_archive(tmp_path)
        assert archive.list_shares_for_archive("does-not-exist") == []


# ── API Tests: share endpoints ─────────────────────────────────────────────


@pytest.fixture
def client_and_archive(tmp_path):
    """FastAPI TestClient with an in-memory archive injected."""
    from tools.archive import AnalysisArchive
    import api
    from api.dependencies import get_archive

    cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "api_test.db"), max_entries=100)
    archive = AnalysisArchive(cfg)

    with (
        patch("api.dependencies.get_archive", return_value=archive),
        patch("api.share.get_archive", return_value=archive),
        patch("api.share.get_current_user", return_value=lambda: _FAKE_USER),
    ):
        api._rate_limiter = None
        yield TestClient(api.app), archive


@pytest.fixture
def client_archive_entry(tmp_path):
    """TestClient + archive + a saved entry ID."""
    from tools.archive import AnalysisArchive
    import api

    cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "api_test2.db"), max_entries=100)
    archive = AnalysisArchive(cfg)
    entry_id = _save_entry(archive)

    with (
        patch("api.dependencies.get_archive", return_value=archive),
        patch("api.share.get_archive", return_value=archive),
        patch("api.dependencies.get_current_user", return_value=_FAKE_USER),
        patch("api.share.get_current_user", return_value=_FAKE_USER),
    ):
        api._rate_limiter = None
        yield TestClient(api.app), archive, entry_id


class TestShareAPIEndpoints:
    def test_public_safe_response_excludes_input_text(self, tmp_path):
        """GET /share/{token} darf input_text nicht enthalten."""
        from tools.archive import AnalysisArchive
        import api

        cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "safe.db"), max_entries=100)
        archive = AnalysisArchive(cfg)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="user-1")

        with patch("api.share.get_archive", return_value=archive):
            api._rate_limiter = None
            client = TestClient(api.app)
            res = client.get(f"/api/v1/share/{share['token']}")

        assert res.status_code == 200
        body = res.json()
        assert "input_text" not in body
        assert body["overall_rating"] == "MISLEADING"
        assert body["confidence"] == 72

    def test_get_share_returns_404_for_unknown_token(self, tmp_path):
        from tools.archive import AnalysisArchive
        import api

        cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "404.db"), max_entries=100)
        archive = AnalysisArchive(cfg)

        with patch("api.share.get_archive", return_value=archive):
            api._rate_limiter = None
            client = TestClient(api.app)
            res = client.get("/api/v1/share/nonexistent-token")

        assert res.status_code == 404

    def test_embed_endpoint_returns_403_when_embed_not_allowed(self, tmp_path):
        from tools.archive import AnalysisArchive
        import api

        cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "embed403.db"), max_entries=100)
        archive = AnalysisArchive(cfg)
        entry_id = _save_entry(archive)
        share = archive.create_share(
            archive_id=entry_id, created_by="user-1", allow_embed=False
        )

        with patch("api.share.get_archive", return_value=archive):
            api._rate_limiter = None
            client = TestClient(api.app)
            res = client.get(f"/api/v1/share/{share['token']}/embed")

        assert res.status_code == 403

    def test_embed_endpoint_returns_200_when_embed_allowed(self, tmp_path):
        from tools.archive import AnalysisArchive
        import api

        cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "embed200.db"), max_entries=100)
        archive = AnalysisArchive(cfg)
        entry_id = _save_entry(archive)
        share = archive.create_share(
            archive_id=entry_id, created_by="user-1", allow_embed=True
        )

        with patch("api.share.get_archive", return_value=archive):
            api._rate_limiter = None
            client = TestClient(api.app)
            res = client.get(f"/api/v1/share/{share['token']}/embed")

        assert res.status_code == 200
        body = res.json()
        assert "input_text" not in body
        assert body["token"] == share["token"]

    def test_delete_share_wrong_user_returns_403(self, tmp_path):
        from tools.archive import AnalysisArchive
        from api.dependencies import get_current_user
        import api

        cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "del403.db"), max_entries=100)
        archive = AnalysisArchive(cfg)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="owner-user")

        other_user = {"id": "other-user", "email": "other@example.com", "tier": "lite", "admin": False}

        with patch("api.share.get_archive", return_value=archive):
            api.app.dependency_overrides[get_current_user] = lambda: other_user
            api._rate_limiter = None
            try:
                client = TestClient(api.app)
                res = client.delete(f"/api/v1/share/{share['token']}")
            finally:
                api.app.dependency_overrides.pop(get_current_user, None)

        assert res.status_code == 403

    def test_delete_share_correct_user_returns_204(self, tmp_path):
        from tools.archive import AnalysisArchive
        from api.dependencies import get_current_user
        import api

        cfg = ArchiveConfig(enabled=True, db_path=str(tmp_path / "del204.db"), max_entries=100)
        archive = AnalysisArchive(cfg)
        entry_id = _save_entry(archive)
        share = archive.create_share(archive_id=entry_id, created_by="user-1")

        with patch("api.share.get_archive", return_value=archive):
            api.app.dependency_overrides[get_current_user] = lambda: _FAKE_USER
            api._rate_limiter = None
            try:
                client = TestClient(api.app)
                res = client.delete(f"/api/v1/share/{share['token']}")
            finally:
                api.app.dependency_overrides.pop(get_current_user, None)

        assert res.status_code == 204
