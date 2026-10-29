# Ch 17 — 模型角色與 setup-pstack

> **目標**：理解 pstack 為什麼把工作拆成「角色」、每個角色配一個模型，以及 `/setup-pstack` 怎麼用一個預算等級調整所有角色的推理強度。你會把 setup-pstack 的換算規則寫成一支腳本，親眼看到一個關鍵事實：**在 Claude Code 上，pstack 預設的「兩家族評審小組」會塌縮成同一個家族**，arena 的跨家族評審會被跳過。最後討論怎麼用這台機器上已經裝好的 Codex plugin 把多樣性補回來。

> **環境**：pstack 0.15.36、Claude Code 2.1.295、Codex CLI 0.159.2（`openai-codex` marketplace 的 `codex` plugin）、Node.js 26。換算腳本與輸出是實際跑的；腳本裡「偵測到的模型清單」是本章假設的一組 Cursor slug，用來示範規則，不是從真實帳號偵測的。

## 為什麼需要這個？

一次 poteto-mode 執行會用到很多個模型呼叫：explorer 讀程式碼、explainer 寫解釋、delegate 寫程式、arena 的 runner 各寫一版、評審挑一版、swarm 的 worker 驗證……。如果全部用同一個最強的模型，很貴、很慢；全部用便宜的模型，判斷的品質會掉。

而且有些工作的價值**就來自模型不同**：arena 的評審要能看出 runner 看不出的問題，interrogate 的對手要能找到作者想不到的失敗。同一個模型評審自己的產出，會有同樣的盲點。

pstack 的回答是：把工作分成**角色**，每個角色配一個模型，用一份設定檔集中管理。

## 先建立直覺：一個團隊的人力配置

```
  角色                         Cursor 預設                  為什麼
  ─────────────────────────   ─────────────────────────   ──────────────────────────────
  寫程式（feature、bug-fix…）   grok-4.7-xhigh-fast          快、便宜、程式碼生成夠好
  最難的變更                   claude-opus-5-5-xhigh        跨切面設計、並發、精細演算法
  判斷與寫作                   claude-opus-5-5-xhigh        解釋、審查、回覆
  how 的 explorer              grok-4.7-xhigh-fast          大量讀檔，求快
  how 的 explainer             claude-opus-5-5-xhigh        把發現寫成解釋
  swarm 的 worker              grok-4.7-xhigh-fast          平行驗證，數量多
  arena / architect / interrogate 小組   opus + grok         兩個家族，互相看對方的盲點
```

README 的說法：「out of the box the mode splits work by model strength: code delegates ... go to grok, while the hardest changes, prose, and judgment go to opus 5.5. the default panel is opus 5.5 / grok.」

## 一、角色清單與設定檔

`/setup-pstack` 寫出的設定檔（Cursor 版在 `~/.cursor/rules/pstack-models.mdc`），預設內容：

```
# budget: large (xhigh)
feature, refactoring: grok-4.7-xhigh-fast
bug-fix: grok-4.7-xhigh-fast
perf-issue: grok-4.7-xhigh-fast
hillclimb: grok-4.7-xhigh-fast
judgment and prose: claude-opus-5-5-xhigh
hardest tasks: claude-opus-5-5-xhigh
how explorer: grok-4.7-xhigh-fast
how explainer: claude-opus-5-5-xhigh
why investigators: grok-4.7-xhigh-fast
why synthesizer: claude-opus-5-5-xhigh
reflect tooling: grok-4.7-xhigh-fast
reflect judgment, divergent, synthesizer: claude-opus-5-5-xhigh
arena runners: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast
arena cross-judge pool: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast
swarm workers: grok-4.7-xhigh-fast
architect runners: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast
interrogate reviewers: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast
```

17 個角色。幾個設計：

- **一個角色一行**，刪掉那一行就回到技能的預設值（「Delete a line to fall back to the skill default」）
- **小組角色的值是一個清單**，清單長度就是小組人數（每個項目一個 subagent）
- **`inherit-parent` 或 `auto`** 是特殊值：省略 subagent 的 `model` 欄位，讓它跑在主 session 的模型上
- 每個技能都**自己帶著預設值**，設定檔只是覆寫層：「every skill reads it and falls back to sensible defaults when the rule is absent, so you override only what you want」

在 Claude Code 上，依 adapter 文件，這個檔案寫在 `~/.claude/rules/pstack-models.md`（沒有 frontmatter），Claude Code 會在每個新 session 載入；role 的值寫成 `<model> <effort>`，例如 `opus xhigh`。

## 二、預算等級：一個旋鈕調整所有角色

`/setup-pstack` 先問一個預算：

