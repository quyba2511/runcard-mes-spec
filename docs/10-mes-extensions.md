# 10 · Chức năng MES mở rộng

Bổ sung 11 nhóm chức năng còn thiếu so với khung MESA-11 / ISA-95. Mã nguồn: `db/003_mes_extensions.sql`, `backend/app/mes.py`, `backend/app/gates.py`, `frontend/src/mes.tsx`. Kiểm thử: `backend/tests/test_mes.py` (11 kịch bản), cùng 75 test cũ vẫn qua.

Mọi thao tác ghi đều đi qua `run_command` như thao tác RunCard: khóa lệnh, `Idempotency-Key`, ghi `audit_logs` và `sync_outbox`.

| # | Chức năng | Màn hình | API chính |
|---|---|---|---|
| 1 | **Lệnh sản xuất (工單)**: tạo, phát lệnh tự chia RunCard theo `lot_size`, tiến độ đạt / đang làm / phế, đóng / hủy | Lệnh sản xuất | `GET/POST /api/work-orders`, `POST /{id}/release`, `/{id}/close` |
| 2 | **Điều độ**: lập lịch tự động theo ưu tiên → hạn giao → tuổi thẻ, chọn máy rảnh sớm nhất, dùng `std_cycle_sec`; Gantt; hàng chờ từng máy; cảnh báo trễ hạn | Điều độ, trang Thiết bị | `POST /api/schedule/auto`, `GET /api/schedule`, `GET /api/dispatch/{equipment_id}` |
| 3 | **Vật tư, BOM, lô, tồn kho**: BOM theo phiên bản và công đoạn; nhập lô, điều chỉnh / giữ / phế lô; công nhân quét lô để cấp vật tư khi thẻ đang chạy (kiểm BOM, hạn dùng, tồn) | Vật tư & BOM, trang RunCard trên tablet | `/api/materials`, `/api/boms`, `/api/lots`, `/api/lots/receive`, `/api/lots/{lot}/adjust`, `POST /api/cards/{id}/issue-material` |
| 4 | **Truy xuất**: truy ngược thẻ / serial lên thẻ gốc rồi cả cây con, kèm công đoạn, máy, 工號, lô vật tư, NCR; truy xuôi lô vật tư → các thẻ đã dùng và thẻ con; tạo serial | Truy xuất | `GET /api/trace/card/{card_no|serial}`, `GET /api/trace/lot/{lot_no}`, `POST /api/cards/{id}/serials` |
| 5 | **Chất lượng**: SPC (X̄, σ, UCL/LCL ±3σ, Cp, Cpk, điểm ngoài kiểm soát); Pareto lỗi; NCR (mở → điều tra → đóng, bắt buộc nguyên nhân + khắc phục); kiểm đầu chuyền 首件 (người kiểm ≠ người duyệt) | Chất lượng, tablet | `/api/quality/spc`, `/api/quality/pareto`, `/api/ncrs`, `/api/first-articles` |
| 6 | **Báo cáo & OEE**: OEE = A × P × Q theo máy; thời gian dừng theo mã lý do; sản lượng theo công đoạn / máy / người / ca / ngày; MTTR, MTBF; máy đếm so với báo công; xuất Excel | Báo cáo & OEE | `GET /api/reports/{oee|downtime|production|maintenance|machine-vs-reported}[.xlsx]` |
| 7 | **Bảo trì**: báo hỏng DU_x tự mở phiếu sửa; bắt đầu → hoàn tất (kết quả, linh kiện) → máy về US_1; kế hoạch PM, worker tự mở phiếu PM khi đến hạn; dụng cụ / khuôn (lắp, tháo, tuổi thọ tự cộng theo sản lượng) | Bảo trì | `/api/maintenance-orders`, `/api/pm-plans`, `/api/tools` |
| 8 | **Ca & tay nghề**: ca làm việc (kể cả ca qua đêm, múi giờ `FACTORY_TZ`, mặc định Asia/Taipei); chứng chỉ tay nghề theo công đoạn, có hạn | Ca & tay nghề | `/api/shifts`, `/api/skills` |
| 9 | **Tài liệu SOP**: tải lên (≤ 10 MB), bản nháp → ban hành (bản cũ tự hết hiệu lực); tablet tự hiện SOP của công đoạn / sản phẩm | Tài liệu SOP, tablet | `/api/documents`, `/{id}/release`, `/{id}/file` |
| 10 | **Kết nối máy**: token riêng cho từng máy; PLC / gateway gửi trạng thái, bộ đếm, thông số; cạnh RUN→DOWN bắn 設備異常 cho người xác nhận | Tích hợp, trang Thiết bị | `POST /api/machine/telemetry` (header `X-Device-Token`) |
| 11 | **ERP hai chiều**: ERP đẩy 工單, BOM, lô vật tư vào (chống trùng theo `external_id`, lỗi được ghi lại); gửi ra theo định dạng `generic` hoặc `wip` (CHECKIN / CHECKOUT, `ExternalTransactionID` = idempotency key); màn đối soát | Tích hợp | `/api/erp/work-orders`, `/api/erp/boms`, `/api/erp/material-lots`, `GET /api/erp/reconcile` |
| + | **Nhãn ZPL** cho RunCard và lô vật tư | RunCard, Vật tư | `/api/labels/card/{id}`, `/api/labels/lot/{lot}` |
| + | **Token tablet riêng**, thu hồi từng máy (thay cho một `DEVICE_TOKEN` chung) | Tích hợp | `/api/devices` |

