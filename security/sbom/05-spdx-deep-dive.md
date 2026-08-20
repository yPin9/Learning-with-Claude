# Ch 5 — SPDX 深挖

> **目標**：拆解 SPDX（Software Package Data Exchange）的每個欄位在說什麼、為什麼這樣設計。從真實的 syft 輸出出發，讀懂 Document Creation Info、Packages、Files、Relationships 四個核心區塊；區分 tag-value 與 JSON 兩種序列化；搞清楚 SPDX 2.3 是目前工具主流、SPDX 3.0 發布後架構大改但生態尚未跟上的現況。

## 為什麼需要這個？

SPDX 是 SBOM 最老牌、最正式的格式之一。由 Linux Foundation 主導，2021 年成為 ISO/IEC 5962 國際標準（目前版本對應 SPDX 2.2）。你在法規、採購合約、政府要求裡看到的 SBOM 規範，通常直接引用 SPDX——美國 NTIA 的 minimum elements 文件就把它列為首選格式之一。

問題是，很多人把 SPDX 當成一個「會自動跑出來的東西」——`syft -o spdx-json` 按下去，丟給對方，就算交差。這讓你完全不知道為什麼某個欄位是 `NOASSERTION`、為什麼 `licenseConcluded` 跟 `licenseDeclared` 不一樣、為什麼 `relationships` 裡有 CONTAINS 又有 DEPENDENCY_OF。

SPDX 格式本身是有設計意圖的，讀懂它，才能判斷一份 SBOM 什麼時候在說真話、什麼時候在敷衍了事。

## 先建立直覺

SPDX 文件的整體結構是**一個帶有關係圖的清單**。最高層次看：

```
SPDX Document
├── Document Header          ← 這份 SBOM 本身是什麼
│   ├── spdxVersion          ← 格式版本
│   ├── dataLicense          ← SBOM 這份文件本身的授權（注意！不是元件的授權）
│   ├── SPDXID               ← 這份文件的唯一 ID
│   ├── documentNamespace    ← 全域唯一 URI
│   └── creationInfo         ← 誰/何時產的
│
├── Packages[]               ← 元件清單（每個 package 是一個元件）
│   ├── name, version
│   ├── SPDXID               ← 文件內唯一 ID（用於 Relationships 引用）
│   ├── licenseConcluded     ← 工具「認定」的授權
│   ├── licenseDeclared      ← 元件宣告的授權
│   ├── externalRefs[]       ← CPE / PURL（讓外部工具識別這個元件）
│   └── checksum             ← hash
│
├── Files[]                  ← 個別檔案的資訊（可選，取決於 depth）
│
└── Relationships[]          ← 元件之間（和文件之間）的有向邊
    ├── DESCRIBES            ← 文件描述哪個 package
    ├── CONTAINS             ← 一個 package 包含某個 file
    ├── DEPENDENCY_OF        ← A 是 B 的依賴
    └── DEPENDS_ON           ← A 依賴 B
```

這個結構的核心設計哲學：**所有元件都是節點（用 SPDXID 識別），所有關係都是有向邊**。SPDX 不是一個簡單的列表，它是一個帶標注的有向圖，只是序列化成 JSON/tag-value 的時候把圖拆成節點清單 + 邊清單。

## 產出真實的 SPDX 輸出

我們用 syft 對 alpine:3.19 產出 SPDX JSON：

```bash
# 在 WSL 裡
DOCKER_CONFIG=/tmp/noconfig syft registry:alpine:3.19 -o spdx-json > /tmp/alpine.spdx.json
```

產出檔案大小約 90 KB，包含 16 個 packages、80 個 files、131 條 relationships（relationship 分佈：CONTAINS 95、DEPENDENCY_OF 20、OTHER 15、DESCRIBES 1）。接下來逐區塊拆解。

## Document Header 拆解

```bash
$ jq '{spdxVersion,dataLicense,SPDXID,name,documentNamespace,creationInfo}' /tmp/alpine.spdx.json
```

真實輸出：

