# Ch 4 — remote 深入

> **目標**：把 `remote` 從「就是 origin」升級成協作的核心概念。理解 remote 是什麼、`origin` vs `upstream`、fetch 與 pull 的根本差別、多 remote 協作、以及 refspec（push/fetch 到底在搬什麼）。這是 fork 協作（Ch 10）和同步上游（Ch 25）的基礎。

> **環境**：git 2.40+、GitHub。

## 為什麼要深入 remote

你會 `git push origin main`、`git pull`，但可能從沒想過 `origin` 是什麼、為什麼叫這名字、能不能有別的 remote。在單機+一個 GitHub repo 的世界，`origin` 是唯一的 remote，你不需要想太多。

但協作——尤其貢獻開源——一定會碰到**多個 remote**：你的 fork 是一個 remote（`origin`）、原專案是另一個 remote（`upstream`）。不懂 remote 的人，會在「我 fork 了專案，但怎麼跟上原專案的更新」這裡卡死。這章把 remote 講透。

## 先建立直覺：remote 是「別份 repo 的捷徑」

Ch 1 講過 git 是分散式的——每個 repo 都是完整獨立的。**remote 就是「我這個 repo 知道的、別份 repo 的地址簿」**，給它取個短名字，這樣你不用每次打完整 URL。

```
   你的本機 repo
   ┌────────────────────────────────────┐
   │  地址簿（remotes）:                  │
   │    origin   → github.com/you/repo    │  ← 一個短名字對應一個 URL
   │    upstream → github.com/orig/repo   │
   │                                      │
   │  你的 branch、commit、歷史...         │
   └────────────────────────────────────┘
       fetch/push 時，用短名字指定要跟「哪份 repo」同步
```

`origin` 不是什麼特殊關鍵字——它只是 `git clone` 時**預設**幫你取的 remote 名字（指向你 clone 的來源）。你完全可以加別的 remote、改名、刪除。

```bash
git remote -v                          # 看所有 remote 和它們的 URL
#   origin  git@github.com:you/repo.git (fetch)
#   origin  git@github.com:you/repo.git (push)

git remote add upstream git@github.com:orig/repo.git   # 加一個叫 upstream 的
git remote rename origin myorigin      # 改名
git remote remove upstream             # 刪除
git remote set-url origin <new-url>    # 改 URL（如 HTTPS 換 SSH）
```

## origin vs upstream：fork 協作的命名慣例

貢獻開源時，你不會（也不能）直接 push 到原專案。流程是：**fork 原專案到你名下 → clone 你的 fork → 從原專案拉更新。** 於是你有兩個 remote：

```
   github.com/torvalds/linux   ← 原專案（你沒有 push 權限）
        │ fork（在 GitHub 上按按鈕，複製一份到你名下）
        ▼
   github.com/you/linux        ← 你的 fork（你有完整權限）
        │ clone
        ▼
   你的本機
   ┌──────────────────────────────────────┐
   │  origin   → github.com/you/linux       │ ← 你的 fork（push 到這）
   │  upstream → github.com/torvalds/linux  │ ← 原專案（從這拉更新）
   └──────────────────────────────────────┘
```

命名慣例（不是 git 強制，但幾乎人人遵守）：

- **`origin`** = 你自己的 fork（你 push 的地方）
- **`upstream`** = 原專案（你拉更新的地方，因為它在你的「上游」）

設定方式（Ch 10 會完整走一遍 fork 流程）：

```bash
git clone git@github.com:you/linux.git    # origin 自動設成你的 fork
cd linux
git remote add upstream git@github.com:torvalds/linux.git   # 手動加 upstream
git remote -v        # 確認兩個都在
```

> 命名陷阱：上一章提過 tracking branch 的「upstream」（本地 branch ↔ 遠端 branch 的綁定）和這裡的「upstream remote」（你的 fork ↔ 原專案）**是不同概念，同一個詞**。前者是 branch 層級的綁定，後者是 remote 的名字。看上下文區分。

## fetch vs pull：最重要的區別

這是新手最該搞懂的一件事。

**`git fetch`**：從 remote **下載**新的 commit 和 branch，更新你的**遠端追蹤 branch**（`origin/main` 等，Ch 3），但**完全不碰你的本地 branch 和工作目錄**。

**`git pull`**：`fetch` + **自動合併**（merge 或 rebase）到你當前的本地 branch。

```
   git fetch                           git pull
   ┌─────────────┐                     ┌─────────────┐
   │ 下載遠端更新 │                     │ 下載遠端更新 │
   │ 更新         │                     │ 更新         │
   │ origin/main  │                     │ origin/main  │
   │              │                     │      +       │
   │ 你的 main    │ ← 不動！             │ merge/rebase │ ← 自動改你的 main！
   │ 工作目錄     │ ← 不動！             │ 進你的 main  │
   └─────────────┘                     └─────────────┘
   安全、可預測                         方便、但會自動改你的 branch
```

