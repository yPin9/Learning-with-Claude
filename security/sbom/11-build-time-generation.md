# Ch 11 — build-time 生成：各語言生態

> **目標**：各語言生態怎麼在 build 時主動產出 SBOM，以及為什麼這比事後掃 artifact 在某些情況下更準確。真跑 Go 和 Python 的例子，其他生態給出指令與預期輸出。

## 為什麼需要這個？

Ch 10 我們深挖了 syft 的 cataloger 機制——它是個「讀法醫現場」的工具，事後從 artifact 裡找證據。但有個根本問題：**某些事情，你必須在 build 的當下才能知道確切發生了什麼**。

以 Java/Maven 為例：你宣告 `spring-boot:3.2.0`，Maven 的 dependency resolution 最終解析出 47 個傳遞依賴，每個都有精確版本。事後掃 JAR 裡的 pom.properties 可以找到大部分，但 `shade` 過的 uber-JAR、在 classpath 裡但不在 JAR 裡的依賴，事後就看不到了。Build-time plugin 在 Maven resolver 解析的當下錄製，是最準確的時機。

**Build-time 的核心優勢**：

```
  Maven dependency resolution
  ─────────────────────────────────────
  spring-boot:3.2.0
    ├── spring-core:6.1.2
    │     ├── spring-jcl:6.1.2
    │     └── ...
    ├── spring-web:6.1.2
    │     └── ...
    └── ... (共 47 個 transitive deps)

  事後掃 JAR：能找到的 = 有 pom.properties 的
  Build-time plugin：能記錄的 = resolver 處理的全部
```

這是原則，不是說 build-time 在所有情況都更好（它看不到 base image 的 OS 套件），而是說**對 build tool 管理的依賴，build-time 是最準確的時機**。

## 先建立直覺：「旁觀者」模型

Build-time 生成工具的工作模式是「旁觀者」：它掛在 build 系統裡，在 build 執行的同時旁觀並記錄，不干涉 build 本身。

```
  沒有 build-time plugin：
  mvn package → build → JAR  （依賴清單 = 事後推斷）

  有 CycloneDX Maven plugin：
  mvn package → build → JAR
       └──── CycloneDX plugin 旁觀 ────→ bom.json
              （resolver 解析什麼它就記什麼）
```

「旁觀者」這個模型有一個重要含義：**SBOM 的生成和 build 本身是分離的**。即使 build 失敗，已經記錄的部分也已經記錄了；即使後來有人改了 Dockerfile 加了東西，build-time plugin 記錄的部分不受影響（也不會更新）。

## Go：syft 掃 go.mod 或 binary

Go 是這幾個語言裡最不需要「額外工具」做 build-time 生成的，因為：

1. `go.mod` + `go.sum` 本身就是精確的 lockfile，記錄所有傳遞依賴的精確版本和 checksum
2. Go build 把這些資訊嵌入 binary 的 `.go.buildinfo` section（Ch 10 已講）

因此 Go 的「build-time SBOM」其實就是直接用 syft 掃 go.mod 或 binary。

### 真跑示範

```bash
# 方法 A：掃 go.mod（source-based，build 前就能跑）
syft scan dir:/tmp/sbom-demo/goapp -o spdx-json > go-source.spdx.json
jq ".packages | length" go-source.spdx.json
```

真實輸出：

```
3
```

（github.com/google/uuid、github.com/pkg/errors、加上描述 directory 本身的 root package）

```bash
# 方法 B：掃 binary（build 後）
syft scan file:/tmp/sbom-demo/goapp/demo-app -o spdx-json > go-binary.spdx.json
jq "[.packages[].name] | sort" go-binary.spdx.json
```

真實輸出：

```json
[
  "demo-app",
  "example.com/demo",
  "github.com/google/uuid",
  "github.com/pkg/errors",
  "stdlib"
]
```

Binary 掃比 source 掃多了 `stdlib go1.18.1`，因為 `.go.buildinfo` 記錄了 Go 編譯器版本。在 CVE 追蹤上這很重要——Go stdlib 本身也有 CVE，你需要知道用的是哪個版本。

