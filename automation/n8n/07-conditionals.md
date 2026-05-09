# Ch 7 — 條件分支：If Node、Switch Node

> 目標：能讓 workflow 根據資料內容走不同路徑，用 If 做二選一、用 Switch 做多選一。

## 為什麼需要條件分支

到目前為止，workflow 是線性的：A → B → C，每個 item 都走一樣的路。

但真實世界裡你會碰到：

- 抓到的訂單狀態是 `paid` 就發出貨通知，是 `cancelled` 就發退款通知
- 天氣溫度超過 35 度才發高溫警告
- API 回應帶 `error` 欄位就走錯誤處理流程

這就需要分支。

---

## If Node：二選一

If Node 有兩個輸出：**true** 和 **false**。每個 item 根據條件被分配到其中一個輸出端口。

```
              ┌─▶ [true 路徑]  → 後續 node
[If Node] ───┤
              └─▶ [false 路徑] → 後續 node
```

### 設定 Condition

If Node 的 Parameters：

```
Condition:
  Value 1: {{ $json.status }}
  Operation: [Equal ▼]
  Value 2: paid
```

常用 Operation：

| Operation | 用途 |
|---|---|
| Equal / Not Equal | 字串或數字相等 |
| Contains / Not Contains | 字串包含 |
| Starts With / Ends With | 字串開頭/結尾 |
| Greater Than / Less Than | 數字比較 |
| Is Empty / Is Not Empty | 欄位是否為空 |
| Regex Match | 正規表達式 |
| Is True / Is False | 布林值 |

### 多個條件：AND / OR

If Node 支援多個條件組合：

```
Condition 1: {{ $json.status }} Equal "paid"
AND
Condition 2: {{ $json.amount }} Greater Than 1000
```

操作方式：點「Add Condition」，然後選 AND 或 OR 連結。

### 範例：根據訂單狀態分流

```
[Webhook] → [If: status == "paid"] → true  → [Send Shipping Email]
                                   → false → [Send Cancellation Email]
```

If Node 設定：
```
Value 1: {{ $json.body.status }}
Operation: Equal
Value 2: paid
```

true 輸出端口接 Send Shipping Email，false 接 Send Cancellation Email。

---

## Switch Node：多選一

當你有三個以上的分支，Switch 比串一堆 If 好讀：

```
                ┌─▶ [paid]      → 發出貨通知
[Switch] ───────┼─▶ [pending]   → 發等待通知
                ├─▶ [cancelled] → 發退款通知
                └─▶ [default]   → 記錄異常狀態
```

### 設定 Switch

```
Mode: Rules

Rules:
  Output 0: {{ $json.status }} Equal "paid"
  Output 1: {{ $json.status }} Equal "pending"
  Output 2: {{ $json.status }} Equal "cancelled"

Fallback Output: Last Output  (當沒有規則命中時)
```

每個規則對應一個輸出端口（編號從 0 開始）。多個規則可以命中同一個 item（item 會被複製到多個輸出）或只命中第一個（預設行為）。

### Mode: Expression

Switch 還有另一種模式，直接用表達式決定輸出端口：

```
Mode: Expression
Output Index: {{ $json.priority - 1 }}
```

如果 `priority` 是 1、2、3，就對應到輸出端口 0、1、2。適合值是連續數字的情況。

---

## 常見踩雷

**If Node 的 Value 2 填數字卻沒命中**

注意 Expression 取出的值是 **字串**，`"30"` 不等於 `30`。改用：

```
Value 1: {{ $json.temp }}
Operation: Greater Than
Value 2: 30
```

n8n 的 Greater Than 會自動轉型做數字比較。或者你在 Code Node 先 `parseInt(item.json.temp)`。

**分支後兩條路的資料要合回來**

If 分出去的兩條路最後要合流，用 **Merge** node（Ch 9 會說）。如果不合流，就是兩條路各自通往不同的終點。

**Switch 的 default 端口**

`Fallback Output: None` 表示沒有規則命中的 item 直接被丟棄，不輸出。`Last Output` 表示送到最後一個端口。根據需求選。

---

## 動手練習

建一個 workflow：

1. **Code Node**（製造測試資料）
```javascript
return [
  { json: { order_id: 1, status: "paid",      amount: 500  } },
  { json: { order_id: 2, status: "pending",   amount: 200  } },
  { json: { order_id: 3, status: "cancelled", amount: 1500 } },
];
```

2. **Switch Node**：根據 `status` 分三條路

3. 每條路接一個 **Set Node**（下一章說明），設定一個 `message` 欄位：
   - paid → `"訂單 #{{$json.order_id}} 已付款，準備出貨"`
   - pending → `"訂單 #{{$json.order_id}} 等待付款"`
   - cancelled → `"訂單 #{{$json.order_id}} 已取消"`

執行後，在 Execution Log 確認三個 item 分別流向了不同的輸出端口。

## 自我檢核

- [ ] 能設定 If Node 的條件（Equal、Contains、Greater Than）
- [ ] 能設定 Switch Node 的多條規則
- [ ] 知道 If Node true/false 兩個輸出的差異
- [ ] 知道數字比較時的型別問題

→ [Ch 8 迴圈與批次 — Loop Over Items、Split In Batches](./08-loops-batches.md)
