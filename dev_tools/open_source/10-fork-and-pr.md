# Ch 10 — Fork 與 PR 的本質

> **目標**：搞懂協作平台的兩個核心——fork（複製一份 repo 到你名下）和 Pull Request（請求把你的改動合進去）。理解 PR **不是 git 概念而是平台概念**、fork-based vs branch-based 兩種協作模式、以及 merge PR 的三種方式（merge/squash/rebase）。這是 Part 3 之後一切的基礎。

> **環境**：GitHub、git 2.40+、`gh` CLI。

## 為什麼要先講清楚 fork 和 PR

你大概聽過「fork 一個專案、發 PR」，但可能不清楚：fork 到底複製了什麼？PR 是 git 的東西嗎？為什麼有時要 fork、有時不用？merge PR 時那三個按鈕（Merge / Squash / Rebase）差在哪？

這些搞不清楚，你會在貢獻時卡住（「我 clone 了專案卻不能 push」）、或在團隊裡用錯流程。這章把 fork 和 PR 的本質講透——尤其要打破一個常見誤解：**PR 不是 git 功能。**

## 先建立直覺：PR 是「合併請求」，不是 git 的東西

最重要的觀念：**Pull Request 是 GitHub（平台）發明的功能，git 本身沒有 PR。**

git 有什麼？git 有 branch、commit、merge。當你想把 A branch 合進 B branch，純 git 的做法是 `git merge`——但這需要你**有權限 push 到那個 repo**。

問題來了（Ch 1 的核心問題二）：開源專案的 main 受保護，**你沒有 push 權限**。你改好了東西，怎麼請原作者合併？

```
   純 git 世界：
   你改好了 → 想合進原專案的 main → 但你沒權限 push → 卡住

   平台（GitHub）的解法：Pull Request
   你改好了 → 開一個 PR「請求」原作者拉你的改動 → 他 review → 他按按鈕合併
```

**PR = 「我做了改動在我的 branch，請你看看、覺得 OK 就合進你的 main」的一個請求 + 討論 + review 的介面。** 它是 git merge 外面包的一層「社會流程」：請求、審查、討論、批准、然後才 merge。

> 歷史脈絡：純 git 確實有個 `git request-pull` 指令（產生一段「請拉我的 branch」的文字，email 給維護者）——這是 Linux kernel 的協作方式。但它很原始（純文字、沒 UI、沒 review 介面）。GitHub 把它變成有討論、有 review、有 CI、有按鈕的網頁介面——這就是現代 PR。所以 PR 是「`git request-pull` 的平台化、社交化版本」。

## Fork：複製一份到你名下

回到「沒權限」的問題。要對一個你沒權限的專案貢獻，第一步是 **fork**——在 GitHub 上按一個按鈕，把整個 repo **複製一份到你自己的帳號下**。

```
   github.com/orig/project        ← 原專案（你沒 push 權限）
        │ Fork（按按鈕）
        ▼
   github.com/you/project         ← 你的 fork（你有完整權限！）
```

fork 之後：
- 你的 fork 是**獨立的 repo**，在你名下，你愛怎麼改怎麼改、想 push 就 push。
- 它「記得」自己是從原專案 fork 來的（GitHub 知道這個關係）。
- 你在 fork 上做改動 → 發 PR → 請求合進**原專案**。

承 Ch 4 的 remote 設定：clone 你的 fork（`origin`）、加原專案為 `upstream`。

```
   你的本機
   ┌──────────────────────────────────────┐
   │ origin   → github.com/you/project      │ ← 你的 fork，push 到這
   │ upstream → github.com/orig/project     │ ← 原專案，從這同步、PR 目標
   └──────────────────────────────────────┘
```

## 兩種協作模式：fork-based vs branch-based

**不是所有協作都要 fork。** 取決於你有沒有 repo 的 push 權限：

### Fork-based workflow（開源貢獻的標準）

你**沒有**原專案的 push 權限（外部貢獻者）：

```
   fork 原專案 → 在你的 fork 開 branch → push 到你的 fork → 開 PR（從你的 fork 到原專案）
```

這是貢獻陌生開源專案的標準流程——因為你不可能有它的 push 權限。

### Branch-based workflow（團隊內部的標準）

你**有** repo 的 push 權限（團隊成員、collaborator）：

```
   直接 clone 原 repo → 開 branch → push branch 到原 repo → 開 PR（同 repo 內，branch 到 main）
```

不用 fork——你有權限，直接在同一個 repo 開 branch、push、發 PR。團隊內部協作幾乎都這樣（大家都是 collaborator）。

```
   有 push 權限？
   ├─ 沒有（外部貢獻開源）→ fork-based（fork + PR）
   └─ 有（團隊成員）      → branch-based（直接 branch + PR）
```

