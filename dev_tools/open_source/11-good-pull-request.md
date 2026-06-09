# Ch 11 — 開一個好的 Pull Request

> **目標**：學會開一個讓維護者「想合併」而不是「想關掉」的 PR。掌握 PR 標題與描述怎麼寫、關聯 issue、draft PR、PR 大小的藝術、PR 範本，以及維護者打開你 PR 的前 30 秒在想什麼。一個好 PR 能決定它被合併還是被無視。

> **環境**：GitHub、`gh` CLI。前置：[Ch 10](./10-fork-and-pr.md)、[Ch 2 commit 是溝通](./02-commit-as-communication.md)。

## 為什麼 PR 的「包裝」決定它的命運

殘酷的事實：**維護者的時間極度有限。** 一個熱門專案一週收到幾十個 PR，維護者掃過去，決定哪些值得花時間 review。一個沒標題、沒說明、改了 500 行、看不出在幹嘛的 PR——直接跳過，或冷處理到爛掉。

PR 不只是「程式碼的容器」，它是**你向維護者推銷你的改動的提案**。同樣的程式碼，包裝好的 PR 幾天內被合併，包裝爛的 PR 永遠躺在那。這章教你怎麼包裝——這往往比 code 本身更決定 PR 的命運。

## 先建立直覺：維護者打開 PR 的前 30 秒

想像你是維護者，打開一個陌生人的 PR。你在 30 秒內想知道：

```
   1. 這在解決什麼問題？        ← 沒說 = 我要自己猜，扣分
   2. 為什麼要這樣解？          ← 有沒有更好的方法？你考慮過嗎？
   3. 改動有多大、碰到什麼？     ← 500 行？我沒時間。50 行？看一下。
   4. 它有對應的 issue 嗎？      ← 是不是大家都同意要做這個？
   5. 測試過嗎？CI 綠嗎？        ← 還是要我幫你 debug？
```

**一個好 PR 在前 30 秒就回答完這五個問題。** 如果維護者要花力氣猜、要問你「這是要做什麼」，你已經輸了一半。整章就是教你怎麼在 PR 裡主動回答這五題。

## PR 標題：第一印象

標題出現在 PR 列表（維護者掃視的地方）。它要像好的 commit subject（Ch 2）：

```
壞：
  "Update"
  "Fix bug"
  "changes"
  "Please merge"

好：
  "Fix race condition in cache eviction (#1234)"
  "Add retry logic to API client for transient 5xx errors"
  "docs: clarify installation steps for Windows"
```

- 祈使句、具體、說清楚做了什麼。
- 夠短能在列表顯示，夠具體讓人一眼知道是什麼。
- 若專案用 Conventional Commits（Ch 27），加前綴（`fix:`/`feat:`/`docs:`）。
- **重要**：如果專案用 squash merge（Ch 10），PR 標題會變成 main 上那個 squash commit 的 message——所以標題要寫得像好的 commit subject。

## PR 描述：回答那五個問題

描述（body）是你推銷的主場。一個好的 PR 描述結構：

```markdown
## What
這個 PR 做了什麼（一兩句）。

## Why
為什麼需要這個改動。解決什麼問題、什麼場景下會遇到。
（這是維護者最想知道的——對應 Ch 2「commit 講 why」）

## How
怎麼解的（如果不顯而易見）。為什麼選這個方法而非別的。

## Testing
怎麼驗證的。跑了什麼測試、手動測了什麼、邊界情況。

Closes #1234
```

實例：

```markdown
## What
Add exponential backoff retry to the API client.

## Why
During upstream deploys, the service returns 503 for a few seconds.
Our nightly job fails spuriously ~3 times a week because of this
(see #482 for logs). Retrying transient 5xx fixes it.

## How
Retry up to 3 times with backoff (1s/2s/4s) on 5xx only.
Deliberately NOT retrying 4xx — those are our bugs, retrying would
mask them.

## Testing
- Added unit tests for retry/backoff logic
- Manually tested against a mock server returning 503 then 200
- All existing tests pass (CI green)

Closes #482
```

注意它主動回答了那五個問題：問題（why）、方法與取捨（how，含「為什麼不 retry 4xx」）、驗證（testing）、關聯 issue（Closes #482）。維護者讀完不用問任何問題就能開始 review。

> `Closes #482` / `Fixes #482` 的魔法：在 PR 描述（或 commit）寫這個，PR 一被 merge，GitHub **自動關閉** #482 那個 issue（Ch 12）。`Refs #482` 只連結不關閉。這串起了 issue 和 PR。

## PR 範本：專案幫你列好要填的

