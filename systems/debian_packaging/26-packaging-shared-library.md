# Ch 26 — 打包 shared library

> **目標**：整合並深化 library 打包——套件命名慣例（為什麼是 `libfoo1` 不是 `libfoo`）、runtime/dev/dbgsym 的完整拆分、SONAME 變動時的套件改名與 transition、以及 library 打包的完整 checklist。

> **環境**：debhelper 13。本章承接 Ch 7（control）、Ch 12（install）、Ch 19（symbols/ABI），把 library 打包講完整。

## 為什麼 library 打包是門獨立學問？

執行檔打包相對簡單——一個 binary 進一個套件。但 shared library 牽涉 ABI 相容、多套件拆分、版本化命名、symbol 追蹤、transition 管理——它是打包裡最需要紀律的部分。打包錯誤的 library 會導致整個依賴它的生態崩潰（用舊版編譯的程式連結新版崩潰）。

前面章節已鋪墊了 SONAME（Ch 19）、`${shlibs:Depends}`（Ch 7）、install 拆分（Ch 12）。這章把它們組裝成完整的 library 打包知識，並補上沒講透的部分。

## 先建立直覺：一個 library 拆成幾個套件

```
upstream libfoo 專案
        │
   debhelper 拆成：
        │
┌───────────────┬──────────────────┬─────────────────┐
│  libfoo1      │  libfoo-dev      │  libfoo1-dbgsym │
│  (runtime)    │  (development)   │  (debug symbols)│
├───────────────┼──────────────────┼─────────────────┤
│ libfoo.so.1   │ libfoo.so        │ .debug 檔案      │
│ libfoo.so.1.* │ (symlink→.so.1)  │ (給 gdb 用)      │
│               │ foo.h (headers)  │                 │
│               │ libfoo.a (靜態)  │ 自動生成        │
│               │ pkgconfig/*.pc   │                 │
├───────────────┼──────────────────┼─────────────────┤
│ 跑程式的人裝   │ 開發的人才裝     │ debug 時才裝     │
└───────────────┴──────────────────┴─────────────────┘
```

三套件拆分的邏輯：使用者只裝需要的。跑程式 → `libfoo1`；開發 → `libfoo-dev`（會自動拉 `libfoo1`）；debug → `libfoo1-dbgsym`。

## 套件命名慣例：為什麼是 libfoo1

```
runtime 套件名 = lib + upstream名 + SONAME主版本號
  libfoo.so.1  → 套件名 libfoo1
  libssl.so.3  → 套件名 libssl3
  libpng16.so.16 → 套件名 libpng16-16（名字含 16 兩次，歷史原因）

dev 套件名 = lib + upstream名 + -dev（無版本號）
  libfoo-dev

為什麼 runtime 含版本號而 dev 不含？
```

關鍵設計：**runtime 套件名含 SONAME 版本，dev 不含**。原因：

```
runtime 含版本 → ABI 不同版本能共存：
  libfoo1（SONAME 1）和 libfoo2（SONAME 2）是「不同套件」
  → 用舊版編譯的程式裝 libfoo1，新版裝 libfoo2，同時存在不衝突

dev 不含版本 → 同時只開發一個版本：
  libfoo-dev 永遠指向「當前要開發的版本」
  你不會同時用兩個 ABI 版本的 header 開發
```

> 這個命名慣例直接服務 ABI 共存（Ch 19）。runtime 名字裡的數字就是 SONAME 主版本。當 upstream 把 SONAME 從 1 升到 2（ABI 破壞），你的 runtime 套件名從 `libfoo1` 改成 `libfoo2`——舊套件還能裝（給舊程式），新套件並存（給新程式）。

## dbgsym：自動的 debug symbol 套件

`dh_strip`（在 dh sequence 裡，Ch 12）做兩件事：
1. 從 binary/library strip 掉 debug symbols（讓套件變小）
2. 把 strip 出來的 symbols **自動**打包成 `-dbgsym` 套件

