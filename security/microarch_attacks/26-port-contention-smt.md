# Ch 26 — Port Contention 與 SMT 側信道

> **目標**：理解同一實體核心的兩個 SMT（Simultaneous Multi-Threading，同步多執行緒）邏輯執行緒如何透過共用執行埠（execution port）產生可量測的時序洩漏；掌握 PortSmash 與 SMoTherSpectre 的攻擊原理；在 i7-10700（Comet Lake）上設計並執行 port contention 實驗；評估 SMT 側信道與 cache 側信道的異同與對應防禦。

---

## 概念：SMT 的後端共享是個雙面刃

Intel 從 Pentium 4 開始推廣 Hyper-Threading（HT）——讓一個實體核心同時維持兩個執行緒的架構狀態（暫存器、PC、EFLAGS），並盡量填滿後端的每條執行管線。這個設計在大多數工作負載下能提升 15–30% 的吞吐，理由是兩個執行緒的指令流可以互補地使用不同的功能單元。

問題在於：後端資源不可能完全分割。執行埠（execution port）是 CPU 後端的核心——每個 port 對應一組功能單元（ALU、multiply、load/store、branch…），排程器（scheduler）把亂序執行視窗（ROB/RS）裡已就緒的 micro-op 分派給空閒的 port。兩個 SMT 執行緒的 micro-op **共用同一個排程器**，也競爭同一批 port。

當執行緒 A 和 B 都大量發出需要同一個 port 的指令，就出現**結構冒險（structural hazard）**：micro-op 在排程器裡排隊等待，吞吐下降、延遲上升。這個延遲不是由 A/B 之間的記憶體存取決定——即使它們完全沒有共享任何記憶體，延遲一樣洩漏對方的執行樣式。

這正是 port contention 側信道的核心：**時序差異本質上是競爭對手的指令型別統計**。

---

## 直覺：兩個執行緒搶 Port 的隊伍

```
實體核心（Physical Core）內部
─────────────────────────────────────────────────────────
  執行緒 T0（CPU 0）           執行緒 T1（CPU 8）
  [imul] [imul] [imul]         [imul] [imul]
         │                             │
         ▼                             ▼
  ┌────────────────────────────────────────────────────┐
  │          統一排程器 (Unified Scheduler)             │
  │   micro-op 佇列（T0 和 T1 的 µop 混合排隊）         │
  └────┬──────────┬──────────┬──────────┬─────────────┘
       │          │          │          │
       ▼          ▼          ▼          ▼
    Port 0     Port 1     Port 5     Port 6
   [imul µop] [imul µop]  [add]      [add]
   ← T0 和 T1 的 imul 都只能走 Port 0/1，Port 不夠用！ →

結果：
  T0 的 imul 等待 Port → 吞吐下降
  T1 的 imul 等待 Port → 吞吐下降
  T0 量到「我自己慢了」→ 推斷 T1 也在用 Port 0/1
                          → 推斷 T1 在做整數乘法運算
                          → 根據時序序列還原 T1 的執行樣式
```

如果把 T1 換成執行 ECDSA 的 OpenSSL：T1 在做純 ALU 乘法的時間段和在做記憶體存取的時間段，T0 受到的干擾完全不同。T0 把這個時間序列記錄下來，就得到了 T1 的執行樣式剖面（execution profile）。

---

## i7-10700 執行埠架構

Comet Lake 使用「Sunny Cove 前」的 14nm Skylake 衍生後端，具備 10 個執行埠（port 0–9），但功能分布不均：

```
Port   功能單元（Functional Unit）
─────  ──────────────────────────────────────────────────────
  0    整數 ALU、分支、整數移位、IMUL（32/64-bit）、
       向量整數/FP ALU（AVX2）、AES-NI、向量乘法
  1    整數 ALU、整數移位、IMUL（32/64-bit）、
       向量整數加法/比較（AVX2）、向量乘法
  2    Load + LEA（地址計算）
  3    Load + LEA（地址計算）
  4    Store Data
  5    整數 ALU（輕量）、向量 shuffle、向量 permute
  6    整數 ALU、分支
  7    Store Address
  8    （無 AVX-512，此 port 在 i7-10700 無實質作用）
  9    （同上）
```

