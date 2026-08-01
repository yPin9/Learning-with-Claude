# Ch 15 — 分支預測器內部

> **目標**：了解 CPU 分支預測器的具體結構——PHT、BTB、BHB、RSB——以及它們為什麼跨 context 共享。Spectre 家族的每個變體打的是不同的預測結構，搞清楚這些結構才能理解 v1（打 PHT）、v2（打 BTB）、RSB（打 Return Stack Buffer）的差異，以及各自的防禦機制為什麼長那樣。

Ch 14 我們實作了 Spectre-v1，過程中「訓練 PHT」是整個攻擊能成立的關鍵步驟。但我們沒有解釋 PHT 到底是什麼、它的結構怎麼讓它「可被訓練」、以及為什麼訓練效果能在攻擊呼叫時留下來。

這章補這個坑。

## 為什麼 CPU 要預測分支

每個條件跳轉（`jne`、`jl`、`jge`...）都可能改變程式的執行流。現代 x86-64 pipeline 有 14–20 個 stage（取決於微架構）。如果不預測，CPU 在「取下一條指令」這個最早的 stage 就必須等「分支結果計算完成」——可能是 4–20 cycles 之後。

現代高效能 CPU 的分支預測準確率超過 99%（在典型工作負載下）。即使只有 1% 的 misprediction，每次 misprediction 造成的 pipeline flush（~15–20 cycles penalty）也是可見的性能損失。如果預測率是 50%（完全不預測），效能損失是災難性的。

這就是為什麼 CPU 設計者花了巨大心力在分支預測器上——也因此創造了一個可被攻擊的結構。

## PHT：Pattern History Table（方向預測）

PHT（Pattern History Table，也叫 BHT, Branch History Table）預測條件分支的「方向」：taken（跳）還是not-taken（不跳）。

### 最簡版本：2-bit Saturating Counter

每個分支在 PHT 裡對應一個 2-bit saturating counter：

```
狀態機：
  00 (Strongly Not-Taken)
    ↑ not-taken ↓ taken
  01 (Weakly Not-Taken)
    ↑ not-taken ↓ taken
  10 (Weakly Taken)
    ↑ not-taken ↓ taken
  11 (Strongly Taken)

預測規則：高位元 = 0 → 預測 not-taken，1 → 預測 taken
回饋更新：分支真正執行後，根據結果移動狀態
```

一個 2-bit counter 能在「大部分時間 taken 但偶爾 not-taken」的模式下保持穩定（不會因為一次 not-taken 就立刻翻轉預測）。

### PHT 如何被索引（Spectre-v1 的關鍵）

PHT 是一個 table（通常 2048–8192 entry），用**分支指令的 PC（程式計數器）低位址 bits 當 index**。

```
分支指令位址：0x...7F4C2A30
                          ↓
                取低 11 bits (or 12 bits)
                         ↓
              PHT index = 0x630
                         ↓
              PHT[0x630] = 2-bit counter = 10 (weakly taken)
```

**關鍵點：不同 branch 如果 PC 低位元相同，會 alias 到同一個 PHT entry。**

Spectre-v1 的訓練就是利用這個：攻擊者在自己的 process 裡，找一個 PC 低位元和受害者 gadget 的 `if(x < size)` 相同（或 alias）的條件分支，反覆讓它 taken——PHT entry 被設成 strongly taken。然後在同一個 PHT context 裡呼叫受害者 gadget，PHT 預測 taken，推測執行展開。

### 現代 PHT：加入分支歷史

現代預測器不只用 PC，還用「最近 N 個分支的 taken/not-taken 歷史」來索引 PHT，讓不同歷史上下文的同一個 branch 可以有不同的預測。

```
PC                BHR（Branch History Register）
0x...A30    XOR  1011001110...（最近 16 個分支的結果）
                              = PHT index
```

這叫 **two-level predictor** 或 **gshare**（global history share）。它讓 PHT 有更精細的區分度，預測更準，但也讓「訓練攻擊」需要同時控制 BHR——這是 Spectre-BHI（Branch History Injection）攻擊的目標（Intel 2022 的新變體）。

## BTB：Branch Target Buffer（目標預測）

BTB 預測的不是跳不跳，而是「跳到哪裡」——主要用於間接跳轉（`jmp rax`、`call [rbx]`、`ret`）和直接跳轉的目標快速查找。

