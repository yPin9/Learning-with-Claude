# Ch 7 — Image recipe 與 rootfs 組裝

> **目標**：理解 Yocto 怎麼把一堆 package recipe 組合成可開機的 image——image recipe 怎麼定義（放哪些套件）、rootfs 怎麼組裝、IMAGE_INSTALL/IMAGE_FEATURES 等控制變數、以及怎麼寫/客製 image recipe。理解 image recipe，你知道你的 toolchain 建出的套件怎麼組成最終 image，也能客製出客戶要的 image。

> **環境**：Yocto（poky + meta-riscv，Ch 3）。看和寫 image recipe。

## 為什麼要懂 image recipe？

前面你 build 了 `core-image-minimal`，但它**怎麼決定放哪些套件**？rootfs（根檔案系統）怎麼從一堆編好的套件**組裝**起來？要客製 image（如加你的測試工具、調整內容），你要懂 **image recipe**——它定義「這個 image 包含哪些套件、有哪些功能」。

對 compiler 工程師，理解 image recipe 有兩個用途：(1) 知道你的 toolchain 建出的套件**怎麼組成最終 image**（你 patch 的 gcc 編出的 library/程式進到 rootfs）；(2) 能**客製 image**（如加一個用你的 patched gcc 編的測試程式，驗證 patch 在真實 image 裡的行為）。這章講 image recipe 的結構和怎麼客製——這讓你掌握「從套件到最終 image」這一步。

## 先建立直覺:image 是套件的組裝

```
image recipe = 「這個 image 放哪些套件」的清單

  一堆編好的套件（gcc 編出的 .ipk/.deb/.rpm）：
    busybox、glibc、你的測試程式、各種 library...
        │
  image recipe 定義「選哪些放進 rootfs」：
    IMAGE_INSTALL = "busybox glibc my-app ..."
    IMAGE_FEATURES = "..." （功能集，如 ssh-server）
        │
  rootfs 組裝（do_rootfs task）：
    1. 安裝選定的套件到一個目錄（rootfs）
    2. 解決依賴（套件的 RDEPENDS 也裝）
    3. 設定（建 user、設權限、跑 post-install）
    4. 打包成 image 格式（ext4/squashfs/...）
        │
  → image recipe 是「套件清單 + 組裝設定」
    bitbake 按它選套件、組裝成 rootfs、打包成可開機 image
    你客製 image = 改 IMAGE_INSTALL（加你要的套件）
```

關鍵心智：**image recipe** 是「這個 image 放哪些套件」的清單——用 **IMAGE_INSTALL**（選哪些套件）、**IMAGE_FEATURES**（功能集）定義。**do_rootfs** task 組裝 rootfs（安裝套件、解決依賴、設定、打包成 image 格式）。你客製 image = 改 IMAGE_INSTALL 加你要的套件。

## image recipe 的結構

```bash
cd ~/yocto/poky
# 看 core-image-minimal 的 recipe
cat meta/recipes-core/images/core-image-minimal.bb
# SUMMARY = "A small image just capable of allowing a device to boot."
# IMAGE_INSTALL = "packagegroup-core-boot ${CORE_IMAGE_EXTRA_INSTALL}"
# IMAGE_LINGUAS = " "
# inherit core-image       ← 繼承 core-image class（rootfs 組裝邏輯）
# → image recipe 很簡單：選套件（IMAGE_INSTALL）+ inherit core-image

# image recipe 的核心變數
cat > /tmp/my-image.bb <<'EOF'
SUMMARY = "我的客製 image"
LICENSE = "MIT"

inherit core-image          # 繼承 image 組裝邏輯

# 放哪些套件（核心！）
IMAGE_INSTALL = "packagegroup-core-boot \
                 my-test-app \          # 你的測試程式
                 nano \                 # 加個編輯器
                 "
# 功能集（高層的功能，自動拉相關套件）
IMAGE_FEATURES += "ssh-server-openssh"   # 加 SSH server
# rootfs 大小、檔案系統格式
IMAGE_FSTYPES = "ext4 wic"
EOF

# build 你的 image
# bitbake my-image
```

