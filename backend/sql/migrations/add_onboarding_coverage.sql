-- ====================================================================
-- Migration: Add Onboarding Coverage Table
-- Student-Centric 'Describe Your Life' Intake Engine
-- ====================================================================

CREATE TABLE IF NOT EXISTS onboarding_coverage (
    user_id       UUID NOT NULL REFERENCES user_profiles(user_id) ON DELETE CASCADE,
    topic_id      VARCHAR(10) NOT NULL,
    status        VARCHAR(20) NOT NULL DEFAULT 'unknown', -- 'unknown' | 'partial' | 'answered' | 'skipped' | 'declined' | 'not_applicable'
    asked_count   INT NOT NULL DEFAULT 0,
    last_asked_at TIMESTAMPTZ,
    answered_via  UUID NULL,
    PRIMARY KEY (user_id, topic_id)
);

CREATE INDEX IF NOT EXISTS idx_onboarding_cov_user_status ON onboarding_coverage(user_id, status);
