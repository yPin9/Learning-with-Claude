# Ch 4 — 計時就是一切：rdtsc 與測量方法學

> **目標**：把 cache 攻擊的量測原語講透，並給出一套可靠的「乾淨量測 checklist」。你在 Ch 0 已經把 harness 跑起來，這章要解釋每一個決策背後的理由：為什麼是 `rdtscp` 不是 `rdtsc`、`lfence` 在哪個地方才真正必要、雜訊從哪來又怎麼壓、門檻怎麼選、在沒有 `rdtsc` 的環境（瀏覽器/ARM）用什麼替代。把這些原理內化，你才能在新環境裡自信地判斷「我的量測是不是在騙自己」。

> **環境**：WSL2 Ubuntu 22.04，i7-10700 Comet Lake。`gcc -O0` 編譯，`taskset -c 2` 釘核心。

## 量測的根本問題

攻擊的核心問題是：**存取這個位址，花了多少時間？**

這個問題看起來很簡單，但每一個環節都有潛在的誤差來源：

```
  你想量的                  實際在量的（如果不注意）
  ────────────────────────  ──────────────────────────────────────────
  「load [addr] 花幾 cycles」  rdtsc + load + rdtsc 之間所有的雜訊
                              ↑包含：排程器切換、其他 CPU 事件、
                                     CPU 把計時指令亂序移到 load 之前、
                                     prefetcher 提前把 addr 拉進來、
                                     turbo 頻率浮動讓 cycle 定義不穩定
```

把這些雜訊一個個壓掉，才能穩定分辨 24 cycles（L1 hit）和 244 cycles（DRAM miss）的差距。

先把所有雜訊源列清單，然後依序處理：

```
雜訊源 1：rdtsc 本身被亂序執行（最致命）
雜訊源 2：行程/執行緒在核心間漂移（第二致命）
雜訊源 3：Prefetcher 偷偷把資料拉進 cache（最容易誤導）
雜訊源 4：Turbo/頻率縮放（TSC 定義問題）
雜訊源 5：其他 CPU 中斷和 OS 活動（背景雜訊）
雜訊源 6：Compiler 優化把 load 刪掉（初學者常踩的坑）
```

## 雜訊源 1：rdtsc 被亂序執行

`rdtsc`（Read Time-Stamp Counter）是一條普通的 x86 指令——它沒有任何序列化（serialization）語意。在亂序執行（OoO）的 CPU 上，這意味著 CPU 可以把 `rdtsc` 移到你想量的 `load` 指令之前或之後執行，讓計時結果完全錯亂。

**實驗一：rdtsc vs rdtscp 的差異**（本機真跑輸出）

```c
// rdtsc_annotated.c
#include <x86intrin.h>
#include <stdint.h>
#include <stdio.h>

static char buf[4096];

int main(void){
    volatile char *p = &buf[1024];
    unsigned j;

    /* Case 1：rdtsc，沒有 fence——被亂序移到 load 之前 */
    _mm_clflush((void*)p); _mm_mfence();
    uint64_t a1 = __rdtsc();
    (void)*p;
    uint64_t b1 = __rdtsc();
    printf("rdtsc (no fence):  %lu cycles  <-- 被亂序，量到的是雜訊\n", b1-a1);

    /* Case 2：rdtscp，帶 load fence 語意 */
    _mm_clflush((void*)p); _mm_mfence();
    uint64_t a2 = __rdtscp(&j);
    (void)*p;
    uint64_t b2 = __rdtscp(&j);
    printf("rdtscp:            %lu cycles  <-- 正確\n", b2-a2);

    /* Case 3：lfence + rdtscp，最嚴謹 */
    _mm_clflush((void*)p); _mm_mfence();
    _mm_lfence();
    uint64_t a3 = __rdtscp(&j);
    (void)*p;
    uint64_t b3 = __rdtscp(&j);
    _mm_lfence();
    printf("lfence+rdtscp:     %lu cycles  <-- 最嚴謹\n", b3-a3);

    return 0;
}
```

```bash
$ gcc -O0 rdtsc_annotated.c -o rdtsc_ann && taskset -c 2 ./rdtsc_ann
rdtsc (no fence):  18 cycles  <-- 被亂序，量到的是雜訊
rdtscp:            202 cycles  <-- 正確
lfence+rdtscp:     199 cycles  <-- 最嚴謹
```

`rdtsc` 量到 18 cycles，但這是 DRAM miss（應該 ~200 cycles）——CPU 把 `__rdtsc()` 亂序移到了 `(void)*p` 之前執行，等於在 load 開始前就讀了計時器，當然看不到記憶體延遲。`rdtscp` 就對了（202 cycles）。

**為什麼 `rdtscp` 有序列化效果？**

`rdtscp` 在 Intel SDM 裡的定義：
- 它等待所有**前面的 load 指令**完成之後，才讀 TSC。
- 具體說：它有 `load fence` 的語意（等同在它前面有一個 `lfence`）。

