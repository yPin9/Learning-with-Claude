# Ch 0 — 環境搭建：poky + bitbake 第一次 build

> **目標**：裝好 Yocto 環境、用 poky（reference distribution）跑一次 `bitbake core-image-minimal`、體會 Yocto 的 build size 和時間、理解這次 build「實際在做什麼」（從 source 編譯整個 Linux distro）。讀完你有一個能 build 的 Yocto 環境，以及對「Yocto 到底在幹嘛」的第一手體會——這是後面所有章節的基礎。

> **環境**：Linux（Ubuntu 22.04 / Debian 12 等 Yocto 支援的 host）。**需要 30+ GB 磁碟、8+ GB RAM**——建議專屬 VM 或雲端 instance。

## 為什麼第一件事是「真的 build 一次」？

Yocto 很容易被當成黑盒子——你看一堆 recipe、layer、bitbake 命令，但不知道它們**實際在做什麼**。最好的破除方法是**真的 build 一次**——跑 `bitbake core-image-minimal`，等它（2-4 小時），看它做了什麼。這一次 build 會讓你體會：Yocto 是「從 source 編譯整個 Linux distro」（不是下載現成的 binary）、它有很多步驟和 dependencies、為什麼吃這麼多磁碟和時間。

對 compiler 工程師，這個體會特別重要——你要懂「客戶 build Yocto image 時，你的 patched GCC 在哪一步被用、build break 時是哪一步」。沒有 build 過一次，你對 Yocto 的理解就停在概念。這章帶你 build 第一次（忍耐那 2-4 小時，你會學到很多），這是後面所有章節的基礎。

## 先建立直覺:Yocto 是「從 source 建整個 distro」

```
Yocto 是什麼（vs 一般 Linux distro）：

  一般 distro（Ubuntu/Debian）：
    你下載「現成的 binary」（apt install）
    別人（distro 維護者）已經編譯好了
        │
  Yocto：
    你「從 source 編譯整個 distro」（bitbake）
    從 toolchain（gcc/binutils）→ libc → 各種套件 → kernel → rootfs
    全部自己編（針對你的目標硬體/需求客製）
        │
  為什麼嵌入式用 Yocto（從 source 建）：
    1. 客製化（只放需要的、針對特定硬體優化）
    2. 跨平台編譯（在 x86 host 編譯 RISC-V/ARM 的 binary）
    3. 可重現（同樣的 recipe 建出同樣的 image）
    4. 完整掌控（從 toolchain 到 rootfs 都自己定）
        │
  → Yocto = 「建造一個專屬 Linux distro 的工廠」
    輸入：recipe（怎麼建每個元件）
    輸出：rootfs + kernel image（能 flash 進裝置）
    對 compiler 工程師：你的 patched GCC 是這個工廠的一個「機台」
```

關鍵心智：Yocto 是「**從 source 編譯整個 Linux distro**」——不像 Ubuntu 下載現成 binary，Yocto 從 toolchain（gcc/binutils）→ libc → 套件 → kernel → rootfs 全部自己編（針對目標硬體客製）。它是「建造專屬 Linux distro 的工廠」，輸入 recipe、輸出 image。對 compiler 工程師，你的 patched GCC 是這個工廠的一個元件。

## 安裝 Yocto 環境

```bash
# === Host 套件（Yocto build 需要的）===
sudo apt update
sudo apt install -y \
    gawk wget git diffstat unzip texinfo gcc build-essential \
    chrpath socat cpio python3 python3-pip python3-pexpect \
    xz-utils debianutils iputils-ping python3-git python3-jinja2 \
    libegl1-mesa libsdl1.2-dev python3-subunit mesa-common-dev \
    zstd liblz4-tool file locales

# 設 locale（Yocto 需要 UTF-8）
sudo locale-gen en_US.UTF-8

# === 確認磁碟和記憶體（Yocto 吃資源！）===
df -h ~                          # 要 30+ GB 可用
free -h                          # 要 8+ GB RAM（建議 16GB）
# 如果不夠，用雲端 instance 或加大 VM

# === 取得 poky（Yocto 的 reference distribution）===
mkdir -p ~/yocto && cd ~/yocto
git clone git://git.yoctoproject.org/poky -b scarthgap   # scarthgap = LTS 版本
cd poky
# poky 包含：bitbake（build engine）+ meta（核心 recipe）+ meta-poky（distro 設定）
ls
# bitbake/  meta/  meta-poky/  meta-yocto-bsp/  oe-init-build-env  ...
```

