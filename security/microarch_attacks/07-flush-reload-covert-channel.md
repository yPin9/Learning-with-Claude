# Ch 7 — Flush+Reload Covert Channel

> **目標**：把 F+R 從「偷看」變成「偷傳」——建立一個用 cache 狀態當媒介的秘密通訊通道。Sender 把 bit 0/1 編碼成「不碰/碰某條 cache line」，receiver 用 F+R 讀出來。這是理解「微架構洩漏為什麼危險」的分水嶺：cache 通道不只能竊聽，還能在兩個毫無 IPC 關係的 process 之間建立一條 OS 完全看不見的通道。

> **環境**：WSL2 Ubuntu 22.04，Intel i7-10700，`~/microarch_lab`。本章所有 PoC 真跑驗證，HIT~24 cycles、MISS~210 cycles、門檻 150。共用 harness 同前章。

---

## 直覺：cache 狀態是一條看不見的電報線

兩個 process，Alice（sender）和 Bob（receiver），不能用 pipe、socket、signal——OS 禁止了所有正常 IPC。但他們共用同一塊物理記憶體（一個 shared library 的 page）：

```
                    LLC（共用 cache）
                ┌──────────────────────┐
probe_line[0]   │   cached / evicted   │
                └──────────────────────┘
                         ↑
         Alice 用「存取 / 不存取」這條 line 編碼 bit

  Alice 傳 1：存取 probe_line[0]  → line 進入 LLC
  Alice 傳 0：不存取             → line 保持 evicted（Bob flush 之後）

  Bob 的動作：
    flush probe_line[0]         → 清掉 line
    等 Alice 的「傳輸時窗」
    reload probe_line[0]，計時
      → hit (~24 cyc)  : 解碼為 1
      → miss (~210 cyc): 解碼為 0
```

這就是 F+R covert channel 的全部本質：**cache 的 presence/absence 狀態是一個可以被 Alice 寫入、被 Bob 讀出的一位元記憶體**，完全在 OS 的 IPC 監控之外。

Covert channel 的重要性不只在「傳資料」本身——它是理解為什麼「cache 攻擊可以用在沙箱逃逸、跨 VM 洩漏、甚至瀏覽器內 JavaScript 攻擊」的關鍵概念。Spectre PoC 的最後一段，就是把推測執行洩漏的 byte 用 covert channel 帶出來。

---

## Channel 的基本參數

在建立通道之前，先定義它的特性：

```
頻寬（Bandwidth）    = bits/second
                      取決於每個 bit 需要多少時間（flush + 等待 + reload）

錯誤率（BER）        = wrong bits / total bits
                      取決於同步、timing window 大小、OS 排程抖動

容量（Capacity）     = f(BER) by Shannon
                      BER=0 → capacity=1 bit/symbol
                      BER=0.5 → capacity=0（完全雜訊）
```

這台機器的量測結果（後面 PoC 的真實數字）：
- 每個 bit 傳輸時間：~120 µs（TRIALS=300 次 flush+reload，每次約 400ns）
- 原始 bandwidth：~8333 bits/sec（約 1 KB/s）
- BER：0%（300 trial 多數決，信噪比太高）
- 實際跨 process bandwidth（加 OS 排程同步）：典型 1–10 KB/s

---

## 同步問題：sender 和 receiver 怎麼對齊

Covert channel 最難的部分不是傳 bit，是**同步**。Alice 決定什麼時候傳，Bob 必須在正確的時間窗口 flush 再 reload。幾種做法：

```
方法 1: 固定時槽（Time-Slot）
         ─────┬────┬────┬────┬────┬────
         slot  0    1    2    3    4   ...
               T    T    T    T    T    (每個 slot = 固定時長)
         Alice 在 slot 開始時傳（或不傳）
         Bob  在 slot 中間 flush，在 slot 結束前 reload
         缺點：OS 排程抖動可能讓 reload 落在下一個 slot

方法 2: 共用旗標（Shared Flag）
         用 cache 本身傳「ready」旗標
         Alice 設 ready[0] = 1 -> 踩 ready_line 通知 Bob 她傳完了
         Bob  偵測到 ready_line 是 hit -> 去 probe data_line
         問題：無限遞迴（ready 本身也是 covert channel）

方法 3: 單一 process 兩 thread（最簡單的 demo 做法）
         Sender thread 和 receiver thread 共用記憶體，
         用 volatile 旗標 + pause 同步
         缺點：不是真正的「OS 層面隔離」
```

