# Ch 0 — 環境搭建：poky + bitbake 第一次 build

> 目標：裝好 Yocto 環境、用 poky（reference distribution）跑一次 `bitbake core-image-minimal`、體會 Yocto 的 build size 跟時間。

## 硬體需求

Yocto 非常 resource-intensive：

- **Disk**：50 GB+（build cache + downloads + artifacts）
- **RAM**：8 GB 可以、16 GB+ 舒適
- **CPU**：多核最好（`bitbake` 高度 parallelize）
- **OS**：Ubuntu 22.04+ / Fedora 最穩。macOS / Windows **不行**（Yocto host 必須 Linux）

如果 dev 機器不夠：

- 雲 VM（AWS c5.2xlarge 之類）
- 或 WSL2（實測可以但較慢）

## 安裝 build dependencies

Ubuntu 22.04/24.04：

```bash
sudo apt update
sudo apt install -y \
    gawk wget git diffstat unzip texinfo gcc build-essential \
    chrpath socat cpio python3 python3-pip python3-pexpect \
    xz-utils debianutils iputils-ping python3-git python3-jinja2 \
    python3-subunit zstd liblz4-tool file locales
    
sudo locale-gen en_US.UTF-8
```

這是 Yocto 官方 dependency list（不同版本小差）。

## 下載 poky

**poky** 是 Yocto 的 reference distribution（非 production 用，但是 build 必備）：

```bash
git clone git://git.yoctoproject.org/poky
cd poky
git checkout kirkstone         # LTS 穩定版 (2022 release, 2026 仍支援)
```

Yocto 常見 branch：

- `kirkstone` (2022-04, LTS) ← 本課主力
- `langdale` (2022-10)
- `mickledore` (2023-04)
- `nanbield` (2023-10)
- `scarthgap` (2024-04, 最新 LTS)

LTS branch 穩、建議 production 用。

## 啟動 build environment

```bash
source oe-init-build-env
```

這個 script 會：

- 進 `build/` directory
- 設環境變數（`BBPATH` 等）
- 建 `conf/local.conf` / `conf/bblayers.conf` if 不存在

之後所有 `bitbake` 指令在此 directory。

## 看預設 config

```bash
cat conf/local.conf | grep -v '^#' | grep -v '^$'
```

關鍵：

```
MACHINE ?= "qemux86-64"       # 預設 target
DISTRO ?= "poky"
SSTATE_DIR = "${TOPDIR}/sstate-cache"
DL_DIR = "${TOPDIR}/downloads"
```

對 RISC-V，改：

```bash
# 編輯 conf/local.conf
MACHINE ?= "qemuriscv64"
```

## 第一次 build：core-image-minimal

```bash
bitbake core-image-minimal
```

**第一次 build 超慢 —— 預計 1-4 小時**。正常。

背後做的事：

1. Download source (kernel, busybox, glibc, gcc, ...) — 幾 GB
2. Build cross-toolchain (gcc, binutils, glibc for target)
3. Build target packages (using the cross-toolchain)
4. Assemble rootfs image

Output：

```
tmp/deploy/images/qemuriscv64/
    core-image-minimal-qemuriscv64.rootfs.ext4
    fw_jump.elf
    u-boot.bin
    ...
```

## 驗證：跑起來 image

Yocto 提供 `runqemu` script：

```bash
runqemu qemuriscv64
```

應該開機進 Linux prompt：

```
Poky (Yocto Project Reference Distro) 4.0.x qemuriscv64 ttySIF0
qemuriscv64 login: root
```

`root` 登入（無 password）。你有 RISC-V Linux 了！

`Ctrl+A X` 結束 QEMU（或 `shutdown -h now`）。

## 第二次 build 快很多

```bash
bitbake core-image-minimal
```

第二次：30 秒到幾分鐘。因為 **sstate-cache** (shared state) 保存了中間產物。

sstate-cache 是 Yocto 效能關鍵：

- 改一 recipe → 只 rebuild 它 + depend 的
- 沒改 → 從 cache 直接拿

## Build 產物 location map

```
build/
├── conf/                        ← 你的 config
│   ├── local.conf
│   └── bblayers.conf
├── downloads/                    ← upstream source tarball cache
├── sstate-cache/                 ← build output cache
├── tmp/                          ← 本地 build 產物
│   ├── deploy/
│   │   └── images/               ← final output (bootable images)
│   ├── work/                     ← per-recipe 的 working dir
│   │   └── riscv64-poky-linux/
│   │       ├── gcc/              ← GCC build 的中繼
│   │       ├── glibc/
│   │       └── ...
│   └── log/                      ← bitbake log
└── cache/                        ← bitbake parse cache
```

