# Ch 34 — 可達性分析與 VEX 自動化

> **目標**：理解「call graph 可達性」如何把 VEX 的 `not_affected` 從人工判斷提升到自動化；掌握 CHA/RTA/pointer analysis 三種 call graph 建構方法的精確度與代價取捨；消化 V1SCAN、Eclipse Steady、EU FASTEN 三個系統的設計決策；誠實評估靜態可達性分析的根本極限，避免過度樂觀的 VEX 變成更危險的謊言。

## 為什麼需要這個？

Ch 16 介紹了 VEX，Ch 33 介紹了消費平台怎麼把 SBOM 與漏洞資料庫關聯起來。現在我們面對一個真實的問題：

你的 Java 後端依賴 `log4j-core 2.14.1`（CVE-2021-44228，Log4Shell）。掃描器回報 Critical。但你只把 log4j 用在一個 batch job 裡印開機訊息，不接受任何外部輸入，`${jndi:...}` 的觸發路徑根本不可能被使用者抵達。

按照 VEX 規範，正確的聲明是 `not_affected`，justification 是 `vulnerable_code_not_in_execute_path`。但你怎麼**證明**這一點？手寫 VEX 靠的是人工審查——每個 CVE 都要一個人去讀脆弱函式的文件、讀你的 call stack、寫下結論。這個過程慢、容易出錯、不可持續。

EU FASTEN 專案的資料說得很清楚：Maven 生態中，約 1/3 的套件有脆弱傳遞依賴，但只有大約 1% 有 reachable call 能到達脆弱方法。這意味著**你掃出的 Critical 裡，有 97% 在 reachability 層面是不相關的**。如果你能自動算出哪些 CVE 真的 reachable、哪些不 reachable，你就把 VEX 的生產成本從「每個漏洞一份人工報告」降到「call graph 跑一次、批量簽發 not_affected」。

這是 SBOM 從合規清單變成真正安全訊號的樞紐：不是你知道自己有哪些元件，而是你知道哪些漏洞在你的產品裡**真的可被利用**。

## 先建立直覺

想像你的軟體是一棟多層建築，`main()` 是大門。攻擊者要利用漏洞，必須從大門進來，沿著走廊（函式呼叫）一路走到目標房間（脆弱函式）。

```
  [大門 main()]
       │
       ├──▶ [processRequest()]
       │         │
       │         ├──▶ [parseJSON()]          ← 走廊 1
       │         │         │
       │         │         └──▶ [vuln_func()]  ◀── 脆弱函式在這裡
       │         │
       │         └──▶ [renderTemplate()]     ← 走廊 2（不通向脆弱房間）
       │
       └──▶ [loadConfig()]
                 │
                 └──▶ [log()]               ← 走廊 3（不通向脆弱房間）

  [orphan_utility()]  ─── 連大門都到不了的孤立函式
  [format_disk()]     ─── （永遠不可達）
```

可達性分析要問的是：「從所有可能的入口點（main、HTTP handler、CLI 命令）出發，沿著所有可能的呼叫路徑，能不能到達脆弱函式？」

如果答案是「不能」，VEX `not_affected` + `vulnerable_code_not_in_execute_path` 就有了技術依據。如果答案是「能」，你就需要進一步評估是否有輸入控制、沙盒隔離等緩解措施，才能做 `not_affected` 的其他 justification 或承認 `affected`。

重點是：這個問題可以被形式化，可以被自動化，但**精確度有天花板**，而且天花板來自語言本身的動態性。

## 完整鏈：從 CVE 到 VEX 自動化

```
  ① 元件有 CVE
  ─────────────────────────────────────────────────────
  CVE-2024-1234 影響 libfoo 1.2.3
  脆弱函式：libfoo::Parser::parse_untrusted(buf, len)
                    │
                    ▼
  ② 找到脆弱函式的精確識別符
  ─────────────────────────────────────────────────────
  - 從 CVE advisory 或 commit diff 取得函式名稱
  - 或用 code clone detection (CENTRIS) 識別哪些 binary 包含這段程式碼
  - 或用 patch-based signature（Eclipse Steady 的做法）
                    │
                    ▼
  ③ 建構你的應用的 call graph
  ─────────────────────────────────────────────────────
  入口點 ──▶ CHA/RTA/pointer analysis ──▶ 完整 call graph
                    │
                    ▼
  ④ 查詢：脆弱函式是否在 reachable 集合中？
  ─────────────────────────────────────────────────────
  reachable_set = { f | ∃ path from entry_points to f in call_graph }
  is_reachable(vuln_func) → True / False
                    │
          ┌─────────┴──────────┐
          ▼                    ▼
     NOT reachable          IS reachable
          │                    │
          ▼                    ▼
  VEX: not_affected       繼續人工評估
  justification:          ─ 攻擊者能控制輸入嗎？
  vulnerable_code_not_in  ─ 有沙盒隔離嗎？
  _execute_path           ─ VEX: affected / not_affected
                            + 其他 justification
```

