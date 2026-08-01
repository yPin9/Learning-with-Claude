# Ch 28 — 微架構 KASLR 破解

> **目標**：理解 KASLR（Kernel Address Space Layout Randomization，核心位址空間配置隨機化）作為 kernel exploit 防線的角色，掌握四類微架構手法如何在不觸碰任何記憶體漏洞的前提下定位 kernel 的確切載入位址，在 WSL2 + KPTI 開啟的真實環境下實做計時掃描實驗，並精確理解每種防禦的覆蓋範圍與盲點。這章是 Part 4 的收尾，也是整條攻擊鏈的橋梁：知道 kernel 在哪裡，才能把前面學到的一切武器化。

---

## 為什麼 KASLR 是 kernel exploit 的標準防線

在現代 Linux 系統上，要把一個記憶體損壞漏洞（heap overflow、UAF、stack smash）升級成 root shell，幾乎每條路都要過同一個門：你需要知道 kernel 中某個符號的**確切虛擬位址**。

- **ROP（Return-Oriented Programming）**：你需要精確的 gadget 位址。kernel image 裡的 gadget 在 KASLR 下每次開機都在不同的位置。
- **覆寫核心資料結構**：`modprobe_path`、`core_pattern`、`n_tty_ops` 之類的攻擊目標——位址不知道，寫到哪裡？
- **呼叫 `commit_creds(&init_cred)` 提權**：`commit_creds` 的位址在 KASLR 下是隨機的。

KASLR 的機制很直接：Linux 在 boot 時把整個 kernel image 對映到虛擬位址空間的某個**隨機偏移量**（offset）上，而不是固定位址。x86-64 上，kernel 的基底位址通常在 `0xffffffff80000000` 附近，但實際偏移量每次 boot 都不同（在高 KASLR entropy 設定下有數十 GB 的搜尋空間）。

這聽起來夠硬，但問題在：KASLR 的「隨機」只在 boot 時發生一次，**跑起來之後 kernel 的位址是固定的**。如果攻擊者能在 exploit 前取得 kernel 當前的載入位址，KASLR 就形同廢紙。

傳統的洩漏方式是靠 kernel 本身的漏洞——資訊洩漏（information disclosure）：kernel 不小心把一個 kernel 指標印到用戶空間，或者 kernel 的某個物件忘了初始化記憶體就回傳給用戶空間，然後攻擊者從那段記憶體中讀出指標計算偏移。但這類漏洞不是隨時都有的。

**微架構 KASLR bypass 的核心觀點是**：即使沒有任何記憶體漏洞，CPU 本身的計時特性就足以告訴你 kernel 在哪裡。這不需要讀 kernel 記憶體，只需要觀察「存取某個位址時花了多久」。

---

## 直覺：攻擊者的掃描場景

```
kernel 虛擬位址空間（高位，x86-64）

  0xffff800000000000
  ┌─────────────────────────────────────────────────────────────┐
  │                    unmapped（無效位址）                      │
  │  probe 0xffff800000000000  →  時間：220 cycles  ← 無對映    │
  │  probe 0xffff800000200000  →  時間：218 cycles  ← 無對映    │
  │  probe 0xffff800000400000  →  時間：219 cycles  ← 無對映    │
  │         ...（數千個候選位址）...                             │
  │  probe 0xffffffff80200000  →  時間：190 cycles  ← 有對映！  │◄─ kernel base?
  │  probe 0xffffffff80400000  →  時間：192 cycles  ← 有對映    │
  │  probe 0xffffffff80600000  →  時間：191 cycles  ← 有對映    │
  │         ...（kernel image 範圍）...                          │
  │  probe 0xffffffff82000000  →  時間：221 cycles  ← 無對映    │
  └─────────────────────────────────────────────────────────────┘
  0xffffffffffffffff

攻擊者視角：
  候選位址        prefetch 時間
  ────────────    ─────────────
  0xffff...000    ~220 cycles   ← 兩峰分布的高峰（無對映）
  0xffff...200    ~218 cycles
  ...
  0xffff...800    ~191 cycles   ← 低峰（有對映，kernel 在這裡！）
  0xffff...a00    ~190 cycles
  ...
  0xffff...c00    ~222 cycles   ← 又回到高峰（kernel image 結束）

  → 找到低峰的起始位址，算出 KASLR offset
  → offset = 低峰起點 - kernel_base_no_kaslr
```

這張圖展示了最基礎的直覺：**已對映的位址和未對映的位址，在某些計時探測下有可量測的時間差**。攻擊者掃描候選位址，繪出時間分佈圖，峰值轉換的邊界就是 kernel image 的邊界。

---

## 機制：為什麼對映狀態能洩漏計時資訊

