# Ch 30 — 隔離類防禦

> **目標**：深挖「不共享」這條防禦路線——當兩個不相信任的實體無法透過微架構狀態互相觀測時，側信道攻擊在物理層面就失去了通道。本章逐一解剖 KPTI/KAISER、Core scheduling、關 SMT、Intel CAT cache partitioning、page coloring，理解每個機制「不讓誰共享什麼」、代價多大、漏了什麼。真跑：讀本機 KPTI 狀態、示範關 SMT 的 sysfs 控制與效果。

---

## 隔離的直覺：攻擊需要通道，通道需要共享

所有微架構側信道攻擊的結構可以濃縮成三步：

```
1. 受害者的行為 → 影響某個 CPU 物理狀態（S）
2. 攻擊者觀測 S → 量出 S 的差異
3. 攻擊者推算 → 從 S 的差異推出秘密
```

這三步裡，「影響」和「觀測」必須作用在**同一個 S**。受害者讓 cache 裡有某條 line，攻擊者計時讀取同一條 line；受害者執行指令影響了 port contention 計數器，攻擊者量出自己在同一個 port 上的延遲。

**通道的存在 = 共享**。只要共享存在，就可能有洩漏。

隔離類防禦的邏輯因此非常直接：**把受害者和攻擊者放到不共享的微架構資源上**。不共享 cache（CAT 分區、物理隔離）、不共享 page table（KPTI）、不共享物理核心（關 SMT、Core scheduling）——側信道通道的物理基礎就不復存在。

這個思路和「堵住特定攻擊」完全不同：你不需要知道攻擊者用什麼具體手段，只要通道本身消失，任何使用這個通道的攻擊都失效。代價是，你可能把有用的共享一起破壞掉（效能），或者你隔不乾淨（殘餘風險）。

---

## KPTI / KAISER：把 kernel 頁從使用者 page table 移除

### 它在隔離什麼

在沒有 KPTI 的系統上，使用者程式和 kernel 共用同一張 page table。使用者頁和 kernel 頁都映射在裡面——kernel 頁只是有 supervisor-only 標記（普通使用者存取會觸發 fault），但映射本身存在。

這個「映射存在」是問題：
- `prefetcht*` 指令在執行時會走 TLB 查映射，即使後來 fault。有映射就 TLB hit，TLB miss 就沒有。計時這個差異，就能從使用者空間推算「這個核心位址有沒有被 kernel 用」——KASLR 破解（Ch 28）。
- Meltdown（Ch 18）的推測執行路徑：CPU 在 fault 被 retire 之前，可以推測性地讀出 kernel 頁的資料，再用 cache 側信道把它洩漏出去。前提是 kernel 頁在當前 page table 裡。

**KPTI 的做法**：維護兩組 page table：

```
使用者模式下的 page table（「shadow」page table）：
  ├── 所有使用者頁的映射（正常）
  ├── kernel 頁：只留「進入 kernel 需要的最小部分」
  │   （trampoline 程式碼，讓 syscall/中斷 能切換到完整 kernel page table）
  └── 其餘 kernel 頁：完全移除

kernel 模式下的 page table（「full」page table）：
  ├── 所有使用者頁的映射（正常）
  └── 所有 kernel 頁的映射（完整）
```

使用者程式在跑的時候，page table 裡根本沒有 kernel 的映射。prefetch 打核心位址：TLB miss，計時數字對任何核心位址都一樣，prefetch-based KASLR 破解失效。Meltdown 的推測路徑：物理上 page table 不包含 kernel 頁，即使 CPU 試圖推測執行也讀不到任何東西，Meltdown 失效。

### 本機狀態

本機 sysfs 輸出（實際跑的結果）：

```
meltdown: Not affected
```

`Not affected` 表示 i7-10700（Comet Lake，2020 年）的硬體已修復 Meltdown 的根本成因——CPU 在 retire 發現 page fault 之前，不再讓推測路徑的讀取進入 cache。KPTI 在這台機器上仍然啟用，作為額外的縱深防禦。

確認 KPTI 是否啟用（kernel 的設定層面）：

