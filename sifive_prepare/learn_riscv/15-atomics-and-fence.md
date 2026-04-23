# Ch 15 — Atomics、fence、LR/SC 的真實行為

> 目標：延續 Ch 14 的 memory model，聚焦 RISC-V atomic 的具體實作細節 — LR/SC 跟 AMO 的選擇、.aq/.rl 修飾子的精確語意、以及 C 標準 atomic 在 RISC-V 後端的 mapping。這章結束你能看懂 `arch/riscv/include/asm/atomic.h` 每一行。

## 先把工具箱列全

RISC-V 的 atomic 工具有三類：

```
1. AMO (atomic memory operation)    一條指令的原子讀改寫
2. LR/SC (load-reserved/store-cond) 可彈性的原子 pattern
3. Fence                              純排序（不改值）
```

加上修飾子：

```
.aq    acquire
.rl    release
.aqrl  both (sequentially consistent-ish)
```

典型指令長相：

```
amoadd.w.aqrl   rd, rs2, (rs1)
lr.w.aq         rd, (rs1)
sc.w.rl         rd, rs2, (rs1)
```

所有 atomic 指令都在 A 擴充內（Ch 4）。

## AMO 家族完整列表

```
amoswap.w / .d     原子 swap  *rs1 ↔ rs2
amoadd.w  / .d     原子 加
amoxor.w  / .d     原子 XOR
amoand.w  / .d     原子 AND
amoor.w   / .d     原子 OR
amomin.w  / .d     原子 signed min
amomax.w  / .d     原子 signed max
amominu.w / .d     原子 unsigned min
amomaxu.w / .d     原子 unsigned max
```

`.w` 操作 32-bit、`.d` 操作 64-bit（RV64 only）。每個都有 `.aq` / `.rl` / `.aqrl` 四個修飾子變體（空、.aq、.rl、.aqrl）。所以實際有 9 × 2 × 4 = 72 條指令。

**rd 可以是 x0** 丟棄舊值（常見於 `amoswap.w x0, x0, (a0)` 當 store-with-full-fence 用）。

## .aq / .rl 修飾子的精確語意

### .aq (acquire)：

保證**這條指令之後**的 memory access 不能「往回」穿過這條指令。

```
A;
B: amoadd.w.aq  ...
C;
D;
```

C 跟 D 不能被硬體 reorder 到 B 之前。但 A 可以被 reorder 到 B 之後（aq 不管前面）。

### .rl (release)：

保證**這條指令之前**的 memory access 不能「往後」穿過這條指令。

```
A;
B;
C: amoadd.w.rl  ...
D;
```

A 跟 B 不能被 reorder 到 C 之後。但 D 可以跑到 C 之前（rl 不管後面）。

### .aqrl：

同時 acquire + release。所有方向的穿越都禁止。**但不等於 sequential consistency**。要達到 SC 還需要 `fence rw, rw` 或特殊 amoswap 組合。

## LR/SC 細節

### 基本形式

```
lr.w    rd, (rs1)          # 從 *rs1 讀，並在硬體內部建立 "reservation"
# ... 計算 ...
sc.w    rd, rs2, (rs1)     # 嘗試存 rs2 到 *rs1，若 reservation 還在 → 成功 (rd=0)
                            #                   若被破壞 → 失敗 (rd ≠ 0)
```

reservation 可以被破壞的方式：

1. 另一個 hart 寫了同一塊 cache line（或夠近的 memory 區）
2. context switch（preempted）
3. 某些 interrupt
4. 一條會破壞的指令（例：另一條 `lr` 重新 reserve）

### 為什麼 LR/SC 比 AMO 強大

LR/SC 可以實作 **任何原子 RMW**，不只 AMO 列表的幾種：

```asm
# atomic {x = f(x)} for arbitrary f
retry:
    lr.w  t0, (a0)           # load
    call  compute_f          # 任意計算
    sc.w  t1, a0, (a0)       # store if not disturbed
    bnez  t1, retry          # 失敗就重試
```

**但有 constraint**：LR / SC 之間不能太長、不能有某些指令（特別是另一條 memory op）、否則 reservation 一定會斷、無法前進。spec 有一份「constrained LR/SC sequences」規範。

### 面試陷阱題

**「為什麼 RISC-V 選 LR/SC 而不是 CAS？」**

標準答案：

1. CAS 只能 compare-and-swap。"compare-and-add" 要兩次 memory op。LR/SC 一次 round-trip。
2. CAS 有 ABA problem（A → B → A 看起來沒變但實際變過）。LR/SC 看 reservation 不看值，天然免 ABA。
3. CAS 在硬體實作要 2 個 bus transaction。LR/SC 可以 fit 進一次。

