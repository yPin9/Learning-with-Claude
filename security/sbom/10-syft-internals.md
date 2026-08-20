# Ch 10 — syft 生成與內部：catalogers 怎麼認 package

> **目標**：把 syft 的「黑箱」拆開，理解 source → file tree → cataloger → package model 這條處理流水線，以及每種 cataloger 是靠什麼「證據」找到 package 的。讀完你能預測「syft 在這個情況能看到什麼、看不到什麼」，而不是靠猜。

## 為什麼需要這個？

Ch 9 建立了三種生成策略的概念框架。syft 是這門課用來做 binary / post-build 分析的主力工具，也能做 source-based 分析。但 syft 的輸出你真的信嗎？

「syft 說這個 image 有 15 個 package」——你怎麼知道不是 30 個，或者只是看到了 30 個裡面的 15 個？

要回答這個問題，你需要知道 syft 的工作原理：它靠什麼找到這 15 個、它為什麼找不到其他的。這不是學術問題，是「你能不能信任這份 SBOM」的問題。

## 先建立直覺：syft 是個「讀法醫現場」的機器

把 syft 想成一個法醫，它的工作是：進入一個「犯罪現場」（container image 或目錄），蒐集所有可識別的「跡證」，然後說出這裡有哪些人（package）曾經到過這裡。

它不問「誰說要來」（manifest），它問「誰在這裡留下了痕跡」（installed files、database records、binary metadata）。

有人「進來」但沒留下痕跡（靜態連結的 C library、vendored 程式碼），法醫就看不到。留下了痕跡但法醫不認識那種痕跡類型（某個冷門語言的套件格式），也看不到。**syft 的能力邊界 = 它知道哪些「痕跡類型」（cataloger）**。

## syft 的處理流水線

```
  syft <source>
       │
       │ 1. Source Resolution
       ▼
  ┌─────────────────────────────────────────────────────┐
  │  把 source 轉換成可分析的 file tree                  │
  │                                                     │
  │  docker image → 解開所有 layer 的 tar               │
  │  dir:path    → 直接讀目錄                           │
  │  file:path   → 單一檔案                             │
  │  registry:   → 從 registry 拉 manifest + layers    │
  └─────────────────────────────────────────────────────┘
       │
       │ 2. Cataloger Selection
       ▼
  ┌─────────────────────────────────────────────────────┐
  │  根據 source type 決定要啟用哪些 cataloger            │
  │                                                     │
  │  image  → os catalogers + language catalogers      │
  │  dir    → language catalogers（宣告型優先）          │
  │  file   → binary catalogers                        │
  └─────────────────────────────────────────────────────┘
       │
       │ 3. Catalogers 並行執行
       ▼
  ┌─────────────────────────────────────────────────────┐
  │  每個 cataloger 各自在 file tree 裡找「自己認得的證據」│
  │                                                     │
  │  apk-db-cataloger      → /lib/apk/db/installed     │
  │  dpkg-db-cataloger     → /var/lib/dpkg/status      │
  │  python-installed-...  → */site-packages/*.dist-info│
  │  go-module-binary-...  → ELF .go.buildinfo section │
  │  javascript-lock-...   → package-lock.json          │
  │  ... 60+ catalogers                                 │
  └─────────────────────────────────────────────────────┘
       │
       │ 4. 正規化
       ▼
  ┌─────────────────────────────────────────────────────┐
  │  把各 cataloger 的「原始格式」轉成統一的 Package model │
  │                                                     │
  │  { name, version, type, language, purl, locations,  │
  │    licenses, cpes, foundBy, metadata }              │
  └─────────────────────────────────────────────────────┘
       │
       │ 5. 輸出格式化
       ▼
     SPDX JSON / CycloneDX JSON / table / ...
```

**關鍵點：syft 不啟動 container，它把 image 的 layer 解開成靜態 tar 後讀檔案**。對 `dir:` source，它直接讀你的目錄。兩者都是「靜態分析」。

## cataloger 清單：syft 1.51.0 支援的全部 cataloger

實際查詢指令：

```bash
syft cataloger list
```

真實輸出（節錄關鍵 package cataloger）：

