# CPU 設計學習筆記：用 SystemVerilog 從零打造一顆 RISC-V core

> 給懂 RISC-V ISA、會 C、但沒碰過硬體設計的人。目標是親手做出「那顆真的執行這些指令的矽」——從邏輯閘一路到能跑你自己 toolchain 編出的 ELF 的 pipelined CPU。

這是一系列循序漸進、可動手的教學文章。從電晶體與邏輯閘講起，經過單週期 datapath、五級 pipeline、hazard 處理、分支預測、cache、虛擬記憶體、CSR/中斷，最後你會有一顆完整的 pipelined RV32I core，通過官方 `riscv-tests`，用 verilator + spike 逐指令對拍。全程 SystemVerilog，不碰 Chisel。

## 為什麼學這個？

- **補齊你 ISA 與 compiler 之間的斷層**：你可能已經讀懂 RISC-V spec（軟硬體介面），也知道 LLVM backend 怎麼把 IR 變成 `.S`。但中間那顆「真的執行指令的硬體」是黑盒。這門課把黑盒拆開——你會知道 `add x1, x2, x3` 在矽裡到底是哪幾條線在動。
- **硬體直覺讓你變強的軟體工程師**：懂 pipeline hazard，你才真懂為什麼 branch 難預測會殺效能；懂 cache 微架構，你才真懂 locality 不是玄學；懂 store buffer，你才真懂 memory model 為什麼長那樣。這些是頂尖系統/toolchain/效能工程師的共同底層。
- **RISC-V 是唯一你能完整做完一顆的 ISA**：x86/ARM 太複雜，教學型 core 幾乎不可能。RV32I 只有 47 條指令、encoding 規整，一個人一門課就能從零做出可跑的實作。
- **SiFive/RISC-V 生態的入場券**：Rocket、BOOM 這些真實 core 就是這套微架構原理的工業級版本。你做完這門課再去讀它們的 RTL，會從「天書」變成「喔，這就是我做過的那個，只是更講究」。

## 先修知識

- **RISC-V ISA**（程度：讀得懂 RV32I encoding、知道 register/ABI/立即數型別）——本課會不斷對照 `architecture/riscv`。沒學過也行，關鍵處會補，但那門課會讓你輕鬆很多。
- **C 語言 + 一點命令列**（程度：會寫、會用 Makefile、能在 WSL/Linux 下跑編譯器）——testbench 用 C++，工具鏈全在命令列。
- **數位邏輯**（程度：**完全不需要**）——Part 0 從 boolean 代數、邏輯閘、flip-flop 從零教起。這是為你這種「軟體強、硬體零」的人設計的。
- 沒有也沒關係的：Verilog/SystemVerilog（Part 0 Ch 4–5 從零教）、FPGA（本課只在 Ch 38 講原理，不上板）。

## 課程地圖

### Part 0 — 數位邏輯與 HDL 地基（Ch 0–5）
- [Ch 0 環境搭建：verilator / gtkwave / riscv toolchain / spike](./00-environment-setup.md)
- [Ch 1 數位邏輯（一）：boolean、邏輯閘、組合元件](./01-digital-logic-combinational.md)
- [Ch 2 數位邏輯（二）：時序邏輯、flip-flop、時鐘與 timing](./02-digital-logic-sequential.md)
- [Ch 3 有限狀態機（FSM）：控制器的本質](./03-finite-state-machine.md)
- [Ch 4 SystemVerilog 語法核心：module / logic / always 與常見雷](./04-systemverilog-core.md)
- [Ch 5 verilator + testbench + 波形：把設計跑起來](./05-verilator-testbench-waveform.md)

### Part 1 — 單週期 RV32I CPU（Ch 6–12）
- [Ch 6 CPU 的心智模型：datapath + control](./06-cpu-datapath-mental-model.md)
- [Ch 7 PC 與 instruction fetch](./07-pc-instruction-fetch.md)
- [Ch 8 Register File（2R1W）](./08-register-file.md)
- [Ch 9 ALU 與 ALU control](./09-alu.md)
- [Ch 10 Control Unit + immediate generator](./10-control-unit-immediate.md)
- [Ch 11 Load/Store、branch/jump datapath](./11-load-store-branch-datapath.md)
- [Ch 12 湊齊單週期 RV32I：跑起第一支真程式](./12-single-cycle-rv32i-complete.md)
- [練習 A：單週期打穿 riscv-tests rv32ui](./practice-a-single-cycle-rv32ui.md)

