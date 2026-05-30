# 練習 C — 零 warning 的套件建置

> **目標**：整合 Ch 14–19 的所有品質工具（sbuild clean build + lintian + autopkgtest + symbols），把練習 B 的 `greet` 專案打磨成一個**零 lintian warning、通過 sbuild 乾淨建置、有完整 autopkgtest、有 symbols ABI 追蹤**的生產級套件。

## 背景與動機

練習 B 你做出了能用的多套件打包，但它有一堆 lintian warning、沒有測試、沒有 symbols 追蹤、只在 host build 過。那是「能用」的等級。

這個練習要把它提升到「**能上傳 Debian archive**」的等級。差別不是功能，是品質的每個細節：乾淨環境驗證、零 warning、自動化測試、ABI 追蹤。這是區分業餘和專業打包的分水嶺。

## 任務規格

從練習 B 的 `greet` 專案（libgreet1 + libgreet-dev + greet）開始，達成以下全部：

| 目標 | 驗收方式 |
|---|---|
| sbuild 乾淨建置通過 | `sbuild -d bookworm greet_*.dsc` 成功 |
| 零 lintian warning（含 info）| `lintian -iI greet_*.changes` 無 E/W/I（pedantic 可有） |
| 完整 man page | `greet.1` 存在且 `dh_installman` 安裝 |
| symbols ABI 追蹤 | `debian/libgreet1.symbols` 存在且 build 時 `-c4` 通過 |
| autopkgtest 通過 | `autopkgtest greet_*.changes -- null`（或 lxc）全綠 |
| 完整 copyright | DEP-5 格式，licensecheck 涵蓋所有檔案 |
| hardening 全開 | lintian 無 `hardening-*` warning |

**禁止**：用 override 壓掉 lintian warning（除非是真正的誤報並附理由）；目標是**真的修好**，不是掩蓋。

## 期望輸出範例

```
$ sbuild -d bookworm greet_1.0-1.dsc
...
| Summary                                                       |
+--------------------------------------------------------------+
| Build Architecture: amd64                                    |
| Status: successful                                           |
| Lintian: pass                                                |  ← 關鍵
| Autopkgtest: pass                                            |  ← 關鍵
+--------------------------------------------------------------+

$ lintian -iI greet_1.0-1_amd64.changes
$ echo $?
0    ← 完全乾淨，無任何輸出
```

## 如果你卡住了

1. 先跑 `lintian -iI` 看完整清單，用 `-i` 的詳細說明逐一理解修法（不要猜）
2. `binary-without-manpage` → 寫 `debian/greet.1`（man page，groff 格式）+ `debian/greet.manpages` 指向它
3. `hardening-no-*` → 確認用 `dh_auto_build`（自動套 dpkg-buildflags），別在 Makefile 寫死 CFLAGS 覆蓋掉
4. symbols 的 `-c4` 失敗 → dpkg-gensymbols 告訴你少了哪些符號，加進 `.symbols`
5. autopkgtest 失敗 → 確認測試用系統路徑（`greet`）不是 `./greet`
6. `description-*` warning → 改寫 Description，別用冠詞開頭、別句點結尾

## 實作步驟建議

### Step 1：建立 baseline，列出所有問題

```bash
cd greet-1.0/
dpkg-buildpackage -us -uc -b
cd ..
lintian -iI --pedantic greet_1.0-1_amd64.changes > /tmp/lintian-before.txt
wc -l /tmp/lintian-before.txt   # 記下起始有多少問題
```

### Step 2：逐一修 lintian（man page、hardening、description...）

### Step 3：加 symbols ABI 追蹤

### Step 4：寫 autopkgtest

### Step 5：sbuild 乾淨建置 + 整合驗證

## 完整參考解答

**寫完再看！**

<details>
<summary>Step 2：修 lintian 問題</summary>