```
┌────────────────────────────────────────┬─────────────────────────────────────────────────────────────────────────────────┐
│ PACKAGE CATALOGER                      │ TAGS                                                                            │
├────────────────────────────────────────┼─────────────────────────────────────────────────────────────────────────────────┤
│ alpm-db-cataloger                      │ alpm, archlinux, directory, image, installed, linux, os, package, pacman        │
│ apk-db-cataloger                       │ alpine, apk, directory, image, installed, linux, os, package                    │
│ binary-classifier-cataloger            │ binary, declared, directory, image, installed, package                          │
│ cargo-auditable-binary-cataloger       │ binary, directory, image, installed, language, package, rust                    │
│ dpkg-db-cataloger                      │ debian, directory, dpkg, image, installed, linux, os, package                   │
│ elixir-mix-lock-cataloger              │ declared, directory, elixir, language, package                                  │
│ erlang-rebar-lock-cataloger            │ declared, directory, erlang, language, package                                  │
│ go-module-binary-cataloger             │ binary, directory, go, golang, gomod, image, installed, language, package       │
│ go-module-file-cataloger               │ declared, directory, go, golang, gomod, language, package                       │
│ graalvm-native-image-cataloger         │ directory, image, installed, java, language, package                            │
│ java-archive-cataloger                 │ directory, image, installed, java, language, maven, package                     │
│ java-gradle-lockfile-cataloger         │ declared, directory, gradle, java, language, package                            │
│ java-pom-cataloger                     │ declared, directory, java, language, maven, package                             │
│ javascript-lock-cataloger              │ declared, deno, directory, javascript, language, node, npm, package             │
│ javascript-package-cataloger           │ image, installed, javascript, language, node, package                           │
│ php-composer-lock-cataloger            │ composer, declared, directory, language, package, php                           │
│ python-installed-package-cataloger     │ directory, image, installed, language, package, python                          │
│ python-package-cataloger               │ declared, directory, language, package, python                                  │
│ r-package-cataloger                    │ directory, image, installed, language, package, r                               │
│ rpm-db-cataloger                       │ directory, image, installed, linux, os, package, redhat, rpm                    │
│ ruby-gemspec-cataloger                 │ declared, directory, gem, gemspec, language, package, ruby                      │
│ ruby-installed-gemspec-cataloger       │ gem, gemspec, image, installed, language, package, ruby                         │
│ rust-cargo-lock-cataloger              │ cargo, declared, directory, language, package, rust                             │
│ swift-package-manager-cataloger        │ declared, directory, language, package, spm, swift                              │
│ terraform-lock-cataloger               │ declared, directory, package, terraform                                         │
└────────────────────────────────────────┴─────────────────────────────────────────────────────────────────────────────────┘
```

Tag 欄位是關鍵：`installed` 代表「只在 image/已安裝的系統裡找」，`declared` 代表「在 source 的 manifest/lockfile 裡找」。同一個生態（如 Go、Python、Ruby）通常有兩個 cataloger，一個掃安裝後的 metadata，一個掃 manifest 檔案。

## 幾個 cataloger 的「證據」機制解析

### OS 套件 cataloger：讀套件資料庫

**apk-db-cataloger**（Alpine）：

```
/lib/apk/db/installed
────────────────────────────────────
C:Q17Lv+...
P:busybox
V:1.36.1-r20
A:x86_64
T:Size optimized toolbox of many common UNIX utilities
...
```

syft 讀 `/lib/apk/db/installed`，這是 Alpine 的 package 資料庫（純文字格式，以空行分隔每個 package）。`P:` = 名稱，`V:` = 版本。

**dpkg-db-cataloger**（Debian/Ubuntu）：

讀 `/var/lib/dpkg/status`，格式類似 APT 的 `deb822`：

```
Package: bash
Status: install ok installed
Version: 5.2.37-2+b9
Architecture: amd64
...
```

**rpm-db-cataloger**（CentOS/RHEL/Fedora）：

讀 `/var/lib/rpm/Packages`，這是一個 Berkeley DB（較舊 distro）或 SQLite database（較新 distro）。

### 語言套件 cataloger：兩種模式

**python-package-cataloger**（declared mode）：

找 `requirements.txt`、`setup.py`、`setup.cfg`、`pyproject.toml`、`Pipfile`、`Pipfile.lock`。這是 source-based，掃的是宣告。

