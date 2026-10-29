# Ch 7 — Investigation：/how、/why、/teach、/recall

> **目標**：學會在「動任何程式碼之前」先把系統搞懂的四個技能。`how` 解釋程式碼**現在怎麼運作**，`why` 挖出它**為什麼長這樣**，`teach` 把兩者織成一份讓人真的懂的解釋，`recall` 重建你自己最近的工作脈絡。你會讀懂 `why` 的五級信心框架，並在一個真實的 git 歷史上跑一次 `why` 的「程式碼錨點」步驟，親眼看到「有直接證據的理由」也可能已經不成立。

> **環境**：pstack 0.15.36、git 2.54、Node.js 26。示範用的 `jobs` repo 與所有 git 指令輸出都是實際跑出來的；agent 的回覆是示意。

## 為什麼需要這個？

官方指南第 3 頁的開頭：

> Editing code you don't understand is how subtle regressions ship, and that's as true for the agent as for you. Agents usually fail in one of two ways. They misread what you want, or they don't have the context to do the work right.

Ch 6 處理第一種（誤解需求）。這一章處理第二種：**context 不足**。一個沒搞懂系統就動手的 agent，會「在第一個看起來合理的地方修症狀」。`how` 先做比第二個 bug 便宜。

還有第二個理由：這四個技能都會讓 agent **用你能檢查的話解釋自己**。當 agent 可能比你更熟某段程式碼時，這是你監督它的方法。

## 先建立直覺：考古學家 vs 機械技師

```
            一段看起來很怪的程式碼：RETRY_LIMIT = 5
                              │
         ┌────────────────────┴────────────────────┐
         ▼                                         ▼
   機械技師（how）                            考古學家（why）
   「它怎麼運作？」                           「它為什麼是這樣？」
   ──────────────                           ──────────────
   讀程式碼、追呼叫流程                         讀 git log、PR、ticket、
   畫出資料怎麼流                              聊天紀錄、事故報告、監控
   輸出：Overview / Key Concepts /             輸出：找到什麼 / 能推論什麼 /
         How It Works / Where Things Live /           有哪些互相競爭的假設 /
         Gotchas                                       不知道什麼 / 查了哪些來源
         │                                         │
         └──────────────────┬──────────────────────┘
                            ▼
                    teach：織成一份講解
                  「它是什麼 → 怎麼運作 → 為什麼 → 邊界情況」
```

技師告訴你引擎現在怎麼轉；考古學家告訴你為什麼當初選了這顆引擎、那個理由現在還成不成立。修車之前兩個都要問。

## 一、Investigation playbook：唯讀，交付一份有引用的答案

全文只有 4 步：

```
### Investigation
**You own the answer. Plan, route, write.**
Investigation requests are read-only. They produce a cited explanation or a recommendation, not a code change.

1. Route through the **how** skill. For motivation questions, also route through the **why** skill.
2. Throughput checkpoint stays one line: `throughput checkpoint: n/a, read-only investigation`.
3. Produce the `how`-shaped output (Overview / Key Concepts / How It Works / Where Things Live / Gotchas),
   or a recommendation with a tradeoffs table if the request is a decision between alternatives.
4. Apply the **unslop** skill to the reply.

No PR, no babysit, no `architect` unless the investigation precedes a code change.
If it does, hand back to the user and re-route to Bug fix or Feature.
```

三個重點：

- **「read-only」是硬性的**。如果調查發現該修，它**不自己開始修**，而是交回給你、重新路由到 Bug fix 或 Feature。這是 Ch 5 說的「路由錯誤成本不對稱」的防線：調查變成修改，是你沒授權的行為。
- **兩種交付形式**：「X 怎麼運作」交付 how 格式的解釋；「該用 X 還是 Y」交付推薦 + 取捨表。
- **「are we sure?」的回答要有真實判斷**：「For 'are we sure?' answers, include your real judgment with reasons. Push back if the premise is wrong.」

觸發它最穩的片語是 `don't change any code yet`：

