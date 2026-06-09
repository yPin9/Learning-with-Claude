# Ch 35 — 進階 git 協作場景

> **目標**：補齊協作中會遇到的進階 git 工具——`git bisect`（二分搜尋找出引入 bug 的 commit）、`git blame` / `git log` 考古、submodule / subtree（一個 repo 包含另一個）、以及 monorepo 的協作。這些不是天天用，但遇到時不會就會卡很久。

> **環境**：git 2.40+。前置：Part 2（中階 git）、Ch 2（atomic commit）。

## 為什麼要會這些進階工具

前面的 git（branch/merge/rebase/衝突）是協作的日常。但有些場景需要更進階的工具：

- 「上週還好好的，現在壞了——是哪個 commit 弄壞的？」→ `git bisect`
- 「這行詭異的 code 是誰、為什麼、什麼時候加的？」→ `git blame` / `git log`
- 「我的專案要包含另一個 repo（共用函式庫）」→ submodule / subtree
- 「公司把所有專案放一個巨型 repo」→ monorepo

這些不是天天用，但遇到時，會與不會差很多——會的人十分鐘解決，不會的人卡半天或用笨方法。這章把它們講清楚，當你的工具箱。

## git bisect：二分搜尋找出引入 bug 的 commit

最強大、最被低估的 debug 工具。場景：**某個功能「以前好好的，現在壞了」，但中間有幾百個 commit，不知道是哪個弄壞的。**

`git bisect` 用**二分搜尋**自動定位：

```
   你知道：           commit （時間 →）
   v1.0 好的 ────●────●────●────●────●──── 現在壞的
              (good)                    (bad)
                  │
   bisect 二分：跳到中間那個 commit，問你「這個好還壞？」
   - 好 → bug 在後半，再二分後半
   - 壞 → bug 在前半，再二分前半
   - log2(N) 次就找到「第一個壞掉的 commit」
   （500 個 commit 只要約 9 次！）
```

用法：

```bash
git bisect start
git bisect bad                    # 當前（現在）是壞的
git bisect good v1.0              # v1.0（已知好的）是好的
# git checkout 到中間某個 commit，你測試它：
#   壞 → git bisect bad
#   好 → git bisect good
# 重複，git 自動二分，最後告訴你「第一個 bad commit」
git bisect reset                  # 結束，回到原本的 HEAD
```

更強的是**自動化**——給一個「測試腳本」（回傳 0=good、非 0=bad），git 自動跑完整個 bisect：

```bash
git bisect start HEAD v1.0
git bisect run ./test-for-bug.sh   # 自動跑，自動定位，完全不用人工
```

`git bisect run` + 測試腳本 = 全自動找出引入 bug 的 commit。這就是為什麼 **atomic commit（Ch 2）重要**——每個 commit 能獨立 build/測試，bisect 才能精準定位到「就是這個 commit」；混雜的大 commit 讓 bisect 指向一坨東西，沒幫助。

> 協作中的價值：找到「引入 bug 的 commit」後，你能看它的 message（為什麼這樣改，Ch 2）、誰寫的、相關 PR——直接定位問題根源，而非瞎猜。對 regression（以前好現在壞）這是降維打擊。

## git blame / git log：程式碼考古

協作久了，你會接手別人的 code、看到看不懂的東西。**考古工具**讓你查「這段 code 的來歷」：

```bash
# blame：每一行是誰、哪個 commit、什麼時候加的
git blame src/file.py
git blame -L 40,50 src/file.py    # 只看 40-50 行
#   a1b2c3 (Alice 2023-05-01) def process(x):

# 從 blame 找到 commit 後，看那個 commit 的完整脈絡
git show a1b2c3                   # 那個 commit 改了什麼、message（為什麼，Ch 2）

# log：某檔案/某行的歷史演變
git log -p src/file.py            # 這個檔案每次改動
git log -L 40,50:src/file.py      # 第 40-50 行的演變史（強！）
git log -S "some_function"        # 哪些 commit 增/刪了含這字串的程式碼（pickaxe）
git log --grep "fix login"        # commit message 含某字的
```

考古的價值：

- **理解「為什麼這樣寫」**：看到可疑 code，blame 找到 commit，看 message 知道原因（也許有你沒看到的理由——別貿然改）。
- **debug 時找線索**：某行 code 何時引入、為什麼——配合 bisect 定位 regression。
- **貢獻前讀懂 codebase**（Ch 17）：blame/log 是理解陌生 code 來歷的利器。

`git log -L`（看某幾行的演變史）和 `git log -S`（pickaxe，找增刪某字串的 commit）是兩個被低估的神器——「這段邏輯是怎麼演變成現在這樣的」「這個函式是哪個 commit 加的/刪的」一查就知道。

> blame 的禮儀：`git blame` 是查來歷的工具，不是「找戰犯」的工具。看到爛 code 用 blame 查脈絡是為了理解、修好，不是為了指責寫的人（對事不對人，Ch 20/29）。GitHub 也有 blame view（按行看歷史）。

