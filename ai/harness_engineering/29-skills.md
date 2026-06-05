# Ch 29 — Skills / 可重用能力包

> **目標**：前面幾章你都是「一個一個工具手工接上去」。本章談 **Skill（技能）**——把「一整套能力」（一段指引 + 可選的腳本與資源檔）打包成一個資料夾，讓 agent **按需載入**，而不是把所有東西都塞進系統提示與 context。讀完你能說出 Skill 是什麼、它跟「工具 / MCP / subagent / prompt 範本」各自的分工、最關鍵的設計原理 **progressive disclosure（漸進揭露）** 為什麼能讓你掛上幾十個 skill 而不爆 context、SKILL.md 的 frontmatter（尤其 `description`）為什麼是「模型決定要不要用它」的唯一依據、以及 skill 跟工具的根本差異——**skill 是給模型讀的「怎麼做」說明書，工具是讓模型「能做」的能力**。

> **環境**：Python + Anthropic SDK 的概念延續，但 Skill 主要是一種**檔案約定 + 載入機制**，不綁特定語言。**你正在用的這個 repo 就有一個 skill**：`.claude/skills/learn/SKILL.md`——這整門課就是那個 skill 被觸發後產出的。本章會直接把它當第一手教材拆解（並標明哪些是 Claude Code/Agent SDK 的具體實作、哪些是通用原則）。

## 為什麼需要這個？工具不夠，prompt 又塞不下

到 Ch 28 為止，你給 agent 加能力的方式只有兩種，都有上限：

1. **加工具**（Ch 18-25）：工具讓 agent「能做某個動作」（讀檔、跑指令、查 API）。但工具不教「**該怎麼做一件複雜的事**」。`write_file` 工具讓 agent 能寫檔，但「怎麼寫一份符合本公司規範的 PDF 報告」——這是一套**流程與知識**，不是一個動作。
2. **塞進系統提示**：你可以把「怎麼做 X」寫進 system prompt。但 system prompt 是**每一回合都載入**的——你有 50 種專門任務（產 PDF、跑特定資料管線、按某風格寫測試……），全塞進去？context 瞬間爆掉，而且 99% 的回合根本用不到那 49 種。

真實需求長這樣：「**我有一大堆專門的、偶爾才用到的 know-how，希望 agent 需要時能調出對應那套、不需要時完全不佔 context。**」這正是 Skill 要解決的。

**Skill = 一個資料夾，裡面有一份 `SKILL.md`（怎麼做某件事的指引）+ 可選的腳本與資源檔。** 它的精髓是**按需載入**：平時只有一行簡短描述在 agent 眼前；當 agent 判斷「這次任務跟某個 skill 對得上」，才把那個 skill 的完整內容拉進 context。你可以掛上幾十個 skill，context 卻幾乎不受影響——因為平時載入的只是它們的「目錄」，不是「內文」。

關鍵心態：**skill 把「能力」從「程式碼裡寫死的工具」變成「檔案系統裡可插拔的知識包」。** 加一個新能力 = 新增一個資料夾，不必改 harness 程式碼。

## 先建立直覺：skill 是「需要時才翻開的工具書」

想像你的桌上有一整櫃工具書：《PDF 報告產製規範》《公司資料管線操作手冊》《前端測試風格指南》……。你不會把每本書的全文都背在腦子裡（那會塞爆你的工作記憶）。你記得的只是**每本書的書名與一句話簡介**。遇到「要產 PDF 報告」的任務，你才走到書櫃、抽出那本、翻開來照著做。做完，闔上，腦子裡又只剩書名。

```
   平時（agent 啟動）：context 裡只有「書名 + 一句簡介」
   ┌────────────────────────────────────────────────┐
   │ skill: pdf-report   「產製符合公司規範的 PDF 報告」│  ← 只有 metadata
   │ skill: data-pipeline 「操作 ETL 資料管線」         │  ← 只有 metadata
   │ skill: test-style    「按本專案風格寫測試」        │  ← 只有 metadata
   │ ...（再掛 47 個也只是 47 行）                      │
   └────────────────────────────────────────────────┘
                       │
        任務進來：「幫我把這份資料做成 PDF 報告」
                       │ agent 比對 description，判定 pdf-report 對得上
                       ▼
   ┌─ 載入 pdf-report/SKILL.md 全文 ─────────────────┐
   │  完整步驟、規範、注意事項…（這時才進 context）   │
   │  需要更細 → 再讀 pdf-report/reference.md         │
   │  要執行 → 跑 pdf-report/scripts/build.py         │
   └──────────────────────────────────────────────────┘
```

