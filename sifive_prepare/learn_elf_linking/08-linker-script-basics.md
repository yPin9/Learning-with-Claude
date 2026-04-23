# Ch 8 — Linker script 語法與心法

> 目標：能讀得懂、改得動 linker script。理解每個 `.text`/`.data`/`.bss` 放哪裡是 linker script 決定的、理解 `MEMORY` / `SECTIONS` / `PROVIDE` 這些關鍵字。這章是嵌入式 / firmware / kernel 工程師的必修。

## Linker script 是什麼

linker 的行為由 linker script 控制。預設 linker 有內建 script，處理「正常」executable。但對 baremetal / kernel / 特殊 memory layout 你要自己寫。

用 linker script 的典型場景：

- MCU：把 code 放 Flash、data 放 RAM、stack 指定位置
- kernel：讓 kernel image 從特定地址起跳（Linux `arch/riscv/kernel/vmlinux.lds.S`）
- bootloader：精細控制每個 section 的位置
- embedded hypervisor：多個獨立 program 共存於同一 binary

## 看一下預設 linker script

```bash
riscv64-unknown-elf-ld --verbose | head -200
```

會印出 250+ 行 default script。你會看到：

```
OUTPUT_FORMAT("elf64-littleriscv", ...)
OUTPUT_ARCH(riscv)
ENTRY(_start)
SECTIONS
{
    PROVIDE (__executable_start = SEGMENT_START("text-segment", 0x10000));
    . = SEGMENT_START("text-segment", 0x10000) + SIZEOF_HEADERS;
    .interp         : { *(.interp) }
    .note.gnu.build-id : { *(.note.gnu.build-id) }
    .hash           : { *(.hash) }
    .gnu.hash       : { *(.gnu.hash) }
    .dynsym         : { *(.dynsym) }
    .dynstr         : { *(.dynstr) }
    ...
}
```

這些規則控制「哪個 section 放哪」。稍後會拆。

## 四個核心元素

一個 linker script 主要有四類 statement：

1. **設定指令**：`ENTRY`, `OUTPUT_FORMAT`, `OUTPUT_ARCH`, `SEARCH_DIR`
2. **MEMORY**：宣告 memory region
3. **SECTIONS**：安排 section 放哪（最重要）
4. **其他**：`ASSERT`, `EXTERN`, `INPUT`, `GROUP`

## ENTRY — 指定 entry point

```
ENTRY(_start)
```

告訴 linker：ELF header 的 `e_entry` 填 `_start` 的地址。loader 從這裡開始執行。

baremetal / kernel 必寫。userspace 沒寫的話預設 `_start`（或對應 lib 定義的）。

## MEMORY — 描述硬體 memory layout

```
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x20000000, LENGTH = 1024K
    RAM   (rwx) : ORIGIN = 0x80000000, LENGTH = 64K
}
```

宣告兩塊 memory：

- `FLASH`：起始 0x20000000、1 MiB、read-execute
- `RAM`：起始 0x80000000、64 KiB、read-write-execute

每個 region 有：

- **名稱**：後面 SECTIONS 可以引用
- **屬性**：r / w / x / a / i / l
- **ORIGIN**：起始 address
- **LENGTH**：大小

沒 MEMORY 宣告時 linker 假設一大塊連續的 VA 空間。userspace executable 通常不用 MEMORY。

## SECTIONS — 主戰場

```
SECTIONS
{
    . = 0x80000000;           /* 設定 location counter */

    .text : {                  /* 定義 output section .text */
        *(.text.init)          /* 所有 .o 裡的 .text.init 先放 */
        *(.text*)              /* 然後所有 .text* */
    } > FLASH                  /* 這個 section 放 FLASH region */

    .rodata : {
        *(.rodata*)
    } > FLASH

    .data : {
        *(.data*)
    } > RAM AT > FLASH         /* VMA=RAM, LMA=FLASH (見下文) */

    .bss : {
        *(.bss*)
        *(COMMON)
    } > RAM
}
```

拆解：

### `.`：location counter

`.` 是當前地址。`. = 0x80000000;` 設起始。每個 `*(...)` assign 後 `.` 自動前進。

你可以手動調：

```
. = ALIGN(16);            /* 對齊到 16 byte */
. += 0x100;               /* 跳 256 byte */
_stack_top = .;            /* 記當前位置為 stack top */
```

### `*(.text)` 萬用符