**python-installed-package-cataloger**（installed mode）：

找 `*/site-packages/*.dist-info/METADATA`——這是 `pip install` 之後寫入的真實 metadata 檔案。確認套件真的被安裝了。

**javascript-lock-cataloger**（declared）：

找 `package-lock.json`（npm）、`yarn.lock`（Yarn）、`pnpm-lock.yaml`（pnpm）、`deno.lock`（Deno）。

**javascript-package-cataloger**（installed）：

找 `node_modules/*/package.json`——node_modules 裡的，確認已安裝。

### Go 的特殊 cataloger：讀 binary metadata

**go-module-binary-cataloger** 是 syft 最有趣的 cataloger 之一。

Go 從 1.12 開始，**build 完的 binary 裡預設嵌入 build info section**，記錄了 Go 版本、主模組、所有依賴模組的版本和 checksum。這個 section 用 `go version -m <binary>` 可以讀出來：

```bash
go version -m /tmp/sbom-demo/goapp/demo-app
```

真實輸出：

```
/tmp/sbom-demo/goapp/demo-app: go1.18.1
	path	example.com/demo
	mod	example.com/demo	(devel)
	dep	github.com/google/uuid	v1.6.0	h1:NIvaJDMOsjHA8n1jAhLSgzrAzy1Hgr+hNrb57e+94F0=
	dep	github.com/pkg/errors	v0.9.1	h1:FEBLx1zS214owpjy7qsBeixbURkuhQAwrK5UwLGTwt4=
	build	-compiler=gc
	build	CGO_ENABLED=1
	build	GOARCH=amd64
	build	GOOS=linux
```

syft 的 `go-module-binary-cataloger` 解析的就是這個 section（在 ELF binary 裡是 `.go.buildinfo`，Windows PE 裡是 `.rdata`）。

```bash
syft scan file:/tmp/sbom-demo/goapp/demo-app -o json | jq ".artifacts[] | {name, version, foundBy, metadata.h1Digest}"
```

真實輸出（節錄）：

```json
{
  "name": "github.com/google/uuid",
  "version": "v1.6.0",
  "foundBy": "go-module-binary-cataloger",
  "metadata.h1Digest": "h1:NIvaJDMOsjHA8n1jAhLSgzrAzy1Hgr+hNrb57e+94F0="
}
```

`h1Digest` 就是 `go.sum` 裡的 checksum——syft 從 binary 裡讀出來的，不需要原始碼。這讓 Go 的 binary 分析達到接近 build-time 記錄的準確度。

重要：**這個 section 不在 debug symbols 段**，`-ldflags="-s -w"` strip 掉 debug info 後，build info 仍然保留：

```bash
# strip 後再掃
go build -ldflags="-s -w" -o demo-stripped .
syft scan file:demo-stripped
# 仍然能看到所有 module，包括 stdlib 版本
```

### **cargo-auditable-binary-cataloger**（Rust）

Rust 的情況比 Go 複雜。標準 Rust binary 不像 Go 那樣預設嵌入依賴清單。`cargo-auditable` 是一個 Cargo wrapper，build 時把依賴清單嵌入 binary 的特定 section（`.dep-v0`）。沒有使用 `cargo-auditable` build 的 Rust binary，syft 的 `cargo-auditable-binary-cataloger` 看不到任何東西——它需要那個 section 存在。

這就是為什麼 Rust 生態在推廣 `cargo auditable build` 作為標準實踐，目的就是讓事後的 SBOM 生成和漏洞掃描成為可能。

## 同一個 package 被多個 cataloger 找到：deduplication

在 `dir:` source 下掃一個包含 go.mod 和 go binary 的目錄，兩個 cataloger 都會找到相同的 module：

```bash
syft scan dir:/tmp/sbom-demo/goapp -o json | jq ".artifacts[] | {name, version, foundBy}"
```

真實輸出：

