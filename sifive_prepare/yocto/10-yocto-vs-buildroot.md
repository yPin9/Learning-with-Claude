# Ch 10 — Yocto vs Buildroot：何時該選誰

> 目標：理解 Buildroot 作為 Yocto 的輕量替代、兩者的設計哲學差異、各自擅長什麼場景。SiFive 客戶會兩個都碰、compiler 工程師要知道怎麼 support 兩個。

## Buildroot 是什麼

**Buildroot** 是另一個嵌入式 Linux build system。發源比 Yocto 早、更簡單。

- 2001 ~ 出現
- 100% GPL
- 用 **Makefiles + Kconfig**（Linux kernel 風格）
- 沒 layer system、沒 package version pinning 
- single config file (`.config`)

## Yocto vs Buildroot：高層對比

| 議題 | Yocto | Buildroot |
|------|-------|-----------|
| 起源 | 2010 | 2001 |
| Build config | Layers + recipes | Single `.config` |
| 語言 | BitBake (Python-ish) + shell | Kconfig + Make |
| Learning curve | 陡 (months) | 緩 (days) |
| 第一次 build | 1-4 hours | 20-60 min |
| Incremental | sstate 快 | re-build 常見 |
| BSP ecosystem | 巨大（meta-*) | 較小 |
| Package 數 | 1000s | ~2000 |
| 商用 BSP | NXP / TI / Xilinx 主力 | 消費性嵌入式多 |
| Debug 難度 | 高 (複雜) | 中 |
| 自訂性 | **極高** | 中 |

## 設計哲學差異

### Yocto: scalability

Yocto 為 **大型 / 多 target / commercial BSP** 設計：

- Layer system 讓多公司 / 多 BSP 併存
- sstate 讓 CI 跑得快
- bbappend 支持 override 任何 upstream recipe
- Hash-based incremental build

**代價**：複雜度。

### Buildroot: simplicity

Buildroot 為 **單一 developer / simple BSP** 設計：

- 一個 config file 表達 all
- `make menuconfig` 視覺化選 package
- Package format 直接 (Kconfig + .mk)
- 小 project，easy to understand

**代價**：難 scale 到 multi-BSP、複雜變種。

## Buildroot 快速示例

```bash
git clone https://github.com/buildroot/buildroot
cd buildroot
make qemu_riscv64_virt_defconfig
make                  # build everything

# Output
ls output/images/
# rootfs.ext2
# Image
# qemu-system-riscv64-virt.readme.txt
```

**4 行 command 就有 boot 得起來的 image**。

比 Yocto 的第一次 build 容易太多。

## 相同任務 in Yocto vs Buildroot

### 加一個 package：`htop`

Yocto:

```
# conf/local.conf
IMAGE_INSTALL:append = " htop"
```

Buildroot:

```bash
make menuconfig   # 勾選 htop, save
make              # rebuild
```

Yocto 1 行 config、Buildroot TUI 操作、最終效果一樣。

### 加 patch 到 package

Yocto: 寫 bbappend、放 file、rebuild。

Buildroot: 放 patch 到 `package/xxx/`、rebuild 會自動 apply。

Buildroot 較簡單、Yocto 較系統。

### Build custom cross-compiler

Yocto: 修 `gcc_%.bbappend`、`cleansstate`、rebuild。多 layer 協調。

Buildroot: 改 `package/gcc/gcc.mk`、`make`。單 file 改動。

## Buildroot 的優勢場景

- **單一 product line**
- **小團隊 / 個人 developer**
- **Consumer-grade embedded**（SmartWatch, IoT device）
- **不需要 multi-BSP**
- **不需要 long-term support**

## Yocto 的優勢場景

- **Commercial BSP（SiFive, TI, NXP）**
- **多 client / multi-target**
- **Long-term maintenance**
- **License compliance 嚴格**
- **Community 生態（meta-*）**

## RISC-V 生態：都用

RISC-V 社群**兩個都用**：

- **Buildroot**：
  - VisionFive 2 原廠 BSP
  - Nezha / Lichee 系列
  - 個人 hobbyist
  - EEMBC's Coremark 主流搭配

- **Yocto**：
  - SiFive 的官方 BSP
  - OpenHarmony
  - 商用客戶
  - Chromium OS (for RISC-V port)

SiFive compiler 工程師**兩個都要知道怎麼 support patch**。

## Patch 一個 compiler fix to Buildroot

Buildroot 的 gcc package：

```
buildroot/package/gcc/
├── Config.in.host           Kconfig
├── gcc.hash
├── gcc.mk                    Makefile (main recipe)
├── 12.3.0/
│   └── 0001-my-patch.patch  ← 放這裡
└── ...
```

