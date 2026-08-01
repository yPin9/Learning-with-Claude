# Ch 29 — 防禦全景與威脅模型

> **目標**：把 Part 2–4 出現過的每一個攻擊，對應到它的防禦機制、防禦的運作層次、以及防禦做不到的地方——建立「攻擊→防禦→殘餘風險」的完整地圖。讀完你能回答「為什麼沒有一個補丁能一勞永逸」，以及「面對不同威脅模型（雲端/個人 PC/嵌入式），防禦策略為什麼不同」。

---

## 為什麼防禦是個永遠沒有終點的問題

這門課從 Ch 1 到 Ch 28 描述了大約二十種微架構攻擊。你可能已經注意到一個規律：**每一個防禦都不是真正「修好了」，而是「把這個特定攻擊的訊噪比壓到不可用的程度」**。攻擊的本質從未消失，消失的只是特定攻擊路徑的成本效益比。

這個觀察有結構性原因：

**1. 攻擊面來自 CPU 的效能設計，防禦的代價就是還原那些設計。**

快取、推測執行、超執行緒、亂序執行——這些都是為了讓 CPU 跑得快而存在的。每一條側信道都源於「CPU 為了快，在兩個不該有關聯的行為之間製造了物理耦合」。要徹底消除這個耦合，就得拿掉那個效能優化。clflush 讓 Flush+Reload 成為可能，要擋住它就得限制 clflush——但 clflush 本來就是 CPU 提供的合法指令。推測執行讓 Spectre 成為可能，要完全封住就得關掉推測——但那樣 CPU 效能會回退到 1995 年。

**2. 補丁是對已知攻擊的補丁，不是對未知攻擊的免疫。**

Spectre-v1 有 `lfence` 補丁、Spectre-v2 有 retpoline、MDS 有 MD_CLEAR。每一個補丁都是在「知道這個具體攻擊的前提下」針對性設計的。新的攻擊手法（BHI、Inception、GhostWrite）被發現時，舊的補丁對它們無效。這不是補丁設計得差，而是「防禦一個已知攻擊」和「防禦一類未知攻擊」之間存在根本的認識論差距。

**3. 現有系統是在「假設沒有側信道」的前提下設計的。**

OS 的多租戶假設、VM 的 shared memory 架構、browser 的 JIT 引擎——這些都假設「執行什麼程式碼」跟「CPU 物理狀態」是解耦的。側信道撕破了這個假設，但整個軟體生態不可能為了這一點從頭重寫。

**結果是**：防禦是分層的、是補丁式的、是每個漏洞各打各的。沒有銀彈，只有「在當前威脅模型下，這個防禦是否讓攻擊成本高過攻擊者能接受的門檻」這個工程判斷。

---

## 三層防禦架構

防禦機制按「在哪裡做」分三層，每層針對不同的攻擊面。

### 層一：軟體層（程式設計與編譯器）

軟體層的目標是「讓程式碼本身不洩漏時間資訊」，或是「在可能被推測執行探索的路徑上加擋板」。

**Constant-time 程式設計（Ch 32）**

核心原則：**所有分支行為不能依賴秘密資料，所有記憶體存取的地址不能依賴秘密資料**。程式的執行時間對外部觀察者而言是常數，無論輸入的秘密值是什麼。

```c
/* 有洩漏：比較密碼，early-exit 洩漏哪個 byte 不對 */
int bad_strcmp(const char *a, const char *b, size_t len) {
    for (size_t i = 0; i < len; i++) {
        if (a[i] != b[i]) return 0;   /* 提前返回，洩漏 i */
    }
    return 1;
}

/* Constant-time：全部跑完，結果用 OR 累積 */
int ct_memeq(const void *a, const void *b, size_t len) {
    const uint8_t *x = a, *y = b;
    uint8_t diff = 0;
    for (size_t i = 0; i < len; i++) {
        diff |= x[i] ^ y[i];   /* 不 early-exit */
    }
    return diff == 0;
}
```

這個防禦對 **cache timing（Flush+Reload/Prime+Probe）** 以及 **Hertzbleed（頻率側信道）** 都有效——因為洩漏的根源是「不同輸入走不同路徑、觸發不同的快取行為」，constant-time 程式碼把這條路切斷。

代價：效能。放棄 early-exit 通常快 2–5 倍；加密庫要寫出正確的 constant-time 實作極難且容易出錯（OpenSSL、BoringSSL、libsodium 都有過 CT 漏洞被找到）。

**`lfence` 系列化（Spectre-v1 的 array_index_nospec）**

在可能被推測執行越過邊界的位置插入 `lfence`，強迫 CPU 在繼續之前先確認分支結果：

```c
/* 核心 kernel 中的防禦寫法 */
index = array_index_nospec(index, array_size);
/* array_index_nospec 展開後等效於：
 *   index &= (signed long)(((int64_t)array_size - 1 - index) >> 63) ^ -1;
 *   _mm_lfence();
 * 讓越界 index 被清零，且 lfence 擋住推測執行路徑 */
val = array[index];
```

