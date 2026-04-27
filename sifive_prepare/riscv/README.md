# RISC-V 學習筆記：從 RV32I 到 Vector、Custom Extension 與生態

> 給已經寫過組語（x86 或 ARM 都行）、想徹底搞懂開放 ISA 是怎麼設計與演化的工程師。目標是讀得懂 spec、讀得懂 `.S`、讀得懂 toolchain 裡「為什麼 RISC-V 要這樣編」。

這是一系列循序漸進的教學文章，從 RV32I 的 47 條指令講起，一路寫到 V 擴充、B 擴充、custom extension、privileged ISA、memory model。最終你會自己寫一個極簡 RV32I emulator，做為後面 compiler backend / linker 課程的共同基座。

## 為什麼學這個？

- **RISC-V 是未來十年的 ISA 主戰場**：雲端、嵌入式、HPC、GPU、AI 加速器都在選它。不會 RISC-V，未來十年的系統軟體工程師履歷就少一角。
- **它是你唯一能完整讀完 spec 的 ISA**：x86-64 的 manual 有 5000+ 頁、ARMv9 是 13000+ 頁，**RISC-V unprivileged spec 才 200 頁左右**。第一次你會有「我真的看得懂一個 ISA 的全貌」的體驗。
- **它的設計取捨很明顯**：load-store 架構、固定 32-bit、modular 擴充、沒有 condition code — 每個決定都有強烈的哲學。懂了這些取捨你看 x86/ARM 會有「喔原來它們是另一種解法」的頓悟。
- **toolchain 工程師的基石**：LLVM / GCC / binutils 裡的 RISC-V 部分是你之後要動手改的對象。沒讀過 spec 就去改 TableGen 是災難。
- **Compiler-ISA 共同演化**：RISC-V 擴充的設計流程有很多是為了「讓 compiler 好產生好程式碼」。你會看到硬體如何受 compiler 影響，而不只是反過來。

## 課程地圖

### Part 0 — 起步
- [Ch 0 環境搭建：toolchain、spike、QEMU](./00-environment-setup.md)

### Part 1 — RV32I 基座
- [Ch 1 RV32I 心法：為什麼只有 47 條指令](./01-rv32i-mindset.md)
- [Ch 2 Register 慣例與 ABI：calling convention 的硬體介面](./02-register-abi.md)
- [Ch 3 Pseudo-instruction 與 assembler 展開](./03-pseudo-instructions.md)

### Part 2 — 標準擴充
- [Ch 4 M / A / F / D / C：標準擴充五件套](./04-standard-extensions.md)
- [Ch 5 Privileged ISA：M/S/U mode、CSR、trap](./05-privileged-isa.md)
- [Ch 6 Zicsr / Zifencei / Zicond：看似瑣碎但很關鍵的小擴充](./06-small-extensions.md)

### Part 3 — 重頭戲擴充
- [Ch 7 B 擴充：bit manipulation 全解](./07-bitmanip-extension.md)
- [Ch 8 V 擴充：vector、vtype、LMUL 心法](./08-vector-extension.md)
- [Ch 9 Zc 擴充與 code size：為什麼嵌入式在乎](./09-code-size-extension.md)
- [Ch 10 Hypervisor extension 速覽](./10-hypervisor-extension.md)

### Part 4 — Custom Extension 與廠商實作
- [Ch 11 Custom extension 的設計範式](./11-custom-extension-patterns.md)
- [Ch 12 SiFive Intelligence / XuanTie / Vector Crypto 巡禮](./12-vendor-extensions.md)
- [Ch 13 擴充是怎麼從 proposal 走到 ratified 的](./13-extension-ratification.md)
- [練習 A：手解 opcode](./practice-a-decode-by-hand.md)

### Part 5 — 記憶體模型與底層
- [Ch 14 RVWMO memory model 最小必懂](./14-memory-model.md)
- [Ch 15 Atomics、fence、LR/SC 的真實行為](./15-atomics-and-fence.md)
- [Ch 16 從 spec 讀 opcode encoding](./16-opcode-encoding.md)