i7-10700 **沒有 AVX-512**，有效執行埠主要是 0–7。關鍵競爭點：

| 指令類型 | 可用 Port | Port 數量 | 競爭強度 |
|---|---|---|---|
| `imul r64, r64` | Port 0, 1 | 2 | **高**（只有 2 個 port 能做 64-bit 乘） |
| `vpmulld ymm` | Port 0 | 1 | **極高**（向量整數乘只有 1 個 port） |
| `mulss xmm` | Port 0, 1 | 2 | 高 |
| `movaps xmm, xmm` | Port 0, 1, 5 | 3 | 中 |
| `add r64, r64` | Port 0, 1, 5, 6 | 4 | **低**（4 個 port 分散） |
| `load` | Port 2, 3 | 2 | 中（ALU port 不競爭） |
| `store` | Port 4, 7 | 2 | 中（ALU port 不競爭） |

**PortSmash 攻擊的選擇**：`imul r64` 是最好的「飽和探針」——只有兩個 port，競爭效果明確，throughput 約每 3 cycle 1 次，容易量到被壓縮的變化。

---

## 機制：Port Contention 如何轉為資訊洩漏

### 步驟拆解

**1. 攻擊者設定探針迴圈**

在 SMT sibling 上連續發出大量 `imul` 指令，讓 port 0/1 接近飽和。用 `rdtscp` 或 PMC（performance monitoring counter，效能計數器）計算每 N 次 `imul` 所花的 cycle 數。

**2. 受害者執行敏感運算**

例如 ECDSA 的純量乘法（scalar multiplication），涉及大量大數乘法（`mulq` 在 x86-64）以及條件分支或記憶體存取。

**3. 攻擊者記錄時序序列**

每個探針窗口（probe window，約 1000–10000 cycle）記錄一次「自己的吞吐減慢了多少」。當受害者也在做 `imul`，port 0/1 更繁忙，攻擊者的吞吐更低；當受害者在做 `load/store`，port 0/1 有空閒，攻擊者的吞吐恢復。

**4. 序列對比分析**

把記錄到的時序序列與已知的演算法「執行特徵圖」對齊，找出峰值和谷值對應的操作，推斷受害者的分支走向或密鑰位元。

### 為什麼這能還原 private key？

ECDSA 的 OpenSSL 實作（2018 年修補前）在 scalar multiplication 的每個 bit 迴圈裡，「0 bit」和「1 bit」走不同的程式碼路徑，產生不同量的乘法指令。攻擊者從 port 競爭的強弱序列讀出 bit 值——還原整個 ephemeral nonce（一次性隨機數），再用 lattice attack 推算 private key。

---

## 範例一：PortSmash（2019）

**論文**：Aldaya et al., "PortSmash: Exploiting Port Contention Side-Channels in a Modern CPU"，USENIX Security 2019。

**目標**：Intel Skylake/Kaby Lake 上的 OpenSSL 1.1.0h，ECDSA P-384 private key 還原。

**攻擊流程**：

1. 攻擊者 thread 綁在 SMT sibling 上，跑緊密的 `imul` 迴圈
2. 受害者 thread 是 OpenSSL TLS server，回應 ECDSA 簽名請求
3. 攻擊者收集約 10,000 個 TLS handshake 的時序樣本
4. 分析出 nonce 的 Hamming weight 分布（非均勻），輸入 lattice solver
5. 還原 P-384 private key（384-bit）

**關鍵特性**：

- 不依賴任何共享記憶體，不是 cache 攻擊
- 不需要提權，普通用戶 process 就能執行
- 對已改為 constant-time scalar multiplication 的版本無效
- Intel 確認影響所有支援 HT 的 Skylake 衍生處理器

---

## 範例二：SMoTherSpectre（2019）

**論文**：Bhattacharyya et al., "SMoTherSpectre: Exploiting Speculative Execution Through Port Contention"，ACM CCS 2019。

**核心創新**：把 port contention 當作 Spectre 的「洩漏讀出通道（readout channel）」。

傳統 Spectre v1 攻擊流程：
```
[推測執行] → 越界讀取秘密值 → 用秘密值作 cache 存取 → 攻擊者量 cache 命中/失誤
```

