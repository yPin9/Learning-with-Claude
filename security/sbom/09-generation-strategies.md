# Ch 9 — 生成策略：source vs build vs binary 分析

> **目標**：搞清楚生成 SBOM 有三條根本不同的路，每條路看到的東西不一樣、看不到的東西也不一樣。讀完你能判斷「在什麼場景該用哪種方法，以及為什麼這份 SBOM 可能不完整」。

## 為什麼需要這個？

Ch 0 我們跑過 `syft alpine:3.19`，Ch 8 講了授權資訊。現在要問一個更根本的問題：**你的 SBOM 是從哪裡長出來的？** 用什麼素材生成的？

這個問題決定 SBOM 的準確性邊界。一份 SBOM 說「我的產品包含 flask 3.0.0」，但它是怎麼知道的？

- 「它在 `requirements.txt` 裡宣告了」——如果 build 系統實際用了不同版本，這份 SBOM 就是錯的
- 「我在 build 過程中錄下了實際載入的套件」——準確，但你要改 build 流程才有
- 「我掃描了最終的 container image，從裡面的檔案推斷出來的」——不需要改任何東西，但遇到靜態連結或沒有 metadata 的 binary 就看不到

三個答案，三種準確性保證，三種適用場景。這不是「哪個最好」的問題，是「你在哪個環節、能取得什麼素材」的問題。

## 先建立直覺

把軟體生命週期想成一條流水線：

```
  寫程式          build             部署
─────────────────────────────────────────────────────────
  原始碼      →   binary/image   →  跑起來的容器
  manifest        
  lockfile     build 過程          runtime
                                   (artifact 已定型)
     ↑                ↑                  ↑
  在這裡掃         在這裡錄           在這裡掃
  (source)       (build-time)        (binary/post-build)
```

**Source-based**：在還沒 build 之前，讀開發者宣告的依賴清單。

**Build-time**：在 build 執行的過程中，讓工具「旁觀」並記錄什麼真的被用到了。

**Binary / post-build**：拿成品（binary、image、目錄），用分析工具推斷裡面有什麼。

這三條路的核心差異只有一句話：**誰知道「真的用了什麼」**。

## (a) Source-based：讀 manifest / lockfile

### 原理

Source-based 分析讀的是開發者「宣告」依賴的檔案——`requirements.txt`、`go.mod`、`package-lock.json`、`pom.xml`、`Cargo.lock`。這些檔案在 build 之前就存在，工具可以在 CI 一開始就分析。

```
  requirements.txt      go.mod            package-lock.json
  ─────────────────     ──────────────    ─────────────────
  requests==2.31.0      require (          "express": {
  flask==3.0.0            github.com/...      "version": "4.18.2"
                        )                 }
        ↓                    ↓                   ↓
          SBOM（宣告的依賴清單）
```

### 能看到什麼

- 開發者明確宣告的依賴，含版本（如果 lockfile 存在，版本是 resolved 版本）
- 直接依賴（direct dependencies）
- 如果用的是 lockfile（而非 manifest），通常也包含傳遞依賴（transitive dependencies）

### 看不到什麼

這裡有個根本限制：**manifest 是人寫的宣告，不是 build 的真實記錄**。

| 情況 | 說明 |
|---|---|
| Build 系統注入的依賴 | 某些 Maven plugin 在 build 時動態下載額外的 artifact，requirements.txt 裡沒有 |
| 動態抓取（`curl` / `wget` 在 Dockerfile 裡） | image build 時直接下載塞進去的 binary，完全不在任何 manifest |
| Build 工具自身 | 用來 build 的 webpack / gradle wrapper 版本，通常不在 requirements 裡 |
| 環境污染 | 開發者機器上已裝的全域 library 被意外拿進 build，本機 `pip list` 比 requirements.txt 多出一堆 |
| Vendored code（直接複製貼上的程式碼） | 沒有任何 manifest 記錄 |

### 適用場景

- CI 的**早期關卡**：push 之後立刻掃，不等 build 完
- 開發者自己的工作站，想快速知道「我的依賴有沒有已知 CVE」
- 沒有容器化的傳統專案

### 真實示範

```bash
# Python：syft 讀 requirements.txt，用 python-package-cataloger
syft scan dir:/tmp/sbom-demo/pyapp
```

```
NAME                VERSION     TYPE
blinker             1.7.0       python
certifi             2023.11.17  python
charset-normalizer  3.3.2       python
click               8.1.7       python
flask               3.0.0       python
idna                3.6         python
itsdangerous        2.1.2       python
jinja2              3.1.2       python
markupsafe          2.1.3       python
requests            2.31.0      python
urllib3             2.1.0       python
werkzeug            3.0.1       python
```

