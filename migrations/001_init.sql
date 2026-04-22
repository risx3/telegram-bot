-- Migration: 001_init.sql
-- Telegram Deposit Bot — initial schema

CREATE TABLE IF NOT EXISTS transactions (
    id          SERIAL PRIMARY KEY,
    txn_id      VARCHAR(64) UNIQUE NOT NULL,
    phone       VARCHAR(20),                    -- NULL until user confirms via bot
    amount      DECIMAL(10,2) NOT NULL,
    bank        VARCHAR(50),
    sms_raw     TEXT NOT NULL,
    confirmed   BOOLEAN DEFAULT FALSE,
    received_at TIMESTAMP DEFAULT NOW(),
    updated_at  TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_transactions_txn_id  ON transactions(txn_id);
CREATE INDEX IF NOT EXISTS idx_transactions_phone    ON transactions(phone);
CREATE INDEX IF NOT EXISTS idx_transactions_confirmed ON transactions(confirmed);