```text
/pstack:poteto-mode investigate why background jobs time out every few hours. give me what we know, what data you used, and your best hypotheses. don't change any code yet.
```

「what data you used」這句讓 agent 把**證據**和**猜測**分開，是官方指南特別點出的寫法。

## 二、`how`：先判斷複雜度，再決定要不要開 explorer

`how` 的流程：

```
  1. Assess complexity
       │
       ├─ Simple（單一模組、小工具、窄問題）
       │     → 不開 explorer，開一個 explainer 一次讀完講完
       │
       └─ Complex（跨多檔或多服務的子系統、橫切功能、整體架構）
             → 2. 把問題拆成 2–4 個角度，一次開 2–4 個唯讀 explorer（同一則訊息內平行）
             → 3. 等全部回來，開一個 explainer 把發現合成一份解釋
  4. Present：輕度編輯可以，不要大改 explainer 的輸出
```

「When in doubt, take the simple path.」對一個 20 行的檔案開四個 explorer 是浪費。

**模型分工**：explorer 用 `how explorer` 角色（Cursor 預設 Grok，快而便宜），explainer 用 `how explainer` 角色（預設 Opus，負責判斷與寫作）。在 Claude Code 裡兩者預設都會落到 Claude（Ch 17）。

**explorer 的回報格式**（`references/explorer-prompt.md`）值得抄進你自己的技能：

```
### Components Found   名稱、檔案路徑、一句話描述
### Flow               一步一步：哪個函式、哪個檔案、做什麼、下一步叫誰、傳什麼資料
### Files Read         讀過的每個檔案（讓 explainer 可以回頭查）
### Boundaries         這個子系統和其他部分的接點
### Non-Obvious Things 令人意外、有歷史原因、容易搞錯的地方
### Open Questions     追不下去或沒搞懂的地方，誠實寫出來
```

注意 `Files Read` 和 `Open Questions`。前者讓下游可以**驗證**，後者讓下游知道**哪裡不可信**。一份沒有 Open Questions 的探索報告，要嘛探索的範圍很小，要嘛有東西被藏起來了。

explainer 的寫作要求也很具體：「Say "the `UserService` calls `AuthClient.refresh()`" not "the service delegates to the client"」。具體的名字，不要抽象的角色。

## 三、`why`：七類證據、平行調查、五級信心

`why` 是 pstack 裡設計最細的技能之一。流程：

```
  Step 1  理解目標與問題（目標模糊就從對話推斷，說出你的理解，讓使用者可以糾正）
  Step 2  建立程式碼錨點（git blame / log / PR 內文）── 一定做
  Step 3  探索可用的 MCP，每一類證據開一個調查員，平行跑
            1 Source control（一定有）   2 Issue tracker     3 Long-form docs
            4 Team chat                  5 Observability     6 Error tracking
            7 Product analytics
  Step 4  一個 synthesizer 依信心框架合成
  Step 5  呈現，不准改寫信心用語
```

**Step 2 程式碼錨點**是在派任何調查員之前、在主 session 裡先做的。它用的是你每天都在用的 git 指令。我們在一個小 repo 上實際跑一次：

```js
// retry.mjs（目前的樣子）
export const RETRY_LIMIT = 5;

export async function runWithRetry(job) {
  for (let attempt = 1; attempt <= RETRY_LIMIT; attempt++) {
    try { return await job(); } catch (e) { if (attempt === RETRY_LIMIT) throw e; }
  }
}

export const BACKOFF_MS = 20_000;
```

問題：「`RETRY_LIMIT` 為什麼是 5？這個理由現在還成立嗎？」

```bash
git blame -L 1,1 retry.mjs
```

```
8e39c151 (dev 2026-04-18 23:40:00 +0800 1) export const RETRY_LIMIT = 5;
```

```bash
git log --oneline -20 -- retry.mjs
```

```
ca39559 chore(jobs): name the backoff constant (#93)
8e39c15 fix(jobs): raise retry limit to 5 (#87)
1042e92 feat(jobs): add runWithRetry (#41)
```

```bash
git log -1 --format=%B 8e39c15
```

