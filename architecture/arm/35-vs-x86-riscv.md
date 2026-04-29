# Ch 35 — 反思：ARM vs x86 vs RISC-V

> 目標：把整門課收尾。對 ARM 與 x86_64、RISC-V 做設計哲學、商業模式、生態與未來走向的對比。寫了底層程式碼後再回頭看，每一條取捨都有故事。

## 三家 ISA 的時代背景

```
1978  Intel 8086 — x86 起點
      Motorola 68000 一路被 Intel 擠到邊緣

1985  Acorn ARM1 — 英國學界產物
      RISC 思想（Berkeley、Stanford 同期）

2010  RISC-V 誕生 — Berkeley 教學需求
      不滿意 ARM、MIPS license 與封閉

2015+ 三足鼎立
      x86 仍佔 PC / server，ARM 統治移動 + 進攻 server
      RISC-V 從學界走向 IoT、HPC、AI 加速器
```

## ISA 設計哲學對照

### x86：不刪除任何過去

x86 從 1978 演化至今，**保持向下兼容 40 年以上**：

- 16-bit Real mode 還在（每顆現代 x86 都能跑 DOS）
- VEX/EVEX 加在舊指令前面繼續用
- string instructions（rep movsb）至今活著
- segment register 仍能讀寫

代價：
- 解碼器極複雜（指令長度 1-15 byte，prefix bytes 可疊）
- microarchitecture 工程師要支援所有歷史指令
- 編碼空間擁擠

收益：
- 最強 binary 兼容
- 既有 software 投資保留
- 工具鏈（gcc / icc / msvc）成熟到極致

### ARM：階段性大重整 + 兼容

ARM **每幾代做一次大重整**：

- ARMv4：Thumb 引入
- ARMv6：SIMD 雛形
- ARMv7：Thumb-2 取代 Thumb
- ARMv8：AArch64 全新 ISA，AArch32 並存
- ARMv9：SVE2、CCA 等新功能

兼容性靠「**execution state 切換**」：v8-A core 可在 EL 不同層跑 AArch32 或 AArch64。

代價：
- 多個 ISA 版本並存增加複雜度
- 工具鏈要支援 A32/T32/A64

收益：
- 隔代清理 cruft（如 AArch64 砍 condition exec、banked register）
- ISA 優雅度高
- microarchitecture 設計負擔小

### RISC-V：模組化、永遠擴展

RISC-V 的核心理念：**ISA 是底子，擴展是組合**

- 基底 RV32I / RV64I 只有 47 條指令（**真的只有 47 條**）
- M（mul/div）、A（atomic）、F（float）、D（double）、C（compress）等是 optional 擴展
- 廠商可加 custom extension（custom-0/1/2/3 編碼空間）
- 標準擴展不斷加：B（bit）、V（vector）、Zk（crypto）、H（hypervisor）...

代價：
- 「合理的 RISC-V」其實要 RV64GC + V + B + Zk = 大堆擴展
- 多種組合的軟體支援碎片化
- vendor 之間的 custom 擴展不通

收益：
- 教育用途無敵（從 RV32I 47 條開始）
- ISA license 為零（開放）
- 任何想做 SoC 的都能用，不用付 ARM
- 可深度 customize（AI 加速器、儲存控制器等）

## 暫存器 vs 指令數量對照

```
                x86_64    ARM AArch64    RISC-V RV64GC
通用暫存器數    16        31             31 + zero (32 名義上)
SIMD reg 數     16/32     32             32（加 V 擴展再 32）
基礎指令數      ~700+     ~250           47 (RV64I) + 擴展
條件碼?         有 RFLAGS 有 NZCV         無（用 cmp+branch）
指令長度        1-15 byte 4 byte（A64）   2/4 byte
```

**指令數**直觀但不公平 — x86 包含古老 + SIMD + AVX-512 + ...，ARM 不算 NEON ~250 + NEON ~600，加起來相當。

