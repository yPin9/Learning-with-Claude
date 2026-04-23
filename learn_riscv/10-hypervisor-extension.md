# Ch 10 — Hypervisor extension 速覽

> 目標：理解 RISC-V 為了跑 hypervisor（KVM / Xen 風格）做了什麼設計、為什麼沒用 x86 式的 VT-x / VMCS 而是把 S-mode 升級成 HS-mode。這章不要求你寫 hypervisor，但要能在面試中解釋 RISC-V 虛擬化的架構決策。

## 為什麼不直接抄 x86

x86 的虛擬化經歷痛苦：

- 早期沒硬體支援、靠 binary translation（VMware ESX 1.x）
- Intel VT-x / AMD-V 後才「原生」虛擬化
- VMCS (Virtual Machine Control Structure) 是一個極其複雜的資料結構

**RISC-V 從一張白紙開始**，選了不同路徑：**把 S-mode 「雙層化」**。

## 架構設計：沒 Hypervisor vs 有 Hypervisor

### 沒 Hypervisor 擴充：三層 M/S/U

```
┌──────────┐
│ U-mode   │  user process
├──────────┤
│ S-mode   │  kernel
├──────────┤
│ M-mode   │  firmware (OpenSBI)
└──────────┘
```

### 有 Hypervisor 擴充：加一個 HS-mode

```
┌──────────┐
│ VU-mode  │  guest user (virtualized)
├──────────┤
│ VS-mode  │  guest kernel (virtualized)
├──────────┤
│ HS-mode  │  hypervisor (host kernel + hypervisor 合一)
├──────────┤
│ M-mode   │  firmware
└──────────┘
```

S-mode 升級成 **HS-mode**（Hypervisor-extended Supervisor mode）。HS-mode 可以：

- 直接做所有 S-mode 能做的事
- 額外管理 VS-mode 與 VU-mode
- 用新的「V-flavored」CSR 控制 guest 狀態

**關鍵**：HS-mode 跟 S-mode 不是兩個 mode，是**同一個 mode 加 flag**。開機 firmware 啟動 HS-mode（如果硬體有 H 擴充）、不用 hypervisor 的場景就退化回 S-mode。

## 兩層 translation

虛擬化的核心問題：guest 看到的「physical address」是假的。硬體要做兩層 translation：

```
guest virtual addr (VA)
      │ guest 的 page table (satp / vsatp)
      ▼
guest physical addr (GPA)
      │ host 的 page table (hgatp)
      ▼
host physical addr (HPA)
```

RISC-V H 擴充新增 `hgatp` CSR（Hypervisor Guest Address Translation & Protection），指向**第二層** page table。硬體 TLB 在 VS-mode 執行時會自動兩層查表。

這就是所謂的 **two-stage translation / nested paging**。x86 叫 EPT、ARM 叫 Stage 2，RISC-V 叫 G-stage。

## 新增的 CSR 大分類

H 擴充引入一堆 CSR，大致分三類：

### 1. 對應 S-mode 的 "virtual shadow" CSR

guest 讀 `satp` 時其實讀到 `vsatp`（V 是 virtual 的意思）：

```
hstatus       guest 的 status
vsstatus      guest S-mode status (guest 看起來像 sstatus)
vstvec        guest S-mode trap vector
vsepc         guest S-mode trap PC
vscause       guest S-mode cause
vstval
vsatp
```

HS-mode 可以讀寫這些做 guest context switch。

### 2. 全新 hypervisor 專屬

```
hedeleg       哪些 exception 直接給 VS-mode
hideleg       哪些 interrupt 直接給 VS-mode
hie / hip     hypervisor interrupt enable / pending
hgatp         G-stage page table root
htval         guest bad address
htinst        trap 發生時的指令
hvip          hypervisor-injected virtual interrupt
```

### 3. Trap 相關（HS-mode 接手的 trap）

HS-mode 的 trap handler 重用 `stvec` / `sepc` / `scause`（因為 HS 是 S 的延伸）。但 `scause` 編碼會**出現新的 code** 表示 "VS-mode" 來的：

