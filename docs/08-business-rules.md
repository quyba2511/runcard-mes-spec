# 08 · Quy tắc nghiệp vụ

Các quy tắc dưới đây đã được kiểm chứng bằng thao tác thực tế trên bản demo. Hệ thống mới **phải giữ** các quy tắc này, trừ khi mục `OPT-xx` trong `09-optimization-backlog.md` đề xuất thay đổi.

## Số lượng
- **BR-01** `current + scrap + rework + split + defect = original` luôn đúng.
- **BR-02** 上工 không nhập số lượng; đầu vào = `current`.
- **BR-03** 下工: `output ≤ input`. Phần thiếu phải phân bổ hết vào mã lỗi → sinh 1 thẻ lỗi `-Dn` chứa số thiếu.
- **BR-04** 拆批: `0 < split_qty < current`.

## Trạng thái và vị trí
- **BR-05** Một RunCard chỉ có tối đa 1 工作紀錄 đang mở.
- **BR-06** 下工 ở trạm cuối → 已完成; trạm khác → 待上工 ở trạm kế.
- **BR-07** QC ngoài chuẩn khi 下工 → 品質保留; operator không thao tác được.
- **BR-08** 放行 thả toàn bộ số còn lại, thẻ về 待上工 tại trạm hiện tại.
- **BR-09** 臨時工序 không thay đổi trạm hiện tại.
- **BR-10** 跳站 cần ủy quyền tạm bằng tài khoản có `work_records:skip_node`.
- **BR-11** Thẻ 免路線 không có tuyến; không chuyển đổi được sau khi tạo.

## Hủy và hoàn tác
- **BR-12** 取消卡片 chỉ ở 待上工; bắt buộc lý do; giữ lịch sử.
- **BR-13** 回復處理 chỉ hủy record cuối; soft delete; lý do bắt buộc; thẻ lỗi phát sinh chưa xử lý bị 作廢 theo.

## 異常結束
- **BR-14** Chỉ khi 進行中; lý do bắt buộc.
- **BR-15** 保留現節點 → 待上工 cùng trạm, đếm ⚠ +1.
- **BR-16** 標記異常結束 → 異常; chỉ admin xử lý (放行 / 報廢 / 重工 / 回復處理).

## Thiết bị
- **BR-17** 上工 chỉ trên máy có trong danh sách 可執行工序 của 工序 hiện tại.
- **BR-18** 上工 chỉ khi máy 啟用中; 維修中 / 未開機 bị chặn.
- **BR-19** 上工 → máy UP; 下工 / 異常結束 → US_4.
- **BR-20** Máy đang có thẻ: operator chỉ đổi lý do trong UP / US_2 / US_5.
- **BR-21** Mỗi lần đổi trạng thái máy đóng bản ghi lịch sử trước và mở bản ghi mới.

## Truy vết
- **BR-22** Mọi thao tác ghi dữ liệu trên tablet phải có 工號.
- **BR-23** Mọi thao tác thay đổi trạng thái RunCard ghi 稽核日誌 (người, từ, đến, lý do).
- **BR-24** Mọi thao tác admin "bù" (補登, 回復處理, 放行, 取消) đều có trường lý do.
