# Ch 7 — SPDX vs CycloneDX 對比與選型

> **目標**：把 SPDX 和 CycloneDX 放在同一張桌子上正面比較。用同一份 alpine:3.19 image 的兩份真實輸出，並列看同一個元件怎麼被兩種格式描述；整理治理、設計取向、資料模型、工具生態、法規接受度的系統性差異；給出明確的選型原則——不是「看情況」，是「做這件事用這個」。最後帶 SWID Tag（NTIA 三格式之一）的定位。

## 為什麼需要這個？

你讀完 Ch 5 和 Ch 6，現在知道兩個格式各長什麼樣，但你還是不知道**應該用哪個**。這個問題在實際工作裡每個月都會被問到——法務問、SRE 問、CSO 問。

「兩個都支援」是一個合法答案，但不是一個有用的答案。有用的答案是：理解兩者的設計取向、用它們各自擅長的場景、知道某些情況只能用某個。這章給你做這個判斷的框架。

## 先建立直覺

把兩個格式的核心基因說清楚：

```
SPDX 的 DNA                        CycloneDX 的 DNA
─────────────────────               ─────────────────────
起源：授權合規、IP 追蹤              起源：資安、漏洞管理
主導：Linux Foundation               主導：OWASP
標準化：ISO/IEC 5962 (2021)          標準化：ECMA-424 (2024)
設計：完整性、精確性                  設計：輕量、machine-actionable
文件取向：給人類讀 + 工具解析         文件取向：給工具消費
授權支援：兩個獨立欄位（宣告/認定）    授權支援：一個 licenses 欄位
依賴圖：Relationships 有向邊         依賴圖：鄰接表（直觀）
漏洞/VEX：需外部文件                 漏洞/VEX：一等公民（原生支援）
Files section：有                    Files section：無
```

這不是說 SPDX 不能做資安、或 CycloneDX 不能做授權合規——兩者都能做。差別在於**誰是設計的第一優先級**，這個優先級差異滲透到每個細節的取捨裡。

## 同一元件的兩種描述：並列對比

最直接的比較方法：把 busybox 這個 package 在兩份格式裡的描述並列。

### SPDX 2.3 裡的 busybox

```bash
$ jq '.packages[] | select(.name == "busybox")' /tmp/alpine.spdx.json
```

真實輸出：

```json
{
  "name": "busybox",
  "SPDXID": "SPDXRef-Package-apk-busybox-27d66ed59ca16b92",
  "versionInfo": "1.36.1-r20",
  "supplier": "NOASSERTION",
  "downloadLocation": "https://busybox.net/",
  "filesAnalyzed": true,
  "packageVerificationCode": {
    "packageVerificationCodeValue": "a04ae91a1e7a88a90ba178ef63006b21e47f018f"
  },
  "sourceInfo": "acquired package info from APK DB: /lib/apk/db/installed",
  "licenseConcluded": "NOASSERTION",
  "licenseDeclared": "GPL-2.0-only",
  "copyrightText": "NOASSERTION",
  "description": "Size optimized toolbox of many common UNIX utilities",
  "externalRefs": [
    {
      "referenceCategory": "SECURITY",
      "referenceType": "cpe23Type",
      "referenceLocator": "cpe:2.3:a:busybox:busybox:1.36.1-r20:*:*:*:*:*:*:*"
    },
    {
      "referenceCategory": "PACKAGE-MANAGER",
      "referenceType": "purl",
      "referenceLocator": "pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9"
    }
  ]
}
```

### CycloneDX 1.7 裡的 busybox

```bash
$ cat /tmp/alpine.cdx.json | python3 -c "
import sys,json
d=json.load(sys.stdin)
c=next(x for x in d['components'] if x['name']=='busybox' and 'upstream' not in x.get('bom-ref',''))
import json as j
print(j.dumps(c, indent=2))
"
```

真實輸出（截取關鍵欄位）：

