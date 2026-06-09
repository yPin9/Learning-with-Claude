# Ch 3 — branch 是協作的單位

> **目標**：把 branch 從「一個我聽過但沒在用的東西」變成你協作的基本動作。理解 branch 到底是什麼（便宜到驚人）、本地 branch vs 遠端 branch、tracking branch、以及為什麼「一個工作項目一條 branch」是協作的基石。

> **環境**：git 2.40+。

## 為什麼 branch 是協作的核心

Ch 1 講過第四個核心問題：未完成的工作不能丟進 main，又要存檔。**branch 是答案。** 在協作裡，幾乎每件事都從「開一條 branch」開始——修 bug 開一條、加功能開一條、貢獻別人的專案也開一條。不會用 branch，等於不會協作。

單機時你可能從沒開過 branch（一直在 main 上 commit）。這章把 branch 講透——它比你想像的簡單、便宜、安全得多。

## 先建立直覺：branch 是一張便利貼

很多人以為 branch 是「複製一整份程式碼出來」——所以覺得它很重、很可怕。**完全不是。**

git 的 branch 本質就是**一張貼在某個 commit 上的便利貼（一個指標）**。

```
   commit 歷史（每個 commit 指向它的 parent）

   A ◄─── B ◄─── C ◄─── D
                        ▲
                     [main]   ← main 只是一張貼在 D 上的便利貼

   你 git switch -c feature 開新 branch：

   A ◄─── B ◄─── C ◄─── D
                        ▲
                  [main][feature]   ← 又一張便利貼，也貼在 D

   你在 feature 上 commit E：

   A ◄─── B ◄─── C ◄─── D ◄─── E
                        ▲       ▲
                     [main]  [feature]   ← feature 移到 E，main 沒動
```

開一個 branch = 多貼一張便利貼，**幾乎零成本**（不複製任何檔案）。你在 branch 上 commit，那張便利貼就跟著往前移。**main 完全不受影響**——它還貼在原處。

這就是為什麼 branch 是「隔離區」：你在 feature 上怎麼搞，main 都安全。做壞了？刪掉 feature 這張便利貼，commit E 沒人指向，自然被回收，main 毫髮無傷。

> 對比舊工具：在 SVN 等集中式系統，branch 真的是「複製目錄」，又慢又重，大家避之唯恐不及。git 的 branch 是指標，便宜到「沒事就該開一條」。這個設計差異，徹底改變了協作習慣——git 鼓勵你大量開 branch。

## branch 的基本操作

```bash
# 看現在有哪些 branch（* 標示你在哪條）
git branch

# 開一條新 branch 並切過去（現代語法，git 2.23+）
git switch -c feature-login
# 舊語法（仍通用）：git checkout -b feature-login

# 切換 branch
git switch main
git switch feature-login

# 改名 / 刪除
git branch -m old-name new-name
git branch -d feature-login        # -d 安全刪（沒合併會擋）；-D 強制刪
```

> `switch`/`restore` vs `checkout`：`checkout` 是老瑞士刀，一個指令做太多事（切 branch、還原檔案、detach HEAD），容易誤用。git 2.23 拆成 `switch`（切 branch）和 `restore`（還原檔案），語意清楚。本課用 `switch`/`restore`，但 `checkout` 你還是會在舊文件/別人的指令裡看到。

## 一個工作項目一條 branch

協作的黃金準則：**每個獨立的工作項目，開一條獨立的 branch。**

```bash
git switch main
git switch -c fix-login-crash       # 修這個 bug 專用
# ...改 code, commit...

git switch main
git switch -c add-dark-mode         # 加這個功能專用（從乾淨的 main 開）
```

為什麼要分開：

- **互不干擾**：dark mode 還沒做完，不影響你隨時切回去發 login 修復的 PR。
- **PR 聚焦**：一條 branch 對一個 PR（Ch 10）。reviewer 看到的是聚焦的改動，不是「順便還改了別的」。
- **可平行**：你能同時有五條 branch 在不同階段（一條在 review、一條在開發、一條等 CI），互不阻塞。

> 命名慣例：branch 名沒有 git 強制規則，但團隊常有約定，如 `fix/login-crash`、`feat/dark-mode`、`username/feature`。讀專案的 CONTRIBUTING（Ch 16）或看現有 branch 命名跟著走。

## 本地 branch vs 遠端 branch

這是新手最容易暈的地方，但對協作至關重要。**你電腦裡的 branch 和 GitHub 上的 branch 是不同的東西。**

