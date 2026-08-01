# Ch 2 — VMM 怎麼跑一個 Guest：從 trap-and-emulate 到硬體輔助

> **目標**：搞懂虛擬化的三個世代——純軟體 trap-and-emulate、半虛擬化、硬體輔助（VT-x/AMD-V）——各自怎麼運作、為什麼一代一代演進，並看清 QEMU 用 KVM 加速與純 TCG 解釋執行的根本差別。這是理解攻擊入口 VMEXIT 的前置課。

要打穿虛擬機的牆，你得先懂這道牆是怎麼砌起來的。「hypervisor 跑一個 guest」聽起來像一句話，實際上是三十年演進出來的一套精巧機制。這章把演進線走一遍——不是掉書袋，而是因為**每一代虛擬化技術都決定了一種攻擊面**，而現代逃逸主戰場（device emulation）之所以長成那樣，正是硬體輔助虛擬化這一代的直接後果。

## 為什麼需要這個？

核心問題只有一個：**怎麼讓一段「以為自己獨佔整台電腦、能執行任何特權指令」的作業系統，安全地跑在一台其實被別人控制、還要跟別的 OS 共用的機器上？**

guest kernel 會執行特權指令：關中斷（`cli`）、改分頁表基底（寫 `CR3`）、讀寫 I/O port（`in`/`out`）、讀機器狀態暫存器（`rdmsr`）。這些指令若讓 guest 直接在真實硬體上跑，它就真的關掉了整台機器的中斷、改掉了真實的分頁表——隔離瞬間崩潰。hypervisor 的工作就是**攔截這些危險動作，替 guest「假裝」它成功了，但實際不讓它碰到真硬體**。

問題是「怎麼攔截」。三十年來有三種答案，一代比一代快、也一代比一代把攻擊面推向不同的地方。看懂這三代，你就懂了 VMEXIT 從哪來、為什麼 device 模擬是攻擊主戰場。

## 先建立直覺

先記住虛擬化要處理的三類東西，後面都繞著它們轉：

```
   guest 想做的事              hypervisor 必須做什麼
   ───────────────           ────────────────────────────
   1. 跑普通運算指令           讓它直接在 CPU 上跑（不干預，才快）
      (add, mov, 迴圈…)

   2. 執行特權/敏感指令         攔下來 → 假裝執行 → 維持隔離
      (cli, mov cr3, rdmsr…)   ← 這是「CPU 虛擬化」

   3. 存取硬體 (I/O)           攔下來 → 用軟體模擬一個假硬體回應它
      (讀寫網卡/磁碟/顯卡暫存器) ← 這是「device 模擬」★ 逃逸主戰場
```

三代技術差在**「怎麼區分並攔截第 2、3 類」**。第 1 類大家都想讓它直接跑（不然太慢）。難的是第 2、3 類要被攔下來——而攔截機制的演進，就是這一章的主線。

## 第一代：純軟體 trap-and-emulate（與它的破綻）

最古典的想法（1974 年 Popek & Goldberg 的虛擬化定理就提出來了）：**讓 guest 整個跑在低權限（user mode），任何特權指令一執行就會觸發 CPU 例外（trap），hypervisor 接住這個 trap、模擬該指令的效果、再把控制權還給 guest。** 這叫 trap-and-emulate。

理論很漂亮。問題出在 **x86 不是「可虛擬化」的架構**——Popek-Goldberg 的定理要求「所有敏感指令都是特權指令（執行時一定會 trap）」，但 x86 有一堆敏感指令在 user mode 執行時**不會 trap、也不報錯，就是靜靜地給你錯的結果**。

經典例子 `popf`（彈出 flags 暫存器）：它會試圖改中斷致能位（IF）。在 kernel mode 這會真的改 IF；但在 user mode 執行，x86 **不 trap、直接忽略對 IF 的修改**。於是 guest kernel 以為自己關了中斷、hypervisor 卻毫不知情——狀態不一致，虛擬化破功。這類「敏感但不特權」的指令，x86 早期有十幾條（`sgdt`、`sidt`、`sldt`、`smsw`、`lar`、`lsl`、`verr`、`verw` 等都屬此類）。

### binary translation：VMware 的魔法

VMware 的解法：**binary translation（BT，動態二進位翻譯）**。既然不能靠 trap 攔，那就在 guest 的機器碼**真正執行之前掃描它**，把危險指令**動態改寫**成安全的等價序列，改寫後的碼再跑。

看一個具體例子，BT 對 `popf` 的改寫方向（簡化）：

