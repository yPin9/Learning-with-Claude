# Ch 31 — 命名與版本求解的理論地基

> **目標**：把兩個看似「技術細節」但實際上是整個 SBOM 系統命脈的問題形式化：(1) 元件命名沒有唯一解——同一個東西可能有多個 identifier，對齊它們是個設計問題；(2) 版本求解本質是約束求解——lockfile 是怎麼來的、為什麼版本選擇是 NP-hard 問題、PubGrub 怎麼繞過這個坑。讀完你能解釋「PURL 比 CPE 精準在哪裡」不只是「格式不同」，以及「更新一個 dependency 為什麼有時會爆炸」的理論根源。深挖章。

## 為什麼需要這個？

Ch 4 已經介紹了 PURL / CPE / SWID 三大識別體系的操作細節。這章不重複那些——這章要往下挖，問兩個更根本的問題：

**問題一**：為什麼 identifier 的設計決定了整個系統的關聯能力？光是「格式不同」不足以解釋為什麼某些系統的漏洞比對準得多、某些系統的誤報率高得離譜。

**問題二**：你的 `package-lock.json` 或 `Cargo.lock` 是怎麼產生的？表面上看它只是「把版本釘住」，但它背後是一個求解過程——而這個求解過程在最壞情況下是 NP-hard 的。SBOM 裡記錄的依賴關係，反映了這個求解過程的結果；如果你不理解這個過程，你就不知道 SBOM 裡的依賴圖在語意上代表什麼。

這兩個問題的答案決定了：設計 SBOM 的**內部表示**（internal representation）時，應該怎麼儲存 identifier 和依賴關係。

## 先建立直覺

### 問題一的直覺：名字是上下文的函數

同一個物理實體在不同語境下有不同的名字，這不是 bug，是每個語境的命名系統各自最優化的結果：

```
同一個「東西」在不同語境下的名字（以 OpenSSL 為例）

  上游原始碼作者  →  openssl（openssl.org 的 project name）
  Alpine 打包者  →  libcrypto3（一個 upstream source 打成兩個 apk）
                    libssl3
  Debian 打包者  →  libssl3（同名但包含的東西不同）
  Red Hat 打包者 →  openssl-libs
  NVD 的 CVE    →  cpe:2.3:a:openssl:openssl:3.x.x（用 upstream 名）
  你的 SBOM     →  ?（取決於你的生成工具看到的是哪個層次）

  結果：
  - 你的 SBOM 說 libcrypto3@3.1.8-r1
  - NVD 的 CVE 說 cpe:2.3:a:openssl:openssl:3.1.8（upstream 版本）
  - 名字根本對不上，比對失敗，漏掉 CVE
```

identifier 設計的本質問題是：**在哪個抽象層次命名**。每個層次都有它的名字，跨層次比對必然需要額外的 mapping。

### 問題二的直覺：版本求解是填空問題

想像你有一個填空題：

```
我的專案需要：
  A ≥ 1.0.0, < 2.0.0
  B ≥ 2.0.0

A@1.2.0 需要：C ≥ 0.5.0
A@1.5.0 需要：C ≥ 0.8.0, D ≥ 1.0.0
B@2.1.0 需要：C ≥ 0.6.0, < 1.0.0
B@2.3.0 需要：C ≥ 0.9.0

問：要選哪個版本的 A、B、C、D，才能讓所有條件同時滿足？
```

這個問題的解是：A@1.2.0, B@2.1.0, C@0.6.x（任何滿足 0.6.0 ≤ C < 1.0.0 的版本）。但如果 C 沒有 0.6.x 版本、只有 0.5.x 和 1.0.x，這題就無解。

**lockfile 就是這道題的解**。版本求解器負責解這道題，然後把解記錄在 lockfile 裡，之後每次安裝就不用重解了。

## 問題一：identifier 的形式化

### 三個識別體系的設計目標對比

| | PURL | CPE 2.3 | SWID (ISO/IEC 19770-2) |
|-|------|---------|------------------------|
| 設計目標 | 生態原生識別 | 漏洞資料庫對齊 | IT 資產管理 |
| 管理機構 | ECMA TC54（ECMA-427，2025 年 12 月第一版） | NIST/MITRE | ISO/IEC |
| 命名哲學 | 每個生態用自己的規則 | 統一的供應商/產品/版本三元組 | 標籤安裝在 endpoint，隨軟體生命週期走 |
| 粒度 | package manager 可見的粒度 | 任意軟體（含 OS、HW） | 安裝單位 |
| 主要痛點 | 不涵蓋沒有 package manager 的東西 | 供應商/產品名稱不標準化、靠人工維護 | 在開源生態基本不存在（標籤沒人寫） |

