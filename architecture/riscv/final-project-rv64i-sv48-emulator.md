# Final Project B — RV64I Emulator + Sv48 Page Walk

> 這個 final project 把整個 Part 8–12 的知識點整合成一個可執行的系統：一個能跑 RV64I 指令、支援 Sv48 虛擬記憶體、能正確處理 exception 的模擬器。不是玩具——你要讓它能跑一個真實的 minimal S-mode kernel。

---

## 總體目標

延伸或重建一個 RV64I emulator，支援：
- 完整的 RV64I 指令集（含 W 後綴、LD/SD/LWU）
- M-mode / S-mode / U-mode 特權層級
- 核心 CSR（mstatus、sstatus、satp、scause、sepc、stval、mtvec、stvec）
- Sv48 四層頁表 walk
- Exception delegation（medeleg/mideleg）
- page fault 的完整 trap 流程

---

## 前置條件

- 已完成 Ch 21–37 的閱讀
- 有 C 語言實作的基礎（會用 `switch`、`struct`、function pointer）
- 已跑過練習 C 和練習 D（熟悉 RV64 assembly 和頁表操作）

---

## Milestone 1：RV64I CPU Core

**目標**：把 RV32I emulator 的 register file 從 32-bit 擴展到 64-bit，並加入 W 後綴和 64-bit load/store 指令。

### 驗收標準

- [ ] 32 個通用暫存器各是 `uint64_t`（或 `int64_t`）
- [ ] PC 是 `uint64_t`
- [ ] `add`/`sub`/`sll`/`srl`/`sra`/`addi`/`slli`/`srli`/`srai`：64-bit 語意
- [ ] `addw`/`subw`/`sllw`/`srlw`/`sraw`/`addiw`/`slliw`/`srliw`/`sraiw`：32-bit 結果 sign-extend 到 64-bit
- [ ] `ld rd, offset(rs1)`：load 64-bit，無延伸
- [ ] `sd rs2, offset(rs1)`：store 64-bit
- [ ] `lwu rd, offset(rs1)`：load 32-bit，zero-extend 到 64-bit
- [ ] 能跑以下測試程式並輸出正確值

### 關鍵資料結構

```c
// cpu.h
#include <stdint.h>

typedef uint64_t reg_t;

typedef struct {
    reg_t  x[32];     // x0–x31（x0 永遠是 0，寫入無效）
    reg_t  pc;

    // CSR（Milestone 2 加入）
    reg_t  mstatus;
    reg_t  sstatus;
    reg_t  satp;
    reg_t  scause;
    reg_t  sepc;
    reg_t  stval;
    reg_t  mtvec;
    reg_t  stvec;
    reg_t  medeleg;
    reg_t  mideleg;
    reg_t  mepc;
    reg_t  mcause;
    reg_t  mtval;

    int    mode;      // 0=U, 1=S, 3=M
} CPU;

// 記憶體（簡化：用一塊 flat memory）
typedef struct {
    uint8_t  *data;
    uint64_t  base;   // 物理地址起始（通常 0x80000000）
    uint64_t  size;
} Memory;
```

### 指令解碼框架

```c
// decode.c
#include "cpu.h"

typedef enum {
    OP_ADD, OP_SUB, OP_ADDW, OP_SUBW,
    OP_ADDI, OP_ADDIW,
    OP_LD, OP_LW, OP_LWU, OP_LH, OP_LB, OP_LHU, OP_LBU,
    OP_SD, OP_SW, OP_SH, OP_SB,
    OP_SLL, OP_SRL, OP_SRA, OP_SLLW, OP_SRLW, OP_SRAW,
    OP_SLLI, OP_SRLI, OP_SRAI, OP_SLLIW, OP_SRLIW, OP_SRAIW,
    OP_BEQ, OP_BNE, OP_BLT, OP_BGE, OP_BLTU, OP_BGEU,
    OP_JAL, OP_JALR,
    OP_LUI, OP_AUIPC,
    OP_ECALL, OP_EBREAK,
    OP_CSRRS, OP_CSRRC, OP_CSRRW, OP_CSRRSI, OP_CSRRCI, OP_CSRRWI,
    OP_MRET, OP_SRET,
    OP_SFENCE_VMA,
    // ...
    OP_ILLEGAL,
} OpCode;

typedef struct {
    OpCode  op;
    int     rd, rs1, rs2;
    int64_t imm;         // sign-extended immediate
    int     shamt;
} DecodedInst;

DecodedInst decode(uint32_t raw_inst);
```

