# Ch 4 — Intel VT-x：VMX root/non-root、VMCS、VMEXIT

> **目標**：從硬體層面理解 Intel VT-x 如何切割 root/non-root 兩個執行宇宙，以及 VMM（Virtual Machine Monitor）如何透過 VMCS 與 VMEXIT 完全掌控 guest。

---

## 為什麼需要這個？

在 VT-x 出現之前（2005 年以前），跑虛擬機靠的是**純軟體模擬**或**準虛擬化（paravirtualization）**。

**純軟體路線**——VMware 早期的 binary translation。VMM 在 guest 執行前掃描每一段程式碼，把敏感指令（`CPUID`、`IN/OUT`、改 `CR3` 等）替換成 VMM 可控制的 trap stub，再快取下來跑。問題是：
- 掃描+改碼本身有 overhead，JIT 快取也要記憶體
- x86 有些指令「靜默地」讀取真實硬體狀態而**不觸發 fault**（`PUSHF/POPF` 在 ring 1 不 trap），這讓 isolation 出現縫隙
- 64-bit 長模式讓分段把戲幾乎失效，binary translation 更難做

**準虛擬化路線**——Xen 讓 guest kernel 主動呼叫 hypercall 配合 hypervisor。速度快但要改 guest kernel，跑 Windows 就不行。

Intel 在 2005 年（Pentium 4 Prescott-2M、Yonah）推出 **VT-x（Virtualization Technology for IA-32/64）**，AMD 同年推出 AMD-V（SVM）。核心思想是在硬體新增一個「比 ring 0 更底層」的特權模式，讓 VMM 躲在那裡，guest kernel 跑在硬體加速的假 ring 0 裡，所有敏感操作自動 trap 回 VMM，不需要改 guest 程式碼。

---

## 先建立直覺

把 CPU 想像成一個有兩張臉的演員：

```
┌──────────────────────────────────────────────────────────┐
│                      PHYSICAL CPU                        │
│                                                          │
│  ┌─────────────────────┐   ┌─────────────────────────┐  │
│  │   VMX ROOT mode     │   │   VMX NON-ROOT mode     │  │
│  │   (host / VMM)      │   │   (guest)               │  │
│  │                     │   │                         │  │
│  │  ring 0: KVM        │   │  ring 0: guest kernel   │  │
│  │  ring 3: QEMU       │   │  ring 3: guest userland │  │
│  │                     │   │                         │  │
│  │  完整 CPU 能力       │   │  受控 CPU 能力          │  │
│  │  VMXON 啟動         │   │  VMLAUNCH/VMRESUME 進入 │  │
│  └─────────────────────┘   └─────────────────────────┘  │
│            ▲                          │                  │
│            │     VMEXIT               │ VMLAUNCH/        │
│            └──────────────────────────┘ VMRESUME         │
└──────────────────────────────────────────────────────────┘
```

兩個 mode 之間的橋是 **VMCS（VM Control Structure）**——一塊 VMM 分配的記憶體頁，裡面存著 guest 的完整 CPU 狀態（RIP、RSP、CR3……）、host 的回返狀態、以及「哪些操作要 trap」的控制位元。

切換動作：
- **VMM → guest**：`VMLAUNCH`（第一次）/ `VMRESUME`（之後）
- **guest → VMM**：硬體偵測到受控事件，自動 VMEXIT，把 reason 寫進 VMCS

---

## VMX root 與 non-root 模式

### VMXON — 打開 VMX 大門

VMM 在正式 launch guest 前，要先把 CPU 切到 VMX operation：

```c
/* 需要 CR4.VMXE = 1，且 MSR IA32_FEATURE_CONTROL 允許 */
vmxon(pa_of_vmxon_region);   /* VMXON 指令，operand = 4KB 對齊實體位址 */
```

`VMXON` 之後，CPU 進入 **VMX root operation**，這裡就是 VMM 的地盤。ring 0 的 VMM 可以用 `VMREAD`/`VMWRITE` 操作 VMCS，也可以呼叫 `VMLAUNCH` 進入 guest。

### 兩個 mode 的差異

| 維度 | VMX root | VMX non-root |
|------|----------|--------------|
| 誰住在這裡 | VMM (KVM / hypervisor) | Guest OS + user |
| ring 0 的能力 | 完整 | 受 VMCS execution controls 限制 |
| 特殊指令行為 | 正常執行 | 依 VMCS 可能直接 VMEXIT |
| VMEXIT 發生時 | 是目的地 | 是來源 |
| CPU 看到的 CR3 | host 頁表 | EPT 下的 GPA 頁表 |

