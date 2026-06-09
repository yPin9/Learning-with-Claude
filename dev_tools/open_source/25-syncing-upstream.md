# Ch 25 — 同步上游與長命 branch

> **目標**：解決長期協作的核心痛點——你的 branch / fork 落後了主線，怎麼同步而不踩雷。掌握同步 fork（fork 跟上 upstream）、維護長命 feature branch（rebase vs merge 跟上 main）、為什麼「分岔越久越痛」、以及怎麼讓長命 branch 保持可合併。

> **環境**：git 2.40+、GitHub、`gh` CLI。前置：Ch 4（remote）、Ch 5/6（merge/rebase）、Ch 8（衝突）。

## 為什麼同步是長期協作的關鍵

短期貢獻（一個 typo PR）不太需要同步——開 branch、改、merge，幾小時的事。但真實協作常常是長期的：

- 你的 fork 落後原專案幾百個 commit（你上個月 fork 的，原專案一直在更新）。
- 你的 feature branch 開了兩週，main 同時被合進幾十個 PR。
- 你維護一個長期 fork（公司基於某開源專案改）。

這些情境，**「怎麼跟上主線」決定你是輕鬆協作還是衝突地獄**。不同步，你的 branch 越來越落後、衝突越積越多、最後痛苦地大爆炸。會同步，你能持續保持 branch 可合併、衝突小而頻繁（好解）。

## 先建立直覺：分岔越久越痛

最重要的原則，承 Ch 5：**衝突來自「兩邊改同一塊」，分岔越久，兩邊改的東西越多，撞在一起的機率越高。**

```
   分岔一天：               分岔一個月：
   你改了 5 個檔案           你改了 30 個檔案
   main 改了 5 個檔案        main 改了 200 個檔案
   重疊機率小 → 衝突少        重疊機率大 → 衝突多、難解
   
   ＋ 分岔久了，你忘了當初為什麼那樣改，解衝突更難判斷
```

結論：**勤同步**。與其讓 branch 落後一個月然後痛苦地一次解一百個衝突，不如每天/每隔幾天同步一次，每次只解幾個小衝突（還記得脈絡、好解）。這是長命 branch 管理的第一原則——也呼應 Ch 22 trunk-based 的「短命 branch」哲學（branch 越短命，根本不用同步）。

## 同步 fork：fork 跟上 upstream

承 Ch 4/10：你 fork 了原專案，但 fork 是某一刻的快照，原專案會繼續更新。你的 fork 不會自動跟上。

```
   github.com/orig/project  ← 一直在更新（upstream）
        │ 你上個月 fork
        ▼
   github.com/you/project   ← 停在上個月（你的 fork，落後了）
```

同步 fork 的三種方式：

### 方式一：GitHub 網頁 / gh（最簡單）

```bash
gh repo sync                       # 同步你 fork 的當前 branch 跟上 upstream
gh repo sync you/project --branch main
# 或 GitHub 網頁 fork 頁面的 "Sync fork" 按鈕
```

### 方式二：命令列手動（理解原理）

```bash
git fetch upstream                 # 下載原專案的更新（Ch 4）
git switch main
git merge upstream/main            # 把原專案的 main 合進你本地 main
git push origin main               # 更新你 fork 的 main
```

承 Ch 4 的 remote 設定（origin=fork、upstream=原專案）。這就是「從 upstream 拉、推到 origin」的同步循環。

> 重要：同步的是 **main**（你 fork 的主線跟上原專案主線）。你的 feature branch 是另一回事（下面講）。先讓 fork 的 main 跟上，再讓 feature branch 基於最新 main。

## 維護長命 feature branch：rebase vs merge

你的 feature branch 開了一陣子，main（或 upstream/main）前進了。你要讓 branch「跟上 main」——兩種方式（承 Ch 5/6 的 merge vs rebase 之爭）：

### 方式 A：rebase（把你的 commit 搬到最新 main 上）

```bash
git switch feature
git fetch origin                   # 或 upstream
git rebase origin/main             # 把你的 commit 重放到最新 main 後面（Ch 6）
#    解可能的衝突（逐 commit，Ch 8）
git push --force-with-lease        # rebase 改寫歷史，要 force（Ch 6）
```

結果：你的 feature 看起來像「剛從最新 main 開始做的」，歷史直線、乾淨。**但要 force-push**（只能用在你個人的 PR branch，沒別人基於它，Ch 6 黃金法則）。

### 方式 B：merge（把 main 合進你的 feature）

```bash
git switch feature
git fetch origin
git merge origin/main              # 把最新 main 合進你的 feature（Ch 5）
#    解可能的衝突（一次解決）
git push                           # 不改寫歷史，不用 force
```