SMoTherSpectre 改成：
```
[推測執行] → 越界讀取秘密值 → 根據秘密值選擇 port-heavy 指令 → 攻擊者量 port 競爭強度
```

**為什麼這很重要**：

有些系統在 cache 側信道通道上加了保護（serialized `clflush`、memory tagging、L1 cache 分割）。但 port contention 完全繞過這些防禦——只要 SMT 開著，後端 port 就無法分割。這證明 transient execution 漏洞的「讀出通道」可以是任何 microarchitectural 的可量測狀態，而不限於 cache。

**技術細節**：gadget 在推測執行中根據秘密值的某個 bit，選擇跳到不同的 port-heavy 路徑（`imul` 密集 vs `add` 密集），攻擊者在 sibling 上探測哪個 port 被飽和，推斷出 bit 值。

---

## 範例三：不同探針指令的訊號強度比較

| 探針指令 | Throughput（cycle/op） | 競爭 Port 數 | 競爭時訊號強度 | 適合作探針 |
|---|---|---|---|---|
| `imul r64, r64` | 1/3 cycle | 2 | 高 | 是 |
| `vpmulld ymm` | 1 | 1 | 極高 | 是（飽和最快） |
| `mulss xmm` | 1/5 cycle | 2 | 高 | 是 |
| `movaps xmm` | 1 | 3 | 中 | 勉強 |
| `add r64, r64` | 4/cycle | 4 | 低 | 否 |
| `mov` load | 2/cycle | 2（load port） | 不和 ALU 競爭 | 否 |

結論：**選 port 少、throughput 高的指令**，讓飽和發生快且訊號清晰。`vpmulld` 理論上最強，但需要 AVX2 且 `vpmulld` 的 latency 高（10 cycle），實際上 `imul r64` 的 latency 只有 3 cycle，探針迴圈更緊密，PortSmash 論文選它是正確的。

---

## 對比：Port Contention vs Cache 側信道

| 特性 | Cache 側信道（Flush+Reload 等） | Port Contention |
|---|---|---|
| 共享資源 | L3 cache line（可跨核） | 執行埠（僅限同實體核心） |
| 需要共享記憶體 | F+R 需要；P+P 不需要 | 完全不需要 |
| 攻擊距離 | 同 LLC 即可（可跨實體核心） | **必須同一實體核心的 SMT sibling** |
| 防禦方向 | cache partition / flush 限制 / randomization | **關 SMT（HT）** |
| 頻寬/噪音 | 中等（cache miss latency 約 200+ cycle） | 低頻寬但低雜訊（直接量吞吐） |
| OS 保護手段 | KPTI、cache side-channel mitigation | NOSMT、process 調度隔離 |
| 雲端攻擊難度 | 同 NUMA node 即可 | 需要排到同一 HT 核（更困難） |
| 可作替代讀出通道 | 主要手段 | SMoTherSpectre 方案 |

**核心差異**：Port contention 的攻擊半徑極小——必須是 SMT sibling。這讓雲端攻擊更困難，但一旦攻擊者和受害者共用同一實體核心，攻擊效果非常穩定且不依賴任何記憶體共享假設。

---

## 真跑實驗：WSL2 上的 Port Contention 量測

### 準備：確認 SMT 拓樸

```bash
# 讀出 sibling 對
cat /sys/devices/system/cpu/cpu0/topology/thread_siblings_list
# i7-10700 預期：0,8（CPU 0 和 CPU 8 是同一實體核心的 sibling）

# 列出所有 sibling 對
for i in $(seq 0 7); do
  echo "CPU $i sibling: $(cat /sys/devices/system/cpu/cpu${i}/topology/thread_siblings_list)"
done
# 預期：0,8  1,9  2,10  3,11  4,12  5,13  6,14  7,15

# 用 lscpu 交叉驗證（CORE 欄位相同 = 同一實體核心）
lscpu -e=CPU,CORE,SOCKET

# 確認 SMT 狀態
cat /sys/devices/system/cpu/smt/active
# 應輸出 1（SMT 開啟）
```

**WSL2 注意**：WSL2 跑在 Hyper-V 上，`/sys/devices/system/cpu/cpu*/topology/thread_siblings_list` 在 WSL2 下**通常能讀到正確的 sibling 對**，但 `sched_setaffinity` 綁到「0,8」後，實際上是否跑在同一實體 HT 對上，取決於 Hyper-V 調度層。如果 WSL2 設定的 `processors` 數少於 16，sibling 拓樸會被截斷。

