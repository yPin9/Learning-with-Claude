# Ch 28 — 打包 Go 程式

> **目標**：理解 Go 程式打包的特殊挑戰——static binary vs 動態連結、vendor 目錄與 Go modules、`dh-golang` 的運作、以及 Debian 對 Go 依賴的「全部打包」哲學與其爭議。

> **環境**：dh-golang、golang-go（Debian 12 的 Go 1.19）。本章假設你會基本 Go（go build、modules）。

## 為什麼 Go 打包這麼特別？

Go 的設計和 C/Python 都不同，帶來獨特的打包挑戰：

```
Go 的特性 → 打包挑戰：

  1. 靜態連結（預設）
     Go binary 預設把所有依賴編進一個執行檔
     → 沒有 .so，沒有 ${shlibs:Depends}，依賴全在 build 時解決

  2. 編譯時需要「所有依賴的原始碼」
     Go 不像 C 連結 .so，它編譯時需要每個依賴的「source」
     → build 依賴是「所有用到的 Go library 的 source 套件」

  3. Go modules / vendor
     upstream 可能用 go.mod 宣告依賴，或 vendor/ 內嵌依賴 source
     → Debian 要決定：用系統打包的依賴，還是 vendor 的？
```

最核心的張力：**Go 靜態連結 + 編譯需要依賴 source**，和 Debian「每個 library 獨立打包、動態連結、統一安全更新」的哲學衝突。這章講 Debian 怎麼調和。

## 先建立直覺：Go 打包的兩種哲學

```
哲學 A：Debian 的「全部打包」（傳統 Debian 方式）
  把每個 Go 依賴都打包成 golang-github-xxx-dev（含 source）
  你的程式 build 時用「系統打包的依賴 source」
  好處：依賴可被安全更新、可追溯、符合 Debian 哲學
  壞處：要打包幾十上百個依賴（Go 程式依賴常很多）

哲學 B：vendor（務實方式）
  用 upstream 的 vendor/ 目錄（內嵌所有依賴 source）
  build 時直接用 vendored 的依賴
  好處：簡單，不用打包每個依賴
  壞處：依賴無法統一安全更新（藏在每個套件的 vendor 裡）
```

> **認識論誠實**：這是 Go 打包真實的爭議。Debian 官方傳統偏好哲學 A（全部打包），因為它符合「library 統一管理 + 安全更新」的核心價值。但 Go 程式動輒幾十個依賴，全打包工作量巨大。實務上：進 Debian archive 的 Go 程式多走 A（或混合）；私有 repo / 快速打包常走 B（vendor）。沒有「正確答案」，是 trade-off。

## dh-golang：Go 打包的 helper

`dh-golang` 提供 Go 的 dh buildsystem：

```makefile
#!/usr/bin/make -f
%:
	dh $@ --builddirectory=_build --buildsystem=golang --with=golang

# dh-golang 自動：
#   - 設定 GOPATH
#   - go build（用正確的 import path）
#   - 安裝 binary 到 /usr/bin
#   - （對 library 套件）安裝 .go source 到 dev 套件
```

`debian/control` 的 Go 特定欄位：

```
Source: myapp
Section: golang
Priority: optional
Build-Depends:
 debhelper-compat (= 13),
 dh-golang,
 golang-any,
 golang-github-spf13-cobra-dev,    ← 依賴的 Go library（已打包的 source）
 golang-github-sirupsen-logrus-dev,
Standards-Version: 4.6.2
XS-Go-Import-Path: github.com/you/myapp    ← Go 的 import path（關鍵！）
Rules-Requires-Root: no

Package: myapp
Architecture: any
Built-Using: ${misc:Built-Using}    ← 記錄靜態編進去的 source（見下）
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: example Go application
 ...
```

關鍵欄位：
- `XS-Go-Import-Path`：Go 的 import path（`github.com/you/myapp`）。dh-golang 用它設定 GOPATH 結構
- `Built-Using`：記錄「靜態編進這個 binary 的 source 套件」（見下）
- Build-Depends 含 `golang-github-*-dev`：每個 Go 依賴的 source 套件

## Built-Using：靜態連結的法律與追溯

Go binary 靜態連結了依賴的 code。這帶來一個法律/追溯問題：**binary 裡含了哪些 source 的 code？**

