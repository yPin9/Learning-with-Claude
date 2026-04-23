# Ch 1 — Yocto 心法：layer / recipe / task / metadata

> 目標：建立 Yocto 的 conceptual model。Layer 是組織單位、recipe 是 build 單位、task 是執行單位、metadata 是黏合劑。理解這套、你讀任何 Yocto repo 都有框架。

## Yocto 的四個核心概念

```
Layer      ─┐
Recipe     ─┼── 你接觸的 3 層
Task       ─┘
 Metadata (bitbake syntax、變數)      ← 貫穿三層
```

## Layer：組織單位

**Layer** = 一個 directory，含相關的 recipe / config。結構：

```
meta-layername/
├── conf/
│   └── layer.conf          ← layer 的 metadata
├── recipes-*/               ← recipes 分類
│   ├── recipes-core/
│   ├── recipes-devtools/
│   └── recipes-kernel/
├── classes/                 ← .bbclass files
└── README
```

常見 layer：

- **poky/meta**：core Yocto metadata（必有）
- **poky/meta-poky**：reference distro config
- **poky/meta-yocto-bsp**：general BSP
- **meta-riscv**：RISC-V 硬體支援 ← SiFive 工程師主力
- **meta-openembedded**：500+ 額外 package
- **meta-<vendor>**：廠商 BSP（e.g., meta-intel, meta-ti）

### layer.conf 範例

```
# meta-riscv/conf/layer.conf
BBPATH .= ":${LAYERDIR}"
BBFILES += "${LAYERDIR}/recipes-*/*/*.bb ${LAYERDIR}/recipes-*/*/*.bbappend"

BBFILE_COLLECTIONS += "riscv"
BBFILE_PATTERN_riscv = "^${LAYERDIR}/"
BBFILE_PRIORITY_riscv = "6"

LAYERDEPENDS_riscv = "core openembedded-layer"
```

Declarations：

- 這個 layer 叫 `riscv`
- 它的 recipes 在 `recipes-*/`
- 優先級 6（higher = more priority when conflict）
- 它 depend on `core` + `openembedded-layer`

### 啟用 layer

編輯 `build/conf/bblayers.conf`：

```
BBLAYERS = " \
    /path/to/poky/meta \
    /path/to/poky/meta-poky \
    /path/to/poky/meta-yocto-bsp \
    /path/to/meta-riscv \
    /path/to/meta-openembedded/meta-oe \
"
```

bitbake 在這些 directory 找 recipe。

## Recipe：build 單位

**Recipe** = 一個 `.bb` 檔，定義一個 package 怎麼 build。

```
# hello_1.0.bb
DESCRIPTION = "Hello world package"
LICENSE = "MIT"
SRC_URI = "git://github.com/example/hello.git;branch=main"
SRCREV = "abc123"

S = "${WORKDIR}/git"

do_compile() {
    make
}

do_install() {
    install -m 755 -D hello ${D}${bindir}/hello
}
```

解讀：

- 這個 recipe 叫 `hello`，version `1.0`
- Source 從 Git clone
- Compile：run `make`
- Install：copy 到 `${D}${bindir}`

`${D}` 是 staging install dir（`tmp/work/.../image/`）。`${bindir}` = `/usr/bin`（by default）。

### Recipe 檔名規則

```
<name>_<version>[-<revision>].bb
```

e.g.：

```
gcc_11.2.bb              name=gcc, version=11.2
linux-yocto_5.15.bb       name=linux-yocto, version=5.15
mypackage_1.0-r2.bb      name=mypackage, version=1.0, revision=2
```

多個 version 同 name：`gcc_10.3.bb`、`gcc_11.2.bb`。bitbake 預設選最新、可用 `PREFERRED_VERSION` override。

### Recipe 變數（variables）

```
DESCRIPTION         package 描述
LICENSE             授權
SRC_URI             source 位置
S                   source directory
PV                  version
PR                  revision

DEPENDS             build-time dep
RDEPENDS_<pkg>      runtime dep

BBCLASSEXTEND       跨 variant 重用 (native, nativesdk)

FILES_<pkg>         這個 binary package 包含哪些 file
```

幾百個變數。慢慢熟。

## Task：執行單位

每 recipe 有一系列 task。default tasks：

```
do_fetch        Download source
do_unpack       Extract
do_patch        Apply patches
do_configure    Run ./configure
do_compile      Run make
do_install      Install to staging
do_package      Package into deb/rpm/ipk
do_package_qa   Sanity check
```

Task order 由 **dependency declaration** 決定：

```
addtask do_compile after do_configure before do_install
```

意思：`do_compile` 在 `do_configure` 之後、`do_install` 之前跑。

### Task 的 shell function

```
do_compile() {
    oe_runmake
}
```

這是 **shell function**（bash）。可以 call 任何 shell command。

### Task 的 Python function

```
python do_check_version() {
    version = d.getVar('PV')
    if not version.startswith('11.'):
        bb.warn(f"Version {version} might be too old")
}
```

Python 版：`python` 關鍵字 prefix、`d` 是 bitbake 的 data store（當前 config）。

### Override tasks

有時要改已存在 task：

```
do_compile:append() {
    # 加在 default do_compile 後
    echo "Extra step"
}

do_compile:prepend() {
    # 加在前
    echo "Before"
}

do_compile() {
    # 完全覆蓋
    ...
}
```

`:append` / `:prepend` / `:replace` 是 bitbake 操作 task 的方式。

## Metadata：黏合劑

Metadata 是 bitbake 的 **configuration 系統**。變數 + 條件 + 覆蓋。