### W 後綴指令實作細節

```c
// execute.c（核心片段）
case OP_ADDW: {
    int32_t result32 = (int32_t)(int32_t)cpu->x[rs1] + (int32_t)cpu->x[rs2];
    cpu->x[rd] = (int64_t)result32;   // sign-extend to 64-bit
    break;
}
case OP_ADDIW: {
    int32_t result32 = (int32_t)cpu->x[rs1] + (int32_t)inst.imm;
    cpu->x[rd] = (int64_t)result32;
    break;
}
case OP_SLLW: {
    uint32_t shamt32 = cpu->x[rs2] & 0x1F;   // 注意：W 指令 shamt 只取 5-bit
    int32_t result32 = (int32_t)((uint32_t)cpu->x[rs1] << shamt32);
    cpu->x[rd] = (int64_t)result32;
    break;
}
case OP_SRAW: {
    uint32_t shamt32 = cpu->x[rs2] & 0x1F;
    int32_t result32 = (int32_t)cpu->x[rs1] >> shamt32;  // arithmetic shift
    cpu->x[rd] = (int64_t)result32;
    break;
}
```

### Milestone 1 測試程式

```asm
# test_rv64i.S（組譯後載入 emulator 執行）
    .globl _start
_start:
    # 測試 ADDW：0x7FFFFFFF + 1 要得到 -2147483648
    li   t0, 0x7FFFFFFF
    addiw t0, t0, 1          # 期望：t0 = 0xFFFFFFFF80000000（-2147483648）

    # 測試 LWU vs LW
    la   a0, test_val
    lw   t1, 0(a0)           # t1 = sign-extended（0xFFFFFFFFDEADBEEF）
    lwu  t2, 0(a0)           # t2 = zero-extended（0x00000000DEADBEEF）

    # 測試 LD
    la   a0, test_val64
    ld   t3, 0(a0)           # t3 = 0xDEADBEEFCAFEBABE

    # 輸出結果（通過 ECALL 或者 MMIO）
    li   a7, 64    # 假設有自訂的 halt syscall
    ecall

    .section .data
test_val:
    .word 0xDEADBEEF
test_val64:
    .dword 0xDEADBEEFCAFEBABE
```

---

## Milestone 2：CSR 與特權模式

**目標**：讓 emulator 支援 M-mode/S-mode/U-mode，能讀寫核心 CSR，能處理 ecall/mret/sret。

### 驗收標準

- [ ] `mode` 欄位正確切換（ecall 進入 M-mode 或 S-mode，mret/sret 返回）
- [ ] `csrr`/`csrw`/`csrrs`/`csrrc` 正確讀寫 CSR
- [ ] `ecall` 從 U-mode 設定 mcause=8（或委派到 S-mode 設 scause=8）
- [ ] `mret` 從 M-mode 返回，使用 mepc，設定 mode = mstatus.MPP
- [ ] `sret` 從 S-mode 返回，使用 sepc，設定 mode = sstatus.SPP
- [ ] medeleg 設定影響 exception routing

### CSR 讀寫實作

```c
// csr.c
#define CSR_MSTATUS  0x300
#define CSR_SSTATUS  0x100
#define CSR_MEDELEG  0x302
#define CSR_MIDELEG  0x303
#define CSR_MTVEC    0x305
#define CSR_MEPC     0x341
#define CSR_MCAUSE   0x342
#define CSR_MTVAL    0x343
#define CSR_SATP     0x180
#define CSR_STVEC    0x105
#define CSR_SEPC     0x141
#define CSR_SCAUSE   0x142
#define CSR_STVAL    0x143
#define CSR_CYCLE    0xC00
#define CSR_TIME     0xC01
#define CSR_INSTRET  0xC02

uint64_t csr_read(CPU *cpu, uint32_t csr_addr) {
    switch (csr_addr) {
        case CSR_MSTATUS:  return cpu->mstatus;
        case CSR_SSTATUS:  return cpu->mstatus & SSTATUS_MASK;  // sstatus 是 mstatus 的 subset
        case CSR_SATP:     return cpu->satp;
        case CSR_SCAUSE:   return cpu->scause;
        case CSR_SEPC:     return cpu->sepc;
        case CSR_STVAL:    return cpu->stval;
        case CSR_STVEC:    return cpu->stvec;
        case CSR_MEDELEG:  return cpu->medeleg;
        case CSR_CYCLE:    return cpu->cycle_count;
        // ...
        default:           return 0;
    }
}

void csr_write(CPU *cpu, uint32_t csr_addr, uint64_t val) {
    switch (csr_addr) {
        case CSR_SATP:
            cpu->satp = val;
            // 注意：寫 satp 後可能要讓 TLB cache 失效（若有實作的話）
            break;
        case CSR_SSTATUS:
            // 只允許 S-mode 可寫的欄位
            cpu->mstatus = (cpu->mstatus & ~SSTATUS_MASK) | (val & SSTATUS_MASK);
            break;
        // ...
    }
}
```