```json
{
  "bom-ref": "pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9&package-id=27d66ed59ca16b92",
  "type": "library",
  "name": "busybox",
  "version": "1.36.1-r20",
  "description": "Size optimized toolbox of many common UNIX utilities",
  "licenses": [
    {
      "license": {
        "id": "GPL-2.0-only"
      }
    }
  ],
  "cpe": "cpe:2.3:a:busybox:busybox:1.36.1-r20:*:*:*:*:*:*:*",
  "purl": "pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9",
  "externalReferences": [
    {
      "url": "https://busybox.net/",
      "type": "website"
    }
  ],
  "properties": [
    { "name": "syft:package:foundBy", "value": "apk-db-cataloger" },
    { "name": "syft:package:type", "value": "apk" },
    { "name": "syft:location:0:layerID", "value": "sha256:0b44b2151d78267ab6f2c76208c3be18688f49b2b0afd6852a9533f2cce121c5" }
  ]
}
```

### 並列比較表

| 欄位 | SPDX 2.3 | CycloneDX 1.7 |
|------|----------|---------------|
| 元件識別 | `SPDXID: "SPDXRef-Package-apk-busybox-..."` | `bom-ref: "pkg:apk/.../busybox@..."` |
| 名稱 | `name` | `name` |
| 版本 | `versionInfo` | `version` |
| PURL | `externalRefs[].referenceLocator`（要找） | `purl`（直接在頂層） |
| CPE | `externalRefs[].referenceLocator`（要找） | `cpe`（直接在頂層） |
| 授權 | `licenseConcluded: "NOASSERTION"` + `licenseDeclared: "GPL-2.0-only"` | `licenses: [{"license":{"id":"GPL-2.0-only"}}]` |
| 完整性 | `packageVerificationCode`（所有檔案 SHA-1 的摘要） | 無對應欄位（CycloneDX 有 `hashes[]` 但 syft 沒填） |
| 資料來源 | `sourceInfo: "acquired from APK DB..."` | `properties[].value: "apk-db-cataloger"` |
| Layer 資訊 | `files[].comment` 裡有 layerID | `properties[].value: "sha256:..."` |
| 元件類型 | 無（package、file、snippet 是類型） | `type: "library"` |

**關鍵觀察**：
1. PURL 在 SPDX 要從 `externalRefs` 陣列篩出 `PACKAGE-MANAGER` 類別才能取到；CycloneDX 直接 `component.purl` 一行拿到。寫消費工具時 CycloneDX 更省事。
2. 授權：SPDX 明確區分「宣告（declared）」和「認定（concluded）」，CycloneDX 只有一個 `licenses`。如果你的法務流程需要「工具宣告的 vs 人工確認的」兩層，SPDX 的設計更適合。
3. `packageVerificationCode` 是 SPDX 特有的 package 完整性機制；CycloneDX 在 component 層級有 `hashes[]`（可以放 SHA-256、SHA-512 等），但 syft 在這個 alpine 範例沒有填。

## 系統性比較

### 治理與標準

| 面向 | SPDX | CycloneDX |
|------|------|-----------|
| 主導組織 | Linux Foundation | OWASP Foundation |
| 技術標準 | ISO/IEC 5962:2021（對應 v2.2） | ECMA-424（v1.6→2024, v1.7→2025） |
| 規範管理 | SPDX GitHub，Linux Foundation 核准 | OWASP GitHub + Ecma TC54 |
| 社群參與 | 企業（Intel, Google, Microsoft, …）主導 | 資安社群 + 企業聯合 |

**ISO vs ECMA**：兩者都是公認的國際標準組織，不存在哪個「更正式」的問題。ISO 5962 在企業採購、政府合規文件裡比較常被直接引用（因為 2021 年就有了）；ECMA-424 在 2024 年才出，但 Ecma 在 JS/C# 這類技術標準上有深厚信譽。

### 設計取向與欄位密度

| 面向 | SPDX | CycloneDX |
|------|------|-----------|
| 授權欄位 | `licenseConcluded` + `licenseDeclared` + `licenseInfoFromFiles[]` | `licenses[]`（一個欄位，支援 SPDX expression） |
| 完整性 | `packageVerificationCode`（package 級）+ file checksum | `hashes[]`（component 級，SHA-256/SHA-512 等） |
| 漏洞 | 無內建（需外部 VEX 文件） | `vulnerabilities[]`（原生，Ch 16 主題） |
| 服務依賴 | 無 | `services[]` |
| 製造流程 | 無（需搭配 SLSA/in-toto） | `formulation[]`（v1.5+） |
| Files section | 有（每個檔案的 hash、授權） | 無 |
| 元件類型 | Package/File/Snippet（3種） | `type` 欄位：14+ 種細分類型 |
| BOM 版本化 | 無（靠 namespace 命名慣例） | `serialNumber` + `version` 原生支援 |