```json
{ "name": "example.com/demo", "version": "UNKNOWN", "foundBy": "go-module-binary-cataloger" }
{ "name": "example.com/demo", "version": "UNKNOWN", "foundBy": "go-module-file-cataloger" }
{ "name": "github.com/google/uuid", "version": "v1.6.0", "foundBy": "go-module-binary-cataloger" }
{ "name": "github.com/google/uuid", "version": "v1.6.0", "foundBy": "go-module-file-cataloger" }
{ "name": "github.com/pkg/errors", "version": "v0.9.1", "foundBy": "go-module-binary-cataloger" }
{ "name": "github.com/pkg/errors", "version": "v0.9.1", "foundBy": "go-module-file-cataloger" }
{ "name": "stdlib", "version": "go1.18.1", "foundBy": "go-module-binary-cataloger" }
```

`table` 格式輸出時你會看到 `(+1 duplicate)` 這樣的標記，syft 在最終 SBOM 裡合併這些，但在 JSON 裡每個 `foundBy` 都有獨立的記錄。`stdlib go1.18.1` 只有 binary cataloger 找到，沒有 file cataloger 的對應記錄，因為 go.mod 不記錄 stdlib。

## 底層機制：.artifacts[].metadata 裡藏了什麼

`-o json` 輸出的 `.artifacts[].metadata` 欄位記錄了每個 cataloger 的「原始證據」——比 table 輸出多出很多資訊。

```bash
syft scan file:/tmp/sbom-demo/goapp/demo-app -o json | \
  jq ".artifacts[] | select(.name == \"github.com/google/uuid\") | .metadata"
```

輸出：

```json
{
  "goCompiledVersion": "go1.18.1",
  "architecture": "amd64",
  "h1Digest": "h1:NIvaJDMOsjHA8n1jAhLSgzrAzy1Hgr+hNrb57e+94F0=",
  "mainModule": "example.com/demo",
  "goCryptoSettings": ["standard-crypto"]
}
```

Python 安裝後的 metadata 更豐富：

```bash
syft scan dir:/tmp/sbom-demo/pyapp -o json | \
  jq ".artifacts[] | select(.name == \"flask\") | {name, version, purl, licenses, metadata}"
```

輸出（節錄）：

```json
{
  "name": "flask",
  "version": "3.0.0",
  "purl": "pkg:pypi/flask@3.0.0",
  "licenses": [],
  "metadata": {
    "name": "flask",
    "version": "3.0.0",
    "author": "",
    "authorEmail": "...",
    "platform": "UNKNOWN",
    "sitePackagesRootPath": ""
  }
}
```

注意 `"licenses": []` 是空的——syft 讀 requirements.txt 時**沒有**去 PyPI 查詢授權資訊，它只讀本地檔案。如果是掃 image 裡的 `.dist-info/METADATA`（installed mode），那個檔案裡有 `License:` 欄位，才會有授權資訊。這是 source-based 和 installed-based 掃描的差距之一。

## 對比與取捨

| cataloger 類型 | 找什麼證據 | 在哪裡 | 準確性說明 |
|---|---|---|---|
| OS db cataloger | apk/dpkg/rpm 的 package 資料庫 | `/lib/apk/db/installed` 等 | 高，資料庫由 package manager 維護 |
| language installed cataloger | `pip install` / `gem install` 後的 metadata 檔案 | `site-packages/*.dist-info` 等 | 高，安裝時寫入 |
| language declared cataloger | manifest/lockfile | `requirements.txt`、`go.mod` 等 | 中，只有宣告不一定是實際版本 |
| go-module-binary-cataloger | ELF `.go.buildinfo` section | binary 本身 | 高，build 時嵌入 |
| cargo-auditable-binary-cataloger | `.dep-v0` section | binary 本身（需 `cargo auditable build`）| 高（若 section 存在），否則 0 |
| binary-classifier-cataloger | 版本字串、二進位指紋 | binary 本身 | 低~中，靠 pattern matching，容易誤報/漏報 |
| java-archive-cataloger | JAR/WAR 裡的 MANIFEST.MF、pom.properties | `.jar` 檔案 | 中~高，依賴 JAR 裡的 metadata 品質 |

## 踩雷集錦

**1. 「syft 說 No packages discovered，表示這個 binary 沒有外部依賴」**

錯誤直覺：syft 沒找到就是沒有。

正確認識：syft 沒找到代表它不認識這個 binary 裡的「痕跡」。C/C++ 靜態連結的 binary、未用 `cargo auditable build` 的 Rust binary、用奇怪打包方式的 binary，syft 都可能看不到任何東西。「沒有」不代表「沒有外部依賴」，只代表「沒有 syft 認識的 metadata 格式」。

