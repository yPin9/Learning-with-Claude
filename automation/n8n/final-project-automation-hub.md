# Final Project — 個人自動化中樞

> 目標：整合課程所有技能，建一個跑在自己 VPS 上、串接 5 個以上服務、含 AI Agent 的完整自動化系統。

## 專案描述

你要建的是一個「個人自動化中樞」，讓你的資訊流、工作流和通知全部集中管理。

**最終系統包含以下五個 workflow：**

1. **每日簡報**：每天早上整合天氣、HN Top 5、TODO 清單，發到 Telegram
2. **內容追蹤**：RSS 訂閱 → 關鍵字過濾 → 存 DB → Notion
3. **AI 問答 Bot**：Telegram bot，能查 FAQ 知識庫 + 記住對話
4. **費用記帳**：Telegram 傳文字 → AI 解析 → 存 Google Sheets
5. **錯誤監控**：上面所有 workflow 的統一錯誤通知

---

## Workflow 1：每日簡報

**觸發**：每天早上 07:30

**資料來源**：

```
[Schedule: 07:30]
       │
       ├──▶ [HTTP: 台北天氣（wttr.in）]
       ├──▶ [HTTP: HN Top 5（Firebase API）]
       └──▶ [Postgres: 今日 TODO（自訂 tasks table）]
                   │
             [Merge: Append 三個來源]
                   │
              [Code: 組成 Telegram 訊息]
                   │
              [Telegram: 發送]
```

**期望輸出格式**：

```
🌅 2026年5月8日 早安日報

🌤 台北天氣
今日：多雲，28°C（體感 30°C）
降雨機率：40%

📰 今日 HN 精選
1. Show HN: ... [1234 pts]
2. Ask HN: ... [987 pts]
（共 5 則）

✅ 今日待辦
- 完成 n8n 課程 Final Project
- 回覆客戶提案

祝今天順利！
```

**技術要點**：

三個來源並行請求（fanout），用 Merge Append 合流，Code Node 統一組訊息。

---

## Workflow 2：內容追蹤

**觸發**：每小時一次

**流程**：

```
[Schedule: 每小時]
      │
[HTTP: RSS Feed（你訂閱的 blog/podcast）]
   （用 https://rss.app 或直接打 RSS XML URL）
      │
[XML: 解析 RSS → items]
      │
[If: 關鍵字命中（你設定的清單）]
  true ──┤
         ├──▶ [Postgres: 去重插入]
         │         │（只有新文章）
         └──▶ [Notion: 新增到閱讀清單 DB]
```

**XML Node 設定**：

n8n 有內建 XML node 可以解析 RSS：

```
XML: 選 Options → Handle Arrays: true
```

RSS item 的欄位：`title`、`link`、`description`、`pubDate`

---

## Workflow 3：AI 問答 Bot

**觸發**：Telegram Bot 收到訊息

**前置工作**：準備你的 FAQ 文件，用入庫 workflow 存到 Qdrant（見 Ch 25）

**流程**：

```
[Telegram Trigger]
      │
[If: 是否是 /start 指令]
  true  ──▶ [Telegram: 發歡迎訊息]
  false ──┤
          │
   [Postgres Chat Memory（session = chat id）]
          │
   [AI Agent]
     Model: claude-haiku-4-5
     System: 你是個人助手，用繁體中文回答，
             如果知識庫有相關資訊優先引用。
     Tools:
       - search_faq（Qdrant RAG）
       - get_weather（HTTP Tool）
          │
   [Telegram: 發送 AI 回答]
```

---

## Workflow 4：費用記帳

**觸發**：Telegram 傳「200 午餐便當」

**AI 解析**：讓 LLM 從自然語言裡抽出金額、類別、描述

**流程**：

