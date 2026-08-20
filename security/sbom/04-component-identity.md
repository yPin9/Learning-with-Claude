# Ch 4 — 元件識別的難題：naming / PURL / CPE / SWID

> **目標**：搞清楚為什麼「唯一識別一個軟體元件」是個真正困難的問題，三大識別體系（PURL / CPE / SWID）各自的設計哲學和取捨，以及 CPE→PURL 對不齊為什麼是漏洞誤報和漏報的根源。讀完你能解釋 `pkg:apk/alpine/libcrypto3@3.1.8-r1?upstream=openssl` 這個 PURL 每一段的意義，以及為什麼 libcrypto3 的 CPE 需要四個變體才能不漏掉比對。深挖章。

## 為什麼需要這個？

你有一份 SBOM，grype 要拿它和漏洞資料庫比對，找出哪些元件有 CVE。這個過程聽起來直接，但藏著一個根本的難題：

**NVD 的漏洞是用 CPE 描述的；你的 SBOM 的元件是用 package name 描述的；同一個東西在不同語境下可能有完全不同的名字。**

- OpenSSL 在 Alpine 叫 `libcrypto3`，在 Debian 叫 `libssl-dev`，在 Red Hat 叫 `openssl-libs`，在 npm 生態裡的 binding 叫 `node-forge`（不同東西，但功能重疊）。
- NVD 的 CVE 可能用 `cpe:2.3:a:openssl:openssl:3.1.8:...` 描述，但你的 Alpine image 裡的 package 叫 `libcrypto3`，字面上根本對不起來。
- Log4j 在 Maven 叫 `log4j-core`，在 NVD 的 CPE 裡是 `cpe:2.3:a:apache:log4j:2.14.1:...`——`log4j-core` 和 `log4j` 就是不同的字串，要人工或靠 mapping 才能對上。

這不是邊緣案例，這是常態。元件識別的不對齊，是造成漏洞掃描「誤報率高」和「漏報率高」同時存在的底層原因。理解三大識別體系如何設計、它們的局限在哪，是讓你能讀懂掃描結果、評估 SBOM 品質的前提。

## 先建立直覺：命名地獄是怎麼來的

想象一下，你有一個 library，它在世界上各種語境下的名字：

```
同一個 OpenSSL library 在不同語境下的名字：

  上游原始碼   →  openssl（openssl.org 上的 project name）
  Alpine apk   →  libcrypto3、libssl3（兩個 package，但來自同一個 openssl source）
  Debian deb   →  libssl3（≈ libcrypto3，但版本號對應方式不同）
  RHEL rpm     →  openssl-libs
  NVD CPE      →  cpe:2.3:a:openssl:openssl:3.x.x:...（用 openssl 原始名）
  PURL         →  pkg:apk/alpine/libcrypto3@...?upstream=openssl（精確知道 distro package）
  CVE 的描述   →  「OpenSSL」（人讀的描述，不是機器用的 ID）
  GitHub repo  →  openssl/openssl
  PyPI         →  cryptography（Python binding，不是 OpenSSL 本體）
```

每個命名系統都是對的——在它的語境下。問題在於這些語境之間沒有標準的 mapping，讓機器自動比對成了一個工程問題。

三大識別體系各自試圖解決這個問題，但用的方法不同，因此各有優缺點：

```
PURL：生態原生，精準，但只涵蓋 package manager 管理的東西
CPE：NVD 官方，是漏洞比對的必要介面，但格式僵硬、命名混亂
SWID：ISO 標準，企業 IT asset management 用，在開源生態裡幾乎不用
```

## PURL（Package URL）

### 設計哲學

PURL（Package URL）規範由社群主導，原始倡議人是 nexB（ScanCode 的作者），現在由 ECMA International 維護（ECMA-427 標準）。設計原則：**生態原生（ecosystem-native）**——每個 package type 用它自己生態的命名方式，加上一層標準化的 wrapper。

### 格式規範

