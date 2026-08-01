# Ch 25 — 頻率/功耗側信道：Hertzbleed

> **目標**：理解 Hertzbleed（2022）為什麼能打破「constant-time code 不受計時攻擊」這個長期假設——從 DVFS（Dynamic Voltage and Frequency Scaling，動態電壓頻率縮放）的基本原理、功耗與資料的連動關係，到如何在不需要任何硬體特權的情況下，從遠端 HTTPS 計時就能恢復完整密鑰。我們也會看 SIKE（Supersingular Isogeny Key Encapsulation）是怎麼被一個純計時通道從理論安全打穿的，並且在 i7-10700（無 AVX-512、有 AVX2）上設計一個能實際跑的頻率-資料相依觀察實驗。

---

## 概念：DVFS 為什麼存在，又為什麼危險

現代 CPU 的功耗粗略可以分成兩部分：

- **靜態功耗**（漏電流）：只要通電就有，跟計算量基本無關。
- **動態功耗**（switching power）：每次邏輯閘翻轉都消耗能量，大約符合 P = α × C × V² × f，其中 α 是翻轉活躍度（activity factor），C 是等效電容，V 是電壓，f 是頻率。

DVFS 是晶片廠商為了在「省電」和「效能」之間自動平衡所做的機制：當工作負載輕，降 V 和 f 省電；當負載重、需要更多運算力，提升 f（往往連帶提升 V）。這個調整在現代 Intel CPU 上可以快到微秒等級（P-state 轉換）。

這裡的關鍵洞察是：**DVFS 的調整依據是「即時功耗需求」，而功耗與正在執行的資料相關**。如果某個 256-bit SIMD 操作因資料全為 0（Hamming weight = 0）而幾乎沒有位元翻轉，其 switching power 就遠低於資料全為 1（Hamming weight = 256）的情況。CPU 的功耗管理硬體偵測到這個差異，動態調整頻率，造成不同資料有不同的實際執行速度——即使 instruction count 完全相同。

這就是 Hertzbleed 的核心：**功耗是資料的函數，頻率是功耗的函數，時間是頻率的函數**，因此時間是資料的函數，constant-time 的假設在這條因果鏈面前直接崩潰。

---

## 直覺：從資料到時間的因果鏈

```
Secret data (key bits)
        │
        ▼
  Hamming weight of AVX/YMM operands
  (每個 YMM/ZMM 暫存器的位元翻轉數)
        │
        ▼
  Switching power (α × C × V² × f)
  ┌──────────────────────────────┐
  │  data = 0x000...0  →  低翻轉  →  低功耗  │
  │  data = 0xFFF...F  →  高翻轉  →  高功耗  │
  └──────────────────────────────┘
        │
        ▼
  DVFS 硬體 (P-state controller)
  偵測瞬間功耗超過/低於 TDP threshold
        │
        ▼
  CPU 頻率上調 / 下調 (e.g. 2.9 GHz ↔ 4.8 GHz)
        │
        ▼
  Wall-clock 執行時間改變
  (相同 cycle count，不同 ns 實際時間)
        │
        ▼
  遠端 HTTPS response timing
  (攻擊者量測 RTT，累積統計後重建 key)
        │
        ▼
  Secret key recovered
```

這條鏈的威力在最後兩步：不需要 RAPL MSR、不需要 rdtsc、不需要 root、不需要共享記憶體——只要能量測 wall-clock time（甚至 TCP RTT）就足夠。這讓 Hertzbleed 從「本地功耗攻擊」升格成「遠端計時攻擊」，威脅面積暴增。

---

## 機制：三層技術細節

### 層一：Hamming weight 與 switching power

CPU 的 CMOS 邏輯閘每次 0→1 或 1→0 轉換都消耗一份能量（0→0 和 1→1 不消耗，或消耗極少）。對一個 256-bit YMM 暫存器做操作：