這就是 **progressive disclosure（漸進揭露）**——本章最重要的一個詞。資訊**分層**載入：先只給「目錄」（每個 skill 一行描述），需要了才給「內文」（SKILL.md 全文），更需要才給「附錄」（額外檔案、腳本）。這讓「掛很多 skill」和「context 不爆」可以同時成立。

> **這個 repo 的 `learn` skill 就是這樣運作的**：平時我（這個 agent）只看到它的 `description`（「使用者想一起學一個技術主題、產出結構化課程時用」）。當你說「我想學 harness engineering」，我比對到這個描述、才把整份 `SKILL.md` 載入，照著裡面的工作流程（釐清目標 → 課綱 → 逐章寫 → 練習 → 總結專案）做事。你現在讀的這一章，就是那個 skill 被揭開後跑出來的產物。

## 一、SKILL.md 的解剖：frontmatter 是「書名與簡介」，本文是「內文」

一個最小的 skill 就是一個資料夾加一個 `SKILL.md`：

```
my-skills/
└── pdf-report/
    ├── SKILL.md            ← 必要：frontmatter + 指引本文
    ├── reference.md        ← 可選：更細的參考，需要時才讀
    └── scripts/
        └── build_pdf.py    ← 可選：可執行的腳本
```

`SKILL.md` 的結構：開頭是 YAML frontmatter，後面是 Markdown 本文。

```markdown
---
name: pdf-report
description: 當使用者要把資料或內容產製成符合公司規範的 PDF 報告時使用。觸發語包括「做成 PDF」「產一份報告」「export 成 PDF」。處理版面、頁首頁尾、品牌樣式。
---

# 產製 PDF 報告

## 步驟
1. 確認資料來源與目標版型…
2. 用 scripts/build_pdf.py 產出初稿…
3. 套用品牌樣式（見 reference.md 的色票與字體規範）…

## 注意
- 頁尾一定要有頁碼與機密等級標示…
```

（細節因產品而異：在 API 的 skill 包裡 `name`+`description` 是必填的 metadata；在 Claude Code 裡 frontmatter 欄位較寬鬆——`name` 預設用資料夾名、`description` 是強烈建議但非硬性必填。本章採「兩個都寫好」的通用最佳實踐，不糾結各產品的細則差異。）

兩個部分，分工天差地別：

- **`description`（frontmatter）是「模型判斷要不要自動揭開這個 skill」的主要依據**。它（連同 `name`）會出現在那份常駐 context 的 skill 清單裡，是漸進揭露的第一層。模型靠比對「當前任務 vs 各 skill 的 description」來決定自動揭開哪一個。所以 description 要寫得**像觸發條件**：講清楚「**什麼情況下該用我**」「會處理什麼」「常見觸發語」——而不是含糊的「一個關於 PDF 的工具」。description 爛 = skill 不會被自動觸發，幾乎等於沒掛。（注意這是「自動觸發」的主依據，不是唯一進入點——多數 harness 還支援**手動叫用**某個 skill，例如使用者直接打 `/skill-名稱`，這條路不靠 description 比中。實作上也常有額外欄位/設定可控制可見性與觸發方式，本章聚焦最通用的 `name`+`description` 自動觸發。）
- **本文（Markdown body）是「怎麼做」的完整說明書**，只有 skill 被觸發後才載入。這裡放步驟、規範、範例、提醒——寫給模型讀的操作手冊。

> **對照 Ch 19「工具描述就是 prompt」**：那一章你已經學過「`description` 欄位是模型行為的最大槓桿」。skill 的 `description` 是同一個道理的放大版——只是這裡它還多一個職責：**當開關用**。工具的 description 影響「怎麼用這個工具」；skill 的 description 還決定「要不要把這整包知識揭開來」。

## 二、Progressive disclosure：為什麼能掛幾十個而不爆 context

