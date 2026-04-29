# Ch 11 — Linker script 全解

> 目標：徹底拆懂 GNU ld linker script 的每一個關鍵字（MEMORY、SECTIONS、AT、KEEP、PROVIDE、>），以及 Cortex-M / Cortex-A bare-metal 各自怎麼寫一份能用的 ld script。

## linker 在做什麼？

```
.o files
   ├─── 收集所有 input section (.text、.data、.bss、.rodata...)
   ├─── 解析 symbol（找出每個符號最終位址）
   ├─── 把 input section 合併成 output section
   ├─── 配 output section 到 memory region
   └─── 產生最終 ELF
```

linker script 就是告訴 linker「**怎麼合併、怎麼配置、symbol 放在哪**」。

對 bare-metal，linker script 比 OS 環境**重要太多**：沒有 OS loader 來幫你決定 binary 在哪 load、`.bss` 在哪、stack 在哪 — 全部自己刻。

## 最簡 ld script

```ld
ENTRY(Reset_Handler)

MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 192K
}

SECTIONS
{
    .isr_vector :
    {
        KEEP(*(.isr_vector))
    } > FLASH

    .text :
    {
        *(.text*)
        *(.rodata*)
        _etext = .;
    } > FLASH

    .data : AT (_etext)
    {
        _sdata = .;
        *(.data*)
        _edata = .;
    } > RAM

    _sidata = LOADADDR(.data);

    .bss :
    {
        _sbss = .;
        *(.bss*)
        *(COMMON)
        _ebss = .;
    } > RAM

    _estack = ORIGIN(RAM) + LENGTH(RAM);
}
```

逐塊看。

## ENTRY

```ld
ENTRY(Reset_Handler)
```

告訴 linker「**入口點符號是這個**」。寫進 ELF header 的 `e_entry` 欄位。對 bare-metal 不是嚴格必要（CPU 從 vector table 開始，不看 ELF entry），但讓 GDB `start` 命令知道哪裡停。

## MEMORY 區塊

```ld
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 192K
}
```

定義 memory regions 與屬性：

- **r** = readable
- **w** = writable
- **x** = executable
- **a** = allocatable（必有）
- **!** = 反向（如 `(rxw!a)`）

實務上：
- **flash 是 `rx`（不可寫）**：應用層運行不應寫 flash
- **RAM 是 `rwx`**：可讀可寫可執行（雖然多數時候不在 RAM 執行）

linker 用屬性檢查「能不能放這個 section」。例如 `.text`（code）需要 `x`，放進 `(rw)` region 會報錯。

## SECTIONS 與 input/output section

```ld
.text :
{
    *(.text*)
    *(.rodata*)
} > FLASH
```

意思：「建立一個 output section 叫 `.text`，內容是 **所有 input file 的 `.text*` 與 `.rodata*` section** 合在一起，放在 FLASH region。」

`*` 是 wildcard：`.text*` match `.text`、`.text.foo`、`.text.bar`。GCC 在 `-ffunction-sections` 下會把每個函式編成獨立 `.text.funcname`，配 `--gc-sections` 能 GC 掉沒用的函式 — 這個 idiom 救了多少嵌入式 bin size。

## AT 與 LMA

`AT(addr)` 指定 **load address (LMA)**，與 virtual address (VMA) 分開：

```ld
.data : AT (_etext)
{
    _sdata = .;
    *(.data*)
    _edata = .;
} > RAM
```

- **VMA**：`.data` 在 RAM 中的 runtime 位址（從 `> RAM` 來的，這裡會配在 RAM 起點）
- **LMA**：`.data` 在 flash 中的儲存位置（`AT(_etext)` 接在 `.text` 後面）

Reset_Handler 的工作：把 LMA 拷到 VMA。startup code 用：

```c
extern uint32_t _sdata, _edata;       // VMA
extern uint32_t _sidata;              // LMA, 由 LOADADDR(.data) 來
```

`LOADADDR(.data)` 是 linker 提供的 expression，回傳 .data 的 LMA。

## KEEP：別 GC 我

```ld
.isr_vector :
{
    KEEP(*(.isr_vector))
} > FLASH
```

`KEEP` 告訴 linker：「**就算用 `--gc-sections` 也別把這個 section 拿掉**」。

vector table 沒有任何 C 函式呼叫它（HW 直接跳），linker 看不到 reference 會誤判沒用，被 GC 掉就完蛋。`KEEP` 防這個。

同樣的道理：`__attribute__((constructor))` 函式、`.init_array`、特定 hook 都要 `KEEP`。

## > region：放在哪

```ld
.text : { ... } > FLASH
```

`> FLASH` 把 output section 配到 FLASH region。配的位址會自動算（從 region 起點開始接續累加）。

可以加多個 region 控制：

```ld
.data : AT > FLASH
{
    *(.data*)
} > RAM
```

`AT > FLASH` 與 `AT(addr)` 對應：用 `> region` 而非具體位址，linker 會在 FLASH region 內自動找位置放 LMA。

## 對齊

所有 section 預設按某對齊起始。手動指定：

```ld
.text :
{
    . = ALIGN(4);            /* 把當前位置 align 到 4-byte */
    *(.text*)
    . = ALIGN(4);
    _etext = .;
} > FLASH
```

`. = ALIGN(N)` 是 location counter 操作，把當前位置進到 N 的倍數。AArch64 通常 16-byte 對齊（搭配 STP/LDP）。Cortex-M 普通 4-byte 即可。