**2. 「dir: scan 比 image scan 更完整」**

錯誤直覺：直接掃源碼目錄，啥都有，一定比掃 image 完整。

正確認識：`dir:` scan 在 source 目錄跑，`declared` cataloger 很活躍（讀 go.mod、requirements.txt），但 `installed` cataloger 幾乎什麼都找不到（因為沒有 `site-packages`、沒有 dpkg database）。掃 image 時 `installed` cataloger 才大放異彩。兩種情境找到的 package 集合是互補的，不是包含關係。

**3. 「-o json 輸出的 artifacts 數量就是 SBOM 的 package 數量」**

錯誤直覺：JSON 裡有幾個 artifact 就是幾個 package。

正確認識：當同一個 package 被多個 cataloger 找到，JSON 裡會有多個 artifact 記錄，每個有不同的 `foundBy`。table 輸出做了 deduplication 顯示 `(+N duplicates)`，但 JSON 保留全部記錄。如果你要數真正的 unique package 數量，要做 deduplication（依名稱+版本）。

**4. 「syft 能看到 JAR 裡 shading 後的依賴」**

錯誤直覺：syft 掃 JAR 就能看到 Maven shadow plugin 塞進去的所有 class。

正確認識：`java-archive-cataloger` 讀的是 JAR 裡的 `MANIFEST.MF` 和 `pom.properties`。如果 shadow plugin 把多個 JAR 合併成一個 uber-JAR，那些被合入的套件的 pom.properties 可能被覆蓋或不存在，syft 就看不到。Shaded JAR 是 Java 生態 SBOM 的著名痛點，Ch 12 會再講。

---

## 進階：再往深一層

### 控制啟用哪些 cataloger

syft 有豐富的 cataloger 控制機制：

```bash
# 只啟用 go 相關的 cataloger
syft scan dir:. --select-catalogers "go"

# 排除特定 cataloger
syft scan dir:. --select-catalogers "-go-module-binary-cataloger"

# 在預設集合之外，額外強制加入某個 cataloger（前綴 +）
syft scan dir:. --select-catalogers "+javascript-lock-cataloger"
```

`--select-catalogers` 接受 cataloger 名稱、tag，或帶前綴的修飾：`-` 從預設集合移除、`+` 強制加入（即使預設不會啟用）。`syft cataloger list` 的 TAGS 欄位就是可用的 tag。

### binary-classifier-cataloger：通用 binary 指紋辨識

對於沒有 OS 套件 metadata 也沒有語言特定 metadata 的 binary，syft 還有一個後備機制：`binary-classifier-cataloger`。它維護一個「版本字串模式」的資料庫，在 binary 的 content 裡找像 `OpenSSL 3.0.7` 或 `curl/7.88.1` 這樣的版本字串。

準確度比較低，可能：
- **誤報**：找到一個看起來像版本字串的東西，但其實是別的
- **漏報**：binary 有 openssl 但版本字串格式不在資料庫裡

但這是對不透明 binary 的最後手段。

### SBOM Cataloger（SBOM 的 SBOM）

syft 有個有趣的 `sbom-cataloger`：如果在目錄裡找到了現有的 SBOM 檔案（`*.spdx.json`、`*.cdx.json`），它會把那份 SBOM 裡的 package 納入輸出。這讓 syft 可以「聚合」已存在的 SBOM。

---

## 動手練習

1. 比較同一個 Go 模組被不同 cataloger 找到的 metadata 差異：
   ```bash
   syft scan dir:/tmp/sbom-demo/goapp -o json | \
     jq "[.artifacts[] | select(.name == \"github.com/google/uuid\")] | {count: length, sources: [.[].foundBy]}"
   ```
   你會看到同一個 uuid v1.6.0 被兩個 cataloger 找到，確認它們的 metadata 是否有差異。

2. 確認 Go binary 的 build info section 不受 strip 影響：
   ```bash
   go build -o demo-full .
   go build -ldflags="-s -w" -o demo-stripped .
   go version -m demo-full
   go version -m demo-stripped
   ```
   兩個輸出應該相同（除了 `-ldflags` build setting 的記錄）。這驗證了為什麼 syft 掃 stripped Go binary 仍然能看到依賴。

