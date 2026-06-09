# Ch 17 — 貢獻前的功課

> **目標**：在動手寫 code 前該做的事——複現問題、認領 issue（避免重複）、設好開發環境、讀懂相關 codebase、跑通測試、處理 CLA/DCO。這些功課決定你的貢獻是「有備而來、一次到位」還是「亂槍打鳥、來回折騰」。

> **環境**：GitHub、git、`gh` CLI。前置：[Ch 16 找專案](./16-finding-projects.md)。

## 為什麼動手前的功課這麼重要

找到專案、選好 issue 後，新手的本能是立刻開始改 code。**這往往是錯的。** 沒做功課就動手，常見下場：

- 改到一半發現「這問題我複現不出來」——白改。
- 做完才發現「有人三天前已經在做了」——重複工。
- PR 開了才發現「忘了簽 CLA」「沒設對開發環境」「測試跑不起來」——卡住。
- 改的地方根本不對——沒讀懂 codebase，改錯位置。

動手前 30 分鐘的功課，省下事後好幾小時的折騰。這章就是那 30 分鐘該做什麼。

## 先建立直覺：像偵探一樣準備

把貢獻想成破案。動手「抓兇手」（改 code）前，偵探要：確認案件是真的（複現）、確認沒別人在查同一案（認領）、熟悉現場（讀 codebase）、備好工具（開發環境）。準備充分的偵探一擊命中；準備不足的瞎忙一場。

```
   功課清單（動手前）：
   1. 複現問題          ← 確認 bug 真的存在、我能觸發
   2. 認領 issue        ← 確認沒別人在做，避免撞車
   3. 設好開發環境       ← 能 build、能跑
   4. 讀相關 codebase    ← 找到該改的地方、理解周邊
   5. 跑通測試          ← 知道現狀（哪些綠、怎麼跑）
   6. 處理 CLA/DCO       ← 法律前置（Ch 21）
```

## 第一步：複現問題

對 bug 修復，**第一件事永遠是複現它**——在你的環境讓那個 bug 真的發生。

```bash
# 1. 設好環境（下面詳述）、跑起來
# 2. 照 issue 的複現步驟，重現那個 bug
# 3. 確認你看到和 issue 一樣的錯誤/行為
```

為什麼複現是第一步：

- **確認 bug 真的存在**：有些 issue 是使用者誤會、環境問題、或早就修好了。複現不出來就別瞎改。
- **建立「修好」的標準**：你要先看到壞的，才能驗證「改完真的修好了」。
- **理解 bug 的本質**：複現過程中你會摸到問題的觸發條件、邊界——這是修對它的前提。

如果複現不出來：在 issue 留言問清楚（缺什麼資訊？什麼版本？什麼環境？）——這本身也是貢獻（幫維護者釐清）。**別在沒複現的情況下亂改**。

## 第二步：認領 issue（避免撞車）

開源是多人同時看同一批 issue。你選了一個 good first issue，可能別人也選了。**動手前先「認領」**：

```bash
# 1. 先看這個 issue 有沒有 assignee、有沒有人留言說在做、有沒有關聯的 open PR
gh issue view 42 --repo owner/project
gh pr list --repo owner/project --search "42"   # 有沒有 PR 在處理它

# 2. 沒人做的話，留言認領
gh issue comment 42 --repo owner/project \
  --body "I'd like to work on this. Could you assign it to me?"
```

認領的禮儀：

- **先確認沒人在做**（assignee、留言、關聯 PR）——撞車浪費雙方時間。
- **禮貌請求**，別命令式（「assign me」太生硬）。
- **認領後盡快動手**——認領了卻消失，會擋住別人，維護者可能會把它釋出（有些專案有「N 天沒動就釋出」規則）。
- **小的 typo/文件修正**通常不用認領，直接 PR 即可（成本低、不太會撞）。

> 認識論誠實：不是所有專案都要求認領。有些專案明說「直接 PR 就好，不用問」；有些大專案 issue 太多，認領是必要的。看 CONTRIBUTING 和專案文化。原則：**較大的任務先認領（避免重複大量工作），小修直接做。**

## 第三步：設好開發環境

讓專案在你本機能 build、能跑、能測。這一步常比想像的麻煩（相依、版本、設定）。

```bash
# 標準流程（細節看 CONTRIBUTING / README）
gh repo fork owner/project --clone     # fork + clone（Ch 10/15）
cd project
git remote add upstream ...            # （gh fork 通常自動設）

# 照 CONTRIBUTING / README 設環境，例如：
npm install          # 或 pip install -e ".[dev]" / make setup / ...
npm run build
npm test             # 確認能跑（這也是第五步）
```

