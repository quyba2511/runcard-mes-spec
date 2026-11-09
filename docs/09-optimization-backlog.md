# 09 · Điểm cần tối ưu

Mỗi mục: vấn đề quan sát được → đề xuất. Mức ưu tiên: P1 (cao) → P3.

| ID | P | Vấn đề | Đề xuất |
|---|---|---|---|
| OPT-01 | P1 | Máy hỏng khi đang chạy thẻ: operator không thấy 維修中, phải biết làm 異常結束 trước rồi quét máy lại (2 luồng, nhiều bước). | Nút **"Báo hỏng máy"** trên màn hình máy đang chạy: một wizard gộp `異常結束·保留現節點` + `維修中/DU_x` trong 1 giao dịch. |
| OPT-02 | P1 | Wizard 下工 bắt cuộn hết trang mới bật nút xác nhận; chậm trên tablet. | Màn tóm tắt gọn 1 màn hình; nút luôn hiện; highlight các giá trị bất thường. |
| OPT-03 | P1 | Hỏi 工號 lại ở mỗi thao tác. | Phiên 工號 ngắn hạn (vd. 5 phút hoặc đến khi quét người khác) có tùy chọn bật/tắt; hoặc quét thẻ nhân viên NFC. |
| OPT-04 | P1 | 取消卡片 khi còn số lượng: phần còn lại không thành 報廢 hay thành phẩm → lệch số khi đồng bộ ERP/MES. | Khi hủy bắt buộc chọn: báo phế toàn bộ / trả kho / chuyển thẻ khác; giữ BR-01. |
| OPT-05 | P2 | 回復處理 chỉ lùi 1 bước mỗi lần. | Cho chọn "lùi về trạm X" với preview tất cả record bị hủy; vẫn là soft delete. |
| OPT-06 | P2 | 跳站 yêu cầu gõ mật khẩu admin trên tablet dùng chung. | Ủy quyền bằng QR/OTP trên điện thoại supervisor, hoặc gửi yêu cầu duyệt qua thông báo. |
| OPT-07 | P2 | 品質保留 / 異常 khóa hoàn toàn operator, không hiện ai đang xử lý. | Hiển thị người nhận thông báo, thời gian chờ, ghi chú QA ngay trên tablet. |
| OPT-08 | P2 | Không có màn hình tổng quan WIP theo trạm. | Dashboard: số thẻ / số lượng chờ tại mỗi 工序, thời gian chờ, máy rảnh/đang chạy/hỏng (realtime qua WebSocket). |
| OPT-09 | P2 | Không có OEE / thời gian dừng theo mã lý do. | Báo cáo từ `equipment_status_history`: availability theo máy/ca, Pareto DU_x. |
| OPT-10 | P2 | Đồng bộ MES 15 phút/lần, có cờ "同步不完全". | Outbox pattern + idempotency key; retry có backoff; màn hình đối soát lỗi. |
| OPT-11 | P3 | Danh sách lý do trạng thái khác nhau theo máy nhưng không thấy cấu hình rõ. | Bảng `equipment_reason_whitelist` có UI quản lý. |
| OPT-12 | P3 | Mã thẻ phụ (-01, -D1, R…) khác quy ước nhau. | Một bộ sinh mã có cấu hình prefix/suffix, luôn lưu `parent_runcard_id` + `source_type`. |
| OPT-13 | P3 | Tablet phụ thuộc mạng. | PWA có hàng đợi offline cho 上工/下工, đồng bộ khi có mạng, chặn xung đột bằng version. |
| OPT-14 | P3 | Chỉ admin có `skip_node`, `revoke_work`. | Gợi ý gán cho vai trò supervisor theo nhà máy; quyền theo khu vực/line. |
