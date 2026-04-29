# Ch 34 — 怎麼讀 Arm Architecture Reference Manual

> 目標：教你怎麼**駕馭** Arm Architecture Reference Manual（俗稱「ARM ARM」）這本 9000+ 頁的怪物。哪些章節你必讀、哪些可跳、怎麼用搜尋、怎麼看 pseudo-code、怎麼從你工作的問題反向找到對應 section。

## ARM ARM 是什麼

正式名稱：**Arm Architecture Reference Manual for A-profile architecture**。

最新版（J.a, 2024）：**9536 頁**。這還只是 A-profile。M-profile 另有一本（短一點，1500+ 頁）。

下載：<https://developer.arm.com/documentation/ddi0487/latest>（免費，要登入）

## 為什麼這麼厚

ARM ARM 是**規範**，不是教科書。它必須：

1. 對每條指令給完整 pseudo-code
2. 對每個 system register 給每個 bit 的精確定義
3. 對所有可選擴展（FEAT_*）逐一說明
4. 對所有例外、trap、abort 提供完整 routing 規則
5. 涵蓋所有 EL × execution state × ISA 變種的組合

寫 hypervisor、kernel、firmware 時，**唯一可信來源**就是這本。教科書、教學影片都會過時或漏細節。

## 結構大綱

ARMv8 ARM 大致分這幾 part：

```
Part A：A-profile Architecture
   章節：架構概覽、execution state、暫存器、PSTATE
        通用指令、運算、load/store、條件
        例外、interrupt、debug
        memory model、cache、TLB
        system register 一覽
        ASCII art 不少（CPU state diagram）

Part B：A-profile Architecture extensions
   章節：擴展與選項（FEAT_PAuth, FEAT_MTE, FEAT_SVE2 等）
        每個 FEAT_X 自己一節，幾十到幾百頁
        新指令、新 register、新 trap routing

Part C：A64 instruction set encoding & semantics
   每條指令一頁：encoding、syntax、operation pseudo-code
        ALU、load/store、branch、SIMD、system 等

Part D：A32 / T32 instruction set
   AArch32 編碼與語意（ARMv7 兼容部分）

Part E：System Operations
   MMU、cache 操作詳細語意
   ASID / VMID、stage-1/2 page table walk 公式
   memory attribute 細節

Part F：Debug Architecture
   外部 debug、self-hosted debug
   breakpoint / watchpoint comparator 行為

Part G：Security
   TrustZone、PAC、Secure EL2

Part H：Other (PMU、generic timer、GIC)

Glossary、appendices
```

## 哪些章節你絕對要看

如果你做 **kernel / hypervisor / firmware**：

- **Chapter B1**：Exception model — 必讀
- **Chapter D5**：Virtual memory system — MMU 全套
- **Chapter D7**：Memory ordering — 最讓人困惑但必懂
- **Chapter D8**：Synchronization & barriers
- **Chapter D11**：Cache management & coherence
- **Chapter D13**：Generic Timer
- **Chapter D14**：Performance Monitor (PMU)

如果你做 **driver / 嵌入式**：

- **Chapter B5**：System register encoding
- **Chapter B14**：Generic Interrupt Controller
- **Part C 對應你關心的指令章節**

如果你做 **debug / tooling**：

- **Part F**：Debug Architecture 全部

## 哪些章節你**不必讀**

- **Part D 的 AArch32 完整章節**：除非你做 ARMv7 移植
- **Part B 的 SVE 章節**：用不到 SVE 的話跳過
- **某些不應實作的 deprecated 行為**：標 IMPLEMENTATION DEFINED 或 DEPRECATED

## 怎麼用 PDF 搜尋

PDF 是你最好的朋友。常用 query：

- 想知道某 system register：搜 register 名字（如 `TTBR0_EL1`）
- 想知道某指令：搜指令名 + ` instruction page`（如 `LDXR instruction page`）
- 想知道某擴展：搜 `FEAT_<X>`（如 `FEAT_PAuth`）
- 找 trap routing：搜 `EC ==` 找對應 syndrome class

