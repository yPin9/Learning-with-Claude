# Ch 31 — 批判與取捨：什麼時候不該用 pstack

> **目標**：退一步，用前面 30 章學到的標準檢查 pstack 本身。你會整理出它的真實成本（每個 session 的常駐 token、每次執行的指引 token、多 agent 的倍數）、它在 Claude Code 上的具體缺口（本課實際找到的六個）、它的設計主張在哪些條件下會失效（Ch 1 的五個信條逐一檢討）、以及一張「什麼任務該用什麼」的決策表。最後是一份關於本課自己的誠實紀錄：寫這門課的過程中，本課寫的檢查工具抓到了作者自己的幾次違規，而那些工具本身也出過幾次錯。

> **環境**：pstack 0.15.36、Claude Code 2.1.295。本章引用的數字都來自前面章節的實測或可查證的公開來源，並在引用處標明。

## 為什麼需要這個？

一門只講優點的課是推銷。pstack 的作者自己就在 README 裡寫「`poteto-mode` is my style. you may not want exactly that」，官方指南的 setup 頁說「Save `/poteto-mode` for work that needs rigor. A small, obvious edit doesn't.」——作者都在提醒你它不是到處都適用。

這一章用本課反覆使用的標準——Explain the Number、Prove It Works、Attack the Premise——來檢查 pstack。

## 一、成本：把數字攤開

