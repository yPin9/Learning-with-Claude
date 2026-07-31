# Ch 2 — .bb / .bbappend / .bbclass 語法

> **目標**：看得懂並會寫三種 bitbake 檔案——.bb（recipe，定義怎麼建一個元件）、.bbappend（擴展已存在的 recipe，不改原檔）、.bbclass（可重用的 build 邏輯）。重點是 bitbake 的變數操作語法（賦值、附加、覆蓋、override）和 .bbappend（compiler 工程師用它擴展 gcc recipe 加 patch 的關鍵）。這章是改 recipe 的語法基礎。

> **環境**：Yocto（poky，Ch 0）。看真實 recipe + 寫 .bbappend。

## 為什麼語法是 compiler 工程師的關鍵基礎？

Ch 1 建立了概念（recipe 描述怎麼建元件、.bbappend 擴展 recipe）。但要實際**改 recipe**（加 patch、改設定），你需要懂 bitbake 的**語法**——變數怎麼賦值/附加/覆蓋、override 怎麼用、.bbappend 怎麼寫。這些語法有些奇特（`:append`、`:prepend`、override 的 `:` 語法），不懂就改不對。

對 compiler 工程師，最重要的是 **.bbappend** 的語法——你用它擴展上游的 gcc recipe（加你的 patch、改 configure 選項），不改上游的 recipe（Ch 1 的「加 layer 不改別人的」）。寫對 .bbappend 是 patch GCC（Ch 5）的關鍵。這章把這三種檔案的語法講清楚，特別是變數操作和 .bbappend——這是你改 recipe 的工具。

## 先建立直覺:三種檔案的角色

```
.bb / .bbappend / .bbclass 的角色：

  .bb（recipe）：定義「怎麼建一個元件」（完整的）
    gcc_13.2.bb → 怎麼建 gcc 13.2
    含：SRC_URI、版本、依賴、build 步驟
        │
  .bbappend（擴展）：「擴展/修改」已存在的 recipe（不改原檔）
    gcc_%.bbappend → 擴展 gcc recipe（加你的 patch/設定）
    在你的 layer 裡，疊加到上游的 .bb 上
    → compiler 工程師主要用這個（加 patch 不改上游）
        │
  .bbclass（class）：「可重用的 build 邏輯」（被 inherit）
    autotools.bbclass → 處理 ./configure + make 的通用邏輯
    recipe 用 inherit autotools 繼承它
    → 避免每個 recipe 重複寫 build 步驟
        │
  → .bb 定義、.bbappend 擴展、.bbclass 共用
    compiler 工程師：讀 .bb（理解 gcc 怎麼建）、寫 .bbappend（加 patch）
```

關鍵心智：**.bb**（recipe，定義怎麼建一個元件）、**.bbappend**（擴展已存在的 recipe，不改原檔——compiler 工程師用它加 patch）、**.bbclass**（可重用的 build 邏輯，被 inherit）。compiler 工程師主要**讀 .bb**（理解 gcc 怎麼建）和**寫 .bbappend**（在自己的 layer 擴展 gcc recipe 加 patch）。

## bitbake 的變數操作語法

bitbake 的變數操作是改 recipe 的核心，有幾種操作：

```bash
# === 變數操作（bitbake 語法）===
# 賦值（基本）
VAR = "value"                    # 直接賦值
VAR ?= "default"                 # 弱賦值（如果還沒設才用）
VAR ??= "weakest default"        # 最弱賦值

# 附加/前置（重要！加東西不覆蓋）
VAR:append = " more"             # 附加（注意前面的空格！直接接在後面）
VAR:prepend = "pre "             # 前置
VAR += "more"                    # 附加（會自動加空格）
VAR =+ "pre"                     # 前置（自動空格）

# 移除
VAR:remove = "unwanted"          # 移除某個值

# === override（針對特定條件的值）===
VAR:riscv64 = "riscv-specific"   # 只在 riscv64 時用這個值
VAR:append:riscv64 = " riscv"    # 只在 riscv64 時附加
SRC_URI:append:class-target = " file://patch.patch"  # 只在 target build 時

# override 的常見用途：
# :MACHINE（針對機器）、:class-target/:class-native（針對 build 類型）
# :pn-recipe（針對特定 recipe）

# === 範例：gcc recipe 的變數 ===
# SRC_URI = "...gcc source... file://patch1.patch"   # source + patch
# SRC_URI:append = " file://my-patch.patch"          # 加你的 patch（不覆蓋原本的）
```

