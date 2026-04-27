# Compiler Backend：LLVM RISC-V 目標全解

> 給已經會 compiler frontend（AST / SSA / IR）、讀過 `riscv` 與 `elf_linking`、想真正動手修 LLVM backend 的工程師。目標是能獨立加一條 custom instruction、能寫 scheduling model、能送 patch 上游。

這是前兩門課的結尾合流。前兩門教你「看懂 RISC-V」與「看懂 ELF / linking」—— 本門把這些變成 **compiler 工程師的 day-to-day 技能**：SelectionDAG / GlobalISel / TableGen / MIR / Scheduler / MC layer。

## 為什麼學這個？

- **SiFive job spec 的核心**：「add new RISC-V extensions」「suggest new compiler optimizations」，全部靠這門課的能力。
- **LLVM 是主流**：RISC-V 目前業界主力 compiler 已經偏 LLVM（雖然 GCC 仍重要）。能改 LLVM backend 是門檻也是護城河。
- **沒有現成中文資源**：LLVM backend 的學習資料稀少，讀 source code 是唯一的路。這門課給你「讀 source 的地圖」。
- **能送 upstream 的人極少**：全球每年能貢獻 LLVM RISC-V 的大概 100 人。你做到就是那 100 人之一。

## 本課與前兩門的關係

```
riscv          (ISA 本身)
     ↓
elf_linking    (binary format + relocation)
     ↓
compiler_backend  (compiler 產出 binary 的後半段)
     ↓
perf_bench + yocto  (後續應用)
```

**建議先讀完前兩門**。否則 Ch 8 講 `RISCVInstrInfo.td` 時會卡在「custom extension 是什麼」，Ch 17 講 MC layer 時會卡在「assembler 語法跟 encoding 怎麼對應」。

## 課程地圖

### Part 0 — 起步
- [Ch 0 環境搭建：build LLVM + llc + opt + llvm-mc](./00-environment-setup.md)

### Part 1 — LLVM IR 與 Pass
- [Ch 1 LLVM IR 心法](./01-llvm-ir-mindset.md)
- [Ch 2 Pass manager：legacy vs new](./02-pass-manager.md)
- [Ch 3 IR optimization 地圖：GVN / LICM / InstCombine](./03-ir-optimization-map.md)

### Part 2 — SelectionDAG Pipeline
- [Ch 4 SelectionDAG 總論](./04-selectiondag-overview.md)
- [Ch 5 Legalization：type + DAG](./05-legalization.md)
- [Ch 6 Instruction selection：pattern matching](./06-instruction-selection.md)

### Part 3 — TableGen 與 RISC-V Target
- [Ch 7 TableGen 語言速成](./07-tablegen-language.md)
- [Ch 8 RISCVInstrInfo.td 走讀](./08-riscv-instrinfo-td.md)
- [Ch 9 加一條 custom instruction 端到端](./09-adding-custom-instruction.md)

### Part 4 — MIR 與低階優化
- [Ch 10 GlobalISel vs SelectionDAG：為什麼在遷移](./10-globalisel-vs-selectiondag.md)
- [Ch 11 Machine IR (MIR)](./11-machine-ir.md)
- [Ch 12 Register allocator：greedy / fast](./12-register-allocator.md)
- [Ch 13 Scheduler 與 scheduling model](./13-scheduler-and-sched-model.md)

### Part 5 — 向量、Intrinsic、Inline Asm
- [Ch 14 Intrinsic → codegen 全流程](./14-intrinsic-codegen.md)
- [Ch 15 RVV codegen 與 VSETVLI 放置](./15-rvv-codegen.md)
- [Ch 16 Inline assembly 與 constraint](./16-inline-assembly.md)

### Part 6 — MC、GCC 對照、Upstream
- [Ch 17 MC layer：assembler / disassembler / streamer](./17-mc-layer.md)
- [Ch 18 GCC 對照篇：machine description / match.pd](./18-gcc-backend-comparison.md)
- [Ch 19 如何送 upstream：review 流程與文化](./19-upstream-contribution.md)
- [Ch 20 RISC-V target 源碼地圖](./20-source-code-map.md)

