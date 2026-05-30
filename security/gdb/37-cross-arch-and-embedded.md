# Ch 37 — 跨架構與嵌入式

> **目標**：把遠端除錯（Ch 36）用到跨架構與裸機——`gdb-multiarch`/cross-gdb、用 QEMU 跑並 debug 別的架構（ARM/RISC-V）、OpenOCD + JTAG/SWD debug 真實 MCU、以及 kernel 除錯（QEMU+kgdb）的入門。學完你能 debug 任何架構、任何裝置上的程式，從 user space 一路到裸機。

> **環境**：GDB 13/14、gdb-multiarch、qemu-user / qemu-system、OpenOCD（選配硬體）。Linux x86_64 host。

## 為什麼跨架構與嵌入式是獨立難題

到目前你 debug 的都是「本機 x86-64 user space」。但真實世界充滿別的：

- **跨架構**：你在 x86 開發，產品是 ARM 手機 / RISC-V IoT。
- **嵌入式 MCU**：跑在 Cortex-M 上、沒有 OS、透過 JTAG/SWD 線除錯。
- **裸機 / bootloader**：在 OS 起來之前的程式碼（呼應 linux_boot 課程）。
- **kernel**：debug Linux kernel 本身。

這些都建在 Ch 36 的遠端機制上（GDB 前端 + 某種 stub），但各有特殊的設定與 stub。這章是你 debug 能力的最後一塊版圖——從 app 一路打通到硬體。

## gdb-multiarch / cross-gdb

x86 的 GDB 不認得 ARM 指令。你需要懂目標架構的 GDB：

```bash
# 方式 1：gdb-multiarch（一個 GDB 支援多架構）
sudo apt install gdb-multiarch
gdb-multiarch ./arm-binary

# 方式 2：cross-gdb（toolchain 附帶，專一架構）
arm-none-eabi-gdb ./firmware.elf       # 嵌入式 ARM
riscv64-unknown-elf-gdb ./prog.elf
aarch64-linux-gnu-gdb ./arm64-binary
```

連線後常要明確設架構：

```
(gdb) set architecture armv7          # 或 aarch64 / riscv:rv64 等
(gdb) show architecture
(gdb) info registers                  # 現在顯示 ARM 暫存器（r0-r15, cpsr...）
```

跨架構時，Ch 11 學的「`$pc`/`$sp` 架構無關別名」就派上用場——你的腳本用 `$pc` 而非 `$rip` 才能跨 ARM/RISC-V。

## QEMU user-mode：跑單一外架構 binary

最簡單的跨架構 debug——用 QEMU user-mode 模擬執行一個 ARM/RISC-V 的 Linux binary，內建 gdb stub：

```bash
# 跑一個 ARM binary（QEMU 翻譯 ARM→x86 執行），-g 開 gdb stub on port 1234
qemu-arm -g 1234 ./arm-hello
# 或 qemu-riscv64 -g 1234 ./riscv-hello
```

```bash
# 另一個 terminal
gdb-multiarch ./arm-hello
(gdb) set sysroot /usr/arm-linux-gnueabihf   # ARM library 的位置
(gdb) target remote :1234                     # 連 QEMU 的 stub（就是 Ch 36 的 RSP！）
(gdb) break main
(gdb) continue
(gdb) info registers                          # ARM 暫存器
```

注意：`qemu-arm -g` 就是一個 RSP stub（Ch 36）！跨架構 debug 本質就是「連到一個模擬目標架構的 gdbserver」。你 Ch 36 的所有知識直接適用，只是 target 換成 QEMU、架構換成 ARM。

## QEMU system-mode：模擬整台機器

要 debug 整個系統（kernel、bootloader、裸機），用 QEMU system-mode 模擬整台機器：

```bash
# 模擬一台 ARM 機器，-s = gdb stub on :1234，-S = 開機就暫停等 GDB
qemu-system-aarch64 -M virt -cpu cortex-a53 \
    -kernel ./kernel.img -s -S -nographic
```

```bash
gdb-multiarch ./kernel.elf            # 帶符號的 kernel
(gdb) target remote :1234
(gdb) break start_kernel              # kernel 的進入點
(gdb) continue
```

`-s -S` 是黃金組合：`-s` 開 gdb stub、`-S` 讓 CPU 一開機就停（等你連上才跑）——你能從**第一條指令**開始 debug，看 bootloader → kernel 的完整啟動（這正是 linux_boot 課程的核心手法）。

## OpenOCD + JTAG/SWD：debug 真實 MCU

要 debug 真實的微控制器（STM32、ESP32 的 Cortex 核等），透過 JTAG 或 SWD 硬體介面。**OpenOCD** 是中間的橋——它連 debug probe（ST-Link、J-Link）並提供 gdb stub：

```
   開發機              debug probe          目標 MCU
   ┌────────┐         ┌──────────┐         ┌─────────┐
   │  GDB   │  RSP    │ OpenOCD  │ JTAG/SWD│ STM32   │
   │        │ ◄─────► │ (gdb     │ ◄─────► │ Cortex-M│
   │        │ :3333   │  stub)   │  硬體線 │         │
   └────────┘         └──────────┘         └─────────┘
```

