# Ch 1 — Yocto 心法：layer / recipe / task / metadata

> **目標**：建立 Yocto 的概念模型——layer（組織單位）、recipe（build 單位）、task（執行單位）、metadata（黏合的設定）。理解這套核心概念的關係和各自的角色，你讀任何 Yocto repo 都有框架，不再是黑盒子。這是 Yocto 心法的核心——掌握它，後面的 recipe 語法（Ch 2）、toolchain recipe（Ch 4）才有意義。

> **環境**：概念章，搭配 poky（Ch 0）的實際結構觀察。

## 為什麼先建立概念模型？

Ch 0 你 build 了一次，但 Yocto 的「結構」還模糊——一堆 layer、recipe、task、各種設定變數，怎麼組織的？沒有清楚的概念模型，你看 Yocto repo 就像看天書（一堆 .bb、.bbappend、.conf 檔，不知道誰管誰）。

這章建立 Yocto 的**概念模型**——四個核心概念（layer/recipe/task/metadata）的角色和關係。理解它們，你看任何 Yocto repo 都有框架：知道 layer 怎麼組織、recipe 怎麼定義「怎麼建一個元件」、task 是 recipe 裡的執行步驟、metadata（變數）怎麼控制行為。對 compiler 工程師，這讓你能定位「toolchain recipe 在哪個 layer、它的 build task 怎麼跑、怎麼改它的設定」——這是後面 patch GCC（Ch 5）的基礎。

## 先建立直覺:四層概念

```
Yocto 的四個核心概念（由大到小）：

  Layer（層）：組織單位
    一個目錄，含一組相關的 recipe 和設定
    例：meta（核心）、meta-riscv（RISC-V 的）、你的 meta-mycompany
    → layer 讓你「模組化」地組織和擴展（不改別人的，加自己的 layer）
        │
  Recipe（.bb）：build 單位
    描述「怎麼建一個元件」（一個套件/library/kernel）
    例：gcc_13.bb（怎麼建 gcc）、busybox_1.36.bb
    含：source 在哪、怎麼 configure/compile/install、依賴什麼
        │
  Task：執行單位
    一個 recipe 的 build 分成多個 task（步驟）
    do_fetch（下載）→ do_configure → do_compile → do_install → ...
    bitbake 排程和執行這些 task
        │
  Metadata：黏合的設定（變數）
    變數（如 SRC_URI、DEPENDS、PV）控制 recipe/task 的行為
    bitbake 解析 metadata 決定怎麼 build
        │
  → Layer 組織 recipe、recipe 定義 build、task 是執行步驟、metadata 控制
    這四層概念是理解任何 Yocto repo 的框架
```

關鍵心智：Yocto 的四個核心概念——**layer**（組織單位，一個含相關 recipe 的目錄）、**recipe（.bb）**（build 單位，描述怎麼建一個元件）、**task**（執行單位，recipe 的 build 步驟如 do_fetch/do_compile）、**metadata**（變數，控制 recipe/task 的行為）。layer 組織 recipe、recipe 定義 build、task 是步驟、metadata 控制——這是理解任何 Yocto repo 的框架。

## Layer:組織與擴展

```bash
cd ~/yocto/poky
# 看 Yocto 的 layer（poky 自帶幾個）
cat build/conf/bblayers.conf
# BBLAYERS ?= " \
#   .../poky/meta \           ← OpenEmbedded-Core（核心 recipe）
#   .../poky/meta-poky \      ← poky distro 設定
#   .../poky/meta-yocto-bsp \ ← reference BSP
#   "
# → bblayers.conf 列出「啟用哪些 layer」

# 一個 layer 的結構
ls meta/
# conf/                  ← layer 設定（layer.conf）
# recipes-core/          ← 核心 recipe（busybox, glibc...）
# recipes-devtools/      ← 開發工具 recipe（gcc, binutils...）  ← compiler 在這！
# recipes-kernel/        ← kernel recipe
# classes/               ← .bbclass（共用的 build 邏輯）
# ...

# 看 toolchain recipe 在哪（compiler 工程師關心的）
ls meta/recipes-devtools/gcc/
# gcc_13.2.bb          ← gcc 的 recipe（怎麼建 gcc）！
# gcc-cross_13.2.bb    ← cross-compiler 的 recipe（Ch 4）
# gcc-common.inc       ← 共用的設定
# ...
```

