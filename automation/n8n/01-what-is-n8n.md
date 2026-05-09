# Ch 1 — n8n 是什麼，為什麼選它

> 目標：說清楚 n8n 解決什麼問題、它和 Zapier/Make 的差在哪、你學完後能做到什麼。

## 自動化工具在解決什麼問題

你每天可能在做這些事：

- 收到 email 附件 → 手動存到 Google Drive → 手動通知同事
- 爬完一個網頁 → 複製貼上到試算表 → 手動寄出報告
- 客戶填了表單 → 手動建 CRM 記錄 → 手動發歡迎信

這些全都是「A 發生了 → 做 B → 做 C」的模式。把這個模式寫死在一個可以重複執行的流程裡，就是工作流自動化（Workflow Automation）的本質。

n8n 讓你用視覺化拖拉的方式描述這個流程，然後幫你跑它。

## n8n 是什麼

```
使用者拖拉 → [Workflow Canvas] → n8n 執行引擎 → 呼叫外部 API / 資料庫 / 服務
```

n8n（發音：「n-eight-n」或「nodemation」）是一個**開源**的工作流自動化平台。核心概念很簡單：

- **Node（節點）**：每個節點代表一個動作，例如「發 HTTP 請求」、「寄 email」、「寫入資料庫」
- **Connection（連線）**：節點之間的箭頭，表示資料流向
- **Workflow（工作流）**：節點 + 連線組成的整張圖，代表一個完整的自動化任務
- **Execution（執行）**：工作流被觸發、跑完一次的過程

你在畫布上把節點連起來，設定好觸發條件，n8n 就會在對的時間自動把整個流程跑一遍。

## 它和 Zapier、Make 差在哪

| 維度 | Zapier | Make（前身 Integromat） | n8n |
|---|---|---|---|
| 定價模式 | 按 task 數收費 | 按 operation 數收費 | Self-host 免費；雲端有付費方案 |
| 資料留在哪 | Zapier 伺服器 | Make 伺服器 | 你自己的機器（self-host）|
| 可程式化程度 | 幾乎不能寫 code | 有限 | Code Node 跑完整 JavaScript |
| 複雜流程支援 | 差 | 中等 | 好（分支、迴圈、子 workflow）|
| 開源 | 否 | 否 | 是（Apache 2.0）|
| 學習曲線 | 最低 | 低 | 中（因為功能最強）|

結論：**如果你的需求很簡單，Zapier 就夠用**。但只要你碰到以下任一情況，就該考慮 n8n：

- 資料敏感，不想放在別人伺服器
- 邏輯複雜，需要寫 code
- 月費扛不住（Zapier 高用量很貴）
- 想完全掌控部署環境

## n8n 的架構長什麼樣

```
┌──────────────────────────────────────────────┐
│                  n8n 核心                     │
│                                              │
│  ┌──────────┐    ┌──────────┐    ┌────────┐  │
│  │ Workflow │    │Execution │    │  Queue │  │
│  │  Engine  │───▶│  Runner  │───▶│ (可選) │  │
│  └──────────┘    └──────────┘    └────────┘  │
│        │                                      │
│  ┌─────▼──────┐    ┌──────────────────────┐  │
│  │  Database  │    │   Node Registry      │  │
│  │(SQLite /   │    │ (400+ built-in nodes) │  │
│  │ Postgres)  │    └──────────────────────┘  │
│  └────────────┘                              │
└──────────────────────────────────────────────┘
         │                    │
         ▼                    ▼
   存 workflow 定義      呼叫外部服務
   + execution log      (HTTP / DB / API)
```

你需要知道的：

- n8n 本身就是一個 Node.js 服務，負責排程、觸發、執行 workflow
- Workflow 定義（你畫的那張圖）存在資料庫裡（預設 SQLite，生產用 Postgres）
- 每次執行的結果也存在資料庫，方便你事後查
- 400+ 內建 node 涵蓋 Slack、GitHub、Google Sheets、Postgres、Stripe 等常見服務

## 學完這套教材你能做到什麼

按 Part 走完後，你會具備：

1. **Part 1–2**：能獨立建一個有觸發條件、分支、迴圈、錯誤處理的完整 workflow
2. **Part 3**：能把任意有 REST API 的服務串進來，不受限於內建 node
3. **Part 4**：能在 workflow 裡寫 JavaScript 處理複雜邏輯，把重複的 workflow 拆成可複用的模組
4. **Part 5**：能把 n8n 部署在自己的 VPS 上，設好權限和備份策略
5. **Part 6**：能把 GPT / Claude 等 LLM 接進 workflow，做出能自主執行任務的 AI Agent

最終你會有一套自己架的自動化系統，不再每月付 Zapier 帳單。

## 動手練習

現在先不裝任何東西。做一件事：

1. 打開 https://n8n.io/workflows
2. 隨便點三個看起來有趣的 workflow template
3. 觀察每個 template 的節點組成：有幾個 node？觸發條件是什麼？

不用看懂細節，感受一下「一張圖描述一個自動化任務」長什麼樣就夠了。

## 自我檢核

- [ ] 說得出 n8n 和 Zapier 的三個核心差異
- [ ] 知道 Workflow / Node / Connection / Execution 各指什麼
- [ ] 清楚 n8n 的架構裡哪個元件負責執行、哪個負責存資料

下一章把環境搭起來，第一次跑 n8n。

→ [Ch 2 環境搭建 — 雲端試玩 + 本地 Docker](./02-environment-setup.md)
