# 練習 B — QEMU virt aarch64 從 EL3 降到 EL1 開 MMU

> 目標：在 QEMU virt 上手刻一個 bare-metal aarch64 程式，從 EL3 啟動，配 secure / non-secure 切換、設好 EL1 環境、降到 EL1、設 page table、開 MMU、印出 hello。完整體驗 Cortex-A 啟動鏈的縮小版。

## 任務規格

| 階段 | 動作 |
|---|---|
| 1 | QEMU 從 EL3 啟動（`-machine secure=on,virtualization=on`） |
| 2 | EL3 設好 SCR_EL3 / SPSR_EL3 / ELR_EL3，ERET 到 EL1 |
| 3 | EL1 設好 vector table、page table |
| 4 | 開啟 MMU，VA = PA identity mapping |
| 5 | 透過 PL011 UART (0x09000000) 印 `hello from EL1 with MMU on` |

## 期望輸出

```
hello from EL3
hello from EL1
hello from EL1 with MMU on
```

## 實作步驟建議

### Step 1：linker script + boot.S 骨架

```ld
/* link.ld */
ENTRY(_start)
SECTIONS
{
    . = 0x40000000;
    .text : { *(.text.boot) *(.text*) }
    .rodata : { *(.rodata*) }
    .data : { *(.data*) }
    .bss : ALIGN(8) { _bss_start = .; *(.bss*) *(COMMON) _bss_end = .; }
    . = ALIGN(4096);
    _stack_bottom = .;
    . = . + 0x10000;
    _stack_top = .;
    . = ALIGN(4096);
    _pgtable = .;
    . = . + 0x4000;          /* 4 個 4 KB pages 給 page table */
}
```

### Step 2：boot.S — 從 EL3 開始

```asm
.section .text.boot

.global _start
_start:
    /* 設定 stack */
    ldr   x0, =_stack_top
    mov   sp, x0

    /* 看目前在哪個 EL */
    mrs   x0, currentel
    lsr   x0, x0, #2

    /* QEMU 啟動時 secure=on,virtualization=on 會在 EL3 */
    cmp   x0, #3
    beq   in_el3
    cmp   x0, #2
    beq   in_el2
    /* 已在 EL1，直接跳 */
    bl    el1_entry
    b     halt

in_el3:
    bl    uart_puts_el3
    /* 設定 EL1 環境並 ERET 過去 */
    bl    drop_to_el1
    /* 不會回來 */

in_el2:
    /* 不應該發生，假裝沒看到 */
    b     halt

halt:
    wfe
    b     halt

uart_puts_el3:
    adr   x0, msg_el3
    bl    uart_puts
    ret

msg_el3: .asciz "hello from EL3\r\n"
msg_el1: .asciz "hello from EL1\r\n"
msg_mmu: .asciz "hello from EL1 with MMU on\r\n"
```

### Step 3：drop_to_el1

```asm
drop_to_el1:
    /* SCR_EL3:
       NS = 1   (non-secure EL1)
       RW = 1   (next EL is AArch64)
       SMD = 1  (smc disabled below EL3)
       HCE = 0  (no HVC)
       EA = 0
       Bit 0..7 reserved-1: 0xb1
    */
    mov   x0, #0x431       /* RW=1 NS=1 SMD=1 */
    msr   scr_el3, x0

    /* 給 EL1 一個 stack（共用 _stack_top） */
    ldr   x0, =_stack_top
    msr   sp_el1, x0

    /* EL1 vector table */
    adr   x0, vectors
    msr   vbar_el1, x0

    /* SPSR_EL3 = "從 EL1 來，AArch64, IRQ/FIQ masked" */
    mov   x0, #0x3c5       /* M[3:0] = 0101 (EL1h), DAIF mask */
    msr   spsr_el3, x0

    /* ELR_EL3 = el1_entry */
    adr   x0, el1_entry
    msr   elr_el3, x0

    eret
```

### Step 4：el1_entry & MMU 設定

