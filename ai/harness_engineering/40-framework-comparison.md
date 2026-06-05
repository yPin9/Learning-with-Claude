# Ch 40 — 框架對比：Claude Agent SDK / LangGraph / OpenAI Agents SDK / 自己寫

> **目標**：到這裡，你已經把 agent harness 的每個零件都**親手刻過一遍**——迴圈、工具協定、context 管理、停止條件、子代理、eval、observability、安全、可重現。現在退一步問一個你終於有資格回答的問題：**這些東西，業界框架幫你做了哪些、又綁住了你什麼？什麼時候該用框架、什麼時候該自己刻？** 本章把三個主流選擇（Claude Agent SDK / LangGraph / OpenAI Agents SDK）對照「自己刻」，但**重點不是教你某個框架的 API**（那會過期），而是給你一套**評估框架的眼光**：每個框架在「控制 ↔ 便利」這條軸上站哪、它替你做的決定是不是你想要的、換掉它有多痛。核心心態：**框架不是「要不要用」的是非題，是「在哪一層用、放棄多少控制權換多少省事」的取捨題——而你現在看得懂它替你做了什麼，這就是手刻一遍的回報。**

> **環境**：本章是**總結與決策**章，不寫新的實作。它把前面 39 章的概念當「檢查清單」，去逐一對照各框架幫你蓋掉哪幾項。讀的時候，把每個框架的功能對回你刻過的章節（迴圈=Ch 4、工具=Ch 5/18-20、context=Ch 10-16、子代理=Ch 26-27、eval=Ch 34、trace=Ch 35、resume=Ch 39），你會發現「喔，這個框架就是把我那段手刻的東西包成一個 API」。

> **注意**：框架版本與 API 變動很快，本章描述為**撰寫當下（2026 年中）**的概況，且**刻意聚焦不易過期的設計取捨**而非具體函式簽名。用之前請以各框架官方文件為準。

## 為什麼需要這個？「自己刻」教會你的，正是看懂框架的眼光

很多人學 agent 是「直接學框架」——LangGraph 教學跟著敲、能跑就好。問題是：當它出錯（而 agent 一定會出錯，Ch 38），你不知道框架在底下做了什麼，只能瞎改參數、貼 issue 等人回。**你變成框架的使用者，不是 agent 的工程師。**

這門課反過來：先讓你手刻，於是現在你看任何框架，都能問出對的問題：

- 它的 agent 迴圈停止條件是什麼？我改得動嗎？（Ch 7）
- context 滿了它怎麼處理——自動壓縮還是直接報錯？壓縮策略我能換嗎？（Ch 13）
- 工具錯誤它怎麼回給模型？是 `is_error` 還是吞掉？（Ch 20）
- 它的 trace 能讓我重放那次壞掉的執行嗎？（Ch 35/39）
- 它幫我做的 prompt injection 防護到哪一層？（Ch 36）

**能問出這些問題，你就有資格選框架**；問不出來，框架對你就是個黑盒，出事只能拜拜。這一章就是帶你用這套眼光把四個選項過一遍。

## 先建立直覺：所有選擇都在同一條軸上

把選項攤在「**你放棄多少控制權，換多少省事**」這條軸上：

```
  控制權多 ◄─────────────────────────────────────────► 省事多
  自己刻        LangGraph         Claude Agent SDK    （託管 / no-code
  (DIY)      (低階編排框架)      OpenAI Agents SDK      平台、Assistants 類)
  │              │                    │                      │
  全部自己決定    給你「狀態機+        給你「一個能跑的         你只填 prompt，
  全部自己負責    持久化」骨架，       agent」連工具/迴圈/      迴圈/工具/部署
  最大彈性、       節點邏輯自己填，     context/session 都      全包，最不靈活
  最多工          中等控制中等省事     打包好，最省事但最綁     最省事
```

關鍵認知：**沒有「最好的」選擇，只有「對這個任務、這個團隊、這個階段最合適的」**。同一個產品，原型期可能用 SDK 快速驗證，規模化後某個關鍵 agent 改成自己刻拿回控制權——這很正常。下面逐一看每個選項替你做了什麼、綁了你什麼。

## 一、自己刻（DIY）：你已經會了

就是這門課做的事。你直接打 Messages API，自己寫迴圈、工具分發、context 管理、停止條件。

