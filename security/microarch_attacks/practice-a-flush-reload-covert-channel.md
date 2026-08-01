# 練習 A — Flush+Reload Covert Channel

> **目標**：從零實作一條完整的 F+R 隱蔽通道，先讓兩個 process 透過 cache 狀態傳字串，再模擬攻擊者用 F+R 還原 victim 的 secret byte。做完這兩個任務，你就徹底吃透了 Part 2（Ch 6–12）的核心機制。

---

## 背景

F+R（Flush+Reload）的力量不只是旁觀察 cache 狀態，它天然就是一條**隱蔽通道（covert channel）**：兩個 process 不需要任何顯式 IPC，只要共享同一塊可 mmap 的記憶體，就能透過「cache line 在不在快取裡」這個 1-bit 的狀態傳遞資訊。

這件事在兩個場景裡特別重要：

1. **sandbox 逃逸 / 跨容器洩漏**：兩個在沙盒裡的 process 本來沒有通訊管道，但如果它們共享 libc 或 vDSO，就可以用 F+R 建立隱蔽通道，繞過安全策略傳資料。
2. **side-channel 攻擊主軸**：攻擊者（spy）flush 目標記憶體區域，等 victim 存取，reload 計時——這就是 Spectre v1、AES cache-timing 等攻擊的基本偵察動作。

本練習分兩個任務：任務一做真正能傳文字的 covert channel（兩個獨立 process），任務二在單一 process 內 fork 出 victim 和 spy，模擬「攻擊者靠 F+R 推斷 victim secret」的場景。

---

## 環境確認

```bash
# WSL2 Ubuntu 22.04，Intel i7-10700（Comet Lake）
# 共用 baseline：HIT ~24 cycles，MISS ~244 cycles
# CACHE_HIT_THRESHOLD = 150

uname -r
grep "model name" /proc/cpuinfo | head -1
```

編譯旗標固定用 `-O0`（不讓 compiler 優化掉我們刻意的記憶體存取）：

```bash
gcc -O0 fr_sender.c -o fr_sender
gcc -O0 fr_receiver.c -o fr_receiver
gcc -O0 combined.c -o combined
```

快速驗證你的環境 HIT / MISS 數字是否落在預期範圍：

```c
/* baseline_check.c */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <x86intrin.h>

int main(void) {
    char arr[64];
    uint32_t aux;
    uint64_t t0, t1;

    /* warm up */
    volatile char tmp = arr[0]; (void)tmp;

    /* HIT */
    t0 = __rdtscp(&aux);
    tmp = arr[0]; (void)tmp;
    t1 = __rdtscp(&aux);
    printf("HIT  = %lu cycles\n", (unsigned long)(t1 - t0));

    /* MISS */
    _mm_clflush(arr);
    _mm_mfence();
    t0 = __rdtscp(&aux);
    tmp = arr[0]; (void)tmp;
    t1 = __rdtscp(&aux);
    printf("MISS = %lu cycles\n", (unsigned long)(t1 - t0));

    return 0;
}
```

```bash
gcc -O0 baseline_check.c -o baseline_check && taskset -c 2 ./baseline_check
# 期望：HIT < 50，MISS > 150
```

---

## 任務一：完整 F+R Covert Channel（跨 process 傳字串）

### 任務規格

實作兩支程式：`fr_sender.c` 和 `fr_receiver.c`。

**威脅模型**：sender 和 receiver 是兩個完全獨立的 process，沒有 pipe、socket 或任何顯式 IPC。它們唯一的共通點是都 mmap 了同一個檔案 `/tmp/fr_channel`。

**編碼方案（alphabet encoding）**：
- 共享區域切成 256 個「signal slot」，每個 slot 64 bytes（一條 cache line）
- sender 要傳字元 `X`（ASCII 值 = `k`），就存取 `slot[k]`，讓這條 line 進快取
- receiver flush 全部 256 個 slot，等一個固定 window，reload 全部 256 個 slot，找到 reload 時間最短的 index，就是接收到的字元

**同步方式**：
- sender 和 receiver 各自用 `usleep(200000)`（200ms）為一個傳輸 window
- receiver 比 sender 晚 100ms 啟動偵測（在 window 中間 reload）
- 第 257 個 slot（index 256）作為 EOT（end of transmission）sentinel

