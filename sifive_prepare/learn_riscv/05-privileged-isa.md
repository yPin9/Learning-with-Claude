# Ch 5 — Privileged ISA：M/S/U mode、CSR、trap

> 目標：理解 RISC-V 為什麼分三個 privilege mode、trap 進來時硬體做了什麼、CSR 家族怎麼組織。不是要你寫完整的 kernel，但要能看懂 OpenSBI / U-Boot / Linux trap handler 的前十行組語。

## 為什麼要有 privilege mode

只有一個 privilege 的 ISA 等於假設「跑的 code 全部可信」。現實世界：

- bootloader 要信任度最高（能動任何硬體）
- kernel 信任度高（能動多數硬體、不能動某些調校用 register）
- user process 信任度最低（只能用 kernel 批准的 syscall）

**多個 privilege mode 就是把這三層寫進硬體**。硬體根據當前 mode 決定：某條指令合不合法、某顆 CSR 讀不讀得到、某塊 memory 存不存取得到。

## RISC-V 的三個 mode

```
┌─────────────────────────────────────────────────────┐
│ M-mode (Machine)     ← 硬體開機啟動在這裡，最高權限  │
│   ├─ OpenSBI / 自家 firmware                         │
│   └─ 單獨跑時也有：極小 MCU 只用 M-mode              │
├─────────────────────────────────────────────────────┤
│ S-mode (Supervisor)                                  │
│   └─ Linux / FreeBSD kernel                          │
├─────────────────────────────────────────────────────┤
│ U-mode (User)                                        │
│   └─ 你的 a.out / syscall-based 程式                 │
└─────────────────────────────────────────────────────┘
```

M 是必選。S 跟 U 是 optional — 嵌入式 MCU 可以只有 M；典型 MCU 可能 M + U；跑 Linux 的系統三個都有。

### 不像 x86 的 Ring 0–3

x86 有四個 ring（0 最高、3 最低），實際只用 0（kernel）跟 3（user），中間兩層廢棄。**RISC-V 跳過這種虛設**，一開始就只定義會真的用到的三層。加上 Hypervisor 擴充（Ch 10）後會多一個 HS-mode，仍然務實。

## CSR：control status register

mode 的所有行為透過 **CSR** 控制。CSR 是 4096 個 12-bit 定址的暫存器空間（多數沒定義）。它們**不在 `x0..x31` register file 裡**，要用 Zicsr 的 `csrr*` 指令存取。

CSR 地址編碼本身就帶 privilege 資訊：

```
csr[11:10]  privilege level     00 = U, 01 = S, 10 = reserved, 11 = M
csr[9:8]    read/write       00–10 = 可讀寫, 11 = 只讀
csr[7:4]    function / area
csr[3:0]    內部 index
```

**存取比自己 mode 高的 CSR 會 trap**。U-mode 讀 `mstatus` → illegal instruction exception。這是硬體層級的保護。

### 怎麼讀 CSR

```asm
csrr  t0, mstatus             # pseudo: csrrs t0, mstatus, x0
csrw  mstatus, t0             # pseudo: csrrw x0, mstatus, t0
csrs  mstatus, t0             # set bits: mstatus |= t0
csrc  mstatus, t0             # clear bits: mstatus &= ~t0
csrsi mstatus, 0x8            # immediate 版本
```

所有 CSR 操作是 **atomic 讀改寫**（一條指令內完成），不用擔心 race。

## M-mode 的核心 CSR

這幾個是寫 trap handler 必須認識的：

| CSR       | 名稱                   | 作用                                    |
|-----------|------------------------|----------------------------------------|
| `mstatus` | Machine status        | 全域狀態（當前 mode、前一個 mode、中斷開關）|
| `mtvec`   | Trap vector base      | trap 發生時跳到哪                       |
| `mepc`    | Exception PC          | trap 發生時的 PC 備份                   |
| `mcause`  | Exception cause       | trap 的原因（中斷 vs 例外 + 編號）      |
| `mtval`   | Trap value            | 額外訊息（出事的地址、illegal 的指令碼）|
| `mie`     | Interrupt enable      | 哪些中斷打開                            |
| `mip`     | Interrupt pending     | 哪些中斷 pending                        |
| `mscratch`| Scratch              | 你可以塞任何東西，trap handler 常用     |

S-mode 有對應的 `sstatus` / `stvec` / `sepc` / ... 全套。名稱只差一個字母。

## Trap 發生時硬體做什麼

**Trap** 是一個廣義詞，涵蓋：

- **Exception**（同步）：illegal instruction、page fault、misaligned access、ecall
- **Interrupt**（非同步）：timer、external、software

當 trap 發生（假設從 U-mode 進到 M-mode）：

```
1. mepc    ← 當前 PC                         # 記住從哪回去
2. mcause  ← 事件原因編號                    # 告訴 handler 發生什麼事
3. mtval   ← 相關值 (ex: faulting address)   # 附加資訊
4. mstatus.MPP ← 11 (之前是 U-mode 的話是 00) # 記住從哪個 mode 來
5. mstatus.MPIE ← mstatus.MIE                # 備份中斷啟用狀態
6. mstatus.MIE  ← 0                          # 關中斷
7. 當前 mode   ← M-mode
8. PC ← mtvec 指的地址                       # 跳到 trap handler
```