### PURL 的形式化語法

PURL（Package URL）由 ECMA-427 標準化，格式是：

```
scheme:type/namespace/name@version?qualifiers#subpath

其中：
  scheme     → 固定字串 "pkg"（必填）
  type       → package ecosystem 標識符（必填，如 npm, pypi, maven, apk, deb, golang）
  namespace  → 命名空間（依 type 決定是否必填）
  name       → package 名稱（必填）
  version    → 版本（選填）
  qualifiers → key=value 的補充描述（選填，以 & 分隔）
  subpath    → package 內的子路徑（選填，以 # 開頭）
```

幾個有代表性的例子，說明 type 的差異有多大：

```
# Maven（Java）：namespace 是 groupId，name 是 artifactId
pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1

# Alpine apk：namespace 是 distro，qualifiers 記錄 upstream
pkg:apk/alpine/libcrypto3@3.1.8-r1?upstream=openssl&distro=alpine-3.19.9

# Go module：namespace 是 module path 前綴
pkg:golang/github.com/gorilla/mux@v1.8.0

# Docker image：namespace 是 registry/owner
pkg:docker/library/alpine@3.19?arch=amd64

# npm（scoped package）：namespace 是 scope
pkg:npm/%40angular/core@18.0.0
```

**PURL 的關鍵設計選擇**：每個 type 定義自己的 namespace 語意和 version 格式。這讓 PURL 能忠實反映各生態的原生命名，代價是「跨 type 的標準化」幾乎不可能——你沒辦法寫一個函式「判斷兩個 PURL 是不是同一個元件」，除非它們的 type 相同。

### CPE 2.3 的形式化語法

CPE（Common Platform Enumeration）2.3 由 NIST 在 2011 年發布的四份 IR 文件（NIST IR 7695–7698）規範，格式是：

```
cpe:2.3:part:vendor:product:version:update:edition:language:sw_edition:target_sw:target_hw:other

其中 part 是：
  a → Application（軟體應用）
  o → Operating System
  h → Hardware

不知道的欄位填 * 或 NA
```

以 Log4j 為例：

```
cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*

分解：
  part       = a（application）
  vendor     = apache（Apache Software Foundation）
  product    = log4j（注意：不是 log4j-core，是整個 log4j 專案）
  version    = 2.14.1
  其餘欄位   = *（任意）
```

**CPE 的根本問題**：`vendor` 和 `product` 欄位是人工維護的自由文字，沒有統一的規範告訴你 Apache Log4j 的 vendor 要寫 `apache` 還是 `apache_software_foundation`（NVD 字典裡兩種都有）。這個不一致性讓「從 package 名稱對應到 CPE」成了一個維護代價高昂的 mapping 問題。

### PURL 和 CPE 的對齊困難

從 PURL 對應到 CPE（SBOM → 漏洞 DB 比對的必經路），有幾個結構性的困難：

**困難一：粒度差異**

Maven 的 `log4j-core@2.14.1` 是 PURL 的 name（`log4j-core`），但 NVD 的 CPE 用 `log4j` 做 product——兩個 artifact ID 指向同一個上游，但字面不同。沒有 mapping 就對不上。

**困難二：版本語意差異**

```
PURL 記錄的 Alpine 版本：libcrypto3@3.1.8-r1
                                        ↑ 這個 -r1 是 Alpine revision，不是 openssl 本身的版本

NVD CPE 記錄的 openssl 版本：openssl@3.1.8
                                      ↑ 這是 upstream 版本
```

Alpine 打包者可能在 `3.1.8-r1` → `3.1.8-r2` 的時候 backport 了一個 security fix，而 upstream openssl 還是 `3.1.8`。NVD 說「openssl < 3.2.0 受影響」，你的 Alpine 版本是 `3.1.8-r2` 但已經 patch 了——純版本比對會誤報。

**困難三：一個 PURL 可能對應多個 CPE**

因為 NVD 的 vendor/product 命名不標準，同一個 openssl 可能有：

```
cpe:2.3:a:openssl:openssl:...
cpe:2.3:a:openssl_project:openssl:...
```

你的比對邏輯要覆蓋所有已知變體，否則就漏。

### SWID 的定位

SWID（ISO/IEC 19770-2:2015，Software Identification Tag）是 XML 格式的標籤，設計上要在軟體安裝時一起安裝到 endpoint，卸載時一起移除。理想情況下：