```bash
# 方法一：/sys/kernel/debug（需要 root 或 debugfs 掛載）
cat /sys/kernel/debug/x86/pti_enabled 2>/dev/null || echo "debug fs 不可用"

# 方法二：查 kernel cmdline，找 nopti（有就是關掉了）
cat /proc/cmdline | grep -o 'nopti' || echo "nopti 未設定（KPTI 啟用）"
# 本機輸出：nopti 未設定（KPTI 啟用）

# 方法三：dmesg（需 root）
sudo dmesg | grep -i "page table isolation" | head -3

# 方法四：看 spectre_v2 的 PBRSB 注記——KPTI 間接依存
cat /sys/devices/system/cpu/vulnerabilities/meltdown
# 本機：Not affected
```

### PCID——TLB flush 的代價緩解

KPTI 最大的效能代價來自 **TLB flush**：每次切換 page table（使用者→kernel，kernel→使用者），CPU 必須讓整個 TLB 失效，因為兩組 page table 裡的映射不同。TLB flush 很貴——x86 上一次全域 flush 需要 100–500 cycles，而且後續所有記憶體存取都要重走 page table walk，直到 TLB 被重新填滿。

**PCID（Process-Context Identifiers）** 是解法：CPU 在每條 TLB entry 上加一個 12-bit 的 context ID，切換 page table 時只要換一個 PCID 值，而不用讓整個 TLB 失效。不同 PCID 的 entry 自動相互不干擾，TLB 可以同時快取多個 page table 的內容。

KPTI + PCID 的組合：使用者 page table 和 kernel page table 各有不同的 PCID，切換時不 flush TLB，只是換 PCID——只有當 page table 本身被修改（如行程切換）才 flush 對應 PCID 的 entry。效能代價從「每次 syscall 都 flush TLB」降到「行程切換時才 flush」。

本機 CPU 支援 PCID（Comet Lake 有 PCID 硬體支援），所以 KPTI 的效能代價比舊 CPU 小得多。

### 效能代價量化

KPTI 對不同工作負載的衝擊差異極大：

```
工作負載類型                 syscall 頻率    KPTI 效能衝擊
──────────────────────────────────────────────────────
網路服務（nginx/Redis）     非常高          10–30%
資料庫（PostgreSQL）        高              8–20%
一般 Web 應用               中              3–8%
科學計算（CPU-bound）       幾乎沒有        < 1%
本機桌面應用                低              1–3%
```

「syscall 密集」才是 KPTI 代價的放大器：每次 syscall = 一次使用者→kernel 頁表切換（+ 回來一次），PCID 減輕但不消除代價。計算密集型工作負載幾乎不 syscall，KPTI 沒什麼影響。

**WSL2 上的情況**：WSL2 本身是 Hyper-V 上的一個輕量 VM，page table 切換的代價在 hypervisor 層面被進一步放大。但 i7-10700 已硬體修復 Meltdown，KPTI 的完整 flush 路徑不再每次 syscall 都執行，代價在本機相對輕微。

---

## 關 SMT：切斷跨 Hardware Thread 的所有共享

### SMT 共享了什麼、為什麼危險

Simultaneous Multi-Threading（SMT）——Intel 稱之為 Hyper-Threading——讓一個物理核心同時跑兩個執行緒（hardware thread）。兩條執行緒共用同一個物理核心的：

```
共享資源                    導致的攻擊
────────────────────────────────────────────────────
L1 指令快取                 指令 cache 側信道
L1 資料快取（L1D）          L1TF/Foreshadow（推測讀跨 thread 的 L1 資料）
執行單元（Port）            Port contention 側信道（Ch 26）
Load/Store buffer           MDS/MFBDS（跨 thread 讀 buffer 殘餘）
分支預測器（BTB/PHT）       Spectre-v2 BTI（跨 thread 污染 BTB）
RSB（Return Stack Buffer）  Spectre-RSB（跨 thread 的 RSB 操控）
```

當兩條 hardware thread 分屬不同信任域——比如 thread 0 跑 VM guest A，thread 1 跑 VM guest B——上述所有共享資源都成為側信道通道。關 SMT 把這些通道全部一次切斷。

### 本機真實操作

```bash
# 查目前狀態
cat /sys/devices/system/cpu/smt/control   # → on
cat /sys/devices/system/cpu/smt/active    # → 1（1 = SMT 啟用中）
nproc                                      # → 16（8 物理核 × 2 HT = 16 邏輯核）

# 關掉 SMT（需要 root）
echo off | sudo tee /sys/devices/system/cpu/smt/control
# 驗證
cat /sys/devices/system/cpu/smt/control   # → off
nproc                                      # → 8（只剩 8 個物理核心）

# 恢復 SMT
echo on | sudo tee /sys/devices/system/cpu/smt/control
nproc                                      # → 16
```