這是**硬體 atomic 做完**，handler 第一條指令開始執行時已經在 M-mode。

### 從 trap 回來

handler 做完要回去：

```asm
csrw   mepc, t0          # 把要回去的地址寫進 mepc (通常已經對的，不用改)
mret                     # 硬體還原：mode = mstatus.MPP; MIE = MPIE; PC = mepc
```

`mret`（machine return）是一條指令，把硬體 state 還原到 trap 前。S-mode 有對應的 `sret`。U-mode 沒有「要 return 去哪」的權力，所以沒 `uret`（其實 spec 過去有過但廢了）。

## 一份最小的 M-mode trap handler

這是你在 bootloader / tiny kernel 會看到的樣板：

```asm
    .align 2
trap_vector:
    # Step 1: 存 caller-saved 暫存器到 stack (或預留區)
    csrrw   sp, mscratch, sp       # 神操作：交換 sp 跟 mscratch (mscratch 平時藏一個 trap stack)
    addi    sp, sp, -128
    sd      t0,   0(sp)
    sd      t1,   8(sp)
    # ... 所有 caller-saved

    # Step 2: 抓資訊
    csrr    a0, mcause             # a0 = cause
    csrr    a1, mepc               # a1 = 發生 PC
    csrr    a2, mtval              # a2 = tval
    call    c_trap_handler         # 跳到 C 語言 handler

    # Step 3: 還原
    ld      t0,   0(sp)
    ld      t1,   8(sp)
    # ...
    addi    sp, sp, 128
    csrrw   sp, mscratch, sp       # 交換回來
    mret
```

`mscratch + csrrw` 是經典 idiom：**handler 進來時，原來的 sp 可能指到 user stack（不能用！）**，所以先把它換掉、搶一個合法的 trap stack 用。

## `mtvec`：trap 跳哪裡

`mtvec` 寫入規則：

```
mtvec[1:0]   Mode:
  00  Direct    — 所有 trap 都跳到 BASE
  01  Vectored  — 中斷跳到 BASE + 4 × cause, 例外跳到 BASE
mtvec[XLEN-1:2]  BASE (必須 4-byte aligned)
```

Direct mode：寫一個 handler，內部用 switch 分流。簡單。
Vectored mode：一堆 4-byte 的 stub（通常 `j real_handler_N`），硬體直接依 cause 跳。對 interrupt latency 有用。

## `mcause` 的編碼

最高 bit 分 exception / interrupt：

```
 bit XLEN-1     = 1 → interrupt
                = 0 → exception
 bit [XLEN-2:0] = 編號
```

常見值（exception）：

| 編號 | 意義                          |
|-----|-------------------------------|
| 0   | Instruction address misaligned |
| 1   | Instruction access fault      |
| 2   | Illegal instruction           |
| 3   | Breakpoint (ebreak)           |
| 4   | Load address misaligned       |
| 5   | Load access fault             |
| 6   | Store address misaligned      |
| 7   | Store access fault            |
| 8   | Environment call from U-mode (ecall in U) |
| 9   | Environment call from S-mode  |
| 11  | Environment call from M-mode  |
| 12  | Instruction page fault        |
| 13  | Load page fault               |
| 15  | Store page fault              |

常見值（interrupt，最高位 = 1）：

| 編號 | 意義                   |
|-----|------------------------|
| 1   | Supervisor software int|
| 3   | Machine software int   |
| 5   | Supervisor timer       |
| 7   | Machine timer          |
| 9   | Supervisor external    |
| 11  | Machine external       |

**看到 `mcause = 8` 就知道「U-mode 呼叫 ecall」**，典型的 syscall entry。handler 會用 `a7` 當 syscall number，其他 `a*` 當參數。

## Delegation：M 把事情丟給 S

Linux 不想每個 syscall 都從 M-mode 接手後再 forward 到 S。硬體支援**直接把某些 trap 派給 S-mode**：

```
medeleg    Machine exception delegation     (哪些 exception 直接給 S 處理)
mideleg    Machine interrupt delegation     (哪些 interrupt 直接給 S)
```

例如 `medeleg bit 8 = 1` → 「U-mode 的 ecall 直接讓 S-mode 接」，Linux kernel 就不用經過 firmware。

**這是 RISC-V 效能的關鍵**：正常 Linux syscall 只進 S 一次，不像某些架構要跨兩層。

delegation 只能**往下**給（M 給 S、S 給 U）。不能反向。

## PMP：memory protection（沒 MMU 前的選擇）

簡單系統沒 MMU 也能做 memory 隔離 — **Physical Memory Protection (PMP)**。一組 CSR 描述 `[start, end)` 範圍與權限：

```
pmpaddr0..15     16 個範圍的地址編碼
pmpcfg0..3       每個範圍的權限 (RWX、鎖定、匹配模式)
```

M-mode 設定好後，lower-privilege mode（S/U）存取越界就 trap。常見於 microkernel / secure boot。

