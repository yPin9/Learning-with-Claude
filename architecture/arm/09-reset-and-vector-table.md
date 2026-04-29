# Ch 9 — Reset 流程與向量表

> 目標：搞清楚 Cortex-M 從上電到執行 `main()` 中間每一個 step。向量表的格式、那個讓人困惑的 `+1`、SystemInit、Reset_Handler 各自做什麼。

## 上電後的第一個 0.0001 秒

```
1. 電源 / reset 訊號釋放
2. CPU 從 0x00000000 讀 4 bytes → 載入 MSP
3. CPU 從 0x00000004 讀 4 bytes → 載入 PC
4. 開始執行 PC 指向的程式 = Reset_Handler
```

就這麼簡單。**不像 x86 從 BIOS / UEFI 走一大圈**，Cortex-M 兩次記憶體讀就準備好了 — 預設下沒有 boot ROM、沒有 firmware blob 在中間。

但 STM32 / NXP 這類「真實」MCU 通常有 system memory boot ROM，會根據 BOOT pin 決定 reset 時是從 flash、system memory、SRAM 哪裡開始 — 那是 SoC 廠把 0x00000000 重映射到不同位置，從 CPU 看仍然是「讀 0x0、再讀 0x4」。

## 向量表格式

```
Address      Content (4 bytes each)
─────────    ──────────────────────
0x00000000   Initial MSP value
0x00000004   Reset_Handler 位址
0x00000008   NMI_Handler 位址
0x0000000C   HardFault_Handler 位址
0x00000010   MemManage_Handler 位址
0x00000014   BusFault_Handler 位址
0x00000018   UsageFault_Handler 位址
0x0000001C   reserved
0x00000020   reserved
0x00000024   reserved
0x00000028   reserved
0x0000002C   SVCall_Handler 位址
0x00000030   DebugMon_Handler 位址
0x00000034   reserved
0x00000038   PendSV_Handler 位址
0x0000003C   SysTick_Handler 位址
0x00000040   IRQ0 Handler                  ← 開始外部中斷
0x00000044   IRQ1 Handler
...          (跟 NVIC 中斷數量決定長度)
```

每個 entry 是 **handler 的位址**，**最低 bit 必須是 1**（標 Thumb mode），否則 CPU 會 fault。

C 寫法：

```c
extern uint32_t _stack_top;     // linker 提供
void Reset_Handler(void);
void Default_Handler(void);
void SysTick_Handler(void) __attribute__((weak, alias("Default_Handler")));

__attribute__((section(".isr_vector")))
void (* const vector_table[])(void) = {
    (void (*)(void))(&_stack_top),    // [0] MSP 初值
    Reset_Handler,                    // [1] Reset
    Default_Handler,                  // [2] NMI
    Default_Handler,                  // [3] HardFault
    /* ... */
    SysTick_Handler,                  // [15] SysTick
    /* IRQ0... */
};
```

幾個重點：

- **第 0 個 entry 是 MSP 初值，不是 handler**（其他都是函式指標）
- **函式指標自動帶 +1**：C 函式指標被取位址時，編譯器自動 OR 1（因為 Cortex-M 永遠 Thumb，指標格式就是 `addr | 1`）。手寫 asm 要自己 OR
- `weak` + `alias("Default_Handler")` 是個漂亮的 trick — 沒實作的 ISR 自動指向 default。實作時直接定義同名函式就會 override

## 那個 +1 的故事

剛接 Cortex-M 的人常踩這個坑。手寫向量表用 `.word`：

```asm
.word 0x20020000          ; MSP
.word Reset_Handler       ; 假設 Reset_Handler 在 0x08000040
                          ; 這裡寫的位址應該是 0x08000041 才對！
```

如果 `.word` 給的是純 0x08000040，CPU 跳過去會 fault（因為 bit[0] = 0 表示 ARM mode，但 Cortex-M 沒 ARM mode）。GNU as 在符號取位址時自動處理這個 — `.word Reset_Handler` 會自動 emit 0x08000041。

但你要是自己組湊位址：

```asm
.word 0x08000040          ; ❌ 會 fault
.word 0x08000041          ; ✅ 正確
```

## Reset_Handler 做什麼？

最小版：

```c
extern uint32_t _sidata, _sdata, _edata;   // .data 的 LMA、VMA 起、VMA 終
extern uint32_t _sbss, _ebss;              // .bss 的範圍
void main(void);
void SystemInit(void);

void Reset_Handler(void) {
    // 1. 把 .data 從 flash 拷到 SRAM
    uint32_t *src = &_sidata;
    uint32_t *dst = &_sdata;
    while (dst < &_edata) *dst++ = *src++;

    // 2. 把 .bss 清零
    dst = &_sbss;
    while (dst < &_ebss) *dst++ = 0;

    // 3. 系統初始化（PLL、FPU 等）
    SystemInit();

    // 4. 進 main
    main();

    // 5. main 不該返回，但萬一返回就 hang
    while (1);
}
```

