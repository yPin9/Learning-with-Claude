# Ch 15 — gh CLI 與自動化

> **目標**：把 Part 3 學的平台操作（PR、issue、review、CI）命令列化。掌握 `gh` CLI——開/管 PR、checkout 別人的 PR、看/開 issue、做 review、查 CI、操作 repo，以及用它寫自動化腳本。學完你能不離開 terminal 完成大部分協作，速度翻倍。

> **環境**：`gh` CLI 2.x（[Ch 0](./00-environment-and-accounts.md) 已裝並 `gh auth login`）。

## 為什麼用命令列做協作

到目前 PR/issue/review 你多半在瀏覽器點。瀏覽器直觀，但有幾個問題：協作流程裡你常常要在「terminal（git 操作）」和「瀏覽器（PR 操作）」之間切換，很碎；重複動作（開 PR、看 CI、checkout PR）點來點去慢；而且瀏覽器操作無法寫進腳本自動化。

`gh` 是 GitHub 官方 CLI，把平台操作搬進 terminal——你在同一個地方做 git + GitHub 的所有事。對重度協作者，`gh` 是效率倍增器；對自動化，它是把協作流程腳本化的關鍵。

## 先建立直覺：git 管 commit，gh 管平台

```
   git                      gh
   ┌──────────────┐         ┌──────────────────────┐
   │ commit       │         │ pr（PR）             │
   │ branch       │         │ issue                │
   │ push/pull    │         │ review               │
   │ merge/rebase │         │ repo                 │
   │ ...          │         │ run（CI）            │
   │              │         │ release / gist / ... │
   │ 本地版本控制  │         │ GitHub 平台功能       │
   └──────────────┘         └──────────────────────┘
```

`git` 處理版本控制（commit/branch/merge），`gh` 處理 GitHub 平台的東西（PR/issue/review）——它們互補。`gh` 底下還是用 GitHub API，所以它能做的就是平台能做的。

## PR 操作：gh pr

最常用的一組。承 Ch 10/11/13：

```bash
# 開 PR（Ch 11）
gh pr create                          # 互動式，問你 title/body/base
gh pr create --fill                   # 用 commit message 自動填 title/body
gh pr create --title "..." --body "..." --base main --draft

# 看 PR
gh pr list                            # 列出 repo 的 open PR
gh pr list --author @me               # 我開的 PR
gh pr status                          # 我相關的 PR（我開的、要我審的）
gh pr view 123                        # 看 PR #123 詳情
gh pr view 123 --web                  # 在瀏覽器打開（需要 UI 時）
gh pr diff 123                        # 看 PR 的 diff

# checkout 別人的 PR 到本地（超實用！）
gh pr checkout 123                    # 把 PR #123 拉到本地 branch 測試/review

# 管理自己的 PR
gh pr checks                          # 看當前 PR 的 CI 狀態（Ch 14）
gh pr ready                           # draft → ready（Ch 11）
gh pr merge 123                       # merge（--squash / --rebase / --merge）
```

**`gh pr checkout 123` 是殺手級功能**：把任何人的 PR（含跨 fork 的）拉到你本地 branch，讓你實際跑、測、review——不用手動加 remote、fetch、checkout（Ch 4 那一套它自動做）。審 PR（Ch 29）時你會天天用。

## issue 操作：gh issue

承 Ch 12：

```bash
gh issue list                         # 列出 open issue
gh issue list --label "good first issue"   # 找新手任務（Ch 16）
gh issue view 456                     # 看 issue #456
gh issue create                       # 開 issue（互動式，帶出範本）
gh issue create --title "..." --body "..." --label bug
gh issue comment 456 --body "I'd like to work on this"   # 認領 issue（Ch 17）
gh issue status                       # 我相關的 issue
```

`gh issue list --label "good first issue"` 直接在命令列找一個專案的新手任務——Ch 16 找專案時很順。

## review 操作：gh pr review

承 Ch 13（被審）/ Ch 29（審人）：

```bash
gh pr review 123 --approve            # 批准
gh pr review 123 --request-changes --body "..."   # 要求修改
gh pr review 123 --comment --body "..."           # 純留言
```

配合 `gh pr checkout` 在本地測試後直接 review——「拉下來跑 → 滿意 → approve」全在 terminal。

## CI 操作：gh run / gh pr checks

