# Ch 26 — PR 拆分與 stacked PR

> **目標**：解決「我的改動太大」的協作難題——怎麼把一個大改動拆成多個小而可審的 PR、stacked PR（相依 PR 鏈）怎麼運作、以及怎麼讓大型功能也能用「小 PR」的方式漸進合併。學完你能處理那些「天生很大」的改動，而不是丟一個 800 行的 PR 讓 reviewer 崩潰。

> **環境**：git 2.40+、GitHub、`gh` CLI。前置：Ch 11/18（PR 往小拆）、Ch 7（rebase）、Ch 25（同步）。

## 為什麼大改動要拆

Ch 11/18 反覆強調「PR 往小拆」——但有些改動**天生就大**：一個新功能要動好幾個模組、一次重構碰幾十個檔案、一個架構調整牽一髮動全身。你不能把它硬塞成一個 800 行的 PR（沒人審得動，Ch 11），但它確實是一件完整的事。

怎麼辦？**把大改動拆成一連串小而可審的 PR。** 這是進階協作者的關鍵技能——它讓「大工作」也能享受「小 PR」的好處（好 review、快合併、低風險）。這章教你怎麼拆、以及進階的 stacked PR 技巧。

## 先建立直覺：把大象切成可吞的塊

```
   壞：一個 800 行的大 PR
   ┌────────────────────────────────┐
   │ Add entire auth system          │  ← reviewer：我沒時間，冷處理
   │ (DB schema + API + UI + tests)  │
   └────────────────────────────────┘

   好：拆成一連串小 PR
   ┌──────────────┐ ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
   │ PR1: DB schema│→│ PR2: API     │→│ PR3: UI      │→│ PR4: docs    │
   │ (100 行)      │ │ (150 行)     │ │ (200 行)     │ │ (50 行)      │
   └──────────────┘ └──────────────┘ └──────────────┘ └──────────────┘
   每個都好審、快合併、低風險
```

拆 PR 的藝術在於：**每個小 PR 本身要是「完整、可獨立 review、不破壞 main」的單位**——不是機械地按行數切，而是按「邏輯上的可獨立步驟」切。

## 怎麼拆：拆分策略

### 策略一：按層次/模組拆

一個功能涉及多層（DB → API → UI），按層拆：

```
   PR1: 加 DB schema + migration（可獨立合併，不影響現有功能）
   PR2: 加 API endpoint（用 PR1 的 schema）
   PR3: 加 UI（用 PR2 的 API）
```

每個 PR 是「下一個的基礎」，但各自完整、可審、合進去不會壞 main（因為新東西還沒被串起來用）。

### 策略二：先鋪路，再蓋房（preparatory refactor）

要加功能 X，但現有 code 結構不適合——先重構（不改行為），再加功能：

```
   PR1: 重構（純結構調整，不改行為，有測試保證行為不變）
   PR2: 在重構後的結構上加功能 X
```

把「重構」和「加功能」分開是黃金原則（Ch 18 的 scope）——混在一起 reviewer 分不清「哪些是重構、哪些是新行為」。分開後，重構 PR 易審（行為沒變、測試綠）、功能 PR 也聚焦。

### 策略三：feature flag 包起來漸進合併

承 Ch 22 trunk-based：大功能用 **feature flag** 包起來，分多個 PR 漸進進 main（功能關著、不影響使用者），全部進去後再開 flag：

```
   PR1-4: 逐步加功能的各部分（都在 if feature_flag 後面，預設關）
   PR5: 開啟 flag（功能正式啟用）
```

這讓「未完成的大功能」能安全地、分批地進 main，不用維護一個長命大 branch（避免 Ch 25 的同步地獄）。

## Stacked PR：相依的 PR 鏈

當小 PR **彼此相依**（PR2 建立在 PR1 上，PR1 還沒合併），就形成 **stacked PR**（堆疊的 PR）：

```
   main
     │
     └─ branch1 (PR1) ──┐
                        └─ branch2 (PR2，基於 branch1) ──┐
                                                        └─ branch3 (PR3，基於 branch2)
```

每個 branch 基於前一個（不是基於 main），各開一個 PR。reviewer 可以一個個審，你也能一邊等 PR1 review、一邊在 PR2 上繼續做。

### Stacked PR 的設定

