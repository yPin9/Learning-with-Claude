# Ch 11 — 原則是可引用的詞彙

> **目標**：理解 pstack 為什麼把 24 條工程原則拆成 24 個獨立的技能檔，而不是寫成一份清單；搞懂「索引一行、全文在 leaf」「只能引用讀過全文的原則」「引用要說出它改變了哪個決定」這三條規則怎麼合起來，讓原則從裝飾變成可以被稽核的推理。你也會學到人怎麼用原則名稱**引導**一個跑偏的 agent，並寫一支工具，從 Claude Code 的對話紀錄裡自動檢查 agent 有沒有引用它沒讀過的原則。

> **環境**：pstack 0.15.36、Node.js 26。`cite-audit.mjs` 與它的輸出是實際跑出來的；測試用的對話紀錄是依照 Claude Code `.jsonl` 實際結構手寫的小型 fixture。

## 為什麼需要這個？

大部分團隊都有一份「工程原則」：KISS、DRY、YAGNI、「先寫測試」……。它們通常貼在 wiki 上，新人入職讀一次，之後就沒人看了。原因在於：**一份原則清單不會告訴你什麼時候用哪一條，也不會讓任何人在違反時發現**。

給 agent 一份原則清單，問題更嚴重。模型會很樂意在回覆裡寫「我遵循了 KISS 原則」，但你無法分辨它是真的因為這條原則改變了某個決定，還是只是把一個好聽的名字貼在它本來就要做的事情上。

pstack 的 24 條原則用了一套設計來解決這兩個問題。

## 先建立直覺：一本有索引的判例集

```
  一份原則清單（常見做法）              pstack 的原則系統
  ──────────────────────              ─────────────────────────────────────
  1. 保持簡單                          poteto-mode 的 Principles 段（索引）：
  2. 不要重複                            每條一行：名稱 + 什麼時候適用 + 一句結論
  3. 先寫測試                                     │
  ...                                            │ 觸發了才去讀
                                                 ▼
  沒有「什麼時候用」                    principle-<name>/SKILL.md（判例全文）：
  沒有「怎麼算違反」                      Why / Pattern / The test / Stop / 和誰不同
  引用無法稽核                                    │
                                                 ▼
                                       回覆裡：「<原則名> 讓我做了 <具體決定>」
                                       而且這個原則的全文必須在本 session 被讀過
```

像法律的判例集：索引告訴你「遇到這類案件，查哪一條」；判例全文告訴你「這一條的完整推理與界線」；判決書裡引用判例時，要說明這個判例**怎麼影響了本案的判決**，而且你不能引用一個你沒讀過的判例。

## 一、為什麼拆成 24 個檔案

24 條原則的檔案大小（位元組）差異很大：

```
  395  principle-exhaust-the-design-space
  519  principle-guard-the-context-window
  552  principle-laziness-protocol
  ...
 1996  principle-minimize-reader-load
 2386  principle-build-the-lever
 2614  principle-test-behavior-not-implementation
```

最短的不到 400 位元組（幾句話），最長的超過 2.5KB。拆檔的理由有三個：

**1. 按需載入。** 一次 Bug fix 通常只觸發 Fix Root Causes、Prove It Works、Sequence Work into Verifiable Units 幾條。拆檔讓 agent 只讀觸發的那幾條，而不是每次都讀完 24 條約 30KB。這是 progressive disclosure 的第二層（第一層是技能描述，第二層是 poteto-mode 本體裡的索引，第三層是 leaf 全文）。

**2. 可以被其他技能引用。** README 說：「the standalone files are there so other skills can reference a principle by name」。Bug fix playbook 寫「This is the canonical **sequence-verifiable-units** principle skill」；hillclimb 寫「(the **build-the-lever** principle skill)」。一條原則只在一個地方定義，所有 playbook 用名稱指向它。這是 Laziness Protocol「Consolidate decisions」用在知識本身。

**3. 原則之間可以互相指涉、劃清界線。** 原則的全文裡常有這種句子：

> This principle is distinct from [Redesign from First Principles](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-redesign-from-first-principles/SKILL.md), which rebuilds a design around a new requirement. It questions a fact the current design assumes.
> （Attack the Premise）

> Distinct from [Encode Lessons in Structure](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-encode-lessons-in-structure/SKILL.md), which makes a recurring instruction a durable guardrail. This is throughput and reviewability on the work in front of you.
> （Build the Lever）

> Distinct from [Prove It Works](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-prove-it-works/SKILL.md), which checks that an output is real.
> （Explain the Number）

