# Ch 3 — 最小要素與生命週期：六型 SBOM 各看到什麼

> **目標**：把 NTIA minimum elements（七個欄位，三個面向）背到能默背，並理解 CISA 六型 SBOM 各在生命週期哪個位置、看到什麼、盲點在哪。這是全課的理論骨幹——後面所有「生成」和「消費」的討論，都是在這個框架下展開的。深挖章，讀慢一點。

## 為什麼需要這個？

你可以找到幾十篇介紹 SBOM 的文章，大部分都說「SBOM 要包含元件名稱、版本、依賴關係」，然後給你一個模糊的列表。這些文章沒有告訴你：

- 官方定義是哪份文件發布的，在哪裡找到原文
- 七個欄位的名稱是什麼（很多人把 3-4 個欄位記錯）
- 「有了 minimum elements 就算 SBOM」和「能真正用於供應鏈安全的 SBOM」之間差多少
- 為什麼「Source SBOM」和「Build SBOM」是兩種不同的東西，工具輸出的到底是哪種

這章要讓你能精確回答這些問題，以及能對著一份 SBOM 說出「這份 SBOM 是哪種型別，它能告訴你什麼，不能告訴你什麼」。

## 先建立直覺：三個問題定位任何 SBOM

面對一份 SBOM，先問三個問題：

```
問題一：它在哪裡生成的？
──────────────────────────────────────────────────────
設計階段     開發中      Build 時     交付後分析    部署後      執行時
 Design     Source      Build       Analyzed    Deployed   Runtime
   SBOM      SBOM        SBOM         SBOM        SBOM       SBOM

問題二：它的資訊從哪來？
  設計文件   原始碼      Build 產物   Binary 掃描  安裝記錄   動態觀測
  /規格書   manifest    輸出 artifact  靜態分析    deployment  hook

問題三：它能回答什麼問題？
  「計畫用什麼」「宣告的依賴」「build 進去的」「binary 裡有什麼」「裝了什麼」「正在跑什麼」
```

六種型別對應六種不同的問題和不同的可信度。沒有一種是「最好的」——不同場景需要不同型別，而且它們可以組合使用（一份 SBOM 可以是 Source + Build 的組合）。

## Part A — NTIA Minimum Elements（2021 年 7 月）

### 來源與背景

2021 年 5 月，美國 EO 14028（行政命令第 14028 號《改善國家網路安全》）指示商務部發布 SBOM 的「最小要素」。NTIA（National Telecommunications and Information Administration，美國國家電信和資訊局）在 2021 年 7 月發布了《The Minimum Elements for a Software Bill of Materials》這份文件。

這份文件是 SBOM 領域最重要的單一政策文件，原文在 `ntia.gov`，後來 CISA 接手了 SBOM 相關的推動工作。以下的七個欄位和三個面向直接來自該文件（原文 Table 1 和 Section 2-4）。