```asm
el1_entry:
    /* 此時在 EL1 */
    bl    uart_puts_el1
    bl    setup_pgtable
    bl    enable_mmu
    bl    uart_puts_mmu
    b     halt

uart_puts_el1:
    adr   x0, msg_el1
    bl    uart_puts
    ret

uart_puts_mmu:
    adr   x0, msg_mmu
    bl    uart_puts
    ret
```

### Step 5：UART 輸出（PL011 在 0x09000000）

```asm
/* x0 = string pointer */
uart_puts:
    ldr   x1, =0x09000000   /* PL011 UART base */
1:  ldrb  w2, [x0], #1
    cbz   w2, 2f
    /* PL011 UARTDR offset 0x000 */
    strb  w2, [x1]
    b     1b
2:  ret
```

QEMU virt 把 UART log 直接打到 stdout（`-nographic`），不用配 baud。

### Step 6：page table（identity map）

我們做最簡單的：1 GB 的 block descriptor 映射 0x00000000 - 0x40000000（device + DRAM）。

```c
/* setup_pgtable.c — 也可純 asm */
extern char _pgtable[];

void setup_pgtable(void) {
    uint64_t *l0 = (uint64_t *)_pgtable;
    uint64_t *l1 = (uint64_t *)(_pgtable + 0x1000);

    /* L0[0] -> L1 table */
    l0[0] = (uint64_t)l1 | 0x3;    /* table descriptor */

    /* L1[0]: 0x00000000–0x3FFFFFFF, device */
    l1[0] = 0x00000000
          | (1ULL << 0)            /* valid */
          | (0ULL << 1)            /* block (= L1 size 1 GB) */
          | (0ULL << 2)            /* AttrIndx = 0 (device, 看 MAIR) */
          | (1ULL << 10);          /* AF */

    /* L1[1]: 0x40000000–0x7FFFFFFF, normal cacheable (DRAM) */
    l1[1] = 0x40000000
          | (1ULL << 0)            /* valid */
          | (0ULL << 1)            /* block */
          | (1ULL << 2)            /* AttrIndx = 1 (normal) */
          | (1ULL << 10);          /* AF */

    asm volatile("dsb sy" ::: "memory");
}
```

### Step 7：enable_mmu

```asm
enable_mmu:
    /* MAIR: Attr0 = device-nGnRnE (0x00), Attr1 = normal WB WA RA (0xff) */
    mov   x0, #0x00
    mov   x1, #0xff
    bfi   x0, x1, #8, #8     /* 把 0xff 放到 Attr1 */
    msr   mair_el1, x0

    /* TCR: T0SZ=25 (39-bit VA), 4K granule, inner WB cacheable, IS */
    mov   x0, #25
    /* T0SZ in bits[5:0], TG0 = 4K (00) in bits[15:14]
       SH0 = IS (11) bits[13:12], IRGN0 = WBWA (01) bits[9:8],
       ORGN0 = WBWA (01) bits[11:10] */
    movz  x1, #0x1900, lsl #0       /* 0x1900: SH0=11, ORGN0=01, IRGN0=01 */
    orr   x0, x0, x1
    msr   tcr_el1, x0
    isb

    /* TTBR0_EL1 = pgtable */
    ldr   x0, =_pgtable
    msr   ttbr0_el1, x0
    isb

    /* SCTLR_EL1: enable MMU + I/D cache */
    mrs   x0, sctlr_el1
    orr   x0, x0, #(1 << 0)    /* M: MMU enable */
    orr   x0, x0, #(1 << 2)    /* C: D-cache */
    orr   x0, x0, #(1 << 12)   /* I: I-cache */
    msr   sctlr_el1, x0
    isb
    ret
```

### Step 8：vectors 表