要理解四大攻擊手法，先要理解一件事：**CPU 對「這個位址有對映」和「這個位址沒有對映」的內部處理路徑不同，這個差異被計時精度放大後就可觀測**。

### Page Table Walk 的差異

當 CPU 執行 prefetch 或 load 指令時，它需要把虛擬位址轉成實體位址，這個過程需要走 page table（或命中 TLB）。

- **位址有對映**：TLB 可能命中（快），或需要 page table walk，但 walk 完成後可以繼續（走到 PTE 後找到有效的 physical frame）。
- **位址沒有對映**：page table walk 走到某一層發現 entry 不存在（present bit = 0），硬體知道這是無效位址。

但關鍵是：**`prefetcht0` 指令在 x86 上不會觸發 fault**，即使位址無效。這讓攻擊者可以對任意位址發出 prefetch 而不會 segfault，同時 prefetch 完成的時間差異就洩漏了位址是否有效。

### KPTI（Kernel Page Table Isolation）改變了什麼

在 KPTI 之前，kernel 頁面雖然有 privilege 保護（ring 0 才能存取），但它們**存在於用戶空間的 page table 中**。這意味著：

1. 用戶空間執行時，kernel 頁面對應的 PTE 是存在的（present = 1）
2. Prefetch 到 kernel 位址時，TLB 或 page table walk 能找到這個 PTE
3. 和對映到不存在位址的 prefetch 相比，時間差異更明顯

KPTI（Linux 4.15 引入，應對 Meltdown）**把 kernel 頁面從用戶空間的 page table 中移除**。在用戶空間執行時，kernel 的 page table 是一份「影子」（shadow），只保留必要的 trampoline 映射（處理 syscall/中斷進出 kernel 的最少 code）。

KPTI 讓 prefetch 計時差異大幅縮小，但**沒有完全消失**——因為：
- 某些 kernel 映射（vsyscall、vDSO、fixmap 的部分）仍保留在用戶空間 page table 中
- Page table 本身的結構（哪一層 PTD entry 存在 vs. 不存在）在某些路徑上仍有計時差異
- Spectre 系列的推測執行路徑繞過了 KPTI 的隔離

---

## 四大攻擊手法

### 手法一：Prefetch Side-Channel（Gruss et al., CCS 2016）

**論文**：Daniel Gruss 等人，"Prefetch Side-Channel Attacks: Bypassing SMAP and Kernel ASLR"，CCS 2016。

這篇論文是這個方向的奠基工作。核心發現：`prefetcht0`（以及 `prefetchnta`、`prefetcht1`、`prefetcht2`）指令在 x86 上是**非故障性（non-faulting）**的——對無效位址也不會觸發 exception，但執行時間因位址對映狀態而有差異。

**具體機制**：

```
prefetcht0 → 微碼執行 → MMU 解析虛擬位址
                              ↓
                   TLB 查詢（已在 TLB 中？）
                   ├── 命中 TLB → 快速完成（記錄時間 T_hit）
                   └── TLB miss → page table walk
                                        ↓
                            走 PML4→PDP→PD→PT 四層
                            ├── 某層 entry not-present
                            │   → 停止，return（約 T_unmapped）
                            └── 所有層都存在
                                → 取出 physical frame number
                                → prefetch cache line（約 T_mapped）
```

攻擊者測量每個候選 kernel 位址的 prefetch 時間，找到明顯比「未對映基準」更短（或更長，取決於具體 CPU 和路徑）的位址叢集，那就是 kernel mapping 所在。

**KPTI 的影響**：KPTI 把大部分 kernel PTE 從用戶空間 page table 移除後，`T_mapped` 和 `T_unmapped` 的差異縮小。在 Gruss 的原始實驗環境（pre-KPTI）可達 50+ cycles 的差異；KPTI 後在部分 CPU 上縮減到 5-15 cycles，需要更多取樣才能可靠分辨。

**繞過 SMAP 的額外發現**：這篇論文同時發現 prefetch 指令**不受 SMAP（Supervisor Mode Access Prevention）保護**。原來 SMAP 阻止 kernel 在 ring 0 存取 ring 3 的記憶體，但 prefetch 是「提示」指令，SMAP 沒有阻止它從用戶空間探測 kernel 位址的計時。（後來 Intel 修正了這個行為，但修正時間因 CPU 世代而異。）

---

### 手法二：Cache/TLB 計時掃描

這是最直觀的方法。在 KPTI 之前，邏輯很直接：

```
1. 呼叫某個 syscall（例如 getpid()）
2. Kernel 執行時，kernel 的 code page 被存取 → 載入 L3 cache
3. Syscall 返回用戶空間
4. 用戶空間對候選 kernel 位址執行 clflush → reload 計時
5. 如果候選位址在 cache 中（步驟 2 帶進來的）→ reload 很快
6. 如果不在 → reload 慢（要去 DRAM）
```