CONTRIBUTING（Ch 16）會說怎麼設環境。設不起來時：先排查（版本對嗎？相依裝了嗎？），真的卡住可在 issue/discussions 問——但先自己努力過（維護者不喜歡「我環境跑不起來，幫我」的伸手）。

## 第四步：讀懂相關 codebase

你不需要讀懂整個專案（大專案讀不完），但要**讀懂你要改的那塊周邊**：

```
   找到該改的地方：
   - 從錯誤訊息 / stack trace 順藤摸瓜（grep 錯誤字串）
   - git blame 看相關 code 的歷史（誰、為什麼這樣寫，Ch 35）
   - 看相關的測試（測試常常是最好的「這段 code 該怎麼用」文件）
   - 找類似的既有功能當範本（「加功能就模仿隔壁怎麼做的」）
```

實用技巧：

```bash
# 用錯誤訊息定位
grep -rn "the error message" src/

# 看某檔案/某行的歷史（為什麼這樣寫）
git log -p src/the-file.py
git blame src/the-file.py             # 每行誰改的、哪個 commit（Ch 35）

# 找相關測試
ls tests/ | grep relevant
```

讀 codebase 的目標：**找到正確的修改位置 + 理解周邊不要改壞別的 + 學專案的風格慣例（模仿它，別自創）**。新手常犯的錯是「改對了功能但風格/位置不符專案慣例」——讀周邊 code 就是為了融入它的風格。

## 第五步：跑通測試（知道現狀）

```bash
npm test          # 或 pytest / make test / ...（CONTRIBUTING 會說）
```

動手前跑一次測試，建立基準：

- **確認測試本來是綠的**（如果本來就紅，可能是環境問題或專案的已知問題，先釐清）。
- **知道怎麼跑測試**（你改完要跑來驗證）。
- **對 bug 修復**：理想是先寫一個「會抓到這個 bug 的測試」（現在會失敗），修好後它變綠——這叫 test-first，是高品質貢獻的標誌（也讓 reviewer 信服你真的修好了）。

## 第六步：CLA / DCO（法律前置）

有些專案要求你在貢獻前同意某種法律協議（Ch 21 深入）：

- **CLA（Contributor License Agreement）**：簽署一份協議（常透過 bot，第一次 PR 時自動要你簽），把你貢獻的某些權利授予專案。常見於公司主導的專案（Google、Meta 的開源）。
- **DCO（Developer Certificate of Origin）**：較輕量，你在每個 commit 加 `Signed-off-by`（`git commit -s`）聲明「這是我寫的、我有權貢獻」。Linux kernel 等用這個。

```bash
git commit -s -m "Fix the bug"    # -s 自動加 Signed-off-by（DCO）
```

怎麼知道要不要：CONTRIBUTING 會說，或第一次發 PR 時 bot 會擋你並提示。**先看清楚**——CLA 沒簽 PR 不會被合併，且 CLA 涉及權利轉讓，公司員工貢獻要注意（Ch 21）。

## 一個完整的「貢獻前功課」流程

```
1. 選定 issue（Ch 16）
2. gh issue view 42 → 看有沒有人在做 → 沒有就留言認領
3. gh repo fork --clone → 設開發環境（照 CONTRIBUTING）
4. 複現 bug（照 issue 步驟，確認我看到一樣的問題）
5. 讀 CONTRIBUTING → 記下 commit 規範、測試指令、CLA/DCO 要求
6. 跑測試 → 確認綠 → 找到/寫一個會抓到 bug 的測試
7. git blame / grep / 讀相關 code → 找到該改的位置、理解周邊
8. （現在才開始改 code，Ch 18）
```

做完這些功課，你動手時是「有備而來」——知道改哪、怎麼驗證、符合什麼規範。這就是高品質貢獻和亂槍打鳥的差別。

## 踩雷集錦

1. **沒複現就改 code**：可能 bug 根本不存在/已修好/是誤會。第一步永遠複現。
2. **沒認領就做大任務，撞車**：別人也在做，重複工。大任務先確認沒人做 + 認領。
3. **認領後消失**：擋住別人。認領了就盡快動手，做不了就說一聲釋出。
4. **環境跑不起來就伸手要人幫**：先自己排查（版本/相依/CONTRIBUTING）。真卡住再問，且要說清楚你試過什麼。
5. **不讀周邊 code 就改**：改對功能但風格/位置不符慣例，被 review 退。模仿專案既有風格。
6. **忘了 CLA/DCO**：PR 開了被 bot 擋。先看 CONTRIBUTING，DCO 用 `git commit -s`。
7. **不跑測試就改**：不知道現狀、改完無法驗證。先跑一次建立基準。

