# Ch 25 — Autonomous run 與過夜契約

> **目標**：搞懂 pstack 怎麼讓 agent 在你不在的時候安全地長時間工作。你會讀懂官方指南的「過夜契約」（目標、完成條件、權限、逃生口）、Autonomous run playbook 的六步、以及「信任要先賺到」的四項檢查；理解 Pause safely 與 Session pickup 這一對互補的 playbook；最後在本機實際跑一個迷你的過夜迴圈：一個能 pass/fail 的 predicate、每次一個最小的改動、每次驗證、每次 commit、每次寫一列決策日誌，在 predicate 通過時自己停下來。

> **環境**：pstack 0.15.36、Claude Code 2.1.295（本機有 `/loop` 技能）、git 2.54、Node.js 26。迷你迴圈用 shell 腳本代替 agent 示範**迴圈的結構**；它的每一步與輸出都是實際跑出來的。

## 為什麼需要這個？

官方指南第 7 頁的開頭：

> This is the payoff for everything before it. An agent you can trust to verify its own work is an agent you can leave alone with a hard task. What makes that safe isn't hope. It's a checkable finish condition, an isolated worktree or cloud agent, and a decision log you audit in the morning.

但讓 agent 自己跑一整晚，失敗的方式和它在你面前工作時不同：

- **它會停下來問你**，然後你在睡覺，它就這樣等了八小時
- **它會一直動但不前進**：「four hours of motion instead of a result」
- **它會悄悄放寬目標**：做不到「全部通過」，就把目標改成「大部分通過」，然後宣告成功
- **它會把錯誤累積起來**：第三次迭代建立在第二次的錯誤上，早上你面對的是一團解不開的東西

## 先建立直覺：一個不需要你在場的工廠

```
   你下班前                                    早上你回來
   ────────                                   ──────────
   給出：                                      讀：
   - 目標                                      - 最後的 predicate 狀態
   - 一個能 pass/fail 的完成條件（predicate）     - 決策日誌（每一列一個決定 + 證據指標）
   - 權限（哪些事不用問）                         - 另一個模型對日誌的審查（Attention 段）
   - 一個逃生口（真的卡住時停下來、寫出原因）       - commit 歷史（一個改動一個 commit）
            │
            ▼
   ┌───────────────── 迴圈 ─────────────────┐
   │  檢查 predicate ─── 通過 ──▶ 停          │
   │       │ 未通過                           │
   │       ▼                                  │
   │  最小的、有根據的改動                      │
   │       ▼                                  │
   │  在真實產物上驗證                          │
   │       ▼                                  │
   │  有進展？ ── 是 ─▶ commit                 │
   │       └── 否 ─▶ 丟掉                      │
   │       ▼                                  │
   │  寫一列日誌 ──────────────────▶ 回到開頭   │
   └──────────────────────────────────────────┘
```

「One change, one check, one log row, every iteration. Changes that didn't help get discarded, not left to ride. A plateau means pivot, not stop, and the finish condition never quietly relaxes to declare victory.」

## 一、先賺到信任

> A loop you don't trust just produces unchecked work faster, and the mess compounds with every iteration.

讓一個迴圈自己跑之前，四項都要成立：

1. **你自己做過一次、或看 agent 做過一次**，所以你知道「好」長什麼樣
2. **agent 有你自己會用的工具與訊號**：驗證技能、profiler、log
3. **每一個階段都證明自己的工作**，而且在工作沒達標時能讓整條線停下來
4. **你讀過幾份 transcript，把反覆出現的失敗變成了工具、技能或檢查**

「Make the loop autonomous only after all four hold. Until then, run it while you watch.」

第 4 項是 Encode Lessons in Structure（Ch 15）。它被列為自主執行的**前提**，不是事後的改進：一個你看過它失敗、並把失敗編碼成檢查的迴圈，才值得讓它在你不在時跑。

## 二、過夜契約

官方的範例：

