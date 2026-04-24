from __future__ import annotations

from pathlib import Path


def test_surface_migration_creates_table_and_backfills_legacy_routes_media() -> None:
    sql = Path("migrations/009_guild_announcement_surfaces.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS guild_announcement_surfaces" in sql
    assert "guild_announcement_surfaces_kind_valid" in sql
    assert "'birthday_announcement'" in sql
    assert "'anniversary'" in sql
    assert "announcement_channel_id" in sql
    assert "announcement_image_url" in sql
    assert "announcement_thumbnail_url" in sql
    assert "anniversary_channel_id" in sql
    assert "ON CONFLICT (guild_id, surface_kind) DO NOTHING" in sql


def test_processing_index_migration_adds_partial_processing_started_index() -> None:
    sql = Path("migrations/010_processing_event_index.sql").read_text(encoding="utf-8")

    assert "CREATE INDEX IF NOT EXISTS celebration_events_processing_started_idx" in sql
    assert "ON celebration_events (processing_started_at_utc)" in sql
    assert "WHERE state = 'processing'" in sql


def test_topgg_vote_receipts_migration_creates_ledger_and_indexes() -> None:
    sql = Path("migrations/011_topgg_vote_receipts.sql").read_text(encoding="utf-8")

    assert "CREATE TABLE IF NOT EXISTS topgg_vote_receipts" in sql
    assert "event_id TEXT PRIMARY KEY" in sql
    assert "discord_user_id BIGINT NOT NULL" in sql
    assert "payload_hash TEXT NOT NULL" in sql
    assert "vote_created_at TIMESTAMPTZ" in sql
    assert "vote_expires_at TIMESTAMPTZ" in sql
    assert "CREATE INDEX IF NOT EXISTS idx_topgg_vote_receipts_user_expires" in sql
