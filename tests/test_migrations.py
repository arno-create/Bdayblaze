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
