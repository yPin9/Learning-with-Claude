# 開源協作學習筆記：從會 commit 到能跟全世界一起寫程式

> 給已經會 `commit` / `push` / `pull` / `clone`、但沒跟別人協作過、對開源貢獻沒頭緒的工程師。

你會基本 git，但「一個人寫」和「一群人一起寫」是兩回事。這門課把你從單機 git 使用者，帶到能自信地：對任何開源專案發 Pull Request、在 code review 裡跟維護者來回、加入團隊用分支策略協作、甚至經營自己的開源專案。從補齊協作必備的中階 git（branch / rebase / 衝突解決）開始，一路到當維護者審別人的 PR。

以 git 2.40+、GitHub、`gh` CLI 為主要環境。**練習包含對真實專案發出你的第一個 PR**。

學完你應該能：

- 熟練 branch / merge / rebase / interactive rebase / 衝突解決 / cherry-pick / reflog——協作必備的中階 git
- 完整跑通 fork → branch → PR → CI → review → merge 的貢獻循環
- 對真實開源專案發出有意義的 Pull Request，並在 review 中迭代到被合併
- 懂開源的軟實力：找專案、跟維護者溝通、開源禮儀、授權與法律
- 用團隊協作模式（GitHub Flow / trunk-based）、保護分支、CODEOWNERS、規範自動化
- 切換到維護者視角：審 PR、issue triage、release 管理、經營社群、安全揭露

## 為什麼學這個？

- **協作是工程師的日常**：你幾乎不會再「一個人寫一個專案」。會不會跟人協作，決定你能不能在團隊裡有效工作。
- **開源是最好的練功場與履歷**：對知名專案的 merged PR，比任何 side project 都有說服力。它逼你寫出別人看得懂、審得過的程式碼。
- **基本 git ≠ 會協作**：`commit`/`push` 只是單機操作。真正的協作在 branch 策略、衝突解決、code review、PR 迭代——這些沒人教你就會卡住。
- **理解流程才不會踩雷**：搞懂 rebase 的 golden rule、force-push 的時機、PR 跟 git 的關係，你才不會在團隊裡闖禍（覆蓋別人的 commit、推爆主線）。
- **雙視角讓你更強**：懂維護者怎麼想，你的 PR 才會好審、好被接受；會貢獻，你才知道怎麼經營一個歡迎貢獻的專案。

## 先修知識

- 基本 git（程度：會 `init`/`clone`/`add`/`commit`/`push`/`pull`，知道 commit 大致是什麼）
- 命令列操作（程度：能在 terminal 跑指令、編輯檔案）
- 任一程式語言能讀寫（程度：看得懂別人的 code、改得動——貢獻需要）
- 沒有也沒關係的：git 底層原理（object/DAG，本課需要時會補，深挖見 [dev_tools/git](../git/README.md)）、CI/CD（[dev_tools/cicd](../cicd/README.md) 有深入）

## 課程地圖

### Part 1 — 協作的心智模型（Ch 0–3）
- [Ch 0 環境與帳號設定](./00-environment-and-accounts.md)
- [Ch 1 為什麼需要協作流程](./01-why-collaboration-workflow.md)
- [Ch 2 commit 是溝通](./02-commit-as-communication.md)
- [Ch 3 branch 是協作的單位](./03-branches-as-units.md)
- [練習 A：把雜亂開發整理成乾淨歷史](./practice-a-clean-history.md)

### Part 2 — 協作必備的中階 Git（Ch 4–9）
- [Ch 4 remote 深入](./04-remotes-deep-dive.md)
- [Ch 5 merge 的本質](./05-merge.md)
- [Ch 6 rebase 與它的爭議](./06-rebase.md)
- [Ch 7 interactive rebase](./07-interactive-rebase.md)
- [Ch 8 衝突解決](./08-conflict-resolution.md)
- [Ch 9 cherry-pick / stash / reflog](./09-cherrypick-stash-reflog.md)
- [練習 B：複雜 rebase 衝突 + reflog 救援](./practice-b-rebase-reflog.md)

### Part 3 — GitHub 平台（Ch 10–15）
- [Ch 10 Fork 與 PR 的本質](./10-fork-and-pr.md)
- [Ch 11 開一個好的 Pull Request](./11-good-pull-request.md)
- [Ch 12 Issue 與專案管理](./12-issues-and-project-management.md)
- [Ch 13 Code Review（被審方）](./13-code-review-as-author.md)
- [Ch 14 GitHub Actions / CI](./14-github-actions-ci.md)
- [Ch 15 gh CLI 與自動化](./15-gh-cli.md)
- [練習 C：完整跑一次貢獻循環](./practice-c-full-pr-cycle.md)