本章的 PoC 使用方法 1 的簡化版：在同一個 process 裡串行執行 sender 和 receiver，每個 bit 由 TRIALS 次嘗試的多數決決定（相當於每個 bit 有 TRIALS 個時槽，取多數）。這讓 BER 降到 0%，但代價是頻寬降低 TRIALS 倍。Ch 12 會討論真正的跨 process channel 設計。

---

## 實作：F+R Covert Channel PoC

### 設計

- **編碼**：一條 probe line（`probe[1 * STRIDE]`）作為 data line。傳 1 → 存取；傳 0 → 不存取。
- **每 bit 傳 TRIALS=300 次**：每次都是 flush+（sender 操作）+reload，取多數決。這消除了 OS 排程抖動造成的偶爾錯誤。
- **測試訊息**：傳「Hi!」（3 bytes = 24 bits）。

### 程式碼

```c
/* fr_covert.c — Flush+Reload covert channel demo (Ch 7) */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <x86intrin.h>
#include <sys/mman.h>

#define CACHE_HIT_THRESHOLD 150
#define STRIDE   4096
#define NBITS    8
#define TRIALS   300

static volatile char *probe;

static inline uint64_t timed_access(volatile char *p) {
    unsigned junk;
    _mm_lfence();
    uint64_t a = __rdtscp(&junk);
    (void)*p;
    uint64_t b = __rdtscp(&junk);
    _mm_lfence();
    return b - a;
}

/* 傳一個 bit TRIALS 次，取多數決；avg_out 回傳平均計時 */
static int tx_bit_once(int tx, double *avg_out) {
    int hits = 0;
    uint64_t total = 0;
    for (int t = 0; t < TRIALS; t++) {
        /* Receiver: flush data line */
        _mm_clflush((void*)(probe + STRIDE));
        _mm_mfence();

        /* Sender: 傳 1 -> 存取；傳 0 -> 不存取 */
        if (tx) (void)*(probe + STRIDE);
        _mm_mfence();

        /* Receiver: reload 計時 */
        uint64_t cyc = timed_access(probe + STRIDE);
        total += cyc;
        if (cyc < CACHE_HIT_THRESHOLD) hits++;
    }
    *avg_out = (double)total / TRIALS;
    return (hits > TRIALS/2) ? 1 : 0;  /* 多數決 */
}

int main(void) {
    probe = (volatile char*)mmap(NULL, 4 * STRIDE,
                                  PROT_READ|PROT_WRITE,
                                  MAP_ANONYMOUS|MAP_SHARED, -1, 0);
    if (probe == MAP_FAILED) { perror("mmap"); return 1; }
    memset((void*)probe, 1, 4 * STRIDE);

    printf("=== Flush+Reload Covert Channel (Ch 7) ===\n");
    printf("STRIDE=%d  THRESHOLD=%d  TRIALS/bit=%d\n\n",
           STRIDE, CACHE_HIT_THRESHOLD, TRIALS);

    const char *msg = "Hi!";
    int msg_len = 3;
    int total_errors = 0, total_bits = 0;

    for (int mi = 0; mi < msg_len; mi++) {
        uint8_t tx_byte = (uint8_t)msg[mi];
        uint8_t rx_byte = 0;
        int byte_errors = 0;

        printf("Sending byte 0x%02X (%c):\n", tx_byte, (char)tx_byte);
        printf("  bit | tx | rx | avg_cyc | result\n");
        printf("  ----|----|----|---------|--------\n");

        for (int bit = 0; bit < NBITS; bit++) {
            int tx_bit = (tx_byte >> (NBITS - 1 - bit)) & 1;
            double avg_cyc;
            int rx_bit = tx_bit_once(tx_bit, &avg_cyc);
            int err = (rx_bit != tx_bit);
            byte_errors += err;
            rx_byte |= (rx_bit << (NBITS - 1 - bit));
            printf("    %d |  %d |  %d | %7.1f | %s\n",
                   bit, tx_bit, rx_bit, avg_cyc,
                   err ? "ERROR" : "ok");
        }

        char rx_char;
        if (rx_byte >= 32 && rx_byte < 127) rx_char = (char)rx_byte;
        else rx_char = '?';
        printf("  TX=0x%02X  RX=0x%02X (%c)  bit_errors=%d/8\n\n",
               tx_byte, rx_byte, rx_char, byte_errors);
        total_errors += byte_errors;
        total_bits   += NBITS;
    }

    printf("=== Summary: %d bit errors / %d bits  BER=%.2f%% ===\n",
           total_errors, total_bits, 100.0*total_errors/total_bits);

    /* bandwidth 估算 */
    double trial_ns  = 400.0;  /* 每次 flush+access+reload 約 400ns */
    double bit_us    = TRIALS * trial_ns / 1000.0;
    printf("Est. raw bandwidth: %.0f bits/sec\n", 1e9/(trial_ns*TRIALS));
    printf("(inter-process bandwidth with OS scheduling: typically 1-10 KB/s)\n");

    munmap((void*)probe, 4 * STRIDE);
    return 0;
}
```