```
最常用的變數操作（compiler 工程師）：

  SRC_URI:append = " file://my.patch"   ← 加你的 patch（最重要！）
    （注意 :append 的前導空格，否則和前面黏在一起）
        │
  EXTRA_OECONF:append = " --enable-foo"  ← 加 configure 選項
        │
  override（條件性）：
    :append:class-target  ← 只在編譯 target 的 gcc 時
    :riscv64              ← 只在 RISC-V 時
        │
  → :append/:prepend（加東西不覆蓋）是最常用的
    = （直接賦值）會覆蓋掉原本的（小心！）
    這就是為什麼 .bbappend 用 :append（疊加上游的，不覆蓋）
```

> **`SRC_URI:append = " file://my.patch"` 是 compiler 工程師加 patch 的核心語法——`:append`（疊加不覆蓋）+ 前導空格是關鍵**。bitbake 的變數操作有幾種，最重要的區別是**「賦值」（覆蓋）vs「附加」（疊加）**：**`VAR = "value"`**（直接賦值，**會覆蓋掉原本的值**——小心！如果你想加東西卻用 `=`，會把原本的全清掉）；**`VAR:append = " more"`**（**附加**——加在後面不覆蓋，**注意前導空格**，否則和前面的值黏在一起）；`VAR += "more"`（附加，自動加空格）；`VAR:prepend`（前置）；`VAR:remove`（移除）。對 **compiler 工程師**，最常用的是 **`SRC_URI:append = " file://my-patch.patch"`**——**加你的 patch 不覆蓋上游的**（上游的 gcc recipe 已有它的 patches，你用 `:append` 加你的，保留它的）。這就是為什麼 **.bbappend 用 `:append`**（疊加上游的，不覆蓋）——如果你在 .bbappend 用 `SRC_URI = "..."`（直接賦值），會把上游的 SRC_URI 整個覆蓋掉（連 gcc 的 source 都沒了，build 壞掉）。**override**（`:riscv64`、`:class-target`）讓變數**針對特定條件**——`SRC_URI:append:class-target = " file://patch"`（只在編譯 target 的 gcc 時加，不影響 native/cross）。理解這些語法（特別是 `:append` 疊加 + 前導空格 + override）是改 recipe 的基礎——用錯（如該 append 卻 assign）會把上游的設定覆蓋掉，build 壞掉。這是 Yocto 語法最容易出錯的地方，要小心。

## .bbappend:擴展上游 recipe

```bash
# .bbappend 是 compiler 工程師加 patch 的關鍵（不改上游 recipe）
cd ~/yocto/poky
# 假設你有自己的 layer：meta-mycompany
# 在裡面建一個 gcc 的 .bbappend

mkdir -p meta-mycompany/recipes-devtools/gcc/gcc
# .bbappend 的命名要對應上游 recipe（gcc_13.2.bb → gcc_%.bbappend 或 gcc_13.2.bbappend）
cat > meta-mycompany/recipes-devtools/gcc/gcc_%.bbappend <<'EOF'
# gcc_%.bbappend —— 擴展 gcc recipe（% = 任何版本）

# 告訴 bitbake 去哪找 patch 檔（FILESEXTRAPATHS）
FILESEXTRAPATHS:prepend := "${THISDIR}/gcc:"

# 加你的 patch（:append 疊加，不覆蓋上游的）
SRC_URI:append = " file://my-gcc-fix.patch"
EOF

# patch 檔放在對應目錄
# cp my-gcc-fix.patch meta-mycompany/recipes-devtools/gcc/gcc/

# → 這個 .bbappend：
#   1. 不改上游的 gcc_13.2.bb（在你的 layer）
#   2. 用 :append 加你的 patch（保留上游的 patches）
#   3. FILESEXTRAPATHS 告訴 bitbake 去哪找 patch 檔
# → bitbake build gcc 時會套上你的 patch（do_patch task）
```

