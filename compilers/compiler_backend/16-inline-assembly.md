# Ch 16 — Inline assembly 與 constraint

> 目標：理解 GCC/Clang 的 inline asm 語法、RISC-V 的 constraint 字母、以及 backend 如何處理 inline asm。寫 kernel、libc、embedded 碼都要碰這個。

## Inline asm 是什麼

C 裡嵌入 assembly：

```c
int add_asm(int a, int b) {
    int result;
    asm volatile (
        "add %0, %1, %2"
        : "=r"(result)     // output operand
        : "r"(a), "r"(b)   // input operands
    );
    return result;
}
```

compiler 不 parse asm 內容，**把它當 "black box"**：

- 讀 input operand、寫 output operand
- 按 constraint 分配 register
- 保留指令周圍的正確 context

產生的 asm：

```asm
add_asm:
    add a0, a0, a1         ← inline asm body
    ret
```

## 為什麼還要 inline asm

有了 intrinsic 為什麼還要 inline asm？

- **ISA 有太多指令**：compiler/intrinsic 沒 cover 全部
- **Kernel 需要**：設 CSR、context switch 等 low-level 操作
- **特殊 pattern**：compiler 無法產的 micro-optimized sequence
- **Custom extension 還沒 intrinsic support**：直接寫 asm 快

但 inline asm 有代價：

- Compiler 看不懂語意 → 無法優化
- 跨 target 不 portable
- Constraint 寫錯容易 miscompile

**優先考慮 intrinsic，真的沒選擇才 inline asm**。

## GCC-style 語法

完整形式：

```c
asm [volatile] (
    "assembly template"
    : output operands        // %0, %1, ...
    : input operands         // %2, %3, ...
    : clobbers               // "memory", specific regs
    : goto labels            // for asm goto
);
```

`volatile`：告訴 compiler「不要優化這個 asm、不要 reorder、即使 output 沒用也不要砍」。

## Output constraint

```c
asm ("..." : "=r"(dst), "+r"(in_out));
```

- `=`：write-only output
- `+`：read-and-write operand
- `&`：early clobber（output 在 input 還活著時就 write，要獨立 reg）

`r` 是 register class code。RISC-V 的：

```
r    general-purpose register (GPR)
f    floating-point register (FPR)
vr   vector register (VR)
I    12-bit signed immediate (for addi)
J    integer 0 (x0)
K    5-bit unsigned immediate (for shift amount)
A    memory address in a single register (for lw/sw)
m    memory (generic)
```

## Input constraint

```c
asm ("..." :: "r"(x), "I"(10));
```

- `r`：任何 GPR
- `I`：12-bit signed immediate

compiler 會把 `x` 放進某個 GPR、`10` 當 immediate。

## Clobbers

```c
asm ("call my_function" ::: "ra", "a0", "a1", "memory");
```

告訴 compiler：

- `"ra"`: asm 會改 `ra` register
- `"a0"`: 會改 `a0`
- `"memory"`: asm 可能讀寫 memory (最強 clobber，compiler 假設任何 memory 可能變)

**漏 clobber 是 miscompile 的來源**。寫 CSR 或 call function 不 clobber 記 → 周圍 code 用 stale 值。

## 一個實用例：讀 cycle CSR

```c
uint64_t read_cycle(void) {
    uint64_t cycle;
    asm volatile ("rdcycle %0" : "=r"(cycle));
    return cycle;
}
```

`"=r"(cycle)`: output 放 GPR，bound 到 C 變數 `cycle`。

## 複雜例：atomic operation

glibc 用 inline asm 實作 atomic：

```c
int atomic_fetch_add(int *ptr, int val) {
    int old;
    asm volatile (
        "amoadd.w.aqrl %0, %2, %1"
        : "=r"(old), "+A"(*ptr)
        : "r"(val)
        : "memory"
    );
    return old;
}
```

- `"+A"(*ptr)`: memory address in a register (RISC-V A constraint)，+read/write
- `"memory"` clobber: 確保 compiler 不 reorder

沒 `"memory"` clobber 的話，周圍的 load/store 可能被 reorder 過 atomic → race condition。

## `%0`, `%1`, `%2`：operand numbering

```c
asm ("op %0, %1, %2"
     : "=r"(out)         // %0
     : "r"(in1),         // %1
       "r"(in2));        // %2
```

順序：先 output、後 input。`%N` 按順序。

有些情況用 `%<name>` 更清楚：

```c
asm ("op %[dst], %[src]"
     : [dst] "=r"(out)
     : [src] "r"(in));
```

## Inline asm 在 backend 的處理

LLVM 把 inline asm IR 表示為 `InlineAsm` 指令：

```llvm
%result = call i32 asm "add $0, $1, $2", "=r,r,r"(i32 %a, i32 %b)
```

注意 `$N` 而非 `%N`（LLVM IR asm 用 `$`，C 語法用 `%`）。

Backend 處理：

1. **Parsing**：`RISCVAsmParser` 解析 template 內容
2. **Register allocation**：constraint 決定哪個 register class、跑 RA
3. **Emit**：把 register 填進 template、產生最終 asm

## Constraint 的 backend 實作

