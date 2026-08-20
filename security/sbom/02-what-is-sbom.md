# Ch 2 — SBOM 的本質：從製造業 BOM 到 dependency graph

> **目標**：建立 SBOM 的正確資料模型——它不是 flat list，是一張有向圖。理解 component 的最小描述、直接依賴 vs 傳遞依賴、diamond dependency，以及真實工具輸出裡的 relationship 欄位。讀完你能拿著真實 SBOM 的 JSON 指出「這就是 dependency graph」。

## 為什麼需要這個？

SBOM 這個詞讓很多人直覺想到「一張依賴清單」。這個直覺對一半，錯一半。

對的部分：它確實是一份清單，列出系統裡有哪些軟體元件。
錯的部分：如果你把它當成一個 flat list（平坦列表），你就漏掉了最重要的東西——**元件之間的關係**。

為什麼關係這麼重要？假設你知道一個漏洞在 `libssl3@3.1.8-r1`，你需要知道的不只是「系統裡有這個版本」，你還需要知道**哪些元件依賴它**——因為那些依賴 libssl3 的元件，可能因為使用方式不同而有不同的暴露程度。這個「誰依賴誰」的資訊就是 dependency graph，而它存在 SBOM 的 relationship/dependencies 欄位裡，不在 component 清單裡。

## 先建立直覺：製造業的 BOM

SBOM 這個詞來自製造業的 BOM（Bill of Materials，物料清單）。在製造業，BOM 是「製造一件產品需要哪些零件、每個零件又由哪些次零件組成」的階層清單。

```
一台桌上型電腦的 BOM（概念示意）

電腦
├── 主機板
│   ├── CPU 插槽
│   ├── 記憶體插槽（×4）
│   └── 晶片組
│       ├── PCH 北橋
│       └── PCH 南橋
├── CPU（Intel Core i7-13700K）
├── 記憶體模組（×2，16GB DDR5）
├── 固態硬碟（Samsung 980 Pro 1TB）
└── 電源供應器（850W）
    ├── 變壓電路
    └── 風扇組件
```

汽車廠、飛機製造商用 BOM 管理上萬個零件，追蹤哪個批次的哪個零件從哪個供應商來，出問題時精確召回。

軟體的 BOM 邏輯完全一樣——你的應用程式是由哪些元件組成的，每個元件又依賴哪些其他元件，每個元件來自哪個「供應商」（upstream 維護者），版本是多少。

差異在哪裡？製造業的 BOM 是樹（tree），因為實體零件的組成關係是嚴格的階層，一個零件只屬於一個上層元件。軟體的 BOM 是有向圖（directed graph），因為一個函式庫可以被多個其他函式庫同時依賴。這個差別讓軟體依賴管理比製造業 BOM 複雜得多。

## 核心資料模型：component + dependency graph

### 一個 component 的最小描述

SBOM 裡每個元件（component）至少需要這些欄位（對應 NTIA 2021 minimum elements，Ch 3 會詳細展開）：

```
component {
    name         = "busybox"                  ← 元件名稱
    version      = "1.36.1-r20"              ← 版本
    identifier   = "pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9"
                                              ← 唯一識別符（PURL），Ch 4 展開
    supplier     = "Alpine Linux Project"     ← 供應者
    relationship = "DEPENDENCY_OF alpine"     ← 它和其他元件的關係
}
```

光有這五個欄位，你就能回答「系統裡有什麼、從哪來、和誰有關係」。真實的 SBOM 格式（SPDX / CycloneDX）在這基礎上加了 hash、license、file list、checksum 等欄位，但最小核心就是這五件事。

### dependency graph 的結構

把 alpine:3.19 的真實依賴關係畫出來（從 syft 生成的 SBOM 的 DEPENDENCY_OF 關係）：

```
                        alpine:3.19（image 本體）
                            │
              ┌─────────────┼─────────────────┐
              │             │                  │
    alpine-baselayout    musl              apk-tools
              │          1.2.4              2.14.4
              │           │  │  │  │  │  │    │
    alpine-baselayout-  bus- zlib ssl lib- mus- scanelf
         data           ybox      _cl cryp l-   1.3.7
                        1.36  1.3 ient pto3 util
                              .1       3.1  s
                         │
                      busybox-
                       binsh
                       1.36

（簡化，完整圖有 16 個節點 20 條 DEPENDENCY_OF 邊）
```

