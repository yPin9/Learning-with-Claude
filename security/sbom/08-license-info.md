# Ch 8 — 授權資訊與 license compliance

> **目標**：搞懂 SBOM 的授權合規面——SPDX License List 標準化識別碼、SPDX license expression 語法（AND/OR/WITH/+）、copyleft 傳染性和授權不相容的具體後果；理解 SBOM 怎麼承載授權資訊（SPDX 的雙欄位設計 vs CycloneDX 的 licenses[]）；看懂 syft 從 alpine 抓到的真實 license 資料；知道 syft 抓的是「宣告的」、深度掃描需要 scancode 等額外工具。

## 為什麼需要這個？

SBOM 被說成「資安工具」，但它本來有兩個同等重要的用途：**漏洞管理**和**授權合規**。很多工程師只記得前者，然後在第一次真正的法務審計或 M&A（併購）盡職調查時，才發現「我們的系統到底用了哪些 GPL 的東西」這個問題完全答不出來。

授權合規的代價是真實的：
- Copyleft（GPL 家族）有「傳染性」——你的程式連結了 GPL library，在某些條件下你的程式也必須開源。如果你不知道用了它，等到被告才發現，問題比早知道大 10 倍。
- 授權之間有不相容性——GPL-2.0-only 和 Apache-2.0 在某些情境下無法共存於同一個可執行檔。
- 企業採購、政府合約、金融監管，都可能要求你提交「授權合規報告」。有 SBOM 的授權欄位，報告能在幾分鐘內自動產出；沒有，要人工清查幾個月。

SBOM 的授權欄位是這個問題的工程化解法。

## 先建立直覺

授權合規的核心挑戰是兩個：**識別**（這個元件到底用什麼授權？）和**分析**（這些授權組合放在一起合法嗎？）。

SBOM 主要解決「識別」這半段——把授權資訊機器可讀地記錄下來。分析仍然需要授權合規工具和法律判斷，但沒有識別，分析無從開始。

```
授權合規的 pipeline

  代碼/Image ──掃描──▶ SBOM（含 license 欄位）
                              │
                              │ licenseDeclared / licenses[].id
                              ▼
                       授權合規工具（FOSSA / Black Duck / scancode）
                              │
                              │ 比對 license policy（哪些是 copyleft？）
                              │ 分析授權相容性
                              ▼
                       合規報告（允許清單 / 違規清單）
                              │
                              ▼
                       法務 / 採購決策
```

## SPDX License List：標準化識別碼從哪來

如果每個工具用自己的字串表達授權（「MIT License」vs「The MIT License」vs「MIT」），機器就沒辦法比對。SPDX License List 解決了這個問題：它是一份由 SPDX 維護的**標準化授權識別碼清單**，每個授權有唯一的短代碼（identifier），工具和文件都用這個代碼。

截至 License List 3.28（syft 1.51.0 使用的版本），列表包含 680+ 個 license identifier。幾個常見的：

| SPDX Identifier | 全名 |
|-----------------|------|
| `MIT` | MIT License |
| `Apache-2.0` | Apache License 2.0 |
| `GPL-2.0-only` | GNU General Public License v2.0 only |
| `GPL-2.0-or-later` | GNU General Public License v2.0 or later |
| `GPL-3.0-only` | GNU General Public License v3.0 only |
| `LGPL-2.1-only` | GNU Lesser General Public License v2.1 only |
| `LGPL-2.1-or-later` | GNU Lesser General Public License v2.1 or later |
| `BSD-2-Clause` | BSD 2-Clause "Simplified" License |
| `BSD-3-Clause` | BSD 3-Clause "New" or "Revised" License |
| `MPL-2.0` | Mozilla Public License 2.0 |
| `CC0-1.0` | Creative Commons Zero v1.0 Universal |
| `ISC` | ISC License |
| `AGPL-3.0-only` | GNU Affero General Public License v3.0 only |