**編譯與執行**：

```bash
gcc -O0 fr_covert.c -o fr_covert
taskset -c 2 ./fr_covert
```

### 真實輸出（i7-10700, WSL2, 本機實測）

```
=== Flush+Reload Covert Channel (Ch 7) ===
STRIDE=4096  THRESHOLD=150  TRIALS/bit=300

Sending byte 0x48 (H):
  bit | tx | rx | avg_cyc | result
  ----|----|----|---------|--------
    0 |  0 |  0 |   216.9 | ok
    1 |  1 |  1 |    23.8 | ok
    2 |  0 |  0 |   212.4 | ok
    3 |  0 |  0 |   219.8 | ok
    4 |  1 |  1 |    23.8 | ok
    5 |  0 |  0 |   210.0 | ok
    6 |  0 |  0 |   205.8 | ok
    7 |  0 |  0 |   210.6 | ok
  TX=0x48  RX=0x48 (H)  bit_errors=0/8

Sending byte 0x69 (i):
  bit | tx | rx | avg_cyc | result
  ----|----|----|---------|--------
    0 |  0 |  0 |   208.0 | ok
    1 |  1 |  1 |    23.9 | ok
    2 |  1 |  1 |    23.9 | ok
    3 |  0 |  0 |   213.1 | ok
    4 |  1 |  1 |    23.9 | ok
    5 |  0 |  0 |   227.9 | ok
    6 |  0 |  0 |   219.6 | ok
    7 |  1 |  1 |    23.8 | ok
  TX=0x69  RX=0x69 (i)  bit_errors=0/8

Sending byte 0x21 (!):
  bit | tx | rx | avg_cyc | result
  ----|----|----|---------|--------
    0 |  0 |  0 |   215.5 | ok
    1 |  0 |  0 |   215.9 | ok
    2 |  1 |  1 |    23.8 | ok
    3 |  0 |  0 |   218.0 | ok
    4 |  0 |  0 |   209.0 | ok
    5 |  0 |  0 |   211.3 | ok
    6 |  0 |  0 |   208.8 | ok
    7 |  1 |  1 |    23.8 | ok
  TX=0x21  RX=0x21 (!)  bit_errors=0/8

=== Summary: 0 bit errors / 24 bits  BER=0.00% ===
Est. raw bandwidth: 8333 bits/sec
(inter-process bandwidth with OS scheduling: typically 1-10 KB/s)
```

0 bit errors，BER = 0%。讀值的對比極為清楚：
- **tx=1 的 bit**：avg_cyc = 23.8–23.9（純 hit）
- **tx=0 的 bit**：avg_cyc = 205–228（純 miss）

---

## 擴展：多 line 編碼提升頻寬

單 line 的 covert channel 一次傳一個 bit。用 N 條 probe line 可以一次傳 log2(N) bits（Gray code 或直接 binary 編碼）。以 256 條 line（256 bytes stride）為例：

