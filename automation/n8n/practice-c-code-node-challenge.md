# 練習 C — Code Node 挑戰：自製資料清洗流水線

> 目標：用 Code Node 處理一批「髒資料」，輸出乾淨、正規化、統計完整的結果。

## 任務規格

你從一個老系統匯出了一份 CSV 資料，轉成 JSON 後長這樣（有意設計得很亂）：

```json
[
  { "ID": "001", "Name": "  alice chen  ", "Email": "ALICE@EXAMPLE.COM", "Amount": "1,200.50", "Date": "2026/05/01", "Status": "Paid" },
  { "ID": "002", "Name": "Bob Wang",       "Email": "bob@example",        "Amount": "$800",    "Date": "01-05-2026", "Status": "paid" },
  { "ID": "003", "Name": "",               "Email": "carol@example.com",  "Amount": "500.00",  "Date": "2026-05-03", "Status": "PENDING" },
  { "ID": "004", "Name": "David Lin",      "Email": "david@example.com",  "Amount": "invalid", "Date": "2026-05-04", "Status": "Cancelled" },
  { "ID": "005", "Name": "Eve Wu",         "Email": "alice@example.com",  "Amount": "2500",    "Date": "2026-05-05", "Status": "paid" }
]
```

問題清單：

- 名字有多餘空格（`  alice chen  `）
- Email 大小寫不一致，有些格式無效（`bob@example` 沒有 TLD）
- 金額格式各異（`1,200.50`、`$800`、`invalid`）
- 日期格式有三種（`2026/05/01`、`01-05-2026`、`2026-05-03`）
- 狀態大小寫不一致（`Paid`、`paid`、`PENDING`）
- 名字欄位可能是空字串
- Email 可能重複（`alice@example.com` 出現兩次）

---

## 期望輸出

### 乾淨資料（每筆一個 item）

| id | name | email | amount | date | status | valid |
|---|---|---|---|---|---|---|
| 1 | Alice Chen | alice@example.com | 1200.50 | 2026-05-01 | paid | true |
| 2 | Bob Wang | bob@example | — | 2026-05-01 | paid | false |
| 3 | (unknown) | carol@example.com | 500.00 | 2026-05-03 | pending | true |
| 4 | David Lin | david@example.com | — | 2026-05-04 | cancelled | false |
| 5 | Eve Wu | alice@example.com | 2500 | 2026-05-05 | paid | false |

`valid: false` 的原因：
- #2：email 格式無效
- #4：金額無效
- #5：email 重複（和 #1 的 email 相同）

### 統計摘要（一個 summary item）

```json
{
  "total":       5,
  "valid":       2,
  "invalid":     3,
  "total_amount": 4200.50,
  "by_status": {
    "paid":       3,
    "pending":    1,
    "cancelled":  1
  },
  "invalid_reasons": {
    "invalid_email":   2,
    "invalid_amount":  1,
    "duplicate_email": 1
  }
}
```

---

## 實作步驟

### Step 1：建立測試資料來源

用 Code Node 直接產生上面的「髒資料」作為 input（不需要真的 CSV）：

```javascript
return [
  { json: { ID: "001", Name: "  alice chen  ",  Email: "ALICE@EXAMPLE.COM", Amount: "1,200.50", Date: "2026/05/01", Status: "Paid"      } },
  { json: { ID: "002", Name: "Bob Wang",         Email: "bob@example",       Amount: "$800",     Date: "01-05-2026", Status: "paid"      } },
  { json: { ID: "003", Name: "",                 Email: "carol@example.com", Amount: "500.00",   Date: "2026-05-03", Status: "PENDING"   } },
  { json: { ID: "004", Name: "David Lin",        Email: "david@example.com", Amount: "invalid",  Date: "2026-05-04", Status: "Cancelled" } },
  { json: { ID: "005", Name: "Eve Wu",           Email: "alice@example.com", Amount: "2500",     Date: "2026-05-05", Status: "paid"      } },
];
```

### Step 2：清洗 Code Node（Run Once for All Items）

在第二個 Code Node 裡完成：

1. 名字清理：`trim()` + 轉 Title Case
2. Email 正規化：`toLowerCase()`，驗證格式（正則）
3. 金額解析：移除 `$` 和 `,`，嘗試 `parseFloat`
4. 日期標準化：偵測格式，統一輸出 `YYYY-MM-DD`
5. 狀態正規化：`toLowerCase()`
6. 去重：記錄已出現的 email，標記重複