3. 看 `python-package-cataloger` 在掃 requirements.txt 時看不到授權：
   ```bash
   syft scan dir:/tmp/sbom-demo/pyapp -o json | \
     jq ".artifacts[] | select(.name == \"flask\") | {name, licenses, purl}"
   ```
   `licenses` 應該是空陣列。這是 source-based 掃描的已知限制：本地沒有 `.dist-info/METADATA`，就不知道授權。

---

## 本章重點整理

- syft 的處理流水線：**source resolution → file tree → cataloger 並行掃描 → 正規化 → 輸出**
- 每個 cataloger 靠特定「證據」認識特定的 package 格式：apk database、dpkg status、`.dist-info/METADATA`、go.mod、`.go.buildinfo` section 等
- syft 1.51.0 有 60+ cataloger，覆蓋 OS 套件（apk/dpkg/rpm/alpm）、15+ 語言生態、binary 指紋、SBOM 聚合
- Go 的 `go-module-binary-cataloger` 能從 strip 後的 binary 還原完整的 module 清單（含 checksum），因為 build info section 不在 debug symbols 段
- Rust 需要用 `cargo-auditable` build 才有對應的 binary cataloger；C/C++ 靜態連結 binary 幾乎無法用 binary 分析取得完整依賴清單
- 同一 package 被多個 cataloger 找到時，JSON 有多條記錄但 table 顯示 `(+N duplicates)`；`metadata` 欄位保留了各 cataloger 的原始證據

## 自我檢核

- [ ] 我能說出 syft 的 5 步處理流水線，以及每步做什麼
- [ ] 我知道 `installed` cataloger 和 `declared` cataloger 的差別，能舉各一個例子
- [ ] 我能解釋為什麼 `go-module-binary-cataloger` 能處理 stripped binary，但 `cargo-auditable-binary-cataloger` 不行（如果沒有 `cargo auditable build`）
- [ ] 我能用 `jq` 從 syft JSON 輸出裡看出某個 package 是被哪個 cataloger 找到的
- [ ] 我知道 `syft cataloger list` 是什麼，以及 TAGS 欄位的 `declared` vs `installed` 意義

## 延伸閱讀

- **[syft cataloger source code](https://github.com/anchore/syft/tree/main/syft/pkg/cataloger)**（Anchore GitHub）
  - **讀哪裡**：每個 cataloger 的子目錄，找你感興趣的生態（如 `python/`、`golang/`），看 `cataloger.go` 的 `FinderPatterns`——那就是它實際找哪些檔案路徑
  - **為什麼值得讀**：不用猜「syft 找什麼檔案」，直接讀 source code；這也是確認某個邊界行為的最可靠方式

- **[Go module build info](https://pkg.go.dev/runtime/debug#ReadBuildInfo)**（Go 官方 pkg.go.dev）
  - **讀哪裡**：`ReadBuildInfo()` 的說明，以及 `BuildInfo.Deps` 欄位的結構
  - **和本章的關聯**：這是 Go binary 裡那個讓 `go-module-binary-cataloger` 能運作的機制的官方說明

- **[cargo-auditable](https://github.com/rust-secure-code/cargo-auditable)**（Rust Secure Code WG）
  - **讀哪裡**：README 的「How it works」，以及 Motivation 部分
  - **和本章的關聯**：理解 Rust 生態為什麼需要一個獨立工具做 Go 原生就支援的事，以及它在 binary 裡嵌了什麼

- **[NTIA Framing SBOM: What About the Unknown Unknowns?](https://www.cisa.gov/sites/default/files/publications/SBOM_Myths%20vs%20Facts_Nov2021_0.pdf)**（NTIA/CISA）
  - **讀哪裡**：「Myths vs. Facts」整份文件，特別是關於生成完整度的那幾條
  - **為什麼值得讀**：官方承認 SBOM 生成有盲點，這份文件正視了「沒有工具能 100% 完整」這件事，是 Ch 12 品質章的好前導

理解了 syft 怎麼工作之後，下一章進入各語言生態的 build-time 生成：在 build 過程中主動記錄，比事後掃 artifact 能得到什麼額外的準確性。

→ [Ch 11 build-time 生成：各語言生態](./11-build-time-generation.md)