```
BTB 結構（簡化）：
  ┌─────────────┬────────────────────────┐
  │ Tag（PC 高位）│ 預測目標 Target Address │
  ├─────────────┼────────────────────────┤
  │ 0x...7F4C   │  0x00007FFF12340000    │
  │ 0x...A3C2   │  0x00007FFF56780000    │
  │ ...         │  ...                   │
  └─────────────┴────────────────────────┘
```

當 CPU fetch 到一個可能是跳轉的指令，它在 BTB 裡查找：這個 PC 以前跳到哪裡？如果有記錄，就推測下一條 fetch 的位址是 BTB 記錄的目標。

### 間接跳轉預測

```c
// 這類程式碼對 BTB 至關重要：
typedef void (*handler_t)(void);
handler_t dispatch[256];
dispatch[type]();   /* 間接 call，call [rbx] */
```

每次 `dispatch[type]()` 的目標可能不同（不同 type → 不同函式）。BTB 記錄「這個 call site 最近一次跳到哪裡」，下次同個 call site 就推測跳到那裡。

**Spectre-v2 打的就是 BTB**：跨 process 訓練 BTB，讓受害者 process 的間接跳轉推測到攻擊者選定的 gadget 位址（Ch 16 詳細講）。

### BTB 是跨 context 共享的嗎？

**是的**，在沒有 IBRS/eIBRS 的情況下，BTB 在同一個物理核心上的不同 process、甚至不同 privilege level 之間共享。這讓 Spectre-v2 能跨 privilege boundary（user → kernel BTB 污染）成立。

eIBRS（Enhanced IBRS）修了這個：它讓 kernel mode 的 BTB prediction 只被 kernel mode 的 branch history 影響，不受 user mode 的訓練污染。

## BHB：Branch History Buffer/Register

BHB（Branch History Buffer，有時叫 BHR，Branch History Register）記錄最近 N 條分支（通常 16–32 條）的 taken/not-taken 歷史。

```
BHB：  [1][0][1][1][0][0][1][0][1][1][1][0][0][1][0][1]
       最新 ←────────────────────────────────── 最舊
       taken = 1, not-taken = 0
```

BHB 與 PHT 結合：PHT 的 index = hash(PC, BHB)。因此，同一個 branch（同一個 PC），在不同的 BHB 歷史下，可以有不同的 PHT 預測。這讓預測器能區分「這個 loop 的第 1 次 taken 和第 100 次 taken」。

**BHB 也是跨 context 共享的**，是 Spectre-BHI（Branch History Injection，CVE-2022-0001）的攻擊目標：攻擊者透過構造特定的分支序列，把 BHB 設成特定狀態，讓 kernel 裡某個 branch 的 PHT 查找到一個被訓練過的位置，讓 kernel 推測執行到攻擊者選的 gadget。

### BHB 的問題：無法用 IBRS 完全隔離

eIBRS 清除了 BTB 的跨 privilege 污染，但 BHB 的共享問題更難修。Intel 2022 發布的 BHI 緩解需要在每次從 user 進入 kernel 時，手動執行一個「BHB 清除序列」（一段構造好的分支序列讓 BHB 進入已知無害狀態）。這個 SW sequence 比 IBRS 更費時。

## RSB：Return Stack Buffer（返回預測）

RSB（Return Stack Buffer，有時叫 RAS, Return Address Stack）是一個小型 hardware stack，專門用於預測 `ret` 指令的目標。

工作原理：
1. `call` 指令執行時：CPU 把返回位址 push 進 RSB
2. `ret` 指令執行時：CPU 從 RSB pop 出最後 push 的值當作預測目標

```
程式執行：
  main → call foo
         RSB: push(&main + offset)  → RSB = [return_to_main]
         foo → call bar
               RSB: push(&foo + offset) → RSB = [return_to_foo, return_to_main]
               bar → ret
                     RSB pop → 預測 return_to_foo   ← 通常是對的！
               → ret
                 RSB pop → 預測 return_to_main
```

RSB 的大小通常是 16–32 entry（Intel Comet Lake 是 ~16）。

### RSB 的問題

