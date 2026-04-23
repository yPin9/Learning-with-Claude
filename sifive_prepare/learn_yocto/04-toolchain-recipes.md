# Ch 4 — Toolchain recipe：gcc-cross / binutils-cross / glibc

> 目標：走一遍 toolchain 在 Yocto 的 build flow —— gcc-cross-initial → glibc → gcc-cross → binutils-cross。理解為什麼要三階段 bootstrap、SiFive 工程師常改哪些 recipe。

## Yocto build cross-toolchain 的三階段

```
1. gcc-cross-initial-riscv64      ← minimal GCC (no libc)
2. glibc                           ← build glibc using initial GCC
3. gcc-cross-riscv64               ← full GCC with libc support
```

這是 **toolchain bootstrap** 的經典 pattern。最後產生能 build target userspace 的 cross-compiler。

## 為什麼要三階段

雞生蛋問題：

- GCC 要 glibc header 才能 build
- glibc 要 GCC 才能 compile
- 解：先 build stripped-down GCC、用它 build glibc、再用 glibc build full GCC

## 一些相關 recipe 名稱

```
gcc-cross-initial-riscv64     stage 1 GCC
gcc-cross-riscv64              stage 3 GCC
gcc-crosssdk-riscv64           SDK 版本
gcc-runtime                    libgcc, libstdc++ for target
binutils-cross-riscv64         as, ld for target
binutils-crosssdk-riscv64      SDK 版本

glibc                          C library (target version)
glibc-locale
glibc-mtrace

linux-libc-headers             kernel header for userspace
```

這些都有 recipe 在 `poky/meta/recipes-devtools/` 或 `poky/meta/recipes-core/glibc/`。

## 看 GCC recipe：`gcc_11.2.bb`

```bash
cat poky/meta/recipes-devtools/gcc/gcc_11.2.bb
```

主要 content：

```
require gcc-common.inc
require gcc-configure-common.inc

PV = "11.2.0"

SRC_URI = "..."
SRC_URI[sha256sum] = "..."

DEPENDS =+ "gmp-native mpfr-native libmpc-native zlib-native flex-native"

# Compiler mods
PV_STRIPPED = "${@gcc_strip_version(d)}"
```

注意：

- `require gcc-common.inc`：核心邏輯在 common file
- `gcc-configure-common.inc`：configure step 的 flags
- SRC_URI 指向 upstream GCC tarball
- DEPENDS 多 `-native`（host 工具）

詳細 logic 在 `gcc-*.inc`。讀這些 file（100+ KB）是 learning material。

## `gcc-cross-initial`：stripped GCC

```bash
cat poky/meta/recipes-devtools/gcc/gcc-cross-initial.inc
```

```
INHIBIT_DEFAULT_DEPS = "1"
DEPENDS = "virtual/${TARGET_PREFIX}binutils \
           gmp-native mpfr-native libmpc-native zlib-native"
PROVIDES = "virtual/${TARGET_PREFIX}gcc-initial"

# Disable features that need libc
EXTRA_OECONF += "--without-headers \
                 --with-newlib \
                 --disable-shared \
                 --disable-threads \
                 --disable-libmudflap \
                 ..."

# Minimal language support
EXTRA_OECONF += "--enable-languages=c"
```

解讀：

- 只 build C、不 build C++
- No shared library、no threads
- 目的：minimal 能用來 build glibc
- stage 1 output 後會被覆蓋

## `binutils-cross` recipe

```bash
cat poky/meta/recipes-devtools/binutils/binutils_2.38.bb
```

核心：

```
require binutils.inc

PV = "2.38"

FILESEXTRAPATHS:prepend := "${THISDIR}/binutils:"

SRC_URI = "\
    ${GNU_MIRROR}/binutils/binutils-${PV}.tar.bz2 \
    file://0001-Fix-compile-error-on-x86_64.patch \
    file://0002-Fix-RISC-V-issue.patch \
    ... (很多 patch) \
"
```

binutils 通常有一堆 distro-specific patch。Yocto upstream 的 `poky/meta/recipes-devtools/binutils/binutils/` 資料夾有。

## glibc recipe

```bash
cat poky/meta/recipes-core/glibc/glibc_2.35.bb
```

```
require glibc.inc

PV = "2.35"

DEPENDS += "gperf-native bison-native \
            virtual/${TARGET_PREFIX}binutils \
            virtual/${TARGET_PREFIX}gcc-initial"

# 太多 config，略
EXTRA_OECONF = "--without-cvs ..."
```

glibc recipe 複雜（幾百行）因為 libc 支援超多 feature (locale, NSS, ... )。

## Cross vs native vs crosssdk

同一 tool 多個 variant：

```
gcc-cross-riscv64           build in host, runs on host, produces RISC-V binaries
gcc-crosssdk-riscv64        build for SDK (runs on developer host, produces RISC-V)
gcc                         target (builds for target, runs on target)
gcc-runtime                 libraries for target (libgcc, libstdc++)
```

Flow：

- target 的 runtime（libgcc）：由 `gcc-cross-riscv64` 產、放 target rootfs
- SDK 用的 GCC：由 `gcc-crosssdk-riscv64` 產、packaging 成 SDK installer

compiler 工程師改 `gcc` recipe 時，所有 variant 都要 rebuild（除非精確控 override）。

## 加 patch 到 GCC recipe

**SiFive 工程師最常做的事**。假設你有 patch `0001-riscv-add-xmyext.patch`：

Step 1: 建 `.bbappend` in your layer

```
meta-sifive/recipes-devtools/gcc/gcc_%.bbappend
```

內容：