很多專案有 **PR 範本**（`.github/PULL_REQUEST_TEMPLATE.md`）——你開 PR 時，描述欄自動帶出專案要你填的格式（checklist、必答問題）。

```markdown
<!-- .github/PULL_REQUEST_TEMPLATE.md 範例 -->
## Description
<!-- What does this PR do? -->

## Related Issue
<!-- Closes #... -->

## Checklist
- [ ] Tests added/updated
- [ ] Docs updated
- [ ] CI passes
- [ ] Follows the style guide
```

看到範本**一定要好好填**——它是維護者明確告訴你「我需要這些資訊」。跳過範本、清空它亂寫，是大忌（維護者會覺得你不尊重流程）。Ch 33 會教你**為自己的專案**寫範本。

## PR 大小：小即是美

這是新手最常犯的錯：**一個 PR 塞太多東西。**

```
   壞的 PR：
   "Refactor auth + add dark mode + fix 3 bugs + update deps"
   → 800 行改動，碰 40 個檔案
   → 維護者：我沒時間審這個。冷處理。

   好的 PR：
   "Fix null deref in login when token expired"
   → 15 行改動，1 個檔案 + 1 個測試
   → 維護者：30 秒看懂，CI 綠，合併！
```

為什麼小 PR 好：

- **好 review**：維護者能快速看懂、有信心合併。大 PR review 累、容易拖。
- **快合併**：小改動爭議少、CI 快、合併快。
- **好 revert**：出問題單獨 revert（Ch 2 的 atomic 原則放大到 PR 層級）。
- **少衝突**：改得少、合得快，較不會和別人撞。

原則：**一個 PR 做一件事**（和 atomic commit 同理，Ch 2）。重構和功能分開、不同 bug 分開、「順手改的」別塞進來。大改動要拆成多個小 PR（Ch 26 教 stacked PR）。

> 例外：有些改動天生就大（大型重構、新模組）。這時更要在描述裡引導 reviewer（「先看 X 檔，那是核心；其他是機械式的跟著改」），或拆成 series。但**預設往小拆**——大 PR 是要被質疑的，不是常態。

## Draft PR：還沒好但想先分享

開 PR 時可選 **draft（草稿）**狀態。draft PR 表示「**還在做、先別 review/merge**」：

```bash
gh pr create --draft     # 開成草稿
# 做好了在 GitHub 按 "Ready for review"，或：
gh pr ready
```

draft PR 的用途：

- **早期分享方向**：「我打算這樣做，方向對嗎？」先開 draft 問維護者，避免做錯方向白費力。
- **跑 CI**：想看 CI 結果但還沒完成。
- **stacked PR 的底層**（Ch 26）。

對大改動，**先開 draft 對齊方向再深入做**，是省力的關鍵——別悶頭做完 800 行才發現維護者根本不想要這個功能。

## 一個完整的開 PR 流程

承 Ch 10 的 fork 流程，加上好的包裝：

```bash
# branch 開好、改完、commit 整理乾淨（Ch 7）、push 到 fork
git push -u origin fix/login-crash

# 用 gh 開 PR（命令列，Ch 15 深入）
gh pr create \
  --repo orig/project \
  --base main \
  --title "Fix null deref in login when token expired" \
  --body "$(cat <<'EOF'
## What
Guard against null token in the login handler.

## Why
When a session token expires mid-request, `token.user` is null,
causing a crash (see #890 for the stack trace).

## Testing
Added a test for the expired-token path. All tests pass.

Closes #890
EOF
)"
```

或 push 後點 GitHub 給的連結，在網頁填表單（會自動帶出 PR 範本）。

## 踩雷集錦

1. **PR 標題/描述空白或敷衍**：維護者要猜你在幹嘛 = 直接被跳過。前 30 秒回答那五個問題。
2. **描述只講 what 不講 why**："Updated the function" 沒用——維護者要知道為什麼、解決什麼問題。
3. **PR 塞太多東西**：重構+功能+多個 bug 混一起 = 沒人想審。一個 PR 一件事，往小拆。
4. **清空或無視 PR 範本**：範本是維護者明說要的資訊，跳過很失禮。好好填。
5. **沒關聯 issue 就開大功能 PR**：維護者不知道大家同不同意要這個。大改動**先開 issue 討論**（Ch 17），有共識再 PR。
6. **CI 紅了就開 PR 等人幫忙**：自己先讓 CI 綠（本地先跑，Ch 14）。紅的 PR 顯得你沒測試。
7. **draft 和 ready 搞錯**：還在做就開 draft，別開 ready 然後 PR 一直在改（reviewer 會困惑「到底好了沒」）。

## 進階：再往深一層