- 若前後值都是 `0x000...0`：沒有翻轉，動態功耗極低。
- 若前後值從 `0x000...0` 變成 `0xFFF...F`：256 個位元全翻轉，動態功耗最高。
- 實際密碼演算法中，operand 的 Hamming weight 受 key bits 影響（例如 ECC scalar multiplication 的中間值分布不均）。

AVX-512 的效應最強，因為 512-bit ZMM 暫存器的翻轉數是 YMM 的兩倍，且許多 AVX-512 指令（如 VPCLMULQDQ、VPCMPEQQ）的功耗差異更明顯。Intel i7-10700（Comet Lake）沒有 AVX-512，但有 AVX2（256-bit YMM），效應量較小但原理相同。

### 層二：Intel Turbo Boost 與 P-state 調整

Intel 的 Turbo Boost 2.0 允許 CPU 在 TDP（Thermal Design Power，熱設計功耗）限制內短暫衝上高頻率。實際頻率選擇由 PCU（Power Control Unit，功耗控制單元）在微秒到毫秒時間粒度做決策，依據包括：

- **PKG_POWER_LIMIT**（MSR 0x610）：package 功耗上限
- **IA32_PERF_STATUS**（MSR 0x198）：當前 P-state
- 即時 thermal margin、VR（Voltage Regulator）狀態

當一段運算功耗極低（如全 0 資料的 SIMD loop），PCU 可能允許頻率上調至 Turbo 最高值；當功耗高（全 1 資料），頻率被壓回 base clock 甚至更低。兩者的時鐘週期數完全相同，但實際消耗的 ns 不同。

### 層三：遠端攻擊場景

Hertzbleed 原始論文（Moghimi 等，IEEE S&P 2023）攻擊 SIKE 實作的方式：

1. 攻擊者對目標伺服器發送精心構造的密文，讓 key decapsulation 觸發特定 Hamming weight 分布的運算。
2. 量測 HTTPS response latency，重複數千次，取中位數或均值。
3. 對不同構造的密文觀察到統計性的時間差（數十微秒量級）。
4. 透過 bit-by-bit oracle 的方式，逐步推斷出 key bits。

原始論文報告的時間差：高 Hamming weight vs 低 Hamming weight 的單次運算差異約 2–6 ns（在有 AVX-512 的 CPU 上），需要累積大量樣本才能在遠端噪音中浮現出統計信號。對 SIKE-503，完整攻擊需要約 2 × 10^7 次 oracle query，在實際網路環境下約需數日。

---

## 範例一：RAPL 能量計數器讀取（WSL2 受限，了解原理為主）

RAPL（Running Average Power Limit）是 Intel 自 Sandy Bridge 起提供的硬體能量計數器，透過 MSR（Model Specific Register）讀取。

```bash
# 先確認 msr-tools 在不在
sudo modprobe msr 2>/dev/null || true
sudo rdmsr -p 0 0x611   # PKG_ENERGY_STATUS (core 0)
sudo rdmsr -p 0 0x639   # PP0 (core energy)
sudo rdmsr -p 0 0x606   # MSR_RAPL_POWER_UNIT (能量單位)
```

**WSL2 實際情況**：這三個命令在 WSL2 下幾乎確定失敗，典型錯誤：

```
msr: open: Operation not permitted
```

即使 `sudo`，Hyper-V hypervisor 也不會 passthrough 這些 MSR，主因是：

- CVE-2020-8694（PLATYPUS 攻擊）之後，Linux 5.9+ 在 kernel 層要求 CAP_SYS_RAWIO 才能讀 RAPL MSR。
- Intel 同步更新 microcode，某些 SKU 在 2020 年後直接在硬體層限制非特權 RAPL 存取。
- WSL2 的 Linux kernel 跑在 Hyper-V VM 裡，hypervisor 可以選擇不 expose 這些 MSR，即使 root 也看不到真實值（可能全回 0 或直接 GPF）。

如果你有原生 Linux 裸機環境（非 VM）、kernel < 5.9 或已允許 RAPL 的 sysfs 路徑（`/sys/class/powercap/intel-rapl/`），可以這樣讀：