這個鏈的技術瓶頸在步驟 ③：call graph 建構。

## Call Graph 建構方法論

call graph（呼叫圖）是一個有向圖：節點是函式，邊 `A → B` 表示函式 A 可能呼叫函式 B。「可能」這個詞很關鍵——靜態分析只能做 over-approximation（多算）或 under-approximation（少算），不可能在所有情況下都精確。

### CHA：Class Hierarchy Analysis

CHA 是最快、最保守的方法。當遇到虛擬呼叫（virtual call）時，它假設：**任何在繼承樹上符合方法簽名的具體類別都可能被呼叫**。

```java
// 程式碼
void process(Shape s) {
    s.draw();  // 虛擬呼叫
}
```

CHA 的 call graph 邊：
```
process() ──▶ Circle::draw()
process() ──▶ Square::draw()
process() ──▶ Triangle::draw()
process() ──▶ HiddenShape::draw()   ← 即使這個類別根本沒有被實例化過
```

CHA 的優點：建構速度 O(n)，n 是程式大小。缺點：大量假邊（spurious edges），over-approximation 嚴重，reachable 集合偏大，VEX 自動化的假陰性（漏掉可以發 not_affected 的 CVE）高。

### RTA：Rapid Type Analysis

RTA 改進 CHA：它**追蹤程式裡哪些類別確實被 `new` 過**（即「live types」），只把 live types 加進 call graph 的候選集。

```
呼叫點 s.draw()
↓
RTA 問：哪些 Shape 的子類別在程式裡有 new 過？
→ 只有 Circle 和 Square 被 new 過
→ call graph 邊：process() ──▶ Circle::draw()
              process() ──▶ Square::draw()
              （Triangle 和 HiddenShape 不算，因為沒被實例化）
```

RTA 比 CHA 精確，代價略高（需要一次全程式掃描找 allocation sites），仍然是 over-approximation，但假邊大幅減少。

### Pointer Analysis / Points-to Analysis

pointer analysis 是最精確的方法：它追蹤**每個指標變數在每個程式點可能指向哪些物件**（points-to set）。

兩種主要變體：

**Andersen's analysis（inclusion-based）**：
- 為每個指標 p 維護 pts(p) = p 可能指向的物件集合
- 約束傳播：`p = q` → pts(p) ⊇ pts(q)；`p = &x` → x ∈ pts(p)
- 複雜度 O(n³)，n 是程式大小
- 相對精確，flow-insensitive（不考慮程式執行順序）

**Steensgaard's analysis（unification-based）**：
- 把「可能指向同一物件的指標」合并成等價類
- 複雜度接近 O(n)（幾乎線性）
- 代價：更多的 over-approximation，精確度低於 Andersen

```
精確度 vs 代價的光譜：

  低代價 ◀──────────────────────────────────▶ 高代價
  低精確度                                  高精確度

  Steensgaard  CHA  RTA  Andersen  context-sensitive pointer analysis
      O(≈n)    O(n) O(n²)  O(n³)          O(2^n) 最壞情況
```

context-sensitive（呼叫點敏感）分析會區分同一函式被不同呼叫點呼叫時的不同 data flow，精確度最高，但在大型程式（數百萬行）上往往不可行。

實務上，大型生態工具（如 FASTEN）用 RTA 作為預設，對精確度要求高的場景才切換 Andersen。

### 全程式分析 vs 增量分析

全程式分析每次重新計算完整 call graph，代價隨程式大小線性到指數成長，只適合 CI 定期批次。增量分析在程式碼變更後只重算受影響的部分（通常是改動函式的鄰域），適合 PR 觸發的即時檢查，代價可壓到分鐘級。

## 底層機制：FASTEN 的函式級 call graph 建構