把漸進揭露的三層講清楚（這是 skill 設計的核心機制）：

| 層級 | 載入時機 | 內容 | 成本 |
|---|---|---|---|
| **第一層：metadata** | **永遠在 context** | 每個 skill 的 `name` + `description`（各約一兩行） | 極低——50 個 skill 也才幾十行 |
| **第二層：SKILL.md 本文** | skill 被**觸發**時 | 完整操作指引（步驟、規範、範例） | 中——但一次只揭開用得到的那一兩個 |
| **第三層：附加檔案/腳本** | 本文**指示去讀/執行**時 | reference.md、範例、可執行腳本 | 視需要——只在真的要用時才進 context |

這個分層直接解決了開頭那個矛盾（「know-how 很多，但 context 塞不下」）：

- **掛很多 skill 幾乎免費**：只要它們不被觸發，就只佔第一層那一行。你可以放心掛 50 個專門 skill。
- **被用到的才花 context**：典型一次任務只揭開一兩個 skill，其餘 48 個完全不佔正文空間。
- **大型 skill 不必把全部塞進 SKILL.md**：把細節推到第三層（reference.md、腳本），SKILL.md 本文保持精簡，只在需要時才往下挖。這也是為什麼一個 skill 可以包很龐大的知識，卻不會在被觸發時一次灌爆 context。

一句話：**漸進揭露讓「能力的總量」和「當前 context 的佔用」解耦。** 這是 skill 相對「全塞進 system prompt」的決定性優勢。

> **這個機制你正在親身使用**：我啟動時，context 裡有一串可用 skill 的名字與簡介（第一層），但我沒有載入它們的全文。需要某個時才透過對應機制把它揭開。本章談的漸進揭露，就是你這個 session 的 skill 系統實際在做的事。

## 三、Skill vs 工具 vs MCP vs subagent：四個容易混淆的東西

這四個都是「給 agent 加能力」，但層次完全不同。釐清它們的分工，是本章的一個關鍵收穫：

| | 它是什麼 | 提供的是 | 何時載入 | 類比 |
|---|---|---|---|---|
| **工具**（Ch 18-25） | 一個函式 + schema | **「能做」一個動作** | schema 常駐 context（除非 deferred，Ch 23） | 手（能抓東西） |
| **MCP**（Ch 24） | 外部伺服器，**提供工具/資源** | 一批**外部來源的工具** | 連上後其工具進 context | 接上一整箱外部工具 |
| **Skill** | 一個資料夾（指引+資源） | **「怎麼做」一套複雜流程的知識** | 漸進揭露：平時只有描述 | 需要時才翻開的工具書 |
| **Subagent**（Ch 26） | 一個獨立的 agent 迴圈 | **「外包」一整段子任務** | 被父 agent 當工具呼叫 | 把活外包給一個同事 |

把它們的關係講白：

- **工具 vs skill 是最核心的區分**：工具讓 agent **能**做某事（`write_file` 能寫檔）；skill 教 agent **該怎麼**做一件事（「怎麼產一份合規 PDF」「按本公司風格寫作」）。**skill 是「怎麼做 / 該知道什麼」的知識包，工具是「能做」的能力。** 兩種典型的 skill：(a) **流程型**——步驟裡會叫 agent 用工具、跑腳本（pdf-report 要用檔案工具產檔），這種沒有對應工具就做不出成果；(b) **純參考型**——只是慣例、風格指南、領域知識，純粹餵給模型改善它的推理/寫作，**不需要任何工具**也有用。所以別誤以為「skill 一定要配工具」——要不要工具，看那個 skill 是否需要對外做動作。
- **skill vs MCP**：MCP 是「**從外部接來一批工具/資源**」（能力的**來源**）；skill 是「**怎麼運用能力完成任務**的打包知識」。一個 skill 的步驟裡，可能就用到某個 MCP 接來的工具。兩者不是替代，是不同層。
- **skill vs subagent**：subagent 是**把一段工作外包給另一個獨立 context 的 agent**（Ch 26 的 context 隔離）；skill 是**載入到當前 agent 的一包知識**。不過兩者可以結合：一個 skill 的指引裡可以說「這一步派個 subagent 去並行調查」。

