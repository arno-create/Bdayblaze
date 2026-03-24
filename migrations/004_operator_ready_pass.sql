BEGIN;

ALTER TABLE guild_settings
    ADD COLUMN IF NOT EXISTS announcement_title_override TEXT NULL,
    ADD COLUMN IF NOT EXISTS announcement_footer_text TEXT NULL,
    ADD COLUMN IF NOT EXISTS announcement_image_url TEXT NULL,
    ADD COLUMN IF NOT EXISTS announcement_thumbnail_url TEXT NULL,
    ADD COLUMN IF NOT EXISTS announcement_accent_color INTEGER NULL,
    ADD COLUMN IF NOT EXISTS birthday_dm_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS birthday_dm_template TEXT NULL,
    ADD COLUMN IF NOT EXISTS anniversary_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS anniversary_channel_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS anniversary_template TEXT NULL,
    ADD COLUMN IF NOT EXISTS eligibility_role_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS ignore_bots BOOLEAN NOT NULL DEFAULT TRUE,
    ADD COLUMN IF NOT EXISTS minimum_membership_days INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS mention_suppression_threshold INTEGER NOT NULL DEFAULT 8;

ALTER TABLE member_birthdays
    ADD COLUMN IF NOT EXISTS profile_visibility TEXT NOT NULL DEFAULT 'private';

UPDATE member_birthdays
SET profile_visibility = 'private'
WHERE profile_visibility IS NULL;

ALTER TABLE member_birthdays
    DROP COLUMN IF EXISTS age_visible;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_settings_announcement_template_length'
    ) THEN
        ALTER TABLE guild_settings DROP CONSTRAINT guild_settings_announcement_template_length;
    END IF;
END $$;

ALTER TABLE guild_settings
    ADD CONSTRAINT guild_settings_announcement_template_length CHECK (
        announcement_template IS NULL OR char_length(announcement_template) <= 1200
    );

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_settings_announcement_theme_valid'
    ) THEN
        ALTER TABLE guild_settings DROP CONSTRAINT guild_settings_announcement_theme_valid;
    END IF;
END $$;

ALTER TABLE guild_settings
    ADD CONSTRAINT guild_settings_announcement_theme_valid CHECK (
        announcement_theme IN ('classic', 'festive', 'minimal', 'cute', 'elegant', 'gaming')
    );

ALTER TABLE guild_settings
    ADD CONSTRAINT guild_settings_membership_days_valid CHECK (
        minimum_membership_days >= 0
    );

ALTER TABLE guild_settings
    ADD CONSTRAINT guild_settings_mention_threshold_valid CHECK (
        mention_suppression_threshold BETWEEN 1 AND 50
    );

ALTER TABLE guild_settings
    ADD CONSTRAINT guild_settings_announcement_accent_color_valid CHECK (
        announcement_accent_color IS NULL
        OR announcement_accent_color BETWEEN 0 AND 16777215
    );

ALTER TABLE member_birthdays
    ADD CONSTRAINT member_birthdays_profile_visibility_valid CHECK (
        profile_visibility IN ('private', 'server_visible')
    );

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'celebration_events_kind_valid'
    ) THEN
        ALTER TABLE celebration_events DROP CONSTRAINT celebration_events_kind_valid;
    END IF;
END $$;

ALTER TABLE celebration_events
    ADD CONSTRAINT celebration_events_kind_valid CHECK (
        event_kind IN (
            'announcement',
            'birthday_dm',
            'anniversary_announcement',
            'recurring_announcement',
            'role_start',
            'role_end'
        )
    );

CREATE TABLE IF NOT EXISTS tracked_member_anniversaries (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    joined_at_utc TIMESTAMPTZ NOT NULL,
    next_occurrence_at_utc TIMESTAMPTZ NOT NULL,
    source TEXT NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_tracked_anniversaries_next_occurrence
    ON tracked_member_anniversaries (next_occurrence_at_utc);

CREATE TABLE IF NOT EXISTS recurring_celebrations (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    name TEXT NOT NULL,
    event_month SMALLINT NOT NULL,
    event_day SMALLINT NOT NULL,
    channel_id BIGINT NULL,
    template TEXT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    next_occurrence_at_utc TIMESTAMPTZ NOT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT recurring_celebrations_date_valid CHECK (
        make_date(2000, event_month, event_day) IS NOT NULL
    ),
    CONSTRAINT recurring_celebrations_name_nonempty CHECK (char_length(btrim(name)) > 0)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_recurring_celebrations_guild_name
    ON recurring_celebrations (guild_id, lower(name));

CREATE INDEX IF NOT EXISTS idx_recurring_celebrations_next_occurrence
    ON recurring_celebrations (next_occurrence_at_utc)
    WHERE enabled = TRUE;

CREATE INDEX IF NOT EXISTS idx_member_birthdays_visibility_month_day
    ON member_birthdays (guild_id, profile_visibility, birth_month, birth_day);

COMMIT;
