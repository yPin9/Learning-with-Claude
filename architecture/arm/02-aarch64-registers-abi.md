# Ch 2 — AArch64 暫存器與 ABI

> 目標：把 AArch64 的暫存器布局、命名、用途約定一次釐清。我們用 x86_64 SysV ABI 當對照組 — 你已經會的部分省講，差別與 ARM 特有的部分多花筆墨。

## 通用暫存器：31 個 + 一個怪胎

AArch64 有 **31 個通用暫存器**，外加一個特殊的 zero / SP register。

```
X0  ─ X30   通用 64-bit 暫存器
X0 / W0      X 是 64-bit 全名，W 是低 32-bit 別名
X30 = LR     Link Register（呼叫返回位址）
SP           Stack Pointer（不在 X0-X30 編號內）
XZR / WZR    讀都得 0、寫被丟棄的「零暫存器」
PC           程式計數器（不能直接讀寫，與 ARMv7-A 不同）
```

幾個重點：

1. **X0–X30 是 64-bit，W0–W30 是同一暫存器的低 32-bit**。寫 W 暫存器會自動把高 32 bit 清零（**這個和 x86_64 寫 32-bit 寄存器相同**：`mov eax, ...` 也會清 RAX 高 32 bit）。
2. **31 號編碼是怪胎**：encoding 上的 `R31` 在不同指令裡可能解釋為 SP（如 `add sp, sp, #16`）或 XZR（如 `mov x0, xzr`）。**沒有「31 號通用暫存器」這回事**。
3. **PC 在 AArch64 不能直接讀寫**：要拿目前 PC 用 `adr` / `adrp` 算 PC-relative 位址。**這是 ARMv8 重大改動**，ARMv7 的 `mov pc, lr` 那種寫法在 AArch64 不存在。

## 對照 x86_64：暫存器數量與規律

```
x86_64 SysV ABI 通用暫存器（16 個）：
  RAX RBX RCX RDX RSI RDI RBP RSP
  R8  R9  R10 R11 R12 R13 R14 R15

AArch64 通用暫存器（31 個）：
  X0  X1  X2  X3  X4  X5  X6  X7   ← 函式參數 / 回傳
  X8                                ← 間接結果暫存器（大型回傳的指標）
  X9  X10 X11 X12 X13 X14 X15      ← caller-saved 臨時
  X16 X17                          ← intra-call temp / IPC scratch（IP0 IP1）
  X18                              ← 平台保留（kernel TLS 等）
  X19 X20 X21 X22 X23 X24 X25 X26 X27 X28  ← callee-saved
  X29 = FP                         ← Frame Pointer
  X30 = LR                         ← Link Register
  SP                               ← Stack Pointer
```

**從 16 個變 31 個**，這是 ARM 設計關鍵差異之一：

- **暫存器多 → 函式參數可以全用暫存器傳**：x86_64 SysV 前 6 個 int 用暫存器（RDI/RSI/RDX/RCX/R8/R9），第 7 個開始進棧。**AArch64 前 8 個用 X0–X7**。
- **暫存器多 → spill 到棧的次數少**：函式體中變數可以盡量留在暫存器，不必常進出棧。這是 RISC 的優勢之一。
- **代價是 instruction encoding 要花更多 bit 編碼暫存器**：AArch64 用 5 bit 編碼（2^5 = 32），x86_64 大多 3 或 4 bit。

## ABI：誰負責保存什麼

```
              Caller-saved          Callee-saved
              (volatile)            (non-volatile)
              ─────────────         ─────────────
AArch64       X0–X18                X19–X28, X29 (FP), X30 (LR)
x86_64 SysV   RAX, RCX, RDX,        RBX, RBP, R12–R15
              RSI, RDI, R8–R11
```

**caller-saved**：呼叫者要自己備份（如果還想用），被呼叫者可以隨意改寫。
**callee-saved**：被呼叫者如果要用，必須先存起來，回去前還原。

注意 AArch64 把 `X30`（LR）也算 callee-saved — 因為被呼叫者一旦再 call 別人，LR 就被覆蓋，所以它有義務先存起來。實務上的 prologue 是：

```asm
sub  sp, sp, #16          ; 開棧
stp  x29, x30, [sp]       ; 同時存 FP 與 LR
mov  x29, sp              ; 設新 frame pointer
... 函式內容 ...
ldp  x29, x30, [sp]       ; 還原
add  sp, sp, #16
ret
```

**STP / LDP** 是 ARM 的「成對 load/store」— 一條指令搬兩個暫存器，這是 ARM 為了減少 prologue/epilogue 開銷設計的。x86 沒有對應指令。

## 浮點與 SIMD：V0–V31

```
V0 ─ V31     128-bit SIMD/FP 暫存器
B0 / H0 / S0 / D0 / Q0   是 V0 的不同寬度視圖：
  B = 8-bit   H = 16-bit   S = 32-bit float
  D = 64-bit double        Q = 128-bit (full V)
```

32 個 SIMD 暫存器，比 x86 SSE 多一倍（x86_64 是 XMM0–15，AVX-512 才到 ZMM0–31）。

**ABI 規定**：V0–V7 傳浮點參數與回傳；V8–V15 是 callee-saved 的低 64-bit（V16–V31 全部 caller-saved）。注意 V8–V15 **只有低 64-bit** 是 callee-saved，高 64-bit 仍 caller-saved，這在寫 NEON kernel 容易踩雷。

## 系統暫存器：用 MSR/MRS 存取

x86_64 用 `MSR` (model-specific register) 透過 `wrmsr`/`rdmsr` 存取；AArch64 也用 **MSR / MRS** 但語意不同：

