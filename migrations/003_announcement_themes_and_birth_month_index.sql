BEGIN;

ALTER TABLE guild_settings
    ADD COLUMN IF NOT EXISTS announcement_theme TEXT NOT NULL DEFAULT 'classic';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_settings_announcement_theme_valid'
    ) THEN
        ALTER TABLE guild_settings
            ADD CONSTRAINT guild_settings_announcement_theme_valid CHECK (
                announcement_theme IN ('classic', 'festive', 'minimal', 'cute')
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_member_birthdays_guild_month_day
    ON member_birthdays (guild_id, birth_month, birth_day);

COMMIT;
