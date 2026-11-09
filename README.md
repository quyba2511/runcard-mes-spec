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

---

## Chạy ứng dụng MES

Repository có bản triển khai FastAPI + PostgreSQL và React/Vite/TypeScript theo `prompts/chatgpt-build.md`.

```bash
python scripts/setup_env.py
docker compose up --build -d
```

Mở `.env` trên máy của bạn để lấy mật khẩu demo và mã thiết lập thiết bị. Script tạo bí mật ngẫu nhiên và không ghi đè file sẵn có.

| Mục | Địa chỉ / thông tin |
|---|---|
| Admin | http://localhost:8080/admin |
| Operator PWA | http://localhost:8080/operator |
| API Swagger | http://localhost:8080/docs |
| Admin / QA | Tài khoản `admin` / `qa`, mật khẩu `DEMO_ADMIN_PASSWORD` trong `.env` |
| Công nhân | 工號 `OP001`, mã thiết lập `DEVICE_TOKEN` trong `.env` |
| Thẻ demo | `DEMO-RC-0001` (100), `DEMO-RC-0002` (50), `DEMO-RC-0003` (200) |
| Máy in kem hàn | `DEMO-PRT01` |
| QC mẫu trạm đầu | Độ dày kem hàn 100–150 μm |

Compose khởi tạo `schema.sql` → `seed_demo.sql` → `002_runtime.sql` trên volume mới. Với database đã có schema, áp dụng migration **một lần**, sao lưu trước và không chạy lại các script khởi tạo. Lệnh `docker compose down` giữ dữ liệu; không dùng `-v` nếu muốn giữ demo.

Để sử dụng camera/PWA trên tablet qua LAN, cần HTTPS. Backend không mở cổng trực tiếp ra ngoài; frontend proxy cùng origin tới API. Không có endpoint ERP/SMTP mặc định, nên không gửi dữ liệu ra hệ thống thật.

### Kiểm thử và phát triển

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements-dev.txt
pytest backend/tests/test_domain.py -q
# Chỉ dùng database kiểm thử riêng: integration tests xóa schema public.
TEST_DATABASE_URL=postgresql://mes:password@localhost:5432/mes_test pytest backend/tests -q
npm --prefix frontend ci
npm --prefix frontend run build
python scripts/export_openapi.py
```

Backend local: thiết lập `DATABASE_URL`, `DEVICE_TOKEN`, và nếu cần dữ liệu tài khoản demo thì thêm `SEED_DEMO_ACCOUNTS=true`, `DEMO_ADMIN_PASSWORD`; chạy `uvicorn app.main:app --app-dir backend --reload`. Frontend local: `npm --prefix frontend run dev`. Worker: `PYTHONPATH=backend python -m app.worker`.

### Tài liệu triển khai

1. [Kiến trúc, cấu trúc thư mục và các quyết định đã chốt](docs/implementation/01-architecture.md)
2. [Danh sách API và hợp đồng tích hợp](docs/implementation/02-api.md) · [OpenAPI JSON](docs/implementation/openapi.json)
3. [State machine](backend/app/domain.py) · [Bộ kiểm thử](backend/tests/)
4. [Backend transaction và actions](backend/app/service.py)
5. [Operator PWA và Admin](frontend/src/main.tsx)
6. [Cách kiểm chứng, giới hạn và kịch bản nghiệm thu](docs/implementation/03-validation.md)
7. [Docker Compose](compose.yaml)

**Điểm đã chốt với chủ repo:** BR-01 mở rộng thêm số lượng trả kho/chuyển thẻ. 放行 có hai chế độ lý do, mặc định bắt buộc theo BR-24. Chi tiết và các mục backlog chưa triển khai được ghi trong tài liệu trên.