```nasm
;; guest 原始指令：
popf           ; 敏感！user mode 下不 trap，但會靜默忽略 IF

;; BT 改寫成：
pop  eax                     ; 先把 flags 值存進 eax
test eax, 0x200              ; 檢查 IF 位
jz   .no_if_change           ; 若 guest 沒要改 IF → 照常更新 flags
call VMM_check_if_allowed     ; 若 guest 要改 IF → 問 hypervisor 允不允許
.no_if_change:
push eax
popf                          ; 這時候執行才安全（或已被 hypervisor 調整過）
```

BT 不是在執行期逐條翻——它在 guest 程式碼**第一次執行前**掃描一個基本區塊（basic block），找出所有敏感指令，把整塊改寫後放進翻譯快取（translation cache）；之後再執行同一塊就直接跑快取版本，不用再翻。效能損失比「每條都 trap」低得多，但仍有開銷——特別是翻譯本身、快取失效、以及對自修改程式碼（self-modifying code）的處理。

- **代價**：BT 要掃描、翻譯、快取翻譯結果，複雜且有效能開銷，尤其對頻繁陷入的 workload。
- **攻擊面啟示**：翻譯器本身是複雜的軟體，是攻擊面；但這一代已基本被硬體輔助取代，不是今天的主戰場。

## 第二代：半虛擬化（paravirtualization）

另一條路，Xen 在 2003 年帶起來的：**既然攔截 guest 很麻煩，那就改造 guest，讓它「知道自己是 VM」，主動配合。**

半虛擬化把 guest kernel 改掉：原本它會執行的特權指令（改分頁表、關中斷…），改成**直接呼叫 hypervisor 提供的 API（叫 hypercall）**。等於 guest 不再偷偷摸摸做特權動作等著被抓，而是禮貌地敲門說「我要改分頁表，麻煩你幫我改」。

```
   全虛擬化 (trap):  guest 執行 mov cr3 → CPU trap → hypervisor 接住模擬
   半虛擬化 (hypercall): guest 主動呼叫 hypercall_update_cr3() → hypervisor 直接處理
```

Xen 的 hypercall ABI 用 x86 的 `SYSCALL` / `INT 0x82` 機制（依版本不同），把 hypercall number 放進 eax，引數放進 ebx/ecx/edx/esi/edi。例如 Xen 的 `HYPERVISOR_mmu_update`（更新分頁表）hypercall：

```c
/* Linux/Xen PV kernel 的 mmu_update 呼叫方式 */
static inline int hypercall_mmu_update(mmu_update_t *req, int count,
                                       int *success_count, domid_t domid)
{
    return HYPERVISOR_mmu_update(req, count, success_count, domid);
    /* 展開後是一個 SYSCALL 指令，eax = __HYPERVISOR_mmu_update */
}
```

- **好處**：沒有「攔截」的開銷，guest 主動配合，效能好，且繞開了 x86 那些不 trap 的敏感指令問題（因為 guest 根本不執行它們了）。
- **代價**：要改 guest kernel 的原始碼。閉源 OS（早年的 Windows）沒法這樣搞。
- **攻擊面啟示**：**hypercall 介面本身變成攻擊面**——guest 主動送進來的每個 hypercall 參數，hypervisor 都得驗。這條線在現代以 **virtio**（半虛擬化的 device 模型）的形式活得好好的，是 Part 4 的主題。半虛擬化沒死，它變成了「device 這一層」的主流做法。

## 第三代：硬體輔助（Intel VT-x / AMD-V）—— 今天的主線

2005–2006 年 Intel（VT-x）與 AMD（AMD-V）給 CPU 加了新的硬體機制，直接從根上解決 x86 不可虛擬化的問題：**加一個全新的 CPU 執行模式，讓 guest 能在近乎原生的速度下跑，同時硬體自動攔截所有該攔的東西。**

### VMEXIT/VMENTRY 迴圈：完整運作圖