注意：這是因為我的 requirements.txt 已經包含所有傳遞依賴（類似 `pip freeze` 的完整 lockfile）。如果只有 `flask==3.0.0 / requests==2.31.0` 這樣的 top-level 清單，syft 只會回報 2 個 package，剩下 10 個傳遞依賴看不到。**「有 lockfile」和「有 manifest」的差距就在這裡。**

```bash
# Go：syft 讀 go.mod，用 go-module-file-cataloger
syft scan dir:/tmp/sbom-demo/goapp
```

```
NAME                    VERSION  TYPE
github.com/google/uuid  v1.6.0   go-module
github.com/pkg/errors   v0.9.1   go-module
```

---

## (b) Build-time：build 過程中錄製

### 原理

在 build 執行的當下，讓工具「旁觀」並記錄 build system 實際使用了什麼。這需要 build 系統或 plugin 的配合。

```
  mvn package                   npm ci
  ──────────────────────────    ────────────────────────
  Maven Resolver 解析依賴          npm ci 用 lockfile 裝
  ↓                              ↓
  CycloneDX Maven Plugin         cyclonedx-npm 旁觀
  記錄 resolution 結果              記錄 node_modules 內容
  ↓                              ↓
  target/*.cdx.json              bom.json（CycloneDX 格式）
```

### 能看到什麼

Build-time 方法能看到**真正被 build system 解析和用到的**依賴，包括：

- 實際 resolved 版本（不只是宣告的範圍，如 `^4.18.0` 解析成 `4.18.2`）
- Build plugin 注入的依賴
- Build graph（誰依賴誰的完整關係圖）

### 看不到什麼

- Build 完之後才在 Dockerfile 裡加的東西（`COPY`、`curl` 下載的 binary）
- Runtime 的 OS 套件（apt 裝的那些）

### 適用場景

- **精度要求最高的場景**：法規合規（EO 14028 要的那種 SBOM）、高安全要求的軟體（醫材、車用）
- 已有完善 build pipeline、願意加 plugin 的團隊
- 需要準確 dependency graph（不只清單，還要知道誰依賴誰）的場景

### 常用工具

| 語言 / 生態 | 工具 | 指令 |
|---|---|---|
| Java / Maven | cyclonedx-maven-plugin | `mvn org.cyclonedx:cyclonedx-maven-plugin:makeAggregateBom` |
| Java / Gradle | cyclonedx-gradle-plugin | `./gradlew cyclonedxBom` |
| Node / npm | cyclonedx-npm | `cyclonedx-npm --output-format JSON bom.json` |
| Python | cyclonedx-py | `cyclonedx-py environment -o bom.json` |
| Go | syft on go.mod 或 binary | （見下文） |
| Rust | cargo-cyclonedx | `cargo cyclonedx` |

Ch 11 會對每個生態做完整示範。

### Go 的特殊情況

Go 在這條軸線上比較特殊：**Go 的 build 本身就把 build info 嵌進 binary**（`go version -m <binary>` 可讀出來）。所以 Go 的「binary 掃描」準確度與「build-time 記錄」幾乎相同，不需要額外 plugin。這是 Go 少數設計上就替 SBOM 著想的特性，其他語言沒有這個待遇。

---

## (c) Binary / post-build 分析：事後掃成品

### 原理

什麼都不改，直接對成品動手——container image、tar、目錄、執行檔。工具把這些「解剖」，靠各種「指紋」推斷裡面有什麼。

```
  container image
  ────────────────────────────────────────────────
  Layer 1: ubuntu 基底 → /var/lib/dpkg/status
                        ↑
                        dpkg-db-cataloger 讀這個
  Layer 2: pip install  → /usr/local/lib/python3.12/
                          dist-packages/flask/METADATA
                        ↑
                        python-installed-package-cataloger 讀這個
  Layer 3: go binary    → ELF 裡的 .go.buildinfo section
                        ↑
                        go-module-binary-cataloger 讀這個
  ─────────────────────────────────────────────────────────
                    syft 把三層的發現合併，輸出 SBOM
```

### 能看到什麼

- 所有在 image / 目錄裡留下了「證據」的 package：OS 套件（apk/dpkg/rpm）、語言套件的 installed metadata（.dist-info、METADATA、.gemspec）、Go binary 的 build info section
- 不需要任何原始碼，對第三方 binary / 外購軟體也能掃
- 能看到 Dockerfile 裡 `curl` 下載後寫死的 Go binary（如果它是 Go 編譯的，且 build info 保留的話）

