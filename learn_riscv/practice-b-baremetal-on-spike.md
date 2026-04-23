# 練習 B — 用 spike 跑 baremetal

> 目標：寫一支完全 baremetal 的 RV64 程式（沒 OS、沒 libc、沒 printf），在 spike 上跑起來。過程會讓你理解 linker script、ELF entry point、trap handler、以及 spike + HTIF 這條 "non-Linux" 的執行路徑。這是做 firmware / hypervisor / 加速器驅動都要打的基礎。

## 為什麼要做這個

前幾章我們都用 `spike pk hello`，背後 `pk`（proxy kernel）替我們處理了 entry point、syscall forwarding、exit。**現實工作中你會遇到 pk 幫不上的場景**：

- 自己寫 bootloader
- 測試某個硬體 feature 要在 M-mode 才能設
- 帶自己寫的 trap handler 做 ecall / exception testing
- 跑 自家 custom extension 的 test suite

本練習分成三階段：

1. **Stage 1**: 最簡單的 baremetal hello（用 spike 的 HTIF interface）
2. **Stage 2**: 加 trap handler，測 `ecall` 行為
3. **Stage 3**: 進 U-mode，測 privilege 切換

## 準備工具

```bash
riscv64-unknown-elf-gcc --version    # 需要
riscv64-unknown-elf-ld --version     # 需要
riscv64-unknown-elf-objdump --version
spike --help                           # 需要
```

新建工作目錄：

```bash
mkdir -p ~/rv-baremetal && cd ~/rv-baremetal
```

## Stage 1：Baremetal Hello World

### 檔案 1: `start.S`（entry point）

```asm
    .section .text.init
    .global _start

_start:
    la      sp, _stack_top           # 設 stack pointer
    call    main                      # 呼叫 C
    j       _hang                     # 正常 return 就停

_hang:
    # 通知 spike 結束（HTIF tohost）
    la      t0, tohost
    li      t1, 1                     # exit code shifted
    sd      t1, 0(t0)

.L_loop:
    j       .L_loop
```

**HTIF (Host Target Interface)** 是 spike 跟 target 溝通的約定。寫 `tohost` 變數 = 發 signal 給 spike。`1` 表示「exit(0)」—spike 會結束模擬。

### 檔案 2: `main.c`

```c
// 極簡 print：直接寫 tohost (byte-by-byte)
extern volatile unsigned long tohost;

void putchar(char c) {
    tohost = 0x0101000000000000UL | c;  // HTIF syscall = putchar
    while (tohost != 0)
        ;  // 等 spike 回應
}

void puts(const char *s) {
    while (*s)
        putchar(*s++);
}

int main(void) {
    puts("hello from baremetal\n");
    return 0;
}
```

注意：spike 的 HTIF 用 64-bit 字：`[type: 8 bit][cmd: 8 bit][data: 48 bit]`。`0x01 01` 代表 "syscall 1 = write" 送到 dev 1 = stdout。

### 檔案 3: `linker.ld`（linker script）

```
ENTRY(_start)

MEMORY {
    RAM (rwx) : ORIGIN = 0x80000000, LENGTH = 16M
}

SECTIONS {
    . = 0x80000000;

    .text.init : { *(.text.init) } > RAM
    .text      : { *(.text*) }    > RAM
    .rodata    : { *(.rodata*) }  > RAM
    .data      : { *(.data*) }    > RAM
    .bss       : { *(.bss*) *(COMMON) } > RAM

    . = ALIGN(16);
    .tohost : {
        PROVIDE(tohost = .);
        . += 8;
        PROVIDE(fromhost = .);
        . += 8;
    } > RAM

    . = ALIGN(16);
    _stack_top = . + 0x10000;
}
```

關鍵：

- **起始地址 `0x80000000`**：這是 spike 默認開機地址（可用 `--pc` 改）。
- **`.tohost` section**：必須存在，spike 會掃描 ELF 找這個符號。
- **Stack** 放在 bss 之後 +64KB。

### 建置

```bash
riscv64-unknown-elf-gcc -march=rv64g -mabi=lp64 -nostdlib \
    -T linker.ld -o hello.elf start.S main.c

riscv64-unknown-elf-objdump -d hello.elf | head -30
```

objdump 應該看到 `_start` 在 0x80000000、`main` 在其後。

### 跑

```bash
spike hello.elf
```

預期輸出：

```
hello from baremetal
```

**如果 spike hang**：最常見的坑是 tohost 符號找不到、或寫錯 HTIF 協定。用 `objdump -t hello.elf | grep tohost` 確認符號存在。

## Stage 2：寫 trap handler，測 ecall

讓我們從 M-mode 發一個 `ecall`，接住、處理、`mret` 回來。

### 增補 `start.S`

```asm
    .section .text.init
    .global _start

_start:
    # 設 mtvec 指向 trap_handler (direct mode)
    la      t0, trap_handler
    csrw    mtvec, t0

    la      sp, _stack_top
    call    main
    j       _hang

    .align 2
trap_handler:
    # 最簡版：只印訊息然後 mret
    csrr    t0, mcause
    csrr    t1, mepc

    # 判斷是否是 ecall (cause = 11 for M-mode ecall)
    li      t2, 11
    bne     t0, t2, .L_trap_other

    # 印「GOT ECALL」
    la      a0, ecall_msg
    call    puts_asm

    # mepc + 4 跳過 ecall 指令
    addi    t1, t1, 4
    csrw    mepc, t1
    mret

.L_trap_other:
    # 其他 trap：印 cause 然後 hang
    la      a0, unknown_msg
    call    puts_asm
    j       _hang

puts_asm:
    # 簡化版 puts，從 a0 讀字串印到 HTIF
    mv      t3, a0
.L_puts_loop:
    lbu     t4, 0(t3)
    beqz    t4, .L_puts_done
    # ...（略，會呼叫 putchar）
.L_puts_done:
    ret

_hang:
    la      t0, tohost
    li      t1, 1
    sd      t1, 0(t0)
1:  j       1b

    .section .rodata
ecall_msg:
    .string "GOT ECALL\n"
unknown_msg:
    .string "UNKNOWN TRAP\n"
```

