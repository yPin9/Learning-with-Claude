# Ch 6 — SDK vs eSDK：給客戶 dev env

> **目標**：理解 Yocto SDK 和 Extensible SDK（eSDK）的差異、各自的用途（給客戶開發環境，含你的 patched toolchain）、何時用哪個、怎麼 build 和 package。這是 BSP vendor（如 SiFive）交付給客戶的主要產物之一——客戶不一定要跑整個 Yocto，可以用 SDK 開發應用。理解 SDK 讓你能把你的 patched toolchain 包成客戶能用的開發環境。

> **環境**：Yocto（poky + meta-riscv，Ch 3）。build SDK。

## 為什麼需要 SDK？

客戶拿到 SiFive 的 chip 和 BSP，要**開發應用程式**（在 RISC-V 上跑的軟體）。但客戶不一定想跑整個 Yocto（複雜、吃資源、build 慢）——他們只想要一個**開發環境**：能 cross-compile RISC-V 應用的 toolchain（含你的 patched GCC）+ 必要的 library 和 header。

**SDK（Software Development Kit）** 就是這個——Yocto 能把 toolchain + sysroot（library/header）打包成一個**獨立的 SDK**，客戶安裝後就能 cross-compile RISC-V 應用，不用跑 Yocto。對 compiler 工程師，SDK 是你交付 patched toolchain 給客戶的方式之一——把你的 patched GCC 包進 SDK，客戶用這個 SDK 開發就用到你的 toolchain。這章講 SDK vs eSDK 的差別、怎麼 build、和它在交付鏈的角色。

## 先建立直覺:打包的開發環境

```
SDK = 打包好的「cross 開發環境」（給客戶）

  客戶要開發 RISC-V 應用，但不想跑整個 Yocto：
        │
  SDK 提供：
    - cross-compiler（你的 patched GCC，編 RISC-V）
    - sysroot（target 的 library + header，連結用）
    - 環境設定腳本（設好 CC/CXX/sysroot 等）
        │
  客戶用 SDK：
    1. 安裝 SDK（一個 self-extracting 的 .sh）
    2. source 環境設定腳本（設好 cross 環境）
    3. 直接 cross-compile（$CC myapp.c → RISC-V binary）
    → 不用跑 Yocto，就能開發 RISC-V 應用
        │
  → SDK = 把 Yocto 的 toolchain + sysroot 打包成獨立環境
    客戶裝了就能 cross-compile（含你的 patched toolchain）
    這是 BSP vendor 交付給客戶的主要產物之一
```

關鍵心智：**SDK** 是「打包好的 cross 開發環境」——含 cross-compiler（你的 patched GCC）+ sysroot（target 的 library/header）+ 環境設定腳本。客戶安裝 SDK + source 環境腳本，就能直接 cross-compile RISC-V 應用，**不用跑整個 Yocto**。這是 BSP vendor 交付給客戶的主要產物。

## SDK vs eSDK

```
SDK vs eSDK（兩種開發環境，不同用途）：

  SDK（標準 SDK）：
    內容：cross-compiler + sysroot + 環境腳本
    用途：開發「應用程式」（cross-compile app）
    客戶：應用開發者（寫 RISC-V app，不碰 Yocto）
    特點：固定的（toolchain + library 都定死）
        │
  eSDK（Extensible SDK）：
    內容：SDK 的一切 + devtool + 部分 Yocto 能力
    用途：開發應用 + 「修改/加 recipe」（更接近 Yocto）
    客戶：要客製 image/加套件的（不只寫 app）
    特點：可擴展（能用 devtool 加 recipe、改 image）
        │
  → SDK：給「只寫 app」的客戶（簡單、固定）
    eSDK：給「要客製 image」的客戶（含 devtool，可擴展）
        │
  對 compiler 工程師：
    兩者都含你的 patched toolchain
    SDK 給應用開發者用你的 gcc 編 app
    eSDK 給進階客戶（能改 recipe、加套件）
```

