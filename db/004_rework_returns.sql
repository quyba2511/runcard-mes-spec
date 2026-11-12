CREATE TABLE rework_returns (
    rework_card_id uuid PRIMARY KEY REFERENCES run_cards(id),
    target_card_id uuid NOT NULL REFERENCES run_cards(id),
    target_bucket text NOT NULL CHECK (target_bucket IN ('rework_qty', 'defect_qty')),
    resume_status runcard_status NOT NULL CHECK (resume_status IN ('WAITING', 'QUALITY_HOLD', 'ABNORMAL')),
    received_qty numeric NOT NULL DEFAULT 0 CHECK (received_qty >= 0),
    finished_at timestamptz,
    CHECK (rework_card_id <> target_card_id)
);

CREATE UNIQUE INDEX rework_return_pending_target
    ON rework_returns(target_card_id) WHERE finished_at IS NULL;