non-root 的 ring 0（guest kernel）**自以為有完整 ring 0**，但 CPU 在硬體層會過濾：讀 `CPUID`、做 `IN/OUT`、遇到 EPT violation——這些都讓 CPU 自動切回 root。

---

## VMCS 結構與關鍵欄位

VMCS 是一個 **4KB 對齊的記憶體頁**，由 VMM 用 `VMPTRLD` 告訴 CPU「這個 VMCS 是當前 vCPU 的」。CPU 不讓你直接用 memcpy 存取，要透過 `VMREAD` / `VMWRITE` 指令，欄位由 **field encoding**（一個 32-bit 常數）索引。

VMCS 在邏輯上分成六個區域（Intel SDM Vol 3C, Ch 24）：

```
VMCS Layout
├── Guest-state area          ← VMLAUNCH 時載入給 guest
│   ├── GUEST_RIP             (0x681E)  guest 下次執行的 RIP
│   ├── GUEST_RSP             (0x681C)
│   ├── GUEST_CR0/CR3/CR4     (0x6800/0x6802/0x6804)
│   ├── GUEST_CS/SS/DS...     segment registers
│   └── GUEST_IA32_EFER       (0x2806)
│
├── Host-state area           ← VMEXIT 時還原給 host VMM
│   ├── HOST_RIP              (0x6C16)  VMEXIT 後 host 從哪繼續
│   ├── HOST_RSP              (0x6C14)
│   ├── HOST_CR0/CR3          (0x6C00/0x6C02)
│   └── HOST_IA32_EFER        (0x2C02)
│
├── VM-execution control      ← 決定哪些操作 trigger VMEXIT
│   ├── CPU-based controls    (0x4002 / 0x401E)
│   ├── PIN-based controls    (0x4000)
│   ├── EPT pointer (EPTP)    (0x201A)  ← 指向 EPT 根頁表
│   └── MSR bitmap            (0x2004)
│
├── VM-exit control           ← VMEXIT 行為設定
│   └── VM_EXIT_CONTROLS      (0x400C)
│
├── VM-entry control          ← VMLAUNCH/VMRESUME 行為設定
│   └── VM_ENTRY_CONTROLS     (0x4012)
│
└── VM-exit information       ← VMEXIT 後 VMM 讀這裡找原因
    ├── VM_EXIT_REASON        (0x4402)
    ├── EXIT_QUALIFICATION    (0x6400)
    ├── GUEST_LINEAR_ADDRESS  (0x640A)
    └── VM_EXIT_INTR_INFO     (0x4404)
```

**最常存取的幾個欄位：**

`VM_EXIT_REASON`——低 16 bit 是 basic exit reason，高 bit 有 flags（是否在 VMX root 發生、是否因 VM-entry failure）。

`EXIT_QUALIFICATION`——reason 的補充資料。對 `EXIT_REASON_IO_INSTRUCTION (30)` 它告訴你是哪個 port、讀還是寫、size 多少；對 `EXIT_REASON_EPT_VIOLATION (48)` 它告訴你是 read/write/execute 違規。

`GUEST_RIP`——VMEXIT 後 guest 停在哪裡。VMM 處理完之後通常要把它加上 instruction length（從 `VM_EXIT_INSTRUCTION_LEN` 讀）再寫回，讓 guest 繼續往下跑。

---

## VMLAUNCH / VMRESUME 流程

```
VMM (root, ring 0)
│
├─ VMWRITE HOST_RIP = vmexit_handler_addr
├─ VMWRITE GUEST_RIP = guest_entry_point
├─ VMWRITE EPTP = eptp               ← 指向 EPT，Ch 5 會細說
├─ ... 其他 VMCS 欄位設好 ...
│
├─ VMLAUNCH ──────────────────────────────────────────┐
│                                                     ▼
│                                          CPU 載入 guest-state
│                                          切換到 non-root mode
│                                          guest RIP 開始執行
│                                                     │
│                                    guest 跑... 跑... 跑...
│                                                     │
│                                    ┌────────────────┘
│                                    │  觸發 VMEXIT 事件
│                                    │  CPU 存 guest-state 進 VMCS
│                                    │  載入 host-state
│                                    │  跳到 HOST_RIP
│                                    ▼
└──────── vmexit_handler() ◄──────────────────────────┘
          │
          ├─ VMREAD VM_EXIT_REASON → reason
          ├─ switch (reason):
          │    case EXIT_REASON_CPUID (10):   模擬 CPUID 回傳
          │    case EXIT_REASON_IO (30):       模擬 port I/O
          │    case EXIT_REASON_EPT (48):      處理 EPT fault
          │    case EXIT_REASON_VMCALL (18):   處理 hypercall
          │    ...
          │
          └─ VMRESUME  ──────────────────────────────── (回到上面 non-root)
```