- 10: Environment call from VS-mode
- 20: Instruction guest-page-fault
- 21: Load guest-page-fault
- 22: Virtual instruction
- 23: Store guest-page-fault

hypervisor 看到這些就知道 guest 觸發了什麼。

## 關鍵指令：HLV / HSV

HS-mode 需要**用 guest 的地址翻譯**去讀寫 memory（例如 emulate MMIO 時，要知道 guest 的 PTE 內容）。一般 load/store 用 host 的 translation，不對。

HLV / HSV 家族專門做這件事：

```
hlv.w    rd, (rs1)      # load word with guest translation
hlv.d    rd, (rs1)      # load doubleword, guest translation
hsv.w    rs2, (rs1)     # store word with guest translation

hlv.wu   (unsigned load)
hlvx.hu  / hlvx.wu      # execute-permission version (特殊用途)
```

這些指令讓 HS-mode 精確模擬 guest 的 memory access。類似 Xen 的 hypercall handler 在做的事。

## SBI 的對應演化

S-mode 跟 M-mode 之間的 SBI（Supervisor Binary Interface）也擴充了。加入的新 call：

- `sbi_hart_start` / `sbi_hart_stop` — SMP boot
- `sbi_system_reset`
- `sbi_hsm_*` — hart state management
- nested 時，guest 可以透過 SBI 呼叫 hypervisor（hypervisor 接住後轉 forward）

## KVM 在 RISC-V 上

Linux KVM 的 RISC-V port 2022 正式 upstream。架構：

```
┌────────────────────────────┐
│ QEMU (user-mode, x86 host) │
├────────────────────────────┤
│ KVM in host Linux          │  HS-mode (如果在 RISC-V host)
│   ↕ ioctl                  │
│   ↕ VS-mode switch         │
├────────────────────────────┤
│ Guest Linux kernel         │  VS-mode
├────────────────────────────┤
│ Guest userspace            │  VU-mode
└────────────────────────────┘
```

程式碼在 `arch/riscv/kvm/`。核心檔：

- `vcpu.c`：context switch
- `vcpu_exit.c`：trap from guest handling
- `mmu.c`：G-stage page table 管理

讀這些 file 大約幾千行，可以完整理解 RISC-V 虛擬化的實作。不是 hypervisor 工程師也值得讀一遍，**這是最具體的 privileged spec 教材**。

## 跟 ARMv8-A EL2 比較

ARM 的 hypervisor 機制：EL0 (user) / EL1 (kernel) / EL2 (hypervisor) / EL3 (firmware)。

比較 RISC-V：

| 特性 | RISC-V | ARMv8-A |
|------|--------|---------|
| Hypervisor 層 | HS-mode（S-mode 雙重化）| EL2 單獨一層 |
| Guest kernel 運行 | VS-mode | EL1 (guest) |
| Guest page table | vsatp + hgatp | VTTBR |
| New hypervisor instructions | HLV/HSV | LDXR/STXR 的 AT 變種 |
| Guest interrupt injection | hvip CSR | GICv3 的 ICH_LR |

**RISC-V 的設計更「一體化」**：S-mode 跟 HS-mode 共用大量 CSR 編碼，code path 很相似。ARM 的 EL2 是獨立一層，code path 要重寫。各有各的道理。

## Hypervisor 擴充的 optional 層級

H 擴充是 optional。RISC-V profiles：

- RVA22：H 是 optional
- RVA23：**H 被列為 mandatory for server**。Server-level RISC-V CPU 必須支援。

個人嵌入式 MCU 基本不會有。server-class core（SiFive P870、XuanTie C920、Rivos 的設計）都有。

## 面試可能的問題

1. **「RISC-V 比 x86 容易做虛擬化嗎？」**
   - 從硬體複雜度看：是。沒有 x86 那種 self-modifying code quirks、沒有 segment register、沒有 SMM 干擾。
   - 從 privilege 翻轉效能看：類似。一次 VM exit 的成本不低。

