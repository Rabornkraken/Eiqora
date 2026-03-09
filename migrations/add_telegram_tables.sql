-- Telegram Bot: subscriber and broadcast log tables
-- Run: psql -f migrations/add_telegram_tables.sql $DATABASE_URL

BEGIN;

CREATE TABLE IF NOT EXISTS telegram_subscriber (
    id              BIGSERIAL PRIMARY KEY,
    chat_id         BIGINT UNIQUE NOT NULL,
    username        TEXT,
    first_name      TEXT,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    preferences     JSONB NOT NULL DEFAULT '{}',
    subscribed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    unsubscribed_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_telegram_subscriber_active
    ON telegram_subscriber (is_active) WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS telegram_broadcast_log (
    id              BIGSERIAL PRIMARY KEY,
    signal_id       UUID,
    broadcast_type  TEXT NOT NULL,  -- 'signal', 'daily_summary', 'command_response'
    message_hash    TEXT,           -- for dedup
    recipients      INT NOT NULL DEFAULT 0,
    failures        INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_broadcast_log_signal
    ON telegram_broadcast_log (signal_id);
CREATE INDEX IF NOT EXISTS idx_broadcast_log_created
    ON telegram_broadcast_log (created_at DESC);

COMMIT;