```
image recipe 的核心變數：

  IMAGE_INSTALL    放哪些套件（最直接，列套件名）
        │
  IMAGE_FEATURES   功能集（高層，如 ssh-server/debug-tweaks）
                   一個 feature 自動拉相關套件
        │
  IMAGE_FSTYPES    image 的檔案系統格式（ext4/squashfs/wic）
        │
  IMAGE_ROOTFS_SIZE / EXTRA_SPACE  rootfs 大小
        │
  inherit core-image  繼承組裝邏輯（do_rootfs）
        │
  → image recipe = 選套件（IMAGE_INSTALL/FEATURES）+ 格式 + inherit
    客製 image 主要改 IMAGE_INSTALL（加套件）
```

> **image recipe 用 IMAGE_INSTALL（選套件）+ IMAGE_FEATURES（功能集）+ inherit core-image（組裝邏輯）——客製 image 主要改 IMAGE_INSTALL 加套件**。image recipe 的結構很簡單（看 core-image-minimal.bb）——核心是**選套件 + 繼承組裝邏輯**。核心變數：**IMAGE_INSTALL**（**放哪些套件**——最直接，列套件名，如 `busybox my-test-app nano`）；**IMAGE_FEATURES**（**功能集**——高層的功能，如 `ssh-server-openssh`/`debug-tweaks`，一個 feature 自動拉相關套件，比逐個列套件方便）；**IMAGE_FSTYPES**（image 的**檔案系統格式**——ext4/squashfs/wic 等，決定產出的 image 格式）；**IMAGE_ROOTFS_SIZE/EXTRA_SPACE**（rootfs 大小）；**`inherit core-image`**（繼承組裝邏輯——do_rootfs 等 task）。所以 image recipe = **選套件（IMAGE_INSTALL/FEATURES）+ 格式（FSTYPES）+ inherit core-image**。**客製 image 主要改 IMAGE_INSTALL**（加你要的套件）。對 compiler 工程師，這讓你能客製測試 image——如加一個用你的 patched gcc 編的測試程式（`IMAGE_INSTALL += "my-test-app"`），build image 後在裡面測你的 patch（Ch 5 的驗證方法之一——在真實 image 裡測）。IMAGE_FEATURES 的常用值：`debug-tweaks`（開發用，root 無密碼等）、`ssh-server-openssh`（SSH）、`tools-debug`（debug 工具）。理解 image recipe 讓你掌握「從套件到 image」——你的 toolchain 編出的套件，透過 IMAGE_INSTALL 選進 image，do_rootfs 組裝成最終的可開機 image。

## rootfs 組裝過程

```bash
cd ~/yocto/poky/build
# 看 image 的 rootfs 組裝（do_rootfs task）
bitbake -c listtasks core-image-minimal 2>/dev/null | grep -E 'rootfs|image'
# do_rootfs       組裝 rootfs（安裝套件）
# do_image        產生 image 檔
# do_image_ext4   產生 ext4 格式
# ...

# rootfs 的組裝過程（do_rootfs 做的）：
# 1. 解析 IMAGE_INSTALL + 依賴（要裝哪些套件）
# 2. 從 package feed 安裝套件到 rootfs 目錄
# 3. 跑 post-install scripts（設定）
# 4. 設定 users/groups/權限
# 5. 最佳化（移除不需要的、strip binary）

# 看組裝好的 rootfs（image 解開）
ls tmp/work/*/core-image-minimal/*/rootfs/ 2>/dev/null
# bin/ etc/ lib/ usr/ ...   ← 標準的 Linux rootfs

# 看最終 image
ls tmp/deploy/images/qemuriscv64/
# core-image-minimal-qemuriscv64.rootfs.ext4   ← 可開機的 image

# image 怎麼來的（從套件到 image）：
# gcc 編套件 → 套件存到 package feed → do_rootfs 選套件組裝 rootfs
# → do_image 打包成 ext4 → 可 flash/boot
```

