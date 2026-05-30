# Ch 19 — 符號管理與 ABI 追蹤

> **目標**：深入理解 shared library 的 SONAME、shlibs 與 symbols 兩種依賴資訊機制、`${shlibs:Depends}` 的完整運作鏈、ABI 與 API 的差別、以及如何用 symbols 檔案做精確的版本依賴追蹤。

> **環境**：dpkg-dev 1.21.x。這是原理深挖章，假設你已理解 Ch 7 的 `${shlibs:Depends}` 和 Ch 12 的 library 拆分。

## 為什麼 ABI 追蹤這麼重要？

練習 B 你看到 `greet` 自動依賴 `libgreet1`，靠的是 `${shlibs:Depends}`。但這背後的問題比「填個依賴」深得多：

**ABI 相容性問題**：假設 `libgreet` 升級，改了某個函式的參數。用舊版編譯的 `greet` 連結新版 `libgreet`，會傳錯參數——崩潰或更糟（資料損壞）。問題是：dpkg 怎麼知道「這個版本的 libgreet 還相容於用舊版編譯的程式」？

這就是 ABI（Application Binary Interface）追蹤要解決的。它讓依賴不只是「需要 libgreet1」，而是「需要 libgreet1 **的某個版本以上**」——精確到 ABI 相容的邊界。

## 先建立直覺：ABI vs API

```
API（Application Programming Interface）— 原始碼層級
  int greet(const char *name);   ← 函式的「長相」
  改 API = 改原始碼介面 = 需要重新編譯（source 不相容）

ABI（Application Binary Interface）— 二進位層級
  函式在 .so 裡的符號名、參數怎麼放進暫存器/堆疊、
  struct 的記憶體佈局、symbol 版本...
  改 ABI = 已編譯的程式無法正確呼叫 = 需要重新編譯（binary 不相容）

關鍵：API 相容不代表 ABI 相容
  在 struct 中間插一個欄位 → API 沒變（原始碼還能編）
  但 ABI 變了（已編譯的程式用舊的記憶體佈局，讀到錯位置）
```

ABI 追蹤的目標：讓 dpkg 知道「library 的這次改動有沒有破壞 ABI」，從而設定正確的依賴版本下限。

## SONAME：ABI 版本的標記

shared library 用 SONAME（Shared Object Name）標記它的 ABI 版本：

```bash
# 看一個 library 的 SONAME
objdump -p /usr/lib/x86_64-linux-gnu/libssl.so.3 | grep SONAME
#   SONAME    libssl.so.3
#                       ↑ ABI 版本號

# library 檔案的命名慣例
ls -l /usr/lib/x86_64-linux-gnu/libssl.so*
# libssl.so.3        → symlink，SONAME（runtime 用這個名字找）
# libssl.so.3.0.13   → 實體檔（完整版本）
# libssl.so          → symlink，dev 用（編譯時連結）
#         ↑ 無版本號，在 -dev 套件
```

```
三個層次的名字：
  libssl.so          ← linker name（dev 套件，編譯時 -lssl 找這個）
  libssl.so.3        ← SONAME（runtime 找這個，ABI 版本 = 3）
  libssl.so.3.0.13   ← real name（實體檔，完整版本）

SONAME 改變（3 → 4）= ABI 破壞 = 套件名也要改（libssl3 → libssl4）
```

**SONAME 的核心規則**：SONAME 的主版本號代表 ABI 版本。ABI 破壞時，upstream 遞增 SONAME（`.so.3` → `.so.4`），Debian 套件名跟著改（`libssl3` → `libssl4`）。這讓舊程式（連結 `.so.3`）和新程式（連結 `.so.4`）能共存——它們依賴不同的套件。

> 這就是為什麼 library 套件名含數字（`libgreet1`、`libssl3`）。數字 = SONAME 主版本 = ABI 版本。SONAME 不變的升級（ABI 相容），套件名不變；SONAME 變了（ABI 破壞），套件名換新數字。

## 兩種依賴資訊：shlibs vs symbols

