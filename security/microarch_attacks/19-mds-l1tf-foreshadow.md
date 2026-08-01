# Ch 19 — MDS 家族 + L1TF/Foreshadow

> **目標**：從 Meltdown 洩漏 L1 cache 的概念延伸，理解 MDS（Microarchitectural Data Sampling）如何從 CPU 內部管線緩衝區取樣資料，掌握 RIDL、ZombieLoad、Fallout 三大攻擊變體的機制差異，再深挖 L1TF/Foreshadow 如何用 not-present PTE 繞過 SGX 安全邊界——以及這台 i7-10700 為何對這些攻擊免疫。

---

## 從 Cache 到內部緩衝區：概念跳躍

Meltdown 的問題是「CPU 暫時讀到它不該讀的 kernel cache 資料」。問題出在 L1 cache 這一層，而 L1 cache 至少在架構規格書裡是看得見的東西。

MDS 更往裡走。被洩漏的不是 cache，而是根本沒有出現在任何架構文件裡的 **CPU 內部管線緩衝區**（microarchitectural buffers）。這些緩衝區的存在是 CPU 實作細節，不是程式設計師該碰的東西——但推測執行讓攻擊者意外觸及它們。

三個關鍵緩衝區：

**Line Fill Buffers（LFB）**：cache miss 時，資料從 L2/L3/DRAM 回來，先進 LFB，等填入 L1 cache。LFB 是「資料在途」的暫存站，裡面可能有任何 context 留下的殘影。

**Store Buffers**：CPU 執行 store 指令後，不會立刻把值寫進 cache，先放 store buffer，等 commit。Store buffer 裡存著「已執行但尚未提交」的寫入值。

**Load Ports**：處理記憶體讀取操作的硬體單元，每個 load 請求都要經過這裡。

```
正常 load 流程:

CPU core
  |
  v
Load Port -----> L1 cache hit  -----------> 回傳正確值 (fast path)
                      |
                      | miss
                      v
              Line Fill Buffer (LFB) -----> L2 cache
                                                |
                                                | miss
                                                v
                                        L3 cache / DRAM
                                        (data comes back, fills LFB -> L1)

MDS 的漏洞所在:

當 load 因特定條件失敗 (fault / assist / 非法 VA) 時,
CPU 推測執行階段竟用 LFB / Store Buffer / Load Port 的「殘留資料」
填入目標暫存器——而非回傳錯誤。

  攻擊者觸發 faulting load
        |
        v
  推測執行窗口: 暫存器 = [LFB 裡某個殘留值]  <-- 洩漏點
        |
        v
  Flush+Reload 側通道 → 攻擊者讀出該值
        |
        v
  架構層 load 被 squash，異常被抑制 (try/catch)
  但 cache state 已留下痕跡
```

---

## MDS 攻擊家族：四個 CVE

### RIDL：Rogue In-Flight Data Load

**CVE-2018-12127**（MLPDS — MDS Load Port Data Sampling）  
**CVE-2018-12130**（MFBDS — MDS Fill Buffer Data Sampling）  
研究團隊：VUSec（Vrije Universiteit Amsterdam），Jan Ruge、Kaveh Razavi 等人。  
論文：*RIDL: Rogue In-Flight Data Load*，IEEE S&P 2019。

RIDL 的核心觀察：當 load 操作遭遇「line-fill replay」或「load port replay」時，推測執行可以取得 LFB 或 Load Port 裡的任意殘留資料。攻擊者不需要知道目標在哪個位址，只要反覆觸發 faulting load 並做 F+R，就能逐 byte 採樣 LFB 的內容。

攻擊面：同一核心的兄弟 SMT 執行緒、OS kernel、甚至 SGX enclave 的資料都可能出現在 LFB 裡。

### ZombieLoad

**CVE-2018-12130**（MFBDS，與 RIDL 共用 CVE）  
研究團隊：TU Graz（Michael Schwarz、Moritz Lipp、Claudio Canella、Daniel Gruss 等）。  
論文：*ZombieLoad: Cross-Privilege-Boundary Data Sampling*，CCS 2019。

