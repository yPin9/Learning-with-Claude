# Ch 5 — EPT 二階分頁：GPA → HPA 與它對逃逸的意義

> **目標**：理解 EPT（Extended Page Tables）如何讓 hypervisor 完全掌控 guest 記憶體存取，以及 EPT violation 為何是 MMIO 攔截與 VM 逃逸研究的硬體基礎。

---

## 為什麼需要這個？

在 VT-x 出現之前，guest OS 運行時必須管理自己的分頁表（page table），但 hypervisor 不可能讓 guest 直接操控真實實體位址（HPA，Host Physical Address）。這造成一個根本矛盾：guest 認為自己看到的是實體位址，但那其實只是虛擬的「guest 實體位址」（GPA，Guest Physical Address）。

**Shadow paging（影子分頁）** 是最初的解法。Hypervisor 維護一套「影子分頁表」，把 GVA（Guest Virtual Address）直接對應到 HPA，讓 CPU 真正使用。每當 guest 嘗試修改自己的分頁表，hypervisor 就必須攔截、同步更新影子表。

這個方案有幾個嚴重問題：

1. **效能爆炸**：guest 每次修改 CR3 或寫入 PTE，都觸發 VMEXIT。分頁表修改是核心操作之一，頻率極高。
2. **記憶體開銷**：每個 guest 進程都需要一套影子分頁表，RAM 很快就被吃光。
3. **複雜度地獄**：guest 分頁表結構有千百種邊界情況（huge page、NX bit、各種 CR0/CR4 組合），全部都得在影子層正確反映。任何 bug 就是安全漏洞。

Intel 在 Nehalem（2008）引入 **EPT（Extended Page Tables，延伸分頁表）**，AMD 的對應技術叫 NPT（Nested Page Tables）。核心概念一樣：在 CPU 硬體層增加第二層位址轉換，讓 guest 的分頁表管 GVA→GPA，EPT 管 GPA→HPA，兩層轉換都由硬體走完，不需要 hypervisor 介入。

值得記住的一件事：shadow paging 的複雜度不是抽象的「工程難度」，而是實打實的漏洞來源。VMware 早年好幾個逃逸都跟影子分頁的同步錯誤有關；guest 有辦法在 hypervisor 同步影子表的視窗裡塞入一個矛盾的 PTE 狀態，讓影子表指向不該指向的 HPA。EPT 把這整塊邏輯搬進 CPU，等於一次抹掉了一整類 bug——但它同時把攻擊面往上推到了「hypervisor 怎麼決定哪段 GPA 該映射、哪段該留空」，也就是我們這門課真正要打的 device emulation。EPT 沒有讓逃逸消失，它只是搬了家。

---

## 先建立直覺

先把三層位址轉換的關係畫清楚，再談細節。

```
Guest 進程視角          Guest OS 視角            Host 視角
─────────────          ─────────────            ─────────
  GVA (虛擬)     →      GPA (guest 實體)   →    HPA (host 實體)
 0x7fff1234           0x00100000              0x1a3bc000

      ↑                      ↑                      ↑
 guest 分頁表            由 EPT 負責               DRAM
 guest OS 管            hypervisor 管
 (CR3 → PML4 →         (EPTP → EPT PML4 →
  PDPT → PD → PT)       EPT PDPT → EPT PD → EPT PT)
```

**類比**：想像兩個人在導航。

- Guest OS 是「本地導航員」，手上的地圖是 GVA→GPA 的對應，他認為 GPA 就是真實道路位置。
- Hypervisor 是「城市規劃師」，手上有另一份地圖說 GPA 其實對應到 HPA 的什麼地方，甚至有些 GPA 根本不存在於地圖上（故意留空）。
- CPU 硬體就是實際開車的人，兩份地圖都得查。

當城市規劃師把某段路標記為「不存在」，開車的人一靠近就必須停下來問路（VMEXIT）。這就是 MMIO 攔截的原理。

---

## GVA → GPA：guest 的分頁表

這一層完全由 guest OS 掌控，與一般 x86_64 的四層分頁結構相同（5 層分頁暫時忽略）：