> **SDK（固定的 app 開發環境）vs eSDK（含 devtool，可擴展，能改 recipe）——兩者都含你的 patched toolchain，給不同需求的客戶**。Yocto 有兩種開發環境：**SDK（標準 SDK）**——含 **cross-compiler + sysroot + 環境腳本**，用途是**開發應用程式**（cross-compile app），給「只寫 RISC-V app、不碰 Yocto」的客戶，特點是**固定的**（toolchain + library 都定死）；**eSDK（Extensible SDK）**——含 SDK 的一切 + **devtool**（Ch 8）+ 部分 Yocto 能力，用途是開發應用 + **修改/加 recipe**（更接近 Yocto），給「要客製 image/加套件」的進階客戶，特點是**可擴展**（能用 devtool 加 recipe、改 image）。**選擇**：**SDK** 給「只寫 app」的客戶（簡單、固定）、**eSDK** 給「要客製 image」的客戶（含 devtool，可擴展）。對 **compiler 工程師**，**兩者都含你的 patched toolchain**——SDK 讓應用開發者用你的 gcc 編 app、eSDK 讓進階客戶能改 recipe（含用你的 toolchain）。所以你交付 patched toolchain 給客戶，可以透過 SDK（給應用開發者）或 eSDK（給要客製的）。理解兩者的差別讓你知道「給客戶哪種開發環境」——多數應用開發者用 SDK（簡單夠用），要客製 BSP 的進階客戶用 eSDK（更強但複雜）。這是 BSP vendor 交付策略的一部分——SiFive 給客戶 chip + BSP + SDK/eSDK（含 patched toolchain），客戶用它開發。

## build 並使用 SDK

```bash
cd ~/yocto/poky/build
# === build SDK（給某個 image 的開發環境）===
# 方法 1：為一個 image build SDK
bitbake core-image-minimal -c populate_sdk
# 產生 SDK installer（self-extracting .sh）
ls tmp/deploy/sdk/
# poky-glibc-x86_64-core-image-minimal-riscv64-...-toolchain-....sh
# → 這是給客戶的 SDK installer（含 cross gcc + sysroot）

# === build eSDK ===
bitbake core-image-minimal -c populate_sdk_ext
# 產生 eSDK installer（含 devtool）

# === 客戶端：安裝並使用 SDK ===
# 1. 安裝（執行 .sh，選安裝目錄）
# ./poky-glibc-x86_64-...-toolchain-....sh
# 安裝到 /opt/poky/... （預設）

# 2. source 環境設定（設好 cross 環境）
# source /opt/poky/.../environment-setup-riscv64-poky-linux
# 這會設好：CC、CXX、CFLAGS、SYSROOT 等（指向你的 patched gcc）

# 3. cross-compile RISC-V 應用
# $CC hello.c -o hello          # 用 SDK 的 gcc（你 patched 的）編
# file hello                    # ELF ... RISC-V ... ← RISC-V binary！
# → 客戶不用 Yocto，用 SDK 就能 cross-compile（用你的 toolchain）

# 驗證 SDK 的 gcc 是你 patched 的
# $CC --version                 # 看版本
# 測你的 patch 修的 bug         # 確認 SDK 的 gcc 含你的 patch
```

```
SDK 的環境設定腳本（environment-setup-*）做什麼：

  source 它之後，設好這些變數：
    CC = riscv64-poky-linux-gcc ...   ← cross gcc（你 patched 的）
    CXX = riscv64-poky-linux-g++ ...
    CFLAGS = ... --sysroot=...         ← 指向 sysroot
    SYSROOT = .../riscv64-poky-linux   ← target 的 library/header
        │
  → 之後 $CC 就是 cross-compiler（編 RISC-V）
    $CC hello.c 自動用對的 sysroot、flag
    客戶不用懂 cross-compile 的細節（SDK 都設好了）
```

