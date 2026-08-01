# Ch 39 — side-channel / CPU bug 對虛擬化的衝擊：L1TF/MDS/Spectre

> **目標**：理解 CPU 推測執行漏洞如何在虛擬化邊界造成跨 VM 記憶體洩漏，以及雲端業者為此付出的 mitigation 代價。

## 為什麼需要這個？

2018 年 1 月 3 日，Meltdown 和 Spectre 同步公開。在此之前，安全界有個基本假設：**只要 memory mapping 正確，一個 process/VM 的記憶體內容就不可能被另一個 process/VM 讀到**。

這個假設在那天碎掉了。

對雲端虛擬化而言，衝擊尤其嚴重。攻擊者不需要找 device emulation 的 C bug，不需要 heap overflow，不需要 ROP——只需要一段在 guest 裡執行的 JavaScript 或 native code，就能在理論上讀到 host memory 或隔壁 VM 的 L1 cache 內容。

本章是這個主題的**威脅模型導覽**，不是逐條 gadget 分析（側信道的深挖是另一門課的份量）。目標是讓你能評估：「這個 hypervisor 有沒有做 L1D flush？」「core scheduling 是什麼、為什麼雲端需要它？」

## 先建立直覺

側信道攻擊和傳統 pwn 的本質差異：

```
傳統 pwn：
  你找到一個記憶體錯誤（OOB/UAF）→ 直接讀寫目標記憶體

側信道：
  你沒有直接存取目標記憶體的方法
  但你能觀察「CPU 的狀態」（cache timing、分支預測器狀態…）
  從這些間接觀察「推斷」出目標記憶體的內容
```

關鍵媒介是**快取時間差（cache timing）**：讀快取中的資料快（~4 cycles），讀主記憶體慢（~200 cycles）。若某段操作造成了某個記憶體位址被載入 L1 cache，我們可以透過計時是否快速讀到那個位址來「判斷它是否被載入過」——即使我們沒有直接讀取那個位址的權限。

### Flush+Reload 的完整時序

這是所有基於 cache timing 的側信道攻擊的核心技術，理解這個時序圖是理解後面所有漏洞的基礎：

```
Flush+Reload 時間軸：

probe_array = 256 個 cache line，每個間隔 4096 bytes（避免 hardware prefetcher 干擾）
probe_array[secret * 4096] 是關鍵位址

t0: 攻擊者執行 clflush(probe_array[N*4096]) for N in 0..255
    → 把整個 probe_array 從 L1/L2/L3 cache 全部清掉
    → 此時任何 cache 存取都會 miss，需要從 DRAM 拿資料

t1: 觸發「受害者的推測執行路徑」
    （Spectre v1：送越界 x，讓分支預測器推測執行 if 內部）
    （L1TF：存取 present=0 的 PTE，觸發 CPU 推測讀 PFN 對應的 L1 cache）

t2: CPU 推測執行路徑讀到 secret 值（假設 secret = 42）
    CPU 用 secret 作 index：access probe_array[42 * 4096]
    → 這條 cache line 現在被載入 L1 cache，變成「熱的」

t3: 推測執行被撤銷（CPU 發現條件不成立，或 page fault 來了）
    CPU 回滾暫存器狀態——寄存器裡的 secret 值消失
    但 cache 狀態不回滾！probe_array[42*4096] 仍然在 L1 cache 裡

t4: 攻擊者 timing loop 掃描全部 256 個 cache line：
    for i in 0..255:
        t_start = rdtscp()
        dummy = probe_array[i * 4096]
        t_end = rdtscp()
        latency[i] = t_end - t_start
    → latency[42] ≈ 4 cycles（cache hit）
    → latency[其他] ≈ 200 cycles（cache miss，需要讀 DRAM）
    → 結論：secret = 42

關鍵認識：
  cache 狀態是「covert channel（隱蔽通道）」
  它在推測執行被撤銷後仍然保留——這是硬體設計的漏洞
  攻擊者不需要直接讀取 secret，只需要觀察「哪個 cache line 熱了」
```