```
CR3
 └─► PML4 (512 entries)
      └─► PDPT (512 entries)
           └─► PD (512 entries)
                └─► PT (512 entries)
                     └─► 4KB 物理頁（在 guest 眼中是 GPA）
```

Guest OS 完全不知道 GPA 和 HPA 的差異。它以為自己在管真實的實體記憶體。Hypervisor 讓它這樣以為，因為 EPT 會在背後默默轉換。

---

## GPA → HPA：EPT 的四層結構

EPT 的結構與 guest 分頁表幾乎一樣，也是四層：

```
EPTP (存於 VMCS)
 └─► EPT PML4 (512 entries, 每個 entry 8 bytes)
      └─► EPT PDPT
           └─► EPT PD
                └─► EPT PT
                     └─► HPA（真實 DRAM 位置）
```

**EPTP（EPT Pointer）** 存在 VMCS 的 `EPT_POINTER` 欄位，格式如下：

```
EPTP [63:0]:
  bits 2:0   = EPT paging structure memory type (通常 6 = WB)
  bits 5:3   = EPT page-walk length minus 1 (4 層 = 3)
  bits 11:6  = 保留（必須為 0）
  bits 51:12 = EPT PML4 的 HPA（4KB 對齊）
  bits 63:52 = 保留
```

---

## EPT Entry 的欄位

每個 EPT entry 8 bytes，關鍵 bit：

```
EPT Entry [63:0]:
  bit  0     = 讀取權限 (R)
  bit  1     = 寫入權限 (W)
  bit  2     = 執行權限 (X) [ring 0 supervisory execute]
  bits 5:3   = 記憶體類型 (000=UC, 110=WB...)
  bit  6     = ignore PAT（是否忽略 guest PAT）
  bit  7     = 大頁旗標（EPT PD/PDPT 層）
  bits 11:8  = 保留
  bits 51:12 = 下一層分頁表或最終頁框的 HPA
  bit 57     = verify guest paging (VGP，需要開啟特定功能)
  bit 58     = paging-write access
  bit 63:58  = 保留
```

最關鍵的三個 bit 是 R/W/X。**任何一個被清零，對應的存取行為就會觸發 EPT violation。**

這裡有個容易搞混的細節：在 x86 一般分頁表裡，一個 entry 若 present bit（bit 0）為 0，其餘欄位對硬體就沒有意義；但 EPT 沒有獨立的 present bit——「是否存在」直接由 R/W/X 三個 bit 是否全為 0 來判定。全 0 就是「這個 GPA 不映射」，這正是 MMIO 範圍在 EPT 裡的樣子。所以當你之後讀 KVM 原始碼看到它把某段 GPA 的 EPT entry 設成 `0`（三權限全清），那不是 bug，那就是「把這段交給 QEMU 軟體模擬」的標記。

再舉兩個具體例子把 R/W/X 的用法坐實：

- **例一：唯讀 code 頁做 dirty tracking**。Live migration 要知道哪些頁在傳輸過程中又被 guest 改了。做法是把所有 RAM 頁的 EPT entry 設成 R=1、W=0、X=1。guest 讀取和執行照跑不誤，一旦寫入就觸發 EPT violation，KVM 記下這個 GPA「髒了」、把 W 補回 1、放行。下一輪只重傳這些髒頁。
- **例二：把 RAM 偽裝成 MMIO**。研究 guest→host 觸發路徑時，我們有時想「攔截 guest 對某段正常 RAM 的存取」。只要把那段的 EPT entry 三權限全清，guest 一碰就 exit，我們就能在 host 端觀察甚至竄改。這正是 EPT 給 hypervisor 的絕對權力：guest 的每一個 byte，host 想不想讓它直接落到 DRAM，是 host 說了算。

---

## EPT Violation：硬體觸發 VMEXIT 的時機

當 CPU 在走 EPT 時遇到以下任一情況，會觸發 EPT violation VMEXIT（exit reason = 48，`EXIT_REASON_EPT_VIOLATION`）：

| 觸發條件 | 說明 |
|---------|------|
| 讀取一個 R=0 的 GPA | 頁面未映射或標記為不可讀 |
| 寫入一個 W=0 的 GPA | 頁面存在但唯讀 |
| 執行一個 X=0 的 GPA | NX 保護 |
| 存取任何層次 EPT entry 的 R=0 | 中間層分頁表也不存在 |

