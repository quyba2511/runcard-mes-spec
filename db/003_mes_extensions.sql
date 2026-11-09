-- 003 · Chức năng MES mở rộng (additive). Chạy sau schema.sql, seed_demo.sql, 002_runtime.sql.

-- ===== Công đoạn: chu kỳ chuẩn, kiểm đầu chuyền, tay nghề =====
ALTER TABLE operations ADD COLUMN std_cycle_sec numeric CHECK (std_cycle_sec IS NULL OR std_cycle_sec > 0);
ALTER TABLE operations ADD COLUMN requires_first_article boolean NOT NULL DEFAULT false;
ALTER TABLE operations ADD COLUMN requires_skill boolean NOT NULL DEFAULT false;

-- ===== Ca làm việc =====
CREATE TABLE shifts (
  code text PRIMARY KEY, name text NOT NULL,
  start_time time NOT NULL, end_time time NOT NULL,       -- end < start nghĩa là qua đêm
  is_active boolean NOT NULL DEFAULT true
);

-- ===== Lệnh sản xuất (工單) =====
CREATE TABLE work_orders (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  wo_no text UNIQUE NOT NULL, item_no text, item_name text,
  planned_qty numeric NOT NULL CHECK (planned_qty > 0),
  unit text NOT NULL DEFAULT 'pcs',
  lot_size numeric CHECK (lot_size IS NULL OR lot_size > 0),
  route_version_id int REFERENCES process_route_versions(id),
  due_date date, priority int NOT NULL DEFAULT 5 CHECK (priority BETWEEN 1 AND 9),
  status text NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED','RELEASED','IN_PROGRESS','COMPLETED','CLOSED','CANCELLED')),
  source text NOT NULL DEFAULT 'SYSTEM' CHECK (source IN ('SYSTEM','ERP')), erp_ref text,
  created_at timestamptz NOT NULL DEFAULT now(), released_at timestamptz, closed_at timestamptz
);
ALTER TABLE run_cards ADD COLUMN work_order_id uuid REFERENCES work_orders(id);
CREATE INDEX ON run_cards(work_order_id);