```
fix(jobs): raise retry limit to 5 (#87)

Upstream payment API returned 503 for ~90s during the 04-18 incident.
3 retries with 20s backoff gave up at 60s; 5 covers 100s.
```

PR 編號從 commit 標題的 `(#87)` 這個格式抽出來；有 GitHub 的話，下一步是 `gh pr view 87 --json title,body,comments,reviews,...` 拉出 PR 討論。

到這裡，我們有一份**直接證據**（Direct）：作者親手寫了理由。依信心框架，這可以用肯定句、現在式、附引用：「`RETRY_LIMIT` 是 5，因為 04-18 事故中上游支付 API 回 503 約 90 秒（commit 8e39c15）。」

**但問題的後半是「這個理由現在還成立嗎」。** 理由的前提是「有 20 秒的 backoff」。看程式碼：`BACKOFF_MS` 被定義了，但 `runWithRetry` 根本沒用它。實際量：

```bash
node -e '
import("./retry.mjs").then(async ({ runWithRetry, RETRY_LIMIT }) => {
  let calls = 0; const t0 = Date.now();
  try { await runWithRetry(async () => { calls++; throw new Error("503"); }); } catch (e) {}
  console.log(`RETRY_LIMIT=${RETRY_LIMIT} calls=${calls} elapsed_ms=${Date.now() - t0}`);
});'
```

```
RETRY_LIMIT=5 calls=5 elapsed_ms=0
```

五次重試在 0 毫秒內全部打完。commit 說「5 covers 100s」，實際上只撐了 0 秒。而且就算有 backoff，5 次嘗試之間只有 4 次等待，20 秒 × 4 = 80 秒，也不是 100 秒——commit 訊息裡的算術本身就有問題。

一份誠實的 `why` 報告會這樣分層（示意）：

> **What We Found**（Direct）：commit 8e39c15 的訊息說，提高到 5 是為了撐過 04-18 事故中約 90 秒的 503。
>
> **What We Can Reasonably Infer**（Inferred）：commit 訊息的計算假設每次重試之間有 20 秒 backoff。`BACKOFF_MS` 在 #93 才被命名，但沒有任何程式碼使用它（`retry.mjs:9`）。實測 5 次失敗在 0ms 內耗盡（上面的指令）。所以 #87 的理由所依賴的機制**看起來**從未存在，或曾經存在但被移除了。
>
> **Competing Hypotheses**（Speculative）：(a) backoff 原本在呼叫端實作，後來被移除；(b) 作者以為 backoff 已經實作了。目前的證據無法區分。
>
> **What We Don't Know**：#87 的 PR 討論與 04-18 的事故紀錄沒有查（這個示範 repo 沒有 GitHub remote 與 MCP）。
>
> **Sources Consulted**：Source control（git blame / log / commit 內文）。Issue tracker、Docs、Chat、Observability、Error tracking、Analytics：跳過，這個環境沒有對應的 MCP（gap，不是選擇）。
>
> **Preserve / Change / Avoid / Risk**（因為這個問題是改程式碼的前奏）：Preserve「撐過約 90 秒的上游中斷」這個目標；Change 讓 backoff 真的生效，或重新計算次數；Avoid 只把次數調高（沒有 backoff 時次數多少都撐不過 90 秒）；Risk 加了 backoff 後，job 的最長執行時間會變長，要確認上游的 timeout。

這個例子示範了 `why` 的三個設計：

1. **直接證據 ≠ 理由仍然成立**。`why` 技能在 Common Failure Modes 裡第一條就是「Recency bias」：不要假設最近的 commit 就是權威。這裡的情況更微妙：理由是直接證據，但**理由所依賴的事實**是錯的。
2. **null 也要報**。「這個環境沒有 chat MCP，所以對話紀錄沒查」寫進 Sources Consulted，標成 gap。使用者因此知道答案的覆蓋範圍。
3. **為改程式碼做準備**。如果 `why` 是改程式碼的前奏，最後要轉成 Preserve / Change / Avoid / Risk 的限制清單，直接餵給下一步的 Feature 或 Bug fix。