```text
/poteto-mode im going to bed. migrate every caller to the new parser in a fresh worktree off <base>.
done means zero old callers, all parser fixtures pass, old api deleted.
keep a decision log. don't ask me before committing.
/loop until done. if you're truly stuck after a few hours, stop and write up why.
```

每一行買到什麼：

| 片語 | 作用 |
|---|---|
| `im going to bed` | session 覆寫：不再停下來問（poteto-mode Autonomy 的 Session overrides）；並且依路由規則，「使用者要離開之後審」會被導到 figure-it-out |
| `done means ...` | 把目標變成每次迭代都能跑的檢查：零個舊呼叫者、fixture 全過、舊 API 已刪除 |
| `in a fresh worktree off <base>` | 不和你開著的其他東西互撞 |
| `don't ask me before committing` | 預先回答 agent 原本會卡住的那個權限問題 |
| `/loop until done` | 喚醒機制：事件發生或心跳時重新檢查完成條件 |
| `if you're truly stuck ... stop and write up why` | 逃生口：「which beats eight hours of creative goal reinterpretation」 |

**Pitfall**：「a duration is not a finish condition. 'work on this for 4 hours' gives the agent nothing to check.」（Ch 6 的 prompt-lint 編碼了這條。）

### 在 Claude Code 上的 `/loop`

pstack 的原文說 `/loop` 是「Cursor's built-in wake mechanism, not a pstack skill」。Claude Code 也有一個 `/loop` 技能（本機的技能清單裡：「Run a prompt or slash command on a recurring interval (e.g. /loop 5m /foo). Omit the interval to let the model self-pace.」）。兩者的語意相近但不保證完全一樣；在 Claude Code 上，把過夜契約的最後一行寫成 `/loop` 加上你的 poteto-mode 指令，是最直接的對應。

> 本章沒有實際用 Claude Code 的 `/loop` 跑一整晚。它的喚醒間隔、可以跑多久、session 被壓縮時的行為，以 Claude Code 的文件與你的實際觀察為準。

## 三、Autonomous run playbook：六步

> **You own the exit condition. Define done, then drive to it without stopping.**

1. **在第一次迭代之前，把結束條件寫成一個可檢查的 predicate**（測試綠、重現修好、N 個 PR 全部合併、像素差為零）
2. **選喚醒機制**：有事件可以等（CI、合併、ref 前進）就用一個 watcher subagent 在事件發生時叫醒你，加上一個長的時間心跳當備援；沒有事件就用固定間隔的心跳，間隔「sized to when the result is worth re-checking」
3. **每次迭代做證據支持的最小改動**，對 predicate 驗證；有進展就 commit，沒幫助就丟掉。「Belt-and-suspenders that 'might help' gets reverted, not left to ride.」
4. **中途發現的、支持目標的問題是你的**：壞掉的技能、相關的 bug、flaky 的驗證器、工具故障……自己處理（用 poteto-mode），帶外的修正放在自己的 PR。「Do not park reversible work for the human or use `AskQuestion`.」只浮出不可逆的動作、真正的產品或偏好決定、真正的死路
5. **每次迭代都用 show-me-your-work 記一列**：改了什麼、predicate 有沒有前進
6. **predicate 通過時停**。「A plateau is not a stop, so keep going and pivot your approach to push past it. Surface a genuine dead end rather than spinning, and never relax the predicate to declare victory.」

第 6 步有三條方向不同的紀律：平台期不是停止（不要太早放棄）；真正的死路要浮出（不要無限打轉）；不准放寬 predicate（不要假裝成功）。

## 四、實驗：一個迷你的過夜迴圈

用官方範例的任務：把所有呼叫端從 `parseOld` 遷移到 `parseNew`，然後刪掉 `parseOld`。

```js
// src/parse.mjs
export const parseOld = (s) => s.split(",").map(Number);
export const parseNew = (s) => s.split(",").map((x) => Number(x.trim()));
```

四個呼叫端 `src/a.mjs` 到 `src/d.mjs`，每個都長這樣：

```js
import { parseOld } from "./parse.mjs";
export const a = (s) => parseOld(s).reduce((x, y) => x + y, 0);
```