**搜 `pseudocode for`** 找指令 / 系統 op 的語意 (各種 pseudocode 描述都用這個 prefix）。

## 看 pseudo-code 的方法

ARM ARM 用一種類 Ada / 偽 Pascal 的語言寫 pseudocode：

```
LDR(指令簡化版):
   address = X[base];
   if write_back_pre_index then
       address = address + offset;
       X[base] = address;
   end
   data = MemA[address, 8];
   X[t] = data;
   if write_back_post_index then
       address = address + offset;
       X[base] = address;
   end
```

關鍵：

- `X[n]` 表示 X 暫存器
- `MemA[addr, size]` 表示 architectural memory access
- `X[base] = ...` 表示寫回
- `if ... then ... end` Pascal-ish

**讀懂 pseudocode = 讀懂指令的精確語意**。教科書只給直覺，這裡給法律 spec。

## 一個範例查詢：LSE atomic LDADD

問題：「`LDADD` 行為什麼？」

1. 搜 `LDADD instruction page` → Part C 找到
2. 看 syntax：`LDADD <Ws>, <Wt>, [<Xn|SP>]`
3. 看 pseudocode：

```
LDADD Operation:
   bits(datasize) data;
   data = X[s, datasize];          // 讀 source operand
   bits(datasize) value;
   value = MemA[X[n], datasize];   // load
   value = value + data;
   MemA[X[n], datasize] = value;   // store
   X[t, datasize] = value(舊);     // return old (如果 t != 31)
```

明確語意：load、加、store、回傳舊值。**所有變體（CASA、SWPL 等）的差別寫在「Operation」末尾的 acquire / release annotation**。

## System register 的查法

每個 system register 有專門 page：

- 名字、長度、access permission（哪 EL 可讀寫）
- 每個 bit field 名字、意義、reset value
- Trap behavior（哪些 trap bit 影響它）
- 對應的 system instruction 編碼

範例：`TTBR0_EL1`：

- 64-bit
- bits[47:1] BADDR (base address)
- bits[63:48] ASID
- 由 EL1+ 讀寫，EL0 trap 到 EL1（依 SCTLR.UCI）

## Exception class (EC) 表

debug exception / fault 時必查：

- Part B chapter on exception entry
- 找 「ESR_ELx, EC field」表
- 比對 EC value 找對應的 syndrome class
- ISS field 的解析規則在對應 sub-section

## 不要從頭讀到尾

ARM ARM 不是給你**通讀**的書。**用法是 reference**：

1. 工作中遇到問題（「為什麼 IRQ 沒被 trap 到 EL2？」）
2. 找對應 chapter（「EL2 routing」）
3. 跳到 routing rule
4. 對照 system register（HCR_EL2.IMO 等）
5. 把問題解決
6. 關 PDF

通讀 9000 頁會瘋，**精準切入** 才是技能。

## ARMv8-M / Cortex-M 的 manual

A-profile ARM ARM 不適用 Cortex-M。M 用 separate manual：

- **Arm v8-M Architecture Reference Manual**：<https://developer.arm.com/documentation/ddi0553/latest>
- 約 1500 頁，相對短
- 涵蓋 Cortex-M23/M33/M55/M85 等

加上 chip-specific manual：

- **Cortex-M3 Technical Reference Manual**：架構級
- **STM32F4xx Reference Manual**：周邊細節
- **STM32F407xx Datasheet**：pin / 電氣

寫嵌入式三本手冊一起翻是常態。

## 一個常見誤解

「我可以靠 stack overflow / blog / AI 寫程式，不用讀 ARM ARM 吧？」

**多數時候可以，但邊角案例你會撞牆**。

stack overflow / AI 給的答案常忽略：

- 特定 chip revision 的 errata
- 細節 bit field 的 reset value
- 某 EL 對某 register 的 trap 條件
- IMPLEMENTATION DEFINED 行為（某些事不規定，廠商各做各的）

**問你自己：寫 firmware 的人是不是要 100% 確定行為？** 是的話 ARM ARM 是唯一可信來源。

## 我的個人讀法

1. 第一次看：花 30 分鐘掃 TOC，知道哪些 part 大致講什麼
2. 工作中需要：**搜尋切入**，看 5–20 頁解決問題就跳出
3. 有時間時：挑一兩章「系統地」讀（如 Memory Model 整章）— 這種「深度章節」一次讀完比片斷查強
4. 看 errata：對你的 chip 至少看一遍，記住 affected version

## 自我檢核

- [ ] 我能說出 ARM ARM 的 Part A-H 大致對應什麼
- [ ] 我能找一個 system register（如 TTBR0_EL1）的描述頁
- [ ] 我能讀懂一條指令的 pseudocode（如 LDADD）
- [ ] 我知道 EC field 在哪個 chapter 找對照表
- [ ] 我能區分 A-profile 與 M-profile manual
- [ ] 我接受「ARM ARM 是 reference 不是 textbook」

下一章是這門課最後一章 — 反思 ARM、x86、RISC-V 三家 ISA 的設計哲學差異與商業生態。

→ [Ch 35 反思：ARM vs x86 vs RISC-V](./35-vs-x86-riscv.md)