**為何 4096 bytes 的間距**：若兩個探測 entry 在同一個 cache line（64 bytes），cache 預取器（hardware prefetcher）可能在你 timing 時自動把相鄰 entry 也載入，造成假 hit。4096 bytes = 一個 page 大小的間距，確保各 entry 在不同 cache line 且 prefetcher 不會跨越。

## 底層機制：推測執行與虛擬化

### Spectre v1 — 邊界繞過（Bounds Check Bypass）

```c
// 受害者程式碼
if (x < array1_size) {         // ← 分支預測可能提前執行 if 內部
    temp = array2[array1[x] * 256];
}

// 攻擊者訓練 CPU 的分支預測器認為 if 總是 taken
// 然後送一個 x >= array1_size 的越界值
// CPU 推測執行了 if 內部，array1[x] 讀到了越界記憶體
// 這個讀到的值被用來 access array2 的某個 cache line
// 即使推測執行被撤銷，array2 的 cache line 仍然熱
// 攻擊者 timing array2 的各 cache line → 推出 array1[x] 的值
```

**對虛擬化的意義**：Spectre v1 的 gadget 要在被攻擊的 process（受害者）的 context 裡找。若 hypervisor 或 guest kernel 裡有這種 gadget，guest userland 可能透過呼叫 guest kernel syscall 訓練分支預測器，然後在推測執行時讀到 guest kernel 的記憶體——這是 Spectre v1 針對 OS 的場景，在 VM 邊界沒有額外 magic，和 container/OS 場景類似。

### Spectre v2 — 間接分支注入（Branch Target Injection）

更嚴重的場景：攻擊者可以訓練 CPU 的**間接分支預測器**，讓受害者行程的間接跳轉在推測執行時跳到攻擊者選擇的「gadget 位址」。

**對虛擬化的意義**：guest 可以嘗試訓練 CPU 的間接分支預測器，影響 hypervisor（VMX root mode）在 VM exit handler 裡的間接跳轉。若 VMEXIT 路徑有合適的 Spectre v2 gadget，guest 可能在推測執行中讀到 host memory。這是最嚴重的 Spectre 虛擬化場景之一。

**緩解**：
- **Retpoline**：把間接跳轉替換成「讓 CPU 的 return predictor 猜測一個受控目標」的 trampoline 結構，使 Spectre v2 gadget 無效化。Linux kernel 和 QEMU 都有 retpoline build option。
- **IBRS（Indirect Branch Restricted Speculation）**：微碼更新，讓 ring 0 不受 ring 3 的分支預測訓練影響。
- **eIBRS（Enhanced IBRS）**：更高效的 IBRS，直接由 CPU 保證 ring 切換後分支預測器隔離。

### Meltdown — 亂序執行越過 permission check（CVE-2017-5754）

```c
// 在 permission check 生效前，CPU 已亂序執行了這行：
char secret = *(char*)kernel_address;  // SIGSEGV 還沒來得及
// secret 已被載入暫存器，且可能已影響 cache 狀態
// 然後用 Flush+Reload 讀出 secret 的值
```

Meltdown 讓 user process 在 kernel 的 permission check 攔住讀取之前，用亂序執行讀到 kernel memory 的值並透過 cache 洩漏。

**對虛擬化**：Meltdown 讓 guest userland 可能讀到 guest kernel memory，這是 guest 內部的問題（guest kernel 的 KPTI 緩解）。在虛擬化邊界，Meltdown 變體（L1TF）才是最嚴重的跨 VM 問題。

**緩解**：KPTI（Kernel Page Table Isolation）——讓 user mode 跑的時候 kernel page table 不在 CR3 裡，使 kernel 位址無法被亂序讀取。代價是 syscall 的 TLB flush overhead。

### L1TF / Foreshadow（CVE-2018-3646）——最嚴重的雲端威脅

