# 練習 C — race + memory ordering bug 抓蟲實況

> 目標：拿到一份故意埋了 IRQ race + memory ordering bug 的 Cortex-M3 程式，**完整跑一次 debug 流程**：從觀察症狀、用 ITM 加 trace、用 watchpoint 鎖 culprit、最終定位並修補。把 Part 4 全套工具串起來。

## 任務規格

下面這份程式應該每秒印 `count: <N>` 而 N 嚴格 +1 遞增。實際跑會偶爾印**重複數字**或**跳號**。

```c
/* buggy.c — 故意埋兩個 bug */
#include <stdint.h>

volatile uint32_t shared_count = 0;
volatile uint32_t ready = 0;

void TIM2_IRQHandler(void) {
    /* 模擬感測器中斷：累積到 shared_count */
    uint32_t local = shared_count;
    /* 故意延遲，讓 race window 變大 */
    for (volatile int i = 0; i < 100; i++) ;
    shared_count = local + 1;        /* BUG 1：non-atomic increment */

    if (shared_count >= 1000)
        ready = 1;                    /* BUG 2: write ready 沒 release barrier */

    TIM2->SR &= ~1;
}

void SysTick_Handler(void) {
    static uint32_t print_count = 1;
    if (print_count <= 10) {
        if (ready) {                  /* BUG 2 配對：read ready 沒 acquire */
            uint32_t snap = shared_count;
            uart_printf("count: %u\n", snap);
            print_count++;
        }
    }
}

int main(void) {
    init_clock();
    init_uart();
    init_tim2(1);          /* 1 kHz */
    init_systick(100);     /* 100 ms */
    init_itm();
    while (1) __WFI();
}
```

埋了兩個 bug：

1. **BUG 1**：`shared_count = local + 1` 不是 atomic。如果 TIM2 IRQ 在「load → +1 → store」之間被另一個（更高優先）IRQ 打斷，且那個 IRQ 也改 `shared_count`，會丟更新。**單核 Cortex-M 有 nested IRQ，這個 bug 真實會發生**。

2. **BUG 2**：寫 `ready = 1` 在 `shared_count` 寫之後（program order），但 ARM 弱記憶體模型下 SysTick 看到 `ready == 1` 時 `shared_count` 不一定已經更新。因為 Cortex-M3 是 in-order pipeline + strong ordering on single hart？實際上 Cortex-M 的 store 也可能 buffered，DMB 還是要做。（這個 bug 在 Cortex-M3 表現比 Cortex-A 弱，但仍可被外部 master 觀察到）。

## 期望輸出 vs 實際

期望：

```
count: 1
count: 2
count: 3
count: 4
count: 5
count: 6
count: 7
count: 8
count: 9
count: 10
```

實際（intermittent）：

```
count: 1
count: 2
count: 3       ← 看起來正常
count: 3       ← 重複了！
count: 5       ← 跳號了
count: 6
count: 7
count: 7
count: 9
count: 10
```

## 實作步驟建議

### Step 1：先確認 bug 真的存在

跑 100 次，統計「重複 / 跳號」次數。Bash one-liner：

```bash
for i in {1..100}; do
    output=$(qemu-system-arm -M mps2-an385 -kernel buggy.elf -nographic -semihosting -serial null -monitor none -no-reboot -append "" -timeout 5 2>&1)
    echo "$output" | grep -c "count:"
done | sort | uniq -c
```

如果偶爾 < 10，就是 race 觸發過。

### Step 2：用 ITM 加 trace

在 IRQ 進入 / 退出加 ITM 寫：

```c
void TIM2_IRQHandler(void) {
    ITM->PORT[1].u8 = 'T';      /* TIM2 entry marker */
    uint32_t local = shared_count;
    ITM->PORT[2].u32 = local;   /* 印當下 local 值 */
    for (volatile int i = 0; i < 100; i++) ;
    shared_count = local + 1;
    ITM->PORT[3].u32 = shared_count;
    if (shared_count >= 1000) ready = 1;
    TIM2->SR &= ~1;
    ITM->PORT[1].u8 = 't';      /* TIM2 exit marker */
}
```

OpenOCD 端 dump SWO：

```
itm port 1 on
itm port 2 on
itm port 3 on
tpiu config internal /tmp/swo.bin uart off 25000000 1000000
```

跑一輪後 parse `swo.bin`，重建 trace 流。應該看到「IRQ A entry → 跑了一半 → IRQ B entry → IRQ B exit → IRQ A exit」這種 nesting 模式，而 IRQ B 與 IRQ A 看到的 `local` 值都是同一個 → 確認 BUG 1。

### Step 3：用 watchpoint 鎖 update 點

GDB:

```
(gdb) watch shared_count
(gdb) c
[hit watch in TIM2_IRQHandler at line X]
(gdb) bt
(gdb) info reg ipsr           ← 看當前是哪個 IRQ
(gdb) c
[hit watch again, 不同 caller?]
```