### 完整 C 程式碼

```c
// port_contention.c
// 編譯：gcc -O2 -pthread -o port_contention port_contention.c
// 執行：./port_contention
//
// 實驗設計：
//   背景攻擊者 thread 綁 CPU 0，用 imul 飽和 Port 0/1
//   主執行緒分別綁到 CPU 8（sibling）和 CPU 2（非 sibling），量 imul 吞吐

#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <pthread.h>
#include <sched.h>
#include <unistd.h>
#include <string.h>

#define SAMPLES 64

typedef struct {
    int cpu_id;
    volatile int ready;
    volatile int stop;
} attacker_arg_t;

// 序列化 TSC 讀取
static inline uint64_t rdtscp_ser(void) {
    uint32_t lo, hi;
    __asm__ volatile (
        "lfence\n\t"
        "rdtsc\n\t"
        "lfence\n\t"
        : "=a"(lo), "=d"(hi)
        :: "memory"
    );
    return ((uint64_t)hi << 32) | lo;
}

// 把當前執行緒綁到指定 CPU
static int pin_to_cpu(int cpu) {
    cpu_set_t set;
    CPU_ZERO(&set);
    CPU_SET(cpu, &set);
    return sched_setaffinity(0, sizeof(set), &set);
}

// 攻擊者執行緒：不停做 imul 飽和 Port 0/1
void *attacker_thread(void *arg) {
    attacker_arg_t *a = (attacker_arg_t *)arg;

    if (pin_to_cpu(a->cpu_id) != 0) {
        perror("attacker pin_to_cpu");
        return NULL;
    }

    __atomic_store_n(&a->ready, 1, __ATOMIC_RELEASE);

    uint64_t val = 0x123456789ABCULL;
    while (!__atomic_load_n(&a->stop, __ATOMIC_ACQUIRE)) {
        // 256 次 imul，unrolled，確保 port 0/1 持續飽和
        __asm__ volatile (
            "movq %0, %%rax\n\t"
            ".rept 256\n\t"
            "imulq %%rax, %%rax\n\t"
            ".endr\n\t"
            "movq %%rax, %0\n\t"
            : "+m"(val) :: "rax"
        );
    }
    return NULL;
}

// 量測 1024 次 imul 所花 cycle 數
static uint64_t measure_imul_cycles(void) {
    uint64_t val = 0xDEADBEEF1234ULL;
    uint64_t t0, t1;

    t0 = rdtscp_ser();
    __asm__ volatile (
        "movq %1, %%rax\n\t"
        ".rept 1024\n\t"
        "imulq %%rax, %%rax\n\t"
        ".endr\n\t"
        "movq %%rax, %0\n\t"
        : "=m"(val)
        : "m"(val)
        : "rax"
    );
    t1 = rdtscp_ser();

    return t1 - t0;
}

// 在當前 CPU 量 SAMPLES 次，輸出統計
static void measure_and_report(int victim_cpu, const char *label) {
    if (pin_to_cpu(victim_cpu) != 0) {
        printf("  [警告] 無法 pin 到 CPU %d\n", victim_cpu);
    }

    uint64_t results[SAMPLES];
    for (int i = 0; i < SAMPLES; i++) {
        results[i] = measure_imul_cycles();
    }

    uint64_t sum = 0, min_val = UINT64_MAX, max_val = 0;
    for (int i = 0; i < SAMPLES; i++) {
        sum += results[i];
        if (results[i] < min_val) min_val = results[i];
        if (results[i] > max_val) max_val = results[i];
    }

    // 排除最高/最低 8 個（12.5%）再算平均
    uint64_t sorted[SAMPLES];
    memcpy(sorted, results, sizeof(results));
    // 簡單 insertion sort（SAMPLES 只有 64 個）
    for (int i = 1; i < SAMPLES; i++) {
        uint64_t key = sorted[i];
        int j = i - 1;
        while (j >= 0 && sorted[j] > key) {
            sorted[j+1] = sorted[j];
            j--;
        }
        sorted[j+1] = key;
    }
    uint64_t trim_sum = 0;
    int trim_count = SAMPLES - 16; // 去掉最高最低各 8 個
    for (int i = 8; i < SAMPLES - 8; i++) trim_sum += sorted[i];

    printf("[%s]\n", label);
    printf("  victim CPU: %d\n", victim_cpu);
    printf("  avg=%.1f  trimmed_avg=%.1f  min=%lu  max=%lu  (cycles/1024-imul)\n",
           (double)sum / SAMPLES,
           (double)trim_sum / trim_count,
           min_val, max_val);
}

// 讀 sibling 資訊
static void print_topology(int ncpus) {
    printf("[SMT Topology]\n");
    for (int i = 0; i < ncpus && i < 8; i++) {
        char path[128], buf[64] = {0};
        snprintf(path, sizeof(path),
                 "/sys/devices/system/cpu/cpu%d/topology/thread_siblings_list", i);
        FILE *f = fopen(path, "r");
        if (f) {
            fread(buf, 1, sizeof(buf)-1, f);
            fclose(f);
            buf[strcspn(buf, "\n")] = 0;
            printf("  CPU %d siblings: %s\n", i, buf);
        } else {
            printf("  CPU %d: topology 不可讀\n", i);
        }
    }
}

int main(void) {
    int ncpus = (int)sysconf(_SC_NPROCESSORS_ONLN);
    printf("=== Port Contention SMT 實驗（i7-10700 Comet Lake）===\n");
    printf("OS 可見 CPU 數: %d\n\n", ncpus);

    print_topology(ncpus);
    printf("\n");

    // 實驗一：基準線（無攻擊者）
    printf("--- 實驗 1：基準線（無攻擊者）---\n");
    measure_and_report(0, "基準：CPU 0，無競爭");
    printf("\n");

    // 啟動攻擊者 thread（綁 CPU 0）
    attacker_arg_t att = { .cpu_id = 0, .ready = 0, .stop = 0 };
    pthread_t att_tid;
    pthread_create(&att_tid, NULL, attacker_thread, &att);

    // 等攻擊者就緒
    while (!__atomic_load_n(&att.ready, __ATOMIC_ACQUIRE))
        usleep(100);

    // 實驗二：sibling 競爭（CPU 0 攻，CPU 8 量）
    printf("--- 實驗 2：sibling 競爭（攻擊者 CPU 0，受害者 CPU 8）---\n");
    if (ncpus >= 9) {
        measure_and_report(8, "攻擊：CPU 8（sibling）");
    } else {
        printf("  [跳過] OS 可見 CPU 數 < 9，無法 pin 到 CPU 8\n");
        printf("  請確認 WSL2 設定：/etc/wsl.conf 的 processors=16\n");
    }
    printf("\n");

    // 實驗三：非 sibling 對照（CPU 0 攻，CPU 2 量）
    printf("--- 實驗 3：非 sibling 對照（攻擊者 CPU 0，受害者 CPU 2）---\n");
    if (ncpus >= 3) {
        measure_and_report(2, "對照：CPU 2（非 sibling）");
    } else {
        printf("  [跳過] CPU 數不足\n");
    }
    printf("\n");

    // 停止攻擊者
    __atomic_store_n(&att.stop, 1, __ATOMIC_RELEASE);
    pthread_join(att_tid, NULL);

    printf("=== 解讀 ===\n");
    printf("若「實驗 2 sibling」的 trimmed_avg 比「基準線」高 30%+ :\n");
    printf("  → Port contention 確認存在，SMT sibling 共用 Port 0/1\n");
    printf("若「實驗 3 非 sibling」的 trimmed_avg 接近基準線 :\n");
    printf("  → 競爭只發生在同一實體核心內，驗證 port 不跨核共享\n");
    printf("若三組結果差不多 :\n");
    printf("  → 可能：WSL2/Hyper-V 使 SMT 拓樸不透明\n");
    printf("          或 imul throughput 受其他瓶頸限制（OOO 視窗不夠深等）\n");

    return 0;
}
```