```
Layer 的價值：模組化擴展（不改別人的）

  Yocto 的擴展哲學：「加 layer，不改別人的 recipe」
        │
  你要客製（如加你的 patched GCC）：
    不改 meta/ 的 gcc recipe（那是上游的）
    而是加一個你的 layer（meta-mycompany）
    在你的 layer 裡用 .bbappend（Ch 2）擴展 gcc recipe
        │
  → layer 讓多方的客製不衝突：
    上游維護 meta（核心）
    BSP 廠商維護 meta-riscv（硬體）
    你維護 meta-mycompany（你的 patch）
    各自的 layer 疊起來 = 完整的 build
        │
  layer 的優先順序（BBLAYERS 的順序 + LAYERSERIES）
    決定衝突時誰贏（後面的可覆蓋前面的）
```

> **Layer 是 Yocto 的「模組化擴展」機制——加你的 layer 不改別人的 recipe，這是客製化 patched GCC 的正確方式**。**Layer** 是 Yocto 的組織和擴展單位——一個目錄含一組相關的 recipe 和設定。`bblayers.conf` 列出「啟用哪些 layer」。一個 layer 的結構：`conf/`（layer 設定）、`recipes-xxx/`（各類 recipe——`recipes-devtools/gcc/` 是 **gcc 的 recipe，compiler 工程師最關心的**）、`classes/`（共用的 build 邏輯）。**Layer 的核心價值是「模組化擴展」**——Yocto 的哲學是「**加 layer，不改別人的 recipe**」。你要客製（如加你的 patched GCC），**不改** `meta/` 的 gcc recipe（那是上游的，改了會和上游衝突、難維護），而是**加一個你的 layer**（`meta-mycompany`），在裡面用 `.bbappend`（Ch 2）擴展 gcc recipe。這讓**多方的客製不衝突**——上游維護 meta（核心）、BSP 廠商維護 meta-riscv（硬體）、你維護 meta-mycompany（你的 patch），各自的 layer 疊起來組成完整的 build。**layer 的優先順序**（BBLAYERS 順序 + priority）決定衝突時誰贏。對 compiler 工程師，理解 layer 是關鍵——你的 patched GCC 應該放在**你自己的 layer**（用 .bbappend 擴展上游的 gcc recipe），而不是改上游的 recipe。這是 Yocto 客製的正確方式（可維護、不衝突、易追蹤）。Ch 5（patch GCC）會實際做這個——在你的 layer 用 .bbappend 加 patch。

## Recipe:怎麼建一個元件

```bash
# 看一個簡單的 recipe（理解 recipe 的結構）
cat ~/yocto/poky/meta/recipes-core/busybox/busybox_1.36.0.bb | head -30
# （busybox 的 recipe，描述怎麼建 busybox）

# 一個 recipe 的核心元素（後面 Ch 2 詳述語法）：
cat > /tmp/example.bb <<'EOF'
# recipe 的核心元素
SUMMARY = "範例套件"
LICENSE = "MIT"
LIC_FILES_CHKSUM = "file://LICENSE;md5=..."

# source 在哪（怎麼取得）
SRC_URI = "https://example.com/foo-${PV}.tar.gz"
SRC_URI[sha256sum] = "..."

# 版本（PV = Package Version，從檔名推斷或明確設）
# PV 在 foo_1.2.bb 裡是 1.2

# 依賴什麼（build 時需要的其他 recipe）
DEPENDS = "zlib openssl"

# 怎麼 configure/compile/install（用 class 或明確的 task）
inherit autotools          # 用 autotools class（自動處理 ./configure make）
EOF

# → recipe = 「怎麼建這個元件」的完整描述
#   source、版本、依賴、build 步驟
```

