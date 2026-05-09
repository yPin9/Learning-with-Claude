# Ch 13 — 資料庫整合：Postgres / MySQL / SQLite

> 目標：能在 n8n workflow 裡讀寫資料庫，包含查詢、插入、更新、刪除，以及用 Expression 帶入動態值。

## n8n 支援的資料庫

n8n 有專屬的資料庫 node：

| Node | 用於 |
|---|---|
| **Postgres** | PostgreSQL 資料庫 |
| **MySQL** | MySQL / MariaDB |
| **SQLite** | 輕量本地資料庫 |
| **Microsoft SQL** | MSSQL |
| **MongoDB** | 文件型資料庫 |

這章以 **Postgres** 為主示範（用法和 MySQL 幾乎一樣），SQLite 用於無需另外設伺服器的輕量場景。

---

## 設定 Credential

在 n8n 建立 Postgres credential：

```
Host:     localhost（或你的資料庫伺服器 IP）
Port:     5432
Database: mydb
User:     n8n_user
Password: your-password
SSL:      根據你的資料庫設定
```

如果你在 Ch 2 的 Docker Compose 裡跑本地 n8n，資料庫也在 Docker 裡的話，Host 填 Docker 網路名稱（`postgres`），不是 `localhost`。

---

## 基本操作

Postgres node 的 Operation：

| Operation | SQL 對應 |
|---|---|
| Select | SELECT |
| Insert | INSERT |
| Update | UPDATE |
| Delete | DELETE |
| Execute Query | 任意 SQL |

### Select（查詢）

```
Operation: Select
Table:     orders
Where:     status = 'paid'
Limit:     100
```

或用 Execute Query 寫完整 SQL（更靈活）：

```sql
SELECT o.id, o.amount, u.email
FROM orders o
JOIN users u ON o.user_id = u.id
WHERE o.status = 'paid'
  AND o.created_at > NOW() - INTERVAL '7 days'
ORDER BY o.amount DESC
LIMIT 50
```

輸出是每一列一個 item，欄位名稱就是 SELECT 的欄位名。

### Insert（插入）

```
Operation: Insert
Table:     events
Columns:   user_id, event_type, properties, created_at
```

它會自動用前一個 node 傳來的 item json 裡對應的欄位填值。如果 item 有 `user_id`、`event_type` 等欄位，直接對應。

也可以開 **Column mappings** 手動指定：

```
Column: user_id    → Value: {{ $json.userId }}
Column: event_type → Value: {{ $json.type }}
Column: properties → Value: {{ JSON.stringify($json.meta) }}
Column: created_at → Value: NOW()
```

### Update（更新）

```
Operation: Update
Table:     orders
Update Key: id     ← 用這個欄位找要更新的列
Columns:   status, updated_at
```

它用 item 的 `id` 找到對應列，更新 `status` 和 `updated_at`。

### Execute Query（任意 SQL + 動態參數）

```
Operation: Execute Query
Query:     UPDATE orders SET status = $1, note = $2 WHERE id = $3
```

Parameters tab 填入：

```
Parameter 1: {{ $json.status }}
Parameter 2: {{ $json.note }}
Parameter 3: {{ $json.order_id }}
```

**使用 `$1`, `$2` 參數化查詢比字串拼接安全**，避免 SQL Injection。

---

## 批次 Insert 優化

如果有 1000 筆資料要 insert，預設每個 item 跑一次 SQL，共 1000 次。很慢。

在 Postgres node 的 Options 開啟：

```
Options:
  Query Batching: Complete
```

n8n 會把所有 item 合成一個 batch insert 語句，大幅提速。

或者用 Code Node 自己組 batch insert SQL：

```javascript
const items = $input.all();
const rows = items.map(i => `('${i.json.name}', '${i.json.email}')`).join(',');
// 注意：實際應用要用 parameterized query 防止 injection
return [{ json: { query: `INSERT INTO users (name, email) VALUES ${rows}` } }];
```

---

## 搭配 SQLite（無伺服器）

SQLite 不需要另外跑一個資料庫伺服器，直接讀寫本地檔案。適合：

- workflow 的簡單快取或去重
- 小量資料的 key-value 儲存
- 開發測試

Credential 只需要填：

```
Database Path: /home/node/.n8n/local.db   ← Docker 容器內的路徑
```

用法和 Postgres node 一樣。

---

## 常見場景

**場景 1：從資料庫拉資料，批次處理，結果寫回**

```
[Schedule Trigger]
     │
[Postgres: SELECT id, email FROM users WHERE notified_at IS NULL]
     │
[HTTP Request: 呼叫發信 API（每個 user 一次）]
     │
[Postgres: UPDATE users SET notified_at = NOW() WHERE id = $1]
```

**場景 2：Webhook 接收資料，存入資料庫**

```
[Webhook: POST /order]
     │
[Postgres: INSERT INTO orders ...]
     │
[Respond to Webhook: { "success": true, "order_id": {{ $json.id }} }]
```

---

## 自我檢核

- [ ] 能設定 Postgres credential 並連上資料庫
- [ ] 能用 Execute Query 寫帶動態參數的 SQL
- [ ] 知道 `$1`, `$2` 參數化查詢防 SQL Injection 的必要性
- [ ] 知道 Query Batching 的用途

→ [Ch 14 通訊整合 — Email / Telegram / Slack / Discord](./14-messaging.md)