> **常見誤解：「skill 是一種特別的工具」**——不是。skill 的核心是**文字指引**（給模型讀的「怎麼做」），它不是一個被呼叫就跑邏輯的函式；agent 真要對外做動作，仍是透過工具（跑 skill 附帶的腳本，也是經由 shell/檔案工具）。把 skill 想成「動態載入的、任務專屬的迷你 prompt + 資源包」，而不是「又一個函式」。
>
> **但要小心一個 caveat**：說「skill 本身不執行東西」是就「它不是函式」這層而言。實務上有些 harness（如 Claude Code）支援在 SKILL.md 裡寫**動態命令注入**——某些指令會在 skill 內容被模型看到「之前」就先執行、把輸出塞進 context；skill 附帶的腳本也會被 agent 跑起來。所以從安全角度，**揭開/信任一個 skill 不能當成「只是讀一段純文字」**（見進階節的安全討論）。

## 四、寫一個好 skill：description 是命脈，本文要像給人的 SOP

實務上寫 skill 的成敗，八成在兩件事：

**1. `description` 寫得「會被正確觸發」。** 這是 skill 唯一常駐 context 的部分，模型全靠它判斷要不要揭開。寫法：

- **講觸發情境，不講功能名詞**。✅「當使用者要把內容產成符合公司規範的 PDF 報告時使用」 ❌「PDF 工具」。前者讓模型知道「什麼任務該叫我」。
- **放常見觸發語**。把使用者可能講的話寫進去（「做成 PDF」「export 報告」），提高比對命中率。
- **講清楚邊界**。如果這個 skill **不**該在某些情況用，也寫出來——避免被錯誤觸發、平白佔 context。
- 看看本 repo 的 `learn` skill description：它列了一串觸發語（「我想學 X」「let's learn X」「continue an existing empty course folder」）並界定了產出（zh-TW 課程章節），就是按這套寫的。

**2. 本文寫得像「給一個能幹但沒有背景知識的人的 SOP」。** 它的讀者是模型，但好的寫法跟寫給新進同事的標準作業程序一樣：

- **步驟化、有順序**。模型會照著走，含糊的「處理一下版面」不如「步驟 3：套用 reference.md 的色票」。
- **把不常用的細節推到第三層**。SKILL.md 本文保持精簡（漸進揭露的精神），龐大的規範、查表、長範例放進 `reference.md`，本文只說「需要時讀 reference.md」。
- **附可執行的腳本**。重複、確定性的步驟（產 PDF、跑某個轉檔）寫成腳本放 `scripts/`，本文叫 agent 去跑——比讓模型每次即興生成程式碼更可靠、更省 token。
- **明確的產出與檢核**。講清楚「做完應該長什麼樣」，讓模型自我驗收。

一句話：**description 決定 skill「會不會被用」，本文決定 skill「用起來好不好」。** 兩個都要顧。

## 對比與取捨

| 設計選擇 | 選項 A | 選項 B | 怎麼選 |
|---|---|---|---|
| 偶爾用到的 know-how 放哪 | 塞進 system prompt | **做成 skill（按需載入）** | 種類多、偶爾用 → skill；每次都要、極短 → system prompt |
| 能力的形態 | 寫死成工具/程式碼 | **打包成可插拔的 skill 資料夾** | 要「怎麼做的流程知識」→ skill；要「能做的動作」→ 工具 |
| skill 內容多寡 | 全寫進 SKILL.md 本文 | **本文精簡 + 細節推到 reference/腳本** | 漸進揭露：本文當入口，重內容放第三層 |
| description 怎麼寫 | 功能名詞（「PDF 工具」） | **觸發情境 + 觸發語 + 邊界** | 寫成「什麼時候該用我」，否則永遠不被觸發 |
| 重複確定性步驟 | 讓模型每次即興生成程式碼 | **寫成腳本，skill 叫它去跑** | 確定性步驟用腳本，省 token、更可靠 |
| 加新能力 | 改 harness 程式碼 | **新增一個 skill 資料夾** | skill 讓能力可插拔，不動核心程式碼 |

## 踩雷集錦