結果：你的 feature 有一個 merge commit（「合進了 main 的更新」），歷史有分岔。**不用 force**（沒改寫歷史），對「有別人也在這 branch 上」較安全。

### 怎麼選

| | rebase | merge |
|---|---|---|
| 歷史 | 直線、乾淨 | 有 merge commit |
| force-push | 需要（個人 branch 才安全）| 不用 |
| 衝突 | 逐 commit（可能多次）| 一次解決 |
| 適合 | 你個人的 PR branch（保持乾淨）| 有別人共用的 branch |

**常見實務**：
- **你個人的 PR branch**：用 rebase 跟上 main（保持乾淨、reviewer 看到的是基於最新狀態的改動）——這是最常見的。
- **有別人也在開發的共享 feature branch**：用 merge（rebase 會害到別人，黃金法則）。
- 看專案/團隊偏好（CONTRIBUTING）。

> 「This branch is out-of-date」：PR 上常看到 GitHub 提示你的 PR 落後 main（尤其開了 Ch 23 的 `Require up to date`）。GitHub 有個 "Update branch" 按鈕（用 merge）、或 "Update with rebase"——或你自己命令列 rebase/merge。保持 up to date 讓 CI 跑的是「合後狀態」（防 semantic conflict，Ch 5）。

## 維護長期 fork（進階情境）

最難的情境：你（或公司）維護一個**長期偏離**原專案的 fork——基於某開源專案改了很多東西，但又要持續跟上原專案的更新（安全修補、新功能）。

```
   upstream（原專案）一直前進
        │
   你的 fork 有大量自己的改動，但要定期吸收 upstream 的更新
        │
   挑戰：每次同步都可能和你的客製改動衝突
```

策略：

- **rerere 救命**（Ch 8）：長期 fork 反覆 merge upstream 會遇到「同樣的衝突」（你的客製改動 vs upstream 的演進撞在同一塊）。`rerere.enabled`（Ch 0）記住解法、自動套用——對長期 fork 是必開的。
- **定期小步同步**：別讓 fork 落後半年才一次大同步（衝突爆炸）。定期（每週/每月）merge upstream，每次衝突小。
- **把客製改動模組化**：盡量讓你的改動集中在少數檔案/用 plugin 機制，減少和 upstream 演進的重疊面（衝突來自重疊）。
- **記錄你的客製**：哪些是你改的、為什麼——同步衝突時才知道怎麼取捨（你的客製 vs upstream 的新版本）。
- **考慮貢獻回上游**（Ch 18）：能貢獻回原專案的改動就貢獻回去——進了 upstream 你就不用再 fork 維護它了（減少 fork 的偏離面）。

## 一個完整的同步工作流

長期貢獻者的日常同步（fork + feature branch）：

```bash
# 1. 定期同步 fork 的 main 跟上 upstream
git switch main
git fetch upstream
git merge upstream/main            # 或 gh repo sync
git push origin main

# 2. 讓你正在做的 feature branch 跟上最新 main
git switch feature
git rebase main                    # 個人 branch 用 rebase（保持乾淨）
#    解衝突（小而頻繁，因為勤同步）
git push --force-with-lease

# 3. 繼續開發...
# 隔幾天重複，別讓 branch 落後太多
```

關鍵節奏：**main 跟 upstream、feature 跟 main、勤做**。這讓你永遠基於最新狀態工作，衝突小、PR 隨時可合併。

## 踩雷集錦

1. **fork 開了很久不同步，落後幾百 commit**：開 branch 都基於舊 main，衝突爆炸。定期 `gh repo sync`。
2. **feature branch 落後一個月才同步**：一次解一百個衝突、忘了脈絡、難判斷。勤同步（每天/每隔幾天），小步解。
3. **rebase 共享的 feature branch**：別人也在上面工作，rebase + force-push 害死他們（Ch 6 黃金法則）。共享 branch 用 merge。
4. **同步 fork 時搞混 main 和 feature**：先讓 fork 的 main 跟上 upstream，再讓 feature 基於最新 main。別直接把 upstream merge 進 feature（會混入一堆不相關的東西）。
5. **長期 fork 不開 rerere**：反覆解同樣的衝突，浪費大量時間。長期 fork 必開 rerere（Ch 0/8）。
6. **`git pull` 在落後很多的 branch 製造大 merge commit**：`pull` 自動 merge（Ch 4）。落後很多時先 fetch 看清楚，決定 rebase 還 merge。
7. **以為 GitHub 的 "Update branch" 一定用 merge**：它預設 merge（產生 merge commit），有些專案不喜歡——確認專案偏好，必要時自己 rebase。