dpkg 有兩套機制告訴 `dpkg-shlibdeps` 「連結這個 library 該依賴什麼版本」：

```
shlibs（粗粒度）：
  「依賴這個 library 的程式，至少要 libgreet1 (>= 1.0)」
  整個 library 一個版本下限，不管你用了哪些函式

symbols（細粒度）：
  「greet_make 函式從 1.0 開始有，greet_v2 從 2.0 開始有」
  記錄每個符號從哪個版本引入
  用了 greet_v2 的程式 → 依賴 >= 2.0
  只用 greet_make 的程式 → 依賴 >= 1.0
```

symbols 更精確——它根據程式**實際用了哪些符號**算出最小版本依賴，而非一刀切。

## shlibs：簡單但粗糙

`dh_makeshlibs` 預設生成 shlibs 資訊：

```bash
# build 後看生成的 shlibs（在 library 套件裡）
cat debian/libgreet1/DEBIAN/shlibs
# libgreet 1 libgreet1 (>= 1.0-1)
#  ────── ─ ───────── ──────────
#  名稱  SONAME  套件名   版本下限

# 意思：任何連結 libgreet.so.1 的程式，依賴 libgreet1 (>= 1.0-1)
```

`dpkg-shlibdeps`（在 `dh_shlibdeps`）掃描程式連結了哪些 `.so`，查對應的 shlibs，生成 `${shlibs:Depends}`。

shlibs 的缺點：版本下限是「整個 library 的版本」。即使你只用了一個從 1.0 就存在的老函式，shlibs 還是讓你依賴最新版——因為它不知道你用了哪些符號。

## symbols：精確的 ABI 追蹤

symbols 檔案記錄**每個符號從哪個版本引入**：

```bash
# 生成 symbols 檔案範本
cd greet-1.0/
dpkg-gensymbols -plibgreet1 -ODdebian/libgreet1.symbols
```

`debian/libgreet1.symbols`：

```
libgreet.so.1 libgreet1 #MINVER#
* Build-Depends-Package: libgreet-dev
 greet_make@Base 1.0
 greet_format@Base 1.0
 greet_v2@Base 2.0
 │          │    │
 符號名    版本節點  從哪個 upstream 版本引入
```

含義：
- `greet_make@Base 1.0`：`greet_make` 符號從 1.0 引入
- `greet_v2@Base 2.0`：`greet_v2` 從 2.0 引入

當 `dpkg-shlibdeps` 處理一個程式：
- 程式用了 `greet_make` → 需要 >= 1.0
- 程式用了 `greet_v2` → 需要 >= 2.0
- 取最大值作為依賴下限

```
程式 A 只用 greet_make → Depends: libgreet1 (>= 1.0)
程式 B 用了 greet_v2   → Depends: libgreet1 (>= 2.0)
        ↑ 精確！B 不會被允許裝在只有 1.x libgreet 的系統
```

## symbols 的維護工作流

每次 library 版本變動，要更新 symbols 檔：

```bash
# build 時 dpkg-gensymbols 會比對「實際符號」和「symbols 檔記錄」
dpkg-buildpackage -b
# 如果 library 新增了符號但 symbols 檔沒記錄：
# dpkg-gensymbols: warning: some symbols disappeared / new symbols appeared
#  --- debian/libgreet1.symbols
#  +++ dpkg-gensymbolsXXXX
#  +greet_v3@Base 2.1      ← 新符號！要加進 symbols 檔

# 更新 symbols 檔（加新符號，標記引入版本）
dpkg-gensymbols -plibgreet1 -Pdebian/libgreet1 -c4
# -c4 = 嚴格檢查，符號不一致就讓 build 失敗（強制你維護）
```

維護規則：
- **新增符號** → 加進 symbols 檔，標當前版本（如 `greet_v3@Base 2.1`）
- **移除符號** → ABI 破壞！要遞增 SONAME 和套件名（`libgreet1` → `libgreet2`）
- **符號消失警告** → dpkg-gensymbols 會抓到，逼你處理