```bash
# PR1：基於 main
git switch main && git switch -c feature/part1
# ...做 part1, commit...
git push -u origin feature/part1
gh pr create --base main --title "Part 1: DB schema"

# PR2：基於 part1（不是 main！）
git switch -c feature/part2          # 從 part1 開
# ...做 part2, commit...
git push -u origin feature/part2
gh pr create --base feature/part1 --title "Part 2: API"   # base 是 part1！

# PR3：基於 part2
git switch -c feature/part3
gh pr create --base feature/part2 --title "Part 3: UI"
```

關鍵：每個 PR 的 **base 是前一個 branch**（`--base feature/part1`），不是 main。這樣 PR2 只顯示「相對於 part1 的增量」（乾淨的 diff），不會混入 part1 的內容。

### Stacked PR 的痛點與工具

stacked PR 最痛的是**維護整疊**——PR1 被 review 改了，PR2/PR3 都要跟著 rebase；PR1 合併進 main 後，PR2 的 base 要改成 main。手動很煩：

```bash
# PR1 改了之後，整疊要跟上
git switch feature/part1     # （改完 PR1）
git switch feature/part2
git rebase feature/part1     # part2 跟上 part1 的新版
git push --force-with-lease
git switch feature/part3
git rebase feature/part2     # part3 跟上 part2
git push --force-with-lease
```

`git rebase --update-refs`（git 2.38+，Ch 7）能在 rebase 時**自動更新整疊的 branch 指標**，省去逐個 rebase。還有專門的 stacked PR 工具（Graphite、`git-branchless`、`spr`、Sapling）把這套自動化——大公司（Meta 等）內部大量用 stacked PR + 工具。

> 認識論誠實：stacked PR 強大但有維護成本，且 GitHub 原生支援不算好（base 改動、整疊 rebase 都要手動或靠工具）。**多數情況，「按可獨立合併的步驟拆成獨立 PR」（一個合了再開下一個）比 stacked PR 簡單**。stacked PR 適合「步驟相依強、且想平行 review/開發」的情境，或有工具支援的團隊。新手先掌握「拆成獨立 PR」，stacked PR 是進階選項。

## 拆 PR 的描述：給 reviewer 地圖

拆成多個 PR 時，幫 reviewer 理解全局——在每個 PR 描述裡標明它在整體中的位置：

```markdown
## Part 2 of 4: API endpoints

This is part of #123 (add auth system). 

- [x] Part 1: DB schema (#456, merged)
- [ ] **Part 2: API endpoints (this PR)**
- [ ] Part 3: UI
- [ ] Part 4: docs

Depends on #456. Review that first.
```

讓 reviewer 知道：這是大計畫的一部分、前面的合了沒、這個依賴什麼、整體目標是什麼。沒有這個地圖，reviewer 看到一個「半截」的 PR 會困惑。

## 一個完整的拆 PR 流程

加一個中型功能（綜合本章）：

```
1. 規劃：把功能拆成 N 個「可獨立 review、不破壞 main」的步驟
   （按層次/先重構後功能/feature flag）

2. 開一個 tracking issue（Ch 12）：列出所有步驟，當總覽

3. 逐個做：
   - 獨立 PR 策略：做 PR1 → 合併 → 基於新 main 做 PR2 → ...（簡單）
   - 或 stacked：PR1/PR2/PR3 各基於前一個，平行 review（進階）

4. 每個 PR 描述標明在整體中的位置（part X of N、depends on、tracking issue）

5. 全部合併後，關閉 tracking issue
```

## 踩雷集錦

1. **硬塞一個大 PR**：reviewer 沒時間、冷處理、拖很久。天生大的改動要拆。
2. **重構和功能混在一個 PR**：reviewer 分不清行為改動。先重構（PR1，行為不變）、再加功能（PR2）。
3. **拆得太碎/沒邏輯**：機械按行數切成不完整的塊（PR1 自己根本跑不起來）。按「可獨立 review 的邏輯步驟」拆。
4. **stacked PR 的 base 設成 main**：PR2 的 base 該是 part1，設成 main 會把 part1 的內容也混進 PR2 的 diff。
5. **stacked PR 不維護整疊**：PR1 改了/合了，PR2/3 沒跟上 → base 錯亂、衝突。用 `--update-refs` 或工具。
6. **沒給 reviewer 地圖**：丟一個「半截」PR 不說明它是大計畫的一部分。每個 PR 標明 part X of N、依賴、tracking issue。
7. **新手就上 stacked PR**：維護成本高、GitHub 支援差。先用「獨立 PR、一個合了再下一個」，stacked 是進階。

## 進階：再往深一層