EU FASTEN 專案提出了生態級（ecosystem-wide）的函式層 call graph 基礎設施，以 Maven 生態為例：

```
  ┌──────────────────────────────────────────────────────────┐
  │                 FASTEN 架構                               │
  │                                                          │
  │  1. 套件層（Package layer）                               │
  │     Maven Central → 下載每個版本的 JAR/源碼               │
  │                                                          │
  │  2. 呼叫圖生成層（CG Generator）                          │
  │     每個 JAR 單獨跑 RTA → 產出 partial call graph         │
  │     格式：FASTEN URI                                      │
  │     fasten://mvn/log4j-core$2.14.1/org.apache.logging..  │
  │     ...log4j.core.impl.Log4jLogEvent.%3Cinit%3E()%2Fvoid │
  │                                                          │
  │  3. 跨套件拼接層（Stitching）                              │
  │     套件 A 呼叫套件 B 的方法 → 拼接兩張 partial CG         │
  │     結果：完整的跨套件函式級 call graph                    │
  │                                                          │
  │  4. 查詢層（Query API）                                   │
  │     給定應用的依賴集合 + 入口點，                          │
  │     查詢某脆弱函式是否可達                                  │
  └──────────────────────────────────────────────────────────┘

  結果（Maven 生態）：
  ─ 有脆弱傳遞依賴的套件：~33%（約 1/3）
  ─ 有 reachable call 到脆弱方法的套件：~1%
  ─ 假陽性壓縮比：約 33x
```

33x 的數字是 reachability 方法的核心論點。在沒有 reachability 的世界裡，你會對 33% 的套件回報「有脆弱依賴」；加上 reachability，真正需要處理的降到 1%。對一個有 500 個 Maven 依賴的企業應用，這意味著要處理的警報從 165 個降到 5 個。

FASTEN URI 格式的設計很重要：它把語言（java）、生態（mvn）、套件（log4j-core）、版本（2.14.1）、命名空間、方法簽名都編碼進一個字串，讓跨生態、跨版本的函式級精確識別成為可能。這個格式設計考量對後來的 SBOM 函式級擴展有直接啟發。

## 精讀論文

### V1SCAN

**標題**：V1SCAN: Discovering 1-day Vulnerabilities in Reused C/C++ Open-source Software Components Using Code Classification Techniques  
**作者**：Woo, Choi, Lee, Oh（Korea University）  
**發表**：USENIX Security 2023

**核心方法**：V1SCAN 結合 version-based 與 code-based 兩種分析。version-based 先用版本號快速篩選「可能受影響」的元件；code-based 再把重用進來的程式碼**分類成三種狀態**——exactly reused（原封不動重用）、changed（被修改過）、unused（實際沒用到），只把「確實被包含進來、且屬於脆弱那一段」的程式碼算成受影響，藉此濾掉 version-based 大量的假陽性。（注意：V1SCAN 用的是 code classification，不是後面會講的 Code Property Graph——別把兩者混為一談。）

**關鍵數字**：
- 假陽性率（FP rate）：從 71% 降到 4%
- 假陰性率（FN rate）：從 33% 降到 7%
- 在 ReactOS 的個案：CENTRIS 先認出 23 個 C/C++ OSS 元件、其中 10 個有漏洞共 52 個 CVE；純 version-based 回報的這 52 個 CVE 裡有 47 個（90%）是假陽性，只有 5 個真正相關——V1SCAN 的程式碼分類正是用來砍掉這 90% 的噪音

**建議讀哪節**：Section 3（系統設計）和 Section 4.2（accuracy evaluation）是核心，重點看三種 code 狀態分類的精確度比較表。

**與本章的關聯**：V1SCAN 是「version-based 只是起點，code-based 才是真相」這個論點最直接的量化依據。71%→4% 的假陽性壓縮，具體化了 code-level 精確識別在 C/C++ 生態（複雜的 `#include`/header-only、靜態連結、多種 clone 型態）下的效果。

### Eclipse Steady（Ponta/Plate/Sabetta）

**標題**：Beyond Metadata: Code-Centric and Usage-Based Analysis of Known Vulnerabilities in Open-Source Software  
**作者**：Ponta, Plate, Sabetta（SAP Security Research）  
**發表**：ICSME 2018（IEEE TCSE Distinguished Paper Award）