```
Recipe 的核心元素：

  SUMMARY/DESCRIPTION  ← 描述
  LICENSE              ← 授權（Yocto 嚴格管授權）
  SRC_URI              ← source 在哪（git/http/local）
  PV (Package Version) ← 版本（常從檔名 foo_1.2.bb 推斷）
  DEPENDS              ← build 依賴（建這個要先有什麼）
  RDEPENDS             ← runtime 依賴（跑這個要有什麼）
  inherit <class>      ← 繼承 build 邏輯（autotools/cmake/...）
  do_xxx               ← 自訂 task（如果 class 不夠）
        │
  → recipe 用這些元素描述「怎麼建一個元件」
    bitbake 讀 recipe，按它建
        │
  對 compiler：gcc recipe 描述「怎麼建 gcc」
    （source、版本、patch、configure 選項、build 步驟）
    你 patch GCC = 改 gcc recipe 的 SRC_URI（加 patch）
```

> **Recipe（.bb）描述「怎麼建一個元件」——source、版本、依賴、build 步驟；compiler 工程師主要動 gcc recipe 的 SRC_URI（加 patch）**。**Recipe**（.bb 檔）是 Yocto 的 build 單位——它**完整描述「怎麼建一個元件」**（一個套件/library/kernel）。核心元素：**SRC_URI**（source 在哪——git/http/本地檔案）、**PV**（Package Version，版本，常從檔名 `foo_1.2.bb` 推斷）、**DEPENDS**（build 依賴——建這個要先有什麼，如 gcc 依賴 binutils）、**RDEPENDS**（runtime 依賴——跑這個要有什麼）、**LICENSE**（授權，Yocto 嚴格管）、**inherit \<class\>**（繼承共用的 build 邏輯，如 autotools class 自動處理 ./configure + make）、**do_xxx**（自訂 task，如果 class 不夠）。bitbake 讀 recipe，按它的描述建元件。對 **compiler 工程師**，**gcc recipe**（`meta/recipes-devtools/gcc/gcc_13.bb`）描述「怎麼建 gcc」——它的 SRC_URI（gcc 的 source + patches）、版本、configure 選項、build 步驟。**你 patch GCC = 改 gcc recipe 的 SRC_URI**（在 SRC_URI 加上你的 patch 檔，bitbake 會在 build 時套用，Ch 5）。所以理解 recipe 的結構（特別是 SRC_URI 和 patch 機制）是 compiler 工程師的核心——你主要動的就是 toolchain recipe 的 SRC_URI（加 patch）和可能的 configure 選項。Ch 2 會深入 recipe 語法，Ch 4 講 toolchain recipe，Ch 5 實際 patch GCC——但現在先理解「recipe 描述怎麼建一個元件，你改它的 SRC_URI 加 patch」。

## Task:build 的執行步驟

```bash
cd ~/yocto/poky/build
# 看一個 recipe 有哪些 task
bitbake -c listtasks busybox 2>/dev/null | grep do_
# do_fetch        下載 source
# do_unpack       解壓
# do_patch        套用 patch
# do_configure    設定（./configure）
# do_compile      編譯（make）
# do_install      安裝到暫存
# do_package      打包
# ...

# 跑單一 task（debug 用）
# bitbake -c compile busybox    # 只跑 do_compile
# bitbake -c clean busybox      # 清掉 busybox 的 build

# task 的順序（依賴關係）：
# do_fetch → do_unpack → do_patch → do_configure → do_compile → do_install → do_package
```