每一步都不可省：

- **`.data` 拷貝**：全域 / static 變數有初值的（`int x = 5;`）儲存在 flash 的 LMA，但 runtime 要在 SRAM 用，所以要拷
- **`.bss` 清零**：全域 / static 沒初值的 C 標準保證為 0
- **SystemInit**：開 FPU、設 PLL（從 4 MHz HSI 拉到 168 MHz）、設 vector table 位置

## SystemInit：CMSIS 提供的鉤子

CMSIS 規定每顆 Cortex-M chip 提供 `SystemInit()`，內容由 SoC 廠寫。STM32F4 的精簡版：

```c
void SystemInit(void) {
    // 開 FPU（Cortex-M4 才有）
    SCB->CPACR |= ((3UL << 10*2) | (3UL << 11*2));

    // 設 vector table base（vendor 有時把 vector table 搬到 SRAM）
    SCB->VTOR = FLASH_BASE;  // 0x08000000

    // PLL 配置（拉到目標頻率）
    // ... 這部分非常 chip-specific
}
```

**重要**：`Reset_Handler` 是先拷 `.data` 還是先呼叫 `SystemInit`？答案要看 SystemInit 用不用初值化的全域變數。安全做法 = **先拷 `.data` 再呼 SystemInit**。CMSIS 模板大多是這個順序。

## VTOR：搬移向量表

`SCB->VTOR`（Vector Table Offset Register）讓你執行期把向量表搬到別的位置：

```c
SCB->VTOR = 0x20000000;   // 把向量表搬到 SRAM
```

用途：

- **bootloader 切換到 application**：bootloader 用 0x08000000 的 vector table，跳到 app 之前把 VTOR 改到 app 的位置
- **runtime patching**：把 vector table 搬到 SRAM 後可以動態改 ISR 指標
- **支援 OTA 更新**

VTOR 必須 **N-byte 對齊**（N = 取下一個 2 的次方且 ≥ 0x80），通常 0x100 或 0x200 對齊。

## QEMU 跑一個最簡 reset

```c
// minimal.c
extern uint32_t _stack_top;
void main(void);

void Reset_Handler(void) {
    main();
    while (1);
}

void Default_Handler(void) { while (1); }

__attribute__((section(".isr_vector"), used))
void (* const vector_table[])(void) = {
    (void (*)(void))(&_stack_top),
    Reset_Handler,
    Default_Handler, /* NMI */
    Default_Handler, /* HardFault */
};

void main(void) {
    volatile int x = 42;
    while (1) x++;
}
```

linker script `minimal.ld`：

```ld
MEMORY {
    FLASH (rx) : ORIGIN = 0x00000000, LENGTH = 256K
    RAM   (rwx): ORIGIN = 0x20000000, LENGTH = 64K
}

SECTIONS {
    .isr_vector : { KEEP(*(.isr_vector)) } > FLASH
    .text       : { *(.text*) }            > FLASH
    .data       : { *(.data*) }            > RAM AT > FLASH
    .bss        : { *(.bss*) }             > RAM
    _stack_top  = ORIGIN(RAM) + LENGTH(RAM);
}
```

編譯與跑：

```bash
arm-none-eabi-gcc -mcpu=cortex-m3 -mthumb -nostdlib -T minimal.ld minimal.c -o minimal.elf
qemu-system-arm -M mps2-an385 -kernel minimal.elf -nographic -S -gdb tcp::1234
# 另一個終端
gdb-multiarch minimal.elf
(gdb) target remote :1234
(gdb) b main
(gdb) c
```

成功的話 GDB 會停在 main 第一行。**這就是這門課要教你做的最小證明**。

## 一個常見誤解

「為什麼 vector table 一定要在 0x00000000？我能不能把它放在別處？」

**Boot 時必須在 0x00000000**（CPU 寫死從這裡讀 MSP / Reset）。**Boot 完可以搬**：用 VTOR 切到任何位置，等於改 base address。

但很多 SoC 把 0x00000000 alias 到 flash 的某個位置（例如 STM32 的 0x08000000 flash 也 alias 到 0x00000000），所以「flash 開頭就是 vector table」是常見。**linker script 要把 `.isr_vector` 放在 ORIGIN(FLASH)**，不要放別處。

## 自我檢核

- [ ] 我能說出 reset 後 CPU 讀的前兩個 4-byte 是什麼
- [ ] 我能列出 vector table 前 16 個 entry 的順序
- [ ] 我能解釋為什麼 handler 位址要 `+1`
- [ ] 我能寫出 Reset_Handler 的最小四步驟
- [ ] 我知道 VTOR 是什麼以及 bootloader 為什麼用它
- [ ] 我能用 QEMU 跑一個自己刻的最簡 Cortex-M 程式

下一章把 startup code 拆得更細 — `_sidata` `_edata` `_sbss` 是哪來的、CRT 的初始化順序、weak symbol 怎麼疊。

→ [Ch 10 Startup code 解剖](./10-startup-code.md)