```xml
<SoftwareIdentity
  name="log4j-core"
  tagId="org.apache.logging.log4j_log4j-core_2.14.1"
  version="2.14.1"
  xmlns="http://standards.iso.org/iso/19770/-2/2015/schema.xsd">
  <Entity name="Apache Software Foundation" role="tagCreator softwareCreator" />
</SoftwareIdentity>
```

**SWID 在開源軟體生態裡的現實**：幾乎沒有開源 package 主動生成 SWID 標籤，因為這個標準設計上是企業軟體（Windows installer、商業 ERP）的產物。它在 IT 資產管理（ITAM）領域有一定使用，在開發者生態裡基本上是邊緣存在。SPDX 和 CycloneDX 都支援記錄 SWID，但你在野外看到它的機率遠低於 PURL 和 CPE。

### 對 SBOM 內部表示的啟發

理解了三個識別體系的設計差異後，設計 SBOM 系統的內部表示時，有幾個決策：

1. **存多個 identifier**：一個元件應該同時存 PURL（生成時的精確識別）和 CPE（漏洞比對需要），因為兩者服務不同的查詢場景，且無法互相替代。

2. **identifier 有信心度**：PURL 從 manifest 直接讀出來的信心度高；從 binary 推斷出來的 PURL 信心度低。CPE 是從 PURL 自動推導的話，信心度更低。這個信心度應該是 SBOM 內部表示的一等公民，不能只存最終結果而丟掉不確定性。

3. **版本要保留原始格式**：Alpine 的 `3.1.8-r1` 和 upstream 的 `3.1.8` 要分開存，不能合並。漏洞比對層要知道這兩個版本的對應關係，而不是假設它們相同。

## 問題二：版本求解的形式化

### lockfile 是什麼

你的 `package-lock.json`（npm）、`Cargo.lock`（Rust）、`poetry.lock`（Python Poetry）、`pubspec.lock`（Dart）都是**版本求解的結果**。它記錄了在某個時間點，滿足所有 dependency 約束的一個完整、確定的版本集合。

```
用戶寫的（約束，不確定）         求解後（確定，是 lockfile 的內容）
────────────────────────         ────────────────────────────────
[dependencies]                   [[package]]
lodash = "^4.0.0"           →    name = "lodash"
axios = "~1.6.0"                 version = "4.17.21"
                                 checksum = "sha256:abc..."

                                 [[package]]
                                 name = "axios"
                                 version = "1.6.8"
                                 checksum = "sha256:def..."
```

用戶寫的是**約束**（`^4.0.0` 代表 `≥ 4.0.0, < 5.0.0`）；lockfile 裡是**具體版本**。求解過程把約束空間壓縮成一個點。

### semver 範圍的形式化

semver（Semantic Versioning 2.0.0）定義版本為 `MAJOR.MINOR.PATCH`，語意是：

- MAJOR 改變 → 不相容的 API 變更
- MINOR 改變 → 向後相容的新功能
- PATCH 改變 → 向後相容的 bug fix

npm、Cargo、Poetry 等都以 semver 為基礎，加上各自的範圍語法（range syntax）：

```
npm / node-semver 範圍語法（常見子集）：

  ^4.17.0    → ≥ 4.17.0, < 5.0.0          （caret：不動 major）
  ~1.6.0     → ≥ 1.6.0,  < 1.7.0          （tilde：不動 minor）
  ≥2.0.0     → 任何 ≥ 2.0.0 的版本
  1.2.3 - 2.3.4 → ≥ 1.2.3, ≤ 2.3.4       （hyphen range）
  1.x        → ≥ 1.0.0, < 2.0.0
  *          → 任何版本

特殊情況（重要）：
  ^0.2.3     → ≥ 0.2.3, < 0.3.0           （0.x.x 中 minor 視為 major）
  ^0.0.3     → ≥ 0.0.3, < 0.0.4           （0.0.x 中 patch 視為 major）
  這是因為 0.x 版本被視為「不穩定」，允許 breaking changes
```

注意：**版本範圍語意是生態特定的**。Python PEP 440 的 `~=1.6` 等同於 `>=1.6, ==1.*`（接受所有 1.x 版本），和 semver 的 tilde 語意不同。Go module 用日期版本（`v0.0.0-20230101000000-abcdef123456`）。跨生態比較版本範圍時，這個差異是真實的對齊錯誤來源。

### 版本求解是約束滿足問題

正式地，版本求解可以定義為：

```
給定：
  - 一個 root package R
  - 每個 package 的可用版本集合 V(p) = {v₁, v₂, ...}
  - 每個版本的依賴關係 dep(p, v) = {(q, range_q), ...}

求：
  一個賦值函式 f：Package → Version
  使得：
    1. f(R) 是我們選的版本（通常是最新相容版）
    2. 對所有 p 被選入的 package：f(p) ∈ V(p)
    3. 對所有 (q, range) ∈ dep(p, f(p))：f(q) ∈ range
    4. 選入的 package 集合是 R 可達的最小閉包
```