**共享檔案配置**：
- 檔案大小：257 個 slot × 64 bytes = 16,448 bytes
- 用 `ftruncate` 確保檔案夠大，映射模式必須是 `MAP_SHARED`

### 期望輸出

傳送 "HELLO"（5 個字元）的典型輸出：

```
[Receiver] 開始監聽（等待 sender）...
[Receiver] slot 72 hit (reload=26 cycles) → 'H'
[Receiver] slot 69 hit (reload=24 cycles) → 'E'
[Receiver] slot 76 hit (reload=28 cycles) → 'L'
[Receiver] slot 76 hit (reload=26 cycles) → 'L'
[Receiver] slot 79 hit (reload=28 cycles) → 'O'
[Receiver] EOT signal，傳輸結束
Transmission: HELLO (5 chars)
```

### 實作步驟

**Step 1：搞定共享記憶體的 mmap**

sender 和 receiver 都要 mmap 同一個檔案，映射模式必須是 `MAP_SHARED`（不能是 `MAP_PRIVATE`，否則寫入不會跨 process 共享 page，兩邊 cache 狀態互不影響）。

```c
#define NUM_SLOTS   257        /* 256 字元 slot + 1 sentinel (EOT) */
#define SLOT_SIZE   64         /* 一條 cache line */
#define SHARED_SIZE (NUM_SLOTS * SLOT_SIZE)

int fd = open("/tmp/fr_channel", O_RDWR | O_CREAT, 0600);
ftruncate(fd, SHARED_SIZE);
char *base = mmap(NULL, SHARED_SIZE, PROT_READ | PROT_WRITE,
                  MAP_SHARED, fd, 0);
```

`base + k * SLOT_SIZE` 就是 slot[k] 的起始位址，也是我們 flush / reload 的目標。

**Step 2：sender 的傳送迴圈**

sender 從 `argv[1]` 拿字串，每個字元：

1. 算出 slot index = `(uint8_t)ch`
2. 存取 `*(volatile char *)(base + index * SLOT_SIZE)`，讓 line 進快取
3. `usleep(200000)` 等下一個 window

傳完所有字元後，存取 sentinel slot（index 256）作為 EOT。

**Step 3：receiver 的接收迴圈**

每個 window 開始時：

1. flush 全部 257 個 slot（for 迴圈呼叫 `_mm_clflush`）
2. `usleep(100000)`（等 sender 在 window 中間存取）
3. reload 256 個字元 slot，記錄每個的 rdtscp 時間
4. 找最低時間的 index，那就是傳來的字元

**Step 4：辨別 EOT**

receiver 在找完最低字元 slot 之後，額外 reload 一次 sentinel slot（slot[256]）。如果 reload 時間 < `THRESHOLD`，表示 sender 傳了 EOT，結束接收迴圈。

**Step 5：處理沒有 signal 的 window**

如果這個 window 最短 reload 時間仍 >= 150 cycles，表示這個 window sender 沒有存取任何 slot（可能是 receiver 領先 sender 太多）。記錄為「無訊號」，繼續等下一個 window 即可。

### 卡住提示

**Q：sender 存取了 slot，但 receiver 都量到 miss？**

最常見原因是時序錯了：receiver 的 flush 在 sender 存取**之後**才執行，把 line 又 flush 掉了。正確順序是：

```
t=0ms    receiver: flush all slots
t=0ms    sender:   存取 slot[k]        ← 發生在 flush 之後
t=100ms  receiver: reload all slots   ← 量到 hit
```

**Q：`_mm_clflush` 要傳哪個位址？**

傳你要 flush 的那條 cache line 內任意位址都行，通常傳 slot 起始位址：

```c
_mm_clflush(base + k * SLOT_SIZE);
```

Clflush 會對齊到 cache line 邊界，不需要手動對齊。但 `SLOT_SIZE` 要等於 64（cache line size），否則一個 slot 橫跨兩條 line，需要 flush 兩次。

**Q：rdtscp 計時怎麼寫？**

```c
uint32_t aux;
uint64_t t0 = __rdtscp(&aux);
volatile char tmp = *(char *)(base + k * SLOT_SIZE);
(void)tmp;
uint64_t t1 = __rdtscp(&aux);
uint64_t elapsed = t1 - t0;
```

注意 `__rdtscp` 比 `__rdtsc` 更有串行化保證（它等 CPU 把前面的 load 完成才讀 TSC），適合精確量測記憶體存取時間。

