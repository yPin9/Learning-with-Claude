# Ch 26 — 64 位元 Inline Assembly 與 Constraint：在 C 裡嵌入 RV64 ASM

> 目標：能在 RV64 C 程式裡正確寫 GCC inline asm；掌握 constraint 在 64-bit 下的行為；能讀懂 Linux kernel 裡的 RISC-V inline asm。

---

## 26.1 GCC Inline ASM 語法回顧

基本格式：

```c
asm volatile (
    "指令\n\t"
    "指令\n\t"
    : 輸出 operand
    : 輸入 operand
    : clobber list
);
```

完整範例：

```c
uint64_t result;
uint64_t a = 10, b = 20;

asm volatile (
    "add %0, %1, %2\n\t"
    : "=r"(result)       // 輸出：result 放在某個 GPR，寫入
    : "r"(a), "r"(b)     // 輸入：a 和 b 各放一個 GPR
    :                    // 沒有 clobber
);
```

`%0` 是第一個 operand（result），`%1` 是第二個（a），`%2` 是第三個（b）。

---

## 26.2 RV64 下的 Constraint

| Constraint | 含義                              | RV64 行為               |
|------------|----------------------------------|------------------------|
| `r`        | 任意 GPR（general-purpose register）| 分配 64-bit GPR        |
| `f`        | 任意 FP register                  | 分配 64-bit FP reg     |
| `i`        | immediate（常數）                  | 整數立即數               |
| `I`        | 12-bit signed immediate           | 適合 addi 的 immediate |
| `K`        | 5-bit unsigned immediate          | 適合移位的 shamt        |
| `m`        | memory operand                    | 記憶體地址               |
| `A`        | addressable memory（可用 ld/st）   | 記憶體地址               |

修飾符：
- `=`：只寫（output only）
- `+`：讀寫（read-write）
- `&`：early clobber（不能和輸入 operand 共用暫存器）

**RV64 vs RV32 的 constraint 差異**：`"r"` 在 RV64 下分配 64-bit GPR，在 RV32 下分配 32-bit GPR。Constraint 本身不變，但 compiler 知道 XLEN。

---

## 26.3 讀 CSR：csrr 指令

```c
static inline uint64_t read_cycle(void) {
    uint64_t val;
    asm volatile ("csrr %0, cycle" : "=r"(val));
    return val;
}

static inline uint64_t read_time(void) {
    uint64_t val;
    asm volatile ("csrr %0, time" : "=r"(val));
    return val;
}

static inline uint64_t read_instret(void) {
    uint64_t val;
    asm volatile ("csrr %0, instret" : "=r"(val));
    return val;
}
```

讀寫 CSR 的完整指令族：

```c
// csrrs：read and set bits
static inline uint64_t csrrs(uint64_t csr_addr, uint64_t bits) {
    // csr_addr 要是立即數，不能是變數——用 __asm__ 裡的 # 或直接 hard-code
    // 通常包成 macro
    uint64_t val;
    asm volatile ("csrrs %0, sstatus, %1"
                  : "=r"(val)
                  : "r"(bits));
    return val;
}
```

注意：CSR 的位址必須是 12-bit 立即數（編譯期常數）。你不能把 CSR 位址放在暫存器裡傳給 csrr——這不是指令集的 addressing mode。

---

## 26.4 Atomic Operation：lr.d / sc.d

RV64A 的 load-reserved / store-conditional（64-bit 版本）：

```c
// 64-bit compare-and-swap
static inline int cas64(uint64_t *addr, uint64_t expected, uint64_t desired) {
    uint64_t result;
    uint64_t tmp;
    asm volatile (
        "1:\n\t"
        "lr.d   %0, (%2)\n\t"        // load-reserved: result = *addr
        "bne    %0, %3, 2f\n\t"      // if result != expected, fail
        "sc.d   %1, %4, (%2)\n\t"    // store-conditional: *addr = desired
        "bnez   %1, 1b\n\t"          // if sc failed (tmp != 0), retry
        "2:\n\t"
        : "=&r"(result), "=&r"(tmp)  // &：early clobber
        : "r"(addr), "r"(expected), "r"(desired)
        : "memory"                   // 告知 compiler 記憶體可能被修改
    );
    return (result == expected) ? 1 : 0;
}
```