### predicate：完成條件寫成一支會 pass/fail 的腳本

官方契約的「done means zero old callers, all parser fixtures pass, old api deleted」：

```sh
#!/bin/sh
# predicate.sh：零個舊呼叫者，而且舊 API 已刪除
n=$(grep -l "parseOld" src/*.mjs | grep -v parse.mjs | wc -l | tr -d ' ')
legacy=$(grep -c "export const parseOld" src/parse.mjs)
echo "old callers=$n legacy_api=$legacy"
[ "$n" -eq 0 ] && [ "$legacy" -eq 0 ]
```

fixture 檢查（「all parser fixtures pass」）是另一支腳本，每次迭代都跑：

```js
// check.mjs：每個呼叫端對同一個輸入都要算出 6
const mods = await Promise.all(["a", "b", "c", "d"].map((m) => import(`./src/${m}.mjs?${Date.now()}`)));
const bad = mods.map((m, i) => [i, Object.values(m)[0]("1, 2,3")]).filter(([, v]) => v !== 6);
if (bad.length) { console.log(`fixtures FAIL for ${bad.map(([i]) => "abcd"[i]).join(",")}`); process.exit(1); }
console.log("fixtures pass");
```

（`?${Date.now()}` 讓 Node 每次重新載入模組，而不是用快取的舊版本——否則 check 量到的是迭代開始前的程式碼，那是 Ch 22 的 Q7。這支 fixture 在遷移前後都會通過，因為對這個輸入 `Number(" 2")` 本來就會去掉空白：它守的是「行為不變」，不是「遷移完成」。後者由 predicate 守。兩支腳本各管一件事。）

### 迴圈

```sh
while ! ./predicate.sh > /tmp/pred.out; do
  iter=$((iter+1)); cat /tmp/pred.out
  f=$(grep -l "parseOld" src/*.mjs | grep -v parse.mjs | head -1)
  if [ -n "$f" ]; then
    sed -i '' 's/parseOld/parseNew/g' "$f"                                  # 最小的改動：一個呼叫端
    if node check.mjs >/tmp/chk.out 2>&1; then
      git commit -qam "refactor: migrate $(basename $f) to parseNew"         # 有進展：commit
      bash $LOGSH $LOG migrate "moved $(basename $f) to parseNew" "one caller per iteration keeps each check local" "commit $(git rev-parse --short HEAD)" "$(cat /tmp/chk.out)"
    else
      git checkout -q "$f"                                                   # 沒幫助：丟掉
      bash $LOGSH $LOG migrate "reverted $(basename $f)" "fixtures failed" "check.mjs" "reverted"
    fi
  else
    sed -i '' '/export const parseOld/d' src/parse.mjs                      # 沒有呼叫者了：刪舊 API
    node check.mjs >/tmp/chk.out && git commit -qam "refactor: delete parseOld" && bash $LOGSH $LOG delete "deleted parseOld" "no callers left, migrate then delete in one wave" "commit $(git rev-parse --short HEAD)" "$(cat /tmp/chk.out)"
  fi
  [ $iter -ge 10 ] && { echo "escape hatch: 10 iterations without meeting the predicate"; break; }   # 逃生口
done
```

（`$LOGSH` 是 show-me-your-work 附的 `scripts/log.sh`，Ch 26 詳談；10 次迭代的逃生口上限是本章為這個 5 步就能完成的任務訂的。）

```
old callers=4 legacy_api=1
old callers=3 legacy_api=1
old callers=2 legacy_api=1
old callers=1 legacy_api=1
old callers=0 legacy_api=1
old callers=0 legacy_api=0
```

predicate 每次迭代單調地前進，最後一行通過，迴圈停止。

決策日誌（節錄欄位）：

```
phase    decision                 evidence        result
migrate  moved a.mjs to parseNew  commit 35071f0  fixtures pass
migrate  moved c.mjs to parseNew  commit 062845c  fixtures pass
migrate  moved b.mjs to parseNew  commit 5aed10b  fixtures pass
migrate  moved d.mjs to parseNew  commit a3def7d  fixtures pass
delete   deleted parseOld         commit 8677205  fixtures pass
```