但 LR/SC **progress 不保證**。spec 給的 guidelines 要 compiler / 人工遵守。實務上兩種機制各有用途。

## C atomic 怎麼 translate

### `__atomic_fetch_add(&x, 1, memory_order)`

```
__ATOMIC_RELAXED →  amoadd.w      t0, t1, (a0)
__ATOMIC_ACQUIRE →  amoadd.w.aq   t0, t1, (a0)
__ATOMIC_RELEASE →  amoadd.w.rl   t0, t1, (a0)
__ATOMIC_ACQ_REL →  amoadd.w.aqrl t0, t1, (a0)
__ATOMIC_SEQ_CST →  amoadd.w.aqrl t0, t1, (a0)
```

**SEQ_CST 跟 ACQ_REL 對 AMO 產生相同 code**。但 SEQ_CST 語意上更強 — `__atomic_thread_fence(SEQ_CST)` 跟 SEQ_CST atomic 之間要有全域總序。目前 RVWMO 的 aqrl 不完全達到 SC，需要額外 fence。實務上 compiler 會加保險：

```
# SEQ_CST load with 嚴格 SC
amoadd.w.aqrl t0, x0, (a0)     # 等同 load
# 或
lr.w.aq  t0, (a0)
sc.w.rl  x0, t0, (a0)          # dummy store
```

這是 compiler backend 工程師的日常 trade-off：code size vs SC 嚴格度。

### `__atomic_compare_exchange`

C 的 CAS 直接翻成 LR/SC loop：

```c
bool __atomic_compare_exchange(int *ptr, int *expected, int desired, ...)
```

```asm
retry:
    lr.w  t0, (a0)             # load current
    bne   t0, a1, .L_fail      # expected 不匹配 → 退出
    sc.w  t1, a2, (a0)         # 嘗試寫 desired
    bnez  t1, retry            # SC 失敗重試
    li    a0, 1                # 返回 true
    ret
.L_fail:
    sw    t0, (a1)             # 寫回 expected = current
    li    a0, 0
    ret
```

這段 code 要配合 constrained LR/SC rules（不跨 call、不太長）。LLVM 的 `AtomicExpandPass` 就是做這個展開。

## Linux kernel 的 atomic.h

讀 `arch/riscv/include/asm/atomic.h` 是學這章的終極練習。典型 snippet（簡化）：

```c
static __always_inline int arch_atomic_read(const atomic_t *v)
{
    return READ_ONCE(v->counter);
}

static __always_inline void arch_atomic_set(atomic_t *v, int i)
{
    WRITE_ONCE(v->counter, i);
}

static __always_inline int arch_atomic_fetch_add(int i, atomic_t *v)
{
    int result;
    asm volatile ("amoadd.w.aqrl %0, %2, %1"
                  : "=r"(result), "+A"(v->counter)
                  : "r"(i) : "memory");
    return result;
}
```

關鍵觀察：

- `arch_atomic_read` / `arch_atomic_set` 沒用 `lr/sc` / `amo`，只是 `READ_ONCE` / `WRITE_ONCE`（對齊的 load/store 就是 atomic）。
- `arch_atomic_fetch_add` 直接用 `amoadd.w.aqrl`。
- inline asm 的 `"+A"` 是特殊 constraint「`(reg)` 形式的 memory operand」。
- `"memory"` clobber 是 compiler barrier（Ch 14 講過）。

## Signal handler / interrupt 的 atomic

**單核系統仍然有 atomic 問題**：interrupt / signal handler 會 preempt 一個 RMW sequence。

```c
static int counter = 0;
void isr(void) { counter++; }     // 非 atomic，會 race
```

解法：

- 在 single-core 上 `counter++` 變 `amoadd.w.aqrl`。一條指令不被中斷。
- 或 disable interrupts during critical section（`csrc mstatus, MIE`）。

**Linux 的 `atomic_t` 是 SMP-safe**，所以即使單核也走 amo，省一條 disable-interrupt。

## Load / Store 的隱式 atomic

RISC-V spec 明確：**對齊的 aligned load/store 是 atomic**（寬度 ≤ XLEN）。

- 對齊 `lw` / `sw` 是 atomic
- 對齊 `ld` / `sd`（RV64）是 atomic
- 對齊 `lh` / `sh` 是 atomic
- misaligned access **不保證** atomic