```bash
# kernel RAPL interface（不需 msr-tools，但仍需 root）
cat /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj
# 等 100ms
sleep 0.1
cat /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj
# 差值 / 0.1s = 平均功率 (μW)
```

MSR 0x606（MSR_RAPL_POWER_UNIT）的 bits[12:8] 定義 ESU（Energy Status Unit）：能量值 = counter × 2^(−ESU)，單位 Joule。Comet Lake 的 ESU 通常是 14，即 1 LSB ≈ 61 μJ。

---

## 範例二：AVX2 頻率-資料相依計時實驗（在 WSL2 i7-10700 上能跑）

這個實驗的設計思路：用 AVX2 YMM 指令對「全 0」和「全 1」資料各做大量操作，用 `rdtsc`/`rdtscp` 量測 cycle count，再換算成 wall-clock time，觀察差異。

**注意**：因為我們只量 cycle count，得到的是「在那段時間內 CPU 跑了多少 cycle」。Hertzbleed 的效應顯示在 wall-clock time（即頻率下降導致相同 cycle 耗更多 ns），所以我們要量的是 **wall-clock nanoseconds**，不是 cycle。正確做法是用 `clock_gettime(CLOCK_MONOTONIC)` 量 ns，或在固定時間窗口內計 cycle 數（cycle 少 = 頻率低）。

```c
// avx2_timing.c  — i7-10700 (AVX2, no AVX-512)
// 編譯：gcc -O2 -mavx2 -o avx2_timing avx2_timing.c
// 執行：./avx2_timing
//
// 注意：WSL2 下 DVFS 控制在 host Windows，結果可能不明顯。
// 原生 Linux 裸機效果更佳。不管結果如何，code 邏輯示範是正確的。

#define _GNU_SOURCE
#include <immintrin.h>
#include <stdint.h>
#include <stdio.h>
#include <time.h>
#include <string.h>

#define ITERATIONS 2000000

// 強制 SIMD 不被最佳化掉
volatile int sink = 0;

static inline long long now_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000000000LL + ts.tv_nsec;
}

void burn_avx2(uint8_t fill_byte, long long *elapsed_out) {
    // 準備 4 個 YMM 暫存器（256-bit 各）
    __m256i a = _mm256_set1_epi8((char)fill_byte);
    __m256i b = _mm256_set1_epi8((char)fill_byte);
    __m256i acc = _mm256_setzero_si256();

    long long t0 = now_ns();

    for (int i = 0; i < ITERATIONS; i++) {
        // XOR + ADD：會造成翻轉（0xFF XOR 0xFF = 0x00，翻轉 8n 次）
        acc = _mm256_xor_si256(acc, a);
        acc = _mm256_add_epi8(acc, b);
    }

    long long t1 = now_ns();
    *elapsed_out = t1 - t0;

    // 防最佳化消除：把 acc 的某個位元讀出來
    int tmp[8];
    _mm256_storeu_si256((__m256i*)tmp, acc);
    sink = tmp[0];
}

int main(void) {
    long long t_zero, t_ones;
    const int TRIALS = 10;
    long long sum_zero = 0, sum_ones = 0;

    printf("AVX2 頻率/功耗相依計時實驗 (i7-10700, WSL2)\n");
    printf("iterations per trial: %d\n\n", ITERATIONS);

    for (int r = 0; r < TRIALS; r++) {
        // --- 全 0 資料 ---
        burn_avx2(0x00, &t_zero);
        sum_zero += t_zero;

        // --- 全 1 資料 ---
        burn_avx2(0xFF, &t_ones);
        sum_ones += t_ones;

        printf("trial %2d: 0x00=%7lld ms  0xFF=%7lld ms  diff=%+lld ms\n",
               r+1,
               t_zero / 1000000,
               t_ones / 1000000,
               (t_ones - t_zero) / 1000000);
    }

    printf("\n平均: 0x00=%.1f ms  0xFF=%.1f ms  diff=%.1f ms\n",
           (double)sum_zero / TRIALS / 1e6,
           (double)sum_ones / TRIALS / 1e6,
           (double)(sum_ones - sum_zero) / TRIALS / 1e6);

    printf("\n解讀：\n");
    printf("  0xFF 操作（高 Hamming weight）若明顯慢於 0x00，\n");
    printf("  說明 DVFS 因功耗差異調低頻率。\n");
    printf("  WSL2 下效果可能不明顯（DVFS 由 host 控制），\n");
    printf("  原生 Linux 上通常有 5–15%% 差距。\n");

    return 0;
}
```