**核心方法**：Eclipse Steady（前身 Vulas，SAP 內部工具）的核心是 usage-based 分析，而非 version-based。它的做法：
1. 在 CVE 的修補 commit 裡找「脆弱版本」和「修復版本」的具體方法簽名差異
2. 在目標應用的 bytecode 裡搜尋這些方法，確認脆弱程式碼是否**存在**
3. 用靜態分析追蹤應用的 call graph，判斷脆弱方法是否**可達**
4. 用動態測試執行（test coverage）補靜態分析的盲點，如果測試確認某路徑沒有被執行到，可進一步降低誤報

**關鍵數字**：論文提供了方法層（method-level）的比對精確度，比版本比對的精確度顯著提升。ICSME 2018 Distinguished Paper 的評審意見特別指出資料集的手工策展品質。

**建議讀哪節**：Section IV（approach）和 Section V（evaluation）；如果對工具使用有興趣，Eclipse Steady 的 GitHub repo（eclipse/steady）有完整的 CLI 使用指南。

**與本章的關聯**：Eclipse Steady 是「雙軌驗證」（靜態 call graph + 動態測試覆蓋）的最具體實作。它也是最早把「method-level 脆弱程式碼識別」與「application-level reachability」串成完整流水線的公開工具，直接影響後來 Dependency-Track 等平台的設計思路。

### EU FASTEN 專案

**標題**：FASTEN: Fine-Grained Analysis of Software Ecosystems as Networks  
**資助**：EU H2020-ICT-2018-2020  
**涵蓋生態**：Java（Maven）、Python（PyPI）、C

**核心方法**：FASTEN 建構了生態級的函式層 call graph 基礎設施。重點創新在「跨套件拼接」——每個套件單獨分析後，按照套件 API 的依賴關係把 partial call graph 拼成跨套件的完整圖，讓「你的應用呼叫的這個方法，最終是否會抵達某個第三方套件的脆弱函式」這個查詢變得可行。

**關鍵數字**：Maven 生態中，有脆弱傳遞依賴的套件約 1/3，但有 reachable call 到脆弱方法的套件只有約 1%，假陽性壓縮約 33 倍。

**建議讀哪節**：FASTEN 專案產出了多篇論文和一個開源工具；入門看專案官方技術報告（D3.1 Deliverable），以及 Hejderup 等人在 ICSE 2018 NIER track 的前導工作 "Software Ecosystem Call Graph for Dependency Management"。

**與本章的關聯**：FASTEN 的 33x 假陽性壓縮比是「reachability 值得做」這個判斷最有力的生態規模量化數據，也是本章核心論點的技術底座。

### Code Property Graph

**標題**：Modeling and Discovering Vulnerabilities with Code Property Graphs  
**作者**：Yamaguchi, Golde, Arp, Rieck  
**發表**：IEEE S&P 2014, pp.590–604

**核心方法**：把 AST（Abstract Syntax Tree）、CFG（Control Flow Graph）、PDG（Program Dependence Graph）合並成單一圖結構——Code Property Graph（CPG）。三種圖各有擅長：AST 反映語法結構，CFG 反映控制流，PDG 反映資料相依關係。CPG 讓你用同一個圖查詢語言（Gremlin/Cypher）表達跨越這三個維度的漏洞模式。

**關鍵數字**：論文在 Linux kernel 等大型 C 專案上自動化發現新的漏洞 pattern，精確度優於單獨使用 AST 或 CFG 的查詢。

**建議讀哪節**：Section 2（code property graphs 定義）和 Section 3（traversals for vulnerability discovery）；開源工具 Joern 是其實作，可以直接上手驗證文中的查詢。

**與本章的關聯**：CPG 是「進階 reachability」的技術方向——它不只問「能不能到達脆弱函式」，而是問「到達時的資料是否來自攻擊者可控的來源」（taint analysis），這是把 reachability 分析從 call graph 層推進到 data flow 層的橋梁。它的開源實作 Joern（下一節「進階」會用到）讓這種 taint 查詢能直接跑。

### Pashchenko 等

**標題**：Vulnerable Open Source Dependencies: Counting Those That Matter  
**作者**：Pashchenko 等  
**發表**：ESEM 2018

**核心方法**：分析 Java 依賴樹中「受影響依賴」的實際暴露情況，區分「在版本範圍內但未被部署」vs「被部署但不可達」vs「可達且可利用」等不同層次的過濾。