### 編譯與執行

```bash
gcc -O2 -pthread -o port_contention port_contention.c
./port_contention
```

### 預期輸出範圍

**實體機**（SMT 開，sibling 確認正確）：

```
=== Port Contention SMT 實驗（i7-10700 Comet Lake）===
OS 可見 CPU 數: 16

[SMT Topology]
  CPU 0 siblings: 0,8
  CPU 1 siblings: 1,9
  CPU 2 siblings: 2,10
  ...

--- 實驗 1：基準線（無攻擊者）---
[基準：CPU 0，無競爭]
  victim CPU: 0
  avg=3852.0  trimmed_avg=3830.0  min=3800  max=4100  (cycles/1024-imul)

--- 實驗 2：sibling 競爭（攻擊者 CPU 0，受害者 CPU 8）---
[攻擊：CPU 8（sibling）]
  victim CPU: 8
  avg=6580.0  trimmed_avg=6410.0  min=6100  max=8200  (cycles/1024-imul)
  ↑ 比基準慢約 67%（port 0/1 被攻擊者飽和）

--- 實驗 3：非 sibling 對照（攻擊者 CPU 0，受害者 CPU 2）---
[對照：CPU 2（非 sibling）]
  victim CPU: 2
  avg=3880.0  trimmed_avg=3860.0  min=3820  max=4050  (cycles/1024-imul)
  ↑ 幾乎和基準線一樣（不同實體核心不共用 port）
```