> **do_rootfs 組裝 rootfs（解析 IMAGE_INSTALL+依賴 → 安裝套件 → 設定 → 最佳化），do_image 打包成 image 格式——這是「從套件到可開機 image」的最後一步**。**rootfs 組裝**由 **do_rootfs** task 做——過程：(1) 解析 **IMAGE_INSTALL + 依賴**（要裝哪些套件——你選的 + 它們的 RDEPENDS）；(2) 從 **package feed**（之前 build 的套件存放處）**安裝套件**到 rootfs 目錄；(3) 跑 **post-install scripts**（套件的安裝後設定）；(4) 設定 **users/groups/權限**；(5) **最佳化**（移除不需要的、strip binary 減小體積）。然後 **do_image**（和 do_image_ext4 等）把組裝好的 rootfs **打包成 image 格式**（ext4/squashfs/wic）。完整的「**從套件到 image**」鏈：**你的 patched gcc 編套件 → 套件存到 package feed → do_rootfs 選套件組裝 rootfs → do_image 打包成 ext4 → 可 flash/boot**。這讓你看到整個 build 的終點——你 patch 的 gcc（Ch 5）編出的套件，透過 image recipe（IMAGE_INSTALL）選進 image，do_rootfs/do_image 組裝成最終的可開機 image。`tmp/work/*/core-image-minimal/*/rootfs/` 是組裝好的 rootfs（標準 Linux 目錄）、`tmp/deploy/images/` 是最終 image。對 compiler 工程師，理解這個讓你知道「你的 toolchain 改動怎麼影響最終 image」——patched gcc 編的套件進到 rootfs，所以 image 裡的程式/library 是用你的 gcc 編的。這也是 Ch 5 驗證的脈絡——在真實 image 裡測 patched gcc 編出的程式的行為。

## 故意弄壞:套件沒進 image

```bash
cd ~/yocto/poky/build
# 常見問題：你加的套件沒進 image

# 加了 IMAGE_INSTALL += "my-app" 但 image 裡沒有
# 可能原因：

# 1. 套件名錯（recipe 名 vs 套件名不同）
#    recipe my-app_1.0.bb 可能產生套件 my-app + my-app-dev + my-app-dbg
#    IMAGE_INSTALL 要用對的「套件名」（通常是 recipe 名，但有變體）
bitbake -e my-app 2>/dev/null | grep '^PACKAGES='
# PACKAGES="my-app my-app-dev my-app-dbg ..."  ← 看產生哪些套件

# 2. 套件沒被 build（recipe 有問題或沒在 layer）
bitbake my-app    # 先確認套件能單獨 build

# 3. 依賴問題（套件的 RDEPENDS 缺）
# 看 do_rootfs log（套件安裝失敗會在這報）
cat tmp/work/*/core-image-minimal/*/temp/log.do_rootfs | grep -i 'my-app'

# 4. 改了 image recipe 但沒重新 build image
bitbake core-image-minimal    # 重新 build image

# 驗證套件在 image 裡：
# runqemu 後 which my-app  或  解開 rootfs 看
ls tmp/work/*/core-image-minimal/*/rootfs/usr/bin/ | grep my-app

# → 教訓：套件沒進 image 的常見原因
#   套件名錯、套件沒 build、依賴缺、沒重 build image
#   debug：bitbake -e 看 PACKAGES、do_rootfs log、解開 rootfs 確認
```