- **給你什麼**：**完全的控制與透明**。每個 token 怎麼進 context、迴圈何時停、錯誤怎麼回，全是你的程式碼，全看得到、改得動。debug 時沒有黑盒（Ch 38）。
- **代價**：**全部自己扛**。重試/退避（Ch 9）、context 壓縮（Ch 13）、MCP 整合（Ch 24）、checkpoint/resume（Ch 39）、observability（Ch 35）——框架免費送的，你都要自己寫、自己維護。
- **什麼時候選**：①你的 agent 邏輯很特殊，框架的抽象反而綁手綁腳；②你需要對成本/延遲/行為做極致控制（Ch 37）；③學習/理解階段（就是現在）；④你不想被任何框架的版本與生命週期綁住。
- **真相**：「自己刻」不代表從零。你仍會用 Anthropic SDK（它已幫你處理 HTTP、streaming、型別）。「DIY」指的是**agent 那層邏輯自己掌握**，不是連 HTTP client 都手寫。

## 二、Claude Agent SDK：把 Claude Code 的引擎開放給你

Anthropic 的 Claude Agent SDK（前身 Claude Code SDK，Python `claude-agent-sdk` / TS `@anthropic-ai/claude-agent-sdk`）。它把**驅動 Claude Code 的那套 agent 迴圈、工具、context 管理**包成 API 給你用。

- **給你什麼**（對回你刻過的章節）：內建的檔案編輯/bash/web 搜尋與抓取工具（Ch 21/22）、帶 human-in-the-loop 確認點的 tool-use 迴圈（Ch 5/25）、**subagents**（有自己 context 的子代理，訊息帶 `parent_tool_use_id` 讓你追是哪個子代理，Ch 26/27）、可續可分叉的 **persistent session**（Ch 14/39）、**第一級的 MCP client**（Ch 24）。基本上你前面手刻的東西，它大多有對應現成件。
- **綁了你什麼**：**以 Anthropic 模型為中心**。你拿到的是「一個已經調好、能跑的 Claude agent」，但也意味著它的迴圈、context 策略是 Anthropic 的選擇——你能設定，但底層哲學跟著它走。換到別家模型不是它的設計重點。
- **什麼時候選**：①你就是要用 Claude、且想要「Claude Code 等級的 agent 行為」但跑在你自己的程式裡；②你重度用 MCP、子代理、檔案/bash 工具；③你想要 Anthropic 持續維護那套 harness（省下你維護的力氣）。
- **注意營運面**：訂閱方案下 Agent SDK 的用量計費規則會變動（例如 2026 年中起有獨立的月度額度），上生產前確認當下的計費與額度政策。

## 三、LangGraph：把 agent 當「狀態機」來編排

LangChain 團隊的 LangGraph，把 agent 流程建模成**圖（graph）**：節點（nodes）是步驟、邊（edges）是流轉、加上**checkpoint** 持久化與一個知道「現在跑到哪」的 runtime。它是經典軟體「狀態機」模式搬到 agent 編排上。

- **給你什麼**（對回章節）：**內建持久化層**——compile 時掛一個 checkpointer，它在**每個 super-step（graph step）邊界**存一份 state 快照、用 thread 組織（同一 super-step 內平行的節點不是每個各存一份）（這正是 Ch 39 的 checkpoint/resume，它幫你做好了）；**time-travel debugging**——因為每個 checkpoint 不可變且有版本，整段執行變成可重放、可檢視的狀態序列（Ch 35/39 的 replay）；內建 human-in-the-loop、條件分支、迴圈。**模型/provider 較中立**（不綁單一家）。
- **綁了你什麼**：**你得用它的程式模型思考**——把任務拆成節點與邊、用它的 state schema、接受它的 runtime 調度。控制權比 SDK 多（節點邏輯你寫），但比 DIY 少（流轉與持久化照它的框架）。抽象有學習曲線，且當行為不如預期，你要 debug 的是「你的節點」+「框架的調度」兩層。
- **什麼時候選**：①你的 agent 是**複雜、有明確狀態流轉的工作流**（多步審批、分支、需要可靠 resume 的長流程）；②你要 provider 中立、想在多家模型間切換；③你重視「中斷可恢復、可 time-travel debug」且不想自己刻整套持久化。

## 四、OpenAI Agents SDK：一小組 primitive，輕量而 opinionated

OpenAI 的開源 Agents SDK（前身 Swarm 的演進）。它給一**小組刻意精簡的 primitive**：`Agent`、`Runner`、`Tools`、`Handoffs`、`Guardrails`、`Sessions`。