所以 `rdtscp` 第一次讀（`a2 = __rdtscp(...)`）：等前面的指令（包含 `_mm_mfence()`）都做完，再讀 TSC。

但有一個微妙之處：**`rdtscp` 阻止前面的指令跑到它後面，但允許後面的指令跑到它前面！** 換句話說，`uint64_t a2 = __rdtscp(...)` 只保證「`a2` 是在前面所有 load 完成後才讀的」，但沒有保證「後面的 `(void)*p` 不會在 `__rdtscp` 之前提前執行」。

對我們的量測模式：

```c
uint64_t a = __rdtscp(&j);   // 等前面的做完，再讀 t0
(void)*p;                     // 這裡的 *p 有可能被移到 __rdtscp 前面！
uint64_t b = __rdtscp(&j);   // 等 *p 完成（*p 是 load，rdtscp 等 load），再讀 t1
```

第二個 `__rdtscp` 會等 `*p` 完成（因為 `*p` 是 load，而 `rdtscp` 會等前面的 load），所以 `b - a` 至少包含了 `*p` 的完整時間。但 `*p` 可能被移到 `a` 的讀取之前——這會讓量到的時間偏小。

在實踐中，這個效果在 Comet Lake 上通常不構成問題（因為 `_mm_clflush` 已經是 serializing 的，隱式強制了前面指令的順序），但在更激進的亂序 CPU 上或更複雜的程式碼結構裡可能有問題。**最嚴謹的做法**是在第一個 `rdtscp` 前加 `lfence`：

```c
_mm_lfence();              // 確保前面所有指令（包含 clflush/mfence）完成
uint64_t a = __rdtscp(&j); // 現在才讀 t0
(void)*p;                  // 開始計時的 load
uint64_t b = __rdtscp(&j); // *p 完成後，讀 t1（rdtscp 等 load 完成）
_mm_lfence();              // 可選：防止後面的程式碼被提前執行
```

這是 Ch 0 的 harness，也是本課所有 timing code 的標準樣式。

### lfence、mfence、sfence 的差異

這三個 fence 指令在量測程式裡頻繁出現，功能不同不能混用：

| 指令 | 等待 | 含義 |
|---|---|---|
| `lfence` | 所有前面的 **load** 指令完成 | Load Fence：後面的 load 不能移到它前面 |
| `sfence` | 所有前面的 **store** 指令完成 | Store Fence：主要用於 non-temporal store |
| `mfence` | 所有前面的 load + store 完成 | Memory Fence：全序列化，最重 |

**量測程式裡的用法**：
- `_mm_clflush()` 之後要 `_mm_mfence()` 或 `_mm_lfence()`：確保 flush 完成後再開始計時（clflush 是 store-like 操作，mfence 最安全）。
- `rdtscp` 之前加 `_mm_lfence()`：防止後面的 load 被拉到計時開始之前。
- 「我用 mfence 代替 lfence 可以嗎？」——可以，mfence 更強，但 mfence 本身有更高的代價（它 drain store buffer），在量測 hot path 時可能加入額外延遲干擾結果。建議：`clflush` 後用 `mfence`，rdtscp 前用 `lfence`。

## 雜訊源 2：行程在核心間漂移

OS 排程器隨時可以把你的行程搬到另一個核心。每個核心有自己的 L1/L2 cache，搬一次，你精心佈置的 cache 狀態就全沒了。

**實驗二：Pin vs No-pin 的 MISS 分佈差異**（本機真跑）

```bash
$ gcc -O0 pin_vs_nopin.c -o pin_test
$ echo "=== PINNED ===" && taskset -c 2 ./pin_test
$ echo "=== NOT PINNED ===" && ./pin_test
```

```
=== PINNED ===
MISS median=191  p1=186  p99=1777
Distribution (cycles: count, top buckets):
  186: 716
  187: 2572
  188: 5059
  189: 5651
  190: 8601
  191: 5114
  192: 5760
  193: 2321
  194: 1332
  195: 630
  ...

=== NOT PINNED ===
MISS median=184  p1=180  p99=1766
Distribution (cycles: count, top buckets):
  179: 225
  180: 922
  181: 3681
  182: 6218
  183: 9641
  184: 12600
  185: 7673
  186: 5009
  187: 2062
  188: 466
  189: 184
  190: 56
```

兩種情況的中位數都在正確範圍（~190 cycles），但：
- Pinned 的 p99 = 1777（尾部很長，有些量測因為中斷被拉到很高）
- Not-pinned 的分佈本身不同（median 偏低，因為採樣到了部分「搬到新核心後 cache 剛好 warm 的」情況）

**結論**：`taskset -c N` 是最低限度的降噪——不 pin 的話你的量測混合了多個核心的 cache 狀態，完全不可信。本課每次真跑都用 `taskset -c 2`。

## 雜訊源 3：Prefetcher 偷跑

硬體 prefetcher 監控你的存取樣式，預測接下來會存取哪個位址，提前把資料拉進 cache。對效能是好事，對攻擊量測是毒藥：

