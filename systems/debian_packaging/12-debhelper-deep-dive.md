# Ch 12 — debhelper 深入

> **目標**：深入 debhelper 的核心 `dh_*` 工具——它們各自做什麼、`debian/*.install` 等 helper 檔案如何控制檔案分配、compat level 的演進與意義、以及如何 debug 「檔案沒被裝進對的套件」這類常見問題。

> **環境**：debhelper 13（compat 13）。本章假設你已理解 Ch 8 的 dh sequencer。

## 為什麼要深入個別 dh_*？

Ch 8 講了 `dh $@` 自動跑一串 `dh_*`。平常你不用管細節。但當出問題時——檔案裝錯套件、man page 沒被壓縮、權限不對、debug symbols 沒分離——你必須知道是哪個 `dh_*` 負責，以及怎麼控制它。

每個 `dh_*` 讀特定的 `debian/` helper 檔案（如 `debian/foo.install`）來決定行為。理解這個「工具 + helper 檔案」的對應，你就能精確控制打包的每個面向。

## 先建立直覺：dh_* 從暫存目錄分配檔案

```
build 流程的關鍵中轉站：

  make install DESTDIR=debian/tmp     ← dh_auto_install 把所有東西
                                          裝進一個暫存目錄
            │
            ▼
  debian/tmp/                          ← upstream 裝出來的所有檔案
    usr/bin/foo
    usr/lib/libfoo.so.1
    usr/include/foo.h
    usr/share/man/man1/foo.1
            │
            │ dh_install 根據 *.install 把檔案
            │ 分配到各個 binary package 的目錄
            ▼
  debian/foo/usr/bin/foo              ← foo 套件
  debian/libfoo1/usr/lib/libfoo.so.1 ← libfoo1 套件
  debian/libfoo-dev/usr/include/foo.h ← libfoo-dev 套件
            │
            ▼
  各自打包成 .deb
```

核心概念：upstream 的 `make install` 把所有東西倒進 `debian/tmp/`（或單一套件時直接 `debian/<pkg>/`），然後 `dh_install` 根據 `debian/<pkg>.install` 檔案把它們**分配**到各個 binary package。

## 關鍵的 dh_* 工具

### dh_auto_*：呼叫 upstream build 系統

| 工具 | 做什麼 |
|---|---|
| `dh_auto_configure` | 偵測並執行 configure（./configure / cmake / meson...）|
| `dh_auto_build` | 編譯（make / ninja...）|
| `dh_auto_test` | 跑 upstream 測試 |
| `dh_auto_install` | `make install DESTDIR=debian/tmp`（或 `debian/<pkg>`）|

### dh_install*：分配與安裝檔案

| 工具 | helper 檔案 | 做什麼 |
|---|---|---|
| `dh_install` | `debian/<pkg>.install` | 把 debian/tmp 的檔案分配到各套件 |
| `dh_installdocs` | `debian/<pkg>.docs` | 裝文件到 /usr/share/doc |
| `dh_installman` | `debian/<pkg>.manpages` | 裝 man page 並壓縮 |
| `dh_installchangelogs` | — | 裝 changelog（含 upstream 的）|
| `dh_installsystemd` | `debian/<pkg>.service` | 裝並啟用 systemd unit（Ch 29）|
| `dh_installexamples` | `debian/<pkg>.examples` | 裝範例到 /usr/share/doc/<pkg>/examples |

### dh_* 後處理：權限、壓縮、strip

| 工具 | 做什麼 |
|---|---|
| `dh_fixperms` | 修正所有檔案權限到 Policy 標準 |
| `dh_compress` | 壓縮 man page、changelog 等（gzip）|
| `dh_strip` | 從 binary 分離 debug symbols 到 -dbgsym 套件 |
| `dh_makeshlibs` | 為 shared library 生成 shlibs 檔（Ch 19）|
| `dh_shlibdeps` | 計算 `${shlibs:Depends}`（Ch 7）|

### dh_* 收尾：生成 metadata 與打包

| 工具 | 做什麼 |
|---|---|
| `dh_gencontrol` | 生成最終的 control（替換 ${...} 變數）|
| `dh_md5sums` | 生成 md5sums |
| `dh_builddeb` | 組裝成 .deb |

## *.install 檔案：控制檔案分配

這是最常打交道的 helper 檔案。它告訴 `dh_install` 哪些檔案進哪個套件。

```
# debian/foo.install — foo 套件要哪些檔案
usr/bin/foo
usr/share/foo/

# debian/libfoo1.install — runtime library
usr/lib/*/libfoo.so.*

# debian/libfoo-dev.install — 開發檔
usr/include/*
usr/lib/*/libfoo.so       ← 注意：.so（無版本號）是 dev 用的 symlink
usr/lib/*/libfoo.a        ← 靜態庫
usr/lib/*/pkgconfig/*
```