關鍵理解：**`git pull` = `git fetch` + `git merge`（或 rebase）。** pull 的「自動合併」這一步，是它和 fetch 的全部差別，也是它有時讓人意外的原因（突然產生 merge commit、突然要解衝突）。

### 為什麼老手常常 fetch 而非 pull

```bash
# 老手的習慣：先 fetch 看清楚，再決定怎麼整合
git fetch origin
git log --oneline main..origin/main    # 看遠端比我多了哪些 commit
git diff main origin/main              # 看差異
# 確認後再決定 merge 還是 rebase（Ch 5/6）
git merge origin/main                  # 或 git rebase origin/main
```

`fetch` 給你「先看再決定」的控制權；`pull` 直接幫你決定（用預設的 merge 或 rebase）。協作中突然冒出的 merge commit、莫名其妙的衝突，很多是 `pull` 自動合併造成的。理解 `pull = fetch + merge`，你就能在需要時拆開來做、掌控節奏。

> `pull` 的合併方式：`git config pull.rebase false`（預設，用 merge）/ `true`（用 rebase）/ `--ff-only`（只允許 fast-forward，否則報錯——很多老手設這個，逼自己明確處理）。Ch 0 的 .gitconfig 設了 `false`，Ch 6 會深入這個選擇。

## refspec：push/fetch 到底搬什麼

進階但重要：當你 `git push origin main`，git 到底做了什麼？背後是 **refspec**——`<來源>:<目標>` 的對應規則。

```bash
git push origin main                   # 簡寫
git push origin main:main              # 完整：把本地 main push 到遠端 main
git push origin HEAD:main              # 把當前位置 push 到遠端 main
git push origin local-name:remote-name # 本地 branch 推到遠端不同名的 branch
git push origin :old-branch            # 來源空 = 刪除遠端的 old-branch！
```

`fetch` 的 refspec 在 `git remote -v` 看不到，但在 `.git/config` 裡：

```ini
[remote "origin"]
    url = git@github.com:you/repo.git
    fetch = +refs/heads/*:refs/remotes/origin/*
    #       把遠端所有 branch，對應到本地的 origin/* 追蹤 branch
```

這條 fetch refspec 就是「為什麼 `git fetch` 後 `origin/main`、`origin/feature` 會更新」的原因——它把遠端的 `refs/heads/*` 映射到本地的 `refs/remotes/origin/*`。

你不用天天寫 refspec，但理解它能解釋幾個「魔法」：為什麼 push 一個 branch 會建立同名遠端 branch、為什麼 `git push origin :branch` 能刪遠端 branch、為什麼 fetch 後追蹤 branch 自動更新。

## 一個 fork 協作的完整 remote 流程

把這章串起來——貢獻開源的 remote 操作（Ch 10 補平台部分）：

```bash
# 1. 在 GitHub fork 原專案到你名下（按按鈕）
# 2. clone 你的 fork
git clone git@github.com:you/project.git
cd project
# origin 已自動指向你的 fork

# 3. 加 upstream 指向原專案
git remote add upstream git@github.com:orig/project.git

# 4. 開發前，先從 upstream 同步最新的 main
git fetch upstream
git switch main
git merge upstream/main          # 把原專案的更新合進你的本地 main（Ch 25 細談）
git push origin main             # 順便更新你 fork 的 main

# 5. 開 branch 做你的貢獻
git switch -c fix/something
# ...改、commit...
git push -u origin fix/something  # push 到你的 fork
# → 開 PR（Ch 10）
```

這個「origin = 我的 fork、upstream = 原專案、從 upstream 同步、push 到 origin」的模式，是開源貢獻的標準骨架。Ch 25 會深入「怎麼維護長命的 fork 跟上快速變動的 upstream」。

## 踩雷集錦

1. **不知道 `pull` 會自動合併**：`pull` 突然產生 merge commit 或要解衝突，是因為它 = fetch + merge。想看清楚再動，用 `fetch` + 手動整合。
2. **fork 後忘了加 upstream**：只有 origin（你的 fork），不知道怎麼拉原專案更新。`git remote add upstream <原專案>`。
3. **直接 push 到 upstream（原專案）**：你沒權限，會被拒。貢獻是 push 到你的 fork（origin）再發 PR。
4. **`origin/main` 不更新**：忘了 `git fetch`。遠端追蹤 branch 只在 fetch/pull 時更新（Ch 3）。
5. **`git push origin :branch` 手滑刪錯遠端 branch**：refspec 來源空 = 刪除。確認 branch 名再按。現在多用 `git push origin --delete branch` 較清楚。
6. **clone 別人的 repo 想貢獻，卻不能 push**：你 clone 的是原專案（沒權限），不是你的 fork。要先 fork 再 clone 你的 fork。
7. **HTTPS clone 後想改 SSH**：`git remote set-url origin git@github.com:...`，不用重 clone。