```bash
# 確認 binary 裡的 build info 與 go.sum 的 checksum 一致
go version -m /tmp/sbom-demo/goapp/demo-app | grep uuid
# dep	github.com/google/uuid	v1.6.0	h1:NIvaJDMOsjHA8n1jAhLSgzrAzy1Hgr+hNrb57e+94F0=
```

這個 h1 hash 跟 `go.sum` 裡的相同，也跟 syft 的 `metadata.h1Digest` 相同。這是一條可驗證的鏈：`go.sum` → binary → syft SBOM。

### 何時用哪個

- **開發階段 / pre-commit**：`syft scan dir:.` 掃 go.mod，快、不需要 build
- **CD pipeline，生產 SBOM**：`syft scan file:<binary>` 或 `syft scan registry:<image>`，取得包含 stdlib 版本的完整清單

---

## Python：syft 掃 requirements 或用 cyclonedx-py

Python 的情況稍複雜，因為 Python 生態沒有像 `go.sum` 那樣明確的全傳遞依賴 lockfile 格式（`requirements.txt` 通常只有直接依賴；`pip freeze` 才是完整清單；Poetry/pdm 有各自的 lockfile）。

### 方法 A：syft 掃 requirements（source-based）

如果你的 `requirements.txt` 是 `pip freeze` 的輸出（包含所有傳遞依賴），直接掃：

```bash
# pyapp 目錄有 requirements.txt（含 12 個 package，包括傳遞依賴）
syft scan dir:/tmp/sbom-demo/pyapp
```

真實輸出：

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

12 個套件，全是傳遞依賴都齊全——因為這個 requirements.txt 本身就是 lockfile 等級的清單。

如果 requirements.txt 只有 `flask==3.0.0` 和 `requests==2.31.0`（只有直接依賴），syft 只會看到 2 個。

### 方法 B：cyclonedx-py（build-time，取得安裝後的真實清單）

`cyclonedx-py` 是 Python 官方的 CycloneDX 生成工具，它能直接問當前 Python 環境「裡面真的裝了什麼」：

```bash
pip install cyclonedx-bom   # 安裝工具
cyclonedx-py environment -o bom.json   # 掃當前 virtualenv
```

（**標注：此指令在本環境 WSL 未實測，因為測試用 virtualenv 未建立**；但指令是官方文件的標準用法，預期輸出是 CycloneDX JSON 格式的 SBOM，包含 `pip freeze` 等級的完整清單。）

預期輸出（CycloneDX JSON 格式，節錄）：

```json
{
  "bomFormat": "CycloneDX",
  "specVersion": "1.6",
  "components": [
    {
      "type": "library",
      "name": "flask",
      "version": "3.0.0",
      "purl": "pkg:pypi/flask@3.0.0",
      "licenses": [{"expression": "BSD-3-Clause"}]
    },
    ...
  ]
}
```

注意這裡的 `"licenses": [{"expression": "BSD-3-Clause"}]`——掃安裝後的 `.dist-info/METADATA` 才能得到授權資訊，source-based 掃 requirements.txt 得不到。這是 build-time（安裝後）比 source-based 多出的重要資訊。

### 方法 C：pip-audit（側重漏洞，但也輸出 SBOM）

```bash
pip install pip-audit
pip-audit --format=cyclonedx-json --output=audit-bom.json
```

pip-audit 同時掃漏洞和生成 SBOM，適合 CI 的「生成 + 掃描」一步到位的需求。

---

## Node / npm（標注：未實測，本環境無 node）

npm 從 npm v8.3.0 開始內建 `npm sbom` 指令：

```bash
# npm 內建 SBOM 生成（需 npm >= 8.3.0）
npm sbom --sbom-format=spdx --sbom-type=package
# 或 CycloneDX
npm sbom --sbom-format=cyclonedx > bom.cdx.json
```

`npm sbom` 讀的是 `package-lock.json`（如果存在），所以它看到的是 `npm install` 解析後的精確版本，包含傳遞依賴。

syft 也能做到類似的事：