VMEXIT 之後，VMCS 的 `EXIT_QUALIFICATION` 欄位會記錄細節：

```
EXIT_QUALIFICATION for EPT Violation [63:0]:
  bit 0  = 造成 violation 的是讀取操作
  bit 1  = 造成 violation 的是寫入操作
  bit 2  = 造成 violation 的是指令 fetch（執行）
  bit 3  = EPT entry 的 R bit（存取的頁面是否可讀）
  bit 4  = EPT entry 的 W bit
  bit 5  = EPT entry 的 X bit
  bit 7  = GLA（guest linear address）是否有效
  bit 8  = 這是否為 guest 分頁表的 walk（非最終存取）
  ...
```

KVM 在處理 EPT violation 時，第一件事就是讀這個欄位來判斷「是什麼操作、打到什麼權限邊界」。

---

## 底層機制：完整位址轉換流程

CPU 執行 guest 指令遇到記憶體存取時，完整流程如下：

```
guest 指令存取 GVA 0x7fff1000
        │
        ▼
[guest TLB 查詢]
  命中 → 直接得到 GPA，跳到 EPT 查詢
  未命中 → 走 guest 分頁表
        │
        ▼
[走 guest PML4/PDPT/PD/PT]
  每一層的 entry 本身是 GPA
  → CPU 需要用 EPT 把 entry 的 GPA 轉成 HPA 才能讀它
  （這意味著走一次完整的 GVA→GPA 分頁表，需要做 4 次 EPT 查詢）
        │
        ▼
得到最終 GPA（guest 認為的實體位址）
        │
        ▼
[EPT TLB 查詢]（也叫 VPID/PCID 對應的 TLB entry）
  命中 → 直接得到 HPA
  未命中 → 走 EPT PML4/PDPT/PD/PT
        │
        ├── EPT entry 的 R/W/X = 1 → 得到 HPA → 存取 DRAM
        │
        └── EPT entry 不存在或 R/W/X = 0
                    │
                    ▼
             EPT Violation VMEXIT
             (exit reason 48)
                    │
                    ▼
             KVM 處理 handler
```

**最差情況下，一次 guest 記憶體存取需要 24 次實體記憶體讀取**：
- 走 guest 分頁表 4 層 × 每層需要 EPT 轉換 4 次 = 16 次
- 加上最終 GPA→HPA 的 EPT walk 4 次
- 實際上 TLB 會快取大量結果，但冷啟動或 context switch 後確實很貴

---

## MMIO 攔截：EPT 的殺手應用

這是把 EPT 和 VM 逃逸接起來的核心連接。

Guest 想存取某個虛擬裝置（例如 VirtIO queue、e1000 網卡暫存器）。這些裝置的「記憶體映射 I/O」（MMIO，Memory-Mapped I/O）區域在 guest 眼中就是一段 GPA 範圍，例如 `0xfea00000 - 0xfea01000`。

QEMU 在建立 VM 時，**故意不在 EPT 裡映射這段 GPA**。這段 GPA 在 EPT 裡根本不存在。

```
EPT 的 GPA 空間（示意）：

  0x00000000 ─────────────────────► 映射到 RAM (R/W/X)
  0x80000000 ─────────────────────► 映射到 RAM (R/W/X)
  0xfea00000 ─── MMIO 範圍 ───────► ❌ 不映射（EPT entry 不存在）
  0xfea01000 ─────────────────────► 映射到 RAM (R/W/X)
```

只要 guest 程式一存取 `0xfea00000`，立刻觸發 EPT violation VMEXIT。控制流回到 KVM，KVM 查看 `EXIT_QUALIFICATION` 和出錯的 GPA（存於 `GUEST_PHYSICAL_ADDRESS` VMCS 欄位），判斷這是 MMIO 範圍，然後傳給 QEMU 的裝置模擬層處理。

這就是為什麼 QEMU 裡的裝置 bug（例如 [Ch 11](./11-device-emulation-dispatch.md) 要討論的 dispatch 層漏洞）能夠被 guest 觸發：硬體幫你把存取攔下來交給軟體處理。

把整條因果鏈記牢，它是後面每一個 device 章節的地基：