**預期結果（原生 Linux，Intel Core，Turbo 開啟）**：

```
trial  1: 0x00=  312 ms  0xFF=  351 ms  diff=+39 ms
trial  2: 0x00=  309 ms  0xFF=  348 ms  diff=+39 ms
...
平均: 0x00=311.4 ms  0xFF=349.7 ms  diff=38.3 ms
```

差距約 10–15%，取決於 CPU SKU 和 TDP 設定。AVX-512（如 Ice Lake / Tiger Lake）上差距可到 20–30%，因為 512-bit 操作功耗更高且 Intel 對這類指令有特殊頻率限制（AVX-512 License 機制）。

**WSL2 預期**：差距可能接近 0 或只有 1–3%，因為 DVFS 決策在 Windows host 層，WSL2 VM 看不到它；也因為 Hyper-V 給 VM 的是「虛擬 CPU 時間」而非真實頻率資訊。

---

## 範例三：SIKE 被打穿的邏輯重建

SIKE（Supersingular Isogeny Key Encapsulation）在 2022 年被 Hertzbleed 攻擊時，是 NIST PQC 第四輪候選演算法之一（後來也因 Castryck-Decru 的數學攻擊在 2022 年完全崩潰，兩個攻擊幾乎同時出現）。

SIKE 的 constant-time 實作依賴大量 isogeny 計算，中間用到 modular multiplication 和 field extension arithmetic。在 Intel 平台的參考實作中，某個核心 loop 使用 AVX-512 的 VPMULUDQ（無符號整數乘法），其功耗因 operand Hamming weight 而異。

攻擊的 oracle 設計如下（概念碼，非真實攻擊碼）：

```python
# 概念：Hertzbleed oracle 如何工作
# 攻擊者控制 ciphertext c，量測 decapsulation 時間

import time
import requests
import statistics

def time_decap(server_url, ciphertext_bytes):
    """量測一次 decapsulation 的 wall-clock time（毫秒）"""
    samples = []
    for _ in range(100):  # 多次取樣，降噪
        t0 = time.perf_counter()
        r = requests.post(server_url, data=ciphertext_bytes, timeout=5)
        t1 = time.perf_counter()
        samples.append((t1 - t0) * 1000)
    return statistics.median(samples)

def recover_key_bit(server_url, c_zero, c_one):
    """
    c_zero：構造成讓 key bit 對應的 isogeny 中間值 Hamming weight 低的密文
    c_one ：構造成讓 Hamming weight 高的密文
    時間差 > threshold → key bit = 1
    """
    t_zero = time_decap(server_url, c_zero)
    t_one  = time_decap(server_url, c_one)
    threshold = 0.05  # 50 μs，論文中約 2–6 ns × loop iterations 累積
    return (t_one - t_zero) > threshold

# 完整攻擊需對每個 key bit 各設計對應的 oracle query
# SIKE-503 key = 126 bytes → 逐 bit 恢復，約 2×10^7 次 query
```

這個 oracle 能 work 的前提：SIKE decapsulation 的 execution trace 是 constant（固定 instruction sequence），但 DVFS 使得每次的 wall-clock time 因 key bits 影響的 Hamming weight 分布而微小但一致地偏移。100 次取樣的中位數能穩定到數微秒的精度，足以區分 bit=0 和 bit=1。

---

## 對比與取捨