- `*(.text)`：所有 input `.o` 的 `.text` section
- `a.o(.text)`：只有 a.o 的 `.text`
- `*(.text.*)`：所有 `.text.` 開頭的 section（對 `-ffunction-sections` 有用）
- `KEEP(*(.isr_vector))`：即使 `--gc-sections` 也不砍

### `> REGION`：指派到 memory region

`.text : { ... } > FLASH` 意思是「這個 output section 放 FLASH region」。linker 會自動把 `.` 設在 FLASH 範圍內、檢查 size 有沒有超出 LENGTH。

### `AT > REGION`：VMA vs LMA

一個 section 有兩個地址：

- **VMA (Virtual Memory Address)**：runtime 時的地址
- **LMA (Load Memory Address)**：被 "load" 到哪（檔案位置 / Flash 位置）

多數 userspace 情況 VMA = LMA。但在嵌入式：

```
.data : { *(.data*) } > RAM AT > FLASH
```

意思：

- VMA = RAM（runtime 時 `.data` 在 RAM 裡）
- LMA = FLASH（開機時從 Flash 拿到）

**開機時 startup code 要把 `.data` 從 Flash copy 到 RAM**。ELF 的 PT_LOAD 會標記 LMA，loader / bootloader 據此 copy。

這是所有 MCU firmware 的基本 pattern。

## PROVIDE — 條件性定義 symbol

```
PROVIDE(__bss_start = .);
```

意思：「如果其他地方沒人定義 `__bss_start`，就由我定義」。如果 C code 裡有 `extern char __bss_start[];` 沒人定義，這裡頂上；如果 C 裡 `char __bss_start[100];` 已經有了，這裡不動。

比較：

- `__bss_start = .`：強制定義（multiple definition 會錯）
- `PROVIDE(__bss_start = .)`：沒人定義才我定
- `PROVIDE_HIDDEN(__bss_start = .)`：同上但 symbol 不 export

startup code 的 `memset(&__bss_start, 0, &__bss_end - &__bss_start)` 依賴的就是 linker 定義的這兩個符號。

## 完整的 MCU 範例

```
ENTRY(_start)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x20000000, LENGTH = 512K
    RAM   (rw)  : ORIGIN = 0x80000000, LENGTH = 32K
}

SECTIONS
{
    /* Code + read-only data 在 Flash */
    .text : {
        KEEP(*(.isr_vector))       /* interrupt vector 必須在起點 */
        *(.text.init)              /* init code */
        *(.text*)
        *(.rodata*)

        . = ALIGN(4);
        __init_array_start = .;
        KEEP(*(.init_array*))
        __init_array_end = .;
    } > FLASH

    /* data 的 VMA 在 RAM、LMA 在 Flash */
    .data : {
        . = ALIGN(4);
        __data_start = .;
        *(.data*)
        . = ALIGN(4);
        __data_end = .;
    } > RAM AT > FLASH
    __data_lma = LOADADDR(.data);

    /* BSS 放 RAM，佔空間但不佔 Flash */
    .bss : {
        . = ALIGN(4);
        __bss_start = .;
        *(.bss*)
        *(COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM

    /* stack 放 RAM 尾端 */
    .stack : {
        . = ALIGN(16);
        . += 0x1000;              /* 4 KiB stack */
        _stack_top = .;
    } > RAM
}
```

startup code 會這樣用：

```c
extern char __data_start, __data_end, __data_lma;
extern char __bss_start, __bss_end;

void _start(void) {
    // 1. copy .data from Flash to RAM
    char *src = &__data_lma;
    char *dst = &__data_start;
    while (dst < &__data_end) *dst++ = *src++;

    // 2. zero .bss
    char *p = &__bss_start;
    while (p < &__bss_end) *p++ = 0;

    // 3. call init_array (C++ static constructors / C constructor attr)
    for (fptr *f = &__init_array_start; f < &__init_array_end; f++) (*f)();

    // 4. main
    int r = main(argc, argv);
    exit(r);
}
```

**這個 pattern 在所有 MCU 都長這樣**。唯一變化是 Flash / RAM 地址跟 interrupt vector 的 layout。

## 內建函式

linker script 裡可以用：

- `ALIGN(exp, align)`：把 exp 對齊
- `ALIGN(align)`：對齊 `.` 本身
- `LOADADDR(section)`：取得 section 的 LMA
- `SIZEOF(section)`：取 size
- `ADDR(section)`：取 VMA
- `DEFINED(symbol)`：symbol 是否已定義

進階：

- `ORIGIN(region)`、`LENGTH(region)`
- `ABSOLUTE(exp)`：強制變絕對值
- `CONSTANT(MAXPAGESIZE)`：系統常數

