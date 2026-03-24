BEGIN;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id BIGINT PRIMARY KEY,
    announcement_channel_id BIGINT NULL,
    default_timezone TEXT NOT NULL,
    birthday_role_id BIGINT NULL,
    announcements_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    role_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    celebration_mode TEXT NOT NULL DEFAULT 'quiet',
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT guild_settings_timezone_nonempty CHECK (char_length(default_timezone) > 0),
    CONSTRAINT guild_settings_mode_valid CHECK (celebration_mode IN ('quiet', 'party'))
);

CREATE TABLE IF NOT EXISTS member_birthdays (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    birth_month SMALLINT NOT NULL,
    birth_day SMALLINT NOT NULL,
    birth_year SMALLINT NULL,
    timezone_override TEXT NULL,
    age_visible BOOLEAN NOT NULL DEFAULT FALSE,
    next_occurrence_at_utc TIMESTAMPTZ NOT NULL,
    next_role_removal_at_utc TIMESTAMPTZ NULL,
    active_birthday_role_id BIGINT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, user_id),
    CONSTRAINT member_birthdays_month_valid CHECK (birth_month BETWEEN 1 AND 12),
    CONSTRAINT member_birthdays_day_valid CHECK (birth_day BETWEEN 1 AND 31),
    CONSTRAINT member_birthdays_real_date CHECK (
        make_date(2000, birth_month, birth_day) IS NOT NULL
    ),
    CONSTRAINT member_birthdays_birth_year_valid CHECK (
        birth_year IS NULL OR birth_year BETWEEN 1900 AND 9999
    ),
    CONSTRAINT member_birthdays_timezone_nonempty CHECK (
        timezone_override IS NULL OR char_length(timezone_override) > 0
    ),
    CONSTRAINT member_birthdays_active_role_consistency CHECK (
        (next_role_removal_at_utc IS NULL AND active_birthday_role_id IS NULL)
        OR (next_role_removal_at_utc IS NOT NULL AND active_birthday_role_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_member_birthdays_next_occurrence
    ON member_birthdays (next_occurrence_at_utc);

CREATE INDEX IF NOT EXISTS idx_member_birthdays_next_role_removal
    ON member_birthdays (next_role_removal_at_utc)
    WHERE next_role_removal_at_utc IS NOT NULL;

CREATE TABLE IF NOT EXISTS celebration_events (
    id BIGSERIAL PRIMARY KEY,
    event_key TEXT NOT NULL UNIQUE,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NULL,
    event_kind TEXT NOT NULL,
    scheduled_for_utc TIMESTAMPTZ NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    payload JSONB NOT NULL DEFAULT '{}'::JSONB,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT NULL,
    message_id BIGINT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at_utc TIMESTAMPTZ NULL,
    processing_started_at_utc TIMESTAMPTZ NULL,
    CONSTRAINT celebration_events_kind_valid CHECK (
        event_kind IN ('announcement', 'role_start', 'role_end')
    ),
    CONSTRAINT celebration_events_state_valid CHECK (
        state IN ('pending', 'processing', 'completed')
    )
);

CREATE INDEX IF NOT EXISTS idx_celebration_events_state_due
    ON celebration_events (state, scheduled_for_utc);

CREATE INDEX IF NOT EXISTS idx_celebration_events_guild_due
    ON celebration_events (guild_id, scheduled_for_utc);

COMMIT;