### 工具生態

| 工具 | SPDX 支援 | CycloneDX 支援 |
|------|-----------|----------------|
| syft | v2.3 JSON / tag-value | v1.6 / v1.7 JSON |
| grype | 讀 SPDX 2.2/2.3 | 讀 CycloneDX 1.4+ |
| trivy | 寫 SPDX 2.3 | 寫 CycloneDX 1.5 |
| Dependency-Track | 讀 SPDX 2.2/2.3 | 讀 CycloneDX 1.0–1.6（1.7 視版本） |
| GitHub Dependency Graph | 讀 SPDX 2.3 | 讀 CycloneDX 1.4 |
| FOSSA | 讀/寫 SPDX | 讀/寫 CycloneDX |
| Black Duck | 讀/寫 SPDX | 讀/寫 CycloneDX |

兩個格式的工具支援都已經夠用。CycloneDX 在資安工具（漏洞掃描、Dependency-Track）生態更深；SPDX 在法律合規工具（FOSSA、Black Duck 的「授權報告」功能）更完整。實際上很多企業兩份都產，給不同受眾：授權審計給法務的用 SPDX，CI/CD 漏洞掃描流程用 CycloneDX。

### 法規接受度

美國 NTIA 的《Minimum Elements for a Software Bill of Materials》（2021）明確列出了三種可接受格式：**SPDX、CycloneDX、SWID Tag**。這份文件是 US Executive Order 14028 的落地指引，也是後來 FDA 醫材 SBOM 要求、EU Cyber Resilience Act 的參考來源。

兩個格式在法規面都被接受，不存在「只交 CycloneDX 會被退件」的情況。選哪個主要看消費方的工具和偏好。

## SWID Tag：第三格式的定位

NTIA 列了 SWID（Software Identification Tag），必須知道它在哪、做什麼，但工具生態最弱。

SWID 是 **ISO/IEC 19770-2** 定義的軟體識別標準，最初設計用途是**軟體資產管理（SAM）**——企業 IT 部門追蹤授權數量、安裝位置。它不是為依賴圖或漏洞管理設計的。

SWID Tag 長這樣（XML 格式，以規範為準）：

```xml
<SoftwareIdentity
  name="BusyBox"
  tagId="org.busybox.busybox-1.36.1"
  version="1.36.1"
  xmlns="http://standards.iso.org/iso/19770/-2/2015/schema.xsd">
  <Entity
    name="BusyBox Authors"
    regid="busybox.net"
    role="licensor"/>
</SoftwareIdentity>
```

主要用途：企業部署的軟體清查（「我們裝了幾套 Windows Server 2022」），不是開發依賴管理。syft 能輸出 SWID（`-o swid-tag`），但幾乎沒有安全工具把它當消費格式。NTIA 列入它，主要是為了讓企業 IT（SAM 工具已經用 SWID）也能符合 minimum elements。

**結論**：SWID Tag 你只需要知道它存在、知道它偏向資產管理而非安全/合規，以及知道法規文件為什麼會提到它。實際工作裡 99% 用 SPDX 或 CycloneDX。

## 選型決策框架

不要靠「哪個更好」這種問題選。靠用途選：

```
你要做什麼？
│
├── 授權合規、IP 審查、法律報告
│   └── 選 SPDX
│       原因：licenseConcluded vs licenseDeclared 的雙層設計
│             SPDX license expression 語法最嚴格
│             法律工具（FOSSA/Black Duck）原生 SPDX
│
├── 漏洞掃描、VEX 聲明、CI 安全門
│   └── 選 CycloneDX
│       原因：vulnerabilities 一等公民
│             VEX 直接在 BOM 裡，不需要另一份文件
│             grype/Dependency-Track 對 CycloneDX 支援更完整
│
├── 法規合規（EO 14028 / FDA / EU CRA）
│   └── 兩個都接受，看消費方要求
│       EO 14028 採購合約通常接受 SPDX 或 CycloneDX
│       FDA 2023 premarket guidance 兩個都可以
│
├── 你的組織已有 SBOM 工具
│   └── 用工具支援的格式，不要為了「更好的格式」換工具
│
└── 不確定，要打通從 CI 到漏洞監控的端到端管線
    └── 兩份都產（syft 一次掃，輸出兩種格式）
        授權報告用 SPDX，漏洞掃描和 Dependency-Track 用 CycloneDX
```

