# 練習 D — 手動建立 Sv48 頁表（Baremetal QEMU）

> 這道練習讓你從零建立一個完整的 Sv48 頁表環境：分配 page frame、建立四層頁表、切換 satp、驗證虛擬地址存取、故意製造一個 page fault。不要用模擬器——在真實的 QEMU 上跑。

---

## 環境需求

```bash
# 工具鏈
riscv64-unknown-elf-gcc --version   # 需要 newlib/bare-metal toolchain
qemu-system-riscv64 --version       # 需要 RISC-V QEMU

# 確認 QEMU 有 virt machine 和 riscv64 cpu
qemu-system-riscv64 -M virt -cpu help
```

---

## 整體架構說明

這個 baremetal 程式執行在 QEMU virt 機器的 M-mode（因為 `-bios none` 沒有 OpenSBI）。它的任務：

```
啟動（M-mode, physical address space）
  │
  ├─ 初始化 UART（用於輸出）
  ├─ 設定 M-mode trap handler（捕捉 page fault 用）
  ├─ 設定 medeleg（把 page fault 委派給 S-mode）
  ├─ 切換到 S-mode（mret）
  │
  v
S-mode 執行
  ├─ 建立 Sv48 四層頁表（分配 5 個 page frame）
  ├─ identity map 0x80200000（程式碼本身所在的 page）
  ├─ identity map data page（0x80300000，存放測試值）
  ├─ 設定 satp，切換到 Sv48
  ├─ sfence.vma
  ├─ 通過虛擬地址讀取 data page 的值，驗證成功
  └─ 故意存取未映射的地址，觸發 page fault
       └─ S-mode page fault handler 確認被呼叫
```

---

## 完整程式碼

### 檔案 1：start.S（啟動程式碼）

```asm
# start.S
    .section .text.start
    .globl _start

_start:
    # 設定 M-mode stack
    la   sp, _mstack_top

    # 設定 M-mode trap handler（用於除錯，通常不會觸發）
    la   t0, m_trap_handler
    csrw mtvec, t0

    # 設定 medeleg：把 page fault 和 U/S-mode ecall 委派給 S-mode
    li   t0, (1 << 12) | (1 << 13) | (1 << 15) | (1 << 8) | (1 << 9)
    csrw medeleg, t0

    # 設定 mideleg：把 S-mode timer/software/external interrupt 委派給 S-mode
    li   t0, (1 << 1) | (1 << 5) | (1 << 9)
    csrw mideleg, t0

    # 設定 S-mode entry
    la   t0, s_mode_entry
    csrw mepc, t0

    # mstatus.MPP = 01（S-mode），這樣 mret 後就在 S-mode
    li   t0, (1 << 11)      # MPP = 01（S-mode = 1）
    # 注意：MPP 在 mstatus bits [12:11]
    # MPP = 01b = S-mode
    li   t1, 0              # 先清零 MPP
    csrc mstatus, t1
    li   t0, (1 << 11)      # 設 MPP bit 11（MPP=01 = S-mode）
    csrs mstatus, t0

    # 開放 S-mode 和 U-mode 讀 cycle/time/instret（mcounteren）
    li   t0, 7
    csrw mcounteren, t0

    mret                    # 跳到 S-mode 的 s_mode_entry

# M-mode trap handler（不應該被呼叫，如果觸發就是 bug）
m_trap_handler:
    la   a0, m_trap_msg
    call uart_puts
    csrr a0, mcause
    call uart_put_hex64
    la   a0, newline
    call uart_puts
1:  wfi
    j    1b

    .section .rodata
m_trap_msg:
    .string "UNEXPECTED M-MODE TRAP! mcause="
newline:
    .string "\n"
```

### 檔案 2：sv48_practice.c（主程式）

