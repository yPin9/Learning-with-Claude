# Ch 24 — CODEOWNERS 與審查制度

> **目標**：解決團隊協作的「誰來審」問題——用 CODEOWNERS 自動把對的 reviewer 指派給對的改動、設計 review 制度（誰審什麼、要幾個 approve）、理解 review 在團隊裡的責任分配。學完你能讓「對的人審到對的 code」自動發生，而不是靠人記得 @ 誰。

> **環境**：GitHub。前置：Ch 13（被審）、Ch 23（branch protection 的 require Code Owners review）。

## 為什麼需要 CODEOWNERS

團隊大了，一個問題浮現：**一個 PR 該找誰審？**

```
   小團隊（3 人）：大家都熟所有 code，隨便 @ 一個人就好。
   
   大團隊（30 人）：
   - 改了資料庫的 code → 該找懂 DB 的人審
   - 改了前端 → 該找前端的人審
   - 改了安全相關 → 該找 security team 審
   - 但發 PR 的人怎麼知道該 @ 誰？
   - 維護者怎麼確保「重要的地方一定有對的人看過」？
```

靠人記得 @ 對的 reviewer 不可靠（忘記、不知道、@ 錯人）。**CODEOWNERS 把「誰負責審哪部分 code」寫成檔案，GitHub 自動指派**——改到某檔案，自動找對應的 owner 來審。這是大型協作「對的人審對的 code」的關鍵機制。

## 先建立直覺：程式碼的「責任地圖」

CODEOWNERS 是一份「哪部分 code 歸誰負責」的地圖：

```
   專案的目錄                     負責審查的人/團隊
   ┌──────────────────────────────────────────┐
   │ /src/database/    →  @db-team               │
   │ /src/frontend/    →  @alice @bob            │
   │ /src/security/    →  @security-team         │
   │ *.md (文件)       →  @docs-team             │
   │ /                 →  @maintainers (其他都歸這)│
   └──────────────────────────────────────────┘

   PR 改了 /src/database/x.py
        │
   GitHub 自動把 @db-team 加為 reviewer（不用發 PR 的人記得）
```

當 PR 碰到某路徑的檔案，GitHub 自動把對應的 owner 加為 reviewer。配合 branch protection 的 `Require review from Code Owners`（Ch 23），就變成「**改到這部分，沒有對應 owner approve 就不能 merge**」——強制「對的人一定審過」。

## CODEOWNERS 檔案語法

放在 repo 的 `.github/CODEOWNERS`（或根目錄、`docs/`）。語法像 `.gitignore` + 負責人：

```
# .github/CODEOWNERS
# 格式：<路徑 pattern>  <負責人（@user 或 @org/team）>

# 預設：所有檔案的 owner（除非被下面更具體的覆蓋）
*                       @maintainers

# 特定目錄
/src/database/          @db-team
/src/frontend/          @alice @bob          # 多個 owner（任一 approve 即可）
/src/security/          @org/security-team    # 用 team

# 特定檔案類型
*.md                    @docs-team
*.sql                   @db-team

# 特定檔案
/Dockerfile             @devops-team
/.github/workflows/     @devops-team          # CI 設定要 devops 審

# 巢狀：更具體的覆蓋更前面的
/src/                   @backend-team
/src/frontend/          @frontend-team        # frontend 覆蓋上面的 backend
```

關鍵規則：

- **最後匹配的 pattern 生效**（不是最先）——所以一般把廣泛的 `*` 放前面、具體的放後面。
- pattern 像 gitignore（`*`、`/path/`、`*.ext`）。
- owner 可以是 `@username` 或 `@org/team`（團隊）。
- 多個 owner 用空格分隔（通常任一 approve 即滿足，依設定）。

## review 制度設計：要幾個 approve、誰能審

CODEOWNERS 解決「誰該審」，還要決定「審查的嚴格度」（配 Ch 23 的 branch protection）：