**關鍵數字**：約 20% 受影響的依賴未被部署到執行環境，不構成實際危險。

**與本章的關聯**：這個 20% 是 metadata 層（是否實際部署）的過濾，比 reachability 層更淺。它說明即使在做 call graph 分析之前，就已經有一層可以過濾的機會——「部署了嗎？」是先決問題，「reachable 嗎？」才是更深的問題。兩層過濾疊加才能真正壓縮假陽性。

### CENTRIS

**標題**：Centris: A Precise and Scalable Approach for Identifying Modified Open-Source Software Reuse  
**作者**：Woo, Park, Kim, Lee, Oh  
**發表**：ICSE 2021, pp.860–872

**核心方法**：code clone detection——識別「哪些 OSS 元件被 include 進目標 binary」。這是 reachability 分析的先決步驟：你必須先知道目標程式裡有哪些 OSS 成分，才能去找對應的 CVE 脆弱函式。

**與本章的關聯**：CENTRIS 回答「哪些元件在裡面」，V1SCAN 的 call graph 分析回答「這些元件的脆弱函式 reachable 嗎」。兩者是 reachability 完整鏈的不同環節，V1SCAN 在設計時就以 CENTRIS 風格的 clone detection 作為前置步驟。

## 對比與取捨

| 方法 | 精確度 | 代價 | 適用場景 | 主要 over-approx 來源 |
|---|---|---|---|---|
| 版本比對（純 version-based） | 最低 | O(1) | 快速篩選第一層 | 忽略所有 code-level 資訊 |
| CHA | 低 | O(n) | 大型 C++ codebase 快速掃 | 虛擬函式過多 candidate |
| RTA | 中 | O(n²) | Java/生態級掃描（FASTEN 預設） | new 過的 live types |
| Andersen pointer analysis | 高 | O(n³) | 安全研究精確分析 | flow-insensitivity |
| context-sensitive pointer analysis | 最高 | O(2^n) 最壞 | 小型關鍵模組 | 幾乎沒有，但可能 timeout |
| 靜態 + 動態雙軌（Eclipse Steady） | 最高（有測試）| 高（需跑測試）| 有 test suite 的成熟專案 | 測試覆蓋率不完全時仍有盲點 |

| 比較維度 | version-based VEX | code-based reachability VEX |
|---|---|---|
| 生產成本 | 低（人工 + 版本比對）| 中（建 call graph + 自動判定）|
| 誤報壓縮 | 版本範圍層面 | 函式級，效果遠大（33x）|
| 假陰性風險 | 幾乎無（保守）| 存在（under-approximation 情況）|
| 可審計性 | 人工記錄 | call graph + 查詢結果可存證 |
| 適用語言 | 所有 | Java/Python 較成熟，C++ 有工具，Rust/Go 仍發展中 |
| 工具成熟度 | grype VEX 已支援 | FASTEN、Eclipse Steady、Joern |

## 踩雷集錦

**1. 動態分派讓 call graph 不完整**

Java/C++ 的 virtual call、介面分派（interface dispatch）是 over-approximation 的主要來源，但在某些情況下反而是 under-approximation：

```java
// 反射動態載入類別
Class<?> cls = Class.forName("com.example.VulnImpl");
Method m = cls.getMethod("exploit");
m.invoke(null);
```

靜態分析看不到 `"com.example.VulnImpl"` 這個字串在執行期代表什麼。如果脆弱函式是透過反射呼叫，call graph 就不會有這條邊，reachability 分析會錯誤地給出「不可達」。你對這個 CVE 發出 `not_affected` 的 VEX，但其實是可利用的——**這是反射盲點造成的危險假陰性**。

Java 生態中反射呼叫相當普遍，任何宣稱對 Java 做「完整 reachability 分析」的工具，都要問清楚它如何處理 `Class.forName`、`Method.invoke`、Spring/Guice 的 DI 容器。

**2. 跨語言 FFI 的盲點**

JNI（Java Native Interface）讓 Java 呼叫 C native code，Python 的 ctypes/cffi 讓 Python 呼叫 .so。靜態 call graph 分析通常在語言邊界切斷：Java-side call graph 不知道 native function 裡面有什麼，C-side call graph 不知道誰會從 Java 呼叫它。

如果脆弱函式在 C native library 裡，但 Java 透過 JNI 呼叫它，Java-level 的 reachability 分析會說「這個 native function 不在 Java call graph 裡」，於是給出 not_affected——但攻擊者可以透過 JNI 觸發這個 C 函式。