**RSB 下溢（underflow）**：如果巢狀呼叫超過 RSB 大小，RSB 滿了。之後的 `ret` 可能 fallback 到 BTB 預測（Intel 某些微架構）或根本預測失敗。

**Spectre-RSB（ret2spec，Ch 17）** 打的是 RSB 的不一致：
- 呼叫者在 RSB 裡有返回位址 A
- 攻擊者（或特殊操作）讓 `ret` 在 RSB 裡對應 B（攻擊者控制的 gadget）
- `ret` 推測執行到 B，把 secret 洩漏出去

### RSB 和 Retpoline

Retpoline（Ch 16 詳細講）是一個間接分支的防禦技術，它把間接跳轉轉換成一個 `ret` 序列：

```asm
; 原始間接跳轉：call [rbx]  ← 受 BTB 攻擊
; Retpoline 替代：
  call capture_spec          ; RSB 記住返回位址
capture_spec:
  pause                      ; 防止推測執行過頭（CPU-specific）
  lfence
  jmp capture_spec           ; 永遠不到這裡（推測停在 pause+lfence）
set_up_target:
  mov [rsp], rbx             ; 把真實目標放到棧頂
  ret                        ; RSB 說「返回 capture_spec 後面」，CPU 推測到那裡
                             ; 實際返回到 rbx 指向的位址
```

Retpoline 的核心：把 BTB 可預測的間接跳轉，換成 RSB 預測的 `ret`——而 RSB 的預測是基於 call/ret 配對的，難以跨 context 訓練。但 RSB 本身（Ch 17）在某些微架構上也有問題。

## 哪些結構跨 context 共享

這是攻擊的關鍵問題：

| 結構 | 在同一物理核心的 thread 間共享（SMT）| 跨 privilege level（user/kernel）| 修法 |
|------|--------------------------------------|----------------------------------|------|
| L1/L2 cache | 通常不共享（separate per-core） | 是（如 L3） | KPTI/flush on privilege switch |
| L3 cache | 是（共享整個 LLC） | 是 | L1D flush、LLC partitioning |
| BTB | 是（SMT siblings 共享） | 是（user 能污染 kernel BTB） | IBRS, eIBRS |
| PHT/BHR | 是 | 是 | 無完整修法，lfence 在 gadget 前 |
| RSB | 通常 per-logical-core | 是（kernel 進入時 RSB 可能被使用者控制） | RSB stuffing（每次 kernel 進入填充 RSB） |
| TLB | 是（SMT 共享） | 是 | PCID / ASID isolation |

**重要觀察**：PHT 和 BHR 幾乎沒有完整的硬體隔離方案。現有的 Spectre-v1 緩解要麼依賴編譯器插入 `lfence`（阻止推測），要麼依賴 swapgs/usercopy barriers（縮小 gadget 攻擊面），但這些都是「減少可利用的 gadget」而非「阻止推測本身」。

## 間接分支預測的內部機制

### indirect call 的兩段式預測

現代 CPU 對間接分支（`jmp rax`、`call [rbx]`）的預測不只是 BTB，而是一個多層機制：

1. **BTB（Branch Target Buffer）主預測**：按 PC 低位元查表，找「這個 call site 最近一次的目標」
2. **ITTAGE（Indirect Target TAGE）**：如果 BTB 找不到或 BTB 的預測不夠精確，用更長的分支歷史做更精細的預測
3. **Loop count predictor**：某些有固定迭代次數的迴圈用 loop predictor

在 Skylake-based 微架構（Comet Lake 同屬此族）上，逆向工程（Evtyushkin et al. 2016）顯示 BTB 是 **set-associative** 結構：

```
BTB 結構（Skylake 家族，推測）：
  ┌──────────────────────────────────────────────────────┐
  │  Set 0: [ tag | target ] [ tag | target ] [ ... ]  │  ← 4-way
  │  Set 1: [ tag | target ] [ tag | target ] [ ... ]  │
  │  ...                                                 │
  │  Set N: [ tag | target ] [ tag | target ] [ ... ]  │
  └──────────────────────────────────────────────────────┘
  Index 由 PC 低位元決定（約 log2(N) bits）
  Tag 由 PC 高位元和/或分支歷史組成

  → 兩個不同 VA 如果低 K bits 相同，就 alias 到同一個 Set
  → Set 裡有 4 個 way，如果全滿就 evict 最舊的
```