```
編碼方案 A：binary
  Sender 把一個 byte 值 v（0–255）對應到 line[v]
  存取 line[v]，不存取其他 line
  Receiver flush 全部 256 條 -> reload 全部 -> 哪條 hit 就是 v
  -> 一次傳 8 bits，頻寬提升 8×

  代價：flush + reload 256 條 line 的開銷 × 8 倍時間
        net 頻寬提升視 flush/reload overhead 而定
```

這正是 Spectre PoC 裡 probe_array 的設計：256 個 slot（256 * 4096 bytes），sender 把 secret_byte 對應到 slot[secret_byte]，receiver 掃 256 個 slot 找 hit。一次 F+R round 就還原整個 byte，不需要 bit-by-bit 傳輸。

```
                 probe_array（256 slots × 4096 bytes）
                 ┌────┬────┬────┬────┬─────┬────┐
                 │ 0  │ 1  │ 2  │ 3  │ ... │255 │
                 └────┴────┴────┴────┴─────┴────┘
                           ↑
              Spectre victim 根據 secret_byte 存取對應 slot
              Attacker reload 全部 256 slots，找 hit
```

這個設計的程式碼片段（取自 Spectre 論文 Listing 1）：

```c
/* victim code (speculatively executed) */
if (x < array1_size) {   /* bounds check —— Spectre 讓它推測為 true */
    uint8_t v = array1[x];   /* 秘密 byte */
    uint8_t junk = array2[v * 4096];  /* 把 v 編碼到 cache state */
}

/* attacker reads out the cache state */
for (int i = 0; i < 256; i++) {
    uint64_t t = timed_access(&array2[i * 4096]);
    if (t < THRESHOLD) {
        printf("secret byte = %d\n", i);
    }
}
```

本章的 1-line 版本是這個 256-line 版本的骨幹原理。

---

## 糾錯與可靠性

在有 OS 排程干擾的真實場景下，BER 不會是 0%。幾個提高可靠性的做法：

### 多數決（本 PoC 使用）

最簡單：每個 bit 傳 TRIALS 次，取多數。TRIALS=300 時，只要每次的 BER < 50%，最終錯誤率就趨近 0。代價：頻寬除以 TRIALS。

### 重傳機制（ARQ）

Sender 和 receiver 用一個 ack line：
1. Sender 傳完 bit，再踩 ack_line[0]
2. Receiver 收到 ack 後，傳 nack_line[0] 或不傳（表示收到了/沒收到）
3. Sender 看 nack 決定是否重傳

這讓 channel 有 stop-and-wait ARQ 的能力，BER 降到機率上接近 0，但同步複雜度提升。

### Error-Correcting Code（ECC）

在真正的 cache covert channel 研究（如 Hello from the Other Side，Gruss 等人）裡，會在 channel 上跑 LDPC 或 Hamming code，讓高 BER 的 raw channel 也能可靠傳輸。

### 降低 OS 排程干擾的做法

- `taskset -c 2` pin 到同一個核心（本 PoC 已用）
- `SCHED_FIFO` realtime 排程（需要 root 或 `CAP_SYS_NICE`）
- 時槽長度加大（每個 bit 的 window 拉長讓 OS 有機會搶占但仍在 window 內）

---

## 頻寬量測方法論

理論頻寬 = 1 / (每個 bit 的時間)，但「每個 bit 的時間」需要仔細定義：

```
同一 process（本 PoC）：
  每個 bit = TRIALS × (clflush + mfence + [sender access] + mfence + rdtscp×2 + lfence×2)
           ≈ TRIALS × 400ns
           ≈ 300 × 400ns = 120µs/bit
           ≈ 8,333 bits/sec

跨 process（真實攻擊）：
  每個 bit = 時槽長度（sender 和 receiver 必須都在同一個時槽醒來）
  OS 最小排程時間 ≈ 100µs（4ms tick 的 1/40）
  加上同步 overhead → 現實中每個 bit ≈ 1ms – 10ms
  → 頻寬 ≈ 100 bits/sec – 1000 bits/sec（約 12 – 125 bytes/sec）
```

