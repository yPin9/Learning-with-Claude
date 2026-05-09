# 練習 B — 多服務 Pipeline

> 目標：把 Ch 11–16 學到的 HTTP、Webhook、DB、通訊、雲端文件整合在一個 workflow 裡。

## 任務規格

你要建一個**內容追蹤 Pipeline**：

1. 每天定時抓 Hacker News Top Stories（RSS/API）
2. 過濾出 point 數超過 300 的文章
3. 把新文章存到 Postgres（避免重複）
4. 已存入的文章更新到 Google Sheets（供非技術同事查看）
5. 每天下午 5 點把今日精選（前 5 名）整理成摘要發到 Telegram

```
[Schedule: 每小時]
       │
[HTTP: HN Top Stories API]
       │
[Code: 解析資料 + 過濾 points >= 300]
       │
[Postgres: SELECT 檢查 item_id 是否已存在]
       │
[If: 是新文章]
  true ──┤
         ├──▶ [Postgres: INSERT 新文章]
         │
         └──▶ [Google Sheets: Append Row]

[Schedule: 每天 17:00] ─────────────────────────────────┐
                                                         │
[Postgres: SELECT TOP 5 today + points DESC]             │
       │                                                 │
[Code: 組成 Telegram 摘要訊息]                           │
       │◀───────────────────────────────────────────────┘
[Telegram: 發送摘要]
```

---

## 資料來源

Hacker News 官方 API（免費，不需要 key）：

```
Top Stories（回傳 item ID 陣列）：
GET https://hacker-news.firebaseio.com/v0/topstories.json?print=pretty

單篇文章詳情：
GET https://hacker-news.firebaseio.com/v0/item/{id}.json?print=pretty
```

單篇詳情結構：

```json
{
  "id": 12345,
  "title": "Show HN: I built a...",
  "url": "https://example.com",
  "score": 458,
  "by": "username",
  "time": 1715000000,
  "descendants": 123
}
```

---

## Postgres Table 設計

先在你的 Postgres 建好 table：

```sql
CREATE TABLE hn_stories (
  id           SERIAL PRIMARY KEY,
  item_id      INTEGER UNIQUE NOT NULL,
  title        TEXT NOT NULL,
  url          TEXT,
  score        INTEGER,
  author       TEXT,
  published_at TIMESTAMPTZ,
  fetched_at   TIMESTAMPTZ DEFAULT NOW()
);
```

---

## 期望輸出

**Google Sheets**：

| item_id | title | url | score | author | published_at |
|---|---|---|---|---|---|
| 12345 | Show HN: ... | https://... | 458 | username | 2026-05-08 |

**Telegram（每天 17:00）**：

```
📰 今日 HN 精選 (2026-05-08)

1. Show HN: I built X [1234 points]
   https://example.com

2. Ask HN: Why does Y happen? [987 points]
   https://news.ycombinator.com/item?id=xxxxx

...（共 5 則）
```

---

## 實作步驟建議

### Step 1：抓 Top Stories + 取詳情

HN Top Stories API 一次回傳 500 個 item ID，你只需要前 30 個：

```javascript
// Code Node：把 top stories 陣列展開成多個 item
const ids = $input.first().json;          // 500 個 id 的陣列
const top30 = ids.slice(0, 30);

return top30.map(id => ({ json: { item_id: id } }));
```

接著用 HTTP Request node 取每篇文章詳情：

```
URL: https://hacker-news.firebaseio.com/v0/item/{{ $json.item_id }}.json?print=pretty
```

這個 node 對 30 個 item 各跑一次，共發 30 個請求。

### Step 2：過濾 score

```
If: {{ $json.score }} >= 300
```

### Step 3：去重檢查

```sql
SELECT id FROM hn_stories WHERE item_id = $1
```

如果查到結果（item 已存在），就不再 insert。用 If Node 判斷：

```
Value 1: {{ $('Postgres Check').first().json.id }}
Operation: Is Empty
```

（用 `first()` 取結果，如果沒找到，first() 回傳 undefined，Is Empty 條件成立）

### Step 4：新增到 Postgres + Google Sheets

Postgres INSERT：

```sql
INSERT INTO hn_stories (item_id, title, url, score, author, published_at)
VALUES ($1, $2, $3, $4, $5, to_timestamp($6))
```

Google Sheets Append Row 取上面 INSERT 的 output（n8n 會回傳剛 insert 的資料）。

### Step 5：下午 5 點的摘要 Workflow

這是**第二個 workflow**，獨立 Schedule Trigger：

```sql
SELECT title, url, score
FROM hn_stories
WHERE fetched_at >= NOW() - INTERVAL '24 hours'
ORDER BY score DESC
LIMIT 5
```

Code Node 組訊息：

```javascript
const stories = $input.all();
const today = new Date().toLocaleDateString('zh-TW', { timeZone: 'Asia/Taipei' });

const lines = stories.map((s, i) => {
  const { title, url, score } = s.json;
  return `${i+1}. ${title} [${score} points]\n   ${url || 'https://news.ycombinator.com'}`;
});

const message = `📰 今日 HN 精選 (${today})\n\n${lines.join('\n\n')}`;
return [{ json: { message } }];
```

---

## 完整參考解答

<details>
<summary>點開看踩雷提示</summary>

**HN API 速率限制**：每秒約 10 個請求。30 個並行請求通常不會被限速，但如果你改成取前 100 個，可能需要加 Split In Batches + Wait node。

**去重邏輯**：Postgres 查詢回 0 筆時，`$('Postgres Check').first()` 會拋錯而不是回 undefined。改成這樣更穩：

```javascript
const existing = $('Postgres Check').all();
if (existing.length > 0) {
  // 已存在，不 insert
  return [];
}
return [$input.item];
```

**Google Sheets 寫入欄位型別**：score 是數字，但 Sheets 可能存成字串。在 Edit Fields node 強制轉型：`{{ parseInt($json.score) }}`。

**時區**：`to_timestamp($6)` 把 Unix timestamp 轉成 Postgres 的 TIMESTAMPTZ，預設 UTC。查詢時用 `AT TIME ZONE 'Asia/Taipei'` 轉換顯示時區。

</details>

---

## 測試用例

1. 手動執行主 workflow，確認 Postgres 有資料、Google Sheets 有新增列
2. 再執行一次，確認不會插入重複資料
3. 手動執行摘要 workflow，確認 Telegram 收到格式正確的訊息
4. 把 score 門檻暫時改成 9999，確認 0 筆時不報錯

---

## 自我檢核

- [ ] 知道如何把 API 回傳的陣列展開成多個 item，並對每個 item 發 HTTP 請求
- [ ] 能設計去重邏輯（先 SELECT，有結果才 INSERT）
- [ ] 兩個 workflow 之間的資料分離（各自獨立觸發，共用同一個 DB）
- [ ] Telegram 訊息的 multi-item 合併（用 Code Node 而不是讓 Telegram node 跑多次）

→ [Ch 17 Code Node 基礎 — 在 n8n 裡寫 JavaScript](./17-code-node-basics.md)
