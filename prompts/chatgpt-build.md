# Prompt mẫu cho ChatGPT / AI coding agent

Copy nguyên khối dưới đây, đính kèm link repo hoặc các file `docs/*.md` + `db/*.sql`.

---

Bạn là senior full-stack engineer chuyên MES. Hãy đọc toàn bộ repo `runcard-mes-spec`:

- `docs/01` → `docs/07`: đặc tả chức năng hiện tại
- `docs/08-business-rules.md`: quy tắc BR-01…BR-24 **bắt buộc giữ**
- `docs/09-optimization-backlog.md`: các vấn đề OPT-01…OPT-14 cần cải tiến
- `db/schema.sql`, `db/seed_demo.sql`: data model và dữ liệu demo

**Mục tiêu:** xây một web app MES dựa trên RunCard, giữ toàn bộ nghiệp vụ hiện tại nhưng tối ưu thao tác cho công nhân và quản lý.

**Stack đề xuất** (có thể đổi nếu giải thích lý do):
- Backend: Node.js (NestJS) hoặc Python (FastAPI) + PostgreSQL; mọi thao tác thay đổi trạng thái chạy trong 1 transaction, khóa lạc quan bằng `run_cards.version`.
- Frontend: React + Vite + TypeScript. Hai app: `admin` (desktop) và `operator` (PWA tối ưu tablet, nút lớn, hỗ trợ máy quét bàn phím và camera).
- Realtime: WebSocket/SSE cho dashboard và thông báo.

**Yêu cầu:**
1. Viết state machine RunCard thành một module thuần (pure function `transition(card, action, ctx) → {card, effects}`), có unit test cho từng BR.
2. Mọi action ghi `audit_logs` và đẩy sự kiện vào `sync_outbox`.
3. Operator app: một ô quét duy nhất; nhận diện RunCard / mã máy / 工號 theo prefix hoặc tra cứu.
4. Giải quyết trước các mục P1: OPT-01, OPT-02, OPT-03, OPT-04.
5. Dashboard WIP theo trạm và trạng thái máy (OPT-08).
6. Giao diện song ngữ 繁體中文 / Tiếng Việt (i18n).

**Đầu ra mong muốn, theo thứ tự:**
1. Sơ đồ kiến trúc + cấu trúc thư mục.
2. Danh sách API (OpenAPI) cho admin và operator.
3. Code module state machine + test.
4. Code backend cho các action: check-in, check-out, abnormal-end, split, rework, backfill, revoke, release, cancel, set-equipment-status.
5. Code operator PWA (các màn: scan, card, equipment, check-out wizard, report-breakdown wizard).
6. Code admin (RunCard list + actions, equipment, routes, work records, audit, notifications).
7. Docker Compose chạy được với `seed_demo.sql`.

Làm từng bước, hỏi lại nếu một quy tắc trong spec mâu thuẫn.