Russ Cox 在 2016 年的「Version SAT」一文中論證了這個問題是 NP-complete——把 3-SAT 歸約到版本選擇問題。（這是部落格文章裡的歸約論證，不是同儕審查的形式化證明，但思路成立、被廣泛接受。）直覺上：一個 boolean 變數可以被建模為一個 package 的兩個版本（`0` 和 `1`），SAT 的子句對應 dependency 約束，整個 SAT 公式對應一個依賴圖。VERSION 問題的解 ↔ SAT 公式的滿足賦值。

**這代表什麼**：在最壞情況下，找到一個滿足所有 dependency 的版本組合需要指數時間。但「最壞情況」在實際套件生態裡不常出現，因為：

- 大多數套件的 dependency 是單調的（只要 ≥ 某個版本）
- 套件數量和版本數量是有限的
- 實用的求解器用啟發式（heuristic）加速，例如「優先試最新版本」

但這個 NP-hardness 結果說明：設計版本求解器時，**在「正確性」和「速度」之間的取捨是有理論基礎的**，不是工程品質問題。

### 鑽石依賴（Diamond Dependency）

鑽石依賴是版本求解最常遇到的衝突形態：

```
        我的專案
        /       \
       A         B
       |         |
       C@^1.0    C@^2.0
          \     /
           C（衝突！^1.0 和 ^2.0 交集為空）

C@1.x 和 C@2.x 是 semver MAJOR 差異，不相容。
A 和 B 各自需要 C 的不同 major 版本。
在「每個 package 只能有一個版本」的約束下，無解。
```

不同生態的處理方式不同：

| 生態 | 處理方式 | 代價 |
|------|---------|------|
| npm/pnpm | 允許同時存在多個版本（`node_modules` 目錄樹） | 磁碟空間大、bundle size 可能膨脹 |
| Rust/Cargo | 允許同時存在多個版本（每個 crate 的 Cargo.toml 各自解析） | 編譯時間增加、binary 大 |
| Python pip | 只允許一個版本，無解就報錯 | 用戶要手動解衝突 |
| Go module | MVS（Minimum Version Selection，見下） | 版本只升不降，但不允許多版本 |

### 依賴圖的語意

版本求解的結果是一個依賴圖，SBOM 記錄的「關係」就是這個圖：

```
  ┌─────────────────────────────────────────────────────────────┐
  │                依賴圖的結構                                    │
  │                                                             │
  │  節點（Node）：確定版本的 package，例如 lodash@4.17.21         │
  │  邊（Edge）：依賴關係，有向（A 依賴 B → A → B）               │
  │                                                             │
  │                   root                                      │
  │                 /   |   \                                   │
  │                A    B    C                （直接依賴）       │
  │               / \   |                                       │
  │              D   E   F                   （傳遞依賴第一層）  │
  │             /                                               │
  │            G                             （傳遞依賴第二層）  │
  │                                                             │
  │  注意：圖可能有循環（C 語言的某些 build system 允許這個）     │
  │  注意：同一個 package 可能從多條路徑被引入（菱形）            │
  └─────────────────────────────────────────────────────────────┘
```

**直接依賴 vs 傳遞依賴的重要性**：SBOM 格式（SPDX/CycloneDX）都允許記錄 relationship 類型（`DEPENDS_ON`、`DEPENDENCY_OF`）。但很多工具在生成時只記錄「所有元件的平鋪清單」，丟掉了圖結構——這讓消費平台沒辦法回答「這個傳遞依賴是從哪條路徑引進來的，去掉哪個直接依賴能移除它」這個問題。

**可能有循環**：雖然大多數語言生態的版本求解假設依賴圖是 DAG（有向無循環圖），但在某些情況下（動態 require、C 的 build system、某些 monorepo 工具）依賴關係可能存在循環。設計 SBOM 內部表示時要能處理這個情況。

### PubGrub：現代版本求解器的設計

PubGrub 是 Natalie Weizenbaum（Dart pub 套件管理器的開發者、Sass 語言作者）於 2018 年為 Dart 的 pub 套件管理器設計的版本求解演算法，後以部落格文章「PubGrub: Next-Generation Version Solving」發表（Medium，nex3.medium.com）。這不是會議論文，是一篇帶完整正式推導的技術部落格文章。

