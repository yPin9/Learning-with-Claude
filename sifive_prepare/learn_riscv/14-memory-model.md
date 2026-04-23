# Ch 14 — RVWMO memory model 最小必懂

> 目標：理解 RVWMO（RISC-V Weak Memory Ordering）是哪種 consistency model、跟 x86 TSO / ARM AArch64 差在哪、以及 compiler 在多核場景下生 code 要遵守哪些規則。這章刻意保留最小夠用，深入細節留給實際出 bug 時再查。

## 為什麼要有 memory model

單核 CPU 的 memory 語意很直觀：寫了就讀得到。多核時問題來了：

```
// CPU 0:          // CPU 1:
x = 1;             r1 = y;
y = 1;             r2 = x;
```

最直覺答案：`r1 == 1` 時 `r2 == 1` 必然。但現代 CPU 會把 store 重排（store buffer），**即使 source 看起來有序，硬體可能先做 `y = 1` 再做 `x = 1`**。結果 CPU 1 可能看到 `r1 == 1, r2 == 0`。

**memory model 就是 spec 對「硬體能做多少重排」的規範**。弱的 model 允許重排多、硬體跑快；強的 model 保證嚴格序、硬體貴但 programmer 省心。

## RVWMO：中等偏弱

RISC-V 的官方 model 叫 **RVWMO**（RISC-V Weak Memory Ordering）。跟市場定位：

```
強 ────────────────────────────────────────────► 弱
x86 TSO   ARM AArch64 RC    RISC-V RVWMO    Alpha
(最強的實用)(較 x86 弱)       (類似 ARM)     (已廢)
```

RVWMO 基本跟 ARM AArch64 同一級別。設計參考了 ARM 的經驗教訓。

## 默認允許的重排

硬體有權重排以下序列（但還是有一些限制）：

```
┌──────────────────┬────────────────┬──────────────────────────┐
│ 前一條           │ 後一條         │ 允許重排？                │
├──────────────────┼────────────────┼──────────────────────────┤
│ Load             │ Load           │ ✓ (如果不同 address)     │
│ Load             │ Store          │ ✓                         │
│ Store            │ Load           │ ✓ (store-to-load 重排最狠)│
│ Store            │ Store          │ ✓ (如果不同 address)     │
└──────────────────┴────────────────┴──────────────────────────┘
```

這跟 ARM 類似、比 x86 TSO 弱（x86 不允許 store-store 重排、不允許 store-to-load 的 speculate-past）。

## 限制：地址依賴、控制依賴

即使 RVWMO 很弱，**有些東西硬體不能動**：

### Address dependency

```asm
ld  a0, (a1)       # load p = *a1
ld  a2, (a0)       # load *p  (依賴 a0)
```

第二條 load 的 address 依賴第一條的結果。**RVWMO 保證兩條不被重排** — 硬體不能 speculate 第二條的結果在第一條之前。

這對 RCU、lockless data structure 關鍵。

### Data dependency

```asm
ld   a0, (a1)       # load
add  a2, a0, 1
sw   a2, (a3)       # store，資料依賴 a0
```

store 的 data 依賴前面的 load。RVWMO 保證不被重排。

### Control dependency

控制依賴**不完全保證**。這是 RVWMO 跟許多其他 model 的陷阱：

```asm
ld   a0, (a1)
bnez a0, .L1
sw   x0, (a2)        # 控制依賴前面的 load
.L1:
```

硬體**可以** speculate `sw` 在 branch 前就發動（對 CPU 1 看到來說）。要保證序必須加 fence。

## fence 指令的能力

```
fence  pred, succ
```

`pred` / `succ` 各是「之前 / 之後」的 memory access 類型：

- `r` = read
- `w` = write
- `i` = input (I/O read)
- `o` = output (I/O write)
- `iorw` = 全部類型

常用組合：

```
fence rw, rw         # full barrier（最強，最貴）
fence w, rw          # store 完才能做後續
fence r, r           # read 之間的序
fence rw, w          # 後續 write 之前要完成所有前面的
fence i, i           # I/O 之間的序（device driver 用）
```

**`fence rw, rw` 是「memory barrier」的標準寫法**，C 的 `__atomic_thread_fence(__ATOMIC_SEQ_CST)` 會展開成這個。

## 例子：Dekker 演算法

```
// CPU 0:                // CPU 1:
flag[0] = 1;             flag[1] = 1;
if (flag[1] == 0) {      if (flag[0] == 0) {
    critical section;        critical section;
}                        }
```