```
   ┌──────────────────────────────── VMX root mode ──────────────────────────────┐
   │                                                                             │
   │  KVM / hypervisor 在這裡                                                    │
   │                                                                             │
   │  1. 設定 VMCS（要 trap 哪些事件、guest CPU 狀態）                             │
   │  2. VMLAUNCH（首次）/ VMRESUME（之後）─────────────────────────┐              │
   │     （VMENTRY）                                              │              │
   │                                                             ▼              │
   │  ┌──────────────────────── VMX non-root mode ────────────────────────┐    │
   │  │                                                                   │    │
   │  │   guest kernel / guest app 直接在真 CPU 上全速執行                  │    │
   │  │   普通指令（add/sub/mov/call/ret…）完全不干預                       │    │
   │  │                                                                   │    │
   │  │   觸發 VMEXIT 的事件（VMCS 裡設定的）：                              │    │
   │  │     ① 特權指令（cpuid/rdmsr/wrmsr/vmcall…）                        │    │
   │  │     ② I/O 指令（in/out，依 I/O bitmap）                             │    │
   │  │     ③ EPT violation（guest 訪問 host 沒映射的 GPA）                  │    │
   │  │     ④ 外部中斷（host 要搶回控制權）                                  │    │
   │  │     ⑤ 其他（PAUSE, HLT, 某些例外…）                                 │    │
   │  │                         │                                          │    │
   │  │                    VMEXIT ─────────────────────────────────────────┤    │
   │  └───────────────────────────────────────────────────────────────────┘    │
   │                         │                                                 │
   │                         ▼  CPU 自動把 guest 狀態存進 VMCS guest-state area  │
   │                                                                            │
   │  3. 讀 VM-exit reason（VMCS 欄位 0x4402）                                   │
   │  4. 依 reason 分發：                                                        │
   │     - KVM 自己能處理 → 直接 VMRESUME 回 guest                               │
   │     - 需要 QEMU 幫忙 → ioctl(KVM_RUN) 返回 → QEMU 處理 → ioctl 再進        │
   │  5. VMRESUME ───────────────────────────────────────────────────────────►  │
   └─────────────────────────────────────────────────────────────────────────────┘

   典型 VMEXIT 頻率（生產環境，非精確數字，理論預期量級）：
     ① CPUID          ：每秒幾千~幾萬次（guest 程式會呼叫）
     ② I/O port       ：每秒幾萬次（PIO 存取，如時鐘/串口）
     ③ EPT violation  ：熱 workload 下幾乎 0，冷啟動/大頁切換時激增
     ④ 外部中斷        ：每秒幾千次（timer interrupt 等）
```

- guest 跑在 **VMX non-root mode**：普通運算指令**全速在真實 CPU 上執行**，hypervisor 完全不插手——這是它比 BT/paravirt 都快的根本原因。
- 一旦 guest 做了「該被攔的事」，CPU **硬體自動觸發 VMEXIT**，把控制權切回 VMX root mode 的 hypervisor。
- 哪些事會觸發 VMEXIT、guest 的完整 CPU 狀態存哪，全記在一個硬體結構 **VMCS（Virtual Machine Control Structure）** 裡。hypervisor 透過設定 VMCS 來「規劃」哪些行為要攔。

**攻擊面啟示（關鍵）**：硬體把 CPU 虛擬化那層防得很嚴（VMCS 由硬體與 KVM 嚴格管控，直接打它的 bug 稀有）。於是攻擊者的注意力被推向 VMEXIT 之後——**當 VMEXIT 的原因是「guest 存取了某個虛擬硬體」，控制權會交給 hypervisor 的 device 模擬程式碼去回應。那段軟體，就是逃逸的主戰場。** 硬體解決了 CPU 虛擬化，卻把大量攻擊面留在了「用軟體假裝硬體」這件事上。

### 影子分頁表 vs EPT：演進中的記憶體虛擬化

VT-x 加入之前，記憶體虛擬化靠**影子分頁表（shadow page table）**：

```
   guest 視角：  guest virtual addr → guest physical addr
                （guest 自己維護分頁表）

   shadow PT 時代（軟體）：
   host 維護一份「影子分頁表」= guest virtual addr → host physical addr
   guest 寫分頁表 → VMEXIT → KVM 同步更新影子分頁表 → 複雜、效能差

   EPT 時代（硬體，VT-x 的一部分）：
   guest 自己管  guest virtual → guest physical（hardware walker）
   EPT 再接手   guest physical → host physical（硬體自動走兩層）
   guest 改分頁表 → 不需 VMEXIT（除非改到 EPT 沒映射的 GPA）
```

EPT（Extended Page Table）讓分頁表走訪全部由硬體完成，省掉了影子分頁表那套昂貴的 VMEXIT-per-写-分頁表機制。Ch 5 會拆 EPT 的每個欄位；這裡只需要知道：**EPT 讓 guest 的記憶體管理從「軟體代勞全部」變成「硬體兩層走訪」，是 VT-x 效能的另一根支柱**，也和 DMA 攻擊（guest 給的 GPA 在 EPT 裡映射到 host 的什麼位址）直接相關。

