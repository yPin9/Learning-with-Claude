# 練習 B — 打包一個真實 C 專案

> **目標**：把 Ch 6–13 學到的東西全部整合，用 debhelper 把一個真實的 C 專案（含一個 shared library + 一個用它的 command-line 工具）打包成多個 `.deb`。完成後你會走過完整的現代打包流程：建立 `debian/` 骨架、寫 control、rules、用 `${shlibs:Depends}`、拆分 library/dev/tool 套件、quilt patch、build、安裝測試。

## 背景與動機

練習 A 你手工組裝了 `.deb`，理解了底層。現在用「真正的方式」——debhelper——打包一個更接近真實世界的專案：一個提供問候功能的 C library `libgreet`，和一個用它的 CLI 工具 `greet`。

這個結構（library + 用它的工具）是無數真實套件的縮影：`libcurl` + `curl`、`libpng` + `pngtools`、`libssl` + `openssl`。學會打包它，你就能打包大部分 C 專案。

## 任務規格

從以下 upstream 專案（你要先建立它，模擬拿到 upstream tarball）開始：

```
greet-1.0/                  ← upstream 專案（你先寫好）
├── Makefile                ← 簡單的 build 系統
├── include/greet.h         ← public header
├── lib/greet.c             ← library 實作
└── src/main.c              ← CLI 工具
```

打包成**三個** binary package：

| 套件 | 內容 | Architecture |
|---|---|---|
| `libgreet1` | runtime shared library（`libgreet.so.1`）| any |
| `libgreet-dev` | header + dev symlink（`greet.h`, `libgreet.so`）| any |
| `greet` | CLI 執行檔（`/usr/bin/greet`）| any |

**驗收標準**：
- `dpkg-buildpackage -us -uc -b` 成功 build 出三個 `.deb`
- `libgreet1` 含 `libgreet.so.1`，`greet` 的 `Depends` 自動含 `libgreet1`（透過 `${shlibs:Depends}`）
- `libgreet-dev` 精確依賴 `libgreet1 (= ${binary:Version})`
- 裝上三個套件後 `greet` 能執行
- 加一個 quilt patch 修改 upstream 的某個行為，build 後生效
- `lintian` 對三個套件不報 error（warning 容後處理，練習 C 才追求零 warning）

**禁止**：用 `checkinstall`、`fpm` 等捷徑工具；必須用 debhelper + `dh`。

## 期望輸出範例

```
$ dpkg-buildpackage -us -uc -b
...
$ ls ../*.deb
../greet_1.0-1_amd64.deb
../libgreet1_1.0-1_amd64.deb
../libgreet-dev_1.0-1_amd64.deb

$ sudo dpkg -i ../libgreet1_*.deb ../greet_*.deb
$ greet World
Hello, World!

$ dpkg-deb -f ../greet_1.0-1_amd64.deb Depends
libc6 (>= 2.34), libgreet1 (>= 1.0)    ← libgreet1 是 ${shlibs:Depends} 自動算出的！
```

## 如果你卡住了

1. upstream 的 Makefile 要支援 `DESTDIR` 和 `PREFIX`，否則 `dh_auto_install` 裝不到暫存目錄
2. library 的 SONAME 要設對（`-Wl,-soname,libgreet.so.1`），否則 `dh_makeshlibs` 算不出版本
3. 三個套件的檔案分配靠三個 `.install` 檔案；runtime 用 `.so.*`，dev 用 `.so`（無版本號）
4. `${shlibs:Depends}` 要生效，需要 `dh_makeshlibs`（為 libgreet1 生成 shlibs）+ `dh_shlibdeps`（為 greet 計算依賴）都跑——`dh $@` 會自動跑
5. `greet` 連結 `libgreet`，build 時要找得到 library，注意 Makefile 的連結順序
6. 回 Ch 7 看 library 拆分的 control 範例，Ch 12 看 `.install` 的 glob 規則

## 實作步驟建議

