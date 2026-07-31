# Ch 3 — meta-riscv layer 解剖

> **目標**：走一遍 meta-riscv layer 的結構——它怎麼組織、有哪些 machine config（qemuriscv64、各 RISC-V 板子）、RISC-V 專屬的 recipe 和設定。理解 BSP layer 的結構，會加 meta-riscv 並 build 一個 RISC-V image。這是所有 RISC-V Yocto BSP 的起點，也是把你的 toolchain 用在 RISC-V 的基礎。

> **環境**：Yocto（poky，Ch 0）+ meta-riscv layer。

## 為什麼從 meta-riscv 開始？

SiFive 出 RISC-V IP、客戶做 chip、chip 上跑 Linux——而 RISC-V 的 Yocto BSP 起點是 **meta-riscv** layer（RISC-V 社群維護的 BSP layer）。它提供 RISC-V 的 machine config（怎麼為 RISC-V 板子 build）、RISC-V 專屬的 recipe 和設定。

對 compiler 工程師，理解 meta-riscv 很重要——你的 patched GCC 要用在 RISC-V，就要在 meta-riscv 的脈絡（RISC-V 的 machine、toolchain 設定）裡 build。Ch 0 的「不要從零建 layer，從現有的改」——對 RISC-V 就是「從 meta-riscv 開始」。這章解剖 meta-riscv 的結構（machine config、recipe、設定），讓你能加它、build RISC-V image、並理解你的 toolchain 在這個脈絡裡的位置。

## 先建立直覺:BSP layer 提供「硬體支援」

```
BSP（Board Support Package）layer 提供什麼：

  poky 的 meta 是「通用的」（架構無關的核心 recipe）
  BSP layer（meta-riscv）加上「特定架構/板子的支援」：
        │
  1. Machine config（machine/*.conf）：
     怎麼為某個板子/架構 build
     qemuriscv64.conf（QEMU RISC-V）、各 RISC-V 板子的 conf
     設定：架構、kernel、bootloader、tuning（-march 等）
        │
  2. 架構專屬的 recipe/設定：
     RISC-V 的 bootloader（opensbi）、kernel 設定
     RISC-V 的 tuning（指令集擴展、ABI）
        │
  3. Tuning（DEFAULTTUNE）：
     RISC-V 的 -march/-mabi（如 rv64gc/lp64d）
     → 這影響 toolchain 怎麼編譯（compiler 工程師關心！）
        │
  → BSP layer 把「通用的 Yocto」變成「能為特定硬體 build」
    meta-riscv = RISC-V 的硬體支援
    對 compiler：tuning（-march/-mabi）決定你的 gcc 怎麼編 RISC-V code
```

關鍵心智：**BSP layer**（meta-riscv）提供「特定架構/板子的硬體支援」——**machine config**（怎麼為 RISC-V 板子 build：架構/kernel/bootloader/tuning）、架構專屬的 recipe（bootloader/kernel）、**tuning**（DEFAULTTUNE——RISC-V 的 -march/-mabi，影響 toolchain 怎麼編譯，compiler 工程師關心）。它把「通用的 Yocto」變成「能為 RISC-V build」。

## 加入 meta-riscv 並 build

```bash
cd ~/yocto
# === 取得 meta-riscv（和 poky 相容的分支）===
git clone https://github.com/riscv/meta-riscv -b scarthgap   # 對應 poky 的版本
# meta-riscv 可能依賴其他 layer（如 meta-openembedded），看它的 README

# === 加入 layer ===
cd poky
source oe-init-build-env       # 進入 build 環境
bitbake-layers add-layer ../meta-riscv
# 或手動編輯 conf/bblayers.conf 加 meta-riscv 的路徑

# 確認 layer 加好了
bitbake-layers show-layers | grep riscv

# === 設定目標為 RISC-V ===
# 編輯 conf/local.conf
# MACHINE = "qemuriscv64"        # QEMU RISC-V 64-bit（最容易測試）
# 或實際的板子：sifive-unmatched、visionfive2 等
echo 'MACHINE = "qemuriscv64"' >> conf/local.conf

# === build RISC-V image ===
bitbake core-image-minimal
# 現在 build 的是 RISC-V 的 image！
# 用了 RISC-V 的 toolchain（cross-compile：x86 host 編 RISC-V binary）

# 跑起來
runqemu qemuriscv64 nographic
# 開機進一個 RISC-V Linux！
```

```
meta-riscv 提供的 machine（目標板子）：

  qemuriscv64    ← QEMU RISC-V 64-bit（測試最方便）
  qemuriscv32    ← QEMU RISC-V 32-bit
  sifive-unmatched ← SiFive HiFive Unmatched 板
  visionfive2    ← StarFive VisionFive 2
  ... 各種 RISC-V 板子
        │
  選 MACHINE = 決定「為哪個板子 build」
    影響：kernel 設定、bootloader、device tree、tuning
        │
  → 測試/學習用 qemuriscv64（不用實體板子，QEMU 跑）
    實際出貨用對應的板子 machine
```

