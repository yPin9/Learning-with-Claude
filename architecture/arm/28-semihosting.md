# Ch 28 — Semihosting

> 目標：搞懂 semihosting — 那個讓 bare-metal 韌體能 `printf`、`fopen`、`exit` 的機制。原理（BKPT 0xAB trick）、ARM-Cortex-M 與 AArch64 的不同、newlib 怎麼整合、為什麼會慢、什麼時候不該用。

## semihosting 解決什麼問題

bare-metal 韌體無法 syscall（沒 OS），但 debug 時想要：

- printf 印 log
- 寫檔案到 host
- 從 host 讀資料當 input
- exit() 回 host

正常方法：寫一個 UART driver 接 printf — 占記憶體、占 USART 周邊、要配 baud。**semihosting 用 debug interface 借 host 的 OS**：韌體呼叫一個特殊指令，host 端 debugger / qemu 接到，代你做 syscall（write, open, read, ...）。

## 原理：BKPT 0xAB

Cortex-M 上 semihosting 用 `BKPT #0xAB`：

```c
int semihost_call(int op, void *arg) {
    register int r0 __asm__("r0") = op;
    register void *r1 __asm__("r1") = arg;
    asm volatile("bkpt #0xAB" : "+r"(r0) : "r"(r1) : "memory");
    return r0;
}
```

CPU 執行 `BKPT 0xAB` → trigger debug exception → debugger（OpenOCD / qemu）在 ESR 看到 `imm = 0xAB` 知道**這是 semihost call，不是 user breakpoint** → 解析 r0 = operation, r1 = args → 代執行 → 把結果寫回 r0 → debugger resume。

整個過程對韌體來說「**像同步 syscall**」。

AArch64 用 `HLT #0xF000`（不同 immediate），語意一樣。

## Operation 編號

```
SYS_OPEN      = 0x01    // 開檔
SYS_CLOSE     = 0x02
SYS_WRITEC    = 0x03    // 寫一個字元到 console
SYS_WRITE0    = 0x04    // 寫一個 null-terminated 字串
SYS_WRITE     = 0x05    // 寫一段 buffer 到 file
SYS_READ      = 0x06
SYS_READC     = 0x07    // 從 console 讀一個字元
SYS_ELAPSED   = 0x30    // 取系統時間
SYS_EXIT      = 0x18    // 結束
... 一共 30+ operations
```

完整列表在 ARM 的 *Semihosting for AArch32 and AArch64* 文件。

## 簡單範例：手刻一個 putc

```c
static inline void semihost_putc(char c) {
    register uint32_t r0 __asm__("r0") = 0x03;   // SYS_WRITEC
    register void *r1 __asm__("r1") = &c;
    asm volatile("bkpt #0xAB" : "+r"(r0) : "r"(r1) : "memory");
}

void puts(const char *s) {
    while (*s) semihost_putc(*s++);
}

int main(void) {
    puts("hello via semihosting!\n");
    while (1);
}
```

**沒有 UART、沒有 baud、沒有 GPIO 配置** — 只要 OpenOCD 開了 semihosting，host stdout 直接看到 hello。

## 啟用 semihosting

OpenOCD 端：

```
(gdb) monitor arm semihosting enable
```

或 `.cfg` 內：

```tcl
arm semihosting enable
```

QEMU 啟動加 `-semihosting`：

```bash
qemu-system-arm -M mps2-an385 -kernel firmware.elf -nographic -semihosting
```

啟用後 semihost call 會被截獲。**沒啟用** → BKPT 0xAB 變普通 breakpoint，韌體 hang 在那。

## 編譯支援：`--specs=rdimon.specs`

如果你用 newlib（用 `printf`），要告訴 linker 把 `_write` 等 syscall stub 連到 semihosting 版：

```bash
arm-none-eabi-gcc \
    -mcpu=cortex-m4 -mthumb \
    --specs=rdimon.specs \
    main.c -o firmware.elf
```

`rdimon.specs` 帶來 **rdimon library**，提供：

- `_write` → SYS_WRITE
- `_read` → SYS_READ
- `_open` → SYS_OPEN
- `_close` → SYS_CLOSE
- `_lseek`, `_fstat`, `_isatty`, `_kill`, ...

