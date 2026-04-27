# Ch 2 — `.bb` / `.bbappend` / `.bbclass` 語法

> 目標：看得懂 + 會寫三種 bitbake 檔案。`.bb` 是 recipe、`.bbappend` 擴充已存在 recipe、`.bbclass` 是 reusable class。這章是 compiler 工程師改 recipe 的語法基礎。

## 三者的關係

```
.bb       ─── 定義 recipe（一個 package）
.bbappend ─── 擴充別人的 .bb（加 patch、改 variable）
.bbclass  ─── 可被 recipe 繼承的 class（通用邏輯）
```

最常改的是 **`.bbappend`**（你把 patch 加到 upstream recipe）。

## `.bb` 的完整結構

```
# headline comment

SUMMARY = "short description"
DESCRIPTION = "longer description \
    spanning multiple lines"
HOMEPAGE = "https://..."
SECTION = "libs"

LICENSE = "LGPL-2.1"
LIC_FILES_CHKSUM = "file://COPYING.LIB;md5=..."

SRC_URI = "git://github.com/foo/bar.git;branch=main"
SRCREV = "abcdef..."

DEPENDS = "zlib openssl"
RDEPENDS:${PN} = "libc-openssl"

S = "${WORKDIR}/git"

inherit autotools pkgconfig

EXTRA_OECONF = "--with-foo --disable-bar"

do_install:append() {
    install -m 0644 ${S}/extra.conf ${D}${sysconfdir}/
}

FILES:${PN} += "${sysconfdir}/extra.conf"

# 測試
do_check() {
    oe_runmake check
}
addtask check after do_install
```

每區塊功能：

## Headers：Description 等

```
SUMMARY       <80 字元 description
DESCRIPTION   長描述
HOMEPAGE
SECTION       categorize (libs, devel, kernel...)
```

Build system 不會用、但 package manager UI 會顯示。

## LICENSE

```
LICENSE = "LGPL-2.1-only & MIT"        # 多 license 用 & 
LIC_FILES_CHKSUM = "file://COPYING;md5=..."
```

Yocto 嚴格 enforce license info。`LIC_FILES_CHKSUM` 是 license 檔的 md5，確保 upstream 沒改 license。

常見 license string：

```
GPL-2.0-only
GPL-2.0-or-later
LGPL-2.1
Apache-2.0
MIT
BSD-3-Clause
...
```

## SRC_URI：source location

多種 protocol：

```
# HTTP/HTTPS
SRC_URI = "https://example.com/foo-1.0.tar.gz"
SRC_URI[md5sum] = "..."
SRC_URI[sha256sum] = "..."

# Git
SRC_URI = "git://github.com/foo/bar.git;branch=main;protocol=https"
SRCREV = "commit-hash-or-tag"

# 本地 file (相對 meta-xxx/recipes-xxx/foo/)
SRC_URI = "file://my-patch.patch"
SRC_URI += "file://mydata.txt"
```

每個 SRC_URI 項會走 `do_fetch`。

## Patch

Yocto 自動 apply 結尾 `.patch` / `.diff` 的 SRC_URI 項：

```
SRC_URI = "https://.../foo.tar.gz \
           file://0001-fix-bug.patch \
           file://0002-improve-perf.patch"
```

bitbake 在 `do_patch` stage 跑 `patch -p1`。

**這是 SiFive 工程師日常**：加 patch 到 toolchain recipe 的 `SRC_URI`。Ch 5 實戰。

## S 變數：source 解壓後的 location

```
S = "${WORKDIR}/git"          # git clone
S = "${WORKDIR}/foo-1.0"      # tarball 解出 foo-1.0/
```

`S` 設對 bitbake 才知道哪邊 source。

`${WORKDIR}` = `tmp/work/<arch>/<recipe>/<ver>-<rev>/`

## DEPENDS / RDEPENDS

```
DEPENDS = "zlib openssl python3-native"
#          ↑     ↑       ↑
#     build dep build-dep native-build-tool

RDEPENDS:${PN} = "libc6 bash"
#                 ↑     ↑
#              runtime dep
```

- **DEPENDS**：build 時要這些 package
- **RDEPENDS**：runtime 要 (rootfs 裡 package 需要它們)
- `-native`：在 host 上 build 的版本（用於 cross-compile）

## PV、PN、PR

內建變數：

```
PN        package name (從 file name, e.g., "gcc")
PV        version (e.g., "11.2")
PR        revision (e.g., "r0"), 改 recipe 沒改 version 時 bump
P = "${PN}-${PV}"
BP = "${PN}-${PV}"
BPN = "${PN}"       (strips -native, -nativesdk suffix)
```

## inherit

```
inherit autotools pkgconfig
```

Include `.bbclass` 檔的邏輯。多個 class 用空格。

常用 class：

- `autotools`：configure/make/make install
- `cmake`：CMake build
- `meson`：Meson build
- `pkgconfig`：pkg-config 支援
- `systemd`：service 支援
- `kernel`：Linux kernel 特別處理
- `cross-canadian`：cross compile 特別

## `.bbappend` 擴充別的 recipe

**最常用**。假設 upstream 有 `gcc_11.2.bb`，你要加自己的 patch：

```
# meta-mycompany/recipes-devtools/gcc/gcc_11.2.bbappend

FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI += "file://0001-sifive-custom-extension.patch"
```