```
scheme:type/namespace/name@version?qualifiers#subpath
```

| 組成 | 必填 | 意義 | 範例 |
|------|------|------|------|
| `scheme` | 是 | 固定為 `pkg` | `pkg` |
| `type` | 是 | package ecosystem | `apk`、`npm`、`pypi`、`maven`、`golang`、`deb` |
| `namespace` | 依 type | 命名空間（distro、org、group）| `alpine`、`@angular`、`org.apache.logging` |
| `name` | 是 | package 名稱 | `libcrypto3`、`lodash`、`log4j-core` |
| `version` | 否 | 版本 | `3.1.8-r1`、`4.17.21`、`2.14.1` |
| `qualifiers` | 否 | 額外描述（key=value）| `arch=x86_64`、`distro=alpine-3.19.9`、`upstream=openssl` |
| `subpath` | 否 | package 內的子路徑 | 少用 |

### 實際範例（從真實 syft 輸出）

```
# Alpine apk package（真實來自 alpine:3.19 的 SBOM）
pkg:apk/alpine/alpine-baselayout@3.4.3-r2?arch=x86_64&distro=alpine-3.19.9

# 同一個 source（openssl）打包成不同的 Alpine package
pkg:apk/alpine/libcrypto3@3.1.8-r1?arch=x86_64&distro=alpine-3.19.9&upstream=openssl
pkg:apk/alpine/libssl3@3.1.8-r1?arch=x86_64&distro=alpine-3.19.9&upstream=openssl
                                                                     ↑
                                              upstream qualifier 連結 distro package → 上游 project

# npm package（PURL spec 範例）
pkg:npm/lodash@4.17.21
pkg:npm/%40angular/animation@12.3.1    （@ 需要 URL encode）

# Python PyPI
pkg:pypi/django@1.11.1

# Maven（Java）
pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1

# Go module
pkg:golang/github.com/pkg/errors@v0.9.0

# Debian deb
pkg:deb/debian/curl@7.50.3-1?arch=i386&distro=jessie
```

### PURL 的優點

**精準**：知道這是哪個生態的哪個 package，有了 `pkg:apk/alpine/libcrypto3` 你就知道是 Alpine Linux 的 libcrypto3 這個 apk package，而不是 Debian 的 libssl3（雖然它們來自同一個 openssl 上游）。

**可程式化**：有了 type 和 namespace，工具可以直接查對應的 package registry API 取得更多資訊（版本歷史、known vulnerabilities 等）。

**upstream qualifier 橋接命名差距**：`upstream=openssl` 讓工具知道 `libcrypto3` 的上游是 `openssl`，可以用 openssl 的 CVE 做比對。這是 PURL 解決命名地獄最聰明的設計之一。

### PURL 的限制

- **只涵蓋 package manager 管理的東西**：如果一個 binary 是手動編譯 + 手動放進去的（curl 下載的 executable、靜態連結進去的 library），通常沒有 PURL，因為它不在任何 package manager 的 registry 裡
- **同一個上游可能有多個 PURL**：openssl 在 Alpine 是 `pkg:apk/alpine/libcrypto3` 和 `pkg:apk/alpine/libssl3`，在 Debian 又不同——要做跨發行版的漏洞比對，還是需要 upstream 資訊
- **version 語義不標準化**：`1.36.1-r20` 是 Alpine 版本號（包含 Alpine 的 patch revision）；`1.11.1` 是 Python package 版本。不同 type 的版本號格式不同，做跨 type 比對很麻煩

## CPE（Common Platform Enumeration）

### 設計哲學

CPE 是 MITRE 設計、NIST 維護的標準，最初目的是讓 NVD（National Vulnerability Database）能用統一的方式描述「這個漏洞影響哪些 platform / product / version」。CPE 的世界觀是**平台識別**，而不是 package 識別——它試圖描述「這個 product 是什麼」，而不是「這個 package 來自哪個生態」。