**本機確認**：上述指令在本機 WSL2 上真跑成功。`echo off` 後 nproc 從 16 降到 8；`echo on` 後恢復 16。這個控制在 WSL2 的 Hyper-V 環境下有效（注：在某些 WSL2 版本或 Hyper-V 設定下可能需要在 host 端操作）。

### 效能代價

關 SMT 最暴力，也最容易量化代價：

```
nproc 從 16 → 8（本機）
多執行緒工作負載：效能腰斬（因為可用核心數減半）
單執行緒工作負載：沒有影響
```

關 SMT 在雲端生產環境「在某些高安全等級場景被啟用，比如隔離持卡人資料的 PCI-DSS 系統」——但更常見的選擇是 Core scheduling（下一節），因為 50% 的代價太高。

### 什麼時候真的需要關 SMT

- **MDS 未修復的舊 CPU + 高安全需求**：MDS 攻擊（RIDL、Fallout）只在 SMT 環境下跨 thread 洩漏才最嚴重。本機 `mds: Not affected`（硬體已修），不必關。
- **L1TF/Foreshadow 風險**：L1TF 讓跨 thread 的 L1 資料洩漏成為可能。本機 `l1tf: Not affected`，不必關。
- **Port contention 側信道（Ch 26）**：在共用物理核心的情況下，攻擊者可以用執行單元爭用洩漏受害者的操作類型。對高安全 cloud tenant 有意義。
- **全部都不確定 + 想要最乾淨的隔離**：付出效能代價換「知道 SMT 共享的所有通道都被關掉」的確定性。

---

## Core Scheduling：SMT 的精細化替代

### 概念

Core scheduling 不是「完全不用 SMT」，而是「讓 SMT 兩條 thread 同時跑的只有互相信任的任務」。

具體做法：OS 排程器在決定把哪個 task 放到哪個邏輯 CPU 時，多一個約束——**一個物理核心的兩條 hardware thread 必須同時跑「同一個 core scheduling group」的 task**（或者其中一條 idle）。

VM hypervisor 把每個 VM 的 vCPU 分到不同的 core scheduling group：VM A 的 vCPU 只能和 VM A 的其他 vCPU 共用物理核心，絕對不和 VM B 的 vCPU 同時在同一個物理核心的兩條 thread 上跑。

```
物理核心 0                      無 Core Scheduling（風險）
  HW Thread 0: VM-A vCPU-1     ← SMT 共享 L1D、執行單元
  HW Thread 1: VM-B vCPU-1     ← VM-A 和 VM-B 可互相觀測

物理核心 0                      有 Core Scheduling（安全）
  HW Thread 0: VM-A vCPU-1     ← 同一個 scheduling group
  HW Thread 1: VM-A vCPU-2     ← VM-A 的 thread 互信，共用 OK
```

### 代價 vs 關 SMT

Core scheduling 的效能代價比關 SMT 小，但不是免費：

- **排程靈活性降低**：排程器原本可以把任意 task 放到任意邏輯 CPU，現在必須同時考慮「這個物理核心的另一條 thread 上是誰、能不能和他共用」。排程器複雜度上升，某些工作負載下 CPU 使用率降低 5–15%。
- **Group 跨 NUMA 的問題**：大 VM 的 vCPU 可能需要跨多個 NUMA 節點，Core scheduling group 的約束會導致 NUMA-unfriendly 的排程決策，記憶體存取延遲上升。
- **不完整的隔離**：Core scheduling 隔離了 SMT 的 HW thread 共享，但**不隔離 LLC（L3）**——兩個不同 VM 的核心仍然共用同一個 L3 cache，Prime+Probe、Flush+Reload 等 LLC 側信道仍然可能。Core scheduling 解決 L1D/buffer 問題，不解決 LLC 問題。

### Linux 的 Core Scheduling 支援

Linux 5.14+ 加入了 core scheduling 支援（`CONFIG_SCHED_CORE`）。使用者空間透過 `prctl(PR_SCHED_CORE, ...)` 把 task 分組：

```c
/* 把當前 task 分到一個新的 core scheduling group */
prctl(PR_SCHED_CORE, PR_SCHED_CORE_CREATE, 0, PIDTYPE_PID, 0);

/* 把某個 task 加入現有 group（用 cookie 識別） */
prctl(PR_SCHED_CORE, PR_SCHED_CORE_SHARE_TO, target_pid, PIDTYPE_PID, 0);
```