```
   review 制度的維度：
   1. 要幾個 approve？        → 1（小團隊/低風險）~ 2+（大團隊/高風險）
   2. 要不要 Code Owner 審？   → 重要部分強制 owner（Ch 23 + CODEOWNERS）
   3. 能不能 self-approve？    → 通常不行（自己審自己沒意義）
   4. stale approval 失效？    → 通常要（新 commit 重審，Ch 23）
   5. 誰有 merge 權限？        → 通常限維護者/特定 role
```

常見配置：

| 情境 | approve 數 | Code Owner | 其他 |
|---|---|---|---|
| 個人專案 | 0（自己 merge）| 無 | CI 綠即可 |
| 小團隊（3-5 人）| 1 | 可選 | dismiss stale |
| 中大團隊 | 1-2 | 重要部分強制 | dismiss stale + conversation resolution |
| 高風險（安全/金融）| 2+ | 強制 + security team | 全套嚴格規則 |

> 平衡：review 太鬆（0 approve）= 沒把關；太嚴（小團隊湊不齊 approve）= 卡死生產力。嚴格度要配團隊規模與風險。一個常見錯誤是「抄大公司的嚴格規則套到三人團隊」，結果天天卡在湊 approve。

## review 在團隊裡的責任分配

CODEOWNERS 不只是技術設定，它定義了**責任**：

- **owner 對那部分 code 負責**：他審過 = 他背書這個改動。所以 owner 要真的懂那部分、認真審（不是橡皮圖章）。
- **避免 bottleneck**：如果某部分只有一個 owner，他休假/離職 PR 就卡住（bus factor，Ch 31）。重要部分設多個 owner / 用 team。
- **owner 也要能被挑戰**：owner 不是獨裁——但他對該領域有最終把關責任。

對貢獻者：你的 PR 自動被指派 owner，理解「為什麼是這個人審我」（因為他負責這塊）。owner 審得慢時，是因為他可能負責很多部分（Ch 20 耐心）。

## 一個完整的審查制度範例

一個中型團隊 repo 的完整 review 設定（CODEOWNERS + branch protection）：

```
# .github/CODEOWNERS
*                    @org/maintainers       # 預設
/src/api/            @org/backend-team
/src/web/            @org/frontend-team
/src/auth/           @org/security-team     # 安全相關，security 必審
/infra/              @org/devops-team
/.github/            @org/devops-team       # CI/設定變更要 devops 審
*.md                 @org/docs-team
```

```
# branch protection（Ch 23）
main:
  ☑ Require PR + 1 approval
  ☑ Require review from Code Owners      ← 配合 CODEOWNERS
  ☑ Dismiss stale approvals
  ☑ Require status checks (CI)
  ☑ Require conversation resolution
  ☑ No force push / Include administrators
```

效果：改到 `/src/auth/` 的 PR，**自動**被指派 security team、且**必須**有 security team 的 approve 才能 merge——「安全相關的改動一定有安全團隊看過」變成系統保證，不靠人記得。

## 踩雷集錦

1. **CODEOWNERS pattern 順序錯**：最後匹配生效，廣泛的 `*` 要放前面、具體的放後面。順序反了會匹配錯人。
2. **owner 寫了不存在/沒權限的 user/team**：GitHub 會忽略無效的 owner（那部分變沒人審）。owner 必須是對 repo 有權限的 user/team。
3. **單一 owner 變 bottleneck**：只有一個 owner 的部分，他不在 PR 就卡住。重要部分設多 owner / team（bus factor，Ch 31）。
4. **review 規則套錯規模**：三人團隊要求 2 approve + 多 team owner = 卡死。配團隊規模。
5. **owner 當橡皮圖章**：被指派就無腦 approve 沒認真審——CODEOWNERS 的意義（對的人把關）就沒了。owner 要真的審（Ch 29）。
6. **CODEOWNERS 沒配 branch protection**：光有 CODEOWNERS 只是「自動加 reviewer」，不強制。要配 Ch 23 的 `Require review from Code Owners` 才真正「必須 owner approve」。
7. **CODEOWNERS 放錯位置**：要放 `.github/`、根目錄、或 `docs/`，放別處不生效。

## 進階：再往深一層

