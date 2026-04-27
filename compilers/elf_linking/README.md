# ELF 與 Linking：從 relocation 到 relaxation 的全景

> 給已經會寫 C、看得懂組語、想徹底搞懂「`.o` → `.so` → 可執行檔」中間發生了什麼的系統軟體工程師。目標是看到任何 link error 能立刻判斷出在哪層、能寫 linker script 把 ELF 佈到任意地址、能解釋 RISC-V 的 linker relaxation 是怎麼回事。

這是 `riscv` 的後續課程。ELF 跟 linking 是所有 compiler / toolchain / kernel / firmware 工程師的隱形地基 —— 你每天在用、卻幾乎沒人從頭系統化學過。本課以 RISC-V 為主 target，但 80% 概念對 x86-64 / ARM 也適用。

## 為什麼學這個？

- **Link error 是工程師最沒生產力的時刻**：多數人看到 `undefined reference` / `relocation overflow` / `recompile with -fPIC` 只會複製貼上去 Google。學完這門課你會在五秒內定位問題。
- **RISC-V 的 linker relaxation 是獨有的怪獸**：x86 / ARM 的 linker 基本不改指令；RISC-V 的 linker 會**主動把兩條指令縮成一條、調整 branch offset**，這讓整個 linking 過程跟傳統思維不一樣。SiFive / T-Head 工程師這個不懂沒辦法工作。
- **shared library 與 PIE 的運作是 Linux 的日常**：你每天用的 `ls`、`python`、`node` 全是動態連結的 PIE。`_dl_runtime_resolve` 是你每次 `printf()` 都走過的路，但多數人根本沒看過它長什麼樣。
- **TLS / visibility / LTO 的交互是無止盡的 bug 源頭**：現代軟體日益依賴這些機制，了解它們能讓你 debug 時少走 99% 的彎路。
- **寫 mini linker 是真正理解的終點**：跟前課的 emulator 一樣，能寫出來才算真懂。

## 本課與 `riscv` 的關係

`riscv` Ch 3 講 pseudo-instruction 時埋了很多伏筆 —— `auipc + addi` / `auipc + jalr` / `R_RISCV_PCREL_HI20` / `R_RISCV_RELAX`。**這門課就是把那些伏筆全部兌現**。如果你還沒讀過 `riscv` 的 Ch 3、Ch 5、Ch 16，建議先補。

## 課程地圖

### Part 0 — 起步
- [Ch 0 環境搭建：readelf / objdump / nm / ld / lld](./00-environment-setup.md)

### Part 1 — ELF 格式基礎
- [Ch 1 ELF 三層結構：header / section / segment](./01-elf-three-layers.md)
- [Ch 2 Section vs Segment：為什麼要分兩套](./02-section-vs-segment.md)
- [Ch 3 Symbol Table 與 String Table](./03-symbol-and-string-table.md)

### Part 2 — Static Linking
- [Ch 4 靜態連結流程：resolution → relocation → layout](./04-static-linking-flow.md)
- [Ch 5 Relocation type 總論](./05-relocation-types.md)
- [Ch 6 RISC-V 專屬 relocation 與 linker relaxation](./06-riscv-relaxation.md)
- [Ch 7 R_RISCV_ALIGN 與 relaxation 的副作用](./07-align-and-relaxation-traps.md)

### Part 3 — Linker Script
- [Ch 8 Linker script 語法與心法](./08-linker-script-basics.md)
- [Ch 9 MEMORY / SECTIONS / PROVIDE 的陷阱](./09-linker-script-gotchas.md)
- [練習 A：手刻 linker script](./practice-a-linker-script.md)

### Part 4 — Dynamic Linking
- [Ch 10 動態連結全貌：GOT / PLT / .dynamic](./10-dynamic-linking.md)
- [Ch 11 PIC / PIE 與 code model](./11-pic-pie-code-model.md)
- [Ch 12 TLS Model：LE / IE / GD / LD](./12-tls-models.md)
- [Ch 13 Lazy Binding 與 _dl_runtime_resolve](./13-lazy-binding.md)

### Part 5 — 進階議題
- [Ch 14 Visibility、LTO 與符號](./14-visibility-and-lto.md)
- [Ch 15 DWARF debug info 與 section 佈局](./15-dwarf-and-debug.md)
- [Ch 16 ld / gold / lld / mold：四個 linker 的取捨](./16-linker-implementations.md)
- [Ch 17 實戰 debug：讀懂真實 link error](./17-debug-real-link-errors.md)
- [練習 B：Debug 一個 relax 炸掉的 bug](./practice-b-relax-gone-wrong.md)

### Part 6 — 寫 linker
- [Ch 18 Mini linker 的結構設計](./18-mini-linker-design.md)

### Part 7 — 整合專案
- [Final Project：靜態 linker 支援 R_RISCV_CALL + relaxation](./final-project-mini-linker.md)

## 學習方式建議

1. **全程 hands-on**：ELF 是 binary format，**只讀文字學不會**。每章都要 `readelf` / `objdump` 一遍。
2. **練習用小程式**：不要拿 Linux kernel 練。`hello.c` + `hello.s` + 幾個 dummy `.a` 就夠做完前 13 章。
3. **RISC-V 為主、x86 為輔**：本課所有範例用 `riscv64-unknown-elf-*` 或 `riscv64-linux-gnu-*`。但偶爾對照 x86-64 的 objdump，你會發現很多 pattern 相似。
4. **讀 spec**：
   - SysV ELF ABI（共通 ELF 格式）
   - RISC-V ELF psABI（RISC-V 專屬的 relocation、ABI 等）
   - Linker script 參考：GNU LD manual（`info ld`）
5. **慢慢 build up**：不要一章 30 分鐘就跳。ELF 格式很多欄位，每一個都有故事。Ch 1–3 可能要讀兩次才懂。

## 本課不涵蓋什麼

- **完整 DWARF 規格**：這可以寫一本書。Ch 15 只講「跟 ELF section 的互動」。
- **debugger 實作**：那是 `gdb` 的事。
- **COFF / Mach-O / PE**：Windows 跟 macOS 的 binary format 不在範圍內（但多數概念共通）。
- **完整的 C++ name mangling**：會提 Itanium ABI 的 mangling rule，但不深究 RTTI / vtable 佈局。
- **JIT / runtime code generation**：是另一個深坑，跳過。

## 參考資料

**一手資料：**
- **System V Application Binary Interface**（ELF 本體）：<https://refspecs.linuxfoundation.org/elf/gabi4+/contents.html>
- **RISC-V ELF psABI**：<https://github.com/riscv-non-isa/riscv-elf-psabi-doc>
- **GNU LD manual**：`info ld` 或 <https://sourceware.org/binutils/docs/ld/>
- **LLVM LLD docs**：<https://lld.llvm.org>

**書：**
- 《Linkers and Loaders》— John Levine（1999，但概念永恆）
- 《程式設計師的自我修養：連結、裝載與庫》— 俞甲子等（中文經典，x86 版）
- 《ELF-64 Object File Format》— Hewlett-Packard 的精簡文件

**工具 manual：**
- `readelf(1)`, `objdump(1)`, `nm(1)`, `ld(1)`, `ldd(1)`, `ldconfig(8)`
- 每一個都值得通讀 manual 一次

**原始碼：**
- GNU binutils source: <https://sourceware.org/git/binutils-gdb.git>
- LLVM LLD source: <https://github.com/llvm/llvm-project/tree/main/lld>
- mold source（新世代 linker，乾淨）: <https://github.com/rui314/mold>
