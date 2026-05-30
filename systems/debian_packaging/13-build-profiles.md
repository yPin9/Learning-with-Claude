# Ch 13 — Build profiles 與條件建置

> **目標**：理解 `DEB_BUILD_OPTIONS`、Build-Profiles、以及 build/host/target 三個架構概念——這些機制讓同一個 source 能在不同情境（跳過測試、bootstrap、交叉編譯）下彈性建置。

> **環境**：dpkg-dev 1.21.x、debhelper 13。Build profiles 由 Build-Profile spec 定義。

## 為什麼需要條件建置？

同一個 source package 可能要在很不同的情境下 build：

- **快速 build**：開發時跳過耗時的測試
- **Bootstrap**：在一個全新架構（如剛移植的 RISC-V）上，工具鏈還不完整時，需要 build 一個「精簡版」打破依賴循環
- **交叉編譯**：在 amd64 機器上 build arm64 套件（為嵌入式裝置）
- **無文件 build**：build farm 不需要生成龐大的文件

寫死的 build 流程無法應付這些。Debian 用幾個機制讓 build 可條件化。

## 先建立直覺：兩個層次的條件控制

```
DEB_BUILD_OPTIONS（環境變數，影響「怎麼 build」）
  nocheck   → 跳過測試
  noopt     → 不優化（-O0，方便 debug）
  parallel=N → 平行度
  → 不改變產出哪些套件，只改 build 方式

Build-Profiles（影響「build 什麼、需要什麼依賴」）
  nodoc     → 不生成文件套件
  nocheck   → 不需要測試相關的 Build-Depends
  stage1    → bootstrap 階段，只 build 核心
  cross     → 交叉編譯
  → 可以改變產出的套件集合和 build 依賴
```

兩者常一起用（如 `nocheck` 兩邊都有），但層次不同：`DEB_BUILD_OPTIONS` 調整 build 行為，Build-Profiles 調整 build 的「形狀」（依賴和產出）。

## DEB_BUILD_OPTIONS

這是個環境變數，空白分隔的選項列表，影響 build 行為：

```bash
# 跳過測試 + 不優化 + 用 4 核平行
DEB_BUILD_OPTIONS="nocheck noopt parallel=4" dpkg-buildpackage -b
```

常見選項（Policy §4.9.1 定義）：

| 選項 | 效果 |
|---|---|
| `nocheck` | 跳過 `dh_auto_test`（不跑測試）|
| `nostrip` | 不 strip binary（保留 debug symbols）|
| `noopt` | 編譯不優化（`-O0`），方便 debug |
| `parallel=N` | 平行 build 用 N 個 job |
| `nodoc` | 跳過文件生成 |
| `terse` | 減少 build 輸出 |

`dh_*` 工具會讀這個變數並調整行為。例如 `dh_auto_test` 看到 `nocheck` 就跳過測試；`dh_auto_build` 看到 `parallel=4` 就 `make -j4`。

```makefile
# 在 rules 裡手動讀 DEB_BUILD_OPTIONS（如果需要自訂邏輯）
ifneq (,$(filter nocheck,$(DEB_BUILD_OPTIONS)))
override_dh_auto_test:
	# nocheck 時跳過
endif
```

> 大部分情況 `dh_auto_*` 自動處理 `DEB_BUILD_OPTIONS`，你不用手動讀。只有自訂 build 邏輯時才需要在 rules 裡 filter。

## Build-Profiles：改變 build 的形狀

Build-Profiles 比 `DEB_BUILD_OPTIONS` 更強——它能改變**需要哪些 Build-Depends** 和**產出哪些套件**。透過 `DEB_BUILD_PROFILES` 環境變數啟用：

```bash
DEB_BUILD_PROFILES="nodoc nocheck" dpkg-buildpackage -b
```

在 `debian/control` 裡用 `<profile>` 標記條件依賴：

```
Build-Depends:
 debhelper-compat (= 13),
 libssl-dev,
 doxygen <!nodoc>,           ← 只在「沒有 nodoc profile」時需要
 valgrind <!nocheck>,        ← 只在「沒有 nocheck」時需要（測試用）
 python3-sphinx <!nodoc>,
```