這個方法在 KPTI 之前非常有效，因為：

- Syscall 後 kernel 的 cache 狀態跨越到用戶空間可見（相同的 cache tag）
- `clflush` 可以用在任何位址（包括 kernel 虛擬位址）而不 fault

**KPTI 之後的狀態**：

KPTI 把 kernel 頁面從用戶空間 page table 移除，所以 clflush 對 kernel 位址的行為變了——沒有對映就沒有 cache line 對應，flush 和 probe 都失去意義。**這個方法基本上被 KPTI 封死了**。

但有兩個殘留洩漏點：

1. **vsyscall 頁面**（位址 `0xffffffffff600000`）：某些 kernel 配置仍保留 vsyscall 映射（emulation 模式），這是**用戶空間 page table 中唯一可預測的 kernel 位址**。它不受 KASLR 影響（vsyscall 位址是固定的），因此不直接給 KASLR offset，但可以確認 cache 側信道在這個系統上的基準計時。

2. **vDSO**：vDSO（virtual Dynamic Shared Object）也在用戶空間 page table 中，但它的位址是有 ASLR 的（用戶空間 ASLR）。不過 vDSO 的基底位址可以從 `/proc/self/maps` 直接讀到，對攻擊者沒有必要用計時破。

---

### 手法三：EchoLoad 與 Data Bounce（推測執行路徑）

**EchoLoad**（2022 年前後揭露的技術）把 Spectre 的機制轉向位址探測：不是用推測執行竊取資料，而是用推測執行確認「這個位址是否存在對映」。

**基本思路**：

```c
// 如果 kernel_addr 有效，推測路徑上的 cache 行為不同
if (/* speculated true */) {
    x = *kernel_addr;           // speculative load
    cache_probe[x * 64];        // encode into cache（如同 Spectre v1）
}
// 測量 cache_probe 的哪個 slot 被存取
// → 如果 slot 被存取：kernel_addr 有效且有資料
// → 如果沒有：kernel_addr 無效（或資料是特定值）
```

但 EchoLoad 更微妙的地方是：即使不試圖讀 kernel 資料，僅靠**推測性的 TLB/cache 存取**本身就能洩漏「位址是否有對映」的資訊。因為 speculative load 到一個有對映位址的行為（TLB hit、cache line 被預載）和 speculative load 到無效位址的行為（MMU 快速拒絕、無 cache 活動）可以透過後續的計時觀測區分。

**Data Bounce** 是另一個 2021-2022 年揭露的技術，利用某些 CPU 的 store-to-load forwarding（儲存到讀取的轉發）行為：

```
1. 對目標位址做 speculative store（不一定成功）
2. 隨後對同位址做 speculative load
3. 如果位址有效，load 可能從 store buffer 轉發
4. 計時觀察這個「轉發」是否發生
→ 轉發行為的計時特徵洩漏了位址對映狀態
```

這類攻擊的重要前提：**需要 Spectre 緩解不完整**。如果系統完整部署了 retpoline + IBPB + LFENCE 等 Spectre 緩解，推測執行路徑被大幅壓制，EchoLoad/Data Bounce 的可行性隨之大降。這也是為什麼「有了 KPTI + Spectre 全套緩解」之後，這類攻擊難度大幅上升（但仍有研究者在找新的推測路徑）。

---

### 手法四：Syscall 後殘留（Prime+Probe on LLC）

這個方法不依賴 prefetch 或推測執行，而是純粹觀察**系統呼叫前後 LLC（Last Level Cache）的狀態變化**。

**攻擊流程**：

```
1. Prime（灌滿 LLC 的特定 set）
   ├── 選定一組 eviction set，代表 LLC 的 S 個 cache set
   └── 用 stride 存取灌滿它們
2. 觸發 syscall（例如 read(0, buf, 0)——不做實際 I/O）
   └── Kernel 執行中：kernel 的 code/data 被存取，
       這些存取按照 VA→PA→cache set 的映射落入某些 cache set
3. Probe（測量 eviction 情況）
   ├── 重新讀取 prime 的每個 set
   └── 計時：快 → set 未被 evict（kernel 沒存取這個 set）
              慢 → set 被 evict（kernel 存取了這個 set！）
4. 記錄哪些 cache set 被 evict → 這是 kernel 存取的 set pattern
```

**從 cache set 推算 kernel 位址**：

實體位址 → cache set 的映射在 Intel CPU 上基本是確定的（set = PA bits[11:6] 對 LLC 的 index bits，雖然 LLC slice 的 hash function 更複雜，但有論文逆向過）。攻擊者如果知道 eviction set 對應哪些 LLC set，就可以從「這個 syscall evict 了這些 set」反推「kernel 在 syscall 中存取了哪些實體位址範圍」。