pure Linux 系統通常用 MMU 虛擬記憶體（satp CSR + page table），PMP 是輔助。

## 虛擬記憶體：satp + Sv39 / Sv48

S-mode 有一顆 `satp`（Supervisor Address Translation and Protection）：

```
satp.MODE   0 = 關閉 (bare), 8 = Sv39, 9 = Sv48, 10 = Sv57
satp.ASID   Address Space ID (類似 PCID)
satp.PPN    根 page table 的實體頁號
```

**Sv39** 最常用：39-bit virtual address、3 層 page table、4 KiB / 2 MiB / 1 GiB 三種頁大小。Linux RISC-V 預設 Sv39。

Sv48 是 48-bit，4 層；Sv57 是 57-bit，5 層，但硬體與 kernel 支援才剛起來。

## ecall：syscall 的入口

```asm
# user code
li  a7, 64          # syscall number (write)
li  a0, 1           # fd = 1
la  a1, msg         # buf
li  a2, 13          # len
ecall
# 結果在 a0
```

U-mode 跑 `ecall` → trap 到 S-mode（如果 delegated）→ Linux kernel 看 `a7` 知道是 write → 做事 → `sret` 回 U。

這就是 Linux 的 syscall。`a7` 是 convention，不是硬體強制。BSD 用類似但不同的 convention。

## 開機序列

上電時：

```
1. PC = 某個 reset vector (RV64 通常 0x1000 或 0x80000000，硬體決定)
2. Mode = M
3. 執行 ROM 裡的 firmware...
4. firmware (例: OpenSBI) 初始化 hardware、設 mtvec、設 medeleg、跳 S-mode
5. S-mode code (例: Linux) 接手、設 satp、啟用 paging
6. 啟動 init process 進 U-mode
```

OpenSBI 是 RISC-V 世界的「firmware 最佳實踐」，地位類似 ARM 的 TF-A。它提供 **SBI (Supervisor Binary Interface)**：S-mode 呼叫 M-mode 服務的標準介面（就像 syscall 但反向）。Linux 開機時會透過 SBI 做 timer 設定、console 輸出等。

## Privileged spec 的東西很多，不要一次吃

完整的 Privileged ISA 包括：

- 三個 mode 的所有 CSR（Unprivileged Spec 不管這些）
- 中斷 controller（CLINT、PLIC、AIA）
- 記憶體模型的 privilege interaction
- Cache-block operations (Zicbom)
- Debug ISA（另一本 spec）

**不要第一次讀就試圖吃完**。先能看懂 trap handler 的前 10 行、能寫一個 `ecall` + 接住它的 baremetal demo、能解釋 delegation 在做什麼 — 這樣就夠面試跟 toolchain 工作了。

## 常見誤會

1. **「M-mode 像 kernel mode」**：不完全。M 更像 firmware 層。真正的 kernel 跑在 S。
2. **「RISC-V 沒有 page table」**：有。S-mode 的 `satp` 指向它。只是 M-mode 可以選擇不啟用。
3. **「CSR 是一般暫存器」**：不是。需要 `csrr*` 指令、跟 privilege mode 綁定、不在 `x0..x31` 裡。
4. **「trap handler 就是 C function」**：最外層必須是 asm stub（要存 caller-saved / 處理 sp）。進 C 只是第二層。
5. **「ecall 在 M-mode 叫 syscall」**：M-mode 的 ecall 通常是 S→M 的 SBI 呼叫，不是 user 的 syscall。

## 動手練習

1. 在 spike 上跑 baremetal：讓 firmware 在 M-mode 設 `mtvec`，然後 `ecall`，handler 印一個訊息後 `mret` 回來。
2. 用 `csrr t0, cycle` 讀 cycle counter，觀察 spike 有沒有把它設成 count-from-zero。
3. 讀 OpenSBI 的 trap_entry.S（<https://github.com/riscv-software-src/opensbi/blob/master/firmware/fw_base.S>），認出「存 caller-saved」那段，對照本章的樣板。
4. 故意在 U-mode 存取 M-mode 的 CSR（例：`csrr t0, mstatus`），看 spike 回報哪種 exception。
5. 讀一次 Privileged Spec 的 "Machine-Level ISA" 章節（大約 30 頁），重點看 mstatus 那張欄位圖。不求全懂，求能對照本章。

## 自我檢核

- [ ] 我能說出 M / S / U 三個 mode 的典型用途
- [ ] 我能畫出 trap 發生時硬體做的 8 步動作
- [ ] 我能讀懂一份最小 trap handler 的 asm 並解釋 mscratch idiom
- [ ] 我能解釋 delegation 的作用以及為什麼對效能重要
- [ ] 我能認出 `mcause` 的編號 8 跟 11 分別代表什麼

Part 1、Part 2 至此告一段落。下一章進 Part 3 的第一站 — Zicsr / Zifencei / Zicond 這三個「小而關鍵」的擴充，尤其 Zicond 是 2023 年才 ratify、補上 RISC-V 長久以來沒有 conditional move 的爭議。

→ [Ch 6 Zicsr / Zifencei / Zicond：小而關鍵的擴充](./06-small-extensions.md)