> **注意**：2025 年 8 月 CISA 發布了更新版 minimum elements 的**草案**徵求公眾意見（評論期至 2025 年 10 月初），在原有基礎上新增 component hash、license、generation context 等建議欄位。本章介紹的是 2021 NTIA 版本，因為它仍是目前大部分工具和法規引用的基準；草案定案前細節可能變動，要對照最新政策請查 [cisa.gov/sbom](https://www.cisa.gov/sbom)。

### 三個面向

NTIA minimum elements 由三個相互關聯的面向組成：

```
┌─────────────────────────────────────────────────────┐
│              NTIA Minimum Elements                  │
│                                                     │
│  ┌──────────────────┐  ┌──────────────────────────┐ │
│  │   Data Fields    │  │  Automation Support      │ │
│  │   七個必填欄位   │  │  機器可讀格式             │ │
│  │                  │  │  (SPDX/CycloneDX/SWID)   │ │
│  └──────────────────┘  └──────────────────────────┘ │
│           │                         │               │
│           └─────────┬───────────────┘               │
│                     ▼                               │
│         ┌──────────────────────┐                   │
│         │  Practices &         │                   │
│         │  Processes           │                   │
│         │  操作流程與政策      │                   │
│         └──────────────────────┘                   │
└─────────────────────────────────────────────────────┘
```

### Data Fields：七個必填欄位

這是最常被引用也最常被記錯的部分。官方原文的七個欄位是：

| 欄位名稱（官方英文）| 中文意義 | 說明 |
|---|---|---|
| **Supplier Name** | 供應者名稱 | 製造或定義這個 component 的個人或組織 |
| **Component Name** | 元件名稱 | 供應者給這個 software unit 的名稱 |
| **Version of the Component** | 版本 | 供應者用來表示軟體從上個版本變動的識別符 |
| **Other Unique Identifiers** | 其他唯一識別符 | 可用來識別 component 或作為查詢鍵值的其他識別符，例如 PURL 或 CPE |
| **Dependency Relationship** | 依賴關係 | 表達 upstream component 對軟體的關係特性 |
| **Author of SBOM Data** | SBOM 資料的作者 | 建立 SBOM 資料的個體名稱（可能不同於 component 供應者） |
| **Timestamp** | 時間戳記 | SBOM 資料組合的日期和時間紀錄 |

這七個欄位的常見錯誤記法：

- **「License」不在 minimum elements 裡**：很多人以為 license 是必填的，但 NTIA 2021 版本沒有。License 是最佳實踐，不是最低要求（CISA 2025 版本才納入）。
- **「Supplier Name」不等於「Component Name」**：供應者是 `Alpine Linux Project`，元件名稱是 `busybox`。兩個欄位。不要合併。
- **「Author of SBOM Data」是生成 SBOM 的工具/人，不是軟體作者**：syft 生成的 SBOM，Author 是 `Tool: syft-1.51.0`，不是 busybox 的 developer。

從真實 SBOM 對照七個欄位（以 alpine SBOM 的 busybox 為例）：

```json
{
  "name": "busybox",                           ← Component Name
  "versionInfo": "1.36.1-r20",                ← Version
  "supplier": "Organization: Alpine Linux",   ← Supplier Name
  "externalRefs": [
    {
      "referenceType": "purl",
      "referenceLocator": "pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9"
    },
    {                                          ← Other Unique Identifiers（purl 和 cpe）
      "referenceType": "cpe23Type",
      "referenceLocator": "cpe:2.3:a:busybox:busybox:1.36.1-r20:*:*:*:*:*:*:*"
    }
  ]
}
```

而 Author of SBOM Data 和 Timestamp 在文件頂層：

```json
{
  "creationInfo": {
    "created": "2026-08-17T11:34:49Z",        ← Timestamp
    "creators": [
      "Organization: Anchore, Inc",            ← Author of SBOM Data
      "Tool: syft-1.51.0"
    ]
  }
}
```

Dependency Relationship 在 `relationships` 陣列裡（前一章詳細展示過）。

### Automation Support

NTIA 要求 SBOM 必須能機器可讀、可自動生成和消費。它列出三個被認可的機器可讀格式：

- **SPDX**（Software Package Data Exchange）：Linux Foundation 主導，ISO/IEC 5962:2021 國際標準，Part 2 的 Ch 5 展開
- **CycloneDX**：OWASP 主導，針對安全使用案例優化，Part 2 的 Ch 6 展開
- **SWID tags**（ISO/IEC 19770-2）：偏向 enterprise IT asset management，在 SBOM 生態裡使用較少，Ch 4 的 SWID 小節會說

「機器可讀」不只是「JSON 格式」。它意味著格式有正式 schema、工具能自動解析、可以跨工具互通。這排除了手工填的 Excel 表格、PDF 報告、或是只有人類讀得懂格式的文件——它們不算是符合 minimum elements 的 SBOM。

### Practices and Processes

第三個面向處理「SBOM 怎麼做」而不是「SBOM 裡有什麼」。NTIA 定義了幾個最佳實踐方向：

- **生成頻率**：每次 release 產生一份；重大安全更新後更新
- **深度**：追蹤傳遞依賴，不只是直接依賴（但「要追蹤幾層」沒有強制規定）
- **未知元件的處理**：記錄已知無法識別的元件，而不是直接略過
- **分發**：SBOM 應該可以被消費方取得，不管是隨 artifact 附上還是透過 API 提供
- **存取控制**：特定情境（如涉及敏感 IP）可以有存取限制，但不能完全不提供

## Part B — CISA 六型 SBOM：生命週期全景

### 來源

CISA 在 2023 年 4 月發布《Types of Software Bill of Materials (SBOM) Documents》。這份文件由 CISA SBOM Tooling & Implementation 工作小組草擬，定義了六種型別。以下定義直接來自該文件（PDF：`cisa.gov/sites/default/files/2023-04/sbom-types-document-508c.pdf`）。

### 六型的生命週期定位

```
軟體生命週期

 需求設計  →  開發中  →  Build CI  →  交付後  →  部署  →  執行中
     │            │           │           │          │          │
     ▼            ▼           ▼           ▼          ▼          ▼
  Design       Source       Build     Analyzed   Deployed   Runtime
   SBOM         SBOM         SBOM       SBOM       SBOM       SBOM
     │            │           │           │          │          │
  規格書/     manifest     build log    binary     系統      動態觀測
   RFP        lock file    artifacts    掃描     inventory    hook
```

### Type 1 — Design SBOM

**官方定義**：「SBOM of intended design of included components (some of which may not exist) for a new software artifact.」

**生成時機**：開發之前——從設計規格書、RFP、或初步概念文件生成。

**看到的東西**：計畫要用的元件和版本，是「預期」而非「實際」。

**盲點**：
- 元件可能根本還沒存在（still in development）
- 設計可能和最終實作不同
- 幾乎沒有工具能自動生成，主要靠手填

**什麼時候有用**：
- 採購決策前評估依賴的 license 相容性
- 在實際開發前識別已知問題的元件
- 合約/RFP 階段讓甲方審查計畫用料

**誠實評估**：在整個六型裡最少被使用、工具支援最差、可信度最低的一種。它描述的是意圖，不是事實。

### Type 2 — Source SBOM

**官方定義**：「SBOM created directly from the development environment, source files, and included dependencies used to build a product artifact.」

**生成時機**：開發過程中，從 source 環境產生。

**看到的東西**：
- `package.json` / `pom.xml` / `go.mod` / `requirements.txt` 宣告的依賴
- lock file（`package-lock.json` / `go.sum` / `Pipfile.lock`）裡的精確版本
- 如果工具夠聰明，也包含傳遞依賴

**盲點**：
- **只看到宣告的依賴，看不到 build 注入的**：很多 build tool 會在 build 過程中動態下載或注入東西（compiler plugin、build plugin），這些在 source SBOM 裡不存在
- **Lock file 和實際 build 結果可能不一致**：如果 build 環境設定不對、或有 override，lock file 裡的版本可能不是真正 build 進去的
- **看不到動態載入的元件**：runtime dynamically loaded plugins 在 source 層看不到
- **傳遞依賴可能漏掉**：工具能追的深度有限

**什麼時候有用**：
- CI/CD pipeline 的 early feedback（在 commit 時就掃問題）
- 開發者本地開發時快速確認依賴
- 配合 VCS，可以追蹤「是哪個 commit 引入了某個依賴」

**對應工具**：syft 對目錄跑（`syft dir:.`）、Dependabot、Snyk CLI

### Type 3 — Build SBOM

**官方定義**：「SBOM generated as part of the process of building the software to create a releasable artifact (e.g., executable, container image) from source files, dependencies, build components, built components, and build ephemeral data from a build process.」

**生成時機**：Build 過程中，作為 build pipeline 的一部分。

**看到的東西**：
- 實際 build 進去的所有元件（包括 build tool 注入的）
- 精確的版本（不只是宣告的，是實際使用的）
- build ephemeral data：build 環境、compiler 版本、build flags
- 可以同時涵蓋 source 和 build artifact 的資訊

**盲點**：
- **動態連結（dynamically linked）的函式庫**：binary 裡可能沒有這些的資訊，只有在 runtime 才會載入
- **runtime 才決定的版本**：如果 runtime 允許替換（dependency injection），build 時看到的版本不等於執行時用到的版本
- **需要修改 build pipeline**：不是「跑一個工具掃一下」，而是要把 SBOM 生成整合進你的 build system

**什麼時候有用**：
- 最接近「真實 artifact 裡有什麼」的 SBOM 型別
- 可以同時簽章 SBOM 和 artifact（同一個 pipeline），最高的 provenance 可信度
- 配合 SLSA framework 使用（Ch 22）

**對應工具**：
- Dockerfile 的 `--sbom` flag（BuildKit 內建）
- Gradle/Maven 的 SBOM plugin
- GitHub Actions 的 SBOM attestation action

### Type 4 — Analyzed SBOM（Binary SBOM）

**官方定義**：「SBOM generated through analysis of artifacts (e.g., executables, packages, containers, and virtual machine images) after its build.」

**生成時機**：Build 完成後，對 artifact（binary、container image、package）做靜態分析。

**看到的東西**：
- Binary 裡能被識別的元件（靠 pattern matching、資料庫比對、字串搜尋）
- Container image 的 package manager database（/var/lib/dpkg/status、/lib/apk/db/installed）
- 靜態連結的 library（有時能識別）
- 不需要 source、不需要接觸 build 環境

**盲點**：
- **識別不準確（prone to errors）**：CISA 文件明確指出「Prone to omissions, errors, or approximations if the tool ... is unable to decompose or recognize the software components precisely.」
- **混淆或 stripped binary**：如果 binary 有混淆或移除了 debug info，識別率大幅下降
- **靜態分析看不到動態行為**：runtime 才會載入的 plugin 看不到
- **隱藏依賴**：手動 `curl` 下載、或靜態連結進去但沒有識別 signature 的函式庫，可能認不出來

**什麼時候有用**：
- **Legacy 系統的 SBOM**：原始碼或 build 環境已不存在，但 binary 還在跑
- **第三方軟體審查**：你拿到的是 binary，沒有 source
- **驗證其他 SBOM**：可以用 analyzed SBOM 和 source/build SBOM 交叉比對，找差異

**syft 產出的 SBOM 主要是 Analyzed SBOM**：當你跑 `syft alpine:3.19`，它分析的是 container image 的靜態檔案系統，這是 analyzed SBOM 的典型模式。Ch 10 會把 syft 的 cataloger 機制拆開來看。

### Type 5 — Deployed SBOM

**官方定義**：「SBOM created by recording the SBOMs and configuration information of deployed instances of one or more software products in a particular system.」

**生成時機**：軟體被部署到系統上之後，記錄什麼版本裝在哪裡。

**看到的東西**：
- 系統上實際安裝的軟體及其版本（可以涵蓋多個 artifact 的 SBOM 組合）
- 部署配置（環境變數、config file 版本）
- 系統層的元件（OS、runtime、middleware 的版本）

**盲點**：
- **配置和 runtime 環境可能不反映真實行為**：安裝了不代表在執行
- **需要部署流程的配合**：要記錄部署記錄，不是事後掃描能得到的
- **某些元件可能在「不可存取的程式碼路徑」**：程式裡有 but never executed

**什麼時候有用**：
- SolarWinds 那種情境：知道哪台機器裝了哪個版本
- 合規審計：系統上安裝了什麼
- Patch management：哪些機器需要更新

**對應工具**：
- Microsoft Defender for Endpoint、CrowdStrike 等 EDR 系統的 software inventory
- Kubernetes 環境的 admission controller（記錄哪個 image 部署在哪個 pod）
- 自定義的 deployment pipeline hook

### Type 6 — Runtime SBOM

**官方定義**：「SBOM generated through instrumenting the system running the software, to capture only what is loaded and executing in memory, as well as external call-outs or dynamically loaded components.」

**生成時機**：軟體執行中，透過 instrumentation（hook 進 runtime）觀測。

**看到的東西**：
- 真正在記憶體裡載入的元件（不只是安裝的，是真的在用的）
- 動態載入的 plugin（runtime 才決定的）
- 外部呼叫（external service 依賴）
- Java 的 JVM loaded classes、Node.js 的 require()、Python 的 import

**盲點**：
- **需要長時間執行才能捕捉完整**：如果某個 code path 只有特定條件才觸發，短時間的 runtime 觀測可能漏掉
- **額外的系統負擔**：instrumentation hook 有 overhead
- **動態行為的不確定性**：不同時間觀測，看到的元件可能不同

**什麼時候有用**：
- 安全的終極目標：知道「系統正在做什麼」而不只是「系統裡有什麼」
- 識別 dead code（安裝了但從來沒 load 的依賴可以移除）
- 對抗 time-of-check/time-of-use 問題：知道 runtime 真正用的是哪個版本

**這是技術上最難的一種**，目前工具成熟度最低，在實務部署上相對少見。一些 runtime security 工具（如 Contrast Security、Datadog）有涉及這個方向。

## 六型對比表

| 維度 | Design | Source | Build | Analyzed | Deployed | Runtime |
|------|--------|--------|-------|----------|---------|---------|
| 生成時機 | 設計前 | 開發中 | Build 時 | Build 後 | 部署後 | 執行中 |
| 資料來源 | 規格/設計文件 | Manifest/lock file | Build pipeline | Binary 靜態掃描 | 部署記錄 | 動態觀測 |
| 傳遞依賴 | 依設計 | 部分 | 高 | 中（靠識別率）| 依 artifact SBOM | 僅實際載入 |
| 動態依賴 | 依設計 | 否 | 否 | 否 | 否 | 是 |
| 準確性 | 意圖非事實 | 宣告非實際 | 高 | 中（靠識別率）| 高（依賴好的 artifact SBOM）| 最高（執行事實）|
| 工具成熟度 | 低 | 高 | 中 | 高 | 中 | 低 |
| 使用場景 | 採購/規格審查 | 開發期快速反饋 | 交付物來源 | 第三方/Legacy | 系統盤點/patch | 安全監控 |
| 實作難度 | 低（手工） | 低（工具多）| 中（需改 pipeline）| 低（工具多）| 中 | 高 |
| Log4Shell 可用 | 否 | 部分（只看 manifest）| 是 | 是 | 是 | 是 |
| SolarWinds 可用 | 否 | 否 | 否 | 否 | 是 | 是 |

> **關鍵洞察**：沒有一種型別能回答所有問題。Log4Shell（「系統裡有沒有 log4j」）需要至少 Build 或 Analyzed SBOM；SolarWinds（「哪台機器裝了哪個版本的 SolarWinds」）需要 Deployed SBOM。現實中，一個成熟的 SBOM 計畫通常會組合多種型別。

## 底層機制：型別決定你能信任哪些問題的答案

用一個具體場景說明：你收到供應商的 SBOM，聲稱它列出了產品裡的所有元件。

你需要問：「這是哪種型別的 SBOM？」

```
情境 A：供應商的 SBOM 是 Source SBOM（從 package.json 讀的）

你能信任的問題：
  ✓ 「供應商宣告使用了哪些依賴？」
  ✓ 「這些宣告的依賴有沒有已知的 CVE？」

你不能靠它回答的問題：
  ✗ 「Build 時有沒有注入其他東西？」  ← 看不到 build plugin
  ✗ 「這個 binary 裡靜態連結了什麼？」  ← 不是 analyzed SBOM
  ✗ 「runtime 動態載入了什麼？」  ← 不是 runtime SBOM

情境 B：供應商的 SBOM 是 Build SBOM（CI pipeline 產出）

你能信任的問題：
  ✓ 「Build artifact 裡實際包含哪些版本的哪些元件？」
  ✓ 「這個 artifact 是哪個 pipeline 在哪個時間點產出的？」
  ✓ （如果有簽章）「這份 SBOM 有沒有被竄改？」

你不能靠它回答的問題：
  ✗ 「runtime 動態載入了什麼外部函式庫？」
  ✗ 「部署到我的系統上的是哪個版本？」
```

這個框架決定了「消費 SBOM 時，這份 SBOM 的哪些聲明可以信任、哪些需要補充驗證」。

## 踩雷集錦

**1. 「syft 給我的 SBOM 就是 Build SBOM」**

不對。`syft alpine:3.19` 分析 container image 是 Analyzed SBOM（事後 binary/image 分析），不是 Build SBOM。Build SBOM 需要在 build 過程中產生，通常是 Dockerfile 的 `--sbom` flag 或 CI pipeline 的 SBOM step。Analyzed SBOM 很好用，但它和 Build SBOM 有不同的可信度和覆蓋範圍。

**2. 「只要有 NTIA minimum elements 的七個欄位，這份 SBOM 就夠用了」**

Minimum elements 是「最低門檻」，不是「最佳實踐」。一份只有七個欄位的 SBOM 是合規的，但可能對你沒有多少實際用處：
- 沒有 hash：你沒辦法驗證元件的完整性
- 沒有 license：你沒辦法做授權合規
- 沒有 relationship（dependency graph）：你只有 flat list，不知道哪個 component 依賴哪個
- 沒有 file list：漏洞影響的是哪個具體檔案你不知道

把「達到 minimum elements」當成終點是錯的，它是起點。

**3. 「Source SBOM 和 Build SBOM 講的是同一件事」**

這是這章最重要的概念分辨。Source SBOM 讀的是 manifest（你宣告的依賴）；Build SBOM 記錄的是 build pipeline 真正 build 進去的東西。兩者之間可能存在差距：

- Maven 的 profile-based dependency：特定 build profile 才啟用的依賴，Source SBOM 可能都列出來，Build SBOM 只有被啟用的那個 profile 裡的
- Go 的 `go mod tidy` 和實際 build 不同步：go.sum 裡的依賴比 go build 實際用到的多
- `npm install --production` 和 `npm install`：只有前者對應真實 production build

**4. 「傳遞依賴太多追不完，只記直接依賴就好」**

直接依賴只是暴露面的一部分。Log4Shell 的受害者裡很多人說「我沒有用 log4j」——他們說的是直接依賴。但傳遞依賴帶進來了。如果你的 SBOM 只記直接依賴，你的漏洞掃描會有大量漏報，而且這些漏報集中在你「以為自己沒用的東西」上，這比已知有問題的東西更危險。

**5. 「Timestamp 是哪個時間？」**

常見的混淆：Timestamp 是 **SBOM 資料組合的時間**，不是 software 的 build 時間，也不是 release 時間。你在 2024 年 6 月對一個 2022 年 build 的 binary 做 analyzed SBOM，Timestamp 是 2024 年 6 月（你掃描的時間），不是 2022 年（binary build 的時間）。這個區別在 supply chain 分析裡很重要：同一個 artifact，在不同時間點掃描，因為漏洞 DB 更新，會得到不同的漏洞清單。

## 進階：再往深一層

**組合型 SBOM（composite SBOM）**：NTIA 和 CISA 都允許把多種型別的 SBOM 資訊合併進一份文件。實務上，一個完整的 SBOM program 通常這樣設計：

```
CI pipeline 生成 Build SBOM（最準確的 artifact 描述）
    +
syft 在 CI 做事後分析生成 Analyzed SBOM（交叉驗證）
    +
Deployment 記錄生成 Deployed SBOM（追蹤哪台機器裝了什麼）
    │
    ▼
三份 SBOM 都存入 Dependency-Track（Ch 17），做持續比對
```

**Timestamp 的信任問題**：如果一份 SBOM 沒有被簽章（Ch 20-21），你怎麼知道 Timestamp 是真的？你怎麼知道這份 SBOM 沒有被往回改日期？這就是為什麼 Build SBOM 的意義要配合 sigstore 的 Rekor 透明日誌才完整——不只是「我說這份 SBOM 是這個時間產出的」，而是「Rekor 的不可竄改日誌記錄了這份 SBOM 在這個時間被簽章上傳」。

**SBOM as contract**：越來越多的 software procurement 開始要求供應商提供特定型別的 SBOM 作為合約的一部分（「你必須提供 Build SBOM 而不是 Source SBOM」）。這讓 SBOM 型別的定義從技術問題變成法律問題——你交付的 SBOM 是哪種型別，在合約層面有意義。

## 動手練習

1. 對 Ch 0 生出的 `/tmp/alpine.spdx.json`，確認它是哪種型別的 SBOM：看 `creationInfo.creators` 欄位（告訴你是什麼工具生成的）和 `documentDescribes`（告訴你描述的是什麼 artifact）。根據生成方式判斷：這是 Analyzed SBOM 還是 Build SBOM？

2. 用 `syft dir:.` 對一個有 `package.json` 或 `pom.xml` 的目錄掃描（找一個 GitHub 上的開源專案克隆下來），對比用 `syft <image>` 對同一個專案的 container image 掃描，列出兩份 SBOM 的 package 數量差異。哪份 SBOM 的 component 比較多？為什麼？

3. 找出你剛才的 SBOM 裡七個 NTIA minimum elements 的對應欄位在哪裡（supplier、component name、version、other unique identifiers、dependency relationship、author of sbom data、timestamp）。如果有哪個欄位是空的或找不到，這份 SBOM 是否還算符合 minimum elements？

4. 想一個場景：如果你的公司剛好用了一個被 SolarWinds 供應鏈攻擊污染的版本，你有 Analyzed SBOM 但沒有 Deployed SBOM，你能回答什麼問題？不能回答什麼問題？

## 本章重點整理

- **NTIA minimum elements（2021）**：三個面向（Data Fields、Automation Support、Practices & Processes），Data Fields 有七個欄位：Supplier Name、Component Name、Version、Other Unique Identifiers、Dependency Relationship、Author of SBOM Data、Timestamp。License 不在七個欄位裡。
- 七個欄位是最低門檻，不是最佳實踐。缺 hash、license、完整 dependency graph 的 SBOM 是合規的但可用性差。
- **CISA 六型 SBOM（2023）**：Design、Source、Build、Analyzed、Deployed、Runtime，各對應生命週期不同位置、不同資料來源、不同盲點。
- **syft 對 image 掃描主要是 Analyzed SBOM**，不是 Build SBOM——它在 build 完成後分析 artifact。
- 沒有一種型別能回答所有問題：Log4Shell 場景需要 Build/Analyzed/Deployed；SolarWinds 場景需要 Deployed。
- Source SBOM ≠ Build SBOM：前者看宣告，後者看實際 build 產物。

## 自我檢核

- [ ] 我能不查資料默出 NTIA minimum elements 的七個欄位名稱，並知道 license 不在裡面
- [ ] 我知道七個欄位在 SPDX JSON 裡各對應哪個欄位（name/versionInfo/externalRefs/creationInfo 等）
- [ ] 我能說出六種 SBOM 型別的名稱、各自在生命週期哪個位置、主要盲點
- [ ] 我知道 `syft alpine:3.19` 產出的主要是哪種型別的 SBOM，以及原因
- [ ] 我理解為什麼 Source SBOM 和 Build SBOM 可能對同一個 artifact 給出不同的元件清單
- [ ] 我能解釋：面對一份供應商 SBOM，我要先問哪個問題才能判斷它能回答什麼問題

## 延伸閱讀

- **[NTIA「The Minimum Elements for a Software Bill of Materials」（2021 年 7 月）](https://www.ntia.gov/report/2021/minimum-elements-software-bill-materials-sbom)**（NTIA 官方）
  - **讀哪裡**：Table 1（七個 data fields 的官方名稱和描述）、Section 3（Automation Support）、Section 4（Practices）——這份文件只有 14 頁，全讀是最快的
  - **和本章的關聯**：本章 Part A 的一手來源

- **[CISA「Types of Software Bill of Materials (SBOM) Documents」（2023 年 4 月）](https://www.cisa.gov/sites/default/files/2023-04/sbom-types-document-508c.pdf)**（CISA 官方，PDF）
  - **讀哪裡**：每種型別的 Definition、Data typically presented、Strengths/Weaknesses 欄位——大概 10 頁
  - **和本章的關聯**：本章 Part B 的一手來源，值得和本章的總結表格對照

- **[CISA「2025 Minimum Elements for a Software Bill of Materials」](https://www.cisa.gov/sites/default/files/2025-08/2025_CISA_SBOM_Minimum_Elements.pdf)**（CISA 2025 更新版）
  - **讀哪裡**：和 NTIA 2021 版本的差異段落——CISA 接手後新增了哪些要求
  - **和本章的關聯**：了解現在的最新基準，比較 2021 和 2025 版本的差距

- **[CISA SBOM 官方入口](https://www.cisa.gov/sbom)**（CISA）
  - **讀哪裡**：首頁的文件清單——CISA 把所有 SBOM 相關文件（types、VEX、sharing、tooling）集中在這個入口
  - **和本章的關聯**：後面各章會多次回到這個入口，現在就標記好

下一章深挖整個 SBOM 領域最痛的問題：元件識別。同一個東西有不同名字，不同識別體系（PURL vs CPE vs SWID）各有設計取捨，而識別不準確是漏洞誤報和漏報的根源。

→ [Ch 4 元件識別的難題：naming / PURL / CPE / SWID](./04-component-identity.md)