真實的依賴關係，從 SBOM 的 `DEPENDENCY_OF` relationship 讀出來：

```
alpine-baselayout-data@3.4.3-r2  →  alpine-baselayout@3.4.3-r2
busybox@1.36.1-r20               →  busybox-binsh@1.36.1-r20
zlib@1.3.1-r0                    →  apk-tools@2.14.4-r0
libcrypto3@3.1.8-r1              →  libssl3@3.1.8-r1
libcrypto3@3.1.8-r1              →  ssl_client@1.36.1-r20
libcrypto3@3.1.8-r1              →  apk-tools@2.14.4-r0
libssl3@3.1.8-r1                 →  ssl_client@1.36.1-r20
libssl3@3.1.8-r1                 →  apk-tools@2.14.4-r0
musl@1.2.4_git20230717-r5       →  busybox@1.36.1-r20
musl@1.2.4_git20230717-r5       →  zlib@1.3.1-r0
musl@1.2.4_git20230717-r5       →  libcrypto3@3.1.8-r1
musl@1.2.4_git20230717-r5       →  libssl3@3.1.8-r1
musl@1.2.4_git20230717-r5       →  musl-utils@1.2.4_git20230717-r5
musl@1.2.4_git20230717-r5       →  ssl_client@1.36.1-r20
musl@1.2.4_git20230717-r5       →  scanelf@1.3.7-r2
musl@1.2.4_git20230717-r5       →  apk-tools@2.14.4-r0
scanelf@1.3.7-r2                 →  musl-utils@1.2.4_git20230717-r5
musl-utils@1.2.4_git20230717-r5 →  libc-utils@0.7.2-r5
busybox-binsh@1.36.1-r20        →  alpine-baselayout@3.4.3-r2
ca-certificates-bundle@20250911-r0 → apk-tools@2.14.4-r0
```

你看到的是一個有向圖，而不是一個 flat list。`apk-tools` 被 zlib、libcrypto3、libssl3、musl、ca-certificates-bundle 共同依賴——如果 `apk-tools` 有漏洞，你能從這張圖知道「它在整個系統裡有多核心」。

## 直接依賴 vs 傳遞依賴

這個區別在 SBOM 的完整性討論裡極其重要：

**直接依賴（direct dependency）**：你在 package.json / pom.xml / go.mod 裡明確寫下的依賴。你知道你加了它，你知道你選了那個版本。

**傳遞依賴（transitive dependency）**：你的直接依賴所依賴的東西。你沒有明確選它，它是被帶進來的，你可能完全不知道它的存在。

```
你的 pom.xml
├── spring-boot-starter-web 3.2.0    ← 直接依賴（你寫的）
│   ├── spring-webmvc 6.1.x          ← 傳遞依賴（Spring Boot 帶進來的）
│   │   └── spring-context 6.1.x     ← 再往下一層
│   ├── tomcat-embed-core 10.1.x     ← 傳遞依賴
│   │   └── tomcat-embed-el 10.1.x   ← 再往下
│   └── jackson-databind 2.15.x      ← 傳遞依賴
│       ├── jackson-core 2.15.x      ← 又一層
│       └── jackson-annotations 2.15 ← 又一層
└── log4j-core 2.14.1               ← 直接依賴（你寫的，這個是 Log4Shell 的問題版本）
    └── log4j-api 2.14.1            ← 傳遞依賴
```

Log4Shell 的殺傷力來自：很多系統的 log4j 不是直接依賴，是被 Elasticsearch、Kafka、Struts 等框架作為傳遞依賴帶進來的。你的 `pom.xml` 裡沒有寫 log4j，但系統裡確實在跑它。

一份品質好的 SBOM 要能追蹤傳遞依賴，不只是直接依賴。這是 SBOM 生成的核心挑戰之一，Ch 9 和 Ch 12 會深挖。

## 底層機制：從真實 SBOM 看 graph 結構

看 syft 生出的真實 SPDX SBOM 裡 relationship 欄位的實際結構。指令：

```bash
$ syft alpine:3.19 -o spdx-json=/tmp/alpine.spdx.json
```

用 Python 看 SBOM 摘要（因為 jq 對大型巢狀 JSON 較難操作）：

```python
import json
with open("/tmp/alpine.spdx.json") as f:
    d = json.load(f)

print("SPDX version:", d["spdxVersion"])      # SPDX-2.3
print("packages:", len(d["packages"]))         # 16
print("relationships:", len(d["relationships"])) # 131
```