### 看不到什麼

這是 binary 分析最大的限制：**沒有留下可識別的證據，就看不到**。

| 情況 | 為什麼看不到 |
|---|---|
| C/C++ 靜態連結的 library | 編進 binary 之後沒有獨立的 metadata；syft 對一個純 C binary 的回報往往是 0 個 package |
| Strip 掉 debug info 的非 Go binary | 沒有版本字串可以找；Go 是例外，因為 build info 不在 debug 段 |
| 手動 `curl` 下載並解壓的 binary（非 Go） | 只有一個沒有任何 metadata 的執行檔 |
| Vendored source（直接複製的程式碼，沒有 pip install） | 不在任何 package database 裡 |

### 真實示範

```bash
# 掃 Go binary：go-module-binary-cataloger 讀 .go.buildinfo section
syft scan file:/tmp/sbom-demo/goapp/demo-app
```

```
NAME                    VERSION   TYPE
example.com/demo        UNKNOWN   go-module
github.com/google/uuid  v1.6.0    go-module
github.com/pkg/errors   v0.9.1    go-module
stdlib                  go1.18.1  go-module
```

注意 `stdlib go1.18.1` 這一條：source-based 掃 go.mod 看不到標準庫版本，binary 掃才看得到（因為它記在 build info 裡）。這是 binary 分析比 source 分析「多出來」的東西之一。

```bash
# 掃 alpine:3.19 image（從 registry 直接拉）
DOCKER_CONFIG=/tmp/empty-docker-config syft registry:alpine:3.19
```

```
NAME                    VERSION               TYPE
alpine-baselayout       3.4.3-r2              apk
alpine-baselayout-data  3.4.3-r2              apk
apk-tools               2.14.4-r0             apk
busybox                 1.36.1-r20            apk
...（共 15 個 apk 套件）
```

```bash
# C binary 的盲點示範：用 zlib 的簡單 C 程式，syft 什麼都看不到
# （C binary 沒有任何 package metadata）
syft scan file:/tmp/sbom-demo/demo-dynamic
# 輸出：No packages discovered
```

這個 `demo-dynamic` 是動態連結 zlib 的 C binary。即使 zlib 的 `.so` 在執行時會被 ld.so 載入，syft 掃這個 binary 本身什麼都看不到。要掃到 zlib，只有放在整個 image/系統 directory 的情境下，靠 dpkg-db-cataloger 讀 `/var/lib/dpkg/status` 才看得到。

---

## 底層機制：三種方法的資訊來源比較

```
                        SOURCE        BUILD-TIME      BINARY
                        BASED         RECORDING       ANALYSIS
                       ──────────    ────────────    ──────────
資訊來源               manifest/      build graph     artifact
                       lockfile       (plugin 記)     指紋辨識

需要改 build？          否             是              否

能看到：
  宣告的直接依賴         ✓             ✓               ✓（間接）
  傳遞依賴（lockfile）   ✓             ✓               ✓
  傳遞依賴（無lockfile） △（不完整）   ✓               △
  OS 套件               ✗             ✗               ✓（image 內）
  Go stdlib 版本         ✗             ✓               ✓
  C/C++ 靜態連結依賴     ✗             ✓               ✗
  Dockerfile COPY 的 binary △         ✗（看 build 範圍）✓（若有 metadata）
  curl 抓的 binary       ✗             ✗               ✗（幾乎不可能）

時機                    build 前       build 中         build 後/任何時候
對 CI 的侵入度           低             中高             低

✓ = 通常能看到  △ = 部分能看到  ✗ = 通常看不到
```

這張表有一個非常重要的洞察：**三種方法的盲點剛好互補，但沒有一種能全部覆蓋**。

---

## 對比與取捨

| 面向 | Source-based | Build-time | Binary / post-build |
|---|---|---|---|
| 準確性 | 中（看宣告，不看實際） | 高（看實際 build graph） | 中高（看 artifact，靠指紋）|
| 對 build 流程的要求 | 無 | 需改 build | 無 |
| 對第三方 binary 的覆蓋 | 無 | 無 | 有（若有 metadata）|
| OS 套件覆蓋 | 無 | 無 | 有 |
| C/C++ 靜態連結覆蓋 | 有（lockfile 裡） | 有（build graph） | 無（沒有 metadata）|
| 適合的 CI 階段 | 早期（pre-build） | build 中 | post-build / CD |
| 主要工具 | syft dir / trivy fs | 語言 plugin | syft image / trivy image |