L1TF（L1 Terminal Fault）是虛擬化場景最嚴重的側信道漏洞之一。

**原理**：Intel CPU 在遇到 page fault 時，在確認 present bit 的合法性之前，會推測性地用 page table entry 裡的 Physical Frame Number（PFN）去 L1 data cache 查詢——即使那個 PTE 的 present bit 是 0（頁面不存在）。

```
攻擊場景：
1. 惡意 guest 在自己的 EPT（extended page table）裡偽造一個
   present=0 但 PFN 指向 host 某個 physical page 的 PTE
2. 觸發對應的虛擬位址存取 → page fault（正常，present=0）
3. 但在 page fault 處理前，CPU 已推測性地把那個 PFN
   對應的 host physical page 的資料載入 L1 cache 的回應通道
4. 用 Flush+Reload 計時推斷被載入的資料

結果：惡意 guest 可以讀到 host L1 cache 中任意 physical page 的內容
     若 host L1 cache 中有另一個 VM 的秘密、或 host kernel 的敏感資料
     → 跨 VM 記憶體洩漏
```

CVE-2018-3646 是 L1TF 中針對 VMX 的變體（SMM、SGX 各有自己的 CVE 編號 CVE-2018-3615、CVE-2018-3620）。

**為什麼 L1D cache 用 physical address 是問題的根源**

L1D cache 使用 **physical address tag** 來定址（PIPT：Physically Indexed Physically Tagged）。這個設計對 cache 一致性很重要，但在虛擬化下造成嚴重問題：

```
L1D cache 定址機制（簡化）：

guest A 的 virtual address  →  guest A 的 physical address  →  L1 tag 比對
guest B 的 virtual address  →  guest B 的 physical address  →  L1 tag 比對

問題：
  兩個 VM 的 virtual address space 完全不同（各有自己的 page table）
  但若 host physical address 相同，L1 cache 的 tag 就相同！
  → 跨 VM 的 cache side-channel 天然存在

正常情況下：
  EPT 由 hypervisor 控制，guest A 的 GPA（guest physical address）
  不可能被 EPT 對應到另一個 VM 的 HPA（host physical address）

L1TF 繞過：
  攻擊者偽造 EPT entry，present=0 → 不經過正常 EPT 翻譯
  但 CPU 推測讀 PFN 時，直接用那個 PFN 去查 L1 cache tag
  → 繞過了 EPT 的存取控制
```

**EPT 兩層地址翻譯與 L1TF 攻擊路徑**：

```
正常虛擬化地址翻譯：

guest virtual addr (GVA)
       ↓  [guest page table]
guest physical addr (GPA)
       ↓  [EPT — Extended Page Table，由 hypervisor 控制]
host physical addr (HPA)
       ↓  [實際 DRAM 存取]

L1TF 攻擊路徑：

guest 偽造一個 EPT entry：
  GPA X → PFN = target_host_page, present = 0

guest 存取 GPA X：
  → EPT walk 發現 present=0，應該 page fault
  → 但 CPU 在確認 present bit 之前，推測用 PFN=target_host_page 查 L1 cache
  → 若 target_host_page 的 cache line 熱，攻擊者能 timing 出其值
  → EPT 的存取控制被繞過了
```

**對雲端的重擊**：若 AWS/GCP/Azure 的實體機上，某個租戶的 guest 能讀到 host L1 cache 的內容，而 host L1 cache 可能有其他租戶的 VM page 被最近換進來的話——多租戶隔離的根基就動搖了。