**重要：歷史遺留 vs 現代識別碼**。`GPL-2.0` 這個舊格式在 SPDX 3.x License List 裡已被移除，正確寫法是 `GPL-2.0-only` 或 `GPL-2.0-or-later`。`-only` 代表「僅限這個版本」，`-or-later` 代表「這個版本或更新版本」。`GPL-2.0-only` 和 `GPL-2.0-or-later` 的法律意義完全不同——Linux kernel 用 `GPL-2.0-only`（不允許用 GPL v3 發行），很多其他 GNU 軟體用 `GPL-2.0-or-later`（允許選擇 GPL v3）。

## SPDX License Expression 語法

單一 identifier 只能表達「這個元件用一種授權」，但現實更複雜：有些套件提供使用者在多個授權中選一個（OR），有些套件的不同部分用不同授權（AND），有些授權還有例外條款（WITH）。SPDX 用 license expression 語法解決這個問題。

### 語法規則（ABNF）

官方規範（SPDX 2.3 Annex D）定義的語法：

```
simple-expression  = license-id / license-id"+" / license-ref
compound-expression = simple-expression
                    / simple-expression "WITH" license-exception-id
                    / compound-expression "AND" compound-expression
                    / compound-expression "OR" compound-expression
                    / "(" compound-expression ")"
license-expression  = simple-expression / compound-expression
```

注意：`+` 緊接在 identifier 後，中間不能有空白（`GPL-2.0+` 正確，`GPL-2.0 +` 是語法錯誤）。

### OR：使用者可選擇

```
MIT OR Apache-2.0
```

使用者可以選擇在 MIT 或 Apache-2.0 條款下使用。這是給使用者最大彈性，企業通常偏好 Apache-2.0（有明確的專利授權），個人開發者偏好 MIT（最短最簡單）。

```
(MIT OR Apache-2.0) AND GPL-3.0-only
```

括號指定優先級。AND 的優先級在裸寫時高於 OR，所以 `MIT OR Apache-2.0 AND GPL-3.0-only` 等於 `MIT OR (Apache-2.0 AND GPL-3.0-only)`。加括號避免歧義是最佳實踐。

### AND：必須同時遵守

```
MPL-2.0 AND MIT
(BSD-2-Clause AND BSD-3-Clause)
MIT AND BSD-2-Clause AND GPL-2.0-or-later
```

必須同時滿足所有授權條款。這通常出現在多作者套件或合併的代碼庫。後面那個例子（`MIT AND BSD-2-Clause AND GPL-2.0-or-later`）是我們在 alpine 真實掃出來的，是 `musl-utils` 的授權。

### WITH：帶例外條款

```
GPL-2.0-only WITH Classpath-exception-2.0
GPL-2.0-or-later WITH Bison-exception-2.2
```

WITH 後面是 license exception identifier（也是 SPDX 維護的清單）。`Classpath-exception-2.0` 是 OpenJDK 用的例外，允許你把 Java 程式和 GPL 授權的 class library 連結而不需要開源你的程式。這個機制讓 GPL 可以附加例外條款而不需要修改 GPL 本身。

### +：or-later 縮寫

```
GPL-2.0+
```

等同於 `GPL-2.0-or-later`。這個縮寫語法在舊文件裡常見，新 SPDX identifier 應該用完整的 `-or-later` 後綴。

### 優先級規則

`WITH` > `AND` > `OR`（從高到低，先綁先計算）。

所以 `MIT OR GPL-2.0-only WITH Classpath-exception-2.0` 解析成：
```
MIT OR (GPL-2.0-only WITH Classpath-exception-2.0)
```

不是：
```
(MIT OR GPL-2.0-only) WITH Classpath-exception-2.0  ← 錯誤解讀
```

有疑問就加括號。

## SBOM 裡的真實 License 資料

### SPDX 的 licenseDeclared

```bash
$ jq '[.packages[] | {name, licenseDeclared}]' /tmp/alpine.spdx.json
```

