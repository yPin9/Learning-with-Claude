# Ch 37 — arch/riscv 程式碼導覽：Kernel 資料夾結構與關鍵路徑

> 目標：能在 Linux source tree 裡快速找到 RISC-V 相關的 code；能沿著 4 條關鍵路徑（啟動、trap、context switch、page fault）找到對應的函式；知道怎麼設定 cscope/ctags 來跳轉。

---

## 37.1 arch/riscv/ 子目錄結構

```
arch/riscv/
├── boot/                    # 壓縮 kernel 的啟動 stub
│   └── compressed/          # 解壓縮用的 loader
├── configs/                 # 架構預設 Kconfig（qemu-virt_defconfig 等）
├── include/
│   ├── asm/                 # 架構相關標頭檔（*.h）
│   │   ├── csr.h            # CSR 位址定義、csrr/csrw 巨集
│   │   ├── page.h           # PAGE_SIZE、PTE flag 定義
│   │   ├── pgtable.h        # 頁表操作巨集（pte_mkwrite、pte_val 等）
│   │   ├── processor.h      # struct thread_struct
│   │   ├── ptrace.h         # pt_regs 定義（trap frame）
│   │   ├── switch_to.h      # switch_to() macro
│   │   └── uaccess.h        # copy_from_user / copy_to_user
│   └── uapi/asm/            # 使用者空間可見的標頭檔
├── kernel/                  # 核心 C code
│   ├── head.S               # 啟動入口（第一條指令）
│   ├── entry.S              # trap entry/exit assembly
│   ├── trap.c               # do_trap_*() 函式
│   ├── process.c            # copy_thread(), do_exit()
│   ├── ptrace.c             # ptrace 支援
│   ├── signal.c             # signal 處理
│   ├── smp.c                # 多核啟動（secondary hart）
│   ├── smpboot.c            # SMP boot helpers
│   ├── syscall_table.c      # 系統呼叫表
│   ├── time.c               # timer 初始化
│   └── irq.c                # interrupt 初始化
├── lib/                     # 架構最佳化的函式庫
│   ├── memcpy.S             # 最佳化 memcpy（用 ld/sd 8-byte 對齊）
│   ├── memset.S             # 最佳化 memset
│   └── strlen.S             # 最佳化 strlen（可選）
├── mm/                      # 記憶體管理
│   ├── init.c               # setup_vm()，頁表初始化
│   ├── fault.c              # do_page_fault()
│   ├── mmap.c               # mmap 架構相關部分
│   └── context.c            # ASID 管理
└── net/                     # 網路相關（幾乎空的）
```

---

## 37.2 關鍵路徑 1：啟動路徑

```
head.S：_start
  │
  ├─ 硬體初始化（清零 BSS，設定 tp 指向 current task）
  ├─ setup_vm()             → mm/init.c
  │    建立早期頁表（fixmap + direct map 的 stub）
  ├─ relocate               → head.S（local function）
  │    csrw satp，切換到虛擬地址
  ├─ setup_trap_vector()    → kernel/entry.S
  │    csrw stvec, handle_exception
  ├─ soc_early_init()       → 廠商特定初始化
  └─ start_kernel()         → init/main.c（架構無關的主流程）
       ├─ setup_arch()      → arch/riscv/kernel/setup.c
       │    解析 FDT，初始化 memory map
       ├─ mm_core_init()    → 初始化正式的頁表管理
       └─ ...（scheduler, networking, driver probe...）
```

**重點檔案**：
- `arch/riscv/kernel/head.S`：第一條指令
- `arch/riscv/mm/init.c`：`setup_vm()` 函式，建立啟動頁表
- `arch/riscv/kernel/setup.c`：`setup_arch()`

---

## 37.3 關鍵路徑 2：Trap 路徑

```
外部事件或指令執行
  │
  ▼ （硬體自動）
  sepc/scause/stval 設定
  PC → stvec = handle_exception（kernel/entry.S）
  │
  ▼ handle_exception（entry.S）
  csrrw sp, sscratch, sp   # 換 kernel stack
  addi  sp, sp, -PT_SIZE   # 配置 trap frame
  保存所有 GPR 和 CSR 到 stack
  │
  ├─ 讀 scause
  ├─ 是 interrupt? → handle_irq()  → kernel/irq.c → PLIC driver
  ├─ 是 ecall?    → handle_syscall() → syscall_table.c
  └─ 是 exception? → do_trap()    → kernel/trap.c
       ├─ page fault（cause 12/13/15）→ mm/fault.c：do_page_fault()
       │    ├─ find_vma(fault_va)
       │    ├─ demand paging / COW / swap
       │    └─ handle_mm_fault()
       ├─ illegal inst → do_trap_insn_illegal()
       └─ ...
  │
  ▼ ret_from_exception（entry.S）
  恢復所有 GPR 和 CSR
  sret → 回到 U-mode（或 S-mode）
```

**重點檔案**：
- `arch/riscv/kernel/entry.S`：`handle_exception`（trap 進出口）
- `arch/riscv/kernel/trap.c`：`do_trap_*()` 函式
- `arch/riscv/mm/fault.c`：`do_page_fault()`

---

## 37.4 關鍵路徑 3：Context Switch 路徑