Cloud hypervisor（KVM、Xen）在建立 VM 時把同一個 VM 的所有 vCPU 設到同一個 core scheduling group，自動達成隔離。

---

## Intel CAT：Cache Allocation Technology

### 它在隔離什麼

CAT（Cache Allocation Technology）是 Intel RDT（Resource Director Technology）的一部分，讓軟體控制每個行程或 VM 能使用 LLC（L3 cache）的哪些「way」。

理解它需要先知道 set-associative cache 的「way」：LLC 通常是 N-way associative（如 16-way），一個 cache set 裡有 N 條 line 的位置。CAT 讓你決定「這個 task 的存取只能用 way 0–7，那個 task 只能用 way 8–15」——兩個 task 的 cache 完全在物理上不重疊，LLC 側信道（Flush+Reload、Prime+Probe）在分區之間失效。

```
LLC（假設 16-way）
  Way:   0  1  2  3  4  5  6  7  8  9  10 11 12 13 14 15
  VM-A:  █  █  █  █  █  █  █  █  ░  ░  ░  ░  ░  ░  ░  ░  （way 0–7）
  VM-B:  ░  ░  ░  ░  ░  ░  ░  ░  █  █  █  █  █  █  █  █  （way 8–15）

VM-A 的 cache 存取不會佔用 VM-B 的 way，反之亦然
→ Flush+Reload、Prime+Probe 跨 VM 失效
```

### 本機狀態

```bash
# 查 Intel RDT/CAT 的 resctrl 介面
ls /sys/fs/resctrl/ 2>/dev/null || echo "no resctrl"
# 本機輸出：no resctrl
```

本機 i7-10700 不支援 CAT（Intel RDT 通常只在 Xeon/服務器級 CPU 上才有）。桌面級 Core i7 沒有這個功能。這是「Client 級 CPU 和 Server 級 CPU 的防禦能力差距」的直接體現：雲端廠商用 Xeon 跑 VM 才能用 CAT 做 LLC 分區，你在家的 i7 沒有這個選項。

**CAT 的概念示範**（在有 RDT 的 Xeon 上）：

```bash
# 掛載 resctrl（需要 root）
mount -t resctrl resctrl /sys/fs/resctrl

# 查目前的 cache schema
cat /sys/fs/resctrl/info/L3/cbm_mask   # → 0xffff（16 bits = 16 ways）
cat /sys/fs/resctrl/info/L3/num_closids # → 可用的隔離組數量

# 建立一個新的 resource group，分配給 VM-A
mkdir /sys/fs/resctrl/vm_a
echo "L3:0=00ff"  > /sys/fs/resctrl/vm_a/schemata  # way 0–7
echo "$(pgrep -d, qemu-kvm)" > /sys/fs/resctrl/vm_a/tasks  # 把 VM-A 的 QEMU 行程加入

# 建立另一個 group 給 VM-B
mkdir /sys/fs/resctrl/vm_b
echo "L3:0=ff00"  > /sys/fs/resctrl/vm_b/schemata  # way 8–15
echo "$(pgrep -d, -f 'qemu.*vm_b')" > /sys/fs/resctrl/vm_b/tasks
```

### CAT 的限制

- **粒度限制**：LLC 的 way 數有限（典型 12–20 way），每個 way 通常是整個 LLC 大小的 1/N。以 32 MiB 16-way LLC 為例，每個 way 2 MiB——你只能以 2 MiB 為最小單位做分區，不能更細。
- **不擋 L1/L2**：CAT 只管 LLC。L1D/L2 cache 是每個核心私有的，不受 CAT 控制。Core scheduling 的跨 thread L1 隔離才擋 L1。
- **只有 Xeon 有**：消費級 CPU 無法使用，雲端場景才實際部署。

---

## Page Coloring：OS 層的 cache 分區替代方案

### 概念

Page coloring 是一個更古老的 OS 技術，不需要硬體支援。它利用 cache 的 set 映射規律：一個物理位址對應的 cache set 由位址的某些 bit 決定（通常是 PA[6:log2(sets)+6]）。如果能控制物理記憶體的分配，讓不同 task 的頁只落在不同的 cache set 上，就能在軟體層實現「不共享 cache set」的效果。