### Part 4 — 貢獻開源（Contributor 視角）（Ch 16–21）
- [Ch 16 找到適合貢獻的專案](./16-finding-projects.md)
- [Ch 17 貢獻前的功課](./17-before-contributing.md)
- [Ch 18 你的第一個 PR](./18-your-first-pr.md)
- [Ch 19 在 review 中迭代](./19-iterating-in-review.md)
- [Ch 20 開源溝通與禮儀](./20-communication-etiquette.md)
- [Ch 21 授權與法律基礎](./21-licensing-and-legal.md)
- [練習 D：對真實專案發出第一個真 PR](./practice-d-first-real-pr.md)

### Part 5 — 團隊協作模式（Ch 22–27）
- [Ch 22 branching model](./22-branching-models.md)
- [Ch 23 保護分支與規則](./23-branch-protection.md)
- [Ch 24 CODEOWNERS 與審查制度](./24-codeowners.md)
- [Ch 25 同步上游與長命 branch](./25-syncing-upstream.md)
- [Ch 26 PR 拆分與 stacked PR](./26-splitting-prs.md)
- [Ch 27 commit/PR 規範自動化](./27-conventions-automation.md)
- [練習 E：設定完整團隊協作規則](./practice-e-team-rules.md)

### Part 6 — 維護者視角（Maintainer）（Ch 28–34）
- [Ch 28 從貢獻者到維護者](./28-becoming-maintainer.md)
- [Ch 29 審 PR（審人方）](./29-reviewing-prs.md)
- [Ch 30 Issue triage](./30-issue-triage.md)
- [Ch 31 經營社群](./31-community.md)
- [Ch 32 release 管理](./32-release-management.md)
- [Ch 33 專案基礎建設](./33-project-infrastructure.md)
- [Ch 34 安全與責任揭露](./34-security-disclosure.md)
- [練習 F：打造一個準備好接受貢獻的專案](./practice-f-contribution-ready.md)

### Part 7 — 進階與整合（Ch 35–37）
- [Ch 35 進階 git 協作場景](./35-advanced-git-scenarios.md)
- [Ch 36 疑難雜症排解](./36-troubleshooting.md)
- [Ch 37 開源生涯](./37-open-source-career.md)
- [Final Project：完整開源貢獻循環](./final-project-real-contribution.md)

## 學習方式建議

1. **每章都開一個 terminal + 一個練習 repo**：協作是動詞，光讀沒用。git 操作每個親手打過，PR 流程每步親手點過。
2. **故意製造衝突**：開兩個 branch 改同一行，故意撞在一起，練習解衝突——這比看十篇文章有用。
3. **真的去發 PR**：練習 D 和 Final 不是模擬。對真實專案發 PR 會逼出你所有沒注意到的細節（CI、CONTRIBUTING、review 禮儀）。從 typo 級開始沒關係。
4. **兩個視角都做**：自己當維護者審一次別人（或自己分身）的 PR，你會立刻懂「為什麼我的 PR 被嫌」。

## 建議環境

- git 2.40+（`git --version`；舊版多數可用，少數新指令如 `git switch`/`restore` 需 2.23+）
- GitHub 帳號（本課以 GitHub 為主；GitLab/Gitea 概念相通，差異會標注）
- `gh` CLI（GitHub 官方命令列工具，Ch 15 起大量使用）
- 一個你能自由實驗的 GitHub 帳號（練習會建立/刪除 repo、發 PR）
- 任一你熟悉的程式語言環境（貢獻時要改 code、跑測試）

Ch 0 會一次把環境與帳號弄好。

## 精選資料庫

整門課最值得反覆參照的資源。每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **[Pro Git](https://git-scm.com/book/zh-tw/v2)** — Scott Chacon, Ben Straub（免費線上書，有繁中）
  - git 的權威教材；本課中階 git（Part 2）的底層解釋以它為準。第 3 章（分支）、第 5 章（分散式 git）、第 7 章（進階）最相關。
- **[GitHub Docs](https://docs.github.com)**
  - GitHub 平台功能的權威來源；PR、Actions、protected branch、CODEOWNERS 等以官方文件為最終仲裁。

### 推薦部落格 / 文章

- **[How to Contribute to Open Source](https://opensource.guide/how-to-contribute/)** — GitHub Open Source Guides
  - 開源貢獻的官方指南，軟實力（找專案、溝通、禮儀）寫得最完整；Part 4 的主要參考。
- **[A successful Git branching model](https://nvie.com/posts/a-successful-git-branching-model/)** — Vincent Driessen
  - Git Flow 的原始提案；Ch 22 branching model 的對照起點（含作者多年後的補充說明）。

### 推薦工具文件

- **[gh CLI manual](https://cli.github.com/manual/)**
  - GitHub CLI 的完整指令參考；Ch 15 與之後大量使用。

### 讀完本課之後

- **[Producing Open Source Software](https://producingoss.com/)** — Karl Fogel（免費線上書）
  - 經營開源專案的聖經，把維護者視角（Part 6）推得更深——社群、治理、永續。
- **[dev_tools/git](../git/README.md)**（本 repo）— 想把 git 工具本身吃更深（hooks、worktree、底層 object/DAG）。