> symbols 維護是 library 打包者的持續責任。每次 release 比對符號，新增的標版本、消失的代表 ABI 破壞要換 SONAME。`-c4`（嚴格模式）讓 build 在符號不一致時失敗，強制你不能忘記更新。

## C++ 的 symbols 惡夢

C++ 的 symbol 是 mangled（名稱含型別資訊）：

```bash
# C 的符號乾淨
greet_make@Base 1.0

# C++ 的符號（mangled，極長且編譯器相關）
_ZN6Greeter4makeERKSs@Base 1.0
# = Greeter::make(std::string const&) 的 mangled name
```

C++ 的 symbols 追蹤極其痛苦：
- mangled name 巨長、難讀
- inline 函式、template 實例化產生大量符號
- 不同 gcc 版本的 mangling 可能不同

> **認識論誠實**：C++ library 的 symbols 維護是公認的苦差事。很多 C++ library 套件選擇**不用 symbols，只用 shlibs**（粗粒度但好維護），接受依賴不夠精確的代價。或用 symbols 但配合 `c++filt`、pattern 等工具。這是真實的 trade-off，沒有完美解。C library 用 symbols（值得），C++ library 視情況。

## 故意弄壞：移除符號但不換 SONAME

```bash
# 假設你刪了 greet_format 函式，但 SONAME 還是 .so.1，套件還是 libgreet1
# build 時 dpkg-gensymbols 抓到：
dpkg-buildpackage -b
# dpkg-gensymbols: warning: symbol greet_format@Base disappeared
# （-c4 模式下會直接 build 失敗）

# 後果（如果硬上）：
# 用舊版編譯、呼叫 greet_format 的程式，連結新版 libgreet1.so.1
# → 找不到符號 → 程式啟動就 "symbol lookup error"
```

移除符號是 ABI 破壞。正確處理：遞增 SONAME（`.so.1` → `.so.2`），改套件名（`libgreet1` → `libgreet2`），讓新舊版本依賴不同套件而能共存。dpkg-gensymbols 的符號消失警告就是在保護你不要無聲破壞 ABI。

## 踩雷集錦

1. **混淆 API 和 ABI 相容**：在 struct 中間加欄位，API 還相容（原始碼能編），但 ABI 破壞（已編譯程式記憶體佈局錯）。ABI 比 API 脆弱得多

2. **移除符號不換 SONAME**：直接破壞所有用該符號的程式。移除符號 = ABI 破壞 = 必須遞增 SONAME 和套件名

3. **symbols 檔不維護**：library 新增符號但 symbols 沒更新，`${shlibs:Depends}` 算出的版本下限不準。用 `-c4` 強制維護

4. **C++ 硬上 symbols**：C++ 的 mangled symbol 維護成本極高。除非有把握，C++ library 用 shlibs 較務實

5. **SONAME 沒設**：library 編譯時忘了 `-Wl,-soname,libfoo.so.1`，dpkg-makeshlibs 算不出 SONAME，`${shlibs:Depends}` 整個鏈斷掉（練習 B 的 Makefile 特別設了 SONAME 就是為此）

6. **dev 套件的 .so symlink 指向錯誤**：`libfoo.so`（dev 用）應該 symlink 到 `.so.1`，讓編譯時 `-lfoo` 找得到。指錯了編譯連結失敗

## 進階：symbol versioning（更細的 ABI 控制）

除了 SONAME，ELF 還支援 **symbol versioning**——同一個 library 裡，同一個函式名能有多個版本：

```
# glibc 用這個機制：同一個 libc.so.6 裡
memcpy@GLIBC_2.2.5
memcpy@GLIBC_2.14    ← 同名函式的不同版本

# 用舊版編譯的程式綁定 @GLIBC_2.2.5
# 用新版編譯的綁定 @GLIBC_2.14
# 同一個 libc.so.6 同時服務兩者，不用換 SONAME！
```

這讓 glibc 能在**不換 SONAME**的情況下演進 ABI——舊程式綁舊符號版本，新程式綁新版本，同一個 `.so` 兼容兩者。這是為什麼 libc6 幾十年來 SONAME 一直是 `.so.6` 卻能持續更新。

