# Ch 18 — 資料操作進階：正規化、聚合、複雜轉換

> 目標：能處理現實世界的亂七八糟 JSON：深層巢狀存取、陣列展平、資料正規化、統計聚合。

## 現實世界的 API 回應長什麼樣

教科書範例的 JSON 都很平整：`{ "name": "Alice", "age": 30 }`。

真實 API 回的是這種：

```json
{
  "status": "ok",
  "data": {
    "users": [
      {
        "id": 1,
        "profile": {
          "name": { "first": "Alice", "last": "Chen" },
          "contact": { "email": "alice@example.com", "phones": ["0912345678", "0223456789"] }
        },
        "tags": [{"id": 10, "name": "VIP"}, {"id": 20, "name": "Early Adopter"}],
        "metadata": null
      }
    ]
  }
}
```

要從這裡取到 `alice@example.com`，你要知道怎麼做。

---

## 深層路徑存取

### Expression 語法

```
{{ $json.data.users[0].profile.contact.email }}
```

n8n 的 Expression 支援：

- 點記法：`$json.a.b.c`
- 括號記法：`$json["a"]["b"]`（欄位名含空格或特殊字元時用）
- 陣列索引：`$json.items[0]`
- 陣列最後一個：`$json.items[-1]`（n8n 支援 Python 風格的負索引）

### Optional Chaining（避免 undefined 炸）

```
{{ $json?.data?.users?.[0]?.profile?.email ?? '無 email' }}
```

`?.` 遇到 null/undefined 會返回 undefined 而不是拋錯，`??` 提供預設值。

### Code Node 裡的深層存取

```javascript
const users = $input.first().json.data.users;

return users.map(user => ({
  json: {
    id:        user.id,
    fullName:  `${user.profile.name.first} ${user.profile.name.last}`,
    email:     user.profile.contact.email,
    phone:     user.profile.contact.phones[0] ?? null,
    tagNames:  user.tags.map(t => t.name).join(', '),
    isVIP:     user.tags.some(t => t.name === 'VIP')
  }
}));
```

---

## 展開巢狀陣列（Flatten）

API 回一個 item，裡面有個陣列，你想把陣列展開成多個 items：

```javascript
// Input: [{ json: { category: "books", items: [...] } }]
// Output: 每本書一個 item

const categories = $input.all();
const result = [];

for (const cat of categories) {
  for (const item of cat.json.items) {
    result.push({
      json: {
        category: cat.json.category,
        ...item
      }
    });
  }
}

return result;
```

或更函數式：

```javascript
return $input.all().flatMap(cat =>
  cat.json.items.map(item => ({
    json: { category: cat.json.category, ...item }
  }))
);
```

---

## 聚合（多個 items → 一個 item）

把多個 items 的資料收攏成統計結果：

```javascript
const items = $input.all();

// 總金額、筆數、平均
const total   = items.reduce((sum, i) => sum + i.json.amount, 0);
const count   = items.length;
const average = total / count;

// 按狀態分組計數
const byStatus = items.reduce((acc, i) => {
  const s = i.json.status;
  acc[s] = (acc[s] || 0) + 1;
  return acc;
}, {});

return [{
  json: {
    total,
    count,
    average: Math.round(average * 100) / 100,
    byStatus
  }
}];
```

也可以用 n8n 內建的 **Aggregate** node（不用寫 code）：

```
Aggregate: Input Fields
Field to Aggregate: amount
Aggregation: Sum / Average / Count / Min / Max
```

---

## 正規化（把不同格式統一）

整合多個 API 時，同一個概念可能有不同的欄位名和格式：

```javascript
// Source A: { "user_id": "123", "user_name": "Alice", "created": "2026-05-08T10:00:00Z" }
// Source B: { "id": "123", "name": "Alice", "createdAt": 1715000000 }

const items = $input.all();

return items.map(item => {
  const d = item.json;

  // 統一欄位名
  const id   = d.user_id  ?? d.id;
  const name = d.user_name ?? d.name;

  // 統一時間格式
  let createdAt;
  if (d.created) {
    createdAt = new Date(d.created).toISOString();
  } else if (d.createdAt) {
    createdAt = new Date(d.createdAt * 1000).toISOString();  // Unix timestamp
  }

  return { json: { id, name, createdAt } };
});
```

---

## 日期時間處理

```javascript
const { DateTime } = require('luxon');

// 解析各種格式
const dt1 = DateTime.fromISO('2026-05-08T10:00:00+08:00');
const dt2 = DateTime.fromFormat('08/05/2026', 'dd/MM/yyyy');
const dt3 = DateTime.fromSeconds(1715000000);

// 格式化
const formatted = dt1.setZone('Asia/Taipei').toFormat('yyyy年M月d日 HH:mm');

// 計算
const daysAgo = dt1.diffNow('days').days;           // 幾天前
const nextWeek = dt1.plus({ weeks: 1 }).toISO();    // 一週後

// 比較
const isRecent = dt1.diffNow('hours').hours > -24;  // 24 小時內
```

---

## 字串處理

```javascript
const d = $input.first().json;

// 清理
const cleaned = d.raw_text
  .trim()
  .replace(/\s+/g, ' ')       // 多餘空格合一
  .replace(/[^\w\s]/g, '');   // 移除特殊字元

// 擷取
const match = d.url.match(/\/product\/(\d+)/);
const productId = match ? match[1] : null;

// 截斷（避免太長）
const preview = d.content.length > 200
  ? d.content.slice(0, 200) + '...'
  : d.content;

return [{ json: { cleaned, productId, preview } }];
```

---

## 自我檢核

- [ ] 能從巢狀 JSON 取到任意深度的欄位
- [ ] 能用 `flatMap` 把一個含陣列的 item 展開成多個 items
- [ ] 能把多個 items 聚合成統計結果（total、count、groupBy）
- [ ] 能用 Luxon 做日期格式轉換和計算
- [ ] 知道 `?.` optional chaining 防止 undefined 錯誤

→ [Ch 19 子工作流（Sub-workflow）與模組化設計](./19-sub-workflows.md)
