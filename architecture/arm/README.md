# ARM 學習筆記：從 ISA 到 JTAG，雙線吃透 Cortex-A/M

> 給已經寫過 x86 / x86_64 組語、會 C、想徹底搞懂 ARM 體系結構與底層除錯的工程師。

這是一系列循序漸進的教學文章，**Cortex-A / Cortex-M 雙線並行**，從 ISA 講到啟動、MMU、Exception Level、CoreSight trace、JTAG/SWD、OpenOCD 與 GDB remote debug。最終你會親手刻一個跑在 Cortex-M3 上的迷你 RTOS-lite，並用 GDB + OpenOCD + ITM 做完整的 trace 與除錯。

## 為什麼學這個？

- **ARM 是當代軟體跑得最多的 ISA**：手機、伺服器、AWS Graviton、Apple Silicon、車機、IoT、嵌入式 — 你寫的軟體有極高機率最終在 ARM 上執行。
- **Cortex-A 與 Cortex-M 是兩個世界但共享 ISA 根**：A profile 在 Linux/Android 底下，M profile 在 STM32/Pico/感測器裡。**雙線並行** 才看得到 ARM 設計者怎麼用同一套指令哲學覆蓋從 cortex 紐扣電池到伺服器級 SoC 的光譜。
- **底層除錯是不教就不會的硬技能**：JTAG / SWD / OpenOCD / CoreSight / ETM 這套工具鏈大部分人靠摸索撞牆，這裡幫你一次串起來。
- **MMU、Cache、記憶體模型、TrustZone、Virtualization** — 這些是 kernel、hypervisor、TEE、driver 工程師的核心知識。x86 那一套你大概懂了，ARM 的取捨完全不一樣，學完你會知道「為什麼 ARM 這樣選」。
- **Apple Silicon、AWS Graviton、聯發科、瑞昱、Arm China**：ARM 是地緣政治、硬體生態、開源 vs 封閉爭論的中心。讀 ARM 你也在讀產業史。

## 課程地圖

### Part 0 — 起點
- [Ch 0 環境搭建：toolchain、QEMU、OpenOCD、JTAG 探針](./00-environment-setup.md)
- [Ch 1 ARM 全貌：公司史、ISA 家族、A/R/M profile 三條線](./01-arm-overview.md)

### Part 1 — ISA 基礎（用 x86 當對照）
- [Ch 2 AArch64 暫存器與 ABI](./02-aarch64-registers-abi.md)
- [Ch 3 AArch32 與 Thumb / Thumb-2](./03-aarch32-thumb.md)
- [Ch 4 Load-Store 架構與定址模式](./04-load-store-addressing.md)
- [Ch 5 條件碼、IT block、barrel shifter](./05-condition-and-shifter.md)
- [Ch 6 函式呼叫與 AAPCS](./06-aapcs-calling-convention.md)
- [Ch 7 系統指令與例外進入](./07-system-instructions.md)

### Part 2 — Cortex-M 線：bare-metal 從 0 開始
- [Ch 8 Cortex-M 處理器模型：Thread/Handler、MSP/PSP](./08-cortex-m-processor-model.md)
- [Ch 9 Reset 流程與向量表](./09-reset-and-vector-table.md)
- [Ch 10 Startup code 解剖](./10-startup-code.md)
- [Ch 11 Linker script 全解](./11-linker-script.md)
- [Ch 12 NVIC：優先權、tail-chaining、late arrival](./12-nvic.md)
- [Ch 13 SysTick 與低功耗](./13-systick-and-sleep.md)
- [Ch 14 MPU（Cortex-M 版）](./14-mpu-cortex-m.md)
- [練習 A：STM32 bare-metal 韌體（LED + UART + Timer）](./practice-a-stm32-baremetal.md)

### Part 3 — Cortex-A 線：AArch64 與 OS 級
- [Ch 15 A profile 處理器模型：Exception Level 0–3](./15-exception-levels.md)
- [Ch 16 AArch64 MMU 與分頁](./16-aarch64-mmu.md)
- [Ch 17 ASID、TLB、context switch](./17-asid-tlb-context-switch.md)
- [Ch 18 Cache 階層、coherency、shareable domain](./18-cache-and-coherency.md)
- [Ch 19 ARM 弱記憶體模型與屏障](./19-memory-model-barriers.md)
- [Ch 20 原子操作：LDXR/STXR 與 LSE atomics](./20-atomics.md)
- [Ch 21 TrustZone 與 EL3](./21-trustzone-el3.md)
- [Ch 22 虛擬化擴展：EL2 與 stage-2 translation](./22-virtualization-el2.md)
- [練習 B：QEMU virt aarch64 從 EL3 降到 EL1 開 MMU](./practice-b-aarch64-el3-to-el1.md)