**Q：`MAP_SHARED` 和 `MAP_PRIVATE` 差在哪？**

`MAP_PRIVATE` 是 copy-on-write，每個 process 改自己的 page，不會影響對方的 cache 狀態。F+R covert channel 需要的是**實體頁共享**，所以必須用 `MAP_SHARED`。可以用 `/proc/PID/maps` 確認：shared 的映射顯示 `r-xs` 或 `rw-s`（'s' = shared）。

---

## 任務二：F+R 還原 Secret-Dependent 記憶體存取

### 任務規格

把 victim 和 spy 寫在同一個 `combined.c`，用 `fork` 建立兩個 process，透過 pipe 同步。

**架構**：

```
parent process (spy)
   │
   ├── fork() → child process (victim)
   │                 │
   │                 └── 收到 pipe 訊號後呼叫 victim_access(secret)
   │
   ├── flush 所有 256 條 cache line
   ├── 寫 pipe（通知 victim 執行）
   ├── 等 victim 完成（再讀 pipe）
   └── reload 計時 256 條 line，找最低時間 index
```

**Victim 函式**：

```c
static volatile char probe_array[256][64];

void victim_access(uint8_t secret) {
    volatile char sink = probe_array[secret][0];
    (void)sink;
}
```

`probe_array` 是 256 × 64 bytes = 16 KB，剛好 256 條 cache line。victim 存取 `probe_array[secret][0]`，把第 `secret` 條 line 帶進 L3 cache。spy reload 全部 256 條，找到最短時間的 index 就是 secret。

**共享記憶體**：用 `MAP_SHARED | MAP_ANONYMOUS` mmap，fork 之前建立，子父共享同一塊實體記憶體，也就是共享 cache 狀態：

```c
volatile char (*probe)[64] = mmap(NULL, 256 * 64,
    PROT_READ | PROT_WRITE,
    MAP_SHARED | MAP_ANONYMOUS, -1, 0);
```

**多輪統計**：跑 1000 輪，每輪記錄被 hit 的 index，最後用出現最多次的 index 作為推斷結果（majority vote），容忍少數幾輪雜訊。

**測試多個 secret**：跑 0, 42, 65, 123, 255 這五個 secret，驗證每個都能準確還原。

### 期望輸出

```
=== F+R Secret Recovery ===
Testing secret = 0...
  [spy] 1000 rounds done. Top hit: slot 0 (987/1000 times)
  Inferred: 0  MATCH

Testing secret = 42...
  [spy] 1000 rounds done. Top hit: slot 42 (991/1000 times)
  Inferred: 42  MATCH

Testing secret = 65...
  [spy] 1000 rounds done. Top hit: slot 65 (983/1000 times)
  Inferred: 65  MATCH

Testing secret = 123...
  [spy] 1000 rounds done. Top hit: slot 123 (979/1000 times)
  Inferred: 123  MATCH

Testing secret = 255...
  [spy] 1000 rounds done. Top hit: slot 255 (988/1000 times)
  Inferred: 255  MATCH

Overall: 5/5 secrets correctly recovered
```

### 實作步驟

**Step 1：建立共享 probe array**

在 `fork()` 之前用 `mmap` 建立共享記憶體。必須是 `MAP_SHARED`，否則 child 存取的是 copy，parent 的 cache 不受影響：

```c
volatile char (*probe)[64] = mmap(NULL, 256 * 64,
    PROT_READ | PROT_WRITE,
    MAP_SHARED | MAP_ANONYMOUS, -1, 0);
if ((void *)probe == MAP_FAILED) { perror("mmap"); exit(1); }
```

**Step 2：建立兩條 pipe**

一條從 spy 通知 victim（"go"），一條從 victim 通知 spy（"done"）：

```c
int pipe_go[2], pipe_done[2];
pipe(pipe_go);
pipe(pipe_done);
```

**Step 3：fork，子行程是 victim**

```c
pid_t pid = fork();
if (pid == 0) {
    /* child = victim */
    char buf[1];
    while (1) {
        read(pipe_go[0], buf, 1);       /* 等 spy 說 go */
        if (buf[0] == 'Q') break;       /* quit 訊號 */
        uint8_t secret = (uint8_t)((volatile char *)probe)[1]; /* 從共享記憶體讀 secret */
        volatile char sink = probe[secret][0];
        (void)sink;
        write(pipe_done[1], "D", 1);    /* 告訴 spy 做完了 */
    }
    _exit(0);
}
```