OS page allocator 把物理頁按「cache color」分組（相同 color = 映射到相同的 cache set 範圍），然後把不同 task 分配到不同 color 的頁。最終效果：不同 task 的資料在 cache 裡不佔用相同的 set，Prime+Probe 跨 task 的攻擊需要繞過 set 隔離才能成功。

### 現實限制

Page coloring 在研究環境有效（有論文），但生產系統幾乎不用，原因：

1. **必須控制物理記憶體分配**：每次 page fault 的 page 都要按 color 選，OS 的分配器必須追蹤每種 color 的可用頁——這讓記憶體碎片化問題大幅惡化。
2. **Huge page 破壞 coloring**：使用 2 MiB huge page 後，一個 huge page 自然涵蓋所有 cache color，coloring 失去意義；但現代系統大量依賴 huge page 降低 TLB 壓力。
3. **LLC 的複雜映射**：現代 Intel LLC 的 set 映射函數不是簡單的位址 bit 擷取，而是 Intel 沒有公開的 hash 函數（DRAMA 論文才逆推出來）。軟體不容易計算 color。

**結論**：page coloring 是「理論上可行，實際上太複雜且代價太高」的方案。CAT 是更乾淨的硬體替代。

---

## 對比與取捨

| 防禦機制 | 隔離什麼 | 代價 | 限制 | 適用場景 |
|---------|---------|------|------|---------|
| KPTI | kernel/user page table 分離 | syscall 密集 5–30% | PCID 可緩解；舊 CPU 更貴 | 所有環境；Meltdown 防禦；KASLR prefetch 防禦 |
| 關 SMT | 所有 SMT 共享資源（L1/buffer/port/BTB） | ~50% 多核工作負載 | 效能代價最高；L2/L3 仍共享 | 高安全 cloud；MDS 未修的舊 CPU |
| Core scheduling | SMT HW thread 間的信任隔離 | 5–15% 排程靈活性 | 不擋 LLC；實作複雜；kernel 5.14+ | Cloud 多租戶；SMT 不能關但需 L1/buffer 隔離 |
| Intel CAT | LLC cache way 分區 | LLC 使用率降低 | 只有 Xeon；粒度 per-way（2 MiB+）；不擋 L1/L2 | Cloud Xeon 主機；高安全 LLC 隔離 |
| Page coloring | LLC cache set 分區（軟體） | 記憶體碎片化嚴重 | 複雜；Huge page 破壞；實際部署少 | 研究/學術；特定嵌入式場景 |
| 物理 CPU 隔離（dedicated host） | 所有快取和微架構資源 | 成本最高（整台機器給一個 tenant） | 租金高；不現實 for 中小 tenant | 最高安全等級 cloud；金融/政府 |

---

## 踩雷集錦

1. **認為 KPTI 擋住了所有 Meltdown 變體**：KPTI 擋的是「kernel 頁從使用者 page table 移除」這一招對應的洩漏路徑。但 Meltdown-type 攻擊有多個變體——L1TF（Foreshadow）是 Meltdown 的 L1D 快取版本，攻擊的是 L1D 的 tag 而非 page table 映射，KPTI 對它沒有直接效果，L1D flush on VM exit 才是正確緩解。不同的 Meltdown 變體需要不同的防禦。

2. **以為關 SMT 等同於「沒有跨行程洩漏」**：關 SMT 只消除了 **SMT 這類共享**導致的通道。L3 cache 在不同物理核心之間仍然共享——兩個行程分別跑在 CPU 0 和 CPU 4 上（不同物理核心），仍然共享 L3，Flush+Reload 和 Prime+Probe 跨行程攻擊不需要 SMT，關 SMT 對它們沒有效果。把「關 SMT = 所有側信道都沒了」當成結論是完全錯的。

3. **不知道 Core scheduling 不隔離 L3**：Core scheduling 確保同一個物理核心的兩條 HW thread 屬於同一個信任域，解決了 L1D/buffer/port 的跨 thread 洩漏。但它不分區 L3——不同物理核心的不同 VM 仍然共享 L3，LLC 側信道（Prime+Probe 打 L3）不受 Core scheduling 影響。Cloud hypervisor 通常把 Core scheduling 和 CAT 合併使用才能全面覆蓋。

4. **忽略 PCID 的存在，高估 KPTI 代價**：2018 年 KPTI 剛推出時，在沒有 PCID 或沒有充分利用 PCID 的 CPU 上，代價確實高達 30%+。但現代 Linux 已充分利用 PCID（`CR4.PCIDE` 啟用），在有 PCID 的 CPU（Ivy Bridge+）上 KPTI 代價大幅降低。不要把 2018 年的效能測試套到 2024 年的 kernel 上。