PubGrub 採納的是 CDCL（Conflict-Driven Clause Learning）的思想——這是現代 SAT solver（如 MiniSat、clasp）最核心的加速機制：

```
核心思想：當求解發現衝突時，不只是回溯（backtrack）到上一步，
而是分析衝突的「根因」，生成一條新的「不相容性」（incompatibility），
之後再遇到相同的配置就能直接跳過，不用重複探索。

這讓求解器能把「從失敗中學到的知識」記錄下來，避免指數爆炸。
```

PubGrub 的核心資料結構：

```
不相容性（Incompatibility）：一個「不可能同時為真」的 package 版本集合

例如：{ foo ∈ ^1.0.0, ¬bar ∈ ^2.0.0 }
意義：「foo 在 ^1.0.0 範圍內」且「bar 不在 ^2.0.0 範圍內」不可能同時成立
來源：foo@1.x 的 dependency 宣告 bar@^2.0.0 → 如果選了 foo@1.x 就必須有 bar@2.x

偏解（Partial Solution）：當前已確定的 package 版本選擇清單（還沒全部確定）

演算法主迴圈：
  1. 單位傳播（Unit Propagation）：
     - 掃描所有不相容性，找到只剩一個「未確定項」的不相容性
     - 把那個項的反面加入偏解（因為整個不相容性不可能成立，所以剩下的那項必須是它的反面）
  2. 決策（Decision）：
     - 選一個還未確定的 package，選「最少可用版本」的那個（啟發式）
     - 把它加入偏解
  3. 衝突解析（Conflict Resolution）：
     - 如果發現矛盾，反向追溯找出根因
     - 生成一條新的不相容性，加入知識庫
     - 回溯到適當位置，繼續
```

PubGrub 相比舊式 backtracking solver 的優勢：

```
舊式 backtracking（以 npm 早期 resolver 類型為例）
  嘗試 A@1.5 → 失敗 → 回到上一步
  嘗試 A@1.4 → 失敗 → 回到上一步
  嘗試 A@1.3 → 失敗 → ...
  （不記錄「為什麼失敗」，每次都從零試）

PubGrub（CDCL 風格）
  嘗試 A@1.5 → 失敗 → 分析根因 → 生成不相容性 I
  （I 說明只要 B@2.x 在場，A ≥ 1.3 就不可能成功）
  → 直接跳過 A@1.4, 1.3, 1.2... → 試 A@1.0 或宣告無解
  （利用學到的知識剪枝）
```

**PubGrub 的採用情況**（查證至 2026 年 8 月）：

- **Dart pub**：原始採用者，PubGrub 就是為它設計的
- **uv**（Astral，Python 的新一代 package 管理器）：使用 pubgrub-rs（Rust 實作的 PubGrub），這讓 uv 的 resolver 顯著快於傳統 pip resolver
- **Cargo**（Rust）：pubgrub-rs 是指定的下一代 Cargo solver 的基礎（Rust Project Goals 有此項目）；截至 2026 年，Cargo 仍以實驗性 unstable 旗標測試中，尚未成為預設
- **Bundler**（Ruby）、**Poetry**（Python）：也有 PubGrub 實作或受其啟發

對比：**Go module 的 MVS（Minimum Version Selection）**是另一種設計哲學——不選「滿足約束的最新版」，而是選「所有 requirement 要求的最低版本中的最大值」。MVS 完全避開了 SAT，因為它的選擇過程是確定性的，但代價是不允許多版本共存，且版本只會往上走不會往下。

### 鑽石問題與 SBOM 的關聯

為什麼 SBOM 設計者要理解版本求解？因為 lockfile 裡記錄的版本——以及 SBOM 裡記錄的傳遞依賴——是求解結果，不是用戶直接指定的。這有幾個直接影響：

1. **SBOM 的傳遞依賴清單反映了一個特定的求解結果**：在不同時間點（依賴版本更新後）重新求解，傳遞依賴清單可能完全不同。「同一個版本的我的軟體，但在不同時間安裝」可能有不同的 SBOM——這是 SBOM 的時效性問題的根源之一。

2. **版本範圍 vs 具體版本**：`package.json` 裡的 `^4.0.0` 是約束，`package-lock.json` 裡的 `4.17.21` 是具體版本。SBOM 應該記錄哪個？——記錄 lockfile 的具體版本（可重現性高），還是記錄 manifest 的約束（更接近開發者意圖）？這是設計決策，SPDX 和 CycloneDX 的立場是記錄具體版本，但兩者也允許記錄 version ranges。