**設計差異**核心：

- x86 富指令（每條指令做多事）
- ARM 中庸（RISC 但有 SIMD、原子、條件 select）
- RISC-V 精簡（標準指令只做基本，擴展補 SIMD）

## 商業模式：付錢 vs 開放

```
x86：Intel + AMD 雙寡頭
  Intel 不賣 license（除歷史 OEM）
  AMD 透過 cross-license 才能做
  競爭者進不來（中國海光、兆芯走 license 縫隙）

ARM：賣 license
  ISA license + Core IP license
  Apple、Qualcomm 高端走 ISA license（自家設計）
  聯發科、瑞昱、ST 等走 Core IP（買 Cortex 直接用）
  賣 IP 比賣晶片利潤高、可擴大生態

RISC-V：完全開放
  RISC-V International 管 ISA spec
  任何人 Verilog 寫一個 RV32I 都可以叫「RISC-V」
  廠商：SiFive, Andes（晶心）, T-Head（阿里平頭哥）
        卓力（OpenHW）, ETH Zurich, ...
  社群驅動 + 開源工具鏈
```

## 生態與軟體支援

| | x86 | ARM | RISC-V |
|---|---|---|---|
| Linux distro 主流支援 | ✅ Ubuntu/Fedora 全 | ✅ Ubuntu/Fedora 全 | ⚠️ Debian/Ubuntu 開始有 |
| Windows | ✅ | ✅ Windows on ARM | ❌ |
| macOS | ❌ (M1 後沒了) | ✅ Apple Silicon | ❌ |
| Android | (x86 emulator) | ✅ | 還在 emulator 階段 |
| 雲端 server | ✅ | ✅ AWS Graviton, Azure ARM | ⚠️ AWS 有 Sapphire 試水 |
| 嵌入式 MCU | ❌（太貴） | ✅ STM32 / NXP / Pico | ⚠️ ESP32-C3 / GD32V |
| HPC | ✅ Intel | ✅ Fujitsu A64FX 富岳 | ❌ |

## 性能：誰快？

短答：**取決於 workload + 微架構**。

詳答：

- **integer single-thread**：Apple M3 / Intel i9 / AMD 7950X 互有勝負
- **float SIMD-heavy**：Intel AVX-512 在 server 強，Apple M / AWS Graviton 在 SVE-light workload 接近
- **多核 server**：AWS Graviton 3 / Ampere Altra / Neoverse V2 性價比超 Intel Xeon
- **電源效率**：ARM 多核小核 配置 efficiency 領先，特別在 web / inference

「x86 比較快」是 2010 年代陳舊印象。**現在 ARM 在效能 / 瓦特 普遍勝**，純效能也接近。

## 安全特性對照

| | x86 | ARM | RISC-V |
|---|---|---|---|
| Pointer auth | ❌ | ✅ PAC | 提案中 (Zicfilp / Zicfiss) |
| Branch target ID | ✅ CET IBT | ✅ BTI | ✅ Zicfilp |
| Memory tagging | ❌ | ✅ MTE | 提案中 |
| TEE | SGX (deprecated)、TDX | TrustZone | Keystone 等 |
| Supervisor mode | Ring 0/3 + VMX | EL0/1/2/3 | M/S/U mode |

ARM 在 hardware safety 走在前面（PAC / MTE），x86 跟隨，RISC-V 走得最慢但模組化程度高。

## 未來預測（有風險，但說了）

**5 年內**：
- ARM 在 server 持續吃 Intel 市場（AWS Graviton 5、Azure Cobalt、Google Axion）
- Apple Silicon 持續主導 PC 高階
- RISC-V 在 IoT / 嵌入式 MCU 崛起（取代 ARM Cortex-M0/M3 一部分）
- AI 加速器 RISC-V 化（不付 ARM license 對 startups 重要）