配合 kernel 的記憶體布局知識（kernel 的 code section 在 image 的哪個 offset？），就能推算出 kernel 載入的實體基底位址，進而推算出虛擬位址（因為 Linux 的 physmap 讓 VA→PA 的偏移可以計算）。

**KPTI 的影響**：KPTI 沒有改變 syscall 執行時的 cache 行為——kernel 執行時仍然存取 kernel 的 code/data，LLC 仍然被影響。這個方法在 KPTI 後仍然有效，但精度要求更高（因為 syscall 的 cache pattern 比較複雜，雜訊更多）。

---

## 實驗：Prefetch 計時掃描（誠實標注 KPTI 影響）

這個實驗在 WSL2 + Intel i7-10700（Comet Lake）上驗證 prefetch 計時差異是否仍然可觀測。**先看環境再跑**。

### 環境確認

```bash
# 確認 KPTI 狀態
grep . /sys/devices/system/cpu/vulnerabilities/meltdown
# 現代 kernel：Mitigation: PTI

# 確認 vsyscall 是否還存在
grep vsyscall /proc/self/maps
# 若存在：ffffffffff600000-ffffffffff601000 r-xp ... [vsyscall]
# 若不存在：表示 vsyscall=none 或 vsyscall=emulate 但沒有對映

# 確認 kernel 版本（KPTI 從 4.15 開始）
uname -r

# 確認 CPU
cat /proc/cpuinfo | grep "model name" | head -1
```

### C 程式碼：掃描 kernel 高位位址的 prefetch 計時

```c
/*
 * kaslr_prefetch_scan.c
 *
 * 掃描 kernel 虛擬位址空間的 prefetch 計時差異
 * 在啟用 KPTI 的現代 kernel 上，時間差可能 < 10 cycles
 *
 * 編譯：gcc -O2 -o kaslr_prefetch_scan kaslr_prefetch_scan.c
 * 執行：sudo ./kaslr_prefetch_scan  （或不加 sudo，但取樣雜訊更多）
 */

#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <x86intrin.h>

#define SAMPLES     1000
#define STEP        (2UL * 1024 * 1024)    /* 2 MB 為步長（page 對齊）*/
#define ADDR_START  0xffff800000000000UL
#define ADDR_END    0xffffffff80000000UL
#define MAX_ENTRIES 4096

typedef struct {
    uint64_t addr;
    uint64_t median_cycles;
} ScanEntry;

/* rdtscp 精確計時 */
static inline uint64_t rdtscp_begin(void) {
    uint32_t aux;
    uint64_t t = __rdtscp(&aux);
    _mm_lfence();
    return t;
}

static inline uint64_t rdtscp_end(void) {
    _mm_lfence();
    uint32_t aux;
    return __rdtscp(&aux);
}

/* 對某個位址執行 prefetch 並計時 */
static uint64_t time_prefetch(volatile void *addr) {
    uint64_t t0, t1;
    t0 = rdtscp_begin();
    __builtin_prefetch((const void *)addr, 0, 0);  /* prefetcht0 */
    _mm_lfence();
    t1 = rdtscp_end();
    return t1 - t0;
}

/* 取 N 個樣本的中位數 */
static uint64_t median_time(volatile void *addr, int n) {
    uint64_t samples[SAMPLES];
    for (int i = 0; i < n; i++) {
        samples[i] = time_prefetch(addr);
        /* 加點「雜訊」防止 CPU 最佳化掉 prefetch */
        __asm__ volatile("" ::: "memory");
    }
    /* 簡單插入排序取中位數（小陣列夠用）*/
    for (int i = 1; i < n; i++) {
        uint64_t key = samples[i];
        int j = i - 1;
        while (j >= 0 && samples[j] > key) {
            samples[j + 1] = samples[j];
            j--;
        }
        samples[j + 1] = key;
    }
    return samples[n / 2];
}

int main(void) {
    ScanEntry entries[MAX_ENTRIES];
    int count = 0;

    printf("[*] Prefetch 計時掃描 kernel 位址空間\n");
    printf("[*] 範圍：0x%lx → 0x%lx，步長 %lu MB\n",
           ADDR_START, ADDR_END, STEP / 1024 / 1024);
    printf("[*] 每個位址 %d 次取樣，取中位數\n\n", SAMPLES);

    /* 先量「確定無對映」位址的基準 */
    volatile void *unmapped_baseline = (void *)0xdead000000000000UL;
    uint64_t baseline = median_time(unmapped_baseline, SAMPLES);
    printf("[*] 無對映基準計時（0xdead...）：%lu cycles\n\n", baseline);

    /* 掃描候選 kernel 位址 */
    uint64_t addr = ADDR_START;
    while (addr < ADDR_END && count < MAX_ENTRIES) {
        volatile void *ptr = (void *)addr;
        uint64_t t = median_time(ptr, SAMPLES);
        entries[count].addr = addr;
        entries[count].median_cycles = t;
        count++;

        /* 只印出比基準快超過 5 cycles 的位址（可能有對映）*/
        if (baseline > t + 5) {
            printf("[!] 位址 0x%lx：%lu cycles（比基準快 %lu）← 可能有對映\n",
                   addr, t, baseline - t);
        } else {
            printf("    位址 0x%lx：%lu cycles\n", addr, t);
        }

        addr += STEP;
    }

    printf("\n[*] 掃描完成，共 %d 個候選位址\n", count);
    printf("\n[注意] 在啟用 KPTI 的 kernel（>= 4.15）上：\n");
    printf("  - 若時間差 < 10 cycles 且無明顯雙峰，KPTI 正在壓制洩漏，屬正常現象\n");
    printf("  - WSL2 / Hyper-V 虛擬化可能引入額外雜訊，使訊號更模糊\n");
    printf("  - 步長 2 MB 可改小到 1 MB，取樣數改到 5000 再試\n");
    printf("  - 若完全看不出差異：恭喜，你的系統防禦有效\n");

    return 0;
}
```