## 四、五級信心框架（`references/epistemics.md`）

| 層級 | 定義 | 用語 | 放在哪一段 |
|---|---|---|---|
| **Direct** | 有人**寫下**了為什麼（PR 描述、ticket、註解、設計文件、作者的訊息） | 肯定句、附引用 | What We Found |
| **Supported** | 多個間接證據指向同一結論 | 肯定但標明是推導，列出每一項證據 | What We Found |
| **Inferred** | 合理的解讀，但沒有東西明確支持 | 「看起來」「可能」，推理鏈要寫出來 | What We Can Reasonably Infer |
| **Speculative** | 薄弱證據上的假設，其他解釋也一樣說得通 | 「一種可能是……，但我們沒有直接證據」 | Competing Hypotheses |
| **Unknown** | 查了，查不到 | 具體寫出查了哪裡、用什麼關鍵字、找到什麼 | What We Don't Know |

這份文件還有幾條很值得內化的規則：

- **用了確定詞就要附引用**：「because」「the reason is」「was designed to」「the team decided」這些詞宣稱了作者意圖，只能用在 Direct 或 Supported。
- **不准用的字**：obviously、clearly、of course、just、I think。理由：「If it were obvious, the user wouldn't be asking.」 <!-- allow-banned: 引用 epistemics.md 的禁用字清單 -->
- **不要合理化**：今天看起來合理的程式碼，當初可能是基於現在已不成立、甚至當時就錯的理由寫的。「Don't turn an absence of evidence into evidence of absence.」
- **討好陷阱**：使用者問「為什麼這樣做，我猜是為了效能？」——不要直接確認。他的猜測是調查的起點，不是要你背書的結論。
- **定稿前檢查**：如果輸出裡沒有 What We Don't Know，「that's suspicious」。

這個框架不只適用於 `why`。它是 poteto-mode「每個斷言帶實測 / 推論 / 猜測標籤」那條規則的完整版。

## 五、`teach`：一份讓人真的懂的解釋

`teach` 疊在 `how` 和 `why` 上面：先讓兩者去挖，再把結果織成一份講解。它的寫作規則很反直覺：

- **先給一個平白的定義**，用資深工程師口頭會說的方式，再連到眼前的案例
- **先給最小的完整答案**（一兩句），然後停下來，等對方問再加深
- **不准印框架標籤**：「the one idea to hold onto」「the key insight」「TL;DR」都不行
- **不准考試、不准演戲**：不要叫對方複述、不要宣布「這是最重要的部分」
- **唯一不能改寫的是 `why` 的信心用語**：「its hedges are findings, not style」

最後一條很重要：teach 可以自由改寫 how 的內容讓它更好懂，但 why 說「看起來」的地方，teach 不能把它改成「是」。不確定性是發現，不是修辭。

官方推薦的用法：

```text
/pstack:teach me how this PR changes retries. convince me it fixes the cause and not the symptom.
```

「convince me」把一份導覽變成一個你可以檢驗的論證。也可以拿來問 agent 自己的選擇：

```text
/pstack:teach me why you implemented it this way and not with a queue. what did you trade off, and why?
```

## 六、`recall`：重建你自己的脈絡

`recall` 從你自己最近的對話紀錄、加上共享紀錄（issue、修過又被 revert 的 fix、還在觸發的錯誤），重建「現在進度到哪、下一步是什麼」。

關鍵步驟：

1. **先分類再路由**：要接手**某一個**特定對話，是 Session pickup playbook，不是 recall
2. **鎖定範圍**：時間窗預設最近 7 天；**預設只看目前的 workspace**，沒被要求不准讀別的專案的紀錄；「Never quietly turn 'all' into 'recent N'」
3. **平行挖**：用便宜快速的模型開幾個 subagent，各自負責一部分對話，依實際修改時間排序、先 grep 主題再只讀相關段落；原始紀錄留在 subagent 裡，主 session 只拿摘要