這個設計取向造成了 CPE 和真實 package 命名的根本張力。

### CPE 2.3 格式

```
cpe:2.3:part:vendor:product:version:update:edition:language:sw_edition:target_sw:target_hw:other
```

共 13 個欄位（以 `:` 分隔），每個欄位含義：

| 欄位 | 意義 | 常見值 |
|------|------|--------|
| `cpe:2.3` | 版本標識 | 固定 |
| `part` | `a`=application, `o`=OS, `h`=hardware | `a` |
| `vendor` | 供應商名稱（NIST 字典裡的名稱）| `openssl`、`apache`、`alpine-baselayout` |
| `product` | 產品名稱 | `openssl`、`log4j`、`alpine-baselayout` |
| `version` | 版本 | `3.1.8`、`2.14.1` |
| `update` | update 或 SP 識別 | `*`（通配符，表示任意） |
| `edition` | 版本特殊描述（已棄用）| `*` |
| `language` | 語言 | `*` |
| `sw_edition`、`target_sw`、`target_hw`、`other` | 進階描述 | 大部分是 `*` |

實務上，大多數 CPE 只有前六個欄位有意義，後面全是 `*`：

```
# openssl 的 CVE 對應的 CPE（NVD 上的官方記法）
cpe:2.3:a:openssl:openssl:3.1.8:*:*:*:*:*:*:*

# Apache Log4j 的 CPE
cpe:2.3:a:apache:log4j:2.14.1:*:*:*:*:*:*:*
```

### 命名地獄的真實案例：alpine-baselayout 有幾個 CPE？

從真實的 syft SBOM 輸出，`alpine-baselayout`（一個 Alpine 的基礎套件）有六個 CPE 變體：

```bash
# 從 /tmp/alpine.spdx.json 讀出的真實輸出
cpe:2.3:a:alpine-baselayout:alpine-baselayout:3.4.3-r2:*:*:*:*:*:*:*
cpe:2.3:a:alpine-baselayout:alpine_baselayout:3.4.3-r2:*:*:*:*:*:*:*
cpe:2.3:a:alpine_baselayout:alpine-baselayout:3.4.3-r2:*:*:*:*:*:*:*
cpe:2.3:a:alpine_baselayout:alpine_baselayout:3.4.3-r2:*:*:*:*:*:*:*
cpe:2.3:a:alpine:alpine-baselayout:3.4.3-r2:*:*:*:*:*:*:*
cpe:2.3:a:alpine:alpine_baselayout:3.4.3-r2:*:*:*:*:*:*:*
```

同樣的，`libcrypto3` 有四個 CPE 變體：

```
cpe:2.3:a:libcrypto3:libcrypto3:3.1.8-r1:*:*:*:*:*:*:*
cpe:2.3:a:libcrypto3:libcrypto:3.1.8-r1:*:*:*:*:*:*:*
cpe:2.3:a:libcrypto:libcrypto3:3.1.8-r1:*:*:*:*:*:*:*
cpe:2.3:a:libcrypto:libcrypto:3.1.8-r1:*:*:*:*:*:*:*
```

這些變體的存在原因：**沒有人知道 NVD 的 CPE dictionary 裡 vendor 和 product 欄位用的是哪個名字**。有時 vendor 和 product 用同一個字串（第一條），有時 vendor 是模組化名稱而 product 是通用名稱（第三條），有時 vendor 是更高層的 vendor name（第五條）。

syft 生成多個 CPE 變體的目的：**把可能被 NVD 匹配的所有寫法都列出來，提高比對率**。這是一種防禦性策略，但代價是 SBOM 裡的 CPE 清單很長、很嘈雜。

**這就是漏洞誤報和漏報的根源**：