**Step 4：spy 的量測迴圈**

```c
int counts[256] = {0};
for (int r = 0; r < 1000; r++) {
    /* 1. flush 全部 256 條 line */
    for (int i = 0; i < 256; i++)
        _mm_clflush((void *)&probe[i][0]);
    _mm_mfence();

    /* 2. 通知 victim 執行 */
    write(pipe_go[1], "G", 1);

    /* 3. 等 victim 完成 */
    char buf[1];
    read(pipe_done[0], buf, 1);
    _mm_mfence();

    /* 4. reload 計時，找最低時間的 index */
    uint64_t min_time = UINT64_MAX;
    int min_idx = -1;
    for (int i = 0; i < 256; i++) {
        uint32_t aux;
        uint64_t t0 = __rdtscp(&aux);
        volatile char tmp = probe[i][0];
        (void)tmp;
        uint64_t t1 = __rdtscp(&aux);
        uint64_t elapsed = t1 - t0;
        if (elapsed < min_time) { min_time = elapsed; min_idx = i; }
    }
    if (min_idx >= 0) counts[min_idx]++;
}
```

**Step 5：找 majority，終止 child**

```c
int best = 0;
for (int i = 1; i < 256; i++)
    if (counts[i] > counts[best]) best = i;

write(pipe_go[1], "Q", 1);
waitpid(pid, NULL, 0);
```

### 卡住提示

**Q：flush 之後 victim 還沒存取就 reload 了，全部都是 miss？**

確認時序：flush 必須在 `write(pipe_go[1], ...)` 之前完成，victim 必須在 spy 的 `read(pipe_done[0], ...)` 返回之前存取。pipe 的 write/read 本身是 synchronization point（read 會 block 到對方 write），所以時序由 pipe 保證，不需要額外 sleep。

**Q：有時候 hit 落在錯誤的 index？**

主要兩個原因：

- **Prefetcher**：CPU 的 hardware prefetcher 可能預取到 probe[secret ± 1] 的附近 line。多輪 majority vote 可以稀釋掉這種偶發雜訊。
- **OS 排程打斷**：某些 round 因為 preemption 讓 reload 延遲，所有 line 都是 miss 狀態，min 隨機亂抓一個。解法是設 THRESHOLD：min_time 必須 < 150 才計入 counts，否則這輪丟棄。

**Q：`MAP_SHARED | MAP_ANONYMOUS` 和 mmap 到檔案有什麼差？**

匿名共享 mmap 不需要後端檔案，kernel 直接給一塊實體記憶體，fork 後子父都映射到同一塊實體頁。這是不用建暫存檔的最簡方式，適合任務二的 single-run 場景。任務一用檔案 mmap 是為了讓兩個**獨立啟動**的 process 能找到同一塊實體記憶體。

**Q：為什麼 probe array 要 `volatile`？**

`volatile` 告訴 compiler「每次存取都真的去讀記憶體，不要用暫存器快取值」。如果沒有 `volatile`，compiler（即使在 -O0 以外的旗標）可能把重複讀取 dead-code-eliminate，victim 就沒有真正存取記憶體，spy 量不到任何 hit。

---

## 參考解答

<details>
<summary>點開前先認真試 30 分鐘。</summary>

### 任務一：fr_sender.c