相近的原則最容易被混用。明確寫出「我和那一條不同在哪」，讓 agent 在兩條之間做選擇時有依據。

## 二、索引的格式：一行三件事

poteto-mode 裡每條原則的索引都是同一個格式：

```markdown
- **Attack the Premise.** Two or more fixes that share one premise have failed the same gate.
  Write the premise down and test it directly before you write another fix that assumes it.
```

```
  **名稱.**  ←  可以在對話裡被說出來的詞
  觸發情境   ←  「什麼時候這條適用」（兩個以上的修正因為同一個前提失敗）
  一句結論   ←  「適用時要做什麼」（把前提寫下來、直接測它）
```

**觸發情境是最重要的部分。** 「保持簡單」沒有觸發情境，所以它永遠適用、也就永遠不會被特別想起。「Attack the Premise」的觸發情境非常具體：**兩個以上**的修正、**共用一個前提**、**在同一個關卡失敗**。這三個條件同時成立時，agent 才該想起它。一條能被精確觸發的原則，才會在對的時間被用上。

索引前面有一句：

> Read the leaf skill in full for any principle you apply.

索引不是原則本身。索引讓 agent 判斷「這條可能適用」，真正要用的時候去讀全文。

## 三、leaf 檔的結構

24 個 leaf 檔沒有統一的模板，但大多數會用到這幾種區塊：

| 區塊 | 作用 | 例子 |
|---|---|---|
| 開頭一句 | 原則本身 | 「When debugging, fix the fault, not the place where it surfaced.」 |
| **Why** | 這條原則在擋什麼 | 「A symptom fix hides the evidence and leaves the fault free to surface somewhere else.」 |
| **Pattern** | 具體做法，條列 | Fix Root Causes 的 8 條 |
| **The test** | 怎麼檢查自己有沒有做到 | Make Operations Idempotent：「What happens if this runs twice in a row?」 |
| **Stop** | 一個明確的停止條件 | Attack the Premise：「Do not start the next fix before the premise is written down and tested.」 |
| 「你跳過了這條的徵兆」 | 讓違反可以被看見 | Model the Domain：「The sign that you skipped this is a new feature that grows an existing if/else chain by one more branch」 |
| Distinct from | 和相近原則的界線 | 見上一節 |

「你跳過了這條的徵兆」特別值得學。Build the Lever 的版本：

> Applying this principle produces a file. If you cited it and there is no codemod, script, generator, or delegate skill in the diff, you didn't apply it.

這把一條原則變成了**可以從 diff 檢查的東西**：引用了 Build the Lever，diff 裡卻沒有任何腳本——那就是沒做。Explain the Number 也有一樣的寫法：「You skipped this when a run started without the result that would change a decision, when the evidence behind a number has no run count, no spread, or no named limiter」。

## 四、三條引用規則

poteto-mode Non-negotiables 的第一段：

> The Principles section below grounds every trigger. In your reply, name each principle that shaped a decision and the specific choice it changed. Cite only principles whose leaf SKILL.md you read this session.

三條規則各擋一種失敗：

```
  規則                              擋的失敗                     例子
  ────────────────────────────     ────────────────────────     ──────────────────────────────
  1. 每個觸發都以原則為依據          規則變成無來由的教條           「為什麼要先重現？」有答案
  2. 引用要說出它改變了哪個決定      裝飾性引用                     ✗「我遵循了 Laziness Protocol」
                                                                ✓「Laziness Protocol 讓我只改一個檔案，
                                                                    沒有另外抽一個 formatter 模組」
  3. 只能引用本 session 讀過全文的    憑名字腦補原則內容             「Laziness Protocol 大概是叫我偷懶」
```

第 3 條有一個很實用的副作用：**它讓原則的使用可以從 transcript 稽核**。如果回覆裡出現「Model the Domain」，對話紀錄裡就應該有一次讀取 `principle-model-the-domain/SKILL.md` 的工具呼叫。沒有，就是違規。

## 五、把稽核寫成工具：cite-audit

照 Build the Lever 原則，我們把「檢查引用有沒有讀過」寫成一支腳本。Claude Code 的對話紀錄是 JSONL，每一行一個事件；assistant 的事件裡，`message.content` 是一串 block，`type: "tool_use"` 的 block 記錄了工具名稱與參數，`type: "text"` 的 block 是回覆文字。