> **套件沒進 image 的常見原因：套件名錯、套件沒 build、依賴缺、沒重 build image——用 bitbake -e 看 PACKAGES、do_rootfs log debug**。客製 image 常見的問題——**你加的套件沒進 image**。常見原因：(1) **套件名錯**——recipe 名 vs 套件名可能不同（`my-app_1.0.bb` 可能產生 `my-app` + `my-app-dev` + `my-app-dbg` 等套件，`bitbake -e my-app | grep PACKAGES` 看產生哪些套件，IMAGE_INSTALL 要用對的套件名）；(2) **套件沒被 build**（recipe 有問題或不在啟用的 layer——`bitbake my-app` 先確認能單獨 build）；(3) **依賴問題**（套件的 RDEPENDS 缺——看 `log.do_rootfs` 套件安裝失敗的訊息）；(4) **改了 image recipe 但沒重 build image**（要 `bitbake core-image-minimal` 重建）。**debug 方法**：`bitbake -e my-app | grep PACKAGES`（看套件名）、`log.do_rootfs`（看安裝問題）、解開 rootfs 確認（`ls tmp/work/*/core-image-minimal/*/rootfs/`）。對 compiler 工程師，這在你客製測試 image 時會遇到——加一個測試程式但 image 裡沒有，照這個流程 debug。理解 image recipe 和組裝過程，你能客製 image（加測試程式驗證 patch）和 debug「套件沒進 image」的問題。這章完成了「從套件到 image」的理解——image recipe 選套件、do_rootfs 組裝、debug 套件沒進去。對 compiler 工程師，這讓你能在真實 image 裡驗證 patched toolchain（Ch 5 驗證方法的脈絡），也理解你的 toolchain 改動怎麼影響最終交付的 image。

## 動手練習

1. 看 image recipe：讀 core-image-minimal.bb，理解 IMAGE_INSTALL/inherit core-image

2. 客製 image：寫一個 image recipe（或用 local.conf 的 IMAGE_INSTALL:append）加一個套件（如 nano）

3. 看 rootfs：解開組裝好的 rootfs（tmp/work/*/core-image*/*/rootfs/），看標準 Linux 結構

4. 看組裝：理解 do_rootfs 怎麼從套件組裝 rootfs（看 listtasks 和 log）

5. debug 套件沒進：故意加一個名字錯的套件，看 image 沒有，用 bitbake -e 看 PACKAGES debug

## 本章重點整理

- image recipe 是「放哪些套件」的清單：IMAGE_INSTALL（選套件）+ IMAGE_FEATURES（功能集）+ inherit core-image
- do_rootfs 組裝 rootfs（解析 IMAGE_INSTALL+依賴 → 安裝套件 → 設定 → 最佳化），do_image 打包成格式
- 從套件到 image：gcc 編套件 → package feed → do_rootfs 選套件組裝 → do_image 打包 → 可開機 image
- 客製 image 主要改 IMAGE_INSTALL（加套件）；compiler 工程師用它加測試程式驗證 patch
- 套件沒進 image 常見原因：套件名錯、沒 build、依賴缺、沒重 build——用 bitbake -e PACKAGES/do_rootfs log debug

## 自我檢核

- [ ] 理解 image recipe 怎麼定義 image（IMAGE_INSTALL/FEATURES）
- [ ] 知道 rootfs 怎麼從套件組裝（do_rootfs → do_image）
- [ ] 會客製 image（加套件）
- [ ] 理解「從套件到 image」的完整鏈
- [ ] 能 debug「套件沒進 image」的問題

## 延伸閱讀

### 官方

- **[Yocto Images](https://docs.yoctoproject.org/dev-manual/customizing-images.html)** — Yocto Project
  - **讀哪裡**：customizing images、IMAGE_INSTALL/FEATURES
  - **為什麼值得讀**：image 客製的官方權威

- **[Image Recipes](https://docs.yoctoproject.org/ref-manual/images.html)** — Yocto
  - **讀哪裡**：內建的 image recipe（core-image-* 的差別）
  - **為什麼值得讀**：理解各種內建 image

### 書籍

- **《Embedded Linux Systems with the Yocto Project》— image 章** — Streif
  - **為什麼值得讀**：image 和 rootfs 的權威

下一章看 devtool workflow——Yocto 提供的方便工具，讓 patch/開發/測試的迭代快很多。這是 day-to-day 比手動改 recipe 更方便的方式（Ch 5 的自動化版）。

→ [Ch 8 devtool workflow：該你常用的指令](./08-devtool-workflow.md)
