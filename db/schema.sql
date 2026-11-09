-- RunCard MES · data model đề xuất (PostgreSQL 15+)
-- Suy ra từ giao diện và hành vi quan sát được; không phải schema gốc của hệ thống tham chiếu.

-- ===== Quyền =====
CREATE TABLE roles (
  id          serial PRIMARY KEY,
  code        text UNIQUE NOT NULL,          -- operator, qa, pc_supervisor...
  name        text NOT NULL
);

CREATE TABLE permissions (
  code        text PRIMARY KEY,              -- run_cards:revoke_work ...
  description text NOT NULL
);

CREATE TABLE role_permissions (
  role_id         int  REFERENCES roles(id) ON DELETE CASCADE,
  permission_code text REFERENCES permissions(code) ON DELETE CASCADE,
  PRIMARY KEY (role_id, permission_code)
);

CREATE TABLE accounts (
  id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  username      text UNIQUE NOT NULL,
  display_name  text NOT NULL,
  employee_no   text UNIQUE,                 -- 工號 (員工編號)
  email         text,
  role_id       int REFERENCES roles(id),
  password_hash text NOT NULL,
  is_active     boolean NOT NULL DEFAULT true,
  created_at    timestamptz NOT NULL DEFAULT now()
);

-- ===== Dữ liệu gốc =====
CREATE TABLE operations (                    -- 工序主檔
  id                 serial PRIMARY KEY,
  code               text UNIQUE NOT NULL,   -- DEMO_PRT
  name               text NOT NULL,
  requires_equipment boolean NOT NULL DEFAULT true,
  is_active          boolean NOT NULL DEFAULT true
);

CREATE TABLE operation_qc_items (            -- hạng mục QC khi 下工
  id            serial PRIMARY KEY,
  operation_id  int REFERENCES operations(id) ON DELETE CASCADE,
  name          text NOT NULL,
  unit          text,
  lower_limit   numeric,
  upper_limit   numeric
);

CREATE TABLE defect_codes (                  -- 瑕疵代碼
  id          serial PRIMARY KEY,
  code        text UNIQUE NOT NULL,
  name        text NOT NULL,
  is_active   boolean NOT NULL DEFAULT true
);

CREATE TABLE operation_defect_codes (        -- mã lỗi dùng được ở 工序
  operation_id   int REFERENCES operations(id) ON DELETE CASCADE,
  defect_code_id int REFERENCES defect_codes(id) ON DELETE CASCADE,
  PRIMARY KEY (operation_id, defect_code_id)
);

CREATE TYPE equipment_state AS ENUM ('ENABLED', 'MAINTENANCE', 'OFF');  -- 啟用中 / 維修中 / 未開機

CREATE TABLE equipment_reasons (
  code          text PRIMARY KEY,            -- UP, US_4, DU_1, OFF
  state         equipment_state NOT NULL,
  main_category text NOT NULL,               -- 稼動時間 / 停機
  sub_category  text NOT NULL,               -- 正常生產 / 待機 / 工程實驗 / 計畫內 / 非計畫內
  name          text NOT NULL,
  allowed_while_occupied boolean NOT NULL DEFAULT false  -- UP, US_2, US_5 = true
);