**man page** — `debian/greet.1`（groff 格式）：
```groff
.TH GREET 1 "2025-05-29" "greet 1.0" "User Commands"
.SH NAME
greet \- print a friendly greeting
.SH SYNOPSIS
.B greet
.RI [ NAME ]
.SH DESCRIPTION
.B greet
prints a greeting message for the given
.IR NAME .
If no name is given, it greets "World".
.SH EXAMPLES
.TP
.B greet Alice
Prints "Hello, Alice!".
.SH AUTHOR
Your Name <you@example.com>
```

`debian/greet.manpages`：
```
debian/greet.1
```

**Description 修正** — `debian/control`（避免冠詞開頭、句點結尾）：
```
# 改前：Description: A command-line greeting tool   ← W: starts-with-article
# 改後：
Description: command-line greeting tool
 greet prints a localized greeting message, built on top of libgreet.
 .
 This is the command-line front-end.
```

**hardening** — 確認 Makefile 不覆蓋 dpkg-buildflags：
```makefile
# 練習 B 的 Makefile 用 CFLAGS ?= -O2 -g -Wall
# ?= 是「未設定時才用」，所以 dh_auto_build 傳入的 CFLAGS 會生效（好）
# 但要確認 LDFLAGS 也用 ?= 且傳給連結步驟
# 如果 lintian 報 hardening-no-bindnow 等，檢查 LDFLAGS 是否被傳遞
```

rules 確保 buildflags 生效（autotools 通常自動，手寫 Makefile 要確認）：
```makefile
#!/usr/bin/make -f
export DEB_BUILD_MAINT_OPTIONS = hardening=+all
%:
	dh $@

override_dh_auto_install:
	dh_auto_install -- PREFIX=/usr LIBDIR=/usr/lib/$(DEB_HOST_MULTIARCH)

DEB_HOST_MULTIARCH ?= $(shell dpkg-architecture -qDEB_HOST_MULTIARCH)
```

`export DEB_BUILD_MAINT_OPTIONS = hardening=+all` 強制全開 hardening。

</details>

<details>
<summary>Step 3：symbols ABI 追蹤</summary>

```bash
cd greet-1.0/
# build 一次產生 library
dpkg-buildpackage -us -uc -b
cd ..

# 生成 symbols 檔範本
cd greet-1.0/
dpkg-gensymbols -plibgreet1 -Pdebian/libgreet1 -Odebian/libgreet1.symbols
cat debian/libgreet1.symbols
```

`debian/libgreet1.symbols`（手動整理後）：
```
libgreet.so.1 libgreet1 #MINVER#
* Build-Depends-Package: libgreet-dev
 greet_make@Base 1.0
```

啟用嚴格檢查（rules 裡）：
```makefile
override_dh_makeshlibs:
	dh_makeshlibs -- -c4
#                     ↑ 符號不一致時 build 失敗，強制維護 symbols
```

測試 ABI 追蹤：在 `lib/greet.c` 加一個新函式 `greet_loud`，重 build，看 dpkg-gensymbols 抓到新符號並（因 -c4）要求你更新 symbols 檔。

</details>

<details>
<summary>Step 4：autopkgtest</summary>

`debian/tests/control`：
```
Tests: cli-smoke
Depends: @

Tests: lib-usable
Depends: @, gcc, libc6-dev
Restrictions: allow-stderr
```

`debian/tests/cli-smoke`（可執行）：
```bash
#!/bin/sh
set -e
# 測 CLI 工具（用系統路徑！）
out=$(greet World)
test "$out" = "Hello, World!" || { echo "FAIL: got '$out'"; exit 1; }
out=$(greet)
test "$out" = "Hello, World!" || { echo "FAIL default: got '$out'"; exit 1; }
echo "PASS: cli-smoke"
```

`debian/tests/lib-usable`（可執行）：
```bash
#!/bin/sh
set -e
# 測 libgreet-dev 能編譯（用系統的 header 和 library）
cat > test.c <<'EOF'
#include <greet.h>
#include <string.h>
#include <stdio.h>
int main(void) {
    if (strcmp(greet_make("X"), "Hello, X!") != 0) return 1;
    return 0;
}
EOF
gcc -o test test.c -lgreet
./test
echo "PASS: lib-usable"
```