```json
{
  "spdxVersion": "SPDX-2.3",
  "dataLicense": "CC0-1.0",
  "SPDXID": "SPDXRef-DOCUMENT",
  "name": "alpine",
  "documentNamespace": "https://anchore.com/syft/image/alpine-1583c935-a2d9-404f-a7f7-234f3fd0fbe1",
  "creationInfo": {
    "licenseListVersion": "3.28",
    "creators": [
      "Organization: Anchore, Inc",
      "Tool: syft-1.51.0"
    ],
    "created": "2026-08-17T11:34:49Z"
  }
}
```

逐欄位說清楚：

**`spdxVersion`**：`SPDX-2.3` 是目前（2024–2026）工具生態的主流版本。不是最新版（SPDX 3.0 在 2024 年 4 月發布），但 3.0 的工具支援尚未成熟，實際生產環境幾乎全在 2.3。

**`dataLicense`**：永遠是 `CC0-1.0`，這是 SPDX 規範規定死的。注意這是 **SBOM 文件本身的授權**，不是裡面元件的授權。CC0 的意思是「你可以隨意使用這份 SBOM，不需要署名」——因為 SBOM 作為基礎設施資料，若有版權限制會破壞自動化工具鏈的可用性。

**`SPDXID: "SPDXRef-DOCUMENT"`**：規範要求文件本身必須有 SPDXID，且一定是 `SPDXRef-DOCUMENT`，用於 Relationships 裡 `DESCRIBES` 關係的出發點。

**`documentNamespace`**：必須全域唯一的 URI。syft 用 UUID 確保唯一性。這個欄位的意義在於：如果兩份 SBOM 最後要合併（merge），或者一份 SBOM 引用另一份（external document reference），這個 URI 就是識別身份的依據。沒有它，你沒辦法做跨 SBOM 的關係引用。

**`creationInfo`**：
- `licenseListVersion`：syft 用的 SPDX license list 版本（3.28），決定它認識哪些 license 短代碼。
- `creators`：誰產的。格式固定：`Organization:` / `Tool:` / `Person:` 三種前綴。工具名帶版本讓你事後知道是哪個版本的 syft 掃的——這對「同一份 code，半年後重掃結果不一樣」的除錯很重要。
- `created`：UTC 時間戳，ISO 8601 格式。

## Package 欄位拆解

```bash
$ jq '.packages[0]' /tmp/alpine.spdx.json
```

真實輸出（alpine-baselayout 這個 apk 套件）：

```json
{
  "name": "alpine-baselayout",
  "SPDXID": "SPDXRef-Package-apk-alpine-baselayout-29e49df2485f6aa9",
  "versionInfo": "3.4.3-r2",
  "supplier": "Person: Natanael Copa (ncopa@alpinelinux.org)",
  "originator": "Person: Natanael Copa (ncopa@alpinelinux.org)",
  "downloadLocation": "https://git.alpinelinux.org/cgit/aports/tree/main/alpine-baselayout",
  "filesAnalyzed": true,
  "packageVerificationCode": {
    "packageVerificationCodeValue": "6a22bff30e2aed347029eeb9d51c810613705455"
  },
  "sourceInfo": "acquired package info from APK DB: /lib/apk/db/installed",
  "licenseConcluded": "NOASSERTION",
  "licenseDeclared": "GPL-2.0-only",
  "copyrightText": "NOASSERTION",
  "description": "Alpine base dir structure and init scripts",
  "externalRefs": [
    {
      "referenceCategory": "SECURITY",
      "referenceType": "cpe23Type",
      "referenceLocator": "cpe:2.3:a:alpine-baselayout:alpine-baselayout:3.4.3-r2:*:*:*:*:*:*:*"
    },
    {
      "referenceCategory": "PACKAGE-MANAGER",
      "referenceType": "purl",
      "referenceLocator": "pkg:apk/alpine/alpine-baselayout@3.4.3-r2?arch=x86_64&distro=alpine-3.19.9"
    }
  ]
}
```

關鍵欄位解說：