- **team 巢狀與權限**：`@org/team` 的成員、team 的巢狀結構影響誰收到指派。org 管理 team 來管 review 責任。
- **`Require review from Code Owners` 的細節**：只有「改到該 owner 負責的檔案」時才要求他審——沒碰到的部分不卡。
- **避免 CODEOWNERS 過細**：太細（每個檔案不同 owner）難維護、PR 動輒指派一堆人。抓對「有意義的責任邊界」（模組/領域層級）。
- **CODEOWNERS 與 monorepo**：monorepo（Ch 35）裡 CODEOWNERS 尤其重要——一個 repo 多個團隊的 code，靠它分責任。
- **auto-assign 的替代/補充**：除了 CODEOWNERS，有 round-robin 自動分配 reviewer 的 app（平均分配 review 負擔），或 `gh` 自動指派。
- **review 的文化面**（Ch 29）：制度（CODEOWNERS/規則）是骨架，review 的品質與文化（建設性、及時、不橡皮圖章）是血肉。兩者都要。

## 動手練習

1. 在測試 repo 寫一個 `.github/CODEOWNERS`：`*` 給你自己、某目錄給另一個 user/team。
2. 開一個改到那個目錄的 PR，確認對應 owner 被自動加為 reviewer。
3. 故意把廣泛 pattern 放在具體的後面，看匹配錯誤——理解「最後匹配生效」。
4. 配 branch protection 開 `Require review from Code Owners`，確認改到該目錄的 PR 必須 owner approve 才能 merge。
5. 看一個大型開源專案的 CODEOWNERS（很多公開專案有），分析它怎麼劃分責任邊界。
6. 為一個（假設的）三人小團隊 vs 三十人大團隊各設計一套 review 制度（approve 數、CODEOWNERS 細度），說明差異理由。

## 本章重點整理

- CODEOWNERS 是「程式碼責任地圖」——把「誰負責審哪部分」寫成檔案，GitHub 自動指派對應 owner。
- 語法像 gitignore + 負責人；**最後匹配的 pattern 生效**（廣泛放前、具體放後）；owner 可為 user 或 team。
- 配 branch protection 的 `Require review from Code Owners`（Ch 23）→ 「改到這部分必須對應 owner approve」變系統保證。
- review 制度維度：approve 數、是否 Code Owner、禁 self-approve、stale 失效、merge 權限——嚴格度配團隊規模與風險。
- 注意：避免單一 owner bottleneck（bus factor）、owner 別當橡皮圖章、CODEOWNERS 要配 branch protection 才強制。

## 自我檢核

- [ ] CODEOWNERS 解決什麼問題？大團隊為什麼需要它？
- [ ] CODEOWNERS 的 pattern 匹配規則是「最先」還是「最後」生效？這影響怎麼排序？
- [ ] 光有 CODEOWNERS 就會「強制 owner 審」嗎？還需要配什麼（Ch 23）？
- [ ] review 制度的嚴格度該根據什麼調整？套錯會怎樣？
- [ ] 什麼是 owner bottleneck / bus factor？怎麼避免？

## 延伸閱讀

### 官方文件

- **[GitHub Docs: About code owners](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/about-code-owners)**
  - **讀哪裡**：CODEOWNERS 語法、位置、與 branch protection 的整合。
  - **和本章的關聯**：本章語法與機制的權威。

### 部落格 / 文章

- **[Scaling code review with CODEOWNERS](https://github.blog/)** 類 GitHub Blog 文章
  - **這篇說什麼**：大型專案用 CODEOWNERS 分配 review 責任的實務。
  - **為什麼值得讀**：把責任地圖放進真實大團隊情境。

- **[Google's Code Review Developer Guide — ownership](https://google.github.io/eng-practices/review/)** — Google
  - **這篇說什麼**：Google 的 code ownership 與 review 制度（OWNERS 檔案，CODEOWNERS 的靈感來源）。
  - **為什麼值得讀**:大規模 review 制度的成熟設計。

責任分配好了，下一章解決長期協作的痛點——你的 feature branch 開久了落後 main，怎麼同步上游、怎麼維護長命 branch 不變成衝突地獄。

→ [Ch 25 同步上游與長命 branch](./25-syncing-upstream.md)
