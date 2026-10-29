# Ch 28 — skill 寫作學：只留會改變決策的文字

> **目標**：從 pstack 的「Authoring or modifying a skill」playbook 與它 58 個技能的寫法，歸納出一套寫 agent 技能的準則：什麼文字值得留、什麼該刪；description 怎麼寫；什麼時候拆成 references、什麼時候寫成 scripts；怎麼讓一條規則可以被引用、被檢查。你會寫一支技能 lint（frontmatter、名稱、description、相對連結），在 pstack 的 58 個技能與這個 repo 的技能上跑——並在過程中抓到這支 lint 自己的兩個 bug。

> **環境**：pstack 0.15.36、Claude Code 2.1.295、Node.js 26。lint 與統計數字是實際跑出來的。

## 為什麼需要這個？

前面 27 章都在讀別人寫的技能。從這一章開始，你要寫自己的。

寫給 agent 的文字和寫給人的文字有一個根本差異。官方指南：

> Agent-facing prose has a higher bar than human prose, because an unhelpful sentence becomes an instruction some future agent follows.

人讀到一句廢話會跳過；agent 讀到一句廢話會**照做**。「請仔細思考」會讓它多花 token 思考；「注意效能」會讓它在不需要的地方做優化；一個沒有說明適用範圍的規則，會被套用到所有地方。技能裡的每一句話都是一條指令，所以每一句話都要值得存在。

## 先建立直覺：技能是一份給陌生人的值班手冊

```
  想像你在寫一份值班手冊，讀者是：
  ─────────────────────────────
  - 半夜三點被叫起來的人（context 很少，時間很緊）
  - 會**逐字照做**的人（不會判斷「這句大概不重要」）
  - 讀完手冊之前什麼都不知道的人（沒有參加過你的會議）
  - 可能會被同時發給五十個人的手冊（一句錯話被執行五十次）

  所以：
  ✗ 背景故事、動機散文、「我們團隊相信……」
  ✗ 重述另一份手冊已經寫過的東西
  ✓ 「遇到 X，做 Y」
  ✓ 「Y 的完整步驟在 runbooks/y.md」
  ✓ 「做完 Y，用 Z 檢查」
```

## 一、Authoring playbook：四步與一段寫作原則

```
### Authoring or modifying a skill
**You own the skill's voice.**

1. Use the **create-skill** skill (Cursor's built-in for authoring SKILL.md files).
2. Validate the skill: frontmatter has `name` and `description`, referenced files exist, cross-skill links resolve.
3. Test cases if structural. Skip if subjective.
4. Run **Opening a PR**.
```

