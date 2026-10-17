-- WagWatch Multi-Household Schema with Row Level Security (RLS)
-- Created for Supabase PostgreSQL (Postgres 17+)

CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS households (
    id VARCHAR(32) PRIMARY KEY,
    name VARCHAR(64) DEFAULT '',
    dog_name VARCHAR(64) DEFAULT 'Your dog',
    location VARCHAR(64) DEFAULT '',
    recovery_hash VARCHAR(64) NOT NULL,
    llm_enabled BOOLEAN DEFAULT FALSE,
    log_enabled BOOLEAN DEFAULT FALSE,
    created_at DOUBLE PRECISION NOT NULL,
    deleted_at DOUBLE PRECISION
);

CREATE TABLE IF NOT EXISTS viewers (
    id VARCHAR(32) PRIMARY KEY,
    household_id VARCHAR(32) NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    role VARCHAR(16) NOT NULL,
    label VARCHAR(64) DEFAULT '',
    token_hash VARCHAR(64) UNIQUE NOT NULL,
    mode_ceiling VARCHAR(16) DEFAULT 'full',
    expires_at DOUBLE PRECISION,
    revoked_at DOUBLE PRECISION,
    created_at DOUBLE PRECISION NOT NULL,
    last_seen DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_viewers_household_id ON viewers(household_id);
CREATE INDEX IF NOT EXISTS ix_viewers_token_hash ON viewers(token_hash);

CREATE TABLE IF NOT EXISTS stations (
    id VARCHAR(32) PRIMARY KEY,
    household_id VARCHAR(32) NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    name VARCHAR(64) DEFAULT '',
    dog_name VARCHAR(64),
    pubkey_spki BYTEA NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    revoked_at DOUBLE PRECISION,
    last_seen DOUBLE PRECISION
);
CREATE INDEX IF NOT EXISTS ix_stations_household_id ON stations(household_id);

CREATE TABLE IF NOT EXISTS pairing_codes (
    code_hash VARCHAR(64) PRIMARY KEY,
    household_id VARCHAR(32) NOT NULL REFERENCES households(id) ON DELETE CASCADE,
    kind VARCHAR(16) NOT NULL,
    expires_at DOUBLE PRECISION NOT NULL,
    used_at DOUBLE PRECISION,
    created_by_viewer VARCHAR(32) REFERENCES viewers(id),
    mode_ceiling VARCHAR(16),
    viewer_label TEXT
);
CREATE INDEX IF NOT EXISTS ix_pairing_codes_household_id ON pairing_codes(household_id);
CREATE INDEX IF NOT EXISTS ix_pairing_codes_expires_at ON pairing_codes(expires_at);

CREATE TABLE IF NOT EXISTS app_settings (
    key VARCHAR(64) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at DOUBLE PRECISION NOT NULL
);

-- ============================================================================
-- Security: Enable Row Level Security (RLS) on all WagWatch tables
-- Ensures anon/public web API access is completely blocked.
-- Only authenticated database roles (backend server with Postgres credentials) have access.
-- ============================================================================
ALTER TABLE meta ENABLE ROW LEVEL SECURITY;
ALTER TABLE households ENABLE ROW LEVEL SECURITY;
ALTER TABLE viewers ENABLE ROW LEVEL SECURITY;
ALTER TABLE stations ENABLE ROW LEVEL SECURITY;
ALTER TABLE pairing_codes ENABLE ROW LEVEL SECURITY;
ALTER TABLE app_settings ENABLE ROW LEVEL SECURITY;