| 維度 | RAPL 功耗攻擊（PLATYPUS） | Hertzbleed 遠端計時 | Cache 計時攻擊（Flush+Reload）|
|------|--------------------------|--------------------|-----------------------------|
| **攻擊者位置** | 本地（讀 MSR/sysfs） | 遠端（網路計時） | 本地（共享記憶體） |
| **需要特權** | 低（舊 kernel 無需 root） | 無 | 無（共享 LLC 即可） |
| **信號強度** | 強（直接量功耗） | 弱（需大量統計） | 中（ns 等級時間差） |
| **query 數量** | 少 | 多（10^7 量級） | 中 |
| **緩解難度** | 容易（限制 RAPL 存取） | 難（需鎖頻率或禁 Turbo） | 容易（FLUSH 需要 clflush）|
| **適用場景** | 近端多租戶（同機 VM） | 遠端 TLS/HTTPS 服務 | 本地多進程 |
| **目標演算法** | AES-NI（key-dependent power） | SIKE、ECC（SIMD heavy）| AES、RSA 的 table lookup |
| **已知被修** | Linux 5.9 限制 RAPL | AVX-512 firmware workaround | clflush 原語難以移除 |

Hertzbleed 的核心優勢是**零特權要求 + 可遠端操作**，這讓它在威脅模型中的適用場景比 PLATYPUS 廣得多。代價是需要大量 oracle query，現實中對高延遲目標（跨大陸 RTT）攻擊難度上升。

---

## 踩雷集錦

**踩雷一：Constant-time ≠ 計時安全**

這是 Hertzbleed 最核心的反直覺。Constant-time 程式設計（Ch 32 會細講）的定義是：execution path 和 cycle count 不依賴 secret data。這個定義在傳統攻擊模型下是正確的安全屬性。Hertzbleed 暴露的問題是：**cycle count 固定，但 wall-clock time 不固定**，因為 CPU 頻率可變。

這個打擊之深在於：所有密碼學函式庫（OpenSSL、libsodium、NSS）都依賴 constant-time 原語，它們的正確性是對的，但「正確的 constant-time」在 DVFS 這個通道面前變成了必要但不充分的條件。

修 fix 要求的是：constant-time AND 固定頻率（或其他方式切斷 power→frequency→time 因果鏈）——兩者缺一不可。

**踩雷二：RAPL 不是攻擊的必要條件**

許多人看到「功耗側信道」就以為需要 RAPL MSR。Hertzbleed 的精妙之處在於：**它利用 DVFS 把功耗資訊轉換成計時資訊，攻擊者從不直接量功耗**。RAPL 是另一個（更強但需要更高特權的）通道（PLATYPUS 用的），Hertzbleed 用的是 wall-clock time，這兩個通道是獨立的。

把 Hertzbleed 當成「需要 RAPL 的攻擊」是一個嚴重的分類錯誤，會導致緩解策略失效（限制 RAPL 根本不影響 Hertzbleed）。

**踩雷三：AVX-512 效應最強，但不是唯一**

Intel 在 firmware 層的主要緩解針對 AVX-512（因為 AVX-512 操作頻率降幅最明顯，且 Intel 已有 AVX-512 frequency license 機制），但 AVX2（256-bit）也有類似效應，只是差距較小。未來如果攻擊目標使用的是 AVX2 路徑的演算法（例如沒有 AVX-512 的平台），firmware workaround 不能完全覆蓋。

同樣，AMD CPU 也有 DVFS，雖然原始論文主要針對 Intel（頻率調整幅度更大），但 AMD 平台並非完全免疫。

**踩雷四：關 Turbo Boost 不等於關 DVFS**

常見誤解：用 `echo 1 > /sys/devices/system/cpu/intel_pstate/no_turbo` 關掉 Turbo Boost 就能防 Hertzbleed。

這個緩解**部分有效但不完整**：關 Turbo 會消除頻率上衝，但 CPU 仍可能在 base clock 以下做 P-state 轉換（尤其在電源限制場景）。完整的緩解需要鎖定到固定的 P-state（e.g., 把 min_perf_pct = max_perf_pct = 固定值），確保頻率不會因工作負載功耗而動態變動。這會造成約 5–15% 的效能損失（視 workload 而定），這也是為什麼雲服務商不一定願意做。