```
guest 指令  mov eax, [0xfea00000]   ← 你在 guest 裡寫的一行
        │
        ▼
CPU 走 EPT，發現 0xfea00000 的 EPT entry = 0（R/W/X 全清）
        │
        ▼
EPT Violation VMEXIT（reason 48）
  GUEST_PHYSICAL_ADDRESS = 0xfea00000
  EXIT_QUALIFICATION.bit0 = 1（讀取造成）
        │
        ▼
KVM handle_ept_violation() → 判定這是 MMIO（無對應 memslot）
        │
        ▼
KVM 填好 kvm_run->exit_reason = KVM_EXIT_MMIO、phys_addr、len、is_write
KVM_RUN ioctl 返回 userspace
        │
        ▼
QEMU 拿到 GPA 0xfea00000 → 查 MemoryRegion → 呼叫該 device 的 .read callback
        │
        ▼
device callback 讀你不該讓它讀的東西 → 這就是 OOB read 的落點
```

換句話說，EPT violation 是「guest 的一次記憶體存取」變成「host 端一次函式呼叫」的那個轉軸。你在 [Ch 7](./07-kvm-to-qemu-exit.md) 會看到 KVM→QEMU 這半段的完整程式碼，在 [Ch 11](./11-device-emulation-dispatch.md) 會看到 QEMU 內部 dispatch 的細節。這章要你先在硬體層把這個轉軸看清楚：不是 QEMU「決定」要攔截，是 EPT 讓它別無選擇必須攔截。

---

## 對比與取捨

| 面向 | Shadow Paging | EPT/NPT |
|------|--------------|---------|
| 硬體需求 | 不需要特殊 CPU 支援 | 需要 VT-x + EPT / AMD-V + NPT |
| GVA→HPA 轉換 | 一層（影子表直接對應） | 兩層（guest table + EPT） |
| Guest 分頁表修改 | 每次修改都 VMEXIT | 無需 VMEXIT（guest 自由改） |
| TLB 失效成本 | Guest CR3 換頁 → 整個 shadow 重建 | Guest CR3 換頁 → 只影響 guest TLB |
| 記憶體開銷 | 每個 guest 進程一套影子表 | 整個 VM 一套 EPT（固定大小） |
| 冷啟動 TLB miss 成本 | 低（只走一套表） | 高（最多 24 次 DRAM 存取） |
| MMIO 攔截機制 | 把 MMIO GPA 標記為 not-present in shadow | 把 MMIO GPA 留空於 EPT |
| 安全隔離強度 | 複雜、歷史 CVE 多 | 清晰、hypervisor 完全掌控 |
| 現代 VMM 使用 | 幾乎棄用 | KVM / VMware / Hyper-V 預設 |

Shadow paging 的複雜度直接對應安全 bug 密度。有大量老式 hypervisor CVE 源自影子分頁的邊界情況處理錯誤。EPT 把複雜度下沉到硬體，VMM 的攻擊面小很多——但並非零。

---

## 踩雷集錦

**1. 以為 EPT 只是「把 GPA 映射到 HPA 的簡單表」**

錯。EPT 也是四層結構，每一層都有自己的 R/W/X bits。一個中間層 entry（EPT PML4E/PDPTE/PDE）的 R bit 如果是 0，整個子樹下的所有 GPA 都無法讀取，哪怕葉層 entry 的 R=1。Hypervisor 要設定 EPT 時必須確保每一層的 permission bits 的交集正確。

**2. 以為 EPT violation 只在「頁不存在」時觸發**

錯。頁面存在（entry 的 R/W/X 至少有一個非零）但本次操作需要的 bit 是 0 也會觸發。例如一個 R=1 W=0 的頁，guest 寫入它 → EPT violation。這用於實作 copy-on-write 和 dirty page tracking。

**3. 以為 EPT TLB 和 guest TLB 是同一個**

錯。CPU 有兩套 TLB。Guest TLB 快取 GVA→GPA；EPT TLB（或說 combined TLB）快取 GPA→HPA 甚至 GVA→HPA 的複合結果。VMCS 的 VPID（Virtual Processor ID）欄位控制 EPT TLB 的識別與沖刷。不正確使用 INVEPT/INVVPID 指令會導致 stale TLB，引發安全或穩定性問題。

