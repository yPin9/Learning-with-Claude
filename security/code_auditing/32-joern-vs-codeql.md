# Ch 32 — Joern vs CodeQL

> **目標**：把兩大 CPG/query 平台正面擺上檯面對照——build 需求、精度、語言支援、查詢語言、生態、**授權**、學習曲線、速度，用一張表講死。然後聚焦 Joern **不可取代的那個場景**：build 不了的 target（閉源殘缺 SDK、韌體抽出的片段、只有部分檔案）——CodeQL 卡死，Joern 還能 fuzzy parse 出近似 CPG 跑 dataflow。你會拿到明確的**決策指引**（何時用哪個、怎麼組合），並搞懂一個常被忽略但對接案/商業 audit 是硬約束的現實：**CodeQL 對非 OSS/商業用途有授權限制，Joern（Apache 2.0）沒有**。
>
> **環境**：Joern 4.0.594，WSL Ubuntu 22.04。對照基於前四章（Ch 18-28 CodeQL、Ch 29-31 Joern）的真跑經驗。組合用法接 [Ch 35 漏斗](./35-funnel-combining-tools.md)。

你現在兩個平台都會了：CodeQL（Ch 18-28）精準、宣告式、生態龐大但要 build；Joern（Ch 29-31）不用 build、fuzzy、Scala traversal 但精度較寬鬆。真實審計不是「選一個當信仰」，而是**看 target 選工具、必要時兩個都用**。這一章把選擇標準講清楚，讓你面對一個新 target 時三十秒內知道該掏哪把。

## 正面對照表

| 面向 | CodeQL | Joern |
|---|---|---|
| **build 需求** | **必須 build**（hook 編譯器建 database） | **不用 build**（fuzzy parser，殘缺也吃） |
| **精度** | 高（type/macro/alias 都解準，編譯器級語意） | 較寬鬆（近似 CPG，type-size/macro 常缺） |
| **查詢語言** | QL（宣告式、邏輯式、自成一套語言） | Scala traversal DSL（CPGQL，程序式方法鏈） |
| **語言支援** | C/C++/C#/Java/JS/TS/Python/Go/Ruby/Swift... 官方深耕 | C/C++/Java/JS/Python/PHP/Go/Kotlin/Ruby/Swift/C#... 多但深淺不一 |
| **dataflow** | global taint 函式庫成熟（`isSource`/`isSink`/`isBarrier`） | `reachableByFlows` + 可自訂 semantic |
| **生態/社群** | 龐大：官方 query pack、GitHub 整合、MRVA、CVE query 庫 | 較小但活躍：query database、社群外掛 |
| **授權** | **有限制**：非 OSS/商業用途受 GitHub 授權條款約束 | **Apache 2.0**：商業/接案自由使用 |
| **學習曲線** | 陡（QL 是全新語言 + dataflow 函式庫龐大） | 中（只需 DSL 子集，但 Scala/圖概念要適應） |
| **速度** | database 建置慢，查詢優化後快 | parse 快（不 build），大 CPG 上 dataflow 可能慢 |
| **CI 整合** | 一流（GitHub code scanning 原生、SARIF） | 可（`joern-scan` + SARIF），但沒有原生託管平台 |

**一句話總結對照**：CodeQL 是「能 build 的專案上的精準重武器，生態最強，但要 build + 授權有限制」；Joern 是「不用 build 就能跑的靈活工具，商業自由，精度換來覆蓋更多 target」。兩者的分水嶺是 **build 得了嗎** 和 **授權允許嗎**。

## 關鍵場景：build 不了的 target

這是 Joern **不可取代**的價值，也是整個 Joern Part 存在的理由。有一類 target，CodeQL 從第一步就出局：

```
┌─────────────────────────────────────────────────────────────┐
│ build 不了的 target（CodeQL 卡在 database create）             │
├─────────────────────────────────────────────────────────────┤
│ • 閉源/殘缺 SDK：只給你 .h + 幾個 .c，沒有 build 系統          │
│ • 韌體抽出的片段：從 binary 反出的 C、缺 header、缺定義        │
│ • 只有部分檔案：漏洞回報只附了幾個檔，湊不齊編譯環境           │
│ • 平台編不了：需要特殊 toolchain/交叉編譯環境你手邊沒有        │
│ • 語法不全：正在寫/正在改的殘缺 code、macro 地獄展不開         │
└─────────────────────────────────────────────────────────────┘
```