### Step 1：建立 upstream 專案（模擬拿到 tarball）

寫好 `greet-1.0/` 的四個檔案，確認它能獨立 `make` 成功。然後打包成 `greet-1.0.tar.gz` 當作 orig tarball。

### Step 2：建立 debian/ 骨架

用 `dh_make` 生成骨架，或手動建立。選 library 類型。

### Step 3：寫 debian/control（三個 stanza）

source stanza + libgreet1 + libgreet-dev + greet。

### Step 4：寫三個 .install 檔案分配檔案

### Step 5：寫 rules（可能只需要 `dh $@`，或加 override）

### Step 6：加一個 quilt patch

### Step 7：build、安裝、測試、lintian

## 完整參考解答

**寫完再看！不要偷看。**

<details>
<summary>Step 1：upstream 專案（greet-1.0/）</summary>

`include/greet.h`：
```c
#ifndef GREET_H
#define GREET_H

/* 回傳一個問候字串給 name（caller 不需 free，回傳 static buffer）*/
const char *greet_make(const char *name);

#endif
```

`lib/greet.c`：
```c
#include "greet.h"
#include <stdio.h>
#include <string.h>

static char buffer[256];

const char *greet_make(const char *name)
{
    snprintf(buffer, sizeof(buffer), "Hello, %s!", name ? name : "stranger");
    return buffer;
}
```

`src/main.c`：
```c
#include "greet.h"
#include <stdio.h>

int main(int argc, char **argv)
{
    const char *name = (argc > 1) ? argv[1] : "World";
    printf("%s\n", greet_make(name));
    return 0;
}
```

`Makefile`（支援 DESTDIR/PREFIX，設 SONAME）：
```makefile
PREFIX  ?= /usr
DESTDIR ?=
CC      ?= cc
CFLAGS  ?= -O2 -g -Wall
LDFLAGS ?=

# multiarch lib 目錄（debhelper 會傳入正確的 triplet）
LIBDIR  ?= $(PREFIX)/lib

SONAME  = libgreet.so.1
LIBFILE = libgreet.so.1.0.0

.PHONY: all install clean

all: $(LIBFILE) greet

# 編 shared library，設定 SONAME（關鍵！）
$(LIBFILE): lib/greet.c
	$(CC) $(CFLAGS) -fPIC -shared -Iinclude \
		-Wl,-soname,$(SONAME) \
		$(LDFLAGS) -o $@ $<
	ln -sf $(LIBFILE) libgreet.so.1
	ln -sf $(LIBFILE) libgreet.so

# 編 CLI 工具，連結 libgreet
greet: src/main.c $(LIBFILE)
	$(CC) $(CFLAGS) -Iinclude $(LDFLAGS) -o $@ $< -L. -lgreet

install: all
	install -d $(DESTDIR)$(LIBDIR)
	install -m 644 $(LIBFILE) $(DESTDIR)$(LIBDIR)/
	ln -sf $(LIBFILE) $(DESTDIR)$(LIBDIR)/libgreet.so.1
	ln -sf $(LIBFILE) $(DESTDIR)$(LIBDIR)/libgreet.so
	install -d $(DESTDIR)$(PREFIX)/include
	install -m 644 include/greet.h $(DESTDIR)$(PREFIX)/include/
	install -d $(DESTDIR)$(PREFIX)/bin
	install -m 755 greet $(DESTDIR)$(PREFIX)/bin/

clean:
	rm -f $(LIBFILE) libgreet.so libgreet.so.1 greet
```

建立 orig tarball：
```bash
tar czf greet_1.0.orig.tar.gz greet-1.0/
```

</details>

<details>
<summary>Step 2–6：debian/ 目錄完整內容</summary>