真實輸出（節錄）：

```json
[
  { "name": "alpine-baselayout",       "licenseDeclared": "GPL-2.0-only" },
  { "name": "alpine-baselayout-data",  "licenseDeclared": "GPL-2.0-only" },
  { "name": "alpine-keys",             "licenseDeclared": "MIT" },
  { "name": "apk-tools",               "licenseDeclared": "GPL-2.0-only" },
  { "name": "busybox",                 "licenseDeclared": "GPL-2.0-only" },
  { "name": "busybox-binsh",           "licenseDeclared": "GPL-2.0-only" },
  { "name": "ca-certificates-bundle",  "licenseDeclared": "(MPL-2.0 AND MIT)" },
  { "name": "libc-utils",              "licenseDeclared": "(BSD-2-Clause AND BSD-3-Clause)" },
  { "name": "libcrypto3",              "licenseDeclared": "Apache-2.0" },
  { "name": "libssl3",                 "licenseDeclared": "Apache-2.0" },
  { "name": "musl",                    "licenseDeclared": "MIT" },
  { "name": "musl-utils",              "licenseDeclared": "(MIT AND BSD-2-Clause AND GPL-2.0-or-later)" },
  { "name": "scanelf",                 "licenseDeclared": "GPL-2.0-only" },
  { "name": "ssl_client",              "licenseDeclared": "GPL-2.0-only" },
  { "name": "zlib",                    "licenseDeclared": "Zlib" },
  { "name": "alpine",                  "licenseDeclared": "NOASSERTION" }
]
```

這是真實輸出。幾個觀察：

`ca-certificates-bundle` 的 `(MPL-2.0 AND MIT)`：ca-certificates 包含了 Mozilla 的根憑證（MPL-2.0）和其他貢獻者的代碼（MIT），兩種授權都需要遵守。

`musl-utils` 的 `(MIT AND BSD-2-Clause AND GPL-2.0-or-later)`：musl-utils 包含了來自不同上游的代碼，授權條款複雜。這裡有 GPL-2.0-or-later，如果你把 musl-utils 靜態連結進你的程式，可能需要提供源代碼。

`alpine`（root image）的 `NOASSERTION`：Container image 本身沒有一個明確的授權，syft 正確填 NOASSERTION。

**`licenseConcluded` 全部是 `NOASSERTION`**——syft 不做人工授權審查，所以它不「斷言」最終認定的授權。這個欄位需要授權合規工具（FOSSA 等）或人工填入。

### CycloneDX 的 licenses[]

```bash
$ cat /tmp/alpine.cdx.json | python3 -c "
import sys,json
d=json.load(sys.stdin)
for c in d['components'][:10]:
    name=c['name']
    lics=[l['license'].get('id',l['license'].get('name','?')) for l in c.get('licenses',[])]
    print(f'{name}: {lics}')
"
```

真實輸出：

```
alpine-baselayout: ['GPL-2.0-only']
alpine-baselayout-data: ['GPL-2.0-only']
alpine-keys: ['MIT']
apk-tools: ['GPL-2.0-only']
busybox: ['GPL-2.0-only']
busybox-binsh: ['GPL-2.0-only']
ca-certificates-bundle: (只有 id 欄位，MIT 和 MPL-2.0 各一個 license 物件)
libc-utils: (BSD-2-Clause 和 BSD-3-Clause 各一個 license 物件)
libcrypto3: ['Apache-2.0']
libssl3: ['Apache-2.0']
```

CycloneDX 的複合授權用多個 `license` 物件的陣列表達，而不是一個 license expression 字串。例如 `ca-certificates-bundle`：

```json
"licenses": [
  { "license": { "id": "MPL-2.0" } },
  { "license": { "id": "MIT" } }
]
```

這等同於 SPDX 的 `(MPL-2.0 AND MIT)`，但表達方式不同。如果要在 CycloneDX 用 license expression，要用：

