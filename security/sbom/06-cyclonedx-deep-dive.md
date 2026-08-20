# Ch 6 — CycloneDX 深挖

> **目標**：拆解 CycloneDX 格式的每個欄位在說什麼，理解它和 SPDX 的設計哲學差異。用真實的 syft 輸出逐段解析 bomFormat/specVersion/metadata/components/dependencies 五大區塊；搞清楚 CycloneDX 為什麼被稱為「security-first」格式，以及它的原生 vulnerabilities、VEX、compositions 支援意味著什麼；了解 ECMA-424 標準化狀態。

## 為什麼需要這個？

CycloneDX 由 OWASP（Open Worldwide Application Security Project）主導，2017 年最初作為 OWASP 孵化專案，從第一天起就是**為資安用途設計的**。它不是從授權合規出發再往資安延伸，而是反過來。這個出發點差異造成了格式在結構、欄位密度、擴充性上跟 SPDX 截然不同的選擇。

2024 年 6 月，CycloneDX v1.6 被 Ecma International 批准為 ECMA-424 第一版（1st Edition）；2025 年 12 月，v1.7 成為 ECMA-424 第二版（2nd Edition）。這讓 CycloneDX 和 SPDX（ISO/IEC 5962）一樣有正式的國際標準身份，消除了「SPDX 才是標準」的誤解。

syft 1.51.0 的 CycloneDX 預設輸出是 v1.7（specVersion: 1.7）。

## 先建立直覺

CycloneDX 的整體結構比 SPDX 扁平，但欄位密度更高：

```
CycloneDX BOM
├── $schema / bomFormat / specVersion  ← 格式識別
├── serialNumber                        ← 這份 BOM 的唯一 UUID
├── version                            ← 同一個 serial 的第幾版（支援增量更新）
│
├── metadata                           ← 關於這份 BOM 本身的 metadata
│   ├── timestamp
│   ├── tools[]                        ← 產生工具
│   └── component                      ← 被掃描的目標（image 本身）
│
├── components[]                       ← 元件清單（核心）
│   ├── type                           ← library/application/framework/container/…
│   ├── name, version
│   ├── purl                           ← 元件識別符（直接放最上層！）
│   ├── cpe                            ← CPE（漏洞比對）
│   ├── licenses[]                     ← 授權
│   ├── hashes[]                       ← 完整性
│   └── externalReferences[]           ← 外部連結（官網/VCS/distribution）
│
├── dependencies[]                     ← 依賴圖（ref + dependsOn 結構）
│
├── vulnerabilities[]                  ← 原生漏洞清單（SPDX 沒有這個！）
├── compositions[]                     ← 元件覆蓋度聲明
├── services[]                         ← 外部服務依賴
└── annotations[]                      ← 任意備注
```

跟 SPDX 最大的視覺差異：**CycloneDX 沒有 Files section**（不走逐檔記錄路線）、**dependencies 是獨立區塊**（不是用 relationship 表達）、**vulnerabilities 是一等公民**（SPDX 沒有等效的內建欄位）。

## 產出真實的 CycloneDX 輸出

```bash
# 在 WSL 裡
DOCKER_CONFIG=/tmp/noconfig syft registry:alpine:3.19 -o cyclonedx-json > /tmp/alpine.cdx.json
```

產出約 54 KB，96 個 components（80 個 file + 15 個 library + 1 個 operating-system）、11 條 dependency 關係。同一個 image 的 SPDX JSON 是 16 packages + 80 files；CycloneDX 預設把檔案也當成 `type: file` 的 component 放進同一個 `components` 陣列，所以數字看起來多——這是 Ch 7 會展開的計數陷阱。

## 文件頭拆解

```bash
$ jq '{bomFormat, specVersion, serialNumber, version, metadata}' /tmp/alpine.cdx.json
```

