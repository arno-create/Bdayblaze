BEGIN;

ALTER TABLE guild_settings
    ADD COLUMN IF NOT EXISTS announcement_template TEXT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_settings_announcement_template_length'
    ) THEN
        ALTER TABLE guild_settings
            ADD CONSTRAINT guild_settings_announcement_template_length CHECK (
                announcement_template IS NULL
                OR char_length(announcement_template) <= 500
            );
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS announcement_batches (
    batch_token TEXT PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    channel_id BIGINT NOT NULL,
    scheduled_for_utc TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    message_id BIGINT NULL,
    send_started_at_utc TIMESTAMPTZ NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT announcement_batches_state_valid CHECK (
        state IN ('pending', 'sending', 'sent')
    )
);

CREATE INDEX IF NOT EXISTS idx_announcement_batches_state_due
    ON announcement_batches (state, scheduled_for_utc);

CREATE INDEX IF NOT EXISTS idx_announcement_batches_guild_due
    ON announcement_batches (guild_id, scheduled_for_utc);

COMMIT;