```cpp
// RISCVISelLowering.cpp
RISCVTargetLowering::ConstraintType
RISCVTargetLowering::getConstraintType(StringRef Constraint) const {
    if (Constraint.size() == 1) {
        switch (Constraint[0]) {
        case 'f':  // FPR
        case 'I':
        case 'J':
        case 'K':
            return C_Immediate;
        ...
        }
    }
    return TargetLowering::getConstraintType(Constraint);
}
```

每個 constraint 字母都要 case。Backend 用這個資訊做 RA。

## 一個非直覺的 constraint：`S`

RISC-V GCC 支援 `"S"` 代表 "symbol name"：

```c
extern int my_global;
asm ("la a0, %0" : : "S"(&my_global));
```

`"S"` 讓 asm template 可以直接引用 symbol（而非 register）。LLVM 也支援。

## `asm goto`：跳出 asm 的 branch

```c
void do_smth(int a) {
    asm goto (
        "beqz %0, %l[error_label]"
        : : "r"(a)
        : /* no clobber */
        : error_label
    );
    // normal path
    return;

error_label:
    // error handling
}
```

`asm goto` 讓 asm 可以 branch 到 C label。Linux kernel 的 static key 用這個實作。

很進階、少用但強大。

## Inline asm 的優化限制

Compiler 對 inline asm 保守：

- 不 reorder 越過 `volatile` asm
- 不 CSE（相同 asm 可能有 side effect）
- 不 inline past asm（除非 asm 是 pure）

**濫用 inline asm 會限制優化**。

## 例：滿牆 inline asm 的 kernel

Linux `arch/riscv/include/asm/` 充斥 inline asm：

```c
// barrier.h
#define __smp_mb() __asm__ __volatile__ ("fence rw, rw" ::: "memory")

// bitops.h
static __always_inline int test_and_set_bit(long nr, volatile unsigned long *addr) {
    unsigned long __old, __mask = BIT_MASK(nr);
    __asm__ __volatile__ (
        "amoor.d.aqrl %0, %2, %1"
        : "=&r" (__old), "+A" (addr[BIT_WORD(nr)])
        : "r" (__mask)
        : "memory"
    );
    return (__old & __mask) != 0;
}
```

這些 macro 在 C 層提供 atomic primitive、讓高層 code 像普通 function call。

## Compiler 可能做的 inline asm 優化

雖然 compiler 不懂 asm 內容，但還是可以：

- 選擇哪個 register class
- 選擇具體哪個 reg（RA 決定）
- 算 register live range
- 避免不必要的 spill

## Debug inline asm

```bash
clang -S hello.c -o hello.s
```

看產生的 asm 中 inline asm 部分是否正確 expand。

錯用 constraint 典型症狀：

- **compile error "impossible constraint"**：constraint 字母拼錯 or target 不支援
- **產出 asm register 錯**：constraint 太寬（`"r"` 給太多選擇，RA 選了不對的）
- **miscompile**：clobber 漏寫、周圍 code optimization 錯

## 寫 inline asm 的原則

1. **能用 intrinsic 就用 intrinsic**
2. **一定要 `volatile`（除非真的 pure）**
3. **永遠加 `"memory"` clobber**（除非確定 asm 不碰 memory）
4. **clobber 任何 asm 改的 reg**
5. **註解 asm 的 side effect**
6. **寫 test** verify 各種 optimization level 下都對

## Constraint modifier 進階

一些 flag：

- `=`: write-only
- `+`: read+write
- `&`: early clobber
- `%`: commutative (可以跟下一個 operand 交換)
- `#`: ignored characters (對 compiler)
- `=&`: write-only early clobber (最常搭配，safe)

## 常見誤會

1. **「inline asm 速度最快」**：不一定。compiler 可能產更優 code。intrinsic 優先。
2. **「`volatile` 等於不動」**：不。`volatile` 防 reorder across asm，但 asm 本身仍有 RA 自由度。
3. **「clobber 只標 reg 就夠」**：memory 存取一定要 `"memory"` clobber。
4. **「constraint 字母跨 target 一致」**：不全是。某些字母 arch-specific（`S`, `A` 等）。
5. **「`asm goto` 很危險」**：用對時 OK。Linux kernel 大量用，stable。

## 動手練習

1. 寫 5 個 RISC-V 常用操作的 inline asm：rdcycle、fence、csrrw、rdtime、ecall。
2. 寫一個 atomic add 的 inline asm，對比 `__atomic_fetch_add` 生成的 code。
3. 寫一個 inline asm 故意不加 `"memory"` clobber、看哪些周圍 load/store 被 compiler reorder。
4. 讀 Linux `arch/riscv/include/asm/bitops.h`，認出每個 constraint。
5. 寫一個 `asm goto` 做 branch、驗證行為。

## 自我檢核

- [ ] 我能寫 correct inline asm 含 output/input/clobber
- [ ] 我知道 RISC-V 的 constraint 字母 (r, f, I, J, K, A, m)
- [ ] 我知道什麼時候該用 `volatile` 跟 `"memory"` clobber
- [ ] 我能讀 Linux kernel 的 atomic / barrier inline asm
- [ ] 我能 debug「constraint 寫錯」類錯誤

Part 5 結束。下一章進 Part 6 —— MC layer，assembler / disassembler 的底層。

→ [Ch 17 MC layer：assembler / disassembler / streamer](./17-mc-layer.md)