## Submodule：一個 repo 引用另一個 repo

協作中常需要「我的專案包含另一個 repo」（共用函式庫、第三方相依的 source）。**submodule** 讓一個 repo 把另一個 repo 當「子目錄」嵌進來，但保持它是獨立的 repo：

```bash
git submodule add https://github.com/org/lib.git libs/lib
# 你的 repo 記錄「libs/lib 應該是 lib repo 的某個 commit」
git submodule update --init --recursive    # clone 後拉取 submodule 內容
```

submodule 的特性與坑：

- **記錄的是「某個 commit」**：你的 repo 不存 submodule 的內容，只存「它該在哪個 commit」（一個指標）。
- **要手動更新**：submodule 不會自動跟著主 repo 更新。`git submodule update` 拉取、改 submodule 要進去 commit + push、再回主 repo 更新指標。
- **clone 要 `--recursive`**：忘了的話 submodule 是空的（`git submodule update --init` 補救）。
- **協作摩擦多**：團隊成員忘了 update submodule、submodule 指標衝突——submodule 是出了名的「容易出錯、新手困惑」。

> 認識論誠實：submodule 功能強但**體驗差、坑多**——很多團隊避用它，改用套件管理器（npm/pip/cargo 管相依）或 subtree。需要「嵌入另一個 repo 的精確版本且要能改它」時 submodule 才合適；單純用第三方函式庫，套件管理器更好。

## Subtree：把另一個 repo 的內容合進來

**subtree** 是 submodule 的替代——它把另一個 repo 的**內容**直接合進你的 repo（不是指標），對使用者透明：

```bash
git subtree add --prefix=libs/lib https://github.com/org/lib.git main --squash
# lib 的內容直接進你的 repo，clone 不用 --recursive，使用者無感
git subtree pull --prefix=libs/lib <lib-url> main --squash   # 更新
```

subtree vs submodule：

| | submodule | subtree |
|---|---|---|
| 存什麼 | 指標（某 commit）| 實際內容 |
| clone | 要 `--recursive` | 透明（內容已在）|
| 使用者 | 要懂 submodule | 無感 |
| 更新 | 指標 | 內容合併 |
| 缺點 | 坑多、易錯 | repo 變大、history 較雜 |

subtree 對「使用者」友善（不用懂 submodule），但維護者操作較複雜。各有取捨——多數情況套件管理器 > 兩者都不用。

## Monorepo：一個巨型 repo 放所有專案

大公司（Google、Meta）和很多團隊用 **monorepo**——一個 repo 放所有專案/服務，而非每個一個 repo（polyrepo）：

```
   monorepo                         polyrepo
   ┌──────────────────┐            ┌────┐ ┌────┐ ┌────┐
   │ /service-a/      │            │ a  │ │ b  │ │ c  │
   │ /service-b/      │            └────┘ └────┘ └────┘
   │ /shared-lib/     │            分散在多個 repo
   │ 全部在一個 repo   │
   └──────────────────┘
```

monorepo 對協作的影響：

- **共用 code 容易**：shared-lib 改了，所有用它的服務立刻看得到（不用發版本、更新相依）。原子化的跨專案改動（一個 PR 同時改 lib + 用它的服務）。
- **CODEOWNERS 更重要**（Ch 24）：一個 repo 多個團隊，靠 CODEOWNERS 分責任。
- **CI 要聰明**：只跑「改到的部分」相關的測試（不是每次跑全部）——靠 build 工具（Bazel、Nx、Turborepo）做 affected detection。
- **巨大 repo 的 git 效能**：超大 monorepo（Google 級）需要特殊工具（sparse-checkout、partial clone，Ch 4）——一般團隊的 monorepo 沒這麼極端。
- **branching/PR 不變**：協作流程（branch/PR/review）和 polyrepo 一樣，只是範圍是整個 monorepo。

> monorepo vs polyrepo 是工程文化之爭（像 Ch 22 的 branching model）。monorepo 利於共用和原子改動，但需要工具支援（CI、build）；polyrepo 簡單但跨 repo 改動麻煩（要協調多個 PR、版本）。沒有絕對對錯，看團隊需求。

## 踩雷集錦

1. **regression 不用 bisect 瞎猜**：「以前好現在壞」用 `git bisect`（甚至 `bisect run` 全自動）幾分鐘定位，別一個個 commit 看。
2. **commit 不 atomic 害 bisect 失效**：混雜的大 commit 讓 bisect 指向一坨。atomic commit（Ch 2）是 bisect 有效的前提。
3. **blame 當找戰犯**：blame 是理解來歷的工具，不是指責。對事不對人。
4. **改可疑 code 前不查脈絡**：blame/log 找到原 commit 看 message——也許有你不知道的原因，別貿然改壞。
5. **clone 含 submodule 的 repo 忘了 `--recursive`**：submodule 是空的。`git submodule update --init --recursive`。
6. **submodule 改了忘了兩邊都 commit**：submodule 要在 submodule 裡 commit+push，再回主 repo commit 新指標。漏一步別人拿不到。
7. **為了用第三方函式庫上 submodule**：套件管理器（npm/pip）更適合。submodule 留給「要嵌入並能改的精確版本」。