在 Claude Code 上，對應 Cursor `create-skill` 的是什麼？社群移植版改寫成「**plugin-dev:skill-development** skill (Claude Code's authoring guidance for SKILL.md files)」；這台機器上也有一個 `anthropic-skills:skill-creator` 技能（「Create new skills, modify and improve existing skills, and measure skill performance」）。官方版的 adapter 沒有為 `create-skill` 列對照，所以在 Claude Code 上跑官方版時，agent 要自己決定用什麼——這是 Ch 4 說的轉譯缺口的又一個例子。

playbook 最有價值的是後面那一段：

> When in doubt, delete. Keep only prose that changes a decision. Tell it to do the thing and skip the reason. Explain only when the rule is confusing without one. Match tone to scope. Point at structural sources (types, READMEs, config) per the **encode-lessons-in-structure** principle skill. Delegate to other skills by path. Don't restate. A workflow you keep hitting but isn't captured → propose a new skill.

拆成七條：

| 規則 | 意思 | pstack 裡的例子 |
|---|---|---|
| **When in doubt, delete** | 不確定一句話有沒有用，就刪 | 上游 commit log 裡有好幾次「concision pass」 |
| **Keep only prose that changes a decision** | 留下的每一句都要改變 agent 的某個決定 | poteto-mode 幾乎全是「條件 → 動作」（Ch 2） |
| **Tell it to do the thing and skip the reason** | 給指令，不要給理由 | 「Reproduce it yourself on the matching surface」 |
| **Explain only when the rule is confusing without one** | 例外：規則反直覺時，一句理由 | 「Interrupt-chained resumes silently drop directives, so fire a fresh subagent」 |
| **Match tone to scope** | 小技能用小篇幅 | `bro` 只有一句話；`multi-phase-plan` 有 2,242 字 |
| **Point at structural sources** | 指向型別、README、設定，不要抄它們的內容 | 「the rule shape shown in step 5 below」 |
| **Delegate to other skills by path. Don't restate.** | 叫另一個技能，不要把它的內容抄過來 | 「the **how** skill」「per the **arena** skill's Phase B」 |

「Tell it to do the thing and skip the reason」最違反人的寫作直覺。人寫文件喜歡解釋為什麼——因為人需要被說服。agent 不需要被說服，它需要知道做什麼。理由佔的 token 會稀釋指令，而且理由有時會被當成另一條指令（「因為效能很重要，所以……」可能讓 agent 在無關的地方也開始關心效能）。

但第二條馬上補上例外：**規則反直覺時，一句理由能防止 agent 把它「修正」掉**。「預設開新的 subagent」很反直覺（續用不是比較省嗎？），所以 poteto-mode 給了一句理由。

## 二、frontmatter：description 是觸發條件

Claude Code 與 Agent Skills 規格的 frontmatter 最少要有兩個欄位（Ch 4 已經查過規格）：

```yaml
---
name: kebab-case-name       # 等於資料夾名，小寫連字號，最多 64 字元
description: ...            # 什麼時候用我
---
```

pstack 還用了 `disable-model-invocation: true`（57 個技能），讓模型不會自己打開它們。

### description 寫什麼

description 的角色取決於技能**會不會被模型自動觸發**：

| 情況 | description 的讀者 | 該寫什麼 |
|---|---|---|
| 模型會自動觸發 | 模型（在每個 session 的技能清單裡） | **觸發條件**：什麼情況、什麼關鍵字、和哪個相近的技能不同 |
| `disable-model-invocation: true` | 人（選單裡的說明） | 一句話說明它做什麼 |
| 被其他技能叫用 | 呼叫它的技能作者 | 它的輸入與輸出 |

看 pstack 裡寫得最細的幾個 description：

```yaml
# how
description: "Use for \"how does X work\", code walkthroughs before changing something, and placement /
  ownership / layering questions (\"where should this live\", \"which package owns this\", \"is this the
  right layer\"). Explains subsystem architecture, runtime flow, onboarding mental models. Use why for motivation."
```

三個技巧：**列出使用者真的會說的話**（「where should this live」）；**說它產出什麼**；**最後一句劃清和相近技能的界線**（「Use why for motivation」）——和 Ch 5 的 playbook 索引「Distinct from X」是同一個技巧。

### pstack 的 description 有多長

用本章的 lint 量（處理過 YAML 的多行寫法）：

```
description chars: min [ 30, 'unslop' ] median [ 196, 'principle-encode-lessons-in-structure' ] max [ 462, 'maintain-formal-verification-skill' ]
```

最短的 `unslop` 只有 30 個字元（「Cut AI tells from any writing.」）——它的名字就說明了一切。最長的 462 字元是一個需要精確說明觸發時機的技能。中位數約 200 字元，大約兩到三句話。

Ch 0 量過：58 個技能的 description 加起來，每個 session 常駐約 3,577 tokens。description 寫長一倍，就是每個 session 多付一倍。

## 三、內文的寫法：pstack 的六個慣例

讀完 58 個技能，可以歸納出幾個 pstack 一致使用的寫法：

**1. 條件 → 動作。** Ch 2 的主題。「Nontrivial change ... → the **how** skill.」可檢查、可逐條修改。

**2. 穩定的編號。** unslop 的規則編號「are stable ids that other skills cite. A removed rule leaves a gap.」（Ch 23）。任何會被其他地方引用的規則清單，都應該有不會變動的識別碼。

**3. 用名稱或路徑引用，不重述。** 「the **sequence-verifiable-units** principle skill」「per `playbooks/shipping.md` step 4」。被引用的東西只存在一份。

**4. 「Distinct from」。** 相近的技能、原則、playbook，明確寫出差別。路由與選擇最難的部分不是「這是什麼」，而是「這和那個很像的有什麼不同」。

**5. 「你跳過了這條的徵兆」。** Build the Lever：「If you cited it and there is no codemod, script, generator, or delegate skill in the diff, you didn't apply it.」讓違反可以被看見（Ch 11）。

**6. 不寫給人看的修辭。** pstack 在 2026-09-08 有一個 commit：「replace semicolons, em dashes, and connector colons in skill prose with periods or commas」。技能本身也遵守 unslop——技能裡的寫作風格，會被 agent 模仿到它的輸出裡。

## 四、什麼時候拆檔：references 與 scripts

```
my-skill/
├── SKILL.md              ← 一定會讀的部分：觸發後的流程、規則
├── references/           ← 某些步驟才需要讀的細節
│   ├── template.md
│   └── checklist.md
└── scripts/              ← 能被執行的部分
    └── check.mjs
```

pstack 的拆法：

| 拆出去的東西 | 例子 | 為什麼拆 |
|---|---|---|
| 只有某些情況才需要的細節 | `why/references/sources/incident-postmortem.md`（只在目標程式碼看起來是防禦性的時候附加） | 按需載入（progressive disclosure 的第三層） |
| 會被原樣傳給 subagent 的 prompt | `how/references/explorer-prompt.md`、`architect/references/runner-prompt.md` | 主 session 不需要讀它，負責把它傳下去 |
| 模板 | `architect/references/rationale-template.md`、`show-me-your-work/references/decision-log-template.tsv` | 結構化的產物格式 |
| 能被機器檢查的規則 | `poteto-mode/scripts/check-plan.mjs` | Encode Lessons in Structure |
| 能被機器執行的動作 | `show-me-your-work/scripts/log.sh`、`poteto-mode/scripts/watch-pr/` | Build the Lever |

判斷標準：**如果一段內容不是每次觸發都要讀，拆出去；如果一個規則能被程式檢查，寫成腳本**。

create-verification-skill 對 helper 有一條很好的要求：「any script the skill ships is executable and its invocation is shown in the skill body. A helper the reader has to reverse-engineer is not a helper.」

## 五、驗證：把 playbook 的 step 2 寫成 lint

Authoring playbook 的 step 2：「Validate the skill: frontmatter has `name` and `description`, referenced files exist, cross-skill links resolve.」照 Build the Lever，寫成一支工具：

```js
// skill-lint.mjs <skills-dir>...：檢查每個技能的 frontmatter、名稱、description 與相對連結
import fs from "node:fs";
import path from "node:path";
const NAME = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const roots = process.argv.slice(2);
let skills = 0, problems = 0;
const report = (dir, msg) => { problems++; console.log(`${path.basename(dir)}: ${msg}`); };
for (const root of roots) for (const d of fs.readdirSync(root)) {
  const dir = path.join(root, d), file = path.join(dir, "SKILL.md");
  if (!fs.existsSync(file)) continue;
  skills++;
  const text = fs.readFileSync(file, "utf8");
  const fm = text.match(/^---\n([\s\S]*?)\n---\n/)?.[1];
  if (!fm) { report(dir, "no frontmatter, the skill never registers"); continue; }
  const name = fm.match(/^name:\s*(.+)$/m)?.[1].trim().replace(/^["']|["']$/g, "");
  let desc = fm.match(/^description:\s*(.+)$/m)?.[1].trim();
  if (desc && /^[>|][-+]?$/.test(desc)) desc = (fm.match(/^description:.*\n((?:[ \t]+.*\n?)+)/m)?.[1] ?? "").replace(/\s+/g, " ").trim();
  if (!name) report(dir, "no name");
  else if (name !== d) report(dir, `name "${name}" is not the folder name`);
  else if (!NAME.test(name) || name.length > 64) report(dir, "name is not lowercase-hyphen, max 64");
  if (!desc) report(dir, "no description");
  else if (desc.length > 1024) report(dir, `description is ${desc.length} chars, over 1024`);
  const prose = text.replace(/^(```|~~~)[\s\S]*?^\1/gm, "");
  for (const [, href] of prose.matchAll(/\]\(([^)\s#]+)(?:#[^)]*)?\)/g)) {
    if (/^[a-z]+:/i.test(href)) continue;
    if (!fs.existsSync(path.resolve(dir, href))) report(dir, `link ${href} does not resolve`);
  }
}
console.log(`${skills} skills, ${problems} problems`);
process.exit(problems ? 1 : 0);
```

（1024 字元的 description 上限是 Agent Skills 規格的限制；以你讀到的規格版本為準。）

### 這支 lint 本身的兩個 bug

**第一版的 lint 沒有略過 code block。** 對這個 repo 的 `.claude/skills/` 跑：

```
learn: link ./<category>/<topic>/README.md does not resolve
learn: link user_<topic>_learning.md does not resolve
learn: link ./0N-xxx.md does not resolve
...
learn: link URL does not resolve
2 skills, 21 problems
```

21 個「問題」全是誤報：`learn` 技能裡有一大段 README 與章節的**模板**，模板放在 code block 裡，裡面的 `[Ch N](./0N-xxx.md)` 是佔位符，本來就不該解析。這和社群移植版 0.9.107 修的 check-plan bug 是同一類：「`check-plan.mjs` no longer counts text inside a fenced example toward the required plan guidance」。**範例裡的文字不是規則**——任何檢查 markdown 的工具都要處理這件事。

**第一版的 lint 讀錯了 YAML 的多行字串。** 量 description 長度時，最短的一個是 3 個字元。追下去：

```yaml
---
name: make-bot-ui
description: >-
  Use when building a custom UI (page, dashboard, buttons) that should wake a
  Grok Bot over a webhook, when the user must provide a webhook sender key, or
  when exposing that UI on Tailscale.
```

`>-` 是 YAML 的折疊字串，真正的內容在下面的縮排行裡。第一版的正規表示式把 `>-` 當成整個 description——它會讓一個**有** description 的技能被判成 description 很短，也可能讓一個 description 真的是空的技能被判成正常。

兩個都修好之後：

```
58 skills, 0 problems
2 skills, 0 problems
```

pstack 的 58 個技能與這個 repo 的 2 個技能都通過。這兩個 bug 是本課第五、第六次「檢查工具本身有 bug」（前面是 Ch 6 的 `hours`、Ch 10 的 profiler、Ch 20 的驗證器漏了條件、Ch 26 的稽核不認得取代列）。教訓一直是同一個：**一支檢查工具要先在它應該抓到、和它不應該抓到的案例上都跑過，才能信任它的「0 problems」**。

（更好的做法是用一個真正的 YAML 解析器，而不是正規表示式。本章為了不依賴套件而用正規表示式，代價就是這種 bug。）

## 六、Test cases if structural, skip if subjective

Authoring playbook 的 step 3。什麼是 structural？

- 技能規定了一個**輸出格式**（例如 check-plan 規定的計畫格式、show-me-your-work 的 TSV 欄位）→ 寫一個檢查格式的測試
- 技能附了**腳本**→ 腳本要有測試（pstack 的 `check-plan.test.ts`、`watch-pr/*.test.ts`）
- 技能的效果是**改變 agent 的判斷**（例如 unslop 讓文字更好）→ subjective，不寫單元測試；要測就用 Eval playbook（下一章）

## 對比與取捨

| 寫法 | 優點 | 缺點 | 適合 |
|---|---|---|---|
| 散文式（背景、動機、原則） | 人讀起來舒服 | agent 照做時會稀釋指令；不可檢查 | 給人看的文件 |
| **條件 → 動作 + 路徑引用（pstack）** | 可檢查、可逐條修改、不重複 | 寫起來難；讀起來生硬 | 給 agent 的技能 |
| 全部寫成腳本 | 一定會被執行 | 只能處理能被機器判斷的事 | 格式、檢查、確定性的動作 |
| 混合：SKILL.md 寫判斷，scripts 寫檢查 | 各取所長 | 要維護兩種東西 | pstack 的大型技能 |

## 踩雷集錦

1. **寫背景故事**：「我們團隊在 2024 年遇到一次事故，所以……」agent 不需要被說服。給指令；只有在規則反直覺時給一句理由。
2. **把另一個技能的內容抄過來**：兩份內容會漂移。用名稱或路徑引用。
3. **description 寫成產品介紹**：「一個強大的、全面的程式碼審查工具」——模型無法從這句話判斷什麼時候該用它。寫觸發條件、使用者會說的話、和相近技能的區別。
4. **檢查工具不略過範例**：本章的 lint 把模板裡的佔位符連結當成壞連結，21 個誤報。範例裡的文字不是規則。
5. **在任務中途改技能**：官方 Pitfall：「don't edit a skill mid-task because it's misbehaving. Fix it in its own PR and keep the task moving. A skill edit that ships tangled into feature work is invisible to review and impossible to evaluate.」

## 進階：再往深一層

- **用 `claude plugin details` 量你的技能的成本**：把你的技能打包成 plugin（或放在一個測試 plugin 裡），`claude plugin details` 會列出每個技能的 always-on 與 on-invoke token 數（Ch 0）。這讓「When in doubt, delete」有了一個可以量的目標。
- **讀上游的 concision pass commit**：`gh api "repos/cursor/plugins/commits?path=pstack&per_page=100" --paginate --jq '.[] | select(.commit.message | test("concision|density|tighten"; "i")) | .commit.message | split("\n")[0]'`。看作者刪掉了哪些句子，比讀任何寫作指南都有效。
- **技能也需要 changelog**：社群移植版的 `CHANGES.md` 每一條都寫了改了什麼、為什麼、怎麼驗證（甚至包括「這個 run 無法區分兩個 prompt」這種負面結果）。一個會被很多 agent 使用的技能，它的改動歷史和程式碼的改動歷史一樣重要。

## 動手練習

1. 拿你的 `CLAUDE.md` 或一個你寫過的技能，逐句問「這句話改變了 agent 的哪個決定？」答不出來的句子刪掉。刪了多少？
2. 照本章寫出 skill-lint，然後**故意**寫三個壞技能（沒有 frontmatter、name 和資料夾名不同、一個指向不存在檔案的連結），確認 lint 抓得到每一個。再寫一個**合法但特殊**的技能（description 用 `|` 多行、內文有一個 code block 裡的假連結），確認 lint 不會誤報。
3. 為這個 repo 的 `learn` 技能寫一個更好的 description：列出使用者真的會說的話、說它產出什麼、和「直接回答一個概念問題」劃清界線（提示：它的原文已經做了很多，看看還能怎麼改進）。

## 本章重點整理

- 寫給 agent 的文字標準更高：每一句都會被照做
- Authoring playbook 的寫作原則：不確定就刪；只留改變決策的文字；給指令不給理由（除非規則反直覺）；篇幅配合範圍；指向結構化來源；用路徑引用其他技能，不重述
- description 的角色取決於會不會被自動觸發；寫觸發條件、使用者會說的話、和相近技能的區別；pstack 的 description 中位數約 200 字元
- pstack 的寫法慣例：條件 → 動作、穩定編號、名稱引用、Distinct from、「你跳過了的徵兆」、技能本身也遵守 unslop
- 不是每次都要讀的拆到 references，能被機器檢查或執行的寫成 scripts
- 本章的 skill-lint 在 pstack 與本 repo 上都通過——但在那之前，它自己有兩個 bug（沒略過 code block、讀錯 YAML 多行字串）

## 自我檢核

- [ ] 不看本章，能說出 Authoring playbook 寫作原則的七條嗎？哪一條最違反你的寫作習慣？
- [ ] 一個 `disable-model-invocation: true` 的技能，description 該寫什麼？和會被自動觸發的技能有什麼不同？
- [ ] 什麼樣的內容該拆到 references？什麼樣的該寫成 scripts？各舉一個 pstack 的例子
- [ ] 本章的 lint 第一版在 `learn` 技能上報了 21 個問題，為什麼它們全是誤報？你自己寫過的檢查工具裡，有沒有類似的盲點？

## 延伸閱讀

### 原始碼

- **[playbooks/authoring-a-skill.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/authoring-a-skill.md)**
  - **讀哪裡**：全文（127 字），特別是最後一段
- **[skills/how/](https://github.com/cursor/plugins/tree/main/pstack/skills/how)** 與 **[skills/why/](https://github.com/cursor/plugins/tree/main/pstack/skills/why)**
  - **讀哪裡**：SKILL.md 與 references/ 的分工
  - **能學到什麼**：一個技能怎麼把「主 session 要讀的」和「傳給 subagent 的」分開

### 官方文件與規格

- **[Claude Code 官方文件：Skills](https://code.claude.com/docs/en/skills)**
  - **讀哪裡**：frontmatter 欄位、技能的放置位置、progressive disclosure
- **[Agent Skills 規格](https://agentskills.io)**
  - **讀哪裡**：`name` 與 `description` 的限制
- **[Anthropic — Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)**
  - **這篇說什麼**：Anthropic 對 Agent Skills 設計的說明，包括 progressive disclosure 的三層與技能裡附帶程式碼的理由
  - **和本章的關聯**：本章「什麼時候拆檔」的官方論述

### 對照閱讀

- **[harness_engineering Ch 29 — Skills](../harness_engineering/29-skills.md)**（本 repo）
  - **和本章的關聯**：從 harness 的角度看技能怎麼被載入；本章是從作者的角度看技能怎麼被寫
- **[本 repo 的 `.claude/skills/learn/SKILL.md`](../../.claude/skills/learn/SKILL.md)**
  - **讀哪裡**：整份
  - **和本章的關聯**：一份寫給 agent、但風格與 pstack 很不同的技能——它大量使用散文、理由、範例模板。用本章的準則讀它，想一想哪些地方 pstack 的寫法會更好、哪些地方它的寫法其實更適合「產出教材」這個任務

寫好了一個技能，你怎麼知道它真的讓 agent 變好了？下一章的 Eval playbook 回答這個問題。

→ [Ch 29 Eval：盲測一個 skill 變更](./29-eval-playbook.md)