名稱由來：CPU「復活」了那些已經失敗、正在被重放（replay）的 load 操作——zombie loads。這些 zombie loads 在 LFB 裡留有殘影，推測執行意外讀到它們。

ZombieLoad 特別強調 **cross-hyperthread** 洩漏：攻擊者的執行緒跑在 HT0，受害者的執行緒跑在 HT1，兩者共用同一個實體核心。只要受害者做任何記憶體操作（資料流過 LFB），攻擊者的 faulting load 就可能採樣到受害者的資料。無需任何共享記憶體，無需任何特殊權限。

### Fallout：Store Buffer 洩漏

**CVE-2018-12126**（MSBDS — MDS Store Buffer Data Sampling）  
研究團隊：TU Graz + Daniel Moghimi（Worcester Polytechnic）等。  
論文：*Fallout: Leaking Data on Meltdown-resistant CPUs*，CCS 2019。

Fallout 的目標是 Store Buffer。攻擊者觸發特定類型的 load（與 store forwarding 相關的路徑），讓推測執行從 store buffer 讀取殘留值。可以洩漏 kernel 最近的 **寫入值**——包括 kernel 才剛寫入、但攻擊者架構上完全看不到的資料。

### MDSUM：Uncacheable Memory 變體

**CVE-2019-11091**（Microarchitectural Data Sampling Uncacheable Memory）  
涉及 non-temporal store/load 以及 UC（uncacheable）記憶體的特殊路徑。條件較複雜，影響範圍相對窄，但原理相同。

---

## MDS 變體對比表

| 變體 | CVE | 目標緩衝區 | 攻擊面 | 核心條件 |
|------|-----|-----------|--------|---------|
| RIDL (MLPDS) | CVE-2018-12127 | Load Ports | SMT 兄弟執行緒、kernel、SGX | load port replay |
| RIDL/ZombieLoad (MFBDS) | CVE-2018-12130 | Line Fill Buffers | SMT 兄弟執行緒、kernel、SGX | LFB zombie load replay |
| Fallout (MSBDS) | CVE-2018-12126 | Store Buffers | kernel 寫入值 | store-to-load forwarding replay |
| MDSUM | CVE-2019-11091 | LFB (UC memory) | 非快取記憶體路徑 | non-temporal / UC memory |

---

## ZombieLoad 攻擊流程（概念）

【未實測，理論預期】  
復現條件：Intel 9 代以前（i7-9700K、i7-8700K、i7-8550U 等）且 **未套用 MDS microcode 更新**（2019 年 5 月前版本），SMT/Hyperthreading 啟用，Linux 核心未啟用 `mds=full`。

```c
// 攻擊者執行緒 (HT0) 的核心循環 — 概念示意
// 受害者執行緒 (HT1) 持續做 AES 加密，資料流過共享 LFB

for (int byte_val = 0; byte_val < 256; byte_val++)
    flush(probe_array + byte_val * 4096);  // Flush 探測陣列

for (int rep = 0; rep < REPS; rep++) {
    // 觸發 faulting load: 存取一個合法 mapping 但有
    // 特殊屬性的位址，造成 load port / LFB replay
    uint8_t leaked;
    try {
        leaked = *(uint8_t *)faulting_addr;  // 架構上會 fault
    } catch (...) {}
    // 推測執行窗口裡, leaked 可能是 LFB 殘留值
    access(probe_array + leaked * 4096);    // 把殘留值編碼進 cache
}

// Reload 階段：找出哪個 probe_array[x] 被快取了
// x 就是洩漏的 byte 值
for (int byte_val = 0; byte_val < 256; byte_val++) {
    uint64_t t = rdtsc_diff(probe_array + byte_val * 4096);
    if (t < THRESHOLD) {
        printf("Leaked byte: 0x%02x\n", byte_val);
    }
}
```

這個流程可以每秒採樣數 KB 的 LFB 資料。對 AES 的攻擊：受害者用 AES-NI 加密，中間的明文 key schedule 資料會流過 LFB，攻擊者可以在幾秒內還原 AES key。

---

## MDS 的防禦：VERW 指令再利用