**緩解**：
- **L1D flush on VMENTRY**：在每次進入 VM 之前，用 WBINVD 或 L1D flush 指令清空 L1 data cache。代價：每次 VM switch 的開銷從~幾百 cycles 增加到~幾千 cycles（flush 要時間）。
- **SMT 禁用**：最激進的方案。若一個 physical core 的兩個 SMT thread（超執行緒）同時跑 guest 和 host，它們共用 L1 cache，L1TF 的攻擊視窗就在 SMT partner 存取 cache 那段時間。直接關掉 SMT（讓 hyperthread 不跑 guest）是確定緩解，但效能代價巨大（少一半核）。
- **EPT 過濾**：不允許 guest 建立 present=0 但 PFN 合法的 PTE（讓 KVM 拒絕這類 EPT entry）。這是較精確的緩解，代價較小，Linux KVM 有實作。

### MDS（Microarchitectural Data Sampling）

MDS 是一類漏洞的統稱，包含 RIDL、Fallout、ZombieLoad（各有不同 CVE）。

**原理**：CPU 在推測執行過程中會把資料丟進各種 microarchitectural buffer（line fill buffer、store buffer、load port）。這些 buffer 在特定情況下可能洩漏前一個 context（不同 process 或 VM）留下的資料。

```
MDS 攻擊流程（高度概略）：
1. 惡意 process/VM 讓 CPU 做大量記憶體操作，試圖「讀」到 stale 資料
2. 這些 stale 資料是前一個在同一個 CPU 核心上跑的 context 的資料
3. 透過 Flush+Reload 或類似技術把 stale 值從 cache 推出來
```

**對虛擬化**：若兩個 VM 的 vCPU 曾在同一個 physical CPU 上跑，前一個 VM 的資料可能在 MDS buffer 裡留下痕跡，被後一個 VM 讀到。

**緩解**：
- **VERW 指令**：在 VM exit（進入 VMX root mode）時執行 VERW，清空 MDS-vulnerable buffer。Linux KVM 在 5.2+ 版本加入此緩解。
- **core scheduling**（見下）

### Retbleed（CVE-2022-29900/CVE-2022-29901）

較新的分支預測攻擊（2022 年）。在某些 CPU 微架構上，`RET` 指令的分支預測可以被攻擊者控制，使其在推測執行中跳到惡意 gadget。比 Spectre v2 的 retpoline 緩解更難對付，因為 retpoline 的 `RET` 本身成了洩漏點。

緩解：更新的 retpoline 變體（unret），或 IBPB on entry（每次 ring 切換清除分支預測歷史）。代價大。

**AMD 的暴露面**：Retbleed 的 CVE-2022-29900 是 AMD 變體，覆蓋 Zen 1/Zen 2 微架構。AMD 在 Meltdown 和 L1TF 上的受影響程度與 Intel 不同——AMD 聲稱不受 Meltdown 影響（其架構在推測執行時不讀 supervisor-only 的資料），且 L1TF 是 Intel-specific（基於 Intel 的 EPT terminal fault 行為）。但 Spectre v1/v2 和 Retbleed 的 AMD 版本表明 AMD 並非無懈可擊，只是暴露面不同。

### Core Scheduling

核心回應：不是修個別漏洞，而是從排程層面確保**不信任的 guest 和 host 不共用 SMT thread**。

```
傳統 SMT 排程：
  HT0：guest A vCPU  ← 惡意 VM
  HT1：host kernel   ← 目標
  → L1TF/MDS/Retbleed 可能洩漏 HT1 的資料到 HT0

Core Scheduling：
  只有同一個「trust group」的 task 才能在同一個 physical core 的兩個 HT 上同時跑
  guest A 的所有 vCPU → trust group A
  host tasks → trust group host
  保證：HT0 跑 guest A 時，HT1 只跑 guest A 的其他 vCPU，不跑 host tasks
```

Linux 5.14 加入 core scheduling 支援，各大 cloud vendor 在此之前用自家 patch 或直接禁 SMT。核心概念是：讓 SMT pair 只在同信任等級的任務之間共用，消除跨信任等級的 microarchitectural state leakage。

代價：排程靈活性下降，特定負載下效能損失 10-30%（未實測具體數字，各 cloud vendor 報告不同）。

## 對比與取捨

