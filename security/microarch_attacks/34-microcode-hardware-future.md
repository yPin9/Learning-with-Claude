# Ch 34 — 微碼與硬體防禦的未來

> **目標**：讀完能解釋微碼更新（microcode update）的機制與限制、VERW/MD_CLEAR 如何緩解 MDS、DIT/DOIT bit 賦予 constant-time 的硬體合約、推測隔離（speculative isolation）和 STT 的設計方向，並對「為什麼硬體修這麼慢、未來能不能根治」給出有根據的答案。

---

這章是 Part 5「防禦」的收束章，也是整門課的終章。我們花了四章分別談 retpoline（Ch 31）、constant-time（Ch 32）、偵測框架（Ch 33），現在退後一步，問一個更根本的問題：**這些方案真的夠嗎？硬體本身能做什麼？**

簡短的答案：軟體緩解有效但有代價、有漏洞、靠工程師自律；真正治本的修法必須從 CPU 設計下手。而 CPU 設計的週期，動輒五年起跳。

---

## 軟體緩解的天花板

快速回顧我們學過的三種軟體層方案：

- **Retpoline**（Ch 31）：把間接跳轉換成推測方向可控的「無底洞」loop，有效繞開 BTB（Branch Target Buffer）預測的攻擊面。代價是每次間接呼叫多跑幾個指令，在 syscall-heavy 的 workload 上性能退步 5–15%。更重要的是，Retbleed（2022）證明 retpoline 在特定 AMD Zen 3 上仍然可利用——「已修」不等於「永遠安全」。

- **Constant-time 程式設計**（Ch 32）：靠工程師人工保證每個密碼學操作的執行時間不依賴 secret。這是一種工程紀律，沒有語言層面或硬體層面的強制保證。編譯器最佳化、CPU 的 power-gating、甚至微碼版本升級都可能破壞人工審查通過的 constant-time 屬性。

- **偵測框架**（Ch 33）：SpecFuzz、CacheQuery 這類工具偵測潛在的推測執行泄漏路徑。偵測是輔助，不是防禦；漏掉一個 gadget，攻擊者照樣能打。

三條路的共同問題：**它們都在「CPU 行為已經決定」之後介入**。CPU 該推測的照推測、該在 BTB 留下痕跡的照留——軟體只是在事後減少傷害。要從源頭切斷，必須改 CPU 本身的行為。

---

## 微碼更新（Microcode Update）的機制

### 什麼是微碼

x86 是個 CISC ISA（Complex Instruction Set Computer Instruction Set Architecture）。一條 `REP MOVS`、`FXSAVE`、甚至 `CPUID` 在 CPU 內部會被拆解成數十個更簡單的**微操作（μops, micro-operations）**。這個拆解過程靠的是一張「解碼表」，也就是**微碼（microcode）**。

微碼是 CPU 內部的韌體，儲存在一塊小型 ROM/RAM 裡（製造商稱之為 microcode ROM）。對外它是不可見的，但它決定了「一條 x86 指令在矽晶體上怎麼跑」。

### 更新路徑

```
BIOS/UEFI 內嵌的微碼 blob
    ↓  (最早載入，CPU 從 reset vector 出來後立刻套用)
OS 開機初期再次更新 (late microcode load)
    ↓  Linux: /dev/cpu/microcode  或  intel-microcode package
    ↓  Windows: Windows Update 內嵌在驅動層
CPU 的 MSR IA32_BIOS_UPDT_TRIG (0x79) 接收更新 blob
```

Linux 載入微碼的方式有兩個階段：
- **Early load**：initramfs 裡放 `/lib/firmware/intel-ucode/` 目錄，kernel 在掛載根目錄前就先更新，確保系統開機時就是新版微碼。
- **Late load**：`/dev/cpu/microcode` 的 write 介面，可以在系統運行中更新，但有風險（SMI 時序問題），多數發行版不建議。

確認當前版本：