### Part 2 — Pipeline（5 級，本課心臟）（Ch 13–20）
- [Ch 13 為什麼要 pipeline：throughput vs latency](./13-why-pipeline.md)
- [Ch 14 IF/ID/EX/MEM/WB 切分與 pipeline register](./14-five-stage-split.md)
- [Ch 15 naive pipeline：先切開，故意跑錯給你看](./15-naive-pipeline.md)
- [Ch 16 Data hazard（一）：forwarding / bypassing](./16-data-hazard-forwarding.md)
- [Ch 17 Data hazard（二）：load-use hazard、stall / bubble](./17-data-hazard-load-use-stall.md)
- [Ch 18 Control hazard：branch 代價、flush、提前 resolve](./18-control-hazard-branch.md)
- [Ch 19 Hazard detection unit + structural hazard 綜合](./19-hazard-detection-unit.md)
- [Ch 20 pipeline 完整整合 + 打穿 riscv-tests](./20-pipeline-complete.md)
- [練習 B：自刻 forwarding + hazard detection](./practice-b-forwarding-hazard.md)

### Part 3 — 分支預測與效能（Ch 21–24）
- [Ch 21 branch prediction 基礎：BTB、2-bit 飽和計數器](./21-branch-prediction-basics.md)
- [Ch 22 進階預測器：gshare / tournament、RAS](./22-advanced-predictors.md)
- [Ch 23 CPI 分析與 pipeline 效能建模](./23-cpi-analysis.md)
- [Ch 24 關鍵路徑、時脈與 hazard 的量化代價](./24-critical-path-timing.md)
- [練習 C：實作 BHT + BTB，量測 CPI 改善](./practice-c-branch-predictor.md)

### Part 4 — 記憶體階層：Cache 與虛擬記憶體（Ch 25–30）
- [Ch 25 為什麼要 cache：memory wall、locality](./25-why-cache.md)
- [Ch 26 Cache 設計：direct-mapped / set-associative，實作 I-cache](./26-cache-design-icache.md)
- [Ch 27 D-cache + 與 pipeline 整合、miss stall](./27-dcache-pipeline-integration.md)
- [Ch 28 虛擬記憶體與 MMU：Sv32 page walk](./28-virtual-memory-mmu-sv32.md)
- [Ch 29 TLB 設計 + page fault 與 pipeline 互動](./29-tlb-page-fault.md)
- [Ch 30 AXI4-Lite 總線：memory-mapped I/O](./30-axi-bus-mmio.md)
- [練習 D：實作 direct-mapped I-cache，量 hit rate](./practice-d-direct-mapped-icache.md)

### Part 5 — 特權模式、CSR、中斷／例外（Ch 31–35）
- [Ch 31 CSR file 實作：mstatus / mtvec / mepc / mcause](./31-csr-file.md)
- [Ch 32 Trap 機制：exception / interrupt 進出、pipeline flush](./32-trap-mechanism.md)
- [Ch 33 M/S/U mode + privilege check](./33-privilege-modes.md)
- [Ch 34 中斷控制：CLINT（timer / software int）、PLIC 速覽](./34-interrupt-clint-plic.md)
- [Ch 35 讓 core 跑得動 trap handler](./35-trap-handler-integration.md)
- [練習 E：加 CSR + timer 中斷，進出 trap handler](./practice-e-csr-timer-interrupt.md)

### Part 6 — 進階微架構與生態（Ch 36–39）
- [Ch 36 superscalar / out-of-order 概念：Tomasulo、renaming](./36-superscalar-ooo-concepts.md)
- [Ch 37 Rocket / BOOM 巡禮：真實 SiFive core 長怎樣](./37-rocket-boom-tour.md)
- [Ch 38 從 RTL 到晶片：synthesis / STA / FPGA 流程原理](./38-rtl-to-silicon.md)
- [Ch 39 驗證方法學：formal / riscv-formal / cocotb / UVM 速覽](./39-verification-methodology.md)

### Part 7 — 整合專案
- [Final Project：完整 pipelined RV32I core + spike 對拍](./final-project-pipelined-rv32i-core.md)

## 學習方式建議

1. **每章都要跑起來**：這門課的一切都在波形裡。讀完一段就 verilate、跑、開 gtkwave 看訊號。硬體的 bug 不像軟體會噴 stack trace——它就是某條線在某個 cycle 值錯了，你只能用波形抓。
2. **故意把它弄壞**：把 forwarding 拔掉看 hazard 怎麼算錯、把 flush 拿掉看 branch 怎麼執行到不該執行的指令。這門課大量用「先跑錯再修對」建立直覺。
3. **和 spike 對拍**：spike 是官方 reference model。你的 core 每執行一條指令，就跟 spike 比對 PC 和 register 狀態。差一個 bit 就停下來查。這是驗證真實硬體的標準做法。
4. **RV32I 為主**：教學最乾淨，`riscv-tests` 有現成的 `rv32ui`。RV64 差異會隨章對照，但主線程式碼全部 RV32。
5. **把單週期打穿再進 pipeline**：Part 1 的單週期 core 是後面一切的基礎。datapath 沒搞懂就進 pipeline，hazard 會讓你崩潰。別急。
6. **對照真實 core**：學到某個機制時，去 rocket-chip / riscv-boom 的 GitHub 搜對應模組。你會發現工業級 core 就是你做的東西 + 大量 corner case 處理。