承 Ch 14：

```bash
gh pr checks                          # 當前 PR 的所有 check 狀態
gh pr checks --watch                  # 持續監看直到跑完
gh run list                           # 最近的 workflow run
gh run view --log-failed              # 看失敗的 log（debug CI 利器）
gh run rerun <run-id>                 # 重跑（flaky test 時）
gh run watch                          # 即時看 run 進度
```

## repo 操作：gh repo

```bash
gh repo fork orig/project --clone     # fork + clone + 自動設 upstream（Ch 4/10 一鍵搞定！）
gh repo clone you/project
gh repo view orig/project             # 看 repo 資訊（README、stats）
gh repo sync                          # 同步你的 fork 跟上 upstream（Ch 25）
gh repo create my-new-project --public
```

**`gh repo fork --clone` 一鍵完成 Ch 10 的整套 fork 流程**——fork、clone 你的 fork、自動加 upstream remote。省掉手動三步。

## 一個完整的「用 gh 貢獻」流程

把 Part 3 全部串成命令列：

```bash
# 1. fork + clone + 設 upstream（一鍵，取代 Ch 10 手動流程）
gh repo fork orig/project --clone
cd project

# 2. 找個 good first issue 來做
gh issue list --label "good first issue"
gh issue view 42                      # 看細節
gh issue comment 42 --body "I'd like to take this"   # 認領

# 3. 開 branch、改、commit（git）
git switch -c fix/issue-42
# ...改、commit...
git push -u origin fix/issue-42

# 4. 開 PR（Ch 11）
gh pr create --fill --base main       # 或帶完整 title/body

# 5. 監看 CI（Ch 14）
gh pr checks --watch

# 6. 處理 review（Ch 13）
gh pr view --comments                 # 看 reviewer 的意見
# ...改、push...
# re-request review 在 web 或 gh api

# 7. 合併後清理
gh pr view                            # 確認 merged
git switch main && gh repo sync       # 同步 fork
```

整個貢獻循環不開瀏覽器（除了偶爾需要 `--web` 看 UI）。熟練後比點瀏覽器快得多。

## 自動化：gh 寫腳本

`gh` 真正的威力在自動化——它能輸出 JSON，接其他工具：

```bash
# 列出所有 open PR 的標題與作者（JSON + jq）
gh pr list --json number,title,author --jq '.[] | "\(.number): \(.title) by \(.author.login)"'

# 找出所有 CI 紅掉的 PR
gh pr list --json number,statusCheckRollup --jq '...'

# 批次：對所有標 stale 的 issue 留言（維護者自動化，Ch 30）
for n in $(gh issue list --label stale --json number --jq '.[].number'); do
  gh issue comment $n --body "This issue will be closed in 7 days if no activity."
done

# gh api：直接打 GitHub API（gh 沒包裝的功能）
gh api repos/orig/project/pulls --jq '.[].title'
gh api graphql -f query='...'         # 連 GraphQL API 都能打
```

`gh api` 是底牌——任何 `gh` 子命令沒涵蓋的 GitHub API 功能，都能用 `gh api` 直接打（它自動帶上你的認證）。維護者的自動化（自動 triage、自動 label、release 自動化）大量用這個（Ch 30/32）。

## 踩雷集錦

1. **沒 `gh auth login` 就用**：gh 需要認證（Ch 0）。`gh auth status` 確認。
2. **在錯的 repo 目錄跑 gh**：`gh` 預設操作「當前目錄的 repo」。在非 repo 目錄或錯的 repo 跑會找錯對象。用 `--repo owner/name` 明確指定。
3. **`gh pr create` 不知道往哪開**：fork-based 時要確認 base 是原專案（`--repo orig/project`），head 是你的 fork branch。`gh` 通常自動偵測，但跨 fork 偶爾要明確指定。
4. **以為 gh 取代 git**：不是。gh 管平台（PR/issue），git 管版本控制（commit/branch）。兩者並用。
5. **`gh pr merge` 在沒權限的 repo**：你 merge 不了沒權限的專案的 PR（那是維護者的事）。`gh pr merge` 用在你有權限的 repo。
6. **忘了 `--web` 這個逃生口**：有些操作（複雜 review、看 UI 元素）瀏覽器還是順。`gh pr view --web` / `gh issue view --web` 隨時跳回瀏覽器。
7. **JSON 欄位名猜錯**：`--json` 的欄位名要對（`gh pr list --json` 不帶值會列出可用欄位）。