2. **「RISC-V 怎麼實作 nested virtualization？」**
   - spec 沒強制要求。要硬體在 G-stage 上再做一層 nested G-stage。目前 spec 還在討論中（2025）。

3. **「Hypervisor 下 guest 的 `ecall` 怎麼處理？」**
   - VU-mode `ecall` → 觸發 cause 8（U-mode ecall），delegated 給 VS-mode（guest kernel）。hypervisor 不介入。
   - VS-mode `ecall` → 觸發 cause 10（VS-mode ecall），**hypervisor 接手**，決定要不要 forward 給 firmware。

4. **「RISC-V hypervisor 的 interrupt 怎麼 route」**
   - PLIC / AIA 的 interrupt → HS-mode。HS 根據策略決定是自己處理還是「inject」到 guest 的 vsip。

## 跟 toolchain 的關係

H 擴充對 compiler 工程師的影響：

- **`-march` 加 `h`**：啟用 H 擴充 intrinsic 跟 HLV/HSV 指令。
- **新的 relocation**：不常見，但 hypervisor-loader 有些 PC-relative relaxation 需要特殊處理。
- **Linux guest 的 compile**：guest kernel compile 時不需要特別 flag，跟普通 RV64 kernel 一樣。HS-mode hypervisor 才需要 H 支援。

SiFive 的 job spec 沒有明確提 hypervisor，但**如果他們的核心是 server-class，幾乎一定會碰**。至少要能講出 two-stage translation、能解釋 HLV 指令的用途。

## 常見誤會

1. **「H 擴充替代 M-mode」**：不。M-mode 還是最底層，HS-mode 在 S 上面。firmware 還在 M。
2. **「Guest 看到的 physical address 是真的」**：不。是 GPA，硬體透過 hgatp 翻譯成 HPA。
3. **「KVM-RISC-V 跟 KVM-x86 API 完全不同」**：API 層級（ioctl）幾乎一致，arch-specific 部分是 vcpu state 結構。
4. **「Hypervisor 只有 server 才用」**：嵌入式也用，例如汽車業的 multi-OS integration。但 server 需求最大。
5. **「不會 Hypervisor extension 就不能寫 RISC-V 系統軟體」**：當然不是。Zcb / RVV / privileged spec 都比 H 更常用。H 是選修但值得瞥一眼。

## 動手練習

1. 讀 Linux `arch/riscv/kvm/vcpu_switch.S`，找出 VS ↔ HS 切換時存了哪些 CSR。
2. 跑 KVM on RISC-V（現在 QEMU 有 `-accel kvm` 可以，但需要 host 本身是 RISC-V 硬體）— 暫時用 qemu-tcg 模擬看 log 也行。
3. 讀 RISC-V Hypervisor spec（Privileged spec 的 Chapter 8）的 `vsatp` 描述。對照 `satp` 看差異。
4. 閱讀 RVA23 profile 文件，找出 H 擴充為什麼是 mandatory。
5. 寫一個最簡 stub：HS-mode 跑一個 hello world、試著設 hgatp 把 guest page table 指到自己的 code — 這是理解 two-stage translation 的最好方式（但門檻高，可選）。

## 自我檢核

- [ ] 我能畫出 M / HS / VS / VU 的 privilege 階層圖
- [ ] 我能解釋 two-stage address translation 與 hgatp 的角色
- [ ] 我知道 HLV / HSV 指令用來做什麼、為什麼 HS-mode 需要它們
- [ ] 我能比較 RISC-V H 擴充跟 ARM EL2 的異同
- [ ] 我知道 RVA23 把 H 列為 mandatory-for-server 的意義

Part 3 至此完整。下一章進 Part 4 — 開始講 custom extension 的設計範式、SiFive / XuanTie 等廠商各自加了什麼 extension、你要怎麼分辨它們。這部分直接對應 SiFive 職務「add new RISC-V extensions」的工作內容。

→ [Ch 11 Custom extension 的設計範式](./11-custom-extension-patterns.md)