## Kiểm soát sản xuất (bật / tắt ở Tích hợp → Kiểm soát sản xuất)

Tất cả mặc định **tắt**, nên quy tắc BR-01…BR-24 không đổi cho đến khi nhà máy chủ động bật.

| Cài đặt | Khi bật | Mã lỗi |
|---|---|---|
| `skill_enforced` | 上工 công đoạn có `requires_skill` cần chứng chỉ còn hạn | `SKILL` |
| `fai_enforced` | 上工 công đoạn có `requires_first_article` cần 首件 PASS được duyệt trong ca hiện tại, trên đúng máy | `FIRST_ARTICLE` |
| `pm_block_overdue` | Máy quá hạn PM không 上工 được | `PM_OVERDUE` |
| `material_enforced` | 下工 cần cấp đủ vật tư theo BOM của công đoạn (định mức × đầu vào) | `MATERIAL` |
| (luôn bật) | Dụng cụ đang lắp đã hết tuổi thọ thì chặn 上工 | `TOOL_LIFE` |

## Gửi dữ liệu từ PLC (ví dụ)

```bash
curl -X POST https://mes.local/api/machine/telemetry \
  -H "X-Device-Token: <token máy>" -H "Content-Type: application/json" \
  -d '{"state":"RUN","counter":15230,"good":15190,"scrap":40,"params":{"temp":245.3}}'
```

Gateway MQTT hoặc OPC-UA chỉ cần chuyển tiếp bản tin sang endpoint này.

## Chưa làm, cần quyết định thêm

- **Loại số lượng 除膜回收退庫 riêng:** cần khách xác nhận, vì sẽ đổi đẳng thức BR-01. Hiện dùng "Trả kho".
- **Hàng đợi báo công khi mất mạng (OPT-13):** chưa làm. PWA mới chỉ lưu sẵn khung giao diện.
- **Phân quyền theo xưởng / chuyền (OPT-14).**
- **Khóa lệnh dùng chung toàn hệ thống:** vẫn giữ để đảm bảo đúng. Nên đo tải thật trước khi đổi sang khóa theo từng thẻ, từng máy.
- **Adapter MQTT / OPC-UA chạy sẵn trong hệ thống:** chưa có, hiện nhận qua HTTP.
- **`docs/implementation/openapi.json`:** giờ được sinh ra khi chạy `scripts/export_openapi.py`. Server đang chạy luôn có `/openapi.json` và `/docs`.