> **加 meta-riscv + 設 MACHINE=qemuriscv64 就能 build RISC-V image——這是 cross-compile（x86 host 編 RISC-V binary）**。把 Yocto 從 x86 轉到 RISC-V 的步驟：(1) **取得 meta-riscv**（RISC-V 社群的 BSP layer，對應 poky 的版本分支，可能依賴 meta-openembedded 等其他 layer）；(2) **`bitbake-layers add-layer`** 加入；(3) **設 `MACHINE = "qemuriscv64"`**（在 local.conf——選 RISC-V 的目標機器）；(4) **`bitbake core-image-minimal`** build RISC-V image。現在 build 的是 **RISC-V 的 image**——這是 **cross-compilation**（在 x86 host 上編譯 RISC-V 的 binary，用 RISC-V 的 cross-compiler，Ch 4）。`runqemu qemuriscv64` 跑起來（一個 RISC-V Linux）。**MACHINE** 決定「為哪個板子 build」——meta-riscv 提供多種 machine：**qemuriscv64**（QEMU，**測試/學習最方便**——不用實體板子）、**sifive-unmatched**（SiFive 的板子）、**visionfive2**（StarFive）等實際板子。MACHINE 影響 kernel 設定、bootloader、device tree、tuning。對學習/測試用 qemuriscv64（QEMU 跑，不用硬體），實際出貨用對應的板子 machine。對 compiler 工程師，這個轉換很重要——你的 patched GCC 要在 RISC-V 的脈絡 build（用 RISC-V 的 tuning，下節），所以理解怎麼設定 RISC-V 的 build 環境是基礎。注意 meta-riscv 的版本要對應 poky（layer 之間有相容性要求，Ch 9 的雷）。

## RISC-V 的 tuning:compiler 工程師關心的

```bash
cd ~/yocto/poky/build
# RISC-V 的 tuning（DEFAULTTUNE）決定 toolchain 怎麼編譯
# 看當前的 tune
bitbake -e core-image-minimal 2>/dev/null | grep -E '^DEFAULTTUNE=|^TUNE_FEATURES='
# DEFAULTTUNE="riscv64"
# TUNE_FEATURES="riscv64 ..."

# RISC-V 的 tune 設定（在 meta-riscv 的 conf/machine/include/）
cat ~/yocto/meta-riscv/conf/machine/include/*.inc 2>/dev/null | grep -E 'TUNE|march|mabi' | head
# 看 RISC-V 的 -march/-mabi 設定

# RISC-V 的 march/mabi（compiler 工程師關心）：
# -march=rv64gc：RV64 + 通用擴展（G=IMAFD, C=壓縮）
# -mabi=lp64d：64-bit ABI with double float
# 或 rv64gcv（加向量 V）—— 如果板子支援
# → 這些 tune 決定「你的 gcc 用什麼指令集編 RISC-V code」

# 看 toolchain 用的 march（你的 patched GCC 會用這個）
bitbake -e gcc-cross-riscv64 2>/dev/null | grep -E 'TUNE_CCARGS' | head
# TUNE_CCARGS="-march=rv64gc -mabi=lp64d ..."
# → 你的 gcc 編 RISC-V 時用這些 flag（Ch 10 of perf_bench 的 march/mabi）
```

> **RISC-V 的 tuning（DEFAULTTUNE/TUNE_FEATURES 決定 -march/-mabi）控制你的 gcc 怎麼編 RISC-V code——這是 compiler 工程師最關心的設定**。**Tuning** 是 Yocto 控制「為哪個架構變體編譯」的機制，對 RISC-V 特別重要（RISC-V 有很多擴展組合）。**DEFAULTTUNE/TUNE_FEATURES** 決定 RISC-V 的 **-march/-mabi**：**-march=rv64gc**（RV64 + 通用擴展 G=IMAFD + C 壓縮）、**-mabi=lp64d**（64-bit ABI with double float），或 **rv64gcv**（加向量 V，如果板子支援，呼應 perf_bench Ch 10/13 的 RVV）。這些 tune **決定你的 patched GCC 用什麼指令集編 RISC-V code**——這是 **compiler 工程師最關心的設定**！`bitbake -e gcc-cross-riscv64 | grep TUNE_CCARGS` 看 toolchain 編 RISC-V 時用的 flag（如 `-march=rv64gc -mabi=lp64d`）。對 compiler 工程師，理解 tuning 很重要——(1) 你的 gcc 改動要在對的 tune 下測試（如果你加了 RVV 的優化，要確認 tune 是 rv64gcv）；(2) tune 不對可能讓你的優化沒生效（如優化針對某擴展但 tune 沒啟用那擴展）；(3) debug 客戶問題時，確認他們的 tune（`bitbake -e` 看 TUNE_CCARGS）——「客戶的 gcc 為什麼沒用某指令」可能是 tune 沒啟用那擴展。這把 perf_bench 的 -march/-mabi 知識（Ch 10）和 Yocto 連起來——Yocto 的 tuning 就是設定 toolchain 的 -march/-mabi。理解 RISC-V 的 tuning，你知道「你的 gcc 在 Yocto 裡怎麼被設定來編 RISC-V」，這是把你的 toolchain 正確用在 RISC-V 的關鍵。