**代價**：每個需要保護的陣列存取要手動加這個。kernel 裡有幾千個地方需要審查，漏一個就是 Spectre gadget。

**retpoline（Spectre-v2 的 indirect branch 替換）**

把間接跳轉（`jmp *%rax`）替換成一個永遠推測失敗、但最終仍正確跳轉的序列——推測執行走的是死路（`pause; call; ret` 無窮迴圈），不是攻擊者填入的 BTB 目標：

```
retpoline_thunk:
    call    set_up_target       ; push 真實目標到 stack
    pause_loop:
        pause                   ; 自旋，降低 BTB 污染視窗
        lfence
        jmp     pause_loop
    set_up_target:
        lea     [rsp], target   ; 把目標位址放到正確位置
        ret                     ; 真正跳轉
```

**代價**：間接跳轉變慢（約 10–30%，依呼叫密度而定）；對沒有 retpoline 的廠商 library（如閉源驅動）無效。

**`__user` pointer sanitization（Spectre-v1 的 syscall 路徑）**

Linux kernel 在 copy_from_user/copy_to_user 系列函式前，對使用者提供的指標做 `__uaccess_mask_ptr` 處理——讓指標在非使用者空間位址的情況下被強制清零，即使推測執行路徑上也不能洩漏 kernel 資料。

---

### 層二：OS 層

OS 層的目標是「讓不同行程/VM 的微架構狀態相互隔離」。

**KPTI（Kernel Page-Table Isolation）**

KPTI 把 kernel 頁從使用者態 page table 徹底移除。使用者程式跑的時候，完整 kernel 映射不存在；只有進入 kernel mode 才切換到包含 kernel 頁的完整 page table。

這同時擋了兩件事：
- **Meltdown**：推測執行讀 kernel 資料的前提是 kernel 頁在當前 page table 裡，KPTI 後這個前提不成立。
- **prefetch timing KASLR 破解**：prefetch 走 page table 才能發現「這裡有映射」，KPTI 後使用者 page table 裡沒有 kernel 頁，timing 差異消失。

**代價**：每次 syscall/中斷 都要切換 page table（TLB flush 或 PCID 切換），syscall 密集的工作負載效能損失 5–30%。PCID（Process-Context Identifiers）能部分緩解 TLB flush 代價，本機 CPU 支援。

本機狀態：

```
meltdown: Not affected
```

表示硬體已修（Comet Lake 把 Meltdown 在微碼/電路層面修好），KPTI 仍作為額外保護層啟用。

**KASLR 與 ASLR**

位址隨機化本身不擋側信道，而是讓資訊洩漏必須額外付出「先破位址」的代價，把兩步攻擊（位址破解 + 利用）串成一個整體，讓每一步的失敗都讓整個 exploit 失敗。

**Core scheduling（關 SMT 的替代方案）**

SMT（超執行緒）讓兩個執行緒共用同一個物理核心的執行資源，導致 port contention（Ch 26）和某些 MDS 攻擊。Core scheduling 讓只有「同一個信任域的執行緒」才能共享物理核心——不同 VM 的 vCPU 不會被排程到同一個物理核心的兩個 hardware thread 上。

**代價**：Core scheduling 實作複雜，排程器需要同時決定「哪個 pCPU 可以跑這個 task」而非任意任何 pCPU，犧牲排程靈活性，吞吐量損失約 5–15%。

**關 SMT（最暴力的 L1TF/MDS 緩解）**

```bash
echo off | sudo tee /sys/devices/system/cpu/smt/control
```

徹底關閉超執行緒，SMT 共享資源導致的側信道全部歸零。代價：CPU 核心數從邏輯 16 降到物理 8，所有多執行緒工作負載效能直接腰斬。

本機驗證（真實跑過）：

```
cat /sys/devices/system/cpu/smt/control  → on
cat /sys/devices/system/cpu/smt/active   → 1
nproc                                     → 16
echo off | sudo tee /sys/devices/system/cpu/smt/control  → 成功
nproc                                     → 8（關 SMT 後）
```

---

### 層三：硬體與微碼層

硬體層的目標是「在 CPU 本身就不讓洩漏發生」，是最根本也最難部署的修復。

**MD_CLEAR（VERW 清 CPU buffer）**

MDS（Microarchitectural Data Sampling，Ch 19）洩漏 CPU 內部 buffer（L1D fill buffer、store buffer、load port）的殘餘資料。MD_CLEAR 在 CPUID 裡新增一個 capability bit，表示 CPU 支援用 `VERW` 指令清空這些 buffer。kernel 在從 kernel mode 返回使用者模式前執行 `VERW`，清空 buffer，讓攻擊者從使用者態沒有辦法採樣到跨邊界的資料。

本機 CPU flags 包含 `md_clear`，表示支援。但本機 sysfs 顯示：

```
mmio_stale_data: Vulnerable: Clear CPU buffers attempted, no microcode
```