**3. 過度樂觀的 VEX 比沒有 VEX 更危險**

這是最重要的告誡。當你的 call graph 是 under-approximation（少算了一些邊），自動化 VEX 會對「實際上可達但分析看不到」的脆弱函式發出 not_affected。消費者看到 VEX not_affected，就不再處理這個 CVE。這比沒有 VEX、讓消費者自己評估更糟：你主動提供了錯誤保證。

這不是理由不做 reachability，而是理由要**誠實標注分析的精確度保證**：
- 我用的是 RTA，Java reflection 未處理
- 我用的是 Andersen，context-insensitive，跨語言 FFI 未覆蓋
- VEX 的 justification 在 tooling 欄位裡寫清楚，讓消費者知道這是工具自動判定、還是人工審查確認

VEX 規範（OpenVEX 1.0）的 `impact_statement` 欄位就是為此設計的：你可以寫「本聲明由 static call graph analysis（RTA）自動生成，未覆蓋 JNI 呼叫路徑，如有 JNI 使用需人工確認」。

**4. JIT 與動態語言的根本困難**

Python、Ruby、JavaScript 這類動態語言，以及 JVM 上的 JIT 最佳化（方法內聯、去虛擬化），都讓靜態 call graph 更不準確。JavaScript 的 prototype chain 動態修改、Python 的 `getattr`/`__getattribute__` 動態分派，使靜態分析的 over-approximation 變得非常保守（或者你選擇做 under-approximation 就會有危險的漏報）。

FASTEN 在 Python 生態（PyPI）的 call graph 建構效果明顯不如 Java（Maven），這正是動態語言本質困難的體現。

**5. 入口點定義不清楚讓「不可達」失去意義**

reachability 的「可達」是相對於「入口點集合」定義的。如果你把 `main()` 定義為唯一入口點，大量函式看起來不可達——但你的服務可能還有 HTTP handler、gRPC handler、scheduled job runner、JMX interface 等。遺漏任何一個入口點，就可能讓「實際上可達」的函式被判為不可達。

一個嚴格的 reachability 分析必須枚舉所有可能的入口點，包括測試框架注入的 mock 物件、dependency injection 容器自動綁定的實現。這比「建 call graph」本身更難，也更容易被忽略。

## 進階：再往深一層

### 從 call graph 到 taint analysis

call graph 可達性只回答「函式能不能被呼叫到」，它不回答「攻擊者能不能控制傳進去的參數」。taint analysis（污點分析）在 call graph 的基礎上追蹤資料流：某個「source」（例如 HTTP request body）的資料，是否能沿著 data flow 邊流到「sink」（例如脆弱的 `parse_untrusted(buf)`）？

Code Property Graph（Yamaguchi 等 IEEE S&P 2014）是這個方向的標準化框架：CPG 把 CFG 的控制流邊和 PDG 的資料相依邊合并，讓你用圖查詢語言直接表達「從任何使用者輸入，沿著資料流，能否到達這個 sink 函式」。

開源工具 Joern（CPG 的實作）可以做這類查詢：

```scala
// Joern 查詢：找所有「用戶輸入能到達 parse_untrusted」的路徑
cpg.call("parse_untrusted")
   .argument
   .reachableByFlows(cpg.parameter.where(_.method.name("handleRequest")))
   .p
```

這個層次的分析在 VEX 自動化裡還不成熟，目前更多用在安全審計和漏洞研究，而非大規模 SBOM 掃描。但它代表了「從 reachability 到 exploitability」這個方向的技術路徑。

### 函式層 SBOM：SBOM 的下一個演化

目前的 SBOM 格式（SPDX/CycloneDX）描述**元件**層級，最細粒度到套件版本。FASTEN 的函式層 call graph 暗示了一個更細粒度的方向：如果 SBOM 能記錄「你的應用實際呼叫了哪些第三方方法」，VEX 的可達性判斷就能直接在這個細粒度上做，不需要每次部署都重建 call graph。

CycloneDX 1.5 開始有 `services` 和 `formulation` 欄位，部分相關；FASTEN URI 格式是函式層識別符的具體提案。這個方向還在演化，但它是「SBOM 從清單到真安全訊號」這條路的邏輯終點。

### 生態級漏洞傳播建模

