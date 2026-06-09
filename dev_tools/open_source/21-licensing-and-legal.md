# Ch 21 — 授權與法律基礎

> **目標**：搞懂貢獻開源的法律面——開源授權（MIT / Apache / GPL 三大類）、你的貢獻在法律上歸誰、CLA vs DCO、以及最容易惹麻煩的雷：別貼公司/別人的 code。這章不是法律意見，是讓你知道「哪裡有地雷、該注意什麼」，避免無心闖禍。

> **環境**：概念為主。前置：[Ch 17](./17-before-contributing.md)（CLA/DCO 初探）。**免責**：本章是教育性概述，不構成法律建議；重大法律問題請諮詢專業。

## 為什麼貢獻者要懂法律

「我只是改個 code，關法律什麼事？」——關係大了。當你貢獻開源，你在做的其實是**把你寫的東西，依某種授權，給全世界用**。這牽涉：

- 你貢獻的 code，著作權歸誰？（你？專案？公司？）
- 你能不能貢獻？（如果是上班時間寫的，可能屬於公司）
- 你貢獻進去的東西，別人怎麼用？（授權決定）
- 你能不能把某段 code 貼進來？（從別處複製可能侵權）

這些不懂，輕則 PR 被擋（沒簽 CLA），重則惹上著作權糾紛（貼了公司/別人的 code）。這章讓你認得地雷。

## 先建立直覺：開源 = 有條件地授權，不是「免費隨便用」

最大的誤解：「開源 = 公共財，隨便用」。**錯。** 開源軟體**有著作權**，只是著作權人透過一個**授權（license）** 給你「在某些條件下使用/修改/散布」的權利。

```
   著作權（copyright）：誰擁有這段 code（預設是寫的人）
        │
   授權（license）：著作權人允許別人怎麼用
        ├─ MIT：幾乎隨便用，只要保留版權聲明
        ├─ Apache：類似 MIT，加專利條款
        └─ GPL：可以用，但你的衍生物也必須開源（copyleft）
```

「沒有 license 的 repo」不是「可以隨便用」——反而是**保留所有權利**（預設著作權法），你**不能**合法使用/修改它（除了 GitHub 服務條款給的有限權利）。所以開源專案一定要有 license（Ch 33 你建專案時要選）。

## 三大類授權

開源授權幾十種，但抓住三大類就懂八成：

| 類型 | 代表 | 核心 | 對你的意義 |
|---|---|---|---|
| **寬鬆（permissive）** | MIT、BSD | 隨便用，只要保留版權與授權聲明 | 最自由，公司也愛用 |
| **寬鬆+專利** | Apache 2.0 | MIT + 明確的專利授權 + 貢獻條款 | 大型/企業專案常用 |
| **著佐權（copyleft）** | GPL、AGPL、LGPL | 可以用，但**衍生作品也必須用同樣授權開源** | 有「傳染性」，商用要小心 |

```
   寬鬆 (MIT/Apache)               著佐權 (GPL)
   "拿去用，記得標出處"             "拿去用，但你改的也要開源回饋"
   → 可閉源商用                     → 衍生物必須開源（傳染）
```

**copyleft 的「傳染性」**是關鍵差異：用了 GPL 的 code，你的整個衍生作品可能也要 GPL 開源。這對公司是大事（不想被迫開源產品），所以公司對 GPL 戒慎，偏好 MIT/Apache。

對你貢獻的意義：**你貢獻到一個專案，你的 code 通常就採用該專案的授權**（你貢獻給 MIT 專案，你的貢獻也是 MIT）。所以貢獻前看一眼專案的 LICENSE，知道你的東西會以什麼授權釋出。

> 認識論誠實：授權是個複雜的法律領域，LGPL/AGPL/MPL 等各有細節，「衍生作品」的定義在不同情境有爭議。本章是「夠你安全貢獻」的概述，不是完整的授權法。商業情境的授權合規請諮詢法務。

## 你的貢獻歸誰：CLA vs DCO

當你貢獻，著作權預設還是你的（你寫的）。但專案需要某種「法律上的清楚」——確保它有權使用、散布你的貢獻。兩種機制（Ch 17 初探）：

### DCO（Developer Certificate of Origin）

**輕量、聲明式。** 你在每個 commit 加一行 `Signed-off-by`，聲明「這是我寫的、我有權貢獻、我同意用專案授權釋出」：

```bash
git commit -s -m "Fix the bug"
# 自動加：Signed-off-by: Your Name <you@example.com>
```

DCO **不轉讓著作權**——你還是著作權人，只是「簽名保證」你有權貢獻。Linux kernel、Docker 等用 DCO。它輕量、不用簽額外文件，很多專案偏好它。

### CLA（Contributor License Agreement）

**正式、協議式。** 你簽署一份法律協議，把某些權利**授予**（有時甚至轉讓）給專案/公司。常透過 bot：第一次發 PR 時，bot 擋住並要你點連結簽 CLA。