這個結構解釋了 Spectre-v2 的「BTB aliasing」：攻擊者在低 K bits 相同的 VA 執行間接跳轉，就能污染受害者的 BTB entry（即使高位元不同）。

### 為什麼 ITTAGE 讓攻擊更難但不解決問題

ITTAGE（Tagged Geometric History indirect predictor）使用比 BTB 更長的分支歷史做 index，讓攻擊者更難精確控制哪個歷史狀態下 alias 成功。但：

- ITTAGE 仍然是 table-based，仍然可以 alias
- Spectre-BHI 展示了即使 ITTAGE 有 tag-based 保護，攻擊者透過精確構造 BHB（Branch History Buffer）的狀態，可以讓 hash(PC, BHB) 命中攻擊者訓練過的 entry

## 小實驗：觀察預測器行為

我們可以用計時間接量測預測器的行為，不需要 PMU（硬體效能計數器）。

### 實驗 1：分支預測命中/失誤的 timing 差異

```c
#include <stdio.h>
#include <stdint.h>
#include <x86intrin.h>

#define N 10000

int main(void) {
    volatile int x = 1;
    uint64_t hit_total = 0, miss_total = 0;
    unsigned junk;
    
    /* 熱身：讓分支預測器學習「幾乎都 taken」 */
    for (int i = 0; i < 1000; i++) {
        if (x > 0) { /* 幾乎都 taken */ }
    }
    
    /* 量測：預測命中的代價（每次都 taken，預測器猜對） */
    for (int i = 0; i < N; i++) {
        uint64_t t0 = __rdtscp(&junk);
        if (x > 0) { asm volatile("" ::: "memory"); }
        uint64_t t1 = __rdtscp(&junk);
        hit_total += t1 - t0;
    }
    
    /* 量測：預測失誤的代價
     * 訓練 PHT 為「taken」，然後突然出現「not-taken」 */
    for (int i = 0; i < N; i++) {
        /* 每 100 次中有 1 次 not-taken → 預測器仍猜 taken → 失誤 */
        x = (i % 100 == 99) ? -1 : 1;
        uint64_t t0 = __rdtscp(&junk);
        if (x > 0) { asm volatile("" ::: "memory"); }
        uint64_t t1 = __rdtscp(&junk);
        if (i % 100 == 99) miss_total += t1 - t0;  /* 只記失誤的 */
    }
    
    printf("Avg hit    (prediction correct):   %.1f cycles\n",
           (double)hit_total / N);
    printf("Avg mispredict (rare not-taken):   %.1f cycles\n",
           (double)miss_total / (N / 100));
    return 0;
}
```

在 i7-10700 上的典型輸出：

```
Avg hit    (prediction correct):   4.2 cycles
Avg mispredict (rare not-taken):   17.8 cycles
```

misprediction penalty 約 17 cycles——這就是 CPU 為推測執行付出的代價（每次猜錯，pipeline flush + 重新 fetch）。

### 實驗 2：間接跳轉的 BTB 行為

```c
typedef void (*fp_t)(void);
static void fn1(void) { asm volatile("nop"); }
static void fn2(void) { asm volatile("nop"); }

int main(void) {
    fp_t targets[2] = { fn1, fn2 };
    unsigned junk;
    
    /* 訓練 BTB：連續 1000 次都跳 fn1 */
    for (int i = 0; i < 1000; i++) targets[0]();
    
    /* 量測：BTB 命中（跳 fn1，BTB 預測 fn1，猜對） */
    uint64_t t0 = __rdtscp(&junk);
    for (int i = 0; i < 1000; i++) targets[0]();
    uint64_t t1 = __rdtscp(&junk);
    printf("BTB hit (fn1):   %.1f cycles/call\n",
           (double)(t1 - t0) / 1000.0);
    
    /* 量測：BTB 失誤（突然跳 fn2，BTB 還猜 fn1，猜錯） */
    t0 = __rdtscp(&junk);
    for (int i = 0; i < 10; i++) targets[1]();  /* 第一次 fn2 → BTB 失誤 */
    t1 = __rdtscp(&junk);
    printf("BTB miss (fn2 first time): %.1f cycles/call\n",
           (double)(t1 - t0) / 10.0);
    
    return 0;
}
```