```
[Telegram Trigger]
      │
[If: 訊息以數字開頭（可能是費用）]
  true ──┤
         │
  [HTTP Request: Claude API]
    Prompt: 從以下文字中提取費用資訊，回傳 JSON：
    "金額（數字）、類別（餐飲/交通/購物/其他）、描述"
    文字：{{ $json.message.text }}
         │
  [Code: 解析 AI 回傳的 JSON]
         │
  [Google Sheets: 新增一列到記帳表]
     date:    今天日期
     amount:  {{ $json.amount }}
     category: {{ $json.category }}
     note:    {{ $json.description }}
         │
  [Telegram: 回覆「已記錄：{{ $json.category }} ${{ $json.amount }}」]
```

**Google Sheets 格式**：

| date | amount | category | note |
|---|---|---|---|
| 2026-05-08 | 200 | 餐飲 | 午餐便當 |

---

## Workflow 5：統一錯誤通知

這個 Sub-workflow 被其他四個 workflow 的 Error Workflow 指向：

```
[Execute Workflow Trigger]
      │
[Code: 組成錯誤報告]
      │
[Telegram: 
  "⚠️ Workflow 出錯
  名稱：{{ $json.workflow.name }}
  錯誤：{{ $json.execution.error.message }}
  時間：{{ new Date().toLocaleString('zh-TW') }}
  查看：{{ $json.execution.url }}"
]
      │
[Postgres: 寫入 error_log table]
  workflow_name, error_message, occurred_at
```

---

## 系統架構圖

```
VPS (n8n + Postgres + Qdrant + Nginx)
├── Workflow 1：每日簡報     ← Schedule
├── Workflow 2：內容追蹤     ← Schedule（每小時）
├── Workflow 3：AI 問答 Bot  ← Telegram Trigger
├── Workflow 4：費用記帳     ← Telegram Trigger
└── Workflow 5：錯誤監控     ← Execute Workflow Trigger
         ↑（全部 workflow 的 Error Workflow 都指向這裡）

外部服務整合：
Telegram Bot │ wttr.in │ HN API │ RSS Feed
Notion │ Google Sheets │ Qdrant │ Claude API
```

---

## 驗收清單

完成 Final Project 後，確認以下全部能正常運作：

**Workflow 1：每日簡報**

- [ ] 手動執行，Telegram 收到包含天氣、HN 和 TODO 的日報
- [ ] 啟用後，在設定的時間自動發送

**Workflow 2：內容追蹤**

- [ ] 手動執行，Postgres 有新文章、Notion 有新記錄
- [ ] 重複執行不會插入重複資料

**Workflow 3：AI 問答 Bot**

- [ ] 傳訊息給 Bot，能用知識庫回答
- [ ] 問兩次相關問題，Bot 記得上一次的回答
- [ ] 問天氣，Bot 能查詢並回答

**Workflow 4：費用記帳**

- [ ] 傳「150 捷運」，Google Sheets 出現正確記錄
- [ ] 傳「320 買了一本書」，類別正確識別為「購物」

**Workflow 5：錯誤監控**

- [ ] 故意讓其中一個 workflow 出錯（填一個壞的 API URL）
- [ ] Telegram 收到錯誤通知，Postgres error_log 有記錄

---

## 延伸挑戰（選做）

完成基本版後，嘗試這些：

1. **費用月報**：每月 1 號，從 Google Sheets 拉上個月資料，用 AI 生成消費分析，發到 Telegram
2. **GitHub 活動追蹤**：GitHub Trigger（push event） → 更新個人 Notion 工作日誌
3. **圖片 OCR 記帳**：Telegram 傳發票照片 → n8n 下載圖片 → Claude Vision 解析金額 → 自動記帳

---

## 完成這個 Final Project，你已經具備

- 用 n8n 建立複雜多步驟 workflow 的能力
- 整合各種第三方服務（API、資料庫、通訊）
- 在 workflow 裡用 JavaScript 處理複雜邏輯
- 把 LLM 接進自動化流程（AI Agent + RAG + Memory）
- Self-host n8n 並設定監控和備份

這就是一個可以直接投入使用的個人自動化中樞。