```
Built-Using: golang-github-spf13-cobra-dev (= 1.6.1-1),
             golang-github-sirupsen-logrus-dev (= 1.9.0-1)
```

`Built-Using` 記錄「這個 binary 靜態編入了哪些 source 套件的哪個版本」。意義：

```
為什麼需要 Built-Using：
  1. 法律：GPL 等要求提供「對應的 source」。binary 靜態編入了 cobra 的
     code，那 cobra 的 source 也要可取得。Built-Using 確保那個版本的
     source 留在 archive（不會被移除）
  2. 安全：cobra 有 CVE 時，能查出哪些 binary 靜態編入了有問題的版本
     → 知道要重新 build 哪些套件
```

dh-golang 透過 `${misc:Built-Using}` 自動生成。這是靜態連結語言（Go、Rust）特有的——動態連結（C）靠 `${shlibs:Depends}` 在 runtime 解決，不需要 Built-Using。

## Go library 套件：golang-*-dev

被其他 Go 程式依賴的 Go library，打包成 `golang-<host>-<path>-dev`，**只含 source**（Go 編譯需要依賴 source）：

```
命名：golang- + import path（用 - 取代 / 和 .）
  github.com/spf13/cobra → golang-github-spf13-cobra-dev
  golang.org/x/sys       → golang-golang-x-sys-dev

內容：只有 .go source 檔（裝到 /usr/share/gocode/src/...）
  沒有編譯產物（Go 沒有預編譯的 .so，編譯時才從 source 編）
  Architecture: all（純 source，架構無關）
```

> Go library 套件是 `-dev` 且只含 source、`Architecture: all`——這和 C library 完全不同（C 有編譯好的 `.so`，架構相關）。因為 Go 「編譯時需要依賴 source」，library 套件提供的就是 source 給下游編譯用。

## vendor 方式（哲學 B）

如果走 vendor 路線（用 upstream 的 vendor/ 目錄）：

```makefile
#!/usr/bin/make -f
%:
	dh $@ --buildsystem=golang --with=golang

override_dh_auto_configure:
	dh_auto_configure
	# 不刪 vendor/，讓 go build 用 vendored 依賴

export GOFLAGS=-mod=vendor    # 強制用 vendor 而非下載
```

vendor 方式 Build-Depends 不需要列每個 `golang-*-dev`（依賴在 vendor/ 裡），簡單很多。但 `Built-Using` 的追溯和安全更新就弱了（依賴藏在 vendor 裡，無法統一更新）。

## 故意弄壞：忘記 XS-Go-Import-Path

```bash
# control 沒有 XS-Go-Import-Path
dpkg-buildpackage -b
# dh-golang 不知道 import path
# → GOPATH 結構錯誤
# → go build 找不到套件自己
#   "cannot find package github.com/you/myapp"
```

`XS-Go-Import-Path` 是 Go 打包的關鍵——dh-golang 用它建立正確的 GOPATH 目錄結構（`$GOPATH/src/github.com/you/myapp`），讓 `go build` 能找到套件。漏了它，build 必然失敗。

## 踩雷集錦

1. **忘記 `XS-Go-Import-Path`**：dh-golang 建不出正確 GOPATH，go build 找不到套件。這是 Go 打包最關鍵的欄位

2. **忘記 `Built-Using`**：靜態連結的 Go binary 必須記錄編入的 source（法律 + 安全）。用 `${misc:Built-Using}` 自動生成

3. **Go library 套件標 `Architecture: any`**：Go library 套件只含 source（架構無關），應該 `all`。標 any 是誤解（以為像 C library 有編譯產物）

4. **混淆「打包所有依賴」和「vendor」沒做選擇**：兩種哲學要選一個並一致。半套（部分系統依賴、部分 vendor）會混亂

5. **Go 版本和依賴 source 版本不匹配**：Go modules 對版本敏感。系統打包的 `golang-*-dev` 版本可能和 upstream go.mod 要求的不同，導致 build 失敗或行為差異

6. **CGO 的混淆**：純 Go 是靜態的（`Architecture: any` 但無 `.so` 依賴）。但用了 CGO（呼叫 C library）的 Go 程式會動態連結那些 C library，這時 `${shlibs:Depends}` 才有意義

## 進階：Go modules、MIN 版本與 Debian 的張力

Go modules 的版本模型和 Debian 的 archive 模型有深層張力：