## type-1 vs type-2 hypervisor

```
   Type-1 (bare-metal)              Type-2 (hosted)
   ─────────────────                ────────────────
   ┌──────────────┐                 ┌──────────────┐
   │ Guest │ Guest│                 │ Guest │ Guest│
   ├───────┴──────┤                 ├───────┴──────┤
   │  Hypervisor  │                 │  Hypervisor  │
   │ (直接管硬體)  │                 ├──────────────┤
   ├──────────────┤                 │   Host OS    │  ← 多一層宿主 OS
   │   Hardware   │                 ├──────────────┤
   └──────────────┘                 │   Hardware   │
                                    └──────────────┘
   例: ESXi, Xen, Hyper-V           例: VirtualBox, VMware Workstation
                                    QEMU/KVM 介於兩者（KVM 在 host kernel，
                                    QEMU 在 host userland）
```

- **Type-1**：hypervisor 直接跑在硬體上，沒有底下的宿主 OS。生產雲的骨幹（ESXi、Xen、Hyper-V）。
- **Type-2**：hypervisor 是宿主 OS 上的一個程式。桌面虛擬化（VirtualBox、VMware Workstation）。
- **QEMU/KVM 是混血**：KVM 是 host Linux kernel 的模組（提供 VT-x 的存取，偏 type-1 的角色），QEMU 是 host 上的 userland 行程（做 device 模擬，偏 type-2 的角色）。**逃逸打的是 QEMU 那個 userland 行程**——這正是它適合當教具的原因：靶是個可除錯的普通程式，但底下用的是真硬體虛擬化，跟生產環境同一套機制。

從攻擊角度看 type 分類：

| | Type-1（ESXi/Hyper-V） | Type-2/混血（QEMU/KVM） |
|---|---|---|
| 逃逸後拿到的 | vmkernel 空間的 code exec（直接打硬體） | host OS 上的 userland 行程 code exec |
| 難度 | 更高（需要懂 vmkernel 內部，沒有 Linux 提供的 mitigations） | 中（靶是帶 glibc 的 userland 程式，工具鏈完整） |
| 市場價值 | 更高（ESXi 是生產雲骨幹） | 高（但低於 ESXi 逃逸） |
| 本課態度 | Part 5/6 遷移方法，Part 1-4 先建 QEMU 基礎 | **主線** |

## 底層機制：QEMU 的 KVM 加速 vs 純 TCG

QEMU 有兩種跑 guest 指令的方式，差別巨大，你必須分清：

```
   ┌─── QEMU + KVM（-accel kvm，生產/預設）─────────────────────────────────────┐
   │                                                                           │
   │  guest 普通指令 ──────────────────► 直接在真 CPU 上跑 (VT-x non-root mode)   │
   │                                                                           │
   │  guest 碰 I/O / 觸發 VMEXIT                                               │
   │          │                                                                │
   │          ▼                                                                │
   │       KVM 判斷 exit reason                                                 │
   │          │                                                                │
   │    能自己處理？─── Yes ──► VMRESUME（回 guest，不經過 QEMU）                 │
   │          │                                                                │
   │         No                                                                │
   │          ▼                                                                │
   │       ioctl(KVM_RUN) 返回 → QEMU userland 接手 → device 模擬 (hw/ 下的 C)  │
   │                                                                           │
   └───────────────────────────────────────────────────────────────────────────┘

   ┌─── QEMU 純 TCG（-accel tcg，無硬體虛擬化時）──────────────────────────────┐
   │                                                                          │
   │  guest 每一條指令                                                          │
   │          │                                                               │
   │          ▼                                                               │
   │       TCG 動態翻譯器：                                                     │
   │       把 guest basic block 翻成 host 機器碼 → 放進翻譯快取 → 執行             │
   │       （全軟體，慢 5–20x，但不需要 /dev/kvm）                               │
   │                                                                          │
   │  guest 碰 I/O                                                            │
   │          │                                                               │
   │          ▼                                                               │
   │       TCG 執行到 I/O 指令 → 呼叫 QEMU 內部的 I/O 函式                       │
   │       → 同一份 device 模擬 (hw/ 下的 C)  ← 和 KVM 路徑相同！               │
   │                                                                          │
   └──────────────────────────────────────────────────────────────────────────┘
```