```c
// sv48_practice.c
#include <stdint.h>

// ============================================================
// UART（QEMU virt，0x10000000）
// ============================================================
#define UART0  ((volatile uint8_t *)0x10000000UL)

void uart_putc(char c) { *UART0 = (uint8_t)c; }
void uart_puts(const char *s) { while (*s) uart_putc(*s++); }
void uart_put_hex64(uint64_t v) {
    uart_puts("0x");
    for (int i = 60; i >= 0; i -= 4) {
        uint8_t n = (v >> i) & 0xF;
        uart_putc(n < 10 ? '0' + n : 'a' + n - 10);
    }
}
void uart_println(const char *s) { uart_puts(s); uart_putc('\n'); }

// ============================================================
// PTE 定義
// ============================================================
#define PTE_V  (1ULL << 0)
#define PTE_R  (1ULL << 1)
#define PTE_W  (1ULL << 2)
#define PTE_X  (1ULL << 3)
#define PTE_U  (1ULL << 4)
#define PTE_A  (1ULL << 6)
#define PTE_D  (1ULL << 7)

#define PA_TO_PTE_PPN(pa)   (((uint64_t)(pa) >> 12) << 10)
#define SATP_MODE_SV48      (10ULL << 60)

// ============================================================
// 靜態 page frame 分配（各 4 KiB，8-byte aligned）
// ============================================================

// 頁表 page frames
static uint64_t pt_l3[512] __attribute__((aligned(4096)));  // root（L3）
static uint64_t pt_l2[512] __attribute__((aligned(4096)));  // L2
static uint64_t pt_l1[512] __attribute__((aligned(4096)));  // L1
static uint64_t pt_l0[512] __attribute__((aligned(4096)));  // L0（leaf）

// 資料 page（存放測試值）
static volatile uint64_t data_page[512] __attribute__((aligned(4096)));

// ============================================================
// S-mode trap handler（捕捉 page fault）
// ============================================================
static int pf_triggered = 0;
static uint64_t pf_va = 0;
static uint64_t pf_cause = 0;

void s_trap_handler_c(uint64_t cause, uint64_t epc, uint64_t tval) {
    pf_triggered = 1;
    pf_va    = tval;
    pf_cause = cause;

    // 如果是 page fault，不要重試（sepc 不改，sret 會無窮迴圈）
    // 修改 sepc 跳過觸發 fault 的指令（假設是 4-byte 指令）
    // 這裡用 csrw sepc 讓 handler 跳到 fault 後的安全位置
    // 在組語 handler 裡設定 sepc += 4
}

// S-mode trap entry（assembly），呼叫 C handler 後修正 sepc
// 這個放在 sv48_entry.S（見下文）

// ============================================================
// 建立 Sv48 頁表
// ============================================================
static void setup_sv48(void) {
    uint64_t va, pa;
    uint64_t vpn3, vpn2, vpn1, vpn0;

    // === Mapping 1：data_page（讀寫）===
    va = (uint64_t)data_page;
    pa = va;   // identity map

    vpn3 = (va >> 39) & 0x1FF;
    vpn2 = (va >> 30) & 0x1FF;
    vpn1 = (va >> 21) & 0x1FF;
    vpn0 = (va >> 12) & 0x1FF;

    uart_puts("data_page VA="); uart_put_hex64(va); uart_putc('\n');
    uart_puts("VPN[3]="); uart_put_hex64(vpn3);
    uart_puts(" VPN[2]="); uart_put_hex64(vpn2);
    uart_puts(" VPN[1]="); uart_put_hex64(vpn1);
    uart_puts(" VPN[0]="); uart_put_hex64(vpn0);
    uart_putc('\n');

    // 建立四層頁表 entry
    pt_l3[vpn3] = PA_TO_PTE_PPN((uint64_t)pt_l2) | PTE_V;
    pt_l2[vpn2] = PA_TO_PTE_PPN((uint64_t)pt_l1) | PTE_V;
    pt_l1[vpn1] = PA_TO_PTE_PPN((uint64_t)pt_l0) | PTE_V;
    pt_l0[vpn0] = PA_TO_PTE_PPN(pa) | PTE_V | PTE_R | PTE_W | PTE_A | PTE_D;

    // TODO（Part 2）：還需要 map .text/.rodata/.data/stack，
    // 否則 csrw satp 後下一條指令就 page fault
    // 簡化版：如果程式碼也在同一個 L3/L2 下（QEMU virt 的 RAM 從 0x80200000 開始），
    // 可以用 Gigapage 把整個 0x80000000–0xBFFFFFFF identity map

    // 用 Gigapage（1 GiB，在 L2 layer 放 leaf PTE）map 整個 0x80000000–0xBFFFFFFF
    // 這樣程式碼本身也被 map 了
    // Gigapage 的 VPN[3] 和 VPN[2]
    uint64_t text_va = 0x80000000UL;
    uint64_t giga_vpn3 = (text_va >> 39) & 0x1FF;  // = 0
    uint64_t giga_vpn2 = (text_va >> 30) & 0x1FF;  // = 2（0x80000000 >> 30 = 2）

    // Gigapage：在 L2 直接放 leaf PTE
    // PPN 要對齊到 1 GiB（PA 的低 30 bit = 0）
    // L3[0] 可能已經有了（如果 data_page 在同一個 L3 index）
    // 確保 L3[giga_vpn3] 指向我們的 l2_pt
    // （可能和上面的 pt_l3[vpn3] 是同一個 entry，沒關係，
    //  因為 data_page 在 0x80xxx 範圍內）
    if (pt_l3[giga_vpn3] == 0) {
        pt_l3[giga_vpn3] = PA_TO_PTE_PPN((uint64_t)pt_l2) | PTE_V;
    }
    // 在 L2 的 giga_vpn2 位置放 Gigapage leaf PTE
    // 注意：PPN 必須對齊到 Gigapage（PA & ~((1<<30)-1)）
    // 0x80000000 已經是 1 GiB 對齊的
    pt_l2[giga_vpn2] = PA_TO_PTE_PPN(0x80000000UL) | PTE_V | PTE_R | PTE_W | PTE_X | PTE_A | PTE_D;
}

// ============================================================
// 切換到 Sv48 並驗證
// ============================================================
static void enable_sv48_and_test(void) {
    // 把已知值寫入 data page
    data_page[0] = 0xDEADBEEFCAFEBABEULL;
    data_page[1] = 0x0123456789ABCDEFULL;

    // 設定 satp
    uint64_t satp = SATP_MODE_SV48 | ((uint64_t)pt_l3 >> 12);
    uart_puts("satp = "); uart_put_hex64(satp); uart_putc('\n');

    __asm__ volatile (
        "csrw satp, %0\n\t"
        "sfence.vma zero, zero\n\t"
        :: "r"(satp) : "memory"
    );
    uart_println("Sv48 enabled.");

    // 通過虛擬地址讀取
    uint64_t val0 = data_page[0];
    uint64_t val1 = data_page[1];

    uart_puts("data_page[0] = "); uart_put_hex64(val0); uart_putc('\n');
    uart_puts("data_page[1] = "); uart_put_hex64(val1); uart_putc('\n');

    if (val0 == 0xDEADBEEFCAFEBABEULL && val1 == 0x0123456789ABCDEFULL) {
        uart_println("PASS: Page table walk correct!");
    } else {
        uart_println("FAIL: Wrong values read back.");
    }
}

// ============================================================
// 故意製造 page fault
// ============================================================
static void trigger_page_fault(void) {
    uart_println("\n--- Triggering page fault ---");

    // 存取一個明確未映射的虛擬地址
    // 0x0000000040000000 不在我們的頁表裡
    volatile uint64_t *bad_ptr = (volatile uint64_t *)0x0000000040000000ULL;

    uint64_t dummy = *bad_ptr;   // 這行會觸發 load page fault
    (void)dummy;
}

// ============================================================
// S-mode 主函式（從 start.S 的 mret 跳過來）
// ============================================================
void s_mode_entry(void) {
    uart_println("=== Sv48 Page Table Practice ===");
    uart_println("[1] Setting up page tables...");
    setup_sv48();

    uart_println("[2] Enabling Sv48 and testing...");
    enable_sv48_and_test();

    uart_println("[3] Testing page fault handler...");
    trigger_page_fault();

    // 如果 page fault handler 正確工作，會在這裡繼續
    if (pf_triggered) {
        uart_puts("Page fault caught! cause=");
        uart_put_hex64(pf_cause);
        uart_puts(" tval=");
        uart_put_hex64(pf_va);
        uart_putc('\n');
        uart_println("PASS: Page fault handled correctly.");
    } else {
        uart_println("FAIL: Page fault was not caught.");
    }

    uart_println("\n=== All tests complete ===");
    while (1) { __asm__ volatile ("wfi"); }
}
```

