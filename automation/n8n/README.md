# n8n 學習筆記：從零建立個人自動化中樞

> 給完全沒碰過工作流自動化、想用 n8n 串接各種服務的工程師。

n8n 是一個開源的視覺化工作流自動化工具。這套教材從最基礎的介面操作出發，逐步涵蓋資料轉換、API 整合、Code Node、Self-host 部署，最後把 LLM Agent 也接進來。學完後你會有一套跑在自己機器上的自動化系統，不再依賴 Zapier 的月費上限。

## 為什麼學這個？

- **自動化就是槓桿**：一個 workflow 抵過每天重複的手動作業，而且 24 小時跑不停。
- **Self-host，資料不外流**：比 Zapier/Make 多一個選項 — 敏感資料留在自己的伺服器。
- **可程式化的自動化**：Code Node 讓你在視覺流程裡插 JavaScript，複雜邏輯不用妥協。

## 課程地圖

### Part 1 — 認識 n8n
- [Ch 1 n8n 是什麼，為什麼選它](./01-what-is-n8n.md)
- [Ch 2 環境搭建 — 雲端試玩 + 本地 Docker](./02-environment-setup.md)
- [Ch 3 介面導覽 — Canvas、Node、Connection、Execution Log](./03-ui-tour.md)
- [Ch 4 第一個 Workflow — 每天早上自動抓天氣發通知](./04-first-workflow.md)

### Part 2 — 核心概念
- [Ch 5 Data Model — Item、JSON 結構、Binary Data](./05-data-model.md)
- [Ch 6 Trigger 全覽 — Schedule / Webhook / Manual / App](./06-triggers.md)
- [Ch 7 條件分支 — If Node、Switch Node](./07-conditionals.md)
- [Ch 8 迴圈與批次 — Loop Over Items、Split In Batches](./08-loops-batches.md)
- [Ch 9 資料轉換 — Set、Edit Fields、Merge、Remove Duplicates](./09-data-transform.md)
- [Ch 10 錯誤處理 — Error Trigger、Retry、Continue On Fail](./10-error-handling.md)
- [練習 A：資料聚合 Workflow](./practice-a-data-aggregation.md)

### Part 3 — 整合常用服務
- [Ch 11 HTTP Request — 呼叫任意 REST API](./11-http-request.md)
- [Ch 12 Webhook — 接收外部觸發、回應結果](./12-webhook.md)
- [Ch 13 資料庫整合 — Postgres / MySQL / SQLite](./13-database.md)
- [Ch 14 通訊整合 — Email / Telegram / Slack / Discord](./14-messaging.md)
- [Ch 15 雲端文件 — Google Sheets、Notion、Airtable](./15-cloud-docs.md)
- [Ch 16 OAuth 2.0 實戰 — Credentials 管理與 Token 刷新](./16-oauth.md)
- [練習 B：多服務 Pipeline](./practice-b-multi-service-pipeline.md)

### Part 4 — Code Node 與進階邏輯
- [Ch 17 Code Node 基礎 — 在 n8n 裡寫 JavaScript](./17-code-node-basics.md)
- [Ch 18 資料操作進階 — 正規化、聚合、複雜轉換](./18-advanced-data-ops.md)
- [Ch 19 子工作流（Sub-workflow）與模組化設計](./19-sub-workflows.md)
- [Ch 20 執行控制 — Wait、Respond to Webhook、並行執行](./20-execution-control.md)
- [練習 C：Code Node 挑戰](./practice-c-code-node-challenge.md)

### Part 5 — Self-Host 與生產部署
- [Ch 21 Docker Compose 完整部署 — Postgres + n8n + Nginx](./21-self-host-docker.md)
- [Ch 22 環境變數、Secrets、使用者權限管理](./22-secrets-permissions.md)
- [Ch 23 監控、日誌、備份與版本升級](./23-monitoring-backup.md)

### Part 6 — AI Agent 整合
- [Ch 24 AI Agent Node — 把 LLM 接進 Workflow](./24-ai-agent-node.md)
- [Ch 25 AI 工具鏈實戰 — RAG、Memory、Tool Calling](./25-ai-toolchain.md)
- [Final Project：個人自動化中樞](./final-project-automation-hub.md)

## 學習方式建議

1. **邊讀邊開 n8n**：每章都有可以直接貼進去跑的 workflow 設定，不要只看截圖。
2. **故意弄壞再修**：把 trigger 停掉、把 API key 填錯，看 Execution Log 怎麼報錯 — 這比讀文件快十倍。
3. **查官方 integrations 頁**：n8n 有 400+ 內建 node，遇到新服務先搜再自己刻 HTTP Request。

## 參考資料

- 官方文件：https://docs.n8n.io
- 官方 workflow 範例庫：https://n8n.io/workflows
- n8n GitHub：https://github.com/n8n-io/n8n