```
poky 的組成（Yocto 的 reference）：

  poky/
    bitbake/        ← build engine（解析 recipe、排程 task、執行）
    meta/           ← OpenEmbedded-Core（核心 recipe：gcc/glibc/各種套件）
    meta-poky/      ← poky distro 的設定
    meta-yocto-bsp/ ← reference BSP（board support package）
    oe-init-build-env  ← 初始化 build 環境的腳本
        │
  → poky = bitbake + 核心 recipe + 設定
    是學 Yocto 的起點（不要從零建，從 poky 改）
    對 RISC-V，之後加 meta-riscv layer（Ch 3）
```

> **Yocto 從 poky（reference distribution）開始，不要從零建——poky = bitbake（build engine）+ meta（核心 recipe）+ 設定**。Yocto 的學習起點是 **poky**——它是 Yocto 的「reference distribution」，包含：**bitbake**（build engine——解析 recipe、排程 task、執行 build）、**meta**（OpenEmbedded-Core，核心 recipe——gcc、glibc、各種套件的 recipe）、**meta-poky**（distro 設定）、**meta-yocto-bsp**（reference BSP）。**不要從零建 layer**（太難，是 Yocto maintainer 的工作）——從 poky 開始，加 layer（如 meta-riscv，Ch 3）、改 recipe。注意 Yocto 的**版本**（release）——用 **LTS 版本**（如 scarthgap、kirkstone——長期支援，穩定）而非最新的開發版（變動快）。安裝要注意**資源**——Yocto build 吃 **30+ GB 磁碟、8+ GB RAM**（建議 16GB），所以用專屬 VM 或雲端 instance（別在主力機器上 build，會佔滿資源）。host 套件也要裝齊（Yocto 的 build 需要一堆工具）。對 compiler 工程師，理解 poky 的組成讓你知道「核心 recipe（含 gcc）在 meta/ 裡」「bitbake 是執行 build 的引擎」——這是後面理解 toolchain recipe（Ch 4）和 patch GCC（Ch 5）的基礎。

## 第一次 build:core-image-minimal

```bash
# === 初始化 build 環境 ===
cd ~/yocto/poky
source oe-init-build-env       # 建立並進入 build/ 目錄，設好環境
# 這會：建立 build/ 目錄、設好 PATH、進入 build/
# 之後在 build/ 裡跑 bitbake

# 看 build 設定（conf/local.conf）
cat conf/local.conf | grep -E '^MACHINE|^DL_DIR|^SSTATE' | head
# MACHINE ??= "qemux86-64"      ← 目標機器（預設 qemu x86-64）
# （之後改成 RISC-V，Ch 3）

# === 第一次 build（忍耐 2-4 小時！）===
bitbake core-image-minimal
# core-image-minimal = 最小的可開機 image（rootfs + kernel）
# bitbake 會：
#   1. 解析所有 recipe（要建什麼、依賴關係）
#   2. 從 source 編譯：toolchain → libc → busybox → kernel → ...
#   3. 組裝成 rootfs + kernel image
# 第一次很久（編譯整個 distro），之後有 sstate-cache 快很多（Ch 9）

# build 完，看產出
ls tmp/deploy/images/qemux86-64/
# core-image-minimal-qemux86-64.rootfs.ext4   ← rootfs
# bzImage                                      ← kernel
# ...

# === 跑起來（QEMU）===
runqemu qemux86-64 nographic
# 開機進一個最小的 Linux！（你從 source 建的）
# login: root（無密碼）
# 退出 QEMU: Ctrl-A 然後 X
```

> **`bitbake core-image-minimal` 從 source 編譯整個最小 Linux distro——第一次 2-4 小時，這個等待讓你體會「Yocto 在做什麼」**。第一次 build 的流程：`source oe-init-build-env`（初始化 build 環境，進入 build/ 目錄）→ `bitbake core-image-minimal`（build 最小的可開機 image）。bitbake 做的事：(1) **解析所有 recipe**（要建什麼、依賴關係——構成一個 task 圖）；(2) **從 source 編譯**——toolchain（gcc/binutils）→ libc（glibc）→ busybox → kernel → 各種元件；(3) **組裝成 rootfs + kernel image**。**第一次很久（2-4 小時）**——因為它**真的從 source 編譯整個 distro**（包括 gcc 自己！Yocto 要先建一個 cross-compiler 才能編目標的東西）。這個等待是值得的——它讓你體會「Yocto 不是下載 binary，是從 source 建一切」（為什麼這麼久、吃這麼多資源）。**之後的 build 快很多**——因為有 **sstate-cache**（shared state cache，Ch 9——快取已建好的元件，不用重建）。build 完，`tmp/deploy/images/` 有 rootfs 和 kernel，`runqemu` 能跑起來（一個你從 source 建的最小 Linux）。對 compiler 工程師，這次 build 讓你看到「gcc 在 build 流程的哪一步被建和使用」——你的 patched GCC 會替換這個流程裡的 gcc（Ch 5）。記住：**第一次 build 慢是正常的（編譯整個 distro），忍耐它，你會學到 Yocto 的本質**。

