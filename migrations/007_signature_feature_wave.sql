BEGIN;

CREATE TABLE IF NOT EXISTS guild_experience_settings (
    guild_id BIGINT PRIMARY KEY,
    capsules_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    quests_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    quest_wish_target INTEGER NOT NULL DEFAULT 3,
    quest_checkin_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    surprises_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT guild_experience_settings_quest_target_valid CHECK (
        quest_wish_target BETWEEN 1 AND 25
    )
);

CREATE TABLE IF NOT EXISTS birthday_wishes (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    author_user_id BIGINT NOT NULL,
    target_user_id BIGINT NOT NULL,
    wish_text TEXT NOT NULL,
    link_url TEXT NULL,
    state TEXT NOT NULL DEFAULT 'queued',
    celebration_occurrence_at_utc TIMESTAMPTZ NULL,
    revealed_at_utc TIMESTAMPTZ NULL,
    removed_at_utc TIMESTAMPTZ NULL,
    moderated_by_user_id BIGINT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT birthday_wishes_state_valid CHECK (
        state IN ('queued', 'revealed', 'removed', 'moderated')
    ),
    CONSTRAINT birthday_wishes_text_nonempty CHECK (char_length(btrim(wish_text)) > 0),
    CONSTRAINT birthday_wishes_text_length CHECK (char_length(wish_text) <= 350),
    CONSTRAINT birthday_wishes_link_length CHECK (
        link_url IS NULL OR char_length(link_url) <= 500
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_birthday_wishes_active_pair
    ON birthday_wishes (guild_id, author_user_id, target_user_id)
    WHERE state = 'queued';

CREATE INDEX IF NOT EXISTS idx_birthday_wishes_target_state
    ON birthday_wishes (guild_id, target_user_id, state, created_at_utc);

CREATE INDEX IF NOT EXISTS idx_birthday_wishes_author_state
    ON birthday_wishes (guild_id, author_user_id, state, created_at_utc);

CREATE TABLE IF NOT EXISTS birthday_celebrations (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    occurrence_start_at_utc TIMESTAMPTZ NOT NULL,
    late_delivery BOOLEAN NOT NULL DEFAULT FALSE,
    capsule_state TEXT NOT NULL DEFAULT 'disabled',
    capsule_message_id BIGINT NULL,
    revealed_wish_count INTEGER NOT NULL DEFAULT 0,
    quest_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    quest_wish_target INTEGER NOT NULL DEFAULT 0,
    quest_wish_goal_met BOOLEAN NOT NULL DEFAULT FALSE,
    quest_checkin_required BOOLEAN NOT NULL DEFAULT FALSE,
    quest_checked_in_at_utc TIMESTAMPTZ NULL,
    quest_completed_at_utc TIMESTAMPTZ NULL,
    featured_birthday BOOLEAN NOT NULL DEFAULT FALSE,
    surprise_reward_type TEXT NULL,
    surprise_reward_label TEXT NULL,
    surprise_note_text TEXT NULL,
    surprise_selected_at_utc TIMESTAMPTZ NULL,
    nitro_fulfillment_status TEXT NULL,
    nitro_fulfilled_by_user_id BIGINT NULL,
    nitro_fulfilled_at_utc TIMESTAMPTZ NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT birthday_celebrations_occurrence_unique UNIQUE (guild_id, user_id, occurrence_start_at_utc),
    CONSTRAINT birthday_celebrations_capsule_state_valid CHECK (
        capsule_state IN (
            'disabled',
            'no_wishes',
            'revealed_private',
            'pending_public',
            'posted_public'
        )
    ),
    CONSTRAINT birthday_celebrations_quest_target_valid CHECK (quest_wish_target >= 0),
    CONSTRAINT birthday_celebrations_surprise_type_valid CHECK (
        surprise_reward_type IS NULL
        OR surprise_reward_type IN ('featured', 'badge', 'custom_note', 'nitro_concierge')
    ),
    CONSTRAINT birthday_celebrations_nitro_status_valid CHECK (
        nitro_fulfillment_status IS NULL
        OR nitro_fulfillment_status IN ('pending', 'delivered', 'not_delivered')
    ),
    CONSTRAINT birthday_celebrations_note_length CHECK (
        surprise_note_text IS NULL OR char_length(surprise_note_text) <= 200
    )
);

CREATE INDEX IF NOT EXISTS idx_birthday_celebrations_guild_occurrence
    ON birthday_celebrations (guild_id, occurrence_start_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_birthday_celebrations_user_occurrence
    ON birthday_celebrations (guild_id, user_id, occurrence_start_at_utc DESC);

CREATE INDEX IF NOT EXISTS idx_birthday_celebrations_nitro_pending
    ON birthday_celebrations (guild_id, nitro_fulfillment_status, occurrence_start_at_utc DESC)
    WHERE nitro_fulfillment_status IS NOT NULL;

CREATE TABLE IF NOT EXISTS guild_surprise_rewards (
    id BIGSERIAL PRIMARY KEY,
    guild_id BIGINT NOT NULL,
    reward_type TEXT NOT NULL,
    label TEXT NOT NULL,
    weight INTEGER NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    note_text TEXT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT guild_surprise_rewards_type_valid CHECK (
        reward_type IN ('featured', 'badge', 'custom_note', 'nitro_concierge')
    ),
    CONSTRAINT guild_surprise_rewards_weight_valid CHECK (weight BETWEEN 0 AND 1000),
    CONSTRAINT guild_surprise_rewards_label_nonempty CHECK (char_length(btrim(label)) > 0),
    CONSTRAINT guild_surprise_rewards_note_length CHECK (
        note_text IS NULL OR char_length(note_text) <= 200
    ),
    CONSTRAINT guild_surprise_rewards_unique_type UNIQUE (guild_id, reward_type)
);

CREATE INDEX IF NOT EXISTS idx_guild_surprise_rewards_enabled
    ON guild_surprise_rewards (guild_id, enabled, weight DESC);

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
            'capsule_reveal',
            'role_start',
            'role_end'
        )
    );

COMMIT;
