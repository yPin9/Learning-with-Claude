# Ch 4 — Toolchain recipe：gcc-cross / binutils-cross / glibc

> **目標**：走一遍 toolchain 在 Yocto 的 build flow——binutils-cross → gcc-cross-initial → glibc → gcc-cross 的多階段 bootstrap、為什麼要這樣分階段、各個 toolchain recipe（gcc-cross/binutils-cross/glibc）的角色、以及 cross/native/target 三種變體的差別。這是 compiler 工程師最核心的章節——理解 toolchain 怎麼在 Yocto 裡建，你才知道你的 patch 該改哪個 recipe、影響哪一步。

> **環境**：Yocto（poky + meta-riscv，Ch 3）。看 toolchain recipe + build flow。

## 為什麼 toolchain build 這麼複雜？

Yocto 要 cross-compile（在 x86 host 上編 RISC-V binary），所以要先**建一個 cross-compiler**（能在 x86 跑、產生 RISC-V code 的 gcc）。但這裡有個雞生蛋問題——**要建 gcc 需要 libc，要建 libc 需要 gcc**。Yocto 用**多階段 bootstrap** 解決這個——先建一個陽春的 gcc（不需完整 libc）、用它建 libc、再用 libc 建完整的 gcc。

對 compiler 工程師，理解這個 build flow 至關重要——你的 patched GCC 涉及哪個階段？你的 patch 改 gcc-cross（編譯 target 的 cross-compiler）還是 gcc-runtime（target 上的 gcc）？build flow 哪一步失敗對應什麼問題？這章解剖 toolchain 的 build flow 和各 recipe 的角色——這是你 patch GCC（Ch 5）、debug toolchain 問題的直接基礎，也是 compiler 工程師在 Yocto 最該懂的部分。

## 先建立直覺:雞生蛋的 bootstrap

```
toolchain bootstrap：解決「建 gcc 要 libc、建 libc 要 gcc」

  雞生蛋問題：
    完整的 gcc 需要 libc（編譯時連結）
    但 libc 要用 gcc 編譯
    → 先有誰？
        │
  多階段 bootstrap（分階段打破循環）：
    1. binutils-cross：先建組譯器/連結器（as/ld）
       （不需 libc，最先建）
        │
    2. gcc-cross-initial：建「陽春版 gcc」
       （只能編簡單的 C，不需完整 libc——夠用來編 libc）
        │
    3. glibc：用陽春 gcc 編 libc
       （現在有 libc 了）
        │
    4. gcc-cross：用 libc 建「完整版 gcc」
       （能編需要 libc 的 C/C++ 程式）
        │
  → 分階段：先建不需 libc 的、用它建 libc、再建完整 gcc
    這是所有 cross-toolchain 的標準 bootstrap
    （不只 Yocto，手動建 cross-compiler 也這樣）
```

關鍵心智：toolchain bootstrap 解決「建 gcc 要 libc、建 libc 要 gcc」的雞生蛋問題——**多階段**：(1) binutils-cross（組譯器/連結器，最先，不需 libc）→ (2) gcc-cross-initial（陽春 gcc，不需完整 libc，夠編 libc）→ (3) glibc（用陽春 gcc 編 libc）→ (4) gcc-cross（用 libc 建完整 gcc）。分階段打破循環——這是所有 cross-toolchain 的標準 bootstrap。

## toolchain 的 build flow

```bash
cd ~/yocto/poky/build
# 看 toolchain 的 build 依賴（為什麼這個順序）
bitbake -g gcc-cross-riscv64 2>/dev/null   # 產生依賴圖
# 看 task-depends.dot 理解依賴關係

# toolchain 相關的 recipe（在 meta/recipes-devtools/）
ls ~/yocto/poky/meta/recipes-devtools/gcc/
# gcc_13.2.bb              ← gcc 的主 recipe（共用設定）
# gcc-cross_13.2.bb        ← cross-compiler（host 跑、編 target）
# gcc-cross-canadian_13.2.bb  ← SDK 用的（Ch 6）
# gcc-crosssdk_13.2.bb     ← SDK toolchain
# gcc-runtime_13.2.bb      ← target 上的 runtime（libstdc++ 等）
# gcc-source_13.2.bb       ← 共用的 source
# libgcc_13.2.bb           ← libgcc（target 的）
# ...

ls ~/yocto/poky/meta/recipes-core/glibc/
# glibc_2.39.bb            ← glibc 的 recipe
# glibc-initial...         ← bootstrap 階段的 glibc

ls ~/yocto/poky/meta/recipes-devtools/binutils/
# binutils-cross_2.42.bb   ← cross binutils（as/ld）
```