```bash
# 1. 啟動 OpenOCD（指定 probe 與目標晶片）
openocd -f interface/stlink.cfg -f target/stm32f4x.cfg
# OpenOCD 在 :3333 提供 gdb stub

# 2. GDB 連過去
arm-none-eabi-gdb ./firmware.elf
(gdb) target extended-remote :3333
(gdb) monitor reset halt              # 透過 OpenOCD 重置並暫停 MCU
(gdb) load                            # 把 firmware 燒進 flash！
(gdb) break main
(gdb) continue
```

嵌入式特有的指令：

- `monitor <cmd>`：把指令直接傳給 OpenOCD（reset、halt、flash 操作）。`monitor reset halt` 重置並停在第一條。
- `load`：把 ELF 燒進目標 flash（OpenOCD 執行實際燒寫）。
- 硬體斷點：MCU 的 flash 不能 patch INT3（唯讀），所以**用硬體斷點**（`hbreak`，Ch 4/39）——數量受 CPU debug 單元限制（Cortex-M 通常 6 個）。

> 認識論誠實：嵌入式 debug 高度依賴具體硬體——不同 probe（ST-Link/J-Link）、不同晶片（STM32/nRF/ESP32）的 OpenOCD 設定檔不同，flash 演算法不同。本章給的是通用流程；實際做要查你的板子的具體設定。這塊和 arm / embedded 課程深度交織。

## kernel 除錯：kgdb / QEMU

debug Linux kernel 本身：

**方法一：QEMU + kernel（最方便，開發用）**

```bash
# 編一個帶 debug info 的 kernel，用 QEMU 跑 + gdb stub
qemu-system-x86_64 -kernel bzImage -append "nokaslr" -s -S ...
```
```
gdb ./vmlinux                         # 帶符號的 kernel
(gdb) target remote :1234
(gdb) break start_kernel
(gdb) continue
(gdb) lx-dmesg                        # Linux 附帶的 GDB Python 腳本！
```

注意 `nokaslr`——kernel 也有 ASLR（KASLR），debug 時要關（呼應 Ch 40）。Linux 原始碼附帶 `scripts/gdb/` 的 Python 腳本（`lx-*` 指令），是 Part 5 GDB Python API 的大型實戰範例——debug kernel 時 `lx-dmesg`、`lx-ps`、`lx-lsmod` 都很有用。

**方法二：kgdb（真機，兩台機器）**

真實機器的 kernel debug 用 kgdb（kernel 內建的 gdb stub），透過串列埠連另一台機器的 GDB。設定複雜，生產 kernel debug 用，這裡點到為止（呼應 kernel_pwn 課程）。

## 跨架構的暫存器與 ABI 差異

debug 別的架構，暫存器與 calling convention 都不同（Ch 11 是 x86-64 專屬）：

| | x86-64 | AArch64 (ARM64) | RISC-V |
|---|---|---|---|
| 參數 | rdi,rsi,rdx,rcx,r8,r9 | x0–x7 | a0–a7 |
| 回傳值 | rax | x0 | a0 |
| PC | rip | pc | pc |
| SP | rsp | sp | sp |
| 返回位址 | stack | x30 (lr) | x1 (ra) |

`info registers` 顯示對應架構的暫存器；`$pc`/`$sp` 別名跨架構通用。理解目標架構的 ABI 才能讀懂它的組語與參數傳遞（呼應 arm / riscv 課程）。

## 踩雷集錦

1. **用 x86 的 gdb debug ARM binary**：認不得指令、暫存器全錯。用 `gdb-multiarch` 或 cross-gdb。
2. **QEMU debug 忘了 `-S`**：程式一啟動就跑完，你還沒連上。`-S` 讓它等 GDB。
3. **kernel debug 忘了 `nokaslr`**：KASLR 讓 kernel 位址隨機，符號對不上。開機參數加 `nokaslr`（Ch 40）。
4. **嵌入式用軟體斷點失敗**：flash 唯讀不能 patch INT3，要 `hbreak`（硬體斷點），且數量有限。
5. **`set sysroot` 沒設**：跨架構找不到目標 library 符號。指向 cross-toolchain 的 sysroot。
6. **OpenOCD 設定檔不對**：probe 或晶片設定錯，連不上或燒不進。查你板子的具體 cfg。
7. **架構沒設對**：連上後 `set architecture` 沒設，GDB 可能誤判指令。`set architecture` 明確指定。
8. **時脈/watchdog**：嵌入式停在斷點時，硬體 watchdog 可能 reset MCU。debug 時要關 watchdog。

## 進階：再往深一層