### vsyscall 頁面的計時基準

vsyscall 頁面（若存在）是理想的「已知有對映」基準，因為它在用戶空間 page table 中是真實存在的：

```c
/*
 * vsyscall_timing.c
 * 測量 vsyscall 頁面 vs 任意無效位址的 prefetch 時間差
 * 編譯：gcc -O2 -o vsyscall_timing vsyscall_timing.c
 */

#include <stdio.h>
#include <stdint.h>
#include <x86intrin.h>

#define VSYSCALL_ADDR  0xffffffffff600000UL
#define INVALID_ADDR   0xffffffffff700000UL
#define SAMPLES 2000

static inline uint64_t time_prefetch_addr(uint64_t addr) {
    uint32_t aux;
    uint64_t t0, t1;
    _mm_lfence();
    t0 = __rdtscp(&aux);
    __builtin_prefetch((const void *)addr, 0, 0);
    _mm_lfence();
    t1 = __rdtscp(&aux);
    return t1 - t0;
}

int main(void) {
    uint64_t vsyscall_times[SAMPLES], invalid_times[SAMPLES];
    uint64_t vs_sum = 0, inv_sum = 0;

    /* 先確認 vsyscall 存在 */
    printf("[*] 請先確認：grep vsyscall /proc/self/maps\n");
    printf("[*] 若無輸出，vsyscall 已關閉，vsyscall 組的數字無意義\n\n");

    for (int i = 0; i < SAMPLES; i++) {
        vsyscall_times[i] = time_prefetch_addr(VSYSCALL_ADDR);
        invalid_times[i]  = time_prefetch_addr(INVALID_ADDR);
        vs_sum  += vsyscall_times[i];
        inv_sum += invalid_times[i];
    }

    printf("vsyscall (0x%lx) 平均：%.1f cycles\n",
           VSYSCALL_ADDR, (double)vs_sum / SAMPLES);
    printf("無效位址 (0x%lx) 平均：%.1f cycles\n",
           INVALID_ADDR, (double)inv_sum / SAMPLES);
    printf("差異：%.1f cycles\n",
           (double)inv_sum / SAMPLES - (double)vs_sum / SAMPLES);
    printf("\n[解讀]\n");
    printf("  差異 > 20 cycles：prefetch side-channel 有效，KPTI 不完整或 vsyscall 真的存在\n");
    printf("  差異 5-20 cycles：邊界情形，需要更多取樣和統計\n");
    printf("  差異 < 5 cycles ：KPTI 生效，計時差異淹沒在量測雜訊中\n");

    return 0;
}
```

### 預期結果與解讀

| 環境 | 預期 vsyscall vs 無效位址差異 | 原因 |
|------|-------------------------------|------|
| 舊 kernel（< 4.15，無 KPTI） | 30–80 cycles | prefetch 完整感知 PTE |
| 現代 kernel（>= 4.15，KPTI on）| 0–15 cycles | kernel PTE 從用戶 page table 移除 |
| WSL2 / Hyper-V | 通常 < 10 cycles | 虛擬化額外隔離 + 計時雜訊 |
| vsyscall=none | < 5 cycles | vsyscall 頁面根本不存在 |