`debian/control`：
```
Source: greet
Section: libs
Priority: optional
Maintainer: Your Name <you@example.com>
Build-Depends: debhelper-compat (= 13)
Standards-Version: 4.6.2
Homepage: https://example.com/greet
Rules-Requires-Root: no

Package: libgreet1
Architecture: any
Multi-Arch: same
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: friendly greeting library (runtime)
 libgreet provides a simple function to generate greeting strings.
 .
 This package contains the shared library.

Package: libgreet-dev
Section: libdevel
Architecture: any
Multi-Arch: same
Depends: libgreet1 (= ${binary:Version}), ${misc:Depends}
Description: friendly greeting library (development files)
 libgreet provides a simple function to generate greeting strings.
 .
 This package contains the header file and the development symlink
 needed to compile programs against libgreet.

Package: greet
Section: utils
Architecture: any
Depends: ${shlibs:Depends}, ${misc:Depends}
Description: command-line greeting tool
 A small command-line program that prints a greeting, built on top
 of libgreet.
```

`debian/libgreet1.install`：
```
usr/lib/*/libgreet.so.*
```

`debian/libgreet-dev.install`：
```
usr/include/greet.h
usr/lib/*/libgreet.so
```

`debian/greet.install`：
```
usr/bin/greet
```

`debian/rules`：
```makefile
#!/usr/bin/make -f
%:
	dh $@

# Makefile 用 LIBDIR 變數，要傳入 multiarch 路徑
override_dh_auto_install:
	dh_auto_install -- \
		PREFIX=/usr \
		LIBDIR=/usr/lib/$(DEB_HOST_MULTIARCH)

# 取得 multiarch triplet（如 x86_64-linux-gnu）
DEB_HOST_MULTIARCH ?= $(shell dpkg-architecture -qDEB_HOST_MULTIARCH)
```

`debian/source/format`：
```
3.0 (quilt)
```

`debian/changelog`（用 `dch --create` 生成）：
```
greet (1.0-1) unstable; urgency=medium

  * Initial release.

 -- Your Name <you@example.com>  Thu, 29 May 2025 12:00:00 +0000
```

`debian/copyright`（DEP-5，省略全文）：
```
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: greet

Files: *
Copyright: 2025 Your Name <you@example.com>
License: MIT
 (full MIT text)

Files: debian/*
Copyright: 2025 Your Name <you@example.com>
License: MIT
 (full MIT text)
```

加一個 quilt patch（修改問候語）：
```bash
cd greet-1.0/
quilt new add-exclamation.patch
quilt add lib/greet.c
# 改 greet.c，把 "Hello, %s!" 改成 "Hello, %s!!!"
sed -i 's/Hello, %s!/Hello, %s!!!/' lib/greet.c
quilt refresh
quilt header -e --dep3   # 寫 DEP-3 說明
```

</details>

<details>
<summary>Step 7：build 與驗證</summary>

```bash
cd greet-1.0/
dpkg-buildpackage -us -uc -b

cd ..
ls *.deb
# greet_1.0-1_amd64.deb
# libgreet1_1.0-1_amd64.deb
# libgreet-dev_1.0-1_amd64.deb

# 檢查 greet 的依賴是否自動含 libgreet1
dpkg-deb -f greet_1.0-1_amd64.deb Depends
# libc6 (>= 2.34), libgreet1 (>= 1.0)   ← ${shlibs:Depends} 生效！

# 檢查 libgreet1 內容
dpkg-deb -c libgreet1_1.0-1_amd64.deb | grep libgreet
#   usr/lib/x86_64-linux-gnu/libgreet.so.1     (symlink)
#   usr/lib/x86_64-linux-gnu/libgreet.so.1.0.0 (實體檔)
# 注意：libgreet1 有 .so.1 和 .so.1.0.0，沒有 .so（那是 dev 的）

# 檢查 libgreet-dev 內容
dpkg-deb -c libgreet-dev_1.0-1_amd64.deb | grep -E "greet.h|libgreet.so$"
#   usr/include/greet.h
#   usr/lib/x86_64-linux-gnu/libgreet.so       (dev symlink，無版本號)

# 安裝測試
sudo dpkg -i libgreet1_*.deb greet_*.deb
greet
# Hello, World!!!    ← patch 生效（三個驚嘆號）
greet Debian
# Hello, Debian!!!

# lintian
lintian *.deb
```