```
時間片到期 → timer interrupt → do_timer()
  │
  ▼
schedule()             → kernel/sched/core.c（架構無關）
  │
  ▼
context_switch(prev, next)  → kernel/sched/core.c
  │
  ├─ switch_mm(prev->mm, next->mm)
  │    設定 satp（換頁表），更新 ASID
  │    → arch/riscv/mm/context.c
  │
  └─ switch_to(prev, next, last)
       │   → arch/riscv/include/asm/switch_to.h
       │
       ├─ （若有 FPU）__switch_to_fpu()
       │    檢查 FS bit，決定是否保存/恢復 FP 暫存器
       │
       └─ __switch_to(prev, next)
            → arch/riscv/kernel/entry.S（或 switch.S）
            保存 prev 的 callee-saved 暫存器
            載入 next 的 callee-saved 暫存器
            ret → 跳到 next 的 ra
```

**重點檔案**：
- `arch/riscv/include/asm/switch_to.h`：`switch_to()` macro
- `arch/riscv/kernel/entry.S` 或 `switch.S`：`__switch_to()` assembly
- `arch/riscv/mm/context.c`：`switch_mm()`

---

## 37.5 關鍵路徑 4：Page Fault 路徑

```
load/store 指令存取不存在的 VA
  │
  ▼ （硬體）
  scause = 13（load pf）或 15（store pf）
  stval = fault VA
  ─→ handle_exception（entry.S）
  ─→ do_trap_load_page_fault() 或 do_trap_store_page_fault()
       → arch/riscv/kernel/trap.c
  │
  ▼
do_page_fault(regs)    → arch/riscv/mm/fault.c
  │
  ├─ 讀 stval（fault VA）
  ├─ fault VA 在 kernel space？→ vmalloc fault 或 kernel bug
  ├─ find_vma(current->mm, fault_va)
  ├─ 找不到 VMA？→ bad_area() → send_sigsegv() → 使用者程式收 SIGSEGV
  ├─ VMA 存在但權限不符？→ bad_area_access_error()
  └─ handle_mm_fault(vma, fault_va, fault_flags, regs)
       → mm/memory.c（架構無關）
       ├─ demand paging：alloc_page() 分配物理頁，建立 PTE
       ├─ COW：copy_cow_page()，建立新 PTE
       └─ swap-in：swapin_readahead()，從 swap device 讀回
```

**重點檔案**：
- `arch/riscv/mm/fault.c`：`do_page_fault()`（架構相關部分）
- `mm/memory.c`：`handle_mm_fault()`（架構無關，通用頁故障處理）

---

## 37.6 重要 Kconfig 選項

```
CONFIG_ARCH_RV64I      # 選擇 RV64I 架構（vs RV32I）
CONFIG_MMU             # 啟用 MMU 支援（Sv39/Sv48）
CONFIG_SMP             # 多核支援
CONFIG_RISCV_ISA_A     # Atomic extension
CONFIG_RISCV_ISA_C     # Compressed extension（16-bit 指令）
CONFIG_FPU             # 浮點支援
CONFIG_RISCV_SBI       # SBI 支援
CONFIG_RISCV_M_MODE    # 啟用 M-mode（通常 OpenSBI 已處理，kernel 不用）
CONFIG_64BIT           # 64-bit kernel（由 ARCH_RV64I 自動設定）
CONFIG_PAGE_OFFSET     # kernel 虛擬地址空間起始（Sv39: 0xffffffe000000000）
CONFIG_PGTABLE_LEVELS  # 頁表層數（Sv39=3, Sv48=4）
```

---

## 37.7 建立 cscope / ctags 索引

```bash
# 在 kernel source tree 根目錄
# cscope（強大，支援跨檔案 symbol 查詢）
make cscope ARCH=riscv
cscope -dq          # 啟動 cscope UI

# 或在 vim 裡
vim -c "cs add cscope.out"
# :cs find s <symbol>   查找符號定義
# :cs find c <symbol>   查找呼叫者
# :cs find f <file>     查找文件

# ctags
make tags ARCH=riscv
# 在 vim 裡：Ctrl-] 跳到定義，Ctrl-T 跳回
```

**只索引 arch/riscv 相關的文件**（更快）：

```bash
find arch/riscv include/asm-generic -name "*.c" -o -name "*.h" \
    > cscope.files
cscope -bq -i cscope.files
```

---

## 37.8 建議閱讀順序

給想深入 kernel 的讀者：

```
第一輪（2 周）：
  1. arch/riscv/kernel/head.S       啟動入口，了解 setup_vm / relocate
  2. arch/riscv/include/asm/csr.h   所有 CSR 的名稱和 bit 定義
  3. arch/riscv/include/asm/page.h  PTE flag 定義
  4. arch/riscv/kernel/entry.S      trap entry/exit 的 assembly

第二輪（1 個月）：
  5. arch/riscv/mm/fault.c          do_page_fault()
  6. arch/riscv/mm/init.c           setup_vm()，啟動頁表
  7. arch/riscv/kernel/trap.c       do_trap_*()
  8. arch/riscv/mm/context.c        ASID 管理

第三輪（持續）：
  9. mm/memory.c                    handle_mm_fault()（架構無關）
  10. kernel/sched/core.c           schedule(), context_switch()
  11. arch/riscv/kernel/process.c   copy_thread(), do_exit()
```

---

## 自我檢核

- [ ] 能說出 `arch/riscv/kernel/` 和 `arch/riscv/mm/` 各放哪類 code
- [ ] 知道 trap 入口在哪個檔案（entry.S）
- [ ] 能說出 `do_page_fault()` 在哪個檔案（mm/fault.c）
- [ ] 能用 `make cscope ARCH=riscv` 建立 cscope 索引
- [ ] 能從 `arch/riscv/include/asm/csr.h` 找到 `satp` 的 CSR 位址定義

這是整個課程的終點。從這裡開始，你有足夠的基礎去讀 kernel source、看 RISC-V spec、追 bug。

→ [練習 C — RV64 Assembly 實戰](practice-c-rv64-assembly.md)