```bash
# 方法 A：rdmsr
rdmsr 0x8B   # IA32_BIOS_SIGN_ID，低 32 bits 是版本號

# 方法 B：/proc/cpuinfo
grep -m1 microcode /proc/cpuinfo
# 輸出範例：microcode : 0xf4
```

### 微碼能改什麼、改不了什麼

| 能改 | 改不了 |
|------|--------|
| 指令的解碼行為（加新行為、禁用某些 μops 序列） | 物理電路（電晶體連接、charge 殘留） |
| 少數控制邏輯（如 speculation depth 的軟旗標） | BTB/PHT/L1D 的物理儲存結構 |
| 加入新的「虛擬指令」語義（如 VERW 的 MD_CLEAR 行為） | DRAM 的 charge leak 現象（Rowhammer） |
| CPUID leaves 的回傳值（宣告新 capability） | Cache 的物理分組方式 |

這個表非常重要。微碼是「軟體定義的解碼層」，但它架在不可改的物理電路上。Rowhammer（Ch 22–24）的根源是 DRAM cell 之間的電磁耦合，微碼根本碰不到。

---

## 具體案例：VERW / MD_CLEAR（MDS 緩解）

MDS（Microarchitectural Data Sampling，Ch 19）的攻擊面是 CPU 內部的幾個暫存緩衝區：
- **LFB（Line Fill Buffer）**：等待填入 L1D 的資料
- **Store Buffer**：等待寫入記憶體的資料
- **WB（Write Buffer）**：合併寫入佇列

這些 buffer 在推測執行期間可能包含來自其他 privilege level 的 stale data，而推測執行可以在它們清空之前把值讀出來（RIDL、Fallout 等變體）。

### MD_CLEAR 的設計

Intel 在 2019 年 5 月的微碼更新中加入了 **MD_CLEAR capability**（透過 CPUID leaf 7 的 MD_CLEAR bit 宣告）。這個更新重新定義了 `VERW` 指令的語義：

- 原本：`VERW r/m16` 只是驗證一個記憶體段的寫入權限，沒有副作用。
- 更新後：執行 `VERW` 時，CPU 額外清空 LFB、Store Buffer、Write Buffer。這個清空動作由微碼實作，在 μop 序列裡插入 buffer flush 操作。

Linux kernel 的 context_switch 路徑在啟用 MDS 緩解時插入 `VERW`：

```c
/* arch/x86/kernel/process.c (概念示意) */
static inline void mds_clear_cpu_buffers(void)
{
    static const u16 ds = __KERNEL_DS;
    asm volatile("verw %[ds]" : : [ds] "m" (ds) : "cc");
}
```

這是「微碼可以加新行為」的最佳範例——在不換 CPU 的前提下，透過微碼讓一條已存在的 x86 指令多做一件事（清 buffer）。

但這有代價：context switch 頻繁的工作負載（高並行 web server、資料庫）性能退步可達 3–8%。更重要的是：**微碼加不了 cache partition**。如果漏洞的根源是 L1D 的 set-associative 結構被不同 privilege 共用，`VERW` 幫不了你，因為那是物理電路。

---

## DOIT bit / DIT bit（Data-Operand-Independent Timing）

Ch 32 的 constant-time 程式設計，靠的是工程師人工確保「程式碼裡沒有 secret-dependent 的 branch 和 memory access」。但這個保證有個漏洞：**CPU 本身不提供合約**。同一條組合語言指令在不同資料值下可能有不同的執行時間（乘法的 early-out、除法的 iterative unit），工程師寫了 constant-time 的 C，編譯出來的機器碼不一定 constant-time。

DOIT/DIT 的想法是：讓 CPU 提供一個**硬體層面的 timing contract**。

### ARM DIT bit（ARMv8.4）

ARM 在 ARMv8.4 引入了 **DIT（Data Independent Timing）** feature。處理器狀態暫存器 PSTATE 裡的 DIT bit 設為 1 時，以下類型的指令保證執行時間不相依資料值：
- 整數算術（ADD、SUB、MUL）
- 位移（LSL、LSR、ASR）
- 部分邏輯運算