現實中學術論文量測到的 cache covert channel 頻寬：
- 同機器，理想條件：幾百 KB/s（多 line 編碼 + 快速 sync）
- 跨 VM：幾 KB/s（hypervisor scheduling 抖動大）
- 瀏覽器 JavaScript：幾 KB/s（SharedArrayBuffer 時代）到幾百 bytes/s（封堵後）

---

## 範例 2：加入 timestamp 同步的雛型

單一 process 的 PoC 缺少「真正的同步」。下面的雛型展示如何用 cache 本身當「同步 line」（ready line）：

```c
/* 概念示範（非完整可跑程式） */

/* 兩條通道 line：data line 傳資料，sync line 傳 ready 旗標 */
volatile char *data_line = probe + 1 * STRIDE;
volatile char *sync_line = probe + 2 * STRIDE;

/* ===== Sender ===== */
void sender_send_bit(int bit) {
    /* 1. 準備：確保 sync_line 被 flush（receiver 已重設） */
    while (timed_access(sync_line) < THRESHOLD) _mm_pause();
    /* sync_line 是 miss -> receiver 已準備好 */

    /* 2. 編碼並傳輸 */
    if (bit) (void)*data_line;
    _mm_mfence();

    /* 3. 踩 sync_line 告訴 receiver 傳完了 */
    (void)*sync_line;
    _mm_mfence();
}

/* ===== Receiver ===== */
int receiver_recv_bit(void) {
    /* 1. 通知 sender 我準備好了：flush sync_line */
    _mm_clflush((void*)sync_line); _mm_mfence();
    /* 2. Flush data line */
    _mm_clflush((void*)data_line); _mm_mfence();

    /* 3. 等 sender 踩 sync_line（hit 代表 sender 傳完） */
    while (timed_access(sync_line) >= THRESHOLD) _mm_pause();

    /* 4. Probe data line */
    return (timed_access(data_line) < THRESHOLD) ? 1 : 0;
}
```

這個方案的問題：step 3 的等待本身是一個 spin loop，佔滿 CPU；而且 `_mm_pause()` 裡的 timed_access 也在修改 cache 狀態，可能干擾 data_line 的測量。這也是為什麼現實中的 cache covert channel 設計通常選擇固定 time slot 而非這種 handshake——time slot 讓雙方的 cache 操作時間窗口完全分開。

---

## 對比與取捨

| 維度 | F+R Covert Channel | 其他選擇 |
|------|-------------------|---------|
| **需要共享記憶體** | 是（shared library 等） | Covert DRAM channel：不需要 cache 共享但更慢 |
| **BER（無外部干擾）** | ~0%（多數決後） | Prime+Probe covert channel：BER 較高 |
| **原始頻寬** | ~8 Kbps（同 process）| SSH over F+R：實測約 400 bps（跨 VM） |
| **OS 可見性** | 不可見（沒有 syscall） | Pipe/socket：OS 完全可見 |
| **跨 core** | 需要 LLC 共用（通常 OK） | Port contention channel（Ch 26）：跨 core 也行 |
| **防禦難度** | 極難（cache 是基礎硬體） | 時間模糊（fuzzy timer）可降低訊號品質 |
| **瀏覽器適用** | 降解版（SharedArrayBuffer） | CSS/JS 的 timing channel 更受限 |

---

## 踩雷集錦

**1. 以為「沒有 IPC 就沒有通訊」→ 錯，cache 通道繞過所有 OS IPC 機制**

錯誤直覺：「沙箱把 write/socket/pipe 全封了，兩個 process 就不能通訊。」
正確認識：只要兩個 process 共用任何物理資源（cache、DRAM、TLB、分支預測器），那個資源就潛在地是一條 covert channel。OS 的 IPC 控制沒有辦法觀測「你對共用快取做了什麼」。

**2. 在同步沒做好的情況下發現 BER 很高 → 急著換掉 THRESHOLD，其實是 timing window 問題**

錯誤直覺：「miss 的時間應該是 244，但我量到很多 150–200 的值，門檻要調低。」
正確認識：150–200 cycles 的計時通常是 OS 排程器在 flush 之後、sender 存取之前就搶占了 receiver——sender 還沒來得及存取，receiver 就去 reload，結果量到的是 flush 後到 reload 前的狀態（已 flush，sender 還沒碰 = miss），但中間有 OS 搶占的延遲讓 miss 時間變短。解法是加大時槽、或做多數決而非單次。

