# Ch 7 — Image recipe 與 rootfs 組裝

> 目標：理解 Yocto 如何把一堆 package recipe 組合成可 boot 的 image。學會寫 image recipe、選 package、調整 rootfs 內容。

## Image 是什麼

**Image recipe** 是特殊 recipe，它的 `do_rootfs` task 把多個 package 組合成 rootfs，再 wrap 成 boot 得起來的 format (ext4、squashfs、wic 等)。

典型 image recipe：

```
# poky/meta/recipes-core/images/core-image-minimal.bb

SUMMARY = "A small image just capable of allowing a device to boot."

IMAGE_INSTALL = "packagegroup-core-boot ${CORE_IMAGE_EXTRA_INSTALL}"

IMAGE_LINGUAS = " "

LICENSE = "MIT"

inherit core-image

IMAGE_ROOTFS_SIZE ?= "8192"
IMAGE_ROOTFS_EXTRA_SPACE:append = "${@bb.utils.contains("DISTRO_FEATURES", "systemd", " + 4096", "" ,d)}"
```

關鍵：

- `IMAGE_INSTALL`：列 package 名 (空格分隔) 決定 rootfs 含什麼
- `inherit core-image`：繼承 core-image.bbclass (image-level 邏輯)
- `IMAGE_ROOTFS_SIZE`：rootfs 大小

## IMAGE_INSTALL：選 package

這是 **image 定義的核心**。列哪些 package 進 rootfs：

```
IMAGE_INSTALL = " \
    packagegroup-core-boot \
    busybox \
    ssh-server-openssh \
    bash \
    nano \
    mycustom-package \
"
```

每個名字對應一個 recipe 的 binary output。

## Package group：一組 package

```
IMAGE_INSTALL += "packagegroup-core-boot"
```

`packagegroup-core-boot` 是「boot 需要的基本 package group」— 定義在 `meta/recipes-core/packagegroups/packagegroup-core-boot.bb`：

```
PACKAGES = "packagegroup-core-boot"
RDEPENDS:packagegroup-core-boot = "\
    base-files \
    base-passwd \
    busybox \
    sysvinit \
    udev \
"
```

Package group recipe 只定義 `RDEPENDS`、install 會 pull 進來。

## Common packagegroups

```
packagegroup-core-boot          基本 boot
packagegroup-core-ssh-openssh   SSH server
packagegroup-core-tools-debug   gdb, strace
packagegroup-core-tools-profile perf, valgrind
packagegroup-core-tools-testapps 測試 binary
packagegroup-core-weston        weston 視窗系統
```

用 `bitbake-layers show-recipes 'packagegroup-*'` 列全部。

## IMAGE_FEATURES：更高階選項

```
# conf/local.conf or image recipe
IMAGE_FEATURES += "ssh-server-openssh debug-tweaks tools-profile"
```

Feature 選項：

```
debug-tweaks          root 無密碼、disable security
read-only-rootfs
ssh-server-dropbear / ssh-server-openssh
tools-debug / tools-profile / tools-sdk
x11 / x11-base
weston / pulseaudio
nfs-server
splash                 開機 logo
```

Feature 是 high-level「要這個能力」、Yocto 自動 install 需要的 package。

## 繼承 core-image.bbclass

`inherit core-image` 帶來：

- `do_rootfs` implementation
- Standard feature handling
- Image conversion 到 ext4 / tar.bz2 等

大多 image recipe 都 inherit 這個。

## IMAGE_FSTYPES：輸出 format

```
IMAGE_FSTYPES = "ext4 tar.bz2 wic"
```

Yocto 會 build 每種 format 一個檔。

Common format：

- `ext4`：rootfs filesystem
- `squashfs`：壓縮 read-only rootfs
- `tar.bz2`：tarball (深度 customize)
- `wic`：partition-aware disk image
- `wic.qcow2`：QEMU disk
- `ubi`：UBI filesystem for flash

## WIC image：partition layout

