# Ch 6 — SDK vs eSDK：給客戶 dev env

> 目標：理解 Yocto SDK 跟 Extensible SDK (eSDK) 的差異、何時給客戶用哪個、如何 build 並 package。這是 BSP vendor（例如 SiFive）交付給客戶的主要產物之一。

## SDK 是什麼

**SDK (Software Development Kit)**：一個 self-contained tarball，讓**開發者**在 **host machine** (x86 Linux) 上為 **target** (RISC-V) 開發。

裡面含：

- Cross compiler (gcc-cross-riscv64)
- binutils cross
- glibc headers
- target libraries
- Environment setup script

客戶拿到 SDK → 解壓 → source env → compile for RISC-V。

**不需要**客戶有整個 Yocto environment。非常方便。

## SDK vs eSDK

兩種：

### Standard SDK

- 固定 compiler / library version
- 小 (~200 MB - 1 GB)
- 客戶只能 compile application、不能改 BSP
- 產出 binary 跟 Yocto 版 100% 兼容

### Extensible SDK (eSDK)

- 含 **bitbake + recipes**
- 大 (~2-5 GB)
- 客戶可以：
  - Compile application
  - **新增 recipe / 修 BSP**
  - 使用 devtool workflow
- 更 powerful 但更複雜

**多數客戶用 standard SDK**。需要高級客製才給 eSDK。

## Build standard SDK

```bash
bitbake -c populate_sdk core-image-minimal
```

耗時 10-30 分鐘（相比 image build 的一小部分）。

產物：

```
tmp/deploy/sdk/
    poky-glibc-x86_64-core-image-minimal-riscv64-qemuriscv64-toolchain-4.0.x.sh
```

一個 `.sh` 檔、約 500 MB - 1 GB。

## 客戶用 SDK

```bash
# 1. 解壓（自選 install path）
./poky-glibc-x86_64-....-toolchain-4.0.x.sh

# 2. Source environment
source /opt/poky/4.0.x/environment-setup-riscv64-poky-linux

# 3. Compile
$CC hello.c -o hello

# 或用 make
make CC=$CC

# 4. 把 hello binary 放 target 跑
```

`$CC` 會 expand 成 `riscv64-poky-linux-gcc` with 對的 cross-compile flags。

## SDK 的 environment-setup script

```bash
cat /opt/poky/4.0.x/environment-setup-riscv64-poky-linux
```

```bash
# Partial content
export SDKTARGETSYSROOT=/opt/poky/4.0.x/sysroots/riscv64-poky-linux
export PATH=/opt/poky/4.0.x/sysroots/x86_64-pokysdk-linux/usr/bin:$PATH
export CC="riscv64-poky-linux-gcc -mcpu=generic-rv64 ..."
export CXX="riscv64-poky-linux-g++ ..."
export CPP="..."
export AS="..."
export LD="..."
export ...
```

**$CC 不只是 compiler path、還含所有 cross-compile flag**。不需要客戶手動設 `-march` / `-mabi`。

## eSDK 用法

Build：

```bash
bitbake -c populate_sdk_ext core-image-minimal
```

產物類似 `.sh` 但更大。

Install + use：

```bash
./poky-glibc-x86_64-...-ext-sdk.sh
source /opt/poky-ext/.../environment-setup-...

# 客戶可以
devtool modify somepackage
devtool add newpackage https://github.com/.../new.git
devtool build-image
```

客戶用 devtool 修 BSP、本地 rebuild。

## SiFive 交付產物的典型 lineup

```
SiFive BSP release = 
    - Linux image (kernel + rootfs)
    - SDK / eSDK
    - Documentation
```

客戶 bring up 流程：

1. Flash image 到 board、boot up
2. 解壓 SDK 到 dev machine
3. 用 SDK compile application、deploy 到 board
4. 需要 BSP 調整 → ask for eSDK or Yocto source access

## 改 SDK 的內容

SDK 預設含 target image 用到的 libraries。想加更多（e.g., `opencv` for vision dev）：