**WSL2 實際狀況**：Hyper-V 的 vCPU 到 HT 線程的映射通常 1:1，但不保證。如果 i7-10700 的 Hyper-V 讓 VM 看到完整的 16 個 vCPU 且映射不亂，實驗結果就準；若有 vCPU 重排，sibling 組的訊號可能只比基準高 10–20%（而不是預期的 50–70%）。

---

## 踩雷

### 1. 搞錯 sibling 是最常見錯誤

「CPU 0 和 CPU 1」在 i7-10700 **不是** sibling——它們是不同實體核心的第一個 HT 線程。正確的 sibling 是「CPU N 和 CPU N+8」（8 核 × 2 thread）。在不同平台：

- i7-10700（8P+0E，16 HT）：CPU N 和 CPU N+8
- Ryzen 5950X（16 核 32 HT）：CPU N 和 CPU N+16
- 某些伺服器 NUMA：多 socket，拓樸更複雜

**永遠先讀** `/sys/devices/system/cpu/cpu*/topology/thread_siblings_list` 確認，不要靠猜測。一旦搞錯，攻擊者和受害者在不同實體核心，port 完全不共享，量不到任何競爭訊號。

### 2. 單次量測雜訊太大，必須統計

Port contention 訊號受許多因素干擾：OS 調度中斷、搶佔、Turbo Boost 頻率波動、ROB 深度、前端 I-cache miss。單次 `rdtscp` 差值完全不可信——至少要跑 50–100 次，取中位數或 trimmed mean（去掉最高最低各 10%）。PortSmash 論文用了數萬次樣本做統計，從中提取 ECDSA 執行特徵。

### 3. 關 SMT 是最直接的防禦，但效能代價不小

Linux runtime 關閉 SMT：
```bash
echo off | sudo tee /sys/devices/system/cpu/smt/control
```

代價：Intel 官方測試顯示伺服器工作負載效能下降 **15–30%**，某些並行密集場景超過 30%。Google Cloud 和 AWS 在 Spectre/Meltdown 後提供了關 HT 的選項，但大多數客戶不願意接受效能損失。如果選擇不關 SMT，就必須依賴 OS 調度隔離（保證同一實體核心的兩個 HT 線程只屬於同一個安全 domain）——這在雲端多租戶環境實作複雜。

### 4. WSL2 下 /sys/devices/system/cpu/*/topology 可能不完整

WSL2 的 topology 資訊由 Hyper-V enlightenment 提供，如果 `/etc/wsl.conf` 裡設定了 `processors=4`（少於實體 CPU 數），topology 會被截斷，sibling 對不完整。確認方式：

```bash
# 確認 WSL2 能看到足夠多的 CPU
nproc
# 應輸出 16（等於 i7-10700 的 HT 線程數）

# 若 nproc < 16，修改 /etc/wsl.conf
# [wsl2]
# processors=16
# 然後重啟 WSL2：wsl --shutdown
```