### 變數賦值

```
# 基本賦值
NAME = "hello"

# 不覆蓋已有
NAME ?= "default"

# Weak assignment
NAME ??= "weaker default"

# 立即 expand
NAME := "${SOME_VAR}"      # expand SOME_VAR 現在的值

# Append with space
NAME += "suffix"
NAME_append = " suffix"    # 舊語法

# Prepend
NAME =+ "prefix"
NAME_prepend = "prefix "   # 舊語法

# 新語法（Yocto 3.4+）
NAME:append = " new way"
NAME:prepend = "new way "
```

**`:append` / `:prepend` 是現代寫法**。舊 recipe 用 `_append`、新寫法用 `:append`。

### Override

Override 讓變數在特定條件下不同：

```
CFLAGS = "-O2"
CFLAGS:riscv64 = "-O2 -march=rv64gc"
CFLAGS:qemuriscv64 = "-O2 -march=rv64gc -mtune=sifive-u74"
```

Override 名（`riscv64`、`qemuriscv64`）對應 `OVERRIDES` variable。典型 override chain：

```
OVERRIDES = "linux:riscv64:qemuriscv64:poky"
```

這讓 bitbake 知道：「對這個 build，可以用 linux / riscv64 / qemuriscv64 / poky 的 override」。

### 條件式

```
DEPENDS += "openssl"
DEPENDS:remove = "openssl"     # 某 condition 下移除

python () {
    if d.getVar('HAVE_FEATURE'):
        d.appendVar('DEPENDS', ' libfoo')
}
```

Python 風格的 inline condition。複雜邏輯用。

## 一個小 recipe 的 full example

```
# meta-mylayer/recipes-example/hello/hello_1.0.bb

SUMMARY = "Simple hello world program"
DESCRIPTION = "A classic hello world, showing Yocto recipe structure"
HOMEPAGE = "https://example.com"

LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=d41d8cd98f00b204e9800998ecf8427e"

SRC_URI = "git://github.com/example/hello.git;protocol=https;branch=main"
SRCREV = "v1.0"

S = "${WORKDIR}/git"

DEPENDS = "zlib"
RDEPENDS:${PN} = "libc"

EXTRA_OECONF = "--enable-foo"

inherit autotools

do_configure:prepend() {
    echo "Pre-configure step"
}

do_install:append() {
    install -m 0644 ${S}/extra-data ${D}${datadir}/hello/
}

FILES:${PN} += "${datadir}/hello/*"
```

每行有意義。Ch 2 細講 syntax。

## bitbake 的 mental model

```
1. Parse all recipes in BBLAYERS
2. Resolve dependency graph
3. Queue tasks in dependency order
4. Run tasks (可能 parallel)
5. Each task output cached in sstate
6. Final task: do_rootfs / do_image 組裝
```

這個 model 讓你知道：

- 改一個 recipe → 那它 + depend it 的要 rebuild
- 改一個 metadata variable → 可能影響全部
- sstate 讓 rebuild 快、但有時幫倒忙（stale cache）

## Recipe 的 lifecycle 視覺化

```
fetch → unpack → patch → configure → compile → install → package → rootfs
  ↓       ↓       ↓         ↓          ↓         ↓          ↓          ↓
 DL_DIR  WORKDIR  patches  configure  make     ${D}        .deb/.rpm  image
```

每 box 是 task、底下是主要產物 location。

## 作為 compiler 工程師的「關鍵 5 個檔」

你日常改的：

```
1. meta-riscv/recipes-devtools/gcc/gcc_%.bbappend         加 patch 給 gcc recipe
2. meta-mycompany/recipes-devtools/binutils/binutils_%.bbappend   同上 for binutils
3. build/conf/local.conf                                   設 MACHINE / override
4. build/conf/bblayers.conf                                加你的 layer
5. 偶爾：poky/meta/recipes-devtools/gcc/gcc_11.bb          看 upstream recipe 學
```

Ch 5 會 walk through 改 GCC recipe 的流程。

## 常見誤會

1. **「Yocto 慢就是差」**：第一次慢、後續 incremental 快。是 trade-off for flexibility。
2. **「改 source code 要改 recipe」**：改 source 要產 patch、加到 recipe 的 `SRC_URI`。
3. **「`.bb` 就是 shell script」**：大部分是、但也含 Python 跟 bitbake 語法。
4. **「我不用看 poky source」**：錯。poky/meta 的 recipe 是 learning material。
5. **「每個 recipe 自 build from scratch」**：sstate cache 讓共用中間產物。

## 動手練習

1. `bitbake-layers show-layers` 看你的環境有哪些 layer。
2. 挑一個 layer，`ls recipes-*` 看結構。
3. 找 `gcc_11.bb`：`find . -name "gcc_11.*.bb"`。讀它。
4. 找一個 `.bbappend` 範例：`find . -name "*.bbappend"`。看它如何 override 別的 recipe。
5. 寫最小 recipe（一個 hello world shell script），把它 build + deploy。

## 自我檢核

- [ ] 我能解釋 layer / recipe / task / metadata 四者關係
- [ ] 我知道 `.bb` 跟 `.bbappend` 差異
- [ ] 我知道 `:append` / `:prepend` / override 語法
- [ ] 我能 trace 一個 recipe 的 task 順序
- [ ] 我能找到 poky 裡的 gcc recipe

下一章深入 recipe syntax —— `.bb` / `.bbappend` / `.bbclass` 三者怎麼組合。

→ [Ch 2 `.bb` / `.bbappend` / `.bbclass` 語法](./02-recipe-syntax.md)