「兩份都產」不是偷懶的答案——syft 的指令很直接：

```bash
# 一次掃，輸出兩種格式
DOCKER_CONFIG=/tmp/noconfig syft registry:alpine:3.19 \
  -o spdx-json=alpine.spdx.json \
  -o cyclonedx-json=alpine.cdx.json
```

實際上這是很多大型組織的做法：SBOM 生成是 CI pipeline 的一步，同時輸出兩種格式，分別推送給法務工具和安全掃描工具。格式轉換工具（如 CycloneDX 的 `cdx-cli convert`）也能在兩者之間轉換，但轉換必然有資訊損失（比如 SPDX 的 `licenseConcluded` 在 CycloneDX 裡無對應欄位），所以能原生輸出就不要轉換。

## 底層機制：格式差異的根因

```
授權合規 workflow（SPDX 的主場）

  代碼庫 ──syft──▶ SPDX SBOM
                      │
                      │ licenseDeclared（從元件元資料讀）
                      │ licenseConcluded（人工/工具確認）
                      ▼
               授權合規工具（FOSSA/Black Duck）
                      │
                      │ 比對 license policy（哪些 copyleft 被允許？）
                      ▼
              合規報告（給法務、採購）

資安 workflow（CycloneDX 的主場）

  Image ──syft──▶ CycloneDX SBOM
                      │
                      │ components + purl/cpe（識別符在頂層，快速比對）
                      ▼
               grype / Dependency-Track
                      │
                      │ 比對漏洞 DB → 填入 vulnerabilities[]
                      ▼
              CycloneDX BOM（含漏洞 + VEX 聲明）
                      │
                      │ 不需要額外文件格式
                      ▼
              漏洞報告 + 優先排序 + SLA 追蹤
```

## 對比與取捨

| 情境 | 推薦格式 | 理由 |
|------|---------|------|
| 企業軟體授權審計 | SPDX | declared/concluded 雙欄位，合規工具原生支援 |
| CI/CD 漏洞掃描 | CycloneDX | purl 在頂層，grype/trivy 消費更快 |
| VEX 聲明內嵌 BOM | CycloneDX | vulnerabilities 一等公民 |
| 政府合規採購 | 兩者皆可 | NTIA 明文接受兩種 |
| 大型 monorepo 的跨元件引用 | SPDX | external document reference 機制更成熟 |
| IoT / 韌體 SBOM | CycloneDX | `device` / `firmware` component type 更準確 |
| AI/ML 系統 SBOM | CycloneDX | v1.5+ 的 ML model component type 和 formulation |
| 需要記錄 container 每個檔案 | SPDX | Files section 原生支援 |

## 踩雷集錦

1. **「CycloneDX 不是正式標準，企業合規只認 SPDX」**：ECMA-424 在 2024 年就成為正式標準。更重要的是，美國 NTIA minimum elements（法規根源）從第一天就同時接受兩種格式，EU CRA 的草稿也沒有說只認 SPDX。這個誤解通常來自「SPDX 比較老、比較多人聽過」的印象偏差。

2. **「兩個格式可以無損轉換」**：轉換工具存在，但有資訊損失。SPDX 的 `licenseConcluded` 在 CycloneDX 裡沒有直接對應欄位；CycloneDX 的 component `type`（`firmware`、`device-driver` 等細分）在 SPDX 裡只能用 `primaryPackagePurpose` 勉強對應。如果你從一開始就知道要給法務，就直接產 SPDX，不要先產 CycloneDX 再轉換。

3. **「grype 吃 SPDX 和 CycloneDX 效果一樣」**：實際上有差。grype 從 CycloneDX 的 `purl` 頂層欄位直接拿識別符；從 SPDX 要解析 `externalRefs` 陣列再找 `referenceCategory: PACKAGE-MANAGER` 的那條。理論上結果應該一樣，但在 edge case（PURL 格式不標準、某些欄位缺失）時，CycloneDX 路徑更穩定。

