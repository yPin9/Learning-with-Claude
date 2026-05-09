# Ch 19 — 子工作流（Sub-workflow）與模組化設計

> 目標：能把重複使用的邏輯抽成獨立 workflow，用 Execute Workflow node 呼叫，讓主 workflow 保持簡潔。

## 為什麼需要 Sub-workflow

你在多個 workflow 裡都需要「把使用者資料寫到 Postgres 再發 Telegram 通知」這個流程。

**壞做法**：複製貼上那 5 個 node 到每個 workflow 裡。之後改邏輯要改 N 個地方。

**好做法**：把這個流程抽成一個獨立的 Sub-workflow，其他 workflow 用 Execute Workflow node 呼叫它。

---

## Execute Workflow Node

這個 node 呼叫另一個 workflow，傳入資料，等它跑完，取回結果。

```
[主 Workflow]
     │
[Execute Workflow]
  Workflow: "通知 + 寫 DB"
  Input:    {{ $json }}
     │
[繼續用 sub-workflow 的輸出]
```

---

## 建立 Sub-workflow

Sub-workflow 的結構和一般 workflow 一樣，但 Trigger 不同：

1. 新建一個 workflow，命名為「寫 DB + 發通知」
2. Trigger 改用 **Execute Workflow Trigger**（不是 Manual、Schedule、Webhook）

```
[Execute Workflow Trigger]
         │
[Postgres: INSERT INTO events ...]
         │
[Telegram: 發通知]
         │
[輸出結果]（自動回傳給呼叫者）
```

Execute Workflow Trigger 接到的 input 就是呼叫者傳進來的 item。

---

## 主 Workflow 呼叫 Sub-workflow

在主 workflow 加 **Execute Workflow** node：

```
Source:           Database
Workflow:         [選「寫 DB + 發通知」]
Wait for Workflow to Complete: ✅
Input Data:       {{ $json }}（或特定欄位）
```

勾選「Wait for Workflow to Complete」：主 workflow 會等 sub-workflow 跑完，然後拿到它的輸出繼續往下。

不勾選（Fire and Forget）：主 workflow 不等結果，sub-workflow 在背景跑。適合你不需要它的輸出、又不想讓主流程卡住的情況。

---

## 傳入參數

Execute Workflow node 的 Input Data 可以傳任意 JSON：

```json
{
  "user_id":  "{{ $json.user_id }}",
  "event":    "signup",
  "metadata": {
    "source":    "{{ $json.source }}",
    "timestamp": "{{ new Date().toISOString() }}"
  }
}
```

Sub-workflow 裡用 `$input.first().json.user_id` 取到這些值。

---

## 模組化設計的幾個模式

### 模式 1：工具型 Sub-workflow

做一件具體的事，輸入一個 item，回傳處理結果。

範例：「把 Markdown 轉成 Telegram 格式的 HTML」

```
Input:  { "markdown": "**Hello** _world_" }
Output: { "html": "<b>Hello</b> <i>world</i>" }
```

### 模式 2：通知型 Sub-workflow

接收 alert 資訊，同時發 Telegram + Slack + Email。主 workflow 只需要呼叫一次。

```
Input:  { "level": "critical", "message": "DB 連線失敗" }
```

Sub-workflow 根據 `level` 決定發哪些管道（If Node：critical 發 Telegram + Email；warning 只發 Slack）。

### 模式 3：資料寫入 Sub-workflow

統一負責寫資料（防止多個 workflow 各自寫、邏輯不一致）：

```
Input:  { "table": "events", "data": {...} }
```

Sub-workflow 裡做資料驗證、格式化、再 INSERT。

---

## 版本管理問題

**注意**：修改 Sub-workflow 會立即影響所有呼叫它的 workflow。這是雙面刃：

- 好處：改一個地方，全部生效
- 壞處：改壞了，全部壞

建議：

1. 在 sub-workflow 裡加 Notes 說明版本和介面規格（輸入/輸出欄位）
2. 有重大改動前，先建一個新 workflow 測試，沒問題再換掉舊的

---

## 動手練習

把練習 A 裡的「發 Telegram 錯誤通知」抽成一個 Sub-workflow：

1. 建新 workflow「錯誤通知」，用 Execute Workflow Trigger
2. 接收 `{ "workflow_name": "...", "error_message": "..." }`
3. 組成 Telegram 訊息發出
4. 修改原本的 Error Trigger workflow，改用 Execute Workflow node 呼叫它

這樣你以後所有 workflow 的 Error Workflow 都指向同一個地方，通知格式統一。

## 自我檢核

- [ ] 知道 Sub-workflow 用 Execute Workflow Trigger 而不是一般 Trigger
- [ ] 能設定 Execute Workflow node 傳入參數和等待結果
- [ ] 知道「Fire and Forget」模式和「Wait for Completion」的差異
- [ ] 能設計至少一個「工具型 Sub-workflow」

→ [Ch 20 執行控制 — Wait、Respond to Webhook、並行執行](./20-execution-control.md)