## 進階：再往深一層

- **`gh alias`**：`gh alias set prs 'pr list --author @me'` 自訂縮寫，常用操作一個詞搞定。
- **`gh pr checkout` 跨 fork**：它自動處理「別人的 fork 的 branch」——加 remote、fetch、建本地 branch。審外部 PR 必備（Ch 29）。
- **`gh extension`**：gh 有擴充生態（`gh extension install ...`），加更多功能（如 `gh dash` 儀表板）。
- **`gh api` + GraphQL**：REST 做不到的批次查詢用 GraphQL（一次拿多層資料），維護者大型自動化用。
- **在 CI 裡用 gh**：GitHub Actions 內建 `GITHUB_TOKEN`，`gh` 在 workflow 裡可直接用——自動開 PR、留言、發 release（Ch 32）。
- **`gh` 與 GitHub Enterprise**：`gh auth login --hostname your-ghe.com` 連企業版。
- **`gh codespace` / `gh pr create --web`**：更多周邊功能，按需探索。

## 動手練習

1. `gh auth status` 確認登入；`gh repo view <某專案>` 看一個 repo 的資訊。
2. `gh repo fork <某小專案> --clone`，確認它一鍵完成 fork + clone + upstream（對比 Ch 10 手動）。
3. `gh issue list --label "good first issue"` 找一個專案的新手任務（為 Ch 16 鋪路）。
4. 在你的測試 repo：`gh pr create --fill` 開 PR、`gh pr checks` 看 CI、`gh pr view` 看詳情、`gh pr merge --squash` 合併——全命令列走一次。
5. `gh pr checkout <某 PR 號>`（找一個開源專案的 open PR），把它拉到本地，跑跑看——體驗 review 別人 PR 的起手式。
6. 玩 `gh pr list --json number,title --jq '.[]'`，再試一個 `gh api repos/{owner}/{repo}` 直接打 API。

## 本章重點整理

- `gh` 是 GitHub 官方 CLI，把平台操作（PR/issue/review/CI/repo）搬進 terminal；git 管版本控制，gh 管平台，互補。
- 核心：`gh pr create/list/view/checkout/checks/merge`、`gh issue list/view/create/comment`、`gh pr review`、`gh run view --log-failed`。
- **`gh repo fork --clone`** 一鍵完成 Ch 10 的 fork 流程；**`gh pr checkout`** 一鍵把任何 PR 拉到本地測試（審 PR 神器）。
- `gh` 輸出 JSON（`--json` + `--jq`）可接其他工具寫自動化；`gh api` 打任何 GitHub API（含 GraphQL）。
- `--web` 是逃生口，隨時跳回瀏覽器處理需要 UI 的操作。

## 自我檢核

- [ ] gh 和 git 的分工是什麼？各管什麼？
- [ ] `gh repo fork --clone` 取代了 Ch 10 的哪些手動步驟？
- [ ] `gh pr checkout` 為什麼是審別人 PR 的神器？它自動做了什麼？
- [ ] 怎麼用 gh 在命令列看 PR 的 CI 失敗 log？
- [ ] 想做 gh 沒直接包裝的 GitHub 操作（寫自動化），用什麼？

## 延伸閱讀

### 工具文件

- **[gh CLI Manual](https://cli.github.com/manual/)**
  - **讀哪裡**：gh pr、gh issue、gh repo、gh run、gh api 各章。
  - **和本章的關聯**：本章所有指令的權威參考，當速查。

### 部落格 / 文章

- **[Scripting with GitHub CLI](https://github.blog/2021-09-23-scripting-with-github-cli/)** — GitHub Blog
  - **這篇說什麼**：用 `gh` + `--json`/`--jq`/`gh api` 寫自動化腳本。
  - **為什麼值得讀**：本章「自動化」段的延伸，維護者自動化（Ch 30/32）的基礎。

Part 3 的平台操作都齊了。用練習 C 把整個 Part（fork→branch→PR→CI→review→merge）在自建 repo 完整跑一次——這是你發真實 PR（練習 D）前的全流程演練。

→ [練習 C：完整跑一次貢獻循環](./practice-c-full-pr-cycle.md)