- 如果 libcrypto3 的 CVE 在 NVD 裡記錄的 CPE 是 `cpe:2.3:a:openssl:openssl:3.1.8:...`（用 openssl 的名字）而不是 libcrypto3 的四個變體，則四個 CPE 全部比對不到，結果是**漏報**（你有漏洞但掃不出來）
- 如果工具生成的 CPE 比對到了 NVD 裡不相關的 product（名字相同但是不同軟體），結果是**誤報**（掃出來的漏洞其實不影響你）

### CPE 和 PURL 之間的鴻溝

用 libcrypto3 做完整示範：

```
PURL（精確，知道 distro 脈絡）：
pkg:apk/alpine/libcrypto3@3.1.8-r1?arch=x86_64&distro=alpine-3.19.9&upstream=openssl

NVD 的 CVE 記錄用的 CPE（針對 openssl 上游）：
cpe:2.3:a:openssl:openssl:3.1.8:*:*:*:*:*:*:*

syft 為 libcrypto3 生成的 CPE 變體（4 個）：
cpe:2.3:a:libcrypto3:libcrypto3:3.1.8-r1:*:*:*:*:*:*:*
cpe:2.3:a:libcrypto3:libcrypto:3.1.8-r1:*:*:*:*:*:*:*
cpe:2.3:a:libcrypto:libcrypto3:3.1.8-r1:*:*:*:*:*:*:*
cpe:2.3:a:libcrypto:libcrypto:3.1.8-r1:*:*:*:*:*:*:*

問題：NVD 用 openssl:openssl，syft 的四個變體都是 libcrypto/libcrypto3
  → 這四個 CPE 比對不到 NVD 的 openssl CVE
  → 但 PURL 的 upstream=openssl 可以橋接這個 gap
  → 所以使用 PURL 做漏洞比對的工具（如 grype 使用 OSV 格式）比只用 CPE 的工具更準
```

這個範例說明了為什麼現代漏洞掃描工具（grype、trivy）同時使用 PURL 和 CPE 做比對，而不是只用其中一個。

## 底層機制：syft 如何同時生成 PURL 和 CPE

用真實指令驗證：

```bash
$ python3 /dev/stdin << 'EOF'
import json
with open("/tmp/alpine.spdx.json") as f:
    d = json.load(f)
pkgs = d.get("packages", [])
# 只看 busybox 這個 package
for p in pkgs:
    if p["name"] == "busybox":
        refs = p.get("externalRefs", [])
        purl = next((r["referenceLocator"] for r in refs if r.get("referenceType") == "purl"), "-")
        cpes = [r["referenceLocator"] for r in refs if r.get("referenceType") == "cpe23Type"]
        print("Package:", p["name"], "@", p.get("versionInfo"))
        print("PURL:", purl)
        print("CPEs:", len(cpes), "variants")
        for c in cpes:
            print("  " + c)
        break
EOF
```

輸出：

```
Package: busybox @ 1.36.1-r20
PURL: pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9
CPEs: 1 variants
  cpe:2.3:a:busybox:busybox:1.36.1-r20:*:*:*:*:*:*:*
```

busybox 只有一個 CPE 變體（vendor=busybox，product=busybox），因為 busybox 的名字簡單，不需要生成多個變體。對比 `alpine-baselayout` 的六個 CPE 變體——名字越複雜、越難確定命名習慣的 package，syft 生成越多變體。

```bash
$ python3 /dev/stdin << 'EOF'
import json
with open("/tmp/alpine.spdx.json") as f:
    d = json.load(f)
pkgs = d.get("packages", [])
# 列出每個 package 有幾個 CPE
for p in pkgs:
    if p.get("versionInfo"):
        refs = p.get("externalRefs", [])
        cpes = [r for r in refs if r.get("referenceType") == "cpe23Type"]
        purl = next((r["referenceLocator"] for r in refs if r.get("referenceType") == "purl"), "-")
        print(p["name"] + "  CPE_count=" + str(len(cpes)) + "  has_upstream=" + ("upstream=" in purl))
EOF
```

輸出：