3. **多版本共存的問題**：npm 的 `node_modules` 裡可能有兩份 `lodash`（不同版本），都是「被安裝的元件」。SBOM 要記錄兩份嗎？還是只記錄一份？如果只記錄一份，用哪個？——實際上應該記錄兩份，並且分別記錄它們是從哪個路徑被引入的。

## 方法與形式化：identifier 對齊的映射問題

從設計者視角，PURL → CPE 的對齊是一個映射問題：

```
PURL 空間                         CPE 空間
─────────────────                 ────────────────────
pkg:apk/alpine/libcrypto3@3.1.8-r1
                    │
                    │ 需要知道：
                    │  1. libcrypto3 的 upstream 是 openssl
                    │  2. Alpine r1 版本對應 upstream 3.1.8
                    │  3. 可能有 backport patch
                    ↓
              cpe:2.3:a:openssl:openssl:3.1.8:*:*:*:*:*:*:*

這個映射不能從 PURL 本身自動推導，需要：
  - Alpine 的 distro-to-upstream 映射資料庫（Alpine 的 OVAL/Secdb）
  - 漏洞資料庫提供者的 AffectedPackage 記錄（OSV 格式做得較好）
  - NVD 的 CPE 字典（非完全準確，靠人工維護）
```

OSV（Open Source Vulnerabilities）格式對這個問題的設計選擇比 NVD/CPE 更合理：它讓每個 ecosystem 用自己的 package name 和 version，然後漏洞描述直接說「pypi 裡的 django@<3.2.13 受影響」，不要求轉換成 CPE。這讓漏洞關聯在「同生態」的情況下準確得多——代價是跨生態的統一視圖更難建。

## 對比與取捨

### identifier 體系選型

| 面向 | PURL | CPE 2.3 | SWID |
|------|------|---------|------|
| 精確度（對同一個 package） | 高（生態原生） | 中（vendor/product 標準化問題） | 高（但依賴廠商實作） |
| 覆蓋廣度 | package manager 管理的元件 | 任意軟體（含 OS/HW） | 安裝在 endpoint 的商業軟體 |
| 漏洞比對能力 | 需要轉 CPE 才能查 NVD；直接查 OSV 準確 | NVD 的原生語言，可直接比對 | 幾乎不用於漏洞比對 |
| 自動生成難度 | 容易（從 manifest 直接讀） | 難（需要 PURL→CPE 映射） | 很難（廠商要主動生成） |
| 在開源生態的覆蓋 | 幾乎全覆蓋 | 不完整（NVD 字典不完全） | 極少 |

### 版本求解策略選型

| 策略 | 代表實作 | 優點 | 缺點 |
|------|---------|------|------|
| Backtracking | 舊式 npm resolver | 實作簡單 | 最壞情況指數時間，錯誤訊息差 |
| PubGrub（CDCL） | Dart pub, uv | 快、錯誤訊息可讀 | 實作複雜 |
| MVS | Go module | 確定性強、快 | 只能用最小版本、不允許多版本 |
| SAT/ASP solver（通用） | Debian（APT 的外部 solver 介面 + EDOS/Mancoosi 的 aspcud） | 最通用 | 過重、對純 semver 場景多餘 |
| 允許多版本（hoisting） | npm v3+ | 解決鑽石依賴 | 磁碟/bundle 膨脹、依賴多版本的行為難預測 |

## 踩雷集錦

1. **「PURL 就夠了，不需要存 CPE」——錯誤直覺**

   正確認識是：漏洞比對的兩條主要路徑是「查 NVD」（需要 CPE）和「查 OSV」（用生態原生名稱，接近 PURL）。只存 PURL 的 SBOM 在查 NVD 時需要即時做 PURL→CPE 推導，準確度受映射資料庫品質限制；最好的設計是同時存兩者，並記錄 CPE 的信心度。

2. **「lockfile 釘住了就代表環境是確定的」——部分正確但有盲點**

   正確認識是：lockfile 釘住了**版本**，但沒有釘住 binary artifact 的 integrity。一個 npm 套件的 `1.2.3` 版在不同時間點 `npm install` 可能得到不同的 binary（如果 registry 上的 tarball 被替換了）。真正的可重現性需要 lockfile + checksum + 必要時 artifact registry 的 content-addressable 儲存。

3. **「版本範圍比對很簡單：把 version 和 range 做個比較」——錯誤直覺**

   正確認識是：各生態的 range 語意不同（semver caret 在 `0.x` 下的行為、PEP 440 的 `~=`、Go 的偽版本），跨生態統一比對是非平凡問題。NVD 的 CPE 用「less than」描述影響範圍，但這個「less than」的語意是哪個生態的版本排序語意？——NVD 沒有明確說，實際上靠比對工具自己猜。

