# Ch 14 — dpkg-buildpackage 全流程

> **目標**：理解 `dpkg-buildpackage` 從 source 到 `.deb` 的完整流程、它呼叫的每個子工具、`.changes` 和 `.buildinfo` 檔案的角色、以及常用 flag 的意義——這是把前面所有 `debian/` 知識「執行」起來的指揮工具。

> **環境**：dpkg-dev 1.21.x。`debuild`（devscripts）是 `dpkg-buildpackage` 的常用 wrapper，本章兩者都講。

## 為什麼需要理解整個流程？

前面幾章你寫了 `debian/control`、`rules`、`changelog`...，但「按下 build 鍵」後到底發生什麼？`dpkg-buildpackage` 是那個指揮官——它按順序呼叫 `dpkg-source`、`debian/rules`、`dpkg-genchanges`、簽署工具，把你的 source 變成完整的、可上傳的一組檔案。

理解這個流程，你才能 debug 「build 在哪一步失敗」、知道產出的各個檔案是什麼、以及為什麼有 `.changes` 和 `.buildinfo`。

## 先建立直覺：dpkg-buildpackage 的指揮順序

```
dpkg-buildpackage 依序做：

  1. dpkg-checkbuilddeps    ← 檢查 Build-Depends 是否滿足
       │
  2. debian/rules clean     ← 清理
       │
  3. dpkg-source -b         ← （若要 source）打包 source package（.dsc + tarballs）
       │
  4. debian/rules build     ← 編譯（dh_auto_configure/build/test）
       │
  5. debian/rules binary    ← 打包成 .deb（dh_auto_install + 一堆 dh_*）
       │
  6. dpkg-genbuildinfo      ← 生成 .buildinfo（記錄 build 環境）
       │
  7. dpkg-genchanges        ← 生成 .changes（上傳清單）
       │
  8. dpkg-source --after-build
       │
  9. signing（debsign）      ← 簽署 .dsc 和 .changes（除非 -us -uc）
```

每一步失敗都會中止，並告訴你卡在哪。理解這個順序，build log 的每一段就有了意義。

## 常用 flag

```bash
# 最常用的學習組合
dpkg-buildpackage -us -uc -b
#   -us : 不簽署 source（.dsc）
#   -uc : 不簽署 .changes
#   -b  : 只 build binary（不打包 source）

# 各種 build 範圍
dpkg-buildpackage -b      # binary only（不要 source）
dpkg-buildpackage -S      # source only（只打包 source，不編譯）
dpkg-buildpackage -A      # 只 build architecture-independent（all）套件
dpkg-buildpackage -B      # 只 build architecture-dependent（any）套件
dpkg-buildpackage         # 全部（source + binary，預設且會簽署）

# 其他常用
dpkg-buildpackage -nc     # no clean，不執行 rules clean（增量 build，debug 用）
dpkg-buildpackage -j4     # parallel build 用 4 核
dpkg-buildpackage -d      # 不檢查 build 依賴（危險，debug 用）
```

| 範圍 flag | 產出 | 用途 |
|---|---|---|
| （無）| source + 所有 binary | 正式上傳 |
| `-b` | 所有 binary（不含 source）| 本地測試、binary-only 重建 |
| `-S` | 只 source（.dsc + tarballs）| 上傳到 build farm 讓它編 |
| `-A` | 只 arch-indep（all）| 多架構流程的一部分 |
| `-B` | 只 arch-dep（any）| 同上 |

> Debian 的 build farm 流程：維護者上傳 `-S`（純 source），build farm 對每個架構各跑一次 `-B`（編出該架構的 binary）。維護者本機通常用 `-b` 測試。

## 產出的檔案

一次完整 build 後（在上層目錄）：

```bash
ls ../
# greet_1.0-1.dsc              ← source 描述檔（Ch 6）
# greet_1.0.orig.tar.gz        ← upstream source
# greet_1.0-1.debian.tar.xz    ← Debian 修改
# greet_1.0-1_amd64.deb        ← binary packages
# libgreet1_1.0-1_amd64.deb
# libgreet-dev_1.0-1_amd64.deb
# greet_1.0-1_amd64.buildinfo  ← build 環境記錄
# greet_1.0-1_amd64.changes    ← 上傳清單
```

### .changes — 上傳清單

```bash
cat greet_1.0-1_amd64.changes
```

```
Format: 1.8
Date: Thu, 29 May 2025 12:00:00 +0000
Source: greet
Binary: greet libgreet1 libgreet-dev
Architecture: amd64 source
Version: 1.0-1
Distribution: unstable        ← 來自 changelog 第一行
Maintainer: Your Name <you@example.com>
Changes:                      ← 來自 changelog
 greet (1.0-1) unstable; urgency=medium
 .
   * Initial release.
Checksums-Sha256:             ← 綁定所有產出檔案的 checksum
 ... greet_1.0-1.dsc
 ... greet_1.0-1_amd64.deb
 ... libgreet1_1.0-1_amd64.deb
Files:
 ...
```