**踩雷五：統計顯著性陷阱**

在 WSL2 或噪音環境下跑上面的實驗，看到 0xFF 和 0x00 的差距不顯著，會誤以為「效應不存在」。正確解讀是：WSL2 的 DVFS 決策在 host 層，VM 看到的 CPU 時間是 normalized 的，WSL2 不是觀察這個效應的適合環境。效應是真實的，只是需要正確的觀察工具（原生 Linux + 裸機 + root RAPL 確認，或 AVX-512 + 更大的 iteration count）。

---

## 進階：再往深一層

### Hertzbleed 的完整攻擊面與修補史

**2022-06-14**：Hertzbleed 論文正式公開（Moghimi、Lipp、Gruss 等）。論文同步附上了對 SIKE 的完整攻擊實作，NIST 同日宣布從 PQC 標準化競賽撤回 SIKE（雖然數學攻擊 Castryck-Decru 才是讓 SIKE 完全崩潰的主因）。

**Intel 的回應**（INTEL-SA-00698）：

1. 建議密碼學實作者在安全敏感場景下透過 software 固定 P-state（不依賴硬體 Turbo）。
2. 在部分 CPU 上透過 microcode 更新修改 AVX-512 的 frequency license 行為，減少資料相依的頻率波動。
3. 注意：Intel 的修補是「緩解」而非「消除」，因為完整消除 DVFS 的資料相依性需要硬體重新設計。

**AMD 的處理**：AMD 確認其 CPU 也受類似機制影響，但幅度較小（頻率調整顆粒度與策略不同），建議同樣在密碼學 code 中固定頻率。

### AVX-512 的頻率降幅機制

Intel 從 Skylake-X 起為 AVX-512 設計了一套特殊的 frequency license 機制：執行 AVX-512 指令時，CPU 進入 "license level 2"，允許的最大頻率比 AVX2（level 1）和純整數（level 0）都低，原因是 512-bit SIMD 單元的功耗太高，在高頻率下超過 TDP。

這個機制造成的延遲（transition latency）本來是效能問題，結果成了 Hertzbleed 的主要信號來源：因為資料 Hamming weight 的不同，operand 對 AVX-512 FU 的功耗需求不同，PCU 在 license level 2 內做更細粒度的頻率調整，洩漏資訊。

### PLATYPUS 與 Hertzbleed 的關係

PLATYPUS（Lipp 等，2021）是利用 RAPL 直接量功耗的攻擊：不需要高特權（kernel 5.9 前可以 user-space 讀），直接從能量計數器重建 AES key。CVE-2020-8694 的修補是限制 RAPL 存取，代價是讓 DRAM 和 CPU 效能監控工具需要 root。

Hertzbleed 在精神上是 PLATYPUS 的「繞過版」：不用讀功耗，只讀時間，而 DVFS 幫我們把功耗轉換成計時信號。兩者的緩解策略因此不能互相取代——關掉 RAPL 不防 Hertzbleed，鎖 P-state 也不防 PLATYPUS（除非功耗徹底被隔離）。

### 頻率側信道的學術演進線

```
2007  Remote Timing Attack on AES (Bernstein)
      └─ cache timing via network RTT
2011  FLUSH+RELOAD 原型工作
2018  Spectre/Meltdown
      └─ speculation side channel
2020  PLATYPUS (RAPL 功耗側信道)
2021  CVE-2020-8694 修補 RAPL 存取
2022  Hertzbleed (DVFS 頻率側信道, 打破 constant-time)
      └─ 繞過 RAPL 修補，遠端可利用
2023  Hertzbleed 論文正式 IEEE S&P 發表
      └─ 分析 SIKE 完整攻擊
      └─ Intel firmware workaround 部分緩解
```

這條線告訴我們：每次「安全假設」被修補（限制 RAPL），攻擊者就找到下一個通道（DVFS 計時）。微架構的複雜性確保了這個貓鼠遊戲還沒結束。