**解答說明**：

- **SONAME 是關鍵**：Makefile 用 `-Wl,-soname,libgreet.so.1` 設定 SONAME。`dh_makeshlibs` 讀 binary 的 SONAME 生成 shlibs 資訊，`dh_shlibdeps` 用它算出 `greet` 依賴 `libgreet1`。SONAME 設錯，整個 `${shlibs:Depends}` 鏈就斷了（Ch 19 詳談）

- **三個 .install 的 glob 精度**：libgreet1 用 `libgreet.so.*`（抓 `.so.1` 和 `.so.1.0.0`），libgreet-dev 用 `libgreet.so`（只抓無版本號的 symlink）。這個區分決定使用者裝 runtime 還是 dev

- **`LIBDIR` 傳 multiarch 路徑**：現代 Debian 把 library 放 `/usr/lib/<triplet>/`（如 `/usr/lib/x86_64-linux-gnu/`）支援多架構共存。rules 用 `dpkg-architecture -qDEB_HOST_MULTIARCH` 取得 triplet 傳給 Makefile

- **`Multi-Arch: same`**：libgreet1 和 dev 標這個，表示不同架構的版本能共存（Ch 18）

- **dev 精確綁定**：`libgreet-dev Depends: libgreet1 (= ${binary:Version})`——header 必須對應精確的 runtime 版本

</details>

## 測試用案例

| 操作 | 預期結果 | 驗證什麼 |
|---|---|---|
| `dpkg-buildpackage -b` | 生成 3 個 .deb | 完整 build |
| `dpkg-deb -f greet Depends` | 含 `libgreet1 (...)` | ${shlibs:Depends} 機制 |
| `dpkg-deb -c libgreet1` | 有 .so.1，無 .so | install glob 正確 |
| `dpkg-deb -c libgreet-dev` | 有 .so 和 .h | dev 套件正確 |
| 裝 libgreet1+greet 後 `greet` | `Hello, World!!!` | patch 生效 + 連結正確 |
| 只裝 greet（不裝 libgreet1）| apt 報缺依賴 | 依賴宣告正確 |
| `lintian *.deb` | 無 error | 基本合規 |

## 延伸挑戰（加分）

- **挑戰一**：加 `libgreet-doc` 套件（`Architecture: all`），放一個 man page（`greet.1`），用 `dh_installman` 安裝並壓縮

- **挑戰二**：加 `debian/libgreet1.symbols` 檔案追蹤 ABI（Ch 19 預習）：`dpkg-gensymbols` 生成 symbols，故意改 library 加一個 function，看 symbols diff

- **挑戰三**：讓 build 支援 `nocheck` profile——加一個假的測試（`make check`），用 `override_dh_auto_test` 尊重 `DEB_BUILD_OPTIONS=nocheck`

- **挑戰四**：用 `gbp`（git-buildpackage）管理整個專案：把 upstream 放 `upstream` branch、debian 放 `debian` branch、用 `gbp buildpackage` build

## 自我檢核

- [ ] 能不看參考，從零建立一個 library + tool 的多套件打包
- [ ] 理解 `${shlibs:Depends}` 如何讓 `greet` 自動依賴 `libgreet1`（SONAME → shlibs → shlibdeps）
- [ ] 知道 runtime（`.so.*`）和 dev（`.so`）套件的 `.install` glob 為什麼不同
- [ ] 能解釋為什麼 library 放 `/usr/lib/<triplet>/` 而非 `/usr/lib/`
- [ ] 能說出自己的多套件打包和練習 A 的手工單套件差在哪（debhelper 自動化了分配、依賴計算、權限、壓縮）

→ [Ch 14 dpkg-buildpackage 全流程](./14-dpkg-buildpackage.md)
