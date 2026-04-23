# Ch 24 — 真實產品拆解

> 目標:看現實 LLM 產品的架構。不是完美工程,是怎麼權衡做出能 ship 的東西。

每個 case 拆開看:

1. **產品做什麼**
2. **關鍵架構決策**
3. **踩過什麼坑(公開的)**
4. **對你有啟發的設計原則**

---

## Case 1: Claude Code

### 做什麼

AI 寫 code / 修 code / 跑 code 的 CLI。能讀本地檔案、改 code、跑 shell、裝 MCP。

### 關鍵架構

- **核心 agent loop**:tool use style,ReAct-based
- **內建 tools**:Read / Write / Edit / Glob / Grep / Bash / WebSearch / WebFetch
- **Permission system**:四種 mode,細粒度 allow/deny
- **Skills / Subagents / Hooks**:extensibility 三件套
- **MCP**:外部工具整合
- **Memory**:CLAUDE.md + auto-memory
- **Plan mode**:強制 Plan-Execute 在高風險場景
- **Session state**:`~/.claude/sessions/`,可 resume

### 設計決策

**為什麼是 CLI 不是 GUI**?

- CLI 是 developer 的母語,門檻低
- 能接進既有工作流(tmux、IDE、scripts)
- 自動化友好(CI / cron / hooks)

**為什麼 tool permission 這麼細**?

- Agent 行為不可完全預測
- 不同 context 安全需求不同(production repo vs scratch)
- User 體驗:信任感分層

**為什麼有 plan mode**?

- 大重構 / 陌生 codebase 風險高
- 純 autonomous agent 走錯路成本大
- Human-in-the-loop 的 proven pattern

**為什麼是 Agent SDK 的底層**?

- 產品 / library 雙用途,內部一致
- 客戶要 build 自己的 agent,共享技術棧

### 對你的啟發

- **Agent 不能全自主**。Plan mode 是重要安全閥。
- **Extensibility 是 build retention 的手段**。Skills / hooks / MCP 讓 user 投入越多越難換。
- **一致性勝過 feature**:CLI 和 SDK 共享引擎,不分裂。

---

## Case 2: Cursor / Windsurf / Zed AI

### 做什麼

AI 優先的 code editor,在 IDE 內嵌 LLM-powered 編輯、chat、agent。

### 關鍵架構

- **VS Code fork(Cursor / Windsurf)或 native(Zed)**
- **多 model**:OpenAI、Anthropic、Google 切換
- **Composer / Cascade**(agent 模式):能跨多檔編輯
- **Context retrieval**:自動抓相關檔做 context
- **Tab completion**:次秒級延遲的 inline suggestion

### 設計決策

**為什麼 fork VS Code(不做 extension)**?

- 深度控制 UI(對話框、inline diff)
- 不受 VS Code extension API 限制
- 獨立 branding

**為什麼多 model**?

- Hedging:一家漲價 / 掛了有替代
- 不同 task 不同模型最佳(Haiku 做 autocomplete、Opus 做 agent)
- User preference

**Context retrieval 的挑戰**?

- 「哪些檔對當前 query 相關」是難題
- 太多 → 慢 + 貴,太少 → 答不出
- 用 codebase embedding + 當前檔 + 打開的 tab 組合

### 對你的啟發

- **UX 和 infra 一體**:好 agent 體驗需要整合 IDE,不是套 chatbot
- **Retrieval 是 code assistant 的勝負手**
- **Tab complete 和 chat 是兩個產品**,各需獨立優化

---

## Case 3: GitHub Copilot Workspace / Devin / Cognition

### 做什麼

給一個 GitHub issue,agent 自動寫 code、開 PR、甚至 iterate 到 CI pass。

### 關鍵架構

- **Long-running agent**:任務跑幾分鐘到幾小時
- **Sandbox 環境**:每個任務 spawn 隔離 VM/container
- **Planning + execution 分離**:先 plan(user 可 review)再做
- **可見的 trajectory**:user 能看 agent 正在做什麼
- **Checkpoint / rollback**:失敗能 resume