```json
"licenses": [
  { "expression": "MPL-2.0 AND MIT" }
]
```

兩種寫法 CycloneDX 都接受，但語意微妙不同：物件陣列表示「有這幾個授權」，expression 字串表示「這個 SPDX expression」（可以帶 OR、WITH 等）。syft 的實作是用物件陣列。

## 底層機制：SPDX 的雙欄位設計

```
licenseDeclared vs licenseConcluded

  套件來源
  (APK/npm/pypi...)
       │
       │ 讀 PKGINFO / package.json / setup.cfg 的授權欄位
       ▼
  licenseDeclared: "GPL-2.0-only"     ← 元件自己宣告的授權
  （syft 能讀到這個）                   （可能不完整或不準確）

       │
       │ 人工審查 / 授權掃描工具分析實際源代碼
       ▼
  licenseConcluded: "GPL-2.0-only"    ← SBOM 發行者「認定」的授權
  （syft 不做這步，填 NOASSERTION）     （需要人或合規工具介入）
```

為什麼要有兩個欄位？因為 `licenseDeclared` 可能是錯的或不完整的：
- 套件 `package.json` 寫 `"license": "MIT"`，但源代碼裡有一個文件是 BSD-2-Clause。
- 作者改了授權但忘記更新 package metadata。
- 套件聲稱 `Apache-2.0` 但實際包含了 GPL 代碼（授權污染）。

`licenseConcluded` 是「做過審查後的判斷」。在 syft 的輸出裡，這個欄位永遠是 `NOASSERTION`，因為 syft 只讀元資料，不掃源代碼。如果你要填正確的 `licenseConcluded`，要麼用專業的授權掃描工具，要麼人工審查後手動更新 SBOM。

## Copyleft、傳染性、授權不相容

SBOM 的授權欄位之所以重要，不只是「記錄一下」，是因為授權之間有具體的法律限制。

### Copyleft 傳染性分類

```
授權強度（傳染性由強到弱）

強 copyleft
  ├── GPL-2.0-only / GPL-3.0-only   ← 連結即傳染
  ├── AGPL-3.0-only                 ← 網路使用也要開源（比 GPL 更嚴）
  └── EUPL-1.2                      ← 歐盟版本的強 copyleft

弱 copyleft
  ├── LGPL-2.1-only / LGPL-2.1-or-later ← 動態連結不傳染，靜態連結要注意
  ├── MPL-2.0                             ← 只有修改的文件需要開源
  └── CDDL-1.0                            ← 文件級別的 copyleft

Permissive（寬鬆）
  ├── MIT                           ← 最寬鬆，幾乎無限制
  ├── Apache-2.0                    ← MIT 加明確的專利授權
  ├── BSD-2-Clause / BSD-3-Clause   ← 類似 MIT，多了不同的禁止條款
  └── ISC                           ← 等同於 MIT 的精簡版
```

**強 copyleft 的傳染場景**：你把一個 `GPL-2.0-only` 的 library 靜態連結進你的閉源商業軟體，GPL 要求你必須開源整個鏈結的可執行檔（包含你的代碼）。這通常不是你想要的。

**解法**：改用 LGPL 版本（如果有的話）、改動態連結、尋找替代的 permissive 授權的 library，或取得商業授權。

### 授權不相容範例

最經典的不相容：`GPL-2.0-only` 和 `Apache-2.0` 無法混在同一個可執行檔裡（有爭議，但 FSF 官方立場是不相容）。原因是 GPL 2.0 不允許附加任何「額外限制」，而 Apache-2.0 有一個關於專利報復的條款（section 3），GPL 認為這構成額外限制。

GPL-3.0-only 修正了這個問題（明確說 Apache-2.0 相容），所以 `GPL-3.0-only` 和 `Apache-2.0` 可以共存，但 `GPL-2.0-only` 和 `Apache-2.0` 不行。