`wic` format 讓 image 有 partition：

```
my-layout.wks:

# short-description: Create an SD card image
part /boot --source bootimg-partition --active --align 4 --size 64 --fstype=vfat
part / --source rootfs --fstype=ext4 --align 4 --size 256
```

在 local.conf：

```
WKS_FILE = "my-layout.wks"
IMAGE_FSTYPES += "wic wic.bmap"
```

產生 `.wic` 檔可以直接 `dd` 到 SD card。

## 自訂 image recipe

```
# meta-sifive/recipes-core/images/sifive-demo-image.bb

require recipes-core/images/core-image-minimal.bb

SUMMARY = "SiFive demo image for RISC-V boards"

IMAGE_FEATURES += "ssh-server-openssh debug-tweaks"

IMAGE_INSTALL:append = " \
    vim \
    htop \
    iperf3 \
    perf \
    sifive-demo-app \
"

IMAGE_ROOTFS_SIZE = "524288"       # 512 MB
```

Build：

```bash
bitbake sifive-demo-image
```

## Customize rootfs

`ROOTFS_POSTPROCESS_COMMAND` 讓你在 rootfs 組完後執行 script：

```
ROOTFS_POSTPROCESS_COMMAND += "my_custom_setup;"

my_custom_setup() {
    # Add custom script
    echo "SiFive BSP 1.0" > ${IMAGE_ROOTFS}/etc/sifive-version
    chmod 0644 ${IMAGE_ROOTFS}/etc/sifive-version
}
```

`${IMAGE_ROOTFS}` 是 rootfs 組裝的 staging dir。

## Init system 選擇

Yocto 預設 sysvinit。改 systemd：

```
# conf/local.conf
DISTRO_FEATURES:append = " systemd"
DISTRO_FEATURES_BACKFILL_CONSIDERED = "sysvinit"
VIRTUAL-RUNTIME_init_manager = "systemd"
VIRTUAL-RUNTIME_initscripts = "systemd-compat-units"
```

需要 `meta-openembedded/meta-oe` 有 systemd recipe。

## 調整 rootfs size

預設 Yocto 算 minimal + 自動 pad：

```
IMAGE_ROOTFS_SIZE = "8192"           # minimum KB
IMAGE_ROOTFS_EXTRA_SPACE = "2048"    # add KB after content
IMAGE_OVERHEAD_FACTOR = "1.3"        # multiply content size
```

如果 image 太大塞不進 SD card → 砍 package、用 squashfs。

## Small image variants

```
core-image-tiny-initramfs         microscopic
core-image-minimal                basic
core-image-full-cmdline           CLI 工具齊全
core-image-weston                 GUI
```

選對 image 做 starting point、不要 from scratch。

## rootfs_deps: 誰決定 final package list

```
bitbake -g core-image-minimal
```

產生 `pn-depends.dot` 等 depend graph。看清楚 which package 被 install。

## 實戰：為 SiFive dev board 出 BSP image

```
# meta-sifive/recipes-core/images/sifive-bsp-image.bb

require recipes-core/images/core-image-minimal.bb

# Core tools for developers
IMAGE_FEATURES += " \
    ssh-server-openssh \
    debug-tweaks \
    tools-debug \
    tools-profile \
"

# SiFive 特定 package
IMAGE_INSTALL:append = " \
    sifive-uart-utils \
    riscv-perf-tool \
    opensbi \
"

# Documentation
IMAGE_INSTALL:append = " packagegroup-core-docs"

# 512 MB rootfs
IMAGE_ROOTFS_SIZE = "524288"

# 產 wic 可 flash 到 SD
IMAGE_FSTYPES = "ext4 wic.gz"
```

客戶拿到這個 image 就能 flash + boot。

## 驗證 image 內容

```bash
# 解壓檢查
mkdir rootfs
cd rootfs
sudo tar -xpf ../tmp/deploy/images/unmatched/sifive-bsp-image-unmatched.tar.bz2

# 看 package list
ls etc/

# 看 version
cat etc/os-release

# 看 binary
ls bin/
```