語法：
- `<!profile>`：當 profile **未啟用**時才需要（最常見）
- `<profile>`：當 profile **啟用**時才需要
- `<!profile1 !profile2>`：多個條件（AND）

```
# 條件產出套件：nodoc 時不 build 文件套件
Package: foo-doc
Architecture: all
Build-Profiles: <!nodoc>     ← nodoc 啟用時，這個套件不 build
Depends: ${misc:Depends}
Description: documentation for foo
```

## 三個架構：build / host / target

交叉編譯（cross-compilation）涉及三個架構概念，這是初學者最容易混淆的地方：

```
build  架構：你「執行 build 工具」的機器（如你的 amd64 桌機）
host   架構：build 出的套件「將要執行」的機器（如 arm64 嵌入式板）
target 架構：（只對編譯器這種特殊套件）它「產生的程式碼」跑在哪

一般套件只需要 build 和 host：
  build=amd64, host=amd64  → 原生編譯（最常見）
  build=amd64, host=arm64  → 交叉編譯（在 amd64 編出 arm64 套件）

target 只對「編譯器套件」有意義：
  build=amd64, host=amd64, target=arm64
  → 在 amd64 上 build 一個「跑在 amd64、產生 arm64 程式碼」的交叉編譯器
```

```bash
# 看當前的架構環境
dpkg-architecture
# DEB_BUILD_ARCH=amd64      ← build 架構
# DEB_HOST_ARCH=amd64       ← host 架構（預設 = build）
# ...

# 交叉編譯：指定 host 架構
dpkg-buildpackage -aarm64    # build arm64 套件（在你的機器上）
# 或
DEB_HOST_ARCH=arm64 dpkg-buildpackage
```

> 「build vs host」的命名來自 autotools 的傳統，反直覺：**host** 是「套件最終執行的地方」，不是「你 build 的地方」。記法：站在「被 build 的程式」的角度——它的「host（家）」是它將執行的架構，「build」是它被製造的地方。

## 交叉編譯的 Build-Depends 標記

交叉編譯時，build 依賴分兩種：在 build 機器執行的工具（如編譯器本身）vs 連結進產物的 library（要 host 架構的版本）：

```
Build-Depends:
 debhelper-compat (= 13),
 libssl-dev,                    ← 預設視為 host 架構（要連結進產物）
 pkg-config:native,             ← :native 表示要 build 架構的版本（執行於 build 機）
 python3:native <cross>,        ← 交叉編譯時要原生 python
```

| 標記 | 意義 |
|---|---|
| （無標記）| host 架構（交叉編譯時要 host 版本的 library）|
| `:native` | build 架構（在 build 機器執行的工具）|
| `:any` | 任何架構皆可（Multi-Arch: allowed 的套件）|

交叉編譯是個深水區，涉及 Multi-Arch（Ch 18）。這裡先建立「build 依賴要區分原生工具和目標 library」的概念。

## 故意弄壞：nocheck 沒生效

```bash
# 你以為設了 DEB_BUILD_OPTIONS 就會跳過測試
DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -b
# 但測試還是跑了！

# 原因：rules 裡 override 了 dh_auto_test 但沒檢查 nocheck
cat debian/rules
# override_dh_auto_test:
#     ./run-my-tests.sh        ← 硬跑，沒看 DEB_BUILD_OPTIONS

# 修正：讓 override 尊重 nocheck
# override_dh_auto_test:
# ifeq (,$(filter nocheck,$(DEB_BUILD_OPTIONS)))
#     ./run-my-tests.sh
# endif
```

教訓：如果你 override 了 `dh_auto_test`，預設的 `nocheck` 處理就沒了，要自己加回。`dh_auto_test`（沒 override 時）會自動尊重 `nocheck`。

## 進階：bootstrap profiles 與依賴循環

一個新架構（如新移植的 LoongArch）要從零開始 build 整個 Debian。問題：很多套件有**依賴循環**——A 的測試需要 B，B 的 build 需要 A。在沒有任何套件的全新架構上，這個循環無法啟動。

Build-Profiles 的 `stage1` / `stageN` 解決這個：

