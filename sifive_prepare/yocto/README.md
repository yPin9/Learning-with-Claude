# Yocto / OpenEmbedded：給 toolchain 工程師的速通

> 給寫 compiler / toolchain、需要把自家 patch 進 BSP 交給客戶的工程師。目標不是精通 Yocto，是能「看得懂 recipe、改 recipe 把 patched GCC 塞進 RISC-V distro image」。

這是 SiFive job spec 第三條 responsibility 的對口課程：**"Work with SiFive's compiler and system software teams to integrate GNU toolchain recipes in the Yocto/OE-based Linux distribution system."**

## 為什麼這門課刻意短

Yocto 水深見底、10+ 年 learning curve。但對 **compiler 工程師**實際需要的是：

- 看懂 `.bb` recipe 結構
- 會改 `gcc-cross` / `binutils-cross` 之類 toolchain recipe
- 能 add patch / bump version
- 基本 debug（build fail 時找 log）
- Yocto vs Buildroot 的 trade-off

**不需要**：

- 成為 Yocto 核心 maintainer
- 設計 BSP layer from scratch
- 深入 bitbake 的 scheduler internals

所以本課 11 章（Ch 0–10）、重 essentials。

## 為什麼 SiFive 要你懂這個

SiFive 出 IP、客戶做 chip。chip 上要跑 Linux → 要有 BSP → Yocto 是業界標準。

客戶流程：

```
SiFive 給 chip design + reference toolchain
  │
  ↓
客戶 build Yocto image：包含 SiFive-patched GCC / binutils
  │
  ↓
Yocto 產 rootfs / kernel image
  │
  ↓
Flash 進 chip、出貨
```

**你當 compiler 工程師要懂**：客戶 Yocto build break 時怎麼 diagnose「是 compiler patch 問題還是 Yocto recipe 問題」。

## 課程地圖

### Part 0 — 起步
- [Ch 0 環境搭建：poky + bitbake 第一次 build](./00-environment-setup.md)

### Part 1 — Yocto 心法
- [Ch 1 Yocto 心法：layer / recipe / task / metadata](./01-yocto-mental-model.md)
- [Ch 2 `.bb` / `.bbappend` / `.bbclass` 語法](./02-recipe-syntax.md)

### Part 2 — RISC-V 與 toolchain
- [Ch 3 `meta-riscv` layer 解剖](./03-meta-riscv.md)
- [Ch 4 Toolchain recipe：`gcc-cross` / `binutils-cross` / `glibc`](./04-toolchain-recipes.md)
- [Ch 5 Patch 一個 upstream GCC bug 進 image](./05-patching-gcc.md)
- [Ch 6 SDK vs eSDK：給客戶 dev env](./06-sdk-vs-esdk.md)

### Part 3 — 日常 workflow 與陷阱
- [Ch 7 Image recipe 與 rootfs 組裝](./07-image-recipe.md)
- [Ch 8 devtool workflow：該你常用的指令](./08-devtool-workflow.md)
- [Ch 9 常見雷：sstate-cache / DEPENDS / PREFERRED_VERSION](./09-common-traps.md)
- [Ch 10 Yocto vs Buildroot：何時該選誰](./10-yocto-vs-buildroot.md)

### Part 4 — 實戰
- [練習：patch 一個 CVE fix 進 gcc recipe](./practice-patch-cve.md)
- [Final Project：把你自家 custom extension patch 塞進 RISC-V Yocto image](./final-project-custom-extension-yocto.md)

## 學習方式建議

1. **真 build 一次**：Yocto 第一次 build 2-4 小時。忍一下、你會學到很多（哪些步驟、什麼 dependencies）。
2. **從 meta-riscv 開始**：不要 from scratch 建 layer。Fork `meta-riscv`、改、學。
3. **看 real recipe**：`poky/meta/recipes-devtools/gcc/gcc_11.2.bb` 是 production 級 recipe。讀它。
4. **用 VM**：Yocto build 吃 30+ GB disk、8+ GB RAM。開個專屬 VM 或雲 instance。
5. **不用會寫 python**：bitbake 大量 Python，但 compiler 工程師多半只要 read + small edit。

## 本課不涵蓋

- **自己設計 layer**：太大，非 compiler 工程師日常
- **Distro customization**：Yocto 強項，但非這門課 focus
- **CI/CD for Yocto**：deployment 議題
- **License compliance**：GPL / 授權議題（Yocto 有工具 `meta-oe-meta` 管）
- **Kernel development**：只 touch rootfs layer

## 參考資料

**官方**：
- **Yocto Mega-Manual**: <https://docs.yoctoproject.org/>
- **BitBake Reference**: <https://docs.yoctoproject.org/bitbake/>
- **meta-riscv**: <https://github.com/riscv/meta-riscv>

**書**：
- 《Embedded Linux Systems with the Yocto Project》— Rudolf J. Streif
- 《Learning Embedded Linux Using the Yocto Project》— Alexandru Vaduva

**社群**：
- Yocto mailing list
- #yocto freenode IRC
- meta-riscv GitHub issues

**教學 blog**：
- Yocto tutorials from Konsulko Group
- NXP / Xilinx 的 Yocto docs（商用 BSP 實例）
