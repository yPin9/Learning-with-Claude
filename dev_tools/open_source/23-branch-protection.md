# Ch 23 — 保護分支與規則

> **目標**：學會用 GitHub 的機制**強制**團隊的協作規矩——protected branch、required reviews、required status checks、linear history、防 force-push。這是把 Ch 1 的「main 受保護」從「口頭約定」變成「系統強制」的關鍵。學完你能設定一個「不可能不小心搞壞 main」的 repo。

> **環境**：GitHub（部分功能需要 repo admin 權限）。前置：Ch 1（為什麼保護 main）、Ch 10（PR）、Ch 14（CI）。

## 為什麼要「強制」而非「約定」

Ch 1 講過協作的核心：main 受保護、改動要經過 review。但如果這只是**口頭約定**，遲早出事——有人趕時間直接 push main、有人沒等 review 就 merge、有人 force-push 蓋掉別人的東西。人會犯錯、會偷懶、會忘記。

**branch protection 把約定變成系統強制**：設定好之後，「直接 push main」「沒 review 就 merge」「CI 紅還 merge」這些事**做不到**——GitHub 直接擋下。這不是不信任團隊，是用機制保護大家（包括你自己）不犯低級錯誤。一個沒有 branch protection 的協作 repo，等於沒上鎖的金庫。

## 先建立直覺：給 main 上一道閘門

```
   沒保護的 main：              有保護的 main：
   ┌──────────┐               ┌──────────┐
   │   main   │               │   main   │
   └────▲─────┘               └────▲─────┘
        │ 任何人直接 push           │ 🚧 閘門：
        │ force-push                │  - 不能直接 push（必須走 PR）
        │ 沒 review 就進            │  - 必須 N 個 approve
   誰都能搞壞                       │  - 必須 CI 綠
                                   │  - 不能 force-push
                                   │  通過才能 merge
```

branch protection 就是給重要 branch（main、develop、release）裝一道閘門，定義「什麼條件才能改動它」。所有改動被迫走正規流程（PR → review → CI → merge），沒有後門。

## GitHub 的保護規則（核心）

在 repo Settings → Branches → Add branch protection rule（或新的 Rulesets），對 `main` 設定。最重要的幾條：

### Require a pull request before merging（必須走 PR）

```
   ☑ Require a pull request before merging
       ☑ Require approvals: [2]          ← 必須 N 個人 approve
       ☑ Dismiss stale approvals         ← 有新 commit 就讓舊 approve 失效
       ☑ Require review from Code Owners  ← 必須相關 owner 審（Ch 24）
```

這是核心——開了之後**不能直接 push main**，所有改動必須開 PR、拿到指定數量的 approve 才能 merge。`Dismiss stale approvals` 很重要：防止「approve 後又偷偷 push 別的東西」（approve 的是舊版本，新 commit 要重審）。

### Require status checks to pass（必須 CI 綠）

```
   ☑ Require status checks to pass before merging
       ☑ Require branches to be up to date before merging
       選擇必過的 checks: [test] [lint] [build]    ← 哪些 CI 必須綠
```

承 Ch 14：選定的 status check 必須綠，merge 按鈕才解鎖。`Require up to date` 表示「PR 必須先跟上 main 的最新狀態（rebase/merge main 進來）才能 merge」——確保 CI 跑的是「合進 main 後的狀態」，防 semantic conflict（Ch 5）。

### Require linear history（強制直線歷史）

```
   ☑ Require linear history
```

禁止 merge commit——只允許 squash 或 rebase merge（Ch 10）。想要 main 歷史是乾淨直線的團隊開這個。它和「只允許某些 merge 方式」配合（Settings → 只勾 squash/rebase）。

### 防 force-push 與刪除

```
   ☑ Do not allow force pushes      ← 防 force-push 改寫 main 歷史（Ch 6 黃金法則的系統強制！）
   ☑ Do not allow deletions         ← 防誤刪 main
```

這直接強制了 Ch 6 的黃金法則——**沒人能 force-push main**，從系統層杜絕「rebase 公開歷史害死隊友」。

### 其他常見

```
   ☑ Require conversation resolution   ← 所有 review comment 必須 resolve 才能 merge（Ch 13）
   ☑ Require signed commits            ← 必須簽署 commit（Ch 0）
   ☑ Include administrators            ← 連管理員也受規則約束（重要！）
```

`Include administrators` 很關鍵——預設 admin 可能繞過規則。開了它，連你自己（admin）也不能繞過，避免「我趕時間就破例一次」累積成壞習慣。

## 一個典型的 main 保護設定

對一個正經的團隊 repo，main 的保護通常是：

```
   main branch protection:
   ☑ Require PR before merging
       ☑ Require 1-2 approvals
       ☑ Dismiss stale approvals on new commits
       ☑ Require review from Code Owners（若有 CODEOWNERS）
   ☑ Require status checks (test, lint, build) + up to date
   ☑ Require conversation resolution
   ☑ Do not allow force pushes
   ☑ Do not allow deletions
   ☑ Include administrators
```

這套設定保證：**每個進 main 的改動，都經過 PR、被人審過、CI 綠、討論解決、沒人能 force-push 或繞過。** 這就是「不可能不小心搞壞 main」的 repo。

## Rulesets：較新、更強的保護機制

GitHub 較新推出 **Rulesets**（Settings → Rules → Rulesets），是 branch protection 的進化版：

- 可用 pattern 一次套用到多條 branch（`release/*`、`main`）。
- 更細的權限控制（誰能繞過、bypass list）。
- 可設 org 層級的 ruleset（一次套用到組織所有 repo）。
- 與舊的 branch protection 並存（兩者都會生效）。

