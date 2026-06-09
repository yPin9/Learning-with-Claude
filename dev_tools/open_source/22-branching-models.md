# Ch 22 — branching model

> **目標**：理解團隊怎麼組織分支——三大主流模型 GitHub Flow、Git Flow、trunk-based development，各自的哲學、適用情境、優缺點，以及怎麼為團隊選對的。沒有「最好」的 model，只有「適合你團隊的」。選錯會讓協作處處卡。

> **環境**：概念為主，git 2.40+、GitHub。前置：Ch 3（branch）、Ch 5/6（merge/rebase）、Ch 10（PR）。

## 為什麼 branching model 重要

Ch 1 講過：很多協作規矩是團隊**約定**的，不是 git 強制的。**branching model** 就是團隊最核心的約定之一——「我們怎麼用 branch 來組織開發、測試、發布」。

選對 model，協作順暢；選錯，處處卡：發布混亂、衝突不斷、hotfix 不知往哪放、新人搞不清該從哪開 branch。而且不同團隊、不同產品（網站 vs 函式庫 vs 手機 app）適合不同 model——硬套一個流行的 model 到不適合的情境，是常見的痛苦來源。

這章把三大主流 model 講清楚，讓你進團隊時看得懂他們在用什麼、為什麼，也讓你將來能為團隊選對。

## 先建立直覺：model 在回答三個問題

所有 branching model 本質都在回答：

```
   1. 開發在哪做？        → feature branch 從哪開、合回哪
   2. 什麼算「可發布」？   → 哪條 branch 代表「能上線的狀態」
   3. 怎麼發布 + 處理急修？ → release 怎麼出、hotfix 往哪放
```

三大 model 的差異，就是對這三題的不同答案。記住這三題，你就能拆解任何 model（包括公司自創的）。

## GitHub Flow：最簡單（多數團隊的起點）

最簡單的 model，適合**持續部署的網站/服務**：

```
   main（永遠可部署）
     │
     ├──● feature-a ──┐
     │                PR + review + CI → merge 回 main → 部署
     ├──● feature-b ──┘
     │
   只有一條長命 branch：main。功能各開短命 branch，做完 PR 合回，立刻部署。
```

規則：
1. `main` 永遠是「可部署」的狀態。
2. 開發開一條 feature branch（從 main）。
3. 做完開 PR、review、CI 過。
4. merge 回 main → **立刻部署**。

優點：**簡單、快、好懂**。沒有一堆 branch 要管。適合 web 服務（持續部署、隨時上線、沒有「版本」概念）。

缺點：沒有「release 階段」的概念——不適合「要同時維護多個版本」（如 v1.x 和 v2.x 都要修 bug 的函式庫）、或「不能隨時上線、要批次發布」的產品。

> 這是**多數團隊和開源專案的預設起點**。如果你不確定用什麼，從 GitHub Flow 開始——它最簡單，需要更複雜時再換。Ch 10 的 branch-based workflow 就是 GitHub Flow。

## Git Flow：複雜（傳統的版本化發布）

2010 年 Vincent Driessen 提出的經典 model，為「有明確版本、批次發布」的軟體設計：

```
   main（只放正式 release，每個 commit 是一個版本 tag）
     │
   develop（整合中的開發版）
     │
     ├── feature/* （從 develop 開，合回 develop）
     │
   release/* （從 develop 開，準備發布、只修 bug，完成後合進 main + develop）
     │
   hotfix/* （從 main 開，緊急修正，合進 main + develop）
```