（puts_asm 的細節留給讀者完成 — 或者從 C 呼叫 putchar。）

### `main.c` 增補：觸發 ecall

```c
int main(void) {
    puts("before ecall\n");
    asm volatile ("ecall");
    puts("after ecall\n");
    return 0;
}
```

### 預期 output

```
before ecall
GOT ECALL
after ecall
```

如果看到這個 → 恭喜，你已經**寫了一個最簡 M-mode trap handler**。

## Stage 3：切進 U-mode

讓程式一半在 M-mode、一半在 U-mode。**這是真 OS kernel 的骨幹**。

### `start.S` 修改

```asm
_start:
    la      t0, trap_handler
    csrw    mtvec, t0

    la      sp, _stack_top

    # 設 mstatus.MPP = 00 (U-mode)
    li      t0, (3 << 11)            # clear MPP bits (currently 11 = M-mode)
    csrc    mstatus, t0

    # 設 mepc 到 user_main
    la      t0, user_main
    csrw    mepc, t0

    mret                              # 跳進 U-mode 跑 user_main
```

### `main.c` 改寫

```c
void user_main(void) {
    // 這裡已經是 U-mode
    // 直接 ecall 測試
    asm volatile ("ecall");

    // 不應該到這裡（看你的 trap handler 有沒有 return）
    asm volatile ("j .");
}
```

### trap handler 要調整

U-mode `ecall` 的 cause 是 8，不是 11。handler 要認這個：

```asm
    li      t2, 8                    # U-mode ecall
    beq     t0, t2, .L_handle_syscall
    li      t2, 11                   # M-mode ecall
    beq     t0, t2, .L_handle_m_ecall
    j       .L_trap_other

.L_handle_syscall:
    # 假設 a7 = syscall number, a0..a6 = args
    # 這裡極簡：印個 "U-MODE ECALL" 就回去
    la      a0, uecall_msg
    call    puts_asm
    csrr    t0, mepc
    addi    t0, t0, 4
    csrw    mepc, t0
    mret                              # mstatus.MPP 還是 U-mode，mret 回 U
```

### 測試

```bash
spike hello.elf
```

應該看到 "U-MODE ECALL" 那行。

## 常見問題與除錯

### spike 卡住沒輸出

- 檢查 tohost 符號在 ELF 裡
- 檢查 HTIF 寫法（要寫 `0x0101000000000000 | c` 不是單純 `c`）
- 用 `spike -l hello.elf` 開 instruction log 看卡在哪

### `Unhandled exception`

- 多半是 stack 沒設、sp 指向無效地址
- 用 `spike --isa=rv64g --pc=0x80000000 -l hello.elf | head -20` 看第幾條指令出事

### `mret` 跳到奇怪地方

- 檢查 `mepc` 是否在 mret 前設對
- 檢查 `mstatus.MPP` 對應你想進的 mode

### `ecall` 沒觸發 trap

- 檢查 `mtvec` 有設
- 檢查 `mstatus.MIE` 是否要開（其實 exception 不看 MIE，只有 interrupt 看）

## 觀察 spike 的 instruction log

```bash
spike -l hello.elf 2>&1 | head -50
```

會輸出每條指令的執行：

```
core   0: 0x0000000080000000 (0x00000297) auipc   t0, 0x0
core   0: 0x0000000080000004 (0x00828293) addi    t0, t0, 8
...
core   0: 0x000000008000001c (0x00000073) ecall
core   0: exception trap_user_ecall, epc 0x000000008000001c
core   0: 0x0000000080000100 (0x342022f3) csrr    t0, mcause
...
```

超實用。**學 privileged ISA 最快的方式是 trace 一個 ecall 的 full flow**。

## 進階挑戰

做完上面三個 stage 後，試試：

1. **Timer interrupt**：設 `mtimecmp`，讓 timer interrupt 觸發，在 handler 裡印訊息後 ack。
2. **PMP 設定**：配一塊 region 成 read-only，從 U-mode 寫進去觀察 exception。
3. **Virtual memory**：設 `satp` 開啟 Sv39，建 page table，驗證 VA → PA 翻譯。
4. **測 RVV**：跑一小段 vector add 的 baremetal code，驗證 spike 的 vector 行為。
5. **加 custom extension**：用 spike 的 `--extension=my_ext` 載入你自己寫的 C++ plugin（需要看 spike 源碼的 customext/ 資料夾）。

## 跟 final project 的關係

這個練習讓你理解「ELF + linker + spike」的端到端鏈。**Final Project 的 RV32I emulator 不需要跑 baremetal**（它是 user-mode），但你需要**理解 ELF 怎麼被載入**、`_start` 在哪裡、sp 要怎麼設。

## 自我檢核

- [ ] 我能寫一個最簡 baremetal hello，用 spike 跑通
- [ ] 我能寫一個最簡 trap handler，接住 ecall 並印訊息
- [ ] 我能把程式從 M-mode 切到 U-mode 並處理 U-mode ecall
- [ ] 我能讀 spike `-l` 的 instruction log 追蹤 trap 流程
- [ ] 我知道 HTIF 的 tohost 機制與 linker script 的關鍵 section

## 下一步

→ [Final Project：Mini RV32I Emulator](./final-project-rv32i-emulator.md)