| 成本 | 數字 | 來源 |
|---|---|---|
| 每個 session 常駐 | 約 3,577 tokens（58 個技能的 description） | Ch 0，`claude plugin details` 實測 |
| poteto-mode 被打開時 | 約 5.4k tokens | Ch 0，同上 |
| 一次 Feature 執行讀進的指引 | 約 10k tokens 的量級（poteto-mode + adapter + playbook + 原則 leaf） | Ch 0，依檔案大小推算，**不是實測** |
| 社群移植版的常駐 | 約 4,191 tokens；poteto-mode 約 6.9k | Ch 0，實測 |
| 多 agent 的倍數 | Anthropic 的資料：agent 約為一般對話的 4 倍 token，多 agent 系統約 15 倍 | [Anthropic 多 agent 研究系統的工程文章](https://www.anthropic.com/engineering/multi-agent-research-system) |

最後一列不是 pstack 的數字，是 Anthropic 自己的系統的數字，但 pstack 的 arena（2+ runner + 評審）、swarm（十條 lane）、why（最多 7 個調查員 + 1 個 synthesizer）都是多 agent 結構。一次完整的 architect（how + arena 兩個 runner + 評審）加上 interrogate（兩個對手），量級上接近那篇文章描述的多 agent 系統。

同一篇文章還有一句對 pstack 的「fearless parallelism」很重要的話：Anthropic 觀察到大部分的程式設計任務，真正可以平行化的部分比研究任務少。這不否定 pstack——pstack 的平行化大多用在**驗證與設計探索**（swarm、arena），而不是把一個功能切成很多塊平行寫（Feature playbook 明確規定「Code-coupled work goes to a single owner」）。但它提醒你：平行化的報酬取決於任務的形狀。

**這筆帳什麼時候划算？** 用 Ch 22 的方式問：「什麼結果會改變你的決定？」如果一個 bug 修錯的代價是一次生產事故，幾萬個 token 很便宜；如果是一個 typo，用 poteto-mode 是在花錢買形式。

## 二、在 Claude Code 上的具體缺口

本課在 Claude Code 上實際找到的問題：

| # | 缺口 | 後果 | 章節 | 狀態 |
|---|---|---|---|---|
| 1 | 評審小組的多樣性塌縮：Grok 對應成 inherit-parent，arena 的跨家族評審被跳過 | 「寫的人不能自己判」只做到一半 | Ch 17 | 設計上的降級，會「say so」 |
| 2 | `poteto-agent` 的 fallback 路徑仍是 `~/.cursor/plugins/` | 主 session 沒傳路徑時，delegate 找不到 poteto-mode 而停下 | Ch 16 | 推論，未實測；安全的失敗 |
| 3 | 執行期轉譯靠 context，長 session 壓縮後可能遺失對照表 | agent 回頭用 Cursor 名稱 | Ch 4 | 推論 |
| 4 | Cursor 專屬功能（Bugbot、cloud agent、Automations、內建 PR 工具）不存在 | 對應步驟要 skip；worktree 只隔離檔案、不隔離 port 與資料庫 | Ch 4、16、27 | adapter 有降級規則 |
| 5 | adapter 說可以傳 `effort`，但本課所用 session 的 Agent 工具 schema 只有 `model` | 推理強度的調整可能無法傳達 | Ch 4、17 | 依版本而定 |
| 6 | 官方版沒有為 Cursor 的 `create-skill` 列對照 | Authoring playbook 的第 1 步在 Claude Code 上沒有明確對應 | Ch 28 | 移植版改寫成 `plugin-dev:skill-development` |

這些都不是致命的，而且 pstack 的設計讓大部分缺口變成「慢」或「可見的降級」而不是「靜默的錯誤」（前提不成立就停、skip 要留一行、評審被跳過要說明）。但如果你在 Claude Code 上用 pstack 做一個重要的決定，你應該知道你得到的是哪一個版本的嚴謹度。

另外，官方 Claude Code 支援是 2026-10-16 才上線的（Ch 1）。本課寫作時距離那天只有 12 天，第 2、6 個缺口在你讀到時可能已經被修了。

## 三、五個信條在什麼條件下會失效

Ch 1 整理了貫穿全課的五個設計信條。逐一用 Attack the Premise 的方式問：它的前提是什麼？什麼時候不成立？

### 信條 1：可檢查勝過可信賴

**前提**：留下的痕跡會被讀。

**失效條件**：沒有人讀。一份三百列的決策日誌、一個每一步都寫了 `skip: <reason>` 的 todo、一份每個斷言都標了「實測」的回覆——如果 reviewer 只看最後一行「predicate 通過」，這些痕跡就只是成本。更糟的是，痕跡本身可能變成一種表演：agent 學會產出**看起來**很嚴謹的痕跡（每一步都打勾、每一列都有 commit SHA），而痕跡和實際工作之間的對應沒人檢查。

pstack 的防禦是 Ch 26 的對帳與異家族審查，但那兩件事本身也要有人做。

### 信條 2：真實產物勝過代理指標

**前提**：真實產物是可以取得的。

**失效條件**：驗證需要的環境不存在或很貴——生產資料、第三方服務的真實回應、只在特定硬體上出現的 bug、需要人類判斷的 UX。這時候 pstack 會要求 inconclusive（「'Inconclusive' or wrong-surface is not a pass」），這是誠實的，但它不會讓驗證變得可能。在這類專案上，pstack 的大部分嚴謹會停在「我們無法證明」。

### 信條 3：寫的人不能自己判

**前提**：有一個真正獨立的判斷者。

**失效條件**：判斷者和作者有一樣的盲點。在 Claude Code 預設環境下（缺口 1），評審和作者是同一個家族，甚至同一個模型；兩次抽樣的獨立性遠低於兩個家族。一個「另一個 agent 審過了」的結論，在這個條件下比它聽起來弱很多。

### 信條 4：能編碼成結構的，就不要寫成文字

**前提**：編碼出來的檢查是對的。

**失效條件**：檢查本身有 bug，而它的「0 problems」被當成證明。本課寫的檢查工具出錯了好幾次（見第五節）。一個錯的檢查比沒有檢查更危險，因為它給出一個看起來權威的「通過」。

pstack 的防禦是反覆要求「先看檢查失敗一次」。但這條紀律本身是文字——它沒有被編碼成結構。

### 信條 5：問人是最後手段

**前提**：agent 對「什麼是可逆的」「什麼是事實問題」的判斷是對的。

**失效條件**：agent 判斷錯了。它可能把一個產品決定當成事實問題（「我做了一個 prototype，A 比較快，所以選 A」——但使用者在乎的其實是 B 的可讀性），或把一個實質上不可逆的動作當成可逆（在團隊聊天室發訊息，Autonomy 把它歸在「直接做」，但訊息被一百個人看到之後，撤回並不等於沒發生）。

pstack 的 Autonomy 界線是作者在 Cursor 團隊內部的判斷。你的團隊的界線可能不同，而 Claude Code 的權限設定是比修改技能文字更可靠的地方去表達你的界線（Ch 15）。

## 四、哲學上的分歧

**「我不相信規劃」**。作者的立場是「the best spec is code」，計畫只在 prototype 回答了開放問題之後才寫。這和這個 repo 的 [spec_driven_development](../spec_driven_development/README.md) 課程站在相反的位置。兩者都有道理：當需求不清楚、需要多方對齊、或錯誤的代價很高時，先寫規格的價值很大；當需求清楚、而最大的不確定性是「這個做法跑起來怎樣」時，先寫程式碼比較快。pstack 的 Multi-phase plan 其實是一個折衷：它會產出非常詳細的計畫，只是不把它當成預設。

**逐字抄 playbook 的代價**。Ch 5 說明了為什麼要逐字抄（摘要會丟掉條件子句）。代價是：一個小任務的 todo 會很長、充滿 `skip: <reason>`，讀起來像官僚流程。對熟悉 pstack 的人，這些 skip 是有用的訊號；對新人，它們是雜訊。

**一個人的風格**。pstack 的很多規則是 poteto 個人的偏好（不准用破折號、PR 描述的段落順序、「No is an acceptable answer」的語氣）。作者自己準備了 `/automate-me` 讓你產生自己的版本——這是對這個問題最誠實的回應，但它也意味著：**照單全收 pstack，你得到的是另一個人的判斷**。

**快速變動**。169 個 commit、5 個月。一條你今天學到的規則，三個月後可能已經改了。這是一個活的系統的特徵，也是成本：你寫的自己的 mode 如果引用 pstack 的規則，要跟著它一起維護。社群移植版的 50 個 fork 檔案（Ch 4）是這個成本的一個具體例子。

## 五、一份關於本課自己的紀錄

這門課的寫作過程本身，就是一次「用 pstack 的標準工作」的實驗。依 Explain the Number 的精神，把觀察到的事實列出來，不加詮釋：

**本課寫的檢查工具，抓到了本課作者的違規：**

| 工具 | 抓到什麼 | 章節 |
|---|---|---|
| banned-words lint | 已寫好的四章裡有 4 處輕描淡寫詞（規則在寫作時就在 context 裡） | Ch 15 |
| banned-words lint | 之後的章節又出現 3 處（Ch 21 一處；Ch 23 兩處，其中一處是「非顯然」這個詞被誤判、一處是引用的反例） | 撰寫過程 | <!-- allow-banned: 討論那個被禁的字本身 -->
| lint-added | Ch 28 的 1 處新增違規、Ch 2 的 1 處在 code block 裡的違規 | Ch 30 |
| check-arena 的設計過程 | rubric 與隱藏測試被 commit 進了 repo，所有 runner 都看得到 | 練習 C |
| 人工讀日誌 | 決策日誌與計畫附錄描述了一個不存在的 prototype；N2 的十條 lane 是 N1 的複製品 | 練習 D |
| 事實查證 | 一個「263 行」的數字實際是 205 行；一個模糊的「9 月新增兩條原則」查證後確定是哪兩條 | Ch 27、Ch 12 |

**本課寫的檢查工具本身，出過的錯：**

| 工具 | 錯誤 | 章節 |
|---|---|---|
| prompt-lint | 正規表示式 `hour\b` 讓「for 2 hours」永遠不會觸發 | Ch 6 |
| interrogate 驗證器 | 只檢查「輸出不同」，漏了「違反意圖」 | Ch 20 |
| audit-log | 不認得「被取代的列」，在正確地追加修正後仍然失敗 | Ch 26 |
| skill-lint | 沒有略過 code block（21 個誤報）；讀錯 YAML 多行字串 | Ch 28 |
| blind-check | 沒檢查工作目錄本身的名稱 | Ch 29 |
| lint-added | 和 banned-words 對 code block 的處理不一致 | Ch 30 |

另外一個不是本課工具的錯，但同一類：Node.js 的 CPU profiler 把 89.5% 的時間算在錯誤的函式上（Ch 10）。

兩張表一起讀，能得到幾個比任何單一章節都更具體的結論：

1. **文字規則確實擋不住它想擋的事**——即使寫的人就是讀了那條規則的 agent
2. **編碼成結構確實抓到了文字規則漏掉的東西**——但只在工具被執行的時候
3. **每一支工具在第一次寫出來時都有盲點**，而那些盲點都是在「讓它跑在一個它應該抓到 / 不應該抓到的案例上」時才被發現的
4. **有兩個錯是所有工具都看不到、只能靠讀才發現的**（練習 D 的兩個）

這四點幾乎就是 pstack 自己的主張：Encode Lessons in Structure、先看檢查失敗、Prove It Works、以及「Read every output yourself」。這門課沒有證明 pstack 是對的——它只是一個人寫一門課的觀察——但它和 pstack 描述的失敗模式是一致的。

## 六、決策表：什麼任務該用什麼

| 任務 | 建議 | 理由 |
|---|---|---|
| typo、改名、一行修改 | 直接用 Claude Code | 嚴謹稅大於風險 |
| 你很熟的程式碼裡的小功能 | 直接用 Claude Code，**prompt 寫好五要素**（Ch 6） | 五要素已經拿到 pstack 大部分的好處 |
| 原因不明的 bug | `/pstack:poteto-mode`（Bug fix） | 重現、二分、紅先綠後的價值最高 |
| 跨模組的功能、會改變所有權 | `/pstack:poteto-mode`（Feature，會觸發 architect） | 設計兩次的價值最高 |
| 一次性的設計決定（命名、格式、演算法） | `/pstack:arena` | 不需要整套流程 |
| 已經寫好、你不太信任的小改動 | `/pstack:interrogate` 或 `/pstack:blast-radius` | 只要審查，不要重做 |
| 效能問題 | `/pstack:poteto-mode`（Perf / Hillclimb）+ `/pstack:benchmark-checklist` | 數字最容易騙人 |
| 你要離開、早上回來審的工作 | `/pstack:poteto-mode` + 過夜契約（會走 figure-it-out） | 決策日誌是唯一能讓你早上審的東西 |
| 探索性的、學習用的程式碼 | 不用 pstack | 你要的是快速迭代與理解，不是證明 |
| 驗證需要的環境你沒有 | 先建驗證技能（Ch 21），或接受 inconclusive | pstack 的嚴謹會停在「無法證明」 |
| 你的團隊有自己的強烈慣例 | `/automate-me` 或寫自己的 mode（Final Project） | 照單全收是別人的判斷 |

## 七、給不同角色的建議

**個人開發者。** 不要整套裝了就到處用。先挑三個成本低、報酬高的：`/pstack:interrogate`（審你不放心的改動）、`/pstack:benchmark-checklist`（任何你要報告的數字）、Bug fix playbook（原因不明的 bug）。用兩週，記下它們真的抓到了什麼。再決定要不要多用。

**Tech lead。** pstack 對團隊最有價值的不是技能，是**共同詞彙**（Ch 11）。「apply prove it works」「subtract before you add」在 code review 裡和在 agent 的 prompt 裡同樣好用。先讓團隊熟悉 24 條原則的名字，比強制每個人用 poteto-mode 有效。其次是 `/correct`：找出團隊 review 裡反覆出現的評論，把它們變成 lint（Ch 30）。

**平台 / DevEx 團隊。** 你要處理的是規模化的問題：驗證技能（Ch 21）是讓 agent 能自主工作的前提，而它要被當成基礎設施維護（`/maintain-verification-skill` 每天跑）。模型角色的設定（Ch 17）要集中管理，特別是評審小組的多樣性——如果公司只有一個模型供應商的合約，arena 與 interrogate 的價值會打折，這應該是一個被明確討論的決定。

**評估導入的人。** 用 Ch 29 的方法：挑三到五個你團隊真實的、已經解決過的任務，在去識別化的環境裡分別用「直接用 Claude Code」與「用 pstack」各跑幾次，用一份事先寫好的 rubric 比較。不要用 pstack 自己的範例任務評估 pstack——那些任務是為了展示它而選的。

## 八、如果只能帶走五件事

1. **給 agent 一個能 pass/fail 的完成條件**，而不是一個願望或一段時間（Ch 6、Ch 25）。這一條不需要任何技能就能做到，而它拿到了 pstack 大部分的好處。
2. **先看檢查失敗一次，再相信它的通過**（Ch 3、Ch 6、Ch 14、Ch 21）。本課寫的每一支檢查工具都證明了這一點。
3. **作者不能自己判**（Ch 16、Ch 20、Ch 24）。審查者要是另一個 agent，最好是另一個家族；在 Claude Code 上，這一點需要你主動補。
4. **重複的錯誤要變成結構，不是更多的文字**（Ch 15、Ch 30）。一條每次都載入的規則，在這個 repo 有五百多次違規。
5. **證據是指標，不是散文**（Ch 7、Ch 26）。commit SHA、檔案路徑、指令輸出；一段寫得很好的「我仔細檢查過了」什麼都沒證明。

這張表的使用方式：**先問風險，再問工具**。一個任務做錯的代價是什麼？可以撤回嗎？驗證需要的東西你有嗎？這三個問題的答案決定了要付多少嚴謹稅，工具只是付稅的方式。

如果你發現自己對大部分任務都選了「直接用 Claude Code」，那不代表 pstack 沒用——它可能代表你的任務大多風險低、而且你已經會寫好的完成條件。pstack 的價值集中在少數高風險的任務上，那些任務的一次失敗，往往就抵得過整年的嚴謹稅。

## 九、pstack 之外：同一類問題的其他解法

pstack 想解決的問題——agent 產出的程式碼品質不穩定、驗證會成為瓶頸、同樣的錯會一再發生——不是只有 pstack 一種解法。把它放在更大的工具箱裡看：

| 問題 | pstack 的做法 | 其他做法 | 什麼時候其他做法更好 |
|---|---|---|---|
| agent 修錯地方 | Bug fix playbook（重現、二分、紅先綠後） | 要求每個 bug fix PR 附一個失敗的測試（CI 檢查「這個 PR 的測試在 base 上會失敗」） | 團隊裡人和 agent 都要遵守同一條規則時，CI 比技能更公平 |
| 審查成為瓶頸 | 異家族的 agent 審查、interrogate | CODEOWNERS、分層 review（只有高風險路徑要人審） | 風險集中在少數模組時 |
| 同樣的錯一再發生 | /correct、Encode Lessons in Structure | 平台團隊統一維護 lint 規則與 golden path 範本 | 多個 repo 共用同一套規則時 |
| 驗證需要驅動真實的 app | 驗證技能（Ch 21） | 既有的 E2E 測試套件（Playwright、Cypress）加上 preview 環境 | 已經有成熟的 E2E 基礎設施時，驗證技能應該**呼叫**它，而不是重寫 |
| 測試碰不到的並發 bug | formal verification 技能 | 在設計審查時用 TLA+ 建模（AWS 的做法）、chaos testing | 系統夠關鍵、值得專人投入時 |
| 效能數字騙人 | benchmark-checklist | 持續的 benchmark CI（每個 commit 跑、畫趨勢圖） | 效能是產品的核心指標時 |

右邊那一欄大多是**人和 agent 都適用**的結構，而 pstack 的大部分機制是**寫給 agent 的指示**。這兩者不衝突：pstack 自己的 Encode Lessons in Structure 就在說「能變成結構的，就不要只寫成指示」。一個成熟的團隊最終應該把 pstack 裡最有價值的那幾條，變成 CI、lint、範本——到那時候，agent 用不用 pstack，這些規則都會被執行。

## 十、本課沒有涵蓋的部分

依認識論誠實的要求，列出 pstack 裡本課只帶過或完全沒有涵蓋的部分，以及要去哪裡讀：

| 部分 | 本課的處理 | 去哪裡讀 |
|---|---|---|
| Visual parity playbook | Ch 9 提了一句（截圖當 pin） | `playbooks/visual-parity.md` |
| Runtime forensics 的實作（CDP、heap snapshot） | Ch 10 只讀了 playbook，沒有實際對一個活的程序做 | `playbooks/runtime-forensics.md`、Chrome DevTools Protocol 文件 |
| Worktree cleanup playbook | 沒有涵蓋 | `playbooks/worktree-cleanup.md`、`scripts/worktree-audit.sh` |
| `watch-pr` 腳本 | Ch 24 描述，沒有執行（需要 Bun） | `poteto-mode/scripts/watch-pr/` 與它的測試 |
| `/statechart`、TLA+ 的實際使用 | Ch 21 描述，沒有執行（本機沒有 TLA+ 工具鏈） | `skills/statechart/`、learntla.com |
| `/ketchup-video`、`/make-bot-ui` | 只在技能地圖裡出現 | 各自的 SKILL.md；make-bot-ui 是給 Grok Bot 用的 |
| benny automation pack | 只在 Ch 1 的地圖外提到 | `automations/benny/FOR_AGENTS.md`；它依賴 Cursor Automations，無法在 Claude Code 上跑 |
| `typescript-best-practices` 的完整內容 | Ch 13 提了它的定位 | `skills/typescript-best-practices/SKILL.md` |
| `bugbot-triage.md` 的評分標準 | Ch 24 提了它的存在 | `poteto-mode/references/bugbot-triage.md` |
| 實際用 subagent 跑完一次 arena、swarm、interrogate | 本課用手寫的候選與 shell 行程示範資料流；練習 C 讓你自己跑 | 你自己的 Claude Code session |

最後一列是本課最大的限制：大部分章節示範的是 pstack 的**規則與資料流**，而不是一個真實的 agent 依照那些規則工作的過程。那個過程每次都不同，本課把它一律標成「示意」。你要親眼看到它，只能自己跑。

## 底層機制：pstack 真正在賣的是什麼

```
  表面上                                    底下
  ──────                                   ──────
  58 個技能、23 份 playbook、24 條原則        一個關於「agent 會怎麼出錯」的模型
                                            ＋
                                            一套讓錯誤「被看見」的機制
                                            （skip 理由、原則引用、證據指標、對帳、異家族審查）
                                            ＋
                                            一套讓錯誤「被擋下」的機制
                                            （check-plan、產生器、log.sh、verify skill）
```

你可以不用 pstack 的任何一個技能，而仍然拿走它最有價值的部分：**那個關於 agent 會怎麼出錯的模型**。修 bug 不先重現、第一個設計就照做、相信自己的「做完了」、量到快取、吞掉錯誤、把一次奇怪的 session 當成規則——這些失敗模式在任何 harness、任何模型上都會出現。pstack 是一份把它們寫下來、並且一一給出對策的田野筆記。

## 對比與取捨

| 選擇 | 你得到的 | 你付出的 |
|---|---|---|
| 照單全收 pstack（官方版） | 一套經過大量使用的完整流程；與上游同步 | token、時間、另一個人的風格、Claude Code 上的降級 |
| 社群移植版 | 自動路由、Claude Code 名稱 | 和原文的差異（42 個 policy fork）、hook 改變行為 |
| 只用幾個技能（arena、interrogate、benchmark-checklist） | 最高價值的部分、成本可控 | 沒有路由與 playbook 的結構 |
| 寫自己的 mode（Final Project） | 符合你的 repo 與團隊 | 維護成本、需要 eval 證明它有效 |
| 只學它的失敗模式模型，不裝 | 零成本 | 每次都要自己記得 |

## 踩雷集錦

1. **因為裝了 pstack 就對所有任務用 poteto-mode**：作者自己說小改動不需要。用決策表。
2. **把 Claude Code 上的 arena 結論當成跨家族的結論**：預設環境下評審被跳過。重要的決定要自己補第二意見。
3. **相信「0 problems」**：本課的六支工具都出過錯。檢查工具給的是必要條件，不是充分條件。
4. **照抄 poteto 的個人偏好**：破折號規則、PR 段落順序、Autonomy 的界線，都是一個人在一個團隊的判斷。用 `/automate-me` 或自己寫。
5. **把痕跡當成工作**：一份很長的決策日誌、一個充滿 skip 的 todo，如果沒有人讀、沒有對帳，只是成本。
6. **用 pstack 自己的範例評估 pstack**：那些範例是為了展示它而選的。用你團隊真實的、已經解決過的任務，在去識別化的環境裡比較（Ch 29）。
7. **以為「本課跑過了」等於「在你的環境會一樣」**：本課的數字來自一台 macOS、Node.js 26、Claude Code 2.1.295 的機器。你的版本、你的模型設定、你的 repo，都可能讓結果不同。

## 進階：再往深一層

- **量你自己的帳**：用 Claude Code 的用量資訊，對同一類任務分別記錄「直接做」和「用 poteto-mode」的 token 數與結果品質（例如之後發現的 bug 數）。累積十次，你就有一個比本章任何論述都可靠的決策依據——這是 Explain the Number 用在「要不要用 pstack」這個決定上。
- **讀 superpowers**：[obra/superpowers](https://github.com/obra/superpowers) 是另一個廣泛使用的 Claude Code 技能庫（社群移植版的 SessionStart 提示裡也提到它：「Other session-start mandates, such as superpowers, still apply」）。它有自己的 brainstorm → plan → execute 流程，在「要不要先規劃」這件事上和 pstack 的立場不同。對照讀兩者，你會更清楚哪些設計是普遍的、哪些是個人的。
- **追蹤缺口的修正**：本章第二節的缺口 2 與 6，用 `gh api` 追上游的 commit，看它們什麼時候被修、怎麼修。這是一個觀察「一個活的系統怎麼回應它的使用者」的好方法。

## 動手練習

1. 拿你過去一週交給 Claude Code 的十個任務，用本章的決策表逐一分類。有幾個該用 poteto-mode？有幾個用了會是浪費？
2. 選 Ch 1 的五個信條之一，在你的團隊或專案裡找一個它會失效的具體情境。你會怎麼補？
3. 從本章第二節的六個缺口裡挑一個，去上游的 commit 歷史裡查它現在修好了沒（`gh api "repos/cursor/plugins/commits?path=pstack&per_page=30"`）。如果還沒，寫一段你會怎麼修的說明——這就是 poteto-mode 說的「Broken skill mid-task → fix it in its own PR」。
4. 寫一段 200 字的「我會怎麼向主管解釋 pstack 值不值得導入」。要包含至少一個具體的數字、一個具體的缺口、一個具體的適用情境。

## 本章重點整理

- 成本：常駐約 3.6k tokens、poteto-mode 打開約 5.4k、一次 Feature 的指引約 10k 量級（推算）；多 agent 結構的 token 倍數可觀；平行化的報酬取決於任務形狀
- 在 Claude Code 上：評審多樣性塌縮、poteto-agent 的 fallback 路徑、執行期轉譯在長 session 的脆弱、Cursor 專屬功能、effort 欄位、create-skill 對照——大多是可見的降級而不是靜默的錯
- 五個信條各有失效條件：痕跡沒人讀、真實產物取不到、判斷者和作者同盲點、檢查本身有 bug、agent 對可逆性的判斷錯了
- 本課自己的紀錄：工具抓到作者的違規、工具本身也出錯、有兩個錯只能靠讀才發現——和 pstack 描述的失敗模式一致
- 最值得帶走的不是 58 個技能，而是那個「agent 會怎麼出錯」的模型

## 自我檢核

- [ ] 不看本章，能說出 pstack 在 Claude Code 上至少三個具體的缺口，以及每個的後果嗎？
- [ ] 選一個信條，說出它的前提與失效條件，並在你的工作裡舉一個例子
- [ ] 本章第五節的兩張表，支持了哪幾個 pstack 的主張？有沒有哪一點是它們**沒有**支持的？
- [ ] 依決策表，你下一個任務該用什麼？為什麼？
- [ ] 本章第九節的表格裡，哪一項在你的團隊已經用「人和 agent 都適用的結構」解決了？哪一項還只靠 agent 的指示？
- [ ] 如果只能從這門課帶走一件事，你會選第八節的哪一條？為什麼是它？

## 延伸閱讀

### 文章

- **[Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)**
  - **讀哪裡**：關於 token 用量與「什麼任務適合多 agent」的段落
  - **和本章的關聯**：本章成本表的來源；也是「程式設計任務的可平行化程度較低」這個觀察的出處
- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)**
  - **讀哪裡**：開頭「When (and when not) to use agents」
  - **和本章的關聯**：「先找最簡單的解法，只在需要時增加複雜度」——用在「要不要用 pstack」這個決定上

### 原始碼

- **[pstack README「make it yours」](https://github.com/cursor/plugins/blob/main/pstack/README.md)**
  - **能學到什麼**：作者自己對「這是我的風格」的說明
- **[obra/superpowers](https://github.com/obra/superpowers)**
  - **讀哪裡**：README 與它的技能清單
  - **和本章的關聯**：另一套 Claude Code 技能庫，用來對照哪些設計是普遍的、哪些是 pstack 特有的

### 對照閱讀

- **[spec_driven_development](../spec_driven_development/README.md)**（本 repo）
  - **和本章的關聯**：站在「先規劃」那一邊的完整論述
- **[harness_engineering Ch 41 — 該不該上 agent](../harness_engineering/41-when-to-agentify.md)** 與 **[Ch 42 — 漸進式落地](../harness_engineering/42-gradual-rollout-trust.md)**（本 repo）
  - **和本章的關聯**：「要不要導入」的決策框架；本章是把那個框架用在 pstack 這個具體的選擇上

全課只剩最後一步：把你學到的東西，變成一份替這個 repo 量身打造的 mode。

→ [Final Project：替這個 repo 打造 course-mode](./final-project-course-mode.md)