| 預算 | 目標推理強度 |
|---|---|
| `unlimited` | max |
| `large` | xhigh（等於預設值） |
| `medium` | high |
| `small` | medium |

換算規則（原文 step 3b）：把每個真實 slug 的**推理強度 token**（最後一個 token，或尾端 `fast` 前面那個）換成目標值；強度的階梯是 `max > xhigh > high > medium > low`；如果換完的 slug 不存在，就用**同一家族**中、強度不超過目標的最高那個。

把規則寫成腳本：

```js
// budget.mjs：模擬 setup-pstack 的預算換算，並對應到 Claude Code 的寫法
const DEFAULTS = {
  "feature, refactoring": "grok-4.7-xhigh-fast",
  "judgment and prose": "claude-opus-5-5-xhigh",
  "how explorer": "grok-4.7-xhigh-fast",
  "how explainer": "claude-opus-5-5-xhigh",
  "swarm workers": "grok-4.7-xhigh-fast",
  "arena runners": "claude-opus-5-5-xhigh, grok-4.7-xhigh-fast",
  "arena cross-judge pool": "claude-opus-5-5-xhigh, grok-4.7-xhigh-fast",
  "interrogate reviewers": "claude-opus-5-5-xhigh, grok-4.7-xhigh-fast",
};
const LADDER = ["low", "medium", "high", "xhigh", "max"];
const TARGET = { unlimited: "max", large: "xhigh", medium: "high", small: "medium" };
// 假設偵測到的模型；Grok 沒有 max（setup-pstack 原文：「Grok slugs top out at xhigh」）
const DETECTED = new Set(["claude-opus-5-5-max", "claude-opus-5-5-xhigh", "claude-opus-5-5-high", "claude-opus-5-5-medium",
  "grok-4.7-xhigh-fast", "grok-4.7-high-fast", "grok-4.7-medium-fast"]);

function withEffort(slug, effort) {
  const parts = slug.split("-");
  const fast = parts.at(-1) === "fast";
  const i = parts.length - (fast ? 2 : 1);
  for (let e = LADDER.indexOf(effort); e >= 0; e--) {
    const candidate = [...parts.slice(0, i), LADDER[e], ...(fast ? ["fast"] : [])].join("-");
    if (DETECTED.has(candidate)) return candidate;
  }
  return `${slug} (needs a choice)`;
}
// adapter：claude-<family>-<version>-<effort>[-fast] → "<family> <effort>"；非 Claude 的 slug → 省略 model
function toClaudeCode(slug) {
  const m = slug.match(/^claude-([a-z]+)-[\d-]+?-(low|medium|high|xhigh|max)(-fast)?$/);
  return m ? `${m[1]} ${m[2]}` : "inherit-parent";
}
const budget = process.argv[2] ?? "large";
console.log(`# budget: ${budget} (${TARGET[budget]})`);
for (const [role, value] of Object.entries(DEFAULTS)) {
  const cursor = value.split(", ").map((s) => withEffort(s, TARGET[budget]));
  const claude = cursor.map(toClaudeCode);
  console.log(`${role.padEnd(24)} cursor: ${cursor.join(", ").padEnd(48)} claude code: ${claude.join(", ")}`);
}
```

（為了篇幅只列了 17 個角色中的 8 個。）

```bash
for b in unlimited large small; do node budget.mjs $b; echo; done
```

```
# budget: unlimited (max)
feature, refactoring     cursor: grok-4.7-xhigh-fast                              claude code: inherit-parent
judgment and prose       cursor: claude-opus-5-5-max                              claude code: opus max
how explorer             cursor: grok-4.7-xhigh-fast                              claude code: inherit-parent
how explainer            cursor: claude-opus-5-5-max                              claude code: opus max
swarm workers            cursor: grok-4.7-xhigh-fast                              claude code: inherit-parent
arena runners            cursor: claude-opus-5-5-max, grok-4.7-xhigh-fast         claude code: opus max, inherit-parent
arena cross-judge pool   cursor: claude-opus-5-5-max, grok-4.7-xhigh-fast         claude code: opus max, inherit-parent
interrogate reviewers    cursor: claude-opus-5-5-max, grok-4.7-xhigh-fast         claude code: opus max, inherit-parent

# budget: large (xhigh)
feature, refactoring     cursor: grok-4.7-xhigh-fast                              claude code: inherit-parent
judgment and prose       cursor: claude-opus-5-5-xhigh                            claude code: opus xhigh
...
arena runners            cursor: claude-opus-5-5-xhigh, grok-4.7-xhigh-fast       claude code: opus xhigh, inherit-parent
...

