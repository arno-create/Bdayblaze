BEGIN;

ALTER TABLE guild_experience_settings
    ADD COLUMN IF NOT EXISTS quest_reaction_target INTEGER NOT NULL DEFAULT 5;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_experience_settings_reaction_target_valid'
    ) THEN
        ALTER TABLE guild_experience_settings
            ADD CONSTRAINT guild_experience_settings_reaction_target_valid CHECK (
                quest_reaction_target BETWEEN 1 AND 25
            );
    END IF;
END $$;

ALTER TABLE birthday_celebrations
    ADD COLUMN IF NOT EXISTS announcement_message_id BIGINT NULL,
    ADD COLUMN IF NOT EXISTS quest_reaction_target INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS quest_reaction_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS quest_reaction_goal_met BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'birthday_celebrations_reaction_target_valid'
    ) THEN
        ALTER TABLE birthday_celebrations
            ADD CONSTRAINT birthday_celebrations_reaction_target_valid CHECK (
                quest_reaction_target >= 0
            );
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'birthday_celebrations_reaction_count_valid'
    ) THEN
        ALTER TABLE birthday_celebrations
            ADD CONSTRAINT birthday_celebrations_reaction_count_valid CHECK (
                quest_reaction_count >= 0
            );
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS idx_birthday_celebrations_announcement_message
    ON birthday_celebrations (guild_id, announcement_message_id)
    WHERE announcement_message_id IS NOT NULL;

COMMIT;