Buildroot 自動 apply `package/gcc/<version>/*.patch`。加 patch file、rebuild。

對比 Yocto 的 bbappend，Buildroot 實際上**更簡單**（但也更 rigid）。

## SDK / toolchain in Buildroot

```bash
make sdk
# 產生 output/images/<name>-sdk-buildroot.tar.gz
```

類似 Yocto 的 SDK。客戶拿來 cross-compile。

Buildroot SDK 通常**比 Yocto 簡單**但**Feature 少**（沒 eSDK 類概念）。

## 商業考量

Yocto 對商用 BSP：

- License tracking 自動化（`LIC_FILES_CHKSUM`）
- CVE tracking via `meta-security`
- Long-term version pinning

Buildroot 對商業：

- 合規要自 track
- 更新 CVE 要自 follow upstream
- 沒 "LTS" 概念（但相對穩定）

大公司通常 Yocto。small team 可能 Buildroot。

## Community 活躍度

**Yocto**:
- 官方 Yocto Project + Linux Foundation 支持
- 幾百 active maintainer
- 每 6 個月 release
- Yocto Project Summit

**Buildroot**:
- 純社群
- 較小 but dedicated community
- 每 3 個月 release
- Buildroot Developer Days

## 我什麼時候建議用哪個

```
你是 SiFive compiler 工程師、要出 BSP 給客戶
  → Yocto (幾乎全部情況)

你是 SiFive 客戶、小 product、team 5 人內
  → Buildroot (簡單 + 夠用)

你是學生 / 業餘 / VisionFive 2 玩家
  → Buildroot (cloud learning curve 最低)

商用 product、multi-board support
  → Yocto
```

## Migration 難度

Yocto → Buildroot：相對簡單（簡化）。
Buildroot → Yocto：**非常痛苦**（要重建 layer、recipe）。

所以**啟動 project 時選擇重要**。

## 其他 embedded Linux build system

除了 Yocto / Buildroot，還有：

- **OpenWrt**：router 專用
- **PTXdist**：德國公司出
- **Gentoo 的 CROSSDEV**：Gentoo 社群
- **meta-Debian**：用 Debian package

SiFive 客戶多元，你可能見到各種。**多見一個、你都能 patch**。

## Yocto 跟 Buildroot 的未來

兩個都持續發展。短期看不出 winner。

- **Yocto 持續 scaling-up**（Meta / Netflix 類 scale）
- **Buildroot 持續 simplifying**（maintain low barrier to entry）

商業趨勢：Yocto 在 commercial 佔主導、Buildroot 在 DIY / hobbyist 佔主導。

## 結課

這是本課最後一章。作為 compiler 工程師，你要：

- 熟 Yocto 主 workflow (Ch 0-9)
- 知道 Buildroot 存在 + 基本 workflow
- 能 patch toolchain 進兩個系統
- 能 debug 客戶 build 問題

**不求精通兩個**，求能 navigate。

## 動手練習

1. 裝 Buildroot、用 `qemu_riscv64_virt_defconfig` build image、boot 起來。
2. 對比 Yocto `bitbake core-image-minimal` 跟 Buildroot `make`：
   - First build time
   - Disk space
   - Steps 數
3. 加 `htop` package 到 Buildroot image (menuconfig)。
4. Patch 一個 GCC fix 到 Buildroot `package/gcc/<version>/`。
5. 寫一份 comparison memo："If I were SiFive, I would use X for scenario Y because ..."

## 常見誤會

1. **「Buildroot 過時了」**：active、仍大量使用。
2. **「Yocto 一定更好」**：Buildroot 在 right context 更有效率。
3. **「兩個能混用」**：technically yes、實務上很痛苦。一 product 選一個。
4. **「SiFive 只出 Yocto」**：主力 yes、但 customer request 可能要 Buildroot support。
5. **「compiler 工程師不用碰 build system」**：錯。patch 要塞進某個 system 才能給客戶。

## 自我檢核

- [ ] 我能快速 build Buildroot image for RISC-V
- [ ] 我能把一個 patch 加到 Buildroot 的 gcc package
- [ ] 我知道 Yocto / Buildroot 各自 strengths
- [ ] 我能幫客戶 debug "Yocto 跟 Buildroot 哪個適合"
- [ ] 我知道 Yocto / Buildroot 不是 唯二 選擇

課程至此結束。恭喜 — 你現在有完整工具面對 SiFive job 的所有三條 responsibility。

→ [練習：patch 一個 CVE fix 進 gcc recipe](./practice-patch-cve.md)
→ [Final Project：把 custom extension patch 塞進 RISC-V Yocto image](./final-project-custom-extension-yocto.md)
