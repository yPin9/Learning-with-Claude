# Ch 6 — 函式呼叫與 AAPCS

> 目標：搞清楚 ARM 的 calling convention（AAPCS / AAPCS64），包含參數傳遞、返回值、棧框、frame pointer、tail call、varargs。寫 inline asm、看反組譯、移植 binary 都會用到。

## AAPCS 是什麼

**AAPCS = ARM Architecture Procedure Call Standard**，定義 ARM 上的 C 函式怎麼互通。一共有幾個變體：

- **AAPCS**（base）：32-bit ARM 共通
- **AAPCS-VFP**：32-bit + 浮點走 VFP register
- **AAPCS-LINUX**：Linux 32-bit 系統用
- **AAPCS64**：64-bit AArch64 專用

平台官方規格在 ARM github：<https://github.com/ARM-software/abi-aa>。

## AAPCS64：參數與返回（最常用）

**整數/指標參數**：前 8 個用 X0–X7（小於 64-bit 用 W0–W7），第 9 個起進棧。
**浮點參數**：前 8 個用 V0–V7（依寬度用 S/D），第 9 個起進棧。
**返回值**：

- 整數/指標：X0（小於 64-bit 用 W0）
- 浮點：V0
- 結構 ≤ 16 bytes：用 X0、X1 拼起來回傳
- 結構 > 16 bytes：caller 在棧上分配空間，把指標放 X8（**Indirect Result Location Register**）

```c
// example
struct Pair { uint64_t a, b; };

struct Pair make_pair(int x, double y) {
    return (struct Pair){.a = x, .b = (uint64_t)y};
}
```

```asm
; 進來時 W0 = x, D0 = y
make_pair:
    sxtw x0, w0           ; sign-extend x → X0（回傳 .a）
    fcvtzu x1, d0         ; double → uint64 → X1（回傳 .b）
    ret
```

X0、X1 拼起來表示 16-byte struct。如果 struct > 16 bytes 就走 X8 indirect。

## AAPCS（32-bit）：略不一樣

**參數**：前 4 個用 R0–R3，再多進棧。**返回**：R0（必要時 R0:R1 表 64-bit）。**浮點**：在 hard-float ABI 下走 S0–S15 或 D0–D7。

```c
int sum(int a, int b, int c, int d, int e) { return a+b+c+d+e; }
```

```asm
; A32
sum:
    add  r0, r0, r1
    add  r0, r0, r2
    add  r0, r0, r3
    ldr  r1, [sp]      ; 第 5 個參數從棧讀
    add  r0, r0, r1
    bx   lr
```

注意 R0–R3 不夠時棧的對齊：AAPCS 規定**棧 4-byte 對齊**（AAPCS-VFP / AAPCS64 是 8-byte / 16-byte 對齊），這在 inline asm 寫錯會 segfault。

## Stack frame：實際長什麼樣

AArch64 的標準 frame：

```
高位址
┌────────────────────────┐
│ caller 的 stack frame  │
│ (前面參數溢出區)       │
├────────────────────────┤  ← old SP
│ 預備給 callee 的參數區 │
│ (第 9 個 args 起)      │
├────────────────────────┤
│ X29 (saved FP)         │  ← X29 (current FP) 指這
│ X30 (saved LR)         │
├────────────────────────┤
│ callee-saved regs      │
│ (X19–X28 視需要)       │
├────────────────────────┤
│ local variables        │
│                        │
├────────────────────────┤  ← SP（必須 16-byte 對齊）
│ 紅區無 — 與 SysV 不同  │
低位址
```

**AArch64 沒有「red zone」**（SysV x86_64 SP 之下 128 bytes 可以隨便用），ARM 全部要顯式 sub SP 配置。

典型 prologue / epilogue：

```asm
foo:
    stp  x29, x30, [sp, #-32]!   ; 預留 32 bytes、存 FP+LR
    mov  x29, sp                 ; 設新 frame pointer
    str  x19, [sp, #16]          ; 存 callee-saved
    ; ... 函式體 ...
    ldr  x19, [sp, #16]
    ldp  x29, x30, [sp], #32     ; 還原 FP+LR、SP+=32
    ret
```

`stp ..., [sp, #-32]!` 是 pre-index：**先 sp -= 32，再 store**。`ldp ..., [sp], #32` 是 post-index：**先 load，再 sp += 32**。

## Frame Pointer：要不要用？

X29 (FP) 是 callee-saved 的 frame pointer。功能：

- **Backtrace 時的鏈條**：每個 frame 的 X29 指向上一個 frame 的 X29，可以線性走訪
- **GDB / perf 用 frame chain 解 stack**：沒有 FP 時要靠 `.eh_frame` / DWARF 才能 unwind

`-fomit-frame-pointer` 可以省掉 X29 設置（編譯器 -O 預設就 omit），代價是 stack trace 工具要靠 unwinding info。