5. **把 CAT 當成「無限細粒度隔離」**：CAT 的最小隔離單位是一個 LLC way（通常 2–4 MiB），你不能把某兩個行程的 cache 隔離到「只有幾 KiB」的粒度。而且 way 數有限（通常 12–20），同一台機器上你只能建立有限個隔離組。Cloud 提供商需要在「每個 VM 能用多少 cache」和「有多少 VM 能隔離」之間做取捨。

6. **WSL2 裡關 SMT 後以為物理 CPU 真的沒有 HT**：WSL2 的 SMT 控制是透過 Hyper-V 虛擬層實現的，`/sys/devices/system/cpu/smt/control` 裡的「off」讓 VM 看到的邏輯 CPU 數減半，但**物理 CPU 的 HT 仍然在跑**。host 端的 Hyper-V 可能把兩個 VM 的 vCPU 排到同一個物理核心的兩條 HT 上。真正的 SMT 隔離需要在 host 端（BIOS 或 hypervisor 設定）操作，不是在 guest VM 內。

---

## 進階：再往深一層

**Intel TDX 的頁表加密——把 KPTI 的思路推到底**：KPTI 是「把 kernel 頁從使用者 page table 移除」，TDX（Trust Domain Extensions）是「把整個 TD（Trust Domain）的記憶體加密，讓 hypervisor 看不到 TD 的任何東西」。TDX 用 Intel TME-MK（Multi-Key Total Memory Encryption）給每個 TD 分配獨立的加密金鑰，hypervisor 存取 TD 的物理記憶體只看到密文。這讓「hypervisor 是攻擊者」的威脅模型有了硬體層面的防禦——超過 KPTI 只能解決的 user vs kernel 邊界。代價是 VM 啟動時的 attestation 流程複雜、效能有小幅代價（TME 加密約 1–5%）。

**AMD SME/SEV 的不同隔離路線**：AMD 的 Secure Memory Encryption (SME) 可以加密整個系統記憶體（對抗 cold boot/DRAM 物理攻擊），SEV 讓每個 VM 有獨立金鑰（對抗 hypervisor 讀 guest 記憶體），SEV-SNP 再加上 Nested Page Table integrity check（對抗 hypervisor 修改 NPT 欺騙 guest）。這個路線和 TDX 的目標類似，但實作細節不同，且 AMD 較早商業部署（EPYC 7003 系列已支援 SEV-SNP）。

**CHERI 的能力架構——最根本的隔離方案**：CHERI 在指標本身附帶了「capability」標籤，CPU 在每次記憶體存取時強制驗證指標的 capability（權限、範圍、有效性）。這讓「即使在推測執行路徑上」，超出能力的存取也在硬體層面直接失敗，不留下任何 cache 痕跡。CHERI 是從根本改變「推測執行洩漏資料」的可能性——但需要整個軟體棧（OS、library、應用）重新編譯支援 CHERI 語意，是 10 年以上的遷移工程。Arm 的 Morello 原型晶片（AArch64 + CHERI）是第一個商業展示，Microsoft Research 和 Cambridge 合作的 CheriBSD 是研究版 OS。

**Cache 側信道的根本難題**：隔離類防禦的極限是「完全不共享 cache」，但 cache 共享本來就是多核效能的基礎——L3 shared cache 讓核心間資料快速交換，去掉共享等於把每個核心變成獨立電腦。理論上最乾淨的方案是每個 tenant 有完全獨立的 DRAM + cache 層級（物理隔離），但這等於「每個 tenant 一台實體機」，雲端多租戶的商業模式就不存在了。所以隔離永遠是「在可接受代價下最大化通道阻斷」，而不是消除通道。

---

## 動手練習

1. **讀本機的 KPTI 狀態**：跑以下命令，確認這台機器的 KPTI 狀態：
   ```bash
   cat /proc/cmdline | grep -o 'nopti' || echo "KPTI 啟用（未設 nopti）"
   cat /sys/devices/system/cpu/vulnerabilities/meltdown
   # 如果是 "Not affected" → 硬體已修，KPTI 仍啟用作為額外保護
   # 如果是 "Mitigation: PTI" → KPTI 是主要緩解措施
   ```
   在你自己的機器上：這台 CPU 是哪年的？有沒有硬體修復 Meltdown？