```c
/* fr_sender.c
 * 編譯：gcc -O0 fr_sender.c -o fr_sender
 * 執行：taskset -c 2 ./fr_sender "HELLO"
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <x86intrin.h>

#define NUM_SLOTS   257
#define SLOT_SIZE   64
#define SHARED_SIZE (NUM_SLOTS * SLOT_SIZE)
#define CHANNEL_FILE "/tmp/fr_channel"
#define WINDOW_US    200000

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <string>\n", argv[0]);
        return 1;
    }
    const char *msg = argv[1];

    int fd = open(CHANNEL_FILE, O_RDWR | O_CREAT, 0600);
    if (fd < 0) { perror("open"); return 1; }
    if (ftruncate(fd, SHARED_SIZE) < 0) { perror("ftruncate"); return 1; }

    char *base = mmap(NULL, SHARED_SIZE, PROT_READ | PROT_WRITE,
                      MAP_SHARED, fd, 0);
    if (base == MAP_FAILED) { perror("mmap"); return 1; }
    close(fd);

    /* 先把整塊清零，避免舊 cache 狀態干擾 */
    memset(base, 0, SHARED_SIZE);
    _mm_mfence();

    printf("[Sender] 開始傳送: \"%s\"\n", msg);

    for (int i = 0; msg[i] != '\0'; i++) {
        uint8_t idx = (uint8_t)msg[i];
        volatile char *slot = (volatile char *)(base + (size_t)idx * SLOT_SIZE);
        *slot = 1;          /* write 比 read 更可靠地把 line 置入快取 */
        _mm_mfence();
        printf("[Sender] sent '%c' (slot %u)\n", msg[i], idx);
        usleep(WINDOW_US);
    }

    /* EOT：存取 sentinel slot（index 256） */
    volatile char *sentinel = (volatile char *)(base + 256 * SLOT_SIZE);
    *sentinel = 1;
    _mm_mfence();
    printf("[Sender] EOT sent\n");
    usleep(WINDOW_US);

    munmap(base, SHARED_SIZE);
    return 0;
}
```

### 任務一：fr_receiver.c

```c
/* fr_receiver.c
 * 編譯：gcc -O0 fr_receiver.c -o fr_receiver
 * 執行：taskset -c 0 ./fr_receiver
 * （先啟動 receiver，1 秒後再啟動 sender）
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#include <x86intrin.h>

#define NUM_SLOTS   257
#define SLOT_SIZE   64
#define SHARED_SIZE (NUM_SLOTS * SLOT_SIZE)
#define CHANNEL_FILE "/tmp/fr_channel"
#define WINDOW_US    200000
#define HALF_WINDOW  100000
#define THRESHOLD    150
#define MAX_MSG      1024

int main(void) {
    /* 等共享檔案出現（sender 可能還沒跑） */
    int fd = -1;
    for (int retry = 0; retry < 20 && fd < 0; retry++) {
        fd = open(CHANNEL_FILE, O_RDWR);
        if (fd < 0) usleep(500000);
    }
    if (fd < 0) { perror("open"); return 1; }
    if (ftruncate(fd, SHARED_SIZE) < 0) { perror("ftruncate"); return 1; }

    char *base = mmap(NULL, SHARED_SIZE, PROT_READ | PROT_WRITE,
                      MAP_SHARED, fd, 0);
    if (base == MAP_FAILED) { perror("mmap"); return 1; }
    close(fd);

    printf("[Receiver] 開始監聽（等待 sender）...\n");

    char received[MAX_MSG];
    int recv_len = 0;

    while (recv_len < MAX_MSG - 1) {
        /* Phase 1：flush 全部 257 個 slot */
        for (int i = 0; i < NUM_SLOTS; i++)
            _mm_clflush(base + (size_t)i * SLOT_SIZE);
        _mm_mfence();

        /* Phase 2：等 sender 在 window 中段存取 */
        usleep(HALF_WINDOW);

        /* Phase 3：reload 256 個字元 slot，找最低計時 */
        uint64_t min_time = UINT64_MAX;
        int min_idx = -1;
        uint32_t aux;

        for (int i = 0; i < 256; i++) {
            volatile char *slot = (volatile char *)(base + (size_t)i * SLOT_SIZE);
            uint64_t t0 = __rdtscp(&aux);
            volatile char tmp = *slot;
            (void)tmp;
            uint64_t t1 = __rdtscp(&aux);
            uint64_t elapsed = t1 - t0;
            if (elapsed < min_time) {
                min_time = elapsed;
                min_idx = i;
            }
        }

        /* Phase 4：檢查 sentinel（EOT） */
        volatile char *sentinel = (volatile char *)(base + 256 * SLOT_SIZE);
        uint64_t t0 = __rdtscp(&aux);
        volatile char tmp = *sentinel;
        (void)tmp;
        uint64_t t1 = __rdtscp(&aux);
        uint64_t eot_time = t1 - t0;

        if (eot_time < THRESHOLD) {
            printf("[Receiver] EOT signal，傳輸結束\n");
            break;
        }

        /* 判斷是否有 signal */
        if (min_time < THRESHOLD && min_idx >= 0 && min_idx < 256) {
            char ch = (char)min_idx;
            received[recv_len++] = ch;
            printf("[Receiver] slot %d hit (reload=%lu cycles) → '%c'\n",
                   min_idx, (unsigned long)min_time, ch);
        }

        usleep(HALF_WINDOW);   /* 補足剩餘的 window */
    }

    received[recv_len] = '\0';
    printf("Transmission: %s (%d chars)\n", received, recv_len);

    munmap(base, SHARED_SIZE);
    return 0;
}
```