```
   你的電腦（本地）              GitHub（遠端）
   ┌──────────────────┐        ┌──────────────────┐
   │ main             │        │ main             │
   │ feature-login    │        │ feature-login    │
   │                  │        │                  │
   │ origin/main      │◄───────┤ （遠端的鏡子）    │
   │ origin/feature.. │ fetch  │                  │
   └──────────────────┘        └──────────────────┘
        ▲ 你能改的              ▲ push 才會更新
   本地 branch（main, feature） + 遠端追蹤 branch（origin/main）
```

你的本地有三種 branch 參照：

1. **本地 branch**（`main`、`feature-login`）：你直接 commit、switch 的那些。
2. **遠端追蹤 branch**（`origin/main`、`origin/feature-login`）：**GitHub 上對應 branch 的本地快照**。它們是「唯讀的鏡子」——你不直接改它們，它們只在你 `fetch`/`pull` 時更新。
3. **遠端的 branch**：實際存在 GitHub 上的，你 `push` 才會更新它。

關鍵理解：`origin/main` **不是** GitHub 上的 main，而是「**你上次 fetch 時，GitHub 的 main 長怎樣**」的本地記錄。所以如果同事剛 push 了東西，你的 `origin/main` 還是舊的，直到你 `fetch`。

```bash
git branch -a              # 看所有 branch，含 remotes/origin/...
git fetch                  # 更新所有遠端追蹤 branch（不改你的本地 branch）
git log origin/main        # 看「上次 fetch 時」遠端 main 的歷史
```

> 為什麼分這兩層：因為 git 是分散式的（Ch 1）。你的本地是完整獨立的 repo，遠端追蹤 branch 是你對「遠端那份 repo」的認知快照。`fetch` 更新認知，`merge`/`rebase` 才整合進你的工作，`push` 才把你的工作送出去。這個分離讓你能離線工作、明確控制何時同步。

## tracking branch：把本地和遠端綁起來

當你的本地 branch 和某個遠端 branch「綁定」（upstream），git 就能：
- `git status` 告訴你「你領先/落後遠端幾個 commit」
- `git push` / `git pull` 不用指定遠端和 branch

```bash
# push 一個新本地 branch 到遠端，並建立綁定
git push -u origin feature-login
#   -u = --set-upstream，之後這條 branch 直接 git push / git pull 即可

# （若 .gitconfig 設了 push.autoSetupRemote = true，Ch 0，第一次 push 自動綁定）

# 看綁定關係
git branch -vv
#   feature-login a3f2b1 [origin/feature-login: ahead 2] ...
```

`[origin/feature-login: ahead 2]` 意思是「你的本地比遠端多 2 個 commit，該 push 了」。`behind` 則表示遠端有你沒有的（該 pull）。這個「ahead/behind」是協作中你天天看的狀態。

## 一個完整的 branch 協作流程

把這章串起來——典型的「開分支做事再送出」：

```bash
git switch main
git pull                            # 先確保 main 是最新的（Ch 4 細談 pull）
git switch -c fix/typo-in-readme    # 開專用 branch
# ...改 README, commit（Ch 2 寫好 message）...
git push -u origin fix/typo-in-readme  # push + 綁定
# → 接下來開 PR（Ch 10）
```

注意第一步 `git switch main && git pull`——**從最新的 main 開 branch**，這樣你的改動是基於最新狀態，減少之後的衝突。從舊的 main 開 branch 是新手常犯的錯（你的 branch 一開始就落後）。

## 踩雷集錦

1. **以為 branch 很重、不敢開**：branch 是便利貼（指標），零成本。協作中「沒事就開一條」是常態。
2. **一直在 main 上 commit**：單機習慣。協作中 main 通常受保護、push 會被拒、且污染主線。永遠開 branch 工作。
3. **`origin/main` 當成 GitHub 的即時狀態**：它只是「上次 fetch 時」的快照。同事剛 push 的你看不到，要先 `git fetch`。
4. **從舊的 main 開 branch**：開 branch 前忘了 `git switch main && git pull`，你的 branch 一出生就落後，之後衝突更多。
5. **一條 branch 塞多個不相關的工作**：違反「一項目一 branch」，導致 PR 雜亂、無法獨立 review/merge。
6. **`git branch -d` 刪不掉就用 `-D`**：`-d` 擋你是因為這 branch 有沒合併的 commit（保護你）。確認真的不要了才 `-D` 強刪——強刪未合併的 branch，commit 可能難找回（但 reflog 還能救，Ch 9）。
7. **push 新 branch 忘了 `-u`**：之後每次都要打完整 `git push origin branch`。用 `-u` 或設 `push.autoSetupRemote`（Ch 0）。

## 進階：再往深一層

