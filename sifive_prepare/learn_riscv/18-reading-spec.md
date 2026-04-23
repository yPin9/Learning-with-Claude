# Ch 18 — 如何讀 ISA spec 而不迷路

> 目標：spec 是你這份工作的第一資料。讀 spec 不是「翻一翻」，是一套可以訓練的技能。這章給你讀 RISC-V spec 的地圖、順序、重點章節、以及如何在 200 頁 PDF 裡快速定位想要的資訊。

## 兩本 spec 的分工

RISC-V 的 ISA spec 分兩本：

### 1. The RISC-V Instruction Set Manual, Volume I: Unprivileged ISA

**約 200 頁**。內容：

- Base ISA（RV32I / RV64I / RV128I / RV32E）
- 標準擴充（M / A / F / D / C 等）
- Vector (V)
- Bitmanip (B)
- Memory model (RVWMO)
- 指令格式圖與 encoding 表

**這是你天天讀的那本**。所有 user-space code 的 ground truth。

### 2. The RISC-V Instruction Set Manual, Volume II: Privileged Architecture

**約 150 頁**。內容：

- M-mode / S-mode / U-mode
- CSR 架構
- Trap 處理
- 虛擬記憶體 (Sv32 / Sv39 / Sv48 / Sv57)
- Hypervisor 擴充
- PMP / PMA

**寫 kernel / firmware / hypervisor 才會深讀**。compiler 工程師看個 30 頁左右就夠。

### 其他

- **RISC-V ABI specification** — 獨立 repo、獨立文件。對應 Ch 2 講的 calling convention。

- **RISC-V Assembly Programmer's Manual** — 解釋 pseudo-instruction 的細節。assembler 開發者必讀。

- 各擴充的獨立 spec（V / Zvk / H 等）— 每個擴充往往有自己的 repo。

## 下載來源

所有官方 spec：<https://riscv.org/technical/specifications/>

PDF + GitHub sources 都有。**建議用 GitHub source 版本**：

- <https://github.com/riscv/riscv-isa-manual>

好處：

- 可以 `git log` 看修訂歷史（「這條指令什麼時候加的？」）
- 可以在 source markdown / AsciiDoc 搜尋
- 看到最新未 ratify 的版本（branch）

## Unprivileged spec 的地圖

目錄大致：

```
Chapter 1:  Introduction
Chapter 2:  RV32I Base Integer Instruction Set
Chapter 3:  RV32E and RV64E Base Integer Instruction Sets
Chapter 4:  RV64I Base Integer Instruction Set
Chapter 5:  RV128I Base Integer Instruction Set
Chapter 6:  RV32/64G Instruction Set Listings
Chapter 7:  "M" Standard Extension for Integer Multiplication and Division
Chapter 8:  "A" Standard Extension for Atomic Instructions
Chapter 9:  "Zicsr" for CSR
Chapter 10: Counters "Zicntr"/"Zihpm"
Chapter 11: "F" Single-Precision FP
Chapter 12: "D" Double-Precision FP
Chapter 13: "Q" Quad-Precision FP (rarely implemented)
Chapter 14: "Zfh" Half-precision FP
Chapter 15: "BF16" Brain Float
Chapter 16: "C" Compressed
Chapter 17: "Zc" extensions
Chapter 18: "B" Bitmanip (Zba/Zbb/Zbc/Zbs)
Chapter 19: "K" Scalar Cryptography
Chapter 20: "V" Vector
Chapter 21: "Zvbc", "Zvbb", "Zvk*" Vector Crypto
Chapter 22: RV32/64G Instruction Set Listings (重整)
Chapter 23: RVWMO Memory Consistency Model
...
```

前 6 章是 base。中間是擴充。最後是 memory model。

## 讀 spec 的順序建議

**第一次讀的路線**：

```
Chapter 1 (總覽，略讀)
→ Chapter 2 (RV32I，細讀)           ← Ch 1 本課的功課
→ Chapter 4 (RV64I，對比讀)
→ Chapter 6 (G instruction listing)
→ Chapter 7 (M)  ┐
→ Chapter 8 (A)  │ 細讀但不背
→ Chapter 9 (Zicsr)
→ Chapter 16 (C)  ┘
→ Chapter 23 (RVWMO，略讀，留到實戰)
```