```js
// cite-audit.mjs：檢查最後一則回覆裡點名的原則，是否在本 session 讀過 leaf 檔
import fs from "node:fs";

const PRINCIPLES = [
  "Laziness Protocol", "Foundational Thinking", "Redesign from First Principles", "Attack the Premise",
  "Subtract Before You Add", "Minimize Reader Load", "Outcome-Oriented Execution", "Experience First",
  "Exhaust the Design Space", "Build the Lever", "Model the Domain", "Boundary Discipline",
  "Type System Discipline", "Make Operations Idempotent", "Migrate Callers Then Delete Legacy APIs",
  "Separate Before Serializing Shared State", "Prove It Works", "Fix Root Causes",
  "Sequence Work into Verifiable Units", "Test Behavior, Not Implementation", "Explain the Number",
  "Guard the Context Window", "Never Block on the Human", "Encode Lessons in Structure",
];
// poteto-mode 規定：leaf 名 = principle- + 標題轉 kebab case，保留連字號、去掉其他標點；例外會在索引裡寫明
const LEAF = { "Sequence Work into Verifiable Units": "principle-sequence-verifiable-units" };
const leafOf = (name) =>
  LEAF[name] ?? "principle-" + name.toLowerCase().replace(/[^a-z0-9 -]/g, "").trim().replace(/\s+/g, "-");

const events = fs.readFileSync(process.argv[2], "utf8").trim().split("\n").map((l) => JSON.parse(l));
const read = new Set();
let lastReply = "";
for (const e of events) {
  if (e.type !== "assistant" || !Array.isArray(e.message?.content)) continue;
  for (const b of e.message.content) {
    if (b.type === "tool_use") {
      const target = b.input?.file_path ?? b.input?.command ?? "";   // Read 的路徑，或 Bash 的指令
      for (const m of target.matchAll(/(principle-[a-z0-9-]+)\/SKILL\.md/g)) read.add(m[1]);
    }
    if (b.type === "text") lastReply = b.text;
  }
}
const cited = PRINCIPLES.filter((p) => lastReply.toLowerCase().includes(p.toLowerCase()));
let bad = 0;
for (const p of cited) {
  const ok = read.has(leafOf(p));
  if (!ok) bad++;
  console.log(`${ok ? "OK  " : "FAIL"} ${p} -> ${leafOf(p)}${ok ? "" : " cited but never read"}`);
}
console.log(`${cited.length} cited, ${read.size} leaves read, ${bad} unsupported`);
process.exit(bad ? 1 : 0);
```

**先驗證工具本身**：名稱到資料夾的轉換規則（poteto-mode 原文：「with the title in kebab case, hyphens kept and other punctuation dropped, unless the entry gives its name」）對 24 條是不是都對？拿上游的實際資料夾清單比對：

```
24 names, 24 leaf dirs, 0 unmapped
```

24 條全部對應得上。唯一的例外是 Sequence Work into Verifiable Units，它的 leaf 叫 `principle-sequence-verifiable-units`（少了 work 和 into），索引裡有明寫這個例外，工具裡用 `LEAF` 表處理。

**用一份 fixture 測試**。模擬一次執行：讀了 Feature playbook、用 Read 讀了 Model the Domain、用 Bash `cat` 讀了 Prove It Works，最後的回覆點名了三條原則：

```jsonl
{"type":"user","message":{"role":"user","content":"/pstack:poteto-mode add --json"}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/x/pstack/skills/poteto-mode/playbooks/feature.md"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Read","input":{"file_path":"/x/pstack/skills/principle-model-the-domain/SKILL.md"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"tool_use","name":"Bash","input":{"command":"cat /x/pstack/skills/principle-prove-it-works/SKILL.md"}}]}}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Model the Domain made me use a formatter table. Prove It Works made me pin a golden file. Laziness Protocol kept the diff to one file."}]}}
```

```bash
node cite-audit.mjs fixture.jsonl; echo "exit=$?"
```

```
FAIL Laziness Protocol -> principle-laziness-protocol cited but never read
OK   Model the Domain -> principle-model-the-domain
OK   Prove It Works -> principle-prove-it-works
3 cited, 2 leaves read, 1 unsupported
exit=1
```

它抓到了：Laziness Protocol 被引用了，但這個 session 從來沒讀過它的全文。注意引用本身寫得很好（「kept the diff to one file」是一個具體的決定）——但寫得好不代表它真的依據了那條原則的完整規則。Laziness Protocol 的全文有兩條（「Consolidate decisions」與「Minimize the diff」）；只憑名字，agent 不會知道第一條。

在真實的 Claude Code session 上用：

```bash
node cite-audit.mjs ~/.claude/projects/<slug>/<session-id>.jsonl
```