Intel 在 microcode 更新中把一個老舊指令 `VERW`（Verify Segment Write Access）賦予新功能：當 CPU 偵測到有新 microcode 支援時，執行 `VERW` 會 **清空 LFB、Store Buffer、Load Port** 的殘留資料。

```nasm
; Linux kernel 在 kernel→user 轉換時插入
; (arch/x86/entry/entry_64.S 的 MITIGATION_MDS 巨集)
sub     $8, %rsp
mov     %ds, (%rsp)
verw    (%rsp)          ; 清空 microarchitectural buffers
add     $8, %rsp
```

`VERW` 本來是 16-bit 分段保護模式的遺物，幾乎沒人用它做段權限檢查。Intel 選它是因為它有「執行某個記憶體運算」的語義，可以在 microcode 層掛勾，而且不影響任何現有程式碼（沒人依賴它的副作用）。

Linux 核心在以下時機插入 VERW：
- `kernel_exit`（kernel→user space 轉換）
- VM exit（hypervisor→guest 轉換）
- SGX enclave 退出

**MDS_NO CPUID bit**（CPUID leaf 7, subleaf 0, EDX bit 5）：CPU 廠商表示此 CPU 硬體上不再有 MDS exploitable buffer，不需要 VERW workaround。

```bash
# 查看 MDS 緩解狀態
cat /sys/devices/system/cpu/vulnerabilities/mds
# 這台 i7-10700: "Not affected"
# → 硬體已內建 MDS_NO，Comet Lake 從出廠就不受影響

# 確認 CPUID MDS_NO 位元
cpuid -1 | grep -i mds
```

---

## L1TF / Foreshadow：用 Not-Present PTE 刺穿 SGX

### 核心機制

Page Table Entry（PTE）的 Present bit（bit 0）= 0 代表這個 page 目前沒有映射。架構上，CPU 遇到 Present=0 的 PTE 應該立刻觸發 #PF（page fault），沒有任何進一步動作。

L1TF 發現的問題：**Intel CPU 在觸發 #PF 之前，會推測性地用 PTE 裡的實體位址（bits 51:12）查詢 L1 cache**。

```
正常語義（架構規格）:
  PTE.Present = 0 → 立即 #PF → 什麼都不讀

實際硬體行為（L1TF 漏洞）:
  PTE.Present = 0, PTE[51:12] = target_physical_addr
       |
       v
  CPU 推測：用 target_physical_addr 查 L1 cache
       |
       | 如果 L1 hit （受害者的資料剛好在 L1）
       v
  推測執行窗口: 暫存器 = target_physical_addr 對應的資料
       |
       v
  F+R: 攻擊者讀出該資料
       |
       v
  架構層: #PF 被抑制 (try/catch)，cache state 已改變
```

攻擊者需要能夠控制 not-present PTE 的內容（設定任意實體位址），然後讓目標資料出現在 L1 cache 裡（透過迫使受害者存取，或等待自然的 cache warm-up）。

---

## L1TF 三個 CVE

### CVE-2018-3615：Foreshadow（L1TF-SGX）

**最先公開、最轟動的版本。**  
研究團隊 1：KU Leuven（Jo Van Bulck 等），2018 年 1 月 3 日通報 Intel（與 Meltdown/Spectre 同天）。  
研究團隊 2：Technion、Michigan、Adelaide、CSIRO（Marina Minkin 等），2018 年 1 月 23 日獨立發現。  
公開：2018 年 8 月 14 日。  
論文：*Foreshadow: Extracting the Keys to the Intel SGX Kingdom with Transient Out-of-Order Execution*，USENIX Security 2018。

SGX enclave 的設計前提：即使 OS 和 hypervisor 被攻陷，enclave 裡的資料和程式碼也不可讀。SGX 的保護機制在 CPU 記憶體控制器層實施，任何不屬於 enclave 的存取都會被拒絕——在架構層。

L1TF 繞過的方式不走架構層。攻擊者是 OS，可以修改 enclave page 對應的 PTE：把 Present bit 清成 0，但在 PTE 的 bits 51:12 填入 enclave 的實體位址。CPU 看到 Present=0，但推測執行仍然用那個實體位址查 L1 cache。如果 enclave 最近有活動（其資料在 L1 裡），攻擊者就能讀到 enclave 的機密。