### 檔案 3：sv48_entry.S（S-mode trap handler）

```asm
# sv48_entry.S
    .section .text
    .globl s_mode_trap_entry
    .globl install_s_trap

# 安裝 S-mode trap handler
install_s_trap:
    la   t0, s_mode_trap_entry
    csrw stvec, t0
    ret

# S-mode trap handler entry
    .align 2    # stvec 必須 4-byte 對齊
s_mode_trap_entry:
    addi sp, sp, -48        # 保存少量暫存器
    sd   ra,  0(sp)
    sd   a0,  8(sp)
    sd   a1, 16(sp)
    sd   a2, 24(sp)

    csrr a0, scause         # a0 = cause
    csrr a1, sepc           # a1 = epc
    csrr a2, stval          # a2 = tval（fault VA）

    call s_trap_handler_c   # 呼叫 C handler

    # 修改 sepc 跳過觸發 fault 的指令（epc + 4）
    csrr t0, sepc
    addi t0, t0, 4
    csrw sepc, t0

    ld   ra,  0(sp)
    ld   a0,  8(sp)
    ld   a1, 16(sp)
    ld   a2, 24(sp)
    addi sp, sp, 48
    sret
```

### 檔案 4：link.ld（Linker Script）

```ld
ENTRY(_start)
MEMORY {
    RAM (rwx) : ORIGIN = 0x80200000, LENGTH = 8M
}
SECTIONS {
    . = 0x80200000;

    .text.start : { *(.text.start) } > RAM
    .text       : { *(.text*) }      > RAM
    .rodata     : { *(.rodata*) }    > RAM
    .data       : { *(.data*) }      > RAM
    .bss        : {
        __bss_start = .;
        *(.bss*) *(COMMON)
        __bss_end = .;
    } > RAM

    /* M-mode stack（在 BSS 之後）*/
    . = ALIGN(4096);
    .mstack : {
        . += 4096;    /* 4 KiB M-mode stack */
        _mstack_top = .;
    } > RAM
}
```