`lr.d` 設定 reservation，`sc.d` 只有在 reservation 還在時才成功（返回 0）。`&` 修飾符很重要——如果沒有，compiler 可能把 `result` 和某個輸入 operand 放在同一個暫存器，導致 `bne` 比較錯誤的值。

---

## 26.5 SFENCE.VMA

```c
// 刷新所有 TLB entries（最重的操作）
static inline void sfence_vma_all(void) {
    asm volatile ("sfence.vma zero, zero" ::: "memory");
}

// 只刷新特定 VA 的 TLB entry
static inline void sfence_vma_va(unsigned long va) {
    asm volatile ("sfence.vma %0, zero" :: "r"(va) : "memory");
}

// 只刷新特定 ASID 的 TLB entries
static inline void sfence_vma_asid(unsigned long asid) {
    asm volatile ("sfence.vma zero, %0" :: "r"(asid) : "memory");
}
```

`"memory"` clobber 是必要的：它告訴 compiler 這條 asm 之後，記憶體的內容可能變了（或之前的 store 對其他 hart 可見），不要把 load 提前到這條 asm 之前。

---

## 26.6 Clobber List 的注意事項

```c
// 錯誤示範：沒有宣告修改了 t0
void bad_example(void) {
    asm volatile (
        "li t0, 42\n\t"
        "add a0, a0, t0\n\t"
    );
    // compiler 可能在這之後還以為 t0 的值是 asm 前的值
}

// 正確做法 1：加入 clobber
void good_example_v1(void) {
    asm volatile (
        "li t0, 42\n\t"
        "add a0, a0, t0\n\t"
        ::: "t0"              // 宣告 t0 被改動了
    );
}

// 正確做法 2：用 operand 讓 compiler 分配暫存器
void good_example_v2(long val) {
    long tmp;
    asm volatile (
        "li %0, 42\n\t"
        "add %0, %1, %0\n\t"
        : "=&r"(tmp)
        : "r"(val)
    );
}
```

---

## 26.7 什麼時候用 Inline ASM

**應該用：**
- 讀寫 CSR（沒有對應的 C 語言語法）
- SFENCE.VMA（memory barrier，compiler 不知道怎麼產生）
- lr.d/sc.d（雖然 `<stdatomic.h>` 的 CAS 通常可以，但有時需要精確控制）
- fence.i（指令 cache 刷新）

**可以用 `__builtin_` 代替：**

```c
// 不用 inline asm 的 atomic add
#include <stdatomic.h>
atomic_long x;
atomic_fetch_add(&x, 1);   // compiler 會產生正確的 AMO 指令

// GCC built-in（也可以）
__sync_fetch_and_add(&x, 1);
```

**不該用的情況：**
- 一般算術（compiler 比你優化得更好）
- memcpy/memset（用 `__builtin_memcpy`）
- 除非你要的就是特定 instruction sequence（效能 profiling 等）

---

## 26.8 常見錯誤

**錯誤 1：`volatile` 忘了加**

沒有 `volatile` 的 asm 可能被 compiler 移動位置或消除（如果 output 沒被用到）。讀 CSR、寫 CSR、任何有 side effect 的指令都要加 `volatile`。

**錯誤 2：漏掉 `"memory"` clobber**

對記憶體有影響的指令（store、atomic、fence）如果沒有 `"memory"` clobber，compiler 可能把 asm 前的 load 提到 asm 之後，或把 asm 後的 store 移到 asm 之前。

**錯誤 3：CSR 名稱拼錯**

```c
asm volatile ("csrr %0, cycl" ...);  // 拼錯！assembler 報錯
```

assembler 在展開 asm 時才會發現，錯誤訊息不總是清楚。

**錯誤 4：對 64-bit 值用 32-bit constraint**

```c
uint64_t val = 0xDEADBEEFCAFEBABEULL;
// "I" constraint 只接受 12-bit immediate，這不行：
asm volatile ("li %0, %1" : "=r"(val) : "I"(0xCAFEBABE));  // 爆！
```

---

## 自我檢核

- [ ] 能寫一個正確讀 `cycle` CSR 的 inline asm 函式
- [ ] 知道 `=&r` 中 `&` 的作用（early clobber）
- [ ] 能說出什麼時候必須加 `"memory"` clobber
- [ ] 知道 `volatile` 在 asm 裡的作用
- [ ] 能解釋為什麼 CSR 位址必須是立即數

→ [Ch 27 — 分頁機制基礎](27-paging-basics.md)