VMLAUNCH 只用**一次**（初始化 vCPU），之後每次從 VMEXIT 回去都用 `VMRESUME`。如果 VMLAUNCH 失敗，CF 或 ZF 會被設起來，`VM_INSTRUCTION_ERROR` 欄位（0x4400）存錯誤碼。

---

## 核心 VMEXIT 事件詳解

### EXIT_REASON_CPUID (10)

Guest 執行 `CPUID` **永遠** trap（不管 execution controls 怎麼設）。這讓 VMM 可以偽裝 CPU 型號、隱藏 hypervisor feature bits，也可以回傳 `0x40000000` leaf 來暴露 hypervisor signature（KVM 用 `"KVMKVMKVM\0\0\0"`）。

VMM 處理邏輯：讀 `GUEST_RAX`（EAX=leaf）、`GUEST_RCX`（ECX=subleaf），模擬 CPUID 結果，寫回 `GUEST_RAX/RBX/RCX/RDX`，然後把 `GUEST_RIP += instruction_len`，VMRESUME。

### EXIT_REASON_IO_INSTRUCTION (30)

`IN`/`OUT` 指令在 non-root 是否 trap 由 **I/O bitmap**（兩個 4KB 頁，涵蓋 port 0x0000–0xFFFF 各一 bit）控制。對應 bit = 1 → trap。

`EXIT_QUALIFICATION` bit 0 = direction（0=OUT, 1=IN），bit 6:3 = size encoding，bit 15:8 = port number（若是 DX 間接定址則從 GUEST_RDX 讀）。

QEMU 靠這個模擬 PCI config space（port 0xCF8/0xCFC）、ISA DMA、傳統 I/O 裝置。這裡也是很多 VM escape 的戰場——模擬裝置的 I/O handler 有 bug → 越界寫。

### EXIT_REASON_EPT_VIOLATION (48)

Guest 做了一個 GPA（Guest Physical Address）存取，但 EPT 對那個 GPA 沒有對應的 HPA 頁表項，**或者**存取類型不符合頁表項的讀/寫/執行權限。

`EXIT_QUALIFICATION` bit 2:0 = 是哪種存取（read/write/exec）觸發違規，bit 7:3 = 該 GPA 頁表項的實際權限。`GUEST_PHYSICAL_ADDRESS`（0x2400）欄位存有違規的 GPA。

VMM（KVM）收到這個 exit 後，通常是去把那個 GPA 補上 HPA 映射，再 VMRESUME。EPT 的細節留給 [Ch 5](./05-ept-second-level-paging.md)。

### EXIT_REASON_VMCALL (18)

Guest 執行 `VMCALL` 指令（在 non-root 唯一合法的「主動求 exit」方式），用來實作 **hypercall**。KVM 用 `EAX` 傳 hypercall number（定義在 `arch/x86/include/uapi/asm/kvm_para.h`），常見的有 `KVM_HC_KICK_CPU (3)`、`KVM_HC_CLOCK_PAIRING (9)`。

從 VM escape 角度看，如果 VMM 的 hypercall dispatcher 解析參數有 bug（長度沒 validate、指標沒 sanitize），就可以在 root mode 的 kernel 空間搞事。

### 把一次 CPUID exit 走完

抽象講完，用一個最單純的 exit 把整個迴圈坐實。guest 執行 `cpuid`（EAX=1，查 feature bits）：