4. **「SPDX 只能做授權，CycloneDX 只能做資安」**：兩者都能做兩件事，只是設計重心不同。SPDX 3.0 加入了 Security Profile（VEX、CVSS）；CycloneDX 的 `licenses` 欄位支援完整的 SPDX license expression。但「擅長」和「能做」是兩回事：能做但設計沒針對的功能，在工具支援和欄位語意上都比較薄弱。

5. **「SWID Tag 可以忽略」**：作為工程師可以不深入，但要知道它存在且 NTIA 接受它，因為你可能在採購規格書或合約裡看到「NTIA 三格式」的說法。不知道 SWID 是什麼，你沒辦法解釋為什麼選 SPDX 而不是 SWID。

## 進階：再往深一層

### SPDX 3.0 縮短了差距

SPDX 3.0 加入了 Security Profile（VEX 聲明）和 Dataset/AI Profile，讓 SPDX 能承載更多 CycloneDX 的強項。但工具生態要到 2026-2027 年才會全面跟上，所以現在討論的差異，在幾年後可能縮小。格式選型是個有時效性的決策：今天的最佳實踐，五年後可能被工具進化改寫。

### 格式轉換的精確損失

如果你真的要做格式轉換，了解損失在哪：

```
SPDX → CycloneDX 損失：
- licenseConcluded（SPDX 雙欄位 → CDX 的單一 licenses）
- packageVerificationCode（CDX 的 hashes[] 語意不完全等價）
- Files section（CDX 無對應）
- Snippets（CDX 無對應）

CycloneDX → SPDX 損失：
- component.type 細分類型（SPDX 的 primaryPackagePurpose 類型較少）
- services[]（SPDX 無對應）
- vulnerabilities[]（SPDX 無對應）
- BOM version/serialNumber 語意（SPDX 無對應欄位）
- compositions[]（SPDX 無直接對應）
```

### 混合策略的實作

有些企業用這個策略：CI pipeline 產 CycloneDX，進 Dependency-Track 做持續監控；每季或每次 release 把 CycloneDX 轉成 SPDX 產授權報告給法務。轉換是可接受的，因為：
1. 授權欄位（`licenseDeclared`）在轉換時保留（CycloneDX `licenses.id` → SPDX `licenseDeclared`）
2. 法務關心的資訊損失有限
3. 自動化轉換比維護兩條生成 pipeline 成本低

## 動手練習

1. 對 alpine.spdx.json 和 alpine.cdx.json 各算一下 busybox 的 purl 的提取路徑，比較用 `jq` 要寫幾行：
   ```bash
   # SPDX
   jq '.packages[] | select(.name=="busybox") | .externalRefs[] | select(.referenceCategory=="PACKAGE-MANAGER") | .referenceLocator' /tmp/alpine.spdx.json
   # CycloneDX
   cat /tmp/alpine.cdx.json | python3 -c "import sys,json;d=json.load(sys.stdin);bx=[c for c in d['components'] if c['name']=='busybox' and 'upstream' not in c.get('bom-ref','')][0];print(bx['purl'])"
   ```
   感受一下 PURL 存放位置對消費工具的影響。

2. 計算兩份 SBOM 的 component/package count 差異：SPDX 的 `.packages` 有 16 個（15 個 apk 套件 + 1 個描述 image 的 root），CycloneDX 的 `.components` 有 96 個。為什麼差這麼多？（提示：`jq '.components | group_by(.type) | map({(.[0].type): length}) | add' /tmp/alpine.cdx.json`）——答案是 CycloneDX 預設把**個別檔案**也當成 `type: file` 的 component 塞進同一個陣列：96 = 80 個 file + 15 個 library + 1 個 operating-system。SPDX 則把檔案放在獨立的 `.files` 區塊（同樣是 80 個），不跟 package 混在一起。所以「兩份 SBOM 的元件數不一樣」多半不是誰漏了東西，而是**它們對「什麼算一個 component」的定義不同**——這正是 SBOM 比對的坑，Ch 12 會深入。