設定方式：

```asm
// AArch64 組合語言
MSR DIT, #1          // 開啟 DIT
// ... constant-time 操作 ...
MSR DIT, #0          // 關閉（恢復正常效能模式）
```

GCC 沒有直接的 intrinsic，通常包成 inline asm helper：

```c
/* 概念示意 */
static inline void enable_dit(void) {
    asm volatile("MSR DIT, #1" : : : "memory");
}
```

ARM Cortex-A55、A75 以後的核心已實作 DIT。確認方式：讀取 `ID_AA64PFR0_EL1` 的 DIT bits（位元 51:48）。

### Intel DOIT 提案

Intel 提出了類似的 **DOIT（Data-Operand-Independent Timing）** 概念，針對 AVX-512（Advanced Vector Extensions 512-bit）部分指令：設定特定控制旗標後，保證向量整數運算的 latency 不依賴操作數值。截至 2026 年，DOIT 仍在提案/實驗階段，尚未進入量產 CPU 的 ISA 規格。

### DIT 的意義和限制

DIT 給 constant-time 程式設計一個硬體合約：只要在 DIT 模式下，整數乘法的 timing 不會因為操作數有多少個 leading zero 而不同。這解決了「CPU 自己不配合」的問題。

但 DIT **不保證：**
- 記憶體存取的 timing（cache 行為由 cache 決定，不受 DIT 影響）
- 分支預測（secret-dependent branch 的 speculation side-channel 仍然存在）
- 演算法層面的 secret-dependent 控制流（DIT 是指令層面的合約，不是演算法層面）

換句話說，DIT 縮小了需要人工保證的範圍，但沒有消除它。

---

## 推測隔離（Speculative Isolation）

Spectre 的根本問題是：CPU 在推測執行期間會污染共用的微架構狀態（BTB、PHT、cache）。不同 privilege domain 的程式碼共用這些狀態，就等於隔著一道「可觀察的共用牆」互相洩漏資訊。

推測隔離的方向是：**讓不同 privilege domain 的推測狀態不共用，或者讓跨 domain 的推測讀取無效。**

### Intel eIBRS（Enhanced IBRS）

原始的 IBRS（Indirect Branch Restricted Speculation）在每次 privilege level 切換時強制刷新分支預測器狀態，代價非常高。eIBRS（Enhanced IBRS）改成 per-core 的持久性隔離：

- 核心狀態被標記為 kernel-mode 或 user-mode
- kernel-mode 的 BTB entries 不允許被 user-mode 的推測利用
- 不需要每次 context switch 都刷新，代價從「每次 syscall」降到「系統啟動設一次」

代價仍然存在（約 2–5%），但比原始 IBRS 的 10–20% 好得多。

**eIBRS 的已知繞過**：Retbleed（2022）在 AMD Zen 3 上（使用 eIBRS 的 Linux kernel 啟用了 retpoline）仍然有效，因為 retpoline 本身的 `RET` 指令在這些核心上可被 RSB（Return Stack Buffer）偽造，而 eIBRS 沒有保護 RSB 的 user→kernel 過渡路徑。

### 未來方向：完整推測沙盒

理論上最徹底的方案：每個 privilege domain 維護獨立的 prediction state（BTB、PHT、RSB、STLB），跨 domain 切換時 prediction state 完全隔離。代價：每次 syscall 都要切換整個預測器狀態，等同於把 TLB flush 的代價加到 predictor 上。現代系統每秒可能發生數百萬次 syscall，這個方案在現實中代價巨大。

Intel 在後 Spectre 世代的設計文件裡承認這個方向，但沒有給出量產時間表。

---

## Secret Tracking（STT / DOLMA）

STT（Speculative Taint Tracking）是學術界提出的一個方向（Taram et al., 2019）：讓 CPU 在硬體層面追蹤「這個 register 的值來自推測執行路徑」（tainted），並且禁止 tainted 值影響 cache 存取地址或其他可觀察的微架構操作。