- **`git rebase --update-refs`**（Ch 7）：rebase 一條 branch 時自動更新疊在它上面的其他 branch 指標——stacked PR 的原生救星。
- **stacked PR 工具**：Graphite、Sapling（Meta 開源）、`git-branchless`、`spr`、`ghstack`——把 stacked PR 的建立、更新、提交自動化。大團隊重度用。
- **split 已存在的大 PR**：已經做出一個大改動才想拆？用 `git rebase -i` 把 commit 分組（Ch 7 拆 commit）、或 cherry-pick 部分 commit 到新 branch（Ch 9）。
- **preparatory refactor 的紀律**：「先重構不改行為」要有測試保證行為真的沒變（測試綠 = 重構安全）——這是讓重構 PR 易審的關鍵。
- **feature flag 的生命週期**：flag 用完要清掉（別累積一堆死 flag）。這正是本課開頭提過的「flag 有 cleanup 義務」。
- **大功能的 RFC/design doc**：超大功能，PR 之前先寫設計文件（RFC）給維護者/團隊審方向（Ch 17/18 的「先討論」放大版）——避免拆了一堆 PR 才發現方向錯。

## 動手練習

1. 拿一個你（假想或真實）的大改動，練習把它拆成 3-4 個「可獨立 review、不破壞 main」的步驟，寫下每個 PR 的範圍。
2. 練「先重構後功能」：拿一段 code，先寫一個「純重構不改行為」的改動（測試保證行為不變），再在上面加功能——體會分開的好處。
3. 在測試 repo 做一個 stacked PR：part1 基於 main、part2 基於 part1（`gh pr create --base part1`），看 part2 的 diff 是否乾淨（只有增量）。
4. 改 part1 後，用 `git rebase --update-refs` 更新整疊，看它自動更新 part2/3 的指標。
5. 為一組拆分的 PR 寫描述（part X of N + depends on + tracking issue 連結）。
6. 看一個大型開源專案怎麼處理大功能（找一個 tracking issue + 一串相關 PR），學它怎麼拆。

## 本章重點整理

- 天生大的改動要拆成「小而可審、各自完整、不破壞 main」的多個 PR——享受小 PR 的好處。
- 拆分策略：按層次/模組、先重構後功能（分開！）、feature flag 漸進合併。
- 按「可獨立 review 的邏輯步驟」拆，不是機械按行數切。
- stacked PR：相依的 PR 鏈，每個 base 是前一個 branch（不是 main），可平行 review/開發；但維護整疊有成本（`--update-refs`/工具幫忙）。
- 多數情況「獨立 PR、一個合了再下一個」比 stacked 簡單；stacked 是進階選項。
- 每個拆分 PR 要給 reviewer 地圖（part X of N、依賴、tracking issue）。

## 自我檢核

- [ ] 為什麼大改動要拆？拆的判準是什麼（不是按行數）？
- [ ] 為什麼「重構」和「加功能」要分成不同 PR？怎麼保證重構安全？
- [ ] stacked PR 是什麼？每個 PR 的 base 該設成什麼？
- [ ] stacked PR 的主要痛點是什麼？有哪些工具/指令緩解？
- [ ] 拆成多個 PR 時，怎麼幫 reviewer 理解全局？

## 延伸閱讀

### 部落格 / 文章

- **[Stacked Diffs Versus Pull Requests](https://jg.gg/2018/09/29/stacked-diffs-versus-pull-requests/)** — Jackson Gabbard（ex-Facebook）
  - **這篇說什麼**：stacked diff 工作流（Facebook 內部）vs GitHub PR 的對比，為什麼大團隊愛 stacked。
  - **為什麼值得讀**:理解 stacked PR 哲學的經典，解釋它解決什麼。

- **[How to split a Pull Request](https://www.thedroidsonroids.com/blog/splitting-pull-request)** 類拆 PR 實務文
  - **這篇說什麼**：拆大 PR 的具體策略與步驟。
  - **為什麼值得讀**：本章拆分策略的實例補充。

- **[Graphite / Sapling docs](https://graphite.dev/docs)** — stacked PR 工具
  - **這是什麼**:把 stacked PR 自動化的工具文件。
  - **和本章的關聯**：想實際用 stacked PR 的工具選項。

PR 拆好了，下一章是讓團隊協作規範自動執行的最後一塊——commit/PR 規範自動化：Conventional Commits、commitlint、pre-commit hooks。

→ [Ch 27 commit/PR 規範自動化](./27-conventions-automation.md)
