-- Dữ liệu demo: dây chuyền lắp ráp PCBA 7 trạm

INSERT INTO equipment_reasons (code, state, main_category, sub_category, name, allowed_while_occupied) VALUES
 ('UE_1','ENABLED','稼動時間','工程實驗','工程實驗前調機',false),
 ('UE_2','ENABLED','稼動時間','工程實驗','工程實驗批(E工單)',false),
 ('UP','ENABLED','稼動時間','正常生產','正常生產',true),
 ('US_1','ENABLED','稼動時間','待機','設備點檢/調機',false),
 ('US_2','ENABLED','稼動時間','待機','首件(抓條件)',true),
 ('US_3','ENABLED','稼動時間','待機','製程中量測',false),
 ('US_4','ENABLED','稼動時間','待機','機台閒置',false),
 ('US_5','ENABLED','稼動時間','待機','人員用餐休息',true),
 ('DS_1','MAINTENANCE','停機','計畫內','定期設備保養維修',false),
 ('DS_2','MAINTENANCE','停機','計畫內','均勻性測試',false),
 ('DS_3','MAINTENANCE','停機','計畫內','設備保養後復機首件',false),
 ('DU_1','MAINTENANCE','停機','非計畫內','機台結構、硬體異常停機',false),
 ('DU_2','MAINTENANCE','停機','非計畫內','軟體異常停機',false),
 ('DU_3','MAINTENANCE','停機','非計畫內','品質異常停機',false),
 ('DU_4','MAINTENANCE','停機','非計畫內','廠務系統因素停機(水、氣、電、CDA等等)',false),
 ('DU_5','MAINTENANCE','停機','非計畫內','其他(地震、工安)',false),
 ('OFF','OFF','未開機','未開機','未開機',false);

INSERT INTO operations (code, name, requires_equipment) VALUES
 ('DEMO_PRT','DEMO 錫膏印刷',true),
 ('DEMO_SMT','DEMO 零件貼裝',true),
 ('DEMO_RFL','DEMO 回焊',true),
 ('DEMO_AOI','DEMO AOI檢測',true),
 ('DEMO_ASM','DEMO 成品組裝',true),
 ('DEMO_FCT','DEMO 功能測試',true),
 ('DEMO_PKG','DEMO 包裝出貨',false);

INSERT INTO defect_codes (code, name) VALUES
 ('DEMO-D01','錫橋'),('DEMO-D02','少錫'),('DEMO-D03','零件偏移'),
 ('DEMO-D04','缺件'),('DEMO-D05','功能不良'),('DEMO-D06','外觀刮傷');

INSERT INTO operation_defect_codes (operation_id, defect_code_id)
SELECT o.id, d.id FROM operations o JOIN defect_codes d ON
 (o.code='DEMO_PRT' AND d.code IN ('DEMO-D01','DEMO-D02')) OR
 (o.code='DEMO_SMT' AND d.code IN ('DEMO-D03','DEMO-D04')) OR
 (o.code='DEMO_RFL' AND d.code IN ('DEMO-D01','DEMO-D02')) OR
 (o.code='DEMO_AOI' AND d.code IN ('DEMO-D01','DEMO-D02','DEMO-D03','DEMO-D04')) OR
 (o.code='DEMO_ASM' AND d.code IN ('DEMO-D06')) OR
 (o.code='DEMO_FCT' AND d.code IN ('DEMO-D05')) OR
 (o.code='DEMO_PKG' AND d.code IN ('DEMO-D06'));

INSERT INTO equipments (mes_code, name, state, reason_code) VALUES
 ('DEMO-PRT01','DEMO 錫膏印刷機','ENABLED','US_4'),
 ('DEMO-SMT01','DEMO 高速貼片機','ENABLED','US_4'),
 ('DEMO-RFL01','DEMO 回焊爐','ENABLED','US_4'),
 ('DEMO-AOI01','DEMO AOI檢測機','ENABLED','US_4'),
 ('DEMO-ASM01','DEMO 組裝工作站','ENABLED','US_4'),
 ('DEMO-FCT01','DEMO 功能測試機','ENABLED','US_4');

INSERT INTO equipment_operations (equipment_id, operation_id)
SELECT e.id, o.id FROM equipments e JOIN operations o
  ON replace(e.mes_code,'DEMO-','') LIKE replace(o.code,'DEMO_','') || '%';

INSERT INTO process_routes (name) VALUES ('DEMO PCBA 組裝標準路線');
INSERT INTO process_route_versions (route_id, version, status)
SELECT id, 'v1.0', 'ACTIVE' FROM process_routes WHERE name='DEMO PCBA 組裝標準路線';
INSERT INTO route_nodes (route_version_id, seq, operation_id)
SELECT v.id, x.seq, o.id
FROM process_route_versions v
CROSS JOIN (VALUES (1,'DEMO_PRT'),(2,'DEMO_SMT'),(3,'DEMO_RFL'),(4,'DEMO_AOI'),
                   (5,'DEMO_ASM'),(6,'DEMO_FCT'),(7,'DEMO_PKG')) x(seq, code)
JOIN operations o ON o.code = x.code
WHERE v.version='v1.0';

INSERT INTO roles (code, name) VALUES
 ('admin','系統管理員'),('operator','作業員'),('qa','品保'),
 ('engineer_process','製程工程師'),('manufacturing_supervisor','製造主管'),
 ('pc_supervisor','生管主管'),('team_leader','組長');
