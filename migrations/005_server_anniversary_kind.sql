BEGIN;

ALTER TABLE recurring_celebrations
    ADD COLUMN IF NOT EXISTS celebration_kind TEXT NOT NULL DEFAULT 'custom',
    ADD COLUMN IF NOT EXISTS use_guild_created_date BOOLEAN NOT NULL DEFAULT FALSE;

UPDATE recurring_celebrations
SET celebration_kind = 'custom'
WHERE celebration_kind IS NULL;

ALTER TABLE recurring_celebrations
    ADD CONSTRAINT recurring_celebrations_kind_valid CHECK (
        celebration_kind IN ('custom', 'server_anniversary')
    );

CREATE UNIQUE INDEX IF NOT EXISTS idx_recurring_celebrations_server_anniversary
    ON recurring_celebrations (guild_id)
    WHERE celebration_kind = 'server_anniversary';

COMMIT;