```bash
syft scan dir:/tmp/sbom-demo/npmapp
```

真實輸出（syft 讀 package-lock.json）：

```
NAME           VERSION  TYPE
accepts        1.3.8    npm
array-flatten  1.1.1    npm
demo-app       1.0.0    npm
express        4.18.2   npm
lodash         4.17.21  npm
mime-db        1.52.0   npm
mime-types     2.1.35   npm
negotiator     0.6.3    npm
```

8 個 package，包含 express 的傳遞依賴（accepts、array-flatten、mime-types 等）——因為 package-lock.json 記錄了全部。如果只有 package.json（沒有 lockfile），syft 只能看到 express 和 lodash 2 個直接依賴。

**lockfile 的存在與否，決定了 source-based 掃描能不能看到傳遞依賴**。這在 Node 生態尤其明顯：`node_modules` 裡可能有 500 個 package，`package.json` 裡只有 5 個直接依賴。

### CycloneDX npm plugin（更豐富的 metadata）

```bash
npm install -g @cyclonedx/cyclonedx-npm
cyclonedx-npm --output-format JSON bom.json
```

比 `npm sbom` 多出 `externalReferences`（package homepage、license URL）等欄位。

---

## Java / Maven（標注：未實測，本環境無 Java）

Maven 是 build-time SBOM 生成最成熟的生態之一，CycloneDX Maven plugin 已經非常成熟：

```xml
<!-- pom.xml 裡加入 plugin -->
<plugin>
  <groupId>org.cyclonedx</groupId>
  <artifactId>cyclonedx-maven-plugin</artifactId>
  <version>2.8.2</version>
</plugin>
```

執行：

```bash
mvn org.cyclonedx:cyclonedx-maven-plugin:makeAggregateBom
# 輸出到 target/bom.json
```

這會讓 Maven resolver 在解析依賴的當下記錄，包括：

- 所有傳遞依賴（complete dependency graph）
- 每個依賴的 `scope`（compile、test、runtime、provided）
- 版本衝突解析的結果（dependency mediation：Maven 用最短路徑規則）

特別重要的是 `scope`：test-only 的依賴（如 JUnit）不會出現在 runtime 的 SBOM 裡，這讓你能做「runtime-only SBOM」——只列真正出貨的依賴，不包含 build/test 工具。事後掃 WAR/JAR 的 pom.properties 無法區分 scope。

**Maven 生態的特有挑戰：Shaded JAR**

```
maven-shade-plugin 把 A、B、C 三個 JAR 合成一個 uber-JAR
  → 只有一個輸出 JAR
  → uber-JAR 裡面可能只有一個 pom.properties（你的 app 的）
  → A、B、C 的 pom.properties 被覆蓋或不存在
  → 事後掃 uber-JAR：看不到 A、B、C
  → Build-time plugin：在合併前就已記錄 A、B、C
```

這是 build-time 比 post-build 分析明顯更準確的典型案例。

---

## Java / Gradle（標注：未實測）

```groovy
// build.gradle 加入 plugin
plugins {
    id("org.cyclonedx.bom") version "1.10.0"
}

// 設定輸出
cyclonedxBom {
    outputName = "bom"
    outputFormat = "json"
}
```

```bash
./gradlew cyclonedxBom
# 輸出到 build/reports/bom.json
```

Gradle 的依賴解析邏輯比 Maven 複雜（version catalog、dynamic version、capability conflict resolution），build-time plugin 在這裡的優勢更明顯。

---

## Rust / cargo（標注：未實測，本環境無 cargo）

Rust 有兩個選項：

### cargo-cyclonedx

讀 `Cargo.lock`（Rust 的精確 lockfile，格式非常明確，記錄所有傳遞依賴）：

```bash
cargo install cargo-cyclonedx
cargo cyclonedx
# 輸出 bom.xml 或 bom.json
```

### cargo-auditable（build-time embedding）

```bash
cargo install cargo-auditable
cargo auditable build
# 把依賴清單嵌入 binary 的 .dep-v0 section
# 之後 syft 的 cargo-auditable-binary-cataloger 就能讀到
```