**WSL2 的額外問題**：WSL2 跑在 Hyper-V 上，`rdtsc` 的精度受虛擬化影響，且 VM 的 TLB 行為與裸機不同。實驗看不到明顯差異不代表概念錯誤，只代表這個 *特定環境* 的防禦有效。要驗證原始概念，需要在裸機 + 舊 kernel 或 no-KPTI 的 VM 上實驗。

---

## 緩解措施對比

| 防禦機制 | 針對手法 | 覆蓋強度 | 代價 |
|---------|----------|----------|------|
| KPTI（Kernel Page Table Isolation） | Prefetch 計時、Cache 掃描 | 強（但不完全） | Syscall overhead +5-30%（舊 CPU 更高；現代 CPU 有 PCID 優化降至 1-5%） |
| KASLR entropy 提升 | 所有掃描手法 | 弱（增加掃描成本但不解決洩漏本身）| 幾乎無 |
| vsyscall=none | Cache 掃描的 vsyscall 基準 | 移除一個固定洩漏點 | 極少 app 依賴 vsyscall，基本無影響 |
| Spectre 完整緩解（retpoline+IBPB+LFENCE）| EchoLoad / Data Bounce | 強（針對推測路徑）| 各種效能損失，因工作負載而異 |
| Prefetcher 關閉 | Prefetch 計時 | 部分（軟體 prefetch 仍可用）| 效能損失 5-15% |
| kptr_restrict=2 + dmesg_restrict=1 | 傳統軟體洩漏路徑 | 強（封住最簡單的洩漏方式）| 幾乎無 |
| 大 KASLR + FG-KASLR（Function Granularity）| 所有位址推測手法 | 強（增加 per-function 的隨機性）| Boot time 增加；部分場景不相容 |

**重要觀點**：這些防禦是分層的。最有效的策略是先確保最容易的洩漏路徑（`/proc/kallsyms`、`dmesg`、`kptr_restrict`）都封住，再考慮微架構側信道。攻擊者永遠選最省力的路徑。

---

## 踩雷

**1. KPTI 不等於完全防住 prefetch 計時攻擊**

KPTI 把大部分 kernel PTE 從用戶空間 page table 移除，但「page table walk 停在哪一層」這件事本身仍然可以洩漏資訊。高 1-2 層的 PDE/PDPTE 在某些 kernel 版本和 CPU 上可能仍有殘留。實際上 KPTI 是「大幅提高攻擊難度」而不是「數學上保證消除洩漏」。2019-2022 年之間有多篇論文展示了 post-KPTI 仍可運作的 prefetch 計時攻擊（只是需要更多取樣和更精細的統計）。

**2. 「KASLR entropy 高就安全」的錯誤直覺**

x86-64 現代 Linux 的 KASLR entropy 大約是 9 bits（512 個可能的 2 MB offset），在裸機 64-bit kernel 上是夠高的。但在某些場景下 entropy 遠低於你以為的：

- **32-bit kernel**：虛擬位址空間只有 4 GB，kernel 通常只有幾十個可能的載入位置，暴力掃描幾分鐘就掃完了
- **Embedded / realtime kernel**：某些 distro 為了效能關閉 KASLR
- **Hibernation / kexec**：有些路徑下 kernel 重用上次的 KASLR offset
- **Hypervisor 環境**：如果 guest 的實體記憶體是連續的且從固定位址開始，PA 的「隨機」程度也受限

KASLR entropy 是「掃描成本乘數」，不是「計時洩漏的解藥」。

**3. 先查 `/proc/kallsyms` 再想微架構攻擊**

在非 hardened 的 Linux 系統上，`/proc/kallsyms` 可以直接給你所有 kernel 符號的位址。只有在 `kptr_restrict >= 1` 且不是 root 的情況下才會隱藏。如果 `dmesg_restrict = 0`，`dmesg` 也可能印出 kernel 指標。

這是微架構攻擊在真實攻擊場景中不常用的原因之一：攻擊者幾乎都會先試最簡單的軟體路徑。微架構 KASLR bypass 的真正價值場景是：**攻擊目標的系統已正確設定 `kptr_restrict=2` + `dmesg_restrict=1`，沒有其他記憶體洩漏漏洞，且攻擊者只有有限用戶空間程式碼執行能力**——這在實際 kernel CVE 的 exploit 開發中是會遇到的情況，但不是最常見的路徑。

**4. EchoLoad / Data Bounce 的前提假設往往不成立**

這兩類攻擊依賴 CPU 在推測執行路徑上的特定行為。Spectre 緩解（尤其是 IBRS/IBPB、lfence 隔離、以及 CPU 微碼更新）大幅改變了推測執行路徑的行為。你在某篇 2022 年論文的實驗平台（特定 CPU 世代、特定 kernel 版本）上看到的數字，在你自己的 i7-10700 + 最新 Ubuntu 22.04 kernel 上可能完全復現不了——因為微碼更新已經打補丁了。這不是論文錯了，而是硬體行為確實隨 firmware 更新而改變。