上面是經典的 mutual exclusion 嘗試。在強 model（TSO）下會有一個 winner（或 both wait）。在 RVWMO，**兩個 CPU 都可能看到對方的 flag 還沒寫進來**（因為 store-to-load 被重排），兩個都進 critical section — 災難。

修正：

```asm
# CPU 0 side
li    t0, 1
sw    t0, flag_0          # flag[0] = 1
fence rw, rw              # <---- 強制等 store 完成
lw    t0, flag_1
bnez  t0, ...             # check flag[1]
```

**compiler 的責任就是在對的地方放對的 fence**。放太多 = 性能掉；放太少 = bug。

## 比較：x86 TSO 的「禮物」

x86 的 memory model 叫 TSO（Total Store Order）：

- 不允許 load-load 重排
- 不允許 load-store 重排
- 不允許 store-store 重排
- **只允許 store 在 load 之前的 speculate**（store buffer）

所以 x86 上 Dekker 演算法不用 fence 也能跑對（除了 store buffer 帶來的 store→load 重排，可以用 `mfence` 修）。

RISC-V 更弱。**從 x86 移植程式來要很小心**，多數 lock-free 實作要補 fence。

## 比較：ARM AArch64

ARM 有類似的弱 memory model，但有**acquire/release 指令變體**：

```
ldar    x0, [x1]       # load with acquire semantics
stlr    x0, [x1]       # store with release semantics
```

這些指令等於 load + fence / fence + store，一步到位。

RISC-V 也有類似但放在 **Atomic extension 的變體**：

```
lr.w.aq      t0, (a0)     # load-reserved with acquire
sc.w.rl      t1, t2, (a0) # store-conditional with release
amoadd.w.aqrl ...          # AMO with both
```

普通 `lw` / `sw` **沒有** aq/rl 版本。必須走 AMO 或用 fence 組合。Ch 15 細講。

## `fence.tso`：給 emulator 用

有一條特殊 fence：

```
fence.tso
```

等同於 `fence rw, rw` 但額外承諾「store 順序像 TSO」。用途：**跑 x86 emulator 在 RISC-V 上**。x86 binary 依賴 TSO，RVWMO 下要 emulator 每次都加 full fence 成本太高。`fence.tso` 給硬體優化空間（硬體知道你只要 TSO 語意，不用全 rw）。

一般 C 程式用不到。碰到 Rosetta / Box86 這類 emulator 才會看到。

## RVWMO 的一些小規則

### Same-address preservation

對**同一個地址**的 load/store **保留程式序**。即使 RVWMO 再弱，你對 `*p` 寫了再讀，讀到的一定是寫的值。這跟 sequential consistency 在 single-address 保持相容。

### atomic RMW 的序

一條 AMO 指令內部 load + store 是 atomic，**不會被任何東西插進中間**。但 AMO 跟「其他」memory access 之間的序還是要 fence / aq / rl 控制。

### Misaligned access

RVWMO 不保證 misaligned access 的 atomic。`lw` 讀一個 word 跨越 cache line → 可能拆成兩次 load，中間能被別的 CPU 插入。這也是為什麼 Linux 對齊 `atomic_t` 到 XLEN 邊界。

## compiler 的視角

實務上 compiler（LLVM / GCC）的對應：

```c
__atomic_load_n(&x, __ATOMIC_RELAXED)    → lw  (沒 fence)
__atomic_load_n(&x, __ATOMIC_ACQUIRE)    → lw + fence r, rw
__atomic_load_n(&x, __ATOMIC_SEQ_CST)    → lw + fence r, rw   (或 lr.w.aq)
__atomic_store_n(&x, v, __ATOMIC_RELEASE) → fence rw, w + sw
__atomic_store_n(&x, v, __ATOMIC_SEQ_CST) → fence rw, w + sw + fence rw, rw

__atomic_thread_fence(__ATOMIC_SEQ_CST)  → fence rw, rw
```

**如果你在 compiler backend 工作，這些 mapping 是考試範圍**。SiFive 面試可能直接問「`__ATOMIC_ACQUIRE` 的 sw 翻成什麼 RISC-V 指令」。

## 「store-release / load-acquire」這對語意

現代語言（C++11、Rust）以 acquire/release 當 atomic 基本單位。它們的保證：

- **release store**：之前所有 memory 操作都要在 store 完成前對其他 CPU 可見
- **acquire load**：之後所有 memory 操作都要在 load 之後才能 begin

用這對可以實作 lock、channel、queue 等 primitives。

RISC-V 的硬體 mapping：

```
release store:   fence rw, w + sw          (或 sw.rl 如果有 aq/rl base op)
acquire load:    lw + fence r, rw           (或 lw.aq)
```