「嘗試清 buffer 但沒有微碼更新」——表示 kernel 有嘗試用 `VERW` 緩解，但 i7-10700 對 MMIO Stale Data 的完整修復需要更新的微碼，本機的微碼版本不夠新，防禦不完整。這是「部分緩解」的真實案例。

**Enhanced IBRS（Indirect Branch Restricted Speculation）**

IBRS 是 Intel 為 Spectre-v2 推出的硬體控制位。設定後，CPU 限制推測執行中的間接分支預測，讓 BTB 跨權限污染更困難：

```
spec_v2: Mitigation: Enhanced / Automatic IBRS; IBPB: conditional; PBRSB-eIBRS: SW sequence; BHI: SW loop
```

「Enhanced IBRS」（eIBRS）是 IBRS 的改進版——不像舊 IBRS 每次 kernel 進入/離開都要寫 MSR（代價巨大），eIBRS 是一個持續啟用的模式，代價低很多。

本機 CPU flags：`ibrs_enhanced`——表示硬體支援 eIBRS。

**IBPB（Indirect Branch Predictor Barrier）**

```
ibpb: conditional
```

`IBPB` 是一個 barrier 指令，讓之前的 BTB 歷史不能影響之後的推測執行。「conditional」表示只在特定高風險邊界（如進出 VM、切換行程到不同信任域）才執行，而非每次 syscall 都執行——因為 IBPB 代價很高（幾百 cycles）。

**STIBP（Single Thread Indirect Branch Predictors）**

```
stibp
```

SMT 環境下，兩個 hardware thread 共用同一個 BTB，一條 thread 的 BTB 填充可以影響另一條 thread 的推測行為。STIBP 把 BTB 隔離到每個 logical CPU，讓跨 thread BTB 污染不可行。代價：約 2–10%，依工作負載。

**SSBD（Speculative Store Bypass Disable）**

```
spec_store_bypass: Mitigation: Speculative Store Bypass disabled via prctl
ssbd: (CPU flag)
```

Spectre Variant 4（SSB）洩漏：store 和後續 load 之間存在 forwarding 推測，load 在 store 的寫入「尚未確認」時就推測性地讀了舊值。SSBD 讓 CPU 不再推測 store forwarding——代價是所有 store/load 都要等 store 完成，store-load forwarding 效能損失。

Linux 通過 `prctl(PR_SET_SPECULATION_CTRL)` 讓行程選擇啟用（安全敏感行程）或不啟用（高效能行程），屬於「per-process opt-in」模型。

---

## 「攻擊→防禦→殘餘風險」完整對照表

| 攻擊 | 類型 | 章節 | 主要防禦 | 防禦層次 | 殘餘風險 |
|------|------|------|---------|---------|---------|
| Flush+Reload | cache 側信道 | Ch 6–7 | 關閉 clflush（不實際）；頁面去共享；noise | 軟體/OS | 合法共享記憶體場景（KSM、共享 library）仍可能；noise 只提高 SNR 閾值 |
| Prime+Probe | cache 側信道 | Ch 8 | Intel CAT（Cache Allocation Technology）做 cache 分區 | 硬體 | 不支援 CAT 的 CPU；L3 分區犧牲利用率；LLC 共享仍存在（Cloud） |
| Evict+Reload | cache 側信道 | Ch 8 | 同 Prime+Probe；去除記憶體共享 | OS | 不需要 clflush，去共享後需進一步隔離 page table |
| Flush+Flush | cache 側信道 | Ch 10 | 同 Flush+Reload；計時 clflush 本身 | 軟體/OS | 完全防禦需禁用 clflush；無實際方法 |
| 跨 VM LLC 洩漏 | cache 側信道 | Ch 12 | CAT 分區；物理 CPU 隔離（dedicated host） | 硬體/雲端策略 | CAT 粒度有限（通常 2 MiB 為最小單元）；多租戶必然共享 LLC |
| crypto cache timing | cache 側信道 | Ch 11 | constant-time 演算法實作（AES-NI/AVX）；table lookup 替換 | 軟體 | 廠商/開源庫不一致；舊版 OpenSSL 仍在生產環境跑 |
| Spectre-v1（BCB） | 推測執行 | Ch 14 | `lfence`；`array_index_nospec`；編譯器標記 | 軟體/編譯器 | 需逐個 gadget 審查；自動工具覆蓋率不完整 |
| Spectre-v2（BTI） | 推測執行 | Ch 16 | retpoline；eIBRS；IBPB | 軟體/硬體微碼 | retpoline 對新 CPU（Skylake+某些路徑）有繞過；BHI 繞過 eIBRS |
| Spectre-RSB | 推測執行 | Ch 17 | RSB stuffing（kernel 在 syscall 返回前填充 RSB） | 軟體 | RSB underflow 在深調用棧中仍可能 |
| Meltdown | 亂序執行 | Ch 18 | KPTI（kernel 頁從使用者 page table 移除）；硬體修復（Comet Lake+） | OS/硬體 | KPTI 效能衝擊（5–30%）；舊硬體（pre-Cascade Lake）需完整 KPTI |
| MDS（MFBDS/MLPDS）| CPU buffer | Ch 19 | MD_CLEAR（VERW）；關 SMT | 微碼/OS | 微碼不完整時（本機 mmio_stale_data）只部分緩解；關 SMT 效能代價大 |
| L1TF/Foreshadow | 推測+L1D | Ch 19 | L1D flush on VM exit；關 SMT；KPTI | OS/微碼 | Cloud 環境跨 VM 仍需謹慎；L1D flush 每次 VM exit 代價高 |
| MMIO Stale Data | CPU buffer | Ch 20 | MD_CLEAR + 新微碼 | 微碼 | **本機：Vulnerable（微碼不夠新）**；舊硬體需廠商更新 |
| Rowhammer（基礎）| DRAM | Ch 22 | TRR（Targeted Row Refresh）；增加 refresh 頻率 | DRAM 控制器 | TRR 可以被繞過（TRRespass）；refresh 加倍犧牲功耗與效能 |
| Rowhammer（進階）| DRAM | Ch 23 | ECC memory（偵測 1-bit flip）；LPDDR5 enhanced TRR | 硬體 | ECC 只偵測奇數個 bit flip；多 bit flip 可能通過 ECC；ECC DRAM 成本高 |
| Hertzbleed | 頻率側信道 | Ch 25 | 關閉 frequency boost（AVX-512 相關）；constant-time 確保功耗不隨資料變化 | 軟體/BIOS | 根本機制（DVFS 依功耗調頻）難根除；constant-time 程式碼功耗仍不完全均一 |
| Port contention（SMoTherSpectre）| 執行單元爭用 | Ch 26 | 關 SMT（最有效）；Core scheduling | OS/BIOS | 關 SMT 效能代價高；Core scheduling 不完全隔離所有資源 |
| TLB/BTB timing | TLB/分支 | Ch 27 | KPTI（TLB 角度）；IBPB（BTB 角度）；eIBRS | OS/微碼 | IBPB 代價高，只在邊界執行；新的 BTB 攻擊（BHI）繞過 eIBRS |
| KASLR timing break | prefetch/TLB | Ch 28 | KPTI；UDEREF；`kptr_restrict` | OS | 仍有其他 KASLR break 路徑（資訊洩漏 gadget）；KPTI 效能代價 |