```
.bbappend 的命名規則（重要）：

  上游 recipe：gcc_13.2.bb
        │
  對應的 .bbappend：
    gcc_13.2.bbappend   ← 只對 13.2 版本
    gcc_%.bbappend      ← 對任何版本（% = wildcard）
        │
  命名要對應（版本要 match），否則 .bbappend 不生效！
  （常見錯誤：版本不對，.bbappend 沒套用）
        │
  驗證 .bbappend 有沒有生效：
    bitbake -e gcc | grep SRC_URI   ← 看你的 patch 有沒有在 SRC_URI 裡
    （Ch 1 的 bitbake -e 技巧）
```

> **.bbappend 用 `:append` 擴展上游 recipe（加 patch 不覆蓋），命名要對應版本（gcc_%.bbappend），用 bitbake -e 驗證生效**。**.bbappend** 是 compiler 工程師的核心工具——它**擴展上游的 recipe，不改原檔**（在你自己的 layer）。寫一個 gcc 的 .bbappend：(1) **命名要對應**——上游 `gcc_13.2.bb` 對應 `gcc_13.2.bbappend`（只對 13.2）或 `gcc_%.bbappend`（`%` = 任何版本，常用——這樣升級 gcc 版本時 .bbappend 還生效）；**命名不對應（版本不 match）= .bbappend 不生效**（常見錯誤——你寫了 .bbappend 但 patch 沒套上，因為版本沒 match）；(2) **`FILESEXTRAPATHS:prepend`** 告訴 bitbake 去哪找 patch 檔（你的 patch 放在 .bbappend 旁的目錄）；(3) **`SRC_URI:append = " file://my.patch"`** 加你的 patch（`:append` 疊加上游的 patches，不覆蓋）。bitbake build gcc 時，do_patch task 會套上你的 patch。**驗證 .bbappend 生效**——`bitbake -e gcc | grep SRC_URI`（Ch 1 的技巧）看你的 patch 有沒有在 SRC_URI 裡（在 = 生效，不在 = .bbappend 沒套用，檢查命名/路徑）。這是 compiler 工程師 patch GCC 的標準方式（Ch 5 會完整做）——**在自己的 layer 寫 .bbappend 加 patch，不改上游的 gcc recipe**。這樣可維護（你的改動隔離在你的 layer）、不衝突（上游升級不影響）、易追蹤（你的 patch 集中在你的 layer）。理解 .bbappend 的語法（命名對應、:append 疊加、FILESEXTRAPATHS、bitbake -e 驗證）是 compiler 工程師在 Yocto 的核心技能。

## .bbclass:可重用的 build 邏輯

```bash
# .bbclass 是「可重用的 build 邏輯」（recipe 用 inherit 繼承）
cd ~/yocto/poky
# 看一個常見的 class
cat meta/classes-recipe/autotools.bbclass | head -20
# autotools.bbclass：處理 autotools 專案的 build（./configure + make + make install）
# 任何用 autotools 的 recipe 只要 inherit autotools，不用自己寫 build 步驟

# recipe 怎麼用 class
# inherit autotools          # 繼承 autotools（自動處理 configure/compile/install）
# inherit cmake              # 繼承 cmake（cmake 專案）
# inherit cross-canadian     # toolchain 相關的 class（Ch 4）

# 常見的 class（compiler 工程師會遇到）：
# autotools / cmake / meson  ← build 系統的 class
# cross / cross-canadian / native  ← toolchain 的 class（Ch 4）
# kernel                     ← kernel recipe 的 class

# → class 讓 recipe 不用重複寫 build 步驟
#   gcc 的 recipe 用了一堆 class（cross/toolchain 相關，Ch 4）
#   你通常不用寫 class（讀現有的就好，compiler 工程師很少寫 class）
```