```
   你開 PR → CLA bot：「請先簽 CLA」→ 你點連結同意 → bot 放行
```

CLA 常見於**公司主導的開源**（Google、Meta、Apache 基金會專案）——公司要法律上的保障（如能重新授權、防專利糾紛）。CLA 可能要求你授予更廣的權利（甚至允許專案改授權）。

> 兩者差異重點：DCO 是「我保證我有權貢獻」（你保留著作權）；CLA 是「我授予專案某些權利」（可能更廣，甚至轉讓）。簽 CLA 前**看清楚你授予了什麼**——尤其涉及專利、改授權權利的條款。多數個人貢獻沒問題，但要知道你簽了什麼。

## 最大的雷：別貼不屬於你的 code

這是貢獻者最容易無心闖禍、後果最嚴重的地雷：**只貢獻你有權貢獻的 code。**

### 雷一：公司的 code

如果你受僱寫程式，**你上班時間寫的、或用公司資源寫的 code，著作權通常屬於公司，不是你**（看僱傭合約/當地法律）。把公司的 code 貼進開源 PR：

- 你可能無權這樣做（侵犯公司著作權）。
- 公司可能追究。
- 專案收了「來路不明」的 code 有法律風險。

**安全做法**：
- 開源貢獻用個人時間、個人設備、個人帳號/email（Ch 0 的 per-repo 身分）。
- 想以公司名義貢獻、或貢獻和工作相關的東西，**先問公司**（很多公司有開源貢獻政策/審批流程）。
- 別把公司專案的 code「搬」去開源。

### 雷二：別人的 code（複製貼上）

從別的專案、Stack Overflow、教學文、其他開源 repo 複製 code 貼進你的 PR：

- 那段 code 有它自己的著作權和授權。
- 從 GPL 專案複製貼進 MIT 專案 = 授權衝突，違法。
- 從 Stack Overflow 複製：SO 的 code 有授權（CC BY-SA），不是隨便貼。
- AI 生成的 code 也可能無意中複製了訓練資料裡有授權的 code（灰色地帶）。

**安全做法**：
- 貢獻你自己寫的 code。
- 真要用別處的東西，確認授權相容、且照規定標示出處。
- 受啟發 OK，逐字複製要小心授權。

> 為什麼這比技術 bug 嚴重：技術 bug 改一改就好；著作權污染可能讓專案被迫移除你的貢獻、甚至面臨法律糾紛。維護者對「來路不明的 code」非常敏感（這也是 CLA/DCO 存在的原因——讓貢獻者保證來源乾淨）。

## 實務：怎麼安全貢獻

把法律面收斂成可操作的習慣：

```bash
# 1. 身分分離（Ch 0）：開源用個人 email/帳號
git config user.email "personal@example.com"   # per-repo，別用公司 email

# 2. DCO 簽署（若專案要求）
git commit -s -m "..."

# 3. CLA（若 bot 要求）：看清楚再簽

# 4. 只貢獻自己寫的 code，別貼公司/別人的

# 5. 和工作相關的貢獻：先確認公司政策
```

進階：用 git 的 `includeIf` 依目錄自動切換身分（Ch 0 提過）：

```ini
# ~/.gitconfig
[includeIf "gitdir:~/oss/"]
    path = ~/.gitconfig-personal      # ~/oss/ 下的 repo 自動用個人身分
[includeIf "gitdir:~/work/"]
    path = ~/.gitconfig-work
```

## 踩雷集錦

1. **以為「開源 = 隨便用」**：開源有著作權，只是授權給你用（有條件）。沒 license 反而是「保留所有權利」，不能用。
2. **不看專案授權就貢獻**：你的貢獻會採該專案授權釋出。GPL 專案的貢獻會是 GPL。看一眼 LICENSE。
3. **貼公司的 code 進開源**：可能無權、侵犯公司著作權。開源用個人時間/身分，工作相關先問公司。
4. **複製別人的 code（含 SO/其他 repo）**：那有自己的授權，可能衝突/侵權。貢獻自己寫的。
5. **不看就簽 CLA**：CLA 可能授予廣泛權利（專利、改授權）。看清楚你授予了什麼。
6. **DCO 和 CLA 搞混**：DCO=保證你有權貢獻（保留著作權，`git commit -s`）；CLA=授予專案權利（可能更廣）。
7. **混用公司/個人 email**（Ch 0）：用公司 email 貢獻個人開源，把貢獻和雇主綁一起、離職失效。per-repo 分開。

## 進階：再往深一層