```asm
mrs  x0, sctlr_el1     ; 讀 SCTLR_EL1 到 x0
msr  sctlr_el1, x0     ; 寫 x0 到 SCTLR_EL1
```

MRS = Move from System Register，MSR = Move to System Register（不是 x86 的 MSR 縮寫！）。

幾個常用系統暫存器後面會反覆出現：

| 暫存器 | 縮寫 | 用途 |
|---|---|---|
| `SCTLR_ELn` | System Control | MMU/cache 開關、endian、各種架構行為旗標 |
| `TTBR0_EL1` / `TTBR1_EL1` | Translation Table Base | 一級 page table 基底 |
| `TCR_EL1` | Translation Control | 分頁參數（granule、地址寬度） |
| `MAIR_EL1` | Memory Attribute Indirection | memory type 對照表 |
| `VBAR_ELn` | Vector Base Address | 例外向量表基底 |
| `CurrentEL` | 目前 EL | 看自己在哪個 exception level |
| `ESR_ELn` | Exception Syndrome | 例外原因 |
| `ELR_ELn` | Exception Link | 例外發生時的 PC（要回去哪） |
| `SPSR_ELn` | Saved Program Status | 例外發生時的 PSTATE 備份 |

`_ELn` 字尾代表「在 EL_n_ 下使用」。Ch 15 會把 EL 講清楚。

## PSTATE：替代 RFLAGS

x86 有 RFLAGS（CF / ZF / SF / OF / PF / IF 等）。AArch64 沒有單一 RFLAGS register，而是把狀態散在 **PSTATE** 概念裡：

```
NZCV     算術 flag（Negative, Zero, Carry, oVerflow）
DAIF     中斷 mask（Debug, SError, IRQ, FIQ）
EL       current exception level
SP       SP_EL0 還是 SP_ELx 選擇
nRW      AArch64(0) / AArch32(1)
PAN      Privileged Access Never
UAO      User Access Override
... 還有 BTI、SSBS 等
```

讀 NZCV：`mrs x0, nzcv`（後面 4 個 bit 才是 N/Z/C/V）。
讀全部 PSTATE：沒有單一指令，要分別讀每個欄位。

**這個設計反映 ARM 的選擇**：避免「一個 flag register 變成熱點」的問題（x86 有 partial flag stall 等微架構痛點）。

## AArch32 vs AArch64：暫存器之差

簡短對比，下一章 (Ch 3) 會再展開：

```
AArch32（ARMv7 / Cortex-M / 32-bit AArch64 兼容模式）：
  R0 ─ R12      通用
  R13 = SP, R14 = LR, R15 = PC   ← PC 是通用暫存器！
  CPSR          狀態暫存器（合在一起）

AArch64：
  X0 ─ X30, SP, XZR
  PC 不可直接讀寫
  PSTATE 拆成多個欄位
```

**最大差別**：AArch32 PC 是 R15，可以 `mov pc, r0` 跳轉；AArch64 PC 不可見，只能透過 `B`/`BR`/`RET` 等控制流指令改 PC。這個改動讓控制流更明確，也更利於 spectre 類緩解。

## 小範例：AArch64 的 hello function

```c
int add(int a, int b) {
    return a + b;
}

int main(void) {
    return add(3, 4);
}
```

`-O0` 編出來大概長這樣：

```asm
add:
    sub  sp, sp, #16
    str  w0, [sp, #12]      ; a
    str  w1, [sp, #8]       ; b
    ldr  w8, [sp, #12]
    ldr  w9, [sp, #8]
    add  w0, w8, w9         ; 結果放 w0（int 是 32-bit，用 W 暫存器）
    add  sp, sp, #16
    ret

main:
    sub  sp, sp, #16
    stp  x29, x30, [sp]
    mov  x29, sp
    mov  w0, #3
    mov  w1, #4
    bl   add                ; Branch with Link，把返回位址放 LR
    ldp  x29, x30, [sp]
    add  sp, sp, #16
    ret
```

對 x86_64 熟的人應該認得出大部分 — `mov`/`add`/`ret` 是直覺的，差別在 `BL` (Branch and Link，等於 x86 的 `call`)、`STP`/`LDP` (成對 load/store)、`SUB SP, SP, #16` 開棧（沒有 `push`/`pop`）。

**ARM 沒有 push/pop**：要靠 SP 加減 + 顯式 store/load。這是 RISC 的選擇 — push/pop 隱含 SP 修改是 CISC 思維。

## 一個常見誤解

「AArch64 是不是就是 ARMv8？」

不完全。**ARMv8-A 包含兩個執行狀態：AArch64（A64 ISA，64-bit）與 AArch32（A32/T32 ISA，32-bit，幾乎等同 ARMv7-A）**。一顆 ARMv8-A core 在不同 EL 可以跑不同狀態。

實務上現代 SoC（伺服器、手機）開機後幾乎全程 AArch64，AArch32 只在跑舊 32-bit Linux app 時切過去。所以**寫新程式只用 AArch64 是合理選擇**。

## 自我檢核

- [ ] 我能說出 X 與 W 暫存器的關係，以及寫 W 對 X 的副作用
- [ ] 我能背出哪些暫存器是 caller-saved 哪些是 callee-saved
- [ ] 我能解釋為什麼 LR (X30) 是 callee-saved
- [ ] 我能說出 STP/LDP 是什麼以及為什麼 ARM 設計這個指令
- [ ] 我知道 PC 在 AArch64 與 AArch32 的差別
- [ ] 我能說出 PSTATE 與 x86 RFLAGS 的概念差別

下一章看 AArch32 與 Thumb，特別是 Cortex-M 為什麼整個 ISA 只剩 Thumb。

→ [Ch 3 AArch32 與 Thumb / Thumb-2](./03-aarch32-thumb.md)