這直接推翻了 SGX 的威脅模型。

### CVE-2018-3620：L1TF-OS（OS/SMM 變體）

攻擊目標：OS kernel 記憶體，或 SMM（System Management Mode）記憶體。  
攻擊者是使用者態程式，透過構造 not-present PTE 指向 kernel 的實體頁，讀取 L1 中的 kernel 資料。

### CVE-2018-3646：Foreshadow-NG（VMM 變體）

攻擊目標：hypervisor 記憶體，或同一台機器上其他 VM 的記憶體。  
攻擊者是 guest VM，透過控制 EPT（Extended Page Table）中的 not-present entry，指向 host 或其他 guest 的實體頁，採樣 L1 cache 內容。雲端環境最為危險——不同客戶的 VM 共用同一台實體機，若 hypervisor 未即時 flush L1，一個 VM 可讀另一個 VM 的資料。

---

## Foreshadow 時間軸

```
2018-01-03  KU Leuven 通報 Intel（Meltdown/Spectre 曝光同一天）
2018-01-23  Technion/Michigan/Adelaide/CSIRO 獨立發現並通報
2018-08-14  公開揭露，Intel 發布 SA-00161
            ↓
緩解措施:
  - Microcode 更新: 讓 SGX 進出時強制 L1D cache flush
  - Linux: VM entry/exit 時 L1D flush (CONDITIONAL 或 ALWAYS)
  - SGX SDK 更新: enclave 進出時 flush L1D
  - 新 CPU: L1TF_NO CPUID bit (硬體不再有此漏洞)
```

---

## Meltdown vs MDS vs L1TF 對比

| 維度 | Meltdown | MDS (ZombieLoad/RIDL) | L1TF / Foreshadow |
|------|----------|----------------------|-------------------|
| 洩漏來源 | L1 cache（kernel VA） | CPU 內部緩衝區（LFB/SB/LP） | L1 cache（物理地址） |
| 觸發方式 | 直接存取 kernel VA → 架構上 fault | Faulting/replaying load → 緩衝區殘留 | Not-present PTE → 推測 L1 查詢 |
| 攻擊者需要 | 知道 kernel VA | 只需反覆觸發 fault；SMT 共核 | 可控制 not-present PTE |
| 受害 CPUs | Intel（多代），AMD 基本免疫 | Intel 第 2-8 代（部分 9 代） | Intel Nehalem 到部分 Skylake |
| 這台 i7-10700 | Not affected | Not affected (MDS_NO) | Not affected (RDCL_NO) |
| 主要防禦 | KPTI（頁表隔離） | VERW（緩衝區清空）+ 禁 SMT | L1D flush on context switch |
| 影響 SGX | 無（SGX 另有保護） | 有（LFB 採樣可跨 enclave） | 直接破壞 SGX 威脅模型 |

---

## L1TF 復現條件

【未實測，理論預期】  
復現 CVE-2018-3615（SGX 版本）需要：

1. SGX-capable CPU：Intel Core 6th–8th gen（Skylake、Kaby Lake、Coffee Lake）且 SGX 已啟用
2. Microcode：2018 年 8 月前版本（未套用 SA-00161 修補）
3. OS 需能操作 enclave page 的 PTE（即攻擊者是 OS 權限）
4. 目標 enclave 有活動，其資料在 L1 cache 中

```bash
# 確認 L1TF 狀態
cat /sys/devices/system/cpu/vulnerabilities/l1tf
# 這台 i7-10700: "Not affected"
# Comet Lake 出廠就帶 RDCL_NO，硬體不做推測性 L1 查詢

# 若在舊機器上確認受影響:
# "Mitigation: PTE Inversion; VMX: flush not necessary, SMT disabled"
# 或
# "Mitigation: PTE Inversion; VMX: conditional cache flushes, SMT vulnerable"
```