---

## 為何沒有銀彈：效能與安全的永恆取捨

每個防禦都有效能代價，這不是工程失誤，而是物理必然：

```
防禦                    典型效能代價（依工作負載）
────────────────────────────────────────────────
KPTI                   syscall 密集：5–30%
                        計算密集（少 syscall）：< 1%

eIBRS（vs 無防禦）     間接跳轉密集：5–15%
                        大多數工作負載：< 3%

IBPB（每次 context switch）  排程密集：10–20%
                              正常服務器：< 5%

關 SMT                 多執行緒工作負載：約 50%（核心數減半）
                        單執行緒：0%

MD_CLEAR（VERW）       syscall 密集：3–8%
                        計算密集：< 1%

retpoline              間接跳轉密集（kernel 進出頻繁）：約 5%
                        正常 userspace：< 2%
```

這些數字加起來，完全防禦的系統（全部防禦都開，SMT 關）可能損失 30–60% 效能。這是現實世界沒有「開全部防禦」的原因：**防禦選擇本質是威脅模型下的工程決策**，而不是「安全 vs 不安全」的二元選擇。

---

## 威脅模型驅動防禦選擇

不同部署環境的威脅模型根本不同，最優防禦集也因此不同。

### 情境一：雲端多租戶（IaaS）

**威脅**：同一個物理主機上跑著不相信任的 VM，攻擊者可以租一台 VM，用微架構側信道讀鄰居 VM 的資料。

**特有風險**：
- 跨 VM LLC 洩漏（共享 L3 cache）
- L1TF/MDS 洩漏（SMT 共享 L1D/buffer）
- BTB 跨 VM 污染（共享分支預測器）

**推薦防禦集**：
- 強制啟用 KPTI、eIBRS、IBPB（每次 VM exit）
- L1D flush on VM exit（L1TF 緩解）
- Core scheduling 或關 SMT（MDS/port contention）
- Intel CAT 做 LLC 分區（Premium tier）
- 高安全等級客戶提供 dedicated host（物理隔離，最貴但最乾淨）

**可以接受的取捨**：效能代價由多租戶定價模型吸收；安全服務(premium tier)收費更高。

### 情境二：個人 PC 或工作站

**威脅**：本機跑著不相信任的程式（瀏覽器 JS、下載的 binary、containerd 裡的第三方程式碼），攻擊者試圖從使用者態讀其他行程的秘密。

**與雲端的差異**：
- 沒有跨 VM 邊界，主要風險是跨行程
- 瀏覽器 JS 的高精度計時 side channel 是主要威脅
- Spectre-v1 gadget 在 kernel 是主要風險

