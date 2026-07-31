# Ch 9 — 常見雷：sstate-cache / DEPENDS / PREFERRED_VERSION

> **目標**：總結 Yocto 日常最容易踩的坑——sstate-cache 沒重建、DEPENDS vs RDEPENDS、PREFERRED_VERSION/PROVIDER、layer 版本不相容、patch 套不上等。每個附症狀 + 診斷 + 修法。這章就是「早說就好」的 debug 指南——對 compiler 工程師，理解這些讓你 debug 客戶的 Yocto build 問題時知道往哪看（是 compiler patch 問題還是 Yocto 設定問題）。

> **環境**：Yocto（poky + meta-riscv，Ch 3）。

## 為什麼集中講坑？

Yocto 的學習曲線陡，很大原因是**坑多**——build 莫名失敗、改了 recipe 沒生效、版本選錯、依賴缺。新手常卡在這些坑上，浪費大量時間。而這些坑大多是**重複出現的固定模式**——理解它們，遇到時能快速診斷和修。

對 compiler 工程師，這特別重要——你的核心工作是「客戶 Yocto build break 時 diagnose 是 compiler patch 問題還是 Yocto 問題」（README）。很多 build 問題其實是**Yocto 的坑**（sstate 沒重建、依賴缺、版本不對），不是 compiler 的問題。理解這些坑讓你能快速排除「這不是我的 patch 的問題，是 Yocto 設定/快取的問題」，或反之確認「這是我的 patch 的問題」。這章集中講最常見的坑——這是你 debug Yocto 的核心參考。

## 坑一:sstate-cache 沒重建（最常見！）

```bash
# 症狀：改了 recipe/patch，但 build 結果沒變（用了舊的快取）
# 你加了 patch、bitbake gcc，但 gcc 還是舊的行為

# 原因：sstate-cache（shared state cache）
# Yocto 快取每個 task 的結果，避免重建（加速 build）
# 但如果它「以為」你的改動不影響某 task，會用快取的舊結果
# → 你改了 recipe，但 sstate 用了舊的 → 改動沒生效

# 診斷：確認 task 有沒有重跑
bitbake gcc-cross-riscv64 2>&1 | grep -E 'do_compile|Setscene'
# 如果看到 "Setscene" / 從 sstate 還原 → 用了快取（沒重建）

# 修法：強制重建
bitbake -c cleansstate gcc-cross-riscv64    # 清掉這個 recipe 的 sstate
bitbake gcc-cross-riscv64                    # 重新 build（這次真的重建）

# 或更徹底（謹慎）：
# bitbake -c clean gcc-cross-riscv64         # 清 work（不清 sstate）
# rm -rf sstate-cache/                       # 清整個 sstate（核武，會很久）

# → 改了 recipe 但沒生效，第一個懷疑：sstate-cache
#   cleansstate 強制重建。這是 Yocto 最常見的坑！
```

> **改了 recipe 但 build 結果沒變——第一個懷疑 sstate-cache（用了舊快取），用 `cleansstate` 強制重建**。**sstate-cache（shared state cache）** 是 Yocto 最常見的坑。Yocto 快取每個 task 的結果（避免重建，加速 build），但有時它**以為你的改動不影響某 task，用了快取的舊結果**——你改了 recipe/patch，但 build 出來還是舊的（改動沒生效）。**症狀**：改了 recipe/加了 patch，但 build 結果沒變（gcc 還是舊行為）。**診斷**：`bitbake gcc 2>&1 | grep Setscene`（如果從 sstate 還原 = 用了快取沒重建）。**修法**：**`bitbake -c cleansstate <recipe>`**（清掉這個 recipe 的 sstate）+ rebuild（這次真的重建）。這是 Ch 5 patch GCC 時強調的——**改了 recipe 後要 cleansstate 確保重建**（否則 patch 可能沒生效）。對 compiler 工程師，這是**第一個要懷疑的坑**——「我加了 patch 但沒生效」很可能是 sstate 用了舊快取（不是 patch 的問題）。記住：**改了 recipe 但沒生效，第一個懷疑 sstate-cache，用 cleansstate 強制重建**。這個坑害無數人——以為自己的改動沒效（其實是用了快取），浪費時間 debug 不存在的問題。理解 sstate（它是加速 build 的快取，但有時太聰明用了舊的），你 debug「改動沒生效」就先排除這個。