### Step 3：統計 Code Node

對清洗後的 items 做聚合，輸出一個 summary item。

### Step 4：Merge

把乾淨資料（多個 items）和統計摘要（一個 item）用 Merge（Append 模式）合在一起輸出。

---

## 完整參考解答

<details>
<summary>展開參考實作</summary>

```javascript
// Step 2 清洗 Code Node

const items = $input.all();
const seenEmails = new Set();

function parseDate(dateStr) {
  // 偵測三種格式
  if (/^\d{4}\/\d{2}\/\d{2}$/.test(dateStr)) {
    return dateStr.replace(/\//g, '-');
  }
  if (/^\d{2}-\d{2}-\d{4}$/.test(dateStr)) {
    const [d, m, y] = dateStr.split('-');
    return `${y}-${m}-${d}`;
  }
  if (/^\d{4}-\d{2}-\d{2}$/.test(dateStr)) {
    return dateStr;
  }
  return null;
}

function parseAmount(amtStr) {
  const cleaned = amtStr.replace(/[$,]/g, '').trim();
  const num = parseFloat(cleaned);
  return isNaN(num) ? null : num;
}

function isValidEmail(email) {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email);
}

function toTitleCase(str) {
  return str
    .trim()
    .toLowerCase()
    .replace(/\b\w/g, c => c.toUpperCase());
}

return items.map(item => {
  const d = item.json;
  const reasons = [];

  const name   = d.Name.trim()
    ? toTitleCase(d.Name)
    : '(unknown)';

  const email  = d.Email.toLowerCase().trim();
  const amount = parseAmount(d.Amount);
  const date   = parseDate(d.Date);
  const status = d.Status.toLowerCase().trim();

  if (!isValidEmail(email))   reasons.push('invalid_email');
  if (amount === null)         reasons.push('invalid_amount');
  if (seenEmails.has(email))  reasons.push('duplicate_email');
  else seenEmails.add(email);

  return {
    json: {
      id:             parseInt(d.ID),
      name,
      email,
      amount,
      date,
      status,
      valid:          reasons.length === 0,
      invalid_reasons: reasons
    }
  };
});
```

```javascript
// Step 3 統計 Code Node

const items = $input.all();

const byStatus = {};
const invalidReasons = {};
let totalAmount = 0;
let validCount = 0;

for (const item of items) {
  const d = item.json;

  // 狀態統計
  byStatus[d.status] = (byStatus[d.status] || 0) + 1;

  // 有效金額加總
  if (d.amount !== null) totalAmount += d.amount;

  // 有效/無效計數
  if (d.valid) {
    validCount++;
  } else {
    for (const r of d.invalid_reasons) {
      invalidReasons[r] = (invalidReasons[r] || 0) + 1;
    }
  }
}

return [{
  json: {
    total:           items.length,
    valid:           validCount,
    invalid:         items.length - validCount,
    total_amount:    Math.round(totalAmount * 100) / 100,
    by_status:       byStatus,
    invalid_reasons: invalidReasons
  }
}];
```

</details>

---

## 測試用例

1. 確認 #001 的 name 是 `Alice Chen`（首字母大寫，trim 過）
2. 確認 #001 的 email 是 `alice@example.com`（全小寫）
3. 確認 #001 的 amount 是 `1200.50`（移除逗號，轉數字）
4. 確認 #002 的 `valid: false` 且 `invalid_reasons: ["invalid_email"]`
5. 確認 #005 的 `valid: false` 且 `invalid_reasons: ["duplicate_email"]`
6. 確認 summary 的 `total_amount` 是 `4200.50`（只加有效金額）

---

## 自我檢核

- [ ] 能用正則驗證 email 格式
- [ ] 能偵測多種日期格式並統一輸出
- [ ] 用 `Set` 做 email 去重檢查
- [ ] 能在一個 Code Node 裡同時做清洗 + 標記，在另一個做聚合
- [ ] 最終輸出同時包含明細 items 和 summary item

恭喜完成 Part 4。接下來 Part 5 把 n8n 部署到生產環境。

→ [Ch 21 Docker Compose 完整部署 — Postgres + n8n + Nginx](./21-self-host-docker.md)
