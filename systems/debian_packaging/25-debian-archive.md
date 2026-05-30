# Ch 25 — Debian archive 的運作

> **目標**：理解 Debian 官方 archive 的運作——DAK（Debian Archive Kit）、NEW queue、ftp-master 的角色、suite 之間的遷移（unstable → testing → stable）、以及透過 mentors/sponsor 流程貢獻套件到 Debian 的完整路徑。

> **環境**：本章是流程與架構章，講 Debian 官方 archive 的運作機制，不需要特定工具版本。

## 為什麼要了解官方 archive？

前面你學了自建 repo（reprepro/aptly）和 PPA。但 Debian 官方 archive 是這套技術的「終極形態」——它管理五萬個套件、十幾個架構、上千個維護者，是世界上最大的自由軟體 archive 之一。

理解它的運作有兩個價值：
- 如果你想**貢獻套件到 Debian**（成為維護者），必須懂這套流程
- 即使不貢獻，理解 archive 如何維持五萬套件的品質和一致性，能讓你的私有 repo 設計借鏡這些經驗

## 先建立直覺：archive 是個有品質閘門的流水線

```
維護者上傳 source
        │
   ┌────▼─────┐
   │ NEW queue │  ← 新套件第一次進來，ftp-master 人工審核
   └────┬─────┘     （授權、套件名、是否該進 Debian）
        │ 通過
   ┌────▼──────┐
   │ unstable   │  ← "sid"，最新的開發版，所有新東西先進這
   │  (sid)     │     build farm 為每個架構 build binary
   └────┬───────┘
        │ 冷卻 + 無 RC bug + 依賴滿足（britney 演算法）
   ┌────▼──────┐
   │ testing    │  ← 未來的 stable，自動從 unstable 遷移
   └────┬───────┘
        │ freeze + release
   ┌────▼──────┐
   │ stable     │  ← 正式發布版，只收安全更新
   └────────────┘
```

核心設計：**新東西先進 unstable，冷卻、測試、無嚴重 bug 後自動遷移到 testing，testing 凍結後成為 stable**。這個流水線加上各種品質閘門（NEW 審核、autopkgtest、RC bug 追蹤），讓 stable 能達到「伺服器級可靠」。

## DAK：Debian Archive Kit

DAK 是管理整個 archive 的軟體——它是「reprepro/aptly 的工業級版本」，處理 Debian archive 的所有操作：

```
DAK 負責：
  - 接收上傳（驗證簽署、檢查 .changes）
  - 管理 NEW queue（新套件審核佇列）
  - 把套件放進 pool、生成 Packages/Sources/Release
  - 簽署 Release（用 Debian archive key）
  - 處理套件移除、override（section/priority 調整）
  - 管理多個 suite 和它們的關係
```

你不會直接用 DAK（它是 ftp-master 團隊運維的），但理解它的存在能解釋「上傳後發生什麼」。`ftp.debian.org` 背後就是 DAK。

## NEW queue：新套件的人工審核

**任何全新的 source package（或新增 binary package）第一次進 Debian，都要過 NEW queue**——由 ftp-master 團隊人工審核：

```
NEW queue 審核什麼：
  - 授權合規（copyright 檔案是否完整、是否 DFSG-free）← Ch 10 的重要性
  - 套件名是否合理（不和現有衝突、命名慣例）
  - 是否真的該進 Debian（不是 spam、不是惡意）
  - binary package 拆分是否合理
```

> NEW 審核可能要等數天到數週（ftp-master 是志工，套件多）。這是 Debian 品質的第一道人工閘門——確保進 archive 的東西授權乾淨、命名合理。這也是為什麼 Ch 10 的 copyright 這麼重要：NEW 審核第一個看的就是它。

通過 NEW 後，之後同名套件的更新（沒有新增 binary package）就不用再過 NEW，直接進 unstable。

## britney：testing 遷移的演算法

套件從 unstable 自動遷移到 testing，由一個叫 **britney** 的演算法決定。遷移條件（簡化）：

```
unstable → testing 的遷移條件：
  1. 在 unstable 待夠久（冷卻期，由 urgency 決定，Ch 9）
     low=10天 / medium=5天 / high=2天
  2. 沒有比 testing 版本更嚴重的 release-critical (RC) bug
  3. 在所有架構都 build 成功
  4. autopkgtest 通過（自己的 + 下游的，Ch 17）
  5. 不會破壞 testing 的依賴一致性
     （遷移它需要的其他套件也要一起遷移）
```

britney 每天跑，自動把符合條件的套件從 unstable 搬進 testing。這個「冷卻 + 自動遷移」機制讓 testing 持續是個「相對穩定的滾動版本」——新東西進來但要先在 unstable 證明自己。