2. **示範關 SMT 的效果**：
   ```bash
   echo "SMT 前 nproc:"; nproc
   echo off | sudo tee /sys/devices/system/cpu/smt/control
   echo "SMT 後 nproc:"; nproc
   # 觀察：邏輯 CPU 數減半
   echo on | sudo tee /sys/devices/system/cpu/smt/control
   echo "恢復後 nproc:"; nproc
   ```
   記錄關 SMT 前後 CPU 數，並用 `time make -j$(nproc) ...` 實測一個多執行緒任務的耗時差異。

3. **查 Intel CAT 支援**：
   ```bash
   ls /sys/fs/resctrl/ 2>/dev/null || echo "此 CPU 不支援 Intel RDT/CAT"
   # Xeon 上：ls 能看到 info/ cpus ... 等項目
   # 桌面 Core i7：看到 no resctrl
   ```
   思考：這台 CPU 是 Client 還是 Server 級？CAT 的缺失對這台機器能做的隔離防禦有什麼影響？

4. **感受 page coloring 的複雜度**：寫一個 C 程式，對 `/proc/self/maps` 讀取本行程的物理頁（透過 `/proc/self/pagemap`，需 root），計算每個頁的「cache set color」（使用 LLC set 數和 cache line size 計算：`PA >> 6 & (LLC_SETS - 1)`）。統計這些頁的 color 分佈是否均勻。如果你有兩個行程，看它們的 color 是否互相覆蓋——覆蓋就代表 Prime+Probe 理論上可行。

5. **閱讀並理解 Core scheduling 的 API**：找 `man 2 prctl` 的 `PR_SCHED_CORE` 部分（Linux 5.14+），或在 kernel 文件搜 `core-scheduling.rst`。理解 `PR_SCHED_CORE_CREATE` 和 `PR_SCHED_CORE_SHARE_TO` 的語意。寫一個小程式：建立兩個 process，把它們放到同一個 core scheduling group，再確認用 `taskset` 讓它們都在同一個物理核心的兩條 HT 上時沒有被排程器分開。

---

## 本章重點整理

- 隔離類防禦的核心邏輯：側信道需要通道，通道需要共享，不共享就切斷通道。不用知道攻擊者的具體手法，只要把共享資源移除，攻擊就失去物理基礎。
- **KPTI**：把 kernel 頁從使用者 page table 移除，同時擋 Meltdown 和 prefetch timing KASLR 破解。PCID 大幅降低代價。本機 i7-10700 硬體已修 Meltdown，KPTI 作為額外保護層啟用。
- **關 SMT**：一刀切掉所有 SMT 共享資源（L1D/buffer/port/BTB）。代價是 50% 多核效能損失。本機已跑過 `echo off`，確認 nproc 16→8 的效果。
- **Core scheduling**：精細化 SMT 隔離——同一個物理核心的兩條 thread 只能跑同一個信任域的 task。代價 5–15%，不完整（不擋 LLC）。
- **Intel CAT**：LLC cache way 分區，讓不同 VM 的 cache 在物理上不重疊，擋 LLC 側信道。只有 Xeon 有，本機桌面 i7 不支援（`no resctrl`）。
- **Page coloring**：OS 層的軟體 cache 分區替代，不需要硬體支援，但碎片化問題和 huge page 使其實際部署困難。
- 每種隔離都只切斷特定的共享：Core scheduling 不擋 LLC，CAT 不擋 L1，關 SMT 不擋跨核心 L3——生產環境需要多種防禦組合，沒有一招通殺。

---

## 自我檢核

- [ ] 解釋為什麼 KPTI 同時擋住了 Meltdown 和 prefetch-based KASLR 破解——不是兩個補丁，而是同一個機制。
- [ ] 關 SMT 以後，Flush+Reload 跨行程攻擊是否還能進行？為什麼？
- [ ] Core scheduling 和關 SMT 的主要差別是什麼？前者解決了後者的什麼缺點，又引入了什麼新的限制？
- [ ] 為什麼「本機在 WSL2 裡關 SMT」不等於「物理 CPU 真的沒有 SMT」？這個差異在安全評估上有什麼意義？
- [ ] Intel CAT 能擋住 Flush+Reload 嗎？能擋住 Port contention 嗎？各自為什麼？
- [ ] 完全不共享任何 CPU 資源的「終極隔離」在現實中是什麼形式？為什麼雲端不能普遍採用？

