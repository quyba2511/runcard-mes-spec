# 04 · Chức năng Admin trên RunCard

## 新增 RunCard

| Trường | Bắt buộc | Ghi chú |
|---|---|---|
| RunCard 編號 | ✓ | Duy nhất |
| 工單編號 | | |
| 品號 / 品名 | | |
| 免路線 | | Checkbox; E 單; không đổi được sau khi tạo |
| 工藝路線 | ✓ (trừ 免路線) | |
| 路線版本 | ✓ | Tự chọn version 啟用中 |
| 原始數量 | ✓ | |
| 單位 | ✓ | pcs, 片… |

## 批量匯入
Tải `.xlsx` theo template (`下載範本`) → `上傳並驗證`. Tất cả thẻ import ở 待上工.

## 拆批 (tách lô)
- Input: 拆批數量 (✓), 備註.
- Không cần lý do ("拆批純屬產能調度").
- Thẻ gốc phải giữ lại > 0.
- Sinh `{gốc}-01`, nhãn 拆批, cùng trạm, 待上工. Gốc tăng `累計拆批`.
- Gửi thông báo "拆批".

## 重工 (rework)
- Sinh thẻ `R{YYMM}{seq}`, lưu `重工來源卡`.
- Chọn công đoạn bắt đầu lại.
- Gốc tăng `累計重工`.

## 臨時工序
工序 (✓) + 臨時工序原因 (✓). "完成後會回到原本規劃的工藝路線繼續，不影響 RunCard 目前所在節點".

## 補登 (ghi bù)
Dialog hiển thị: RunCard, 狀態, 工藝路線 (version), 目前工序, danh sách trạm (mở rộng xem 工作紀錄 từng trạm).

| Thẻ đang | Form | Trường |
|---|---|---|
| 待上工 | 補登上工 | 設備代碼 ✓, 單位 ✓, 實際作業員 ✓ (chọn tài khoản), 上工時間 (trống = now), 補登原因 ✓ |
| 進行中 | 補登下工 | 產出數量 ✓, 單位 ✓, 瑕疵項目 (thêm nhiều dòng), 原因, 下工時間 |

## 回復處理 (undo)
- Mỗi lần chỉ hủy **1 工作紀錄 cuối cùng** (soft delete). Nếu record đang mở (進行中) → hủy lần 上工 đó.
- Thẻ quay về số lượng + trạm trước khi 上工 của record đó.
- Lùi nhiều trạm = làm nhiều lần.
- 撤銷原因 ✓.
- Phiếu lỗi mở từ record đó mà QA chưa xử lý → 作廢 theo.
- Quyền: `run_cards:revoke_work`.

## 放行 (release)
- Áp dụng cho 品質保留 / 異常.
- "放行為最終動作，將釋放當下全部剩餘數量；如需部分報廢，請先使用報廢功能分批處理".
- 判定原因 tùy chọn.
- Kết quả: 待上工 tại đúng trạm.

## 報廢
Báo phế một phần hoặc toàn bộ; tăng `累計報廢`; hết số lượng → 已報廢. Thông báo "品質報廢".

## 編輯
Sửa thông tin thẻ khi 待上工.

## 取消卡片
- Chỉ khi 待上工. 進行中 / 已完成 không hủy được.
- 取消原因 ✓.
- Dữ liệu + 工作紀錄 được giữ.
- ⚠ Số lượng còn lại không được ghi là phế hay thành phẩm (xem OPT-04).

## 列印
In RunCard (barcode) để dán theo lô.