> **.bbclass 是可重用的 build 邏輯（recipe 用 inherit 繼承）——compiler 工程師主要「讀」class（理解 gcc 用了哪些）而非寫 class**。**.bbclass** 是「可重用的 build 邏輯」——把通用的 build 步驟（如 autotools 專案的 ./configure + make + make install）封裝起來，recipe 用 **`inherit <class>`** 繼承，不用每個 recipe 重複寫。常見的 class：**autotools/cmake/meson**（build 系統的 class——處理對應 build 系統的編譯步驟）、**cross/cross-canadian/native**（**toolchain 相關的 class**，Ch 4——處理 cross-compiler、native tool 的特殊 build）、**kernel**（kernel recipe 的 class）。對 **compiler 工程師**，你主要**讀** class（理解 gcc recipe 用了哪些 class、它們做什麼——gcc 的 recipe 用了一堆 toolchain 相關的 class，Ch 4 會看）而**很少寫** class（寫 class 是 Yocto 進階，compiler 工程師通常用現有的）。理解 class 的概念（可重用的 build 邏輯，inherit 繼承）讓你看懂 recipe 的 `inherit` 行（知道它繼承了什麼 build 邏輯）。對 gcc recipe，理解它繼承的 toolchain class 是 Ch 4 的內容——這些 class 處理 cross-compilation 的複雜性（在 x86 host 建 RISC-V 的 gcc）。現在先理解 class 是什麼（可重用邏輯）、recipe 怎麼用它（inherit），以及 compiler 工程師的角色（讀 class 理解 gcc，不寫 class）。三種檔案（.bb/.bbappend/.bbclass）+ 變數語法是你改 recipe 的工具——主要用 .bbappend（擴展加 patch）、讀 .bb 和 .bbclass（理解怎麼建）。

## 故意弄壞:.bbappend 命名錯誤

```bash
cd ~/yocto/poky
# 展示「.bbappend 命名錯誤導致 patch 沒生效」（最常見的 .bbappend 錯誤）

# 假設上游是 gcc_13.2.bb，但你的 .bbappend 命名錯
# 錯誤：gcc_12.bbappend（版本不對，不 match 13.2）
# → bitbake 不會套用這個 .bbappend（版本沒 match）

# 正確：gcc_%.bbappend（% match 任何版本）或 gcc_13.2.bbappend

# 驗證 .bbappend 有沒有生效（bitbake -e，Ch 1）
bitbake -e gcc 2>/dev/null | grep 'my-gcc-fix.patch'
# 如果有 → .bbappend 生效（patch 在 SRC_URI）
# 如果沒有 → .bbappend 沒生效（檢查命名/路徑/FILESEXTRAPATHS）

# 也能看 bitbake 載入了哪些 .bbappend
bitbake-layers show-appends gcc 2>/dev/null
# 顯示「gcc 套用了哪些 .bbappend」→ 確認你的有沒有被載入

# → 常見的 .bbappend 錯誤：
#   1. 命名版本不對（gcc_12.bbappend 對 gcc_13.2.bb 不生效）→ 用 %
#   2. 沒設 FILESEXTRAPATHS（bitbake 找不到 patch 檔）
#   3. 用 = 而非 :append（覆蓋掉上游的 SRC_URI）
#   4. patch 檔路徑不對
# → debug：bitbake -e 看 SRC_URI、show-appends 看載入的 .bbappend
```