```
alpine-baselayout  CPE_count=6  has_upstream=False
alpine-baselayout-data  CPE_count=10  has_upstream=True
alpine-keys  CPE_count=6  has_upstream=False
apk-tools  CPE_count=6  has_upstream=False
busybox  CPE_count=1  has_upstream=False
busybox-binsh  CPE_count=4  has_upstream=True
ca-certificates-bundle  CPE_count=4  has_upstream=True
libc-utils  CPE_count=4  has_upstream=True
libcrypto3  CPE_count=4  has_upstream=True
libssl3  CPE_count=4  has_upstream=True
musl  CPE_count=1  has_upstream=False
musl-utils  CPE_count=4  has_upstream=True
scanelf  CPE_count=1  has_upstream=True
ssl_client  CPE_count=4  has_upstream=True
zlib  CPE_count=1  has_upstream=False
```

規律：
- 有 `upstream=` qualifier 的 package（libcrypto3、libssl3、ca-certificates-bundle 等），代表它們是某個上游專案的 distro 包裝，CPE 往往有更多變體（因為 distro 名和上游名都可能是 CPE 的 vendor/product）
- 名字不複雜的 package（busybox、musl、zlib）只有一個 CPE 變體
- `alpine-baselayout-data` 有 10 個 CPE 變體——因為它的名字有連字號又有分隔，各種組合（連字號 vs 底線、hyphenated vs underscored）都要試

## SWID（Software Identification Tags）

SWID tag 是 ISO/IEC 19770-2 標準，由 NIST 和 SWID tag 工作組維護。設計背景是**企業 IT asset management**——知道你的組織在哪些機器上裝了哪些軟體、哪些版本、license 是否合規。

SWID tag 是一個 XML 文件，隨軟體安裝一起部署（通常放在 `/var/lib/swid/` 或 Windows 的 `%PROGRAMFILES%`）：

```xml
<SoftwareIdentity
  name="openssl"
  tagId="openssl.org-openssl-3.1.8"
  version="3.1.8"
  xmlns="http://standards.iso.org/iso/19770/-2/2015/schema.xsd">
  <Entity
    name="OpenSSL Software Foundation"
    regid="openssl.org"
    role="softwareCreator tagCreator"/>
</SoftwareIdentity>
```

### SWID 在 SBOM 生態的地位

NTIA minimum elements 把 SWID 列為三個機器可讀格式之一，但在實務上，SWID 在開源生態裡的採用率遠低於 SPDX 和 CycloneDX：

- **優點**：是 ISO 國際標準；enterprise 軟體廠商（Microsoft、Oracle、SAP）有採用；能和 NIST SCAP（Security Content Automation Protocol）整合
- **限制**：
  - 開源 package 幾乎沒有 SWID tag（沒有強制機制要求 open-source project 生成它）
  - 主要靠軟體「自我聲明」（安裝時放進去），而不是事後掃描識別
  - 工具生態遠不如 SPDX / CycloneDX 成熟
  - CycloneDX 支援把 SWID tag 資訊嵌入 SBOM（`swid` 欄位），這是它最常見的出現方式

**結論**：如果你在 enterprise 環境用 SCCM（System Center Configuration Manager）或 CSAM（Cyber Security Asset Management），你可能會遇到 SWID。在 container / cloud native / 開源生態裡，SWID 幾乎不是你需要手動處理的東西。SPDX 和 CycloneDX 才是主戰場。

## 對比與取捨：三大識別體系

| 維度 | PURL | CPE 2.3 | SWID |
|------|------|---------|------|
| 標準化組織 | ECMA International（ECMA-427） | NIST | ISO/IEC 19770-2 |
| 格式 | URI-like 字串 | 冒號分隔字串（13欄）| XML 文件 |
| 命名哲學 | 生態原生（ecosystem-native） | 平台識別（vendor:product:version）| 軟體自我聲明 |
| 漏洞比對適用性 | 高（配合 OSV）| 必要（NVD 用 CPE）| 低 |
| 開源生態採用率 | 高（syft/grype/CycloneDX 主用）| 高（NVD 基準）| 低 |
| 命名一致性 | 高（ecosystem 內部一致）| 低（vendor/product 命名混亂）| 高（XML schema 強制）|
| 工具生成準確率 | 高 | 中（syft 靠多變體補足）| 低（大部分工具不生成）|
| 能描述「上游關係」| 是（upstream qualifier）| 否 | 否 |
| 適合 container | 是 | 是（但命名問題多）| 否 |
| 適合 Enterprise IT | 中 | 是 | 是 |