### Exception / Trap Dispatch

```c
// trap.c
void raise_exception(CPU *cpu, uint64_t cause, uint64_t tval) {
    // 決定要進入 M-mode 還是 S-mode
    int to_smode = 0;
    if (cause < 64) {  // exception（非 interrupt）
        uint64_t bit = 1ULL << cause;
        if ((cpu->medeleg & bit) && cpu->mode <= 1) {
            to_smode = 1;
        }
    }

    if (to_smode) {
        // 委派給 S-mode
        cpu->sepc   = cpu->pc;
        cpu->scause = cause;
        cpu->stval  = tval;
        // 更新 sstatus：SPIE = SIE, SIE = 0, SPP = 當前 mode
        uint64_t sie = (cpu->mstatus >> 1) & 1;
        cpu->mstatus &= ~((1ULL << 1) | (1ULL << 5) | (1ULL << 8));  // 清 SIE, SPIE, SPP
        cpu->mstatus |= (sie << 5);                                    // SPIE = old SIE
        cpu->mstatus |= ((uint64_t)(cpu->mode == 1) << 8);             // SPP = old mode
        cpu->mode = 1;  // 進入 S-mode
        cpu->pc = cpu->stvec & ~3ULL;  // 跳到 stvec（direct mode）
    } else {
        // M-mode 處理
        cpu->mepc   = cpu->pc;
        cpu->mcause = cause;
        cpu->mtval  = tval;
        // 更新 mstatus
        cpu->mode = 3;  // 進入 M-mode
        cpu->pc = cpu->mtvec & ~3ULL;
    }
}
```

---

## Milestone 3：Sv48 Page Table Walker

**目標**：實作 `translate(cpu, va, access_type)` 函式，執行完整的 Sv48 四層頁表 walk。

### 驗收標準

- [ ] 正確解析 satp 的 MODE（Bare/Sv39/Sv48）
- [ ] 四層頁表 walk：VPN[3:0] 分別索引 L3/L2/L1/L0
- [ ] 每層檢查 PTE.V bit，V=0 就 raise page fault
- [ ] 正確識別 leaf PTE（R/W/X 至少一個非零）
- [ ] leaf PTE 的 PA = PTE.PPN × 4096 + VA.offset
- [ ] 支援大頁（non-leaf 層的 leaf PTE：Megapage/Gigapage/Terapage）
- [ ] 權限檢查：Load 需要 R=1，Store 需要 W=1，Fetch 需要 X=1

### translate() 實作骨架