- **給你什麼**（對回章節）：**Handoffs**——一個 agent 把任務交棒給另一個，**預設（未加 `input_filter` 時）完整對話歷史一起轉移**，接手的 agent 像從頭就在場（這是 Ch 27 multi-agent 的一種「交棒式」模式，跟「orchestrator 派工」不同；歷史可用 `input_filter` 改寫，巢狀交棒也有 beta 的摘要包裝）；**Guardrails**——輸入護欄（在訊息到 agent 前驗證）與輸出護欄（在回應到使用者前驗證），對應 Ch 36 的注入防護與 Ch 32 的輸出驗證；**Sessions**——agent 迴圈內維持 working context 的記憶層（Ch 14）；內建 tracing（Ch 35）。可透過 LiteLLM 接 100+ 家模型，但對齊 OpenAI 的 Responses / Realtime API 最順。
- **綁了你什麼**：**設計很 opinionated**——它把 handoff 當成多 agent 的一級模式、把 guardrail 當成安全的標準做法。但它**不只**能交棒：官方也支援 `Agent.as_tool()` 的 manager/worker（agents-as-tools）、code orchestration、平行 agents，所以 orchestrator-worker 需求其實做得到，只是 handoff 是它最突出的那條路。合用時非常省事，但你的世界觀若跟它差很遠，仍會覺得在順著它的假設走。以 OpenAI 生態為重心。
- **什麼時候選**：①你的多 agent 結構天然是「交棒/路由」式（客服分流、專家轉介）；②你想要極輕量、primitive 少、上手快；③你已在 OpenAI 生態裡。

## 對比與取捨

| 維度 | 自己刻 (DIY) | Claude Agent SDK | LangGraph | OpenAI Agents SDK |
|---|---|---|---|---|
| **控制權** | 最高（全是你的碼） | 中（可設定，底層它的哲學） | 中高（節點你寫，調度它的） | 中（primitive 內你寫，世界觀它的） |
| **省事程度** | 最低 | 高 | 中 | 高 |
| **核心抽象** | 無（就是程式） | 「一個能跑的 Claude agent」+ 工具/session/MCP | 圖：節點/邊/checkpoint | primitive：Agent/Handoff/Guardrail/Session |
| **provider** | 任意（你打誰的 API） | Claude 模型為中心（可走 Anthropic API/Bedrock/Vertex/Azure 等部署） | 較中立 | OpenAI 為主（可 LiteLLM 接他家） |
| **resume/持久化** | 自己刻（Ch 39） | session 可續/分叉 | **內建 checkpointer + time-travel** | Sessions（對話記憶/歷史持久化；非一般化 graph checkpoint） |
| **multi-agent** | 自己刻（Ch 27） | subagents（自帶 context） | 圖節點即可組 | **Handoffs 為主，亦支援 agents-as-tools/code orchestration** |
| **debug 透明度** | 最高（無黑盒） | 中 | 中（兩層：你的節點+調度） | 中 |
| **最適情境** | 特殊邏輯/極致控制/學習 | 要 Claude＋MCP＋子代理 | 複雜有狀態的工作流＋可靠 resume | 交棒式多 agent＋輕量 |

幾個橫切的取捨原則：

- **框架省的是「常見路」的力氣，不是「你那條特殊路」的力氣**。你的需求越標準，框架越划算；越特殊，抽象越容易變成負擔。
- **省事的代價是 debug 變兩層**：出事時你要分清「我的碼錯」還是「框架行為不如我以為」（Ch 38 的模型↔harness 邊界問題，在框架裡多了一層「我↔框架」邊界）。
- **lock-in 是真實成本**：換框架＝重寫 agent 那層。選之前先想「最壞情況換掉它有多痛」，痛的話把跟框架耦合的部分隔離在一個薄層後面。
- **可以混用、分層用**：用 SDK 快速起原型驗證價值，把其中最關鍵、最需控制的那個 agent 改成 DIY；或外層用框架編排、某個節點內部自己刻。不是全有全無。

## 踩雷集錦