**`SPDXID`**：這是 Package 在文件內的唯一識別子。syft 的命名規則是 `SPDXRef-Package-{type}-{name}-{hash}`。這個 ID 是 Relationships 裡邊的「指針」——所有說「A 包含 B」「A 依賴 B」的關係，都靠這個 ID 引用。

**`filesAnalyzed: true`**：代表這個 package 的檔案被逐一分析了（計算了 hash、找到對應的 Files section 條目）。`false` 則代表「我知道有這個套件，但沒有分析它的內容」——整個 image 的 root package（alpine 本身）就是 `filesAnalyzed: false`。

**`packageVerificationCode`**：根據這個 package 所有已知檔案的 SHA-1 計算出來的「指紋」，讓接收方可以驗證清單沒被竄改。計算方法是 SPDX 規範定義的（把每個檔案 SHA-1 排序後算雜湊），不是任何一個檔案的 hash。

**`licenseConcluded` vs `licenseDeclared`**：這是最常搞混的一對欄位。
- `licenseDeclared`：套件自己宣告的授權（從 `PKGINFO`、`package.json` 等元資料讀來的）。這裡是 `GPL-2.0-only`——apk 資料庫裡就這樣寫。
- `licenseConcluded`：**掃描工具或人工審查「認定」的授權**。syft 沒有做人工審查，無法對每個套件下確定性判斷，所以填 `NOASSERTION`——意思是「我不斷言這個授權，請你自行審查」。如果是法務確認過的，應該填 `GPL-2.0-only`。
- 這個設計反映 SPDX 的立場：工具能讀到宣告，但「這份 SBOM 的發行者確認這個授權是正確的」需要人的介入。

**`sourceInfo`**：非必填，但 syft 很貼心地告訴你這個 package 的資訊從哪裡讀來的（`/lib/apk/db/installed`），方便除錯「為什麼它認到這個」。

**`externalRefs`**：元件的外部識別符，是連接 SBOM 和外部生態的橋梁：
- `SECURITY + cpe23Type`：CPE 2.3 格式，用於比對漏洞資料庫（NVD 用 CPE）
- `PACKAGE-MANAGER + purl`：PURL 格式，用於比對套件生態（grype/OSV 用 PURL）

## Relationships 拆解

```bash
$ jq '[.relationships[] | select(.relationshipType == "DESCRIBES")]' /tmp/alpine.spdx.json
```

輸出：

```json
[
  {
    "spdxElementId": "SPDXRef-DOCUMENT",
    "relatedSpdxElement": "SPDXRef-DocumentRoot-Image-alpine",
    "relationshipType": "DESCRIBES"
  }
]
```

```bash
$ jq '[.relationships[] | .relationshipType] | unique' /tmp/alpine.spdx.json
```

輸出：`["CONTAINS", "DEPENDENCY_OF", "DESCRIBES", "OTHER"]`

SPDX 定義了超過 30 種 relationship types，這份 alpine SBOM 用到四種：

| 關係類型 | 語意 | 這份 SBOM 中的例子 |
|----------|------|-------------------|
| `DESCRIBES` | 文件描述某個 package | Document → Image root package |
| `CONTAINS` | A 包含 B（通常是 package 含 file） | Package → 它的每個 file |
| `DEPENDENCY_OF` | A 是 B 的依賴（注意方向：B 依賴 A） | alpine-baselayout-data → alpine-baselayout |
| `DEPENDS_ON` | A 依賴 B（方向相反） | 較少用，方向容易混淆 |

方向很重要：`DEPENDENCY_OF` 的語意是「spdxElementId 是 relatedSpdxElement 的依賴」，讀起來是「A is dependency of B」，所以 A 在前、B 在後。很多人第一次看會把方向搞反。

## Files section

Files section 是每個被分析的檔案的詳細記錄。alpine 這份 SBOM 有 115 個 file 條目，都是各 apk 套件安裝的具體檔案：

```bash
$ jq '.files[0]' /tmp/alpine.spdx.json
```

真實輸出：

