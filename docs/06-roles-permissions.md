# 06 · Vai trò và quyền

## 19 quyền

| Mã | Ý nghĩa |
|---|---|
| `backend:access` | Vào Admin |
| `audit:view_system` | Xem 稽核日誌 hệ thống |
| `accounts:manage` | Quản lý tài khoản |
| `operations:manage` / `operations:update` | Quản lý / sửa 工序 |
| `defect_codes:manage` | Quản lý mã lỗi |
| `equipments:manage` | Quản lý thiết bị |
| `process_routes:manage` | Quản lý tuyến |
| `run_cards:create` | Tạo RunCard |
| `run_cards:manage` | Quản lý RunCard |
| `notifications:manage` | Cấu hình thông báo |
| `mes_sync:manage` | Đồng bộ MES |
| `equipment_status:set` | 設定／解除設備狀態 |
| `quality:judge` | 品質判定／複判 (放行, 報廢) |
| `rework:create` | Tạo 重工 |
| `run_cards:revoke_work` | 回復處理 (soft delete 工作紀錄 và lùi trạm) |
| `work_records:abnormal_end` | 異常結束 (operator) |
| `work_records:backfill` | 補登 上下工 / 異常結束 (admin) |
| `work_records:skip_node` | 跳站 (ủy quyền tạm trên tablet) |

## Ma trận vai trò (quyền nghiệp vụ)

| Vai trò | backend | equip_status | judge | rework | abnormal_end | backfill | Khác |
|---|---|---|---|---|---|---|---|
| admin | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | Toàn quyền |
| operator | | ✓ | | | ✓ | | |
| operator_measurement | ✓ | ✓ | | ✓ | | | |
| qa | ✓ | | ✓ | ✓ | | | |
| engineer_equipment | ✓ | ✓ | | | ✓ | | |
| engineer_process | ✓ | ✓ | ✓ | | ✓ | ✓ | |
| engineer_measurement | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| engineering_supervisor | ✓ | ✓ | ✓ | | ✓ | ✓ | process_routes:manage |
| manufacturing_supervisor | ✓ | ✓ | ✓ | | ✓ | ✓ | |
| manufacturing_supervisor_rework | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| pc_supervisor | ✓ | ✓ | ✓ | | ✓ | ✓ | run_cards:create |
| qa_supervisor | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |
| team_leader | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | |

`skip_node` và `revoke_work` hiện chỉ admin có.
