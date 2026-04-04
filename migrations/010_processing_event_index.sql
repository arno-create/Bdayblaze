BEGIN;

CREATE INDEX IF NOT EXISTS celebration_events_processing_started_idx
    ON celebration_events (processing_started_at_utc)
    WHERE state = 'processing';

COMMIT;