**4. 以為修改 EPT 立即生效**

不完全對。CPU 可能已快取舊的 EPT 結果在 TLB 裡。Hypervisor 修改 EPT entry 後，必須執行 `INVEPT` 指令（single-context 或 all-context 模式）來清除過期的 TLB 快取，修改才真正對 guest 可見。KVM 有 mmu_notifier 機制處理這件事，但這也是歷史上 bug 的溫床。

**5. 以為 MMIO 範圍一定完全不映射**

不一定。某些進階技術（例如 VFIO 的 dirty page tracking、或 VM snapshot 機制）會把頁面映射為「存在但 W=0」，觸發寫入時的 EPT violation 來追蹤哪些頁被修改過，而讀取仍直接走硬體。EPT violation 不只用於 MMIO，它是一個通用的存取控制 hook。

---

## 進階：再往深一層

**Large pages in EPT**

EPT 支援 2MB（在 EPT PD 層停止走表）和 1GB（在 EPT PDPT 層停止）大頁。對大型 VM 而言，用大頁可以大幅降低 TLB miss 率。但大頁也意味著更粗的保護粒度：一個 2MB 的大頁只有一組 R/W/X，如果其中有 4KB 需要不同保護（例如 MMIO 範圍夾在 RAM 中間），就要做 page splitting，把大頁拆回 512 個 4KB。KVM 的 `kvm_mmu_split_huge_page()` 就是幹這件事。

**EPT Accessed/Dirty bits**

EPT entry 的 bit 8（A，accessed）和 bit 9（D，dirty）讓硬體在走 EPT 時自動設定，hypervisor 可以用來追蹤 guest 哪些 GPA 被讀過、哪些被寫過。這對 live migration（即時遷移 VM 到另一台機器）至關重要——dirty page tracking 決定哪些記憶體頁需要在最後一輪同步傳送。

**VM-Function：EPTP Switching**

Intel 有一個叫 EPTP switching 的 VM function，允許 guest 在不觸發 VMEXIT 的情況下切換 EPTP（切換到不同的 EPT）。這本來是給 guest 快速切換記憶體視圖用的，但也被研究人員拿來研究是否能從限制較寬的 EPTP 逃到另一個 EPTP 的空間。CVE-2019-0117（Intel CSME）相關研究有涉及這個方向。

**MBEC（Mode-Based Execute Control）**

EPT entry 的 bit 10 在開啟 MBEC 後代表 user-mode execute 權限，bit 2 則只管 supervisor-mode execute。這讓 hypervisor 可以對 guest 做細粒度的「ring 0 可以執行但 ring 3 不行」控制。Windows 的 Virtualization Based Security（VBS）/HVCI 重度依賴這個功能。

---

## 動手練習

**練習 A：讀 VMCS 的 EPTP**

在有 KVM 的 Linux 環境，跑一個 VM，然後用 `sudo rdmsr -p <vcpu_pcpu> 0x201A`（VMCS_EPT_POINTER 在 IA32_VMX_BASIC 描述的結構中）讀出 EPTP 值。解析 bits 2:0（memory type）、bits 5:3（walk length）、bits 51:12（PML4 HPA）。確認 walk length = 3（代表 4 層）。

實際上直接讀 MSR 不一定拿得到 VMCS 欄位（VMCS 是 CPU-local 的），但你可以在 KVM 原始碼 `arch/x86/kvm/vmx/vmx.c` 裡找 `vmcs_write64(EPT_POINTER, ...)` 的呼叫點，把那個值 printk 出來。

**練習 B：模擬 EPT violation**

在 QEMU 的 `-device` 選項中加一個 `e1000` 虛擬網卡，然後在 guest 裡用 `/dev/mem` 或 mmap 直接存取 e1000 的 MMIO 基底地址（`lspci -v` 查 BAR0 位址）。

用 `perf kvm stat` 或 `trace-cmd record -e kvm:kvm_exit` 觀察是否出現 `EPT_VIOLATION` 事件。對照 exit reason 48 確認是 EPT violation，不是其他類型的 VMEXIT。

**練習 C：追 KVM 的 EPT violation handler**