這個實驗展示 BTB 的行為：訓練過 fn1 的 BTB entry 在第一次切換到 fn2 時失誤，之後 BTB 更新為 fn2，再次預測正確。Spectre-v2 就是在這個切換的「BTB 失誤 → 推測到錯誤目標」的窗口裡注入惡意 gadget。

## Spectre-v1 打的是 PHT 的哪個特性

回到 Ch 14 的 Spectre-v1 攻擊，我們做了「訓練 PHT」這個步驟。現在我們能更精確地說明：

```c
/* 訓練階段：呼叫 20 次合法的 victim(x) */
for (int i = 0; i < 20; i++) victim(i % 16);

/* PHT 被更新：
 * PHT[hash(PC_of_if, BHR)] += "TAKEN" 計數
 * 連續 20 次 taken → saturating counter 達到 "Strongly Taken"
 */

/* 攻擊階段：呼叫 victim(160)（越界）
 * CPU 在分支解析前先查 PHT：
 * PHT[hash(PC_of_if, BHR)] = "Strongly Taken"
 * → 推測 taken → 推測執行 array1[160]...
 */
```

PHT 的「可訓練性」依賴以下特性：
1. **全局歷史（Global History）**：BHR 記錄了最近 N 條分支，victim() 的 if 本身也在這個歷史裡
2. **無 context 隔離**：PHT 不區分訓練呼叫和攻擊呼叫——都是同一個 PC，同一個 BHR 狀態，命中同一個 PHT entry
3. **Saturation**：20 次 taken 讓 counter 達到 max，單次 not-taken 不影響預測——攻擊者的一次越界呼叫後，PHT 仍然預測 taken

## 對比取捨：各種預測結構

| 預測結構 | 目的 | 攻擊變體 | 修法難度 | 現有修法 |
|---------|------|---------|---------|---------|
| PHT（2-bit counter） | 條件分支方向 | Spectre-v1 | 高（pervasive in code） | lfence 在 gadget 前；IndexMask |
| BHR（歷史移位暫存器） | 改善 PHT 索引 | Spectre-BHI（2022） | 非常高 | SW BHB clear sequence |
| BTB（分支目標緩衝） | 間接跳轉目標 | Spectre-v2 | 中 | retpoline、IBRS、eIBRS |
| RSB（返回位址棧） | ret 目標 | Spectre-RSB/ret2spec | 中 | RSB stuffing、SMEP |
| IBPB（Indirect Branch Predictor Barrier） | N/A（這是防禦） | 清除所有 BPU 狀態 | N/A | 用於 context switch 時清除 |

## 踩雷集錦

**1. 「PHT 是全局唯一的」**

不對。現代 CPU 通常有 multiple levels 的預測器（L1 BHT, L2 BHT），而且每個邏輯核心有自己的預測器（不跨核心共享）。但在同一個邏輯核心的不同 privilege level（user/kernel）之間，PHT 通常是共享的——這是 Spectre-v1 的攻擊面之一。

**2. 「BTB 按 process 隔離」**

沒有隔離。在 eIBRS 之前，BTB 在同一物理核心的所有 context（不同 process、user vs kernel）之間完全共享。攻擊者 process 訓練 BTB → 受害者 process 使用同一個 BTB entry 進行推測 → 跨 process 的 BTB 污染成立。這是 Spectre-v2 能做到跨 context 攻擊的根本原因。

**3. 「retpoline 修好了所有的間接跳轉問題」**

retpoline 修的是 BTB-based 間接跳轉預測。但 RSB-based 的 `ret` 預測是另一個攻擊面（Spectre-RSB，Ch 17）。在某些微架構（Skylake）上，當 RSB 下溢時，`ret` 預測 fallback 到 BTB，retpoline 就失效了。這促生了「RSB stuffing」技術（每次 kernel 進入時用一系列 call 把 RSB 填滿，讓它不需要 fallback 到 BTB）。

**4. 「PHT 的 2-bit counter 記不住複雜的模式」**

現代處理器的 PHT 不是簡單的 2-bit counter。Intel 用的是 TAGE（Tagged Geometric history length predictor）、ITTAGE 等高階預測器，能記住更長的歷史（up to 64 或更多分支的歷史）。Spectre-BHI 攻擊的就是這些更複雜結構裡的 history 共享問題。