```
toolchain recipe 的家族（理解誰是誰）：

  binutils-cross    組譯器/連結器（as/ld），host 跑、處理 target 的
        │
  gcc-cross         cross-compiler，host 跑、編出 target 的 binary ★ 最核心
        │
  gcc-cross-initial 陽春版（bootstrap 用）
        │
  glibc             target 的 C library
        │
  gcc-runtime       target 上的 runtime（libstdc++/libgcc 等）
        │
  gcc-cross-canadian / crosssdk  SDK 用的（給客戶開發，Ch 6）
        │
  → compiler 工程師最常改：gcc-cross（cross-compiler 本身）
    或 gcc-source（共用 source，patch 加在這影響所有 gcc 變體）
    視 patch 的性質（影響 compiler 本身 vs target runtime）
```

> **gcc-cross 是最核心的 recipe（編 target 的 cross-compiler），gcc-source 是共用 source（patch 加這影響所有 gcc 變體）——compiler 工程師主要改這兩個**。Yocto 的 toolchain recipe 是個家族（在 `meta/recipes-devtools/gcc/` 和 `recipes-core/glibc/`）：**binutils-cross**（組譯器/連結器 as/ld，host 跑、處理 target 的）、**gcc-cross**（**cross-compiler，host 跑、編出 target 的 binary——最核心**）、**gcc-cross-initial**（陽春版，bootstrap 用）、**glibc**（target 的 C library）、**gcc-runtime**（target 上的 runtime——libstdc++/libgcc）、**gcc-cross-canadian/crosssdk**（SDK 用的，給客戶開發，Ch 6）。對 **compiler 工程師**，最常改的是：**gcc-cross**（cross-compiler 本身——如果你的 patch 改變 compiler 的行為/優化/code generation）或 **gcc-source**（共用 source——**patch 加在這會影響所有 gcc 變體**，因為各變體共用同一份 source）。選哪個取決於 patch 的性質：影響 compiler 本身（code gen、優化）→ 改 gcc 的 source（透過 gcc-source 或 gcc 的 .bbappend，影響所有變體）；影響 target runtime（libstdc++）→ 改 gcc-runtime。**多數 compiler patch（GCC bug fix、新優化、新指令支援）是改 gcc 的 source**——所以你的 .bbappend 加 patch 到 gcc recipe（影響 gcc-cross 等所有變體，因為共用 source，Ch 5）。理解這個家族讓你知道「你的 patch 該針對哪個 recipe」「改了影響哪些變體」——這是 patch GCC 的前提。`bitbake -g gcc-cross-riscv64` 能產生依賴圖（看 toolchain build 的依賴關係）。

## cross / native / target 三種變體

```
Yocto 的三種 build 變體（同一個套件可能建多份）：

  native（host 用）：
    在 host（x86）跑的版本
    例：build 過程需要的工具（編 x86 跑的）
        │
  cross（host 跑、產 target）：
    在 host 跑，但「產生 target 的東西」
    例：gcc-cross（x86 跑、編 RISC-V binary）★ compiler 關心
        │
  target（target 上跑）：
    在 target（RISC-V）上跑的版本
    例：gcc-runtime（RISC-V 上的 libstdc++）
        │
  為什麼分這麼多：
    cross-compilation 的本質——build 機器 ≠ 目標機器
    host 要工具（native）、要 cross-compiler（cross）
    target 要 runtime（target）
        │
  override 對應：
    :class-native / :class-cross / :class-target
    （在 recipe/bbappend 針對特定變體，Ch 2 的 override）
        │
  → 你的 patch 影響哪個變體？
    改 compiler 行為 → cross（編 target 的 gcc）
    改 target runtime → target
    用 override（:class-cross 等）針對性套用
```