# budget: small (medium)
feature, refactoring     cursor: grok-4.7-medium-fast                             claude code: inherit-parent
judgment and prose       cursor: claude-opus-5-5-medium                           claude code: opus medium
...
arena runners            cursor: claude-opus-5-5-medium, grok-4.7-medium-fast     claude code: opus medium, inherit-parent
...
```

兩個觀察：

1. **`unlimited` 下 Grok 停在 xhigh**：因為 Grok 沒有 max，fallback 找到同家族最高的 xhigh。這和 setup-pstack 原文的例子一致（「under `unlimited` the fallback puts Grok at `xhigh` and keeps `grok-4.7-xhigh-fast` as it is」）。
2. **Claude Code 欄位裡所有 Grok 都變成了 `inherit-parent`**。不管預算是多少。

## 三、關鍵問題：在 Claude Code 上，多樣性塌縮了

看 `arena runners` 那一列在 Claude Code 上的值：`opus xhigh, inherit-parent`。如果你的主 session 也是 Opus，這兩個 runner **是同一個家族的同一個模型**。

再看 `arena cross-judge pool`：`opus xhigh, inherit-parent`。arena 的 Phase C 規定：

> The judge is never from the parent's family ... An `auto` or `inherit-parent` entry is the parent model, so it never judges.

在一個 Claude 主 session 上：`opus` 和主 session 同家族 → 不能當評審；`inherit-parent` 就是主 session → 不能當評審。arena 接著的規則是：

> If no entry from another family runs, try the default pool's entries from another family the same way. If none of those runs, skip the judge, pick the base alone in Phase D, and say so.

所以在預設設定的 Claude Code 上，**arena 的跨家族評審會被跳過**，由主 session 自己挑。這不是 bug——pstack 有寫好的降級路徑，而且要求「say so」——但這代表 Ch 1 說的信條 3「寫的人不能自己判」，在 Claude Code 上預設只做到一半：runner 和挑選者是同一家族。

```
  Cursor（預設）                              Claude Code（預設）
  ───────────────                            ───────────────────
  runner 1：Opus                              runner 1：Opus
  runner 2：Grok        ← 不同家族             runner 2：主 session（Opus）← 同家族
  評審：Grok（主 session 是 Opus 時）           評審：跳過，主 session 自己挑
  ─────                                      ─────
  兩個家族互相看盲點                           一個家族，兩次抽樣
```

同一個模型跑兩次仍然有價值（兩次抽樣會產生不同的設計，arena 也說「Use the same model N times when the work is generation-bound rather than judgment-sensitive」），但「判斷」這一層的多樣性沒了。

## 四、在 Claude Code 上把多樣性補回來

三個方向，由簡單到複雜：

**1. 在 Claude 家族內做到「不同模型」。** 把小組角色設成不同的 Claude 模型，例如 `arena runners: opus xhigh, sonnet high`。同家族不同模型的多樣性比同一個模型跑兩次高，但仍然是同一個訓練來源。注意 arena 的家族是**依前綴判斷**的（`claude-*` 都是一家），所以 Sonnet 仍然不能當 Opus 主 session 的評審。

**2. 手動用另一個家族做評審。** 這台機器上裝了 OpenAI 的 Codex plugin（`openai-codex` marketplace），它提供一個 `codex:codex-rescue` subagent 和一個 `/codex:adversarial-review` 指令，會把任務轉給 Codex CLI（GPT 家族）。pstack **不會自動用它**——adapter 對非 Claude 的 slug 一律「Omit `model` so the agent runs on the session model」——但你可以在 arena 或 interrogate 跑完之後，自己把結果交給 Codex 再審一次：

```text
/codex:adversarial-review the branch arena produced. intent: <使用者的目標>. only report findings you can reproduce with a command.
```

這補上的是「跨家族的第二意見」，不是 arena 原本的「跨家族挑底」——因為它發生在主 session 已經挑完之後。

> 這個做法本章沒有實際執行。`codex-rescue` 的定義（`model: sonnet`、`tools: Bash`、「a thin forwarding wrapper around the Codex companion task runtime」）顯示它是用一個 Sonnet subagent 去呼叫 Codex CLI；實際行為與成本以 Codex plugin 的文件為準。

**3. 寫一個自己的 cross-judge 技能。** 把 arena Phase C 的評審步驟換成「呼叫 Codex CLI 讀候選、依 rubric 打分」。這是 Ch 28 與 Final Project 的範圍：當 pstack 的預設不適合你的環境，寫你自己的版本。

## 五、「Match the model to the job」：每次 spawn 前的調整

除了設定檔，poteto-mode 的 Subagents 段落還規定了**每一次 spawn** 怎麼微調：

- 從角色的設定值開始
- **最難、最精細的工作往上調一級**（最多到 max）
- **判斷類工作往下調一級，例行工作往下調兩級以上**
- **只有緊急時才用 fast 模式**：「meaning CI or trunk is red or a person is blocked on the result right now」
- **驗證是例外**：固定用 `swarm workers` 的模型與速度，不調整
- **只改強度與 fast，不改家族**：「so a diverse panel stays diverse」

最後一條很重要：調整的時候不能「為了省錢把 Grok 換成 Claude Haiku」，因為那會破壞小組的多樣性。

還有一大段關於「模型被拒絕時怎麼辦」的規則（slug 不存在、被 Task 工具拒絕時的 fallback 順序）。它們看起來很瑣碎，但反映了一個真實的工程問題：**模型清單會變**。新模型上線、舊模型下架、帳號權限不同，技能裡寫死的 slug 隨時可能失效。pstack 的規則確保失效時有明確的降級路徑，而且每次降級都要「say so」。

## 底層機制：設定檔怎麼流進每一次 spawn

```
  ~/.claude/rules/pstack-models.md（每個新 session 載入）
        │
        │  arena runners: opus xhigh, inherit-parent
        ▼
  arena 技能 Phase A step 3：「Use the `arena runners` line」
        │
        ├─ opus xhigh       → Agent(model: "opus", ...)
        │                     （effort 欄位：依 adapter 可傳；以你的工具 schema 為準）
        └─ inherit-parent   → Agent(...)  （省略 model，跑在主 session 的模型上）
        │
        ▼
  Phase C：cross-judge pool → 過濾掉同家族與 inherit-parent → 空 → 跳過評審，並說明
