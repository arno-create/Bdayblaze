from __future__ import annotations

from pathlib import Path

import asyncpg

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "migrations"


async def apply_migrations(pool: asyncpg.Pool) -> list[str]:
    applied: list[str] = []
    async with pool.acquire() as connection:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        existing_rows = await connection.fetch("SELECT version FROM schema_migrations")
        existing = {row["version"] for row in existing_rows}
        for migration_path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            version = migration_path.name
            if version in existing:
                continue
            sql = migration_path.read_text(encoding="utf-8")
            await connection.execute(sql)
            await connection.execute(
                "INSERT INTO schema_migrations (version) VALUES ($1)",
                version,
            )
            applied.append(version)
    return applied
