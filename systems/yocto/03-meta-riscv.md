# Ch 3 — `meta-riscv` layer 解剖

> 目標：走一遍 `meta-riscv` layer 的結構、找出 machine config 跟 RISC-V 專屬 recipe。這是所有 RISC-V Yocto BSP 的起點。

## meta-riscv 是什麼

**meta-riscv** 是 RISC-V International 跟社群維護的 Yocto layer。提供：

- RISC-V machine config (qemuriscv64, HiFive Unmatched, VisionFive 2, ...)
- RISC-V 專屬 recipe 或 bbappend (OpenSBI, U-Boot 等)
- Toolchain tweaks

Repo：<https://github.com/riscv/meta-riscv>

## 取得 meta-riscv

```bash
cd /path/to/yocto-workspace
git clone https://github.com/riscv/meta-riscv
```

Branch 跟 poky 配對：

```bash
cd meta-riscv
git checkout kirkstone   # 對應 poky 的 kirkstone
```

加到 `bblayers.conf`：

```
BBLAYERS = " \
    /path/to/poky/meta \
    /path/to/poky/meta-poky \
    /path/to/poky/meta-yocto-bsp \
    /path/to/meta-riscv \
"
```

## 結構總覽

```
meta-riscv/
├── conf/
│   ├── layer.conf                       ← layer metadata
│   └── machine/
│       ├── qemuriscv64.conf              ← QEMU target
│       ├── qemuriscv32.conf
│       ├── freedom-u540.conf             ← SiFive HiFive Unleashed
│       ├── beaglev.conf
│       ├── nezha.conf                    ← Allwinner D1
│       ├── unmatched.conf                ← SiFive HiFive Unmatched
│       ├── visionfive2.conf              ← StarFive VisionFive 2
│       ├── licheepi4a.conf               ← T-Head
│       └── include/
│           ├── riscv-base.inc            ← shared across RISC-V machines
│           ├── riscv64-base.inc
│           └── ...
├── recipes-bsp/                          ← 板特定 BSP (u-boot, opensbi)
├── recipes-kernel/                       ← kernel patches
├── recipes-core/                         ← core adjustments
├── classes/
└── dynamic-layers/                       ← 條件啟用 layer
```

## 第一個看：`conf/machine/qemuriscv64.conf`

```
#@TYPE: Machine
#@NAME: qemuriscv64
#@DESCRIPTION: QEMU 64-bit RISC-V machine

require conf/machine/include/riscv/tune-riscv.inc

DEFAULTTUNE ?= "riscv64"

IMAGE_FSTYPES += "ext4 wic.qcow2"

SERIAL_CONSOLES ?= "115200;ttyS0"

PREFERRED_PROVIDER_virtual/kernel ?= "linux-yocto"

QB_SYSTEM_NAME = "qemu-system-riscv64"
QB_MACHINE = "-machine virt"
QB_CPU = "-cpu rv64"
QB_SMP = "-smp 4"
QB_MEM = "-m 512M"
QB_DEFAULT_FSTYPE = "ext4"
QB_KERNEL_CMDLINE_APPEND = "earlycon=sbi console=ttyS0 rw"
```

解讀：

- `require ...riscv-base.inc`：include RISC-V 共通 config
- `DEFAULTTUNE`：CPU tune (rv64gc etc.)
- `IMAGE_FSTYPES`：產哪種 image format
- `PREFERRED_PROVIDER_virtual/kernel`：選 linux-yocto
- `QB_*`：qemu 啟動參數（給 `runqemu` 用）

## `tune-riscv.inc`：CPU tune