## build 過程觀察

```bash
# build 時（或 build 後）觀察 Yocto 在做什麼
cd ~/yocto/poky/build

# 看 build 的進度（bitbake 顯示正在建哪些 recipe）
# Currently building: gcc-cross, glibc, busybox, linux-yocto...

# build 的目錄結構
ls tmp/
# work/      ← 每個 recipe 的 build 目錄（source + 編譯結果）
# deploy/    ← 最終產出（images、packages）
# sstate-control/  ← sstate-cache 的控制
# ...

# 看某個 recipe 的 work 目錄（如 gcc）
ls tmp/work/*/gcc-cross*/        # gcc-cross 的 build（toolchain！Ch 4）
# 裡面有：source（git/解壓的）、編譯的中間檔、log

# 看 build log（debug 用，Ch 9）
ls tmp/work/*/gcc*/*/temp/
# log.do_compile  log.do_configure  ...   ← 每個 task 的 log

# 磁碟用量（Yocto 吃很多）
du -sh tmp/        # 可能 20-40 GB
du -sh sstate-cache/   # sstate-cache 也佔空間
```

> **Yocto build 的產物在 `tmp/`——`work/`（每個 recipe 的 build）、`deploy/`（最終 image）、log（debug 用）——這是後面 debug 的地圖**。build 過程和產物在 **`tmp/`**：**`work/`**（每個 recipe 的 build 目錄——source、編譯中間檔、結果。如 `tmp/work/*/gcc-cross*/` 是 gcc cross-compiler 的 build，Ch 4 的 toolchain）；**`deploy/`**（最終產出——images、packages）；**`sstate-control/`**（sstate-cache 控制）。每個 recipe 的 task 有 **log**（`tmp/work/*/recipe*/*/temp/log.do_compile` 等）——這是 **debug build fail 的關鍵**（Ch 9——build 失敗時看對應 task 的 log 找原因）。對 compiler 工程師，這個目錄結構是 debug 的地圖——「客戶說 Yocto build 失敗」，你去 `tmp/work/*/gcc*/temp/log.do_compile` 看 gcc 的編譯 log，判斷「是 compiler patch 的問題還是 recipe 的問題」（這正是你的工作，Ch 5/9）。注意 Yocto 吃大量磁碟（`tmp/` 可能 20-40 GB，sstate-cache 也佔空間）——所以用專屬 VM/雲端，定期清理（`bitbake -c clean` 或刪 tmp/）。理解 build 的產物和 log 位置，你後面 debug 和改 recipe 就有方向。第一次 build 完，花點時間探索 `tmp/`——看 gcc 的 work 目錄、看 log、看 deploy 的 image——這建立了「Yocto build 的具體樣貌」的理解。

## 故意弄壞:看一個 build fail 的 log

```bash
cd ~/yocto/poky/build
# 體會「build fail 時怎麼找 log」（Ch 9 的預習，這是 compiler 工程師的核心）

# 製造一個 build error（改一個 recipe 引入錯誤，安全測試）
# 例：故意給一個不存在的 source URL（會 fetch 失敗）
# 或：用一個會編譯失敗的 patch

# 當 bitbake 報錯：
# ERROR: recipe-name failed
# ERROR: Logfile of failure stored in: .../temp/log.do_compile.12345
# → bitbake 直接告訴你「失敗的 log 在哪」

# 看那個 log
# cat tmp/work/*/recipe*/*/temp/log.do_compile
# → 裡面是實際的編譯輸出和錯誤訊息

# 對 compiler 工程師的關鍵技能：
# build fail 時，判斷「是哪一步、什麼原因」：
#   - do_fetch 失敗 → source 下載問題（URL/網路）
#   - do_configure 失敗 → 設定問題
#   - do_compile 失敗 → 編譯錯誤（這裡常是 compiler patch 的問題！）
#   - do_install 失敗 → 安裝問題
# → 你的工作：看 log，判斷「是 compiler patch 問題還是 recipe 問題」
```