```
1. guest 執行 CPUID，EAX=1
2. 硬體無條件 VMEXIT
   VM_EXIT_REASON            = 10 (EXIT_REASON_CPUID)
   GUEST_RIP                 = 0x401000 (CPUID 那條指令的位址)
   VM_EXIT_INSTRUCTION_LEN   = 2       (CPUID 是 2 bytes: 0F A2)
   guest RAX/RCX 已被存進 VMCS guest-state
3. host 的 vmexit_handler 跑起來：
   reason = VMREAD(VM_EXIT_REASON) & 0xffff   → 10
   leaf   = VMREAD(GUEST_RAX)                 → 1
   模擬 CPUID(1)，但故意清掉 ECX bit 31（hypervisor-present bit）
   或不清，讓 guest 知道自己在 VM 裡（KVM 預設會設）
   VMWRITE(GUEST_RAX, ...); VMWRITE(GUEST_RBX, ...); (RCX/RDX 同)
4. 推進 RIP，否則 guest 會卡在同一條 CPUID 上無限 exit：
   rip = VMREAD(GUEST_RIP) + VMREAD(VM_EXIT_INSTRUCTION_LEN)
   VMWRITE(GUEST_RIP, rip)   → 0x401002
5. VMRESUME → guest 從 0x401002 繼續，完全不知道剛剛出去了一趟
```

> **未實測，理論預期**：以上 field encoding 與流程依 Intel SDM Vol 3C Ch 25-27，本教材環境（Windows）未實跑 VMX 指令。你若想親眼看到 KVM 版本的這條路，在 Linux 上 `trace-cmd record -e kvm:kvm_exit`，過濾 `reason 10` 即可看到每一次 CPUID exit 的計數與 RIP。

這五步就是所有 exit 的骨架：**讀 reason → 讀相關欄位 → 模擬 → 推進 RIP → VMRESUME**。後面每個複雜 exit（IO、EPT、MMIO）只是第 3 步變複雜，骨架不變。記住這個骨架，你在 [Ch 8](./08-minimal-kvm-hypervisor.md) 親手寫 KVM 版本時就會發現 ioctl 介面只是把這五步包了一層 syscall。

---

## 底層機制：VMEXIT 那一刻 CPU 做了什麼

以下流程完全由**硬體執行**，VMM 沒有介入機會（Intel SDM Vol 3C, Ch 27.2）：

```
Guest 在 non-root 執行
        │
        │  觸發條件（CPUID/EPT fault/...）
        ▼
1. CPU 停止 guest 指令流
2. 把 guest 暫存器狀態存入 VMCS guest-state area
   (RIP, RSP, RFLAGS, CR0/CR3/CR4, segment regs, ...)
3. 把 exit 資訊寫入 VMCS:
   VM_EXIT_REASON
   EXIT_QUALIFICATION
   VM_EXIT_INTR_INFO (若是 exception)
   VM_EXIT_INSTRUCTION_LEN (若需要 skip)
4. 從 VMCS host-state area 載入 host 暫存器
   (CR0/CR3, RSP = HOST_RSP, RIP = HOST_RIP, ...)
5. 切回 VMX root operation
6. 跳到 HOST_RIP（= VMM 的 vmexit_handler）
```

整個過程**不涉及任何 VMM 程式碼執行**——是純硬體狀態機。這也是為什麼 VMEXIT 開銷比 syscall 高：CPU 要存/還原的暫存器更多，還要重新載入 host 頁表（CR3 換掉）、flush 部分 TLB。

KVM 的 VMEXIT handler 入口在 `arch/x86/kvm/vmx/vmx.c: vmx_handle_exit()`，它讀 `VM_EXIT_REASON`，用一個函數指標陣列 `kvm_vmx_exit_handlers[]` dispatch 到對應的 handler。

---

## AMD-V（SVM）一句話比較

AMD 的對應技術叫 **SVM（Secure Virtual Machine）**，控制結構叫 **VMCB（VM Control Block）** 而不是 VMCS，進入 guest 用 `VMRUN` 而不是 `VMLAUNCH/VMRESUME`，exit 叫 `#VMEXIT`，exit reason 叫 `EXITCODE`。概念完全一樣，欄位名稱和 encoding 不同，KVM 在 `arch/x86/kvm/svm/` 有平行實作。

---

## 對比與取捨

| 比較維度 | VT-x / VMX | 無硬體虛擬化（binary translation） |
|----------|------------|-------------------------------------|
| Guest 改動需求 | 不需要 | 不需要（VMware 老方案） |
| 敏感指令攔截 | 硬體自動 | 掃碼替換，有漏洞 |
| 64-bit guest 支援 | 完整 | 極難，binary translation 放棄 |
| VMEXIT overhead | ~1000+ cycles | 視替換後代碼而定，通常更高 |
| 隔離強度 | 硬體保證 | 軟體保證，理論上可繞過 |
| 需要 kernel 支援 | 是（KVM 模組） | 不一定（userland 可做） |