commit 歷史：

```
8677205 refactor: delete parseOld
a3def7d refactor: migrate d.mjs to parseNew
5aed10b refactor: migrate b.mjs to parseNew
062845c refactor: migrate c.mjs to parseNew
35071f0 refactor: migrate a.mjs to parseNew
47617d1 baseline
```

早上回來，你要讀的就是這三樣東西：predicate 的最後狀態（通過）、日誌（五個決定、每個附 commit）、commit 歷史（每個改動可以單獨 revert）。你不需要重讀整晚的 transcript。

這個迷你迴圈沒有示範的部分（真實的 agent 迴圈才會遇到）：一次迭代失敗被 revert、平台期的 pivot、中途發現的壞掉的技能、逃生口真的被觸發。

## 五、Pause safely 與 Session pickup

一個長任務不一定能一次跑完：你要關電腦、session 要被壓縮、你明天要換一台機器。pstack 用一對互補的 playbook 處理「停下來」與「接著做」。

### Pause safely：留下一個冷啟動也能接手的檢查點

> This is explicit only. On "keep going", "going to bed, keep going", or "don't stop", do not pause.

1. **停在安全的邊界**：完成目前的原子步驟，或退出它；不開始新的；取消巢狀的 subagent
2. **暫停時不做不可逆的事**：沒有 PR、不 push（除非已經有了）
3. **讓工作持久化**：把未提交的修改 commit 成一個清楚的 `wip:` commit；樹是壞的就在 commit body 寫一行說明
4. **在 context 之外寫 resume note**：意圖、在做什麼、進度與已驗證的部分、目前狀態、下一步、關鍵檔案、陷阱。為了 context 壓縮而暫停時，寫到像 `/tmp/<slug>-resume.md` 的檔案；有 show-me-your-work 的軌跡就指向它，不要重複

一份 resume note 的骨架（依第 4 步的要求）：

```markdown
# parser migration: resume note

Intent: every caller uses parseNew; parseOld deleted. Predicate: ./predicate.sh exits 0.
Doing: migrating callers one per commit.
Verified: a, c, b migrated (commits 35071f0, 062845c, 5aed10b), fixtures pass after each.
State: branch refactor/parser, tree clean except wip commit 1a2b3c4 (d.mjs half edited, fixtures fail).
Next: finish d.mjs, run check.mjs, then delete parseOld.
Key files: src/parse.mjs, src/d.mjs, predicate.sh, check.mjs.
Gotchas: check.mjs needs the ?timestamp import or it tests stale modules.
Trail: /tmp/lab/ch25/decisions.tsv
```

### Session pickup：把前一個軌跡當成權威

> **You own the resume point. Read the prior trail, don't redo it.**

最重要的一句在第 3 步：

> A "let me verify from scratch" pass means you're treating the trail as untrustworthy when it's authoritative.

接手的 agent 有一個很強的傾向：「我不確定前一個 agent 做得對不對，我從頭驗證一次」。這會讓一個做到 80% 的任務重頭來過。pstack 的規定是：**前一個軌跡是權威輸入**，從它重建狀態、找出接續點、不重做已完成的工作。

但第 5 步加了一個平衡：「Verify the inherited claims against the original goal on the real artifact. A passing prior self-report is not the proof.」——不重做**工作**，但要驗證**宣稱**。差別在於：重跑一次 predicate 與 fixture（便宜、針對原始目標）是驗證；把已經遷移的 a、c、b 重新遷移一次是重做。

## 底層機制：為什麼 predicate 必須是一支腳本

```
  predicate 寫在 prompt 裡（文字）              predicate 是一支腳本
  ─────────────────────────                   ─────────────────
  「done means zero old callers」              ./predicate.sh → exit 0 / 1
         │                                           │
  每次迭代，agent 要「判斷」是否達成               每次迭代，跑一次，看 exit code
         │                                           │
  第 37 次迭代、context 被壓縮過兩次之後：          第 37 次迭代：
  「大部分的呼叫者都遷移了，                        old callers=3 → exit 1 → 繼續
    剩下的幾個是特殊情況，可以視為完成」
  → 悄悄放寬了 predicate                          → 無法被放寬
```