**5. 「eIBRS 開了就不用管 Spectre-v2 了」**

eIBRS 防止了 user → kernel 的 BTB 污染，但「user → user」的跨 process BTB 污染（Spectre-v2 user-to-user variant）仍然需要 IBPB（在 context switch 時清除 BPU 狀態）。eIBRS 本身不清除 IBPB。這導致了 Linux kernel 在 non-SMT 情況下對 eIBRS-only 的處理有 bug，是後來多次 Spectre-v2 修補的原因之一（參見 kernel CVE-2022-23960 等）。

## 進階：再往深一層

### Intel 的 TAGEd 預測器

TAGE（Tagged Geometric history length predictor）是 Intel 從 Sandy Bridge 之後使用的核心預測結構（雖然 Intel 從未公開確認，但學術界透過 reverse engineering 推斷）：

- 多個 table，每個用不同長度的歷史（幾何級數：2, 4, 8, 16, 32, 64...）
- 每個 entry 有 tag（用 hash(PC, history) 匹配）+ 計數器
- 預測結果取「歷史最長且 tag 匹配的 table」的計數器值
- 沒有 tag 匹配時，fallback 到 base predictor

TAGE 的精確行為是 microarchitectural secret——Intel 沒有公開，但安全研究者（如 Dmitry Evtyushkin 的工作，2016）透過計時實驗 reverse engineering 了不少細節。Spectre-BHI 的發現就利用了對 TAGE 行為的理解：攻擊者能構造一個分支序列，讓 TAGE 的 history 表達攻擊者想要的 PHT 索引。

### RAS / RSB 的大小與邊界效應

RSB 通常是 16 entry（Comet Lake）。以下情況會造成 RSB 問題：

1. **巢狀呼叫超過 16 層**：RSB 滿了，最舊的 entry 被覆蓋。之後的 `ret` 如果 RSB 已空（underflow），Intel Skylake/Kaby Lake/Comet Lake fallback 到 BTB 預測——retpoline 在這個 fallback 上失效。

2. **vmexit/vmenter 時的 RSB 污染**：VMM 進入/退出 guest 時，RSB 的 guest 的狀態需要被清除或還原。KVM 的 RSB stuffing 在 vmenter 時執行 `__fill_return_buffer` 把 RSB 填滿已知安全的返回位址。

3. **longjmp / setjmp**：這些函式直接操作 stack，但不通知 RSB——RSB 和 real stack 可能不同步。之後的 `ret` 就 RSB 不命中，影響效能（但通常不是安全問題）。

### 歷史追蹤：分支預測器的演進

了解攻擊面的演進有助於理解防禦的迭代：

```
1995: Pentium Pro — 簡單 BTB + bimodal predictor
1998: Pentium II/III — gshare predictor（PC XOR global history）
2003: Pentium 4 — 兩層預測器（L1 + L2 BHT）
2006: Core 2 — 更大 BTB，改進 BHT
2011: Sandy Bridge — 推測是 TAGE-like predictor；RSB 引入
2017: Kocher et al. Spectre — BTB 跨 context 攻擊公開
2018: Skylake IBRS — BTB isolation（但效能代價高）
2019: Cascade Lake eIBRS — efficient IBRS，常開低代價
2022: Spectre-BHI — BHR 跨 context 攻擊，需要 SW sequence
```

## 各種 Spectre 變體與預測結構的對應

理解了 PHT、BTB、BHB、RSB 之後，就能精確定位每個 Spectre 變體打的是哪裡：

