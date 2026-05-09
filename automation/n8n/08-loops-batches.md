# Ch 8 — 迴圈與批次：Loop Over Items、Split In Batches

> 目標：知道什麼時候 n8n 的自動迭代不夠用，以及如何手動控制迴圈和批次大小。

## n8n 的自動迭代

Ch 5 說過：一個 node 收到 N 個 item，**自動對每個 item 執行一次**。

這個自動迭代在大多數情況下就夠了，你不需要手動寫迴圈。

但有兩個例外：

1. **需要在一次執行裡循環直到條件滿足**（例：不斷輪詢 API 直到任務完成）
2. **輸入太多 item，需要分批送給限速的 API**（例：每次最多送 10 個 item）

這兩種情況分別用 Loop Over Items 和 Split In Batches。

---

## Loop Over Items：主動迴圈

這個 node 讓你建立一個**明確的迴圈**，把一批 item 依序處理，每次循環可以帶著上一次的結果繼續跑。

```
[Loop Over Items]
       │
       ├─▶ Loop Output（每次迭代，一個 item）
       │         │
       │    [你的處理邏輯]
       │         │
       └─────────┘（迴圈回去，直到所有 item 跑完）
       │
       └─▶ Done Output（全部跑完後繼續）
```

### 何時用 Loop Over Items

最典型的場景：你有一個 API，一次只接受一個請求，而且你需要等上一個請求完成才能發下一個（不能並行）。

n8n 的自動迭代是**並行**的，Loop Over Items 可以讓你改成**循序**（sequential）處理。

### 設定

```
Batch Size: 1      ← 每次送幾個 item 進迴圈
```

`Batch Size` 設 1 就是一次一個；設 5 就是一次五個。

### 完整範例：循序呼叫 API

```
[Code Node] → [Loop Over Items] → [HTTP Request: 呼叫限速 API] → (回到 Loop)
                    │（Done）
                    ▼
               [後續處理]
```

Code Node 輸出 10 個 item，Loop Over Items 的 Batch Size 設 1，就能確保 10 個 HTTP 請求一個一個依序發出。

---

## Split In Batches：分批處理

Split In Batches 比 Loop Over Items 更簡單，它只做一件事：**把大量 item 切成小批次**。

```
[100 個 items 進來]
       │
[Split In Batches]  Batch Size: 10
       │
[一次輸出 10 個 items]
[跑完這 10 個，再輸出下 10 個]
[...共跑 10 次]
```

### 設定

```
Batch Size: 10
```

### 何時用 Split In Batches

你在打一個有 Rate Limit 的 API，一次最多 10 個請求。你有 100 筆資料，直接送會被限速 reject。

```
[Code Node: 製造 100 個 items]
       │
[Split In Batches: 10]
       │
[HTTP Request: 批次 API（每次接受 10 筆）]
```

---

## 兩者的差異

| | Loop Over Items | Split In Batches |
|---|---|---|
| 主要用途 | 建立明確迴圈，支援循序執行 | 把大批 items 切成小批次 |
| 回圈控制 | 可帶狀態（前一次結果可影響下次）| 純切割，無狀態 |
| 複雜度 | 較高，有兩個輸出端口 | 簡單 |
| 典型場景 | 輪詢、依序執行 | 批次 API 呼叫 |

大多數情況，先考慮 n8n 的自動迭代夠不夠用。不夠再考慮 Split In Batches，最後才用 Loop Over Items。

---

## 輪詢模式（等待外部任務完成）

有時你需要：送出一個非同步任務 → 等它完成 → 繼續處理結果。

```
[送出任務] → [Loop Over Items] → [查詢任務狀態]
                  │                    │
                  │     status == "running"
                  │◀────────────────────┘
                  │
                  │     status == "done"
                  └─▶ [繼續處理]
```

在迴圈裡的 If Node：

- 狀態是 `running` → 接回 Loop Over Items 的 loop output（繼續迴圈）
- 狀態是 `done` → 接到 Loop Over Items 的 done output（結束）

如果需要在每次輪詢之間等幾秒，接上 **Wait Node**（Ch 20 說明）。

---

## 踩雷

**Loop Over Items 跑無限迴圈**

最常見的原因是 loop output 接了一條線但 done output 沒接，workflow 會一直跑。確認 If Node 正確地在「完成條件」達成時輸出到 done output。

**Split In Batches 後 items 順序亂掉**

Split In Batches 本身不保證輸出順序，如果你在意順序，在前面先用 Sort node 排好。

---

## 自我檢核

- [ ] 知道什麼情況下需要 Loop Over Items 而不是讓 n8n 自動迭代
- [ ] 能設定 Split In Batches 的批次大小
- [ ] 說得出 Loop Over Items 的 Loop Output 和 Done Output 各自的意義
- [ ] 知道輪詢模式的 workflow 結構

→ [Ch 9 資料轉換 — Set、Edit Fields、Merge、Remove Duplicates](./09-data-transform.md)