## 坑二:DEPENDS vs RDEPENDS

```bash
# 症狀：build 時找不到某 library，或 runtime 時缺某東西
# 混淆了 build 依賴和 runtime 依賴

# DEPENDS（build 時依賴）：
#   建這個 recipe「需要」的其他 recipe（編譯時用）
#   例：DEPENDS = "zlib openssl"（編譯時要連結這些）
#   → build 時這些要先建好

# RDEPENDS（runtime 依賴）：
#   跑這個套件「需要」的其他套件（執行時用）
#   例：RDEPENDS:${PN} = "bash python3"（執行時要這些在 rootfs）
#   → 安裝到 rootfs 時這些也要裝

# 常見錯誤：
# 1. 該 DEPENDS 卻沒寫 → build 時找不到 library（do_compile/configure 失敗）
# 2. 該 RDEPENDS 卻沒寫 → runtime 時缺東西（程式跑不起來，缺 library/工具）
# 3. 混淆兩者 → build 依賴寫成 runtime（或反之）

# 診斷：
bitbake -e my-recipe 2>/dev/null | grep -E '^DEPENDS=|^RDEPENDS'
# 看依賴設對沒

# → DEPENDS = build 時要的、RDEPENDS = runtime 要的
#   分清楚，build 找不到 library 看 DEPENDS、runtime 缺東西看 RDEPENDS
```

> **DEPENDS（build 時依賴）vs RDEPENDS（runtime 依賴）——build 找不到 library 看 DEPENDS、runtime 缺東西看 RDEPENDS**。**DEPENDS vs RDEPENDS** 的混淆是常見坑。**DEPENDS**（**build 時依賴**——建這個 recipe 編譯時需要的其他 recipe，如 `DEPENDS = "zlib openssl"` 表示編譯時要連結這些，build 時這些要先建好）；**RDEPENDS**（**runtime 依賴**——跑這個套件執行時需要的其他套件，如 `RDEPENDS:${PN} = "bash python3"` 表示執行時要這些在 rootfs，安裝到 rootfs 時這些也要裝）。**常見錯誤**：(1) 該 DEPENDS 沒寫 → **build 時找不到 library**（do_compile/configure 失敗）；(2) 該 RDEPENDS 沒寫 → **runtime 時缺東西**（程式在 image 裡跑不起來，缺 library/工具）；(3) 混淆兩者。**診斷**：`bitbake -e my-recipe | grep DEPENDS`（看依賴設對沒）。**判斷**：**build 找不到 library 看 DEPENDS**（編譯時的依賴）、**runtime 缺東西看 RDEPENDS**（執行時的依賴）。對 compiler 工程師，理解這個區別讓你 debug 依賴問題——「build 時 gcc 找不到某 library」是 DEPENDS 問題、「image 裡程式缺某東西」是 RDEPENDS 問題。這也呼應 Ch 4 的 toolchain——gcc 的 DEPENDS（build gcc 要 binutils 等）vs gcc-runtime 的 RDEPENDS。分清 build 時 vs runtime 的依賴，是理解 Yocto 依賴的基礎。

## 坑三:PREFERRED_VERSION / PROVIDER