### Part 7 — 實戰
- [練習 A：讀懂一個 LLVM pass](./practice-a-read-a-pass.md)
- [練習 B：加一條 pseudo-instruction](./practice-b-add-pseudo-instruction.md)
- [Final Project：加一個 custom extension 端到端](./final-project-add-extension.md)

## 學習方式建議

1. **LLVM source 放手邊**：<https://github.com/llvm/llvm-project>。本課每章都會引用某個 file / function。**不讀 source 學不會 backend**。
2. **寫 micro-testcase**：看 IR → asm 的對應，最快的方法是寫 10 行 C、`clang -emit-llvm -S` 產 IR、`llc -march=riscv64` 產 asm，三者對照。
3. **善用 `-debug-only=<name>`**：LLVM 有豐富的 debug output。例如 `llc -debug-only=isel hello.ll` 印出 instruction selection 過程。
4. **對照 `riscv` / `elf_linking` 的章節**：當課內提到「這對應 RISC-V 的 XXX」時，回去翻前課。
5. **做 final project**：加一個 custom extension 的完整流程（spec → TableGen → codegen → test）是本課的終點。比 40 章口頭知識值錢。

## 本課不涵蓋什麼

- **Frontend 細節**：Clang 的 AST / template / name mangling 不碰（`compiler_frontend` 的範圍）。
- **LLVM IR 的完整 spec**：只抓到 backend 相關的那 60%。想深究請讀 LangRef.
- **LLVM pass 的完整列表**：太多了。我們只講 backend 關鍵 pass + 示範幾個代表 IR pass。
- **JIT / MCJIT / ORC**：LLVM JIT 另一個大世界，不在 static compiler 的主線。
- **GPU / OpenCL 的 backend**：雖然也基於 LLVM，但跟 RISC-V 關注點不同。

## 參考資料

**官方：**
- **LLVM Documentation**：<https://llvm.org/docs/>
- **LLVM Programmer's Manual**：<https://llvm.org/docs/ProgrammersManual.html>
- **Writing an LLVM Backend**：<https://llvm.org/docs/WritingAnLLVMBackend.html>（官方 tutorial，稍舊但仍有用）
- **TableGen overview**：<https://llvm.org/docs/TableGen/>
- **RISC-V in LLVM**：<https://llvm.org/docs/RISCVUsage.html>

**課外文章 / 演講：**
- **"2018 LLVM Dev Meeting" YouTube 頻道**：每年都有 RISC-V 相關 talk
- **LLVM Discourse**：<https://discourse.llvm.org>，active 社群
- **Alex Bradbury 的部落格**：RISC-V + LLVM 的早期推手，文章值得讀

**書：**
- 《Getting Started with LLVM Core Libraries》— Bruno Cardoso Lopes（偏 frontend 但 backend 部分乾淨）
- 《LLVM Essentials》— Suyog Sarda（過時但概念清楚）
- **沒有一本真正好的 LLVM backend 書**。Source + docs 是主力。

**關鍵 source 檔案（本課反覆引用）：**

```
llvm/lib/Target/RISCV/
├── RISCV.td                     ← top-level target description
├── RISCVInstrInfo.td             ← main instruction definitions
├── RISCVInstrInfoV.td            ← Vector extension
├── RISCVInstrInfoXSf*.td         ← SiFive vendor extensions
├── RISCVISelLowering.cpp         ← lowering to SelectionDAG
├── RISCVISelDAGToDAG.cpp         ← SelectionDAG → MachineInstr
├── RISCVFrameLowering.cpp        ← prologue/epilogue
├── RISCVTargetMachine.cpp        ← top-level backend entry
├── RISCVInsertVSETVLI.cpp        ← (RVV) vsetvl insertion pass
├── MCTargetDesc/RISCVMCCodeEmitter.cpp  ← MC encoding
└── AsmParser/RISCVAsmParser.cpp  ← 語法 parse
```

**每個檔案的 grep 你應該嘗試一次**。讀熟這 15 個檔，你就是 RISC-V LLVM 專家。