這是 Ch 10 提到的「讓 syft 的 binary cataloger 能工作」的前提。

---

## 生態對比表

| 語言/生態 | 主要 build-time 工具 | lockfile 覆蓋傳遞依賴？ | 未實測標注 |
|---|---|---|---|
| Go | syft scan go.mod / binary | 是（go.sum） | - |
| Python（pip） | cyclonedx-py / pip-audit | 要靠 pip freeze | 未在本環境測 |
| Node / npm | `npm sbom` / cyclonedx-npm | 是（package-lock.json） | 未在本環境測 |
| Java / Maven | cyclonedx-maven-plugin | 是（Maven pom + resolver） | 未在本環境測 |
| Java / Gradle | cyclonedx-gradle-plugin | 是（Gradle lock） | 未在本環境測 |
| Rust | cargo-cyclonedx / cargo-auditable | 是（Cargo.lock） | 未在本環境測 |
| .NET | CycloneDX .NET CLI | 是（packages.lock.json） | 未在本環境測 |
| PHP / Composer | cyclonedx-php-composer | 是（composer.lock） | 未在本環境測 |

---

## 底層機制：為什麼「lockfile 存在」這麼關鍵

```
  scenario A（有 lockfile）：
  package.json: "express": "^4.18.0"
      │
      │ npm install（解析 + 寫入 lockfile）
      ▼
  package-lock.json: "express": { "version": "4.18.2", ... }
                     "accepts": { "version": "1.3.8", ... }
                     ... (全部 8 個傳遞依賴)
      │
      │ syft scan dir:. （讀 lockfile）
      ▼
  SBOM: 8 個 package（含傳遞依賴）✓

  scenario B（只有 manifest）：
  package.json: "express": "^4.18.0"
      │
      │ syft scan dir:. （只讀 package.json）
      ▼
  SBOM: 2 個 package（express + lodash，只有直接依賴）✗
```

Lockfile 是 package manager 在「install」那一刻寫入的精確解析結果。有 lockfile = 有精確的傳遞依賴記錄。**把 lockfile 加進版本控制，是 source-based SBOM 生成能不能覆蓋傳遞依賴的決定性因素**。

---

## 對比與取捨

| 考量點 | syft 掃 source | build-time plugin | syft 掃 image |
|---|---|---|---|
| 能否在 build 前跑 | 是 | 否 | 否 |
| 需改 build 設定 | 否 | 是 | 否 |
| 看到傳遞依賴 | 有 lockfile 才完整 | 完整（build graph） | 取決於 installed metadata |
| 授權資訊 | 通常沒有（requirements.txt 不帶） | 有（resolver 查 registry） | 有（installed metadata 帶） |
| Build scope 資訊（test vs runtime） | 無 | 有（Maven/Gradle） | 無 |
| OS 套件覆蓋 | 無 | 無 | 有 |
| 主要適合 | 快速 pre-build 掃描 | 高精度法規合規 SBOM | 最終 artifact 完整掃描 |

---

## 踩雷集錦

**1. 「pip freeze > requirements.txt 的輸出就是 build-time SBOM 了」**

錯誤直覺：`pip freeze` 輸出了所有安裝的 package，這就是最完整的清單了。

正確認識：`pip freeze` 輸出的是**當前環境**的清單，不是 build 的清單。如果你的 virtualenv 裡有一些裝來測試、手動裝的 package，`pip freeze` 也會包含它們。正確的做法是在乾淨的 virtualenv 裡安裝 `requirements.txt`，再 `pip freeze`；或者用 `pip-audit`、`cyclonedx-py` 這類工具，它們能過濾掉非宣告的 package。

**2. 「Maven 的 pom.xml 裡有所有依賴」**

錯誤直覺：我在 pom.xml 裡看到的 `<dependencies>` 就是全部。

正確認識：pom.xml 裡的是**直接依賴**（你宣告的），傳遞依賴是 Maven resolver 計算出來的。最終進 build 的可能有幾十到幾百個傳遞依賴，你的 pom.xml 完全不列它們。這就是 `mvn dependency:tree` 和 CycloneDX plugin 存在的原因。