判準很簡單：**你能不能 push 到那個 repo？** 能就 branch-based，不能就 fork-based。本課練習 C/D 會各做一次。

## 開一個 PR 的完整流程（fork-based）

把 Ch 4 的 remote 操作接上平台：

```bash
# 1. 在 GitHub fork 原專案（按 Fork 按鈕）
# 2. clone 你的 fork
git clone git@github.com:you/project.git && cd project
git remote add upstream git@github.com:orig/project.git

# 3. 從最新的 upstream 開 branch
git fetch upstream
git switch -c fix/the-bug upstream/main    # 基於原專案最新 main 開

# 4. 改、commit（Ch 2 寫好 message）
# ...
git push -u origin fix/the-bug             # push 到你的 fork

# 5. 開 PR
gh pr create --repo orig/project --base main --head you:fix/the-bug
# 或：push 後 GitHub 會顯示一個連結，點開填表單
```

PR 開好後，原專案的維護者會看到它——進入 review、CI、討論的流程（Ch 11-14）。

## Merge PR 的三種方式

PR 通過 review 後，維護者按「合併」——但有**三種**合併方式（GitHub 上是一個下拉選單），它們對歷史的影響完全不同：

```
   你的 PR 有三個 commit：
   A ◄─ B ◄─ C   (你的 PR branch)

   要合進 main 的三種方式：

   1. Create a merge commit（保留所有 commit + 一個 merge commit）
      main: ... ◄─ A ◄─ B ◄─ C ◄─ M(merge)
      → 完整保留你的三個 commit 和分岔記錄

   2. Squash and merge（壓成一個 commit）
      main: ... ◄─ S(A+B+C 壓成一個)
      → 你的三個 commit 變成 main 上的「一個」commit

   3. Rebase and merge（rebase 後直線接上，無 merge commit）
      main: ... ◄─ A' ◄─ B' ◄─ C'
      → 你的 commit 直線接到 main，但無 merge commit
```

| 方式 | 結果 | 適合 |
|---|---|---|
| **Merge commit** | 保留所有 commit + merge commit | 想保留完整開發歷史、feature 邊界清楚 |
| **Squash** | 整個 PR 壓成一個 commit | main 歷史乾淨（一 PR 一 commit），最流行 |
| **Rebase** | commit 直線接上、無 merge commit | 想要直線歷史又保留個別 commit |

**Squash and merge 是目前最流行的**——它讓 main 的歷史是「一個 PR 一個 commit」，超乾淨，且你 PR 裡那些 "wip"/"fix" 通通被壓掉（所以有些用 squash 的專案不要求你整理 PR 歷史，反正會壓）。但缺點是失去個別 commit 的粒度（bisect 只能定位到整個 PR）。

> 認識論誠實：選哪種 merge 是專案的政策（在 repo Settings 設定哪些選項可用）。有些專案三種都開、讓你選；有些只准 squash。這也影響你「要不要花力氣整理 PR 的 commit」——如果專案 squash merge，你的多個 commit 反正會被壓成一個，整理的收益較低（但清楚的 PR 標題/描述仍重要，因為那會變成 squash commit 的 message）。讀專案的 CONTRIBUTING 或看它的 merge 設定。

## PR 跨 fork 怎麼運作

一個你可能困惑的點：你的改動在**你的 fork** 的 branch，PR 卻顯示在**原專案**——怎麼辦到的？

GitHub 知道 fork 關係，所以 PR 能「跨 repo」：PR 的 **head**（來源）是 `you:fix/the-bug`（你 fork 的 branch），**base**（目標）是 `orig:main`（原專案的 main）。維護者 review 你 fork 的 branch、CI 跑你的 branch、merge 時把你 fork branch 的改動拉進原專案的 main。

```
   PR：  head = you/project:fix/the-bug   →   base = orig/project:main
         （你的 fork 的 branch）              （原專案的主線）
```

你後續 push 到你 fork 的那條 branch，PR 會**自動更新**（這就是 review 中迭代的機制，Ch 13/19）——你不用重開 PR，push 就更新。

## 踩雷集錦

1. **以為 PR 是 git 功能**：PR 是 GitHub/GitLab 等平台的功能。純 git 沒有 PR（只有原始的 `git request-pull`）。
2. **clone 原專案卻不能 push**：你 clone 了沒權限的原專案。貢獻要先 fork、clone 你的 fork。
3. **有權限還去 fork**：團隊成員（有 push 權限）不用 fork，直接 branch-based。fork 是給沒權限的外部貢獻者。
4. **不懂三種 merge 方式的差別**：squash 把整個 PR 壓成一個 commit；merge commit 保留全部；rebase 直線無 merge commit。影響 main 歷史長相和你要不要整理 PR commit。
5. **fork 後不同步，PR 基於老舊 main**：fork 是某一刻的快照，原專案會繼續更新。開 branch 前要 `git fetch upstream` 基於最新 main（Ch 25 深入同步）。
6. **PR 開錯 base/head**：base（目標）應是原專案的 main，head（來源）是你 fork 的 branch。開錯方向（或開成你 fork 內部的 PR）是常見失誤。
7. **squash merge 後刪不刪 fork branch**：merge 後你 fork 的那條 branch 沒用了，可以刪（GitHub 會提示）。但別刪你 fork 整個 repo（除非確定不再貢獻該專案）。