另外，`sched_setaffinity` 作用在 vCPU 層，Hyper-V 不保證 vCPU 被調度到哪個 HT 線程——這是 WSL2 實驗固有的不確定性，誠實標注。

### 5. 不同微架構的 port 分配差異很大

不能把 Skylake 的 port 表套到 Zen 3 或 ARM Cortex-A。AMD Zen 的執行引擎設計不同（例如 Zen 4 的整數 IMUL 只走 pipe 2），Apple M 系列的後端更是完全不同的設計。移植實驗前必須查對應的微架構指南（Agner Fog 的 `instruction_tables.pdf` 是標準參考）。

---

## 進階方向

### 使用 PMC 取代 rdtscp

`rdtscp` 量的是 wall-clock cycle，受 Turbo Boost 和頻率波動影響。更準確的做法是讀 **PMC（performance monitoring counter）**，直接計 `UOPS_DISPATCHED_PORT.PORT_0` 或 `UOPS_DISPATCHED_PORT.PORT_1`，量攻擊者實際佔用 port 的 micro-op 數：

```bash
# 用 perf 看 port 利用率（需要 CAP_PERFMON 或 paranoid <= 1）
perf stat -e \
  'cpu/event=0xA1,umask=0x01,name=PORT_0/' \
  -e 'cpu/event=0xA1,umask=0x02,name=PORT_1/' \
  -- ./attacker_only

# 調整 perf_event_paranoid 讓非 root 可用
echo 1 | sudo tee /proc/sys/kernel/perf_event_paranoid
```

### SMoTherSpectre Gadget 設計方向

在推測執行中，根據秘密值 bit 選擇 port-heavy 或 port-light 指令序列：

```c
// 示意 gadget（不完整，需搭配 BTB poisoning）
void speculative_gadget(size_t idx, uint8_t *array, size_t array_size) {
    if (idx < array_size) {              // branch 被 predictor 猜為真
        uint8_t secret = array[idx];    // 推測讀出秘密
        if (secret & 1)
            do_imul_heavy();            // 飽和 Port 0/1
        else
            do_add_light();             // 不競爭主要 port
    }
    // 攻擊者在 sibling 量 port 0/1 競爭 → 推斷 secret 的 bit 0
}
```

完整實作需要：(1) branch predictor poisoning（讓 predictor 誤判 idx < array_size 為真），(2) 精確的探針時序，(3) 多次平均消除雜訊。實作複雜度遠高於 PortSmash，但繞過所有純 cache 防禦。

### Port Contention 作為 QoS 攻擊

Port contention 不只是「竊聽」，也可以是「惡意擾動」：攻擊者持續飽和受害者使用的 port，造成受害者計算延遲增加，形成 denial-of-service。這在雲端共租（multi-tenancy）場景是個 QoS 攻擊面，且無法透過 cache isolation 解決——即使 LLC 被完全分割，HT 線程仍然共享 port。

### 防禦進階：OS 調度 SMT 隔離

不關 SMT 但仍想防禦的方案：讓 OS 調度器保證**同一實體核心的兩個 HT 線程只屬於同一個安全 domain**。Linux 的 `CONFIG_SCHED_SMT_POLICY` 以及 KVM 的 `nosmt` 選項嘗試實現這個目標。實作難點在於：當系統負載低、某個 HT 線程空閒時，調度器很難完全避免跨 domain 的 sibling 共存。

---

## 動手練習

**練習 1：確認 SMT sibling 對**

```bash
for i in $(seq 0 15); do
  f="/sys/devices/system/cpu/cpu${i}/topology/thread_siblings_list"
  [ -f "$f" ] && echo "CPU $i: $(cat $f)"
done
lscpu -e=CPU,CORE,SOCKET
```

驗證 i7-10700 的 sibling 對是 (0,8), (1,9), …, (7,15)。

**練習 2：編譯並跑 port contention 實驗**

按上述 C 程式碼編譯，記錄三組數字，計算「競爭放大比」（sibling trimmed_avg / 基準線 trimmed_avg）。在實體機預期看到 1.5–2.0x 的放大比。

**練習 3：換不同探針指令**