1. **description 寫成功能名詞**：「一個 PDF 相關的 skill」——模型不知道什麼任務該觸發它，於是永遠不被用。要寫「**什麼情況下該用我**」+ 觸發語。
2. **把所有細節塞進 SKILL.md 本文**：違背漸進揭露，skill 一被觸發就灌爆 context。重內容推到 reference.md / 腳本（第三層），本文當精簡入口。
3. **以為 skill 會「自己執行」**：skill 只是載入文字指引，本身不跑任何東西。要執行得靠工具（跑它附帶的腳本仍需 shell/檔案工具）。沒給 agent 對應工具，skill 讀了也做不出來。
4. **skill 之間描述重疊、互相搶觸發**：兩個 skill 的 description 講的情境太像，模型不知道該揭哪個。描述要彼此區隔、邊界清楚。
5. **用 skill 做「一個動作」的事**：如果某能力就是「呼叫一個 API / 做一個動作」，那是**工具**該做的，不必包成 skill。skill 是給「一套有步驟的複雜流程 + 知識」用的。
6. **skill 本文寫得像給人的散文而非 SOP**：模型照著做，含糊、無順序的描述會讓它做歪。寫成步驟化、有明確產出與檢核的操作手冊。
7. **掛了一堆永遠不觸發的 skill**：description 沒寫好 + 跟任務對不上。掛 skill 前先想「它的 description 真的會在對的任務被比中嗎」。

## 進階：再往深一層

- **Skill 的可組合性（composability）**：多個 skill 可以疊加用——一次任務裡模型可能先揭開 `data-pipeline` 取資料、再揭開 `pdf-report` 產出。設計 skill 時讓它「做好一件事」，比做一個包山包海的巨型 skill 更好組合。
- **Skill + subagent**：一個 skill 的指引裡可以明確寫「這一步派 N 個 subagent 並行調查」（Ch 26-27），把 skill 當成「編排的劇本」。也可以讓某個 subagent 啟動時就帶著特定 skill。
- **Skill 的分發與版本**：skill 就是資料夾，天生好分享（丟進 git repo、做成可安裝的包）。團隊可以維護一組共用 skill，像共用函式庫一樣演進、版控。這就是「能力可插拔」帶來的工程好處。
- **安全面：skill 不只是純文字**：skill 可以附腳本（agent 會去跑）、可帶動態命令注入（某些 harness 會在模型看到內容前就執行）、還可能夾帶權限/allowed-tools 設定。所以**安裝或信任一份來路不明的 skill 帶有程式碼執行風險**——這跟「執行任意程式碼」是同一類威脅（Ch 22 sandbox、Ch 25 權限、Ch 36 注入安全）。要審查內容、要沙箱，別把它當成「就讀一段純文字說明」。
- **Skill vs RAG/檢索**：兩者都是「按需把外部知識拉進 context」，但 skill 是**模型主動依 description 判斷要不要整包揭開**的「程序性知識」（怎麼做）；RAG 通常是依語意相似度檢索**片段事實**（是什麼）。複雜任務常常兩者並用。
- **這個機制是 Anthropic 的 Agent Skills**：Claude Code 與 Agent SDK 把上述約定（`SKILL.md` + frontmatter + 漸進揭露 + 可帶腳本）實作成正式功能。延伸閱讀的官方文件是最權威的細節來源——本章建立的是「為什麼這樣設計」的直覺。

## 動手練習

1. 讀本 repo 的 `.claude/skills/learn/SKILL.md`：找出它的 `name` 與 `description`，分析 description 怎麼寫觸發情境與觸發語。再看本文怎麼把工作流程步驟化——這是一個真實 skill 的範本。
2. 寫一個你自己的最小 skill：建一個資料夾 + `SKILL.md`，frontmatter 寫好 name/description，本文寫三到五步的 SOP（例如「把一段 commit log 整理成 release notes」）。重點練習 description 的「觸發情境」寫法。
3. **把細節推到第三層**：把上一題 skill 裡某段冗長的規範（例如格式範本）抽到 `reference.md`，本文只留「需要時讀 reference.md」。對照前後 SKILL.md 本文的長度，體會漸進揭露。
4. **加一個腳本**：把 skill 裡某個確定性步驟寫成 `scripts/xxx.py`，本文改成「跑 scripts/xxx.py」。想想這比「讓模型每次即興寫程式碼」好在哪。
5. （概念）為一個你會掛的 5 個 skill 設計各自的 description，刻意讓它們**邊界清楚、不互相搶觸發**。再想：如果其中兩個情境很像，你會怎麼在 description 裡區隔它們？