輸出：
```
SPDX version: SPDX-2.3
packages: 16
relationships: 131
```

131 條 relationships 對 16 個 packages，這個數字說明什麼？大量的 relationship 是 `CONTAINS`（記錄 package 包含哪些具體的檔案），這是 SBOM 的細粒度記錄，讓你能追蹤到「這個漏洞影響的是哪個 binary 檔案」。

Relationship 的類型分布：

```
CONTAINS:      95  （package → 它包含的檔案）
DEPENDENCY_OF: 20  （A 是 B 的依賴）
OTHER:         15  （其他關係，如指向 DB 來源）
DESCRIBES:      1  （文件本體描述 root package）
```

這是 SBOM 不只是 flat list 的直接證明——它有四種不同語義的關係，構成一張完整的有向圖。

### 用 PURL 唯一識別元件（預告 Ch 4）

從同一份 SBOM 看 component 的識別欄位：

```
busybox:
  purl = pkg:apk/alpine/busybox@1.36.1-r20?arch=x86_64&distro=alpine-3.19.9
  cpe  = cpe:2.3:a:busybox:busybox:1.36.1-r20:*:*:*:*:*:*:*

libcrypto3:
  purl = pkg:apk/alpine/libcrypto3@3.1.8-r1?arch=x86_64&distro=alpine-3.19.9&upstream=openssl
  cpe  = cpe:2.3:a:libcrypto3:libcrypto3:3.1.8-r1:*:*:*:*:*:*:*
         （另有 3 個 CPE 變體，處理命名不確定性）
```

`upstream=openssl` 這個 PURL qualifier 說明：Alpine 把 openssl 打包成了 `libcrypto3` 這個 package 名稱，但 upstream（上游原作者）是 openssl 專案。這個資訊對漏洞比對很關鍵——NVD 的 CVE 是對 `openssl` 的，但 Alpine 的 package 叫 `libcrypto3`，沒有這個連結，漏洞掃描工具可能比對不到。Ch 4 會深挖這個命名地獄。

## Diamond dependency：有向圖獨有的問題

製造業的 BOM 是樹，不會有 diamond dependency（菱形依賴）。軟體的有向圖會。

```
Diamond dependency 示例：

    你的應用
    /       \
框架 A      框架 B
  \           /
   crypto-lib     ← 同時被 A 和 B 依賴，但 A 要 1.x，B 要 2.x
```

npm 用 node_modules 隔離（A 和 B 各自帶一份 crypto-lib），導致重複安裝。Go modules 用 MVS（Minimum Version Selection）選一個版本讓所有人共用。Maven 用最近者優先（nearest-wins）。這些策略都在 runtime 真的只跑一個版本，但 SBOM 需要記錄哪個版本最終被選到——而 source SBOM（從 pom.xml 讀的）和 build SBOM（從 build 結果讀的）可能給出不同答案，Ch 3 會說明這為什麼是問題。

## 對比與取捨

| 概念 | 是什麼 | 不是什麼 |
|------|--------|----------|
| SBOM | 描述 artifact 裡有哪些 component 及其關係的結構化文件 | 一份 txt 的 dependency 列表 |
| Flat list | `pip list` / `npm list` 的輸出 | SBOM（缺少關係、唯一識別符、機器可讀的標準結構） |
| Dependency graph | SBOM 裡 relationship 欄位記錄的有向圖 | 依賴樹（樹是有向無環圖的特例，但軟體依賴可以有複雜的 DAG 結構） |
| 直接依賴 | 你在 package manifest 裡明確寫的 | 全部的依賴（傳遞依賴不在 manifest 裡） |
| 傳遞依賴 | 被你的直接依賴帶進來的 | 可以被你省略不管（出事時你還是要負責） |
| component 的 identity | name + version + unique ID（purl/cpe） | 只有名字（不夠：openssl 和 libssl3 名字不同，是同一個東西） |

## 踩雷集錦

**1. 「`docker inspect <image>` 輸出就是我的 SBOM」**

`docker inspect` 輸出的是 image metadata（層 hash、環境變數、CMD 等），不是 SBOM。它告訴你 image 的結構，但不告訴你 image 裡裝了哪些套件、版本、依賴關係、PURL。SBOM 需要進入 image 的檔案系統（syft 做的事）才能產出。