## 進階：再往深一層

- **test-first 貢獻**：先寫一個會失敗的測試重現 bug，再修。這讓 reviewer 一眼信服「真的修好了」，是高品質貢獻的標誌。
- **git blame / log 考古**（Ch 35）：理解「這段 code 為什麼這樣寫」——可能有你沒看到的原因，避免「修了一個 bug 引入另一個」。
- **找相關 PR/issue 的歷史**：你要改的東西，可能之前有人嘗試過（被拒？為什麼？）。搜尋既有 PR 避免重蹈覆轍。
- **大專案的開發者文件**：除了 CONTRIBUTING，大專案常有 `docs/development/` 詳細指南（架構、慣例、如何加某類功能）。
- **CLA 的公司問題**（Ch 21）：你以員工身分貢獻，CLA 可能要公司簽（不是你個人）。涉及智財，公司專案要先確認政策。
- **maintainer 的時間經濟學**：你的功課做得越足，維護者花的時間越少、越願意合併。把「降低維護者的負擔」當目標。

## 動手練習

1. 對你練習 D 選定的 issue，先複現它（設環境、照步驟、確認看到一樣的問題）。複現不出來就在 issue 問。
2. 確認那個 issue 沒人在做（assignee、留言、關聯 PR），禮貌留言認領。
3. fork + clone + 設好開發環境，確認能 build、能跑測試（綠）。
4. 讀那個專案的 CONTRIBUTING，列出：commit 規範、測試指令、要不要 CLA/DCO。
5. 用 `git blame` / `grep` 找到你要改的 code 位置，讀它周邊 + 相關測試，理解該怎麼改、符合什麼風格。
6. （對 bug）試著先寫一個「會抓到這個 bug 的測試」（現在會失敗）——體驗 test-first。

## 本章重點整理

- 動手前的功課決定貢獻品質：有備而來 vs 亂槍打鳥。30 分鐘功課省下事後數小時折騰。
- 六步功課：複現問題 → 認領 issue（避免撞車）→ 設開發環境 → 讀相關 codebase → 跑通測試 → 處理 CLA/DCO。
- **複現是第一步**（確認 bug 存在、建立修好的標準）；複現不出來就問，別瞎改。
- 大任務先認領（看 assignee/留言/PR、禮貌請求、認領後盡快動手）；小修直接做。
- 讀周邊 code 是為了找對位置 + 不改壞別的 + 融入專案風格（模仿，別自創）。
- CLA（權利協議，常 bot 處理）/ DCO（`git commit -s` 加 Signed-off-by）依專案要求，CONTRIBUTING 會說。

## 自我檢核

- [ ] 為什麼修 bug 的第一步是「複現」而不是「改 code」？
- [ ] 動手前怎麼避免和別人撞車？什麼任務需要認領、什麼不用？
- [ ] 為什麼要讀「周邊 code」而不只是改你要改的那行？
- [ ] test-first 貢獻是什麼？為什麼它讓 reviewer 信服？
- [ ] CLA 和 DCO 各是什麼？怎麼知道專案要不要、怎麼處理？

## 延伸閱讀

### 官方指南

- **[GitHub Open Source Guides: How to Contribute — Before you contribute / Communicating effectively](https://opensource.guide/how-to-contribute/#how-to-submit-a-contribution)** — GitHub
  - **讀哪裡**："Before you contribute" 與 "Opening a pull request" 之間的準備建議。
  - **和本章的關聯**：貢獻前準備的官方建議。

### 部落格 / 文章

- **[The first steps to contributing to open source](https://github.blog/2020-06-30-how-to-contribute-to-open-source-effectively/)** 類 GitHub Blog 文章
  - **這篇說什麼**：有效貢獻的準備工作（複現、溝通、認領）。
  - **為什麼值得讀**：把「功課」放進真實貢獻情境。

### 工具

- **[Developer Certificate of Origin](https://developercertificate.org/)** — 官方文本
  - **讀哪裡**：DCO 的完整文本（很短）。
  - **和本章的關聯**：理解 `git commit -s` 到底簽了什麼（Ch 21 深入）。

功課做足，下一章正式動手——你的第一個 PR：怎麼把改動做到「一次到位」，從最小、最安全的開始。

→ [Ch 18 你的第一個 PR](./18-your-first-pr.md)