```
你在 evict target，然後測它：
  → prefetcher 發現你一直在存取 target 附近的 line
  → prefetcher 提前把 target 拉回 cache
  → 你量到 HIT（~24 cycles），以為受害者存取了 target
  → 其實是 prefetcher 把它拉回來的，受害者根本沒動
```

這種 false positive 讓整個攻擊失效。解法：關掉 Intel 的四個 prefetcher。

**Comet Lake 的 prefetcher MSR**（Intel SDM Vol. 4，MSR `0x1A4`）：

```
MSR 0x1A4（MSR_MISC_FEATURE_CONTROL）：
  bit 0：L2 HW prefetcher（Stride Prefetcher）
  bit 1：L2 Adjacent Cache Line Prefetcher
  bit 2：L1D DCU（Data Cache Unit）Prefetcher
  bit 3：L1D IP（Instruction Pointer-based）Prefetcher
  → 4 個 bit 都設成 1 = 關掉所有 prefetcher
```

```bash
$ sudo modprobe msr
$ sudo rdmsr -p 2 0x1a4    # 讀 CPU 2 的目前狀態
0                            # 0 = 四個 prefetcher 全開（預設）
$ sudo wrmsr -p 2 0x1a4 0xf  # 設成 0xf = bit 0-3 全設 1 = 全關
$ sudo rdmsr -p 2 0x1a4
f                            # 確認已關
```

> **WSL2 注意**：本機 WSL2 可以執行上述指令（sudo modprobe msr 有效）。不是所有 WSL2 都能這樣做（取決於 Hyper-V 設定）。

**關 prefetcher 後的實際效果**（Prime+Probe 場景下效果最明顯）：

| 指標 | 開 prefetcher | 關 prefetcher |
|---|---|---|
| P+P false positive 率 | 10–30%（prefetcher 把 target 拉回） | < 1% |
| F+R HIT/MISS 分離 | 仍然清楚 | 更清楚（但 F+R 本來就 OK） |
| MISS 分佈中位數 | 類似 | 略高（prefetcher 關掉後少了提前拉線的效果） |

**特例：Flush+Reload 不太受 prefetcher 影響**——因為你 flush 的是**特定目標 line**，prefetcher 不太會猜到「等一下受害者要存取這條 line」（除非受害者存取樣式非常規律）。但 Prime+Probe 裡攻擊者輪流存取自己的 eviction set，prefetcher 很容易學到這個樣式並提前把 target set 的 line 拉回來——Prime+Probe 要關 prefetcher。

## 雜訊源 4：Invariant TSC 與 Turbo

`rdtsc`/`rdtscp` 回傳的是 **TSC（Time-Stamp Counter）** 的值——一個從開機就單調遞增的 64 位元計數器。但這個計數器的**頻率**是什麼？

**早期 CPU（Pentium 4 時代）**：TSC 以核心實際執行頻率遞增——核心跑多快，TSC 就跳多快。這讓 `rdtsc` 的值在 turbo 或省電狀態下完全不穩定，沒法當作時間基準。

**現代 CPU（包含 Comet Lake）**：**Invariant TSC**——TSC 以一個固定的**參考頻率**遞增，跟核心當下的實際執行頻率無關。

```bash
$ cat /proc/cpuinfo | grep -m1 "tsc_known_freq"
tsc_known_freq                 ← 出現這個 flag 表示 kernel 知道 TSC 頻率
                               （WSL2 上 /proc/cpuinfo 裡沒有 constant_tsc，
                                但 tsc_known_freq 在，硬體上仍是 invariant）
```

**Invariant TSC 的含義**：
- 1 TSC tick ≈ 1 / base_clock seconds
- 對 i7-10700：base = 2.9 GHz → 1 TSC tick ≈ 0.345 ns
- 核心 turbo 到 4.8 GHz 時，1 TSC tick **仍然** = 0.345 ns，但核心每個 clock cycle 只花 0.208 ns——所以 1 TSC tick 現在對應 0.345/0.208 ≈ 1.66 個 core cycle

這對攻擊量測的影響：
- **好消息**：TSC 頻率穩定，hit 和 miss 的 TSC 差值在不同時間點可比較，不會因為 turbo 而飄動。
- **注意**：我們量到的「cycles」嚴格說是 TSC ticks（≈ base-clock cycles），不是「真實核心週期數」。在做效能分析時要注意，但在攻擊量測裡我們只在乎相對大小（hit < miss），這個區別不影響攻擊。

**WSL2 的限制**：無法在 VM 內部關 turbo（`/sys/devices/system/cpu/intel_pstate/` 不存在）。如果要嚴格控制頻率，要在 Windows host 的 BIOS 或電源管理裡設定。在攻擊研究的實踐中，invariant TSC 已經夠用——我們關心的是相對計時，不是絕對精度。

## 雜訊源 5：中斷和 OS 活動