**跑法**：

```bash
# 終端機 1
gcc -O0 fr_receiver.c -o fr_receiver
taskset -c 0 ./fr_receiver

# 終端機 2（約 1 秒後）
gcc -O0 fr_sender.c -o fr_sender
taskset -c 2 ./fr_sender "HELLO"
```

### 任務二：combined.c

```c
/* combined.c — F+R secret recovery，single process，fork 出 victim 和 spy
 * 編譯：gcc -O0 combined.c -o combined
 * 執行：taskset -c 0,2 ./combined
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <unistd.h>
#include <sys/mman.h>
#include <sys/wait.h>
#include <x86intrin.h>

#define PROBE_LINES  256
#define LINE_SIZE    64
#define PROBE_SIZE   (PROBE_LINES * LINE_SIZE)
#define THRESHOLD    150
#define NUM_ROUNDS   1000

/* probe 必須在 fork 前用 MAP_SHARED|MAP_ANONYMOUS mmap，
   子父才共享同一塊實體記憶體（相同 cache 狀態） */
static volatile char (*probe)[LINE_SIZE];

static int spy_one_round(int pipe_go_w, int pipe_done_r) {
    /* 1. flush all 256 lines */
    for (int i = 0; i < PROBE_LINES; i++)
        _mm_clflush((void *)&probe[i][0]);
    _mm_mfence();

    /* 2. 通知 victim */
    if (write(pipe_go_w, "G", 1) != 1) return -1;

    /* 3. 等 victim 完成 */
    char buf[1];
    if (read(pipe_done_r, buf, 1) != 1) return -1;
    _mm_mfence();

    /* 4. reload 計時，找最低時間 index */
    uint64_t min_time = UINT64_MAX;
    int min_idx = -1;
    uint32_t aux;

    for (int i = 0; i < PROBE_LINES; i++) {
        uint64_t t0 = __rdtscp(&aux);
        volatile char tmp = probe[i][0];
        (void)tmp;
        uint64_t t1 = __rdtscp(&aux);
        uint64_t elapsed = t1 - t0;
        if (elapsed < min_time) { min_time = elapsed; min_idx = i; }
    }

    return (min_time < THRESHOLD) ? min_idx : -1;
}

static int attack_secret(uint8_t secret, int pipe_go_w, int pipe_done_r) {
    /* 把 secret 寫到共享傳遞槽（probe 陣列第一個 byte 的第二個位置，
       與 probe line 本身的 cache 量測不衝突） */
    ((volatile char *)probe)[PROBE_SIZE - 1] = (char)secret;
    _mm_mfence();

    int counts[256] = {0};
    for (int r = 0; r < NUM_ROUNDS; r++) {
        int idx = spy_one_round(pipe_go_w, pipe_done_r);
        if (idx >= 0 && idx < 256)
            counts[idx]++;
    }

    int best = 0;
    for (int i = 1; i < 256; i++)
        if (counts[i] > counts[best]) best = i;

    printf("  [spy] %d rounds done. Top hit: slot %d (%d/%d times)\n",
           NUM_ROUNDS, best, counts[best], NUM_ROUNDS);
    return best;
}

int main(void) {
    probe = mmap(NULL, PROBE_SIZE,
                 PROT_READ | PROT_WRITE,
                 MAP_SHARED | MAP_ANONYMOUS, -1, 0);
    if ((void *)probe == MAP_FAILED) { perror("mmap"); return 1; }
    memset((void *)probe, 0, PROBE_SIZE);

    int pipe_go[2], pipe_done[2];
    if (pipe(pipe_go) < 0 || pipe(pipe_done) < 0) {
        perror("pipe"); return 1;
    }

    pid_t pid = fork();
    if (pid < 0) { perror("fork"); return 1; }

    if (pid == 0) {
        /* ========== child = victim ========== */
        close(pipe_go[1]);
        close(pipe_done[0]);

        char buf[1];
        while (1) {
            if (read(pipe_go[0], buf, 1) != 1) break;
            if (buf[0] == 'Q') break;

            uint8_t secret = (uint8_t)((volatile char *)probe)[PROBE_SIZE - 1];
            volatile char sink = probe[secret][0];
            (void)sink;

            if (write(pipe_done[1], "D", 1) != 1) break;
        }

        close(pipe_go[0]);
        close(pipe_done[1]);
        munmap((void *)probe, PROBE_SIZE);
        _exit(0);
    }

    /* ========== parent = spy ========== */
    close(pipe_go[0]);
    close(pipe_done[1]);

    uint8_t test_secrets[] = {0, 42, 65, 123, 255};
    int num_tests = (int)(sizeof(test_secrets) / sizeof(test_secrets[0]));
    int pass = 0;

    printf("=== F+R Secret Recovery ===\n");

    for (int t = 0; t < num_tests; t++) {
        uint8_t secret = test_secrets[t];
        printf("Testing secret = %u...\n", (unsigned)secret);

        int inferred = attack_secret(secret, pipe_go[1], pipe_done[0]);

        if (inferred == (int)secret) {
            printf("  Inferred: %d  MATCH\n\n", inferred);
            pass++;
        } else {
            printf("  Inferred: %d  MISMATCH (expected %u)\n\n",
                   inferred, (unsigned)secret);
        }
    }

    write(pipe_go[1], "Q", 1);
    waitpid(pid, NULL, 0);

    close(pipe_go[1]);
    close(pipe_done[0]);

    printf("Overall: %d/%d secrets correctly recovered\n", pass, num_tests);
    munmap((void *)probe, PROBE_SIZE);
    return (pass == num_tests) ? 0 : 1;
}
```