**推薦防禦集**：
- 保持 kernel 更新（KPTI、array_index_nospec 一起來）
- 瀏覽器端：降低 `performance.now()` 精度（Firefox/Chrome 已預設 20µs 粒度）、SharedArrayBuffer 需 COOP/COEP header
- 不必關 SMT（本機同一個使用者的行程通常互信）
- 開 SSBD（prctl per-process，只有 secret-sensitive 行程需要）

**可以接受的取捨**：SMT 不用關，效能基本不變；瀏覽器精度降低影響很小。

### 情境三：嵌入式系統與 IoT

**威脅**：固件更新機制被攻擊；同一晶片上跑著不同信任度的程式碼（TEE + REE 雙態）；物理接觸攻擊（Rowhammer on LPDDR）。

**特有差異**：
- ARM Cortex-A/M 而非 x86，防禦機制完全不同（ARM 的 CSV2/CSV3、v8.5 的 BTI/MTE）
- 微碼更新通常不可能（出廠後不更新），硬體設計是唯一的防線
- TrustZone 隔離依賴正確的 world switch 實作

**推薦防禦集**：
- 選用有 MTE（Memory Tagging Extension）的 ARMv8.5+ 晶片（Rowhammer/UAF 緩解）
- 開 BTI（Branch Target Identification，ARM 版 CET）
- 確保 TrustZone 的 NS bit 切換正確、不洩漏 secure world 位址到 normal world
- 固件在設計時就用 constant-time 密碼庫（如 mbedTLS 的 CT 模式）

**可以接受的取捨**：嵌入式通常不開 SMT（ARM Cortex-M 根本沒有 SMT）；KPTI 類似的機制由 TrustZone 處理；最大投資放在「防止固件被改」而非「防止側信道」（實體防護優先）。

---

## 本機防禦狀態對照

以下是 i7-10700 WSL2 Ubuntu 22.04 的防禦全貌，對照 sysfs 真實輸出：

```
gather_data_sampling:    Unknown: Dependent on hypervisor status
ghostwrite:              Not affected
indirect_target_selection: Mitigation: Aligned branch/return thunks
itlb_multihit:           KVM: Mitigation: Split huge pages
l1tf:                    Not affected                    ← 硬體修（Comet Lake+）
mds:                     Not affected                    ← 硬體修 + MD_CLEAR
meltdown:                Not affected                    ← 硬體修，KPTI 仍啟用
mmio_stale_data:         Vulnerable: Clear CPU buffers attempted, no microcode
old_microcode:           Not affected
reg_file_data_sampling:  Not affected
retbleed:                Mitigation: Enhanced IBRS
spec_rstack_overflow:    Not affected
spec_store_bypass:       Mitigation: Speculative Store Bypass disabled via prctl
spectre_v1:              Mitigation: usercopy/swapgs barriers and __user pointer sanitization
spectre_v2:              Mitigation: Enhanced / Automatic IBRS; IBPB: conditional;
                          PBRSB-eIBRS: SW sequence; BHI: SW loop, KVM: SW loop
srbds:                   Unknown: Dependent on hypervisor status
tsa:                     Not affected
tsx_async_abort:         Not affected
vmscape:                 Not affected
```

**逐條解讀：**

- **Meltdown/L1TF/MDS/TSX** `Not affected`：Comet Lake（10 代）在矽晶片層面修掉了這幾個，不需要純軟體 workaround。代價是晶片面積/功耗，使用者感受不到。

- **mmio_stale_data：Vulnerable**：這台機器對 MMIO Stale Data Sampling 的防禦**不完整**。kernel 嘗試用 MD_CLEAR（VERW）清 CPU buffer，但 i7-10700 的這個特定變體需要微碼更新才能完整封住，而本機微碼版本不夠新。這是這台機器的**已知未修漏洞**。

- **spectre_v2**：防禦組合複雜：Enhanced IBRS（eIBRS）擋 BTI；IBPB conditional（高風險邊界才用，避免全開的效能代價）；PBRSB-eIBRS SW sequence（針對 eIBRS 的一個繞過路徑）；BHI SW loop（Branch History Injection 的 software 繞過，hw fix 不完整所以用 SW loop）。每一個 suffix 都對應一個更新的攻擊發現和對應的補充。

- **gather_data_sampling / srbds：Unknown**：WSL2 Hyper-V VM 不把這兩個的完整狀態透出來，只能知道「hypervisor 決定，不確定」。這是 VM 化讓安全評估複雜化的直接體現。

- **SMT 狀態**：`smt/control = on`，本機 16 邏輯核心全開。這台機器選擇了「不關 SMT」，因為 MDS 和 L1TF 硬體已修，port contention 側信道在個人 PC 威脅模型下不是優先考慮。

---

## 對比與取捨