**3. 「有 go.sum 就等於有 build-time SBOM」**

錯誤直覺：go.sum 記錄了所有依賴，等於完美的 SBOM 素材。

正確認識：go.sum 記錄的是**你宣告的依賴的 checksum**，不是 build 用到了哪些。如果你的 go.mod 有一個依賴但沒有在 code 裡 import，Go build 可能不會把它連結進去（Go 編譯器做 dead code elimination）。嚴格來說，「實際連結進 binary 的」比 go.mod 更準確——這就是為什麼掃 binary 的 `.go.buildinfo` 比掃 go.mod 更接近「實際 runtime 用到的」。不過差距在大多數情境下很小，因為很少有人把沒在用的 module 留在 go.mod 裡。

**4. 「build-time SBOM 是終態，之後不需要再更新」**

錯誤直覺：build 完、SBOM 也生完，就結束了。

正確認識：SBOM 描述的是那個時間點的 build。如果三個月後新 CVE 出現，影響你那時候 build 進去的某個版本，你需要有一個地方把這份 SBOM 和「那個版本的那次 build」關聯起來、持續監控。這是 Part 4（Dependency-Track）和 Part 5（簽章/provenance）的主題。Build-time SBOM 本身是靜態快照，持續監控是另一個工作。

---

## 進階：再往深一層

### 在 CI/CD 裡的典型位置

```yaml
# GitHub Actions 範例（概念，未真跑）
jobs:
  sbom:
    runs-on: ubuntu-latest
    steps:
      # Stage 1: pre-build（source-based）
      - name: Source SBOM
        run: syft scan dir:. -o cyclonedx-json > source-sbom.json

      # Stage 2: build（build-time）
      - name: Build
        run: mvn package  # CycloneDX plugin 在這裡生成 target/bom.json

      # Stage 3: post-build（binary/image）
      - name: Image SBOM
        run: syft scan registry:${{ env.IMAGE }}:${{ env.TAG }} -o spdx-json > image-sbom.json

      # Stage 4: 上傳到 Dependency-Track（Ch 17）
      - name: Upload SBOMs
        run: |
          curl -X POST $DTRACK_URL/api/v1/bom \
            -H "X-Api-Key: $DTRACK_KEY" \
            -F "bom=@image-sbom.json"
```

### SBOM 的時間戳記和 lifecycle phase

CycloneDX 1.5+ 支援在 SBOM 裡聲明 `lifecyclePhasePrimary`：

```json
{
  "metadata": {
    "timestamp": "2026-08-17T19:36:00Z",
    "lifecycles": [
      { "phase": "build" }
    ],
    "tools": [{"name": "cyclonedx-maven-plugin", "version": "2.8.2"}]
  }
}
```

這讓 SBOM 消費者能知道「這份 SBOM 是 build 時產的，不是事後推斷的」，信任度判斷不同。SPDX 3.0 也有類似的 lifecycle 欄位。

---

## 動手練習

1. 驗證 Go 的 source 掃和 binary 掃的差異：

   ```bash
   # Source scan（只有 go.mod）
   syft scan dir:/tmp/sbom-demo/goapp -o json 2>/dev/null | \
     jq -r "[.artifacts[] | select(.foundBy == \"go-module-file-cataloger\")] | length"

   # Binary scan
   syft scan file:/tmp/sbom-demo/goapp/demo-app -o json 2>/dev/null | \
     jq -r ".artifacts | length"
   ```

   確認 binary scan 比 source scan 多出 `stdlib go1.18.1`（binary 有 4 個 artifact，source 只有 2-3 個）。

2. 驗證「minimal requirements vs full lockfile」的差距：

   ```bash
   # minimal：只有直接依賴
   mkdir -p /tmp/sbom-demo/minimal-py
   echo -e "flask==3.0.0\nrequests==2.31.0" > /tmp/sbom-demo/minimal-py/requirements.txt
   syft scan dir:/tmp/sbom-demo/minimal-py 2>/dev/null | wc -l

   # full lockfile
   syft scan dir:/tmp/sbom-demo/pyapp 2>/dev/null | wc -l
   ```

   前者應該只有 2 個 package，後者 12 個。這就是「manifest vs lockfile」的差距。