復現 CVE-2018-3646（VM 版本）額外需要：
- 使用 KVM 或 VMware 的 hypervisor
- Guest 能讀寫自身 EPT 或可觸發特定 EPT misconfiguration
- Host 的 `kvm.ko` 未啟用 `vmentry_l1d_flush=always`

---

## 對比與取捨

**禁用 SMT vs 保留 SMT + VERW**

禁用 SMT（Hyperthreading）是 MDS 的根治方案——沒有兄弟執行緒共用緩衝區，cross-SMT 採樣無從發生。代價是吞吐量下降 20-40%，視工作負載而定。伺服器環境（尤其多租戶雲端）通常值得付這個代價。

VERW 緩解不禁 SMT，效能損失相對小（L5 幾個 ns 的開銷），但只保護 privilege boundary transitions。在同一個 ring level 內（例如兩個使用者態執行緒共用一個實體核心），VERW 不在這些路徑上執行，理論上仍有殘餘風險。MDS_NO 硬體完全消除這個取捨。

**L1D flush 的代價**

L1TF 的緩解要求 VM entry 前 flush L1D（32 KB）。L1D flush 本身約 cost 數千個 cycle，在 VM-heavy 的工作負載（頻繁 VM exit/entry）上可造成 5-15% 效能衰退。`vmentry_l1d_flush=conditional`（只在必要時 flush）vs `always`（每次都 flush）是一個安全性 vs 效能的取捨旋鈕。

---

## 踩雷集錦

**MDS 需要跨進程攻擊** — 錯。ZombieLoad 的核心攻擊是 SMT cross-thread：攻擊者在 HT0，受害者在 HT1，兩者只需共用同一個實體核心。不需要共享記憶體，不需要任何進程間互動。攻擊者甚至不知道採樣到的是誰的資料，只知道這個 byte 流過共用的 LFB。這在雲端環境意味著：一個 VM 的執行緒可以採樣同一實體核心上另一個 VM 的執行緒留在 LFB 裡的任何資料。

**「關掉 SMT 太極端」** — 在有 MDS_NO 的新 CPU 上這是正確的，但在受影響的舊 CPU 上嚴重低估威脅。VUSec 展示了從 sibling thread 採樣 AES key 的攻擊：受害者做 AES 加密，攻擊者採樣 LFB，幾秒內還原 128-bit key。這是高置信度的實際攻擊，不是學術概念驗證。`mds=full` 只加 VERW，`mds=full,nosmt` 才是完整保護。

**「KPTI 順便修了 MDS」** — 完全不同的機制。KPTI 分離 kernel/user 頁表，防止使用者態推測讀 kernel 虛擬位址空間（Meltdown 的攻擊面）。MDS 洩漏的是 CPU 內部緩衝區的殘留資料，與頁表佈局毫無關係。套了 KPTI 但沒更新 microcode、沒啟用 VERW 的機器，對 MDS 完全沒有保護。

**「L1TF 只影響 SGX」** — Foreshadow（CVE-2018-3615）針對 SGX 是最戲劇性的，但 CVE-2018-3620 同樣影響 OS/kernel/SMM 邊界，CVE-2018-3646 影響 VMM/guest 邊界。在沒有 SGX 的伺服器上，Foreshadow-NG 同樣可以讓惡意 guest VM 讀取 hypervisor 或其他 VM 的 L1 cache 內容。把 L1TF 等同於「SGX 問題」會讓雲端運算人員忽視 -3620 和 -3646 的修補。

---

## 進階：再往深一層

**LFB 採樣的「任意」性**

攻擊者無法 **指定** 採樣 LFB 的哪個 slot。LFB 有多個 entry（Intel 文件從不公開精確數量，研究者透過逆向估計約 10-20 個 entry），faulting load 採樣到的是當前「活躍」的 entry。攻擊者用統計方法：反覆採樣，對 256 個可能 byte 值做頻率統計，頻率最高的就是洩漏值。這也是為什麼 ZombieLoad PoC 需要重複數千次才能高信心度提取一個 byte。

**Spectre v1 結合 MDS**