---

## 動手練習

### 練習 A：AVX2 實驗（WSL2 可跑）

把上面 `avx2_timing.c` 的範例編譯並執行。建議修改：

1. 把 `ITERATIONS` 改成 5,000,000，觀察 trial 間的變異係數（CV）是否在 WSL2 下更高（VM 時間片分配不穩定）。
2. 加入第三組測試：`fill_byte = 0xAA`（01010101 pattern），理論上 Hamming weight 介於 0x00 和 0xFF 之間，計時應該也居中。
3. 用 `taskset -c 0 ./avx2_timing` 把 process 綁到固定核心，看是否減少變異。

記錄結果並分析：WSL2 的差距是否明顯？如果不明顯，寫一段說明解釋原因。

### 練習 B：RAPL 嘗試（了解限制）

```bash
# 確認 kernel 版本
uname -r

# 嘗試讀 RAPL（可能失敗，記錄錯誤訊息）
sudo rdmsr -p 0 0x606   # MSR_RAPL_POWER_UNIT
sudo rdmsr -p 0 0x611   # PKG_ENERGY_STATUS

# 嘗試 powercap sysfs（較新的 interface）
ls /sys/class/powercap/ 2>/dev/null || echo "powercap not available in WSL2"
cat /sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj 2>/dev/null

# 記錄具體錯誤，解釋是 kernel 限制還是 hypervisor 攔截
```

把你得到的錯誤訊息貼出來，並寫一段分析：為什麼在這個環境下這條通道不可用？在什麼環境下它才可用？

### 練習 C：威脅建模

設計一個場景：你是一家提供密碼學 API 服務的公司，API endpoint 是 HTTPS POST `/v1/sign`，後端用 ECDSA（P-256）做簽名，使用 AVX2 最佳化的 modular multiplication。

回答以下問題：

1. Hertzbleed 在這個場景下的攻擊可行性如何？（需要多少 query？每個 query 需要什麼控制能力？）
2. 你會採取哪些緩解措施？（至少列 3 個，說明各自的效能代價）
3. 鎖定 CPU P-state 的 sysfs 操作是什麼？如何驗證頻率確實被鎖定？

```bash
# 提示：查看當前 P-state 設定
cat /sys/devices/system/cpu/intel_pstate/min_perf_pct
cat /sys/devices/system/cpu/intel_pstate/max_perf_pct
cat /sys/devices/system/cpu/intel_pstate/no_turbo

# 查看實際當前頻率（每秒更新）
watch -n1 "cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq"
```

---

## 本章重點整理

- **DVFS 是 Hertzbleed 的根因**：CPU 為了在 TDP 限制內最大化效能，根據即時功耗動態調整頻率，這個機制使 wall-clock time 成為功耗的函數。

- **功耗是資料的函數**：SIMD 操作（AVX2/AVX-512）的 switching power 取決於 operand 的 Hamming weight；key-dependent 的中間值因此洩漏到功耗，再洩漏到頻率，再洩漏到時間。

- **Constant-time 是必要條件，不是充分條件**：Hertzbleed 打破的正是這個長期假設。一個 instruction-level constant-time 的實作，在 DVFS 通道面前仍然是計時可觀察的。

- **遠端計時即可，不需要特殊硬體權限**：RAPL 是另一個通道（PLATYPUS），Hertzbleed 用的是 wall-clock latency，任何能量測 HTTPS response time 的攻擊者都能利用。

- **SIKE 是具體受害者**：一個數學上設計成 constant-time 的 PQC 候選，被 Hertzbleed 從遠端計時破解，是「理論安全但微架構讓它洩漏」的教科書案例。

- **緩解有真實代價**：關 Turbo Boost 或鎖 P-state 能大幅削弱攻擊，但帶來 5–15% 效能損失；雲服務商在共享硬體上做全局鎖頻很困難，這讓 Hertzbleed 的修復在生產環境中至今仍不完整。