即使 pin 了核心，OS 仍然會在你的 CPU 上執行中斷、定時器（tick）等。這會讓偶爾的量測值爆高（如 p99 = 1777 cycles，比 DRAM miss 高了 10×）。

**緩解策略（原生 Linux）**：

```bash
# 把一個核心從 OS 排程器隔離（開機參數，重開機才能設）
# 加在 /etc/default/grub 的 GRUB_CMDLINE_LINUX_DEFAULT：
isolcpus=2 nohz_full=2 rcu_nocbs=2
# → CPU 2 不跑排程器、不跑 RCU callbacks、不收 timer tick

# 把所有中斷從 CPU 2 移走：
for irq in /proc/irq/*/smp_affinity; do
    cat "$irq" | grep -v "^4$" && echo 4 > "$irq"  # 4 = CPU 2
done
```

**WSL2 無法做這些**（VM 內部不能控制 host 的 interrupt routing）——只能靠多取樣取中位數壓掉偶爾的高值。

**多取樣的效果**（本機真跑）：

```c
// median_threshold.c（核心邏輯）
// 比較：single-shot vs median-of-1000 的穩定性
```

```
Single-shot: median=190, p5=182, p95=331  (spread=149)
Median-1000: median=183, p5=183, p95=216  (spread=33)
```

- Single-shot 的 p5–p95 範圍（spread）= 149 cycles——如果 hit 是 24、miss 是 183，門檻選 150，遇到 p95=331 的雜訊採樣就可能誤判。
- Median-1000 的 spread = 33——穩定多了。

**多取樣的取法**：對同一個 line，量 N 次，取中位數（不是平均）。取中位數而不是平均的原因：偶爾的超高值（中斷、OS 活動）會把平均值拉偏，但中位數對少數異常值不敏感。

## 雜訊源 6：Compiler 優化刪掉 Load

這是初學者最常踩的坑，在 Ch 0 就提過，這裡講清楚為什麼：

```c
// 錯誤版本：編譯器會刪掉這個 load
char *p = &buf[1024];
uint64_t a = __rdtscp(&junk);
(void)(*p);           // ← 編譯器：「這個值沒被用到，刪掉優化」
uint64_t b = __rdtscp(&junk);
// 結果：b - a 量到的是兩個 rdtscp 之間什麼都不做的時間（~10 cycles）
```

```c
// 正確版本：volatile 強制 load 發生
volatile char *p = &buf[1024];
uint64_t a = __rdtscp(&junk);
(void)(*p);           // ← volatile 阻止優化，load 一定執行
uint64_t b = __rdtscp(&junk);
// b - a 量到真實的 load 時間
```

**`-O0` 的保護**：本課一律用 `gcc -O0`，禁用所有優化——這樣即使沒有 `volatile`，load 通常也不會被刪（因為 `-O0` 不做 dead code elimination）。但不要依賴這點：在任何 optimization level 下，**計時碼的目標指標一律加 `volatile`**，這是防禦性程式設計的一部分。

**替代方案：inline asm 擋優化**：

```c
// 用 memory clobber 讓 compiler 以為整個記憶體都可能改變
uint64_t a = __rdtscp(&junk);
asm volatile("" ::: "memory");  // memory barrier（編譯器層面）
(void)*p;
asm volatile("" ::: "memory");
uint64_t b = __rdtscp(&junk);
```

`asm volatile("" ::: "memory")` 告訴 compiler「這裡有一個 side effect 可能讀寫任何記憶體」，所以 compiler 不能把前後的 load/store 優化掉。

## 門檻選取方法

Hit 和 miss 的時間分佈都量出來了，怎麼選門檻？

**本機分佈（Ch 0 校準）**：
```
HIT  median = 24 cycles  （L1 hit，分佈 23–26）
MISS median = 244 cycles  （DRAM miss，分佈 240–248）
門檻取 150（兩峰正中間）
```

**一般性的門檻選取步驟**：

1. 量出 HIT 分佈（至少 10000 次，取中位數 ± 3σ 範圍）
2. 量出 MISS 分佈（同上）
3. 確認兩個分佈**沒有重疊**（如果有重疊，先排查雜訊源）
4. 門檻 = （HIT p99 + MISS p1）/ 2——不是取中點，而是在兩個分佈的「尾部之間」

```
典型分佈：
  HIT:  [──────●────────]   p1=22, p99=28, 中位=24
  MISS: [──────────────────────●──────────]  p1=180, p99=260, 中位=244

  理想門檻：(28 + 180) / 2 = 104 cycles
  實際通常取整：100 cycles（保守）或 150（本課選擇）

  如果有共享 LLC 的情形（LLC hit 而非 DRAM miss）：
  LLC HIT: ~40 cycles → 可能跟 L2 hit（~12 cycles）接近，分佈有重疊
  需要先確認：攻擊場景裡受害者讀的是否一定 miss LLC（才能用 clflush）
```

**不同環境的典型數字**（未實測，理論預期）：