### 設計決策

**為什麼 sandbox 是必須**?

- Agent 要跑 build、install、test——不能在共享 infra
- 安全:agent 不能觸及 host

**為什麼 planning 要 user review**?

- Agent 在陌生 codebase 常誤會 user intent
- User 早期 intervene 成本遠低於事後修
- 建立信任——user 願意把權力交給看得到的 planner

**Cost economics**?

- 這類產品的每個「fix 一個 issue」動輒 $5–$50 的 LLM cost
- 只在「每個 PR 省開發者 1-2 小時」才經濟
- **定價與人力成本掛鉤**,不是「按 token」

### 對你的啟發

- **真正的 autonomous agent 需要 sandbox**
- **User-visible trajectory** 是 trust 的前提
- **LLM cost ≈ 傳統 CI cost 的幾十倍**,產品定位要想好

---

## Case 4: Perplexity / Phind / Brave Leo

### 做什麼

「AI 版 Google」:query → web search + LLM 摘要 + 引用。

### 關鍵架構

- **Real-time web search + LLM**
- **Source ranking**:選哪些頁面給 LLM 看
- **Citation 強制**:output 必須有 inline 引用 URL
- **Multi-turn**:可 follow-up,記住前輪 context
- **Focus mode**:限制 search 到學術、Reddit、news...

### 設計決策

**為什麼不用 RAG over own corpus**?

- Web 規模太大,自己 index 不 scale
- 資料時效性——搜尋引擎即時
- 合規:讓 user 看到來源,不是「根據神秘黑盒」

**Search + LLM 的 latency**?

- Search:1–3s
- LLM(with streaming):2–10s
- Total:3–13s,比 Google 慢很多
- User 願意等——因為答案直接,不用點連結

### 對你的啟發

- **RAG 不一定自己建**,接現有 search engine 也可
- **Citation 是信任**。強制輸出 source 讓用戶可驗證
- **比對手慢但答案好** → user 願意等

---

## Case 5: 客服 / SDR chatbot(Intercom Fin、Drift、公司內部)

### 做什麼

回客戶訊息、篩線索、自動預約會議、升級至 human。

### 關鍵架構

- **RAG over 公司文件**(FAQ、policy、product docs)
- **Deterministic workflow for 常見問題**
- **LLM agent for 長尾問題**
- **Clear escalation path**:confidence 低 / 客戶要求 / 敏感話題 → 轉人工
- **Ticket integration**:完整對話 log 給接手的客服
- **Conversion tracking**

### 設計決策

**Why workflow 為主 agent 為輔**?

- 客服問題 80% 是重複 FAQ 類 → workflow 快、便宜、可控
- 20% 長尾 → agent 解決,人工 bailout
- 成本:全 agent 一個月燒五位數;workflow 一半以下

**Escalation 標準**?

- 語氣偵測:怒氣值 > threshold → 轉人
- Topic:提到「refund」「legal」「complaint」立刻轉
- Confidence:LLM 自己說「I'm not sure」→ 轉
- User 明示:「I want to speak to a human」

**KPI 是什麼**?

- Auto-resolution rate(不用升級就解決的 %)
- Cost per conversation
- CSAT(客戶滿意度)
- Not 只是 response latency

### 對你的啟發

- **Escalation 設計跟主流程一樣重要**
- **商業 metric**(CSAT、cost)**才是 north star**,不是 LLM quality
- **Workflow + Agent 混合架構**是最常見的現實

---

## Case 6: AI 內部工具(公司自用 chatbot、分析助理)

### 做什麼

公司內部員工用:查 policy、問 HR、取 BI report、寫初稿。

### 關鍵架構

- **內部 RAG**(wiki、HR docs、slack 精華)
- **接 SSO / LDAP**:只有員工能用
- **接公司系統 via MCP**:Jira、Salesforce、DB
- **Privacy-sensitive**:不輸出到 Anthropic 以外