`.changes` 是**上傳的清單與憑證**。`dput`（上傳工具，Ch 20）讀它，知道要上傳哪些檔案、它們的 checksum、目標 distribution。簽署 `.changes` 等於簽署「這整組檔案是我發布的」。

### .buildinfo — build 環境記錄

```bash
cat greet_1.0-1_amd64.buildinfo
```

```
Format: 1.0
Source: greet
Binary: greet libgreet1 libgreet-dev
Architecture: amd64
Version: 1.0-1
Build-Architecture: amd64
Installed-Build-Depends:        ← build 時系統裝了哪些套件的精確版本！
 debhelper-compat (= 13),
 gcc (= 4:12.2.0-3),
 libc6-dev (= 2.36-9),
 ... （build 環境的完整快照）
Environment:
 DEB_BUILD_OPTIONS="parallel=4"
 ...
```

`.buildinfo` 是 **reproducible builds 的關鍵**。它精確記錄 build 時的環境（每個 build 依賴的確切版本、環境變數）。有了它，別人能重建**完全相同**的 build 環境，驗證能 byte-for-byte 重現你的 `.deb`。這是供應鏈安全的基石（Ch 4 提過 reproducible builds）。

## debuild：更方便的 wrapper

`debuild`（devscripts 提供）包裝 `dpkg-buildpackage`，自動加上品質檢查：

```bash
debuild -us -uc
#   = dpkg-buildpackage + 自動跑 lintian + 設好乾淨的環境變數
```

`debuild` 的好處：
- build 後**自動跑 lintian**（Ch 16），不用手動
- 清理環境變數（避免你的 shell 環境污染 build）
- 設定 `DEB_BUILD_OPTIONS` 等的合理預設

> 日常開發推薦 `debuild -us -uc`（自動 lintian 很方便）。但要注意 `debuild` 仍然在你的 host 系統 build，不是 clean room——真正的乾淨 build 要用 sbuild（Ch 15）。

## build 流程的環境變數

build 過程中，dpkg 設定一堆環境變數供 `rules` 和 `dh_*` 使用：

```bash
# build 時這些變數可用
DEB_BUILD_ARCH        # build 架構（amd64）
DEB_HOST_ARCH         # host 架構（交叉編譯時不同）
DEB_HOST_MULTIARCH    # multiarch triplet（x86_64-linux-gnu）
DEB_BUILD_OPTIONS     # nocheck/noopt/parallel=N（Ch 13）
SOURCE_DATE_EPOCH     # 固定的 timestamp（reproducible builds）

# 在 rules 裡可以讀它們
# override_dh_auto_configure:
#     dh_auto_configure -- --host=$(DEB_HOST_GNU_TYPE)
```

## 故意弄壞：build 依賴沒滿足

```bash
dpkg-buildpackage -us -uc -b
# dpkg-buildpackage: info: source package greet
# dpkg-checkbuilddeps: error: Unmet build dependencies: libssl-dev
# dpkg-buildpackage: warning: build dependencies/conflicts unsatisfied; aborting
# dpkg-buildpackage: warning: (Use -d flag to override.)
```

第一步 `dpkg-checkbuilddeps` 就擋下來了。解法：
```bash
sudo apt build-dep .         # 在 source 目錄裡，裝齊 Build-Depends
# 或
sudo mk-build-deps -ir       # devscripts 工具，建一個假套件拉所有 build 依賴
```

`mk-build-deps -ir` 是個巧妙的工具：它讀 `debian/control` 的 Build-Depends，生成一個空的 `.deb`（只有依賴），裝上它讓 apt 拉齊所有 build 依賴。`-r` 之後移除這個假套件很乾淨。

## 進階：dpkg-buildpackage 與 hook

`dpkg-buildpackage` 支援在各階段插入 hook（debug 或客製化用）：

```bash
dpkg-buildpackage --hook-build='echo "build starting"' \
                  --hook-binary='echo "binary stage"' -b
```

更實用的是理解它和 `debian/rules` 的介面：`dpkg-buildpackage` 只呼叫 rules 的標準 target（clean/build/binary），所有實際工作在 rules（即 dh sequence）裡。所以 build 出問題，先看是 `dpkg-buildpackage` 層（依賴檢查、source 打包）還是 rules 層（編譯、安裝）。

```bash
# 直接呼叫 rules 的某個 target 來隔離問題
fakeroot debian/rules clean
fakeroot debian/rules build       # 只跑編譯，不打包
fakeroot debian/rules binary      # 只跑打包
# 這樣能精確定位失敗在哪個階段
```