```
Task 是 recipe 的「執行步驟」：

  一個 recipe 的 build 分成多個 task（按順序）：
    do_fetch     下載 source（從 SRC_URI）
    do_unpack    解壓
    do_patch     套用 patch（SRC_URI 裡的 .patch）★ compiler 關心
    do_configure 設定
    do_compile   編譯 ★ compiler patch 的問題常在這
    do_install   安裝到暫存目錄
    do_package   打包成 .deb/.rpm/.ipk
        │
  bitbake 排程 task：
    依賴關係決定順序（gcc 的 do_compile 要先有 binutils）
    sstate-cache 跳過已建好的（Ch 9）
        │
  → task 是「執行單位」，bitbake 排程和執行
    debug 時：看是哪個 task 失敗（do_compile? do_patch?）
    對 compiler：do_patch（套 patch）和 do_compile（編譯）最相關
```

> **Task 是 recipe 的執行步驟（do_fetch→do_patch→do_compile→do_install…）——對 compiler 工程師，do_patch（套 patch）和 do_compile（編譯）最相關**。**Task** 是 recipe 的執行單位——一個 recipe 的 build 分成多個 task，按順序執行：**do_fetch**（從 SRC_URI 下載 source）→ **do_unpack**（解壓）→ **do_patch**（**套用 patch**——SRC_URI 裡的 .patch 檔，**compiler 工程師關心**——你的 GCC patch 在這步被套用）→ **do_configure**（設定）→ **do_compile**（**編譯**——**compiler patch 的問題常在這**，你的 patched GCC 編譯時出錯就是這步失敗）→ **do_install**（安裝到暫存）→ **do_package**（打包）。bitbake **排程 task**——按依賴關係決定順序（gcc 的 do_compile 要先有 binutils）、用 sstate-cache 跳過已建好的（Ch 9）。`bitbake -c <task> <recipe>` 跑單一 task（debug 用，如 `-c compile` 只編譯）。對 **compiler 工程師**，最相關的 task 是 **do_patch**（你的 GCC patch 在這套用——如果 patch 套不上，do_patch 失敗）和 **do_compile**（編譯——你的 patched GCC 編譯 GCC 自己或其他套件時出錯，do_compile 失敗）。所以 debug 時（Ch 0 提的「build fail 看 log」），看是**哪個 task** 失敗——do_patch 失敗 = patch 問題（patch 套不上，可能 source 版本不對）、do_compile 失敗 = 編譯問題（可能是 compiler patch 的 bug）。理解 task 讓你能精確定位 build 問題在哪一步，這是 compiler 工程師 debug Yocto build 的基礎。

## Metadata:控制行為的變數

```bash
# metadata = 變數，控制 build 的行為
cd ~/yocto/poky/build

# 看一個 recipe 的變數值（bitbake -e 展開所有變數）
bitbake -e busybox 2>/dev/null | grep -E '^PV=|^SRC_URI=|^DEPENDS=' | head
# PV="1.36.0"
# SRC_URI="https://busybox.net/downloads/busybox-1.36.0.tar.bz2 ..."
# DEPENDS="..."
# → bitbake -e 顯示「最終展開的變數值」（debug 變數的關鍵）

# 變數的來源（層層覆蓋）：
# 1. recipe（.bb）設的
# 2. .bbappend 覆蓋的（你的 layer）
# 3. .bbclass 設的（繼承的）
# 4. conf（local.conf / layer.conf / machine.conf）設的
# → 最終值是這些「層層疊加/覆蓋」的結果

# 重要的全域變數（在 conf 設）：
# MACHINE：目標機器（qemux86-64 / qemuriscv64 / ...）
# DISTRO：distro 設定
# PREFERRED_VERSION_gcc：用哪個版本的 gcc（Ch 9 的雷）
```