## 進階：再往深一層

- **不用 fork 也能對開源貢獻？** 如果你被加為 collaborator（少見於大專案），就能 branch-based。但外部貢獻幾乎都 fork-based。
- **fork 的同步**：fork 不會自動跟上原專案。GitHub 網頁有 "Sync fork" 按鈕，或命令列 `gh repo sync`，或手動 `git fetch upstream && merge`（Ch 25）。
- **PR 的 "Allow edits by maintainers"**：開 PR 時的選項，讓維護者能直接 push 到你的 PR branch（幫你改小東西）。方便但要理解它給了維護者寫你 fork branch 的權限。
- **draft PR**：開成 draft（草稿）表示「還沒好、先別 review/merge」——適合早期分享想法、跑 CI（Ch 11）。
- **跨 fork 的 CI 安全**：外部 PR 的 CI 在維護者 repo 跑，涉及 secret 的 workflow 對 fork PR 有限制（防惡意 PR 偷 secret）——Ch 14、34 談。
- **`gh pr create` 的威力**：命令列開 PR、指定 base/head/reviewer/label，全自動化（Ch 15）。

## 動手練習

1. 在 GitHub fork 一個小的開源專案，clone 你的 fork，`git remote -v` 確認 origin（fork）、加 upstream（原專案），`git remote -v` 再確認。
2. 在你的 fork 開一個 branch、做個小改動（如改 README typo）、push 到你的 fork。
3. 開一個 PR（先別真的送給陌生專案——可以用你自己的兩個 repo 或測試 repo 練），觀察 PR 的 head/base 顯示。
4. 在一個你**有權限**的 repo（自己的）練 branch-based：直接開 branch、push、開 PR（不用 fork）——對比 fork-based。
5. 在自己的測試 repo 開三種 merge 方式各一次（先在 Settings 開啟三種），對比 merge 後 `git log --graph` 的差異（merge commit vs squash vs rebase）。
6. push 新 commit 到一個已開 PR 的 branch，看 PR 自動更新——理解「PR 追蹤 branch」。

## 本章重點整理

- **PR 不是 git 功能，是平台（GitHub）功能**——它是 `git merge` 外面包的「請求 + review + 討論」社會流程，解決「沒 push 權限怎麼貢獻」。
- fork = 複製一份 repo 到你名下（取得完整權限），用於對沒權限的專案貢獻。
- 兩種模式：沒 push 權限 → fork-based（fork + PR）；有權限（團隊）→ branch-based（直接 branch + PR）。判準：能不能 push 到那 repo。
- PR 跨 fork：head=你 fork 的 branch，base=原專案 main；後續 push 自動更新 PR。
- 三種 merge：merge commit（保留全部）/ squash（壓成一個，最流行）/ rebase（直線無 merge commit）——影響 main 歷史與你要不要整理 PR commit。

## 自我檢核

- [ ] PR 是 git 的功能嗎？它本質上是什麼？解決了什麼問題？
- [ ] 什麼時候要 fork、什麼時候不用？判準是什麼？
- [ ] fork-based 流程裡，PR 的 head 和 base 各指向哪？
- [ ] squash merge 和 merge commit 對 main 歷史的影響差在哪？哪個最流行、為什麼？
- [ ] 為什麼 push 到 fork 的 branch 後 PR 會自動更新？

## 延伸閱讀

### 官方文件

- **[GitHub Docs: About pull requests](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/about-pull-requests)** 與 **[About forks](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/about-forks)**
  - **讀哪裡**：PR 與 fork 的概念、fork-based workflow。
  - **和本章的關聯**：本章核心概念的權威。

- **[GitHub Docs: About merge methods](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/about-merge-methods-on-github)**
  - **讀哪裡**：merge commit / squash / rebase 三種方式的精確行為。
  - **和本章的關聯**：三種 merge 的官方說明。

### 部落格 / 文章

- **[GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow)** — GitHub
  - **這篇說什麼**：branch-based 協作流程的官方教學。
  - **和本章的關聯**：branch-based workflow 的完整版（Ch 22 對比其他 model）。

知道怎麼開 PR 後，下一章談「怎麼開一個**好**的 PR」——讓 reviewer 看了想合併，而不是想關掉。

→ [Ch 11 開一個好的 Pull Request](./11-good-pull-request.md)