> **cross（host 跑編 target，gcc-cross）/ native（host 用）/ target（target 上跑）三種變體——你的 patch 用 override（:class-cross 等）針對性套用**。Yocto 因為 cross-compilation（build 機器 x86 ≠ 目標 RISC-V），同一個套件可能建**三種變體**：**native**（在 host x86 跑的版本——build 過程需要的工具）、**cross**（在 host 跑但**產生 target 的東西**——**gcc-cross**：x86 跑、編 RISC-V binary，**compiler 工程師關心的**）、**target**（在 target RISC-V 上跑的版本——gcc-runtime：RISC-V 上的 libstdc++）。為什麼分這麼多——cross-compilation 的本質是「build 機器 ≠ 目標機器」，所以 host 要工具（native）、要 cross-compiler（cross）、target 要 runtime（target）。對應的 **override**（Ch 2）：`:class-native`/`:class-cross`/`:class-target`——在 recipe/bbappend 針對特定變體。對 **compiler 工程師**，理解變體讓你的 patch **針對對的變體**——改 compiler 行為（code gen/優化）→ 針對 **cross**（編 target 的 gcc，`:class-cross` 或影響共用 source）；改 target runtime → 針對 **target**。例如你的 patch 只該影響「編 target 的 cross-compiler」，可以用 `SRC_URI:append:class-target = " file://patch"`（只在 target build 套用——這裡 class-target 指「為 target 編譯的 gcc」，命名有點繞，看 gcc recipe 的慣例）。理解變體和 override，你能精確控制「patch 影響哪個 gcc」——避免 patch 意外影響不該影響的變體（如 native 的 gcc）。這是 patch GCC 的精細之處——多數情況 patch 加到共用 source 影響所有變體就好，但有時要針對特定變體（用 override）。

## 看 gcc recipe 的結構

```bash
cd ~/yocto/poky
# 看 gcc 的主 recipe（理解 toolchain recipe 怎麼寫）
cat meta/recipes-devtools/gcc/gcc-cross_13.2.bb
# require recipes-devtools/gcc/gcc-cross.inc   ← 引入共用設定
# require recipes-devtools/gcc/gcc-${PV}.inc    ← 版本特定設定

# gcc 的 SRC_URI（source 和 patches——你 patch 的地方）
cat meta/recipes-devtools/gcc/gcc-13.2.inc | grep -A30 'SRC_URI'
# SRC_URI = "..gcc source.. \
#            file://0001-xxx.patch \      ← 上游已有的 patches
#            file://0002-yyy.patch \
#            ..."
# → 你加 patch = 在你的 .bbappend 用 SRC_URI:append 加一條（Ch 2/5）

# gcc 的 configure 選項（影響怎麼建 gcc）
bitbake -e gcc-cross-riscv64 2>/dev/null | grep -E 'EXTRA_OECONF' | head -1
# EXTRA_OECONF="--enable-languages=c,c++ --with-arch=rv64gc ..."
# → gcc 的 configure 選項（如啟用哪些語言、target arch）
# 你可能改這個（如加一個 configure 選項）用 EXTRA_OECONF:append

# gcc 的 patch 目錄（上游 patches 放哪）
ls meta/recipes-devtools/gcc/gcc/
# 一堆 0001-xxx.patch ... ← gcc 的 patches（上游維護的）
```

