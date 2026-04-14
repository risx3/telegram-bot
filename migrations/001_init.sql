-- Migration: 001_init.sql
-- Telegram Deposit Bot — initial schema

CREATE TABLE IF NOT EXISTS users (
    id              SERIAL PRIMARY KEY,
    phone           VARCHAR(15) UNIQUE NOT NULL,
    telegram_id     BIGINT UNIQUE,
    name            VARCHAR(100),
    balance         DECIMAL(12,2) DEFAULT 0.00,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS received_transactions (
    id              SERIAL PRIMARY KEY,
    txn_id          VARCHAR(64) UNIQUE NOT NULL,
    amount          DECIMAL(10,2) NOT NULL,
    received_at     TIMESTAMP DEFAULT NOW(),
    sms_raw         TEXT NOT NULL,
    matched_user_id INT REFERENCES users(id),
    credited        BOOLEAN DEFAULT FALSE,
    created_at      TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS deposit_sessions (
    id              SERIAL PRIMARY KEY,
    user_id         INT REFERENCES users(id),
    session_ref     VARCHAR(20) UNIQUE NOT NULL,
    status          VARCHAR(20) DEFAULT 'pending',
    created_at      TIMESTAMP DEFAULT NOW(),
    expires_at      TIMESTAMP DEFAULT NOW() + INTERVAL '2 hours'
);

CREATE TABLE IF NOT EXISTS manual_review (
    id              SERIAL PRIMARY KEY,
    telegram_id     BIGINT NOT NULL,
    txn_id          VARCHAR(64) NOT NULL,
    submitted_at    TIMESTAMP DEFAULT NOW(),
    resolved        BOOLEAN DEFAULT FALSE,
    notes           TEXT
);

-- Indexes for frequent lookups
CREATE INDEX IF NOT EXISTS idx_received_transactions_txn_id ON received_transactions(txn_id);
CREATE INDEX IF NOT EXISTS idx_received_transactions_credited ON received_transactions(credited);
CREATE INDEX IF NOT EXISTS idx_deposit_sessions_user_id ON deposit_sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_deposit_sessions_status ON deposit_sessions(status);
CREATE INDEX IF NOT EXISTS idx_manual_review_resolved ON manual_review(resolved);