新專案建議用 Rulesets（更彈性），但 branch protection 仍廣泛使用、概念相通。本章的規則在兩者都適用。

## 對貢獻者的意義

身為貢獻者（被保護規則約束的一方），你會遇到：

- **merge 按鈕灰掉**：因為某條 required check 沒滿足（CI 紅、approve 不夠、conversation 沒 resolve、PR 沒 up to date）——看 PR 上的提示，逐條滿足。
- **「This branch must be up to date」**：要先把 main 的最新狀態合/rebase 進你的 PR branch（Ch 25）。
- **不能 push main**：正常，走 PR（Ch 10）。
- **approve 被 dismiss**：你 approve 後又 push 了新 commit，舊 approve 失效，要重新請審。

理解這些是「保護規則在運作」，不是 bug——它在確保進 main 的東西都合規。

## 踩雷集錦

1. **協作 repo 沒設 branch protection**：等於沒上鎖。遲早有人直接 push main、force-push 搞砸。一定要設。
2. **沒勾 Include administrators**：admin 能繞過規則，「破例一次」累積成壞習慣，保護形同虛設。勾上。
3. **沒勾 Dismiss stale approvals**：有人 approve 後偷塞別的 commit，舊 approve 還算數——審的是舊版本。勾上。
4. **required check 名字設錯**：required status check 的名字要和 CI job 名完全對應，設錯會「永遠等一個不存在的 check」卡住所有 PR。
5. **沒勾 up to date 導致 semantic conflict**：PR 各自基於舊 main、CI 各自綠，合進去卻壞（Ch 5）。勾 up to date 讓 CI 跑「合後狀態」。
6. **保護太嚴拖慢小團隊**：兩人團隊要求 2 approve（但只有 2 人）= 卡死。規則嚴格度要配團隊規模（小團隊 1 approve 甚至 0 + CI）。
7. **誤刪/force-push 才想到沒防**：`Do not allow force pushes/deletions` 要事先設，出事後才設來不及。

## 進階：再往深一層

- **bypass list（Rulesets）**：可設「某些 app/role 能繞過特定規則」——例如 release bot 能 push tag、但人不行。精細控制。
- **org-level rulesets**：在組織層級一次對所有 repo 套規則（如「所有 repo 的 main 都要 PR + CI」），避免每個 repo 各設。
- **CODEOWNERS 整合**（Ch 24）：`Require review from Code Owners` 讓改特定檔案必須對應 owner 審——把「誰該審什麼」自動化。
- **required check 與 merge queue**：大團隊用 merge queue——PR 排隊、每個合併前自動 rebase + 跑 CI，確保 main 永遠綠（解決「多個 PR 各自綠、合進去互相衝突」）。
- **保護 tag / release**：tag protection rule 防止亂改 release tag。
- **auto-merge**：開了保護規則後，可設 PR「滿足所有條件就自動 merge」（`gh pr merge --auto`）——你不用守著等 CI/approve，達標自動合。

## 動手練習

1. 在你自己的測試 repo，對 main 設 branch protection：require PR + require 1 approval + require status check。
2. 故意試「直接 push main」——確認被擋（`! [remote rejected] ... protected branch`）。
3. 開一個 PR，故意讓 CI 紅，確認 merge 按鈕灰掉；修綠後解鎖。
4. 開 `Dismiss stale approvals`，approve 一個 PR 後再 push 新 commit，看 approve 失效。
5. 開 `Do not allow force pushes`，試 force-push main，確認被擋——體驗「黃金法則的系統強制」。
6. 看你用過的開源專案，從 PR 頁面的 merge 條件（要幾個 approve、哪些 check）推斷它的保護設定。

## 本章重點整理

- branch protection 把「main 受保護」從口頭約定變成系統強制——用機制保護大家不犯低級錯誤。
- 核心規則：require PR + N approvals（+ dismiss stale）、require status checks（+ up to date）、require linear history、do not allow force push/deletion、require conversation resolution、**include administrators**。
- `Do not allow force pushes` 是 Ch 6 黃金法則的系統強制；`up to date` 防 semantic conflict；`include administrators` 防破例。
- Rulesets 是較新、更強的版本（pattern 套多 branch、org 層級、細權限）；概念與 branch protection 相通。
- 規則嚴格度要配團隊規模（小團隊別要求湊不齊的 approve 數）。
- 貢獻者端：merge 按鈕灰掉/approve 被 dismiss/要 up to date 是規則在運作，逐條滿足。

## 自我檢核

- [ ] 為什麼「口頭約定 main 受保護」不夠、要用系統強制？
- [ ] 一個正經團隊 repo 的 main 該設哪些保護規則？
- [ ] `Include administrators` 和 `Dismiss stale approvals` 各防什麼？為什麼重要？
- [ ] `Require up to date` 防的是什麼問題（提示：Ch 5）？
- [ ] 貢獻者看到 merge 按鈕灰掉，可能是哪些原因？怎麼排查？

## 延伸閱讀

### 官方文件

- **[GitHub Docs: About protected branches](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-protected-branches/about-protected-branches)** 與 **[About rulesets](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets/about-rulesets)**
  - **讀哪裡**：各保護規則的精確行為；Rulesets 的能力。
  - **和本章的關聯**：本章每條規則的權威，設定時的參考。

### 部落格 / 文章

- **[Branch protection best practices](https://github.blog/2022-04-15-branch-protection-rules/)** 類 GitHub Blog
  - **這篇說什麼**：保護規則的實務建議與常見組合。
  - **為什麼值得讀**：把規則放進真實團隊情境。

main 受保護後，下一章解決「誰來審」的問題——CODEOWNERS：自動把對的 reviewer 指派給對的改動。

→ [Ch 24 CODEOWNERS 與審查制度](./24-codeowners.md)