語法：
- 每行一個來源路徑（相對於 `debian/tmp/`），可用 glob（`*`）
- 可選的目標路徑：`source/path target/dir`（把 source 裝到 target）
- 路徑不寫前導 `/`

```
# 帶目標路徑的例子：把 upstream 的某檔案改放位置
build/output/foo.conf  etc/foo/
# 把 build/output/foo.conf 裝到 /etc/foo/
```

> library 套件拆分的關鍵在 `.install` 的 glob：runtime 套件用 `libfoo.so.*`（帶版本號的實體檔），dev 套件用 `libfoo.so`（無版本號的 symlink）+ headers + `.a`。這個區分讓使用者只裝 runtime，開發者才裝 dev。Ch 26 詳談。

## compat level：debhelper 的行為版本

debhelper 的預設行為隨版本演進。`compat level` 凍結你的套件用哪個版本的行為，確保 debhelper 升級不會無聲改變你的 build。

現代宣告方式（在 `debian/control` 的 Build-Depends）：

```
Build-Depends: debhelper-compat (= 13)
```

`debhelper-compat (= 13)` 同時做兩件事：宣告 build 依賴 debhelper，且設定 compat level 為 13。這取代了舊的 `debian/compat` 檔案。

compat level 演進的重要變化：

| Level | 引入的主要變化 |
|---|---|
| 9 | multiarch 路徑支援（`usr/lib/*/`）|
| 10 | 預設啟用 autoreconf、parallel build |
| 11 | `dh_systemd_*` 整合進 `dh_installsystemd` |
| 12 | `dh_missing --fail-missing` 預設（沒裝的檔案會報錯）|
| 13 | `dh_auto_install` 裝到 `debian/<pkg>` 而非 `debian/tmp`（單套件時）；更多現代預設 |

> 用最新的 compat level（13）。舊套件可能還是低 level，但新套件沒理由用舊的。`debian/compat` 檔案（寫一個數字）是舊方式，已被 `debhelper-compat (= N)` 取代——別再用 `debian/compat`。

## dh_missing：抓出沒裝到的檔案

compat 12+ 預設 `dh_missing --fail-missing`：如果 upstream 裝出來的檔案沒有任何 `.install` 檔案認領，build **失敗**。這是好事——它逼你明確處理每個檔案。

```bash
dpkg-buildpackage -b
# ...
# dh_missing: warning: usr/lib/foo/plugin.so exists in debian/tmp but is not
#   installed to anywhere
# dh_missing: error: missing files, aborting
```

這表示 upstream 裝了 `plugin.so` 但你的 `.install` 沒提到它。你要嘛把它加進某個套件的 `.install`，要嘛明確說「不要它」（`debian/not-installed`）。

```
# debian/not-installed — 明確聲明這些檔案故意不打包
usr/lib/foo/plugin.so
usr/share/foo/redundant-data
```

> `dh_missing` 是品質保證的重要一環。它確保你**有意識地**處理 upstream 的每個輸出，而非無聲丟掉檔案。新手覺得它煩，老手知道它救過很多「咦那個檔案怎麼不見了」的事故。

## 單套件 vs 多套件的檔案分配

```
單一 binary package（最簡單）：
  不需要 .install 檔案！
  dh_auto_install 直接裝進 debian/<pkg>/，全部進那個套件

多個 binary package：
  需要 .install 檔案決定每個檔案進哪個套件
  dh_auto_install 裝進 debian/tmp/
  dh_install 根據 *.install 分配
```

所以打包單一執行檔的小工具，你可能完全不需要 `.install`——dh 自動把所有東西放進那個唯一的套件。一旦要拆成多個套件（library + dev + tool），才需要 `.install` 指揮分配。

## 故意弄壞：檔案裝錯套件

```bash
# 假設 libfoo-dev.install 寫成這樣（錯把 runtime .so 也放進 dev）
cat debian/libfoo-dev.install
# usr/include/*
# usr/lib/*/libfoo.so*      ← 錯！* 把 libfoo.so.1 也抓進來了

dpkg-buildpackage -b
# build 可能成功，但...
dpkg-deb -c ../libfoo-dev_*.deb | grep libfoo.so
#   usr/lib/x86_64-linux-gnu/libfoo.so      ← 對，dev 要的 symlink
#   usr/lib/x86_64-linux-gnu/libfoo.so.1    ← 錯！runtime 的實體檔跑來 dev 了
dpkg-deb -c ../libfoo1_*.deb | grep libfoo.so
#   （空的！runtime 套件沒有 .so，被 dev 搶走了）

# 後果：使用者裝 libfoo1 跑程式，但 .so.1 不在 libfoo1 裡 → 程式找不到 library
```