```
正常執行（committed）: 值可以影響記憶體地址 → 允許
推測執行（not yet committed）: 值 tainted → 任何以 tainted 值為基礎的記憶體讀取被延遲或無效化
```

**DOLMA**（Islam et al., 2019）是 STT 的延伸，把 taint 域擴大到更多微架構結構，試圖覆蓋 STT 遺漏的洩漏路徑。

### STT/DOLMA 的現實問題

推測執行的本質就是「在指令 commit 之前先跑」，所以幾乎所有推測執行的值都會被 taint。要讓 STT 不把性能打垮，需要極精細的 taint propagation 和 early de-taint（確認值其實不依賴 secret）。

目前的模擬研究顯示 STT 的性能代價在 5–15%，但沒有量產 CPU 實作過完整的 STT。主要障礙：
- Taint 位元需要跟著每個 μop 和 register file entry 流動，面積和功耗增加
- Taint propagation 的保守性越高，誤報越多，性能越差
- Taint propagation 不夠保守，就會有漏洞

---

## ARM MTE（Memory Tagging Extension）

MTE（Memory Tagging Extension）是 ARMv8.5 引入的功能，主要設計目標是記憶體安全（偵測 use-after-free、buffer overflow），但對 Rowhammer（Ch 22–24）有間接的防禦意義：

- 每個 16-byte aligned 的記憶體區域可以附帶一個 4-bit tag
- 指標本身也帶一個 4-bit tag（存在指標的高位）
- 存取時 CPU 比對指標 tag 和記憶體 tag，不符合就 fault

Rowhammer 的攻擊路徑通常包含：hammer 某個實體頁、flip 到目標結構的 bit、然後讀取或寫入錯誤的資料。MTE 讓「讀取翻轉後的 tag-mismatch 資料」更容易被偵測，但不阻止 bit flip 本身（bit flip 發生在 DRAM 電路層，MTE 是 CPU 的功能）。

MTE 的存在代表 ARM 的設計方向：把「存取權限」明確標記進指標和記憶體，而不只是依靠頁表保護。這個方向和 Spectre/Meltdown 的防禦哲學（隔離存取）是一致的。

---

## 為什麼硬體修得慢

一顆現代 CPU 從設計到量產：

```
需求規格 → RTL 設計（Register Transfer Level）
    → 功能驗證（simulation + formal verification，6–18 個月）
    → 時序收斂（Synthesis + Place-and-Route）
    → Tape-out（把最終 GDS 檔送進晶圓廠）
    → 量產（良率調整，3–6 個月）
    → 出貨
總計：通常 3–5 年（複雜架構可達 6–8 年）
```

Spectre/Meltdown 在 2018 年初公開，第一批「設計層面有緩解」的 Intel CPU（Ice Lake，有 IBRS-always 等硬體緩解）在 2019 年底才出貨。從公開到有硬體修，最快也要近兩年。

微碼更新可以快得多（幾個月），但只能動「軟體定義的解碼層」：

- 加 `VERW` 的 MD_CLEAR 語義：可以，因為 buffer flush 的邏輯只需要微碼序列
- 修改 BTB 的物理分組方式：不行，那是電路
- 修 DRAM 的 charge leak：絕對不行

### 舊 CPU 的長尾問題

市場上一台資料中心伺服器的使用壽命是 5–10 年，工業控制系統甚至 10–20 年。即使 2024 年出廠的 CPU 已經有完整的硬體緩解，2016 年出廠的 Broadwell 還在跑生產系統。

更糟的是：Intel 的微碼更新有硬體世代限制，老到一定程度的 CPU 不再收到新微碼（Ivy Bridge 在 Spectre 公開後只收到部分更新，Sandy Bridge 幾乎沒有）。軟體緩解（retpoline、KPTI）對這些 CPU 是唯一的防線，不可能廢除。

---

## 對比與取捨