| 防禦策略 | 安全提升 | 效能代價 | 部署複雜度 | 適用場景 |
|---------|---------|---------|-----------|---------|
| KPTI | 擋 Meltdown + prefetch KASLR | 5–30% (syscall 密集) | 低（kernel 預設開） | 所有環境 |
| eIBRS | 擋 Spectre-v2/Retbleed | < 5% (現代 CPU) | 低（微碼 + kernel 預設） | 所有環境 |
| retpoline | 舊 CPU 的 Spectre-v2 替代 | 5–15% (依跳轉密度) | 中（需重編譯 kernel + GCC 支援） | 舊 CPU 無 eIBRS |
| 關 SMT | 擋 MDS/port contention/L1TF | ~50% 多核工作負載 | 低（sysfs 一行） | 雲端高安全；個人 PC 非必要 |
| Core scheduling | 軟性替代關 SMT | 5–15% | 高（需 kernel 版本 + hypervisor 支援） | 雲端多租戶 |
| MD_CLEAR | 擋 MDS（部分）| 3–8% syscall 密集 | 低（微碼更新後自動）| 所有有 MDS 風險的環境 |
| Constant-time 程式碼 | 擋 cache timing/Hertzbleed | 2–10% 依演算法 | 高（需重寫密碼實作、驗證困難）| 密碼庫、金融、政府 |
| Intel CAT 分區 | 擋 LLC 側信道 | 快取使用率降低 | 高（需硬體支援 + 配置）| 雲端高安全 tier |
| 瀏覽器降精度 | 擋 JS timing 攻擊 | 幾乎無感 | 低（瀏覽器預設）| 個人 PC、Web 場景 |
| ECC DRAM | 偵測 Rowhammer 1-bit flip | 輕微延遲 | 中（換硬體）| 伺服器、高可靠性系統 |

---

## 踩雷集錦

1. **把 `Not affected` 誤讀成「完全安全」**：`Not affected` 只表示這台 CPU 對**這個已知漏洞**的硬體路徑不存在。同一行攻擊可能有其他變體（如 Spectre-v2 的 BHI），那個漏洞的狀態要看另一行。看 sysfs 要每一行都讀，不能只看某幾個關鍵字。

2. **認為 retpoline 一次性解決 Spectre-v2**：retpoline 是針對 BTI（Branch Target Injection）的 SW 緩解，但它有已知繞過路徑（PBRSB for eIBRS CPUs）。2022 年的 Retbleed 和 2023 年的 Inception 都繞過了不同版本的 retpoline/IBRS 設計。防禦需要持續跟進，不是「打一個補丁就好」。

3. **在 VM 裡看 sysfs 以為自己知道硬體狀態**：本機 `gather_data_sampling` 和 `srbds` 都顯示 `Unknown: Dependent on hypervisor status`。WSL2 的 Hyper-V 不把完整的 MSR 狀態透給 guest。如果你需要真實評估裸機的安全狀態，要在 bare metal Linux 上看 sysfs，或用 `cpuid` 指令直接查 CPU capabilities。VM 內的 sysfs 只反映「hypervisor 告訴 guest 的」，不一定是物理真相。

4. **把 constant-time 程式碼等同於「沒有洩漏」**：constant-time 的定義是「執行時間不隨秘密輸入變化」，但這不等於「功耗不隨輸入變化」（Hertzbleed）也不等於「cache footprint 不隨輸入變化」。一段程式碼同時滿足所有側信道的 constant-time 要求非常難；現實中的「constant-time」通常只針對特定威脅模型。

5. **認為關 SMT 是萬能方案**：關 SMT 確實消除了所有 SMT 共享資源導致的側信道，但它用 50% 的效能代價換到的不是「完全安全」，而是「消除了 SMT 這一類的洩漏路徑」。Spectre/Meltdown/Rowhammer 不需要 SMT，關了也沒有效果。防禦要針對威脅模型，不能靠一個開關全覆蓋。

6. **忽略微碼更新的重要性**：本機的 `mmio_stale_data: Vulnerable` 就是因為微碼版本不夠新。很多 MDS 類的防禦需要 CPU 廠商發布微碼更新才完整；kernel 的 SW workaround 是備用手段，效果不完整。生產系統要確保 intel-microcode/amd-ucode 套件在最新版本，這是常被忽略的維護工作。

---

## 進階：再往深一層

**BHI（Branch History Injection）繞過 eIBRS**：2022 年 VUSec 發現的攻擊，繞過了 eIBRS 對 Spectre-v2 的保護。eIBRS 擋住的是「不同特權級之間的 BTB 污染」，但攻擊者可以用**同特權級的歷史記錄**（branch history buffer，BHB）引導到 hypervisor/kernel 裡的 gadget，再洩漏資料。本機的 `BHI: SW loop` 是 kernel 的軟體緩解——在進入 kernel 前清空 BHB，代價是每次 kernel entry 多執行一段迴圈。硬體修復（支援 CTRL_BHB_DIS 的新 CPU）更乾淨但更貴。

**Intel TDX（Trust Domain Extensions）的不同思路**：傳統方案是「讓 host 的 hypervisor 保護 VM 裡的 guest」，但 Spectre/MDS 類攻擊讓 host hypervisor 本身變成不可信的威脅。TDX 把 VM 加密隔離，讓 hypervisor 連 guest 記憶體都讀不到，從根本改變信任模型。代價是 VM 啟動複雜、attestation 機制複雜、與現有軟體棧不相容。