## 進階：再往深一層

- **`git bisect skip`**：bisect 遇到「無法測試」的 commit（build 壞、無關）時 skip，git 繞過它。
- **`git bisect run` 的退出碼**：腳本回 0=good、1-124/126-127=bad、125=skip——精細控制自動 bisect。
- **`git log` 的更多選項**：`--author`、`--since`/`--until`、`--follow`（跨 rename 追檔案歷史）、`-G`（regex pickaxe）。
- **reverse blame（`-L` + log）**：追一段 code「從哪來、怎麼演變」的完整考古。
- **submodule 的 `.gitmodules`**：記錄 submodule 的 URL/path/branch；`git submodule foreach` 對所有 submodule 批次操作。
- **monorepo 工具**：Bazel/Nx/Turborepo/pnpm workspaces——管理 monorepo 的 build/test/相依/affected detection。
- **sparse-checkout / partial clone**（Ch 4）：超大 repo 只 checkout/拉取你需要的部分——Google 級 monorepo 的必備。
- **`git worktree`**（Ch 3 提過）：同時 checkout 多 branch 到不同目錄，monorepo/多功能開發時方便。

## 動手練習

1. 在一個有歷史的 repo（你的或 clone 一個），製造一個 regression（在某個 commit 弄壞一個測試），用 `git bisect` 手動定位引入它的 commit。
2. 寫一個測試腳本，用 `git bisect run` 全自動定位——體驗自動化的威力。
3. 對一段你看不懂的 code 用 `git blame` 找到原 commit，`git show` 看它的 message 和脈絡。
4. 用 `git log -L <行範圍>:<檔案>` 看某幾行的演變史；用 `git log -S "某函式名"` 找哪個 commit 加/刪了它。
5. 建一個含 submodule 的測試 repo（`git submodule add`），clone 它（故意忘了 `--recursive`），看 submodule 空的，再 `update --init` 補救——體會 submodule 的坑。
6. 看一個真實的 monorepo（很多公開的，如某些大型 JS 專案），看它的目錄結構、CODEOWNERS、CI 怎麼處理「只測改到的部分」。

## 本章重點整理

- `git bisect` 二分搜尋找出引入 bug 的 commit（regression debug 神器）；`bisect run` + 測試腳本全自動。atomic commit（Ch 2）是它有效的前提。
- `git blame`（每行的來歷）+ `git log -L`/`-S`/`-p`（程式碼演變/pickaxe）是考古工具——理解陌生 code、找 bug 線索、貢獻前讀懂 codebase。blame 是理解不是指責。
- submodule（記指標、要 `--recursive`、坑多）vs subtree（記內容、使用者無感、操作複雜）——「一個 repo 含另一個」的兩種方式；多數情況套件管理器更好。
- monorepo（一個 repo 放所有專案）利於共用/原子改動，需要工具支援（CI affected detection、CODEOWNERS）；vs polyrepo 是工程文化之爭。

## 自我檢核

- [ ] 「以前好現在壞」的 regression，怎麼快速找出是哪個 commit 弄壞的？為什麼 atomic commit 重要？
- [ ] `git blame` 和 `git log -L` 各查什麼？blame 該用什麼心態？
- [ ] submodule 和 subtree 的核心差別是什麼？什麼時候用、什麼時候用套件管理器就好？
- [ ] monorepo 對協作有什麼影響（共用、CODEOWNERS、CI）？
- [ ] `git bisect run` 怎麼全自動定位 bug commit？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 7.10 (Debugging with Git — bisect/blame)](https://git-scm.com/book/en/v2/Git-Tools-Debugging-with-Git)** 與 **[Ch 7.11 (Submodules)](https://git-scm.com/book/en/v2/Git-Tools-Submodules)**
  - **讀哪幾章**:7.10（bisect + blame）、7.11（submodule 完整）。
  - **和本章的關聯**:本章工具的官方完整版。

### 部落格 / 文章

- **[git bisect run automation](https://lwn.net/Articles/317154/)** 類 / git 官方 `git-bisect` 文件
  - **這篇說什麼**:bisect 與自動化的實戰。
  - **為什麼值得讀**:把 bisect 用到極致。

- **[Monorepo vs Polyrepo](https://github.com/joelparkerhenderson/monorepo-vs-polyrepo)** / **[Trunk-based + monorepo 的論述]**
  - **這篇說什麼**:兩種 repo 策略的取捨。
  - **為什麼值得讀**:理解工程文化之爭，呼應 Ch 22。

進階工具會了，下一章是協作中總會遇到的「災難現場」——疑難雜症排解：救壞掉的 PR、歷史改寫的代價、誤推 secret 的處理。

→ [Ch 36 疑難雜症排解](./36-troubleshooting.md)