### Part 4 — 除錯全套
- [Ch 23 JTAG vs SWD 硬體層](./23-jtag-swd.md)
- [Ch 24 CoreSight：DAP、ETM、ITM、TPIU](./24-coresight.md)
- [Ch 25 OpenOCD 架構與 config](./25-openocd.md)
- [Ch 26 GDB Remote Serial Protocol](./26-gdb-rsp.md)
- [Ch 27 硬體斷點 vs 軟體斷點、watchpoint](./27-breakpoints-watchpoints.md)
- [Ch 28 Semihosting](./28-semihosting.md)
- [Ch 29 ITM / SWO trace 與 printf debugging](./29-itm-swo-trace.md)
- [Ch 30 GDB Python 進階用法](./30-gdb-python.md)
- [練習 C：race + memory ordering bug 抓蟲實況](./practice-c-race-bug-hunt.md)

### Part 5 — 進階主題與反思
- [Ch 31 SIMD：NEON、Advanced SIMD、SVE/SVE2](./31-simd-neon-sve.md)
- [Ch 32 ARM 硬體安全：PAC、BTI、MTE](./32-pac-bti-mte.md)
- [Ch 33 ARM 的 CPU bug 與 errata 史](./33-errata-and-cpu-bugs.md)
- [Ch 34 怎麼讀 Arm Architecture Reference Manual](./34-reading-arm-arm.md)
- [Ch 35 反思：ARM vs x86 vs RISC-V](./35-vs-x86-riscv.md)

### Final Project
- [Final Project：Cortex-M3 Mini RTOS-lite](./final-project-mini-rtos.md)

## 學習方式建議

1. **每章親手敲過一輪**：ARM 的細節在指令編碼、暫存器名稱、CMSIS 巨集裡都會吃人，看不會記住，敲過才會。
2. **QEMU + 實體板雙軌進行**：QEMU 跑 mps2-an385（Cortex-M3）與 virt（Cortex-A）就夠用八成。實體 STM32F4 Discovery / Raspberry Pi 4 用來感受真硬體的時序、抖動、JTAG 連線體驗。沒有板子也能完課。
3. **故意做錯**：把向量表第一格寫錯、把 MMU page table 的 attribute bit 設反、把 DMB 拿掉看為什麼壞 — 教材會主動帶你做這些事。
4. **不要怕 manual**：本書反覆指向 Arm ARM 的某個 section，告訴你怎麼搜、看哪裡、跳過哪裡。讀懂 manual 才是終局，教材只是降低 cost。
5. **Cortex-M 與 Cortex-A 不要偏食**：常見錯誤是寫 STM32 的人不碰 Linux、寫 kernel 的人不寫 bare-metal。這門課故意逼你雙線都做。
6. **除錯是核心不是附錄**：Part 4 不是「進階補充」，是和 Part 2/3 平起平坐的章節。寫完韌體不會 debug 等於還沒完成。

## 本教材不涵蓋什麼

- **不教 RTL / 微架構設計**：我們講軟硬介面（ISA + 架構級行為），不講 cycle-accurate timing、pipeline forwarding、Branch predictor 設計。
- **不深入 Linux on ARM 移植**：Part 3 只到能看懂 boot 流程、page table walk、context switch 為止。porting 整個 BSP 是 Yocto 課的事。
- **不教 Android HAL / Trusty TEE 開發**：Ch 21 TrustZone 是概念與低層機制，OP-TEE 完整實作另一回事。
- **不教 Mali GPU / NPU**：ARM 出 GPU/NPU 但那是另一個世界。本課只專注 CPU side。
- **AArch32 A profile 不單獨開章**：實務上 ARMv8-A 之後新系統都 AArch64，AArch32 只在和 Cortex-M 共用的 ISA 章帶過。

## 參考資料

**一手資料（Arm 官方，全部免費註冊後可下載）：**
- Arm Architecture Reference Manual (ARM ARM) — A-profile：<https://developer.arm.com/documentation/ddi0487/latest/>
- Arm v8-M Architecture Reference Manual：<https://developer.arm.com/documentation/ddi0553/latest>
- Cortex-M3 / M4 / M7 / A53 / A72 Technical Reference Manual — Arm Developer 網站
- AAPCS64 / AAPCS32：<https://github.com/ARM-software/abi-aa>
- Arm Trusted Firmware-A：<https://www.trustedfirmware.org/projects/tf-a/>

**書：**
- 《The Definitive Guide to ARM Cortex-M3 and Cortex-M4 Processors》— Joseph Yiu（M 系列必讀）
- 《Learning the ARM 64 Architecture》— Pyeatt & Ughetta（AArch64 入門）
- 《Practical Reverse Engineering》— Dang/Gazet/Bachaalany（涵蓋 ARM RE 與 debugging）
- 《Linux Device Drivers》— Corbet 等（搭 Cortex-A 章節參考）

**社群與工具：**
- OpenOCD 官方：<https://openocd.org/>
- probe-rs（Rust 寫的現代替代品）：<https://probe.rs/>
- pyOCD（Python，CMSIS-DAP 友善）：<https://pyocd.io/>
- QEMU ARM：upstream，裝 qemu 就有
- Arm Connect 論壇：<https://community.arm.com/>
- CMSIS：<https://github.com/ARM-software/CMSIS_5>

**故事與背景閱讀：**
- 《The Soul of A New Machine》氛圍同類：找 Furber / Wilson 早年訪談（ARM 起源）
- Hennessy & Patterson 的 ACM Turing Lecture（談 RISC-V 與 ARM 對比）
