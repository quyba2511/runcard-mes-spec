# 07 · Thông báo

## 11 sự kiện

| Sự kiện | Mức | Kích hoạt |
|---|---|---|
| 臨時工序放行 | | Tạo 臨時工序 |
| 瑕疵單開立 | | 下工 sinh thẻ lỗi |
| 瑕疵單作廢 | | Thẻ lỗi bị hủy (vd. qua 回復處理) |
| 設備異常 | 嚴重 | Máy báo lỗi |
| 設備維修 | 嚴重 | Máy chuyển 維修中 |
| 跳站上工 | | 跳站 được ủy quyền |
| 品質不合格待判定 | | QC ngoài chuẩn → 品質保留 |
| 品質報廢 | | 報廢 |
| 重工判定 | | Tạo 重工 |
| RunCard 異常 | 嚴重 | 標記異常結束 |
| 拆批 | | Tách lô |

## Cấu hình mỗi sự kiện

- 通知管道: 信件 (email) / 站內 (in-app)
- 初次通知對象: theo 角色 và/hoặc người cụ thể
- 是否重複通知: khoảng lặp + số lần tối đa (vd. 30 phút × 5)
- 是否升級呈報: leo thang sau X phút nếu chưa xử lý

Mặc định 設備異常 / 設備維修: email + in-app, lặp 30' tối đa 5 lần, leo thang sau 30'.

## Hiển thị

Chuông góc phải Admin: badge số chưa đọc, danh sách (tiêu đề, nội dung, thời gian), `全部標為已讀`, phân trang.

Ví dụ nội dung:
- `設備維修：DEMO 錫膏印刷機` — "設備 … 已切換為維修中，原因：機台結構、硬體異常停機"
- `RunCard 異常：DEMO-RC-0002` — "RunCard … 已標記為異常，原因：…"
- `品質不合格待判定：…` — "品質檢查未全數合格，已進入待判定狀態，請盡快處理（放行 / 重工 / 報廢）"