```bash
# build 後自動生成 dbgsym（不用你寫任何東西）
dpkg-buildpackage -b
ls ../*.deb
# libfoo1_1.0-1_amd64.deb
# libfoo1-dbgsym_1.0-1_amd64.deb   ← dh_strip 自動生成！
# libfoo-dev_1.0-1_amd64.deb
```

dbgsym 套件讓使用者在需要 debug 時（用 gdb 追 library 的 crash）能裝上 debug symbols，平時不佔空間。

> 舊式手動 `-dbg` 套件（如 `libfoo1-dbg`）已被自動 `-dbgsym` 取代。你不再需要手寫 dbg 套件——`dh_strip` 自動處理。dbgsym 通常上傳到專門的 debian-debug archive，不和主套件混在一起。

## dev 套件該放什麼

`libfoo-dev` 是開發者編譯程式時需要的所有東西：

```
debian/libfoo-dev.install:
  usr/include/*                    ← headers（編譯時 #include）
  usr/lib/*/libfoo.so              ← linker symlink（編譯時 -lfoo 找它）
  usr/lib/*/libfoo.a               ← 靜態庫（如果提供靜態連結）
  usr/lib/*/pkgconfig/foo.pc       ← pkg-config metadata
  usr/share/man/man3/*             ← API 的 man page（如果有）
```

| dev 檔案 | 作用 |
|---|---|
| headers (`*.h`) | 編譯時 `#include <foo.h>` |
| `libfoo.so`（無版本）| linker name，`gcc -lfoo` 找它（它 symlink 到 runtime 的 `.so.1`）|
| `libfoo.a` | 靜態庫（靜態連結用，非必須）|
| `foo.pc` | pkg-config 資訊（`pkg-config --cflags --libs foo`）|

`libfoo.so`（dev 的 symlink）指向 `libfoo.so.1`（runtime 的實體）——這就是為什麼裝 `-dev` 必須也裝 runtime（`Depends: libfoo1 (= ${binary:Version})`）。

## .symbols 或 .shlibs：給 dev 套件

library 套件要提供 `${shlibs:Depends}` 的資料來源（Ch 19）。放在 source 的 `debian/` 下：

```
debian/libfoo1.symbols   ← 精確的 symbol 追蹤（推薦給 C library）
debian/libfoo1.shlibs    ← 粗粒度版本（或讓 dh_makeshlibs 自動生成）
```

`dh_makeshlibs` 自動生成 shlibs；symbols 要你維護（Ch 19 的工作流）。C library 值得用 symbols；C++ 可能退而用 shlibs。

## SONAME 變動：套件改名與 transition

當 upstream 出新版且 **SONAME 改變**（ABI 破壞），這是 library 維護最重大的事件：

```
upstream libfoo 2.0 把 SONAME 從 libfoo.so.1 改成 libfoo.so.2
        │
  你的打包要：
  1. runtime 套件改名：libfoo1 → libfoo2
     （control 裡 Package: libfoo1 改成 libfoo2）
  2. dev 套件名不變（libfoo-dev），但現在裝 libfoo2
  3. 新建 libfoo2 的 symbols 檔
        │
  後果：所有「Depends: libfoo1」的套件需要重新 build
        │  改成依賴 libfoo2（透過重新 build，shlibdeps 自動更新）
        ▼
  這叫 library transition（Ch 33 詳談）
```

> SONAME 變動觸發 **transition**：因為依賴舊 library 的所有套件都要重新 build 連結新 library。在 Debian archive，這由 release team 協調（一個大 library 的 transition 可能影響數百個套件）。在你的私有 repo，你要記得重 build 所有下游。這是 library 打包最有「全域影響」的操作。

## 完整的 library 打包 checklist