| 防禦方向 | 防禦覆蓋面 | 性能代價 | 部署時間線 | 備注 |
|---------|-----------|---------|-----------|------|
| 微碼更新（Microcode）| 解碼層行為；不含物理電路 | 低~中（視更新內容）| 數月（BIOS/OS 更新）| 不覆蓋 Rowhammer；舊 CPU 可能不支援 |
| DOIT/DIT bit | 指令層面 timing contract | 低（僅影響設定位元的 routine）| ARMv8.4 已量產；Intel DOIT 未量產 | 不保證 cache timing；不保護 branch |
| 推測隔離（eIBRS/完整沙盒）| Spectre-v2 家族 | 中（eIBRS 2–5%；完整沙盒可達 20%+）| eIBRS 已量產；完整沙盒是研究方向 | Retbleed 繞過 eIBRS；完整沙盒無量產計畫 |
| STT/DOLMA | 理論上覆蓋所有 transient execution | 高（5–15%，且有設計難點）| 無量產實作 | 學術提案；面積/功耗成本未解決 |
| MTE | 記憶體存取合法性（非直接防 Spectre）| 低（5% 以下）| ARMv8.5 已量產 | 間接協助 Rowhammer 偵測；不阻止 bit flip |

---

## 軍備競賽的未來展望

歷史告訴我們一件事：**每次「這個攻擊已修」之後，研究者都會在同一批 CPU 上找到新洞。**

- 2018：Spectre-v1/v2/v3（Meltdown）
- 2018：Spectre-NG（包括 Spectre-v3a/v4）
- 2019：MDS（RIDL、Fallout、ZombieLoad）
- 2022：Retbleed（在 eIBRS + retpoline 系統上有效）
- 2022：SQUIP（AMD Zen 3 的 Scheduler Queue 洩漏）
- 2023：Downfall（Intel AVX gather 指令的 GDS 洩漏）
- 2023：Inception（AMD Zen 3/4 的 phantom branch 洩漏）

修一條邊界往往暴露另一條邊界。推測執行的設計哲學是「先跑、遇錯再丟棄」，這個哲學在安全上天然衝突：「遇錯再丟棄」沒有丟掉微架構的副作用。

**徹底根治的代價**：如果完全禁用推測執行（OoO 執行），Spectre 家族整個消失，但現代高效能 CPU 大約 2–5 倍的 IPC 優勢也消失。Intel/AMD 不可能走這條路，Google 的 Spectre-hardened kernel 也只在特定環境下用。

**RISC-V 的可能性**：RISC-V 的指令集比 x86 乾淨，沒有 CISC 的歷史包袱，理論上更容易設計安全擴展（DOIT、STT 之類的機制更容易整合進 ISA 規格）。但「ISA 設計上更容易」和「市場真的大規模部署有硬體緩解的 RISC-V 伺服器」是兩件事，後者目前還沒發生。

---

## 踩雷集錦

**1. 「裝了微碼更新就完全安全」**

微碼更新只改解碼層行為。Rowhammer（Ch 22–24）的根源是 DRAM 電磁耦合，微碼沒有任何機制觸及 DRAM 物理特性。裝了最新微碼，Rowhammer PoC 照樣能打。

**2. 「設了 DIT/DOIT 就不用寫 constant-time code」**

DIT 保證的是「指令層面的執行時間不依賴操作數值」，覆蓋範圍是整數算術和位移。它不覆蓋：
- 記憶體存取的 cache timing
- 演算法中 secret-dependent 的 if/else 分支
- 任何使用 DIT 未宣告保護的指令的操作

Ch 32 的 constant-time 技法——避免 secret-dependent branch、確保存取 pattern 固定——在 DIT 存在的情況下仍然必要。DIT 縮小了需要人工保證的範圍，沒有消除它。

**3. 「eIBRS 修好了 Spectre-v2」**

eIBRS 確實大幅縮小了 Spectre-v2 的攻擊面，但 Retbleed（2022）證明它不夠。在 AMD Zen 3 和特定 Intel 核心上，retpoline + eIBRS 的組合在 RSB 下溢的條件下仍然可以被利用。eIBRS 的假設是「kernel BTB entries 不能被 user 利用」，Retbleed 繞的是 RSB（Return Stack Buffer），不是 BTB。「eIBRS 修好 Spectre-v2」是 2019–2021 年的共識，Retbleed 打破了它。

