# Ch 10 — Startup code 解剖

> 目標：徹底拆 Cortex-M 的 startup code（通常叫 `startup_xxx.s`），看懂 `_sidata` 這些怪 symbol、global constructor 怎麼跑、newlib 的 syscall stub 是什麼。

## 一份標準的 startup_xxx.s

直接從 STM32CubeF4 的 `startup_stm32f407xx.s` 抽精華（簡化版）：

```asm
.syntax unified
.cpu cortex-m4
.fpu softvfp
.thumb

.global g_pfnVectors
.global Default_Handler

/* 從 linker script 來的 symbol */
.word _sidata
.word _sdata
.word _edata
.word _sbss
.word _ebss

.section .text.Reset_Handler
.weak Reset_Handler
.type Reset_Handler, %function
Reset_Handler:
    ldr  sp, =_estack            /* 設定 SP（保險，雖然 vector[0] 已給） */

/* 拷 .data */
    ldr  r0, =_sdata
    ldr  r1, =_edata
    ldr  r2, =_sidata
    movs r3, #0
    b    LoopCopyDataInit
CopyDataInit:
    ldr  r4, [r2, r3]
    str  r4, [r0, r3]
    adds r3, r3, #4
LoopCopyDataInit:
    adds r4, r0, r3
    cmp  r4, r1
    bcc  CopyDataInit

/* 清 .bss */
    ldr  r2, =_sbss
    ldr  r4, =_ebss
    movs r3, #0
    b    LoopFillZerobss
FillZerobss:
    str  r3, [r2]
    adds r2, r2, #4
LoopFillZerobss:
    cmp  r2, r4
    bcc  FillZerobss

/* SystemInit & __libc_init_array & main */
    bl   SystemInit
    bl   __libc_init_array       /* 跑 C++ ctors / __attribute__((constructor)) */
    bl   main
LoopForever:
    b    LoopForever

.size Reset_Handler, .-Reset_Handler

/* Default handler — 死循環 */
.section .text.Default_Handler,"ax",%progbits
Default_Handler:
    b    .

/* Vector table */
.section .isr_vector,"a",%progbits
.global g_pfnVectors
.type g_pfnVectors, %object
g_pfnVectors:
    .word _estack
    .word Reset_Handler
    .word NMI_Handler
    .word HardFault_Handler
    .word MemManage_Handler
    .word BusFault_Handler
    .word UsageFault_Handler
    .word 0
    .word 0
    .word 0
    .word 0
    .word SVC_Handler
    .word DebugMon_Handler
    .word 0
    .word PendSV_Handler
    .word SysTick_Handler
    /* 接下來是外部中斷 IRQ0..IRQn */
    .word WWDG_IRQHandler
    .word PVD_IRQHandler
    /* ... 一大堆 STM32F4 的 IRQ */

/* Weak alias：沒實作的 ISR 預設指 Default_Handler */
.weak NMI_Handler
.thumb_set NMI_Handler, Default_Handler
.weak HardFault_Handler
.thumb_set HardFault_Handler, Default_Handler
/* ... 對每個 ISR 都做一次 */
```

## 那些 symbol 從哪來

`_sdata` `_edata` `_sidata` `_sbss` `_ebss` `_estack` — 這些不是 C source 定義的，**linker script 提供**：

```ld
SECTIONS {
    .text : {
        KEEP(*(.isr_vector))
        *(.text*)
        *(.rodata*)
        _etext = .;
    } > FLASH

    /* .data section：在 RAM 但 LMA 在 FLASH */
    .data : AT(_etext)
    {
        _sdata = .;          /* RAM 中 .data 起 */
        *(.data*)
        _edata = .;          /* RAM 中 .data 終 */
    } > RAM

    _sidata = LOADADDR(.data);   /* FLASH 中 .data 的位置（拷貝來源） */

    .bss : {
        _sbss = .;
        *(.bss*)
        *(COMMON)
        _ebss = .;
    } > RAM

    _estack = ORIGIN(RAM) + LENGTH(RAM);
}
```

`AT(_etext)` 與 `LOADADDR(.data)` 配合，告訴 linker：「`.data` 的 VMA（runtime 位置）在 RAM，但 LMA（load 位置）在 flash」。startup code 把 LMA → VMA 的拷貝就是手刻 loader。

LMA / VMA 觀念是 ELF 標準的一部分。現代 OS 有 loader 自動處理，bare-metal 沒 loader，**你就是 loader**。

## `__libc_init_array`：不只是 C++ 的事

```c
extern void (*__init_array_start[])(void) __attribute__((weak));
extern void (*__init_array_end[])(void) __attribute__((weak));

void __libc_init_array(void) {
    size_t count = __init_array_end - __init_array_start;
    for (size_t i = 0; i < count; i++)
        __init_array_start[i]();
    /* 之後還會呼叫 _init() — 早期 GNU 的東西 */
}
```

`.init_array` section 收集兩種東西：

1. **C++ global object 的 constructor**
2. **`__attribute__((constructor))` 標記的 C 函式**