```json
{
  "fileName": "etc/apk/keys/alpine-devel@lists.alpinelinux.org-4a6a0840.rsa.pub",
  "SPDXID": "SPDXRef-File-...alpine-devel-lists.alpinelinux.org-4a6a0840.rsa.pub-0ad37c888b84d51a",
  "fileTypes": ["TEXT"],
  "checksums": [
    {
      "algorithm": "SHA1",
      "checksumValue": "3af08548ef78cfdedcf349880c2c6a1a48763a0e"
    },
    {
      "algorithm": "SHA256",
      "checksumValue": "9c102bcc376af1498d549b77bdbfa815ae86faa1d2d82f040e616b18ef2df2d4"
    }
  ],
  "licenseConcluded": "NOASSERTION",
  "licenseInfoInFiles": ["NOASSERTION"],
  "copyrightText": "NOASSERTION",
  "comment": "layerID: sha256:0b44b2151d78267ab6f2c76208c3be18688f49b2b0afd6852a9533f2cce121c5"
}
```

`comment` 裡的 `layerID` 是 syft 自己加的——讓你知道這個檔案在 container image 的哪一層，方便除錯 Dockerfile 的哪個指令產生了哪些檔案。這是 syft 擴充的資訊，不是 SPDX 規範本身的欄位。

## 底層機制：SPDX 的資料模型設計

```
SPDX 2.3 資料模型（簡化）

  ┌──────────────────────────────────────────────┐
  │              SPDX Document                   │
  │                                              │
  │  ┌──────────┐     ┌──────────┐              │
  │  │ Package  │─────│ Package  │  ← 都是節點   │
  │  │  (node)  │     │  (node)  │              │
  │  └────┬─────┘     └─────┬────┘              │
  │       │  CONTAINS        │ DEPENDENCY_OF     │
  │       ▼                  ▼                   │
  │  ┌──────────┐     ┌──────────┐              │
  │  │   File   │     │ Package  │  ← 邊是有向的 │
  │  │  (node)  │     │  (node)  │              │
  │  └──────────┘     └──────────┘              │
  │                                              │
  │  Relationships[]                             │
  │  (from: SPDXID → to: SPDXID, type: enum)    │
  └──────────────────────────────────────────────┘
```

SPDX 2.x 的資料模型相對直觀：三種一等公民（Package、File、Snippet）加上它們之間的有向關係。一個節點可以同時是多條邊的出發點和終點。

**SPDX 2.x 的兩種序列化格式**：

**Tag-value（`.spdx`）**：
```
SPDXVersion: SPDX-2.3
DataLicense: CC0-1.0
SPDXID: SPDXRef-DOCUMENT
DocumentName: alpine
DocumentNamespace: https://anchore.com/syft/image/alpine-f7ee...

PackageName: alpine-baselayout
SPDXID: SPDXRef-Package-apk-alpine-baselayout-29e49df2485f6aa9
PackageVersion: 3.4.3-r2
PackageDownloadLocation: https://git.alpinelinux.org/...
FilesAnalyzed: true
PackageLicenseConcluded: NOASSERTION
PackageLicenseDeclared: GPL-2.0-only
ExternalRef: PACKAGE-MANAGER purl pkg:apk/alpine/alpine-baselayout@3.4.3-r2?arch=x86_64&distro=alpine-3.19.9
```

Tag-value 是人可以直接讀的，每一行是 `Tag: Value`。但它無法表達巢狀結構，也不適合機器解析。大型文件讀起來像一個超長的設定檔。

**JSON（`.spdx.json`）**：結構化物件，工具友善，是 syft 的預設 spdx 輸出格式，也是本章用的格式。規範也定義了 YAML 和 RDF/XML 序列化，但工具生態幾乎都用 JSON。

## SPDX 3.0：架構大改，但現況要冷靜看

2024 年 4 月，SPDX 3.0 發布。這是一次真正的破壞性改版，資料模型從根本重構：

**3.0 的核心改變**：