```
control:
  □ runtime 套件名含 SONAME 版本（libfoo1）
  □ dev 套件名無版本（libfoo-dev）
  □ dev Depends: libfoo1 (= ${binary:Version})  ← 精確綁定
  □ runtime Depends: ${shlibs:Depends}, ${misc:Depends}
  □ runtime + dev 標 Multi-Arch: same（如果檔案都在 multiarch 路徑）

install 拆分:
  □ libfoo1.install: usr/lib/*/libfoo.so.*（帶版本號）
  □ libfoo-dev.install: headers + libfoo.so（無版本）+ .a + pkgconfig

ABI 追蹤:
  □ debian/libfoo1.symbols（C library）或讓 dh_makeshlibs 生成 shlibs
  □ dh_makeshlibs -- -c4（嚴格檢查符號一致）

build:
  □ library 編譯時設正確 SONAME（-Wl,-soname,libfoo.so.1）
  □ library 裝到 multiarch 路徑（/usr/lib/<triplet>/）
  □ dbgsym 自動生成（dh_strip，不用手動）

品質:
  □ lintian 無 shared-lib 相關 warning
  □ autopkgtest 測 dev 套件可編譯（Ch 17）
```

## 故意弄壞：dev 套件依賴範圍太寬

```bash
# 錯誤：dev 用 >= 而非 =
# debian/control:
# Package: libfoo-dev
# Depends: libfoo1 (>= 1.0), ...    ← 錯！

# 後果情境：
# 系統裝了 libfoo1 = 1.5（較新的 ABI 相容版本）
# 你裝 libfoo-dev = 1.0
# dev 的 header 是 1.0 的，runtime 是 1.5 的
# → header 宣告的某個 struct 和 1.5 的實際 layout 可能不同
# → 編譯出的程式行為詭異（header/runtime 不匹配）

# 正確：精確綁定
# Depends: libfoo1 (= ${binary:Version})
```

`-dev` 的 header 必須對應**精確**的 runtime 版本。`(= ${binary:Version})` 確保裝 dev 時 runtime 是完全對應的版本。用 `>=` 會允許 header 和 runtime 版本不匹配，導致難以察覺的 bug。

## 踩雷集錦

1. **runtime 套件名沒含 SONAME 版本**：命名成 `libfoo` 而非 `libfoo1`，導致 SONAME 變動時無法共存（新舊套件同名衝突）。runtime 名字必須含版本

2. **dev 用 `>=` 而非 `=` 綁定 runtime**：header/runtime 版本不匹配。dev 必須 `(= ${binary:Version})`

3. **install glob 不分 runtime/dev**：`libfoo.so*` 把 dev 的 symlink 和 runtime 的實體混在一起（Ch 12 的雷）。runtime `.so.*`，dev `.so`

4. **SONAME 變了不改套件名**：直接破壞所有依賴舊 library 的程式。SONAME 變 = 套件改名 = transition

5. **手寫 `-dbg` 套件**：已過時，`dh_strip` 自動生成 `-dbgsym`。別手寫 dbg

6. **library 沒裝 multiarch 路徑**：放 `/usr/lib/` 而非 `/usr/lib/<triplet>/`，破壞 Multi-Arch（Ch 18）。現代 library 必須在 multiarch 路徑

## 進階：靜態庫、LTO 與 versioned symbols

幾個 library 打包的進階考量：

**靜態庫（`.a`）**：放 dev 套件。但現代趨勢是減少靜態連結（靜態連結的程式無法受益於 library 的安全更新）。提供 `.a` 是選項，不是必須。

**LTO（Link-Time Optimization）**：開了 LTO 的 `.a` 含中間表示而非機器碼，跨編譯器版本不相容。打包 LTO 的靜態庫要小心（通常 `-ffat-lto-objects` 同時含機器碼）。

**versioned symbols（Ch 19 進階）**：像 glibc 那樣在不換 SONAME 下演進 ABI。一般 library 用不到，但如果 upstream 用了 symbol versioning，你的 symbols 檔要正確記錄 versioned symbol（`foo@VERS_1.0`）。