把 `imul` 換成 `add r64, r64`（Port 0/1/5/6），重跑實驗，觀察 port 數量多的指令競爭訊號是否明顯更弱（預期放大比下降到 1.05–1.15x）。再把 `imul` 換成 `vpmulld ymm`（僅 Port 0，需確認 `/proc/cpuinfo` 有 `avx2`），觀察訊號是否更強。

**練習 4：模擬簡化版 PortSmash 訊號提取**

修改攻擊者 thread，每 1 ms 切換模式：奇數秒做 `imul` 密集（模擬 ECDSA scalar mul 的「1 bit」），偶數秒做 `nop` 密集（模擬「0 bit」）。受害者記錄時序序列，觀察能否從序列中看出攻擊者的 01 模式。

---

## 本章重點整理

- **Port contention** 源於 SMT 兩執行緒共用後端排程器和執行埠，是純後端的結構冒險，和記憶體或 cache 無關。
- **PortSmash（2019）**：攻擊者 thread 在 sibling 上用 `imul` 探針記錄時序序列，推斷受害者（OpenSSL ECDSA）的執行樣式，還原 private key，不依賴共享記憶體。
- **SMoTherSpectre（2019）**：把 port contention 當 Spectre 的讀出通道，繞過所有基於 cache 的防禦措施。
- **i7-10700 關鍵 port**：Port 0/1 承載整數乘法（`imul`），只有 2 個 port，競爭訊號最強；`add` 可走 4 個 port，訊號弱。
- **實驗要領**：必須確認 sibling 對（`thread_siblings_list`），多次量測取 trimmed mean，對照組要用非 sibling 核心。
- **WSL2 限制**：Hyper-V vCPU 映射不透明，實驗訊號可能弱化，需誠實標注。
- **最有效防禦**：關 SMT（HT），代價是 15–30% 效能損失；替代方案是 OS 調度隔離確保 sibling 只跑同 domain 執行緒。
- **和 cache 攻擊的根本差異**：cache 攻擊可跨實體核心（同 LLC），port contention **必須同一實體核心**，攻擊半徑更小但防禦方向完全不同。

---

## 自我檢核

1. 為什麼 `imul r64` 比 `add r64` 更適合當 port contention 探針？
2. PortSmash 需要攻擊者和受害者共享哪種資源？是否需要共享記憶體？
3. SMoTherSpectre 為什麼能繞過 cache side-channel 防禦？
4. 在 i7-10700 上，CPU 5 的 SMT sibling 是哪個 CPU？
5. 關 SMT 後，port contention 攻擊為什麼不再有效？
6. 為什麼 WSL2 下的 port contention 實驗結果可能和實體機不一致？

---

## 延伸閱讀

- Aldaya, A. C. et al., **"PortSmash: Exploiting Port Contention Side-Channels in a Modern CPU"**, USENIX Security 2019 — 原始論文，完整攻擊流程與 OpenSSL ECDSA P-384 還原細節，附 PoC 程式碼。
- Bhattacharyya, A. et al., **"SMoTherSpectre: Exploiting Speculative Execution Through Port Contention"**, ACM CCS 2019 — 把 port contention 當 Spectre 讀出通道的完整技術方案，附 gadget 設計細節。
- Intel, **"Intel HT Technology Security Advisory"** (INTEL-SA-00270) — Intel 官方的 Hyper-Threading 安全建議，含關 HT 的指引與評估框架。
- Fog, Agner, **"Instruction Tables: Lists of instruction latencies, throughputs and micro-operation breakdowns"** (agner.org/optimize) — 每一代 x86 微架構的完整 port 分配表，PortSmash 類攻擊的必備參考，定期更新。
- Intel, **"Intel 64 and IA-32 Architectures Optimization Reference Manual"**, Chapter 2（Front End, Back End, Port Assignment）— 官方 Comet Lake 後端架構說明，含 micro-op 分派規則。

---

Port contention 讓我們看到：只要兩個執行緒共用**任何**後端微架構資源，時序差異就能成為資訊洩漏通道——不需要記憶體、不需要 cache，只需要坐在同一顆核心的另一個 HT 線程上靜靜量吞吐。SMoTherSpectre 更進一步證明，當一個側信道通道被堵住，攻擊者會換另一個微架構狀態填補那個位置——每個共享的後端資源都是潛在的洩漏面。

→ [下一章](27-tlb-and-other-channels.md)