-- ===== Điều độ =====
CREATE TABLE schedule_slots (
  id bigserial PRIMARY KEY,
  equipment_id uuid NOT NULL REFERENCES equipments(id),
  run_card_id uuid NOT NULL REFERENCES run_cards(id),
  operation_id int NOT NULL REFERENCES operations(id),
  planned_start timestamptz NOT NULL, planned_end timestamptz NOT NULL CHECK (planned_end > planned_start),
  status text NOT NULL DEFAULT 'PLANNED' CHECK (status IN ('PLANNED','DONE','CANCELLED')),
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX one_plan_per_card_op ON schedule_slots(run_card_id, operation_id) WHERE status = 'PLANNED';

-- ===== Vật tư, BOM, lô vật tư, tồn kho =====
CREATE TABLE materials (
  id serial PRIMARY KEY, code text UNIQUE NOT NULL, name text NOT NULL,
  unit text NOT NULL DEFAULT 'pcs', is_active boolean NOT NULL DEFAULT true
);
CREATE TABLE boms (
  id serial PRIMARY KEY, item_no text NOT NULL, version text NOT NULL,
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('DRAFT','ACTIVE','RETIRED')),
  created_at timestamptz NOT NULL DEFAULT now(), UNIQUE (item_no, version)
);
CREATE UNIQUE INDEX one_active_bom ON boms(item_no) WHERE status = 'ACTIVE';
CREATE TABLE bom_lines (
  id serial PRIMARY KEY, bom_id int NOT NULL REFERENCES boms(id) ON DELETE CASCADE,
  material_id int NOT NULL REFERENCES materials(id),
  operation_id int REFERENCES operations(id),             -- tiêu hao tại công đoạn nào
  qty_per numeric NOT NULL CHECK (qty_per > 0),
  UNIQUE (bom_id, material_id, operation_id)
);
CREATE TABLE material_lots (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  lot_no text UNIQUE NOT NULL, material_id int NOT NULL REFERENCES materials(id),
  supplier text, received_qty numeric NOT NULL CHECK (received_qty > 0),
  remaining_qty numeric NOT NULL CHECK (remaining_qty >= 0),
  status text NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE','HOLD','CONSUMED','EXPIRED')),
  expires_at date, received_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE inventory_transactions (
  id bigserial PRIMARY KEY, lot_id uuid NOT NULL REFERENCES material_lots(id),
  txn_type text NOT NULL CHECK (txn_type IN ('RECEIVE','ISSUE','RETURN','ADJUST','SCRAP')),
  qty numeric NOT NULL, run_card_id uuid REFERENCES run_cards(id), work_record_id uuid REFERENCES work_records(id),
  actor_id uuid REFERENCES accounts(id), reason text, at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE material_consumptions (
  id bigserial PRIMARY KEY, run_card_id uuid NOT NULL REFERENCES run_cards(id),
  work_record_id uuid REFERENCES work_records(id), lot_id uuid NOT NULL REFERENCES material_lots(id),
  material_id int NOT NULL REFERENCES materials(id), operation_id int REFERENCES operations(id),
  qty numeric NOT NULL CHECK (qty > 0), actor_id uuid REFERENCES accounts(id), at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON material_consumptions(run_card_id);
CREATE INDEX ON material_consumptions(lot_id);

-- ===== Serial =====
CREATE TABLE serial_numbers (
  serial text PRIMARY KEY, run_card_id uuid NOT NULL REFERENCES run_cards(id),
  status text NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE','SCRAPPED','SHIPPED')),
  created_at timestamptz NOT NULL DEFAULT now()
);

-- ===== Chất lượng =====
CREATE TABLE ncrs (
  id bigserial PRIMARY KEY, ncr_no text UNIQUE NOT NULL,
  run_card_id uuid REFERENCES run_cards(id), defect_code_id int REFERENCES defect_codes(id),
  qty numeric, description text NOT NULL, root_cause text, corrective_action text, preventive_action text,
  owner_id uuid REFERENCES accounts(id),
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','INVESTIGATING','CLOSED')),
  opened_by uuid REFERENCES accounts(id), opened_at timestamptz NOT NULL DEFAULT now(), closed_at timestamptz
);
CREATE TABLE first_article_checks (
  id bigserial PRIMARY KEY, equipment_id uuid NOT NULL REFERENCES equipments(id),
  operation_id int NOT NULL REFERENCES operations(id), run_card_id uuid REFERENCES run_cards(id),
  result text NOT NULL CHECK (result IN ('PASS','FAIL')), note text,
  checked_by uuid NOT NULL REFERENCES accounts(id), approved_by uuid REFERENCES accounts(id),
  approved_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);

-- ===== Bảo trì, dụng cụ =====
CREATE TABLE pm_plans (
  id serial PRIMARY KEY, equipment_id uuid NOT NULL REFERENCES equipments(id),
  name text NOT NULL, interval_days int NOT NULL CHECK (interval_days > 0),
  last_done_at timestamptz, next_due_at timestamptz NOT NULL, is_active boolean NOT NULL DEFAULT true
);
CREATE TABLE maintenance_orders (
  id bigserial PRIMARY KEY, mo_no text UNIQUE NOT NULL,
  equipment_id uuid NOT NULL REFERENCES equipments(id),
  kind text NOT NULL CHECK (kind IN ('BREAKDOWN','PM')), reason_code text REFERENCES equipment_reasons(code),
  pm_plan_id int REFERENCES pm_plans(id), description text,
  status text NOT NULL DEFAULT 'OPEN' CHECK (status IN ('OPEN','IN_PROGRESS','DONE')),
  reported_by uuid REFERENCES accounts(id), assigned_to uuid REFERENCES accounts(id),
  reported_at timestamptz NOT NULL DEFAULT now(), started_at timestamptz, finished_at timestamptz,
  findings text, parts_used text
);
CREATE UNIQUE INDEX one_open_breakdown ON maintenance_orders(equipment_id) WHERE kind = 'BREAKDOWN' AND status <> 'DONE';
CREATE TABLE tools (
  id serial PRIMARY KEY, code text UNIQUE NOT NULL, name text NOT NULL, kind text NOT NULL DEFAULT 'TOOL',
  life_limit numeric CHECK (life_limit IS NULL OR life_limit > 0), used numeric NOT NULL DEFAULT 0,
  equipment_id uuid REFERENCES equipments(id),
  status text NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE','MOUNTED','REPAIR','RETIRED'))
);

-- ===== Tay nghề =====
CREATE TABLE operator_skills (
  account_id uuid NOT NULL REFERENCES accounts(id) ON DELETE CASCADE,
  operation_id int NOT NULL REFERENCES operations(id) ON DELETE CASCADE,
  level int NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 3),
  certified_at date NOT NULL DEFAULT current_date, expires_at date,
  PRIMARY KEY (account_id, operation_id)
);

-- ===== Tài liệu / SOP =====
CREATE TABLE documents (
  id serial PRIMARY KEY, doc_no text NOT NULL, version text NOT NULL, title text NOT NULL,
  operation_id int REFERENCES operations(id), item_no text,
  status text NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','RELEASED','OBSOLETE')),
  file_name text NOT NULL, content_type text NOT NULL, content bytea NOT NULL,
  uploaded_by uuid REFERENCES accounts(id), created_at timestamptz NOT NULL DEFAULT now(), released_at timestamptz,
  UNIQUE (doc_no, version)
);
CREATE UNIQUE INDEX one_released_doc ON documents(doc_no) WHERE status = 'RELEASED';

-- ===== Thiết bị kết nối: tablet, máy, ERP =====
CREATE TABLE devices (
  id serial PRIMARY KEY, name text NOT NULL,
  kind text NOT NULL CHECK (kind IN ('TABLET','MACHINE','ERP')),
  token_hash text UNIQUE NOT NULL, equipment_id uuid REFERENCES equipments(id),
  is_active boolean NOT NULL DEFAULT true, created_at timestamptz NOT NULL DEFAULT now(), last_seen_at timestamptz,
  CHECK (kind <> 'MACHINE' OR equipment_id IS NOT NULL)
);
CREATE TABLE machine_events (
  id bigserial PRIMARY KEY, equipment_id uuid NOT NULL REFERENCES equipments(id),
  at timestamptz NOT NULL DEFAULT now(), state text CHECK (state IN ('RUN','IDLE','DOWN','OFF')),
  counter numeric, good numeric, scrap numeric, params jsonb NOT NULL DEFAULT '{}'
);
CREATE INDEX ON machine_events(equipment_id, at DESC);
CREATE TABLE erp_inbound_log (
  id bigserial PRIMARY KEY, kind text NOT NULL, external_id text NOT NULL, payload jsonb NOT NULL,
  status text NOT NULL CHECK (status IN ('APPLIED','REJECTED')), error text,
  at timestamptz NOT NULL DEFAULT now(), UNIQUE (kind, external_id)
);

-- ===== Cấu hình =====
INSERT INTO app_settings VALUES
 ('fai_enforced','false'), ('skill_enforced','false'), ('pm_block_overdue','false'),
 ('material_enforced','false'), ('erp_format','"generic"');

INSERT INTO shifts VALUES ('A','Ca ngày / 日班','08:00','20:00',true), ('B','Ca đêm / 夜班','20:00','08:00',true);

INSERT INTO notification_rules(event_code,name,severity,channel_email,repeat_enabled,repeat_every_min,repeat_max,escalate_enabled,escalate_after_min)
VALUES ('PM_DUE','保養到期','NORMAL',false,false,30,5,false,30),
       ('NCR_OPENED','不合格品報告','NORMAL',false,false,30,5,false,30),
       ('TOOL_LIFE','治具壽命到期','NORMAL',false,false,30,5,false,30)
ON CONFLICT DO NOTHING;
-- Database đã chạy bootstrap trước đó: thêm người nhận cho 3 sự kiện mới (lần đầu bootstrap sẽ tự thêm).
INSERT INTO notification_rule_targets(event_code,role_id,is_escalation)
SELECT e, r.id, false FROM unnest(ARRAY['PM_DUE','NCR_OPENED','TOOL_LIFE']) e JOIN roles r ON r.code IN ('admin','qa')
WHERE EXISTS (SELECT 1 FROM app_settings WHERE key='bootstrapped');

-- ===== Dữ liệu demo (chỉ khi có tuyến DEMO) =====
UPDATE operations SET std_cycle_sec = CASE code WHEN 'DEMO_PRT' THEN 20 WHEN 'DEMO_SMT' THEN 30 WHEN 'DEMO_RFL' THEN 15
  WHEN 'DEMO_AOI' THEN 10 WHEN 'DEMO_ASM' THEN 45 WHEN 'DEMO_FCT' THEN 60 WHEN 'DEMO_PKG' THEN 5 END
 WHERE code LIKE 'DEMO_%';
INSERT INTO materials(code,name,unit)
SELECT * FROM (VALUES ('DEMO-PCB','PCB trần 裸板','pcs'),('DEMO-SOLDER','Kem hàn 錫膏','g'),
                      ('DEMO-IC','IC điều khiển 控制IC','pcs'),('DEMO-BOX','Hộp đóng gói 包裝盒','pcs')) v
WHERE EXISTS (SELECT 1 FROM operations WHERE code='DEMO_PRT');
INSERT INTO boms(item_no,version,status) SELECT 'PCBA-001','A','ACTIVE' WHERE EXISTS (SELECT 1 FROM materials WHERE code='DEMO-PCB');
INSERT INTO bom_lines(bom_id,material_id,operation_id,qty_per)
SELECT b.id, m.id, o.id, x.q FROM boms b
JOIN (VALUES ('DEMO-PCB','DEMO_PRT',1.0),('DEMO-SOLDER','DEMO_PRT',0.5),('DEMO-IC','DEMO_SMT',2.0),('DEMO-BOX','DEMO_PKG',1.0)) x(m,o,q) ON true
JOIN materials m ON m.code=x.m JOIN operations o ON o.code=x.o WHERE b.item_no='PCBA-001';
INSERT INTO material_lots(lot_no,material_id,supplier,received_qty,remaining_qty,expires_at)
SELECT x.l, m.id, x.s, x.q, x.q, current_date + 180 FROM
 (VALUES ('LOT-PCB-001','DEMO-PCB','Supplier A',500),('LOT-SOL-001','DEMO-SOLDER','Supplier B',1000),
         ('LOT-IC-001','DEMO-IC','Supplier C',2000),('LOT-BOX-001','DEMO-BOX','Supplier D',500)) x(l,m,s,q)
JOIN materials m ON m.code=x.m;
INSERT INTO inventory_transactions(lot_id,txn_type,qty,reason) SELECT id,'RECEIVE',received_qty,'demo' FROM material_lots;
INSERT INTO pm_plans(equipment_id,name,interval_days,next_due_at)
SELECT id,'Bảo dưỡng định kỳ 定期保養',30,now()+interval '30 days' FROM equipments WHERE mes_code LIKE 'DEMO-%';
INSERT INTO tools(code,name,kind,life_limit,equipment_id,status)
SELECT 'DEMO-STENCIL-01','Lưới in kem hàn 鋼板','STENCIL',20000,id,'MOUNTED' FROM equipments WHERE mes_code='DEMO-PRT01';