```c
// mmu.c
#define PAGE_FAULT_INST   12
#define PAGE_FAULT_LOAD   13
#define PAGE_FAULT_STORE  15

typedef enum { ACCESS_READ, ACCESS_WRITE, ACCESS_FETCH } AccessType;

// 返回 0 表示成功（pa 已填入），-1 表示 page fault（已呼叫 raise_exception）
int translate(CPU *cpu, Memory *mem, uint64_t va, AccessType atype, uint64_t *pa_out) {
    uint64_t satp = cpu->satp;
    int mode = (satp >> 60) & 0xF;

    // Bare mode：VA = PA
    if (mode == 0) {
        *pa_out = va;
        return 0;
    }

    // 確認是 Sv48（mode == 10）
    if (mode != 10) {
        // 也可以支援 Sv39（mode==9），邏輯類似，只是少一層
        fprintf(stderr, "Unsupported satp mode: %d\n", mode);
        return -1;
    }

    // Sv48：48-bit canonical VA 檢查
    // bit 47 要等於 bits 63:48（sign-extension）
    int64_t signed_va = (int64_t)va;
    if (signed_va != ((signed_va << 16) >> 16)) {
        // 非 canonical VA → page fault
        goto page_fault;
    }

    // 分解 VA
    uint64_t vpn[4];
    vpn[3] = (va >> 39) & 0x1FF;
    vpn[2] = (va >> 30) & 0x1FF;
    vpn[1] = (va >> 21) & 0x1FF;
    vpn[0] = (va >> 12) & 0x1FF;
    uint64_t offset = va & 0xFFF;

    // root page table 的物理地址
    uint64_t pt_pa = (satp & ((1ULL << 44) - 1)) << 12;

    // 四層 walk
    for (int level = 3; level >= 0; level--) {
        uint64_t pte_pa = pt_pa + vpn[level] * 8;

        // 從模擬記憶體讀 PTE
        uint64_t pte;
        if (mem_read64(mem, pte_pa, &pte) != 0) {
            // 物理地址越界 → access fault
            goto page_fault;
        }

        // 檢查 V bit
        if (!(pte & PTE_V)) {
            goto page_fault;
        }

        // 判斷是否是 leaf PTE
        int is_leaf = (pte & (PTE_R | PTE_W | PTE_X)) != 0;

        if (!is_leaf) {
            // Non-leaf：取出下一層頁表的物理地址
            pt_pa = ((pte >> 10) & ((1ULL << 44) - 1)) << 12;
            continue;
        }

        // Leaf PTE：進行權限檢查
        if (atype == ACCESS_READ  && !(pte & PTE_R)) goto page_fault;
        if (atype == ACCESS_WRITE && !(pte & PTE_W)) goto page_fault;
        if (atype == ACCESS_FETCH && !(pte & PTE_X)) goto page_fault;

        // 計算 PA
        uint64_t ppn = (pte >> 10) & ((1ULL << 44) - 1);

        // 大頁對齊檢查：如果是大頁，低位的 PPN 必須是 0
        // （level=2 是 Megapage，level=1 是 Gigapage，level=3 是 Terapage in Sv48 不存在）
        // 但 spec 要求大頁的 PPN 低位對齊，這裡簡化略過

        // 計算物理地址
        uint64_t pa;
        if (level == 0) {
            pa = (ppn << 12) | offset;
        } else if (level == 1) {
            // Megapage（2 MiB）：PPN 的低 level*9 bits 來自 VA 的 VPN
            pa = (ppn << 12) | ((va >> 12) & ((1ULL << (level * 9)) - 1)) << 12 | offset;
            // 簡化版（假設大頁 PPN 已對齊）：
            pa = (ppn << 12) | (va & ((1ULL << 21) - 1));
        } else if (level == 2) {
            // Gigapage（1 GiB）
            pa = (ppn << 12) | (va & ((1ULL << 30) - 1));
        } else {
            // Terapage（512 GiB，level=3，Sv48 only）
            pa = (ppn << 12) | (va & ((1ULL << 39) - 1));
        }

        *pa_out = pa;
        return 0;
    }

page_fault:
    {
        uint64_t cause;
        switch (atype) {
            case ACCESS_FETCH: cause = PAGE_FAULT_INST;  break;
            case ACCESS_READ:  cause = PAGE_FAULT_LOAD;  break;
            case ACCESS_WRITE: cause = PAGE_FAULT_STORE; break;
        }
        raise_exception(cpu, cause, va);
        return -1;
    }
}
```

---

## Milestone 4：Exception 處理

**目標**：所有 exception 都能正確路由並觸發 trap flow；page fault 能設定 scause/sepc/stval 並跳到 stvec。

### 驗收標準

- [ ] ecall from U-mode：raise cause=8，根據 medeleg 決定進入 S-mode 或 M-mode
- [ ] ecall from S-mode：raise cause=9，進入 M-mode（M-mode ecall 不委派）
- [ ] Load page fault：raise cause=13，stval=fault VA，PC=stvec
- [ ] mret：PC=mepc，mode=mstatus.MPP，mstatus.MIE=mstatus.MPIE
- [ ] sret：PC=sepc，mode=sstatus.SPP，sstatus.SIE=sstatus.SPIE

### 測試 Sequence

```
// 測試 page fault trap flow：
// 1. 設定 stvec = test_handler
// 2. 設定 satp = Sv48 mode（但不建立任何頁表 entry）
// 3. 存取任意 VA → 應觸發 load page fault（cause=13）
// 4. 確認 sepc = 存取指令的 PC，stval = 存取的 VA
// 5. 確認 PC 跳到 stvec（test_handler）
```

---

## Milestone 5：整合測試

**目標**：讓 emulator 能跑一個 minimal S-mode kernel binary，這個 kernel 會設定 Sv48 頁表、切換 mode、讀寫虛擬地址。