> **gcc recipe 的 SRC_URI（source + patches）和 EXTRA_OECONF（configure 選項）是 compiler 工程師主要動的——加 patch 用 SRC_URI:append、加選項用 EXTRA_OECONF:append**。看 gcc recipe 的結構（理解你要動什麼）：gcc 的主 recipe（如 `gcc-cross_13.2.bb`）用 `require` 引入共用設定（`gcc-cross.inc`、`gcc-${PV}.inc`——版本特定）。關鍵的兩個地方：(1) **SRC_URI**（source + patches——`gcc-13.2.inc` 裡有 gcc 的 source URL + **上游已有的 patches**，如 `0001-xxx.patch`）——**你加 patch = 在你的 .bbappend 用 `SRC_URI:append` 加一條**（Ch 2/5），上游的 patches 在 `meta/recipes-devtools/gcc/gcc/` 目錄；(2) **EXTRA_OECONF**（configure 選項——`--enable-languages=c,c++ --with-arch=rv64gc` 等，控制怎麼建 gcc：啟用哪些語言、target arch、各種 gcc 的 configure 選項）——**你可能改這個**（如加一個 configure 選項啟用某功能）用 `EXTRA_OECONF:append`。這兩個是 compiler 工程師在 Yocto 主要動的——**加 patch（SRC_URI:append）和改 configure 選項（EXTRA_OECONF:append）**。`bitbake -e gcc-cross-riscv64 | grep EXTRA_OECONF` 看當前的 configure 選項（理解 gcc 怎麼被建）。理解 gcc recipe 的結構，你知道「patch 加在 SRC_URI、選項改在 EXTRA_OECONF、用 .bbappend 不改原 recipe」——這是 Ch 5（patch GCC）的直接準備。gcc recipe 看起來複雜（一堆 .inc、變體、patches），但你關心的核心就是 SRC_URI（加 patch）和 EXTRA_OECONF（改選項）——抓住這兩個，你就能 patch GCC 和調整它的 build。

## 故意弄壞:看 toolchain build flow 的某階段

```bash
cd ~/yocto/poky/build
# 觀察 toolchain 的 bootstrap 階段（理解 build flow）

# 看 gcc-cross 的 build 依賴（它依賴什麼，反映 bootstrap 順序）
bitbake -e gcc-cross-riscv64 2>/dev/null | grep '^DEPENDS=' | head -1
# DEPENDS="... binutils-cross... gcc-cross-initial... glibc..."
# → 看到 bootstrap 的依賴：binutils → gcc-initial → glibc → gcc-cross

# 單獨 build 一個 toolchain 階段（觀察）
# bitbake binutils-cross-riscv64    # 階段 1
# bitbake gcc-cross-initial-riscv64 # 階段 2（陽春 gcc）
# bitbake glibc                     # 階段 3
# bitbake gcc-cross-riscv64         # 階段 4（完整 gcc）

# 如果你的 patch 讓某階段失敗，怎麼定位：
# build fail 在 gcc-cross-initial → 你的 patch 在陽春 gcc 階段就出問題
#   （可能是基礎的 compiler bug）
# build fail 在 gcc-cross → 完整 gcc 階段（可能是需要 libc 的部分）
# build fail 在用 gcc-cross 編某個套件 → patch 讓 gcc 產生壞 code
#   （最常見的「compiler patch 問題」——gcc 自己能建但編出來的有問題）

# → 理解 build flow 讓你定位「patch 在哪個階段出問題」
#   這是 compiler 工程師 debug 的關鍵（是 gcc 本身 build fail，
#   還是 gcc 編別的套件時出問題）
```

> **理解 toolchain build flow 讓你定位「patch 在哪個階段出問題」——gcc 自己 build fail vs gcc 編別的套件時出問題，是不同的 debug 方向**。理解 toolchain 的 build flow（bootstrap 階段）對 debug 至關重要——當你的 patch 讓 build 失敗，**定位是哪個階段**：(1) **gcc-cross-initial 失敗**（陽春 gcc 階段）→ 你的 patch 在最基礎的 compiler 就出問題（可能是基礎的 compiler bug，patch 改壞了核心）；(2) **gcc-cross 失敗**（完整 gcc 階段）→ 完整 gcc 的 build 問題（可能是需要 libc 的部分）；(3) **用 gcc-cross 編某個套件時失敗**（如 busybox 的 do_compile）→ **你的 patched GCC 自己能 build，但編別的套件時產生問題**（最常見的「compiler patch 問題」——gcc 本身編譯成功，但它編出來的 code 有 bug，或它在編某個套件時 crash/報錯）。這三種是**不同的 debug 方向**——(1)(2) 是「gcc 自己 build 不起來」（你的 patch 讓 gcc 的 source 編譯失敗，看 gcc 的 do_compile log）、(3) 是「gcc 能用但行為有問題」（你的 patch 改變了 gcc 的行為，導致它編某些 code 出錯，看那個套件的 do_compile log 和你的 patch 改了什麼 code gen）。**這正是 compiler 工程師在 Yocto 的核心 debug**（README 的「diagnose 是 compiler patch 問題還是 recipe 問題」）——理解 build flow 讓你知道「失敗在哪個階段、對應什麼問題、往哪 debug」。Ch 5 會實際 patch GCC，Ch 9 講更多 debug——但現在理解 toolchain build flow（bootstrap 階段 + cross/native/target 變體）和你的 patch 影響哪裡，是 compiler 工程師最該懂的基礎。這章是 yocto 課對 compiler 工程師最核心的——toolchain 怎麼在 Yocto 裡建，你的 patch 該針對哪個 recipe/變體/階段。