MDS 提供「從哪裡洩漏」（LFB 殘留資料），Spectre v1 提供「如何放大洩漏」（array bounds bypass 讓 gadget 把洩漏值傳進 cache）。理論上兩者可以串鏈：先用 MDS 採樣到一個有用的值（例如 kernel 的 stack canary），再用 Spectre v1 做後續利用。這類串鏈在實際 exploit 中出現，是 2018-2019 年研究社群的核心課題。

**L1TF 的 PTE Inversion 防禦**

Linux 的 L1TF 軟體緩解（不依賴 microcode）：把所有 not-present PTE 的 bits 51:12 清成全 0，或設為無意義的實體位址，使 CPU 推測查詢打到 frame 0（通常是 BIOS 保留區，不含有意義資料）。這叫「PTE Inversion」。代價：OS 不能再用 not-present PTE 的 bits 51:12 儲存任何 metadata（有些 OS 的 swap 機制會這麼做），需要重構這些資料結構。

**SGX Foreshadow 的攻擊精度**

Foreshadow 對 SGX 的攻擊可以讀出整個 enclave memory，包括：
- Enclave 的程式碼段
- Enclave 內的秘鑰材料（例如 sealing key、attestation key）
- 更危險的：讀出 Quoting Enclave 的 EPID private key，意味著可以偽造任意 SGX attestation report

這不只是記憶體洩漏，而是 SGX 整個信任鏈的瓦解。Intel 在修補後重新設計了 attestation 撤銷機制（TCB Recovery），要求受影響平台重新做 provisioning。

---

## 動手練習

以下練習皆不需要實際易受攻擊的 CPU。

**練習一：確認緩解狀態**

```bash
# 完整的 vulnerability sysfs 輸出
for f in /sys/devices/system/cpu/vulnerabilities/*; do
    echo "$(basename $f): $(cat $f)"
done

# 確認 CPUID bits
# MDS_NO: CPUID leaf 7, subleaf 0, EDX bit 5
# L1TF_NO (RDCL_NO): CPUID leaf 7, subleaf 0, EDX bit 17
cpuid -1 -l 7 2>/dev/null | grep -E "MDS|RDCL|L1TF"

# 確認 kernel 啟動參數
grep -E "mds|l1tf|spectre|pti" /proc/cmdline
```

**練習二：VERW 的實際位置**

```bash
# 下載 Linux 原始碼後查 VERW 插入點
grep -rn "verw" arch/x86/entry/ --include="*.S" | head -20
grep -rn "CLEAR_CPU_BUFFERS\|MDS_USER_CLEAR" arch/x86/ | head -20

# 查看 kernel 的 MDS 緩解函數
grep -rn "mds_clear_cpu_buffers\|x86_clear_cpu_buf" arch/x86/ | head -10
```

**練習三：模擬 LFB sampling 統計分析**

```python
#!/usr/bin/env python3
# 模擬 MDS 採樣的統計特性（不需真實 CPU 漏洞）
import random

def simulate_lfb_sampling(target_byte: int, noise: float = 0.3) -> dict:
    """
    模擬 LFB 採樣：目標 byte 以 (1-noise) 機率出現，
    其餘 255 個值以均勻隨機填充。
    """
    samples = {}
    for _ in range(10000):
        if random.random() > noise:
            val = target_byte
        else:
            val = random.randint(0, 255)
        samples[val] = samples.get(val, 0) + 1
    return samples

target = 0x41  # 模擬受害者的秘鑰 byte
results = simulate_lfb_sampling(target, noise=0.7)
top = sorted(results.items(), key=lambda x: -x[1])[:5]
print(f"Target: 0x{target:02x}")
print("Top candidates:")
for val, count in top:
    print(f"  0x{val:02x}: {count} hits {'<-- target' if val == target else ''}")
```

**練習四：分析 L1TF PTE Inversion**

```c
// 查看 Linux 如何清除 not-present PTE 的 PA 欄位
// 參考 arch/x86/mm/init.c 和 include/asm/pgtable.h

// 受 L1TF 影響的 PTE (攻擊者可利用):
// bits [51:12] = target_physical_addr, bit [0] = 0 (not present)

// PTE Inversion 後 (Linux 緩解):
// bits [51:12] = 0 or inverted, bit [0] = 0 (not present)
// 攻擊者設定的 PA 被抹除，CPU 推測查詢打不到目標

// 查看 kernel 中的 pfn_pte / pte_clear 實作
// grep -n "l1tf_pfn_limit\|pte_pfn_invert" arch/x86/include/asm/
```

