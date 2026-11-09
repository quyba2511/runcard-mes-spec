-- Additive migration. The original spec files remain unchanged.
ALTER TABLE run_cards ADD COLUMN returned_qty numeric NOT NULL DEFAULT 0;
ALTER TABLE run_cards ADD COLUMN transferred_qty numeric NOT NULL DEFAULT 0;
ALTER TABLE run_cards ADD COLUMN abnormal_counts jsonb NOT NULL DEFAULT '{}';
ALTER TABLE run_cards ADD COLUMN origin_record_id uuid REFERENCES work_records(id);
ALTER TABLE run_cards DROP CONSTRAINT qty_identity;
ALTER TABLE run_cards ADD CONSTRAINT qty_identity CHECK (current_qty+scrap_qty+rework_qty+split_qty+defect_qty+returned_qty+transferred_qty=original_qty);
ALTER TABLE run_cards ADD CONSTRAINT nonnegative_qty CHECK (current_qty>=0 AND scrap_qty>=0 AND rework_qty>=0 AND split_qty>=0 AND defect_qty>=0 AND returned_qty>=0 AND transferred_qty>=0);
ALTER TABLE run_cards ADD CONSTRAINT route_consistent CHECK ((route_free AND route_version_id IS NULL AND current_node_id IS NULL) OR (NOT route_free AND route_version_id IS NOT NULL AND current_node_id IS NOT NULL));
ALTER TABLE work_records ADD COLUMN before_card jsonb;
ALTER TABLE work_records ADD COLUMN after_card_version int;
ALTER TABLE work_records ADD CONSTRAINT work_times CHECK (ended_at IS NULL OR ended_at>=started_at);
ALTER TABLE sync_outbox ADD COLUMN next_attempt_at timestamptz NOT NULL DEFAULT now();
CREATE INDEX pending_outbox ON sync_outbox(next_attempt_at) WHERE status <> 'SENT';
CREATE SEQUENCE child_card_seq;
CREATE TABLE quantity_movements (
 id bigserial PRIMARY KEY, run_card_id uuid NOT NULL REFERENCES run_cards(id),
 disposition text NOT NULL CHECK (disposition IN ('scrap','return','transfer','revoke-defect')),
 qty numeric NOT NULL CHECK(qty>0), target_card_id uuid REFERENCES run_cards(id),
 actor_id uuid NOT NULL REFERENCES accounts(id), reason text NOT NULL, at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE auth_sessions (
 token_hash text PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(id),
 mode text NOT NULL CHECK(mode IN ('admin','operator')), expires_at timestamptz NOT NULL,
 single_use boolean NOT NULL DEFAULT false
);
CREATE TABLE skip_authorizations (
 token_hash text PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(id),
 run_card_id uuid NOT NULL REFERENCES run_cards(id), target_node_id int NOT NULL REFERENCES route_nodes(id),
 expires_at timestamptz NOT NULL, used_at timestamptz
);
CREATE TABLE command_receipts (
 actor_id uuid NOT NULL REFERENCES accounts(id), key text NOT NULL, fingerprint text NOT NULL,
 result jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(actor_id,key)
);
CREATE TABLE app_settings (key text PRIMARY KEY, value jsonb NOT NULL);
INSERT INTO app_settings VALUES ('release_reason_required','true');
CREATE TABLE notification_jobs (
 id bigserial PRIMARY KEY, event_code text NOT NULL REFERENCES notification_rules(event_code),
 run_card_id uuid REFERENCES run_cards(id), equipment_id uuid REFERENCES equipments(id),
 title text NOT NULL, body text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
 next_at timestamptz NOT NULL DEFAULT now(), repeat_count int NOT NULL DEFAULT 0,
 escalated boolean NOT NULL DEFAULT false, resolved_at timestamptz
);
CREATE TABLE email_deliveries (
 id bigserial PRIMARY KEY, account_id uuid NOT NULL REFERENCES accounts(id), title text NOT NULL, body text NOT NULL,
 attempts int NOT NULL DEFAULT 0, next_at timestamptz NOT NULL DEFAULT now(), sent_at timestamptz, last_error text
);
INSERT INTO equipment_status_history(equipment_id,state,reason_code) SELECT id,state,reason_code FROM equipments;
ALTER TABLE route_nodes ADD CONSTRAINT node_version_pair UNIQUE(id,route_version_id);
ALTER TABLE run_cards ADD CONSTRAINT card_node_same_version FOREIGN KEY(current_node_id,route_version_id) REFERENCES route_nodes(id,route_version_id);
CREATE FUNCTION preserve_card_route() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.route_free IS DISTINCT FROM OLD.route_free OR NEW.route_version_id IS DISTINCT FROM OLD.route_version_id THEN
  RAISE EXCEPTION 'BR-11: card route mode and version are immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER immutable_card_route BEFORE UPDATE ON run_cards FOR EACH ROW EXECUTE FUNCTION preserve_card_route();
ALTER TABLE work_records ADD COLUMN after_card jsonb;