這些不相容性在現代 Linux 系統裡處處存在（musl 是 MIT，busybox 是 GPL-2.0-only，openssl 是 Apache-2.0），但它們之所以「沒問題」，是因為它們是動態連結的獨立可執行檔，不是靜態合併的單一 binary。理解連結方式是授權分析的關鍵，SBOM 只記錄授權，不記錄連結方式——所以 SBOM 是分析的起點，不是終點。

## 工具怎麼掃 License

### syft：讀宣告的

syft 讀的是套件**元資料**裡宣告的授權：
- APK：讀 `/lib/apk/db/installed` 裡的 `license:` 欄位
- npm：讀 `package.json` 的 `"license"` 欄位
- Python：讀 wheel 的 `METADATA` 或 `PKG-INFO` 的 `License:` 欄位
- Go：讀 `go.mod`（Go 本身沒有標準的 license 欄位，syft 用其他啟發式方法）

優點：快，幾秒出結果。缺點：只看元資料宣告，不掃代碼本體。如果套件宣告錯了，syft 就跟著錯。

### scancode-toolkit：掃代碼本體

[scancode-toolkit](https://github.com/nexB/scancode-toolkit) 是 AboutCode 維護的開源工具，它讀取原始碼、比對已知 license 的文字指紋。能找到：
- 代碼裡的 `// SPDX-License-Identifier: MIT` 行
- 沒有 SPDX 標記但能辨識的 license 文字（靠模式比對）
- 宣告授權和代碼授權不一致的情況

代價是慢很多（需要有源代碼，而且要跑全文搜索）。生產流程通常是：CI pipeline 跑 syft（快速），定期或 release 前跑 scancode（精確）。

### FOSSA / Black Duck：商業工具

商業工具能做的：自動 deep scan、授權相容性分析（基於知識庫）、自動生成合規報告、追蹤 license 條款義務（需要提供哪些 notice、是否需要開源）。代價是貴，但對需要交授權合規文件給客戶的企業，通常值得。

**底線**：syft 的 `licenseDeclared` 給你一個快速基線，scancode 給你更深的驗證，商業工具給你完整的合規報告。三層工具回答三個不同的問題。

## 踩雷集錦

1. **「syft 抓到 license 了，授權合規就完成了」**：syft 抓的是 `licenseDeclared`，即元件自己宣告的。如果元件宣告錯（或根本沒宣告），syft 不會發現。真正的授權合規還需要：(1) 掃代碼本體確認宣告是否準確；(2) 分析授權相容性；(3) 追蹤授權義務（要附哪些 notice）。`licenseDeclared` 是起點，不是終點。

2. **「GPL-2.0 和 GPL-2.0-only 是一樣的」**：不對。`GPL-2.0` 是舊的 SPDX identifier（在 License List 3.x 裡已棄用），`GPL-2.0-only` 是明確說「僅限 v2」的現代 identifier，`GPL-2.0-or-later` 是「v2 或更新版本」。用 `GPL-2.0` 在新的 SPDX 驗證工具上可能會報警告。

3. **「LGPL 就代表可以隨便連結」**：LGPL 允許你的程式透過「動態連結」使用 LGPL library 而不用開源你的程式，但「靜態連結」LGPL library 通常要求提供 object file 讓使用者可以重新連結（LGPL v2.1 section 6）。很多嵌入式系統工程師在靜態連結 musl 時沒想到這個問題。

4. **「`NOASSERTION` 代表工具不知道授權是什麼」**：更精確的說法是「工具不對授權做斷言」。syft 其實讀到了 `licenseDeclared`，但在 `licenseConcluded` 填 `NOASSERTION` 是說「我沒有做過授權審查，我不確認這個授權判斷」。NOASSERTION 是謹慎的立場，不是無知的表現。

5. **「Apache-2.0 和 MIT 可以自由混用，沒有任何限制」**：Apache-2.0 要求你在再發行時包含 NOTICE 文件（如果原套件有的話），MIT 也要保留著作權聲明。這些不是大問題，但「permissive 就是沒有任何義務」是錯誤認知。義務只是比 copyleft 輕很多，不是零。

6. **「license expression 的 AND 代表『可以選其中一個』」**：正好相反。AND 是「必須同時遵守」，OR 才是「可以選一個」。`MIT AND Apache-2.0` 要求你同時滿足 MIT 條款和 Apache-2.0 條款；`MIT OR Apache-2.0` 才是二選一。這是很多工程師反直覺的地方。

## 進階：再往深一層

### SPDX licenseInfoFromFiles

除了 `licenseDeclared`，SPDX Package 還有一個欄位叫 `licenseInfoFromFiles`——記錄這個 package 所有已知文件裡掃到的授權。如果一個套件的源代碼裡有五個文件各帶不同授權聲明，這個欄位就會列出所有五個。

syft 對 alpine apk 套件不填這個欄位（因為它沒有掃代碼文件），但對源代碼套件（如 `syft -o spdx-json /path/to/source-tree`）這個欄位才有實際意義。

### LicenseRef 自定義識別符

如果一個元件的授權在 SPDX License List 上找不到，可以用 `LicenseRef-` 前綴的自定義 identifier：

```
LicenseRef-Proprietary-Vendor-A
LicenseRef-Custom-Dual-License
```

然後在 SPDX 文件的 `otherLicensingInformationDetected` 區塊提供自定義授權的全文或參考。這讓 SPDX 能表達任意授權，不只是標準 list 上的。

### 授權義務追蹤

不同授權有不同的「義務」——你使用了這個套件，你必須做什麼：

| 授權類型 | 主要義務 |
|----------|---------|
| MIT / BSD | 保留著作權聲明（在文件或 UI 裡顯示） |
| Apache-2.0 | 保留 NOTICE 文件 + 著作權聲明 |
| LGPL | 動態連結 OK，靜態連結要提供 object file |
| GPL | 散佈時提供源代碼（或書面要約） |
| AGPL | 透過網路提供服務也算「散佈」，要提供源代碼 |

SBOM 的 license 欄位只記錄「是什麼授權」，不記錄「你的使用情境需要履行什麼義務」。義務追蹤是授權合規工具的工作，SBOM 提供的是輸入資料。

## 動手練習

1. 找出 alpine SBOM 裡所有帶 copyleft 的套件：
   ```bash
   jq '[.packages[] | select(.licenseDeclared | test("GPL")) | {name, licenseDeclared}]' /tmp/alpine.spdx.json
   ```
   數數看有幾個，想想如果你要把這個 alpine image 當作你閉源商業軟體的基底，你需要面對哪些合規問題（提示：動態連結 vs 靜態連結的差別）。

2. 找出所有有複合授權（含 AND 或 OR）的套件：
   ```bash
   jq '[.packages[] | select(.licenseDeclared | test("AND|OR")) | {name, licenseDeclared}]' /tmp/alpine.spdx.json
   ```
   仔細看 `musl-utils` 的 `(MIT AND BSD-2-Clause AND GPL-2.0-or-later)`，思考這個組合的法律含義：GPL 的傳染性和 MIT/BSD 的寬鬆性放在一起，你必須遵守 GPL 的要求。

3. 用 `jq` 統計 alpine SBOM 裡各授權的出現次數（排除 NOASSERTION）：
   ```bash
   jq '[.packages[].licenseDeclared | select(. != "NOASSERTION")] | group_by(.) | map({license: .[0], count: length}) | sort_by(-.count)' /tmp/alpine.spdx.json
   ```

## 本章重點整理

- SPDX License List 提供標準化 identifier（`MIT`、`GPL-2.0-only`、`Apache-2.0`），讓授權資訊機器可讀。`GPL-2.0` 已棄用，要用 `GPL-2.0-only` 或 `GPL-2.0-or-later`。
- License expression 語法：AND（同時遵守）、OR（選一個）、WITH（帶例外條款）、+（or-later 縮寫）。優先級：WITH > AND > OR，有疑問加括號。
- SPDX 有 `licenseDeclared`（元件宣告的）和 `licenseConcluded`（審查後認定的）；syft 的 `licenseConcluded` 全是 `NOASSERTION`，這是正確行為。CycloneDX 只有 `licenses[]`，不區分這兩層。
- syft 讀元資料（快），scancode 掃代碼本體（精確），商業工具做義務追蹤和相容性分析（完整）。
- Copyleft 有傳染性，強度：GPL/AGPL > LGPL > MPL > MIT/Apache-2.0。授權相容性是 legal 問題，SBOM 提供資料，不代替法律判斷。

## 自我檢核

- [ ] 我能說出 `GPL-2.0-only` 和 `GPL-2.0-or-later` 的法律差異
- [ ] 我能解析 `(MIT AND BSD-2-Clause AND GPL-2.0-or-later)` 這個 expression 的語意：要同時遵守三個授權
- [ ] 我知道 SPDX `licenseConcluded: NOASSERTION` 和 `licenseDeclared: GPL-2.0-only` 的差別，以及為什麼 syft 不填 `licenseConcluded`
- [ ] 我能說出 copyleft 傳染性和連結方式（靜態/動態）的關係
- [ ] 我知道 syft 的授權掃描和 scancode 的授權掃描分別在回答什麼問題

## 延伸閱讀

- **[SPDX License List](https://spdx.org/licenses/)**（SPDX 官方）
  - **讀哪裡**：首頁的搜尋框，搜尋你專案常用的授權確認 identifier 拼法；以及「Deprecated Identifiers」清單，看哪些舊寫法已被棄用
  - **為什麼值得讀**：identifier 的拼法是工具互通的基礎，`GPL-2.0` 和 `GPL-2.0-only` 不同，填錯就比對不到

- **[SPDX 2.3 Annex D：License Expression 語法](https://spdx.github.io/spdx-spec/v2.3/SPDX-license-expressions/)**（SPDX 官方）
  - **讀哪裡**：整個 Annex D 不長，包含 ABNF 語法規則和範例；重點是優先級規則
  - **為什麼值得讀**：你在寫解析 SBOM 的工具，或驗證客戶交付的 SBOM 時，這份 spec 是判斷 expression 合法性的 ground truth

- **[GPL 授權常見問題](https://www.gnu.org/licenses/gpl-faq.html)**（FSF 官方）
  - **讀哪裡**：「何謂連結」（What constitutes combining programs?）、LGPL 相關問題
  - **為什麼值得讀**：FSF 的官方解釋是 GPL 解讀的最重要參考來源。「連結方式決定傳染性」這個概念在這裡有官方解釋，雖然不是法律建議，但是業界最廣泛接受的標準詮釋

- **[scancode-toolkit](https://github.com/nexB/scancode-toolkit)**（AboutCode）
  - **讀哪裡**：README 的 Quick Start，以及 `--license` 旗標說明
  - **為什麼值得讀**：syft 的補充工具，真正掃源代碼裡的授權文字。如果你要做嚴格的授權審計，這是比 syft 更深入的開源工具

- **[TLDR Legal](https://tldrlegal.com/)**（社群維護）
  - **讀哪裡**：搜尋任何 license 短代碼（MIT、GPL-2.0、Apache-2.0），看白話文摘要
  - **為什麼值得讀**：不是法律建議，但是快速理解「這個授權主要限制什麼」的最佳入口；特別適合非法務背景的工程師

Part 2 到這裡結束。你現在清楚兩大格式各自的欄位語意、設計取向差異、授權資訊如何承載和解讀。Part 3 轉向「如何生成一份好的 SBOM」：生成策略、syft 的內部機制、各語言生態的 build-time 整合，以及如何評估一份 SBOM 的品質和完整度。

→ [Ch 9 生成策略：source vs build vs binary 分析](./09-generation-strategies.md)