```
Build-Depends:
 debhelper-compat (= 13),
 libfancy-dev <!stage1>,    ← stage1 時不需要這個（打破循環）

Package: foo
...
# stage1 build 一個精簡版的 foo（少功能但能用來 build 別的）
# 等依賴鏈建立後，再正常 build 完整版
```

bootstrap 的流程：先用 `stage1` profile build 各套件的精簡版打破循環，逐步建立依賴鏈，最後正常 build 完整版。這是移植 Debian 到新架構的關鍵機制，由 `bootstrap.debian.net` 等工具自動化。

一般打包者很少手寫 stage profiles，但理解它的存在能解釋「為什麼某些 Build-Depends 有 `<!stage1>` 標記」。

## 動手練習

1. 用不同的 `DEB_BUILD_OPTIONS` build 同一個套件，觀察差別：
   ```bash
   dpkg-buildpackage -b                           # 正常
   DEB_BUILD_OPTIONS=nocheck dpkg-buildpackage -b # 看測試被跳過
   DEB_BUILD_OPTIONS=noopt dpkg-buildpackage -b   # 看編譯 flag 變 -O0
   ```

2. 跑 `dpkg-architecture` 看你的 build/host 架構。再 `dpkg-architecture -aarm64` 看交叉編譯時各變數變成什麼

3. 找一個有 `<!nodoc>` 或 `<!nocheck>` 標記的套件（很多大套件有），看它的 `Build-Depends` 怎麼用 profile 標記條件依賴

4. 在一個套件的 rules override `dh_auto_test`（硬跑測試），故意不檢查 nocheck，確認 `DEB_BUILD_OPTIONS=nocheck` 無效，再加上 nocheck 檢查修復

## 本章重點整理

- `DEB_BUILD_OPTIONS`（nocheck/noopt/parallel...）調整 build 行為，不改變產出的套件集合
- Build-Profiles（nodoc/nocheck/stage1/cross）能改變 Build-Depends 和產出套件，用 `<!profile>` 標記
- build 架構 = 製造的機器，host 架構 = 套件將執行的機器（命名反直覺）
- 交叉編譯用 `-a<arch>` 或 `DEB_HOST_ARCH`；build 依賴用 `:native` 區分原生工具
- override `dh_auto_test` 後要自己處理 `nocheck`，否則它失效

## 自我檢核

- [ ] 能說出 `DEB_BUILD_OPTIONS` 和 Build-Profiles 的層次差別
- [ ] 不看筆記，能解釋交叉編譯的 build vs host 架構（誰是製造、誰是執行）
- [ ] 知道 `<!nodoc>` 在 Build-Depends 裡是什麼意思
- [ ] 知道為什麼 override `dh_auto_test` 後 `nocheck` 可能失效
- [ ] 能說出 stage1 profile 解決什麼問題（bootstrap 依賴循環）

## 延伸閱讀

### 官方文件

- **[Debian Policy §4.9.1 (DEB_BUILD_OPTIONS)](https://www.debian.org/doc/debian-policy/ch-source.html#debian-rules-and-deb-build-options)**
  - **讀哪裡**：所有 `DEB_BUILD_OPTIONS` 選項的定義
  - **學什麼**：每個選項的精確語意；本章列了常見的，這是完整清單
  - **前提**：讀完本章

- **[Debian BuildProfileSpec](https://wiki.debian.org/BuildProfileSpec)**
  - **讀哪裡**：profile 語法和 well-known profiles（nodoc/nocheck/stage1/cross...）
  - **學什麼**：Build-Profiles 的完整規格和標準 profile 列表
  - **前提**：本章的 Build-Profiles 部分

### 部落格 / 文章

- **[Multiarch cross-building HOWTO](https://wiki.debian.org/CrossCompiling)** — Debian Wiki
  - **這篇說什麼**：交叉編譯的完整實戰，build/host 架構、:native 標記、常見問題
  - **讀哪裡**：前半的概念和 build/host 解釋
  - **為什麼值得讀**：交叉編譯是本章較淺的部分，這份 HOWTO 補足細節（配合 Ch 18 Multi-Arch）

→ [練習 B：打包一個真實 C 專案](./practice-b-package-c-project.md)