| 面向 | SPDX 2.3 | SPDX 3.0 |
|------|----------|----------|
| 資料模型 | Package / File / Snippet 三種節點 | 統一的 `Element` 基底類別，所有東西都是 Element |
| 識別體系 | SPDXID（文件內唯一） | `spdxId` 改為全域 URI |
| 序列化 | tag-value / JSON / RDF/XML / YAML | 主推 JSON-LD（捨棄 tag-value） |
| Profile 架構 | 無（一張規範涵蓋一切） | 模組化 profile：Core / Software / Security / Licensing / AI / Dataset / Build / Lite |
| 授權框架 | Annex D license expression | 拆成 Simple Licensing 和 Expanded Licensing 兩個獨立 profile |
| 標準化 | ISO/IEC 5962（2021，對應 SPDX 2.2） | 提交 ISO 更新（3.0 於 2024 發布） |

**Profile 的意義**：3.0 之前，一份 SPDX 文件必須遵循整份規範（幾百頁）。3.0 之後，你可以說「我這份 SBOM 只用 Core + Software Profile」，接收工具也只需要實作它關心的 profile。這讓 SPDX 能涵蓋 AI-BOM、Hardware-BOM 等更廣的用途，而不是全靠單一文件格式撐。

**現實是：3.0 的工具生態還沒跟上**。syft 1.51.0（本課環境）產出的是 `SPDX-2.3`，不是 3.0。大部分消費 SBOM 的工具（grype、Dependency-Track 等）都還在 2.3。如果你的法規或合約要求「SPDX」，99% 的情況預期的是 SPDX 2.3。SPDX 3.0 的落地需要整個生態一起動，預計 2025–2026 年才會看到真實部署。**學 SPDX 先學透 2.3，再了解 3.0 改了什麼，這個順序是對的。**

## 對比與取捨

| 面向 | 選擇 | 理由 |
|------|------|------|
| 序列化格式 | spdx-json | 工具鏈最友善，jq 可直接切 |
| 序列化格式 | spdx-tag-value | 人工閱讀、git diff 追蹤比較清楚；但有換行/空白的解析坑 |
| 詳細程度 | `filesAnalyzed: true` | 最完整，但檔案數多時 SBOM 很肥（alpine 這個小 image 就 90KB） |
| 詳細程度 | `filesAnalyzed: false` | 只記錄 package 不記錄 file，輕量，NTIA minimum elements 允許這樣 |
| 關係類型 | DESCRIBES + CONTAINS | 描述 package 包含哪些 file，是結構性關係 |
| 關係類型 | DEPENDS_ON / DEPENDENCY_OF | 描述依賴關係，來源要有 dependency graph（package manager 的 lock file） |

## 踩雷集錦

1. **「`dataLicense: CC0-1.0` 代表裡面的元件都是 CC0」**：完全錯。`dataLicense` 是 SBOM 文件本身的授權，不是元件的授權。CC0 讓任何人都能使用這份 SBOM 而不需署名，但 Package 的授權要看 `licenseDeclared`。

2. **「`licenseConcluded: NOASSERTION` 代表這個套件沒有授權資訊」**：不對。`NOASSERTION` 是明確的宣告：「工具不對授權下判斷」。`NONE` 才是「這個套件確實沒有任何授權」。`NOASSERTION` 代表不確定，`NONE` 代表確定沒有，兩者語意完全不同。syft 在沒有做人工授權審查時都填 `NOASSERTION`，這是正確行為，不是 bug。

3. **「SPDX 3.0 已經是標準了，用 2.3 就是落後」**：SPDX 3.0 在 2024 年 4 月發布，但工具生態跟上需要時間。syft、grype、Dependency-Track 在 2026 年初的主流版本仍輸出/接受 SPDX 2.3。法規（NTIA、FDA、EU CRA）目前接受的格式描述也是 2.x 行為。追求最新沒錯，但如果你把 3.0 格式交給一個只懂 2.3 的消費工具，對方讀不懂，比 2.3 更慘。