---

## 本章重點整理

- MDS 洩漏來源是 CPU **內部管線緩衝區**（LFB、Store Buffer、Load Port），不是 cache——這是比 Meltdown 更深一層的漏洞。

- 四個 MDS CVE：RIDL（-12127 Load Port、-12130 LFB 共用）、ZombieLoad（-12130 LFB）、Fallout（-12126 Store Buffer）、MDSUM（-11091 UC memory）。

- ZombieLoad 的 SMT cross-thread 攻擊不需要任何共享記憶體——條件是攻擊者和受害者共用同一個實體核心。

- MDS 防禦：microcode 讓 `VERW` 清空緩衝區；在 kernel→user 和 VM exit 路徑插入 VERW；最徹底方案是禁用 SMT（`mds=full,nosmt`）。

- L1TF：not-present PTE 的 bits 51:12 被 CPU 推測性地用來查 L1 cache，Present=0 不能阻止推測存取。

- L1TF 三個 CVE：-3615 破 SGX（Foreshadow，可偽造 attestation）、-3620 破 OS 邊界、-3646 破 VMM 邊界（雲端多租戶風險）。

- 這台 i7-10700（Comet Lake）：`mds: Not affected`（MDS_NO 硬體），`l1tf: Not affected`（RDCL_NO 硬體）。

---

## 自我檢核

1. Line Fill Buffer 在 CPU 記憶體階層中扮演什麼角色？為什麼它的殘留資料會被推測執行讀到？

2. ZombieLoad 和 Fallout 都屬於 MDS 家族，但洩漏的目標緩衝區不同。請說明各自的目標，以及攻擊者分別可以讀到什麼類型的受害者資料。

3. `VERW` 指令原本的用途是什麼？Intel 如何透過 microcode 讓它具備清空 MDS 緩衝區的效果？

4. 為什麼 KPTI 無法防禦 MDS？請從兩種攻擊的洩漏來源解釋差異。

5. L1TF 的核心問題在於 not-present PTE 的哪個行為？Linux 的 PTE Inversion 如何從軟體層面緩解這個問題？

6. Foreshadow（CVE-2018-3615）為何被視為比一般記憶體洩漏更嚴重？從 SGX attestation 信任鏈的角度說明。

---

## 延伸閱讀

- Van Bulck, J. et al., "Foreshadow: Extracting the Keys to the Intel SGX Kingdom with Transient Out-of-Order Execution," USENIX Security 2018 — https://foreshadowattack.eu/

- Schwarz, M. et al., "ZombieLoad: Cross-Privilege-Boundary Data Sampling," CCS 2019 — https://zombieloadattack.com/

- Van Ridl, S. et al. (VUSec), "RIDL: Rogue In-Flight Data Load," IEEE S&P 2019 — https://mdsattacks.com/

- Canella, C. et al., "Fallout: Leaking Data on Meltdown-resistant CPUs," CCS 2019

- Intel MDS 官方 advisory — https://www.intel.com/content/www/us/en/developer/articles/technical/software-security-guidance/advisory-guidance/microarchitectural-data-sampling.html

- Linux kernel documentation: `Documentation/admin-guide/hw-vuln/mds.rst`，`Documentation/admin-guide/hw-vuln/l1tf.rst`

---

MDS 和 L1TF 代表了 CPU 安全研究在 2018-2019 年的高峰：研究者已不再滿足於 cache 這一層，而是深入到沒有任何公開文件的 CPU 內部緩衝區。下一章轉向更晚近的變體——Spectre v2 的延伸、SRBDS、以及 MMIO Stale Data（Processor MMIO Stale Data Vulnerabilities），看 Intel 和研究社群如何在一輪又一輪的 patch-and-exploit 循環中繼續博弈。 → [Ch 20](20-later-generation-transient.md)