| 漏洞 | 跨 VM | 需要 SMT | 主要緩解 | 效能代價量化 |
|---|---|---|---|---|
| Spectre v1 | 同 VM 內（guest 使用者→guest kernel）| 否 | compiler barriers（`array_index_nospec`）| 低，針對性插入 |
| Spectre v2 | 可跨 ring（guest→hypervisor）| 否 | retpoline/eIBRS | 中；retpoline 本身低，IBRS 微碼路徑高（~5-10%）|
| Meltdown | 同 VM 內（guest 使用者→guest kernel）| 否 | KPTI | syscall 延遲增加 10-30%；PCID 優化後降至 5-15% |
| L1TF（CVE-2018-3646）| **是**（guest→host L1 cache）| 否，SMT 擴大視窗 | L1D flush + EPT filter | L1D flush：每次 VM switch 多 ~1000-5000 cycles（未實測）|
| MDS | **是**（同 core 不同 context）| **是**（共用 buffer）| VERW + core scheduling | VERW 本身輕量；core scheduling 整體 5-20% |
| Retbleed | 可跨 ring | 否 | unret/IBPB on entry | IBPB：每次 context switch 清歷史，延遲增加 10-40% |

**代價量化說明**：

- **L1D flush**：每次 VM entry/exit 前必須把 L1D cache 整個清空（L1D 通常 32-48 KB）。Intel 後來的微碼版本提供了比 WBINVD 更快的 L1D flush 機制（透過 VERW 指令同時清 L1D + MDS buffer），降低了部份代價。
- **KPTI 與 PCID**：原始 KPTI 每次 syscall 都要切換 CR3，觸發 TLB flush（成本高）。後來利用 PCID（Process-Context Identifiers）讓切換 CR3 不強制 flush TLB，代價從 30% 降到 5-15%。有 PCID 支援的現代 CPU（Haswell+）上影響相對可接受。
- **core scheduling 的實際分布**：高 syscall 密度的 I/O 負載損失較大，計算密集型負載損失較小。各 cloud vendor 的公開數字差異大（5-20%），因為 workload mix 不同。

## 真實影響案例

### AWS 的 L1TF 緊急維護（2018 年 8 月）

L1TF（CVE-2018-3646）的公開披露走的是 Intel 協調披露流程，AWS、Google、Microsoft 都提前收到通知。AWS 在漏洞公開前執行了有史以來規模最大的一次緊急維護視窗：

- 大量 EC2 instance 在一個短暫的維護視窗內被強制重啟（live migration 或 reboot）
- 原因：host kernel 需要打 KVM 的 L1TF patch + 微碼更新，而微碼更新通常需要重啟 host
- 規模：影響數十萬台 host 機，對應的 tenant instance 全部需要遷移或重啟
- 使用者側感受：部份 EC2 instance 收到「scheduled maintenance」通知，時間窗口極短（相比日常維護）

這個事件說明了一件事：**一個 CPU 硬體漏洞可以迫使整個雲端業者在幾週內完成大規模的 fleet-wide patch，且必須接受短暫停機或效能損失**。這不是軟體 bug 可以熱更新解決的問題。

### 雲端業者的 SMT 策略分歧

L1TF 和 MDS 的緩解逼迫各大 cloud vendor 做 SMT 策略決策，結果走了不同的路：

**AWS 的路**：對高安全性 instance（如 `c5n`、部份 `m5`、`bare metal` 系列）預設關閉 SMT（Hyperthreading）。這讓攻擊面大幅縮小，代價是 vCPU 數量減半。AWS 同時提供「Hyperthreading 控制」API，讓使用者可以根據需求開關——這本身也說明 AWS 不認為預設開 SMT 在高安全性場景下是合理的。

**Google 的路**：Google Cloud 選擇 core scheduling（部份是 Google 工程師主導開發的 Linux patch）。保留 SMT，但透過排程保證不同 tenant 的 vCPU 不共用同一個 physical core。代價是排程器複雜度提升，但保留了 SMT 的效能優勢。