```bash
# 檢查 library 用了哪些進階特性
objdump -T libfoo.so.1 | head    # 看 dynamic symbols（含 version）
readelf -d libfoo.so.1           # 看 dynamic section（SONAME、依賴）
nm -D libfoo.so.1                # 看 dynamic symbols
```

這些工具（`objdump`、`readelf`、`nm`）是 debug library 打包問題的利器——當 `${shlibs:Depends}` 算不對、symbols 對不上，用它們直接看 library 的真實內容。

## 動手練習

1. 把練習 B 的 libgreet 完善成完整 library 打包：加 dbgsym（確認 `dh_strip` 自動生成）、加 `.pc` 檔（pkg-config）、確認 dev 用 `(= ${binary:Version})`

2. 模擬 SONAME transition：把 libgreet 的 SONAME 從 `.so.1` 改成 `.so.2`，套件改名 `libgreet1` → `libgreet2`，重 build，觀察 `greet`（依賴它的）需要重新 build 才會依賴 libgreet2

3. 用 `readelf -d`、`objdump -T`、`nm -D` 檢視一個真實 library（如 `/usr/lib/*/libssl.so.3`）的 SONAME、依賴、dynamic symbols

4. 對比 runtime 和 dbgsym 套件大小：`ls -la ../libgreet1_*.deb ../libgreet1-dbgsym_*.deb`，理解 strip 省了多少空間

## 本章重點整理

- library 拆三套件：runtime（`libfoo1`，含 SONAME 版本）/ dev（`libfoo-dev`，無版本）/ dbgsym（自動）
- runtime 名含版本讓 ABI 不同版本共存；dev 不含版本（同時只開發一個版本）
- dev 必須 `Depends: libfoo1 (= ${binary:Version})`——header/runtime 精確對應
- dbgsym 由 `dh_strip` 自動生成（別手寫 `-dbg`）；library 裝 multiarch 路徑
- SONAME 變動 = ABI 破壞 = 套件改名（libfoo1→libfoo2）+ transition（下游全部重 build）

## 自我檢核

- [ ] 能解釋為什麼 runtime 套件名含版本（libfoo1）而 dev 不含
- [ ] 知道 dev 套件該放哪些檔案（headers/`.so`/`.a`/`.pc`）
- [ ] 能說出為什麼 dev 要 `(= ${binary:Version})` 而非 `>=`
- [ ] 知道 dbgsym 怎麼來的（dh_strip 自動），取代了什麼（手寫 -dbg）
- [ ] 能描述 SONAME 變動時的完整處理（改名 + transition）

## 延伸閱讀

### 官方文件

- **[Debian Policy §8 (Shared libraries)](https://www.debian.org/doc/debian-policy/ch-sharedlibs.html)**
  - **讀哪裡**：整章，library 套件的所有規則
  - **學什麼**：命名、SONAME、shlibs/symbols、dev 套件的權威規範
  - **前提**：讀完本章和 Ch 19

- **[Debian Library Packaging Guide](https://www.netfort.gr.jp/~dancer/column/libpkg-guide/libpkg-guide.html)** — Junichi Uekawa
  - **讀哪裡**：整份（它就是專講 library 打包）
  - **學什麼**：library 打包的完整實戰，本章和 Ch 19 的綜合對照
  - **前提**：無

### 部落格 / 文章

- **[Debian transitions explained](https://wiki.debian.org/Teams/ReleaseTeam/Transitions)** — Debian Release Team
  - **這篇說什麼**：SONAME 變動如何觸發 transition、release team 如何協調
  - **讀哪裡**：what is a transition 那節
  - **為什麼值得讀**：理解 library 打包的「全域影響」，Ch 33 會深入

→ [Ch 27 打包 Python 套件](./27-packaging-python.md)
