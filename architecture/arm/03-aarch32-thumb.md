# Ch 3 — AArch32 與 Thumb / Thumb-2

> 目標：搞懂 AArch32（A32 + T32）的暫存器、ARM/Thumb 切換、為什麼 Cortex-M 只跑 Thumb，以及 Thumb-2 是怎麼把 16-bit 與 32-bit 編碼混在一起的。

## AArch32 的暫存器布局

```
R0 ─ R12      通用 32-bit 暫存器
R13 = SP      Stack Pointer
R14 = LR      Link Register
R15 = PC      Program Counter（可直接讀寫！）
CPSR          Current Program Status Register
SPSR          Saved Program Status（每種 mode 一份）
```

對比 AArch64：暫存器少（13 個通用 vs 31 個）、PC 是通用暫存器、狀態合在一個 CPSR 裡。

CPSR 內容：

```
 31 30 29 28 27        24       9 8 7 6 5      0
┌──┬──┬──┬──┬──┬─────┬────────┬─┬─┬─┬─┬──────┐
│ N│ Z│ C│ V│ Q│ ... │   IT   │E│A│I│F│ Mode │
└──┴──┴──┴──┴──┴─────┴────────┴─┴─┴─┴─┴──────┘
N/Z/C/V: 算術 flag
Q:       saturation flag (DSP)
IT:      If-Then 區塊狀態（Thumb-2 才有）
E:       endian
A/I/F:   masks (SError / IRQ / FIQ)
Mode:    User/FIQ/IRQ/Supervisor/Abort/Undef/System
```

**Mode 是 AArch32 特有的概念**：USR / FIQ / IRQ / SVC / ABT / UND / SYS / HYP / MON 共 9 種。AArch64 把這個改成 EL0–EL3 + Selected SP，更乾淨。

## Banked register：mode 切換時改寫什麼？

AArch32 處理例外時，**部分暫存器在不同 mode 下是「分身」**：

```
USR mode:   R0 R1 R2 ... R12  R13_usr  R14_usr  R15  CPSR
FIQ mode:   R0 R1 R2 ... R7   R8_fiq R9_fiq R10_fiq R11_fiq R12_fiq
                                R13_fiq  R14_fiq  R15  SPSR_fiq
IRQ mode:   R0 ─ R12          R13_irq  R14_irq  R15  SPSR_irq
SVC mode:   R0 ─ R12          R13_svc  R14_svc  R15  SPSR_svc
...
```

意思是：進 IRQ mode 時，CPU 自動改用 `R13_irq` 當 SP、`R14_irq` 當 LR；FIQ 還多 banked R8–R12（為了快，FIQ 可以不存暫存器）。

這個設計是 ARMv4–v7 的傳統，**到 AArch64 完全砍掉**：AArch64 只有 SP_EL0/EL1/EL2/EL3 是 banked，其他暫存器全共用，由軟體負責保存。設計趨勢是更簡潔。

## 兩種 ISA：A32 vs T32（Thumb）

ARMv7-A 同時支援兩種指令編碼：

| ISA | 指令寬 | 暫存器存取 | code density |
|---|---|---|---|
| **A32** (ARM mode) | 固定 32-bit | R0–R15 全可用 | 較大 |
| **T32** (Thumb mode) | 16-bit / 32-bit 混合 | 16-bit 指令多數限 R0–R7 | 較小（~30% 省） |

Thumb 1990 年代為「嵌入式市場省 ROM」設計：16-bit 編碼，code size 大幅縮小。但能編碼的指令受限（只能用 R0–R7、立即數小、條件少）。

**Thumb-2**（ARMv6-T2、ARMv7 起）解決這個限制：**16-bit 與 32-bit 指令混在同一個指令流裡**。常用簡單指令繼續 16-bit、複雜指令用 32-bit 形式，整體 code density 接近 Thumb，能力接近 A32。

## ARM/Thumb 切換：interworking

A32 與 T32 在記憶體裡是不同編碼，CPU 怎麼知道要用哪一個？答案藏在**目標位址的最低 bit**：

```
BX R0     ; Branch and eXchange instruction set
          ; 跳到 R0，且根據 R0 bit[0] 切換 ISA：
          ;   bit[0] = 1 → 切 Thumb（PC = R0 & ~1）
          ;   bit[0] = 0 → 切 ARM（PC = R0）
```

**指令位址永遠是偶數**（ARM 4-byte 對齊，Thumb 2-byte 對齊），所以 bit[0] 不是真的位址，是 ISA marker。

寫組語時要注意函式指標的 bit[0]：

```c
void (*func)(void) = (void(*)(void))0x08001234;   // ARM 程式碼
void (*func)(void) = (void(*)(void))0x08001235;   // Thumb 程式碼（位址 +1）
```

linker 與 toolchain 通常自動處理，但你手刻向量表或 jump table 時可能要自己 OR 上 1。Cortex-M 的 reset vector 寫位址必須 +1（因為 Cortex-M 永遠 Thumb）— Ch 9 會看到這個踩雷點。

## Cortex-M 為什麼只有 Thumb？

Cortex-M 的 ISA **只有 T32（Thumb-2）**，沒有 A32 mode、沒有 BX 切換。