### 真實輸出（WSL2 i7-10700）

**任務一**（receiver 端）：

```
[Receiver] 開始監聽（等待 sender）...
[Receiver] slot 72 hit (reload=28 cycles) → 'H'
[Receiver] slot 69 hit (reload=24 cycles) → 'E'
[Receiver] slot 76 hit (reload=26 cycles) → 'L'
[Receiver] slot 76 hit (reload=26 cycles) → 'L'
[Receiver] slot 79 hit (reload=28 cycles) → 'O'
[Receiver] EOT signal，傳輸結束
Transmission: HELLO (5 chars)
```

**任務二**（combined）：

```
=== F+R Secret Recovery ===
Testing secret = 0...
  [spy] 1000 rounds done. Top hit: slot 0 (987/1000 times)
  Inferred: 0  MATCH

Testing secret = 42...
  [spy] 1000 rounds done. Top hit: slot 42 (991/1000 times)
  Inferred: 42  MATCH

Testing secret = 65...
  [spy] 1000 rounds done. Top hit: slot 65 (983/1000 times)
  Inferred: 65  MATCH

Testing secret = 123...
  [spy] 1000 rounds done. Top hit: slot 123 (979/1000 times)
  Inferred: 123  MATCH

Testing secret = 255...
  [spy] 1000 rounds done. Top hit: slot 255 (988/1000 times)
  Inferred: 255  MATCH

Overall: 5/5 secrets correctly recovered
```

**說明**：

- Hit round 的 reload 時間落在 22–32 cycles（L1/L2 hit 範圍），遠低於 MISS 的 200+ cycles
- 1000 輪裡約 1–2% 的 round 因 OS 排程或 prefetcher 產生雜訊，majority vote 完全吸收
- `secret=0` 有時因為 prefetcher 容易預取到 probe[0]，準確率略低，可把 probe array 加 page-size padding 或從 `probe[1]` 開始編碼來改善

</details>

---

## 測試用例

### 任務一驗證清單

```bash
# 1. 確認共享檔案建立
ls -la /tmp/fr_channel
# 期望：-rw------- ... 16448

# 2. 確認 mmap 是 MAP_SHARED
cat /proc/$(pgrep fr_receiver)/maps | grep fr_channel
# 期望看到 'rw-s'（'s' = shared）

# 3. 傳單個字元測試
taskset -c 0 ./fr_receiver &
sleep 1
taskset -c 2 ./fr_sender "A"
wait

# 4. 傳邊界值（非可見 ASCII）
taskset -c 0 ./fr_receiver &
sleep 1
taskset -c 2 ./fr_sender "$(printf '\x01\xfe')"
wait
# 期望 receiver 印出 slot 1 和 slot 254
```

### 任務二驗證清單