```
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://0001-riscv-add-xmyext.patch \
"
```

Step 2: Patch file 放對 path

```
meta-sifive/recipes-devtools/gcc/files/0001-riscv-add-xmyext.patch
```

Step 3: Rebuild

```bash
bitbake -c cleansstate gcc-cross-riscv64
bitbake gcc-cross-riscv64
```

Step 4: Verify

```bash
# Patch applied?
cat tmp/work/x86_64-linux/gcc-cross-riscv64/11.2-r0/temp/log.do_patch

# Binary 產出?
ls tmp/work/x86_64-linux/gcc-cross-riscv64/11.2-r0/sysroot-destdir/usr/bin/
```

## 常見 pattern：bump GCC version

假設 upstream 出 GCC 12，你想用：

Option 1: **Override PREFERRED_VERSION**

```
# conf/local.conf
PREFERRED_VERSION_gcc = "12.2%"
```

但 poky 要有 gcc_12.2.bb。

Option 2: 自己加 gcc_12.2.bb

複製 11.2 的、改 version、update SRC_URI + sha256。測試。

Option 3: **等 poky 跟進**

通常 poky LTS 是可用 GCC version。急就跳 master branch。

## 如果 recipe build fail

```bash
# 看哪個 task fail
bitbake gcc-cross-riscv64 2>&1 | tail

# 看具體 error
tail tmp/work/x86_64-linux/gcc-cross-riscv64/11.2-r0/temp/log.do_compile
```

Common issues：

- Patch doesn't apply cleanly → fix patch
- New compile error → source / toolchain 不相容
- Out of memory → reduce BB_NUMBER_THREADS

## 多 branch 的 CI

SiFive 內部 CI 可能 build：

- GCC upstream + SiFive patches
- LLVM upstream + SiFive patches
- binutils upstream + SiFive patches
- Against multiple core targets (U74, P670, P870)

每個組合都要 build + smoketest. 自動化 script 配 Yocto 跑。

## CMake 的 cross compile

Yocto 的 CMake recipe 自動 set cross compile flag。`inherit cmake` 在 `.bb` 裡會 handle。

測試：

```bash
bitbake -c compile llvm-project
```

LLVM 用 CMake、Yocto 的 cmake class 幫你設 `CMAKE_CROSSCOMPILING=1` 等。

## `gcc-runtime`：target libs

這是 runtime libraries（libgcc.so、libstdc++.so）for target：

```bash
cat poky/meta/recipes-devtools/gcc/gcc-runtime.inc
```

產生的 .so files 進 rootfs `/usr/lib/`。所有 C++ program 需要 libstdc++.so.6。

加 SiFive 自家 libgcc 優化（假設你有）就改這裡。

## `libstdc++` 特殊

`libstdc++.so` 有自己的 sub-recipe。C++ ABI 變動時慎重 handle。SiFive 改 C++ backend 時可能需要 rebuild libstdc++。

## recipe 的 priority

多個 `.bb` 定義同一 PN 時，bitbake 選：

- 最新 PV（若 PREFERRED_VERSION 沒指）
- Layer priority 高的（若 PREFERRED_VERSION 指 version）

用 `BBFILE_PRIORITY_<collection>` 調 layer priority。

## SiFive 工程師的 toolchain 三關

三個常 touch recipes：

1. **`gcc_%.bbappend`**：加 SiFive GCC patch
2. **`binutils_%.bbappend`**：加 binutils patch (e.g., 新 relocation type)
3. **`newlib_%.bbappend`** (for embedded)：加 newlib patch

以及 meta-data：

4. **`tune-sifive.inc`**：新增 custom tune value
5. **`conf/machine/sifive-*.conf`**：你的 board config

這五個是 SiFive job spec 第三條 "integrate GNU toolchain recipes in Yocto/OE" 的具體對象。

## 動手練習

1. 讀 `poky/meta/recipes-devtools/gcc/gcc_11.2.bb` + `gcc-cross.inc`。
2. 找出 `gcc-cross-riscv64` 的 recipe（hint: `.bb` 或 `.bbclass` 系統）。
3. 建一個 `.bbappend` 加空 patch 到 gcc、驗證 build 觸發 patch step。
4. `bitbake -e gcc-cross-riscv64 | grep -E "^SRC_URI|^DEPENDS"` 看變數。
5. Rebuild gcc-cross from scratch、量時間跟 disk usage。

## 常見誤會

1. **「toolchain bootstrap 是 legacy」**：還是 stadnard。某些 newer Yocto 會用 externalsrc / devtool 更方便。
2. **「我 patch 了 gcc 但 target 仍舊 behavior」**：check 是 `gcc-cross-riscv64` 還是 `gcc` (target native)。兩個 recipe 不同。
3. **「glibc version 可以隨便升」**：會影響整個 system ABI。慎重。
4. **「binutils 很少改」**：對 SiFive 相反。新 relocation type 常要改 binutils。
5. **「recipe 不能改 SRC_URI git」**：可以，改 SRCREV 就是拉不同 commit。

## 自我檢核

- [ ] 我能 trace GCC toolchain 的三階段 build
- [ ] 我知道 gcc-cross / gcc-crosssdk / gcc-runtime 差異
- [ ] 我能寫 `.bbappend` 加 patch 到 gcc recipe
- [ ] 我知道 binutils / glibc / libstdc++ 各自角色
- [ ] 我能 rebuild cross-toolchain 並 verify 產物

下一章實戰：patch 一個 CVE fix 進 gcc recipe。

→ [Ch 5 Patch 一個 upstream GCC bug 進 image](./05-patching-gcc.md)