```bash
# 症狀：用了錯的版本，或多個 recipe 提供同個東西時選錯

# PREFERRED_VERSION：多版本時選哪個
# 如果有 gcc_13.2.bb 和 gcc_14.1.bb，預設選高版本
# 要指定用某版本：
# PREFERRED_VERSION_gcc = "13.2%"     # 用 13.2（% = 任何子版本）

# PREFERRED_PROVIDER：多個 recipe 提供同個 PROVIDES 時選哪個
# 例：多個 recipe 都 PROVIDES = "virtual/kernel"
# PREFERRED_PROVIDER_virtual/kernel = "linux-yocto"   # 選哪個

# 症狀和診斷：
# 用了非預期的版本/provider → build 出來不對
bitbake -e gcc 2>/dev/null | grep -E '^PV=|PREFERRED'
# 看選了哪個版本

# 對 compiler 工程師：
# 客戶有多個 gcc 版本，你的 patch 針對某版本
# 要確認 PREFERRED_VERSION 選的是你 patch 的版本
# 否則：你 patch 了 gcc 13.2，但 build 用 gcc 14.1（你的 patch 沒用上！）

# 修法：設 PREFERRED_VERSION 選對版本
# PREFERRED_VERSION_gcc = "13.2%"

# → 多版本/多 provider 時，用 PREFERRED_VERSION/PROVIDER 明確選
#   compiler 工程師：確認 build 用的是你 patch 的 gcc 版本
```

> **PREFERRED_VERSION（多版本選哪個）/ PREFERRED_PROVIDER（多 provider 選哪個）——compiler 工程師要確認 build 用的是你 patch 的 gcc 版本**。**PREFERRED_VERSION / PREFERRED_PROVIDER** 的坑——當有**多個版本或多個 provider**時，選錯的問題。**PREFERRED_VERSION**——多版本時選哪個（如有 gcc_13.2.bb 和 gcc_14.1.bb，預設選高版本；`PREFERRED_VERSION_gcc = "13.2%"` 指定用 13.2）；**PREFERRED_PROVIDER**——多個 recipe 提供同個 PROVIDES 時選哪個（如多個 recipe 都 `PROVIDES = "virtual/kernel"`，`PREFERRED_PROVIDER_virtual/kernel = "linux-yocto"` 選哪個）。**症狀**：用了非預期的版本/provider，build 出來不對。**診斷**：`bitbake -e gcc | grep -E 'PV=|PREFERRED'`（看選了哪個版本）。**對 compiler 工程師特別重要**——客戶可能有多個 gcc 版本，你的 patch 針對某版本（如 13.2），要**確認 PREFERRED_VERSION 選的是你 patch 的版本**——否則：你 patch 了 gcc 13.2，但 build 用 gcc 14.1（你的 patch 沒用上！這很隱蔽——build 成功但用錯版本，你的 patch 白做）。**修法**：設 `PREFERRED_VERSION_gcc = "13.2%"` 選對版本。這呼應 Ch 1 的 `bitbake -e` 追變數——確認 gcc 的版本（PV）是你 patch 的。對 compiler 工程師，這是要確認的——**build 用的 gcc 版本 = 你 patch 的版本**（用 PREFERRED_VERSION 確保 + bitbake -e 驗證）。多版本/多 provider 時明確指定（PREFERRED_VERSION/PROVIDER），避免選錯。

## 坑四到坑七:其他常見坑

```
坑四：layer 版本不相容（Ch 3 提過）
  症狀：ERROR: Layer X is not compatible with core layer
  原因：layer 版本不對應（poky scarthgap + meta-riscv kirkstone）
  修法：所有 layer 用對應的版本（都 scarthgap）
        │
坑五：patch 套不上（Ch 5 提過）
  症狀：do_patch 失敗，hunk FAILED
  原因：patch 和 source 版本不 match
  修法：rebase patch 到對的版本（或 devtool upgrade）
        │
坑六：磁碟空間不足
  症狀：build 失敗，No space left
  原因：Yocto 吃 30+ GB，tmp/ 和 sstate 佔滿
  修法：清 tmp/、用 rm_work（build 完自動清 work）、加磁碟
        │
坑七：找不到 patch 檔（FILESEXTRAPATHS）
  症狀：do_fetch/unpack 失敗，找不到 file://xxx.patch
  原因：FILESEXTRAPATHS 沒設或路徑錯（Ch 2/5）
  修法：設對 FILESEXTRAPATHS:prepend，patch 放對目錄
        │
坑八：bitbake 解析錯誤
  症狀：語法錯誤、變數展開錯
  原因：recipe 語法錯（:append 忘了空格、override 拼錯）
  修法：bitbake -e 看變數、檢查語法（Ch 2）
```

