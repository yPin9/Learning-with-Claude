# Ch 1 — ARM 全貌：公司史、ISA 家族、A/R/M profile

> 目標：在啃 ISA 之前，先看清楚 ARM 是什麼、從哪來、長成什麼樣子。Cortex-A53 / Cortex-M4 / ARMv7-A / ARMv8-A / Apple M3 — 這些名字之間的關係要先釐清，後面才不會迷路。

## 一段 40 年公司史的精簡版

```
1983  Acorn Computers 在英國劍橋，員工 < 30 人
      老闆要做 16-bit 處理器，去 Intel/Motorola 找解法被冷處理
      Sophie Wilson + Steve Furber 自己設計
1985  ARM1 流片成功（Acorn RISC Machine）
      傳奇故事：插上電源前就跑得起來，因為 power 不接也能撐住
1990  Apple 投資 Acorn ARM 部門，獨立成公司
      Advanced RISC Machines Ltd. 成立（ARM 改名了）
1991  ARM6 出貨給 Apple Newton
1998  ARM 在納斯達克與倫敦上市
2000s ARM7TDMI / ARM9 / ARM11 統治手機市場
      Nokia、Ericsson、早期 iPhone 都用
2007  Cortex-A8（iPhone 1）— Cortex 命名首次出現
2011  ARMv8-A 規格公布（首次 64-bit）
2016  軟銀以 $32B 收購 ARM
2020  NVIDIA 嘗試以 $40B 收購 ARM（2022 因反壟斷取消）
2023  ARM 在納斯達克再上市（IPO）
2024+ Apple Silicon、AWS Graviton、AI 晶片大量採用
```

ARM 從一家英國小公司賣 IP，現在是地緣政治焦點 — 這段歷史會反覆在後面章節出現。

## ARM 的商業模式：賣 IP，不賣晶片

ARM 公司本身**幾乎不做晶片**。它的產品是：

- **ISA license**：你拿 ARM 指令集去自己設計 CPU 核（Apple、高通自家核都是這樣）
- **Core IP license**：直接買 ARM 設計好的 Cortex-A53 / M4 等核，整合進你的 SoC（聯發科、瑞昱、ST 等多數家）
- **POP（Processor Optimization Pack）**：給特定製程廠優化過的 hard IP

**這個模式讓 ARM 變成軟體 ISA 的事實標準**，因為任何想做 SoC 的人都可以買 license，不用自己從 0 設計 ISA。Intel 從不賣 x86 license，是策略不同的商業選擇。

## ARM ISA 版本演化

ARM 自己叫法：**Architecture v1, v2, ..., v9**（不是 ARM-1 不是 ARMv1.5）。

| 版本 | 年代 | 重點 |
|---|---|---|
| ARMv1 | 1985 | ARM1，第一版 |
| ARMv4 | 1993 | ARM7，Thumb 指令集（16-bit 編碼）首次出現於 ARMv4T |
| ARMv5 | 1999 | ARM9 / ARM11，加 DSP / Jazelle（Java accel） |
| ARMv6 | 2002 | ARM11，**SIMD 雛形**、Thumb-2（混 16/32-bit） |
| ARMv6-M | 2007 | Cortex-M0/M1 — **profile 概念正式引入** |
| ARMv7-A | 2007 | Cortex-A8/A9/A15，VFPv3、NEON、LPAE |
| ARMv7-R | 2008 | Cortex-R4/R5，real-time 用 |
| ARMv7-M | 2007 | Cortex-M3/M4 |
| **ARMv8-A** | 2011 | **首次 64-bit（AArch64）**、Cortex-A53 起 |
| ARMv8-M | 2016 | Cortex-M23/M33，加 TrustZone for M |
| ARMv9-A | 2021 | SVE2、CCA（confidential compute） |

關鍵分水嶺是 **ARMv8-A**：64-bit（AArch64）的引入讓 ARM 真正進入伺服器、PC 戰場。本課的 A profile 內容主要講 AArch64。

## A、R、M 三個 profile

ARMv7 開始 ARM 把 ISA 拆成三個 profile，**指令大宗共用，但週邊與用途完全不同**。

```
       ┌─────────────────────────────────────────┐
       │           ARM ISA 共用基礎              │
       │ (load/store, ALU, branch, condition)    │
       └────────────┬───────────┬────────────────┘
                    │           │           │
               ┌────▼────┐ ┌────▼────┐ ┌────▼────┐
               │    A    │ │    R    │ │    M    │
               │Application│ Real-time│ Microcontr.│
               └─────────┘ └─────────┘ └─────────┘
               MMU + EL    MPU       MPU + NVIC
               跑 Linux/    硬即時、 即時、低延遲
                Android     高安全   中斷、低功耗
                iOS、伺服器  汽車、 STM32、Pico、
                            航太     感測器
```

| Profile | 代表型號 | 有 MMU？ | 有 OS？ | 學了能做什麼 |
|---|---|---|---|---|
| **A** (Application) | Cortex-A53/A72/A78、Apple M、Graviton | 有 | Linux、iOS、Android | kernel、driver、hypervisor、TEE |
| **R** (Real-time) | Cortex-R5/R52 | 沒有，只有 MPU | RTOS / bare-metal | 汽車 ECU、5G baseband、儲存控制器 |
| **M** (Microcontroller) | Cortex-M0/M3/M4/M7/M33 | 沒有，可選 MPU | bare-metal、FreeRTOS、Zephyr | 韌體、感測器、IoT、馬達控制 |

**這門課做 A 與 M，跳過 R**。R profile 在嵌入式領域很重要但相對小眾，且大多概念會被 M（即時、中斷）+ A（部分機制）覆蓋。