> britney 處理的「依賴一致性」是個約束滿足問題（呼應 Ch 3）——它要找出「哪組套件能一起遷移而不破壞 testing」。有時一個套件卡住（因為它依賴的東西還沒遷移），整組要一起遷移，這叫 transition（Ch 33）。

## 從 unstable 到 stable 的完整生命

```
套件版本的一生（以一個更新為例）：

  維護者上傳 1.0-1 到 unstable
        │ build farm 編譯所有架構
        │ CI 跑 autopkgtest
        │ 冷卻 5 天（urgency=medium）
        │ 無 RC bug
        ▼
  britney 遷移 1.0-1 到 testing
        │ 在 testing 待著，隨 testing 一起被測試
        ▼
  （某個時間點）Debian 宣布 freeze
        │ testing 凍結，只修 bug 不加新功能
        │ 幾個月的 freeze 期密集除錯
        ▼
  Debian release！testing → 新的 stable
        │ 1.0-1 成為 stable 的一部分
        ▼
  stable 之後只收安全更新（stable-security）和重大修復（stable-updates）
```

這個「unstable 開發 → testing 穩定化 → freeze → stable」的循環，每約兩年一個 Debian stable release。

## 貢獻套件到 Debian：mentors/sponsor 流程

你打包了一個好軟體，想讓它進 Debian 官方。但你還不是 Debian Developer（DD，有上傳權限）。流程：

```
1. 打包（符合 Policy、零 lintian、有 autopkgtest——本課教的全部）
        │
2. 上傳到 mentors.debian.net（不是直接進 archive）
   dput mentors greet_1.0-1_source.changes
        │
3. 在 debian-mentors 郵件清單發 RFS（Request For Sponsorship）
        │
4. 找一個 DD/DM 審核你的套件（sponsor）
   sponsor 檢查品質、給 review 意見、你修正
        │
5. sponsor 滿意後，用「他的」上傳權限把套件上傳到 archive
        │
6. 過 NEW queue → unstable → ...（前面的流水線）
        │
7. （長期）你可以申請成為 Debian Maintainer (DM) 或 Developer (DD)
   獲得自己的上傳權限
```

> mentors/sponsor 是 Debian 的「品質 + 信任」雙重把關：sponsor（資深維護者）確保套件品質，也對「讓這個新人的東西進 archive」背書。這是個學習過程——好的 sponsor 會教你很多。本課教的所有東西（Policy、lintian、autopkgtest、symbols）就是為了讓你的套件能通過 sponsor review。

## 角色階層

```
Debian 的維護者角色：

  一般貢獻者         → 透過 mentors + sponsor 上傳
  Debian Maintainer (DM) → 有限的上傳權限（自己維護的特定套件）
  Debian Developer (DD)  → 完整上傳權限 + 投票權，archive 的正式成員
  ftp-master            → 管理 archive、審核 NEW、運維 DAK
  Release Team          → 決定 freeze、管理 testing→stable
```

從貢獻者到 DD 是個逐步建立信任的過程（New Member process），通常要數月到一年，證明你的技術能力和對 Debian 價值觀的認同。

## 故意對照：你的私有 repo vs Debian archive

| 面向 | 你的私有 repo（aptly）| Debian archive（DAK）|
|---|---|---|
| 規模 | 幾個到幾百套件 | 五萬+ 套件 |
| 審核 | 你自己 | NEW queue 人工 + 自動 QA |
| build | 你的 CI | 全球 build farm，十幾架構 |
| 遷移 | 你手動 snapshot/switch | britney 自動演算法 |
| 簽署 | 你的 key | Debian archive key |
| 品質閘門 | 你的 lintian/autopkgtest | 同樣工具 + RC bug 追蹤 + 全 archive CI |

你的私有 repo 是 Debian archive 的「縮小版」——用同樣的概念（pool/dists、簽署、lintian、autopkgtest），只是規模和自動化程度不同。理解 archive 讓你知道自己的 repo 該往哪個方向成熟。

## 踩雷集錦

1. **以為打包好就能直接進 Debian**：你需要透過 mentors + sponsor（除非你已是 DD）。直接 `dput` 到 ftp-master 沒有權限會被拒

2. **NEW queue 等很久就以為被忽略**：NEW 審核是志工人工做的，數週很正常。耐心等，別反覆催。確保 copyright 完美（最常見的 NEW 卡關原因）

3. **混淆 testing 和 stable 的用途**：testing 是「未來的 stable」滾動版，適合桌面/開發；stable 是「兩年一發、只修安全」的伺服器版。生產伺服器用 stable