> 這支工具的限制：它用字串比對找原則名，所以「not a Laziness Protocol situation」這種否定句也會被當成引用；它只看最後一則文字回覆；它認得 `Read` 的 `file_path` 和 `Bash` 的 `command`，其他讀檔方式（例如透過 subagent 讀）它看不到——subagent 讀的檔案記在 `<session>/subagents/` 底下的獨立紀錄裡。這些都是可以改進的方向，但即使是這個粗糙的版本，也已經把「靠人記得去檢查」變成「跑一個指令」。

## 六、人怎麼用原則：引導，而不是叫用

官方指南第 8 頁：

> You don't invoke principles. You use their names to steer. Each name points at a complete rule the agent has already read, so one phrase redirects the work more precisely than a paragraph of instructions.

三個官方範例：

```text
use subtract before you add. delete the obsolete adapters first, then design what's left.
```

```text
apply prove it works. run the real import flow and show me the written records.
```

```text
separate before serializing shared state. give each attempt its own worktree, no locks.
```

這是共同詞彙的價值：**一個名字壓縮了一整段規則**。你不需要每次解釋「我希望你先刪掉不用的東西再加新的，因為……」，說「subtract before you add」就夠了——前提是 agent 讀過那條原則的全文（引用規則 3 的另一面）。

官方對學習方式的建議：

> Don't memorize the list. Skim it now, then come back when you catch the agent doing something a name here would have prevented. That's how the vocabulary sticks.

## 底層機制：引用為什麼能約束推理

```
  沒有引用規則                               有引用規則
  ────────────                              ──────────
  模型做決定 ──▶ 寫回覆                      模型做決定 ──▶ 要寫「原則 X 改變了決定 Y」
                  │                                          │
                  └─ 事後加上好聽的原則名      ┌─────────────┴──────────────┐
                     （合理化）                ▼                            ▼
                                     X 的全文讀過嗎？            Y 是具體的嗎？
                                     沒有 → 不能引用             「遵循了」→ 不算
                                     （或先去讀，可能發現         「讓我改用查表」→ 算
                                      決定其實違反了 X）
```

最有價值的是左下那條路：**要引用就得先讀全文，而讀全文常常會讓 agent 發現自己的決定其實不符合這條原則**。引用規則因此不只是事後的格式要求，它會在寫回覆的時候，回頭修正推理。

## 對比與取捨

| 方式 | 可觸發性 | 可稽核性 | context 成本 | 例子 |
|---|---|---|---|---|
| wiki 上的原則清單 | 低（沒有觸發情境） | 無 | 0（沒人讀） | 大多數團隊 |
| system prompt 裡的原則清單 | 中 | 低（無法區分真引用與裝飾） | 每輪都付 | 常見的 CLAUDE.md |
| **索引 + leaf + 引用規則（pstack）** | 高（每條有觸發情境） | 高（可從 transcript 檢查） | 索引常駐、全文按需 | poteto-mode |
| 把原則編碼成 lint | 最高（自動執行） | 最高 | 0 | Encode Lessons in Structure 的終點 |

最後一列提醒我們：原則的最終形態不是文字，是檢查。能編碼成 lint 的原則（例如「不准在 production code 用 `any`」），就不需要靠引用規則。引用規則是給那些**需要判斷、無法機械檢查**的原則用的。

## 踩雷集錦

1. **只讀索引就說懂了原則**：索引是觸發條件加一句結論。Fix Root Causes 的全文有 8 條 pattern，包括「When something fails only after a restart, suspect persisted state first」——索引裡完全沒有。
2. **接受「我遵循了 X 原則」這種引用**：沒有說出改變了哪個決定的引用，是裝飾。直接問：「如果沒有這條原則，你會做什麼不一樣的事？」答不出來，就是沒用上。
3. **把原則當成要叫用的技能**：24 條原則都是 `disable-model-invocation: true`，而且官方說「You don't invoke principles」。你用它們的名字引導，agent 在需要時讀它們。
4. **原則互相打架時亂選**：Laziness Protocol 說「最小改動」，Foundational Thinking 說「先把資料結構弄對」，兩者可能衝突。pstack 的解法是讓每條原則的**觸發情境**夠具體——大多數時候它們不會在同一個決定上同時被觸發。真的衝突時，回覆裡要說出你怎麼取捨，這本身就是一個「原則改變了決定」的引用。
5. **相信稽核工具沒有盲點**：本章的 cite-audit 看不到 subagent 讀的檔案、會把否定句當引用。工具給你的是「該去看哪裡」，不是最終判決。

## 進階：再往深一層