五種 branch：
- **main**：只放正式發布的版本（每個 commit 對應一個 release tag）。
- **develop**：開發的整合線（功能都先合到這）。
- **feature/***：開發新功能（從 develop，合回 develop）。
- **release/***：準備發布（從 develop 拉出，凍結功能、只修 bug，完成合進 main + develop）。
- **hotfix/***：緊急修正正式版（從 main，合進 main + develop）。

優點：**結構嚴謹**，清楚分離「開發中」「準備發布」「已發布」「緊急修正」。適合有明確版本、定期批次發布、要維護多版本的產品（傳統桌面軟體、有發布週期的產品）。

缺點：**複雜、笨重**。一堆 branch 要管、一個改動要合好幾條 branch、不適合持續部署。**連原作者後來都加註說：如果你做的是持續部署的 web app，Git Flow 可能過度複雜，考慮簡單的 model。**

> 認識論誠實：Git Flow 曾經非常流行（幾乎被當成標準），但近年很多團隊認為它對現代持續部署太複雜，轉向更簡單的 GitHub Flow / trunk-based。它仍適合「有版本、批次發布、維護多版本」的情境，但別因為它有名就無腦套用——對 web 服務它常常是過度設計。

## Trunk-Based Development：極簡（高頻整合）

近年在高效能團隊（Google、Facebook 等）流行的哲學，走另一個極端：

```
   main / trunk（大家頻繁直接整合，一天多次）
     │
     ├─● 極短命 branch（幾小時~一天就合回）
     │
   核心：branch 越短命越好，頻繁合進 trunk，靠 feature flag 隱藏未完成功能
```

規則：
1. 大家都頻繁地（一天多次）把小改動整合進 main/trunk。
2. branch 極短命（幾小時到一天），或甚至直接 commit 到 trunk（小團隊）。
3. 未完成的功能用 **feature flag**（功能開關）隱藏——code 進 main 但功能關著，不影響使用者。
4. 高度依賴強大的 CI/CD 和自動化測試（頻繁整合要有自動把關）。

優點：**衝突最少**（分岔越短衝突越少，Ch 5/25）、整合最快、最適合大團隊高頻協作。

缺點：需要**成熟的工程文化**——強 CI、完整測試、feature flag 基建、團隊紀律。小團隊/新團隊硬上可能翻車（沒測試保護就頻繁進 main = 災難）。

> trunk-based 的核心洞察：**長命 branch 是萬惡之源**（分岔越久、衝突越多、整合越痛，Ch 25）。所以與其管理一堆長命 branch，不如讓 branch 短到幾乎不存在、頻繁整合。但這要強大的自動化兜底——它把「人工管理 branch」換成「自動化把關 + feature flag」。

## 三者對照

| | GitHub Flow | Git Flow | Trunk-Based |
|---|---|---|---|
| 長命 branch | main | main + develop | 只有 main/trunk |
| branch 壽命 | 短（天~週）| 中~長 | 極短（時~天）|
| 複雜度 | 簡單 | 複雜 | 極簡（但要強自動化）|
| 適合 | web 服務、持續部署 | 版本化、批次發布、多版本 | 大團隊、高頻、強 CI |
| 衝突 | 中 | 多（branch 多）| 少（分岔短）|
| 發布 | 持續 | 批次（release branch）| 持續（feature flag）|
| 未完成功能 | 留在 branch | 留在 feature branch | feature flag 藏在 main |

## 怎麼選

```
   你的情境？
   ├─ web 服務、想持續部署、團隊不大 → GitHub Flow（最簡單，預設選這）
   ├─ 有版本號、批次發布、要維護多個版本（如函式庫 v1/v2）→ Git Flow（或它的簡化變體）
   ├─ 大團隊、高頻整合、有成熟 CI/feature flag → Trunk-Based
   └─ 不確定 → GitHub Flow 起步，需要時再演進
```

實務上很多團隊用**變體/混合**：GitHub Flow + 一條 release branch、Git Flow 但砍掉 develop、trunk-based 但留短命 PR branch。**model 是起點不是教條**——依團隊痛點調整。重點是團隊**有共識、寫下來**（在 CONTRIBUTING / 內部 wiki），而不是各做各的。

> 進團隊先問：「我們用什麼 branching model？feature branch 從哪開、合回哪？hotfix 怎麼處理？release 怎麼出？」——這幾題搞懂，你就知道怎麼在這個團隊協作。看不出來就直接問，別猜（猜錯會 merge 到錯的 branch）。

## 踩雷集錦

1. **無腦套用 Git Flow 到 web 服務**：對持續部署過度複雜（連原作者都這樣說）。web 服務多半 GitHub Flow 就夠。
2. **以為有「最好的」model**：沒有。看情境（產品類型、團隊規模、發布方式、CI 成熟度）。
3. **trunk-based 沒有強 CI 就硬上**：頻繁進 main 但沒測試保護 = 頻繁弄壞 main。trunk-based 要自動化兜底。
4. **長命 branch 不同步**：不管哪個 model，feature branch 開太久不跟 main 同步 = 衝突地獄（Ch 25）。短命或勤同步。
5. **團隊沒共識、各做各的**：有人 GitHub Flow、有人自創——混亂。model 要團隊統一、寫下來。
6. **進新團隊用舊習慣**：每個團隊 model 不同。先問清楚他們怎麼做，別套用上一份工作的習慣。
7. **hotfix 不知往哪放**：每個 model 對緊急修正有不同處理（Git Flow 有 hotfix branch、GitHub Flow 就是普通 PR + 快速部署）。搞清楚你團隊的 hotfix 流程。

## 進階：再往深一層

- **release branch 的細節**：批次發布的團隊用 release branch 凍結功能、只進 bug fix，同時 main 繼續開發——理解「凍結 + 並行開發」怎麼運作。
- **feature flag**：trunk-based 的命脈。功能 code 進 main 但用開關控制是否啟用——讓「未完成的東西能安全進主線」。也用於 A/B test、漸進發布（呼應其他工程實踐）。
- **環境分支（environment branch）**：有些團隊用 `staging`/`production` branch 對應部署環境（push 到 staging = 部署到測試環境）。這是 model 的另一個維度。
- **monorepo 的 branching**：一個 repo 放多專案時，branching 策略更複雜（Ch 35）。
- **GitLab Flow**：GitHub Flow + environment/release branch 的折衷，介於 GitHub Flow 和 Git Flow 之間。
- **release train**：固定週期發布（如每兩週），到點就從 main 切 release，不管功能做完沒——大團隊協調發布的方式。

## 動手練習

1. 對著「三個核心問題」（開發在哪做、什麼算可發布、怎麼發布+hotfix），分別用三個 model 回答一遍。
2. 看你用過的 3 個開源專案，從它們的 branch 列表（main？develop？release/*？）和 CONTRIBUTING 推斷它們用哪個 model。
3. 在一個測試 repo 模擬 GitHub Flow：main + 開 feature branch + PR + merge——體驗最簡單的流程。
4. 模擬 Git Flow 的 hotfix：從 main（假裝是正式版）開 hotfix branch、修、合回 main + develop——體驗多 branch 的複雜。
5. 給三個情境選 model 並說理由：(a) 個人 side project 網站；(b) 一個有 v1/v2 都要維護的開源函式庫；(c) 50 人團隊、有完整 CI 的大型 web app。
6. （思考）你（假設）的團隊/專案該用哪個？列出選擇理由與可能的調整。

## 本章重點整理

- branching model 是團隊核心約定：回答「開發在哪做、什麼算可發布、怎麼發布+hotfix」三題。
- **GitHub Flow**：最簡單，只有 main + 短命 feature branch，持續部署——多數團隊/開源的預設起點。
- **Git Flow**：複雜，main+develop+feature/release/hotfix，適合版本化/批次發布/多版本——但對 web 服務常過度複雜（連原作者都這樣說）。
- **Trunk-Based**：極簡，極短命 branch + 頻繁進 trunk + feature flag，衝突最少，但要強 CI/自動化兜底。
- 沒有「最好」的 model，只有「適合情境的」；常用變體/混合；team 要有共識、寫下來。
- 進新團隊先問清楚他們的 model（feature 從哪開、合回哪、hotfix 怎麼處理），別套舊習慣。

## 自我檢核

- [ ] 三大 model 各怎麼回答「開發在哪做、什麼算可發布、怎麼發布」？
- [ ] GitHub Flow 和 Git Flow 各適合什麼產品？為什麼 Git Flow 對 web 服務常過度複雜？
- [ ] trunk-based 的核心洞察是什麼？它靠什麼讓「未完成功能能進 main」？沒有什麼就不該用它？
- [ ] 進新團隊，你會問哪幾個問題來搞懂他們的 branching model？
- [ ] 為什麼說「沒有最好的 model」？選擇看什麼因素？

## 延伸閱讀

### 部落格 / 文章

- **[A successful Git branching model](https://nvie.com/posts/a-successful-git-branching-model/)** — Vincent Driessen
  - **這篇說什麼**：Git Flow 的原始提案。
  - **讀哪裡**：整篇，**特別注意開頭作者 2020 年加的 note**——他說明 Git Flow 對持續部署的 web app 可能過度複雜。
  - **為什麼值得讀**：Git Flow 的權威來源，加上作者自己的反思。

- **[GitHub flow](https://docs.github.com/en/get-started/using-github/github-flow)** — GitHub
  - **這篇說什麼**：GitHub Flow 的官方說明。
  - **和本章的關聯**：最簡單 model 的權威。

- **[Trunk Based Development](https://trunkbaseddevelopment.com/)** — Paul Hammant
  - **這篇說什麼**：trunk-based 的完整論述（為什麼長命 branch 有害、feature flag、怎麼做）。
  - **讀哪裡**:首頁概論 + "5-minute overview"。
  - **為什麼值得讀**：trunk-based 哲學的權威站點。

### 書籍

- **[Accelerate](https://itrevolution.com/product/accelerate/)** — Forsgren, Humble, Kim
  - **這本的定位**：用資料證明高效能團隊的工程實踐（trunk-based、CI/CD 與績效的關聯）。
  - **讀哪幾章**：關於 version control 與 trunk-based 的章節。
  - **為什麼值得讀**：給「為什麼高效團隊偏好 trunk-based / 短命 branch」的研究實證。

選好 model 後，下一章是讓 model 真正生效的機制——保護分支：怎麼防止有人直接推 main、強制 review 和 CI。

→ [Ch 23 保護分支與規則](./23-branch-protection.md)