**5. 計時測量的統計要夠，不能只看單次**

Prefetch 計時差異在 KPTI 後可能只有 5-15 cycles，而單次 `rdtsc` 的量測誤差本身就可達 10-20 cycles（系統中斷、TLB flush、prefetcher 行為）。用 100 次取樣的平均值來判斷「有對映 vs 無對映」是不夠的——需要至少 1000-5000 次取樣，然後做統計檢定（比如 t-test 或 Welch's test），看兩個分佈是否顯著不同。「我跑了 100 次，沒看到差異，所以 KPTI 防住了」和「我跑了 5000 次，做了 t-test，p < 0.001，分佈有統計顯著差異」是完全不同量級的結論。

---

## 進階方向

### FG-KASLR（Function-Granularity KASLR）

標準 KASLR 隨機化整個 kernel image 的載入偏移量，但 image 內各符號的相對位置是固定的——攻擊者知道任何一個符號的位址，就能算出所有符號的位址。

FG-KASLR（由 Google 提出，在 Linux 5.x 時代有 out-of-tree patch）把每個 function 都對映到各自隨機的位址，讓「知道一個函數的位址」不代表「知道其他函數的位址」。這對 KASLR bypass 之後的 ROP 構造是重大障礙——你算出了 `commit_creds` 的位址，但 gadget 在哪？

FG-KASLR 沒有進主線 kernel（效能/相容性問題），但作為研究方向很重要，某些安全 kernel（如 KSPP）有自己的實作。

### KASLR Entropy 強化

Linux 的 CONFIG_RANDOMIZE_BASE 在 64-bit 上提供的 entropy 大約是 9 bits（512 種可能，步長 2 MB）。有提案把步長縮小到 4 KB，entropy 提升到更高 bits，但代價是需要更複雜的 page table 設置和潛在的效能影響。

### Spectre-v1 型 KASLR Bypass

一個組合攻擊：用 Spectre-v1 的 bounds check bypass 在 kernel 的推測執行路徑上洩漏某個 kernel 指標，再用這個指標推算 KASLR offset。這比純微架構計時掃描更強力（一次洩漏就夠），但需要在 kernel 中找到合適的 Spectre gadget（存在 kernel 中的 `if (user_controlled_index < bound) { x = array[index]; }` 樣式）。

---

## 動手練習

完整的練習在 [練習 C：破 KASLR](practice-c-break-kaslr.md)，這裡列出幾個你現在就能做的熱身：

**練習 1：環境掃描**

```bash
# 確認所有相關的 kernel 安全設定
grep . /sys/devices/system/cpu/vulnerabilities/*
cat /proc/sys/kernel/kptr_restrict
cat /proc/sys/kernel/dmesg_restrict
grep vsyscall /proc/self/maps || echo "vsyscall not mapped"
grep vdso /proc/self/maps

# 確認 KASLR 是否啟用
cat /proc/cmdline | grep -o "nokaslr" || echo "KASLR 已啟用（無 nokaslr 參數）"
```

**練習 2：計時基準建立**

```bash
# 編譯並執行 vsyscall_timing.c（本章 code），記錄差異
gcc -O2 -o vsyscall_timing vsyscall_timing.c
./vsyscall_timing
# 取樣 10 次，觀察差異的穩定性
for i in $(seq 10); do ./vsyscall_timing 2>/dev/null | grep "差異"; done
```

**練習 3：和 kernel 符號對比**

```bash
# root 下取得 kernel base（參考答案）
sudo cat /proc/kallsyms | grep " _text" | head -1
# 對比你的掃描結果（若有）和這個位址
# _text 是 kernel image 的起點
```

**練習 4：思考題**

如果你在不知道 kernel 位址的情況下，只有 `getpid()` 這個 syscall 可以呼叫（沒有其他 syscall），設計一個 Prime+Probe 實驗，估計 `getpid()` 執行路徑上的 kernel code 觸碰了哪些 LLC cache set？你需要什麼先備知識（LLC 的 set 數、hash function）？

---

## 本章重點整理

- **KASLR 的實質**：一次性 boot-time 隨機化，kernel 執行期間位址固定。bypass KASLR 等於讓後續一切 exploit 技術可以精確定位目標。

- **微架構 bypass 的核心洩漏源**：「已對映位址 vs 未對映位址」在 TLB/cache/prefetch 路徑上的計時差異，不需要讀 kernel 記憶體，只要計時就夠。