3. 用同一份 image 同時產出兩種格式，計時：
   ```bash
   time (DOCKER_CONFIG=/tmp/noconfig syft registry:alpine:3.19 \
     -o spdx-json=/tmp/a.spdx.json \
     -o cyclonedx-json=/tmp/a.cdx.json 2>/dev/null)
   ```
   看看產兩份比產一份慢多少（應該幾乎沒差，因為掃描只做一次，只是序列化輸出兩次）。

## 本章重點整理

- SPDX 偏 licensing/合規完整；CycloneDX 偏 security/輕量。兩者都是合法的 NTIA 接受格式。
- 同一個 busybox package，SPDX 把 PURL 放 `externalRefs[]`（需要篩）；CycloneDX 直接在頂層 `purl` 欄位。CycloneDX 對消費工具更友善。
- 授權欄位：SPDX 有 `licenseConcluded`/`licenseDeclared` 雙層；CycloneDX 只有 `licenses[]`。法律審計用 SPDX 更精確。
- 漏洞/VEX：CycloneDX 原生 `vulnerabilities[]`，SPDX 需外部文件。CI 安全流程用 CycloneDX 更省事。
- SWID Tag 是 NTIA 第三格式，偏資產管理，實際工具生態最弱。
- 「兩份都產」是合法且常見的策略——syft 一次掃可以輸出多種格式。格式轉換有資訊損失，能原生輸出就不要轉換。

## 自我檢核

- [ ] 給我一個 busybox 的 SPDX 條目，我能找出 PURL 在哪、用幾行 jq
- [ ] 我能說出 `licenseConcluded` 在 CycloneDX 裡對應什麼（提示：沒有直接對應）
- [ ] 我能解釋為什麼「漏洞掃描場景選 CycloneDX」，而不只說「因為 CycloneDX 比較好」
- [ ] 我知道 SWID Tag 的設計用途和為什麼工具生態最弱
- [ ] 我能說出兩個從 CycloneDX 轉成 SPDX 時會損失的資訊

## 延伸閱讀

- **[NTIA Minimum Elements for an SBOM](https://www.ntia.gov/report/2021/minimum-elements-software-bill-materials-sbom)**（美國 NTIA）
  - **讀哪裡**：Section 2（推薦格式）和 Appendix B（SPDX/CycloneDX/SWID 各自的 minimum elements mapping）
  - **為什麼值得讀**：這份文件是「SPDX 和 CycloneDX 都被接受」的白紙黑字來源；看 Appendix B 可以理解兩個格式的哪些欄位對應 NTIA minimum elements

- **[CISA 格式比較](https://www.cisa.gov/sbom)**（美國 CISA）
  - **讀哪裡**：SBOM 頁面的格式討論段落
  - **為什麼值得讀**：政府視角的格式選擇論述，比格式各自的宣傳更中立

- **[CycloneDX cdx-cli 轉換工具](https://github.com/CycloneDX/cyclonedx-cli)**（CycloneDX GitHub）
  - **讀哪裡**：README 的 `convert` 指令說明，以及已知的轉換限制
  - **為什麼值得讀**：如果你真的要做格式轉換，用官方工具並了解它的限制，而不是自己寫 JSON mapper

- **[SPDX 3.0 Security Profile](https://spdx.github.io/spdx-spec/v3.0.1/model/Security/)**（SPDX 規範）
  - **讀哪裡**：Security Profile 那章，看 VEX 在 SPDX 3.0 裡怎麼表達
  - **為什麼值得讀**：了解 SPDX 3.0 如何縮短跟 CycloneDX 在資安面的差距，評估未來選型

- **[SWID Tag ISO/IEC 19770-2](https://www.iso.org/standard/65666.html)**（ISO）
  - **讀哪裡**：摘要就夠，了解標準的定位和適用範圍
  - **為什麼值得讀**：NTIA 文件裡會提到 SWID，知道它是什麼才能在對話中給出準確說明而不是模糊帶過

格式選完了，兩個格式裡都有一個高頻出現但常被忽略的欄位：license 授權資訊。下一章深挖 SBOM 的另一半價值——授權合規。

→ [Ch 8 授權資訊與 license compliance](./08-license-info.md)