- **semihosting**：嵌入式沒有 stdout，semihosting 讓 firmware 透過 debug 通道把 printf 輸出到 GDB/OpenOCD。`monitor arm semihosting enable`。
- **SVD 檔與週邊暫存器**：MCU 的週邊（GPIO、UART 暫存器）不是 CPU 暫存器，要靠 SVD 描述檔讓 GDB/工具認得。`svd-loader` 類插件（Part 5 Python！）讀 SVD 顯示週邊狀態。
- **Linux scripts/gdb**：`lx-*` 指令是用 GDB Python API 寫的 kernel debug 工具，是學習「為複雜系統寫 GDB 擴充」的絕佳範例（呼應 Part 5）。
- **RTOS 感知**：debug FreeRTOS/Zephyr 時，GDB 預設看不到 RTOS task（像 Go goroutine，Ch 31），需要 RTOS-aware 的擴充（OpenOCD 有 RTOS 支援）。
- **trace（ETM/ITM）**：ARM 的硬體 trace 單元能記錄執行流（類似 Intel PT），配合做 reverse 或 profiling。
- **多核 debug**：SMP 系統（多核 ARM）debug，每個核是一個「thread」，OpenOCD/GDB 要正確處理。
- **與其他課程**：這章和 architecture/arm（JTAG/OpenOCD 深入）、systems/linux_boot（QEMU+gdb 開機除錯）、security/kernel_pwn（kernel debug）深度交織——它是這些課程的 debug 基礎設施。

## 動手練習

1. 裝 `gdb-multiarch` + `qemu-user`，編一個 ARM hello（或下載 ARM binary），`qemu-arm -g 1234` + `gdb-multiarch` 連上，`info registers` 看 ARM 暫存器。
2. 同上用 RISC-V（`qemu-riscv64 -g`），對比兩架構的暫存器與組語。
3. 寫一個用 `$pc`/`$sp`（架構無關別名）的小 Python 指令，確認它在 x86 和 ARM target 都能用。
4. （有 QEMU system）用 `-s -S` 跑一個 kernel/bootloader，從 `start_kernel`（或 reset vector）開始 debug 開機——呼應 linux_boot。
5. （有硬體）用 ST-Link + OpenOCD debug 一塊 STM32：`monitor reset halt` + `load` + `hbreak main`——呼應 arm/embedded。
6. 對一個 QEMU kernel，試 `lx-dmesg`/`lx-ps`（Linux 的 GDB 腳本），看 Part 5 API 的大型實戰。

## 本章重點整理

- 跨架構/嵌入式都建在 Ch 36 遠端機制上（GDB 前端 + 某種 stub），只是 target 與架構不同。
- 用 `gdb-multiarch` 或 cross-gdb；`set architecture` 指定；`$pc`/`$sp` 別名跨架構通用。
- QEMU user-mode（`qemu-arm -g`）debug 單一外架構 binary；system-mode（`-s -S`）debug 整機/kernel/bootloader，`-S` 讓它等 GDB。
- 真實 MCU 透過 OpenOCD + JTAG/SWD：`monitor` 傳指令、`load` 燒 flash、flash 唯讀用 `hbreak`。
- kernel：QEMU+`nokaslr`+`vmlinux`，配 Linux 的 `lx-*` GDB 腳本（Part 5 實戰範例）。

## 自我檢核

- [ ] debug ARM binary 為什麼不能用一般 x86 GDB？用什麼？
- [ ] QEMU 的 `-s` 和 `-S` 各做什麼？為什麼 debug 開機需要 `-S`？
- [ ] 真實 MCU 為什麼不能用軟體斷點？用什麼？OpenOCD 扮演什麼角色？
- [ ] kernel debug 為什麼要 `nokaslr`？
- [ ] 跨架構時，為什麼腳本要用 `$pc` 而非 `$rip`？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Configuration-Specific / Embedded](https://sourceware.org/gdb/current/onlinedocs/gdb/Embedded-Processors.html)** 與 **[Specifying a Debugging Target](https://sourceware.org/gdb/current/onlinedocs/gdb/Targets.html)**
  - **讀哪裡**：架構特定設定、target 類型。
  - **和本章的關聯**：跨架構/嵌入式設定的權威。

- **[OpenOCD User Guide](https://openocd.org/doc/html/index.html)**
  - **讀哪裡**：GDB and OpenOCD、Flash Commands、`monitor` 指令。
  - **和本章的關聯**：JTAG/SWD debug 的權威；查你板子的具體設定。

### 部落格 / 文章

- **[Debugging the Linux kernel with QEMU and GDB](https://www.kernel.org/doc/html/latest/dev-tools/gdb-kernel-debugging.html)** — kernel docs
  - **讀哪裡**：QEMU+gdb 設定、`lx-*` 指令。
  - **和本章的關聯**：kernel debug 入門的權威；Part 5 Python API 的大型實戰。

- **[Interrupt blog — embedded GDB/OpenOCD](https://interrupt.memfault.com/blog/)** — Memfault
  - **為什麼值得讀**：嵌入式 debug 的大量實戰文（semihosting、SVD、RTOS-aware），呼應 arm/embedded 課程。

Part 7 收尾用練習 G：從一個 production core dump 還原當機現場，把 core 分析、backtrace、多執行緒、符號技能綜合起來。

→ [練習 G：從 production core dump 還原現場](./practice-g-production-core-dump.md)