**兩次 fence 的形式會慢**。有些提案要擴充 `lw.aq` / `sw.rl` 給普通 load/store，但還沒 ratified。當前 code 就是 fence pair。

## 一個常見 bug：publish-subscribe pattern

```c
// Writer:
data = create_object();
*ptr_ptr = data;

// Reader:
obj = *ptr_ptr;
use(obj->field);
```

**Naive RISC-V compile**：

```asm
# Writer:
sw  a0, (data_ptr)     # publish pointer

# Reader:
lw  a0, (data_ptr)     # read pointer
lw  a1, (a0)           # read object's field
```

Reader 看到 `a0 != NULL` 不代表 `a0` 指的物件已經完全初始化（writer 那邊可能還沒 flush 全部）。需要：

```asm
# Writer:
# ... init data_object ...
fence rw, w            # 確保前面的 init 都可見才 publish
sw  a0, (data_ptr)

# Reader:
lw  a0, (data_ptr)
# 這邊理論上需要 fence r, rw，但因為 address dependency 保證序，可以省
lw  a1, (a0)
```

**Reader 端靠 address dependency 省掉 fence**。這是 Linux RCU 的基礎原理。

## 面試題目庫

SiFive 面試常見 memory model 考法：

1. **「解釋 `fence rw, rw` 跟 `fence.tso` 的差別」**
2. **「RVWMO 下 double-checked locking 怎麼寫才對？」**
3. **「`__ATOMIC_RELAXED` 跟沒加 atomic 的 `volatile` 差在哪？」**
4. **「compiler 何時會 reorder 跨越 atomic 指令？」** — 答：never。atomic 是 compiler barrier，但**不是 CPU barrier**（需要額外 fence）。
5. **「為什麼 RISC-V 選 weak model 而不是 TSO？」** — 硬體設計彈性大、OoO pipeline 簡單。

## 常見誤會

1. **「`volatile` 就是 atomic」**：錯。`volatile` 只保證 compiler 不優化掉，**不保證 CPU 不重排**。真正的 atomic 要 `_Atomic` / `std::atomic`。
2. **「RISC-V 總是要 fence」**：不。同一地址的 load/store 有序、address/data dependency 有序。很多 pattern 天然有序。
3. **「fence rw, rw 很便宜」**：不便宜。full fence 的典型 overhead 20-50 cycles，在 hot path 會嚴重影響效能。
4. **「ARM / RISC-V 的 fence 可以一對一翻譯」**：大致但不完全。有些 ARM 的 `dmb ishld` / `dmb ishst` 精細化，RISC-V 用 fence pred/succ 組合。
5. **「single-core 不用想 memory model」**：差。single-core 也有 interrupt / signal handler 打斷 memory 序，需要 compiler barrier（`__sync_synchronize()` 或 `asm volatile("" ::: "memory")`）。但不需要 CPU fence（單核不會亂序到自己）。

## 動手練習

1. 寫一個 producer-consumer，用 `fence rw, w` / `fence r, rw` pair，在 QEMU 的 multi-core 模式下測試正確性。
2. 觀察 Linux kernel 的 `arch/riscv/include/asm/barrier.h`，列出它定義的 barrier 跟對應 RISC-V fence 組合。
3. 寫 C 版的 simple spinlock，用 `__atomic_test_and_set` / `__atomic_clear`，用 `-S` 看 compiler 生成的 fence 分布。
4. 查 RISC-V Unprivileged Spec 的 "RVWMO" 章節（Chapter 17），挑一條 rule 讀懂並用自己的話複述。
5. 用 `herd7` 或 RMEM tool 跑 litmus test，驗證 Dekker 演算法在 RVWMO 下的行為（需要一些 litmus syntax 學習，但這是嚴謹理解 memory model 的王道）。

## 自我檢核

- [ ] 我能說出 RVWMO 默認允許的四種重排
- [ ] 我能用 `fence` 指令寫出 full memory barrier 跟 release / acquire
- [ ] 我能解釋 address / data / control dependency 的差異
- [ ] 我知道 x86 TSO 比 RVWMO 強在哪、ARM AArch64 跟 RVWMO 差在哪
- [ ] 我能正確 translate `__atomic_*` 的 memory order 到 fence 組合

下一章直接處理 atomic 指令本身 — LR/SC vs AMO 的取捨、.aq/.rl 的精細語意、為什麼 C11 atomic_compare_exchange 在 RISC-V 翻成 LR/SC loop。

→ [Ch 15 Atomics、fence、LR/SC 的真實行為](./15-atomics-and-fence.md)