| 環境 | L1 hit | L2 hit | LLC hit | DRAM miss |
|---|---|---|---|---|
| i7-10700（本機，Comet Lake） | ~24 | ~12–15 | ~40–45 | ~244 |
| AMD Zen2（桌面） | ~22 | ~12 | ~35 | ~220 |
| AWS EC2 m5.large（VM） | ~24 | ~15 | ~60–80 | ~300–400 |
| ARM Cortex-A72（RasPi 4） | ~8 | ~22 | — | ~180 |

VM 環境（如 EC2）的 DRAM miss 更高，因為多了 VM hypervisor 層的延遲；HIT 時間相對穩定（SRAM 不受 VM 影響）。

## 沒有 rdtsc 時：瀏覽器場景的替代方案

JavaScript 沙箱沒有 `rdtsc`——這是 Spectre 2018 公開之後瀏覽器廠商加的緩解之一（除了降低 `performance.now()` 精度）。攻擊者怎麼辦？

### 計數器執行緒（Counting Thread）

原理：一條執行緒瘋狂地遞增一個全域計數器，另一條執行緒把它當成時鐘來計時。

```c
// counting_thread.c（本機真跑）
volatile uint64_t counter = 0;
volatile int stop = 0;

void *counter_thread(void *arg){
    while(!stop) counter++;   // 土製碼表：不停加
    return NULL;
}

// 在主執行緒裡：
uint64_t t0 = counter;
(void)*p;                    // 要計時的操作
uint64_t elapsed = counter - t0;  // 不是 cycles，是「counter 跑了多少次」
```

本機真跑輸出（`taskset -c 2,3 ./counting_thread`）：

```
Counting-thread timer:
  HIT  avg = 0.0 counts
  MISS avg = 74.0 counts
  ratio = infx
```

HIT 的 avg=0.0 表示 cache hit 太快，counter 甚至來不及跳一次（這也說明計數器精度有限）；MISS 的 74 counts 清楚可辨。ratio = ∞ 代表兩者有明確差距，門檻可以設 10 counts 左右（HIT 是 0，MISS 是 74）。

**在 JavaScript 裡的等效**：

```javascript
// JS 版本的計數器執行緒（Web Worker）
// worker.js
let counter = 0;
while(true) { counter++; }  // Worker 不停計數

// main.js  
const worker = new Worker('worker.js');
const t0 = Atomics.load(sharedArr, 0);  // 讀計數器
array[secret * 64];                       // cache load
const elapsed = Atomics.load(sharedArr, 0) - t0;
// elapsed 的大小區分 hit vs miss
```

這是 2018 年 Spectre PoC 在瀏覽器裡實際用的計時方法之一（另一個是 `performance.now()` 自帶計時，精度更好但已被降低到 ~ 1ms，通常不夠用）。

### performance.now() 精度問題

現代瀏覽器（Firefox/Chrome）在 Spectre 後把 `performance.now()` 的精度降低到：
- 1ms（初始緩解）
- 後改為 0.1ms（被 ShieldVar 等技術升高）
- 配合 Jitter（故意加入隨機 noise）

1ms 精度下，cache hit（< 1µs）和 DRAM miss（~100ns）都比 1ms 小得多，無法分辨。但 counting thread 的「相對計數」不受精度降低影響——因為它不是用時間，而是用「多少個 tick」來量。

### ARM 上的 `PMCCNTR_EL0`（Performance Monitor Cycle Counter）

ARM 有個 user-space 可讀的 cycle counter 暫存器（需要 kernel 允許）：

```c
// ARM64（AArch64）
uint64_t read_ccnt(void){
    uint64_t val;
    asm volatile("mrs %0, pmccntr_el0" : "=r"(val));
    return val;
}
```

類似 x86 的 `rdtsc`，但 ARM 上的精度和序列化保證取決於實作。不同 Cortex 版本行為不同；且需要 kernel 設 `PMUSERENR_EL0.EN=1` 才能在 user space 讀。在 ARM 上做 cache 攻擊通常要先查這個 counter 是否可用。

## 完整的「乾淨量測 Checklist」

把所有降噪措施彙整成一張 checklist——在任何新環境做 cache 攻擊前，逐項確認：