所以 single-variable 的 read / write 只要對齊就不用加 atomic instruction，但是要**關 compiler optimization**（用 `volatile` / `READ_ONCE`），以防 compiler 把多個 load 合成一個、或拆一個 write 成多個 byte store。

## fence 的另類用法：`amoadd.x0, x0, (a0)`

一個冷門 idiom：

```
amoadd.w.aqrl x0, x0, (a0)
```

對地址 a0 做 "add 0"，結果丟棄 — 純粹是為了 **`.aqrl` 的 barrier 效果**。某些情境比 `fence rw, rw` 便宜（硬體實作可能更快）。

現代 compiler 很少這麼寫，但手寫優化時會遇到。

## 多核下的 LR/SC 效能陷阱

LR/SC 在多核下 contention 時會**互相搶 reservation**：

```
CPU 0:         CPU 1:
lr.w ...       lr.w ...         (兩個都取得 reservation)
...            ...
sc.w → OK      sc.w → FAIL      (CPU 0 先 commit，CPU 1 被踢)
              → retry ...
```

若 CPU 1 又搶到 reservation 先完成，CPU 0 下次 SC 又失敗 → **livelock**。硬體通常有 backoff 機制（spec 允許但不保證）。

**嚴重 contention 的 lock 應該用 ticket lock 或 MCS lock** 而不是 naive spinning。Linux kernel 的 qspinlock 是重要參考。

## RVWMO 的「store forwarding」陷阱

考慮：

```asm
sw  t0, 0(a0)          # 寫
lw  t1, 0(a0)          # 讀同一地址
```

硬體**一定**會 forward 新值（同地址保證）。但：

```asm
sw  t0, 0(a0)          # 寫 4 byte
lb  t1, 0(a0)          # 讀 1 byte（不同粒度）
```

硬體**不一定**能 forward（要把 store 內部拆看 byte offset）。有些實作禁止這種 forwarding 會 stall。這是硬體設計決策，但 compiler 看到這種 pattern 會避免。

## 常見坑

1. **`amoadd` 的 rs2 跟 rd 搞混**：AMO 的格式是 `rd` 拿舊值、`rs2` 是要加的值。寫錯會加錯值或讀錯地方。
2. **LR 跟 SC 用不同地址**：`lr.w t0, (a0); sc.w t1, t2, (a1)` — 地址不同，SC 一定失敗。
3. **忘了 `"memory"` clobber**：inline asm atomic 忘加 `"memory"` → compiler 把周遭的 load/store 重排 → 失去 atomic 保護。
4. **用 `volatile` 當 atomic**：`volatile` 只保證 compiler 不優化，不保證 CPU 不重排。必須配合 fence 或 atomic 指令。
5. **RV32 上想 atomic 操作 64-bit 變數**：RV32 沒 `amoadd.d`。要 atomic 操作 64-bit 要用 LR/SC（如果 CPU 支援 64-bit reservation）或 ticket lock。Linux `atomic64_t` 在 RV32 真的用 spinlock 實作。

## 動手練習

1. 用 inline asm 寫一個 `my_atomic_add_return`，呼叫後用 `__atomic_fetch_add` 版 gcc -S 對比，看 compiler 跟你的寫法差在哪。
2. 寫一個最簡 ticket lock，用 `amoadd.w.aqrl` 取號、用 `lw + fence` 等待。
3. 在 qemu-user multi-thread 下跑一個 race condition code（`counter++` 無 atomic），觀察錯誤次數。改成 atomic 版本再跑。
4. 讀 Linux `arch/riscv/include/asm/cmpxchg.h` 的 `__cmpxchg_release` 實作，確認它對應本章哪個模式。
5. 用 `llvm-mca` 或 spike 量 `amoadd.w.aqrl` vs `fence rw, rw + sw + fence rw, rw` 的 latency 差距。

## 自我檢核

- [ ] 我能列出 AMO 家族的 9 種 primitive
- [ ] 我能精確解釋 .aq / .rl / .aqrl 的語意（哪個方向禁止重排）
- [ ] 我能寫一個 LR/SC loop 實作任意原子 RMW
- [ ] 我知道 C atomic 各 memory_order 對應的 RISC-V 指令
- [ ] 我能解釋為什麼 naive LR/SC 可能 livelock、對應 mitigation 策略

Part 5 結束。下一章進 Part 6 — 深入讀 spec 的能力訓練，從手解 opcode 到如何在 200 頁 PDF 裡快速定位需要的章節。

→ [Ch 16 從 spec 讀 opcode encoding](./16-opcode-encoding.md)