4. **「PubGrub 是 Natalie Weizenbaum 的一篇論文」——需要更精確**

   正確認識是：PubGrub 以技術部落格文章（Medium，2018 年）發表，不是會議論文。它有完整的形式化推導，但不是同儕審查的學術論文。演算法基礎來自 Gebser 等人對 clasp SAT solver 的研究（《Answer Set Solving in Practice》）。引用時要準確。

5. **「SBOM 記錄的是用戶宣告的依賴」——錯誤直覺**

   正確認識是：SBOM（尤其是 build-time 或 binary 生成的）記錄的是**求解結果**：所有被實際引入的元件，包括傳遞依賴。用戶宣告的只是直接依賴和版本約束。這兩個不同，而 SBOM 的用途（漏洞掃描、SCA）需要的是求解結果，不是用戶宣告。

## 進階：再往深一層

### PubGrub 的錯誤訊息品質

PubGrub 的一個被低估的優點是：當求解失敗時，它能給出**人類可讀的衝突解釋**，因為不相容性的推導鏈完整保存了「為什麼這個組合不可能成立」的推理過程。

傳統 backtracking solver 只能說「無解」或給出一個大多數人看不懂的版本約束列表；PubGrub 能說：

```
由於 root 依賴 A@^1.0.0，以及 A@1.x 依賴 C@^2.0.0，
而 B@2.x 依賴 C@^1.0.0（與 ^2.0.0 不相容），
因此 root 不能同時依賴 A@^1.0.0 和 B@^2.0.0。
```

這對用戶解決依賴衝突的體驗影響很大。對 SBOM 系統設計者而言，這個特性也有啟發：如果你的系統要提供「為什麼這個元件不能升級」的解釋，需要保留求解過程的推理鏈，不是只存結果。

### Go MVS 的哲學差異

Go module 的 MVS（Minimum Version Selection，由 Russ Cox 設計）代表了和 PubGrub 不同的哲學：

```
MVS 的核心選擇規則：
  每個 package 選「所有直接或傳遞 requirement 要求的版本中，最小的那個」

例：
  root 要求 A@≥1.2
  A@1.2 要求 B@≥1.0
  root 也要求 B@≥1.5

  MVS 選擇：A@1.2（最小滿足 root 的版本），B@1.5（max(1.0, 1.5)）

這個過程是線性時間的，且是確定性的。
```

MVS 的代價是：它不允許版本降級（你加的 dependency 只會讓整個解往版本號大的方向走），也不允許多版本共存。在一個有強大向後相容承諾的生態（Go 的 module compatibility promise）下這是合理的；在一個 breaking change 頻繁的生態（npm 的 `0.x`）下會製造問題。

### identifier 設計的研究前沿

2024 年以後，識別問題的研究前沿已從「如何標準化 PURL」移向：

- **跨生態 canonical identity**：同一個 upstream source 在不同 distro 打成不同 package 名，能否自動建立 canonical mapping？目前沒有開放的、自動維護的標準解答。
- **binary-level fingerprinting**：從 binary 提取唯一識別符（不靠 manifest），方法有 ELF 特徵、符號集指紋、function-level hash——Ch 32 會深挖。
- **AI-assisted CPE matching**：用 LLM 或 embedding 把 package name 對應到 CPE——準確度有改善但仍有 false positive。

## 動手練習

這章是理論章，練習以「在紙上設計 / 在腦中推導」為主：

1. **版本求解手推**：給定以下依賴關係，手動找出一個滿足所有約束的版本賦值，或證明無解：
   ```
   root → A@^1.0, B@^2.0
   A@1.2 → C@^1.5
   A@1.5 → C@^2.0
   B@2.1 → C@^1.3, < 2.0
   B@2.3 → C@^2.0
   C 可用版本：1.3.0, 1.7.0, 2.1.0
   ```
   （提示：先列出所有 (A version, B version) 組合，再看各組合對 C 的要求是否有交集）

2. **identifier 對齊分析**：取你自己的一個 Python 或 Node 專案，用 `pip list` 或 `npm list` 列出所有安裝的套件，選其中一個（最好是有 CVE 記錄的），找出：
   - 它的 PURL（根據 ECMA-427 的格式自己寫）
   - NVD 上它的 CPE（去 nvd.nist.gov 搜它的名字）
   - 這兩個 identifier 的 vendor/product/version 對應得上嗎？如果對不上，原因是什麼？

3. **讀 PubGrub 部落格**：閱讀 Natalie Weizenbaum 的原文（https://nex3.medium.com/pubgrub-2fb6470504f），找出：
   - 「不相容性（incompatibility）」的定義
   - 「單位傳播（unit propagation）」的直覺解釋
   - 它和傳統 backtracking 的關鍵差異是什麼