把所有套件的 call graph 拼接起來，可以問「如果 log4j-core 的這個方法被攻擊，哪些套件的哪些函式有傳遞可達的呼叫路徑？」這是 FASTEN 的生態研究方向，也是未來「漏洞影響範圍快速評估」的基礎設施。想像 2021 年 Log4Shell 事件：如果有預建的 Maven 生態函式 call graph，可以在數小時內精確列出「所有包含 reachable call 到 JndiLookup.lookup() 的套件」，而不是靠人工和版本掃描拼湊了好幾週。

## 動手練習

這些練習是研究/探索性質的，你需要閱讀工具文件、讀論文、或跑工具來完成。

**練習 1：量化你手邊 Java 專案的假陽性壓縮**

取一個有完整 Maven 依賴的 Java 專案（可以用 Apache Commons、Spring Boot starter template）。
1. 用 `mvn dependency:tree` 列出所有傳遞依賴
2. 用 grype 或 osv-scanner 掃出所有 CVE（只看 Java 生態）
3. 安裝 Eclipse Steady CLI，對同一個專案跑 reachability 分析
4. 比較「有 CVE 的依賴數」vs「有 reachable CVE 的依賴數」
5. 算出你的假陽性壓縮比，和 FASTEN 的 33x 數字比較

**練習 2：理解 Joern CPG 查詢**

1. 下載 Joern（`joernio/joern`）並安裝
2. 用一個簡單的 C 程式（例如含 `strcpy` 的 buffer overflow 範例）建 CPG
3. 跑以下查詢，理解 AST/CFG/PDG 三個圖在 CPG 裡的表現：
   - `cpg.method.name("main").ast.l` — 看 AST 節點
   - `cpg.method.name("main").cfg.l` — 看 CFG 邊
   - `cpg.call("strcpy").argument(2).reachableBy(cpg.method.parameter).l` — 找 strcpy 的第二個引數是否來自函式參數（taint source）

**練習 3：VEX 可達性聲明的書面設計**

設計一個「reachability-backed VEX 生成流水線」：
1. 描述你會選哪種 call graph 分析方法（CHA/RTA/Andersen），理由是什麼
2. 你如何處理 Java reflection？（選項：忽略並在 impact_statement 說明；用啟發式字串分析嘗試解析；限制 not_affected 只用於無 reflection 的路徑）
3. 你的 VEX 文件的 `tooling` 和 `impact_statement` 欄位應該寫什麼，讓消費者知道這份 not_affected 的可信度邊界？
4. 什麼情況下你會拒絕自動生成 VEX、要求人工審查？（列出至少三個觸發條件）

**練習 4：閱讀 FASTEN 專案技術報告**

FASTEN 專案的 Deliverable D3.1 公開在 `fasten-project.eu`（或 Zenodo）。閱讀後回答：
1. FASTEN 如何處理 Python 動態分派的不確定性？他們的 call graph 是 over-approximation 還是 under-approximation，為什麼？
2. FASTEN URI 格式如何編碼方法的參數型別？這對 method overloading（Java）有何意義？
3. 拼接（stitching）步驟的主要技術挑戰是什麼？

## 本章重點整理

- **核心命題**：call graph 可達性能把假陽性大幅壓縮，是 VEX not_affected 自動化的技術底層。FASTEN 在 Maven 生態量化了 33x 的壓縮效果。
- **完整鏈**：CVE → 脆弱函式識別 → call graph 建構 → 可達性查詢 → 自動 VEX 聲明，每個環節都有工具和方法。
- **三種 call graph 方法**：CHA（快但不精確）→ RTA（中間）→ pointer analysis（精確但昂貴）。實務上 RTA 是生態掃描的主流選擇。
- **四個根本極限**：反射（reflection）、跨語言 FFI（JNI/ctypes）、動態語言本質困難、入口點枚舉不完整——這四個問題讓「完整精確的靜態 call graph」在現實大型系統中不可能。
- **過度樂觀的 VEX 是危險的**：under-approximation 的 call graph 會發出錯誤的 not_affected。工具自動生成的 VEX 必須在 impact_statement 裡誠實說明分析邊界，讓消費者知道可信度。
- **技術進化方向**：從 call graph（能不能到達）→ taint analysis（資料能不能被攻擊者控制）→ 函式層 SBOM（把可達資訊持久化進 SBOM 格式）。
- **這是全 Part 的樞紐**：不做 reachability，SBOM 是合規清單；做了 reachability，SBOM 才真的能告訴你「哪些漏洞在你的產品裡有實際風險」。

