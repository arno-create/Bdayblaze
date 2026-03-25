BEGIN;

ALTER TABLE guild_settings
    ADD COLUMN IF NOT EXISTS studio_audit_channel_id BIGINT NULL;

COMMIT;