- **AVX-512 效應最強但不獨有**：Intel firmware workaround 主要針對 AVX-512 license 機制，AVX2 和其他平台的殘留效應仍然存在。

---

## 自我檢核

1. 解釋為什麼「Hamming weight 高的資料 = 功耗高」，這跟 CMOS 邏輯閘的哪個物理特性相關？
2. DVFS 的頻率調整顆粒度（時間尺度）是多少？為什麼這個顆粒度對攻擊的 oracle query 數量有影響？
3. Hertzbleed 和 PLATYPUS 的信號通道有何本質差異？為什麼 CVE-2020-8694 的修補不能防 Hertzbleed？
4. 如果你想在原生 Linux 裸機上驗證 AVX2 的功耗相依計時效應，除了 wall-clock time 之外，還有哪個 OS-level 工具可以觀察頻率變化？（提示：`turbostat`）
5. 鎖定 P-state 為固定值能消除 Hertzbleed 的根因嗎？還有哪些情況下頻率仍可能變動（thermal throttling、power capping）？
6. SIKE 在 Hertzbleed 發現前已有 constant-time 審計，為什麼審計沒有抓到這個問題？這對密碼學函式庫審計流程有什麼啟示？

---

## 延伸閱讀

**原始論文**

- Moghimi, D., Lipp, M., Gruss, D. et al. **"Hertzbleed: Turning Power Side-Channel Attacks Into Remote Timing Attacks on x86."** IEEE Symposium on Security and Privacy (S&P) 2023. 這是必讀的原始論文，第 4–6 節詳述攻擊模型，第 7 節說明 SIKE 端到端攻擊，第 8 節分析緩解。https://www.hertzbleed.com/hertzbleed.pdf

- Lipp, M. et al. **"PLATYPUS: Software-based Power Side-Channel Attacks on x86."** IEEE Symposium on Security and Privacy (S&P) 2021. Hertzbleed 的前驅：直接用 RAPL 量功耗。理解 PLATYPUS 和 Hertzbleed 的差異是理解「通道選擇」的關鍵。https://platypusattack.com/platypus.pdf

- Castryck, W. & Decru, T. **"An Efficient Key Recovery Attack on SIDH."** EUROCRYPT 2023. 和 Hertzbleed 幾乎同時出現的數學攻擊，徹底摧毀 SIKE 的密碼學基礎（不依賴微架構）。兩個攻擊合讀，理解一個演算法同時死於數學和實作兩個面向。https://eprint.iacr.org/2022/975

**Intel 官方資料**

- Intel Security Advisory INTEL-SA-00698: **"Hertzbleed Advisory."** Intel 對 Hertzbleed 的官方回應，列出受影響 CPU 型號、推薦的 software workaround、和 microcode 更新狀態。https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/advisory-guidance/hertzbleed.html

**背景閱讀**

- Kocher, P. **"Timing Attacks on Implementations of Diffie-Hellman, RSA, DSS, and Other Systems."** CRYPTO 1996. 計時攻擊的起源論文，Hertzbleed 是它在 2022 年的「DVFS 版本」，對照閱讀可以看到這個攻擊面 26 年來的演進。

- Gruss, D. et al. **"KASLR is Dead: Long Live KASLR."** ESSoS 2017. TU Graz 在微架構側信道領域的代表作之一，Hertzbleed 的部分作者來自同一個團隊，了解他們的工作方法有助於理解 Hertzbleed 的研究設計思路。

---

這一章把功耗側信道從「需要特殊硬體的本地攻擊」推進到「遠端計時就能重建密鑰」的新威脅層級。Hertzbleed 最讓人不安的地方不只是它能攻擊什麼，而是它打破了一個已經被廣泛接受為「正確做法」的安全假設——這種假設破碎意味著已有的工具（constant-time auditing、timing test suite）都不足以確保安全。下一章我們轉向另一個 SMT 帶來的側信道：execution port contention，這次攻擊的不是功耗，而是 CPU 裡的執行資源競爭。

→ [下一章](26-port-contention-smt.md)