| 比較維度 | VT-x | AMD-V (SVM) |
|----------|------|-------------|
| 控制結構 | VMCS | VMCB |
| 進入 guest | VMLAUNCH / VMRESUME | VMRUN |
| Exit 指令 | VMEXIT（硬體自動） | #VMEXIT |
| Nested paging | EPT | NPT（Nested Page Tables） |
| Linux KVM 位置 | `kvm/vmx/` | `kvm/svm/` |

---

## 踩雷集錦

**1. 以為 VMCS 可以直接 memcpy 讀**

錯誤直覺：VMCS 就是個 struct，cast 一下就能讀欄位。

正確理解：VMCS 格式是 CPU microarchitecture-defined，不同 CPU stepping 佈局不同，Intel 沒公開 spec。唯一合法存取方式是 `VMREAD dst, field_encoding` 和 `VMWRITE field_encoding, src`。直接 memcpy 讀到的是亂碼。

**2. 以為 VMEXIT 後 guest 狀態完全被凍結**

錯誤直覺：VMEXIT 發生，guest 的所有狀態一動也不動。

正確理解：guest-state area 的欄位被存進 VMCS，但 **VMM 可以在 VMRESUME 之前用 VMWRITE 修改它們**。這是 VMM 模擬指令效果的基礎，也是 VM escape 後 rootkit 藏身的地方——在 VMCS 裡動手腳讓 guest 以為自己在正常跑。

**3. 以為 VMRESUME 之後 guest 從 VMEXIT 的下一條指令繼續**

錯誤直覺：VMEXIT 發生在某條指令，VMRESUME 後 guest 自動跳過那條指令繼續。

正確理解：`GUEST_RIP` 指向**觸發 VMEXIT 的那條指令本身**，不是下一條。如果 VMM 不手動把 `GUEST_RIP += VM_EXIT_INSTRUCTION_LEN`，guest 會無窮迴圈在同一條指令上。對 CPUID、VMCALL、IN/OUT 這種「VMM 模擬完要繼續」的，必須自己推進 RIP。

**4. 以為 CPU-based execution controls 可以攔截所有指令**

錯誤直覺：把所有 bit 都設起來就能 trap 任何 guest 行為。

正確理解：某些指令**無條件** trap（CPUID、VMCALL），某些可選擇性 trap（IN/OUT 靠 I/O bitmap，MOV-to-CR 靠 CR-access controls），某些**永遠不** trap（`NOP`、一般算術）。controls 只管 VMX non-root 的敏感操作，普通指令不過這條路。

**5. 以為 VMXON 之後就可以直接 VMLAUNCH**

錯誤直覺：VMXON 開啟 VMX，然後直接 VMLAUNCH 進 guest。

正確理解：VMLAUNCH 之前要先 `VMPTRLD`（把分配好的 VMCS 頁和當前 PCPU 綁定），再用 `VMWRITE` 把所有必填的 guest-state、host-state、control 欄位填好，VMLAUNCH 才不會因 VM-entry failure（CF=1）噴出來。KVM 的初始化程式碼在 `vmx_vcpu_create()` 和 `vmx_set_cr0()` 等一堆函數裡慢慢設好每個欄位。

---

## 進階：再往深一層

### VMCS shadowing（Nested Virtualization 的基礎）

當 L1 hypervisor（跑在 non-root 的 VMM）想啟動 L2 guest 時，L1 本身也要執行 `VMLAUNCH`。問題來了：non-root 裡執行 `VMLAUNCH` 會 VMEXIT 到 L0（KVM）。L0 要攔截這個，讀 L1 的 VMCS 寫入、模擬 L2 的 context、建立 shadow VMCS 給硬體用，這就是 **nested VMX**。

Intel 在較新 CPU 上支援 **VMCS shadowing**（VMCS link pointer），讓 L1 對 L2 VMCS 的 `VMREAD`/`VMWRITE` 可以不 exit，直接打到 shadow VMCS——減少 nested 的三層 exit overhead。KVM 的實作在 `arch/x86/kvm/vmx/nested.c`，幾千行。