**2. 「SBOM 裡的 packages 數量等於 `apt list --installed` 的行數」**

不一定。syft 會額外加一個代表 image 本體的 root package（上面 alpine 的例子是 16 個 packages，image 裡實際安裝的 Alpine package 是 15 個）。此外，syft 可能同時認出 apt/npm/pip 多種生態的 package，總數會超過任何單一 package manager 的輸出。

**3. 「我的 SBOM 有 relationships 欄位，所以我知道完整的 dependency graph」**

依賴圖的完整性取決於生成方式。Binary 分析（analyzed SBOM）可能只能認出直接依賴，看不到傳遞依賴。Source SBOM 看的是 manifest（package.json / pom.xml），能看到宣告的依賴但可能漏掉動態載入的東西。只有 build SBOM 有機會看到 build 過程真正引入的所有東西。Ch 3 和 Ch 9 會深挖每種生成方式的盲點。

**4. 「dependency graph 就是依賴樹（tree）」**

樹是每個節點只有一個 parent 的有向無環圖（DAG）。軟體依賴是 DAG，不是樹——一個函式庫可以被多個其他函式庫同時依賴（多個 parent）。Diamond dependency 就是最典型的例子。如果你的 SBOM 工具把 dependency graph 當樹輸出，它就在做近似，而不是精確描述。

## 進階：再往深一層

**SPDX 的 relationship 語義**：SPDX 2.3 定義了 40 多種 relationship type，包括 `DESCRIBES`（文件描述哪個 package）、`CONTAINS`（package 包含哪些檔案）、`DYNAMIC_LINK`（動態連結）、`STATIC_LINK`（靜態連結）、`BUILD_TOOL_OF`（build 工具而非 runtime 依賴）等。這些細分讓你能區分「這個函式庫是 runtime 依賴」還是「只是 build 時用到」，對漏洞影響評估很有用（只在 build 時用到、不在 binary 裡的 lib，就算有 CVE 也不影響你的用戶）。

**CycloneDX 的 component type**：CycloneDX 把 component 分成 library、framework、application、container、device、firmware、service 等類型。這個分類讓 SBOM 能描述更複雜的現代架構——比如一個微服務系統裡，每個服務是一個 `application`，它依賴的外部 API 是 `service`，容器基底是 `container`。這比 SPDX 的 package-centric 模型更能描述雲端架構的依賴。

**圖的可達性分析**：如果你有完整的 dependency graph，你可以做「影響分析」（impact analysis）：找出所有能到達某個有漏洞元件的路徑，也就是「哪些 component 直接或間接依賴這個有問題的 package」。這是 Dependency-Track（Ch 17）的核心功能之一。

**SBOM as a graph vs SBOM as a document**：一個很常見的架構決策問題是，要把 SBOM 當成一個靜態文件（每次 release 產一份 JSON）還是一個動態圖（持續更新的圖資料庫）。前者是今天大部分工具的做法；後者是 Dependency-Track 的做法——它把每份 SBOM 的 component 和 relationship 存進圖資料庫，讓你能跨時間、跨版本做查詢（「哪個版本第一次引入了 libcrypto3」、「哪些 service 目前還在用有漏洞的版本」）。兩種做法不互斥，可以組合：用靜態文件做每次 release 的快照，用圖資料庫做長期的持續監控。

**根 component（root package）的意義**：alpine SBOM 裡有 16 個 packages 但 Alpine 只裝了 15 個 apk package——多出來的那一個是 `alpine:3.19` image 本體，由 `DESCRIBES` relationship 作為整份 SBOM 的 root。這個設計允許 SBOM 描述「這份文件是在描述哪個 artifact」，讓消費方知道這份 SBOM 的範圍（是整個 image？還是某個 directory？還是某個具體的 binary？）。在有多個 artifact 的系統裡，root 的識別讓 SBOM 能被正確歸屬，不會張冠李戴。

## 動手練習