修正：dev 用 `libfoo.so`（精確，無版本號），runtime 用 `libfoo.so.*`：

```
# libfoo-dev.install
usr/include/*
usr/lib/*/libfoo.so          ← 只要無版本號的 symlink

# libfoo1.install
usr/lib/*/libfoo.so.*        ← 帶版本號的實體檔和 symlink
```

> glob 的精確度很重要。`libfoo.so*`（含實體檔）vs `libfoo.so`（只 symlink）差一個字元，套件就壞了。Ch 26 會把 library 拆分講透。

## 進階：override 與 helper 檔案的選擇

很多事情有兩種做法：寫 helper 檔案（如 `debian/foo.install`）或在 rules 裡 override。原則：

```
能用 helper 檔案就用 helper 檔案（宣告式，清楚）：
  debian/foo.install         ← 檔案分配
  debian/foo.docs            ← 文件
  debian/foo.dirs            ← 建立空目錄

需要邏輯/條件時才用 override（命令式）：
  override_dh_install:
      dh_install
      # 額外的條件處理
```

helper 檔案是宣告式的，一眼看懂「哪些檔案進哪」。override 是命令式的，適合需要 shell 邏輯的情況。優先用 helper 檔案，複雜了才 override。

debhelper 13 還有 `execute_after_dh_X` / `execute_before_dh_X`（Ch 8），比 override 更輕量——只加動作不取代原工具。

## 動手練習

1. 找一個多套件的 source（`apt source` 一個 library，如 `libpng`），看它的 `debian/*.install` 檔案，理解 runtime/dev/各套件怎麼分配檔案

2. 用 `dh_install --no-act` 或讀某套件 build log，追蹤 `dh_auto_install` 裝到哪、`dh_install` 怎麼分配。看 `debian/tmp/` 和 `debian/<pkg>/` 的差別

3. 故意製造 `dh_missing` 錯誤：在一個套件的 `.install` 刪掉某個檔案的行，build 看 `dh_missing` 報錯，再用 `debian/not-installed` 或補回 `.install` 修復

4. 玩 library 拆分的雷：故意把 `libfoo.so*`（含版本號實體檔）寫進 dev 套件，build 後用 `dpkg-deb -c` 檢查兩個套件的內容，確認 runtime 套件「空了」

## 本章重點整理

- `dh_auto_install` 把 upstream 輸出裝進暫存目錄，`dh_install` 根據 `*.install` 分配到各 binary package
- 每個 `dh_*` 讀對應的 helper 檔案（`*.install` / `*.docs` / `*.manpages`...）控制行為
- compat level 用 `debhelper-compat (= 13)` 宣告，凍結 debhelper 行為版本（別用舊的 `debian/compat`）
- `dh_missing --fail-missing`（compat 12+）抓出沒被認領的檔案，用 `debian/not-installed` 明確排除
- 單套件不需要 `.install`；多套件才需要分配；library 拆分靠 glob 精確度（`.so` vs `.so.*`）

## 自我檢核

- [ ] 能畫出檔案從 `make install` 到各個 `.deb` 的流動（debian/tmp → dh_install → debian/<pkg>）
- [ ] 知道 `debian/foo.install` 的語法，以及 library runtime vs dev 的 glob 差別
- [ ] 能解釋 compat level 是什麼，現代怎麼宣告（debhelper-compat）
- [ ] 知道 `dh_missing` 報錯時兩種處理方式（加進 .install 或 not-installed）
- [ ] 知道什麼時候用 helper 檔案、什麼時候用 override

## 延伸閱讀

### 官方文件

- **[debhelper(7) man page](https://manpages.debian.org/bookworm/debhelper/debhelper.7.html)**
  - **讀哪裡**:「COMPATIBILITY LEVELS」整節（每個 level 的變化）和 helper 檔案列表
  - **學什麼**：compat level 演進的完整歷史、所有 helper 檔案的索引
  - **前提**：讀完本章

- **[dh_install(1) man page](https://manpages.debian.org/bookworm/debhelper/dh_install.1.html)**
  - **讀哪裡**:「FILES」（.install 語法）和範例
  - **學什麼**：`.install` 檔案的完整語法，含目標路徑、glob 規則
  - **前提**：無

### 部落格 / 文章

- **[Debian New Maintainers' Guide §5 (other files in debian/)](https://www.debian.org/doc/manuals/maint-guide/dother.en.html)**
  - **這篇說什麼**：逐一介紹 `debian/` 下各種 helper 檔案（install/docs/dirs/...）的用途
  - **讀哪裡**：§5 整節
  - **為什麼值得讀**：把所有 helper 檔案串起來，是本章的官方對照

→ [Ch 13 Build profiles 與條件建置](./13-build-profiles.md)