## 本章重點整理

- **命名問題**：PURL 是生態原生（精確但不跨生態）、CPE 是漏洞 DB 語言（覆蓋廣但不精確）、SWID 是企業 IT 標準（在開源生態幾乎不存在）。三者的對齊是結構性難題，不能自動精確完成。
- **版本求解**：版本選擇是 NP-complete 問題（Russ Cox 2016 年的 Version SAT 結果），但實際生態中啟發式求解器能快速工作。PubGrub（Natalie Weizenbaum，Dart/pub 起源，2018）用 CDCL 技術避免指數爆炸，現被 uv 採用，也是 Cargo 指定的下一代 solver 基礎。
- **lockfile 語意**：lockfile 是版本求解的結果，是確定性的版本賦值。SBOM 記錄的傳遞依賴反映了求解結果，在不同時間點可能不同（依賴更新後重新求解）。
- **對 SBOM 設計的啟發**：identifier 設計要存多個（PURL + CPE），版本要保留原始格式，依賴圖結構要保留（不能只存平鋪清單），多版本共存要能表達。

## 自我檢核

- [ ] 我能說出 PURL 的七個組成部分，以及 `namespace` 在不同 type 下的語意差異
- [ ] 我能說出 CPE 2.3 的格式，以及為什麼 `vendor`/`product` 欄位是最大問題所在
- [ ] 我能解釋「版本求解是 NP-complete」的直覺（不需要知道完整的歸約，但要知道它跟 SAT 的關聯）
- [ ] 我能解釋 PubGrub 的 CDCL 思想比 backtracking 好在哪裡
- [ ] 我能說出鑽石依賴的形態，以及 npm 和 Go 各自用什麼不同策略處理它

## 延伸閱讀

- **[ECMA-427 Package-URL 規範](https://ecma-tc54.github.io/ECMA-427/)** — PURL 的正式標準，2025 年 12 月第一版
  - **讀哪裡**：Section 3（Specification）中 scheme 各部分的正式定義，以及 Appendix 裡的 type 定義（每個生態的 namespace 語意）
  - **和本章的關聯**：本章的語法描述都以此為準；特別注意各 type 下 namespace 的語意，是「生態原生」原則最具體的體現

- **[Russ Cox: Version SAT](https://research.swtch.com/version-sat)**（2016）
  - **讀哪裡**：全文不長；特別注意他把 boolean 變數編碼成 package 版本的那段歸約
  - **和本章的關聯**：這是「版本求解是 NP-complete」論斷的來源；讀完你能用自己的話重述那個歸約的思路

- **[Natalie Weizenbaum: PubGrub: Next-Generation Version Solving](https://nex3.medium.com/pubgrub-2fb6470504f)**（Medium，2018）
  - **讀哪裡**：全文。重點是 incompatibility 的定義、unit propagation 的例子、以及「conflict resolution」那節的推導
  - **和本章的關聯**：這是 PubGrub 演算法的原始描述，配合本章的形式化理解，是設計版本求解器前必讀的第一手資料

- **[dart-lang/pub: doc/solver.md](https://github.com/dart-lang/pub/blob/master/doc/solver.md)** — Dart pub 求解器的技術文件
  - **讀哪裡**：「Terms and Incompatibilities」和「The Algorithm」兩節
  - **和本章的關聯**：這是 PubGrub 最接近參考實作的文件，比部落格文章更形式化，有助於理解「不相容性推導」的具體機制

- **[NIST IR 7695: CPE Naming Specification 2.3](https://csrc.nist.gov/pubs/ir/7695/final)**（2011）
  - **讀哪裡**：Section 5（格式定義）和 Section 6（範例）
  - **和本章的關聯**：CPE 2.3 的一次性準確閱讀；特別注意 `*`（任意）和 `NA`（不適用）的語意差異，以及 edition 欄位的歷史包袱

- **[OSV Schema 規範](https://ossf.github.io/osv-schema/)** — Google/OpenSSF 的漏洞格式，是 CPE 的現代替代方案
  - **讀哪裡**：`affected` 字段的定義，特別是它怎麼用 `package.ecosystem` + `package.name` 取代 CPE 的 vendor/product
  - **和本章的關聯**：對比 OSV 和 NVD/CPE 的設計選擇，能具體理解本章「identifier 設計決定關聯能力」的論點

→ [Ch 32 生成引擎的架構：從 manifest 到二進位識別](./32-generation-engine-architecture.md)
