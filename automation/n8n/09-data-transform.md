# Ch 9 — 資料轉換：Set、Edit Fields、Merge、Remove Duplicates

> 目標：能對 item 新增/修改/刪除欄位、把多個來源的資料合併成一個、去除重複資料。

## 為什麼需要資料轉換

API 吐給你的 JSON 很少是你直接能用的格式。你可能需要：

- 把三個欄位合成一個字串
- 只保留你需要的欄位，丟掉其餘
- 把兩個 API 的結果合在一起
- 日期格式從 Unix timestamp 轉成可讀字串

這章介紹幾個專門做資料整形的 node。

---

## Edit Fields（Set）Node

這是最常用的資料轉換 node。舊名稱是「Set」，新版 n8n 改名為「Edit Fields」。

主要做兩件事：

1. **新增或修改欄位**
2. **只保留指定欄位（Keep Only Set）**

### 新增/修改欄位

```
Mode: Manual Mapping

Fields to Set:
  Name: fullName
  Value: {{ $json.firstName + " " + $json.lastName }}

  Name: createdAt
  Value: {{ new Date().toISOString() }}
```

上面的設定會在每個 item 的 json 裡新增/覆蓋 `fullName` 和 `createdAt` 兩個欄位，原本的欄位保留。

### 只保留指定欄位

勾選 **Include Other Input Fields: false**（或切換到 Keep Only Set 模式），只輸出你明確設定的欄位，其餘全刪。

適合從一個有 50 個欄位的 API 回應裡，只取你需要的 3 個。

### JSON 模式

如果欄位很多，切換到 **JSON** 模式，直接用物件描述整個輸出：

```json
{
  "id":       "{{ $json.user_id }}",
  "name":     "{{ $json.first_name }} {{ $json.last_name }}",
  "email":    "{{ $json.contact.email }}",
  "joinedAt": "{{ $json.created_timestamp }}"
}
```

---

## Merge Node：合併多個來源

Merge node 把兩個（或多個）輸入的 item 合在一起。常用於：

- 用同一個 ID 把兩個 API 的資料 JOIN 起來
- 把分叉後的兩條路的結果合流

Merge 有幾種模式：

### Append

最簡單。把 Input 1 的 items 和 Input 2 的 items 直接串接成一個陣列。

```
Input 1: [A, B]
Input 2: [C, D]
輸出:    [A, B, C, D]
```

### Merge By Index

把 Input 1 的第 N 個 item 和 Input 2 的第 N 個 item 合成一個 item（屬性合併）。

```
Input 1: [{ id: 1, name: "Alice" }]
Input 2: [{ id: 1, score: 95 }]
輸出:    [{ id: 1, name: "Alice", score: 95 }]
```

如果 Input 1 和 Input 2 的 item 數不一樣，多出來的 item 會根據設定決定保留或丟棄。

### Merge By Key

根據指定欄位做 JOIN，類似 SQL 的 JOIN 操作：

```
Key Field: id

Input 1: [{ id: 1, name: "Alice" }, { id: 2, name: "Bob" }]
Input 2: [{ id: 1, score: 95    }, { id: 2, score: 88    }]
輸出:    [{ id: 1, name: "Alice", score: 95 },
          { id: 2, name: "Bob",   score: 88 }]
```

這個模式可以把兩個不同 API 的資料用共同的 key（例如 user_id）合起來。

### SQL Query

最強大的模式，直接在 Merge node 裡寫 SQL 來 JOIN：

```sql
SELECT a.name, b.score
FROM input1 a
JOIN input2 b ON a.id = b.id
WHERE b.score > 90
```

適合複雜的合流邏輯。

---

## Remove Duplicates Node

從 item 陣列裡去除重複項目。

```
Compare By: Specific Fields
Fields to Compare: email
```

上面設定會以 `email` 欄位為 key，相同 email 的 item 只保留第一筆。

**注意**：只比較你指定的欄位，其他欄位不參與比較。如果你想比較整個 item，選「All Fields」。

---

## 資料轉換的完整範例

你有兩個 API：

- `GET /users` → `[{ id: 1, name: "Alice" }, { id: 2, name: "Bob" }]`
- `GET /scores` → `[{ user_id: 1, score: 95 }, { user_id: 2, score: 88 }]`

你想合出 `[{ name: "Alice", score: 95 }, { name: "Bob", score: 88 }]`，只保留 name 和 score。

```
[HTTP Request: /users]         [HTTP Request: /scores]
        │                               │
        └───────────┬───────────────────┘
                    ▼
         [Merge: Merge By Key]
           Key 1: id
           Key 2: user_id
                    │
                    ▼
         [Edit Fields]
           name:  {{ $json.name }}
           score: {{ $json.score }}
           （不保留其他欄位）
```

---

## 其他常用轉換 Node

| Node | 用途 |
|---|---|
| **Aggregate** | 把多個 items 的欄位匯聚成一個 item（統計、列表）|
| **Sort** | 依欄位排序 items |
| **Limit** | 只保留前 N 個 items |
| **Filter** | 按條件過濾 items（比 If 輕量）|
| **Date & Time** | 日期格式轉換 |
| **Crypto** | hash、HMAC、加解密 |

---

## 自我檢核

- [ ] 能用 Edit Fields 新增計算欄位（組合兩個欄位的值）
- [ ] 能用 Edit Fields 的 Keep Only Set 只保留需要的欄位
- [ ] 知道 Merge 的 Append / Merge By Index / Merge By Key 差在哪
- [ ] 能用 Remove Duplicates 去除 email 重複的 item

→ [Ch 10 錯誤處理 — Error Trigger、Retry、Continue On Fail](./10-error-handling.md)