```
# conf/local.conf
TOOLCHAIN_TARGET_TASK:append = " opencv opencv-dev"
TOOLCHAIN_HOST_TASK:append = " nativesdk-cmake"

bitbake -c populate_sdk core-image-minimal
```

- `TOOLCHAIN_TARGET_TASK`：SDK 裡 target 的 library
- `TOOLCHAIN_HOST_TASK`：host 的工具

## meta-toolchain

快速 build 只含 toolchain 的 SDK（不從 image 推）：

```bash
bitbake meta-toolchain
```

產純 cross-compiler SDK、沒 target library。用於**「給客戶只 compile、不用 runtime」**的情境。

## SDK 版本管理

每次 BSP release 給客戶 fresh SDK：

```
sifive-bsp-1.0.0/toolchain.sh
sifive-bsp-1.1.0/toolchain.sh    # bumped GCC
sifive-bsp-2.0.0/toolchain.sh    # rewrote BSP
```

Version naming + release note 是 BSP vendor 的 discipline。

## SDK 包含 build ID 驗證

產 SDK 時 Yocto 記下所有 package 的 checksum、client 可以 verify 收到的是 expected version。

## 跨 host 的考量

SDK 對特定 host arch (x86_64 Linux typical)：

- **SDK_ARCH = "x86_64"**：給 x86 Linux host
- **SDK_ARCH = "aarch64"**：給 ARM Mac 或 dev board (2024+ 需求增長)

多 host support 要多份 SDK build。

## Debug SDK issue

Client 報「SDK 裡 gcc 不 work」：

```bash
# Step 1: 在 SDK 解壓目錄
ls /opt/poky/4.0.x/sysroots/x86_64-pokysdk-linux/usr/bin/

# Step 2: Try gcc directly
/opt/poky/4.0.x/sysroots/x86_64-pokysdk-linux/usr/bin/riscv64-poky-linux-gcc hello.c

# Step 3: Check environment-setup script
source environment-setup-riscv64-poky-linux
echo $CC
echo $SDKTARGETSYSROOT
ls $SDKTARGETSYSROOT
```

常見問題：

- SDK 裝在有空格的 path → broken
- client 的 glibc version 舊 → 某些 SDK binary 跑不了
- SDK 解壓後被 `strip` → broken

## License 考量

SDK 含 GPL 程式 → 依 GPL 要提供 source。Yocto 會自動 build `populate_sdk_src` 含 source。

商用 product 多用 eSDK（客戶可自改）避免 redistribute source 問題。

## 動手練習

1. `bitbake -c populate_sdk core-image-minimal` build SDK。
2. 裝到 /opt，source environment script。
3. 用 $CC compile hello world、看 arch 正確 (`file hello`)。
4. 試 `bitbake -c populate_sdk_ext`、看 eSDK 差異。
5. 在 conf/local.conf 加 `TOOLCHAIN_TARGET_TASK:append = " zlib-dev"`、rebuild、verify zlib-dev 在 SDK sysroot。

## 常見誤會

1. **「SDK 永遠比 Yocto 好用」**：看需求。SDK 簡單、Yocto 靈活。
2. **「eSDK 是 Yocto 的替代」**：eSDK 是 Yocto 的 sub-set、不替代 full Yocto for BSP work。
3. **「SDK 跨 host」**：Build 時 SDK_ARCH 固定。multi-host 要多份。
4. **「客戶改 SDK 內檔案會在 rebuild 保留」**：不。SDK 是只讀 distribution、不 persist 修改。eSDK 才能。
5. **「SDK 的 gcc 跟 Yocto 的 gcc-cross 一樣」**：接近但不同 package（crosssdk vs cross）。

## 自我檢核

- [ ] 我能 build SDK + 客戶端 install 用起來
- [ ] 我知道 SDK vs eSDK 的差別
- [ ] 我能加 library 到 SDK 的 sysroot
- [ ] 我能 debug SDK 相關 issue
- [ ] 我知道 SiFive 交付給客戶的典型產物 lineup

下一章進 image recipe 跟 rootfs 組裝。

→ [Ch 7 Image recipe 與 rootfs 組裝](./07-image-recipe.md)