```
Spectre 家族全圖（截至 2023）：

PHT（條件分支方向）─────────────────────────────
  Spectre-v1 (CVE-2017-5753)
    └─ 訓練 PHT，讓 if(x<size) 被推測為 taken
       gadget 在 victim 的 intra-process AS

  Spectre-v1.1 / v1.2（Kiriansky & Waldspurger 2018）
    └─ v1.1: 寫入 OOB 的瞬態版（speculatively stores to OOB）
       v1.2: read-only 保護可被推測越過

BTB（間接跳轉目標）─────────────────────────────
  Spectre-v2 (CVE-2017-5715)
    └─ 跨 context 污染 BTB，讓 victim 的 indirect call
       推測到攻擊者選的 gadget

  Retbleed (CVE-2022-29901)
    └─ ret 指令在某些微架構上 fallback 到 BTB 預測
       讓 retpoline 失效

BHB/BHR（分支歷史）─────────────────────────────
  Spectre-BHI (CVE-2022-0001/CVE-2022-0002)
    └─ 繞過 eIBRS，透過歷史注入讓 kernel BTB 查找
       到攻擊者訓練的目標

RSB（Return Stack Buffer）───────────────────────
  Spectre-RSB / ret2spec (Koruyeh et al. 2018)
    └─ RSB 被填入攻擊者控制的返回位址
       ret 推測執行到攻擊者的 gadget

  SMEP bypass + RSB（用 Spectre-RSB 配合 SMEP 繞過）
    └─ 透過 RSB 污染讓 kernel 推測執行到 user 的 gadget
       （需要 SMEP 未啟用或有額外繞過）
```

這個全圖說明了為什麼「修好 Spectre-v2（用 retpoline + IBRS）之後還有後繼攻擊」——每個修法只關閉了一個預測結構的攻擊面，但 CPU 有多個預測結構，每個都是潛在的通道。

## 動手練習

1. **量測 misprediction penalty**：用「實驗 1」的程式，測量不同的 not-taken 頻率（1%, 5%, 10%, 50%）下的 average cycle count。觀察 misprediction penalty 是否和頻率無關（每次失誤固定 ~15–20 cycles，不管失誤多頻繁）。

2. **觀察 PHT alias**：寫兩個不同的函式，它們各自有一個條件分支，但 PC 低 12 bits 相同（可以透過 `__attribute__((section(".text.alias")))` 控制排列，或用 linker script 強制對齊）。先訓練函式 A 的分支，然後呼叫函式 B 的分支（不訓練），觀察是否有「串跨函式」的預測效果（timing 變化）。

3. **用 Linux perf 觀察 branch miss**（原生 Linux，需 perf）：
   ```bash
   perf stat -e branch-misses,branches ./your_program
   ```
   對比「全 taken」和「交替 taken/not-taken」的兩個程式，觀察 branch-miss 數量。

4. **RSB 下溢觀察**：寫一個遞迴 24 層深的函式（超過 RSB 的 16 entry），用 `rdtscp` 量測最深層 `return` 的時間。與 8 層深的版本比較，看是否有 timing 差異（RSB miss 帶來的 BTB fallback 代價）。

## 分支預測器作為攻擊面：整體評估

在「可攻擊的微架構結構」這個維度上，分支預測器是 **特別危險的一類**，原因是：

### 可訓練性（Trainability）

分支預測器不只是可觀測的——它是**可以被外部代碼修改的**。攻擊者透過執行特定的分支序列，可以把 PHT、BTB、BHB、RSB 設成任何攻擊者想要的狀態。這比「只能觀察」的攻擊面強大得多：

```
被動觀察型（如 cache timing）：
  攻擊者 → 觀察 victim 的 cache 行為 → 推導 secret

主動修改型（如 BTB pollution）：
  攻擊者 → 修改 BTB 狀態 → 觸發 victim 的推測執行 → 放大 secret 洩漏
  ↑ 攻擊者控制了 victim 的推測方向，這是質的飛躍
```

### 無清除機制（No Architectural Flush）

x86-64 ISA 沒有一條指令能「清除所有分支預測器狀態」（IBPB 是透過 MSR 的 vendor-specific 擴充，不是 ISA 標準指令）。這意味著：
- OS context switch 不會自動清除 BP 狀態
- 同一個核心的不同 process 共享同一套 BP（沒有加 IBPB 的情況下）
- 這是 Spectre-v2 成立的根本前提

### 跨 privilege 的透通性

在沒有 IBRS 的情況下，user mode 的分支歷史直接影響 kernel mode 的分支預測（因為它們用同一個 PHT 和 BTB）。這打破了 ring 0 和 ring 3 的隔離——不是記憶體層面的隔離（page table 仍然有效），而是「推測執行方向」的隔離。

### 修法的根本困境

修分支預測器攻擊面的根本問題是：**分支預測器越精準，攻擊面越大**。

更精準的預測器：
- 記更長的歷史（更大的 BHR）→ 更容易被歷史注入（BHI）
- 有更多 pattern 的分類能力（TAGE）→ 更多可以被 aliased 的 entry
- 對 indirect call 的目標更精準（ITTAGE）→ BTB alias 的粒度更小，但 aliasing 仍然存在