### 實務上怎麼選

你不需要「選一個」，而是理解它們各自的作用：

```
PURL  →  SBOM 內部的元件識別、package registry 查詢、OSV 漏洞比對
CPE   →  NVD 漏洞比對的必要介面（無論你喜不喜歡，NVD 用 CPE）
SWID  →  企業 IT 資產管理（如果你的組織用相關工具）

實務建議：
- 你的 SBOM 工具（syft/trivy）應該同時輸出 PURL 和 CPE
- 用 grype 或 trivy 掃漏洞時，它們會同時用兩種做比對，取聯集結果
- 不要只依賴 CPE 做比對，因為命名混亂；也不要只依賴 PURL，因為 NVD 必須靠 CPE
- 關注 upstream qualifier（upstream=openssl 這類），它是橋接 distro 命名和 upstream CVE 的關鍵
```

## 踩雷集錦

**1. 「只要 package name 一樣就是同一個東西」**

完全不能這樣假設。`openssl` 在不同語境下可能是：
- Python 的 `openssl` package（PyPI，不是 OpenSSL 本體，是一個早已廢棄的舊 binding）
- Go 的 `github.com/openssl/openssl`（Go binding）
- Alpine 的 `openssl` package（確實是 OpenSSL 本體的 apk）

PURL 的 `type` 欄位（`pkg:pypi/openssl` vs `pkg:apk/alpine/openssl`）解決了這個問題。沒有 PURL type 的情況下，只看 name 是不可靠的。

**2. 「CPE 比對到了就代表有這個漏洞」**

CPE 比對只是第一步，告訴你「版本範圍可能受影響」。CPE 的精確度不夠高，特別是在 `*` 通配的欄位。很多 CVE 的 CPE 是 `cpe:2.3:a:openssl:openssl:*:*:*:*:*:*:*:*`（任意版本），需要另外查 advisory 的 version range 才能確定你的版本是否真的受影響。這就是漏洞掃描誤報的另一個來源——CPE 匹配上了，但版本其實不在受影響範圍。

**3. 「PURL 的版本號就是 upstream 的版本號」**

Alpine 的版本號帶有 Alpine 自己的 patch revision：`1.36.1-r20` 裡的 `r20` 是 Alpine 的 release 號，不是 busybox upstream 的。這個 `-rN` suffix 不存在於上游的版本號裡。如果你拿 `1.36.1-r20` 去 NVD 的 CPE 查，可能查不到，因為 NVD 用的是 `1.36.1`。PURL 的 qualifier `distro=alpine-3.19.9` 讓工具知道這是 Alpine distro 的版本號，可以相應剝掉 `-rN` 再做比對。

**4. 「syft 生成的 CPE 是官方的 CPE」**

不是。syft 生成的 CPE 是**猜測性的**（heuristic-based），它用 package 名稱套幾種常見的 vendor/product 命名模式，生成可能正確的 CPE 清單。真正「官方的」CPE 在 NVD 的 CPE Dictionary 裡，由 NIST 維護。如果一個 package 在 NVD CPE Dictionary 裡根本沒有對應的 entry，syft 猜出來的 CPE 也不會比對到任何 CVE。這個 gap 是整個 SBOM 生態的技術債之一。

**5. 「PURL 和 CPE 只需要一個就夠了」**