---

## 延伸閱讀

### 論文

- **[KAISER: Hiding the Kernel from User Space](https://gruss.cc/files/kaiser.pdf)** — Gruss et al., USENIX Security 2017
  - **讀哪裡**：Section 3（KAISER 設計原理）和 Section 5（效能評估）——這是 KPTI 的原始學術提案。
  - **學到什麼**：為什麼「只把 kernel text 頁的 supervisor-only 標記設好還不夠」，必須把整個 kernel 從使用者 page table 移除；PCID 的作用在這篇就有詳細分析。
  - **為什麼值得**：KPTI 的 kernel 實作直接基於這篇論文，理解這篇才能真正理解 KPTI 的設計選擇。

- **[DRAMA: Exploiting DRAM Addressing for Cross-CPU Attacks](https://www.usenix.org/system/files/conference/usenixsecurity16/sec16_paper_pessl.pdf)** — Pessl et al., USENIX Security 2016
  - **讀哪裡**：Section 3（逆推 DRAM 映射函數）和 Section 6（side-channel 跨核心 LLC 攻擊）。
  - **學到什麼**：如何用計時實驗逆推 Intel 未公開的 LLC 映射函數（就是 page coloring 需要的那個函數）；cache 共享如何讓 DRAM 和 LLC 同時成為攻擊面。
  - **為什麼值得**：這篇讓 Rowhammer 攻擊者有了系統性找目標 row 的方法；也是理解 page coloring 為何困難的理論基礎。

- **[PLATYPUS: Software-based Power Side-Channel Attacks on x86](https://platypusattack.com/)** — Lipp et al., IEEE S&P 2021
  - **讀哪裡**：Section 4（Intel RAPL 界面的洩漏）和 Section 6（修復）。
  - **學到什麼**：RAPL（Running Average Power Limit）能量計數器在使用者空間可讀，用它可以做功耗側信道攻擊，提取 AES 金鑰——即使 constant-time 程式碼。這是「隔離」防禦的另一個需要考慮的洩漏面：Linux 後來預設移除普通使用者對 RAPL 的讀取權限。
  - **為什麼值得**：說明隔離不能只考慮 cache 和 SMT，能量計量介面也是側信道。

### 官方文件

- **[Linux Kernel: Core Scheduling Documentation](https://www.kernel.org/doc/html/latest/admin-guide/hw-vuln/core-scheduling.html)** — kernel.org
  - **讀哪裡**：全文，重點看「Introduction」（為什麼需要）、「Architecture」（怎麼工作）和「Interface」（怎麼使用）。
  - **學到什麼**：Core scheduling 的實作層面；`PR_SCHED_CORE` prctl 的完整 API；和關 SMT 的比較。

- **[Intel Resource Director Technology (RDT) Software User Guides](https://www.intel.com/content/www/us/en/developer/articles/technical/introduction-to-intel-resource-director-technology.html)**
  - **讀哪裡**：CAT 的部分（Cache Allocation Technology）。
  - **學到什麼**：CAT 的設定介面、CLOSID（Class of Service ID）的概念、如何在 resctrl 裡配置。
  - **為什麼值得**：要在 Xeon 上實際部署 LLC 分區隔離，這是必讀文件。

### 技術文章

- **[Meltdown/Spectre 修補的效能數字追蹤（Phoronix）](https://www.phoronix.com/)** — Michael Larabel
  - **搜尋關鍵字**：`KPTI performance`, `SMT disable performance`, `Spectre mitigation benchmark`
  - **讀哪裡**：2018–2024 年各個補丁發布後的 benchmark 文章，Phoronix 有最系統性的 Linux 效能測試。
  - **學到什麼**：實際生產環境（PostgreSQL、Redis、nginx 等）的效能衝擊數字——比理論估計可靠得多。
  - **為什麼值得**：讓你能把「KPTI 有效能代價」轉換成「具體到什麼場景代價有多大」的具體數字，面試或設計決策時有說服力。

下一章把防禦的視角轉向「直接壓制推測執行本身」——retpoline 怎麼讓間接跳轉不被 BTB 預測到、lfence 如何在 bounds check 後切斷推測路徑、IBRS/IBPB/STIBP 這些硬體 barrier 的精確語意、以及真跑加了 lfence 的 Spectre-v1 PoC 和未加的版本對照。

→ [Ch 31 推測抑制](31-speculation-suppression.md)