3. 確認 npm 的 package-lock.json 讓 syft 看到傳遞依賴：

   ```bash
   syft scan dir:/tmp/sbom-demo/npmapp -o json 2>/dev/null | \
     jq -r ".artifacts[] | .name + \" \" + .version"
   ```

   你應該看到 express 的 8 個傳遞依賴，而不只是 package.json 裡的 express 和 lodash。

---

## 本章重點整理

- Build-time 生成的核心優勢：在 build tool 解析依賴的當下錄製，能取得**完整的 dependency graph 和精確的 resolved version**，包括 shade 合併前的依賴、test scope 的區分
- **Go** 不需要額外 plugin，直接用 syft 掃 go.mod（source-based）或 binary（binary 分析，多出 stdlib 版本）
- **Python** 的關鍵在 lockfile：requirements.txt 只有直接依賴，`pip freeze` 輸出的全清單 + cyclonedx-py 或 pip-audit 才能取得完整清單含授權資訊
- **Java/Maven、Gradle** 的 CycloneDX plugin 能拿到 scope 資訊（test vs runtime）和 shade 前的依賴，這是 post-build 掃 JAR 做不到的
- **Lockfile 是 source-based 掃描能覆蓋傳遞依賴的決定性因素**：有 lockfile（package-lock.json、go.sum、Cargo.lock）→ 完整；只有 manifest → 只有直接依賴
- 未在本環境實測的生態（Node/npm、Java/Maven、Rust）的指令已給出，均為官方文件的標準用法

## 自我檢核

- [ ] 我能解釋為什麼 Go 不需要額外 build plugin 就能有高品質的 SBOM
- [ ] 我知道 Python requirements.txt 和 pip freeze 輸出的差別，以及它對 SBOM 完整度的影響
- [ ] 我能說出為什麼 Maven shaded JAR 讓 post-build 掃描比 build-time plugin 看到的少
- [ ] 我知道 CycloneDX Maven plugin 能取得 test scope 資訊，而事後掃 JAR 無法
- [ ] 我能說出 lockfile 對 source-based SBOM 完整度的關鍵作用

## 延伸閱讀

- **[CycloneDX Tool Center](https://cyclonedx.org/tool-center/)**（CycloneDX 官方）
  - **讀哪裡**：按語言篩選，看各生態的 build plugin 清單和狀態（maintained / deprecated）
  - **和本章的關聯**：本章講的 plugin 只是主流選項，這裡有完整清單，找你的生態的工具從這裡開始

- **[npm sbom 文件](https://docs.npmjs.com/cli/v8/commands/npm-sbom)**（npm 官方）
  - **讀哪裡**：`--sbom-type` 和 `--sbom-format` 兩個 flag 的說明
  - **和本章的關聯**：npm 內建 SBOM 支援是 npm v8.3.0 的重要特性，這是最短路徑的 Node SBOM 生成

- **[cyclonedx-maven-plugin GitHub](https://github.com/CycloneDX/cyclonedx-maven-plugin)**（CycloneDX）
  - **讀哪裡**：README 的 Goal 說明，以及 `includeTestScope` 等設定的含義
  - **和本章的關聯**：理解 Maven plugin 的 scope 過濾設定，決定 test 依賴要不要進 SBOM

- **[cargo-auditable](https://github.com/rust-secure-code/cargo-auditable)**（Rust Secure Code WG）
  - **讀哪裡**：README 的「Why?」一節
  - **為什麼值得讀**：Rust 生態對 binary SBOM 的設計選擇（嵌入 binary），對比 Go 的設計（Go build 原生嵌入），理解不同語言在 SBOM 可見性上的設計取捨

下一章把焦點放在「一份 SBOM 可能哪裡爛」——品質維度、如何評分、如何一眼看出靠不靠譜。

→ [Ch 12 SBOM 品質與完整度](./12-sbom-quality.md)