- **HEAD 是什麼**：`HEAD` 是「你現在在哪」的指標，通常指向某個 branch（那 branch 再指向 commit）。`switch` 就是移動 HEAD。`git switch --detach <sha>` 進 detached HEAD（HEAD 直接指 commit，不在任何 branch），實驗用，commit 了沒 branch 接住容易弄丟（reflog 可救，Ch 9）。
- **branch 刪除與 commit 回收**：刪 branch 只是撕掉便利貼，commit 還在（一段時間內），被 git gc 回收前 reflog 都找得回（Ch 9）。
- **`git switch -`**：切回上一個 branch（像 shell 的 `cd -`），來回跳很方便。
- **worktree**：`git worktree` 讓你同時 checkout 多個 branch 到不同目錄，不用一直 switch——同時開發多功能時神器。深挖見 [dev_tools/git](../git/README.md)。
- **upstream 的兩種「上游」**：tracking branch 的 upstream（本地↔遠端綁定）和 fork 協作的 upstream remote（你的 fork ↔ 原專案，Ch 4/10）是不同概念，別搞混——同一個詞，兩個意思。
- **遠端 branch 的清理**：別人的 branch 被 merge 刪掉後，你本地的 `origin/xxx` 還在。`git fetch --prune`（或設 `fetch.prune=true`，Ch 0）清掉它們。

## 動手練習

1. 在一個測試 repo，`git switch -c test-branch`、做幾個 commit、`git switch main`，確認 main 上看不到那些 commit（branch 隔離）。
2. `git branch -a` 看本地與遠端追蹤 branch；`git branch -vv` 看 ahead/behind。
3. push 一個新 branch 用 `-u`，然後 `git branch -vv` 看綁定，故意多 commit 一個但不 push，看 status 顯示 "ahead 1"。
4. 開兩條 branch 各做不同的事，體會「一項目一 branch」如何讓你能隨時切換、互不干擾。
5. 做一個 commit 在某 branch、刪掉那 branch（`-D`）、再用 `git reflog`（Ch 9 預習）找回那個 commit——理解「刪 branch 不等於刪 commit」。
6. 故意從一個舊的 main 開 branch，再對比從 `git pull` 後的 main 開——觀察前者一開始就 behind。

## 本章重點整理

- branch 是一張貼在 commit 上的便利貼（指標），零成本，鼓勵大量開——和 SVN 的重量級 branch 完全不同。
- 協作黃金準則：一個工作項目一條 branch；從最新的 main 開（先 `pull`）。
- 本地 branch（你改的）≠ 遠端追蹤 branch（`origin/main`，遠端的本地快照，fetch 才更新）≠ 遠端 branch（GitHub 上的，push 才更新）。
- tracking branch 把本地↔遠端綁定，`git status` 顯示 ahead/behind，是協作天天看的狀態。
- 刪 branch 只撕便利貼，commit 還在（reflog 可救）。

## 自我檢核

- [ ] 用「便利貼」解釋 branch，並說明為什麼開 branch 幾乎零成本、為什麼 main 不受影響。
- [ ] `origin/main` 是 GitHub 上的 main 嗎？它什麼時候更新？
- [ ] 為什麼開 branch 前要先 `git switch main && git pull`？
- [ ] ahead/behind 各代表什麼？你會根據它做什麼？
- [ ] 刪掉一個有未合併 commit 的 branch，那些 commit 立刻消失了嗎？怎麼找回？

## 延伸閱讀

### 書籍

- **[Pro Git, Ch 3 — Git Branching](https://git-scm.com/book/en/v2/Git-Branching-Branches-in-a-Nutshell)**
  - **這本書的定位**：branch 的權威解釋，含「branch 是指標」的底層圖解。
  - **讀哪幾章**：3.1（Branches in a Nutshell，看 branch 怎麼是指標）、3.5（Remote Branches，本地 vs 遠端 branch 的完整版）。
  - **和本章的關聯**：本章「便利貼」直覺的底層機制版。

### 官方文件

- **[git switch / git restore 公告](https://github.blog/2019-08-16-highlights-from-git-2-23/)** — GitHub Blog
  - **這篇說什麼**：為什麼把 `checkout` 拆成 `switch`/`restore`。
  - **和本章的關聯**：理解新舊指令的關係，看別人用 `checkout` 不困惑。

### 部落格 / 文章

- **[Git Branching for Teams](https://www.atlassian.com/git/tutorials/using-branches)** — Atlassian
  - **這篇說什麼**：branch 在團隊協作的實務用法，圖解清楚。
  - **讀哪裡**：Branches in the team workflow 段；branching model 留到 Ch 22。

Part 1 的心智模型建立完了。用練習 A 把「乾淨 commit + branch」綜合起來：把一段亂糟糟的開發，整理成可以拿出去見人的歷史。

→ [練習 A：把雜亂開發整理成乾淨歷史](./practice-a-clean-history.md)