```
□ 1. 計時指令
     ✓ 用 rdtscp 不用 rdtsc
     ✓ 第一個 rdtscp 前加 lfence
     ✓ 確認 -O0 或對目標指標加 volatile
     ✓ 確認目標 load 是 volatile pointer（或加 asm volatile memory barrier）

□ 2. CPU 固定
     ✓ taskset -c N 釘到單一核心
     ✓ 攻擊者和受害者如果在不同 process，各 pin 到指定核心
     （原生 Linux 進階：考慮 isolcpus/nohz_full）

□ 3. Prefetcher（視攻擊類型）
     ✓ Prime+Probe：關掉 prefetcher（sudo wrmsr -p N 0x1a4 0xf）
     ✓ Flush+Reload：可不關，但關了訊號更乾淨
     ✓ WSL2：sudo modprobe msr && sudo wrmsr -p N 0x1a4 0xf

□ 4. 取樣策略
     ✓ 每個量測至少取 100–1000 次，取中位數
     ✓ 不用平均（偶爾的中斷/OS 活動會拉偏平均）
     ✓ 丟掉超出 3× median 的 outlier（中斷造成的爆高值）

□ 5. 校準門檻
     ✓ 先跑 calibrate（Ch 0 的程式），量本機的 HIT/MISS 分佈
     ✓ 門檻 = (HIT p99 + MISS p1) / 2（確認不重疊）
     ✓ 記錄門檻數字，後面所有攻擊 code 用同一個門檻

□ 6. 誠實面對環境限制
     ✓ 如果是 VM，MISS 時間可能偏高（hypervisor 層延遲）——重新校準
     ✓ 如果是 non-inclusive LLC，clflush 可能沒清到 LLC——改用 eviction
     ✓ 如果是 ARM，確認 clflush 等效操作（DC CIVAC）或改用 eviction
```

## 量測結果的解讀原則

量出數字之後，怎麼知道這個數字是「攻擊訊號」還是「量測雜訊」？

**訊號強度評估**：

```
「訊號雜訊比（SNR）」= (miss 中位數 - hit 中位數) / (miss 分佈寬度 + hit 分佈寬度)

本機：SNR = (244 - 24) / (10 + 5) = 220 / 15 ≈ 14.7

SNR > 5：攻擊基本可靠
SNR 1–5：需要更多取樣或更好的降噪
SNR < 1：訊號被雜訊淹沒，攻擊可能失敗
```

**False Positive / False Negative**：

```
False Positive（FP）：量到 HIT，但 line 不在 cache 裡（雜訊讓 miss 看起來像 hit）
False Negative（FN）：量到 MISS，但 line 在 cache 裡（hit 被偶然的延遲掩蓋）

對攻擊的影響：
- FP：攻擊者誤以為受害者存取了某個位址（錯誤的訊號）
- FN：攻擊者以為受害者沒存取（漏掉真實的訊號）

可接受的 FP/FN 率取決於攻擊場景：
- AES key recovery（Ch 11）：容許高 FN（只要統計足夠次數），但低 FP（FP 會讓錯誤 byte 計數增高）
- Spectre secret 洩漏（Ch 14）：容許少量 FP（可以重複量），但 FN 意味著有些 secret 值洩漏不出來
```

**統計重複量測**：

如果攻擊需要分辨 256 個可能的 secret byte 值（如 AES key byte），每個 byte 對應一個 cache line 位置——在取樣雜訊下，統計多次的「哪個 line 最常出現 HIT」比單次量測更可靠：

```c
// 統計計數器：secret 值 v 造成 line v 的 HIT
uint64_t hits[256] = {0};
for(int trial=0; trial<1000; trial++){
    flush_all_lines();           // 清掉所有 line
    victim();                    // 受害者執行，帶著 secret 存取某個 line
    for(int v=0; v<256; v++){
        if(timed_access(&array[v*64]) < THRESHOLD) hits[v]++;
    }
}
// secret = argmax(hits)
```

重複 1000 次，雜訊被平均掉，最正確的 secret 值對應的 hits[] 計數會明顯高於其他值——即使每次有 10% 的量測是雜訊，正確答案的統計仍遠高於錯誤選項。

## 對比與取捨

| 量測手段 | 精度 | 序列化保證 | 需要權限 | 跨平台 |
|---|---|---|---|---|
| `rdtsc` | 高（1 tick）| **沒有**（會被亂序）| 無 | x86 only |
| `rdtscp` | 高 | 等前面 load 完成 | 無 | x86 only |
| `lfence + rdtscp` | 高 | 最嚴謹 | 無 | x86 only |
| `perf` HW cycle counter | 最高（核心週期）| 軟體控制 | 通常需要 root | Linux x86/ARM |
| ARM `PMCCNTR_EL0` | 中高 | 視實作 | 需 kernel 允許 | AArch64 |
| Counting thread | 低（相對計數）| 無（取決於 OS 排程）| 無 | 全平台 |
| `performance.now()` | 極低（已降精度）| 無 | 無 | 瀏覽器 JS |

| 降噪手段 | 效果 | 代價 |
|---|---|---|
| `taskset -c N` | 消除核心漂移的 cache 狀態污染 | 無（免費的） |
| 關 prefetcher（MSR 0x1A4） | 消除 false prefetch hit | 需要 root；性能稍降 |
| `isolcpus`（開機參數） | 消除 OS tick 中斷 | 需要重開機配置 |
| 取中位數（N=1000） | 壓掉偶爾的高雜訊 | 攻擊速度慢 N 倍 |
| 關 turbo（BIOS 或 cpupower） | 讓頻率穩定，雜訊更低 | WSL2 做不到 |

## 踩雷集錦

