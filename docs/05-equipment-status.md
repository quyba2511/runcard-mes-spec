# 05 · Trạng thái thiết bị

## 3 trạng thái + mã lý do

| 狀態 | 主分類 | 次分類 | Mã | Mô tả |
|---|---|---|---|---|
| 啟用中 | 稼動時間 | 工程實驗 | UE_1 | 工程實驗前調機 |
| 啟用中 | 稼動時間 | 工程實驗 | UE_2 | 工程實驗批 (E 工單) |
| 啟用中 | 稼動時間 | 正常生產 | UP | 正常生產 |
| 啟用中 | 稼動時間 | 待機 | US_1 | 設備點檢/調機 |
| 啟用中 | 稼動時間 | 待機 | US_2 | 首件 (抓條件) |
| 啟用中 | 稼動時間 | 待機 | US_3 | 製程中量測 |
| 啟用中 | 稼動時間 | 待機 | US_4 | 機台閒置 |
| 啟用中 | 稼動時間 | 待機 | US_5 | 人員用餐休息 |
| 維修中 | 停機 | 計畫內 | DS_1 | 定期設備保養維修 |
| 維修中 | 停機 | 計畫內 | DS_2 | 均勻性測試 |
| 維修中 | 停機 | 計畫內 | DS_3 | 設備保養後復機首件 |
| 維修中 | 停機 | 非計畫內 | DU_1 | 機台結構、硬體異常停機 |
| 維修中 | 停機 | 非計畫內 | DU_2 | 軟體異常停機 |
| 維修中 | 停機 | 非計畫內 | DU_3 | 品質異常停機 |
| 維修中 | 停機 | 非計畫內 | DU_4 | 廠務系統因素停機 (水、氣、電、CDA) |
| 維修中 | 停機 | 非計畫內 | DU_5 | 其他 (地震、工安) |
| 未開機 | — | — | OFF | 未開機 |

Danh sách lý do có thể khác nhau theo từng máy. Màn hình hiển thị nhãn ngắn: 生產中 (UP), 機台閒置 (US_4), 維修中, 異常…

## Quy tắc tự động

| Sự kiện | Trạng thái máy sau |
|---|---|
| 上工 | 啟用中 / UP (生產中), gắn RunCard |
| 下工 | 啟用中 / US_4 |
| 異常結束 (cả 2 kiểu) | 啟用中 / US_4 |
| Chuyển 維修中 | Bắn thông báo "設備維修" |

## Ràng buộc

- 上工 chỉ khi máy 啟用中.
- Khi máy đang có thẻ: operator chỉ chọn được UP / US_2 / US_5.
- Admin `設定設備狀態` luôn có đủ 3 trạng thái (cần quyền `equipment_status:set`).
- Mỗi lần đổi tạo 1 dòng trong `設備狀態歷程`: bản ghi trước được đóng (`end_at`), thời lượng tính tự động.

## 設備狀態歷程 (cột)

設備名稱 · 主分類 · 次分類 · 原因 · 狀態 · RunCard · 記錄人 · 開始時間 · 結束時間 · 持續時間. Lọc theo máy, phân loại, "僅顯示進行中".