**為什麼不統一做法**：因為 trade-off 不同。禁 SMT 代價確定（少一半核，vCPU density 減半，成本上升），但緩解確定。core scheduling 保留了效能，但實作複雜，且依賴排程器正確性（核心路徑有 bug 就形同虛設）。沒有對錯，只有對各自商業模型的 risk trade-off。

### 為什麼雲端業者不能選擇「不修」

多租戶隔離是雲端服務的**商業基礎假設**，不是一個「nice to have」的功能。若攻擊者能從一個 EC2 guest 讀到另一個 guest 的記憶體——即使只是 L1 cache 的片段——以下後果會同時發生：

1. **直接危害**：加密金鑰、session token、資料庫 credential 可能在 cache 中被採樣
2. **法規危害**：PCI-DSS、HIPAA 等合規標準要求資料隔離，一旦違反，雲端業者面臨稽核
3. **商業信任危害**：客戶會開始問「我的資料安全嗎」，沒有好答案就是流失客戶

所以雲端業者的緩解不是可選的，是生意成立的前提。效能損失是他們願意付的代價。

## 踩雷集錦

**「修了 Meltdown 就修了 L1TF」**
→ 兩者原理不同。Meltdown 是 page permission check 慢於 cache 的問題；L1TF 是 EPT 的 terminal fault 觸發 L1 cache 存取的問題。KPTI 緩解 Meltdown 但不緩解 L1TF；L1TF 需要獨立的緩解（L1D flush + EPT filter）。確認方式：`cat /sys/devices/system/cpu/vulnerabilities/l1tf` 和 `cat /sys/devices/system/cpu/vulnerabilities/meltdown` 是兩個獨立的 sysfs 節點，狀態要分別確認。

**「開了 KPTI 雲端就安全了」**
→ KPTI 只緩解 Meltdown（guest 使用者讀 guest kernel 的場景）。L1TF 和 MDS 是獨立的攻擊路徑，需要各自的緩解。2018 年的「安全更新」是一個接一個的，不是一次解決。確認方式：用 `spectre-meltdown-checker`（GitHub 上的 shell script）一次掃所有已知漏洞的緩解狀態，輸出清晰標記哪些 CVE 還是 VULNERABLE。

**「禁掉 SMT 就解決所有側信道問題」**
→ MDS 是主要受益者。L1TF 在單執行緒場景仍然存在（只是 SMT 擴大了攻擊視窗）。Spectre v1/v2 不依賴 SMT。禁 SMT 是代價最大的緩解，適用於「無法接受任何資訊洩漏」的場景（如 EC2 高安全性 instance），但不是萬能藥。確認方式：`cat /sys/devices/system/cpu/smt/active`（1 = SMT 開，0 = 關），關掉 SMT 後再看 `vulnerabilities/mds` 是否變成 `Mitigation: Clear CPU buffers; SMT disabled`。

**「Firecracker 用 Rust 所以不受這些側信道影響」**
→ 側信道是硬體問題，與上層的程式語言無關。Rust/Go/C 的 VMM 在同一顆 Intel CPU 上跑，L1TF 的攻擊路徑完全相同。側信道的緩解在 CPU 微碼和 kernel 層，不在 VMM 語言層。確認方式：Firecracker 的 security documentation 明確說明它依賴 host kernel 的 L1TF/MDS 緩解，並要求 host 開啟 SMT 控制或 core scheduling。

**「cloud provider 已經 patch 了所以不用管」**
→ 每個新的 CPU 側信道都需要新的 patch，且緩解往往帶來效能損失（所以不是所有 instance type 都預設全開）。評估雲端安全時，要確認目標 cloud 對每個 CVE 的緩解狀態，以及你的 instance 在什麼 CPU 代上跑。確認方式：各大 cloud vendor 都有 security bulletin 頁面（AWS Security Advisories、Google Cloud Security Bulletins），Spectre/Meltdown 系列的 CVE 對應緩解狀態都有記錄，值得定期查看新增的 CPU 側信道公告。