```

技能本身不知道你用什麼模型；它只知道「讀這個角色的那一行」。這讓同一份技能能在 Cursor（有 Grok）、Claude Code（只有 Claude）、加上別的 plugin 的環境裡都能跑，差別只在設定檔。

## 對比與取捨

| 設定 | 成本 | 多樣性 | 適合 |
|---|---|---|---|
| 全部 `inherit-parent` | 取決於主 session 模型 | 無（全是同一個模型） | 預算有限、先熟悉流程 |
| Claude Code 預設（opus + inherit-parent） | 中到高 | 低（同家族） | 大部分 Claude Code 使用者的起點 |
| 小組用不同 Claude 模型（opus + sonnet） | 中 | 中（同家族不同模型） | 想要一點多樣性、不想加工具 |
| + 手動用 Codex 做第二意見 | 高 | 高（跨家族，但在挑選之後） | 難以撤回的決定 |
| Cursor 預設（opus + grok） | 中到高 | 高（跨家族、評審在挑選前） | pstack 原本設計的環境 |

官方的省錢建議（setup 指南）：「A strong model in the main chat with cheaper, faster models in the code roles is a good split.」以及最重要的一條：「Save `/poteto-mode` for work that needs rigor.」

## 踩雷集錦

1. **以為 Claude Code 上的 arena 有跨家族評審**：預設設定下沒有。它會跳過評審並說明（如果它沒說明，那是偏離）。重要的決定要自己補第二意見。
2. **把 `auto` 當成一個模型 slug**：官方 Pitfall：「`auto` and `inherit-parent` mean "omit the model field so the subagent inherits the parent chat model."」它們不會「自動挑一個好模型」。
3. **為了省錢換掉小組的家族**：Match the model to the job 只允許調強度與 fast，「keep the family the role names」。
4. **改了預設之後設定檔沒更新**：setup 指南：「When a default changes, a rule written before the change still pins the old default, so delete those role lines, or delete the file, then run `/setup-pstack` again.」pstack 在 2026-09-23 把預設改成 Opus 5.5 與 Grok 4.7；在那之前跑過 setup 的人，設定檔裡還是舊模型。
5. **把角色設定寫進 CLAUDE.md**：adapter 明確說「Never put the role lines in `CLAUDE.md` or `AGENTS.md`」。它們有自己的檔案，混進專案指示會讓專案設定和個人的模型偏好綁在一起。

## 進階：再往深一層

- **把 budget.mjs 擴成完整的 17 個角色**，並加上「從 Agent 工具的 schema 偵測模型」這一步（setup-pstack 在 Claude Code 上就是這樣做的）。比較你的輸出和真的跑 `/pstack:setup-pstack` 產生的 `~/.claude/rules/pstack-models.md`。
- **量一次 arena 的成本**：在一個小任務上，分別用「全部 inherit-parent」和「opus + sonnet」跑一次 `/pstack:arena`，用 Claude Code 的用量資訊比較兩次的 token 數。把數字和結果品質一起記下來——這是 Explain the Number 用在模型選擇上。
- **讀 setup-pstack 的 step 6 之後**：它會在設定結束時，偵測專案有沒有驗證技能、要不要產生一個（Ch 21）。設定模型和設定驗證放在同一個流程裡，是因為作者認為兩者都是「讓 agent 能自己工作」的前提。

## 動手練習

1. 照本章跑 budget.mjs，然後在 `DETECTED` 裡拿掉 `claude-opus-5-5-max`，再跑 `unlimited`。`judgment and prose` 變成什麼？這對應 setup-pstack 原文的哪一句？
2. 列出你在 Claude Code 能用的模型（看 Agent 工具的 `model` 參數接受哪些值），為 arena runners、cross-judge pool、interrogate reviewers 各設計一組值。你的 cross-judge pool 在 arena 的規則下，能不能產生一個評審？
3. 讀 `$PSTACK/skills/arena/SKILL.md` 的 Phase A step 3 與 Phase C，畫出「一個 runner 的 slug 被拒絕」時的 fallback 流程圖。

## 本章重點整理

- pstack 把工作分成 17 個角色，每個角色一行設定；技能自帶預設值，設定檔只是覆寫層
- 一個預算旋鈕（unlimited / large / medium / small）調整所有角色的推理強度，找不到就用同家族、不超過目標的最高者
- 在 Claude Code 上，Grok 對應成 `inherit-parent`，預設的兩家族小組塌縮成同一家族，arena 的跨家族評審會被跳過（並且應該說明）
- 補回多樣性：小組用不同的 Claude 模型；重要決定手動交給另一個家族（例如 Codex plugin）再審；或寫自己的 cross-judge 技能
- 每次 spawn 依工作難度調強度，但只調強度與 fast，不換家族；驗證固定用 swarm workers 的模型

## 自我檢核

- [ ] 不看本章，能解釋為什麼在 Claude Code 上 arena 的評審會被跳過嗎？請引用 arena 的那條規則
- [ ] `inherit-parent` 和 `auto` 的意思是什麼？為什麼說它們「不是模型 slug」？
- [ ] 「Match the model to the job」允許調整什麼、禁止調整什麼？禁止的那一項在保護什麼？
- [ ] 你的環境裡有沒有一個「不同家族」的模型可以當第二意見？如果沒有，你會怎麼降低「同家族自己判自己」的風險？

## 延伸閱讀

### 原始碼

- **[skills/setup-pstack/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/setup-pstack/SKILL.md)**
  - **讀哪裡**：step 3（預算換算）與 step 5（設定檔格式）
  - **能學到什麼**：本章 budget.mjs 實作的原文規則
- **[skills/arena/SKILL.md](https://github.com/cursor/plugins/blob/main/pstack/skills/arena/SKILL.md)**
  - **讀哪裡**：Phase A step 3 與 Phase C
  - **能學到什麼**：家族的判斷方式（依前綴）、評審的 fallback 順序、「skip the judge ... and say so」
- **[docs/harness/claude-code.md 的 Models 表](https://github.com/cursor/plugins/blob/main/pstack/docs/harness/claude-code.md)**
  - **讀哪裡**：Models 與 setup-pstack 兩節
  - **能學到什麼**：Grok 為什麼對應成 inherit-parent

### 官方文件

- **[Claude Code 官方文件：Model configuration](https://code.claude.com/docs/en/model-config)**
  - **讀哪裡**：模型別名（opus、sonnet、haiku）與 subagent 的模型設定
  - **能學到什麼**：Agent 工具的 `model` 欄位在你的版本接受哪些值
- **[openai/codex-plugin-cc](https://github.com/openai/codex-plugin-cc)**
  - **讀哪裡**：README 與 `plugins/codex/commands/adversarial-review.md`
  - **能學到什麼**：在 Claude Code 裡呼叫另一個模型家族的一種現成方式
  - **注意**：本章沒有實際用它執行；成本、權限與行為以它的文件為準

### 文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** 的「Parallelization」一節
  - **這篇說什麼**：sectioning（切成獨立子任務）與 voting（同一任務多次取多數）兩種平行化
  - **和本章的關聯**：arena 的「同一個 brief 給多個 runner」是 voting 的變形；它的價值取決於 runner 之間有多獨立——這正是多樣性塌縮會削弱的東西

模型配好了，接下來看最依賴多模型的兩個技能：architect 與 arena。

→ [Ch 18 architect 與 arena：設計兩次，挑底再嫁接](./18-architect-arena.md)