在 Claude Code 裡，對話紀錄的位置依 adapter 是 `~/.claude/projects/<slug>/<session>.jsonl`，slug 是 workspace 路徑把 `/` 和 `.` 換成 `-`。例如這個 repo：

```bash
ls ~/.claude/projects/-Users-robertliao-Documents-github-Learning-with-Claude/
```

```
69a16b75-3be3-4049-b6e4-6ef5e5c4b291
69a16b75-3be3-4049-b6e4-6ef5e5c4b291.jsonl
memory
```

（`.jsonl` 是對話本身；同名資料夾裡放 subagent 的紀錄；`memory` 是 Claude Code 的記憶功能。）

「只讀這個 workspace」這條規則在 pstack 裡反覆出現（recall、reflect、eval、session-pickup、automate-me 都有）：「Do not glob across `~/.cursor/projects/*/`. That crosses workspace boundaries and reads private chats from unrelated projects.」一個能讀你所有對話紀錄的 agent，預設應該只讀和當前任務有關的那一部分。

## 對比與取捨

| 技能 | 回答的問題 | 主要證據 | 產出 | 成本 |
|---|---|---|---|---|
| `how` | 它現在怎麼運作？ | 程式碼 | 架構解釋 | 低（Simple）到中（2–4 explorer） |
| `why` | 它為什麼長這樣？理由還成立嗎？ | git、PR、ticket、文件、聊天、監控 | 分級信心報告 + 限制清單 | 高（最多 7 個平行調查員） |
| `teach` | 讓我真的懂 | how + why 的結果 | 分層講解 | 中到高 |
| `recall` | 我上次做到哪？ | 你的對話紀錄 + 共享紀錄 | 現況摘要 + 下一步 | 中 |
| `blast-radius` | 這個改動會弄壞別處什麼？ | 程式碼 + 實際執行 | 一個被證明的安全事實 + 風險清單 | 中（Ch 20） |

官方建議的組合：「do why first then how」——當你懷疑歷史能解釋眼前的混亂時。

## 踩雷集錦

1. **「agent 反正會讀程式碼，不用先跑 how」**：官方 Pitfall 原文：「An agent that starts editing without a traced model tends to fix the symptom at the first plausible spot. `/how` first is cheaper than the second bug.」
2. **把直接證據當成理由仍然成立**：本章示範的 `RETRY_LIMIT` 有作者親筆寫的理由，但理由的前提（backoff）在程式碼裡不存在。「為什麼」和「現在還對不對」是兩個問題。
3. **Investigation 變成修改**：調查到一半發現問題，agent 順手修了。playbook 明確禁止：交回給你、重新路由。如果你看到 Investigation 產生了 diff，它偏離了。
4. **`why` 只查 git**：如果你的環境有 issue tracker、Slack 的 MCP，`why` 應該全部平行查。只查 git 的報告要在 Sources Consulted 裡寫出其他類別為什麼跳過；沒寫就是漏了。
5. **把你的猜測寫進問題裡**：「為什麼 retry 是 5，是為了撐過那次事故吧？」依 epistemics 的討好陷阱規則，好的 `why` 會把它當成一個待驗證的假設；但你一開始就不寫，結果更乾淨。

## 進階：再往深一層

- **讀 `why/references/sources/`**：每一類證據有自己的「怎麼查」手冊（`code-archaeology.md`、`slack.md`、`sentry.md`、`incident-postmortem.md`……）。`incident-postmortem.md` 只在目標程式碼「看起來是防禦性的」（null check、retry、timeout、rate limit、feature flag）時才附加。這是「按需載入參考資料」在一個技能內部的再一次應用。
- **把錨點步驟寫成腳本**：本章的四個 git 指令（blame、log、commit body、抽 PR 編號）每次都一樣。寫成一支 `anchor.sh <file> <line>`，輸出一份固定格式的錨點摘要，你的 `why` 就有一個可重跑的第一步（Build the Lever）。
- **`teach` 的反模式清單**是一份很好的技術寫作檢查表。下次你寫 onboarding 文件時，對照它刪掉「重點來了」「TL;DR」這類框架標籤。

## 動手練習

