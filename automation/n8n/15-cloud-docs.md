# Ch 15 — 雲端文件：Google Sheets、Notion、Airtable

> 目標：能把 workflow 產生的資料寫入三種主流雲端試算表/資料庫工具，並從中讀取資料驅動 workflow。

## 為什麼雲端文件是好的 workflow 終點

資料庫（Ch 13）適合程式讀取，但雲端文件有一個優勢：**人可以直接打開看**。

試算表和 Notion 讓你的非技術同事也能存取 workflow 產生的資料，不需要任何 SQL 查詢工具。

---

## Google Sheets

### Credential 設定

使用 OAuth2：

1. Google Cloud Console → 建新 Project（或用現有的）
2. APIs & Services → Enable API → 搜尋「Google Sheets API」啟用
3. OAuth consent screen → 設定（外部，測試模式）
4. Credentials → OAuth 2.0 Client IDs → Web application
5. Authorized redirect URI 填 n8n 給的 URL
6. 在 n8n 建 Google Sheets OAuth2 credential，完成授權

### 讀取資料（Get Many Rows）

```
Resource:   Spreadsheet
Operation:  Get Many Rows
Document:   [選你的試算表]
Sheet:      Sheet1
Filters:
  Column: status  Value: pending
```

每一列輸出一個 item，欄位名稱來自第一列（標題列）。

### 寫入資料（Append or Update Row）

```
Resource:       Spreadsheet
Operation:      Append or Update Row
Document:       [選你的試算表]
Sheet:          Sheet1
Matching Column: id   ← 用這個欄位判斷要更新還是新增
Columns:
  id:     {{ $json.id }}
  name:   {{ $json.name }}
  status: {{ $json.status }}
```

「Append or Update」行為：

- 如果 `id` 已存在 → 更新那一列
- 如果 `id` 不存在 → 新增一列

### 清除資料（Clear）

```
Operation: Clear
Range:     A2:Z1000   ← 清除這個範圍（保留第一列標題）
```

---

## Notion

Notion 的 node 操作對象是 **Database**（資料庫頁面），每一列是一個 Notion Page。

### Credential 設定

1. Notion → Settings → Integrations → 建立新 Integration
2. 複製 Internal Integration Token
3. 在想整合的 Notion Database 頁面，點右上角「...」→ Connect to → 選你的 Integration
4. n8n 建 Notion credential → 填入 token

### 新增一列（Create Page）

```
Resource:    Database Page
Operation:   Create
Database ID: [從 Notion URL 複製，格式是 32 位十六進位]
Title:       {{ $json.title }}
Properties:
  Status:     {{ $json.status }}
  Priority:   {{ $json.priority }}
  Due Date:   {{ $json.due_date }}
```

**Notion 的欄位型別要對應**：Notion database 欄位設定是什麼型別（Select、Date、Number...），n8n 的 value 也要是對應格式。

### 查詢資料庫（Get Many）

```
Resource:    Database Page
Operation:   Get Many
Database ID: [你的 database ID]
Filters:
  Property: Status  Condition: Equals  Value: In Progress
Sort:
  Property: Due Date  Direction: Ascending
```

---

## Airtable

Airtable 用 API Key 或 OAuth，操作類似 Google Sheets 但更有彈性。

### Credential 設定

1. Airtable → Account → API → 建立 Personal Access Token
2. Scopes：`data.records:read` + `data.records:write` + `schema.bases:read`
3. n8n 建 Airtable Token credential

### 讀取（List Records）

```
Operation:  List
Base ID:    appXXXXXXXX       ← 從 Airtable URL 取得
Table Name: Leads
Filters:
  Field: status  Operator: =  Value: new
```

### 新增記錄（Create Record）

```
Operation:  Create
Base ID:    appXXXXXXXX
Table Name: Leads
Fields:
  Name:   {{ $json.name }}
  Email:  {{ $json.email }}
  Source: {{ $json.source }}
```

### 更新記錄（Update Record）

```
Operation: Update
Record ID: {{ $json.airtable_id }}   ← 需要 Airtable 的 record ID
Fields:
  Status: Contacted
  Notes:  {{ $json.notes }}
```

更新需要 Airtable 的 Record ID（格式 `recXXXXXXXX`）。通常先用 List 查到 ID，再用 Update。

---

## 三者比較

| | Google Sheets | Notion | Airtable |
|---|---|---|---|
| 設定難度 | 中（OAuth 較麻煩）| 低 | 低 |
| 欄位型別支援 | 全文字 | 豐富 | 豐富 |
| 查詢/過濾 | 有限 | 有限 | 強 |
| 適合場景 | 簡單試算表、熟悉 Excel 的團隊 | 文件 + 資料混合 | 輕量資料庫、視覺化 |

---

## 自我檢核

- [ ] 能設定 Google Sheets OAuth2 credential 並讀寫試算表
- [ ] 知道 Google Sheets「Append or Update Row」的 Matching Column 用途
- [ ] 能設定 Notion Integration 並新增/查詢 Database Page
- [ ] 知道 Airtable 更新操作需要 Record ID

→ [Ch 16 OAuth 2.0 實戰 — Credentials 管理與 Token 刷新](./16-oauth.md)