```
# meta-riscv/conf/machine/include/riscv/tune-riscv.inc

# Base definitions
...

# Tune definitions
AVAILTUNES += "riscv64 riscv32 riscv64nf riscv32nf"
TUNE_FEATURES:tune-riscv64 = "riscv64"
TUNE_FEATURES:tune-riscv64nf = "riscv64 nf"        # no float

# Packages
PACKAGE_EXTRA_ARCHS:tune-riscv64 = "riscv64"
PACKAGE_EXTRA_ARCHS:tune-riscv64nf = "riscv64 riscv64nf"

# gcc 的 flag
TUNEVALID[riscv64] = "Enable 64-bit RISC-V optimizations"
TUNEVALID[riscv32] = "Enable 32-bit RISC-V optimizations"
TUNEVALID[nf] = "Disable floating-point"
```

**TUNE_FEATURES** 讓 recipe 知道目標 CPU。compiler 的 recipe 會根據 tune features 決定 `-march` / `-mabi`。

## 實際 board：HiFive Unmatched

```
# conf/machine/unmatched.conf

require conf/machine/include/riscv/tune-riscv.inc

DEFAULTTUNE ?= "riscv64"

MACHINE_FEATURES = "rtc sdio ethernet usbhost pci"

PREFERRED_PROVIDER_virtual/kernel ?= "linux-yocto"
PREFERRED_VERSION_linux-yocto ?= "5.15%"

IMAGE_FSTYPES += "wic"

WKS_FILE = "unmatched-hifive.wks"

# bootloader
EXTRA_IMAGEDEPENDS += "u-boot opensbi"
```

`WKS_FILE`: kickstart-style 檔案描述 image layout（SD card / eMMC partition）。

## `recipes-bsp/`：U-Boot、OpenSBI

```
meta-riscv/recipes-bsp/
├── u-boot/
│   ├── u-boot_%.bbappend
│   ├── u-boot-common-riscv.inc
│   └── files/
│       └── ...patches for U-Boot on RISC-V
└── opensbi/
    ├── opensbi_%.bbappend
    └── files/
```

這些 `.bbappend` 把 RISC-V 特定 patch 加到 upstream U-Boot / OpenSBI recipe。

## `recipes-kernel/`

```
meta-riscv/recipes-kernel/
└── linux/
    ├── linux-yocto_%.bbappend
    └── files/
        └── riscv-defconfig.patch
```

Linux kernel 的 RISC-V 配置。

## `dynamic-layers/`：條件 layer

```
meta-riscv/dynamic-layers/
├── openembedded-layer/        ← 若 meta-openembedded 存在，啟用
├── virtualization-layer/
└── ...
```

只有 user 有對應 layer 時才起作用。避免 hard dependency。

## Machine override

因為 `MACHINE = "qemuriscv64"`，Yocto 會看到 override:

```
OVERRIDES = "...qemuriscv64:riscv64:..."
```

Recipe 可以對 machine 特化：

```
# 某個 recipe 裡
CFLAGS:qemuriscv64 = "-O2 -march=rv64gc"
```

## 加你公司 layer 的典型結構

你 SiFive 內部 layer 可能長這樣：

```
meta-sifive/
├── conf/
│   ├── layer.conf
│   ├── machine/
│   │   ├── sifive-p670-dev-board.conf
│   │   └── include/
│   │       └── tune-sifive.inc           ← 自家 tune
│   └── distro/
│       └── sifive-linux.conf             ← custom distro
├── recipes-devtools/
│   └── gcc/
│       └── gcc_%.bbappend                ← 加 SiFive 自家 patch
├── recipes-kernel/
└── recipes-bsp/
```

## 走訪一個 recipe：linux-yocto bbappend

```bash
cat meta-riscv/recipes-kernel/linux/linux-yocto_%.bbappend
```

```
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

SRC_URI:append = " \
    file://riscv-defconfig.patch \
"

COMPATIBLE_MACHINE = "(qemuriscv64|qemuriscv32|unmatched|...|visionfive2)"
```

`COMPATIBLE_MACHINE`：regex match，這個 bbappend 只對這些 machine 生效。

## Kernel defconfig