1. 照本章建出 `jobs` repo（三個 commit，日期與訊息照抄），自己跑一遍錨點的四個指令。然後**修好它**：讓 `BACKOFF_MS` 真的生效，再量一次 elapsed。5 次嘗試要多久？是 commit 說的 100 秒嗎？
2. 在你自己的一個專案裡，挑一個你不確定為什麼存在的常數或 if 判斷，手動跑 `why` 的 Step 2。寫一份只有 What We Found / What We Don't Know 兩段的迷你報告。
3. 用 epistemics 的五級，把你最近在 code review 留過的三則評論分級。有幾則用了「because」卻沒有引用？

## 本章重點整理

- Investigation 是唯讀的：交付有引用的解釋或推薦，發現該修就交回、重新路由
- `how` 先判斷複雜度，簡單就一個 explainer，複雜才開 2–4 個平行 explorer；回報要有 Files Read 與 Open Questions
- `why` 先建程式碼錨點，再按可用的 MCP 每類證據開一個調查員；用五級信心框架分層；null 也要報
- 有直接證據的理由，不代表它現在還成立——要回頭用真實行為檢驗
- `teach` 可以改寫解釋，但不能改寫 `why` 的信心用語；`recall` 預設只讀當前 workspace

## 自我檢核

- [ ] 不看本章，能說出 `why` 的五級信心，以及每一級的典型用語嗎？
- [ ] 本章的 `RETRY_LIMIT` 例子裡，哪一句是 Direct、哪一句是 Inferred？為什麼「BACKOFF 沒被使用」不能被寫成 Direct？
- [ ] 一份 `why` 報告沒有 What We Don't Know 段落，你會懷疑什麼？
- [ ] 想一個你團隊裡「大家都知道為什麼這樣做」的設計。如果用 `why` 去查，你預期能找到 Direct 證據嗎？找不到的話，那是什麼意思？

## 延伸閱讀

### 原始碼

- **[skills/why/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/why/SKILL.md)** 與 **[references/epistemics.md](https://github.com/cursor/plugins/blob/main/pstack/skills/why/references/epistemics.md)**
  - **讀哪裡**：SKILL.md 的 Step 2–3；epistemics.md 整份
  - **能學到什麼**：本章信心框架的原文；特別是「Avoid Rationalization」與「The Sycophancy Trap」兩節
  - **前提**：無
- **[skills/how/references/](https://github.com/cursor/plugins/tree/main/pstack/skills/how/references)**
  - **讀哪裡**：explorer-prompt.md 與 explainer-prompt.md
  - **能學到什麼**：兩份可以直接借用的 subagent prompt 模板
- **[skills/teach/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/teach/SKILL.md)**
  - **讀哪裡**：步驟 3–4 的寫作規則
  - **能學到什麼**：一份很少見的「教學反模式」清單

### 官方指南

- **[docs/guide/03-understand.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/03-understand.md)**
  - **讀哪裡**：整頁
  - **能學到什麼**：四個技能的官方 prompt 範例與 Session pickup 的對照

### 書籍與文章

- **《Working Effectively with Legacy Code》** — Michael Feathers（Prentice Hall, 2004）
  - **這本書的定位**：「先理解再修改」的經典；它的 characterization test 概念在 Ch 9 的 Refactoring playbook 會直接用到
  - **讀哪幾章**：Chapter 13「I Need to Make a Change, but I Don't Know What Tests to Write」與 Chapter 16「I Don't Understand the Code Well Enough to Change It」
  - **注意**：出版超過 20 年，範例語言老舊，但方法論完全適用
- **[Julia Evans — 系列 zine 與部落格](https://jvns.ca/)**
  - **為什麼值得讀**：她示範了「用問題驅動、用具體指令回答」的調查方式，和 `how` 的「具體的名字，不要抽象的角色」是同一種寫作態度

搞懂了系統，就可以開始修了。下一章是 pstack 裡最常用的 playbook：Bug fix。

→ [Ch 8 Bug fix：先重現、二分搜尋、紅先綠後](./08-bug-fix-playbook.md)