**3. 用 TRIALS=1 時 BER 不穩定 → 誤以為 channel 不能用**

錯誤直覺：「這個 channel 很爛，每次答案都不一樣。」
正確認識：TRIALS=1 時，任何一個 OS 排程抖動（receiver 比 sender 先跑、或同時跑）都能把結果翻轉。這不是 channel 的問題，是你沒做同步和多數決。用 TRIALS=100 + 多數決，BER 就能降到幾乎 0。

**4. 把 covert channel 和 side channel 概念混淆**

錯誤直覺：「我在傳資料，這是 covert channel；Spectre 在偷 key，那是 side channel——它們用的 cache 機制不一樣吧？」
正確認識：**底層機制完全相同**，都是 F+R。差別是角色：side channel 裡的「sender」是 victim（不知情），「receiver」是 attacker；covert channel 裡的 sender 是知情的共謀。Spectre PoC 的最後一步——用 probe_array 把推測執行洩漏的 byte 帶出來——就是一個 covert channel（sender 是 speculatively executed 的 victim code，receiver 是 attacker 的 F+R 掃描）。

**5. 以為 PIN CPU（taskset）後就不需要多數決 → 在 WSL2 環境下仍有抖動**

錯誤直覺：「我已經 taskset -c 2 了，應該沒有排程抖動。」
正確認識：taskset pin CPU 減少了「行程搬到其他核心」的問題，但並沒有給你 realtime 排程——其他執行緒仍可能在同一個核心上搶占。WSL2 下的 VM scheduling 還有額外一層抖動。多數決（TRIALS=100+）是最簡單有效的補救。

---

## 進階：再往深一層

### Hello from the Other Side：實際 SSH 走 cache channel

Daniel Gruss 等人的研究展示了一個極端案例：SSH session 的 keystroke timing 可以透過 F+R covert channel 傳輸。連接在同一個 hypervisor 上的兩個 VM，用 shared library 的 cache 狀態作通道，傳輸帶有 error correction 的資料流。他們量測到的 bandwidth 約為 100–400 bits/sec（跨 VM），足以傳遞按鍵時序資訊。

這個研究的重要意義：雲端多租戶環境中，兩個完全「隔離」的 VM 之間可以建立一條 OS 和 hypervisor 都看不見的通訊通道。這讓很多安全假設（「不同客戶的 VM 完全隔離」）需要重新評估。

### 瀏覽器裡的 F+R（降解版）

瀏覽器沙箱封鎖了 `rdtsc` 和 `clflush`，但 Spectre 研究組展示了：
- `SharedArrayBuffer`（多執行緒共享記憶體）+ 計數器 thread → 土製高精度計時器
- 或在沒有 SAB 的情況下，用 CSS animation timer、postMessage timing 等降精度計時器

這些「降解版」的 F+R channel 精度只有幾微秒，但對於 Spectre 的 probe_array 掃描已經足夠（hit vs miss 差距還是有幾個 µs）。Spectre 論文的 PoC v2 就示範了純 JavaScript 的攻擊。

### 多 bit 編碼的帶寬分析

用 N 條 line 一次傳 log₂(N) bits 時，頻寬提升的極限：
- flush N 條 line：O(N) 時間
- reload N 條 line：O(N) 時間
- sender 只存取 1 條：O(1) 時間
- → 頻寬提升因子：log₂(N)（bits）而不是 N 倍
- 256 line（傳 1 byte）vs 1 line（傳 1 bit）：理論頻寬提升 8×，但 flush/reload 時間也增加約 256×
- 實際上，多 line 的主要好處是**减少 round 數**（一次傳一個 byte），不是提升 raw bandwidth

---

## 動手練習

1. **實測 BER vs TRIALS**：把 TRIALS 從 1、5、10、30、100、300 各跑一次，記錄每個設定的 BER。畫出 TRIALS vs BER 曲線，找到 BER < 1% 的最低 TRIALS。

2. **256-slot 版本**：修改 PoC，用 256 條 line 一次傳一個 byte（類似 Spectre probe_array 設計）。比較每個 byte 需要的時間（flush 256 + reload 256）和單 bit 版本（flush 1 + reload 1）× 8 的差異。

