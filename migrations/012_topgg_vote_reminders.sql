CREATE TABLE IF NOT EXISTS topgg_vote_reminders (
    discord_user_id BIGINT PRIMARY KEY,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    scheduled_vote_expires_at TIMESTAMPTZ,
    scheduled_reminder_at TIMESTAMPTZ,
    processing_started_at TIMESTAMPTZ,
    last_reminded_vote_expires_at TIMESTAMPTZ,
    last_reminded_at TIMESTAMPTZ,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    last_error_code TEXT,
    timing_source TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_topgg_vote_reminders_due
    ON topgg_vote_reminders (scheduled_reminder_at)
    WHERE enabled = TRUE AND scheduled_reminder_at IS NOT NULL;