解讀：

- file name 要 **完全 match** upstream: `gcc_11.2.bbappend` （version 跟 `_11.2` 一致）
- `FILESEXTRAPATHS:prepend` 加 search path：my `files/` dir 放 patch
- `SRC_URI += ` 把 patch 加到 upstream 的 SRC_URI list
- bitbake `do_patch` 自動 apply

**這是 compiler 工程師 90% 的工作**：寫 `.bbappend` 加 patch 到 toolchain recipe。

### 版本 wildcard

```
gcc_%.bbappend        匹配所有 gcc_*.bb
```

百搭對所有 gcc version。小心：可能 version 不支援你的 patch。

推薦明確 version（`gcc_11.2.bbappend`）或 `gcc_11.%.bbappend`（11.x 系列）。

## `.bbclass` 定義可 reuse 的邏輯

```
# classes/mycompany-toolchain.bbclass

mycompany_add_flag_to_cflags() {
    CFLAGS += " -DMYCOMPANY=1"
}

EXTRA_OECONF:append = " --enable-mycompany"
```

Recipe 繼承：

```
inherit mycompany-toolchain
```

多個 recipe 有共同 config → 抽到 `.bbclass`。

## 變數 expansion

```
NAME = "hello"
FOO = "${NAME}-world"     # Foo = "hello-world", lazy expand
FOO2 := "${NAME}-world"   # Foo2 = "hello-world", immediate expand
```

通常用 `=`（lazy）。`:=` 在特定場景才要（e.g., append path 前後順序）。

## Python 寫 variables

```
python () {
    # Executed at parse time
    if d.getVar('HAS_FOO'):
        d.appendVar('DEPENDS', ' libfoo')
}
```

`d` 是 bitbake DataStore。這讓你寫複雜邏輯。

## 除錯 recipe

### 1. Dump variables

```bash
bitbake -e gcc | grep -E "^SRC_URI|^DEPENDS|^S="
```

`-e` 印 expanded environment for that recipe。

### 2. Check recipe parse

```bash
bitbake -p
```

Parse all recipes、show error。不 build。

### 3. 強制 rebuild

```bash
bitbake -c cleansstate gcc
bitbake gcc
```

`cleansstate` 砍 sstate cache、force re-do everything。

### 4. Break at specific task

```bash
bitbake -c devshell gcc
```

進 recipe 的 source dir 的 shell、可以手動 run make / configure debug。

## 常見 recipe pattern

### Cross compile

```
# 假設是 host binary (native)
BBCLASSEXTEND = "native"
```

會自動產生 `hello-native` 變體、用 host compiler 編。

### Multiple binary packages

一個 source 產 multi `.deb`：

```
PACKAGES = "${PN} ${PN}-tools ${PN}-dev"

FILES:${PN} = "${bindir}/foo"
FILES:${PN}-tools = "${bindir}/foo-*"
FILES:${PN}-dev = "${includedir}/*"
```

## FILESEXTRAPATHS

`.bbappend` 加自家 patch 時的關鍵：

```
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"
```

`THISDIR` = 這個 `.bbappend` 所在 dir。加 `files/` 子 dir 到 search path。之後 `SRC_URI += "file://patch"` 會找這 path。

不加 FILESEXTRAPATHS → bitbake 找不到 patch file。

## 常見語法錯誤

### Error 1：`SRC_URI =+ "..."`

`=+` 是舊 prepend with space。新寫法 `:prepend`。

### Error 2：忘記 `SRCREV`

git source 沒 SRCREV → recipe 不穩、每 build 拿不同 commit（bitbake 會警告）。

### Error 3：Patch path 錯

`SRC_URI += "file://my.patch"` 但 patch 不在 `FILESEXTRAPATHS` 的路徑。

Error：`Fetcher failure: Unable to find file...`

### Error 4：變數命名錯

```
# 錯
DEPEND = "zlib"      # 多數變數有 's'

# 對
DEPENDS = "zlib"
```

bitbake 不警告、變數只是沒 effect。

### Error 5：license mismatch

`LIC_FILES_CHKSUM` 的 md5 對不上。upstream 改 license file、bitbake 拒 build（protection 好）。

## 動手練習

1. 讀 `poky/meta/recipes-core/busybox/busybox_1.35.bb`，辨認 SUMMARY / SRC_URI / do_install。
2. 找一個 `.bbappend`，看它 override 什麼。
3. 寫一個最小 recipe：印 "hello from yocto" 的 shell script。deploy。
4. 讀 `poky/meta/classes/autotools.bbclass` 的前 100 行，看 autotools class 做什麼。
5. `bitbake -e <recipe> | less`，找 DEPENDS / SRC_URI / S。

## 自我檢核

- [ ] 我能讀一個 `.bb` recipe、說出每區塊功能
- [ ] 我能寫 `.bbappend` 加 patch 到已存在 recipe
- [ ] 我知道 `:append` / `:prepend` / `:=` / `=` 差異
- [ ] 我能用 `bitbake -e` debug 變數值
- [ ] 我知道 `FILESEXTRAPATHS` 的作用

下一章看 `meta-riscv` layer — RISC-V 生態的 Yocto 主力。

→ [Ch 3 `meta-riscv` layer 解剖](./03-meta-riscv.md)