不行。CPE 是 NVD 的語言，不管你喜不喜歡，要查 NVD 的漏洞就必須說 CPE。PURL 是 package registry 的語言，精準、可程式化，是現代工具的首選。OSV（Google 的 Open Source Vulnerabilities database）主要用 PURL；NVD 用 CPE。一個完整的漏洞掃描同時查兩個資料庫，所以需要兩個識別符。

## 進階：再往深一層

**CPE Dictionary 的問題**：NIST 的 CPE Dictionary 是一個人工維護的資料庫，目前（2025）有超過 100 萬個 entry，但收錄非常不均衡——Windows 軟體收錄很完整，Linux 發行版的套件收錄嚴重不足。很多 Alpine 的套件根本不在 CPE Dictionary 裡，這直接造成漏洞比對的死角。這個問題是 grype 使用 OSV 格式（用 PURL 比對）的主要動機之一。

**OSV（Open Source Vulnerabilities）**：Google 在 2021 年推出的漏洞格式，主要設計來和 PURL 互操作。OSV 的優點是直接用 package ecosystem + name + version range 描述漏洞，不需要 CPE 的間接映射。grype 和 osv-scanner 的比對準確率比只用 CPE 的工具高，在開源生態的覆蓋率更好。Ch 14 會深挖漏洞資料庫的全景。

**CPE→PURL mapping 的工程**：有一些工作在試圖建立 CPE 和 PURL 之間的 mapping 表（community 維護的 `cpe-guesser`、RedHat 的 `cpe-dictionary` 等），讓比對更準確。CISA 也在推動更好的 identifier mapping 工作（2023 年的 SBOM Sharing Guidance 裡有提到）。這個問題目前沒有完全解決，是這個領域的活躍研究方向。

## 動手練習

1. 對 `/tmp/alpine.spdx.json`，找出所有有 `upstream=` qualifier 的 PURL：

   ```python
   import json
   with open("/tmp/alpine.spdx.json") as f:
       d = json.load(f)
   for p in d["packages"]:
       for ref in p.get("externalRefs", []):
           if ref.get("referenceType") == "purl" and "upstream=" in ref["referenceLocator"]:
               upstream = [q for q in ref["referenceLocator"].split("?")[1].split("&") if "upstream=" in q]
               print(p["name"], "→ upstream:", upstream)
   ```

   列出哪些 Alpine package 的上游是什麼。這些 upstream 對漏洞比對有什麼意義？

2. 找出 CPE 變體最多的那個 package（你在「底層機制」節看到了 `alpine-baselayout-data` 有 10 個）。把這 10 個 CPE 全部列出來，分析它們之間的差異模式（vendor/product 的命名組合）。這告訴你 syft 的 CPE heuristic 邏輯是什麼？

3. 打開 NVD（`nvd.nist.gov/vuln/search`），搜尋 CVE-2023-45853（zlib 的一個 CVE），看它的官方 CPE 是什麼。和 `/tmp/alpine.spdx.json` 裡 zlib package 的 CPE 比對，能對上嗎？這說明了什麼？

4. 把以下三個 PURL 拆解開，解釋每個部分的意義：
   - `pkg:maven/org.apache.logging.log4j/log4j-core@2.14.1`
   - `pkg:apk/alpine/libssl3@3.1.8-r1?arch=x86_64&distro=alpine-3.19.9&upstream=openssl`
   - `pkg:golang/github.com/pkg/errors@v0.9.0`

## 本章重點整理

- **元件識別是整個 SBOM 生態最底層的難題**：同一個東西在不同語境下有不同名字，機器比對需要統一的識別符。
- **PURL**（Package URL）：生態原生、精準，`upstream=` qualifier 橋接 distro 名和上游名，是現代工具的首選。格式：`pkg:type/namespace/name@version?qualifiers`。
- **CPE 2.3**：NVD 的語言，13 個欄位，vendor:product:version 是核心三欄，大部分其餘欄位是 `*`。命名混亂，同一個 package 可能需要多個 CPE 變體才能覆蓋所有比對可能。
- **syft 同時生成 PURL 和 CPE**，兩者並存於 SBOM 的 `externalRefs` 欄位。CPE 變體的數量反映命名的不確定程度（busybox=1，alpine-baselayout-data=10）。
- **漏洞誤報/漏報的根源**：CPE 命名混亂讓比對不準；PURL upstream qualifier 和 OSV 格式是改善方向。
- **SWID**：ISO/IEC 19770-2，enterprise IT asset management 用，在開源生態幾乎不出現，了解即可。

