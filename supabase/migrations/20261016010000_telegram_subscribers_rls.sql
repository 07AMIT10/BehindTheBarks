-- household_telegram_subscribers (ORM: backend/store/models.py HouseholdTelegramSubscriber).
--
-- This is the 6th ORM table. Until now it was created implicitly by SQLAlchemy create_all, which
-- builds it WITHOUT Row Level Security; with the Data API auto-exposing new tables in the public
-- schema it becomes the only anon-readable app table (Telegram chat ids + household ids).
-- Create it explicitly, default-deny like the rest of the schema, before any app code runs.
--
-- Column/type parity with create_all on Postgres (verified against
-- CreateTable(HouseholdTelegramSubscriber).compile(dialect=postgresql)): Float -> DOUBLE PRECISION;
-- the ORM's client-side defaults are mirrored as server defaults so hand-written rows match
-- ORM-written ones.

CREATE TABLE IF NOT EXISTS household_telegram_subscribers (
    id VARCHAR(32) PRIMARY KEY,
    household_id VARCHAR(32) NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    chat_id VARCHAR(64) NOT NULL,
    chat_type VARCHAR(16) DEFAULT 'private' NOT NULL,
    title VARCHAR(128) DEFAULT '' NOT NULL,
    label VARCHAR(64) DEFAULT '' NOT NULL,
    notify_barks BOOLEAN DEFAULT TRUE NOT NULL,
    notify_distress BOOLEAN DEFAULT TRUE NOT NULL,
    notify_battery BOOLEAN DEFAULT TRUE NOT NULL,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_household_telegram_subscribers_household_id
    ON household_telegram_subscribers(household_id);
CREATE INDEX IF NOT EXISTS ix_household_telegram_subscribers_chat_id
    ON household_telegram_subscribers(chat_id);

-- Security: default-deny RLS, matching 20261016000000_init_wagwatch.sql.
-- No permissive policies are granted: anon/authenticated get nothing;
-- the backend's Postgres role bypasses RLS by design.
ALTER TABLE household_telegram_subscribers ENABLE ROW LEVEL SECURITY;
