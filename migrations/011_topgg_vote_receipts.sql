BEGIN;

CREATE TABLE IF NOT EXISTS topgg_vote_receipts (
    event_id TEXT PRIMARY KEY,
    discord_user_id BIGINT NOT NULL,
    event_type TEXT NOT NULL,
    webhook_mode TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    trace_id TEXT NULL,
    signature_timestamp TIMESTAMPTZ NULL,
    vote_created_at TIMESTAMPTZ NULL,
    vote_expires_at TIMESTAMPTZ NULL,
    timing_source TEXT NULL,
    weight INTEGER NOT NULL DEFAULT 1,
    received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    processed_at TIMESTAMPTZ NOT NULL,
    status TEXT NOT NULL,
    error_text TEXT NULL,
    CONSTRAINT topgg_vote_receipts_webhook_mode_valid CHECK (
        webhook_mode IN ('v2', 'legacy')
    ),
    CONSTRAINT topgg_vote_receipts_timing_source_valid CHECK (
        timing_source IS NULL OR timing_source IN ('exact', 'legacy_estimated')
    ),
    CONSTRAINT topgg_vote_receipts_status_valid CHECK (
        status IN ('processed', 'ignored_test')
    ),
    CONSTRAINT topgg_vote_receipts_weight_valid CHECK (weight >= 0)
);

CREATE INDEX IF NOT EXISTS idx_topgg_vote_receipts_user_expires
    ON topgg_vote_receipts (discord_user_id, vote_expires_at DESC, processed_at DESC);

CREATE INDEX IF NOT EXISTS idx_topgg_vote_receipts_received
    ON topgg_vote_receipts (received_at DESC);

COMMIT;