**10 年內**：
- RISC-V 進 server 是真實可能性（已有 Ventana / Rivos / Tenstorrent 競爭）
- x86 仍會在 Windows desktop 與部分高效能 PC 留存
- ARM 與 RISC-V 平行發展，不會其中一家「贏」

**但**：硬體與軟體生態演化慢，今天說的「未來」5 年後可能完全不一樣。1990 年代沒人預測到 x86 會吃下 supercomputer 市場（從 SGI / Sun）。歷史會 surprise 你。

## 給工程師的選擇建議

如果你**今天決定學一個 ISA**：

- 寫嵌入式 / IoT：**ARM Cortex-M**（市場最大、教材最多、工具最成熟）
- 寫 kernel / hypervisor：**ARM AArch64**（Linux 主場已轉，蘋果 + AWS + 雲都在這）
- 對 ISA 學術 / 工具鏈感興趣：**RISC-V**（規範易讀、可深入到 verification）
- 維護 legacy / 寫 OS X / Windows native：**x86_64**（仍多 PC / server）

**全部都學一些**是最有彈性的策略。本課把 ARM 學透是個好基礎；之後加學 RISC-V（已經有 RISC-V 課程在這 repo）你會看到「同個問題兩種解」，理解更深。

## 一些有趣的對比例子

### Stack pointer 設計

- x86：RSP 是普通 GP register，可以 mov 隨便改
- ARM：SP 是專用 register（不在 X0–X30 內），有 alignment 強制
- RISC-V：SP 用 X2，慣例上是 SP 但 ISA 不強制

ARM 強制 SP 16-byte 對齊；x86 沒強制（但 ABI 要 16-byte）；RISC-V 純慣例。

### 條件分支

- x86：`cmp + jcc`，flags 隱含
- ARM A32：condition execution 加 NZCV，AArch64 用 csel
- RISC-V：直接 `beq rs1, rs2, label`，沒 flags

ARM AArch64 與 x86 走中間路線（保 flags），RISC-V 完全砍掉 flags。

### Atomic

- x86：`lock cmpxchg` 等 RMW + barrier
- ARM：LL/SC 為主，LSE 補強
- RISC-V：LL/SC（lr.w / sc.w）+ A 擴展 amo

實作策略各不同，但 lock-free 程式可移植性都靠 C++ atomic 抽象。

## 自我檢核（也是課程結業檢核）

- [ ] 我能說出三家 ISA 的設計哲學差異
- [ ] 我能比較三家的商業模式
- [ ] 我能說出 ARM 為什麼在 server 與行動上站住腳
- [ ] 我能識別「這個機制 ARM/x86/RISC-V 各怎麼做」（如 atomic、SIMD、特權層級）
- [ ] 我能告訴一個朋友「為什麼學 ARM 值得」
- [ ] 我寫過 Cortex-M bare-metal 韌體
- [ ] 我設過 AArch64 page table、開過 MMU
- [ ] 我用 GDB + OpenOCD debug 過實機 / QEMU 程式
- [ ] 我看 ARM ARM 不再害怕

## 結業

恭喜你看到這裡。這 35 章 + 3 練習 + 1 final project（接下來），你應該已經：

- 看到 STM32 / Pico bare-metal code 不會卡
- 讀 Linux ARM kernel 開機 sequence 看得懂大致
- debug 嵌入式 firmware 知道用什麼工具、設什麼斷點
- 看 ARM ARM 知道怎麼搜、怎麼跳
- 寫 lock-free / SIMD code 不踩 memory model 雷

ARM 的世界很大 — KVM/Xen on ARM、TrustZone TA 開發、Linux ARM driver、嵌入式 RTOS 移植、SoC bring-up — 這門課給你的是入場券，往後的深度 specialization 各自展開。

最後一個關卡：**Final Project**。我們把所學集大成，刻一個迷你 RTOS。

→ [Final Project：Cortex-M3 Mini RTOS-lite](./final-project-mini-rtos.md)