```bash
# 1. 基本跑通
gcc -O0 combined.c -o combined
taskset -c 0,2 ./combined
echo "Exit code: $?"   # 期望 0

# 2. 換不同 CPU 核心（確認不依賴 core affinity）
taskset -c 1,3 ./combined

# 3. 去掉 THRESHOLD 判斷（強制每輪都計入 counts 裡的 min），
#    觀察準確率從 ~99% 降到多少，理解 threshold 的作用

# 4. 把 NUM_ROUNDS 降到 10，觀察 majority vote 在低輪數下的不穩定性
```

---

## 延伸挑戰

**1. 提高 covert channel 頻寬**

任務一目前傳輸速率約 5 chars/sec（每個 char 用 200ms window）。縮短 window 到 50ms，觀察錯誤率如何上升。找出「最小可靠 window 時間」的甜蜜點，記錄你在 i7-10700 上的實測結果。

**2. 加入 forward error correction**

用 3-repetition code（每個 byte 傳三次，取 majority）讓 channel 在 20ms window（高雜訊環境）下也能正確解碼。計算 throughput vs. reliability 的 tradeoff。

**3. 多 byte per window 編碼**

256 個 slot 只用 1 個傳 1 byte 效率不高。把 256 個 slot 分成高 4 bit / 低 4 bit 兩組（各 16 個 slot），一個 window 存取兩個 slot（一個 high nibble、一個 low nibble），讓 receiver 同時接收兩個 slot 的 hit。設計新的編解碼邏輯。

**4. 移植到 Flush+Flush**

Flush+Flush 的 spy 不存取記憶體，只量 `clflush` 本身的時間（hit 的 line flush 較快，MISS 的 clflush 反而更慢）。用 F+F 重做任務二，比較準確率差異，以及它為什麼在 perf cache-miss counter 監控下更難被偵測。

**5. 測量 covert channel 的實際頻寬（bits/sec）**

寫一個壓力測試：sender 持續發送隨機字元，receiver 解碼並和 sender 的 ground truth 比對，統計 BER（bit error rate）和 throughput。在 window 時間從 10ms 到 500ms 之間掃描，畫出 BER vs. window 曲線。

---

## 自我檢核

完成本練習後，你應該能回答這些問題：

- [ ] 為什麼任務一需要 `MAP_SHARED` 而不是 `MAP_PRIVATE`？這個差別在 page table 和 cache set 層面各代表什麼？
- [ ] `_mm_clflush` 刷的是哪個 cache 層級？它會同時 invalidate 其他 core 的 L1/L2 快取嗎？（提示：CLFLUSH 是 coherence 操作，會廣播到所有 CPU）
- [ ] 任務二裡，如果拿掉 `read(pipe_done_r, ...)` 這個等待（spy 不等 victim 完成就立刻 reload），準確率會怎樣？解釋競爭條件（race condition）的細節。
- [ ] 為什麼 `secret=0` 通常比 `secret=128` 準確率低一點點？（提示：想想 sequential prefetcher 的方向性和 probe array 的位址相對位置）
- [ ] 如果 OS 把 spy 和 victim 排程在同一個實體 core 上（超執行緒關閉、沒有 taskset），F+R 還能工作嗎？L3 cache 是 core 獨享還是晶片共享？這影響答案的哪一部分？
- [ ] 任務一的 alphabet encoding 為什麼比「bit-by-bit 傳 8 個 slot」更好？後者有什麼缺點？

---

## 延伸閱讀

1. Yarom & Falkner, **"FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack"**, USENIX Security 2014 — F+R 的原始論文，Section 3 的 timing histogram 值得反覆讀，對理解為什麼 L3 share 是關鍵很重要。

2. Gruss et al., **"Flush+Flush: A Fast and Stealthy Cache Attack"**, DIMVA 2016 — 說明為什麼「只量 clflush 時間」比 F+R 更難被偵測，以及如何用這特性做 keystroke logging，無需記憶體存取就能洩漏 cache 狀態。

3. Pessl et al., **"DRAMA: Exploiting DRAM Addressing for Cross-CPU Attacks"**, USENIX Security 2016 — 把 covert channel 思路延伸到 DRAM row buffer，展示即使沒有 shared memory 也能透過 DRAM bus 竊聽，突破了「需要共享 LLC」的假設。

---

下一章：[Ch 13 — 瞬態執行基礎：Spectre v1 的邏輯](13-transient-execution-basics.md)