### 驗收標準

- [ ] 能正確執行 Ch 29 的 Sv48 baremetal C code（sv48_baremetal.c）
- [ ] 合法 VA 讀寫成功（translate 返回 PA，實際讀寫 Memory 的對應位置）
- [ ] 非法 VA 觸發 page fault，scause=13，stval=非法 VA
- [ ] page fault handler 能設定 sepc += 4，sret 後繼續執行

### 整合測試程式

把 Ch 29 的 `sv48_baremetal.c` 編譯成 ELF，載入到 emulator 的記憶體，執行到輸出 `PASS`。

```c
// emulator_main.c
#include "cpu.h"
#include "mmu.h"
#include "decode.h"
#include "execute.h"

int main(int argc, char *argv[]) {
    if (argc < 2) {
        fprintf(stderr, "Usage: %s <elf_file>\n", argv[0]);
        return 1;
    }

    // 初始化
    CPU cpu = {0};
    Memory mem = {0};
    mem.size = 32 * 1024 * 1024;   // 32 MiB
    mem.base = 0x80200000;
    mem.data = calloc(1, mem.size);
    cpu.mode = 3;  // 從 M-mode 開始

    // 載入 ELF
    load_elf(argv[1], &mem, &cpu.pc);

    // 執行
    while (1) {
        // Fetch（需要走頁表）
        uint64_t inst_pa;
        if (translate(&cpu, &mem, cpu.pc, ACCESS_FETCH, &inst_pa) != 0) {
            // page fault 已在 translate 裡 raise，繼續執行（會跳到 stvec）
            continue;
        }
        uint32_t raw_inst = mem_read32(&mem, inst_pa);

        // Decode
        DecodedInst inst = decode(raw_inst);

        // Execute（包含 memory access，也需要走頁表）
        execute(&cpu, &mem, &inst);

        // 確保 x0 永遠是 0
        cpu.x[0] = 0;

        // 更新計數器
        cpu.cycle_count++;
        cpu.instret_count++;
    }

    return 0;
}
```

---

## 程式碼組織建議

```
rv64_emulator/
├── src/
│   ├── cpu.h          # CPU state, 型別定義
│   ├── memory.h       # Memory 讀寫介面
│   ├── csr.h/c        # CSR 讀寫
│   ├── decode.h/c     # 指令解碼
│   ├── execute.h/c    # 指令執行
│   ├── mmu.h/c        # Sv48 page table walker
│   ├── trap.h/c       # exception/interrupt 處理
│   ├── elf_loader.h/c # 載入 ELF 到記憶體
│   └── main.c         # 主程式
├── tests/
│   ├── test_rv64i.S   # Milestone 1 測試
│   ├── test_csr.S     # Milestone 2 測試
│   ├── test_sv48.c    # Milestone 3 測試
│   └── test_pf.S      # Milestone 4 測試
└── Makefile
```

---

## 除錯建議

**Milestone 1 debug**：實作一個 `cpu_dump(CPU *cpu)` 函式，在每條指令執行後印出所有暫存器，比對預期值。

**Milestone 3 debug**：加入一個 `--trace-mmu` 旗標，在 `translate()` 裡印出每層 PTE 的值和地址，確認 walk 路徑正確。

```c
// 在 translate() 的每層加入：
#ifdef DEBUG_MMU
fprintf(stderr, "  L%d: pt_pa=0x%lx, vpn=%ld, pte_pa=0x%lx, pte=0x%lx\n",
        level, pt_pa, vpn[level], pte_pa, pte);
#endif
```

**Milestone 4 debug**：實作一個 `exception_log` 陣列，記錄所有 exception 的 cause/epc/tval，在測試後 dump 出來比對。

---

## 自我檢核

完成整個 Final Project 後，你應該能說是：

- [ ] 寫出了一個能執行 RV64I 指令（含 W 後綴）的模擬器
- [ ] 實作了 Sv48 四層頁表 walk，包含大頁支援
- [ ] exception delegation（medeleg）能正確把 page fault 路由到 S-mode
- [ ] page fault 的 trap flow（sepc/scause/stval/stvec）正確
- [ ] 能跑 sv48_baremetal.c 的 PASS 輸出

如果你做到了這裡，你對 RISC-V RV64I 的理解已經超過了大多數只讀過 spec 的人。下一步是讀真正的 RISC-V Linux kernel code，或者玩 RISC-V CTF。