symbols 檔案能記錄這些 versioned symbol（`memcpy@GLIBC_2.14 (...)`）。一般 library 用不到 symbol versioning（太複雜），但理解它能解釋為什麼某些核心 library（glibc）的 ABI 管理特別精細。

## 動手練習

1. 看真實 library 的三層名字：`ls -l /usr/lib/*/libssl.so*`，找出 linker name、SONAME symlink、real name。`objdump -p` 確認 SONAME

2. 對練習 B 的 libgreet1 生成 symbols 檔（`dpkg-gensymbols`），看它記錄了哪些符號。然後在 `lib/greet.c` 加一個新函式，重 build，看 dpkg-gensymbols 抓到新符號

3. 對比 shlibs 和 symbols：看 libgreet1 的 `DEBIAN/shlibs`（粗）和 `debian/libgreet1.symbols`（細）的差別

4. 看一個 C++ library 的 symbols（`apt source` 一個 C++ library，看它有沒有 symbols 檔，內容多醜）。或看它選擇只用 shlibs

## 本章重點整理

- ABI（二進位介面）比 API（原始碼介面）脆弱：API 相容不代表 ABI 相容
- SONAME 標記 ABI 版本；主版本號 = ABI 版本 = 套件名的數字（libgreet**1**）；ABI 破壞要遞增
- shlibs（粗粒度，整個 library 一個版本下限）vs symbols（細粒度，每個符號記引入版本）
- symbols 讓 `${shlibs:Depends}` 根據程式實際用的符號算精確版本下限
- 移除符號 = ABI 破壞 = 必須換 SONAME 和套件名；dpkg-gensymbols 的消失警告保護你
- C++ 的 mangled symbols 維護成本高，常退而用 shlibs

## 自我檢核

- [ ] 能用自己的話解釋 API 和 ABI 的差別，舉一個「API 相容但 ABI 破壞」的例子
- [ ] 知道 SONAME 是什麼、為什麼 library 套件名含數字、ABI 破壞時怎麼處理
- [ ] 能說出 shlibs 和 symbols 的差別（粗 vs 細）和各自的 trade-off
- [ ] 知道 symbols 檔如何讓依賴版本下限更精確
- [ ] 知道為什麼移除符號要換 SONAME，不換會發生什麼

## 延伸閱讀

### 官方文件

- **[dpkg-gensymbols(1) man page](https://manpages.debian.org/bookworm/dpkg-dev/dpkg-gensymbols.1.html)**
  - **讀哪裡**:「USING SYMBOLS FILES」和符號標記語法整節
  - **學什麼**：symbols 檔案的完整格式、versioned symbol、pattern 語法；本章是教學版
  - **前提**：讀完本章

- **[Debian Policy §8 (Shared libraries)](https://www.debian.org/doc/debian-policy/ch-sharedlibs.html)**
  - **讀哪裡**：整章，SONAME、shlibs、symbols 的 Policy 規則
  - **學什麼**：shared library 打包的權威規範
  - **前提**：本章

### 部落格 / 文章

- **[Debian Library Packaging Guide](https://www.netfort.gr.jp/~dancer/column/libpkg-guide/libpkg-guide.html)** — Junichi Uekawa
  - **這篇說什麼**：library 套件的完整打包，含 SONAME、symbols、ABI 管理的實戰
  - **讀哪裡**：SONAME 和 symbols 章節
  - **為什麼值得讀**：把本章的 ABI 理論和 Ch 26 的 library 打包實務連起來

- **[How To Write Shared Libraries](https://www.akkadia.org/drepper/dsohowto.pdf)** — Ulrich Drepper（glibc 前維護者）
  - **這篇說什麼**：shared library 的底層機制（symbol versioning、ELF、動態連結），權威到不能再權威
  - **讀哪裡**：symbol versioning 那節（其餘很深，選讀）
  - **為什麼值得讀**：作者是 glibc 核心開發者；想真正理解 ABI/symbol versioning 的底層，這是聖經（但很硬）

→ [練習 C：零 warning 的套件建置](./practice-c-zero-warning-build.md)