「never relax the predicate to declare victory」這條規則，在 predicate 是文字時要靠 agent 的紀律；在 predicate 是腳本時，它是結構化的保證（Encode Lessons in Structure）。長時間執行的 agent 會被壓縮、會疲勞（在 context 意義上），所以越長的迴圈，越需要把判斷交給腳本。

## 對比與取捨

| playbook | 什麼時候用 | 結束條件 | 和其他的區別 |
|---|---|---|---|
| Autonomous run | 一個任務，跑到一個 predicate | predicate 通過，或真正的死路 | 一個任務 |
| Hillclimb | 改善一個指標 | 目標 + 嘗試次數下限 | 每次迭代都要量測、keep or revert（Ch 10）；借用 Autonomous run 的喚醒機制，**不借它的停止規則** |
| figure-it-out | 大型工作、使用者要離開後審 | 自己設計的 predicate | 先設計流程，強制留下決策日誌（Ch 26） |
| Orchestrate | 多天、很多 PR、一群 subagent | 整個專案 | 協調者不寫程式（Ch 27） |
| Pause safely | 明確要求暫停 | 一個乾淨的檢查點 + resume note | 只在明確要求時；「keep going」不觸發 |
| Session pickup | 接手前一個軌跡 | 找到接續點，交給對應的 playbook | 不重做，但驗證宣稱 |

## 踩雷集錦

1. **用時間當完成條件**：「work on this for 4 hours」沒有東西可以檢查。寫一個 predicate。
2. **predicate 只寫在 prompt 裡**：長時間執行後，文字的 predicate 會被重新詮釋。寫成腳本，讓它無法被放寬。
3. **還沒賺到信任就讓它自己跑**：四項檢查沒過，就在你看得到的時候跑。原文：「Until then, run it while you watch.」
4. **接手時從頭驗證一切**：Session pickup 明確說這是把權威軌跡當成不可信。重跑 predicate，不重做工作。
5. **「keep going」觸發了暫停**：Pause safely 只在明確要求時執行。「going to bed, keep going」是繼續的意思，不是暫停。

## 進階：再往深一層

- **事件驅動 vs 心跳**：Autonomous run 的第 2 步區分兩種喚醒。等 CI 這種有明確事件的情況，用一個 watcher 在事件發生時叫醒主 session，比每 5 分鐘檢查一次便宜得多；心跳只當備援。在 Claude Code 裡，背景執行的 Bash 指令完成時會叫醒 session，這可以當成一種事件來源（依 Claude Code 的背景任務行為，本章沒有實測）。
- **用 hook 強制 predicate**：Claude Code 的 Stop hook 在 agent 要結束回合時執行。讓它跑 predicate.sh，未通過就把結果加回 context、讓 agent 繼續——這是「不准放寬 predicate」的最強形式。注意要配一個逃生口（例如次數上限或一個「真正卡住」的檔案標記），否則一個永遠無法通過的 predicate 會讓 agent 永遠停不下來。
- **讀 Orchestrate 的 tick prompt**：Multi-phase plan 裡的 `/loop 1h` tick prompt（Ch 27）是一個「每小時自我審計」的範例：重讀 playbook、對照它審計目前的操作、處理卡住的 lane、只在有新變化時才發訊息給人。

## 動手練習

1. 照本章跑迷你迴圈。然後在 `src/c.mjs` 裡故意製造一個遷移會壞的情況（例如它依賴 `parseOld` 不 trim 的行為：讓它檢查 `parseOld(" 1")[0] === 1`……想一個讓 parseNew 行為不同的輸入），再跑一次迴圈。它會怎麼處理 c？逃生口會被觸發嗎？
2. 寫一份你自己的過夜契約，對象是你目前手上的一個任務。逐行檢查：每一行買到了什麼？你的「done means」能寫成一支腳本嗎？
3. 中途中斷迷你迴圈（例如在第 2 次迭代後 Ctrl-C），依 Pause safely 的四步留下一個檢查點與 resume note。然後開一個新的 Claude Code session，請它依 Session pickup 接手。它有沒有重做 a.mjs？