**4. 「軟體 patch 等等就好，不用換新 CPU」**

部分 CPU 的微碼更新只支援特定製造批次（stepping）。以 Intel Sandy Bridge 為例：Spectre 公開後，Intel 宣布停止更新這個世代的微碼（2018 年 4 月），理由是設計複雜度和測試成本。這批 CPU 永遠不會收到微碼層面的 Spectre 修復，只能靠 OS 的軟體緩解（retpoline），而且某些軟體緩解無法完整部署（需要 IBRS/IBPB 的 CPU feature，老 CPU 沒有）。

---

## 進階：再往深一層

### Intel TDX / AMD SEV：把 hypervisor 移出威脅模型

Intel TDX（Trust Domain Extensions）和 AMD SEV-SNP（Secure Encrypted Virtualization-Secure Nested Paging）的目標是：**讓 VM 不需要信任 hypervisor**。

傳統虛擬化的威脅模型假設 hypervisor 可信。但如果 hypervisor 被攻陷（或者 hypervisor 本身是惡意的，如惡意的雲端提供者），它可以讀取 VM 的記憶體。TDX/SEV 的做法：

- VM 的記憶體加密，hypervisor 沒有解密金鑰
- 記憶體完整性保護（SEV-SNP 的 Reverse Map Table）防止 hypervisor 重映射 VM 的頁面

但 TDX/SEV 和微架構攻擊的交集是個已知問題：**TDX VM 仍然受 RIDL/MDS 影響**，直到微碼完整支援 TDX 環境下的 MD_CLEAR 語義。因為 MDS 是讀取 buffer 的 stale data，TDX 的記憶體加密在 data 已經在 CPU 內部的 buffer 裡的時候幫不上忙——加密在 DRAM 層，buffer 在 CPU 核心內部，遠在加密電路之前。

這是一個「防禦層次不配」的典型案例：記憶體加密解決了「hypervisor 直接讀記憶體」，沒有解決「transient execution 讀 CPU 內部 buffer」。

---

## 動手練習

**練習 1：查微碼版本並對照安全公告**

```bash
# 查目前微碼版本
grep -m1 microcode /proc/cpuinfo
# 或
sudo rdmsr -ax 0x8B

# 查 CPU model
grep -m1 "model name" /proc/cpuinfo

# 對照 Intel 的 microcode 更新歷史
# https://github.com/intel/Intel-Linux-Processor-Microcode-Data-Files/blob/main/releasenote.md
# 找你的 stepping (Family/Model/Stepping)，確認是否有 Spectre/MDS 相關更新
```

找出你的 CPU 在 INTEL-SA-00075（Meltdown）和 INTEL-SA-00115（MDS）的微碼版本需求，比對你目前的版本是否達標。

**練習 2：列出 CPU 的安全 capability flags**

```bash
# 方法 A：/proc/cpuinfo
grep -E "ibrs|ibpb|stibp|md_clear|spec_ctrl|ssbd" /proc/cpuinfo

# 方法 B：cpuid 工具（apt install cpuid）
cpuid | grep -i -A2 "structured extended"

# 預期看到的 flags（視 CPU 世代）：
# ibrs_ibpb: Indirect Branch Restricted Speculation
# stibp: Single Thread Indirect Branch Predictors
# md_clear: MDS mitigation (VERW flushes buffers)
# ssbd: Speculative Store Bypass Disable
```

對每個看到的 flag，說明它防禦哪種攻擊、是微碼層還是硬體層的功能。

**練習 3：閱讀 INTEL-SA-00088 並分類緩解層次**

閱讀 Intel Security Advisory INTEL-SA-00088（Spectre/Meltdown 官方通報，2018）：
https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/advisory-guidance/spectre-variant-1-and-variant-2.html