4. **「`DEPENDENCY_OF` 和 `DEPENDS_ON` 是一樣的」**：方向完全相反。`A DEPENDENCY_OF B` = B 依賴 A；`A DEPENDS_ON B` = A 依賴 B。SPDX 規範同時定義了兩個方向，讓你可以從任一端出發描述關係。方向搞錯，dependency graph 就整個翻轉。

5. **「`documentNamespace` 不重要，隨便填」**：如果你要跨文件引用（一份 SBOM 引用另一份的 package），`documentNamespace` 是識別依據，填一樣的就衝突了。syft 用 UUID 確保唯一性是正確做法，你自己產生的 SBOM 也要有唯一 namespace。

## 進階：再往深一層

### packageVerificationCode 的計算

SPDX 規範（Annex H）定義了 `packageVerificationCode` 的計算方式：把這個 package 的所有已知檔案的 SHA-1 值收集起來，排序後拼接，再算一次 SHA-1。這讓接收方可以「驗證清單的自我一致性」——如果 SBOM 裡聲稱有某個檔案但 hash 對不上，接收方能發現。注意：這不等於驗證 SBOM 文件沒被篡改（那需要 Ch 21 的 cosign 簽章），只是驗證 SBOM 內部自洽。

### External Document Reference

SPDX 支援一份文件引用另一份文件的 package，語法是：

```
ExternalDocumentRef: DocumentRef-somelib https://example.com/somelib.spdx.json SHA256:abc123...
```

然後在 Relationships 裡用 `DocumentRef-somelib:SPDXRef-Package-foo` 跨文件引用。這個機制讓大型組織可以把每個 library 的 SBOM 單獨維護，再組合成應用層級的 SBOM，而不是把所有東西塞進一個巨大檔案。

### SPDX 3.0 的 JSON-LD 序列化

SPDX 3.0 主推 JSON-LD，讓 SPDX 資料可以直接對接語意網路（RDF）。一份 SPDX 3.0 JSON-LD 文件長這樣（概念示意，**非 syft 輸出，以規範為準**）：

```json
{
  "@context": "https://spdx.org/rdf/3.0.1/spdx-context.jsonld",
  "@graph": [
    {
      "type": "software_Package",
      "spdxId": "https://example.org/sbom/pkg-foo",
      "name": "foo",
      "software_packageVersion": "1.2.3"
    }
  ]
}
```

`spdxId` 從文件內的本地 ID 變成全域 URI，這是 3.0 最大的模型改變之一。目前（2026 年初）syft 不支援輸出 SPDX 3.0 格式，上面是概念示意。

## 動手練習

1. 跑 `jq '.packages | length' /tmp/alpine.spdx.json`，然後跑 `jq '.files | length' /tmp/alpine.spdx.json`。數字是 16 和 115，想想為什麼 package 有 16 個（15 個 apk 套件 + 1 個 image root）、file 有 115 個（遠多於 package 數）。

2. 找出所有 `licenseDeclared` 含有複合授權表達式（有 `AND`/`OR`）的 package：
   ```bash
   jq '[.packages[] | select(.licenseDeclared | test("AND|OR")) | {name, licenseDeclared}]' /tmp/alpine.spdx.json
   ```
   看看 `ca-certificates-bundle`（`MPL-2.0 AND MIT`）和 `musl-utils`（`MIT AND BSD-2-Clause AND GPL-2.0-or-later`），思考為什麼一個套件會有多個授權。

3. 把 alpine 的 SBOM 也用 tag-value 格式產一份：
   ```bash
   DOCKER_CONFIG=/tmp/noconfig syft registry:alpine:3.19 -o spdx-tag-value > /tmp/alpine.spdx.tv
   head -30 /tmp/alpine.spdx.tv
   ```
   比較同一個 package 在 tag-value 和 JSON 裡的表達差異，哪個比較容易用 `grep` 搜尋？哪個比較容易用程式解析？

## 本章重點整理