沒有絕對的贏家。實務上，成熟的供應鏈安全流程會**層疊使用**：

1. Pre-commit / pre-build：source-based 快速掃，擋明顯問題
2. Build-time（至少對核心產品）：plugin 記錄 resolution 結果
3. CD pipeline：掃最終 image，這是「出貨前最後一道」

---

## 踩雷集錦

**1. 「我的 requirements.txt 就是完整的 SBOM」**

錯誤直覺：requirements.txt 裡有的就是全部。

正確認識：requirements.txt 通常只有直接依賴，甚至只有 `flask>=3.0` 這樣的範圍限制，不是具體版本。`pip install flask` 會裝 flask 及其所有傳遞依賴，但這些都不在你的 requirements.txt 裡。用 `pip freeze > requirements-lock.txt` 才是「真正裝了什麼」的完整清單，syft 掃那個才會看到全部。

**2. 「Binary 分析比 source 分析準確」**

錯誤直覺：binary 是最終產物，所以一定最準。

正確認識：「準確」要看指的是哪個面向。Binary 分析能看到 OS 套件、Go 的 stdlib 版本——這些 source 看不到。但 binary 分析對 C/C++ 靜態連結的依賴是瞎的——那些在 binary 裡沒有任何可識別的 metadata。一個用 OpenSSL 靜態連結的 C binary，syft 看它看不到 OpenSSL。Source-based + binary 各有盲點，不是誰取代誰。

**3. 「用了 build-time plugin，就不需要再掃 image 了」**

錯誤直覺：build-time 最準，掃完就夠了。

正確認識：Build-time plugin 通常只看 build tool 管理的依賴（如 Maven dependencies），不看 Dockerfile 的 base image 帶來的 OS 套件，也不看 build 完之後再複製進 image 的東西。最終 image 的完整 SBOM，依然需要 post-build 掃 image 才能取得完整的 OS 套件清單。

**4. 「Source-based 和 binary 的結果應該一樣」**

錯誤直覺：同一個專案，掃 go.mod 和掃 go binary，應該得到相同清單。

正確認識：不一樣，而且差距是可以分析的。Go binary 掃出來會多一個 `stdlib go1.18.1`，是 source 掃看不到的。Go binary 掃出來的主模組版本是 `UNKNOWN`（因為本地 build），source 掃也是 `UNKNOWN`。一個多語言 image，binary 掃出來有 OS 套件，source 掃沒有。學會看「差在哪裡」比「哪個對」更重要。

---

## 進階：再往深一層

### CI pipeline 的分層配置

真實的 pipeline 通常這樣安排：

```
  git push
    │
    ▼
  pre-build scan (source-based)
  ─ syft dir:. 或 trivy fs .
  ─ 目的：快速找已知 CVE，不等 build
    │
    ▼（build）
  build-time recording（選用，高安全要求）
  ─ Maven CycloneDX plugin / npm cyclonedx
  ─ 目的：記錄 resolution 的精確結果
    │
    ▼
  post-build scan (binary/image)
  ─ syft registry:<image> 或 trivy image <image>
  ─ 目的：最終 artifact 的完整清單，含 OS 套件
    │
    ▼
  SBOM 產出、簽章、上傳（Ch 20-21）
```

### 多份 SBOM 的合併問題

如果你跑了三種方法，你有三份 SBOM，裡面的 component 可能有重疊（同一個 flask 被三份都記錄了）也可能互補（OS 套件只在 binary scan 那份）。

把三份合成一份「最完整的 SBOM」在技術上是可行的，但有個麻煩：**同一個 component 在不同方法下可能有不同的 PURL、不同的 metadata 豐富度**。`syft merge` 指令可以做基本的合併，但合併邏輯和去重邏輯會讓結果不透明。在實務上，多數組織會選一份作為「official SBOM」（通常是 post-build image scan），再把 build-time 的精確 graph 作為補充資訊。

### 當你無法取得原始碼

對於商業現成軟體（COTS）或第三方 binary，你只能用 binary 分析。這是 binary 分析存在的重要理由之一。但要記得它的限制：如果 COTS 供應商靜態連結了一堆老舊版本的 C library，你從外面掃幾乎看不到。這是為什麼法規（如 EO 14028）要求軟體廠商自己提供 SBOM——你自己提供比買方事後掃準確多了。

---

## 動手練習