1. 對 Ch 0 生出來的 `/tmp/alpine.spdx.json`，用以下 Python 片段印出所有 `DEPENDENCY_OF` 關係：

   ```python
   import json
   with open("/tmp/alpine.spdx.json") as f:
       d = json.load(f)
   pkgs = {p["SPDXID"]: p["name"]+"@"+p.get("versionInfo","?") for p in d["packages"]}
   for r in d["relationships"]:
       if r["relationshipType"] == "DEPENDENCY_OF":
           src = pkgs.get(r["spdxElementId"], r["spdxElementId"][:30])
           dst = pkgs.get(r["relatedSpdxElement"], r["relatedSpdxElement"][:30])
           print(src, "→", dst)
   ```

   對照本章的 dependency graph 圖，確認你看到的和圖上一致。

2. 找出 `apk-tools` 有多少個直接依賴者（有多少個 package `DEPENDENCY_OF apk-tools`）。這個數字說明 `apk-tools` 如果有漏洞，影響面有多大。

3. 同樣的 SBOM，數一數 `CONTAINS` 關係有幾條、`DEPENDENCY_OF` 有幾條。比例是多少？想想為什麼 `CONTAINS` 遠多於 `DEPENDENCY_OF`（提示：每個 file 都有一條 CONTAINS）。

## 本章重點整理

- SBOM 的核心資料模型是 **component + dependency graph**，不是 flat list。
- 一個 component 的最小描述：name、version、unique identifier（purl/cpe）、supplier、relationship。
- Dependency graph 是有向圖，記錄誰依賴誰，包含直接依賴和傳遞依賴。
- 真實 SBOM 的 relationship 不只是依賴，還有 CONTAINS（記錄檔案）、DESCRIBES（根節點）等多種語義。
- Diamond dependency 是有向圖獨有的現象，不同語言生態用不同策略解決，SBOM 需要記錄最終選定的版本。
- `upstream=openssl` 這類 PURL qualifier 連結了 distribution package name 和上游 project name，是漏洞比對的關鍵橋樑。

## 自我檢核

- [ ] 我能說出 SBOM 資料模型的兩個核心部分（component + dependency graph），以及為什麼只有 component list 不夠
- [ ] 我知道直接依賴和傳遞依賴的差別，以及 Log4Shell 為什麼和傳遞依賴有關
- [ ] 我能解釋 diamond dependency 是什麼，以及為什麼它在樹狀結構裡不存在、在有向圖裡存在
- [ ] 我從 alpine 的 SBOM 裡找出了 `apk-tools` 的所有直接依賴者，並理解 CONTAINS vs DEPENDENCY_OF 的差異
- [ ] 我能解釋 `upstream=openssl` 在 libcrypto3 的 PURL 裡為什麼重要

## 延伸閱讀

- **[SPDX 2.3 規範：Relationships](https://spdx.github.io/spdx-spec/v2.3/relationships-between-SPDX-elements/)**（SPDX 官方）
  - **讀哪裡**：Table 1（所有 relationship types 及其定義）和 Section 11.1 的 examples
  - **和本章的關聯**：完整的 relationship 語義表，讓你理解 SBOM 裡的 graph edge 可以有多細的粒度

- **[CycloneDX 規範：Component Types](https://cyclonedx.org/docs/1.6/json/#components_items_type)**（CycloneDX 官方）
  - **讀哪裡**：component 物件的 `type` 欄位定義（library、framework、application、container 等）
  - **和本章的關聯**：展示 CycloneDX 比 SPDX 更豐富的 component 分類系統，以及它為什麼更適合描述微服務架構

- **[NTIA「Framing Software Component Transparency」](https://www.ntia.gov/files/ntia/publications/framingsbom_20191112.pdf)**（NTIA，2019）
  - **讀哪裡**：Section 2「Components and Relationships」——這份 2019 的早期文件清楚定義了 SBOM 為什麼需要 relationship 而不只是 component list
  - **和本章的關聯**：本章核心論點的官方來源

- **[Package URL Specification](https://github.com/package-url/purl-spec)**（PURL spec，GitHub）
  - **讀哪裡**：README 的格式定義和 `purl-types.md`（每種 ecosystem 的 PURL 範例）
  - **和本章的關聯**：本章提到的 PURL 格式的完整規範，Ch 4 會深挖，這裡可以先掃一遍認識語法

下一章進入細節：NTIA 定義的 minimum elements 到底有哪幾項（別靠記憶，官方文件只有七個欄位，很多人記錯）、CISA 的六型 SBOM 各在生命週期哪個位置、每種型別能看到什麼、看不到什麼。

→ [Ch 3 最小要素與生命週期：六型 SBOM 各看到什麼](./03-minimum-elements-lifecycle.md)