```asm
.align 11               /* 2 KB alignment */
vectors:
.org 0x000              /* sync, current EL with SP_EL0 */
    b   .
.org 0x080              /* IRQ */
    b   .
.org 0x100              /* FIQ */
    b   .
.org 0x180              /* SError */
    b   .
.org 0x200              /* sync, current EL with SP_ELx */
    b   .
.org 0x280
    b   .
.org 0x300
    b   .
.org 0x380
    b   .
.org 0x400              /* sync, lower EL using AArch64 */
    b   .
.org 0x480
    b   .
.org 0x500
    b   .
.org 0x580
    b   .
.org 0x600              /* lower EL using AArch32 */
    b   .
.org 0x680
    b   .
.org 0x700
    b   .
.org 0x780
    b   .
```

每個 entry 都死循環（測試這個練習不會 trap），實際 OS 要寫 handler。

## 編譯 & 執行

```bash
aarch64-none-elf-gcc -nostdlib -nostartfiles -ffreestanding \
    -T link.ld -o boot.elf boot.S setup_pgtable.c

qemu-system-aarch64 \
    -machine virt,secure=on,virtualization=on \
    -cpu cortex-a72 \
    -nographic -semihosting \
    -kernel boot.elf
```

`secure=on` 讓 QEMU 啟動時在 EL3。`virtualization=on` 開 EL2（這個練習不用，但 future-proof）。

## 完整參考解答

**先動手寫過再看**。完整 zip 解答放 `solutions/arm/practice-b/` 即可，這裡列關鍵驗證點：

<details>
<summary>checklist — 你的 code 至少要做對這些事</summary>

1. `_start` 在 0x40000000（QEMU virt 的 DRAM 起點）
2. 用 `currentel` 真的判斷 EL，不是寫死 EL3
3. `drop_to_el1`：SCR_EL3 設 RW=1（不然 ERET 會切回 AArch32 fault）
4. SPSR_EL3 = 0x3c5（M=0101 EL1h，DAIF mask 全開）
5. ELR_EL3 設好 el1_entry **位址**（不要忘記 `adr` 不是 `ldr =`）
6. ERET 後在 EL1，可印「hello from EL1」 — 這時還沒 MMU
7. page table L0/L1 設對了 — 1 GB block 用 L1 entry
8. MAIR 至少有兩個 type（device + normal）
9. TCR_EL1：T0SZ、IRGN0、ORGN0、SH0、TG0 都要正確
10. SCTLR_EL1.M = 1 之後印「hello from EL1 with MMU on」
11. 如果 step 10 不出 → MMU 設定有錯，最常見：page table 沒 cover UART (0x09000000) 或 PC 自己（_start 在 0x40000000）
12. ISB 在 SCTLR 改完後別忘了

</details>

## 測試用例

- **正常路徑**：應印三行 hello。如果只印兩行，MMU enable 後就崩了（很可能 PC 不在已 map 的 region）
- **故意把 SCR_EL3.RW 設 0**：ERET 後 CPU 切到 AArch32，會 fault，看不到 EL1 print
- **故意把 L1 entry 第 0 個 (UART region) 的 AttrIndx 設成 normal cacheable**：可能會看到一些行，但寫 PL011 register 進 cache 沒 flush 時不會出
- **MMU 開啟後 SP 還是 stack 範圍**：SP 必須在 mapped region，否則 enable MMU 那刻就 stack fault

## 自我檢核

- [ ] 我能寫一個從 EL3 ERET 到 EL1 的 boot 程式
- [ ] 我能設好 SCR_EL3 / SPSR_EL3 / ELR_EL3 三個 register
- [ ] 我能寫一個 1 GB block descriptor 的最簡 page table
- [ ] 我能設 MAIR_EL1 / TCR_EL1 / SCTLR_EL1 開 MMU
- [ ] 我用 GDB+QEMU 抓得到 MMU 開啟前後的 state 差異
- [ ] 我看得懂 vector table 的 16 個 entry 對應什麼

到這裡 Part 3 結束。下一個 Part 是除錯全套 — 我們已經寫了不少底層 code，現在學怎麼用 GDB / OpenOCD / JTAG 與 SWD / CoreSight 把它們玩明白。

→ [Ch 23 JTAG vs SWD 硬體層](./23-jtag-swd.md)