## 動手練習

1. 看 toolchain 家族：探索 meta/recipes-devtools/gcc/，認識各 recipe（gcc-cross/initial/runtime）

2. 看 bootstrap 依賴：`bitbake -e gcc-cross-riscv64 | grep DEPENDS`，理解 bootstrap 順序

3. 看 SRC_URI：看 gcc recipe 的 SRC_URI（source + 上游 patches），理解你 patch 加在哪

4. 看 EXTRA_OECONF：`bitbake -e gcc-cross-riscv64 | grep EXTRA_OECONF`，理解 gcc 的 configure 選項

5. 理解變體：理解 cross/native/target 的差別，你的 patch 該針對哪個

## 本章重點整理

- toolchain bootstrap 解決雞生蛋（建 gcc 要 libc、建 libc 要 gcc）：binutils → gcc-initial → glibc → gcc-cross
- toolchain recipe 家族：gcc-cross（最核心，編 target 的 cross-compiler）、gcc-source（共用 source，patch 加這影響所有變體）
- 三種變體：native（host 用）、cross（host 跑編 target，gcc-cross）、target（target 上跑，gcc-runtime）；用 override 針對
- gcc recipe 的核心：SRC_URI（加 patch 用 :append）、EXTRA_OECONF（改 configure 選項用 :append）
- 理解 build flow 讓你定位 patch 在哪階段出問題：gcc 自己 build fail vs gcc 編別套件時出問題（不同 debug 方向）

## 自我檢核

- [ ] 理解 toolchain bootstrap 為什麼要多階段（雞生蛋）
- [ ] 認識 toolchain recipe 家族，知道 compiler 工程師主要改哪個
- [ ] 理解 cross/native/target 三種變體，你的 patch 該針對哪個
- [ ] 知道 gcc recipe 的 SRC_URI（patch）和 EXTRA_OECONF（選項）
- [ ] 能定位 patch 在 build flow 哪個階段出問題

## 延伸閱讀

### 官方

- **[Yocto Toolchain](https://docs.yoctoproject.org/overview-manual/concepts.html#cross-development-toolchain-generation)** — Yocto Project
  - **讀哪裡**：cross-development toolchain generation
  - **為什麼值得讀**：Yocto toolchain build flow 的官方說明

- **[gcc recipe（poky）](https://git.yoctoproject.org/poky/tree/meta/recipes-devtools/gcc)** — Yocto
  - **讀哪裡**：gcc-cross.inc、gcc-${PV}.inc 的結構
  - **為什麼值得讀**：production 級的 toolchain recipe（README 推薦讀的）

### 背景

- **[Cross-compiler bootstrap](https://wiki.osdev.org/GCC_Cross-Compiler)** — OSDev Wiki
  - **這篇說什麼**：手動建 cross-compiler 的 bootstrap（理解 Yocto 自動做的）
  - **為什麼值得讀**：理解 toolchain bootstrap 的原理（不限 Yocto）

下一章是 compiler 工程師的核心實戰——patch 一個 upstream GCC bug 進 image。把這章的 toolchain 知識和 Ch 2 的 .bbappend 語法整合，實際做一次 patch GCC。

→ [Ch 5 Patch 一個 upstream GCC bug 進 image](./05-patching-gcc.md)