> **layer 版本不相容、patch 套不上、磁碟不足、找不到 patch 檔、語法錯——這些坑各有固定的症狀和修法，認得就能快速排除**。其他常見坑（各有固定模式）：**坑四 layer 版本不相容**（Ch 3——症狀 "not compatible"，修法所有 layer 用對應版本）；**坑五 patch 套不上**（Ch 5——症狀 hunk FAILED，修法 rebase patch 或 devtool upgrade）；**坑六 磁碟空間不足**（症狀 "No space left"，Yocto 吃 30+ GB，修法清 tmp/、用 **`INHERIT += "rm_work"`**（build 完自動清 work 目錄省空間）、加磁碟）；**坑七 找不到 patch 檔**（症狀 do_unpack 失敗找不到 file://xxx.patch，原因 FILESEXTRAPATHS 沒設或路徑錯，修法設對 FILESEXTRAPATHS + patch 放對目錄，Ch 2/5）；**坑八 bitbake 解析錯誤**（症狀語法錯/變數展開錯，原因 recipe 語法錯如 :append 忘了空格、override 拼錯，修法 bitbake -e 看變數、檢查語法，Ch 2）。這些坑**各有固定的症狀和修法**——認得症狀就能快速對應到原因和修法（不用每次從頭 debug）。對 compiler 工程師，這個「坑的型錄」是 debug Yocto 的快速參考——遇到問題先對照症狀（是哪個坑），用對應的修法。很多 build 問題是這些 Yocto 的坑（不是 compiler 的問題）——認得它們讓你快速排除「這是 Yocto 的坑」vs「這是我的 patch 的問題」。這正是你的核心工作（diagnose 問題歸屬）——理解這些坑讓你不會把 Yocto 的坑誤認為 compiler 問題（或反之）。

## debug 方法論:定位問題歸屬

```
compiler 工程師 debug Yocto build 的方法論：

  build 失敗 → 系統地定位「是什麼問題」：
        │
  1. 看 bitbake 的 error（它指向失敗的 recipe + task + log）
        │
  2. 看失敗的 task（哪一步）：
     do_fetch/unpack → source/patch 問題（Yocto）
     do_patch → patch 套不上（patch 版本不 match）
     do_configure → 設定問題（Yocto recipe）
     do_compile → 編譯錯誤 ★ 可能是 compiler patch 問題！
        │
  3. 看 log（temp/log.do_xxx）找實際錯誤
        │
  4. 判斷問題歸屬：
     是 Yocto 的坑（sstate/依賴/版本/語法）→ 修 Yocto 設定
     是 compiler patch 問題（gcc 編譯出錯/產生壞 code）→ 修 patch
        │
  5. 排除法：
     先排除 sstate（cleansstate 重試）
     確認版本對（PREFERRED_VERSION）
     確認 patch 生效（bitbake -e）
        │
  → 系統地定位：task → log → 問題歸屬 → 修
    這是 compiler 工程師 debug Yocto 的核心方法論
```