**AMD SEV/SEV-SNP 的類似路線**：AMD 的 Secure Encrypted Virtualization 在 VM 記憶體層面加密，讓 hypervisor 無法讀取 guest 資料，應對 MDS/L1TF 類的跨 VM 洩漏。SNP（Secure Nested Paging）再加上對 nested page table 篡改的完整性保護。這兩個 Intel TDX / AMD SEV-SNP 代表了雲端安全的下一個架構方向——不是修補現有 side channel，而是在架構上假設「hypervisor 不可信」。

**Spectre 的根本解法爭論**：有研究者認為 Spectre 的根本解法是 CHERI（Capability Hardware Enhanced RISC Instructions，Cambridge 的架構研究），讓指標攜帶能力標籤，讓推測執行無法讀出沒有能力的記憶體。Microsoft Research AArch64 的 Morello 晶片是第一個 CHERI 的商業實作嘗試。但 CHERI 需要整個軟體棧重編譯，是 10 年以上的遷移工程——不是近期答案，是長期研究方向。

---

## 動手練習

1. **對照你自己的機器**：跑 `for f in /sys/devices/system/cpu/vulnerabilities/*; do printf "%-28s %s\n" "$(basename $f):" "$(cat $f)"; done`，對照本章的對照表，找出你的機器哪些已修、哪些有 mitigation、哪些 Vulnerable。特別注意 `Unknown` 的項目——如果你在 VM 裡，試著在 host 上也跑一次，比較差異。

2. **測量 KPTI 的效能衝擊**：用 `sysbench` 或 `lat_syscall`（lmbench）量 syscall 延遲。如果你有一台非 Meltdown-affected 的 CPU（Comet Lake+），KPTI 應該幾乎沒有衝擊（因為硬體修了，KPTI 只是額外保護）。如果你有 Haswell/Broadwell 的機器，對比有無 `nopti` 開機參數的延遲，能看到真實的 5–30% 代價。

3. **實測 SMT 關/開的效能差**：`nproc`（開 SMT 前）→ `echo off | sudo tee /sys/devices/system/cpu/smt/control` → `nproc`（關後）。用 `make -j$(nproc)` 跑一個大型編譯（如 Linux kernel），比較兩種狀態下的耗時。然後 `echo on | sudo tee /sys/devices/system/cpu/smt/control` 恢復。感受「效能代價不是理論數字，是真實的分鐘」。