> **.bbappend 命名版本不對（最常見錯誤）導致 patch 沒生效——用 `gcc_%.bbappend`（wildcard）+ `bitbake-layers show-appends` 驗證**。.bbappend 最常見的錯誤是**命名問題**——.bbappend 的檔名要**對應上游 recipe 的版本**：上游 `gcc_13.2.bb`，你的 .bbappend 要是 `gcc_13.2.bbappend`（只對 13.2）或 `gcc_%.bbappend`（`%` wildcard，對任何版本——**推薦**，因為上游升級版本時你的 .bbappend 還生效）。如果命名版本不對（如 `gcc_12.bbappend` 對 `gcc_13.2.bb`），**bitbake 不會套用**（版本沒 match）——你的 patch 默默地沒生效（build 成功但沒套你的 patch，你以為 patch 生效了其實沒有，這很隱蔽）。其他常見錯誤：沒設 FILESEXTRAPATHS（bitbake 找不到 patch 檔）、用 `=` 而非 `:append`（覆蓋掉上游的 SRC_URI——連 gcc source 都沒了）、patch 檔路徑不對。**驗證 .bbappend 生效的兩個工具**：(1) **`bitbake -e gcc | grep my.patch`**（看你的 patch 有沒有在 SRC_URI——Ch 1 的技巧）；(2) **`bitbake-layers show-appends gcc`**（看 gcc 套用了哪些 .bbappend——確認你的有被載入）。這兩個是 debug .bbappend 的核心。對 compiler 工程師，這特別重要——你 patch GCC（Ch 5）後，**一定要驗證 patch 真的生效**（bitbake -e 看 SRC_URI），否則可能「build 成功但 patch 沒套上」（最隱蔽的問題——客戶說「你的 fix 沒效」，其實是 .bbappend 命名錯導致 patch 沒生效）。理解 .bbappend 的命名規則和驗證方法，你才能可靠地 patch GCC。這章的語法（變數操作、.bbappend、驗證）是 Ch 5（patch GCC）的直接基礎。

## 動手練習

1. 看變數操作：在真實 recipe 找 `:append`/`:prepend`/override 的例子，理解它們

2. 寫 .bbappend：建一個簡單的 .bbappend（如給 busybox 加一個設定），用 bitbake -e 驗證

3. 看 class：讀 autotools.bbclass，理解 recipe 怎麼用 inherit 繼承 build 邏輯

4. .bbappend 命名：理解 `gcc_%.bbappend` vs `gcc_13.2.bbappend`，為什麼用 %

5. 驗證工具：用 `bitbake -e` 和 `bitbake-layers show-appends` 驗證 .bbappend 生效

## 本章重點整理

- .bb（recipe，定義建元件）、.bbappend（擴展上游 recipe 不改原檔）、.bbclass（可重用 build 邏輯，inherit）
- 變數操作：= （覆蓋，小心）、:append（疊加，前導空格）、:prepend、override（:riscv64/:class-target 條件性）
- compiler 工程師核心：SRC_URI:append 加 patch（疊加不覆蓋）；.bbappend 命名要對應版本（用 gcc_%）
- .bbappend 要設 FILESEXTRAPATHS（找 patch 檔）；用 = 而非 :append 會覆蓋掉上游 SRC_URI（build 壞）
- 驗證 .bbappend 生效：bitbake -e（看 SRC_URI）、bitbake-layers show-appends（看載入的 .bbappend）

## 自我檢核

- [ ] 知道 .bb/.bbappend/.bbclass 的角色和差別
- [ ] 理解變數操作（= 覆蓋 vs :append 疊加），override 的條件性
- [ ] 會寫 .bbappend 加 patch（命名對應、:append、FILESEXTRAPATHS）
- [ ] 知道 .bbappend 常見錯誤（命名版本不對、用 = 覆蓋）
- [ ] 會驗證 .bbappend 生效（bitbake -e、show-appends）

## 延伸閱讀

### 官方

- **[BitBake Syntax](https://docs.yoctoproject.org/bitbake/bitbake-user-manual/bitbake-user-manual-metadata.html)** — Yocto Project
  - **讀哪裡**：變數操作（append/prepend/override）、.bbappend
  - **為什麼值得讀**：bitbake 語法的權威

- **[Yocto Recipe 語法](https://docs.yoctoproject.org/ref-manual/varlocality.html)** — Yocto Project
  - **讀哪裡**：變數和 override
  - **為什麼值得讀**：recipe 語法的權威

### 書籍

- **《Embedded Linux Systems with the Yocto Project》— recipe 章** — Streif
  - **為什麼值得讀**：recipe 語法的權威書

下一章進入 RISC-V——meta-riscv layer 的解剖。理解 RISC-V 的 BSP layer 怎麼組織、有哪些 machine/recipe，這是把你的 toolchain 用在 RISC-V 的基礎。

→ [Ch 3 meta-riscv layer 解剖](./03-meta-riscv.md)
