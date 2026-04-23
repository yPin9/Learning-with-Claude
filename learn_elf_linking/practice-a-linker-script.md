# 練習 A — 手刻 linker script

> 目標：用一份你自己寫的 linker script，link 一個能在 spike 跑的 baremetal hello world。過程涵蓋 MEMORY / SECTIONS / VMA/LMA / 對齊 / startup code 的協作。

## 任務描述

寫一個：

1. 自己的 `start.S`（沒 pk、沒 libc）
2. 自己的 `main.c`（用 HTIF putchar 輸出）
3. 自己的 `link.lds`（linker script）

讓 `spike your_elf` 印出 `hello baremetal`。

## Stage 1 — 最簡版

### `start.S`

```asm
    .section .text.init
    .global _start
_start:
    # 設 stack
    la      sp, _stack_top
    # 呼叫 main
    call    main
    # 呼叫後進入 hang（同時 tell spike 退出）
_hang:
    la      t0, tohost
    li      t1, 1
    sd      t1, 0(t0)
1:  j       1b
```

### `main.c`

```c
extern volatile unsigned long tohost;

void _putchar(char c) {
    tohost = 0x0101000000000000UL | (unsigned char)c;
    while (tohost != 0) ;
}

void _puts(const char *s) {
    while (*s) _putchar(*s++);
}

int main(void) {
    _puts("hello baremetal\n");
    return 0;
}
```

### `link.lds`（你的任務）

要求：

- entry point `_start`
- 把所有 code 放 0x80000000 起
- 明確預留 stack 空間
- 宣告 `tohost` 符號（HTIF 協定需要）
- 應用 ALIGN 確保對齊

**提示**：本書 Ch 8 的「完整的 MCU 範例」是好起點。但 spike 的 HTIF 需要特殊的 `tohost` / `fromhost` section。

參考骨架（你要填空）：

```
ENTRY(_start)

SECTIONS
{
    . = 0x80000000;

    /* TODO: .text section */

    /* TODO: .rodata / .data / .bss */

    /* HTIF symbols (固定要有) */
    . = ALIGN(16);
    .tohost : {
        PROVIDE(tohost = .);
        . += 8;
        PROVIDE(fromhost = .);
        . += 8;
    }

    /* TODO: stack */
}
```

### 建置 + 測試

```bash
riscv64-unknown-elf-gcc -march=rv64g -nostdlib -T link.lds -o hello start.S main.c
spike hello
# 預期: hello baremetal
```

### 檢核

- [ ] spike 印出正確字串
- [ ] `readelf -l hello` 看 PT_LOAD 起始是 0x80000000
- [ ] `objdump -d hello | grep _start` 找得到 entry
- [ ] `objdump -t hello | grep tohost` 有 tohost symbol

## Stage 2 — Flash + RAM 分離

這階段模擬 MCU。用兩個假 MEMORY region：

```
FLASH (rx)  : ORIGIN = 0x80000000, LENGTH = 64K
RAM   (rw)  : ORIGIN = 0x80100000, LENGTH = 16K
```

任務：

1. `.text` / `.rodata` 放 FLASH
2. `.data` 放 RAM（LMA FLASH），VMA 跟 LMA 不同
3. `.bss` 放 RAM（NOLOAD）
4. Stack 在 RAM 尾
5. startup 要 copy `.data` 從 FLASH 到 RAM、zero `.bss`

### `start.S` 要加的

```asm
    .section .text.init
_start:
    la      sp, _stack_top

    # 1. Copy .data from FLASH to RAM
    la      t0, __data_lma       # source in FLASH
    la      t1, __data_start     # dest in RAM
    la      t2, __data_end
.L_copy:
    beq     t1, t2, .L_copy_done
    lw      t3, 0(t0)
    sw      t3, 0(t1)
    addi    t0, t0, 4
    addi    t1, t1, 4
    j       .L_copy
.L_copy_done:

    # 2. Zero .bss
    la      t0, __bss_start
    la      t1, __bss_end
.L_bss:
    beq     t0, t1, .L_bss_done
    sw      x0, 0(t0)
    addi    t0, t0, 4
    j       .L_bss
.L_bss_done:

    # 3. main
    call    main
    j       _hang
```

### linker script 要給這些 symbol

```
.data : {
    . = ALIGN(4);
    PROVIDE(__data_start = .);
    *(.data*)
    . = ALIGN(4);
    PROVIDE(__data_end = .);
} > RAM AT > FLASH
PROVIDE(__data_lma = LOADADDR(.data));
```

### `main.c` 增加 test

```c
int initialized_var = 42;     // 進 .data
int zero_var;                  // 進 .bss

int main(void) {
    if (initialized_var == 42 && zero_var == 0) {
        _puts("data/bss OK\n");
    } else {
        _puts("data/bss FAIL\n");
    }
    return 0;
}
```

### 檢核

- [ ] `objdump -h hello` 看 .data 的 LMA ≠ VMA
- [ ] spike 印出 "data/bss OK"
- [ ] 故意移除 copy `.data` 的 code，看 `initialized_var` 讀到垃圾

## Stage 3 — 加 KEEP 與 gc-sections

增加一些測試：

```c
void __attribute__((section(".isr_vector"))) fake_isr(void) {
    // 假的 interrupt handler
}

void unused_function(void) {
    _puts("never called\n");
}
```

編時加 `-ffunction-sections -fdata-sections` 跟 `-Wl,--gc-sections`。

**任務**：

1. 在 linker script 加入 `.isr_vector` 的 placement（放 .text 最前面）
2. 確保 `.isr_vector` 不被 gc-sections 砍掉（用 `KEEP`）
3. 確認 `unused_function` 被砍了

### 檢核

```bash
nm hello | grep -E "fake_isr|unused_function"
```

- `fake_isr` 應該還在（因為 KEEP）
- `unused_function` 應該不見（被 gc）

## Stage 4 — Map file 驗證

加 `-Wl,-Map=link.map` 產 map file。

讀 `link.map` 找出：

1. 每個 output section 的起始地址
2. 每個 input section 放在哪
3. `unused_function` 是不是真的被砍

這是 production linker script debug 的必備工具。

## 一個容易踩的坑

### 忘記對齊 sp

```asm
la sp, _stack_top   # stack top
```

若 `_stack_top` 不是 16-byte 對齊，之後 `call` 時會產生 mis-aligned sp。linker script 要確保：

```
. = ALIGN(16);
_stack_top = .;
```

## Stage 5（進階）— PLIC / UART

如果想挑戰，把 tohost 換成 virtual UART。但這需要 spike 的 virtio / UART 支援，複雜許多。初學建議不走這條路。

## 完成後的收穫

做完 stage 1-4 你有：

- 能讀懂任何 MCU linker script
- 能寫 startup code 對應 linker script
- 知道 relax / gc-sections / KEEP 如何協作
- 這是 embedded / kernel 工程師面試的基本功

## 自我檢核

- [ ] 我能寫一份乾淨的 linker script 給 FLASH + RAM 分離的 MCU
- [ ] 我知道 VMA / LMA 的設定與 startup 的 copy 對應
- [ ] 我能解釋 KEEP 的必要性並 debug gc-sections 的 bug
- [ ] 我能讀 Map file 驗證 layout
- [ ] 我知道對齊要求與 stack alignment 問題

## 下一步

→ [練習 B：Debug 一個 relax 炸掉的 bug](./practice-b-relax-gone-wrong.md)
→ [Final Project：Mini static linker](./final-project-mini-linker.md)