> **build fail 時 bitbake 告訴你「log 在哪」——看對應 task 的 log 判斷「哪一步、什麼原因」是 compiler 工程師的核心技能**。Yocto build 失敗時，bitbake 直接告訴你 **「失敗的 log 在哪」**（`Logfile of failure stored in: .../temp/log.do_xxx`）——看那個 log 就知道實際的錯誤。關鍵是判斷「**是哪一步失敗**」（每個 recipe 有多個 task）：**do_fetch**（下載 source）失敗 → source 問題（URL/網路）；**do_configure** 失敗 → 設定問題；**do_compile** 失敗 → **編譯錯誤**（這裡**常是 compiler patch 的問題**！你的 patched GCC 編譯某個套件失敗）；**do_install** 失敗 → 安裝問題。**這正是 compiler 工程師在 Yocto 的核心工作**（README 強調的）——客戶說「Yocto build 失敗」，你要**判斷「是 compiler patch 問題還是 Yocto recipe 問題」**：看 do_compile 的 log——如果是你的 patched GCC 編譯時 crash 或產生錯誤的 code，是 compiler 問題（你要修 patch）；如果是 recipe 的設定/依賴錯，是 Yocto 問題（recipe 工程師修）。這個「看 log、定位是哪一步、判斷是 compiler 還 recipe 問題」是你在 Yocto 的主要技能。Ch 9（常見雷）會深入 debug，但現在先建立這個認知——**build fail 時看 log、定位 task、判斷問題歸屬**。這也是為什麼要 build 一次（體會 build 流程和 log 的位置），你才能在真實的 build fail 時知道去哪找、怎麼判斷。第一次 build 成功後，可以故意製造一個 error（如改壞一個 recipe）體會 debug 流程——這是 Ch 9 的預習。

## 動手練習

1. 裝環境：裝齊 host 套件、clone poky、確認磁碟/記憶體夠

2. 第一次 build：`bitbake core-image-minimal`（忍耐 2-4 小時），體會 Yocto 從 source 建 distro

3. 跑起來：`runqemu` 跑你建的 image，登入看看（一個你從 source 建的 Linux）

4. 探索 tmp/：看 work/（gcc 的 build 目錄）、deploy/（image）、log，理解 build 產物

5. 看 log：找一個 recipe 的 do_compile log，理解「build 的每一步有 log」（Ch 9 預習）

## 本章重點整理

- Yocto 是「從 source 編譯整個 Linux distro」（不像 Ubuntu 下載 binary）——建造專屬 distro 的工廠
- 從 poky（reference distribution）開始：bitbake（build engine）+ meta（核心 recipe）+ 設定；用 LTS 版本
- 第一次 build（bitbake core-image-minimal）2-4 小時——真的編譯整個 distro（含 gcc 自己）；之後 sstate-cache 快
- build 產物在 tmp/：work/（每個 recipe 的 build）、deploy/（image）、log（debug 用）
- build fail 時看對應 task 的 log（do_fetch/configure/compile/install），判斷「是 compiler patch 還 recipe 問題」

## 自我檢核

- [ ] 環境裝好，能 build core-image-minimal 並用 runqemu 跑
- [ ] 理解 Yocto 是「從 source 建整個 distro」，為什麼嵌入式用它
- [ ] 知道 poky 的組成（bitbake/meta/設定）
- [ ] 知道 build 產物在 tmp/（work/deploy/log）
- [ ] 知道 build fail 時看 log、定位 task、判斷問題歸屬（compiler 工程師的核心）

## 延伸閱讀

### 官方

- **[Yocto Quick Build](https://docs.yoctoproject.org/brief-yoctoprojectqs/index.html)** — Yocto Project
  - **讀哪裡**：整個 quick build（第一次 build 的官方教學）
  - **為什麼值得讀**：Yocto 入門的權威，本章的官方版

- **[Yocto Mega-Manual](https://docs.yoctoproject.org/)** — Yocto Project
  - **讀哪裡**：Overview、Getting Started（後面章節會指向特定部分）
  - **為什麼值得讀**：Yocto 的完整文件（巨大，當參考查）

### 書籍

- **《Embedded Linux Systems with the Yocto Project》— Ch 1-3** — Rudolf J. Streif
  - **讀哪幾章**：Ch 1-3（Yocto 概念、第一次 build）
  - **這本書的定位**：Yocto 的權威書
  - **前提**：本章

下一章建立 Yocto 的心法——layer/recipe/task/metadata 的核心概念。理解這些，你看 Yocto 就不是黑盒子，而是有結構的 build 系統。

→ [Ch 1 Yocto 心法：layer / recipe / task / metadata](./01-yocto-mental-model.md)