### TSC scaling 與 VM-exit overhead 量化

每次 VMEXIT 要存/還原上百個暫存器，加上 TLB flush、pipeline flush，實測開銷約 1000–5000 cycles（視 CPU 世代和 exit reason）。高頻 VMEXIT（例如 guest 大量做 `RDTSC`）是效能瓶頸，所以 execution controls 有個 `RDTSC exiting` bit 預設是 off，讓 guest 直接讀 TSC 不 exit，再靠 TSC offset 欄位（VMCS 0x2010）做時間校正。

### VMFUNC — 不用 VMEXIT 的 EPT 切換

Intel 在 Haswell 引入 `VMFUNC` 指令，讓 guest 可以在**不觸發 VMEXIT** 的情況下呼叫 VMM 預先定義好的函數（目前只有 function 0 = EPTP switching）。Project Zero 的 Jann Horn 發現某些 VMFUNC 實作有 bug 可被利用——這條路直接影響 sandbox escape 的攻擊面。

---

## 動手練習

> **免責聲明**：以 Intel SDM 為準，本教材環境（Windows）未實際執行 VT-x 相關程式碼，所有行為描述以 SDM 原文與社群文件為依據。以下練習在 Linux 環境、需要載入 KVM 模組，或者在現有 Linux VM 內做觀測。

**練習 1：用 KVM API 觀察 VMEXIT**

在 Linux 環境，安裝 `kvmtool` 或自己寫一個最小 KVM 程式（參考 `samples/kvm/` in kernel source）。在 `ioctl(vcpu_fd, KVM_RUN, 0)` 之後檢查 `kvm_run->exit_reason`，跑一個只有 `CPUID; HLT` 的 flat binary，觀察第一個 exit reason 是不是 `KVM_EXIT_MMIO` 還是 `KVM_EXIT_IO`（取決於你的初始設定）。

**練習 2：讀 KVM trace events**

```bash
# 在 Linux host 上
echo 1 > /sys/kernel/debug/tracing/events/kvm/kvm_exit/enable
echo 1 > /sys/kernel/debug/tracing/tracing_on
# 跑一個 VM ...
cat /sys/kernel/debug/tracing/trace | grep kvm_exit | head -50
```

觀察 `exit_reason` 欄位，看看哪些 reason 最頻繁（通常是 HLT 和 EPT violation）。

**練習 3：VMCS field encoding 查表**

查 Intel SDM Vol 3C, Appendix B（VMCS Field Encoding），找出以下欄位的 encoding 十六進位值，並說明它屬於哪個 VMCS 區域：
- `GUEST_CR3`
- `HOST_RIP`
- `VM_EXIT_REASON`
- `EXIT_QUALIFICATION`
- `EPT_POINTER`

**練習 4：推斷 exit reason**

假設一個 guest 在執行 `IN AL, 0x64`（鍵盤控制器 port）時觸發 VMEXIT。根據 SDM 描述，`EXIT_QUALIFICATION` 的值應該是多少？（方向=IN, size=1 byte, port=0x64, 非 string, 非 REP）手動計算該欄位的 bit 佈局。

---

## 本章重點整理

- VT-x 引入兩個執行 mode：**VMX root**（VMM 地盤）和 **VMX non-root**（guest 地盤），硬體切割特權
- **VMCS** 是兩個 mode 之間的共享狀態容器，分 guest-state / host-state / execution controls / exit info 四大區域，只能透過 `VMREAD`/`VMWRITE` 存取
- 切換流程：`VMXON` → `VMPTRLD` → `VMWRITE` 初始化 → `VMLAUNCH` → guest 跑 → VMEXIT → `vmexit_handler` → `VMRESUME` → 循環
- **VMEXIT 是硬體自動觸發**，CPU 存 guest state、寫 exit info、跳 HOST_RIP，VMM 沒有中間介入機會
- 四個關鍵 exit reason：CPUID (10)、IO (30)、EPT violation (48)、VMCALL (18)——每個都有不同的 EXIT_QUALIFICATION 語義
- VMM 模擬完指令後**必須手動推進 GUEST_RIP**，否則 guest 無窮迴圈
- AMD-V 的對應技術叫 SVM，控制結構叫 VMCB，進入用 `VMRUN`

---

## 自我檢核

