# Ch 5 — Data Model：Item、JSON 結構、Binary Data

> 目標：搞懂 n8n 在節點之間傳的資料長什麼樣，以及為什麼「每個 node 跑多次」這件事是設計而不是 bug。

## n8n 資料的基本單位：Item

n8n 節點之間傳遞的資料是一個 **Item 陣列**。每個 Item 是一筆獨立的資料記錄，結構長這樣：

```json
{
  "json": {
    "name": "Alice",
    "age": 30,
    "email": "alice@example.com"
  },
  "binary": {
    "data": { ... }
  }
}
```

`json` 是你放業務資料的地方。`binary` 是放檔案、圖片等二進位資料的地方（後面說）。

---

## 一個 Node 收到多個 Item

這是 n8n 最容易搞混的地方：

**當上一個 node 輸出 N 個 item，下一個 node 預設會對每個 item 各執行一次。**

```
[HTTP Request] → 輸出 1 個 item（整個 JSON response）

[Code Node 把它拆成 3 個 item]

[Telegram Node] → 對 3 個 item 各跑一次 → 發了 3 則訊息
```

來看具體例子。假設 Code Node 的 input 是：

```json
[
  { "json": { "city": "Taipei", "temp": 30 } },
  { "json": { "city": "Tainan", "temp": 33 } },
  { "json": { "city": "Kaohsiung", "temp": 34 } }
]
```

接在後面的 Telegram node 會自動跑三次，分別發三則訊息。你**不需要**自己寫迴圈，n8n 幫你迭代。

這個設計讓大多數操作自然批次化，但也意味著你要清楚「現在有幾個 item」。

---

## 在 Expression 裡存取 Item

在 node 的 Parameters 裡，按 `=` 圖示切換到 Expression 模式，用以下語法存取資料：

```
當前 item：
  {{ $json.fieldName }}
  {{ $json["field-with-dash"] }}

前一個 node（任意 item）：
  {{ $('Node Name').item.json.fieldName }}

第一個 item：
  {{ $('Node Name').first().json.fieldName }}

所有 item（回傳陣列）：
  {{ $('Node Name').all().map(i => i.json.fieldName) }}

目前是第幾個 item（從 0 起）：
  {{ $itemIndex }}
```

範例：Telegram Node 的 Text 欄位填：

```
城市：{{ $json.city }}，溫度：{{ $json.temp }}°C
```

如果 input 有 3 個 item，這個 node 會跑 3 次，每次 `$json.city` 和 `$json.temp` 是對應那個 item 的值。

---

## Code Node 裡的 Item 操作

在 Code Node 裡，用 `$input` 存取輸入：

```javascript
// 取得所有 input items
const items = $input.all();

// 取得第一個 item 的 json
const first = $input.first().json;

// 取得目前迭代的 item（「Run Once Per Item」模式）
const current = $input.item.json;
```

Code Node 有兩種執行模式，在右側 Parameters 選：

| 模式 | 說明 |
|---|---|
| **Run Once for All Items** | 整批 items 進來，你自己控制迭代 |
| **Run Once for Each Item** | 每個 item 分別執行一次，用 `$input.item` |

大多數情況用「Run Once for All Items」比較直覺：

```javascript
// Run Once for All Items 模式
const items = $input.all();

return items.map(item => {
  const data = item.json;
  return {
    json: {
      summary: `${data.city}: ${data.temp}°C`,
      original: data
    }
  };
});
```

**回傳格式必須是 `[{ json: {...} }, ...]` 的陣列**，否則 n8n 不認識。

---

## Binary Data

某些 node 會輸出二進位資料，例如：

- **Read Binary File**：讀取本地檔案
- **HTTP Request**（下載模式）：抓圖片或 PDF
- **Gmail**：附件

Binary Data 存在 item 的 `binary` 欄位下，每個 key 是一個具名的二進位資料：

```json
{
  "json": { "filename": "report.pdf" },
  "binary": {
    "data": {
      "mimeType": "application/pdf",
      "fileName": "report.pdf",
      "data": "base64encodedstring..."
    }
  }
}
```

在 Expression 存取：

```
{{ $binary.data.fileName }}
{{ $binary.data.mimeType }}
```

實際處理 binary 通常用內建 node（上傳 Google Drive、發 Telegram 附件）而不是自己解 base64。

---

## 常見踩雷

**踩雷一：把 item 和 JSON 搞混**

`$json` 是當前 item 的 `json` 欄位，不是整個 item。`$json.name` 等同於 `$input.item.json.name`。

**踩雷二：忘記 Code Node 的回傳格式**

錯的：
```javascript
return { name: "Alice" };         // 不是陣列，也沒有 json 包裹
```

對的：
```javascript
return [{ json: { name: "Alice" } }];
```

**踩雷三：誤以為 node 只跑一次**

如果前一個 node 輸出 100 個 item，後面的 node 會跑 100 次。Telegram 會發 100 則訊息。想避免這個，用 **Aggregate** node 先把所有 item 合成一個，或在 Code Node 自己合。

---

## 動手練習

在 Code Node 裡寫這段，看輸出：

```javascript
// 手動製造 3 個 item
return [
  { json: { city: "Taipei",     temp: 30 } },
  { json: { city: "Tainan",     temp: 33 } },
  { json: { city: "Kaohsiung",  temp: 34 } }
];
```

接上第二個 Code Node（Run Once for Each Item 模式）：

```javascript
const { city, temp } = $input.item.json;
return [{ json: { message: `${city} 現在 ${temp}°C` } }];
```

觀察第二個 node 的執行：它被呼叫了幾次？每次的輸入是什麼？

## 自我檢核

- [ ] 知道 n8n Item 的結構（json + binary）
- [ ] 說得出「node 對每個 item 各執行一次」的含義
- [ ] 能在 Expression 裡用 `$json.field` 取值
- [ ] Code Node 回傳格式是 `[{ json: {...} }]` 陣列
- [ ] 知道 Run Once for All Items 和 Run Once for Each Item 的差異

→ [Ch 6 Trigger 全覽 — Schedule / Webhook / Manual / App](./06-triggers.md)