## meta-riscv 的結構

```bash
cd ~/yocto/meta-riscv
ls
# conf/                ← layer 設定 + machine config
#   layer.conf         ← layer 的設定（優先順序、相容版本）
#   machine/           ← machine config（qemuriscv64.conf 等）
#     include/         ← 共用的 tune/設定
# recipes-bsp/         ← BSP recipe（bootloader opensbi 等）
# recipes-kernel/      ← RISC-V kernel 設定
# recipes-devtools/    ← RISC-V 相關的工具
# ...

# 看一個 machine config
cat conf/machine/qemuriscv64.conf
# require conf/machine/include/qemuriscv.inc   ← 引入共用設定
# 設定：KERNEL、bootloader、device tree、串列埠...

# 看 layer.conf（layer 的相容性）
cat conf/layer.conf | grep -E 'LAYERSERIES_COMPAT|BBFILE_PRIORITY'
# LAYERSERIES_COMPAT_meta-riscv = "scarthgap ..."  ← 相容哪些 poky 版本
# → 版本要對應（Ch 9 的雷：layer 版本不對應會出問題）
```

> **meta-riscv 的結構：machine config（為 RISC-V 板子 build）+ BSP recipe（bootloader/kernel）+ tune 設定——layer.conf 的 LAYERSERIES_COMPAT 是版本相容的關鍵**。meta-riscv 的結構：**conf/**（layer 設定 + machine config——`machine/qemuriscv64.conf` 等定義各 RISC-V 板子怎麼 build、`machine/include/` 共用的 tune 設定）、**recipes-bsp/**（BSP recipe——bootloader 如 opensbi）、**recipes-kernel/**（RISC-V kernel 設定）。一個 machine config（如 qemuriscv64.conf）`require` 引入共用設定，定義 KERNEL/bootloader/device tree/串列埠等。**layer.conf** 的 **LAYERSERIES_COMPAT** 是版本相容的關鍵——它宣告「這個 layer 相容哪些 poky 版本」（如 `scarthgap`）。**版本要對應**（Ch 9 的常見雷）——meta-riscv 的版本要和 poky 的版本相容，否則會出問題（layer 不相容、recipe 找不到、build 錯誤）。對 compiler 工程師，理解 meta-riscv 的結構讓你知道「RISC-V 的 build 怎麼設定」（machine config、tune），以及你的 toolchain 改動在這個脈絡的位置。你通常**不改 meta-riscv**（它是 BSP layer，由 RISC-V 社群維護）——而是在你自己的 layer（meta-mycompany）用 .bbappend 加你的 toolchain patch（Ch 2/5），meta-riscv 提供 RISC-V 的 machine/tune 設定。這呼應「加 layer 不改別人的」哲學——meta-riscv 提供硬體支援、你的 layer 提供 toolchain patch、poky 提供核心，三者疊起來。理解 meta-riscv 是把你的 toolchain 用在 RISC-V 的基礎——它定義了 RISC-V 的 build 環境（machine/tune），你的 gcc 在這個環境裡被 build 和使用。

## 故意弄壞:layer 版本不相容

```bash
cd ~/yocto
# 展示「layer 版本不相容」的問題（Ch 9 的雷，這裡預習）

# 如果 meta-riscv 的版本和 poky 不對應：
# 例：poky 是 scarthgap，但 meta-riscv clone 了 kirkstone（舊版）
# bitbake 會報錯（LAYERSERIES_COMPAT 不 match）：
# ERROR: Layer meta-riscv is not compatible with the core layer ...

# 檢查 layer 相容性
cd poky/build
bitbake-layers show-layers   # 看所有 layer
# 確認 meta-riscv 的版本和 poky 對應

# 看 layer 的相容性宣告
grep LAYERSERIES_COMPAT ~/yocto/meta-riscv/conf/layer.conf
# 確認它相容你的 poky 版本

# → 教訓：layer 之間有版本相容性要求
#   poky、meta-riscv、meta-openembedded 等都要用「對應的版本」
#   （都用 scarthgap，或都用 kirkstone——不要混）
#   版本不對應是 Yocto 新手最常見的問題之一（Ch 9 詳述）
```

> **layer 版本要對應（poky/meta-riscv/meta-openembedded 都用同一個 release 如 scarthgap）——版本不對應是 Yocto 最常見的問題之一**。Yocto 的 layer 之間有**版本相容性要求**——poky、meta-riscv、meta-openembedded 等 layer 都要用「**對應的版本**」（同一個 Yocto release，如都用 scarthgap，或都用 kirkstone——**不要混**）。每個 layer 的 layer.conf 用 **LAYERSERIES_COMPAT** 宣告「相容哪些版本」。如果版本不對應（如 poky 是 scarthgap 但 meta-riscv 是 kirkstone），bitbake **報錯**（"Layer X is not compatible with the core layer")。這是 **Yocto 新手最常見的問題之一**——clone 了不對應版本的 layer，build 不起來。debug：`bitbake-layers show-layers` 看所有 layer、`grep LAYERSERIES_COMPAT` 看相容性宣告——確認所有 layer 用對應的版本。對 compiler 工程師，這個認知重要——你 setup RISC-V Yocto 環境時，要確保所有 layer（poky/meta-riscv/你的 layer/依賴的 layer）版本對應，否則 build 不起來（在你 patch GCC 之前就卡住）。Ch 9（常見雷）會深入這類問題，但現在先建立認知——**layer 版本要對應，是 setup Yocto 環境的基本要求**。這也是為什麼 clone layer 時要指定對應的分支（`-b scarthgap`）。理解 meta-riscv（RISC-V 的 BSP）+ 版本相容性，你能正確地 setup RISC-V Yocto 環境，這是後面 toolchain recipe（Ch 4）和 patch GCC（Ch 5）的前提。

## 動手練習

1. 加 meta-riscv：clone meta-riscv（對應版本）、加入 layer、設 MACHINE=qemuriscv64

2. build RISC-V：`bitbake core-image-minimal`（RISC-V），`runqemu qemuriscv64` 跑起來

3. 看 tuning：用 `bitbake -e` 看 RISC-V 的 DEFAULTTUNE/TUNE_CCARGS（-march/-mabi）

4. 探索結構：看 meta-riscv 的 machine config（qemuriscv64.conf）、layer.conf

5. 版本相容：檢查 meta-riscv 的 LAYERSERIES_COMPAT，理解版本要對應

## 本章重點整理

- meta-riscv 是 RISC-V 的 BSP layer——提供 machine config（為 RISC-V 板子 build）、tune（-march/-mabi）、BSP recipe
- 加 meta-riscv + 設 MACHINE=qemuriscv64 就能 build RISC-V image（cross-compile：x86 host 編 RISC-V）
- tuning（DEFAULTTUNE/TUNE_CCARGS）決定你的 gcc 用什麼指令集編 RISC-V（rv64gc/lp64d 或 rv64gcv）——compiler 最關心
- meta-riscv 結構：machine config + BSP recipe + tune；你不改它，在自己的 layer 用 .bbappend 加 toolchain patch
- layer 版本要對應（poky/meta-riscv 都用同 release）——版本不對應是最常見的問題（Ch 9）

## 自我檢核

- [ ] 會加 meta-riscv 並 build RISC-V image（MACHINE=qemuriscv64）
- [ ] 理解 BSP layer 提供什麼（machine config/tune/BSP recipe）
- [ ] 知道 RISC-V 的 tuning（-march/-mabi）怎麼影響你的 gcc 編譯
- [ ] 理解 meta-riscv 的結構，你的 toolchain patch 該放哪（你的 layer，不改 meta-riscv）
- [ ] 知道 layer 版本要對應，怎麼檢查相容性

## 延伸閱讀

### 官方

- **[meta-riscv](https://github.com/riscv/meta-riscv)** — RISC-V
  - **讀哪裡**：README（怎麼用）、machine config、layer.conf
  - **為什麼值得讀**：RISC-V Yocto BSP 的權威；實際的 RISC-V layer

- **[Yocto BSP Guide](https://docs.yoctoproject.org/bsp-guide/index.html)** — Yocto Project
  - **讀哪裡**：BSP layer 的結構、machine config
  - **為什麼值得讀**：BSP layer 的官方權威

### RISC-V

- **[RISC-V tuning in Yocto](https://github.com/riscv/meta-riscv/tree/master/conf/machine/include)** — meta-riscv
  - **為什麼值得讀**：RISC-V 的 tune 設定（-march/-mabi），compiler 工程師關心

下一章深入 toolchain recipe——gcc-cross、binutils-cross、glibc 的 recipe。理解 cross-compilation 的 toolchain 怎麼在 Yocto 裡建，這是你 patch GCC 的直接基礎。

→ [Ch 4 Toolchain recipe：gcc-cross / binutils-cross / glibc](./04-toolchain-recipes.md)