1. **先選框架再想需求**：被教學/熱度帶著走，選了個 opinionated 框架，結果你的需求不長那樣，整天在繞它的抽象。先想清楚任務形狀（工作流？交棒？極致控制？）再選。
2. **把框架當黑盒、出事拜拜**：不知道它的迴圈停止條件、context 策略、錯誤處理——這正是這門課要治的病。用框架也要搞懂它在底下對應你哪一章的決定。
3. **低估 lock-in**：一路深耦合框架 API，要換時發現 agent 邏輯全綁死。把框架接觸面收斂到一個薄層。
4. **以為「用框架就不用懂 agent」**：框架幫你寫程式，不幫你做設計決定——context 要不要壓、工具結果怎麼回、何時該停，還是你的判斷。懂這些的人用框架如虎添翼，不懂的人用框架是放大鏡放大他的不懂。
5. **拿過時的對比下結論**：這領域三個月就變樣（SDK 改名、primitive 增刪、計費調整）。任何「X 比 Y 好」的結論都有保鮮期，決策前查當下官方文件。
6. **框架的「免費功能」沒驗證就信**：它說有 resume、有 guardrail、有 tracing——但深淺、邊界、能不能滿足你的場景，要實測（它的 resume 對不冪等工具安不安全？Ch 39）。別把功能清單當保證。
7. **用框架就不做 eval/observability**：框架給你 tracing 鉤子≠你有 eval（Ch 34）。框架換、模型升級照樣會讓你的 agent 退化，你的回歸防線還是得自己建。

## 進階：再往深一層

- **「薄框架 vs 厚框架」的長期賭注**：薄框架（給 primitive，少替你決定）遷移成本低、天花板高，但要自己組更多；厚框架（替你決定很多）上手快，但你被它的世界觀綁住。選的時候是在賭「我的需求會不會長成它假設的樣子」。
- **框架的抽象洩漏（leaky abstraction）**：所有框架在 happy path 都很美，一旦你的需求踩到它沒設想的邊角（特殊 context 策略、非標準工具協定、奇怪的停止條件），抽象就漏了，你被迫鑽進它的內部——這時你手刻過的理解就是救命的。
- **多框架/多模型的中介層**：大型系統常自己寫一層薄薄的 agent 介面，底下可插不同框架/模型，把 lock-in 關在中介層裡。這是「既用框架省事、又保留換的自由」的折衷，代價是多一層要維護。
- **框架也在收斂**：注意各框架都在補齊彼此的功能（持久化、子代理、護欄、tracing、MCP）。短中期可觀察到功能互補/收斂，但「選哪個框架」的差異不會歸零——它仍取決於抽象、provider、持久化模型與生態；而「你懂不懂 agent」的差異不會縮小——這也是為什麼這門課押在原理而非某個 API。
- **生產化的真正分水嶺不在框架**：在 eval（Ch 34）、observability（Ch 35）、安全（Ch 36）、成本（Ch 37）、可靠性（Ch 39）這些「框架不會幫你想清楚」的工程實踐。框架選對能省事，但這些做不好，用哪個框架都會出事。

## 動手練習

1. **框架體檢表**：挑一個你考慮的框架，對照本課的章節做一張表——它怎麼處理「迴圈停止條件、context 滿、工具錯誤、resume、injection 防護、trace/replay」？哪些它包好了、哪些要你補、哪些你改不動？（這張表就是你的選型依據。）
2. **同任務三實作**：拿你 Practice 的一個 agent 任務，分別用「DIY（你已有的）」和「一個框架」各實作一次最小版，比較程式量、你放棄了哪些控制、debug 體驗差在哪。
3. **lock-in 壓測**：在你的框架實作裡，標出所有直接呼叫框架 API 的地方，設計一個「薄介面層」把它們包起來，估算「換掉這個框架」要動多少碼。
4. **抽象洩漏實驗**：故意給框架一個它不擅長的需求（例如非標準的 context 壓縮策略、或一個奇怪的停止條件），看你能不能在不鑽進框架內部的情況下做到——記錄它在哪裡「漏」了。
5. **選型決策備忘**：用一段話寫下「對我這個專案，現在該用 X，因為 ___；若未來 ___ 改變，我會重新評估」——逼自己把選型講成有條件、可複查的決定，而不是信仰。

## 本章重點整理