對這些，`codeql database create` 需要看著編譯器成功編過每個 translation unit——編不過，database 建不出來，**CodeQL 全套（查詢、taint、MRVA）從此無從談起**。

Joern 不 hook 編譯器，它的 `c2cpg` 前端容錯：未定義型別當 identifier、未定義函式建 stub、語法殘缺盡力 parse。所以它能對上面每一種 target **吐出一份近似 CPG**，讓你在上面跑 `reachableByFlows`。近似歸近似，**「有一份能查的圖」對編不起來的 target 是從 0 到 1**——這是練習 E 要你親手驗證的：同一個殘缺 C，gcc/CodeQL 編不過（`unknown type name` + 語法錯），Joern 照樣 parse 出 CPG 並抓到 taint flow。

在**韌體、閉源 SDK、漏洞回報片段、逆向 workflow**（對回 reading_code/RE 的直覺）這些場景裡，你多半 build 不了目標——這正是 Joern 的主場。

## 何時用哪個：決策指引

面對新 target，照這個順序問：

```
1. build 得了嗎？
   ├─ 不行 ──────────────────────────────► Joern（唯一選項）
   └─ 可以 ──► 2. 授權允許用 CodeQL 嗎？
                ├─ 商業/接案且無授權 ──► Joern（或買 CodeQL 授權）
                └─ OSS/研究/已授權 ──► 3. 要快篩還是要精查？
                                        ├─ 快篩偵察 ──► joern-scan / Joern 粗掃
                                        └─ 精準定案 ──► CodeQL（精度 + 生態）
```

實務上最常見的三種決策：

- **能 build 的 OSS，要深查一類漏洞的所有變體** → CodeQL（精度 + MRVA 跨庫 + 成熟 query 庫，Ch 27）。
- **閉源/韌體/片段，或商業接案沒 CodeQL 授權** → Joern（唯一能跑或授權允許）。
- **能 build 但想先快速摸清攻擊面** → Joern/`joern-scan` 快篩找候選 → 挑感興趣的用 CodeQL 精查。這是 Ch 35 的**漏斗**：寬進窄出，Joern 當粗篩、CodeQL 當精篩。

**組合用法**（Ch 35 展開）：Joern 不用 build 所以起步快，先用 `joern-scan` + `reachableByFlows` 在整包 code 上撒網撈候選、把結果匯出 JSON（Ch 31）；對能 build 且值得深挖的部分，再建 CodeQL database 精查。兩個工具不是二選一，是流水線上下游。

## 授權現實：接案/商業 audit 的硬約束

這條常被技術討論忽略，但對**接案顧問、商業安全團隊**是硬約束，必須講清楚。

**CodeQL 的授權有限制**：CodeQL 的 CLI 與 query 對「開源專案的分析」「學術研究」免費，但**對非開源軟體的商業用途受 GitHub 授權條款約束**——簡單說，你拿 CodeQL 去審一個**閉源商業產品**（無論是你自家的還是客戶的），一般需要對應的授權（如透過 GitHub Advanced Security）。這不是技術問題，是合約問題，踩到是法律風險。

**Joern 是 Apache 2.0**：商業使用、接案審計、閉源產品分析，**沒有授權障礙**。這讓 Joern 在**顧問/接案**場景有一個 CodeQL 給不了的優勢：客戶的閉源產品你可以直接上 Joern，不用擔心授權。

> **免責與務實建議**：授權條款會變，本章講的是「有這個維度要考慮」的原則，**不是法律意見**。真要對閉源商業 target 用 CodeQL，去讀當下的 GitHub CodeQL 授權條款、或直接問法務；要避開這整個問題，Joern（Apache 2.0）是乾淨的選擇。技術選型時把授權當一個和 build 需求同等的**第一級篩選條件**，別等做完才發現不能用。

## 別把 Joern 當「免費 CodeQL」

一個要破的迷思：Joern 開源免費，是不是就等於「免費版 CodeQL」？**不是**。兩者是不同取捨的工具，各有 CodeQL 給不了/Joern 給不了的東西：