> **debug Yocto 的方法論：看失敗的 task → 看 log → 判斷問題歸屬（Yocto 坑 vs compiler patch）→ 排除法（先排除 sstate/版本）——這是 compiler 工程師的核心 debug 流程**。compiler 工程師 debug Yocto build 失敗的**系統方法論**：(1) **看 bitbake 的 error**（它指向失敗的 recipe + task + log 位置）；(2) **看失敗的 task**（Ch 4——哪一步：do_fetch/unpack = source/patch 問題、do_patch = patch 套不上、do_configure = 設定問題、**do_compile = 編譯錯誤，可能是 compiler patch 問題**）；(3) **看 log**（`temp/log.do_xxx` 找實際錯誤）；(4) **判斷問題歸屬**——**是 Yocto 的坑**（sstate/依賴/版本/語法 → 修 Yocto 設定）還是 **compiler patch 問題**（gcc 編譯出錯/產生壞 code → 修 patch）；(5) **排除法**——先排除 sstate（cleansstate 重試）、確認版本對（PREFERRED_VERSION）、確認 patch 生效（bitbake -e）。**這是 compiler 工程師 debug Yocto 的核心方法論**（README 的「diagnose 是 compiler patch 問題還是 Yocto 問題」）——系統地從 task → log → 問題歸屬 → 修。關鍵是**判斷問題歸屬**——很多問題是 Yocto 的坑（你快速排除「不是我的 patch」），有些是 compiler 問題（你修 patch）。這個方法論讓你高效 debug——不亂猜，系統地定位（哪個 task、什麼 log、什麼歸屬）。對 compiler 工程師，這是 day-to-day 的核心技能——客戶報 build 失敗，你用這個方法論快速定位和歸屬問題。理解這些坑（型錄）+ debug 方法論（流程），你能高效處理 Yocto build 問題，準確判斷是 compiler 還是 Yocto 的問題。這章是 yocto 課對 compiler 工程師最實用的 debug 參考。

## 動手練習

1. sstate 坑：改一個 recipe 看沒生效，用 cleansstate 修，體會這個最常見的坑

2. DEPENDS/RDEPENDS：理解兩者差別，看一個 recipe 的依賴設定

3. PREFERRED_VERSION：理解多版本時怎麼選，確認 gcc 用的版本

4. 製造坑：故意製造一個坑（patch 套不上/找不到 patch 檔），用方法論 debug

5. debug 方法論：對一個 build 失敗，走「task → log → 歸屬」的流程

## 本章重點整理

- sstate-cache 沒重建（最常見）：改了 recipe 沒生效，用 cleansstate 強制重建
- DEPENDS（build 依賴）vs RDEPENDS（runtime 依賴）：build 找不到 library 看 DEPENDS、runtime 缺看 RDEPENDS
- PREFERRED_VERSION/PROVIDER：多版本/provider 時明確選；compiler 確認 build 用的是你 patch 的 gcc 版本
- 其他坑：layer 版本不相容、patch 套不上、磁碟不足、找不到 patch 檔、語法錯——各有固定症狀和修法
- debug 方法論：失敗 task → log → 判斷歸屬（Yocto 坑 vs compiler patch）→ 排除法——compiler 工程師核心技能

## 自我檢核

- [ ] 知道 sstate-cache 的坑，會用 cleansstate
- [ ] 分得清 DEPENDS 和 RDEPENDS
- [ ] 知道 PREFERRED_VERSION，會確認 build 用的 gcc 版本
- [ ] 認得常見坑的症狀和修法
- [ ] 會用 debug 方法論定位問題歸屬（Yocto vs compiler）

## 延伸閱讀

### 官方

- **[Yocto Common Tasks / Debugging](https://docs.yoctoproject.org/dev-manual/debugging.html)** — Yocto Project
  - **讀哪裡**：debugging build failures、各種常見問題
  - **為什麼值得讀**：官方的 debug 指南

- **[BitBake Cache (sstate)](https://docs.yoctoproject.org/overview-manual/concepts.html#shared-state-cache)** — Yocto
  - **讀哪裡**：shared state cache 怎麼運作
  - **為什麼值得讀**：理解 sstate 的坑

### 社群

- **[Yocto mailing list / meta-riscv issues](https://github.com/riscv/meta-riscv/issues)**
  - **為什麼值得讀**：真實的問題和解法（搜尋你遇到的錯誤訊息）

下一章是最後一章——Yocto vs Buildroot：何時該選誰。理解兩個嵌入式 build 系統的取捨，讓你能在實際專案做出對的選擇。

→ [Ch 10 Yocto vs Buildroot：何時該選誰](./10-yocto-vs-buildroot.md)