initialise 也要 call 一次：

```c
extern void initialise_monitor_handles(void);

int main(void) {
    initialise_monitor_handles();   // semihosting init
    printf("hello, %s\n", "world");
    return 0;
}
```

之後 `printf`、`fopen`、`scanf` 全部能用，**像在 OS 上寫程式**。

## SYS_EXIT 與 exit code

```c
int main(void) {
    do_test();
    if (failed) {
        exit(1);   // → SYS_EXIT
    }
    exit(0);
}
```

QEMU 接到 SYS_EXIT 會**真的 exit**（with the exit code），可在 CI 用：

```bash
qemu-system-arm ... -kernel test.elf
echo $?              # 看 exit code，1 = test failed
```

**寫 unit test on bare-metal 用 semihosting + QEMU 是經典 idiom**。Zephyr、TF-A、OP-TEE 的 test rig 都用這個。

## 為什麼很慢

每個 semihost call：

1. CPU 跑 BKPT → exception
2. CPU halt，等 host 處理
3. host 透過 SWD 讀 ESR / r0 / r1
4. host 解析、執行 host syscall
5. host 寫回 r0
6. host 透過 SWD resume CPU

**SWD 通訊是瓶頸**：每個 round-trip 數 ms。`printf("%d\n", x)` 內部多次 SYS_WRITEC，可能要幾十 ms。

對比 ITM printf：register write，幾 ns。

**semihosting 適合 bring-up、debug print、test rig 的 exit code，不適合性能敏感 / 高頻 log**。

## QEMU vs OpenOCD：行為差異

QEMU 有自己 semihost 處理（軟體模擬），**QEMU 跑 semihost 接近原速**（不需要 SWD 來回）。所以單元測試 + QEMU 跑 thousand of test 還可接受。

實機 + OpenOCD 慢得多。Production firmware **絕對不要留 semihost call 在裡面** — 一旦沒接 debugger，CPU 跑到 BKPT 會 hang。

## 安全考量

semihost 提供 SoC 對 host 檔案系統的存取。**生產韌體留 semihost call = 安全洞** — 攻擊者接 SWD 後可任意讀 / 寫 host 檔案。

**Production code 必須 disable semihosting**：

- 不用 `--specs=rdimon.specs`
- 移除 `initialise_monitor_handles()`
- 改用 UART / ITM driver

## AArch64 上的 semihosting

AArch64 用 `HLT #0xF000`：

```c
register uint64_t x0 __asm__("x0") = SYS_WRITEC;
register const char *x1 __asm__("x1") = &c;
asm volatile("hlt #0xf000" : "+r"(x0) : "r"(x1));
```

operation 編號跟 32-bit 兼容，但 register 用 X0 / X1 而非 R0 / R1。

QEMU virt aarch64 `-semihosting` 同樣有效，可從 EL1 / EL2 / EL3 任一 EL 呼叫（QEMU 都接住）。

## 一個常見誤解

「semihosting 是不是只有 ARM 有？」

不只 ARM。MIPS、RISC-V 都有類似機制。RISC-V 的 「**HTIF (Host-Target Interface)**」是 spike emulator 用的，把 magic 位址 write 解讀為 syscall。 概念類似 semihost，介面不同。

ARM 的 semihosting 因為**生態最完整**（newlib、qemu、OpenOCD 都支援），最常被 work-out 來。

## 自我檢核

- [ ] 我能寫一個用 BKPT 0xAB 的 semihost putc
- [ ] 我能說出 SYS_WRITEC vs SYS_WRITE 的差別
- [ ] 我能用 `--specs=rdimon.specs` 編出能 printf 的 firmware
- [ ] 我能用 QEMU + SYS_EXIT 寫 CI test rig
- [ ] 我能解釋為什麼 semihost 慢（與 ITM 對比）
- [ ] 我知道 production code 為什麼要 disable semihosting

下一章看 ITM/SWO trace — 比 semihosting 快百倍的「printf」+ event trace。

→ [Ch 29 ITM / SWO trace 與 printf debugging](./29-itm-swo-trace.md)