**Apple Silicon 與 ARM Linux 的選擇不同**：
- macOS 預設 **保留 frame pointer**（perf / leaks 工具靠它）
- Linux distros 有些 omit 有些保留，最近幾年又轉回保留（Fedora、Ubuntu 24.04 起預設 `-fno-omit-frame-pointer` 給 perf）

## Tail call：BR 而不是 BL

當 `f()` 最後一條是 `return g(args)`，編譯器可以做 **tail call**：

```c
int f(int x) { return g(x + 1); }
```

```asm
f:
    add  w0, w0, #1
    b    g            ; B（unconditional branch）而非 BL
                      ; 不用建自己的 frame，直接跳 g
                      ; g 的 ret 直接回 f 的 caller
```

**B 不更新 LR**，所以 g 結束時 ret 回到 f 的呼叫者。省一層 frame、省一次 BL 的 LR 寫入。

tail call 對遞迴函式特別重要 — 沒有 tail call optimization 的話，深遞迴會爆棧。

## Varargs：va_list 的實作

AArch64 的 `va_list` 是個結構：

```c
typedef struct {
    void *__stack;       // 棧上參數起始
    void *__gr_top;      // GP register save area top
    void *__vr_top;      // FP register save area top
    int __gr_offs;       // GP 已用 offset（負值）
    int __vr_offs;       // FP 已用 offset
} va_list;
```

實作思路：函式 prologue 把 X0–X7、V0–V7 全 spill 到棧某個區域（**register save area**），`va_arg` 從那區域抓參數，超過 8 個就改從棧抓。

這個設計**比 x86_64 SysV 的 `__va_list_tag` 還複雜一點**，但都是同套思路：暫存器參數要 spill 才能用統一介面遍歷。

## Naked function：寫純 asm 函式

寫 startup、context switch 時你不要 prologue/epilogue：

```c
__attribute__((naked))
void context_switch(void) {
    __asm__(
        "stp x19, x20, [sp, #-16]!\n"
        // ...
        "ret\n"
    );
}
```

`naked` 告訴編譯器**完全不要插入 prologue/epilogue/return**，函式體必須自己用 asm 寫完整。Cortex-M 的 PendSV handler 寫 context switch 經典用法。

## 結構回傳的 Indirect Result：X8

> 16 bytes 的 struct 用 X8：

```c
struct Big { uint64_t a, b, c; };  // 24 bytes
struct Big foo(int x);
```

caller 端：

```asm
sub  sp, sp, #32         ; 配置 24 bytes（對齊 16）
mov  x8, sp              ; X8 = 結果寫入位置
mov  w0, w_x
bl   foo
; 結果在 [sp]，三個 uint64
```

callee 端 foo() 在 X8 指向的位置寫 24 bytes，用 X0 沒意義所以不寫。回到 caller 從棧上取結果。

這個機制讓 struct 大小不限，但有一次記憶體往返成本。性能在意的場景應該避免大 struct 回傳（用 out-pointer 或 std::array 之類）。

## 平台特殊：X18 是什麼

X18 在 AAPCS64 規格上是「**平台暫存器**」，由 OS 平台自定義：

- **Linux**：保留（不用，但編譯器不會踩）
- **Windows on ARM**：TEB（Thread Environment Block）
- **iOS / macOS**：保留給 system library
- **Fuchsia**：shadow call stack 指標

寫跨平台 ARM library 時，**不要碰 X18**。clang 有 `-ffixed-x18` 旗標確保編譯器不用它。

## 一個常見誤解

「ARM 暫存器多，是不是函式呼叫成本就比 x86 低？」

不一定。**呼叫本身（BL / RET）的成本，ARM 與 x86 差不多**。差別在：

- ARM 前 8 個 int 參數用暫存器，x86_64 SysV 是 6 個 → ARM **能多省 2 次 spill**（如果參數真的多）
- ARM callee-saved 多（X19–X28 共 10 個），「需要保存的暫存器」可能反而比 x86 多
- ARM 沒 `push`/`pop`，prologue 多幾條指令；但有 `stp`/`ldp` 又能合併

整體在現代 cache-hot code 上**幾乎沒差**。差距在編譯器優化品質與微架構，不在 ABI 本身。

## 自我檢核

- [ ] 我能畫出 AArch64 函式 prologue / epilogue 的指令序列
- [ ] 我能說出 AAPCS64 整數參數是 X0–X7、超過用棧
- [ ] 我能解釋 X8 (Indirect Result) 的用途
- [ ] 我能說出 frame pointer (X29) 的作用與何時可以 omit
- [ ] 我能解釋 tail call 為什麼用 B 而不是 BL
- [ ] 我聽過 AArch32 的 R0–R3 + R4–R11 的對應規則

下一章看 ARM 的系統指令族 — `svc` `hvc` `smc` `brk` 與例外進入機制，這是 Cortex-A 與 Cortex-M 走進不同世界的分水嶺。

→ [Ch 7 系統指令與例外進入](./07-system-instructions.md)