第二次讀（進擴充）：
```
→ Chapter 18 (B) — 跟本課 Ch 7 對照
→ Chapter 20 (V) — 跟本課 Ch 8 對照
→ Chapter 19 (K) — 如果做 crypto 才細讀
```

**不要一次讀完**。分批讀，回來修正理解。

## 一頁 instruction spec 的閱讀方法

取 `ADD` 為例（Unprivileged spec 第 2 章某處）：

```
┌─────────────────────────────────────────────────┐
│ ADD — 2.4.2                                     │  ← 章節編號
├─────────────────────────────────────────────────┤
│ Format: R-type                                  │  ← 指令格式
├─────────────────────────────────────────────────┤
│                                                 │
│ funct7   rs2    rs1    funct3   rd    opcode   │  ← encoding
│ 0000000  rs2    rs1    000      rd    0110011  │
│                                                 │
├─────────────────────────────────────────────────┤
│ x[rd] = x[rs1] + x[rs2]                          │  ← semantic
├─────────────────────────────────────────────────┤
│ Arithmetic overflow is ignored, result...       │  ← 註解
└─────────────────────────────────────────────────┘
```

**逐欄讀**：

1. **章節編號**：你的書籤位置。
2. **Format**：告訴你 R / I / S / B / U / J。
3. **Encoding table**：所有欄位的 bit pattern。照著查就是 Ch 16 的手組機器碼。
4. **Semantic**：偽代碼描述行為。注意 edge case（overflow / divide by zero / NaN）。
5. **Commentary**：spec 的備註區，通常藏著關鍵細節（例：對齊要求、哪些 implementations 必須支援）。

**不跳過 commentary**。那是 spec 作者在回答「為什麼這樣設計」、「哪些 corner case 要小心」— 很多 toolchain bug 是因為沒讀 commentary。

## 搜尋技巧

spec 幾百頁、常要找「某個 keyword 的定義在哪」。技巧：

### 1. 全文搜尋 keyword

`Ctrl+F` (PDF) 或 `grep -r` (Markdown/AsciiDoc)：

```
"ebreak" → 找出它在哪章被正式定義
"trap"   → 找 trap 的處理流程
```

### 2. 從 index 找

spec 最後通常有 instruction index。`ADD` → 查到 section 2.4.2。

### 3. 從 encoding 表找

spec 的 Chapter 6 / 22 是「所有 G instruction 的 encoding table」。給你 funct7 / funct3 / opcode 直接查出指令。這是 disassembler 開發者的 reference。

### 4. Git log 查歷史

```bash
cd riscv-isa-manual
git log --all --oneline -- src/rvv.adoc
```

看某節什麼時候被改、怎麼改。對「這個語意是不是最近才變」很有用。

## 讀 spec 的心態

### 1. 不求全懂，但求能定位

第一次讀看不懂很正常。目標是知道「如果將來我碰到 aq/rl 的問題，要回去 Chapter 8 A 擴充」。

### 2. 找矛盾就找到寶

spec 是人寫的，偶有矛盾。當你讀一遍覺得「這個 sentence 說 A、但那個 table 說 B」，回去查 errata。找到真矛盾可以送 pull request、你的名字就在 spec acknowledgments。

### 3. 對照實作讀

同時開：

- spec PDF
- spike 的 C++ 實作（`riscv/insn_*.h`）
- LLVM 的 TableGen (`llvm/lib/Target/RISCV/*.td`)

三份對照讀，能發現 spec 可能模糊的地方 — 不同實作怎麼解讀。

### 4. 讀 errata

每個 spec 有 errata 文件（GitHub issues）。重要 bug fix 會發。**讀 errata 比讀更多主文更能理解 spec 的邊緣**。

## Privileged spec 最該讀的三節

如果你是 compiler 工程師（不是 kernel 工程師），**Privileged spec 看這三節就夠 80% 場景**：

### 1. Chapter "Machine-Level ISA"

- `mstatus` / `mtvec` / `mepc` / `mcause` / `mtval` 的欄位
- trap 的硬體序列
- `mret` 指令的語意