## 本章重點整理

- **Skill = 一個資料夾（`SKILL.md` + 可選腳本/資源），打包「怎麼做某件複雜事」的知識，按需載入。** 加能力 = 新增資料夾，不動 harness 程式碼。
- **核心機制是 progressive disclosure（漸進揭露）**：三層——metadata（name+description，永遠在 context）→ SKILL.md 本文（觸發時載入）→ 附加檔案/腳本（需要時載入）。這讓「能力總量」和「context 佔用」解耦，所以能掛幾十個 skill 不爆。
- **`description` 是命脈**：它是模型決定「要不要揭開這個 skill」的唯一依據，要寫成「**什麼情況下該用我**」+ 觸發語 + 邊界，不是功能名詞。
- **skill ≠ 工具**：skill 是「怎麼做」的說明書（給模型讀的文字指引，本身不執行），工具是「能做」的動作。skill 通常會用到工具。也別跟 MCP（能力來源）、subagent（外包子任務）搞混。
- **本文寫成 SOP**：步驟化、細節推到第三層、確定性步驟寫成腳本、有明確產出與檢核。
- **skill 會帶可執行碼**：載入不明來源的 skill = 執行別人的程式碼，要審查 + 沙箱（接 Ch 22/25/36）。

## 自我檢核

- [ ] 我能解釋 skill 解決什麼問題（know-how 很多但塞不進 system prompt）
- [ ] 我能說出 progressive disclosure 的三層，以及為什麼它讓「掛很多 skill」可行
- [ ] 我能解釋 `description` 為什麼是 skill 的命脈，並寫出一個「會被正確觸發」的 description
- [ ] 我能講清楚 skill 跟工具、MCP、subagent 各自的分工，不混淆
- [ ] 我能寫出一個最小 skill 的資料夾結構與 SKILL.md
- [ ] 我知道載入不明來源 skill 的安全風險，並能連到 Ch 22/25/36

## 延伸閱讀

### 官方文件

- **[Anthropic — Agent Skills](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)** — Anthropic
  - **讀哪裡**：Skill 的結構（SKILL.md + frontmatter）、progressive disclosure 的機制、`description` 的寫法建議。
  - **能學到什麼**：本章所有概念的權威定義與最新實作細節，以及官方推薦的 skill 撰寫規範。
  - **前提知識**：Ch 19（工具描述即 prompt）會讓你更快抓到 description 的重要性。

- **[Anthropic — Claude Code / Agent SDK 文件](https://docs.claude.com/en/docs/claude-code)** — Anthropic
  - **讀哪裡**：skill 在 Claude Code 裡怎麼放置（專案層 `.claude/skills/`、個人層 `~/.claude/skills/`、外掛附帶；Agent SDK 還可用 `setting_sources` 控制載入來源）、怎麼被觸發、怎麼附帶腳本。
  - **能學到什麼**：一個成熟 harness 怎麼把 skill 機制做成可用功能——對照本 repo 的 `learn` skill。
  - **前提知識**：Ch 24（MCP）有助於分辨 skill 與 MCP 的層次差異。

### 部落格 / 技術文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** — Anthropic
  - **這篇說什麼**：雖然沒直接講 skill，但它「能用簡單方案就別上複雜方案」的精神，正好對應本章「能用工具解決就別包成 skill、能寫死 workflow 就別動態」的判斷。
  - **讀哪裡**：開頭關於「何時該加複雜度」的論述。
  - **為什麼值得讀**：幫你判斷「這個能力到底該做成工具、skill、還是 subagent」——避免過度設計。

下一章換個角度：到目前為止，控制 agent 行為的都是「你給它什麼工具、什麼 skill、什麼 prompt」。下一章談 **hooks（鉤子）**——一種在 agent 生命週期的特定時點（工具呼叫前後、回合結束時…）插入你自己程式碼的機制，讓你能在迴圈之外**攔截、檢查、改寫、阻擋** agent 的行為。

→ [Ch 30 Hooks](./30-hooks.md)