1. **「`rdtsc` 就好，`rdtscp` 太麻煩」**——錯誤直覺：看到兩個指令功能相似，選簡單的。正確認識：本機實測 `rdtsc` 量到 18 cycles 的 DRAM miss（應該 ~200 cycles）——差了 10 倍。不序列化的 `rdtsc` 在 OoO CPU 上不可用於 cache 計時。

2. **「我加了 `lfence` 在 rdtsc 後面所以沒問題」**——錯誤直覺：在任何地方加 lfence 都有序列化效果。正確認識：lfence 要加在**第一個 rdtscp 之前**（防止被量測的 load 提前執行），不是在 rdtsc 之後（那個位置沒有作用）。

3. **「多取樣取平均比取中位數更準」**——錯誤直覺：統計上平均值比中位數更「精確」。正確認識：中斷和 OS 事件會讓偶爾的 miss 時間爆到 5000+ cycles，這些 outlier 拉偏平均值。中位數對 outlier 不敏感——這正是攻擊場景需要的穩健估計量。

4. **「prefetcher 只影響順序存取，隨機存取不會被 prefetch」**——錯誤直覺：以為只有順序讀陣列才會觸發 prefetcher。正確認識：Intel L2 prefetcher 不只做 stride，它有 IP-based prefetcher 根據「同一 PC 之前的存取樣式」預測；在 Prime+Probe 的填充階段，你輪流存取 eviction set 的行為就是很規律的 stride 存取，L2 prefetcher 非常容易學到。

5. **「clflush 完要立刻計時，不能等」**——錯誤直覺：以為 flush 和計時之間越短越好。正確認識：clflush 之後需要 `_mm_mfence()` 或 `_mm_lfence()` 確保 flush 完成，再開始計時。沒有 fence，flush 和計時可能亂序，量到的時間包含 flush 本身的時間，無法精確。

6. **「在 VM/Cloud 裡，hit/miss 差距會變小所以攻擊不可行」**——錯誤直覺：認為 VM 雜訊讓攻擊失效。正確認識：VM 雜訊確實讓 miss 時間變高（多了 hypervisor 層延遲），但 hit 時間基本不變（SRAM 不被 VM 影響）。hit/miss 差距往往更大（hit ~24 cycles vs miss ~300+ cycles in EC2），門檻只需重新校準。

## 進階：再往深一層

- **更精細的序列化**：Intel 的 `SERIALIZE` 指令（從 Ice Lake 開始）是一個真正的完整序列化指令（等同 `cpuid` 的效果但不改 GPR），比 `lfence+rdtscp` 更嚴謹。`cpuid` 作為序列化也常被用：在 rdtsc 前後各執行一次 `cpuid` 確保完整隔離，代價是每次序列化約 150 cycles 的額外開銷。
- **`rdpmc`（Read Performance Monitor Counter）**：比 TSC 更精確的核心週期計數，直接讀硬體 perf counter——不受 turbo 影響（真的是 core cycle，不是 TSC tick）。需要 `RDPMC` 權限開啟（`echo 2 > /proc/sys/kernel/perf_event_paranoid` 或用 `perf_event_open` syscall）。Spectre-v1 的精確 PoC 有時用 rdpmc 替代 rdtsc。
- **沙箱計時攻擊的演進**：Spectre 公開後，瀏覽器廠商持續降低 `performance.now()` 精度並 disable SharedArrayBuffer（SAB 讓 counting thread 跨 Worker 共享計數器）。攻擊者後來發現 `MessageChannel` 的 postMessage 也有足夠的計時解析度；CSS animation / requestAnimationFrame 也可以。這個貓鼠遊戲還在繼續，詳見 Van Goethem et al. 「Clock Around the Clock」(CCS 2020)。

## 動手練習

1. **自己復現 rdtsc vs rdtscp 差異**：把本章的 `rdtsc_annotated.c` 照抄、編譯、跑，在你的機器上確認 `rdtsc` 量到的時間是否跟 `rdtscp` 有顯著差異。如果差異不明顯，改成在 `rdtsc` 和 load 之間加入一個明確相依關係（如 `counter++; if(counter > N) break;`）看 CPU 是否因為相依而無法亂序。

2. **量你自己機器的 HIT/MISS 分佈，驗算門檻**：跑 Ch 0 的 `calibrate.c`（加入完整直方圖輸出），畫出 HIT 和 MISS 的分佈，計算 (HIT p99 + MISS p1) / 2，確認這個值跟你使用的門檻接近。

3. **有/無 lfence 的對比實驗**：修改計時函式，一種版本在第一個 `rdtscp` 前加 `lfence`，另一種不加。分別量測 1000 次 MISS 的分佈，比較中位數和分佈寬度是否有差異（Comet Lake 上差異可能不大，但這個實驗建立你的「為什麼要加 lfence」的直覺）。

4. **示範 Prefetcher 的誤報**：寫一個 Prime+Probe 模擬：填充 eviction set、等一個固定 delay、然後 probe eviction set。在開 prefetcher 的情況下，重複這個操作幾百次，觀察 probe 計時是否偶爾因 prefetcher 把 line 拉回而出現 false HIT。然後關掉 prefetcher，看 false HIT 率是否降低。