- **讓 cite-audit 看見 subagent**：Claude Code 把 subagent 的紀錄放在 `<session>/subagents/*.jsonl`。擴充工具，把主 session 與它所有 subagent 讀過的 leaf 合起來算。再想一想：主 session 引用一條**只有 subagent 讀過**的原則，算不算違規？（poteto-mode 的規則是「you read this session」；主 session 和 subagent 是不同的 context，主 session 沒讀過就不知道全文。）
- **把 cite-audit 接成 Stop hook**：Claude Code 的 Stop hook 在 agent 要結束回合時執行。如果 cite-audit 回傳失敗，hook 可以把結果加回 context，讓 agent 在回合結束前補讀那條原則、修正回覆。這是把引用規則從「要求」變成「強制」。參考 [harness_engineering Ch 30 — Hooks](../harness_engineering/30-hooks.md)。
- **為你的團隊寫三條原則**：每條都要有觸發情境、一句結論、一個「你跳過了這條的徵兆」。如果你寫不出徵兆，這條原則可能太模糊，沒辦法被稽核。

## 動手練習

1. 照本章寫出 cite-audit，跑通 fixture。然後修改 fixture，讓最後的回覆寫「This is not a Laziness Protocol situation」，看工具怎麼判斷。改進工具處理這個情況（或決定不處理，並說明理由）。
2. 從 24 條原則裡挑三條你最常在 code review 裡想說、但說不清楚的。讀它們的全文，各寫一句「用這個名字引導 agent」的 prompt。
3. 打開你的 `CLAUDE.md`（或團隊的工程規範），找一條「沒有觸發情境」的規則，用索引格式（名稱 + 什麼時候適用 + 一句結論）改寫它。

## 本章重點整理

- 24 條原則拆成 24 個 leaf 檔：按需載入、可被其他技能用名稱引用、可以互相劃清界線
- 索引一行三件事：名稱、觸發情境、一句結論；觸發情境越具體，原則越會在對的時間被想起
- leaf 檔常見的「你跳過了這條的徵兆」讓違反可以被看見（例如引用 Build the Lever 卻沒有腳本）
- 三條引用規則：以原則為依據、說出改變了哪個決定、只引用讀過全文的；第三條讓引用可以從 transcript 自動稽核
- 人用原則名稱引導 agent，而不是叫用它們

## 自我檢核

- [ ] 不看本章，能說出 pstack 原則系統的三層（技能描述、索引、leaf 全文），以及引用規則的三條嗎？
- [ ] 為什麼「只能引用讀過全文的原則」不只是格式要求，還會影響 agent 的推理？
- [ ] cite-audit 的三個盲點是什麼？哪一個你覺得最需要先補？
- [ ] 拿你最近的一次 code review，找出一則你本來可以用「一個原則名稱」代替一整段解釋的評論

## 延伸閱讀

### 原始碼

- **[poteto-mode/SKILL.md 的 Principles 段](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/SKILL.md)**
  - **讀哪裡**：從「## Principles」到「## Autonomy」之前
  - **能學到什麼**：24 條索引的原文，以及 leaf 命名規則
- **[principle-build-the-lever](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-build-the-lever/SKILL.md)** 與 **[principle-explain-the-number](https://github.com/cursor/plugins/blob/main/pstack/skills/principle-explain-the-number/SKILL.md)**
  - **讀哪裡**：最後一段（「you didn't apply it」「You skipped this when」）
  - **能學到什麼**：兩個「讓違反可以被看見」的寫法範本

### 官方指南

- **[docs/guide/08-principles.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/08-principles.md)**
  - **讀哪裡**：整頁
  - **能學到什麼**：用原則名稱引導的官方範例，以及 24 條原則的一句話摘要

### 官方文件

- **[Claude Code 官方文件：Hooks 的 Stop 事件](https://code.claude.com/docs/en/hooks)**
  - **讀哪裡**：Stop / SubagentStop 事件與 hook 輸出如何回到 context
  - **能學到什麼**：把 cite-audit 接成強制檢查所需的機制

### 文章

- **[Martin Fowler — Ubiquitous Language](https://martinfowler.com/bliki/UbiquitousLanguage.html)**
  - **這篇說什麼**：DDD 的「通用語言」——團隊共用一組精確的詞彙，讓溝通不會在翻譯中失真
  - **和本章的關聯**：pstack 的原則名稱就是人與 agent 之間的通用語言；一個名字背後有一份大家都讀過的完整定義，溝通才會精確

知道了原則系統怎麼運作，接下來逐條讀原則本身。先從十條 Core 原則開始。

→ [Ch 12 Core 原則十條](./12-core-principles.md)