真實輸出：

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.7",
  "serialNumber": "urn:uuid:d0276795-976a-4b6a-8d04-35bc5d203b42",
  "version": 1,
  "metadata": {
    "timestamp": "2026-08-17T19:34:59+08:00",
    "tools": {
      "components": [
        {
          "type": "application",
          "author": "anchore",
          "name": "syft",
          "version": "1.51.0"
        }
      ]
    },
    "component": {
      "bom-ref": "eaac5b7a85e8e5d3",
      "type": "container",
      "name": "alpine",
      "version": "3.19"
    }
  }
}
```

逐欄位說清楚：

**`bomFormat: "CycloneDX"`**：必填，固定值。消費工具靠這個識別格式，不需要靠副檔名。

**`specVersion: "1.7"`**：CycloneDX 格式版本。對照歷史：v1.0（2017）只有 library 元件；v1.2（2020）加入 services 和 dependency graph；v1.4（2022）加入 vulnerabilities 和 compositions；v1.5（2023）加入 VEX 和 formulation；v1.6（2024）成為 ECMA-424 標準並加入 crypto、SBOM-in-SBOM；v1.7（2025）加入 AI/ML 元件、attestation、citations。

**`serialNumber`**：格式是 `urn:uuid:<UUID>`，全域唯一識別這份 BOM。語意上等同於 SPDX 的 `documentNamespace`，但格式強制 UUID，更嚴格。

**`version: 1`**：CycloneDX 有個 SPDX 沒有的概念——**BOM 的版本號**。如果你對同一個軟體產了第二份 SBOM（更新了什麼），`serialNumber` 不變、`version` 從 1 變 2。消費工具能分辨「這是同一個產品的第二版清單，而不是另一個產品」。SPDX 要做到這件事只能靠文件 namespace 的命名慣例，沒有欄位級別的支援。

**`metadata.tools`**：產生工具的結構在 v1.7 改了——工具從 `tools.components[]` 陣列表達（每個工具自己是一個 component），而非字串列表。這讓工具本身也能有 PURL、版本、hash。

**`metadata.component`**：描述「被掃描的目標」，這裡是 alpine:3.19 這個 container image（`type: "container"`）。這和 SPDX 裡 DESCRIBES 關係指向的 root package 概念對應，但 CycloneDX 直接放在 metadata 裡，不需要在 components 清單再找。

## Component 拆解

```bash
$ jq '.components[0]' /tmp/alpine.cdx.json
```

真實輸出（alpine-baselayout）：

```json
{
  "bom-ref": "pkg:apk/alpine/alpine-baselayout@3.4.3-r2?arch=x86_64&distro=alpine-3.19.9&package-id=29e49df2485f6aa9",
  "type": "library",
  "publisher": "Natanael Copa <ncopa@alpinelinux.org>",
  "name": "alpine-baselayout",
  "version": "3.4.3-r2",
  "description": "Alpine base dir structure and init scripts",
  "licenses": [
    {
      "license": {
        "id": "GPL-2.0-only"
      }
    }
  ],
  "cpe": "cpe:2.3:a:alpine-baselayout:alpine-baselayout:3.4.3-r2:*:*:*:*:*:*:*",
  "purl": "pkg:apk/alpine/alpine-baselayout@3.4.3-r2?arch=x86_64&distro=alpine-3.19.9",
  "externalReferences": [
    {
      "url": "https://git.alpinelinux.org/cgit/aports/tree/main/alpine-baselayout",
      "type": "distribution"
    }
  ],
  "properties": [
    { "name": "syft:package:foundBy", "value": "apk-db-cataloger" },
    { "name": "syft:package:type", "value": "apk" },
    { "name": "syft:location:0:layerID", "value": "sha256:0b44b2151d78267ab6f2c76208c3be18688f49b2b0afd6852a9533f2cce121c5" },
    { "name": "syft:location:0:path", "value": "/lib/apk/db/installed" },
    { "name": "syft:metadata:installedSize", "value": "331776" }
  ]
}
```

關鍵欄位解說：

**`bom-ref`**：文件內的唯一引用 ID，用於 `dependencies` 區塊的 `ref` 和 `dependsOn` 欄位。syft 直接用 PURL 加 `package-id` hash 當 `bom-ref`，這樣比對的時候很直覺，但規範允許任意字串。

**`type`**：元件類型，這是 CycloneDX 比 SPDX 更細緻的地方。完整的 type 列表包括：
- `library`：函式庫（最常見）
- `application`：可執行應用程式
- `framework`：框架
- `container`：容器 image
- `platform`：作業系統、runtime 環境
- `device`：硬體設備
- `device-driver`：驅動程式
- `firmware`：韌體
- `file`：個別檔案
- `machine-learning-model`：ML 模型（v1.5+）
- `data`：資料集（v1.5+）
- `cryptographic-asset`：密碼學資產（v1.6+）

alpine-baselayout 這個 apk 套件被歸類為 `library`——對 apk 套件來說有些爭議（它其實更像 framework/platform），但 syft 的 cataloger 就這樣歸類。

**`licenses`**：結構是 `[{ "license": { "id": "GPL-2.0-only" } }]`。CycloneDX 的 license 欄位允許三種形式：
- `{ "license": { "id": "SPDX-ID" } }`：用 SPDX license identifier（推薦）
- `{ "license": { "name": "自由文字" } }`：無對應 SPDX identifier 時
- `{ "expression": "SPDX expression" }`：複合授權表達式

注意 CycloneDX 在最上層把授權資訊（`licenses`）和元件識別符（`purl`、`cpe`）放在同一層級——SPDX 把 `externalRefs` 埋得比較深、授權是獨立欄位。CycloneDX 的做法讓你一眼就能在 component 物件裡找到所有關鍵資訊。

**`purl`**：在 CycloneDX 裡是最高層級的欄位（和 `name`、`version` 並列），不像 SPDX 把它埋在 `externalRefs` 陣列裡。這反映 CycloneDX 把 PURL 視為主要識別符，而非附加資訊。

**`properties`**：CycloneDX 的通用擴充機制，用 `name`/`value` 鍵值對。syft 用它儲存工具私有的元資料（找到這個套件的 cataloger 名稱、Docker layer ID、安裝路徑等）。任何工具都可以用 `properties` 加自己的資訊，不破壞互通性。

## Dependencies 拆解

```bash
$ jq '.dependencies[0:3]' /tmp/alpine.cdx.json
```

真實輸出（節錄前三條）：

```json
[
  {
    "ref": "pkg:apk/alpine/alpine-baselayout@3.4.3-r2?arch=x86_64&distro=alpine-3.19.9&package-id=29e49df2485f6aa9",
    "dependsOn": [
      "pkg:apk/alpine/alpine-baselayout-data@3.4.3-r2?arch=x86_64&distro=alpine-3.19.9&package-id=0b98be673fabb009&upstream=alpine-baselayout",
      "pkg:apk/alpine/busybox-binsh@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9&package-id=c350666544085d21&upstream=busybox"
    ]
  },
  {
    "ref": "pkg:apk/alpine/apk-tools@2.14.4-r0?arch=x86_64&distro=alpine-3.19.9&package-id=d579f72331dbd0e8",
    "dependsOn": [
      "pkg:apk/alpine/ca-certificates-bundle@20250911-r0?...",
      "pkg:apk/alpine/libcrypto3@3.1.8-r1?...",
      "pkg:apk/alpine/libssl3@3.1.8-r1?...",
      "pkg:apk/alpine/musl@1.2.4_git20230717-r5?...",
      "pkg:apk/alpine/zlib@1.3.1-r0?..."
    ]
  },
  {
    "ref": "pkg:apk/alpine/busybox-binsh@1.36.1-r20?...",
    "dependsOn": [
      "pkg:apk/alpine/busybox@1.36.1-r20?..."
    ]
  }
]
```

CycloneDX 的依賴圖表達比 SPDX 直觀：每個 `ref` 是一個 component 的 `bom-ref`，`dependsOn` 是它直接依賴的 component 的 `bom-ref` 列表。這是一個**鄰接表**（adjacency list）格式。

對比 SPDX：SPDX 的依賴關係是 Relationships 陣列裡的 `DEPENDENCY_OF`/`DEPENDS_ON` 邊，每條邊是獨立的一個物件。CycloneDX 的做法讓你更快找到「這個元件依賴誰」（直接查對應的 `ref`），但 SPDX 的做法讓你更容易增量添加關係（每條邊獨立）。

這份 alpine SBOM 有 11 條 dependency 關係，反映 alpine 的 apk 套件依賴圖。

## 底層機制：CycloneDX 的設計哲學

```
CycloneDX 的 security-first 設計

  components[]
  （把識別符放最上層：purl, cpe 和 name/version 並列）
       │
       │ 消費工具直接抓 purl → 比對漏洞 DB → 出結果
       ▼
  vulnerabilities[]        ← SPDX 沒有對應的一等公民欄位
  ┌───────────────────┐
  │ id: CVE-2024-xxxx │
  │ ratings: CVSS      │
  │ affects: [ref]     │ ← 指向哪個 component 受影響
  │ analysis: {}       │ ← VEX 資訊（狀態、理由）
  └───────────────────┘
       │
       │ 在同一份 BOM 內做漏洞聲明，不需要另一份文件
       ▼
  compositions[]           ← 元件覆蓋度聲明
  ┌────────────────────────────────────┐
  │ aggregate: "complete"              │ ← 我保證清單是完整的
  │ assemblies: [所有 component bom-ref]│
  └────────────────────────────────────┘