> **Metadata（變數）控制 build 行為，值是「層層覆蓋」的結果——`bitbake -e` 看最終展開的值是 debug 變數的關鍵**。**Metadata** 是控制 build 行為的**變數**——recipe、task 的行為由變數決定（SRC_URI 決定 source、DEPENDS 決定依賴、PV 決定版本）。變數的值是**「層層覆蓋」**的結果——從多個來源疊加：(1) recipe（.bb）設的；(2) **.bbappend 覆蓋**的（你的 layer 擴展，Ch 2）；(3) .bbclass 設的（繼承的共用邏輯）；(4) conf（local.conf/layer.conf/machine.conf）設的全域設定。最終值是這些層層疊加/覆蓋的結果。**`bitbake -e <recipe>`** 顯示「**最終展開的變數值**」——這是 **debug 變數的關鍵**（當你不確定某個變數的最終值是什麼、為什麼，`bitbake -e | grep VAR` 看它的最終值和來源）。重要的全域變數（在 conf 設）：**MACHINE**（目標機器——qemux86-64/qemuriscv64，Ch 3 改成 RISC-V）、**DISTRO**（distro 設定）、**PREFERRED_VERSION_gcc**（用哪個版本的 gcc——Ch 9 的常見雷，多版本時的選擇）。對 compiler 工程師，理解 metadata 讓你能控制 build（如設 PREFERRED_VERSION 選你的 gcc 版本、用 .bbappend 覆蓋 SRC_URI 加 patch）和 debug（`bitbake -e` 看變數為什麼是這個值）。這四個概念（layer/recipe/task/metadata）組成了 Yocto 的完整心法——layer 組織、recipe 定義、task 執行、metadata 控制。理解它們，你看任何 Yocto repo 都有框架，能定位和操作你關心的部分（toolchain recipe）。

## 故意弄壞:用 bitbake -e 追變數

```bash
cd ~/yocto/poky/build
# 用 bitbake -e 理解「一個變數的最終值從哪來」（debug metadata 的核心技能）

# 看 gcc 的版本變數（compiler 工程師關心）
bitbake -e gcc 2>/dev/null | grep -E '^PV=' | head -1
# PV="13.2.0"  ← gcc 的版本

# 看 SRC_URI（source 從哪來，patch 在這）
bitbake -e gcc 2>/dev/null | grep '^SRC_URI=' | head -1
# SRC_URI="...gcc-13.2.0... file://patch1.patch file://patch2.patch..."
# → 看到 gcc 的 source 和已套用的 patch（你加 patch 會出現在這）

# 追變數的來源（-e 還能看「哪個檔案設的」）
bitbake -e gcc 2>/dev/null | grep -B2 'PREFERRED_VERSION'
# 看 PREFERRED_VERSION_gcc 在哪設、值是什麼

# → bitbake -e 是 debug metadata 的核心：
#   「這個變數的最終值是什麼」「從哪個檔案來」「為什麼是這個值」
#   對 compiler 工程師：確認 gcc 的版本、SRC_URI（patch 有沒有加進去）
#   是 debug「我的 patch 有沒有生效」的關鍵
```

> **`bitbake -e gcc | grep SRC_URI` 確認「你的 patch 有沒有加進 gcc 的 source」——這是 compiler 工程師 debug patch 的核心技能**。`bitbake -e <recipe>` 是 debug metadata 的核心工具——它展開並顯示一個 recipe 的**所有最終變數值**（和來源）。對 compiler 工程師最有用的查詢：(1) **`bitbake -e gcc | grep '^PV='`**（gcc 的版本——確認用的是哪個版本）；(2) **`bitbake -e gcc | grep '^SRC_URI='`**（gcc 的 source 和 patch——**確認你加的 patch 有沒有出現在 SRC_URI**，這是「我的 patch 有沒有生效」的關鍵檢查，Ch 5）；(3) **`bitbake -e gcc | grep PREFERRED_VERSION`**（哪個版本被選，Ch 9 的多版本問題）。這個「用 bitbake -e 追變數」是 Yocto debug 的核心技能——當你不確定「某個變數的最終值、為什麼是這個、你的設定有沒有生效」，`bitbake -e` 給答案（展開的最終值 + 來源檔案）。對 compiler 工程師，這特別重要——你加了 patch 到 gcc recipe，`bitbake -e gcc | grep SRC_URI` 確認 patch 在 SRC_URI 裡（生效了）；你設了 PREFERRED_VERSION，`bitbake -e` 確認選對版本。這把抽象的「metadata 層層覆蓋」變成可檢查的（看最終值）。理解四個概念（layer/recipe/task/metadata）+ 會用 `bitbake -e` 追變數，你就有了 Yocto 的心法和基本的 debug 能力——這是後面所有操作（改 recipe、patch GCC、debug build）的基礎。Ch 2 會深入 recipe 語法（.bb/.bbappend/.bbclass），讓你能實際寫和改 recipe。