### 2. Chapter "Supervisor-Level ISA"

- `sstatus` / `stvec` / `sepc` / `scause` 的對應
- `satp` 的欄位與虛擬記憶體 mode 選擇

### 3. Section "Hypervisor Extension" (if applicable)

- HS-mode 跟 VS-mode 的對應
- `hgatp` / two-stage translation

這些對應 Ch 5 / Ch 10。深入的 TLB / PMA 細節可以先跳過。

## 讀 extension-specific spec

各擴充有自己的 repo，格式跟 unprivileged spec 類似但有自己慣例。

### V 擴充

<https://github.com/riscv/riscv-v-spec>

重點：

- Chapter 3：vector config (vsetvl / vtype)
- Chapter 5：integer vector ops
- Chapter 11：vector mask ops
- Chapter 15：vector permutation

### Vector crypto

<https://github.com/riscv/riscv-crypto>

重點：每個 Zvk* 一章，語意極精細（crypto 不能錯一 bit）。

### Bitmanip

<https://github.com/riscv/riscv-bitmanip>

Zba/Zbb/Zbc/Zbs 各自一章，非常短。

## 練習：找出 `fence.i` 的規範

這是好的 warm-up：

1. 打開 Unprivileged spec。
2. Index 查 `fence.i` → 找到在 Chapter 3 或 Zifencei 相關章節。
3. 讀定義：「synchronizes the instruction and data streams」。
4. 讀 commentary：「Only guaranteed to be visible to the hart that executes it」。
5. **這給你寫 SBI `remote_fence_i` 的邏輯依據**（要多 hart 同步，要每個 hart 都執行）。

你就這樣走完一個 spec 查詢流程。

## 對 toolchain 的意義

面試 SiFive 會問：「你如何實作 X extension 的 LLVM 支援？」答案的第一步永遠是：

1. **讀 spec 的 semantic section**
2. **讀 encoding table**
3. **讀 commentary 找邊緣**
4. 然後才開始寫 TableGen

跳過讀 spec 直接寫 code = 高機率寫錯、被 reviewer 彈回重來。

## 常見誤會

1. **「spec 是完美的」**：不是。有 errata、有模糊、有矛盾。讀多了能辨。
2. **「讀 spec 要一次讀完」**：反而 toxic。spec 太密，一次讀完 99% 會忘。
3. **「spec 看不懂是我笨」**：spec 寫得就是有點給 ISA 設計者看。門檻本來就高。多讀、對照實作能補。
4. **「spec = 文件」**：spec 是**法律**。toolchain / 硬體以它為準，**有問題要改 spec 不是改 code**。
5. **「讀一次就夠」**：不夠。每做一個新專案回去查相關章節。你會發現第 N 次看才懂的細節。

## 動手練習

1. 下載 Unprivileged spec PDF，讀 Chapter 2 RV32I（~25 頁）。對照本課 Ch 1。
2. 找出 `sra`（shift right arithmetic）的定義，閱讀 commentary。對比 `srl`（logical）。
3. 讀 Chapter 23 RVWMO 的 `Preserved Program Order` 章節。對照本課 Ch 14 的四種默認重排描述。
4. 打開 riscv-isa-manual repo，用 `git log --grep` 找「commit 說 fix memory model」的 patch，讀 diff。
5. 找一條你**不認識**的指令（例：`sfence.vma`），讀 spec 查定義、搜 spike 看實作、寫一段組語呼叫它、objdump 驗證。完成一個「陌生指令探索」的完整流程。

## 自我檢核

- [ ] 我知道 RISC-V 兩本 spec 的分工與何時該看哪本
- [ ] 我能在 Unprivileged spec 快速定位任一標準擴充的章節
- [ ] 我能讀一頁 instruction spec 並對每個欄位解釋它的用途
- [ ] 我能搜尋 spec repo 找出某條規則的最近一次修改
- [ ] 我知道 spec 有 errata 並會去查

下一章講 RISC-V 的生態政治 — RISC-V International 的治理、profile 制度、廠商聯盟、對 compiler 工程師的意義。

→ [Ch 19 RISC-V International 生態與流程](./19-ecosystem.md)
