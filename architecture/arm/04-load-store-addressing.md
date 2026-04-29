# Ch 4 — Load-Store 架構與定址模式

> 目標：搞清楚 ARM 的 Load-Store 是什麼意思（vs x86 的 register-memory），以及 ARM 招牌的 pre-index、post-index、PC-relative、addressing 變體是怎麼設計的。

## Load-Store 是什麼？

x86 是 **register-memory** 架構，記憶體可以直接當運算元：

```asm
; x86_64
add  qword ptr [rdi], rax     ; 直接把 rax 加到 [rdi] 指向的記憶體
add  rax, [rdi + 8]           ; 從記憶體讀，加到 rax
```

ARM 是 **load-store** 架構（也叫 register-register）：**運算指令的運算元只能是暫存器或立即數，碰記憶體只能用 LDR/STR**。

```asm
; AArch64 — 不能直接 add memory
ldr  x1, [x0]          ; 先 load
add  x1, x1, x2        ; 算
str  x1, [x0]          ; 再 store
```

這是 RISC 設計的核心取捨：

| | RISC（ARM） | CISC（x86） |
|---|---|---|
| 指令長度 | 固定（32-bit AArch64 / 16+32-bit Thumb-2） | 可變（1–15 bytes） |
| 記憶體運算 | 只 load/store | 多種指令可碰記憶體 |
| 解碼複雜度 | 低（流水線好做） | 高（需要 microcode） |
| code density | 中（Thumb-2 改善） | 高 |
| 編譯器自由度 | 高（暫存器多、指令獨立） | 低（多規矩） |

## 基本 Load/Store

```asm
; AArch64
ldr  x0, [x1]              ; x0 = *x1（load 64-bit）
ldr  w0, [x1]              ; w0 = *(uint32_t*)x1
ldrb w0, [x1]              ; w0 = (uint8_t)*x1（zero-extend to 32-bit）
ldrsb x0, [x1]             ; x0 = (int8_t)*x1（sign-extend to 64-bit）
ldrh w0, [x1]              ; load 16-bit zero-extend
ldrsh x0, [x1]             ; load 16-bit sign-extend to 64-bit

str  x0, [x1]              ; *x1 = x0
strb w0, [x1]              ; *(uint8_t*)x1 = w0 & 0xff
```

寬度後綴：`B`（byte）`H`（halfword 16-bit）`W`（word 32-bit，AArch64 透過暫存器選 W vs X 不需後綴）`X`（doubleword 64-bit）。

**signed vs zero extend** 用 `LDRS*` 系列。x86 的 `MOVSX`/`MOVZX` 是同一概念。

## 定址模式（addressing modes）

ARM 提供五種 addressing：

### 1. Register（基本）

```asm
ldr  x0, [x1]              ; 位址 = x1
```

### 2. Immediate offset（立即數偏移）

```asm
ldr  x0, [x1, #16]         ; 位址 = x1 + 16
ldr  x0, [x1, #-8]         ; 位址 = x1 - 8（有負偏移）
```

立即數範圍受指令編碼限制：AArch64 LDR 通常 **±256 unscaled** 或 **0–32760 scaled by access size**（4096 / 8 = 32760，因為 64-bit access 必須對齊 8）。

### 3. Register offset（暫存器偏移）

```asm
ldr  x0, [x1, x2]          ; 位址 = x1 + x2
ldr  x0, [x1, x2, lsl #3]  ; 位址 = x1 + (x2 << 3)，做 8-byte 索引超好用
ldr  x0, [x1, w2, sxtw]    ; 位址 = x1 + sign_extend(w2)
```

`lsl #3` 是「左移 3」= 乘 8。陣列索引時超方便：

```c
// arr[i] where arr is uint64_t*
// 等價於：
uint64_t v = arr[i];
```

```asm
ldr  x0, [x_arr, x_i, lsl #3]   ; 一條指令搞定
```

x86 也有 `[rdi + rsi*8]` 對應，但 ARM 的擴展更通用（支援 sign-extend、zero-extend）。

### 4. Pre-index（先算位址再 load，且寫回）

```asm
ldr  x0, [x1, #16]!        ; x1 = x1 + 16; x0 = *x1
                           ; 注意 ! 號
```

「先把 base 加上 offset，把結果 **寫回 base**，再用新 base 當位址」。等效於：

```c
x1 += 16;
x0 = *x1;
```

### 5. Post-index（先 load 再加，也寫回）

```asm
ldr  x0, [x1], #16         ; x0 = *x1; x1 = x1 + 16
                           ; 注意 ] 後面才是 offset
```

「先用 base 當位址，load 完後 **把 base + offset 寫回 base**」。等效於：

```c
x0 = *x1;
x1 += 16;
```

post-index 經典用途是**遍歷陣列**：

```c
for (int i = 0; i < n; i++) sum += arr[i];
```

```asm
loop:
    ldr  x2, [x_arr], #8     ; load *arr，arr += 8
    add  x_sum, x_sum, x2
    subs x_n, x_n, #1
    bne  loop
```

## PC-relative：ADR / ADRP / LDR literal

要拿 PC-relative 位址（例如指向同一個檔案的全域變數），AArch64 用 `ADR` 與 `ADRP`：