## 自我檢核

- [ ] 我能拆解 `pkg:apk/alpine/libcrypto3@3.1.8-r1?arch=x86_64&distro=alpine-3.19.9&upstream=openssl` 的每一段，解釋它的意義
- [ ] 我知道 CPE 格式有 13 個欄位，其中 part/vendor/product/version 是最重要的，`*` 是通配符
- [ ] 我理解為什麼 `alpine-baselayout` 需要 6 個 CPE 變體而 `busybox` 只需要 1 個
- [ ] 我能解釋 `upstream=openssl` qualifier 如何橋接 libcrypto3 這個 Alpine package 和 NVD 的 openssl CVE
- [ ] 我知道 SWID 存在、在哪個場景被使用，以及為什麼它在開源生態裡不重要
- [ ] 我做了練習 3（NVD 的 zlib CPE），並能說出比對結果說明了什麼

## 延伸閱讀

- **[PURL Specification（ECMA-427）](https://github.com/package-url/purl-spec)**（ECMA International / GitHub）
  - **讀哪裡**：`README.rst` 裡的格式規範，以及 `purl-types.md`（每種 ecosystem 的 PURL 範例）——這是 PURL 的一手規範
  - **和本章的關聯**：本章所有 PURL 範例的規範來源；type 和 qualifier 的完整清單

- **[NIST NVD CPE 搜尋](https://nvd.nist.gov/products/cpe/search)**（NIST）
  - **讀哪裡**：直接搜尋你用到的 package（如 openssl），看 NVD 官方怎麼記 vendor 和 product——和 syft 生成的 CPE 對比
  - **和本章的關聯**：理解「syft 猜的 CPE」和「NVD 官方 CPE」之間的落差；練習 3 的直接操作平台

- **[OSV Schema](https://ossf.github.io/osv-schema/)**（OpenSSF）
  - **讀哪裡**：Package 欄位的定義——OSV 怎麼用 PURL 識別受影響的 package；和 NVD CPE 格式的對比
  - **和本章的關聯**：理解為什麼 grype 使用 OSV 格式能比只用 NVD/CPE 的工具更準確地比對開源漏洞

- **[CISA「SBOM Sharing Guidance」（2023）](https://www.cisa.gov/resources-tools/resources/sbom-sharing-primer)**（CISA）
  - **讀哪裡**：Section on identifiers——CISA 對 identifier 問題的官方立場和建議
  - **和本章的關聯**：政策層面如何看待 PURL/CPE 的 identifier 問題，是後面法規討論的前導

- **[SWID Tag Portal](https://csrc.nist.gov/Projects/Software-Identification-SWID/)**（NIST CSRC）
  - **讀哪裡**：Overview 頁面——SWID 的設計目標和用例，以及和 CPE 的整合關係
  - **和本章的關聯**：補完三大識別體系的最後一塊；確認 SWID 的真實定位（enterprise IT，非開源生態主流）

Part 1 結束。你現在有了 SBOM 的完整心智模型：它是什麼（Ch 2 的 dependency graph）、要包含什麼（Ch 3 的 minimum elements 和六型）、元件怎麼識別（Ch 4 的 PURL/CPE/SWID）。下面進入 Part 2——格式深挖，把 SPDX 和 CycloneDX 兩大標準格式的實際結構和設計取捨弄清楚。

→ [Ch 5 SPDX 深挖](./05-spdx-deep-dive.md)
