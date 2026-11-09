# RunCard MES — Functional Spec

Đặc tả chức năng của một hệ thống MES cho xưởng sản xuất dựa trên **RunCard** (phiếu theo lô / traveler card) và **máy tính bảng báo công**. Toàn bộ nội dung được ghi lại từ việc thao tác thực tế trên môi trường demo, dùng làm đầu vào để xây lại một web app tối ưu hơn.

> Dữ liệu mẫu chỉ dùng tuyến và thiết bị `DEMO-*`. Repo không chứa mã nguồn, dữ liệu khách hàng hay địa chỉ máy chủ nội bộ.

## Cấu trúc repo

| File | Nội dung |
|---|---|
| [docs/01-overview.md](docs/01-overview.md) | Kiến trúc, 2 giao diện (Admin / Operator), 13 menu, thuật ngữ |
| [docs/02-runcard-state-machine.md](docs/02-runcard-state-machine.md) | Trạng thái RunCard, chuyển trạng thái, đẳng thức số lượng |
| [docs/03-operator-flows.md](docs/03-operator-flows.md) | Luồng máy tính bảng: 上工, 下工, 異常結束, 臨時工序, 跳站 |
| [docs/04-admin-functions.md](docs/04-admin-functions.md) | Chức năng Admin: tạo thẻ, 拆批, 重工, 補登, 回復處理, 放行… |
| [docs/05-equipment-status.md](docs/05-equipment-status.md) | Trạng thái thiết bị, mã lý do, quy tắc tự động |
| [docs/06-roles-permissions.md](docs/06-roles-permissions.md) | 19 quyền, 13 vai trò, ma trận |
| [docs/07-notifications.md](docs/07-notifications.md) | 11 sự kiện thông báo, kênh, lặp lại, leo thang |
| [docs/08-business-rules.md](docs/08-business-rules.md) | Danh sách quy tắc nghiệp vụ đánh số `BR-xx` (đã kiểm chứng) |
| [docs/09-optimization-backlog.md](docs/09-optimization-backlog.md) | Điểm bất tiện / lỗ hổng và đề xuất cải tiến |
| [db/schema.sql](db/schema.sql) | Data model đề xuất (PostgreSQL), suy ra từ UI |
| [db/seed_demo.sql](db/seed_demo.sql) | Dữ liệu demo: tuyến PCBA 7 trạm, 6 máy, mã lỗi |
| [prompts/chatgpt-build.md](prompts/chatgpt-build.md) | Prompt mẫu để giao cho ChatGPT / AI coding agent |

## Cách dùng với ChatGPT

1. Gửi link repo (hoặc tải zip và đính kèm toàn bộ thư mục `docs/` + `db/`).
2. Dán prompt trong [prompts/chatgpt-build.md](prompts/chatgpt-build.md).
3. Yêu cầu AI tuân thủ các `BR-xx` trong `08-business-rules.md` và giải quyết các mục `OPT-xx` trong `09-optimization-backlog.md`.

## Thuật ngữ nhanh

| Từ | Nghĩa |
|---|---|
| RunCard | Phiếu đi theo một lô hàng qua các trạm |
| 工單 | Lệnh sản xuất (work order); 1 工單 có thể nhiều RunCard |
| 工序 | Công đoạn / operation |
| 工藝路線 | Tuyến công nghệ (routing), có phiên bản |
| 上工 / 下工 | Bắt đầu / kết thúc làm một công đoạn (check-in / check-out) |
| 報工 | Báo công (ghi nhận sản lượng) |
| 工號 | Mã nhân viên, dùng như chữ ký trên máy tính bảng |
| 瑕疵 / 瑕疵單 | Lỗi / phiếu lỗi (thẻ con hậu tố `-D1`) |
| 報廢 | Báo phế |
| 重工 | Làm lại (rework), sinh thẻ `R…` |
| 拆批 | Tách lô, sinh thẻ `-01` |
| 放行 | Cho qua sau khi giữ chất lượng |
| 補登 | Ghi bù từ admin |
| 回復處理 | Hoàn tác bản ghi công việc cuối |
| 異常結束 | Kết thúc bất thường |
| 跳站 | Nhảy trạm |
| 臨時工序 | Công đoạn tạm chèn ngoài tuyến |