## 提供 stack 與 heap

bare-metal stack 通常放 RAM 末端：

```ld
SECTIONS
{
    /* ... 所有正式 section ... */

    .bss :
    {
        _sbss = .;
        *(.bss*)
        _ebss = .;
        _end = .;             /* heap 從這裡開始 */
    } > RAM

    /* heap 不分配實體 section，只 reserve 空間 */
    .heap (NOLOAD) :
    {
        . = ALIGN(8);
        _sheap = .;
        . = . + 0x1000;       /* 4 KB heap */
        _eheap = .;
    } > RAM

    _estack = ORIGIN(RAM) + LENGTH(RAM);   /* stack top */
    PROVIDE(_stack_top = _estack);
    PROVIDE(_stack_size = 0x2000);          /* 8 KB stack */
}
```

幾個關鍵字：

- **`(NOLOAD)`**：這個 section 不放實際資料到 ELF 的 LOAD segment（heap 不需要 init data）
- **`PROVIDE(sym = expr)`**：定義 symbol 但**只在 source 裡沒定義時才生效**（avoiding double-define）

## 多 RAM region：CCM / SRAM2 / DTCM

STM32F4 有 SRAM、CCM (Core-Coupled Memory)、SRAM2、backup SRAM，各別物理區塊：

```ld
MEMORY
{
    FLASH    (rx)  : ORIGIN = 0x08000000, LENGTH = 1024K
    SRAM     (rwx) : ORIGIN = 0x20000000, LENGTH = 128K
    CCMRAM   (rwx) : ORIGIN = 0x10000000, LENGTH = 64K
    SRAM2    (rwx) : ORIGIN = 0x2001C000, LENGTH = 16K
}

SECTIONS
{
    /* 把高效能變數放 CCMRAM */
    .ccmram_data :
    {
        *(.ccmram_data*)
    } > CCMRAM

    /* DMA buffer 放 SRAM2（DMA 不能存 CCMRAM） */
    .sram2_buf (NOLOAD) :
    {
        *(.sram2_buf*)
    } > SRAM2

    /* 其他 ... */
}
```

C 端：

```c
__attribute__((section(".ccmram_data")))
int hot_var = 42;
```

這個技巧對性能敏感的嵌入式專案重要 — DMA 對 CCMRAM 不可達是真實踩雷點。

## Cortex-A 的 linker script

Cortex-A bare-metal（QEMU virt）寫法：

```ld
ENTRY(_start)

MEMORY
{
    RAM (rwx) : ORIGIN = 0x40000000, LENGTH = 128M
}

SECTIONS
{
    . = 0x40000000;

    .text : ALIGN(4096)
    {
        *(.text.boot)        /* boot code 放最前 */
        *(.text*)
    } > RAM

    .rodata : ALIGN(4096) { *(.rodata*) } > RAM

    .data : ALIGN(4096) { *(.data*) } > RAM

    .bss : ALIGN(4096)
    {
        _bss_start = .;
        *(.bss*)
        *(COMMON)
        _bss_end = .;
    } > RAM

    . = ALIGN(4096);
    .stack (NOLOAD) :
    {
        . = . + 0x10000;      /* 64 KB stack */
        _stack_top = .;
    } > RAM
}
```

差別：

- **沒有分 FLASH / RAM**：QEMU 從同一個 RAM image 啟動
- **page 對齊**（4 KB）**很重要**：MMU 設定 page table 時 section 邊界要對應 page boundary
- 沒有 `.data` LMA 拷貝那套（image 直接 load 到 RAM）

Practice B 會用到這份 script。

## section 順序的隱藏陷阱

想想這個錯：

```ld
.text : { *(.text*) } > FLASH
.data : { *(.data*) } > RAM       /* 沒寫 AT */
```

少了 `AT(_etext)`，`.data` 的 LMA 會直接接 RAM 起點 — 但 `.data` 的初值在哪？**ELF 沒地方塞**！QEMU 會把 RAM 裡的 `.data` 區清零，反正你 binary 裡的 `.data` 內容都丟了。

正確要 `AT > FLASH` 把 `.data` 的初值塞進 flash image。

## 一個常見誤解

「我能不能不用 linker script？」

可以但不建議。GNU ld 內建一個 default linker script（`ld --verbose` 看），但它預設給 OS 環境（Linux user space），不適合 bare-metal。bare-metal 一定要寫自己的。

OS 環境（user space app）也用 default script，但那個 script 假設你能呼叫 OS syscall、有 dynamic loader、`/lib/ld-linux.so` 處理 .data 拷貝。bare-metal 沒這些。

## 自我檢核

- [ ] 我能寫一份可用的 Cortex-M linker script
- [ ] 我能解釋 MEMORY、SECTIONS、`>`、`AT()`、`KEEP()` 各自做什麼
- [ ] 我能說出 LMA 與 VMA 的差別
- [ ] 我能用 `LOADADDR(.data)` 取出 LMA 給 startup 用
- [ ] 我能把 hot 變數放到 CCMRAM
- [ ] 我能比較 bare-metal Cortex-M 與 Cortex-A 的 linker script 差異

下一章看 NVIC — Cortex-M 的中斷控制器，優先權、tail-chaining、late arrival 全套。

→ [Ch 12 NVIC：優先權、tail-chaining、late arrival](./12-nvic.md)