## Cortex 命名解碼

「Cortex-A53」「Cortex-M4」這些型號常讓人困惑，看一下命名規律：

```
Cortex- <profile> <代號>
        │         │
        │         └── 不是版本號，是「型號」
        │             數字大不一定 ISA 新
        └── A / R / M profile
```

- **Cortex-A 系列**：A5 < A7 < A53 < A57 < A72 < A78 < X1 < X3...
  - A5/A7 是 ARMv7-A，A53 起是 ARMv8-A
  - A53/A72 是「big.LITTLE」的經典組合（A53 small core，A72 big core）
  - **X 系列**：A 系列高端（如 Cortex-X3），給旗艦手機

- **Cortex-M 系列**：M0/M0+/M1 < M3/M4 < M7 < M23/M33/M55/M85
  - M0/M0+/M1 是 ARMv6-M（最小核）
  - M3/M4 是 ARMv7-M
  - M23/M33 是 ARMv8-M（加 TrustZone）
  - M4 vs M3：M4 有 DSP/FPU，M3 沒有
  - M7 比 M4 強很多：dual-issue、cache、更高頻率

- **Neoverse 系列**：A 的伺服器版（N1/V1/N2 等），AWS Graviton 用的就是這條線

## 你常見的設備跑什麼？

| 設備 | 處理器 | ISA |
|---|---|---|
| iPhone 15 | Apple A17 Pro（自家設計） | ARMv8.6-A 變種 |
| Apple M3 Mac | Apple M3 | ARMv8.6-A 變種 |
| Pixel 8 | Google Tensor G3 | Cortex-X3 + A715 + A510 |
| Raspberry Pi 4 | Cortex-A72 | ARMv8-A |
| Raspberry Pi Pico | Cortex-M0+ × 2 | ARMv6-M |
| AWS Graviton 3 | Neoverse V1 | ARMv8.4-A + SVE |
| 多數 STM32F | Cortex-M0/M3/M4/M7 | ARMv6-M / v7-M |
| Tesla Model S MCU | NVIDIA Tegra（Cortex-A） | ARMv8-A |

## ARM 與政治、生態的故事

讀 ARM 也是讀產業史，挑幾個和工程師相關的：

**Apple Silicon (M1, 2020)**：Apple 用自家設計的 ARM 核取代 Intel x86 — 這對 PC 產業是地震級事件。Rosetta 2 把 x86_64 binary 動態 binary translation 到 ARM。如果你以為 ARM 只是手機 CPU，看 M1 的 benchmark。

**AWS Graviton (2018+)**：AWS 自家 ARM 伺服器晶片。EC2 的 Graviton 比同等 Intel 機型便宜 20% / 性能類似。**伺服器市場開始 ARM-ization**。

**Arm China 鬧劇 (2020-2022)**：ARM 公司中國子公司「安謀科技」原 CEO 吳雄昂拒絕被英國總部解雇，自己「分裂」成獨立公司，走上多年法律戰。直到 2022 才平息。這個故事告訴你：ARM 不是純技術公司，地緣政治敏感。

**NVIDIA 收購未遂 (2020-2022)**：NVIDIA 想以 $40B 買下 ARM，被英、美、歐、中監管反對，最終取消。原因：ARM 是中立 IP 提供商，被 NVIDIA 收購會破壞中立性。這個事件讓 ARM 後來走向 IPO。

**RISC-V 的崛起**：開源 ISA RISC-V 對 ARM 是真實威脅。ARM 的反應是「擴大 license 範圍 + 加速架構演進」，例如 SVE 推 SVE2、推 CCA。

讀 ARM 的時候保持這個背景：**它是一家賣 IP 的公司，每個架構決定都受商業、生態、地緣政治影響**，不是純技術選擇。

## 一個常見誤解

「ARM 是 RISC，所以指令一定簡單。」

不全然。**早期 ARM**（v4 / v5）很 RISC，但 ARMv7-A / v8-A 已經有非常複雜的指令（NEON SIMD、condition execute、巴塞爾移位、LSE atomics、SVE 變長向量）。

ARM 的設計哲學是「**RISC-like 但實用主義**」：核心指令是 RISC，但會為了效能、code density、特定應用加擴充。和 RISC-V「正交、模組化、儘量減少特例」是兩種不同哲學。Ch 35 會深談。

## 學 ARM 的順序建議

很多人卡在不知道從哪入手。本課給的順序：

1. **先打底 ISA**（Part 1）：暫存器、指令、ABI — 這是 A 與 M 共用的根
2. **接 M 線**（Part 2）：bare-metal 架構單純，從這裡入門啟動、向量表、linker script，痛點少
3. **再上 A 線**（Part 3）：MMU、EL、cache coherency、TrustZone — 概念多、抽象高，先有 M 經驗會比較容易消化
4. **最後是除錯**（Part 4）：寫了 M 與 A，再學 GDB / OpenOCD / JTAG / CoreSight 才有具體的 debug 對象

## 自我檢核

- [ ] 我能說出 ARM 公司的商業模式（賣 IP）和它為什麼贏
- [ ] 我能區分 ARMv8-A、Cortex-A72、AArch64 三個概念
- [ ] 我能說出 A/R/M profile 的差別
- [ ] 我知道 Cortex-M0/M3/M4/M7 大致對應哪個 ARMv 版本
- [ ] 我能說出 iPhone / Mac / Pi 4 / STM32 各自跑的 ISA

下一章正式進 ISA — 從 AArch64 暫存器與 ABI 開始，用你已經會的 x86_64 SysV ABI 對照講。

→ [Ch 2 AArch64 暫存器與 ABI](./02-aarch64-registers-abi.md)