- 所有選項在同一條軸上：**控制權 ↔ 省事**。DIY 控制最多最費工，託管平台最省事最綁，SDK 與 LangGraph 在中間。**沒有最好，只有最合適。**
- **手刻一遍的回報**：你現在能對任何框架問出對的問題（它的迴圈/context/錯誤/resume/injection 各怎麼處理、我改得動嗎）——這就是「agent 工程師」與「框架使用者」的差別。
- **Claude Agent SDK**：把 Claude Code 的引擎開放給你，內建工具/子代理/session/MCP，以 Anthropic 模型為中心。
- **LangGraph**：把 agent 當狀態機（節點/邊/checkpoint），內建持久化與 time-travel replay，適合複雜有狀態的工作流、要可靠 resume、provider 較中立。
- **OpenAI Agents SDK**：一小組 opinionated primitive（Agent/Handoff/Guardrail/Session），適合交棒式多 agent、輕量上手、OpenAI 生態。
- **橫切原則**：框架省的是常見路的力氣；省事的代價是 debug 多一層、且有 lock-in；可混用分層；功能清單要實測別盡信。
- **生產化的分水嶺不在框架**，在 eval / observability / 安全 / 成本 / 可靠性——這些框架不會幫你想清楚，而你已經學過了。

## 自我檢核

- [ ] 我能把四個選項放上「控制 ↔ 省事」軸，並說出各自的核心抽象
- [ ] 我能對一個框架，逐章問出「它怎麼處理迴圈/context/錯誤/resume/injection」並判斷我改不改得動
- [ ] 我能說出 Claude Agent SDK / LangGraph / OpenAI Agents SDK 各自最適合的情境與主要綁定
- [ ] 我理解「框架省常見路的力氣、不省特殊路的力氣」以及抽象洩漏的風險
- [ ] 我能評估一個框架的 lock-in 成本，並知道用薄介面層隔離的做法
- [ ] 我明白生產化的真正難點不在選框架，而在 eval/observability/安全/成本/可靠性

## 延伸閱讀

### 官方文件

- **[Anthropic — Agent SDK overview](https://platform.claude.com/docs/en/agent-sdk/overview)** — Anthropic
  - **讀哪裡**：它內建的工具、subagents、session、MCP client 的概念，對照你手刻的版本。
  - **能學到什麼**：「Claude Code 的引擎」被包成 API 後長什麼樣，哪些決定它替你做了。
  - **前提知識**：Ch 4-5（迴圈/工具）、Ch 24（MCP）、Ch 26-27（子代理）。

- **[LangGraph — Persistence (checkpointing)](https://docs.langchain.com/oss/python/langgraph/persistence)** — LangChain
  - **讀哪裡**：checkpointer、thread、time-travel 的機制。
  - **能學到什麼**：Ch 39 你手刻的 checkpoint/resume，框架級的成熟做法是什麼樣（不可變、有版本的 state 序列）。
  - **前提知識**：Ch 39（確定性與 resume）。

- **[OpenAI — Agents SDK](https://openai.github.io/openai-agents-python/)** — OpenAI
  - **讀哪裡**：Agent / Handoff / Guardrail / Session 四個 primitive 的定義。
  - **能學到什麼**：一個「opinionated、輕量」框架怎麼用最少的概念覆蓋多 agent、安全、記憶——對照 Ch 27/36 你會看出它的取捨。
  - **前提知識**：Ch 27（multi-agent）、Ch 36（injection/護欄）。

### 部落格 / 技術文章

- **[Anthropic — Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)** — Anthropic
  - **這篇說什麼**：反覆強調「由簡入繁、能不加複雜度就不加、先量測再加抽象」——這正是「該不該上框架」的判斷準則。
  - **讀哪裡**：關於「何時用框架 vs 直接呼叫 API」與避免過早抽象的段落。
  - **為什麼值得讀**：它直接講到「很多框架增加的抽象層會讓 debug 變難」——跟本章「省事的代價是 debug 多一層」完全呼應，是這整門課選擇「先手刻」的理由出處。

到這裡，**Part 5（品質、可靠性與安全）結束**，整套「概念章」也走完了。你已經從「一個 while 迴圈打 API」一路蓋到「可評測、可觀測、安全、可重現、且知道何時該用框架」的完整 harness 工程視野。

接下來是**收尾的實作**：[練習 E](./practice-e-eval-tracing.md) 把 Ch 34/35 落地——給你的 harness 接上 eval 與 tracing；最後的 [Final Project](./final-project-mini-harness.md) 讓你把這 40 章融會貫通，**從頭刻一個你自己的 mini agent harness**。

→ [練習 E：給 harness 加上 eval + tracing](./practice-e-eval-tracing.md)