### 編譯與執行

```bash
# 編譯
riscv64-unknown-elf-gcc \
    -nostdlib -nostartfiles \
    -march=rv64imafdc \
    -mabi=lp64d \
    -T link.ld \
    -O0 -g \
    start.S sv48_entry.S sv48_practice.c \
    -o sv48_practice.elf

# 執行
qemu-system-riscv64 \
    -M virt \
    -m 32M \
    -bios none \
    -kernel sv48_practice.elf \
    -nographic \
    -serial stdio
```

---

## 期望輸出

```
=== Sv48 Page Table Practice ===
[1] Setting up page tables...
data_page VA=0x802xxxxx
VPN[3]=0x0 VPN[2]=0x2 VPN[1]=0x1 VPN[0]=0x...
[2] Enabling Sv48 and testing...
satp = 0xa000000000802xxx
Sv48 enabled.
data_page[0] = 0xdeadbeefcafebabe
data_page[1] = 0x0123456789abcdef
PASS: Page table walk correct!
[3] Testing page fault handler...

--- Triggering page fault ---
Page fault caught! cause=0x000000000000000d tval=0x0000000040000000
PASS: Page fault handled correctly.

=== All tests complete ===
```

---

## 常見錯誤排查

### 問題 1：QEMU 掛住，沒有輸出

**原因**：啟動後就 crash（可能是 stack 未設定，或第一條指令就錯了）。

**排查**：加 `-d in_asm,cpu_reset` flag 看 QEMU 的 debug 輸出：
```bash
qemu-system-riscv64 -M virt -bios none -kernel sv48_practice.elf \
    -nographic -d in_asm,cpu_reset 2>&1 | head -50
```

### 問題 2：`csrw satp` 後立刻 page fault（instruction page fault）

**原因**：.text 段沒有被 map 進頁表。

**修正**：確認 Gigapage 正確 map 了 0x80000000 起始的 1 GiB 區域，且 X bit 有設。

### 問題 3：data_page 讀到 0 或垃圾值

**原因 1**：PPN 計算錯誤（忘了右移 12）。

檢查：
```c
// 驗證 PTE 值
uart_puts("pt_l0[vpn0] = "); uart_put_hex64(pt_l0[vpn0]); uart_putc('\n');
// PPN bits [53:10]，應該等於 (pa >> 12) << 10
```

**原因 2**：sfence.vma 沒有執行，TLB 快取了舊的無效 entry。

### 問題 4：page fault handler 沒被呼叫

**原因**：stvec 沒有設定。確認 `install_s_trap()` 在切換 satp 之前被呼叫。

---

## 延伸挑戰（選做）

1. **加入 Stack Mapping**：不用 Gigapage，改用精確的 page 級別 mapping，只 map 實際用到的 pages（.text, .data, stack）。
2. **Megapage**：在 L1 層放 leaf PTE，map 一個 2 MiB 的範圍，驗證正確。
3. **ASID**：把 satp 的 ASID 設成非 0，確認不同 ASID 之間的隔離。
4. **多 VA 測試**：把兩個不同的 VA 映射到同一個物理頁（aliasing），驗證兩個 VA 讀到一樣的值。

---

## 自我檢核

- [ ] 能從 QEMU 輸出確認 Sv48 mapping 成功（讀到正確值）
- [ ] 能說出為什麼要用 Gigapage map .text（不然 csrw satp 後就 inst page fault）
- [ ] 能解釋 `PA_TO_PTE_PPN` 的兩個移位操作
- [ ] 能從 `pf_cause` 的值判斷是哪種 page fault（13=load, 15=store）
- [ ] 能手動計算 data_page 的 VA 到 VPN[3:0] 的分解

→ [Final Project B — RV64I Emulator + Sv48 Page Walk](final-project-rv64i-sv48-emulator.md)