### 設計決策

**Self-hosted or API?**

- 敏感資料 → self-hosted(Llama / Qwen / etc)
- 一般用途 → Claude / GPT 的 zero-retention plan
- 混合:簡單 task 用 local,複雜用 Claude

**Multi-tenant?**

- 員工各看各資料(per-role retrieval filter)
- 部門隔離(財務資料財務部才看)

### 對你的啟發

- **合規 / privacy 推著架構走**
- **內部工具是 AI 應用最穩的 goldmine**——上線容錯高,ROI 可見

---

## Case 7: Code / Test / Doc 自動化(SWE-agent、Devin 類)

### 做什麼

Agent 接 issue → 自動 clone repo → 寫 code → 跑 test → 開 PR。

### 關鍵架構

- **每個任務 sandbox(Docker container)**
- **工具極限**:Read、Write、Bash、Git、Test-runner
- **Long context**:codebase embedding + retrieval
- **Fail gracefully**:fail 3 次就 escalate,不硬幹
- **Cost metering**:per-issue budget cap

### 踩過的坑(公開)

- **Infinite loops**:agent 試同樣 approach 無數次
- **Hallucinated imports**:生成不存在的套件名
- **Silent failures**:test pass 了但 feature 其實沒 work
- **Context collapse**:長 session 忘了早期需求

### 對你的啟發

- **Autonomous code agent 遠不成熟**。SWE-bench 頂尖 agent 的成功率還在 50% 左右
- **每個任務預算必須有**,不然帳單爆
- **Test 是唯一可自動驗證的 signal**,產品設計要圍繞 test

---

## Case 8: 創作類(Midjourney、ChatGPT w/ DALL-E、Runway)

### 做什麼

文生圖、文生影片、文生音樂。

### 關鍵架構

- **Prompt 優化**:user 寫「cat」,後端擴成「high-quality photo of a cat, bokeh, 50mm lens」
- **生成 multiple variants**,user 選
- **Iterative refinement**:user 說「more blue」,系統在原圖基礎上調
- **Queue + batch**:GPU 資源密集,非即時

### 設計決策

**為什麼先擴 prompt**?

- 原始 prompt 太短 → 生成質量低
- User 沒有 domain vocabulary(「rule of thirds」、「anamorphic」)
- LLM 幫 user 拔升品質

**為什麼 multiple variants**?

- 生成是 stochastic,一張可能不理想
- 給選擇權:user 拒絕次數低 → 滿意度高

### 對你的啟發

- **LLM 當前處理(user prompt)就能大幅改善 UX**
- **Stochastic 產出要給 choice**,不要「只給一個」

---

## 共通的 7 個 lessons

拆完這些案例,共通心得:

### 1. Workflow + Agent 混合是主流

全 agent 少,全 workflow 也少,mix 最常見。

### 2. Observability 是 scale 的前提

Prod 看不到 → 無法 debug → 無法改進。沒例外。

### 3. 成本是真實 constraint

LLM 很貴,產品設計要有 cost model。

### 4. Sandbox 必要

Agent 跑外部操作 → sandbox 不是 optional。

### 5. Trust via Visibility

User 看得到 agent 想什麼、做什麼 → 願意給權力。

### 6. Escalation 跟主流程一樣重要

什麼時候「放棄」、怎麼 graceful 交給人工 → 產品成敗關鍵。

### 7. 沒 eval 不能改

每個穩定的產品都有 eval loop。

---

## 自我檢核

- [ ] Claude Code 和 Cursor 的 agent 設計有什麼不同?
- [ ] Autonomous PR-writing agent(Devin 類)為什麼要 sandbox?
- [ ] 客服 chatbot 為什麼多半用 workflow + 小部分 agent?
- [ ] Perplexity 的 citation 設計為什麼重要?
- [ ] 這 7 個 lessons 中,你覺得最常被忽視的是哪個?

→ [Ch 25 成本、降級、失敗處理](./25-ops.md)