```asm
adr  x0, label             ; x0 = PC + offset，offset 範圍 ±1MB
adrp x0, page_label        ; x0 = (PC & ~0xFFF) + offset，到 4KB page，範圍 ±4GB
add  x0, x0, :lo12:label   ; 加上 page 內的低 12 bit
```

為什麼分 ADR 與 ADRP？AArch64 指令固定 32-bit，編碼不下完整 4GB 範圍的 offset。所以拆兩步：先 ADRP 跳到 4KB page，再 add 取低 12 bit。**整個過程能覆蓋 ±4GB**。

```asm
; 取 global variable 的位址
adrp x0, my_var
add  x0, x0, :lo12:my_var
ldr  x1, [x0]              ; load value
```

或更直接的 PC-relative literal load：

```asm
ldr  x0, =0xDEADBEEF12345678  ; assembler 會把常數放到 literal pool
                              ; 編成 ldr x0, [pc, #offset]
```

## Load/Store Pair：STP / LDP

ARM 的祕密武器：**一條指令搬兩個暫存器**。

```asm
stp  x0, x1, [sp, #16]     ; *(sp+16) = x0; *(sp+24) = x1
ldp  x0, x1, [sp, #16]     ; x0 = *(sp+16); x1 = *(sp+24)
```

主要用於：

- **prologue / epilogue 存還原 callee-saved**：`stp x29, x30, [sp, #-16]!`
- **memcpy 加速**：一次搬 16 bytes
- **struct copy**：兩個欄位一起搬

x86 沒有對應指令（`pusha`/`popa` 在 64-bit 下被砍）。

STP/LDP 也支援 pre-index / post-index：

```asm
stp  x29, x30, [sp, #-16]!   ; 先 sp -= 16，再 stp
ldp  x29, x30, [sp], #16     ; 先 ldp，再 sp += 16
```

這個 idiom 是 AArch64 函式 prologue / epilogue 的標準寫法。

## AArch32 的 LDM / STM：load multiple

A32 / Thumb-2 還有更狠的 **load/store multiple**：

```asm
; A32
ldmia r0, {r1, r2, r3, r4}    ; load 4 registers from [r0]
                              ; 「IA」= Increment After
stmdb sp!, {r4 - r11, lr}     ; 把 r4-r11 與 lr 一次 push 到棧
                              ; 「DB」= Decrement Before；! 寫回 sp
```

這是 push/pop 多個暫存器的方便寫法。**AArch64 砍掉了**（因為對流水線太複雜，例外發生時要 partial undo），改用多個 STP/LDP。

但 A32 的 `STMDB SP!, {...}` / `LDMIA SP!, {...}` 在嵌入式很常見，看 STM32 的 startup code 會碰到。

## Exclusive Load/Store：LDXR / STXR

原子操作的基石。Ch 20 會展開，這裡先打照面：

```asm
retry:
    ldxr  w0, [x1]          ; load-exclusive：標記 x1 為「監控中」
    add   w0, w0, #1
    stxr  w2, w0, [x1]      ; store-exclusive：成功 w2 = 0；失敗 w2 = 1
    cbnz  w2, retry         ; 失敗就重試
```

LL/SC（Load-Linked / Store-Conditional）模式。x86 用 `lock cmpxchg`，ARM 走 LL/SC 路線。**ARMv8.1-A 後加了 LSE（Large System Extensions）**，提供 `CAS`/`SWP`/`LDADD` 等單指令版，更好用、多核擴展性更好。

## 對齊與 unaligned access

- **指令必須對齊**：ARM 指令永遠 4-byte 對齊（Thumb 2-byte）
- **資料 unaligned access**：AArch64 預設**允許** unaligned load/store（普通 LDR/STR），但對 LDP/STP / load-exclusive 仍要對齊
- AArch32 預設不允許 unaligned，要設 SCTLR.A bit 才開

unaligned access 的代價：可能切兩次 cache line，速度變慢。**對齊資料是性能慣例**，不是強制。

## 一個常見誤解

「Load-store 架構是不是會比 x86 慢？因為要多一條 LDR」

不會。現代 x86 解碼器把 `add [mem], rax` 內部拆成 micro-op：load → add → store。**和 ARM 的 LDR + ADD + STR 在硬體層面差異不大**。

ARM 的優勢在解碼簡單、流水線淺、超純量設計成本低。x86 的優勢在 code density 與舊 binary 兼容。誰快取決於微架構實作，不是 ISA 層的差異。

## 自我檢核

- [ ] 我能用一句話定義 load-store 架構
- [ ] 我能寫出 LDR/STR 五種定址模式
- [ ] 我能解釋 pre-index 跟 post-index 的差別與寫回機制
- [ ] 我知道為什麼 AArch64 取位址要用 ADRP + ADD :lo12:
- [ ] 我能說出 STP/LDP 的用途與為什麼 AArch64 砍掉 LDM/STM
- [ ] 我聽過 LDXR/STXR 知道它是 LL/SC 原子操作

下一章看條件執行、IT block、那個有名的 barrel shifter — ARM 早期招牌特性中最 funky 的設計。

→ [Ch 5 條件碼、IT block、barrel shifter](./05-condition-and-shifter.md)