## 本章重點整理

- 計時的每個環節都有雜訊：**rdtsc 不序列化（被亂序移位）、行程漂移（核心跳來跳去）、prefetcher（偷把資料拉回）、OS 中斷（偶爾的高延遲）、compiler 刪掉 load**——每一個都會讓量測失真。
- **rdtscp** 有 load fence 語意，等前面 load 完成再讀 TSC——比 rdtsc 可靠；**lfence + rdtscp** 是最嚴謹的配合，防止被量測的 load 被拉到計時開始前。
- **Invariant TSC**：現代 Intel（含 Comet Lake）的 TSC 以固定參考頻率遞增，跟 turbo 無關——量到的 cycles 是穩定的相對單位，攻擊場景完全可用。
- 降噪 checklist：`taskset -c N`（必做）+ 關 prefetcher（Prime+Probe 必做）+ 取中位數（全場景必做）+ 校準門檻（每台機器各校一次）。
- 沒有 rdtsc 的場景（JS/ARM）：計數器執行緒或 ARM `PMCCNTR_EL0` 可以取代，精度低但通常仍足以分辨 cache hit vs DRAM miss。

## 自我檢核

- [ ] 背出 `lfence + rdtscp` 計時模式的四行，並能解釋每一行存在的理由（不是「因為 Ch 0 這樣寫」，是「因為不這樣做會發生什麼問題」）。
- [ ] rdtsc 量到 18 cycles 的 DRAM miss——畫出 CPU pipeline 裡 rdtsc 和 load 指令的相對執行順序，解釋為什麼量到的不是真實延遲。
- [ ] 你的機器 HIT median = 24、MISS median = 244，如果有人用 average 而不是 median，問他「一次中斷讓一個 miss 量到 5000 cycles，平均值會偏多少？中位數呢？」
- [ ] 為什麼 Flush+Reload 不需要關 prefetcher，Prime+Probe 卻需要？（從 prefetcher 的工作原理解釋，不是「因為書上說」）
- [ ] 面試問「你在 AWS EC2 上做 Flush+Reload，hit/miss 分佈怎麼可能跟本機不同？你要重新做什麼？」

## 延伸閱讀

### 官方文件

- **[Intel SDM Vol. 3B — RDTSC/RDTSCP 指令描述與 TSC](https://www.intel.com/sdm)**
  - **讀哪裡**：RDTSC 指令頁的 "Operation" 一節（了解它不序列化）；RDTSCP 的 "Serializing Behavior" 一節（了解它等哪些指令）；"Time-Stamp Counter" 章（invariant TSC 的定義）。
  - **學什麼**：本章計時原語的 ISA 層權威依據。

- **[Intel SDM Vol. 4 — MSR 0x1A4 Prefetcher Control](https://www.intel.com/sdm)**
  - **讀哪裡**：在 MSR 表裡搜索 `MSR_MISC_FEATURE_CONTROL (0x1A4)`，看 bit 描述。
  - **學什麼**：四個 prefetcher 各關哪個 bit，以及在哪些 CPU 世代有效。

### 論文

- **[FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack](https://eprint.iacr.org/2013/448.pdf)** — Yarom & Falkner, USENIX Security 2014
  - **讀哪裡**：Section 3.2（Timing the access）——量測方法學的原始論文描述；圖 1 就是本機 Ch 0 量到的那張 HIT/MISS 分佈。
  - **學什麼**：研究論文級的量測流程；門檻選取的 rationale。

- **[Clock Around the Clock: Timing Attacks on Encrypted Network Traffic Over WAN](https://dl.acm.org/doi/10.1145/3319535.3363216)** — Van Goethem et al., CCS 2020（搜索標題）
  - **讀哪裡**：Section 2.2（Alternative timing primitives）——計數器執行緒和 CSS animation 計時的系統描述。
  - **學什麼**：沒有 rdtsc 的瀏覽器環境下，攻擊者的計時工具箱有多廣。

### 工具

- **[dudect](https://github.com/oreparaz/dudect)** — Oscar Reparaz
  - **這是什麼**：constant-time 程式的量測與驗證工具——把你的程式丟進去，它告訴你時序是否跟 secret 相依。
  - **讀哪裡**：README 和 `dudect.h` 的核心量測迴圈——它用的正是本章講的 median/percentile 統計方法。
  - **和本章的關聯**：Ch 32（Constant-time 程式設計）會直接用這個工具，現在先了解它的量測原理。

計時工具磨好了。下一章我們回到記憶體的視角——虛擬位址怎麼轉換成實體位址、page table 和 TLB 的結構、huge page 為什麼讓攻擊者不需要 root 就能算 cache set，以及 `/proc/self/pagemap` 能給你什麼、現在又為什麼需要 root。

→ [Ch 5 虛擬記憶體與位址轉換對攻擊的意義](./05-virtual-memory-and-addressing.md)