這是一個根本性的 tension：效能和安全在這裡不能同時完全滿足。目前的修法（IBRS、eIBRS、retpoline、BHI SW 序列）都是在「可接受的效能代價」下最大化隔離，而不是根本消除攻擊面。

## 本章重點整理

- **PHT（Pattern History Table）**：預測條件分支方向（taken/not-taken），以 PC 低位元 + 分支歷史（BHR）為 index，每個 entry 是 2-bit saturating counter 或更複雜結構。Spectre-v1 打這裡。
- **BTB（Branch Target Buffer）**：預測間接跳轉/直接跳轉的目標位址，跨 context 共享（沒有 IBRS 時）。Spectre-v2 打這裡。
- **BHR/BHB（Branch History Register/Buffer）**：記錄最近 N 條分支的 taken/not-taken 歷史，用於改善 PHT 索引。Spectre-BHI 打這裡。
- **RSB（Return Stack Buffer）**：預測 `ret` 指令目標，基於 call/ret 配對，通常 16 entry。Spectre-RSB 打這裡。
- **跨 context 共享是根本問題**：所有這些結構在沒有隔離機制的情況下，在同一物理核心的不同 process 和 privilege level 之間共享。
- **修法各不同**：eIBRS 修 BTB 跨 privilege 污染，retpoline 避免 BTB 被用於間接跳轉，RSB stuffing 保護 ret，BHB SW 清除序列修 BHI。

## 自我檢核

1. PHT 的 2-bit saturating counter 狀態機有 4 個狀態。為什麼用 2-bit 而不是 1-bit？1-bit 的預測器有什麼問題（提示：想想「大部分時間 taken，偶爾 not-taken」的循環）？
2. BTB 和 PHT 的作用分別是什麼？如果只有 PHT 沒有 BTB，間接跳轉 `jmp rax` 的預測會怎樣？
3. 解釋「RSB 下溢」這個問題：什麼情況下會發生，Intel 的某些微架構如何 fallback，這個 fallback 對 retpoline 防禦有什麼影響？
4. BHR（Branch History Register）和 BTB 哪個更難防禦 Spectre 攻擊？為什麼？
5. IBPB（Indirect Branch Predictor Barrier）和 IBRS（Indirect Branch Restricted Speculation）的區別是什麼？各自在什麼場景下使用？

## 延伸閱讀

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  讀 Section VI（Discussion: Understanding Spectre v2 Gadgets）和 Appendix A（BPU Details）。這裡有 BTB 和 RSB 的更多技術細節，以及攻擊者如何選擇和訓練 BTB。關聯：本章介紹的 BTB 結構在這裡有具體攻擊應用。

- **[Spectre Returns! Speculation Attacks using the Return Stack Buffer](https://arxiv.org/abs/1807.07940)** — Koruyeh et al., WOOT 2018
  Spectre-RSB（ret2spec）的原始論文。讀 Section III（Attack Model）了解 RSB 下溢如何被利用，以及各個微架構的差異。本課 Ch 17 的直接依據。

- **[Branch History Injection (BHI): New Spectre-v2 Variant](https://www.vusec.net/projects/bhi-spectre-bhb/)** — VUSec, Intel CVE-2022-0001
  2022 年的新發現，展示了在 eIBRS 下仍然可以透過 BHB（分支歷史）做 Spectre-v2 風格的攻擊。讀 Technical Details 部分，了解為什麼 eIBRS 只修了 BTB 但沒有修 BHB 的問題。

- **[Reverse Engineering Intel Branch Predictors](http://www.cs.unc.edu/~efeng/docs/evtyushkin2016.pdf)** — Evtyushkin et al., IEEE S&P 2016
  在 Spectre 公開之前，這篇論文透過純計時實驗 reverse engineering 了 Intel BTB 的結構（set-associative，4-way，幾千個 entry）。讀 Section IV（BTB Structure Analysis），了解攻擊者如何在不知道 CPU 文件的情況下推導出可攻擊的 alias 條件。

---

→ [下一章：Ch 16 Spectre v2（Branch Target Injection）](16-spectre-v2-branch-target-injection.md)