## 進階：再往深一層

- **`git remote show origin`**：看一個 remote 的詳細狀態——哪些 branch tracked、哪些 stale、push/pull 的對應。診斷 remote 問題的利器。
- **多 fork 協作**：審查同事的 PR 時，可以加他的 fork 當 remote（`git remote add alice <alice-fork>`），fetch 下來本地測試他的 branch。`gh pr checkout`（Ch 15）把這自動化。
- **`git fetch --all`**：一次更新所有 remote 的追蹤 branch。
- **`fetch.prune` / `--prune`**：清掉遠端已刪 branch 的本地追蹤參照（Ch 0 設了 prune=true）。沒設的話 `origin/old-branch` 會殘留。
- **mirror / bare repo**：`git clone --mirror` 做完整鏡像（含所有 ref），備份或遷移 repo 用。
- **partial clone / shallow clone**：`git clone --depth 1`（只拉最近歷史）、`--filter=blob:none`（按需拉檔案）——巨型 repo（如 chromium）的協作必備，省頻寬與空間。
- **insteadOf**：`git config url."git@github.com:".insteadOf "https://github.com/"` 自動把 HTTPS URL 改寫成 SSH，省得一個個改 remote。

## 動手練習

1. `git remote -v` 看你某個 repo 的 remote；`git remote show origin` 看詳細狀態。
2. 在 GitHub fork 一個小專案，clone 你的 fork，`git remote add upstream <原專案>`，`git remote -v` 確認 origin/upstream 都在。
3. 對比 `git fetch upstream`（看 main 沒變、`upstream/main` 更新了）vs `git pull upstream main`（main 直接被改）——親身體會 fetch/pull 差別。
4. `git log --oneline main..origin/main` 看「遠端比本地多的 commit」（fetch 後）。
5. 故意在一個測試遠端 branch 上 `git push origin :test-branch` 刪除它，再用 `git push origin --delete` 刪另一個，比較兩種寫法。
6. 看 `.git/config` 裡 `[remote "origin"]` 的 fetch refspec，理解 `origin/*` 追蹤 branch 怎麼來的。

## 本章重點整理

- remote 是「別份 repo 的地址簿短名」；`origin` 只是 clone 時的預設名字，不特殊。
- fork 協作慣例：`origin` = 你的 fork（push 到這）、`upstream` = 原專案（從這拉更新）。
- **`git pull` = `git fetch` + `git merge`（或 rebase）**；fetch 只更新追蹤 branch（安全、可預測），pull 自動改你的本地 branch。
- 老手常 fetch 後「先看再決定」，掌控整合節奏，避免 pull 的意外 merge/衝突。
- refspec（`來源:目標`）是 push/fetch 的底層規則，解釋了同名 branch、刪遠端 branch、追蹤 branch 更新等行為。
- 開源貢獻骨架：origin=fork、upstream=原專案、從 upstream 同步、push 到 origin、發 PR。

## 自我檢核

- [ ] `origin` 是 git 關鍵字嗎？它怎麼來的？能不能改/刪/多加？
- [ ] fork 協作裡 origin 和 upstream 各指向什麼？為什麼貢獻不能直接 push upstream？
- [ ] `git fetch` 和 `git pull` 的根本差別是什麼？為什麼老手常用 fetch？
- [ ] 為什麼 `git fetch` 後 `origin/main` 會更新？（提示：refspec）
- [ ] fork 一個專案後，要拉原專案的更新，remote 怎麼設、怎麼拉？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 2.5 — Working with Remotes](https://git-scm.com/book/en/v2/Git-Basics-Working-with-Remotes)** 與 **[Ch 10.5 — The Refspec](https://git-scm.com/book/en/v2/Git-Internals-The-Refspec)**
  - **讀哪幾章**：2.5（remote 基本操作）；10.5（refspec 的完整機制，本章進階部分的權威）。
  - **和本章的關聯**：remote 與 refspec 的底層解釋。

### 官方文件

- **[GitHub Docs: Configuring a remote repository for a fork](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/configuring-a-remote-repository-for-a-fork)**
  - **讀哪裡**：加 upstream remote 的官方步驟。
  - **和本章的關聯**：fork 協作 remote 設定的權威；Ch 10/25 會再用。

### 部落格 / 文章

- **[The Difference Between git fetch and git pull](https://longair.net/blog/2009/04/16/git-fetch-and-merge/)** — Mark Longair
  - **這篇說什麼**：fetch/pull/merge 三者關係的清楚剖析。
  - **為什麼值得讀**：把「pull = fetch + merge」講到底，本章最重要區別的延伸。

remote 搞懂了，接下來兩章是整合別人改動的兩種方式——先看 merge（合併），再看它的爭議對手 rebase。

→ [Ch 5 merge 的本質](./05-merge.md)