對每個建議的緩解措施，回答：
- 這個緩解靠**微碼更新**（改解碼行為）、**OS patch**（retpoline/KPTI）、還是**硬體重設計**？
- 緩解後仍有哪些殘留風險？（對照 Retbleed、Downfall 等後續研究）

---

## 本章重點整理

- **微碼**是 CPU 的「指令解碼韌體」，可以在不換 CPU 的情況下改變部分行為，但改不了物理電路
- 更新路徑：BIOS/UEFI → OS（`/dev/cpu/microcode`）→ MSR 0x79；確認用 `rdmsr 0x8B` 或 `/proc/cpuinfo`
- **VERW / MD_CLEAR**：微碼更新讓 `VERW` 指令在執行時清空 LFB/Store/Write Buffer，緩解 MDS；這是微碼能做的事的最佳範例
- **DIT / DOIT bit**：硬體層面的 timing contract，保證部分指令的執行時間不依賴資料值；ARM ARMv8.4 已量產，Intel DOIT 仍是提案
- **eIBRS**：per-core 的分支預測器隔離，代價比原始 IBRS 低，但 Retbleed 證明它不完整
- **STT / DOLMA**：追蹤 tainted 的推測執行值並禁止其影響 cache——理論最完整，尚無量產實作
- 硬體修得慢：tape-out 週期 3–5 年；微碼快但只改解碼層；舊 CPU 的長尾問題讓軟體緩解不可廢
- 軍備競賽未完：Retbleed / Downfall / Inception 全在「已修」CPU 上找到新洞；完全禁用推測執行能根治但代價不可接受

---

## 自我檢核

1. 微碼更新的載入路徑是什麼？`rdmsr 0x8B` 和 `rdmsr 0x79` 分別讀的是什麼？
2. VERW 指令原本的語義是什麼？MD_CLEAR 微碼更新後它多做了什麼事？為什麼這樣能緩解 MDS？
3. ARM DIT bit 設定後，哪些類型的指令保證 data-independent timing？哪些不在保護範圍內？
4. eIBRS 和原始 IBRS 的主要差異是什麼？Retbleed 是怎麼繞過 eIBRS 的（哪個 CPU 結構被攻擊）？
5. STT 的核心想法是什麼？為什麼在量產 CPU 上難以實作？
6. 為什麼 Rowhammer 無法靠微碼更新修復？
7. Intel TDX 的記憶體加密為什麼無法防禦 MDS/RIDL 攻擊？

---

## 延伸閱讀

- Intel. "Deep Dive: Intel Analysis of Microarchitectural Data Sampling." 2019. — MDS/MD_CLEAR 的官方技術文件，詳細說明 VERW 的 buffer flush 語義和各 CPU 世代的支援狀況。
- Mambretti et al. "SpecFuzz: Bringing Spectre-type vulnerabilities to the surface." USENIX Security 2020. — 用 fuzzing 找 Spectre gadget；呼應 Ch 33 的偵測主題，說明軟體偵測的極限。
- Weisse et al. "Foreshadow-NG: Breaking the Virtual Memory Abstraction with Transient Out-of-Order Execution." 2018. — SGX enclave 和 VM 的記憶體抽象在 L1TF（L1 Terminal Fault）下的失效分析；和 TDX 的交集值得深讀。
- ARM. "Arm Architecture Reference Manual ARMv8, for A-profile." — 搜尋 "Data Independent Timing" 一節，是 DIT bit 的規格原文，包括哪些指令在 DIT 模式下保證 timing。
- Taram et al. "Context-Sensitive Fencing: Securing Speculative Execution via Microcode Customization." ASPLOS 2019. — STT 的前身論文，說明微碼層面的推測執行控制如何實作。
- Wikner and Razavi. "RETBLEED: Arbitrary Speculative Code Execution with Return Instructions." USENIX Security 2022. — 詳細分析 Retbleed 如何在 eIBRS + retpoline 的系統上仍然有效；是「已修未修」問題的最佳案例研究。

---

→ [練習 D：把洩漏的 code 改成 constant-time](practice-d-constant-time-fix.md)