CREATE TABLE equipments (                    -- 設備
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  mes_code     text UNIQUE NOT NULL,         -- DEMO-PRT01 (dùng làm barcode/QR)
  name         text NOT NULL,
  location     text,
  state        equipment_state NOT NULL DEFAULT 'OFF',
  reason_code  text REFERENCES equipment_reasons(code),
  is_active    boolean NOT NULL DEFAULT true,
  created_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE equipment_operations (          -- 設定可執行工序
  equipment_id uuid REFERENCES equipments(id) ON DELETE CASCADE,
  operation_id int  REFERENCES operations(id) ON DELETE CASCADE,
  PRIMARY KEY (equipment_id, operation_id)
);

CREATE TABLE equipment_reason_whitelist (    -- lý do hiển thị theo máy (OPT-11)
  equipment_id uuid REFERENCES equipments(id) ON DELETE CASCADE,
  reason_code  text REFERENCES equipment_reasons(code),
  PRIMARY KEY (equipment_id, reason_code)
);

-- ===== Tuyến =====
CREATE TABLE process_routes (                -- 工藝路線
  id        serial PRIMARY KEY,
  name      text NOT NULL,
  is_active boolean NOT NULL DEFAULT true
);

CREATE TABLE process_route_versions (
  id         serial PRIMARY KEY,
  route_id   int REFERENCES process_routes(id) ON DELETE CASCADE,
  version    text NOT NULL,                  -- v1.0
  status     text NOT NULL CHECK (status IN ('DRAFT','ACTIVE','RETIRED')),
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (route_id, version)
);
CREATE UNIQUE INDEX one_active_version ON process_route_versions(route_id) WHERE status = 'ACTIVE';

CREATE TABLE route_nodes (
  id               serial PRIMARY KEY,
  route_version_id int REFERENCES process_route_versions(id) ON DELETE CASCADE,
  seq              int NOT NULL,
  operation_id     int REFERENCES operations(id),
  UNIQUE (route_version_id, seq)
);

-- ===== RunCard =====
CREATE TYPE runcard_status AS ENUM (
  'WAITING',        -- 待上工
  'IN_PROGRESS',    -- 進行中
  'QUALITY_HOLD',   -- 品質保留
  'DEFECT_HOLD',    -- 瑕疵保留 (thẻ -D)
  'ABNORMAL',       -- 異常
  'COMPLETED',      -- 已完成
  'SCRAPPED',       -- 已報廢
  'CANCELLED',      -- 已取消
  'TRANSFERRED'     -- 已轉單
);

CREATE TYPE runcard_source AS ENUM ('NORMAL','REWORK','SPLIT','DEFECT');  -- 一般建立/重工/拆批/瑕疵單
CREATE TYPE data_source   AS ENUM ('SYSTEM','MES_SYNC');

CREATE TABLE run_cards (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  card_no           text UNIQUE NOT NULL,
  work_order_no     text,
  item_no           text,
  item_name         text,
  unit              text NOT NULL,
  route_free        boolean NOT NULL DEFAULT false,      -- 免路線 (E 單)
  route_version_id  int REFERENCES process_route_versions(id),
  current_node_id   int REFERENCES route_nodes(id),
  status            runcard_status NOT NULL DEFAULT 'WAITING',
  source_type       runcard_source NOT NULL DEFAULT 'NORMAL',
  data_source       data_source    NOT NULL DEFAULT 'SYSTEM',
  parent_card_id    uuid REFERENCES run_cards(id),       -- 重工來源卡 / 拆批來源 / 瑕疵來源
  original_qty      numeric NOT NULL CHECK (original_qty > 0),
  current_qty       numeric NOT NULL,
  scrap_qty         numeric NOT NULL DEFAULT 0,
  rework_qty        numeric NOT NULL DEFAULT 0,
  split_qty         numeric NOT NULL DEFAULT 0,
  defect_qty        numeric NOT NULL DEFAULT 0,
  cancel_reason     text,
  version           int NOT NULL DEFAULT 1,              -- optimistic lock
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now(),
  -- BR-01
  CONSTRAINT qty_identity CHECK (current_qty + scrap_qty + rework_qty + split_qty + defect_qty = original_qty),
  CONSTRAINT route_required CHECK (route_free OR route_version_id IS NOT NULL)
);
CREATE INDEX ON run_cards(status);
CREATE INDEX ON run_cards(work_order_no);

-- ===== Công việc =====
CREATE TYPE work_record_end AS ENUM ('NORMAL','ABNORMAL_KEEP','ABNORMAL_MARK','BACKFILL');

CREATE TABLE work_records (                  -- 工作紀錄: 1 lần 上工→下工
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  run_card_id     uuid NOT NULL REFERENCES run_cards(id),
  node_id         int REFERENCES route_nodes(id),
  operation_id    int NOT NULL REFERENCES operations(id),
  is_temporary    boolean NOT NULL DEFAULT false,      -- 臨時工序
  is_skip         boolean NOT NULL DEFAULT false,      -- 跳站
  authorized_by   uuid REFERENCES accounts(id),        -- người ủy quyền 跳站
  equipment_id    uuid REFERENCES equipments(id),
  operator_id     uuid NOT NULL REFERENCES accounts(id),   -- 工號 lúc 上工
  end_operator_id uuid REFERENCES accounts(id),            -- 工號 lúc 下工
  input_qty       numeric NOT NULL,
  output_qty      numeric,
  scrap_qty       numeric NOT NULL DEFAULT 0,
  scrap_reason    text,
  quality_result  text CHECK (quality_result IN ('PASS','FAIL')),
  production_note text,                                -- 生產註記
  started_at      timestamptz NOT NULL DEFAULT now(),
  ended_at        timestamptz,
  end_type        work_record_end,
  end_reason      text,                                -- lý do 異常結束 / 補登
  is_backfilled   boolean NOT NULL DEFAULT false,
  revoked_at      timestamptz,                         -- 回復處理 (soft delete)
  revoked_by      uuid REFERENCES accounts(id),
  revoke_reason   text
);
-- BR-05: tối đa 1 record mở / thẻ
CREATE UNIQUE INDEX one_open_record ON work_records(run_card_id) WHERE ended_at IS NULL AND revoked_at IS NULL;

CREATE TABLE work_record_defects (
  work_record_id uuid REFERENCES work_records(id) ON DELETE CASCADE,
  defect_code_id int  REFERENCES defect_codes(id),
  qty            numeric NOT NULL CHECK (qty > 0),
  remark         text,
  PRIMARY KEY (work_record_id, defect_code_id)
);

CREATE TABLE work_record_qc_values (
  work_record_id uuid REFERENCES work_records(id) ON DELETE CASCADE,
  qc_item_id     int  REFERENCES operation_qc_items(id),
  value          numeric NOT NULL,
  in_spec        boolean NOT NULL,
  PRIMARY KEY (work_record_id, qc_item_id)
);

-- ===== Lịch sử =====
CREATE TABLE equipment_status_history (      -- 設備狀態歷程
  id            bigserial PRIMARY KEY,
  equipment_id  uuid NOT NULL REFERENCES equipments(id),
  state         equipment_state NOT NULL,
  reason_code   text NOT NULL REFERENCES equipment_reasons(code),
  run_card_id   uuid REFERENCES run_cards(id),
  recorded_by   uuid REFERENCES accounts(id),
  note          text,
  started_at    timestamptz NOT NULL DEFAULT now(),
  ended_at      timestamptz
);
CREATE UNIQUE INDEX one_open_status ON equipment_status_history(equipment_id) WHERE ended_at IS NULL;

CREATE TABLE audit_logs (                    -- 稽核日誌
  id           bigserial PRIMARY KEY,
  at           timestamptz NOT NULL DEFAULT now(),
  action       text NOT NULL,                -- CHECK_IN, CHECK_OUT, SPLIT, REVOKE, RELEASE...
  actor_id     uuid REFERENCES accounts(id),
  run_card_id  uuid REFERENCES run_cards(id),
  from_status  runcard_status,
  to_status    runcard_status,
  reason       text,
  payload      jsonb
);

-- ===== Thông báo =====
CREATE TABLE notification_rules (
  event_code        text PRIMARY KEY,        -- EQUIPMENT_MAINTENANCE, RUNCARD_ABNORMAL...
  name              text NOT NULL,
  severity          text NOT NULL DEFAULT 'NORMAL',
  channel_email     boolean NOT NULL DEFAULT false,
  channel_inapp     boolean NOT NULL DEFAULT true,
  repeat_enabled    boolean NOT NULL DEFAULT false,
  repeat_every_min  int,
  repeat_max        int,
  escalate_enabled  boolean NOT NULL DEFAULT false,
  escalate_after_min int
);

CREATE TABLE notification_rule_targets (
  event_code  text REFERENCES notification_rules(event_code) ON DELETE CASCADE,
  role_id     int  REFERENCES roles(id),
  account_id  uuid REFERENCES accounts(id),
  is_escalation boolean NOT NULL DEFAULT false
);

CREATE TABLE notifications (
  id          bigserial PRIMARY KEY,
  event_code  text REFERENCES notification_rules(event_code),
  account_id  uuid REFERENCES accounts(id),
  title       text NOT NULL,
  body        text NOT NULL,
  run_card_id uuid REFERENCES run_cards(id),
  equipment_id uuid REFERENCES equipments(id),
  created_at  timestamptz NOT NULL DEFAULT now(),
  read_at     timestamptz
);

-- ===== Đồng bộ ngoài (OPT-10) =====
CREATE TABLE sync_outbox (
  id             bigserial PRIMARY KEY,
  idempotency_key text UNIQUE NOT NULL,
  event_type     text NOT NULL,              -- CHECK_IN / CHECK_OUT / SCRAP ...
  payload        jsonb NOT NULL,
  status         text NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','SENT','FAILED')),
  attempts       int NOT NULL DEFAULT 0,
  last_error     text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  sent_at        timestamptz
);