- **四大手法及適用條件**：
  - Prefetch 計時：最基礎，KPTI 大幅壓制但未完全消除
  - Cache/TLB 掃描：KPTI 前有效，KPTI 後基本失效
  - EchoLoad / Data Bounce：推測執行路徑，需 Spectre 緩解不完整
  - Syscall 後殘留 Prime+Probe：對 LLC 計時，KPTI 無法防護，但精度需求高

- **KPTI 是最有效的緩解**：把 kernel PTE 從用戶 page table 移除，大幅縮小計時差異。但它是「大幅提高攻擊難度」而不是「數學保證安全」。

- **WSL2 現實**：Hyper-V 虛擬化 + KPTI 雙重影響，實驗看不到明顯差異屬正常。裸機 + 舊 kernel 才能復現原始論文的數字。

- **攻擊鏈意義**：這個「不需要記憶體漏洞就能定位 kernel」的能力，是 kernel exploit 中「有漏洞但需要 KASLR offset」和「有完整 exploit 鏈」之間的橋。

---

## 自我檢核

讀完本章，你應該能回答：

1. KASLR 的設計目標是什麼？它保護了什麼、沒有保護什麼？
2. `prefetcht0` 指令為什麼可以在不觸發 fault 的情況下探測 kernel 位址的對映狀態？
3. KPTI 如何改變 prefetch 計時攻擊的可行性？殘留的洩漏點在哪裡？
4. Syscall 後 LLC 殘留的 Prime+Probe 攻擊為什麼 KPTI 無法防護？
5. EchoLoad 和 Data Bounce 的核心前提是什麼？當 Spectre 緩解完整時為什麼難用？
6. `kptr_restrict` 和 `dmesg_restrict` 防護的是哪一類洩漏路徑？和微架構攻擊的關係是什麼？
7. 為什麼「KASLR entropy 高」不等於「微架構 KASLR bypass 無效」？
8. FG-KASLR 比標準 KASLR 多提供了什麼保護，代價是什麼？

---

## 延伸閱讀

- **Gruss 等人，"Prefetch Side-Channel Attacks: Bypassing SMAP and Kernel ASLR"，CCS 2016**
  這個方向的奠基論文。完整閱讀 Section 4（計時差異的成因）和 Section 5（KASLR bypass 實驗），理解「為什麼 prefetch 不會 fault 但仍洩漏對映狀態」的硬體機制說明。

- **Canella 等人，"A Systematic Evaluation of Transient Execution Attacks and Defenses"，USENIX Security 2019**
  瞬態執行攻擊的分類學。其中 Section 6 討論 KASLR bypass 作為 covert channel 的應用場景，以及哪些瞬態執行機制可以組合成 KASLR oracle。

- **Jonathan Corbet，"KAISER: hiding the kernel from user space"，LWN.net，2017**
  `https://lwn.net/Articles/738975/` ——KPTI 前身 KAISER 的技術說明文章，解釋為什麼把 kernel PTE 從用戶 page table 移除可以防護 Meltdown 和 prefetch 計時攻擊，以及 PCID 如何降低 context switch 的效能損失。

- **Jonathan Corbet，"The current state of kernel page-table isolation"，LWN.net，2018**
  `https://lwn.net/Articles/741878/` ——KPTI 正式合併進 Linux 4.15 時的技術細節。包含 vsyscall emulation 的保留原因，以及哪些映射必須留在用戶 page table 中（trampoline）。

- **van Schaik 等人，"RIDL: Rogue In-Flight Data Load"，IEEE S&P 2019**
  MDS 攻擊家族之一。雖然主要是資料洩漏，但其中對「推測執行路徑如何影響對映狀態感知」的分析對理解 EchoLoad 類攻擊有幫助。

- **Hund 等人，"Practical Timing Side Channel Attacks against Kernel Space ASLR"，IEEE S&P 2013**
  早期的 kernel ASLR timing attack（早於 KPTI），展示了在沒有軟體漏洞的情況下純靠計時 page fault handling 時間差破 kernel ASLR。雖然被 KPTI 封住了，但原始機制的說明非常清晰。

---

Part 4 到此結束。我們從 Rowhammer（改寫實體記憶體的位元）走到 port contention（利用執行埠的競爭）、TLB 側信道，最後到這章的「不需要記憶體漏洞、純靠計時找到 kernel 在哪裡」。這個「定位 kernel」的能力，直接銜接 `binary_exploitation` → `kernel_pwn` 的攻擊鏈：

知道了 KASLR offset → 算出 `commit_creds` 真實位址 → 算出 ROP gadget 真實位址 → exploit 鏈成立。

接下來 Part 5 要問的問題是：上面這些攻擊，有哪些已經被修了，修法是什麼，修法的縫又在哪裡？

→ [練習 C：破 KASLR](practice-c-break-kaslr.md)