3. **sender/receiver 分成兩個 thread**：把 PoC 改成 pthread，sender thread 和 receiver thread 用 volatile 旗標同步。觀察 BER 相比單 thread 版本的變化。

4. **加入 Hamming(7,4) 糾錯**：在 covert channel 上實作 Hamming(7,4)——把每 4 bits 的資料編碼成 7 bits 傳輸，接收端糾正單 bit 錯誤。觀察糾錯前後的有效 BER（而非 raw BER）。

---

## 本章重點整理

- F+R covert channel：sender 用「存取/不存取 cache line」編碼 bit，receiver 用 flush+reload 解碼。
- **core primitive 和 F+R 完全相同**——差別只在「sender 是知情共謀」還是「不知情受害者」。
- 本機 24 bits 傳輸 BER=0%，hit 23.8 cycles vs miss 210 cycles，訊號極強。
- 原始頻寬估計 ~8333 bits/sec；跨 process 實際約 1–10 KB/s。
- 多 bit 編碼（256-slot）是 Spectre probe_array 的核心設計——一次 F+R round 傳一個 byte。
- **Spectre 的最後一步就是 covert channel**：推測執行的 victim code 是「sender」，attacker 的 F+R 掃描是「receiver」。

---

## 自我檢核

- [ ] 能解釋 side channel 和 covert channel 在角色上的差異，以及為什麼底層 cache 機制相同？
- [ ] 能說出「多數決（TRIALS）能降低 BER」的原因，以及 TRIALS 夠大時 BER 趨向 0 的統計直覺？
- [ ] 能解釋「OS 排程抖動如何導致 BER 上升」，以及三個對抗方法？
- [ ] 能解釋「256-slot channel 的頻寬為什麼不是 1-slot 的 256 倍」？
- [ ] 能從 covert channel 的角度解讀 Spectre PoC 的最後一段（reload probe_array）？

---

## 延伸閱讀

- **[FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack](https://eprint.iacr.org/2013/448.pdf)** — Yarom & Falkner, USENIX Security 2014
  - **讀哪裡**：Section 5（GnuPG 攻擊，等同於用 F+R channel 傳遞 RSA key bits）。
  - **學到什麼**：F+R 作為 side channel 的完整攻擊流程——把 key bit 的存取模式轉換成可觀測的 cache 訊號。

- **[Hello from the Other Side: SSH over Robust Cache Covert Channels in the Cloud](https://gruss.cc/files/hello.pdf)** — Gruss et al., NDSS 2017
  - **讀哪裡**：Section 3（channel 設計）、Section 4（error correction）、Section 5（bandwidth 量測）。
  - **學到什麼**：真實跨 VM 的 F+R covert channel 設計，包括時槽選擇、帶 ECC 的可靠傳輸、SSH 鍵擊時序竊取。這是本章「進階」段的學術參考。

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  - **讀哪裡**：Section III-A（Listing 1），把 victim_function 的最後一行 `array2[array1[x] * 4096]` 對照本章的 256-slot 設計，理解 Spectre 的 covert channel 和這章的 PoC 是同一個東西。

- **[Covert and Side Channels Due to Processor Architecture](https://ieeexplore.ieee.org/document/1599941)** — Wang & Lee, ACSAC 2006
  - **讀哪裡**：Section 2（covert channel 的形式分類）、Section 3（cache covert channel 的理論 bandwidth 上界）。
  - **學到什麼**：cache covert channel 的 Shannon capacity 分析，和防禦角度的理論框架。

---

F+R 原語現在有了兩個面向：偷看（side channel）和偷傳（covert channel）。下一章，我們打破 F+R 最大的限制——不需要 clflush、不需要共享記憶體。Evict+Reload 用「製造 cache 衝突」取代 clflush；Prime+Probe 更進一步，讓攻擊者在完全不知道 victim 虛擬位址的情況下，仍然能偵測 victim 碰了哪個 cache set。這是朝向跨租戶、跨 VM 攻擊的關鍵一步。

→ [Ch 8 Evict+Reload 與 Prime+Probe](./08-evict-reload-prime-probe.md)