- **授權相容性**：把 A 授權的 code 用進 B 授權的專案，要看相容（MIT→GPL 可以，GPL→MIT 不行）。混用多授權的相依時要查。
- **AGPL 的網路條款**：AGPL 連「透過網路提供服務」都觸發 copyleft（不只散布 binary）——SaaS 用 AGPL code 要極小心。
- **dual licensing / 重新授權**：有些專案 dual license（如 GPL + 商業），CLA 常是為了讓專案能這樣做（所以要你授予改授權的權利）。
- **SPDX 標識**：`SPDX-License-Identifier: MIT` 是標準化標示每個檔案授權的方式，大專案常用。
- **公司的開源政策**：成熟公司有 OSPO（Open Source Program Office）、貢獻審批、允許/禁止的授權清單。在公司想貢獻先了解。
- **專利條款**：Apache 2.0 的專利授權、CLA 的專利條款——防貢獻者事後用專利告專案。涉及專利的貢獻要注意。
- **AI 生成 code 的授權灰色地帶**（呼應 Ch 20）：AI 可能輸出訓練資料裡有授權的 code，著作權歸屬有爭議，正在演變。對 AI 輔助的貢獻多一份警覺。

## 動手練習

1. 看三個你常用的開源專案的 LICENSE，分辨它們是 permissive（MIT/Apache）還是 copyleft（GPL）——並說出對「商用/閉源」的意義差異。
2. 看一個用 DCO 的專案（如 Docker/Linux），找它要求 `Signed-off-by` 的說明；用 `git commit -s` 做一個帶 sign-off 的 commit，看 message 裡的那行。
3. 找一個用 CLA 的專案（如 Google/CNCF 的專案），看它的 CLA 流程（bot、要簽什麼）。
4. 設定 `includeIf` 讓 `~/oss/` 和 `~/work/` 下的 repo 自動用不同身分（Ch 0），驗證切換有效。
5. 反思：如果你在公司上班，你想貢獻的東西和工作相關嗎？該先問誰？
6. 想一個情境：你在 PR 裡想用一段從 Stack Overflow 看到的 code——你會怎麼處理授權問題？

## 本章重點整理

- 開源不是「隨便用」——有著作權，透過 license 有條件授權；沒 license = 保留所有權利、不能用。
- 三大類授權：permissive（MIT/Apache，隨便用+標出處）、copyleft（GPL，衍生物也須開源、有傳染性）。你的貢獻通常採專案的授權。
- DCO（`git commit -s` 的 Signed-off-by，保證你有權貢獻、保留著作權）vs CLA（簽協議授予專案權利，可能更廣，常公司專案用）——簽 CLA 看清楚授予了什麼。
- **最大的雷：別貼不屬於你的 code**——公司的（上班寫的可能屬公司，工作相關先問）、別人的（SO/其他 repo 有自己的授權）。
- 安全習慣：個人身分/時間貢獻（per-repo email、`includeIf`）、只貢獻自己寫的、和工作相關先確認公司政策。

## 自我檢核

- [ ] 「開源 = 隨便用」錯在哪？沒有 LICENSE 的 repo 你能合法用嗎？
- [ ] permissive 和 copyleft 的核心差異是什麼？為什麼公司對 GPL 戒慎？
- [ ] DCO 和 CLA 各做什麼、差在哪？`git commit -s` 是哪個？
- [ ] 為什麼「貼公司/別人的 code」比技術 bug 嚴重？怎麼避免？
- [ ] 你想貢獻和工作相關的東西，該先做什麼？

## 延伸閱讀

### 官方 / 權威

- **[choosealicense.com](https://choosealicense.com/)** — GitHub
  - **讀哪裡**：三大類授權的比較、各授權的白話說明。
  - **和本章的關聯**：授權選擇的權威工具；Ch 33 建專案選授權會用。

- **[Developer Certificate of Origin](https://developercertificate.org/)** 與 **[Contributor License Agreements (overview)](https://opensource.guide/legal/)**
  - **讀哪裡**：DCO 全文（很短）；opensource.guide 的 legal 章講 CLA/DCO 取捨。
  - **和本章的關聯**：DCO/CLA 的權威來源。

### 部落格 / 文章

- **[Open Source Licensing for the Pragmatic Developer](https://snyk.io/learn/open-source-licenses/)** 類務實授權指南
  - **這篇說什麼**：開發者該懂的授權實務（相容性、copyleft 傳染、商用考量）。
  - **為什麼值得讀**:把法律概念翻成開發者能用的判斷。

### 書籍

- **[Open Source Guides: The Legal Side of Open Source](https://opensource.guide/legal/)** — GitHub
  - **這本的定位**：貢獻與經營開源的法律面完整指南。
  - **讀哪幾章**：整章不長，貢獻者與維護者（Ch 31/33）都該讀一遍。

Part 4 的知識都齊了——找專案、做功課、發 PR、迭代、溝通、法律。用練習 D 把它們用在最真實的場景：對一個真實的開源專案，發出你的第一個真 PR。

→ [練習 D：對真實專案發出第一個真 PR](./practice-d-first-real-pr.md)