- **KVM（Kernel-based Virtual Machine）**：host Linux 的核心模組，把 VT-x 包成 `/dev/kvm` 這個介面給 QEMU 用。有它，guest 的普通指令全速跑在真 CPU 上，QEMU 只在 VMEXIT（主要是 I/O）時才被叫回來模擬硬體。這是生產環境的樣子。
- **TCG（Tiny Code Generator）**：QEMU 自帶的動態翻譯器（本質是一種 JIT/解釋器），把 guest 的每條指令翻成 host 指令來跑。**不需要硬體虛擬化支援**——你在沒有 `/dev/kvm` 的環境（例如 WSL2、或在一個 VM 裡再跑 VM）也能用。代價是慢（解釋執行）。
- **對逃逸的關鍵事實（重申）**：**不論 KVM 還是 TCG，guest 一旦碰 I/O，走的都是 QEMU 同一份 `hw/` 下的 device 模擬 C 程式碼。** KVM 只加速「跑 guest 普通指令」，device bug 的觸發與利用兩種模式一致。所以本課大量 device 攻擊練習在 TCG 下也能做——這就是為什麼 Ch 0 說 WSL2 沒 KVM 也能練。

## 一個真實的 VMEXIT 長什麼樣：從 KVM 到 QEMU 的交接

理解 VMEXIT 不只是看圖，你也要能在程式碼層面看到它。這裡把 KVM 到 QEMU 的 exit 交接路徑攤開（程式碼為 Linux 6.x / QEMU 8.x 的簡化版，未實測）：

**KVM 端（host kernel，`virt/kvm/kvm_main.c` 與 `arch/x86/kvm/vmx/vmx.c`）**：

```c
/* KVM 的 vCPU run 迴圈（極度簡化）*/
static int vcpu_run(struct kvm_vcpu *vcpu) {
    for (;;) {
        /* VMENTRY：把 guest 放回 CPU */
        r = vcpu_enter_guest(vcpu);  /* 內部呼叫 vmx_vcpu_run() → VMRESUME */

        /* VMEXIT 後：檢查 exit reason */
        if (r <= 0)
            break;  /* 需要回到 userland 處理 */

        /* KVM 自己能處理的 exit（如 cpuid、某些 msr）直接 continue */
        /* 需要 QEMU 的 exit（如 device MMIO/PIO）→ 從 ioctl 返回 */
    }
    return r;
}

/* QEMU 呼叫的是 ioctl(kvm_fd, KVM_RUN, 0)
   KVM_RUN 回傳後，QEMU 讀 kvm_run->exit_reason 知道 why */
```

**QEMU 端（host userland，`accel/kvm/kvm-all.c`）**：

```c
/* QEMU 的 KVM vCPU 執行函式（kvm_cpu_exec，簡化）*/
int kvm_cpu_exec(CPUState *cpu) {
    struct kvm_run *run = cpu->kvm_run;

    do {
        /* 進入 KVM，等 VMEXIT */
        ret = kvm_vcpu_ioctl(cpu, KVM_RUN, 0);

        /* 分析 exit reason */
        switch (run->exit_reason) {
        case KVM_EXIT_IO:
            /* PIO (in/out 指令) → 找對應的 I/O port handler */
            kvm_handle_io(run->io.port, ...);
            break;
        case KVM_EXIT_MMIO:
            /* MMIO → 走 address_space_write/read */
            address_space_rw(&address_space_memory, run->mmio.phys_addr, ...);
            break;                     /* ↑ 這裡就進入 device dispatch 了 */
        case KVM_EXIT_SHUTDOWN:
            /* guest 關機 */
            ...
        }
    } while (ret == 0);
}
```

這兩段程式碼把 Ch 2 的 VMEXIT 迴圈圖「落地」到了實際程式碼。逃逸的「攻擊入口」就是 `KVM_EXIT_MMIO` 這條路徑進去的 `address_space_rw` → device callback 鏈。Ch 7 會完整追蹤整條路徑，現在你只需要知道這個結構存在。

## 對比與取捨

| 世代 | 攔截機制 | 要改 guest？ | 效能 | 主要攻擊面 | 現況 |
|---|---|---|---|---|---|
| trap-and-emulate | CPU 特權指令自然 trap | 否 | 差（x86 有漏洞） | 翻譯器（理論，實際沒用） | 已淘汰 |
| BT（binary translation） | 軟體掃描改寫 guest 機器碼 | 否 | 中（BT 有開銷） | BT 翻譯器本身 | 基本被取代 |
| 半虛擬化（paravirt） | guest 主動 hypercall | **是**（改 guest kernel） | 好 | hypercall / virtio 介面 | device 層以 virtio 形式活著 |
| 硬體輔助（VT-x/AMD-V） | CPU 硬體自動 VMEXIT | 否 | 佳（普通指令全速） | **device 模擬**（VMEXIT 之後那層） | **今日主線** |