4. **以為 unstable (sid) 不能用**：sid 其實相當可用（很多開發者日常用），「unstable」指的是「套件版本流動快」不是「會壞」。但它沒有 stable 的保證

5. **不理解 transition 為什麼卡住**：一個套件（尤其 library）的更新可能觸發 transition——所有依賴它的套件要一起重新 build 並遷移。這期間相關套件可能卡在 unstable（Ch 33）

## 進階：Debian 的品質基礎設施全景

Debian archive 周邊有龐大的自動化品質基礎設施，值得知道：

```
Debian 的 QA 工具生態：
  - tracker.debian.org    : 每個套件的儀表板（bug、lintian、CI、遷移狀態）
  - ci.debian.net         : 全 archive autopkgtest（Ch 17）
  - lintian.debian.org    : 全 archive lintian 報告（Ch 16）
  - bugs.debian.org (BTS) : bug 追蹤系統，RC bug 阻擋遷移
  - reproducible-builds.org/debian : 可重現性追蹤（Ch 4）
  - piuparts.debian.org   : 測試安裝/升級/移除是否乾淨
  - dose3 / edos          : 偵測不可安裝套件（依賴分析，Ch 3）
```

這些工具持續對五萬個套件跑檢查，產生全 archive 的品質視圖。當某個工具發現新的問題類型（如新的 lintian check），能立刻看到全 archive 有多少套件中招，驅動大規模修復。這套基礎設施是 Debian 能維持品質的真正原因——不靠英雄，靠系統化的自動檢查 + 人工審核的結合。

```bash
# 看任一套件在這個生態的狀態
# https://tracker.debian.org/pkg/<package>
# 一頁看到它的 bug、lintian、CI、遷移、可重現性...
```

## 動手練習

1. 瀏覽 `tracker.debian.org`，挑一個你熟悉的套件（如 `curl`），看它的 dashboard：有哪些 bug？lintian 狀態？autopkgtest 狀態？在哪個 suite？

2. 看 NEW queue：`https://ftp-master.debian.org/new.html`，看現在有哪些套件在等審核，等了多久

3. 看 britney 的遷移狀態：`https://qa.debian.org/excuses.php`（excuses 列出套件為什麼還沒遷移到 testing）

4. 看一個套件的版本在不同 suite：`https://tracker.debian.org/pkg/bash` 看 bash 在 stable/testing/unstable 各是什麼版本，理解流水線

## 本章重點整理

- Debian archive 是流水線：NEW queue（人工審核）→ unstable → testing（britney 自動遷移）→ stable
- DAK 是管理 archive 的工業級軟體（reprepro/aptly 的終極版）；NEW queue 是新套件的品質閘門
- britney 演算法根據冷卻期、RC bug、autopkgtest、依賴一致性決定 testing 遷移
- 貢獻流程：打包 → mentors.debian.net → debian-mentors 發 RFS → sponsor review → 上傳
- 龐大的自動化 QA 基礎設施（tracker/ci/lintian/BTS/piuparts）是 Debian 維持品質的真正引擎

## 自我檢核

- [ ] 能畫出套件從上傳到進 stable 的完整流水線（NEW → unstable → testing → stable）
- [ ] 知道 NEW queue 審核什麼，為什麼 copyright 這麼關鍵
- [ ] 能解釋 britney 遷移 testing 的主要條件
- [ ] 知道非 DD 如何貢獻套件到 Debian（mentors + sponsor）
- [ ] 能說出你的私有 repo 和 Debian archive 用的是同樣概念、不同規模

## 延伸閱讀

### 官方文件

- **[Debian Developer's Reference](https://www.debian.org/doc/manuals/developers-reference/)**
  - **讀哪裡**：Ch 5（managing packages，上傳流程）、Ch 3（roles）
  - **學什麼**：成為 Debian 維護者的完整流程和最佳實踐
  - **前提**：讀完本課大部分

- **[mentors.debian.net intro](https://mentors.debian.net/intro-maintainers/)**
  - **讀哪裡**：sponsorship 流程
  - **學什麼**：如何透過 sponsor 把套件貢獻到 Debian
  - **前提**：本章的 mentors 部分

### 部落格 / 文章

- **[How Debian testing works (the britney algorithm)](https://wiki.debian.org/Teams/ReleaseTeam/Britney)**
  - **這篇說什麼**：britney 遷移演算法的詳細運作
  - **讀哪裡**：overview 和 migration criteria
  - **為什麼值得讀**：理解 Debian 如何自動維持 testing 的一致性，是約束滿足在實務的精彩應用

→ [Ch 26 打包 shared library](./26-packaging-shared-library.md)