- SPDX 文件 = Document Header + Packages[] + Files[] + Relationships[]，是一個帶有向邊的節點圖。
- `licenseConcluded` 是工具/人認定的授權，`licenseDeclared` 是元件宣告的授權；syft 沒做人工審查所以 `licenseConcluded` 全部是 `NOASSERTION`，這是正確行為。
- `NOASSERTION` 和 `NONE` 語意不同：前者是「不確定」，後者是「確定沒有」。
- Relationships 是有向邊，`DEPENDENCY_OF` 和 `DEPENDS_ON` 方向相反，不要搞混。
- SPDX 2.3 是目前工具生態主流；SPDX 3.0（2024 年 4 月）改用模組化 Profile + JSON-LD，但工具支援尚未成熟，實際生產用 2.3。
- tag-value 人讀友善，JSON 工具友善，選 JSON 如果你要自動化處理。

## 自我檢核

- [ ] 我能說出 `documentNamespace` 為什麼必須是全域唯一 URI
- [ ] 我能解釋 `licenseConcluded` 和 `licenseDeclared` 的差別，以及為什麼 syft 輸出 `NOASSERTION` 是正確的而不是 bug
- [ ] 我能從 relationships 陣列裡找出 `DESCRIBES` 那條邊，並說明它在說什麼
- [ ] 我能說出 SPDX 3.0 和 2.3 的三個主要架構差異
- [ ] 給我一條 `DEPENDENCY_OF` 關係，我能正確說出誰依賴誰

## 延伸閱讀

- **[SPDX 2.3 規範](https://spdx.github.io/spdx-spec/v2.3/)**（SPDX 官方）
  - **讀哪裡**：Chapter 3（Package Information）和 Annex D（License Expressions）；前者定義每個欄位的語意和必填性，後者定義本章沒有深挖的 license expression 語法（AND/OR/WITH）
  - **為什麼值得讀**：規範本身其實寫得很清楚，每個欄位都有「Cardinality（必填/選填）」和「Value Format」，碰到「這個欄位到底允許填什麼」時直接查

- **[SPDX 3.0.1 規範](https://spdx.github.io/spdx-spec/v3.0.1/)**（SPDX 官方）
  - **讀哪裡**：Introduction 那章，說清楚 3.0 為什麼這樣設計、Profile 的概念、與 2.x 的主要差異
  - **為什麼值得讀**：不需要讀完，但至少知道 3.0 改了什麼，才能在有人問你「我們應該用 3.0 嗎」時給出有根據的答案

- **[NTIA SBOM Minimum Elements](https://www.ntia.gov/report/2021/minimum-elements-software-bill-materials-sbom)**（美國 NTIA）
  - **讀哪裡**：Table 1（七個 minimum elements）和 Section 3（格式建議）
  - **為什麼值得讀**：NTIA 的 minimum elements 是「一份 SBOM 法規合規的最低門檻」，對照 SPDX 的哪些欄位對應哪些 minimum element，可以判斷一份 syft 產的 SBOM 是否合規

- **[syft SPDX 輸出文件](https://github.com/anchore/syft/blob/main/internal/formats/spdx22json/README.md)**（Anchore GitHub）
  - **讀哪裡**：syft 的 SPDX 格式說明，解釋它在哪些欄位做了哪些決策（比如為什麼 `licenseConcluded` 都是 `NOASSERTION`）
  - **為什麼值得讀**：工具行為和規範之間永遠有差距，這份文件說明 syft 自己怎麼詮釋規範

- **[SPDX License List](https://spdx.org/licenses/)**（SPDX 官方）
  - **讀哪裡**：搜索你專案常用的 license 短代碼（`MIT`、`Apache-2.0`、`GPL-3.0-only`）確認拼法
  - **為什麼值得讀**：`GPL-2.0` 已不在 3.28 版的標準 list 裡（被 `GPL-2.0-only` 和 `GPL-2.0-or-later` 取代），用錯 identifier 在某些嚴格工具上會驗證失敗

下一章轉戰 OWASP 陣營，看同一份 alpine image 用 CycloneDX 格式描述出來長什麼樣，設計哲學又有哪些關鍵差異。

→ [Ch 6 CycloneDX 深挖](./06-cyclonedx-deep-dive.md)