```

CycloneDX 的設計讓一份 BOM 文件就能同時表達「有什麼元件」和「這些元件有哪些已知漏洞及如何看待它們（VEX）」。SPDX 要達成類似效果，需要搭配額外的 VEX 文件（CSAF 格式，或 OpenVEX），兩份文件關聯起來。

這個差異在實際工作流裡很重要：如果你要做「某個 CVE 對我有沒有影響」的聲明，CycloneDX 讓你在同一份 BOM 裡做；SPDX 的標準做法是產兩份文件。Ch 16 的 VEX 章會深入這個比較。

## CycloneDX 版本演進

| 版本 | 發布 | 重要新增 |
|------|------|---------|
| 1.0 | 2017 | 最初版，只有 library 元件 |
| 1.1 | 2019 | 加入 hashes、`externalReferences` |
| 1.2 | 2020 | 加入 services、dependency graph、`compositions` |
| 1.3 | 2021 | 加入 `metadata` 改良、licence expression |
| 1.4 | 2022 | 加入 `vulnerabilities`（漏洞清單） |
| 1.5 | 2023 | 加入 VEX、`formulation`（製造流程）、ML model type |
| 1.6 | 2024 | ECMA-424 1st Ed；加入 crypto-asset、SBOM-in-SBOM、`declarations` |
| 1.7 | 2025 | ECMA-424 2nd Ed；加入 AI/ML 元件強化、`attestation`、`citations` |

syft 1.51.0 預設輸出 v1.7，但你可以用 `-o cyclonedx-json@1.4` 這類語法指定舊版本（以官方工具文件為準，行為可能隨版本異動）。

## 進階欄位：services、compositions、vulnerabilities

### services

CycloneDX 原生支援「外部服務依賴」的表達，這是 SPDX 沒有的概念：

```json
{
  "services": [
    {
      "bom-ref": "svc-payment-api",
      "name": "payment-api",
      "version": "v3",
      "endpoints": ["https://api.payment.example.com/v3/charge"],
      "authenticated": true,
      "data": [
        { "classification": "PII", "flow": "outbound" }
      ]
    }
  ]
}
```

（上面是概念示意，syft 不產 services，以規範為準）

一個微服務系統除了有 library 依賴，還有外部 API 依賴。SBOM 不記錄這些，你的攻擊面分析就不完整。CycloneDX 的 services 讓 SBOM 往「系統拓撲文件」延伸。

### compositions

```json
{
  "compositions": [
    {
      "aggregate": "complete",
      "assemblies": ["bom-ref-A", "bom-ref-B", "..."]
    }
  ]
}
```

`aggregate` 可以是 `complete`（保證列了全部元件）、`incomplete`（知道有遺漏）、`incomplete_first_party_only`（只列了自家元件）等。這個欄位讓 SBOM 的消費方知道「這份清單是全的，還是只列了工具能看到的一部分」——對 SBOM 品質評估（Ch 12 主題）很重要。

### vulnerabilities（VEX 承載）

```json
{
  "vulnerabilities": [
    {
      "id": "CVE-2024-3094",
      "source": { "name": "NVD", "url": "https://nvd.nist.gov/vuln/detail/CVE-2024-3094" },
      "ratings": [
        { "source": { "name": "NVD" }, "score": 10.0, "severity": "critical", "method": "CVSSv3" }
      ],
      "affects": [
        {
          "ref": "pkg:apk/alpine/xz@5.4.6-r0?...",
          "versions": [{ "version": "5.4.6-r0", "status": "affected" }]
        }
      ],
      "analysis": {
        "state": "not_affected",
        "justification": "code_not_reachable",
        "response": ["will_not_fix"],
        "detail": "Alpine 3.19 的 xz-utils 不含受影響的 systemd 整合路徑"
      }
    }
  ]
}
```

（上面是概念示意，syft 產的 SBOM 不含 vulnerabilities，以規範為準；grype 等工具可以合併漏洞資訊進 CycloneDX）

`analysis` 物件就是 VEX（Vulnerability Exploitability eXchange）的承載點——`state` 是「not_affected/affected/fixed/in_triage」，`justification` 解釋為什麼不受影響，`detail` 是人類可讀說明。CycloneDX 是少數格式可以在同一份文件裡同時說「有這個元件」和「這個 CVE 對我不適用，理由如下」。

## 對比與取捨

| 面向 | CycloneDX 的選擇 | 對比 SPDX |
|------|-----------------|----------|
| PURL 的位置 | 最上層欄位，和 name/version 並列 | `externalRefs[]` 裡的一個條目 |
| 依賴圖 | 獨立的 `dependencies[]` 鄰接表 | `relationships[]` 陣列裡的有向邊 |
| 漏洞 | 原生 `vulnerabilities[]` + VEX | 無內建（需外部 VEX 文件） |
| 授權 | `licenses[]` 在 component 裡 | `licenseConcluded` / `licenseDeclared` 欄位 |
| 檔案記錄 | 無 Files section | 有 Files section（可選） |
| 版本化 | `serialNumber` + `version` 支援增量更新 | `documentNamespace` 不帶版本號概念 |
| 擴充 | `properties[]` 鍵值對 | `annotations[]`、自訂 prefix |
| 標準化 | ECMA-424（2024 起） | ISO/IEC 5962（2021） |

## 踩雷集錦

1. **「CycloneDX 不是正式標準」**：2024 年之後這句話就不對了。v1.6 是 ECMA-424 1st Edition，v1.7 是 2nd Edition。ECMA 的地位跟 ECMA-262（JavaScript 規範）同級，不是業界草案。說 SPDX 是唯一標準的人，沒有更新自己的知識。

2. **「`type: library` 代表這個東西是函式庫」**：syft 對 apk 套件一律填 `library`，但 apk 套件包含的東西可以是 binary、設定檔、函式庫、腳本——叫 `library` 是工具的命名約定，不一定準確反映元件的本質。消費工具做類型過濾時要注意這個。

3. **「`dependencies` 空了就是沒有依賴」**：不對。`dependencies` 空代表工具沒有讀到 dependency graph 的資訊，通常因為沒有 lock file 或 build manifest。alpine 這個純二進位環境，syft 能從 apk DB 讀到依賴，所以有 11 條；換成直接掃一個 docker 拉下來的 Go binary，`dependencies` 就會是空的，因為靜態編進去的依賴沒有在執行環境留下 lock file。

4. **「CycloneDX 不記錄授權，所以不適合合規」**：`licenses[]` 就在每個 component 裡，而且支援 SPDX license expression 語法（`expression` 欄位）。CycloneDX 記錄授權，只是沒有像 SPDX 那樣區分「宣告的」和「認定的」兩個欄位。授權合規用 CycloneDX 完全可以，只是 SPDX 對這個用途設計得更細緻。

5. **「`bom-ref` 就是 PURL」**：syft 剛好用 PURL（加 hash）當 `bom-ref`，但規範說 `bom-ref` 是任意字串，只要在文件內唯一就行。消費工具不能假設 `bom-ref` 等於 `purl`，要分開處理。

## 進階：再往深一層

### CycloneDX 的 SBOM-in-SBOM

v1.6 加入的功能：一份 CycloneDX BOM 可以「內嵌」另一份 BOM 作為 component。這讓「把供應商提供的 SBOM 整合進我自己的 SBOM」成為一個標準操作，而不是自己把兩份文件的欄位手動合併。

### Formulation（製造流程）

v1.5 加入的 `formulation` 區塊讓你記錄軟體怎麼被建構的——build 工具、build 指令、環境變數、輸入 artifact、輸出 artifact。這讓 CycloneDX BOM 可以當 build attestation（SLSA provenance）的承載格式，打通「元件清單」和「來源證明」。

### Crypto-BOM（v1.6）

`component.type = "cryptographic-asset"` 配合新增的 `cryptoProperties`，讓你記錄軟體使用的密碼學演算法（AES-128、RSA-2048、…）。在後量子密碼學（PQC）過渡時期，這個功能讓你能快速回答「哪些系統用了脆弱的密碼學」，就像 SBOM 讓你快速回答「誰用了 log4j」。

## 動手練習

1. 查一下 alpine SBOM 裡 components 的 type 分佈：
   ```bash
   cat /tmp/alpine.cdx.json | python3 -c "
   import sys,json
   d=json.load(sys.stdin)
   types={}
   for c in d['components']:
       t=c.get('type','?')
       types[t]=types.get(t,0)+1
   print(types)
   "
   ```
   你會看到大部分是 `library`，思考這反映了什麼。

2. 找到 `libcrypto3` 的 component，看它的 purl 和 cpe：
   ```bash
   cat /tmp/alpine.cdx.json | python3 -c "
   import sys,json
   d=json.load(sys.stdin)
   c=next(x for x in d['components'] if x['name']=='libcrypto3')
   import json as j
   print(j.dumps({k:v for k,v in c.items() if k in ['name','version','purl','cpe','licenses']}, indent=2))
   "
   ```

3. 畫出 `apk-tools` 的直接依賴圖：從 `dependencies[]` 找到 `apk-tools` 的條目，列出它的 `dependsOn`，再各自找一層。用 ASCII 畫出三層的依賴樹。

## 本章重點整理

- CycloneDX 是 OWASP 主導、security-first 設計的格式，v1.6/v1.7 是 ECMA-424 國際標準。
- 文件結構：bomFormat + specVersion + serialNumber + version + metadata + components + dependencies。`serialNumber` + `version` 的組合讓 BOM 支援版本化增量更新。
- components 的 `type` 欄位細分元件類型；`purl` 在最上層（不像 SPDX 藏在 externalRefs）；`licenses` 在每個 component 內。
- dependencies 是鄰接表格式（`ref → dependsOn[]`），比 SPDX 的 relationship 邊更直觀查詢「這個元件依賴誰」。
- 原生 `vulnerabilities[]` + VEX 支援（v1.4/v1.5+）是 CycloneDX 對 SPDX 的最大差異化優勢。
- `properties[]` 是工具擴充的標準機制，syft 用它記錄 cataloger、layer ID 等資訊。

## 自我檢核

- [ ] 我能說出 `serialNumber` 和 `version` 的組合設計有什麼用途
- [ ] 我能找到某個 component 的 `purl`，並說明它為什麼和 SPDX 的放置位置不同
- [ ] 我能從 `dependencies[]` 鄰接表讀出「A 依賴 B 和 C」的關係
- [ ] 我能說出 CycloneDX 的 `vulnerabilities` 區塊和 VEX 有什麼關係
- [ ] 我知道 CycloneDX 從哪個版本開始成為 ECMA 標準，以及 syft 1.51.0 預設輸出哪個版本

## 延伸閱讀

- **[CycloneDX 規範](https://cyclonedx.org/specification/overview/)**（OWASP/Ecma TC54）
  - **讀哪裡**：Object Model 那一節，用圖的方式展示 components/services/vulnerabilities/dependencies 的關係；以及 Versioning section，看歷代版本加了什麼
  - **為什麼值得讀**：規範網站比較容易讀，圖例清楚，是核對「這個欄位到底是什麼語意」的第一手來源

- **[CycloneDX GitHub Schema](https://github.com/CycloneDX/specification/tree/master/schema)**（CycloneDX GitHub）
  - **讀哪裡**：`bom-1.7.schema.json`（JSON Schema），每個欄位都有 `description`，比規範網站更容易機器查詢
  - **為什麼值得讀**：你在寫 CycloneDX 的解析器或驗證器時，JSON Schema 是 ground truth

- **[ECMA-424 標準](https://ecma-international.org/publications-and-standards/standards/ecma-424/)**（Ecma International）
  - **讀哪裡**：標準本文本身，確認 CycloneDX 的國際標準地位（免費下載）
  - **為什麼值得讀**：如果你要對客戶或主管說「CycloneDX 有正式標準背書」，這是引用來源

- **[CycloneDX VEX 使用指南](https://cyclonedx.org/capabilities/vex/)**（CycloneDX 官方）
  - **讀哪裡**：`vulnerabilities[]` 區塊的結構說明，以及 `analysis.state` 每個值的語意
  - **為什麼值得讀**：VEX 是這門課 Ch 16 的主題，這份文件是那一章的最佳預習材料

- **[syft CycloneDX 輸出說明](https://github.com/anchore/syft)**（Anchore GitHub）
  - **讀哪裡**：搜尋 `cyclonedx` 的 output format 說明，了解 syft 哪些欄位沒有填（比如 syft 的 CycloneDX 輸出不含 `vulnerabilities`，需要 grype 或 Dependency-Track 補）
  - **為什麼值得讀**：工具的實際行為和格式規範之間永遠有差距，知道差距在哪才不會對輸出有錯誤期待

下一章我們把 SPDX 和 CycloneDX 放在同一張桌子上正面比較，同一份 alpine image 的兩份輸出並列，逐欄位看它們如何描述同一件事，然後給出明確的選型建議。

→ [Ch 7 SPDX vs CycloneDX 對比與選型](./07-spdx-vs-cyclonedx.md)