> **`bitbake <image> -c populate_sdk` build SDK，客戶 source environment-setup 腳本後 `$CC` 就是你的 patched cross gcc——這是交付 toolchain 的方式**。build SDK：**`bitbake <image> -c populate_sdk`**（標準 SDK）或 **`-c populate_sdk_ext`**（eSDK）——產生 **self-extracting 的 installer**（`.sh`，在 `tmp/deploy/sdk/`），含 cross gcc + sysroot。**客戶端使用**：(1) **安裝**（執行 .sh，裝到 /opt/poky/）；(2) **source 環境設定腳本**（`environment-setup-riscv64-poky-linux`——設好 `CC`/`CXX`/`CFLAGS`/`SYSROOT` 等，**指向你的 patched gcc 和 sysroot**）；(3) **cross-compile**（`$CC hello.c -o hello` → RISC-V binary，`file` 確認是 RISC-V）。客戶**不用 Yocto，用 SDK 就能 cross-compile**（用你的 toolchain）。**環境設定腳本**做的事——設好 `CC`（= 你 patched 的 cross gcc）、`CFLAGS`（含 `--sysroot` 指向 target 的 library/header）、`SYSROOT`——之後 `$CC` 就是 cross-compiler，客戶不用懂 cross-compile 的細節（SDK 都設好了）。**驗證 SDK 的 gcc 是你 patched 的**——`$CC --version` 看版本、測你 patch 修的 bug（確認 SDK 含你的 patch）。對 compiler 工程師，這是交付 patched toolchain 的方式之一——你 patch GCC（Ch 5）→ build 含 patched gcc 的 SDK → 交給客戶 → 客戶用 SDK 開發就用到你的 toolchain。SDK 讓客戶不用碰 Yocto 的複雜性（直接 cross-compile），同時用到你的 patched toolchain。這是 BSP vendor 的標準交付——chip + BSP + SDK（含 patched toolchain）。

## 故意弄壞:SDK 沒含 patch 的問題

```bash
cd ~/yocto/poky/build
# 常見問題：SDK 沒含你的 patch（patch 沒進到 SDK 的 gcc）

# 原因：SDK 用的 gcc 變體可能和你 patch 的不同
# 你 patch 了 gcc（影響 gcc-cross），但 SDK 用 gcc-cross-canadian（Ch 4）
# → 如果你的 patch 只針對某變體，SDK 的 gcc 可能沒含

# 驗證 SDK 的 gcc 有沒有你的 patch：
# 1. 確認 patch 加在「共用 source」（影響所有變體，Ch 4）
bitbake -e gcc-crosssdk 2>/dev/null | grep 'my-gcc-fix.patch'
# 看 SDK 用的 gcc 變體（crosssdk/cross-canadian）有沒有你的 patch

# 2. 如果沒有，patch 要加在共用 source 層級（gcc-source 或 gcc 的共用 inc）
#    而非只針對 gcc-cross
# → SRC_URI:append 加在 gcc recipe（共用的）影響所有變體
#    包括 SDK 用的 cross-canadian/crosssdk

# 3. rebuild SDK
bitbake core-image-minimal -c populate_sdk

# → 教訓：patch 要影響「SDK 用的 gcc 變體」
#   gcc 的變體多（cross/cross-canadian/crosssdk，Ch 4）
#   patch 加在共用 source（影響所有變體）最保險
#   否則 SDK 的 gcc 可能沒含你的 patch（你以為交付了 patched toolchain
#   但客戶用 SDK 編出來沒含 patch）
```