- **CodeQL 有、Joern 較弱的**：編譯器級精度（type-size、macro 展開、精準 alias）、成熟龐大的官方 query 庫與 dataflow 函式庫、GitHub 原生託管與 MRVA（跨數千 repo 掃）、更完整的 flow-state 建模。
- **Joern 有、CodeQL 給不了的**：不用 build（覆蓋編不起來的 target）、Apache 2.0（商業自由）、對未定義函式也能建 stub + semantic。

在**能 build 的 target 上硬用 Joern** 是放棄精度——CodeQL 能算準的 type/alias，Joern 只有近似字串，你會多吞誤報、也可能因近似而漏。反過來，**在編不起來的 target 上等 CodeQL** 是白等——它根本起不了步。選錯不是「差一點」，是「用錯場景」。

## 踩雷集錦

**錯誤直覺：「Joern 開源免費，等於免費版 CodeQL，能用 Joern 就不碰 CodeQL。」**
正確認識：兩者是**不同取捨**，不是同一工具的免費/付費版。Joern 精度較寬鬆、query 生態較小、沒有 MRVA 這種跨庫規模化；CodeQL 精度高、生態龐大但要 build 且授權有限。能 build 的 OSS 深查該用 CodeQL 的精度與生態；編不起來/商業閉源才是 Joern 的主場。把 Joern 當「免費 CodeQL」會在能 build 的 target 上白白放棄精度。

**錯誤直覺：「用什麼靜態分析工具是純技術選擇，跟授權無關。」**
正確認識：**授權是第一級篩選條件**，和 build 需求同等重要。CodeQL 對閉源商業用途受 GitHub 授權條款約束，接案審客戶的閉源產品可能需要授權；Joern（Apache 2.0）沒這問題。做完一整輪審計才發現「這個 target 不能用 CodeQL」是最貴的錯——選型時就要把授權查清楚。

**錯誤直覺：「能 build 的 target 也用 Joern 比較省事（不用建 database）。」**
正確認識：能 build 的 target 上，CodeQL 的**精度**是它的核心價值——精準 type/alias/macro 讓誤報少、能表達 Joern 近似圖上表達不了的精確條件。為了省一次 database 建置就用 Joern，等於用一堆誤報 triage 時間換那次建置時間，通常不划算。能 build 且要深查 → CodeQL；Joern 留給它不可取代的場景。

**錯誤直覺：「Joern 精度較寬鬆 = Joern 比較爛。」**
正確認識：「寬鬆」在對的場景是**優點**——它讓 Joern 能對編不起來的 target 吐出近似 CPG，這是 CodeQL 給不了的覆蓋率。工具沒有絕對好壞，只有「這個 target 該用哪個」。在 build 不了的韌體片段上，一份寬鬆的近似 CPG 遠勝於一份根本建不出來的精準 database。

**錯誤直覺：「兩個工具只能二選一。」**
正確認識：最佳實務常是**組合**（Ch 35 漏斗）——Joern 不用 build、起步快，先粗篩撈候選；能 build 的部分再用 CodeQL 精查。兩者是流水線上下游，不是互斥。把它們當「非此即彼」會錯過「寬進窄出」這個最有效的實戰配置。

## 進階延伸

- **CPG 這個共同底層**：Joern 和 CodeQL（以及 Ch 3 的理論）背後都是 CPG/類 CPG 表示。理解這點後，你在一個平台學的「source/sink/sanitizer 建模」「dataflow 是可達性」直覺能無縫平移到另一個——差別只在 DSL 語法。把兩個平台當「同一套理論的兩種前端」而不是兩門獨立技術，學習成本會大幅下降。
- **其他 CPG 系工具**：除了這兩個，還有 Semgrep（Ch 13-17，pattern + 輕量 taint，介於語法與語意之間）、以及各家商業 SAST。放在同一張「精度 vs build 需求 vs 授權 vs 生態」的座標上，你能為任何新工具快速定位。Ch 34 的 structural search family 會把這幅地圖補全。
- **授權/合規的更廣面向**：靜態分析工具的授權只是冰山一角——把客戶原始碼上傳到雲端 SAST 的資料外洩風險、SBOM/供應鏈合規、審計報告的責任歸屬，都是接案審計要處理的非技術面。技術選型時把這些一起考慮，才不會做完技術部分卻卡在合規。