1. 在 `/tmp/sbom-demo/pyapp` 建一個 `requirements-top.txt`，只放 `flask==3.0.0` 和 `requests==2.31.0`（不含傳遞依賴），再建一個 `requirements-full.txt` 包含所有傳遞依賴（werkzeug、jinja2 等 12 個）。分別用 `syft scan dir:` 掃，比較 package 數量差距。這就是「manifest vs lockfile」的差距。

2. 在你的 goapp 目錄裡，分別跑：
   ```bash
   syft scan dir:/tmp/sbom-demo/goapp -o json | jq "[.artifacts[].foundBy] | unique"
   syft scan file:/tmp/sbom-demo/goapp/demo-app -o json | jq "[.artifacts[].foundBy] | unique"
   ```
   比較兩個指令的 `foundBy` 欄位，確認不同 cataloger 在不同情境下被觸發。

3. 故意製造一個「source 看到但 binary 看不到」的情境：寫一個 Go 程式，import 一個 package，然後在 `main()` 用 `_ = pkg.SomeFunc()` 把它的副作用引用掉——不引用的話 Go compiler 會優化掉。先確認 go.mod 有這個依賴，再 build，再用 syft 掃 binary，確認它還在（因為真的有連結進去）。

---

## 本章重點整理

- 生成 SBOM 有三種根本策略，各有不同的資訊來源與準確性保證：
  - **Source-based**：讀 manifest/lockfile，快、不侵入 build，但看不到 build 注入的依賴和 OS 套件
  - **Build-time**：build 過程中錄製，最準確，但要改 build 流程
  - **Binary / post-build**：事後掃 artifact，不侵入、能看 OS 套件，但 C/C++ 靜態連結看不到
- 三種方法的盲點互補，沒有一種能全部覆蓋
- Go 的 build info section 是特例：即使是 binary 分析，也能準確還原 Go 的 module 依賴
- 實務上的最佳實踐是**層疊使用**：pre-build source scan + build-time（高安全要求）+ post-build image scan

## 自我檢核

- [ ] 我能說出三種生成策略各自的資訊來源是什麼
- [ ] 我知道為什麼 `requirements.txt` 只有直接依賴，而 `pip freeze` 的輸出不一樣
- [ ] 我能解釋為什麼 syft 掃一個 C binary 往往回報 0 個 package
- [ ] 我知道 Go binary 的 build info section 是什麼，以及為什麼它讓 Go 的 binary 掃描比其他語言準確
- [ ] 我能說出在 CI pipeline 裡三種策略各自適合放在哪個階段

## 延伸閱讀

- **[CISA SBOM Types Document](https://www.cisa.gov/sites/default/files/2023-04/sbom-types-document-508c.pdf)**（CISA）
  - **讀哪裡**：第 3 節「SBOM Types」的六型定義，直接對應本章三種生成策略的底層分類
  - **為什麼值得讀**：這是 CISA 官方定義，後面法規章（Ch 24）會引用；先讀懂這個，之後不會被術語搞混

- **[syft 官方 README — Supported Ecosystems](https://github.com/anchore/syft)**（Anchore）
  - **讀哪裡**：README 的「Supported Ecosystems」表，以及各 cataloger 的描述
  - **和本章的關聯**：Ch 10 會深挖 cataloger 機制；這裡先對照「binary cataloger vs file cataloger」的分類，理解 syft 在哪個策略下用哪個 cataloger

- **[SPDX Specification — SBOM Build Phase](https://spdx.github.io/spdx-spec/v2.3/document-creation-information/)**（Linux Foundation）
  - **讀哪裡**：`BuildDate`、`LifecyclePhasePrimary` 等欄位的定義
  - **為什麼值得讀**：SPDX 已開始支援在 SBOM 裡宣告「這份 SBOM 是在 build 的哪個階段生成的」，這正是本章三種策略在格式層面的對應

- **[Framing Software Component Transparency, 3rd Edition](https://www.cisa.gov/resources-tools/resources/software-bill-materials-sbom)**（NTIA/CISA）
  - **讀哪裡**：「Generation Approaches」那節，以及附錄的 tooling landscape
  - **和本章的關聯**：NTIA 的框架文件也把生成方法分成三類，用的術語略有不同（analyzed/enriched/…），但核心概念吻合

接下來要把焦點放在 binary / post-build 策略的核心工具 syft 上，拆開它的內部機制：cataloger 是怎麼在檔案系統裡找到每個 package 的？

→ [Ch 10 syft 生成與內部：catalogers 怎麼認 package](./10-syft-internals.md)