**compiler 工程師最常看 `tmp/work/.../gcc/` 跟 `tmp/log/`**。

## `tmp/work/.../gcc/`：recipe 的 sausage

```bash
cd tmp/work/riscv64-poky-linux/gcc/11.2-r0
ls
# source/      從 source tarball / git 解出來
# build/       compiler build 的 working dir
# image/       準備 install 的 files
# temp/        log、script
# ...
```

**這是你 debug 時看的**：build 失敗看這裡、產出 binary 也在這裡。

## 其他必知指令

```bash
# Parse + show recipe info (不 build)
bitbake-layers show-recipes gcc

# Show layer list
bitbake-layers show-layers

# Parse all recipes (find errors)
bitbake -p

# Clean a specific recipe
bitbake -c clean gcc
bitbake -c cleansstate gcc    # 連 sstate 都清

# Build single task
bitbake -c fetch gcc          # 只跑 fetch
bitbake -c unpack gcc         # 只 unpack source
bitbake -c compile gcc

# Force rebuild
bitbake -f -c compile gcc
```

`-c <task>` 是 bitbake 強大之處：可以精細控制 recipe 的生命週期各階段。

## Task 簡介

每個 recipe 有一系列 task：

```
do_fetch       → download source
do_unpack       → extract
do_patch        → apply patches
do_configure    → ./configure
do_compile      → make
do_install      → make install to staging
do_package      → package into deb/rpm/ipk
do_rootfs       → assemble rootfs
```

每個 task 一個 shell/python script（在 recipe 裡定義）。bitbake 決定 order 跑哪個。

## 第一次的 log 讀法

build fail 時：

```bash
# 看哪條 task fail
tail tmp/log/cooker/qemuriscv64/console-latest.log

# 看 recipe 自己的 log
ls tmp/work/riscv64-poky-linux/gcc/11.2-r0/temp/log.do_compile
cat tmp/work/riscv64-poky-linux/gcc/11.2-r0/temp/log.do_compile
```

每 task 有 own log。`log.do_compile` 最常看（跟 GCC build error 對應）。

## 硬碟空間警告

第一次 build 完 `tmp/` 可能 20+ GB、`downloads/` 5+ GB、`sstate-cache/` 5+ GB。

週期性清理：

```bash
# 刪 tmp 但保留 cache（重 build 快）
rm -rf tmp/

# 全清（包括 cache）
rm -rf tmp/ sstate-cache/ downloads/
```

## Common first-build error

### Error 1: disk full

```
ERROR: No space left on device
```

解：擴大 disk 或清 tmp。

### Error 2: network issue during fetch

某 source tarball 下載失敗、URL 變了。解：加 mirror 或 retry。

### Error 3: checksum mismatch

source tarball 跟 recipe 裡的 `SRC_URI[sha256sum]` 不一致。解：確認 source 來源對、或更新 recipe 的 checksum。

### Error 4: "Missing build dependency"

host 系統缺某 binary。解：照 error 提示 `sudo apt install xxx`。

## 常見誤會

1. **「Yocto 就是 distro」**：不。Yocto 是 **build system**。產出的 image 是 distro。poky 是 Yocto 的 **reference distro**。
2. **「Yocto 就是 bitbake」**：bitbake 是 build tool、Yocto 是整個 project。但兩詞常互換。
3. **「bitbake 一次改 recipe 立刻生效」**：要 `-c cleansstate` 才完全重 build，否則 cache 可能被 reuse。
4. **「Yocto 只給嵌入式」**：90% 是嵌入式，但也有 server/edge 用的 case。
5. **「不用懂 bash」**：Yocto 大量 shell + Python。熟練就好。

## 動手練習

1. 裝好 Yocto、build `core-image-minimal` for qemuriscv64。
2. 用 `runqemu` 跑起來、登入。
3. `ls tmp/work/riscv64-poky-linux/` 看有哪些 package。
4. 找 gcc recipe：`find . -name "gcc_*.bb"`。
5. 用 `bitbake -c clean ncurses` 清一個 package、再 build。觀察只 rebuild 它。

## 自我檢核

- [ ] 我裝好 Yocto dependency + clone poky
- [ ] 我完成第一次 `bitbake core-image-minimal`
- [ ] 我 boot image in QEMU 成功
- [ ] 我知道 `tmp/work/`, `tmp/log/`, `sstate-cache/` 的作用
- [ ] 我能用 `-c <task>` 精細控制 recipe 某階段

下一章進入 Yocto 的 mental model —— layer / recipe / task。

→ [Ch 1 Yocto 心法：layer / recipe / task / metadata](./01-yocto-mental-model.md)