| QEMU 執行後端 | KVM 加速 | 純 TCG |
|---|---|---|
| guest 普通指令 | 真 CPU 全速跑 | 逐條翻譯，慢 |
| 需要 `/dev/kvm` | 是 | 否 |
| device 模擬路徑 | `hw/` 下 C（VMEXIT 後） | `hw/` 下同一份 C |
| 對 device 逃逸練習 | 可 | 可（慢但可） |
| 適用場景 | 生產環境、研究主線 | WSL2、巢狀虛擬化、跨架構模擬 |

**為什麼硬體輔助最終勝出**——三點直接原因：
1. **不需改 guest**：Windows 無法修改 kernel（閉源），paravirt 行不通。VT-x 讓 Windows guest 全速跑、不修改。
2. **效能**：BT 有 5–30% 開銷（workload 相關），VT-x 的 VMEXIT 開銷只在「確實該攔」的點發生，普通計算零開銷。
3. **正確性**：BT 要追蹤自修改程式碼、處理跨基本區塊的 jump，極複雜容易出 bug。硬體把「該攔什麼」的問題直接搬到 CPU 設計裡，語意更明確。

## 踩雷集錦

- **錯誤直覺**：「x86 天生就能虛擬化，trap-and-emulate 一開始就好用。」→ **正確認識**：x86 有一堆「敏感但不 trap」的指令（如 user mode 的 `popf` 靜默忽略 IF、`pushf` 返回假的 IF 值），讓純 trap-and-emulate 破功。VMware 是靠 binary translation 硬繞過，直到 VT-x 才從硬體根治。
- **錯誤直覺**：「半虛擬化是過時的老技術。」→ **正確認識**：paravirt 的 CPU 那部分被 VT-x 取代了，但它的 **device 那部分以 virtio 形式成為現代雲的主流**（快、乾淨）。virtio 是 Part 4 專章，一點都不過時。
- **錯誤直覺**：「有了 VT-x，硬體就把整台 VM 都虛擬化好了，QEMU 只是啟動器。」→ **正確認識**：VT-x 只管 CPU 虛擬化（與 EPT 管記憶體虛擬化）。**所有虛擬硬體（網卡、磁碟、顯卡…）還是 QEMU 用軟體模擬的**。VMEXIT 之後那一大段 device 模擬 C 碼才是逃逸主戰場。
- **錯誤直覺**：「沒有 KVM 就跑不了 guest / 練不了逃逸。」→ **正確認識**：TCG 純軟體也能跑 guest（慢而已），且 device 模擬路徑與 KVM 相同，絕大多數 device 逃逸練習照做。在 WSL2 下沒有 `/dev/kvm` 也能把本課 90% 的練習做完。
- **錯誤直覺**：「打逃逸就是打 KVM（host kernel 模組）。」→ **正確認識**：KVM bug 也存在且更嚴重（直接 host kernel），但稀有且難。本課主線打的是 **QEMU userland 行程的 device 模擬**，那才是 bug 密度最高、writeup 最多的地方。
- **錯誤直覺**：「VMEXIT 很貴，每次都要 context switch 到 hypervisor。」→ **正確認識**：VMEXIT 確實有開銷（硬體要儲存 guest CPU 狀態到 VMCS，通常幾百 ns 量級），但它只在「真的需要攔」的點觸發。普通計算完全沒有 VMEXIT，現代硬體輔助虛擬化的效能損耗在多數 workload 下低於 5%。

## 進階：再往深一層

### EPT 與 DMA 攻擊的關係

EPT 把「guest 實體位址（GPA）→ host 實體位址（HPA）」的映射表交給 CPU 硬體管理。對逃逸而言，這有兩個直接意義：

1. **guest RAM 的物理本質**：guest 眼中的 GPA 0x1000000 在 host 上其實是 QEMU `mmap` 出來的一塊記憶體（HVA，host virtual address），再透過 EPT 映射到 HPA。當 device 做 DMA「去 GPA X 讀資料」，host 端是走 `address_space_read(GPA)` → 算出對應 HVA → `memcpy` 讀。**guest 完全掌控自己 GPA 空間的內容，等於完全控制 DMA 讀到什麼**。
2. **EPT violation 當原語**：某些進階攻擊會故意觸發 EPT violation（存取沒有映射的 GPA），讓 hypervisor 陷入特定的錯誤處理路徑，這是較冷門的攻擊入口，Ch 16 會帶到。