```bash
chmod +x debian/tests/cli-smoke debian/tests/lib-usable
```

</details>

<details>
<summary>Step 5：整合驗證</summary>

```bash
cd greet-1.0/
dpkg-buildpackage -S -us -uc    # 打包 source
cd ..

# 1. sbuild 乾淨建置（含 lintian + autopkgtest 如果 .sbuildrc 開了）
sbuild -d bookworm \
    --run-lintian \
    --run-autopkgtest \
    greet_1.0-1.dsc

# 2. 獨立確認零 lintian
lintian -iI greet_1.0-1_amd64.changes
echo "lintian exit: $?"   # 應該 0，無輸出

# 3. 獨立跑 autopkgtest
autopkgtest greet_1.0-1_amd64.changes -- null
#（或用 lxc 更乾淨）
```

**解答說明**：

- **零 warning 的關鍵不是 override，是真修**：man page 補上、Description 改好、hardening 開全、symbols 維護好。每個 warning 對應一個真實的品質缺陷
- **`DEB_BUILD_MAINT_OPTIONS = hardening=+all`** 強制所有 hardening（stack protector、PIE、RELRO、bindnow、fortify）。手寫 Makefile 的專案容易漏，這行補上
- **symbols 的 `-c4`** 讓 build 在符號不一致時失敗——這是強制 ABI 紀律的機制，逼你每次改 library 都更新 symbols
- **autopkgtest 用系統路徑**（`greet`、`-lgreet`）—測「裝起來能用」而非「build 目錄能用」
- **sbuild + lintian + autopkgtest 三合一**：sbuild 確保依賴完整、lintian 確保形狀正確、autopkgtest 確保功能可用。三者全綠 = 生產級

</details>

## 測試用案例

| 檢查 | 通過標準 |
|---|---|
| `sbuild -d bookworm greet_*.dsc` | Status: successful |
| `lintian -iI greet_*.changes` | 無輸出（exit 0）|
| `dpkg-deb -c greet_*.deb \| grep man` | 含 `greet.1.gz`（壓縮的 man page）|
| `cat debian/libgreet1.symbols` | 含 `greet_make@Base 1.0` |
| build 後加新函式重 build | dpkg-gensymbols 抓到，-c4 要求更新 |
| `autopkgtest greet_*.changes -- null` | 兩個測試都 PASS |
| `lintian` 的 hardening tags | 無（hardening 全開）|

## 延伸挑戰（加分）

- **挑戰一**：做到連 `--pedantic` 都零輸出（pedantic 是吹毛求疵級，全清需要極致細節，如 `debian/upstream/metadata` 等）

- **挑戰二**：測試 ABI 破壞流程：移除 `greet_make`，看 dpkg-gensymbols 報符號消失、-c4 讓 build 失敗，然後正確處理（遞增 SONAME `.so.2`、套件改名 `libgreet2`、symbols 更新）

- **挑戰三**：加 reproducible build 驗證：用 `SOURCE_DATE_EPOCH` 固定 build 兩次，用 `diffoscope` 比對兩個 `.deb` 是否 byte-for-byte 相同

- **挑戰四**：把整個流程接上 sbuild 的 `--run-autopkgtest`，讓一條 sbuild 命令同時做 clean build + lintian + autopkgtest

## 自我檢核

- [ ] 能把一個有 warning 的套件**真正修到**零 warning（不靠 override 掩蓋）
- [ ] 理解 sbuild（依賴）、lintian（形狀）、autopkgtest（功能）三者各驗證什麼，缺一不可
- [ ] 知道 `DEB_BUILD_MAINT_OPTIONS = hardening=+all` 解決什麼
- [ ] 能維護 symbols 檔，知道 `-c4` 的作用
- [ ] 能說出「能用的套件」和「能上傳 archive 的套件」的具體差別（這個練習做的所有事）

→ [Ch 20 GPG 簽署機制](./20-gpg-signing.md)