## 踩雷集錦

1. **忘記 `-us -uc` 結果卡在簽署**：不加這兩個 flag，build 最後會嘗試用 GPG 簽署，沒設 key 就卡住或報錯。學習階段一律 `-us -uc`

2. **以為 `-b` 不用 orig tarball**：`-b`（binary only）仍然需要 orig tarball 存在（在上層目錄）才能解開 source。`-b` 只是不**重新打包** source，不是不需要它

3. **在 host 系統 build 以為乾淨**：`dpkg-buildpackage` 和 `debuild` 在你的系統 build，會受你裝的套件影響。「在我機器上 build 成功」不代表在乾淨環境成功。正式驗證要 sbuild（Ch 15）

4. **`-nc` 留下髒狀態導致詭異錯誤**：`-nc`（no clean）跳過 clean，適合快速重 build，但上次 build 的殘留檔案可能造成莫名問題。出怪事時先正常 build（不加 -nc）排除

5. **改了 control 的依賴沒重裝 build-dep**：你在 control 加了新的 Build-Depends，但沒 `apt build-dep` 重裝，build 失敗。改 Build-Depends 後要重新裝依賴

## 動手練習

1. 對練習 B 的 greet 專案跑各種 build 範圍：`-b`、`-S`、`-A`、`-B`，比較產出的檔案差異。`-S` 產出什麼？`-b` 產出什麼？

2. 讀一次完整的 build log（`dpkg-buildpackage -b 2>&1 | less`），對照本章的指揮順序，找出 `dpkg-checkbuilddeps`、`dpkg-source`、`debian/rules build/binary`、`dpkg-genchanges` 各在哪裡

3. 看 `.changes` 和 `.buildinfo` 的內容，理解前者是「上傳清單」後者是「環境快照」。`.buildinfo` 裡的 `Installed-Build-Depends` 有幾個套件？

4. 用 `mk-build-deps -ir` 裝 build 依賴，build 完用 `apt remove` 移除那個假套件。對比直接 `apt build-dep` 的差別

## 本章重點整理

- `dpkg-buildpackage` 是指揮官：依序跑 checkbuilddeps → rules clean/build/binary → genchanges → 簽署
- 範圍 flag：`-b`（binary）/`-S`（source）/`-A`（indep）/`-B`（arch）；`-us -uc` 不簽署
- `.changes` 是上傳清單（dput 讀它）；`.buildinfo` 是 build 環境快照（reproducible builds 用）
- `debuild` 是方便的 wrapper（自動跑 lintian），但仍在 host 系統 build（非 clean room）
- `mk-build-deps -ir` 用假套件巧妙地裝齊並可乾淨移除 build 依賴

## 自我檢核

- [ ] 不看筆記，能說出 `dpkg-buildpackage` 的主要步驟順序
- [ ] 知道 `-b`、`-S`、`-A`、`-B` 各產出什麼，以及 Debian build farm 怎麼用 `-S` + `-B`
- [ ] 能解釋 `.changes` 和 `.buildinfo` 各自的角色
- [ ] 知道為什麼 `debuild` 方便但不算乾淨 build
- [ ] 知道 `mk-build-deps -ir` 做什麼

## 延伸閱讀

### 官方文件

- **[dpkg-buildpackage(1) man page](https://manpages.debian.org/bookworm/dpkg-dev/dpkg-buildpackage.1.html)**
  - **讀哪裡**：所有 flag 的說明，特別是 `-b/-S/-A/-B` 和簽署選項
  - **學什麼**：完整的 flag 參考；本章講了常用的
  - **前提**：讀完本章

- **[deb-changes(5)](https://manpages.debian.org/bookworm/dpkg-dev/deb-changes.5.html)** 和 **[deb-buildinfo(5)](https://manpages.debian.org/bookworm/dpkg-dev/deb-buildinfo.5.html)**
  - **讀哪裡**：兩個檔案格式的欄位定義
  - **學什麼**：`.changes` 和 `.buildinfo` 每個欄位的精確意義
  - **前提**：本章的對應段落

### 部落格 / 文章

- **[Reproducible Builds: .buildinfo files](https://reproducible-builds.org/docs/recording/)** — Reproducible Builds 計畫
  - **這篇說什麼**：`.buildinfo` 如何記錄 build 環境、如何用它重現 build
  - **讀哪裡**：buildinfo 那節
  - **為什麼值得讀**：把 `.buildinfo` 和供應鏈安全連起來，理解它不只是個附帶檔案

→ [Ch 15 Clean build：sbuild 與 pbuilder](./15-clean-build-sbuild.md)
