-- Migration: add outcome_type and outcome_details columns to match table
-- Run this against MySQL in production. For SQLite (local dev), use migrate_match_outcome.py instead.

ALTER TABLE `match`
  ADD COLUMN `outcome_type`    VARCHAR(20) NOT NULL DEFAULT 'regular',
  ADD COLUMN `outcome_details` TEXT        NULL;

