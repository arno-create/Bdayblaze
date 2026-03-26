BEGIN;

CREATE TABLE IF NOT EXISTS guild_announcement_surfaces (
    guild_id BIGINT NOT NULL,
    surface_kind TEXT NOT NULL,
    channel_id BIGINT NULL,
    image_url TEXT NULL,
    thumbnail_url TEXT NULL,
    created_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at_utc TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (guild_id, surface_kind)
);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'guild_announcement_surfaces_kind_valid'
    ) THEN
        ALTER TABLE guild_announcement_surfaces
            ADD CONSTRAINT guild_announcement_surfaces_kind_valid CHECK (
                surface_kind IN (
                    'birthday_announcement',
                    'anniversary',
                    'server_anniversary',
                    'recurring_event'
                )
            );
    END IF;
END $$;

INSERT INTO guild_announcement_surfaces (
    guild_id,
    surface_kind,
    channel_id,
    image_url,
    thumbnail_url
)
SELECT
    guild_id,
    'birthday_announcement',
    announcement_channel_id,
    announcement_image_url,
    announcement_thumbnail_url
FROM guild_settings
WHERE announcement_channel_id IS NOT NULL
   OR announcement_image_url IS NOT NULL
   OR announcement_thumbnail_url IS NOT NULL
ON CONFLICT (guild_id, surface_kind) DO NOTHING;

INSERT INTO guild_announcement_surfaces (
    guild_id,
    surface_kind,
    channel_id,
    image_url,
    thumbnail_url
)
SELECT
    guild_id,
    'anniversary',
    anniversary_channel_id,
    NULL,
    NULL
FROM guild_settings
WHERE anniversary_channel_id IS NOT NULL
ON CONFLICT (guild_id, surface_kind) DO NOTHING;

COMMIT;
