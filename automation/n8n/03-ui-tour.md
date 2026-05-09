# Ch 3 — 介面導覽：Canvas、Node、Connection、Execution Log

> 目標：認識 n8n 主介面的每個區塊，能獨立新建 workflow 並找到任何 node。

## 主介面佈局

```
┌─────────────────────────────────────────────────────────────┐
│  [≡ 漢堡選單]  n8n    [Workflow 名稱]    [Save][Execute][▶] │  ← 頂部工具列
├────────────────────────────────┬────────────────────────────┤
│                                │                            │
│                                │                            │
│         Canvas（畫布）          │   Node Panel（右側面板）   │
│                                │   （點擊 node 後出現）     │
│   [Start]──▶[Node]──▶[Node]   │                            │
│                                │   Parameters               │
│                                │   Settings                 │
│                                │   Notes                    │
│                                │                            │
├────────────────────────────────┴────────────────────────────┤
│  [Executions]  [Canvas]  [+Add node]                        │  ← 底部工具列
└─────────────────────────────────────────────────────────────┘
```

---

## 頂部工具列

| 元素 | 功能 |
|---|---|
| 漢堡選單（≡） | 回到 workflow 列表、設定、credentials 管理 |
| Workflow 名稱 | 點擊可重新命名 |
| Save | 儲存目前 workflow（快捷鍵 Ctrl+S） |
| Execute Workflow | 手動觸發整個 workflow 跑一次 |
| 啟用開關（▶/⏹） | 開關 workflow 的自動觸發 |

**workflow 預設是關閉的**。你在 Canvas 上按「Execute Workflow」是手動跑，和啟用開關無關。啟用開關是讓 Trigger node（排程、webhook）真正開始監聽。

---

## Canvas（畫布）

這是你工作的主要區域。幾個操作要熟：

**新增 node**
- 點畫布空白處 → 搜尋框彈出 → 輸入 node 名稱 → 點選
- 或點擊 node 右側的 `+` 按鈕，會自動接上新 node

**移動畫布**
- 按住空白鍵 + 拖拉：平移畫布
- Ctrl/Cmd + 滾輪：縮放
- Ctrl/Cmd + Shift + H：把 workflow 置中

**選取 node**
- 單擊：選取並展開右側 Panel
- 按住 Shift + 拖拉：框選多個 node

**快捷鍵**
| 快捷鍵 | 動作 |
|---|---|
| Ctrl+Z | 復原 |
| Delete / Backspace | 刪除選取的 node |
| Ctrl+D | 複製選取的 node |
| Ctrl+A | 選取全部 |
| Ctrl+Alt+N | 新增 node |

---

## Node Panel（右側面板）

點擊任一 node 後，右側出現三個 tab：

### Parameters（參數）

這是你最常待的地方。每個 node 的設定都在這裡：

```
HTTP Request node 的 Parameters 範例：

Method:  [GET ▼]
URL:     https://api.example.com/data
Authentication: [None ▼]
Headers: [+ Add Header]
Body:    [None ▼]
```

值可以是固定字串，也可以用 **Expression**（表達式）動態帶入前一個 node 的資料。按 `=` 圖示切換。

### Settings

每個 node 通用的設定：

- **Always Output Data**：即使沒有輸出 item，也繼續執行後續 node
- **Execute Once**：不管輸入幾個 item，只執行一次
- **Retry On Fail**：失敗時自動重試
- **Continue On Fail**：失敗也繼續往下跑
- **Notes**：給這個 node 寫備注

### Notes

Markdown 格式的文字，顯示在 node 底部，方便團隊協作時說明用途。

---

## Connection（連線）

節點之間的箭頭。有幾件事要知道：

**輸出端口（Output）**

大多數 node 只有一個 output。If node 有兩個：`true` 和 `false`。Switch node 有多個。

**資料傳遞方式**

連線傳遞的是 **item 陣列**。一個 node 接到前一個 node 的所有 item，處理後輸出新的 item 陣列。

```
[前一個 node 輸出 3 個 items]
        │
        ▼
[下一個 node 收到 3 個 items，各跑一次，輸出 3 個處理後的 items]
```

這個機制在 Ch 5 詳細說。

**刪除連線**

點擊連線 → 出現垃圾桶圖示 → 點擊刪除。

---

## Execution Log

左下角的 **Executions** tab 或頂部 Executions 頁面，記錄每次 workflow 執行的歷史。

```
Executions 列表：
┌────────────────────────────────────────────────────┐
│ ID    Status    Started At         Duration  Mode   │
│ #42   ✅ Success 2026-05-08 10:00  1.2s      Manual │
│ #41   ❌ Error   2026-05-08 09:55  0.3s      Webhook│
│ #40   ✅ Success 2026-05-08 09:00  2.1s      Schedule│
└────────────────────────────────────────────────────┘
```

點開一筆 execution 可以看到：

- 每個 node 的 **輸入資料**（input）
- 每個 node 的 **輸出資料**（output）
- 每個 node 的執行時間
- 如果出錯，錯在哪個 node、錯誤訊息是什麼

**這是你 debug 最重要的工具**。workflow 跑壞了，第一步就是打開 Execution Log 看哪個 node 輸出了什麼。

---

## 搜尋 Node

n8n 有 400+ 個 node，找 node 的方式：

1. 畫布空白處按 `Ctrl+Alt+N` 或點 `+`
2. 搜尋框輸入服務名稱（英文）

常見 node 速查：

| 要做什麼 | 搜尋什麼 |
|---|---|
| 發 HTTP 請求 | `HTTP Request` |
| 排程觸發 | `Schedule Trigger` |
| 接收 webhook | `Webhook` |
| 寫 JavaScript | `Code` |
| 條件判斷 | `If` |
| 迴圈 | `Loop Over Items` |
| 合併資料 | `Merge` |
| 設定欄位 | `Edit Fields` |
| 發 Telegram | `Telegram` |
| 讀寫 Google Sheets | `Google Sheets` |

---

## 動手練習

做一件事，不用設定任何東西：

1. 進入 n8n，新建一個 workflow（點左上角 + New）
2. 在畫布上加入三個 node：`Manual Trigger` → `HTTP Request` → `Code`
3. 點每個 node，看看 Parameters、Settings、Notes 各有什麼
4. 把 HTTP Request 的 URL 填入 `https://httpbin.org/get`（一個回傳你請求資訊的測試 API）
5. 點頂部 **Execute Workflow**
6. 點 HTTP Request node，在右側看它的輸出資料

如果右側出現 JSON 資料，代表你已經發出了第一個 HTTP 請求。

## 自我檢核

- [ ] 知道頂部工具列裡 Save / Execute / 啟用開關的差異
- [ ] 能用快捷鍵新增、複製、刪除 node
- [ ] 知道 Node Panel 的 Parameters / Settings / Notes 各放什麼
- [ ] 會打開 Execution Log 查看某次執行的 node 輸出

下一章把這個介面用起來，建第一個真正有用的 workflow。

→ [Ch 4 第一個 Workflow — 每天早上自動抓天氣發通知](./04-first-workflow.md)