> **SDK 沒含 patch 的問題：SDK 用的 gcc 變體（cross-canadian/crosssdk）和你 patch 的不同——patch 要加在共用 source 影響所有變體**。一個隱蔽的問題——**SDK 沒含你的 patch**。原因（呼應 Ch 4 的變體）：你 patch 了 gcc（可能針對 gcc-cross），但 **SDK 用不同的 gcc 變體**（**gcc-cross-canadian/gcc-crosssdk**——SDK 專用的 gcc 變體，Ch 4）。如果你的 patch 只針對某個變體（如只 gcc-cross），**SDK 的 gcc 可能沒含你的 patch**——你以為交付了 patched toolchain，但客戶用 SDK 編出來的東西**沒含 patch**（最隱蔽的問題——build 都成功，但 SDK 的 gcc 不是 patched 的）。**驗證**：`bitbake -e gcc-crosssdk | grep my-gcc-fix.patch`（看 SDK 用的 gcc 變體有沒有你的 patch）。**解法**：patch 加在**共用 source 層級**（gcc 的共用 .inc 或 gcc-source，Ch 4——影響**所有** gcc 變體，包括 SDK 用的 cross-canadian/crosssdk），而非只針對 gcc-cross。`SRC_URI:append` 加在 gcc recipe（共用的部分）會影響所有變體（因為各變體 require 共用的 inc）。**教訓**：patch 要影響「SDK 用的 gcc 變體」——gcc 的變體多（cross/cross-canadian/crosssdk），**patch 加在共用 source（影響所有變體）最保險**。對 compiler 工程師，這個認知很重要——你交付 patched toolchain 給客戶（透過 SDK），要**確認 SDK 的 gcc 真的含你的 patch**（驗證 SDK 用的變體有 patch）。這呼應 Ch 5 的「驗證每一步」+ Ch 4 的「變體」——patch 要影響對的變體（含 SDK 用的）。理解 SDK 和變體的關係，你交付的 SDK 才真的含你的 patched toolchain（不會「以為交付了但 SDK 沒含」）。這章完成了「給客戶開發環境」的理解——SDK/eSDK 怎麼打包你的 patched toolchain 給客戶，以及確認 patch 真的進到 SDK。

## 動手練習

1. build SDK：`bitbake core-image-minimal -c populate_sdk`，看產生的 SDK installer

2. 用 SDK：安裝 SDK、source 環境腳本、cross-compile 一個 hello.c（RISC-V binary）

3. SDK vs eSDK：build eSDK（populate_sdk_ext），理解它多了 devtool

4. 驗證 patch 在 SDK：確認 SDK 的 gcc 含你的 patch（測 patch 修的行為）

5. 變體問題：理解 SDK 用的 gcc 變體（crosssdk），patch 要加共用 source 才會進 SDK

## 本章重點整理

- SDK 是打包的 cross 開發環境（cross gcc + sysroot + 環境腳本），客戶不用跑 Yocto 就能 cross-compile
- SDK（固定，給只寫 app 的客戶）vs eSDK（含 devtool，可擴展，給要客製 image 的客戶）
- build：bitbake <image> -c populate_sdk（SDK）/ -c populate_sdk_ext（eSDK）；產生 self-extracting installer
- 客戶用：安裝 → source environment-setup → $CC 就是你的 patched cross gcc
- SDK 沒含 patch 的問題：SDK 用不同 gcc 變體（crosssdk）—patch 要加共用 source 影響所有變體

## 自我檢核

- [ ] 理解 SDK 是什麼，為什麼客戶用它（不用跑 Yocto）
- [ ] 知道 SDK vs eSDK 的差別和各自用途
- [ ] 會 build SDK 並用它 cross-compile
- [ ] 知道怎麼確認 SDK 含你的 patched toolchain
- [ ] 理解 SDK 的 gcc 變體問題（patch 要加共用 source）

## 延伸閱讀

### 官方

- **[Yocto SDK Manual](https://docs.yoctoproject.org/sdk-manual/index.html)** — Yocto Project
  - **讀哪裡**：SDK vs eSDK、build 和使用
  - **為什麼值得讀**：SDK 的官方權威

- **[Application Development with eSDK](https://docs.yoctoproject.org/sdk-manual/extensible.html)** — Yocto
  - **讀哪裡**：eSDK 的 devtool 工作流
  - **為什麼值得讀**：eSDK 的進階用法

### 書籍

- **《Embedded Linux Systems with the Yocto Project》— SDK 章** — Streif
  - **為什麼值得讀**：SDK 在開發流程的角色

下一章看 image recipe 與 rootfs 組裝——怎麼定義一個 image（放哪些套件）、rootfs 怎麼組裝起來。理解 image recipe，你知道你的 toolchain 建出的套件怎麼組成最終的 image。

→ [Ch 7 Image recipe 與 rootfs 組裝](./07-image-recipe.md)