4. **讀 BHI 論文的 gadget 部分**：找 [Branch History Injection, VUSec 2022](https://www.vusec.net/projects/bhi-spectre-bhb/) 的論文，讀 Section 4「Finding Gadgets」——理解攻擊者怎麼在 kernel 裡找可利用的 gadget，把這個過程跟本機的 `BHI: SW loop` 防禦對照。核心問題：SW loop 能完整擋住哪類 gadget、不能擋住哪類？

5. **驗證瀏覽器的計時降精度**：打開 Firefox 開發者工具，在 console 執行：`let t0 = performance.now(); while(performance.now() - t0 < 1); console.log(performance.now() - t0 - 1);` 重複幾次，觀察 `performance.now()` 的最小解析度（應該是 20µs 或更粗）。這個降精度是為了讓 Spectre-v1 型的 JS timing 攻擊難以測量 cache 狀態。

---

## 本章重點整理

- 微架構攻擊的防禦沒有銀彈：每個攻擊有對應的防禦，每個防禦有效能代價，新攻擊不斷繞過舊防禦。這是結構性問題，不是工程失誤。
- **三層防禦**：軟體層（constant-time、lfence、retpoline）擋程式碼本身的洩漏；OS 層（KPTI、Core scheduling）擋跨行程邊界；硬體/微碼層（MD_CLEAR、eIBRS、IBPB）在 CPU 內部封堵。
- **本機狀態**：meltdown/l1tf/mds 硬體已修；spectre 系列有多層 SW+HW 緩解；**mmio_stale_data Vulnerable**（微碼不夠新）；SMT 開啟但 MDS 已硬體修所以不需關。
- **威脅模型決定防禦選擇**：雲端多租戶最嚴（需 Core scheduling/關 SMT、L1D flush、CAT 分區）；個人 PC 中間（保持 kernel 更新 + 瀏覽器降精度）；嵌入式靠硬體設計（MTE/BTI/TrustZone）。
- 「攻擊→防禦→殘餘風險」三元組是評估任何微架構防禦的正確框架：問清楚「這個防禦擋的是哪個攻擊的哪個環節」「它做不到什麼」。
- 微碼更新不是可選項：`mmio_stale_data` 的 `no microcode` 正是說明，SW workaround 只是備用，硬體廠商的微碼更新是根本解。

---

## 自我檢核

- [ ] 說出三個「為什麼沒有銀彈能一次性解決微架構側信道」的結構性原因，不要用「太複雜」搪塞，要能具體說每個原因。
- [ ] 給你一個攻擊（例：MDS/MFBDS），你能說出它的主要防禦是什麼、防禦的代價是什麼、殘餘風險在哪裡嗎？把對照表裡任意三列背後的邏輯都能還原。
- [ ] 面試問「雲端多租戶和個人 PC 的防禦策略有什麼不同」，你能給一個有具體技術點的回答，不是「雲端更重要所以防禦更強」的廢話嗎？
- [ ] 本機的 `mmio_stale_data: Vulnerable` 代表什麼風險？攻擊者需要哪些條件才能利用它？
- [ ] KPTI 同時擋住了哪兩個攻擊類型？為什麼一個 mitigation 能同時有效？
- [ ] BHI 是怎麼繞過 eIBRS 的？為什麼 eIBRS 沒辦法防住它？本機的 `BHI: SW loop` 緩解是怎麼運作的？

---

## 延伸閱讀

### 論文

- **[Transient Execution Attacks](https://arxiv.org/abs/1811.05441)** — Canella et al., IEEE S&P 2019
  - **讀哪裡**：Section 5「Mitigations」——系統性地整理了所有已知瞬態執行攻擊的防禦，包含哪些防禦互相依存、哪些可以獨立部署。
  - **學到什麼**：防禦的正式分類框架；哪些 mitigation 是「必須」、哪些是「建議」。
  - **為什麼值得**：這是整個領域最全面的 survey，是評估防禦組合的學術基礎。

- **[Branch History Injection: On the Effectiveness of Hardware Mitigations Against Cross-Privilege Spectre-v2 Attacks](https://www.vusec.net/projects/bhi-spectre-bhb/)** — Barberis et al., USENIX Security 2022
  - **讀哪裡**：Section 3（BHI 攻擊機制）和 Section 6（Intel/AMD 的回應）。
  - **學到什麼**：為什麼 eIBRS 不能完整擋 Spectre-v2；`BHI: SW loop` 是怎麼設計出來的；攻擊者怎麼在 kernel 裡找 disclosure gadget。
  - **為什麼值得**：直接對應本章 `spectre_v2: BHI: SW loop` 這一行的來源。

- **[RIDL: Rogue In-flight Data Load](https://mdsattacks.com/)** — van Schaik et al., IEEE S&P 2019
  - **讀哪裡**：Section 4（攻擊細節）和 Section 7（對比 MD_CLEAR 的有效性）。
  - **學到什麼**：MDS 的具體 exploit 路徑；為什麼 MD_CLEAR 只是「清 buffer」而不是完整修復；微碼更新的必要性。
  - **為什麼值得**：本章 `mmio_stale_data: Vulnerable: no microcode` 這行的背景就在這裡。

### 官方文件

- **[Intel Microarchitectural Data Sampling Advisory + INTEL-SA-00233](https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/advisory-guidance/microarchitectural-data-sampling.html)**
  - **讀哪裡**：Affected Products 表（判斷你的 CPU 需要哪些 mitigation）；Mitigation Options 的優先順序清單。
  - **學到什麼**：Intel 自己對每個型號 CPU 的 MDS 建議防禦組合；微碼版本號對應的修復項目。
  - **為什麼值得**：這是「哪個 CPU 需要什麼微碼才能修好 mmio_stale_data」的官方回答。

- **[Linux kernel documentation: hw-vuln/](https://www.kernel.org/doc/html/latest/admin-guide/hw-vuln/)** — kernel.org
  - **讀哪裡**：每個子頁（spectre.rst、mds.rst、l1tf.rst…），每個都有「Mitigation selection guide」章節。
  - **學到什麼**：kernel 提供的所有開機參數（`spectre_v2=off`、`l1tf=full,force`…）的意義；在什麼情況下可以關掉某個 mitigation 節省效能。
  - **為什麼值得**：部署防禦前的必讀文件；懂這個才能把 sysfs 輸出的每一行跟開機參數對應起來。

### 技術部落格

- **[Daniel Gruss 的個人頁 + TU Graz System Security](https://gruss.cc/)** — Daniel Gruss
  - **讀哪裡**：Publications 頁，按年份看 2018–2024 的新攻擊（Meltdown、ZombieLoad、Spectre-BHB/Inception）是怎麼一代一代出來的，對應的防禦怎麼加。
  - **學到什麼**：「攻擊→防禦→新攻擊繞過→新防禦」的迭代模式的第一手時間線，直觀感受為什麼這場攻防永遠不會結束。

本章把課程 Part 2–4 的所有攻擊串成了一張防禦地圖。下一章把視野拉到「隔離」這個更大的架構概念：從 process isolation 到 VM isolation，從 hardware enclave（SGX/TDX）到 language-level isolation，看不同隔離邊界怎麼設計、各自能擋什麼、各自的 trade-off 是什麼。

→ [Ch 30 隔離類防禦](30-isolation-defenses.md)