### VMCS 是攻擊者也想懂的結構

VMCS 記錄 guest/host 狀態與「哪些行為觸發 VMEXIT」的控制位。理解它你才知道「為什麼一次 `out` 指令會 VMEXIT、一次 `add` 不會」。關鍵欄位：

- `VM-exit reason`（0x4402）：上次 VMEXIT 的原因（I/O、EPT、特權指令…）
- `Exit qualification`（0x6400）：補充資訊（I/O port 號、存取寬度、方向…）
- `Guest-state area`：VMEXIT 時自動儲存的 guest 暫存器（RIP、RSP、CR3…）
- `Primary processor-based controls`：哪些事件觸發 VMEXIT 的 bitmap（I/O bitmap、RDTSC 攔截等）

Ch 4 逐欄拆。現在只需要知道：**KVM 在 `vmx.c` 裡設定 VMCS，決定了「guest 做什麼事會觸發 VMEXIT 交給我（KVM）或 QEMU 處理」**——這個設定決定了攻擊面的邊界。

### binary translation 沒完全消失

在不支援巢狀虛擬化、或跨架構模擬（例如在 x86 上跑 ARM guest）時，QEMU 的 TCG 仍是唯一手段。TCG 本身（翻譯器）也偶有安全 bug（如 2017 年的 TCG JIT buffer overflow CVE），但屬冷門攻擊面，本課不主攻。

### 巢狀虛擬化（nested virtualization）

在 guest 裡再跑一個 hypervisor（VMCS shadowing）：KVM 支援 `-cpu host` 下的巢狀虛擬化，讓 L1 guest 裡的 KVM 能正常用 VT-x。從攻擊角度，巢狀虛擬化引入了「L0 KVM 怎麼處理 L1 guest 發出的 VMLAUNCH/VMRESUME」這個額外攻擊面——CVE-2021-22543 就是 KVM 巢狀虛擬化路徑的 bug。先知道術語存在，後面需要時再深挖。

## 動手練習

（概念章為主，練習偏觀察與推理。）

1. **分類三代**：不看課文，畫出三代虛擬化（trap-and-emulate/BT、paravirt、VT-x）各自「怎麼攔截 guest 的特權/I/O 動作」，並各寫出它引入的主要攻擊面。
2. **實測 KVM vs TCG 速度**（需 Linux + `/dev/kvm`）：同一個 guest，分別用 `-accel kvm` 和 `-accel tcg` 開機，記錄開機到 shell 的時間差。體會「普通指令全速 vs 逐條翻譯」的量級差距。（未實測，理論預期 TCG 慢 5–15x）
3. **觀察 VMEXIT 分布**（需 Linux + KVM）：guest 開機時在 host 跑 `kvm_stat`（或 `perf kvm stat`），看 exit reason 的統計——哪類 VMEXIT 最多、I/O 佔多少。這直觀對應「device I/O 是 hypervisor 最常被叫回來處理的事」。
4. **確認 device 路徑共用**：分別在 KVM 與 TCG 下開一個帶 `-device edu` 的 guest，兩邊都用 Ch 0 的手法 `gdb -p` 斷在 `edu_mmio_write`。確認**兩種後端下同一個 device callback 都會被觸發**，親手驗證「device bug 不挑執行後端」。
5. **推導 x86 的不可虛擬化指令**：查 x86 架構文件，找出至少三條「敏感但在 user mode 不 trap」的指令（除了 `popf`）。說明它們在 pure trap-and-emulate 下會造成什麼虛擬化語意錯誤。

## 本章重點整理