## 動手練習

1. 看 layer：看 bblayers.conf（啟用哪些 layer）、探索 meta/ 的結構（recipes-devtools/gcc 在哪）

2. 看 recipe：讀 busybox 或 gcc 的 .bb，找出 SRC_URI/DEPENDS/inherit 等核心元素

3. 看 task：`bitbake -c listtasks <recipe>`，看一個 recipe 有哪些 task（do_fetch→compile→install）

4. 用 bitbake -e：看 gcc 的 PV/SRC_URI（version 和 source/patch），理解 metadata 的最終值

5. 追變數來源：用 `bitbake -e | grep VAR` 追一個變數，理解「層層覆蓋」

## 本章重點整理

- Yocto 四核心概念：layer（組織單位）、recipe（build 單位）、task（執行單位）、metadata（控制變數）
- Layer 是模組化擴展——加你的 layer 不改別人的 recipe（客製 patched GCC 的正確方式，用 .bbappend）
- Recipe（.bb）描述怎麼建一個元件（SRC_URI/PV/DEPENDS/inherit）；compiler 主要動 gcc recipe 的 SRC_URI 加 patch
- Task 是 recipe 的執行步驟（do_fetch→do_patch→do_compile→do_install）；compiler 關心 do_patch 和 do_compile
- Metadata（變數）控制行為，值是層層覆蓋的結果；`bitbake -e` 看最終展開值是 debug 的核心

## 自我檢核

- [ ] 能說出 layer/recipe/task/metadata 各自的角色和關係
- [ ] 理解 layer 的模組化擴展（加 layer 不改別人的，用 .bbappend）
- [ ] 知道 recipe 的核心元素（SRC_URI/DEPENDS/inherit），gcc recipe 在哪
- [ ] 知道 task 的順序，compiler 工程師關心哪些 task
- [ ] 會用 bitbake -e 追變數的最終值（debug metadata）

## 延伸閱讀

### 官方

- **[Yocto Concepts](https://docs.yoctoproject.org/overview-manual/concepts.html)** — Yocto Project
  - **讀哪裡**：layer/recipe/task/metadata 的概念
  - **為什麼值得讀**：本章概念的官方權威

- **[BitBake User Manual](https://docs.yoctoproject.org/bitbake/)** — Yocto Project
  - **讀哪裡**：metadata、task execution
  - **為什麼值得讀**：bitbake（task/metadata）的權威

### 書籍

- **《Embedded Linux Systems with the Yocto Project》— Ch 4-5** — Rudolf J. Streif
  - **讀哪幾章**：Ch 4（metadata）、Ch 5（recipe）
  - **為什麼值得讀**：Yocto 概念的權威書

下一章深入 recipe 語法——.bb（recipe）、.bbappend（擴展）、.bbclass（共用邏輯）的語法。理解這些，你能實際寫和改 recipe（特別是用 .bbappend 擴展 gcc recipe 加 patch）。

→ [Ch 2 .bb / .bbappend / .bbclass 語法](./02-recipe-syntax.md)