- [ ] 我能說出 VMX root 和 non-root 的根本差異，不只是「一個是 host 一個是 guest」
- [ ] 我能列出 VMCS 的六個邏輯區域並說明每個的用途
- [ ] 我能解釋為什麼 VMLAUNCH 之後的每次 re-entry 要用 VMRESUME 而不是 VMLAUNCH
- [ ] 我能說出 EXIT_REASON_EPT_VIOLATION 的 EXIT_QUALIFICATION bit 代表什麼（讀/寫/執行）
- [ ] 我能解釋 VMM 處理 CPUID exit 的完整步驟（讀 EAX、模擬、寫回、推進 RIP、VMRESUME）
- [ ] 我能說出為什麼 VMEXIT 比 syscall 開銷更高
- [ ] 我能說出 SVM 和 VMX 在術語上的三個主要差異
- [ ] 我知道「VMM 不推進 GUEST_RIP」會導致什麼後果

---

## 延伸閱讀

1. **Intel SDM Vol 3C, Ch 23–27**（《Intel® 64 and IA-32 Architectures Software Developer's Manual》）
   - 在哪讀：[intel.com/sdm](https://www.intel.com/content/www/us/en/developer/articles/technical/intel-sdm.html)，下載 combined PDF，Vol 3C 即 Part 3 后半
   - 學到什麼：VMX 的完整規格，VMCS field encoding（Appendix B）、所有 exit reason 的 EXIT_QUALIFICATION 定義（Ch 27.2）、VM-entry 失敗的錯誤碼（Ch 26.7）
   - 本章連結：本章所有欄位名稱和 encoding 都直接來自 SDM，讀原文才能確認 bit 定義細節

2. **KVM API 文件**（`Documentation/virt/kvm/api.rst` in kernel source）
   - 在哪讀：[kernel.org docs](https://www.kernel.org/doc/html/latest/virt/kvm/api.html) 或 kernel source tree
   - 學到什麼：`KVM_RUN` ioctl、`kvm_run` struct 的 exit reason 列表、`KVM_SET_CPUID2`、`KVM_SET_USER_MEMORY_REGION` 等—— Linux 怎麼把 VT-x 包裝給 userland
   - 本章連結：把 VT-x 的硬體概念對應到 Linux API 層，看 `exit_reason` 對應 `VM_EXIT_REASON`

3. **QEMU 源碼：`target/i386/kvm/kvm.c`**（QEMU on GitHub）
   - 在哪讀：[github.com/qemu/qemu](https://github.com/qemu/qemu)，搜 `kvm_handle_exit`
   - 學到什麼：QEMU 怎麼處理 KVM 回傳的 exit reason，I/O exit 怎麼路由到裝置模擬，CPUID 怎麼被攔截篡改
   - 本章連結：exit reason dispatch 的真實實作，對應本章「VMEXIT handler 的工作」

4. **LWN: "A tour of the Linux KVM API"**（Paolo Bonzini, 2021）
   - 在哪讀：[lwn.net/Articles/658511](https://lwn.net/Articles/658511/)（及後續系列）
   - 學到什麼：從 Linux 開發者視角看 KVM 設計決策，vCPU 的 thread model，memory slot 機制，為什麼要分 KVM_RUN loop
   - 本章連結：把本章的 VMLAUNCH/VMRESUME loop 對應到 KVM 的 userspace/kernel 協作模型

5. **Project Zero: "Virtually Secure" / Jann Horn 的 VMFUNC 分析**
   - 在哪讀：[googleprojectzero.blogspot.com](https://googleprojectzero.blogspot.com/)，搜 "vmfunc" 或 "KVM vulnerability"
   - 學到什麼：攻擊者如何利用 VMCS/EPT 相關 bug 做 VM escape，VMFUNC EPTP switching 的攻擊面，真實 CVE 的 root cause 分析
   - 本章連結：本章是底層機制，Project Zero 的文章把機制轉成攻擊語言，直接連到後面 escape 章節的思維

---

本章把 VT-x 的硬體骨架搭起來了：root/non-root 切割、VMCS 狀態容器、VMEXIT dispatch 循環。下一步是理解 guest 的記憶體視角——guest 以為自己在操作實體記憶體，但每個 GPA 背後都有一張 EPT 頁表把它對應到真正的 HPA。

→ [Ch 5 EPT 二階分頁：GPA → HPA 與它對逃逸的意義](./05-ept-second-level-paging.md)