- 虛擬化的核心難題：讓一個以為獨佔硬體的 OS 安全共用機器——必須攔截它的特權指令與 I/O 存取。
- 三代演進：**trap-and-emulate/BT**（軟體攔，x86 不可虛擬化靠 binary translation 硬繞）→ **半虛擬化**（改 guest，主動 hypercall，快但要改 guest；其 device 形式即現代 virtio）→ **硬體輔助 VT-x/AMD-V**（CPU 硬體自動 VMEXIT，guest 全速跑、不用改，今天的主線）。
- VT-x 引入 root/non-root 模式與 VMCS：guest 普通指令全速跑，碰到該攔的事硬體自動 **VMEXIT** 交回 hypervisor。硬體防死了 CPU 虛擬化層，把攻擊面推向 VMEXIT 之後的 **device 模擬**。
- 影子分頁表（舊）被 **EPT** 取代：兩層硬體走訪，DMA 攻擊的「guest GPA = host 可控 buffer」就來自這個映射關係。
- type-1（bare-metal，ESXi/Xen/Hyper-V）vs type-2（hosted，VirtualBox/VMware WS）；**QEMU/KVM 是混血**，逃逸打的是 QEMU 那個 userland 行程。
- QEMU 兩種後端：**KVM**（用 VT-x，快，需 `/dev/kvm`）vs **TCG**（純軟體翻譯，慢，到處能跑）。**兩者的 device 模擬走同一份 `hw/` C 碼**，故 device 逃逸練習不挑後端。

## 自我檢核

- [ ] 我能說出 x86 為何「不可虛擬化」，以及 VMware 早年怎麼用 binary translation 繞過，並舉出一個具體的「敏感但不 trap」指令例子。
- [ ] 我能解釋半虛擬化的核心（改 guest、hypercall），以及它今天以什麼形式（virtio）存活。
- [ ] 我能畫出 VT-x 的 root / non-root 模式與 VMEXIT/VMENTRY 完整迴圈，並說明它把攻擊面推向哪裡。
- [ ] 我能區分 type-1 / type-2，並說明 QEMU/KVM 為何是混血、逃逸打的是哪個部分。
- [ ] 我能講清楚 KVM 與 TCG 的差別，以及為什麼 device 逃逸兩種後端都能練。
- [ ] 我能說明影子分頁表被 EPT 取代的意義，以及 EPT 和 DMA 攻擊的關聯。

## 延伸閱讀

- **Popek & Goldberg, "Formal Requirements for Virtualizable Third Generation Architectures" (1974)**——虛擬化理論的源頭。讀「敏感指令必須是特權指令」這個條件，就懂 x86 為何不合格、為何需要 BT/VT-x。這篇論文定義了此後五十年虛擬化研究的框架，兩頁的定理讀完能讓你對每一代技術選擇「為什麼」的問題有直接的理論答案。

- **VMware, "A Comparison of Software and Hardware Techniques for x86 Virtualization"（Adams & Agesen, ASPLOS 2006）**——binary translation 與早期 VT-x 的一手比較，出自 VMware 工程師。理解 BT 的精巧與 VT-x 初代的侷限（早期 VT-x 某些路徑比 BT 還慢）；這篇也解釋了為什麼「硬體輔助」初登場時效能並非全面勝出，要到第二三代 Intel VT-x 才拉開差距。

- **Intel® 64 and IA-32 Architectures Software Developer's Manual, Vol. 3C，Chapter 24–33（VMX 章節）**——VT-x 的權威文件：VMCS 結構、VMEXIT reason 完整列表（Appendix C）、root/non-root 模式。Ch 4 會逐項對照，先知道你最終要讀的一手材料在這。特別推薦先讀 Table C-1（VM-Exit Reasons）的前十條，對應本章的 VMEXIT 分類圖。

- **KVM API 文件（Linux 原始碼 `Documentation/virt/kvm/api.rst`）**——KVM 提供給 QEMU 的 `/dev/kvm` ioctl 介面。讀 `KVM_RUN` 與 `kvm_run` struct 的 exit reason 定義（`KVM_EXIT_IO`、`KVM_EXIT_MMIO`、`KVM_EXIT_EPT_VIOLATION`…），Ch 7「KVM 到 QEMU 的 exit 交接」會實作對應流程。

- **QEMU `docs/devel/tcg.rst` 與 `accel/tcg/` 目錄、`accel/kvm/` 目錄**——TCG 與 KVM accelerator 的實作說明。特別看 `accel/kvm/kvm-all.c` 裡的 `kvm_cpu_exec()`，這個函式就是 KVM 加速下的 VMEXIT 主迴圈，對應本章 VMEXIT 迴圈圖的 host 端程式碼。理解 QEMU 如何在 `KVM_RUN` 返回後分發 exit reason 到不同 handler，你就掌握了逃逸入口的完整路徑。

三代機制過完，你知道了 VMEXIT 是攻擊入口、device 模擬是主戰場。下一章我們把整個 hypervisor 的攻擊面攤成一張全圖——系統性列出所有 guest→host 通道，並給每個通道配一個真實 CVE，當作後面所有 Part 的地圖。

→ [Ch 3 攻擊面全圖](./03-attack-surface-map.md)