- **PR 描述用 GFM**：GitHub Flavored Markdown——checklist（`- [ ]`）、程式碼區塊、表格、`![截圖]`（UI 改動附 before/after 截圖超加分）、`<details>` 摺疊長內容。
- **task list 追蹤進度**：描述裡的 `- [ ]` checklist 會在 PR 顯示進度條，適合多步驟 PR。
- **連結多個 issue**：`Closes #1, closes #2`（每個都要 closes 關鍵字才會各自關閉）。
- **PR 與 commit message 的關係**：squash merge 時 PR 標題+描述 → squash commit message；merge commit 時保留各 commit。依專案 merge 方式調整你的重心（Ch 10）。
- **`gh pr create --fill`**：用你的 commit message 自動填 PR 標題/描述——commit 寫得好就省事（Ch 2 的回報）。
- **PR 自我 review**：開 PR 後自己先過一遍 diff（GitHub 上看 Files changed），常能抓到忘了刪的 debug code、漏掉的東西——維護者會欣賞「乾淨」的 PR。

## 動手練習

1. 找一個你最近做的改動，練習寫一個含 What/Why/How/Testing 的完整 PR 描述。
2. 對著本章「維護者前 30 秒的五個問題」，檢查你的描述有沒有都回答到。
3. 在一個測試 repo 開一個 PR，描述用 markdown（checklist + 程式碼區塊），看它怎麼 render。
4. 開一個 draft PR、做點改動、再 `gh pr ready` 轉正——體驗 draft 流程。
5. 找三個真實開源專案的「最近被合併的 PR」，看它們的標題/描述怎麼寫——學好的範例。再找一個「躺很久沒人理的 PR」，分析它哪裡包裝不好。
6. 用 `Closes #N`（測試 repo 有 issue）開 PR，merge 後確認 issue 自動關閉。

## 本章重點整理

- PR 是「推銷你改動的提案」，包裝往往比 code 更決定命運——維護者時間有限，掃過去決定審不審。
- 好 PR 在前 30 秒回答五題：解決什麼問題、為什麼這樣解、改動多大、有無關聯 issue、測試了嗎。
- 標題像好 commit subject（具體、祈使句）；描述用 What/Why/How/Testing 結構，重點是 **why**。
- `Closes #N` 讓 PR merge 時自動關閉 issue；PR 範本是維護者明說要的資訊，好好填。
- **PR 往小拆**（一個 PR 一件事）——小 PR 好 review、快合併、少衝突。大改動先開 draft 對齊方向、或拆 series。

## 自我檢核

- [ ] 維護者打開你 PR 的前 30 秒想知道哪五件事？你的 PR 有回答嗎？
- [ ] PR 描述最重要的部分是什麼（what/why/how/testing 哪個）？為什麼？
- [ ] 為什麼小 PR 比大 PR 好？大改動該怎麼處理？
- [ ] 看到 PR 範本該怎麼做？無視它有什麼後果？
- [ ] draft PR 什麼時候用？它解決什麼問題？

## 延伸閱讀

### 官方文件 / 指南

- **[GitHub Open Source Guides: Opening a pull request](https://opensource.guide/how-to-contribute/#opening-a-pull-request)** — GitHub
  - **讀哪裡**："Opening a pull request" 那節。
  - **和本章的關聯**：開 PR 的官方建議，含禮儀面。

- **[GitHub Docs: Creating a pull request](https://docs.github.com/en/pull-requests/collaborating-with-pull-requests/proposing-changes-to-your-work-with-pull-requests/creating-a-pull-request)** 與 **[Linking a PR to an issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/linking-a-pull-request-to-an-issue)**
  - **讀哪裡**：建立 PR、draft、closing keywords。
  - **和本章的關聯**：操作面的權威，含自動關 issue 的關鍵字。

### 部落格 / 文章

- **[The (written) unwritten guide to pull requests](https://www.atlassian.com/blog/git/written-unwritten-guide-pull-requests)** — Atlassian
  - **這篇說什麼**：好 PR 的描述、大小、溝通的實務建議。
  - **為什麼值得讀**：把「怎麼讓 PR 被接受」講得很實際。

- **[How to Make Your Code Reviewer Fall in Love with You](https://mtlynch.io/code-review-love/)** — Michael Lynch
  - **這篇說什麼**：從 reviewer 角度，怎麼讓你的 PR 好審、討人喜歡。
  - **為什麼值得讀**：換位思考的經典，本章「維護者視角」的延伸。

PR 開好了，下一章談 PR 的搭檔——issue：怎麼開好的 issue、專案怎麼用 issue 組織工作。

→ [Ch 12 Issue 與專案管理](./12-issues-and-project-management.md)