理由：

1. **code density 對 MCU 是生死問題**：MCU flash 從 8 KB 起跳，能多塞點程式 = 多功能。Thumb-2 比 A32 省約 30% code size。
2. **複雜度減少**：不用 banked register、不用 mode 切換、不用 ISA 切換。整個架構簡潔，適合即時與低功耗。
3. **指令集足夠**：Thumb-2 已經能做大部分 MCU 任務（ALU、load/store、浮點、DSP — 後者在 M4/M7 用 Helium 或 NEON 補）。

這帶來一個有趣後果：**Cortex-M 反而更純的 RISC**。沒有 A32 的條件執行（除了 `IT` block 殘留）、沒有複雜定址模式。

## Thumb-2 的 IT block：條件執行的回光返照

A32 一個招牌特性是**幾乎每條指令都可以條件執行**：

```asm
; A32
cmp r0, #0
moveq r1, #5     ; r0 == 0 才執行
movne r1, #6     ; r0 != 0 才執行
```

這在 16-bit Thumb 編碼中放不下。Thumb-2 加了 **IT (If-Then) block** 來模擬：

```asm
; Thumb-2
cmp  r0, #0
itte eq          ; If-Then-Then-Else for "eq"
moveq r1, #5     ; eq 條件
moveq r2, #5     ; eq 條件（同 IT block 第 2 條）
movne r3, #6     ; ne 條件（else）
```

`ITTE` 編碼了「下面 3 條指令的條件模式」：T (then) / E (else)。最多覆蓋 4 條後續指令。

**ARMv8 的趨勢是廢棄 IT block**：AArch64 只剩 `csel` (conditional select) / `cinc` 等少數條件指令；ARMv8.1-M 也標記 IT block deprecated。原因：複雜度高、對分支預測器不友善、現代編譯器寧可走無條件分支配 cmov。Ch 5 會深談。

## Thumb 指令編碼：怎麼判斷 16 vs 32-bit？

CPU 在 Thumb mode 看一條指令，怎麼知道是 16-bit 還是 32-bit？看**前 5 bit (halfword 0 的 bits[15:11])**：

```
0xE800 ─ 0xFFFF   (即 11101 / 11110 / 11111 開頭)
                  → 32-bit 指令，後面跟著第二個 halfword
其它              → 16-bit 指令
```

寫組語你不用管這個，組譯器會幫你。但 disassembly 看到 `0xF000` 開頭的 word 就要知道是 32-bit Thumb-2 指令。

## 一個 Hello 對照：ARM mode vs Thumb mode

```c
int add(int a, int b) { return a + b; }
```

**A32 編碼（`-marm`）**：

```asm
add:
    add  r0, r0, r1     ; e0800001 (4 bytes)
    bx   lr             ; e12fff1e (4 bytes)
```

**Thumb-2 編碼（`-mthumb`）**：

```asm
add:
    adds r0, r0, r1     ; 1840    (2 bytes)
    bx   lr             ; 4770    (2 bytes)
```

Thumb 把同樣的事情用一半 byte 完成。記住這也有代價：每條 Thumb 指令最多 8 個暫存器（R0–R7）能用，要動到 R8–R12 必須走 32-bit Thumb-2 編碼。

## CPSR vs PSTATE：歷史遺留

AArch32 把全狀態合在 CPSR 一個 32-bit register；AArch64 拆成 NZCV / DAIF / SPSel / 等多個欄位的 PSTATE。

從硬體角度，**狀態不集中對流水線友善**：避免 partial flag stall（多條指令同時想改 flag 不同 bit 造成假相依）。從軟體角度，要 atomically 改 CPSR 的某個 bit，AArch32 要用 `MSR CPSR_c, ...`（用 mask 標哪個 byte），AArch64 用 `MSR DAIFSet/Clr` 直接改 mask、用 `MSR NZCV` 改 flag。

## 一個常見誤解

「Cortex-M 是不是性能很差所以只能 Thumb？」

倒過來。Cortex-M 設計目標就是「**MCU 級的低功耗、低成本、小面積**」，**故意只實作 Thumb-2** 換取簡潔。Cortex-M7（高階 M）跑 600 MHz、有 cache、有 SIMD（Helium / Cortex-M55 起），不弱。

只是 Thumb-2 是 MCU 場景下「夠用且省空間」的最佳解。如果一顆晶片做手機 SoC 才需要 A32 + AArch64 的能力光譜。

## 自我檢核

- [ ] 我能列出 AArch32 的暫存器與它們和 AArch64 的對應
- [ ] 我能解釋 banked register 是什麼以及 AArch64 為什麼砍掉
- [ ] 我能說出 ARM / Thumb 切換的 BX 指令做了什麼
- [ ] 我能解釋為什麼 Cortex-M 永遠 Thumb
- [ ] 我能解釋 Thumb-2 的 16/32-bit 混合編碼
- [ ] 我看得出 IT block 的語意

下一章我們從 ISA 形態進入 ISA 行為 — load-store 架構、定址模式、為什麼 ARM 不能 `add [mem], r0`。

→ [Ch 4 Load-Store 架構與定址模式](./04-load-store-addressing.md)