## 進階：再往深一層

**Flush+Reload 計時精度與 VM 干擾**：所有這些側信道的「讀出資料」步驟都依賴快取計時。在 VM 環境裡，guest 需要高精度計時器（`rdtsc`、`rdtscp` 或類似）來區分 cache hit vs. miss 的 timing 差異。雲端對 `rdtsc` 的虛擬化（是否允許精確計時）本身是一個緩解旋鈕——把 `rdtsc` 的精度降低可以讓 Flush+Reload 更難，但不是不可能。

VUSec 的研究（2018-2019 年間）顯示：即使 `rdtsc` 被 hypervisor 加入 noise 或限制精度，攻擊者仍然可以用**計數執行緒（counting thread）**作替代計時器——一個空迴圈跑的次數就是時間的代理指標。這意味著 rdtsc 精度限制只是增加攻擊難度，不能從根本上消除計時側信道。

**VMX TSC scaling**：KVM/Xen 會對 guest 的 `rdtsc` 進行 scaling，讓 guest 看到的 TSC 頻率可以和 host 不同（用於 live migration 或 vCPU 超賣）。這個 scaling 在每次 VM exit 時有 overhead，且可以引入 jitter——攻擊者感知到的計時精度因此下降。但 jitter 要達到 cache hit/miss 差距（~200 cycles）的量級才有效，TSC scaling 通常達不到這個量。

**Spectre v2 針對 KVM**：KVM 需要在 host kernel 裡跑 guest 的 VM exit handler，這個路徑上的 indirect jump（如 function pointer dispatch）是 Spectre v2 的 gadget 目標。Google Project Zero 有深入分析 KVM + Spectre v2 的組合，是理解「攻擊者如何從 guest 透過側信道讀到 host memory」的最佳讀物。

**RIDL（Rogue In-flight Data Load）**：MDS 家族中最強的一個，可以讀 line fill buffer 中的資料，覆蓋範圍更廣。與其他 MDS 變體相比，RIDL 的洩漏路徑不限於 store buffer 或 load port，而是在資料從記憶體載入到 CPU 的 in-flight 階段就洩漏——攻擊者只需要讓 CPU 做任何記憶體讀取操作就能觸發洩漏視窗。VUSec 的原始論文有完整 PoC 說明（理論性質）。

**AMD 的不同暴露面**：AMD 架構在推測執行的記憶體存取控制上與 Intel 設計不同，導致 Meltdown 和 L1TF（Intel-specific 行為）對 AMD 不適用。但 Spectre v1/v2 基於分支預測器的特性，AMD 和 Intel 都有。Retbleed 的 AMD 變體（CVE-2022-29900）覆蓋 Zen 1/Zen 2，其推測返回（speculative return）行為與 Intel 類似但 gadget 不完全相同。這意味著在評估一台 AMD EPYC 伺服器跑的 KVM 環境時，L1TF 可以不管，但 MDS 和 Retbleed 的 AMD 對應緩解要確認。

## 動手練習

1. **確認你的 host kernel 側信道緩解狀態**：
   ```bash
   cat /sys/devices/system/cpu/vulnerabilities/*
   ```
   觀察 `l1tf`、`mds`、`spectre_v1`、`spectre_v2`、`retbleed` 的狀態。（在有實體 Linux 機的環境可跑）

2. **觀察 L1D flush 的效能影響**：在有 KVM 的 host 上，比較啟用和停用 `l1tf=full,force` 的 VM 切換延遲（需要適當的 benchmark）。感受「安全有代價」的真實含義。（未實測，理論預期）

3. **讀 Spectre 的 PoC**：找到 spectre PoC（GitHub 上有多個公開實作，如 `IAIK/meltdown`），讀懂 Flush+Reload 的實作邏輯（`clflush` + timing loop），理解計時精度的重要性。