## 本章重點整理

- 自主執行的安全來自可檢查的完成條件、隔離的工作區、早上可以審的決策日誌，而不是希望
- 先賺到信任（四項）：看過一次、工具齊全、每階段自證並能停線、把重複的失敗編碼成檢查
- 過夜契約：目標、`done means` 的 predicate、權限、逃生口；時間長度不是完成條件
- Autonomous run：每次迭代一個最小改動、驗證、有進展就 commit 沒幫助就丟、每次記一列；平台期要 pivot、死路要浮出、predicate 不准放寬
- 本章的迷你迴圈：predicate 從 4 個舊呼叫者單調降到 0、舊 API 刪除，五個 commit、五列日誌
- Pause safely 與 Session pickup 互補：前者留下冷啟動也能接手的檢查點，後者把軌跡當權威、不重做工作但驗證宣稱

## 自我檢核

- [ ] 不看本章，能說出「先賺到信任」的四項，以及為什麼第 4 項是前提而不是事後改進嗎？
- [ ] 過夜契約的六個片語，各自買到了什麼？拿掉逃生口會怎樣？
- [ ] 為什麼 predicate 寫成腳本比寫在 prompt 裡可靠？在長時間執行中，文字的 predicate 會怎麼失效？
- [ ] Session pickup 的「不重做」和「驗證宣稱」怎麼同時成立？舉一個是驗證、一個是重做的例子

## 延伸閱讀

### 原始碼

- **[playbooks/autonomous-run.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/autonomous-run.md)**、**[pause-safely.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/pause-safely.md)**、**[session-pickup.md](https://github.com/cursor/plugins/blob/main/pstack/skills/poteto-mode/playbooks/session-pickup.md)**
  - **讀哪裡**：三份全文（都在 320 字以內）

### 官方指南

- **[docs/guide/07-overnight.md](https://github.com/cursor/plugins/blob/main/pstack/docs/guide/07-overnight.md)**
  - **讀哪裡**：「Earn the trust before the loop」「The overnight contract」「What the loop does all night」
  - **能學到什麼**：本章引用的過夜契約與迴圈圖的原文

### 官方文件

- **[Claude Code 官方文件：Hooks 的 Stop 事件](https://code.claude.com/docs/en/hooks)**
  - **能學到什麼**：用 hook 強制 predicate 所需的機制
- **[Claude Code 官方文件：背景任務與排程](https://code.claude.com/docs/en/overview)**
  - **讀哪裡**：背景執行、`/loop`、排程相關的段落
  - **注意**：這些功能更新頻繁，以你的版本為準

### 文章

- **[Anthropic — Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)** 的「Agents」一節
  - **這篇說什麼**：agent 在迴圈中從環境取得「ground truth」（工具結果、程式碼執行）來評估進度，以及「stopping conditions (such as a maximum number of iterations)」
  - **和本章的關聯**：本章的 predicate 就是 ground truth、逃生口就是 stopping condition；pstack 把這兩個概念寫成了一份契約

### 對照閱讀

- **[harness_engineering Ch 31 — 背景任務與長時間執行](../harness_engineering/31-background-tasks.md)** 與 **[Ch 39 — 確定性與可重現](../harness_engineering/39-determinism-resume.md)**（本 repo）
  - **和本章的關聯**：從 harness 層面看「長時間執行」與「從中斷處接續」；pstack 的 Pause safely / Session pickup 是建立在這些機制之上的工作流規則

迴圈跑了一整晚，早上你要讀的那份決策日誌，是下一章的主題。

→ [Ch 26 figure-it-out 與決策日誌](./26-figure-it-out-decision-log.md)