## 本教材涵蓋與不涵蓋

**涵蓋**：數位邏輯地基、SystemVerilog、單週期到五級 pipeline、data/control/structural hazard、forwarding/stall、分支預測、cache（I/D）、Sv32 虛擬記憶體與 TLB、AXI4-Lite、CSR/trap/中斷、M 擴充（可選）、驗證方法學、FPGA/合成流程原理。

**不涵蓋**：
- **不實作 out-of-order / superscalar**：Ch 36 只講 Tomasulo / renaming 概念。真要手刻亂序 core 是另一門大課。
- **不上實體 FPGA**：Ch 38 講 synthesis / STA / FPGA 流程原理，但本課終點是 verilator 模擬。要上板你有足夠基礎自己接。
- **不深挖類比電路 / 製程 / 佈局繞線**：這是數位 RTL 課，不是 VLSI 物理設計課。
- **不教 Chisel**：照選擇，全程 SystemVerilog。想看 Chisel 生態 Ch 37 會指路。

## 精選資料庫

這裡列整門課最值得反覆參照的資源，每章「延伸閱讀」會指向更具體的小節。

### 必讀基礎

- **《Computer Organization and Design, RISC-V Edition》** — Patterson & Hennessy（Morgan Kaufmann）
  - 這門課的主要參考書。第 4 章（The Processor）就是單週期→pipeline→hazard 的經典教材，本課 Part 1–2 大量對照它的 datapath 圖。
- **《Digital Design and Computer Architecture, RISC-V Edition》** — Harris & Harris（Morgan Kaufmann）
  - 最適合「數位邏輯零基礎」補地基的書。第 1–5 章從 boolean 到 FSM 到 HDL，正好對應本課 Part 0；第 7 章的 RISC-V 微架構是 Part 1–2 的另一條主線。
- **[RISC-V Unprivileged ISA Spec](https://riscv.org/technical/specifications/)**
  - 權威來源。實作指令行為有疑義時的最終仲裁。搭配 `architecture/riscv` 課服用。

### 推薦論文 / 技術報告

- **[The RISC-V Instruction Set Manual, Volume II: Privileged Architecture](https://riscv.org/technical/specifications/)**
  - Part 5（CSR/trap/中斷）的規格依據。Machine-Level ISA 那章定義了 mstatus/mtvec/mcause 等 CSR 的精確語意。
- **[The Berkeley Out-of-Order Machine (BOOM) 技術報告](https://docs.boom-core.org/)** — Celio et al., UC Berkeley
  - Ch 36–37 的延伸。想知道教學型 pipeline 怎麼進化成工業級亂序 core，這是最好的公開資料。

### 推薦部落格 / 開源專案

- **[picorv32](https://github.com/YosysHQ/picorv32)** — Claire Wolf
  - 一個極簡、可讀、被大量使用的 RV32 core（Verilog）。想看「一個真正被人用的小 core」怎麼寫，讀這個。本課設計取捨會不時和它對照。
- **[Sodor 教學 core 系列](https://github.com/ucb-bar/riscv-sodor)** — UC Berkeley
  - 官方教學用的 1/2/3/5-stage RISC-V core（Chisel）。本課 Part 1–2 的微架構和它一脈相承，是最好的「標準答案」對照組。
- **[Verilator 官方文件](https://verilator.org/guide/latest/)**
  - 本課模擬與驗證的工具。Ch 5 會帶你入門，之後每章的 testbench 都靠它。

### 讀完本課之後

- **《A Primer on Memory Consistency and Cache Coherence》** — Nagarajan, Sorin, Hill, Wood（Morgan & Claypool）
  - 把 Part 4 的 cache 推向多核 coherence（MESI/MOESI）。單核做完想做多核，從這本開始。
- **[rocket-chip](https://github.com/chipsalliance/rocket-chip)**
  - SiFive 的工業級 RISC-V SoC 生成器。做完本課，這份 code base 對你不再是天書。
- **[riscv-tests](https://github.com/riscv-software-src/riscv-tests) / [riscof](https://github.com/riscv-software-src/riscof)**
  - 官方一致性測試。本課用 riscv-tests 驗證 core；想做到「正式合規」用 riscof。