## 注意：Linker script 的條件

可以做簡單條件判斷：

```
_stack_size = DEFINED(_stack_size) ? _stack_size : 0x1000;
```

但不能做完整 if / for。複雜 logic 要在外部（makefile / python 產生 linker script）。

## `INPUT` 與 `GROUP`

```
INPUT(a.o b.o c.o)              /* 強制把這些 .o 加入 link */
GROUP(-la -lb)                  /* 相當於 --start-group -la -lb --end-group */
```

少直接用，但 default script 裡會看到。

## 常見 linker script 陷阱

### 陷阱 1：忘記 `KEEP`

```
.isr_vector : { *(.isr_vector) }
```

開 `--gc-sections` 後，沒被引用的 section 會被砍 —— 包括這個 vector table。但 vector table 沒「被 C code 呼叫」，只是 hardware 讀。砍了就開機失敗。

正確：

```
.isr_vector : { KEEP(*(.isr_vector)) }
```

### 陷阱 2：.data 沒設 AT > FLASH

```
.data : { *(.data*) } > RAM    /* 錯：沒 LMA */
```

這樣 LMA = VMA = RAM。但開機時 RAM 是空的、data 沒地方 copy。應該：

```
.data : { *(.data*) } > RAM AT > FLASH
```

### 陷阱 3：沒預留 stack

Linker script 不自動配 stack。要自己：

```
.stack : { . += 0x1000; _stack_top = .; } > RAM
```

然後 startup code `la sp, _stack_top`。

### 陷阱 4：ORDER 錯

```
.text : { *(.text*) *(.text.init) }
```

`.text.init` 要最先！否則 interrupt vector 沒在 0x0。寫：

```
.text : {
    *(.text.init)
    *(.text*)
}
```

### 陷阱 5：section attribute 跟 placement 衝突

C code：

```c
__attribute__((section(".mydata"))) int x = 42;
```

Linker script 忘了 `.mydata` → 變成 `orphan section`、linker 自己決定位置（可能在奇怪地方）。**最好明確在 linker script 寫 `.mydata : { ... }`**。

## `info ld` 是你的朋友

```
info ld
```

GNU LD manual。超詳細。linker script 所有語法都在這。**嚴肅做 firmware / kernel 的工程師應該讀過一次**。

## 動手練習

1. 用 `ld --verbose` 印預設 linker script，找出 `.text` / `.rodata` / `.data` 的 placement 規則。
2. 寫一個最簡 linker script 放 code 在 0x80000000，跑 hello world（跟 practice-b 對照）。
3. 改寫 script 把 `.text` 放 Flash、`.data` 放 RAM（AT > Flash），寫 startup code 做 copy。
4. 用 `-Wl,-T,my.lds -Wl,--verbose` 看 linker 怎麼解讀你的 script。
5. 把某個 function 用 `__attribute__((section(".hot")))` 標記，在 linker script 中把 `.hot` 放最前面（優化 icache 命中）。

## 常見誤會

1. **「Linker script 只有嵌入式才用」**：錯。Linux kernel / U-Boot / glibc 都有自己 linker script。userspace 用 default 但仍存在。
2. **「Linker script 很複雜，我學不會」**：基本語法一天就學會。進階 case 慢慢累積。
3. **「一個 linker script 可以給所有 arch」**：不。通常每個 arch 一個 script（`vmlinux.lds.S` 每 arch 都有）。
4. **「VMA 必須 = LMA」**：不。嵌入式常見 VMA ≠ LMA（Flash 載入、RAM 執行）。
5. **「用 linker script 一定要寫 MEMORY」**：不。userspace 用 VA 連續空間不用 MEMORY。

## 自我檢核

- [ ] 我能解釋 MEMORY / SECTIONS / PROVIDE / ENTRY 各自用途
- [ ] 我能寫一個簡單 MCU linker script 含 Flash + RAM
- [ ] 我知道 VMA vs LMA 的差異以及 `AT > REGION` 怎麼用
- [ ] 我能用 `KEEP` 保護不想被 `--gc-sections` 砍的 section
- [ ] 我能處理「C code 宣告 section, linker script 沒接」的 orphan 問題

下一章專攻 linker script 的深坑 — `MEMORY` / `SECTIONS` / `PROVIDE` 的實戰陷阱。這些坑我踩過，你看完至少會少踩 5 個。

→ [Ch 9 MEMORY / SECTIONS / PROVIDE 的陷阱](./09-linker-script-gotchas.md)