## 進階：再往深一層

- **`git rebase --onto`**（Ch 36）：branch 開錯 base、或要把一段 commit 搬到不同 base 時用——長命 branch 重整的進階工具。
- **`merge --no-ff` vs ff 同步**：merge 跟上 main 時，是否要 merge commit 看團隊歷史偏好（Ch 5）。
- **upstream 改寫了歷史怎麼辦**：罕見但會發生（原專案 force-push 了，違反黃金法則但有時不可避免）——你的 fork 同步會很痛，可能要 reset 對齊。
- **submodule / 相依的同步**（Ch 35）：你的專案依賴的 submodule 或 vendored 相依也要同步跟上。
- **自動化同步**：用 CI/bot 定期自動同步 fork、或自動開 PR 把 upstream 更新拉進來（如 Dependabot 對相依、自訂 action 對 fork）。
- **「stacked」branch 的同步**（Ch 26）：多個相依的 branch 疊在一起時，同步 base 會牽動整疊——`rebase --update-refs` 幫忙。

## 動手練習

1. fork 一個還在更新的開源專案，過幾天後 `gh repo sync`（或手動 fetch upstream + merge + push），看你的 fork 跟上了多少 commit。
2. 開一個 feature branch，在 main 上製造一些新 commit（模擬主線前進），分別用 rebase 和 merge 讓 feature 跟上，比較歷史差異（直線 vs merge commit）。
3. 製造一個「分岔久」的情境（feature 和 main 各改很多重疊的東西），體會衝突比「分岔短」時多——理解「勤同步」的價值。
4. 開 rerere，模擬長期 fork 反覆 merge upstream 遇到同樣衝突，看 rerere 自動套用解法。
5. 在 GitHub 上對一個落後的 PR 用 "Update branch"（merge）和 "Update with rebase"，比較結果。
6. （思考）你維護一個公司 fork（基於某開源專案大改），要持續跟上 upstream——列出你的同步策略（頻率、rebase/merge、rerere、模組化）。

## 本章重點整理

- 長期協作的核心痛點是「跟上主線」；第一原則：**勤同步**（分岔越久衝突越多越難解）。
- 同步 fork：`gh repo sync` 或手動 `fetch upstream + merge + push origin`——先讓 fork 的 main 跟上 upstream。
- 讓 feature branch 跟上 main：rebase（直線乾淨、要 force、個人 branch 用）vs merge（有 merge commit、不用 force、共享 branch 用）。
- 維護長期 fork：開 rerere（反覆衝突自動解）、定期小步同步、客製模組化、能貢獻回上游就貢獻（減少偏離）。
- 日常節奏：main 跟 upstream、feature 跟 main、勤做——永遠基於最新狀態，衝突小、PR 隨時可合併。

## 自我檢核

- [ ] 為什麼「勤同步」比「落後很久再一次同步」好？（提示：衝突與分岔的關係）
- [ ] 同步 fork 的步驟是什麼？同步的是哪條 branch？
- [ ] 讓 feature branch 跟上 main，rebase 和 merge 各適合什麼情境？哪個要 force-push？
- [ ] 維護長期 fork 為什麼一定要開 rerere？還有哪些策略？
- [ ] PR 顯示 "out of date"，為什麼要更新？不更新有什麼風險（提示：Ch 5）？

## 延伸閱讀

### 官方文件

- **[GitHub Docs: Syncing a fork](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/working-with-forks/syncing-a-fork)**
  - **讀哪裡**：網頁 Sync fork、`gh repo sync`、命令列同步。
  - **和本章的關聯**：同步 fork 的權威步驟。

### 書籍

- **[Pro Git, Ch 5.2 — Contributing to a Project (rebasing/keeping up to date)](https://git-scm.com/book/en/v2/Distributed-Git-Contributing-to-a-Project)**
  - **讀哪幾章**：5.2 關於跟上 upstream、rebase vs merge 維護貢獻 branch。
  - **和本章的關聯**：同步策略的官方說明。

### 部落格 / 文章

- **[Keeping a fork up to date / long-running forks](https://www.atlassian.com/git/tutorials/git-forks-and-upstreams)** — Atlassian
  - **這篇說什麼**：fork 與 upstream 的同步實務。
  - **為什麼值得讀**：把同步流程講得清楚，補強本章。

同步搞定了，下一章解決另一個長命 branch 的問題——你的改動太大，怎麼拆成可審的小 PR，以及 stacked PR 的進階技巧。

→ [Ch 26 PR 拆分與 stacked PR](./26-splitting-prs.md)