連續觀察兩次 watch 命中，看 IPSR 是否同一個 IRQ。如果**不同 IRQ 各自命中** = 確認多個 source 在改 → race 確認。

### Step 4：寫 GDB Python script 自動化

每次 watch 命中印 IRQ 編號 + shared_count 值 + LR：

```python
import gdb

def on_stop(event):
    if isinstance(event, gdb.BreakpointEvent):
        for bp in event.breakpoints:
            if bp.is_watchpoint():
                ipsr = int(gdb.parse_and_eval("$ipsr"))
                count = int(gdb.parse_and_eval("shared_count"))
                lr = int(gdb.parse_and_eval("$lr"))
                print(f"[IPSR={ipsr & 0x1ff:3d}] shared_count={count} from LR=0x{lr:08x}")
                gdb.execute("continue")

gdb.events.stop.connect(on_stop)
gdb.execute("watch shared_count")
gdb.execute("continue")
```

這個自動印 trace 而不停下來。跑一陣子看到 IPSR 不同的 caller 互相打架 → race 確認。

### Step 5：修 BUG 1

最直接：把 increment 改成 atomic，或用 critical section：

```c
// 方案 A：用 ARM atomic（CMSIS）
void TIM2_IRQHandler(void) {
    __atomic_add_fetch(&shared_count, 1, __ATOMIC_ACQ_REL);
    if (__atomic_load_n(&shared_count, __ATOMIC_ACQUIRE) >= 1000)
        __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    TIM2->SR &= ~1;
}

// 方案 B：用 critical section
void TIM2_IRQHandler(void) {
    uint32_t primask = __get_PRIMASK();
    __disable_irq();
    shared_count++;
    if (shared_count >= 1000) {
        __DMB();
        ready = 1;
    }
    __set_PRIMASK(primask);
    TIM2->SR &= ~1;
}
```

方案 A 用 LDREX/STREX retry loop，IRQ 失敗會重試。方案 B 用 PRIMASK mask 全部 IRQ — 簡單但會延遲 hard real-time IRQ。

### Step 6：修 BUG 2

寫者：

```c
shared_count = ...;
__DMB();             // 確保 shared_count 寫到 memory 後再寫 ready
ready = 1;
```

讀者：

```c
if (ready) {
    __DMB();         // 確保 read ready 後 read shared_count
    uint32_t v = shared_count;
}
```

或用 `__atomic_*` 配 `__ATOMIC_RELEASE` / `__ATOMIC_ACQUIRE` 自動產生屏障。

### Step 7：驗證修復

重跑 step 1 的 100 次測試，全部要印 1..10 完整無漏。

## 完整參考解答

<details>
<summary>修好的版本</summary>

```c
#include <stdint.h>

volatile uint32_t shared_count = 0;
volatile uint32_t ready = 0;

void TIM2_IRQHandler(void) {
    /* atomic increment with acquire-release */
    uint32_t new = __atomic_add_fetch(&shared_count, 1, __ATOMIC_ACQ_REL);
    if (new >= 1000) {
        __atomic_store_n(&ready, 1, __ATOMIC_RELEASE);
    }
    TIM2->SR &= ~1;
}

void SysTick_Handler(void) {
    static uint32_t print_count = 1;
    if (print_count <= 10) {
        if (__atomic_load_n(&ready, __ATOMIC_ACQUIRE)) {
            uint32_t snap = __atomic_load_n(&shared_count, __ATOMIC_RELAXED);
            uart_printf("count: %u\n", snap);
            print_count++;
        }
    }
}
```

</details>

## 測試用例

1. **修補前**：跑 100 次，印重複或跳號的次數應 > 0
2. **修補後**：跑 100 次，每次都印 1..10 嚴格遞增，無重複、無跳號
3. **加大壓力**：把 TIM2 頻率拉到 10 kHz，仍應穩定

## 自我檢核

- [ ] 我能用 ITM 在 IRQ entry/exit 加 trace marker
- [ ] 我能用 OpenOCD 把 SWO dump 到 file 並 parse
- [ ] 我能用 GDB watchpoint + IPSR 確認哪個 IRQ 在改變數
- [ ] 我能寫 GDB Python 自動 dump trace 而不阻塞
- [ ] 我能識別 non-atomic increment race 並用 `__atomic_*` 修
- [ ] 我能識別 release / acquire 缺失並用 DMB 修

到這裡 Part 4 與三個練習都結束。下一個 Part 進階主題 — SIMD、PAC/BTI/MTE、CPU bug 史、ARM ARM 閱讀法、與 ARM vs x86 vs RISC-V 反思。

→ [Ch 31 SIMD：NEON、Advanced SIMD、SVE/SVE2](./31-simd-neon-sve.md)