```
Go modules 的世界觀：
  每個程式在 go.mod 釘選依賴的「精確版本」
  go.sum 記錄 checksum
  → 每個程式可以用不同版本的同一個依賴

Debian 的世界觀：
  整個 archive 對每個 library 用「單一版本」（unstable 裡一個版本）
  所有套件共用、統一更新
  → 不允許「每個程式用不同版本」
```

這個張力是根本的：Go 鼓勵「每個專案釘選版本」，Debian 要求「全系統單一版本」。Debian 的處理：把 Go 依賴打包成 `golang-*-dev`（單一版本），所有 Go 程式用這個版本——可能和 upstream go.mod 釘選的不同。

```bash
# Debian 的 Go 套件不嚴格遵守 go.mod 的版本釘選
# 而是用 archive 裡那個版本的 golang-*-dev
# → 偶爾導致和 upstream 的細微行為差異
```

> 這個張力沒有完美解。Debian 選擇「整個 archive 的依賴一致性 + 安全更新」優先於「每個程式的精確版本釘選」。對某些對版本極敏感的 Go 程式，這可能不適合進 Debian（或要 vendor）。理解這個張力，你才知道 Go 打包的取捨在哪。Rust（cargo）有類似但不完全相同的張力。

## 動手練習

1. 打包一個簡單的 Go CLI（用一兩個依賴），設好 `XS-Go-Import-Path` 和 Build-Depends 的 `golang-*-dev`，確認 build 成功且 `Built-Using` 自動生成

2. 看一個真實 Go 套件的打包：`apt source` 一個 Debian 的 Go 程式（如 `hugo` 或 `restic` 如果有），看它的 control（import path、依賴、Built-Using）

3. 對比兩種哲學：找一個用「全部打包依賴」的 Go 套件和一個用 vendor 的，看 Build-Depends 的差異（前者列一堆 golang-*-dev，後者很少）

4. 看 Go library 套件：`apt show golang-github-spf13-cobra-dev`，確認它是 `Architecture: all`、只含 source

## 本章重點整理

- Go 靜態連結 + 編譯需要依賴 source，和 Debian「動態連結 + 統一更新」哲學衝突
- 兩種哲學：全部打包依賴（`golang-*-dev`，符合 Debian 但工作量大）vs vendor（簡單但難統一更新）
- `dh-golang` + `XS-Go-Import-Path`（關鍵）+ `Built-Using`（記錄靜態編入的 source）
- Go library 套件 `golang-*-dev` 只含 source、`Architecture: all`（和 C library 完全不同）
- Go modules 的「每程式釘選版本」和 Debian 的「全 archive 單一版本」有根本張力

## 自我檢核

- [ ] 能解釋為什麼 Go 打包和 C 打包根本不同（靜態連結、編譯需要 source）
- [ ] 知道 `Built-Using` 解決什麼問題（靜態連結的法律 + 安全追溯）
- [ ] 知道 `XS-Go-Import-Path` 為什麼是 Go 打包的關鍵欄位
- [ ] 能說出「全部打包依賴」和「vendor」兩種哲學的 trade-off
- [ ] 能描述 Go modules 版本模型和 Debian archive 模型的張力

## 延伸閱讀

### 官方文件

- **[Debian Go Packaging](https://go-team.pages.debian.net/packaging.html)** — Debian Go team
  - **讀哪裡**：packaging workflow、dh-golang 用法、依賴處理
  - **學什麼**：Debian Go 打包的官方指南；本章是教學版
  - **前提**：讀完本章

- **[dh-golang README](https://manpages.debian.org/bookworm/dh-golang/dh-golang.7.html)**
  - **讀哪裡**：buildsystem 行為和環境變數
  - **學什麼**：dh-golang 的完整運作
  - **前提**：本章

### 部落格 / 文章

- **[Packaging Go in Debian (Michael Stapelberg)](https://people.debian.org/~stapelberg/2019/01/16/cgo-static-linking.html)** — Michael Stapelberg（Debian Go team 核心）
  - **這篇說什麼**：Go 靜態連結、CGO、Debian 打包的深入討論
  - **讀哪裡**：static linking 和 packaging 那節
  - **為什麼值得讀**：作者是 Debian Go team 核心，講 Go 打包的真實挑戰和取捨

→ [Ch 29 打包 systemd service](./29-packaging-systemd.md)