## deb / rpm / ipk package

`PACKAGE_CLASSES` 控制：

```
PACKAGE_CLASSES = "package_rpm"        # RedHat style
# 或
PACKAGE_CLASSES = "package_deb"         # Debian style
# 或
PACKAGE_CLASSES = "package_ipk"         # Opkg (embedded)
```

IPK (opkg) 是 embedded 系統常見選擇。每 recipe 產 `.ipk` file、rootfs 用 opkg 裝。

## Runtime dependency resolution

Image build 時 opkg / dpkg / rpm 解 RDEPENDS → 自動拉入缺 dependencies。

```bash
# Check what's installed
du -sh tmp/deploy/ipk/
ls tmp/deploy/ipk/riscv64/
```

每個 `.ipk` 對應一個 runtime package.

## /etc configuration

Yocto 提供多種方式改 `/etc`：

1. **Per-recipe**：recipe 的 `do_install` copy `.conf` file
2. **ROOTFS_POSTPROCESS_COMMAND**：image 的 post-process
3. **bbappend** to existing recipe：加 `file://conf` 到 SRC_URI

對 SiFive BSP，常 customize 的：

- `/etc/motd`：banner
- `/etc/profile`：shell env
- `/etc/opensbi.conf`：SBI 設定
- `/etc/network/`：network

## 網路 config

default Yocto image 用 DHCP。想固定 IP、或 WiFi：加對應 package + config。

`meta-networking` 層有很多網路 package：NetworkManager、hostapd、openvpn 等。

## 動手練習

1. `bitbake core-image-minimal`，解壓 rootfs 看內容、列出 `bin/` 有啥。
2. 寫 `mycompany-image.bb`，含 `ssh` + `htop` + 自選 package。
3. 試 `IMAGE_FEATURES += "debug-tweaks tools-profile"`，觀察 rootfs 大小變化。
4. 寫 `ROOTFS_POSTPROCESS_COMMAND` 在 image `/etc/` 加一個 file。
5. 用 `bitbake -g myimage` 產生 depend graph、看包含的 package。

## 常見坑

1. **IMAGE_INSTALL 跟 PACKAGES 搞混**：IMAGE_INSTALL 是 binary package 名、PACKAGES 是 recipe 產的 package list。
2. **Rootfs 太大**：IMAGE_ROOTFS_SIZE 要大於實際 content。bitbake 報錯告訴你。
3. **Feature 衝突**：`image-feature-xx` 跟 `IMAGE_INSTALL` 都指向相同 package 可能出錯。
4. **rootfs 沒你加的 file**：postprocess script 沒 run 或 path 錯。看 log。
5. **Size 不夠 flash**：用 squashfs 或砍 package。

## 常見誤會

1. **「Image 總是含所有 package」**：只含 IMAGE_INSTALL + dependency。
2. **「改 IMAGE_INSTALL 要重 build toolchain」**：不用。只 rebuild rootfs。
3. **「image recipe 等於 package recipe」**：不。image 不是 package、是 rootfs 組裝單位。
4. **「IMAGE_FEATURES 是必要語法」**：optional。`IMAGE_INSTALL` 也能達成。Features 較 high-level。
5. **「rootfs 大小由 IMAGE_INSTALL 決定」**：也受 feature / postprocess 影響。

## 自我檢核

- [ ] 我能寫自訂 image recipe
- [ ] 我知道 IMAGE_INSTALL / IMAGE_FEATURES / PACKAGE_CLASSES 的分工
- [ ] 我能改 rootfs 內容（加 file、config）
- [ ] 我知道 wic image 的 partition layout
- [ ] 我能 build + verify image 產出

下一章 devtool workflow — 比 bbappend 更便捷的日常工具。

→ [Ch 8 devtool workflow](./08-devtool-workflow.md)