## 本章重點整理

- CodeQL vs Joern 的兩條分水嶺：**build 得了嗎**（不行 → 只能 Joern）、**授權允許嗎**（商業閉源 CodeQL 受限、Joern Apache 2.0 自由）。其餘（精度、生態、查詢語言、速度）是次級考量。
- **Joern 不可取代的場景 = build 不了的 target**：閉源殘缺 SDK、韌體片段、部分檔案、語法不全——CodeQL 卡在 database create，Joern fuzzy parse 出近似 CPG 照跑（練習 E 親手驗）。
- 決策順序：build 得了嗎 → 授權允許嗎 → 快篩還是精查。能 build 的 OSS 深查 → CodeQL（精度+生態+MRVA）；編不起來/商業閉源 → Joern；先摸攻擊面 → Joern 粗篩 → CodeQL 精查（Ch 35 漏斗）。
- **授權是第一級篩選條件**，不是技術細節：CodeQL 對閉源商業用途受 GitHub 授權約束，接案審客戶閉源產品可能需授權；Joern 沒這問題。做完才發現不能用是最貴的錯。
- **Joern ≠ 免費 CodeQL**：不同取捨。能 build 硬用 Joern = 放棄精度；編不起來等 CodeQL = 白等。工具沒絕對好壞，只有「這個 target 該用哪個」。

## 自我檢核

- 面對一個新 target，你問的頭兩個問題（決定用 Joern 還是 CodeQL）是什麼？為什麼是這兩個而不是「哪個精度高」？
- 舉三種 CodeQL **完全出局**的 target，說明為什麼它們讓 `codeql database create` 卡死、而 Joern 還能跑。
- 「Joern 是免費版 CodeQL」錯在哪？各舉一個「CodeQL 有 Joern 沒有」「Joern 有 CodeQL 沒有」的能力。
- 你接了一個案子審客戶的**閉源商業產品**（原始碼能 build）。授權層面，用 CodeQL 要注意什麼？用 Joern 呢？
- 描述一個「兩個工具組合」的 workflow（提示：Ch 35 漏斗，誰粗篩誰精查、為什麼這樣排）。
- 為什麼說「Joern 精度較寬鬆」在對的場景反而是優點？舉那個場景。

## 延伸閱讀

- **Ch 35 漏斗：組合多工具（./35-funnel-combining-tools.md）**——本章「組合用法」的完整版：Joern 粗篩 → CodeQL 精查怎麼實際串。讀哪裡：漏斗那節。學什麼：把兩個平台從「二選一」變「上下游」的具體流程。前提：本章、Ch 31。
- **GitHub CodeQL 授權條款（github.com 的 CodeQL CLI / Advanced Security 授權頁）**——CodeQL 商業/閉源用途的權威授權說明（會變，用時查當下版本）。讀哪裡：CLI licensing 與 GHAS 相關條款。學什麼：本章「授權現實」的一手依據，接案前必讀。前提：本章（且非法律意見，需要時問法務）。
- **Joern 官方文件（docs.joern.io）與 Apache 2.0 授權**——Joern 的能力邊界與授權。學什麼：確認 Joern 商業使用的自由度、對照本章表格的各項。前提：Ch 29-31。
- **Ch 32-背景：Yamaguchi S&P'14（前章已列）**——兩個平台共同的 CPG 理論底。學什麼：理解「同一套理論、兩種前端」後，跨平台平移直覺。前提：Ch 3。

兩個 CPG 平台的對照與決策講完了。但 CPG/query 平台不是靜態分析的全部——還有一整族**結構化搜尋**工具（weggli、Semgrep 的結構模式、grep-plus），它們不建完整 CPG、不追 dataflow，卻在「快速結構匹配」上比 CPG 平台更輕更快。下一章從 weggli 開始，補上這族工具，讓你的兵器庫從「重型 CPG 平台」延伸到「輕量結構搜尋」。但在那之前——先用練習 E 把 Joern 的殺手級場景（build 不了的 target）親手跑一遍。

→ [練習 E：Joern 無 build dataflow 查詢](./practice-e-joern-no-build.md)
