# Ch 17 — Code Node 基礎：在 n8n 裡寫 JavaScript

> 目標：能在 Code Node 裡用 JavaScript 讀取/轉換/產生 items，理解執行環境的限制與可用工具。

## 為什麼需要 Code Node

n8n 的內建 node 很強，但有些事它們做不到：

- 複雜的字串解析（正則、分割、拼接）
- 數學計算（平均值、百分比）
- 陣列操作（排序、去重、分組）
- 呼叫 Node.js 內建模組（`crypto`、`url`）
- 業務邏輯（多個條件組合的判斷）

Code Node 讓你在 workflow 裡插入任意 JavaScript，補足這些缺口。

---

## Code Node 的兩種執行模式

在 Parameters 選 **Mode**：

```
Run Once for All Items   ← 整批 items 一起進來，你完全控制迭代
Run Once for Each Item   ← 每個 item 分別執行，用 $input.item 取當前 item
```

**什麼時候用哪種？**

- 需要在 items 之間做操作（統計、排序、分組）→ All Items
- 對每個 item 做獨立的轉換 → Each Item（或 All Items + map 也行）

---

## All Items 模式

```javascript
// 取得所有 input items
const items = $input.all();

// 對每個 item 做處理
const result = items.map(item => {
  const d = item.json;
  return {
    json: {
      id:       d.id,
      fullName: `${d.first_name} ${d.last_name}`,
      score:    Math.round(d.raw_score * 100) / 100
    }
  };
});

return result;
```

**回傳值必須是 `[{ json: {...} }, ...]` 格式**，否則 n8n 無法識別。

---

## Each Item 模式

```javascript
// Run Once for Each Item 模式
const d = $input.item.json;

return {
  json: {
    name:  d.name.trim().toUpperCase(),
    valid: d.email.includes('@')
  }
};
```

Each Item 模式回傳**單個物件**（不是陣列），n8n 自動把它包成 item。

---

## 可用的全域變數

| 變數 | 說明 |
|---|---|
| `$input` | 輸入資料存取 |
| `$input.all()` | 所有 input items |
| `$input.first()` | 第一個 input item |
| `$input.last()` | 最後一個 input item |
| `$input.item` | 當前 item（Each Item 模式）|
| `$('Node Name').all()` | 指定 node 的所有輸出 items |
| `$('Node Name').first()` | 指定 node 的第一個輸出 item |
| `$json` | 當前 item 的 json（同 `$input.item.json`，Each Item 模式）|
| `$itemIndex` | 目前 item 的索引（從 0 開始）|
| `$runIndex` | 目前執行批次索引 |
| `$workflow` | workflow 的 id 和 name |
| `$execution` | 本次執行的 id 和 mode |
| `$now` | 當前時間（Luxon DateTime 物件）|
| `$today` | 今天日期（Luxon DateTime）|
| `$env` | 環境變數（Ch 22 說明）|

---

## 可用的 Node.js 模組

Code Node 不能用 `require()` 載入 npm 套件，但 n8n 預先提供了幾個常用模組：

```javascript
// 日期時間
const { DateTime } = require('luxon');
const now = DateTime.now().setZone('Asia/Taipei');
const formatted = now.toFormat('yyyy-MM-dd HH:mm');

// 加密
const crypto = require('crypto');
const hash = crypto.createHmac('sha256', 'secret').update('message').digest('hex');

// URL
const url = require('url');
const parsed = new URL('https://example.com/path?foo=bar');
const foo = parsed.searchParams.get('foo');

// 工具函式庫（n8n 內建）
const _ = require('lodash');
const grouped = _.groupBy(items, i => i.json.category);
```

內建可用的模組完整列表：`crypto`, `url`, `querystring`, `string_decoder`, `stream`, `buffer`, `path`, `os`, `util` 以及 `lodash`, `moment-timezone`, `luxon`, `xml2js`。

---

## 常見操作模式

### 過濾

```javascript
const items = $input.all();
return items.filter(i => i.json.score >= 80);
```

### 分組統計

```javascript
const items = $input.all();
const stats = {};

for (const item of items) {
  const cat = item.json.category;
  if (!stats[cat]) stats[cat] = { category: cat, count: 0, total: 0 };
  stats[cat].count++;
  stats[cat].total += item.json.amount;
}

return Object.values(stats).map(s => ({
  json: { ...s, avg: s.total / s.count }
}));
```

### 合併多個 node 的資料

```javascript
const users  = $('User API').all().map(i => i.json);
const orders = $('Order API').all().map(i => i.json);

const userMap = Object.fromEntries(users.map(u => [u.id, u]));

return orders.map(order => ({
  json: {
    ...order,
    user_email: userMap[order.user_id]?.email ?? 'unknown'
  }
}));
```

---

## 錯誤處理

在 Code Node 裡用 `try/catch`：

```javascript
const items = $input.all();

return items.map(item => {
  try {
    const d = item.json;
    const parsed = JSON.parse(d.raw_json);
    return { json: { ...d, parsed } };
  } catch (e) {
    return { json: { ...item.json, parseError: e.message } };
  }
});
```

或直接 throw，讓 n8n 的錯誤處理機制接管：

```javascript
if (!$input.first().json.required_field) {
  throw new Error('缺少 required_field，無法繼續處理');
}
```

---

## 踩雷

**「Cannot read properties of undefined」**

通常是 `$input.first()` 在輸入是空陣列時回 undefined，再取 `.json` 就炸。先加 guard：

```javascript
if ($input.all().length === 0) return [];
```

**不能用 async/await**

Code Node 的執行環境不支援非同步，`fetch`、`axios` 都不能用。要呼叫 API，用 HTTP Request node，不要在 Code Node 裡做。

**修改 item 要回傳新物件**

n8n 的 item 是不可變的，直接修改 `item.json.xxx = ...` 不會作用。要回傳新的 item：

```javascript
// 錯的
items[0].json.name = 'Alice';
return items;

// 對的
return items.map(i => ({ json: { ...i.json, name: 'Alice' } }));
```

---

## 自我檢核

- [ ] 能在 All Items 模式裡用 `$input.all()` + `map` 轉換 items
- [ ] 知道 Code Node 回傳格式必須是 `[{ json: {...} }]`
- [ ] 能用 `$('Node Name').all()` 存取指定 node 的輸出
- [ ] 知道能用 `require('luxon')` 和 `require('crypto')` 但不能用一般 npm 套件
- [ ] 知道為什麼不能在 Code Node 裡做 HTTP 請求

→ [Ch 18 資料操作進階 — 正規化、聚合、複雜轉換](./18-advanced-data-ops.md)