## 本章重點整理

- 側信道攻擊的核心是「透過 CPU 的微架構狀態（cache timing）推斷別人的記憶體內容」
- Flush+Reload 的關鍵洞見：推測執行被撤銷後 cache 狀態不回滾，這個 cache 狀態是 covert channel
- L1TF（CVE-2018-3646）是對虛擬化最嚴重的威脅：惡意 guest 可能讀到 host L1 cache 的內容；根本原因是 L1D 用 physical address tag 且 CPU 在確認 EPT present bit 前就推測讀 cache
- MDS 在 SMT 環境下讓同 physical core 的不同 VM 之間有洩漏路徑
- 緩解：L1D flush（VMENTRY 前清 L1 cache）、VERW（清 MDS buffer）、core scheduling（不讓不同信任等級共用 SMT）、KPTI（Meltdown）
- 緩解有真實的效能代價（5-40% 視具體緩解和負載），是雲端安全的 trade-off 核心議題
- 側信道與 VMM 語言無關，是硬體問題，Rust/Go 寫的 VMM 同樣暴露
- AWS/Google 的 SMT 決策差異說明：沒有完美答案，只有對各自商業模型的 risk trade-off
- AMD 的暴露面與 Intel 不同：Meltdown/L1TF 不受影響，但 Spectre/Retbleed 有自己的 AMD 變體

## 自我檢核

- [ ] 能說出 Flush+Reload 的基本原理（clflush + timing）以及為何 cache 狀態不隨推測執行回滾
- [ ] 能解釋 L1TF（CVE-2018-3646）的攻擊流程（EPT present=0 + PFN + L1 cache 推測存取）
- [ ] 能說明 L1TF 和 Meltdown 的差異（EPT terminal fault vs. page permission check timing）
- [ ] 能解釋 core scheduling 解決了什麼問題（不信任 task 不共用 physical core）
- [ ] 知道如何在 Linux 系統查詢 CPU 側信道緩解狀態（/sys/devices/system/cpu/vulnerabilities/）
- [ ] 能說出 AWS 和 Google 在 SMT 策略上的不同選擇及其 trade-off

## 延伸閱讀

1. **「Foreshadow: Breaking the Virtual Memory Abstraction with Transient Out-of-Order Execution」（Van Bulck et al., USENIX Security 2018）**
   - L1TF 的學術原始論文，詳細說明 EPT present=0 如何觸發推測執行洩漏。學什麼：L1TF 的完整攻擊機制與對雲端的影響。

2. **Google Project Zero 的 Spectre 原始分析**（`googleprojectzero.blogspot.com/2018/01/reading-privileged-memory-with-side.html`）
   - Spectre/Meltdown 的原始發現者之一，說明各種攻擊路徑。學什麼：Spectre v1/v2/v3 的區分與攻擊流程。

3. **Linux kernel 文件「L1TF - L1 Terminal Fault」**（`kernel.org/doc/html/latest/admin-guide/hw-vuln/l1tf.html`）
   - Linux 對 L1TF 的緩解方案（L1D flush + EPT filter + SMT 處理）的官方說明。學什麼：實際緩解的配置旋鈕與效能影響。

4. **「RIDL: Rogue In-flight Data Load」（VUSec，2019）**
   - MDS 家族中最強的攻擊的原始論文，PoC 可在 GitHub 找到。學什麼：MDS 的具體機制（line fill buffer），是 VERW 緩解的動機。

5. **「Core Scheduling for Linux」（LWN.net 系列，2021）**
   - Core scheduling 的設計討論，包含為什麼需要它、如何實作、trade-off。學什麼：cloud vendor 如何在不完全禁 SMT 的條件下緩解 SMT 側信道。

---

→ [Ch 40 — 完整 chain 全景：guest → VM escape → host kernel LPE](./40-full-chain.md)