在 Linux kernel 原始碼搜索 `handle_ept_violation`（在 `arch/x86/kvm/vmx/vmx.c`）。閱讀它如何讀取 `EXIT_QUALIFICATION`、`GUEST_PHYSICAL_ADDRESS`，然後呼叫 `kvm_mmu_page_fault()`。追蹤到 `tdp_page_fault()`，理解 KVM 在什麼條件下會把控制流傳回 QEMU（`-ENOENT`/`KVM_EXIT_MMIO`）。

---

## 本章重點整理

- x86 虛擬化有三層位址：GVA（guest 虛擬）→ GPA（guest 實體）→ HPA（host 實體）
- GVA→GPA 由 guest OS 的分頁表負責；GPA→HPA 由 EPT 負責，hypervisor 完全掌控
- Shadow paging 是前 EPT 時代的方案，需要 hypervisor 維護影子表，效能差、bug 多
- EPT 也是四層結構（PML4/PDPT/PD/PT），EPTP 存於 VMCS
- EPT entry 的 bit 0/1/2 = R/W/X，任一存取違反對應 bit 就觸發 EPT violation VMEXIT（exit reason 48）
- EXIT_QUALIFICATION 記錄 violation 的操作類型和頁面的現有權限
- MMIO 攔截的實現：hypervisor 故意不映射 MMIO GPA 範圍 → guest 存取 → EPT violation → KVM → QEMU 裝置模擬
- EPT violation 不只用於 MMIO，也用於 dirty page tracking、COW、NX 保護等
- 修改 EPT 後必須 INVEPT，否則 stale TLB 可能導致問題

---

## 自我檢核

- [ ] 能說出 GVA、GPA、HPA 三者的分工與各自由誰控制
- [ ] 能解釋 shadow paging 的三個主要缺點
- [ ] 能畫出 EPT 四層結構並說明 EPTP 的位置
- [ ] 能說出 EPT entry 的 R/W/X bit 位置與 violation 觸發條件
- [ ] 能解釋 MMIO 攔截為何依賴「故意不映射」而非標記特殊 flag
- [ ] 知道 EXIT_QUALIFICATION 在 EPT violation 時提供哪些資訊
- [ ] 理解為什麼 EPT 修改後需要 INVEPT

---

## 延伸閱讀

1. **Intel SDM Vol 3C, Chapter 28-29**（*Intel® 64 and IA-32 Architectures Software Developer's Manual*）：EPT 的權威規格，28 章講 VMX 記憶體模型，29 章講 EPT 結構與 violation 細節。沒有比這更準確的一手資料。https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html

2. **KVM source: `arch/x86/kvm/mmu/`**：`mmu.c`、`tdp_mmu.c`、`paging_tmpl.h` 是 KVM 的 EPT/shadow paging 實作。`handle_ept_violation()` 在 `vmx/vmx.c`。直接讀 Linux mainline，比任何二手解說都精確。https://github.com/torvalds/linux/tree/master/arch/x86/kvm/mmu

3. **"A Tour Through the Linux Kernel Memory Management" — LWN**：雖然著重在 host 端 MM，但其中關於 mmu_notifier 和 KVM 互動的部分對理解 EPT 同步機制很有幫助。https://lwn.net/Articles/743319/

4. **"Understanding EPT" — Felix Cloutier（2019）**：一篇清晰的 blog post，用圖示說明 EPT walk 的每一步，包含 TLB 行為與 INVEPT 的使用時機。https://www.felixcloutier.com/x86/invept

5. **QEMU source: `target/i386/kvm/`、`hw/net/e1000.c`**：想看 MMIO 從 KVM 回到 QEMU 之後怎麼被路由到裝置 handler 的，就從這裡開始。`kvm_cpu_exec()` 的 `KVM_EXIT_MMIO` 分支是起點。https://github.com/qemu/qemu

---

本章建立了 EPT 的完整硬體圖像。下一章把 KVM 的軟體架構加進來，看 `/dev/kvm`、ioctl 介面、vCPU 迴圈如何把硬體 VMEXIT 串接成可操作的軟體事件。

→ [Ch 6 KVM 架構：/dev/kvm、ioctl、vCPU 迴圈](./06-kvm-architecture.md)