### Part 6 — 比較與讀 spec
- [Ch 17 與 ARM / x86 對照：三種設計哲學](./17-vs-arm-x86.md)
- [Ch 18 如何讀 ISA spec 而不迷路](./18-reading-spec.md)
- [Ch 19 RISC-V International 生態與流程](./19-ecosystem.md)
- [Ch 20 反思：RISC-V 的爭議與未來](./20-reflections.md)
- [練習 B：用 spike 跑 baremetal](./practice-b-baremetal-on-spike.md)

### Part 7 — 整合專案
- [Final Project：Mini RV32I Emulator](./final-project-rv32i-emulator.md)

## 學習方式建議

1. **把 spec PDF 放在旁邊**：<https://riscv.org/technical/specifications/>。本教材會不斷指向 spec 的某一節，叫你對照看原文。讀得懂 spec 才是最終目標 — 教材只是降低你讀 spec 的成本，不能取代 spec 本身。
2. **所有 `.S` 都手打一次**：不要 copy-paste。RISC-V assembler syntax 很容易看錯（`addi` 跟 `addiw` 差一個字母，語意不同），手打過才會長肌肉記憶。
3. **spike 與 QEMU 擇一為主**：spike 是官方 reference model，語意最正；QEMU 跑得快、支援 Linux。**本教材以 spike 為主驗證指令行為，QEMU 用來跑 user-space Linux 程式**。Ch 0 兩個都裝。
4. **RV64 為主，但根要扎在 RV32**：業界新系統幾乎全部 RV64。但 RV32I 是整個 ISA 的邏輯起點，先把 RV32I 打穿，RV64I 只是加幾條 `w` 後綴指令的延伸。Ch 1–3 全部用 RV32I。
5. **不要跳過 privileged ISA**：很多教材只講 unprivileged 就結束。如果你是 toolchain 或 kernel 方向，**CSR / trap / delegation** 是繞不開的。Ch 5 必讀。
6. **擴充不是選修**：以為「會 RV32I 就算會 RISC-V」是誤會。**業界真正用的是 RV64GC + V + B**，而 custom extension 是 SiFive 這類職缺的核心能力。Part 3、Part 4 是本課的重點。

## 本教材不涵蓋什麼

- **不教 CPU 微架構設計**：我們講 ISA（軟硬體介面），不講怎麼用 Chisel 寫 Rocket / BOOM。那是硬體課。
- **不教 Verilog / Chisel**：同上。如果你想設計 RISC-V core，這不是對的教材。
- **不深挖 Linux porting**：`learn_bpf` 與未來的 `learn_yocto` 會碰到。本課只講 privileged ISA 到能看懂 trap handler 為止。
- **不會變成刷題指南**：面試題不是目標，讀懂 spec 與 toolchain 是目標。

## 參考資料

**一手資料（全部免費）：**
- RISC-V Unprivileged ISA Spec — <https://riscv.org/technical/specifications/>
- RISC-V Privileged ISA Spec — 同上連結
- RISC-V ABI Spec — <https://github.com/riscv-non-isa/riscv-elf-psabi-doc>
- RISC-V Assembly Programmer's Manual — <https://github.com/riscv-non-isa/riscv-asm-manual>

**書：**
- 《The RISC-V Reader》— Patterson & Waterman（精簡權威，RISC-V 創辦人之一親撰）
- 《Computer Organization and Design, RISC-V Edition》— Patterson & Hennessy（從微架構切入，適合補背景）

**社群：**
- RISC-V International: <https://riscv.org>
- 論壇（討論超活躍）：<https://lists.riscv.org>
- GitHub org：<https://github.com/riscv>

**工具：**
- spike（官方 ISA simulator）：<https://github.com/riscv-software-src/riscv-isa-sim>
- riscv-gnu-toolchain：<https://github.com/riscv-collab/riscv-gnu-toolchain>
- QEMU RISC-V：已進 upstream，裝 qemu 就有