```c
__attribute__((constructor))
void my_init(void) {
    /* 在 main 前執行 */
}
```

如果你寫純 bare-metal C 沒用這些功能，可以**省掉 `__libc_init_array` 這個 bl**。但 newlib / C++ 標準庫期待它被呼叫，預設留著。

## newlib 與 _start

GCC 的標準 startup（不是嵌入式版本）會產生 `_start`，內容類似：

```c
void _start(void) {
    __libc_init_array();
    int argc = ...;
    char **argv = ...;
    int ret = main(argc, argv);
    exit(ret);
}
```

bare-metal Cortex-M 通常不要 `_start`（用自己的 Reset_Handler）。如果你 link `-lc` 的 newlib，newlib 還會要：

- **`_sbrk(int incr)`**：給 malloc 用，回傳 heap 區域增量
- **`_write(int fd, char *buf, int len)`**：給 printf 用
- **`_read(int fd, char *buf, int len)`**：給 scanf 用
- **`_exit(int status)`**：exit 用
- **`_close` / `_lseek` / `_fstat` / ... 等等**：file 相關，多數可 stub

最簡 syscall stub：

```c
int _write(int fd, char *buf, int len) {
    for (int i = 0; i < len; i++) UART_Send(buf[i]);
    return len;
}

void *_sbrk(int incr) {
    extern char _end;            /* heap 起點，linker 提供 */
    static char *heap = &_end;
    char *prev = heap;
    heap += incr;
    return prev;
}

void _exit(int status) {
    while (1);
}
```

沒提供 `_sbrk` 但用 `printf` 就會 link error；提供了但 heap 邊界沒設好就 runtime crash。

## Newlib-nano：嵌入式版本

`newlib` 完整版很大（printf 帶 float、locale、wide char）。**newlib-nano** 是嵌入式 trim 版：

```bash
arm-none-eabi-gcc ... --specs=nano.specs ...
```

加上去後：

- printf 砍掉 float 支援（要用 `--specs=nosys.specs` 或加 `-u _printf_float` 開回來）
- 整體靜態大小縮小 80%+
- 仍 ABI 兼容

實務上 STM32 / Pico 都建議用 nano.specs。

## 最小化 startup（沒有 newlib）

如果你不要 newlib，連 `__libc_init_array` 都不需要：

```c
void Reset_Handler(void) {
    /* copy .data */
    extern uint32_t _sidata, _sdata, _edata;
    uint32_t *src = &_sidata, *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;

    /* zero .bss */
    extern uint32_t _sbss, _ebss;
    dst = &_sbss;
    while (dst < &_ebss) *dst++ = 0;

    /* 直接進 main */
    main();
    while (1);
}
```

配合 `-nostdlib` 編譯就完整自足。Practice A 走這條路。

## Weak symbol：override 神技

`.weak Foo` 後 `.thumb_set Foo, Default_Handler` 等於：

```c
__attribute__((weak, alias("Default_Handler"))) void Foo(void);
```

意思「**Foo 預設等於 Default_Handler，但如果別處有非 weak 的 Foo 定義，那個會贏**」。

效果：vector table 寫了 `SysTick_Handler` 但你沒實作 — link 時 fallback 到 Default_Handler；你實作了 — 就用你的版本。**不需要改 vector table，靠 link order 自動處理**。

CMSIS / ARM startup 大量用這個 idiom。

## 從 `main` 返回怎麼辦？

bare-metal 規矩：**`main` 不該返回**。如果 `main()` 有個 `return 0`：

```c
int main(void) {
    do_stuff();
    return 0;
}
```

返回後執行 `Reset_Handler` 中 `bl main` 後面的指令 — 那是 `LoopForever: b .`，等於死循環。**沒事**，但代表 CPU 在那邊空轉。

通常 bare-metal `main` 寫成：

```c
void main(void) {
    init_stuff();
    while (1) {
        do_one_iteration();
    }
}
```

從不返回。

## 一個常見誤解

「我用 STM32CubeIDE 就有 startup code 了，為什麼還要學？」

因為當你**移植到新 SoC、寫 bootloader、debug 啟動 hang、做 Memory layout 換位**時，那份 startup_xxx.s 你會看不懂、改不動。多數真實 firmware bug 出在 startup（拷貝順序、bss 清零範圍、`.text` 沒 KEEP 被 GC、SystemInit 用了未初始化的 .data）— 不會看 startup 等於不會 debug 啟動問題。

## 自我檢核

- [ ] 我能逐行讀懂 startup_xxx.s
- [ ] 我能說出 `_sidata` / `_sdata` / `_edata` 來自哪、各代表什麼
- [ ] 我能解釋 LMA 與 VMA 的差別
- [ ] 我能寫一個沒有 newlib 的最小 Reset_Handler
- [ ] 我聽過 newlib-nano 知道為什麼嵌入式用它
- [ ] 我能用 weak symbol idiom 把預設 ISR 換掉

下一章看 linker script — `MEMORY`、`SECTIONS`、`KEEP`、所有 startup 仰賴的「另一半魔法」。

→ [Ch 11 Linker script 全解](./11-linker-script.md)