Linux kernel 的 `.config` 選 which feature / driver 有。RISC-V machine 的 bbappend 通常加 `defconfig.patch` 覆蓋 Yocto default 給 RISC-V 合適 config。

Yocto 的 kernel 用 `meta/recipes-kernel/linux/files/yocto-*.cfg` fragment system 拼。meta-riscv 加自己 fragment。

## u-boot config for RISC-V

```bash
cat meta-riscv/recipes-bsp/u-boot/u-boot_%.bbappend
```

```
FILESEXTRAPATHS:prepend := "${THISDIR}/files:"

UBOOT_CONFIG:qemuriscv64 = "qemu"
UBOOT_CONFIG[qemu] = "qemu-riscv64_smode_defconfig,,u-boot.bin"
```

`UBOOT_CONFIG` 選 which defconfig。對應 U-Boot source 的 `configs/qemu-riscv64_smode_defconfig`。

## 實際 build 一個 RISC-V image

假設改 MACHINE 成 `qemuriscv64`：

```bash
cd build
echo 'MACHINE = "qemuriscv64"' >> conf/local.conf
bitbake core-image-minimal
```

bitbake：

1. Parse all recipes
2. Filter COMPATIBLE_MACHINE
3. For qemuriscv64: linux-yocto, u-boot, opensbi 用 RISC-V config
4. GCC/glibc 用 RISC-V cross version
5. Build rootfs with RISC-V binaries

## 結果

```
tmp/deploy/images/qemuriscv64/
    core-image-minimal-qemuriscv64.ext4
    fw_jump.elf                     ← OpenSBI
    u-boot.bin                        ← U-Boot
    Image                             ← Kernel
    ...
```

這些合起來組成 bootable image。

## Debug: recipe 哪個 layer 出的

```bash
bitbake-layers show-appends
```

印每個 recipe 的 bbappend 來自哪。

```bash
bitbake-layers show-recipes gcc
```

印 gcc recipe 的 version + 來源。

## Contribute to meta-riscv

meta-riscv 歡迎 PR。加新 board：

1. Fork repo
2. 加 `conf/machine/newboard.conf`
3. 加必要 recipes
4. Build + test
5. Send PR

典型 submission process。

## 常見坑

1. **COMPATIBLE_MACHINE 沒 include 你 machine**：bbappend 不 effect。
2. **Default kernel 版本跟 machine 不 match**：用 PREFERRED_VERSION.
3. **TUNE_FEATURES 跟 march 不一致**：build fail。
4. **meta-riscv 的 branch 跟 poky 不同步**：recipe 格式不兼容。
5. **bblayers 順序錯**：priority 衝突、recipe 被 wrong version override。

## 動手練習

1. 讀 `meta-riscv/conf/machine/qemuriscv64.conf` 跟 `unmatched.conf`，對比差異。
2. 用 `bitbake-layers show-appends | grep linux-yocto` 看 linux-yocto 的 bbappend。
3. 改 `MACHINE = "qemuriscv64"` 跑 `bitbake core-image-minimal`、runqemu 驗證。
4. 讀 `recipes-bsp/u-boot/u-boot_%.bbappend` 跟 `recipes-bsp/opensbi/`，看 RISC-V 改了什麼。
5. 找 `meta-sifive` 或 `meta-starfive`（其他 vendor layer），看它們如何 customize。

## 自我檢核

- [ ] 我知道 meta-riscv 的 layer 結構
- [ ] 我能讀 `qemuriscv64.conf` 跟 `tune-riscv.inc`
- [ ] 我能 build RISC-V image 並用 QEMU run
- [ ] 我知道 COMPATIBLE_MACHINE 的作用
- [ ] 我知道 meta-riscv / meta-sifive / meta-starfive 的分工

下一章深入 toolchain recipe — gcc-cross / binutils-cross 怎麼 build。

→ [Ch 4 Toolchain recipe：gcc-cross / binutils-cross / glibc](./04-toolchain-recipes.md)