## 自我檢核

1. CHA 和 RTA 在處理虛擬呼叫時的差異是什麼？哪個的 reachable 集合更大？
2. EU FASTEN 在 Maven 生態發現的假陽性壓縮比是多少？這個數字代表什麼現實意義？
3. V1SCAN 結合了哪兩種方法？它在哪個資料集上取得 FP rate 71%→4% 的改進？
4. Eclipse Steady 使用了「雙軌」驗證，兩軌分別是什麼？
5. 為什麼 Java reflection 對靜態 call graph 分析是一個根本性的問題，而不只是「邊緣情況」？
6. 過度樂觀的 VEX（基於 under-approximating call graph）在實際安全影響上比沒有 VEX 更危險的原因是什麼？
7. 從 call graph 可達性到 taint analysis，分析能力的提升是什麼？Code Property Graph 如何支援這個提升？
8. 為什麼「入口點定義」是 reachability 分析中容易被忽略但影響最大的設定？

## 精讀論文

| 論文 | venue | 推薦理由 |
|---|---|---|
| Woo 等，"V1SCAN: Discovering 1-day Vulnerabilities..." | USENIX Security 2023 | 量化 code-based reachability 對 FP/FN 的改進，C/C++ 生態最佳資料點 |
| Ponta/Plate/Sabetta，"Beyond Metadata: Code-Centric and Usage-Based Analysis of Known Vulnerabilities in Open-Source Software" | ICSME 2018 | Eclipse Steady 設計原點，雙軌分析的具體實作，TCSE Distinguished Paper |
| Yamaguchi 等，"Modeling and Discovering Vulnerabilities with Code Property Graphs" | IEEE S&P 2014 | CPG 的原始論文，taint analysis 向 reachability 的進化基礎 |
| Pashchenko 等，"Vulnerable Open Source Dependencies: Counting Those That Matter" | ESEM 2018 | 量化 metadata 層（未部署）vs reachability 層的過濾效果，多層過濾的量化基礎 |
| Woo 等，"Centris: A Precise and Scalable Approach for Identifying Modified Open-Source Software Reuse" | ICSE 2021, pp.860–872 | code clone detection 是 reachability 分析的先決步驟，V1SCAN 的前置方法 |
| Hejderup 等，"Software Ecosystem Call Graph for Dependency Management" | ICSE 2018（NIER）| FASTEN 的前導工作，函式層 call graph 在生態規模的第一個量化實驗 |

## 延伸閱讀

- **FASTEN 專案官網與 Deliverable**：`fasten-project.eu` — 函式層 call graph 基礎設施的完整設計文件，包括 FASTEN URI 規範
- **Eclipse Steady GitHub**：`eclipse/steady` — open source 工具，可直接在 Maven/Gradle 專案上跑 reachability 分析
- **Joern 官網**：`joern.io` — Code Property Graph 的開源實作，支援 C/C++/Java/Python 的 CPG 建構與 taint 查詢
- **OpenVEX 規範**：`github.com/openvex/spec` — VEX 的完整欄位定義，特別看 `justification` 與 `impact_statement` 的語意
- **CISA VEX 使用案例文件**："Minimum Requirements for VEX"（CISA, 2023）— 政策層面對 not_affected justification 的最低要求，包括 `vulnerable_code_not_in_execute_path` 的使用條件
- **"An Empirical Analysis of the Python Package Index (PyPI)"**（Bommarito & Katz, 2019）— PyPI 依賴圖的基礎資料，理解 Python 生態 reachability 困難的背景
- **NIST SSDF（Secure Software Development Framework）**：reachability 分析可以對應到 SSDF 的 "Analyze the software to identify vulnerabilities" 實踐，理解合規框架如何看待這個技術

---

Ch 34 是 Part 8 的技術樞紐：前面的 Ch 30–33 建立了系統設計的架構觀，這裡把「可達性」這個概念完整展開；後面的 Ch 35 要討論威脅模型與防禦設計，是在「你已經知道哪些漏洞真的可達」的基礎上，問「你的防禦邊界應該畫在哪裡」。reachability 給你了優先順序；威脅模型給你防禦策略。

→ [Ch 35 — 威脅模型與防禦設計](./35-threat-model-defense-design.md)
