# Final Project — Mini Static Linker

> 目標：寫一個支援 `R_RISCV_CALL` + relaxation 的 static linker。能把 2-3 個 `.o` 連成能在 spike + pk 跑的 hello world。完成後你是「真正懂 linker 的人」，而不只是會讀 spec 的人。

## 為什麼這是好 final project

1. **整合前 18 章**：ELF parse、symbol resolution、relocation、relaxation、ELF emit 全部用到
2. **對 SiFive 面試有直接 value**：**能寫 linker 的人極少**。100 人投 SiFive，3 人有能力做這個
3. **incremental**：最小版 1 週、完整版 3 週
4. **放 GitHub**：可見的工程能力證明

## MVP 定義

**最小可用版本**：

- 讀 2 個 RV64 `.o` 檔（ELF64）
- 處理以下 relocation type：
  - `R_RISCV_CALL`
  - `R_RISCV_PCREL_HI20`
  - `R_RISCV_PCREL_LO12_I`
  - `R_RISCV_PCREL_LO12_S`
  - `R_RISCV_64` (data reference)
- 基本 symbol resolution（undefined / multiple def 錯誤）
- 輸出能跑的 ELF executable
- 能跑：

```bash
./mini_ld hello.o write_host.o -o hello.elf
spike pk hello.elf
# hello from my linker
```

估算：約 1500-3000 行 C/C++/Rust。

## Stretch goals

**進階（每個約 +500-1000 行）**：

1. **Linker relaxation**：`call` → `jal` 的轉換 + cascade 更新
2. **`.a` archive**：lazy inclusion
3. **Simple linker script**：支援 ENTRY 跟 basic SECTIONS
4. **Full relocation set**：加 `R_RISCV_ADD*` / `SUB*`
5. **R_RISCV_ALIGN 處理**
6. **錯誤訊息**：像 GNU ld 那樣漂亮的 error message

## 分階段 milestone

### Week 1 — 基礎

**M1: ELF parser**

- 用你選的語言寫 ELF64 的 struct
- 實作 `parse_elf(path) -> InputFile`
- 測試：parse `hello.o`，驗證欄位跟 `readelf` 輸出一致

**M2: Symbol + Section 資料結構**

- 讀 symbol table
- 讀 section content
- 讀 relocation table

**M3: Simple emit**

- 能把讀進來的單個 `.o` re-emit 成一樣的檔案
- 驗證：`./mini_ld input.o -o output; diff input.o output` bit-identical

### Week 2 — Link logic

**M4: Section merge**

- 把多個 input `.o` 的 `.text` / `.data` / `.rodata` 合併
- 決定 VA（`.text` from 0x10000, `.data` follows aligned）

**M5: Symbol resolution**

- 建 global symbol table
- 處理 `STB_GLOBAL` 衝突
- error 對 undefined reference

**M6: Basic relocation**

- `R_RISCV_64`（填絕對地址）
- `R_RISCV_PCREL_HI20` / `LO12`
- 測試：用 GNU ld 的輸出 diff，驗證 bit pattern 一致

### Week 3 — Executable emit + 測試

**M7: Produce executable**

- 建 ELF header（`ET_EXEC`）
- 建 program headers（PT_LOAD）
- 對齊 page boundary

**M8: 跑 hello world**

- 寫個 baseline hello
- `./mini_ld hello.o write.o -o hello.elf && spike pk hello.elf`

### Week 4 (optional) — Relaxation

**M9: Relax `call` → `jal`**

- 識別 `R_RISCV_CALL` 且 target 在 ±1MiB
- 改寫 auipc+jalr → jal
- cascade 更新 offset

## 建議技術棧

```
語言:    C++17 或 Rust 2021
Build:   Cmake / Cargo
Test:    cmp / diff + 自動 bash test script
Output:  /tmp/*.elf
Parser:  手刻（不用 libelf），加強理解
CI:      GitHub Actions 跑測試
```

## 核心 class / struct（C++）

```cpp
struct ElfFile {
    ElfHeader header;
    std::vector<Section> sections;
    std::vector<Symbol> symbols;
    StringTable shstrtab;
    StringTable strtab;
};

struct Section {
    std::string name;
    uint32_t type;
    uint64_t flags;
    uint64_t vaddr;       // 在 output 的 VA
    std::vector<uint8_t> data;
    std::vector<Relocation> relocs;
    // 合併時記錄對應的 output section
    OutputSection *output;
    uint64_t offset_in_output;
};

struct Symbol {
    std::string name;
    uint8_t bind;
    uint8_t type;
    // defined or undefined
    Section *section;      // nullptr = undefined
    uint64_t value;        // offset in section (before layout)
                           // VA (after layout)
    uint64_t size;
};

struct Relocation {
    uint64_t offset;
    uint32_t type;
    Symbol *sym;
    int64_t addend;
};

struct Linker {
    std::vector<ElfFile> inputs;
    std::map<std::string, Symbol*> global_syms;
    std::vector<OutputSection> outputs;

    void read_inputs();
    void merge_sections();
    void resolve_symbols();
    void layout();
    void apply_relocations();
    void relax();  // optional
    void emit(std::string path);
};
```

## 關鍵 function：relocation apply

```cpp
void apply_R_RISCV_CALL(uint8_t *data, uint64_t offset, int64_t delta) {
    // auipc t1, ... (first 4 byte, U-type)
    // jalr ra, t1, ... (second 4 byte, I-type)

    int64_t hi = (delta + 0x800) >> 12;     // 注意 +0x800 trick
    int64_t lo = delta & 0xfff;

    // patch auipc: imm 在 bits [31:12]
    uint32_t *auipc = (uint32_t *)(data + offset);
    *auipc = (*auipc & 0xfff) | (hi << 12);

    // patch jalr: imm 在 bits [31:20]
    uint32_t *jalr = (uint32_t *)(data + offset + 4);
    *jalr = (*jalr & 0xfffff) | ((lo & 0xfff) << 20);
}
```

## 測試策略

```bash
# test/minimal/
# 最簡：一個 .c 產 .o，直接 link

# test/two_files/
# 兩個 .c：main.c 呼叫 foo.c 的 function

# test/data_ref/
# 跨 .c 的 global variable 引用

# test/relaxation/
# 能 relax 的 code pattern（若有做）

# test/hello/
# 完整 hello world for spike + pk
```

每個 test：

```bash
#!/bin/bash
# test/minimal/run.sh
./mini_ld input.o -o output.elf
diff <(readelf -a output.elf) <(readelf -a expected.elf)
```

`expected.elf` 是 GNU ld 產生的 reference。你的 output 不必 bit-identical，但 symbol / section 結構要一致。

## 陷阱預警

### 陷阱 1: ELF endianness

ELF 結構是 little-endian（你的 host 多半也是），直接 `memcpy` struct 通常 OK。但要 portable 的話每個欄位 `htole*()` 處理。

### 陷阱 2: alignment

寫 ELF 時各 section 要 page-align。忘了會 kernel load error。

### 陷阱 3: `.bss` 的 NOBITS

emit 時 `.bss` 只佔 VA、不寫 data 到檔案。file offset 的計算小心。

### 陷阱 4: program header 的數量

一般 executable 至少 2 個 PT_LOAD（RX + RW）。加上 PT_PHDR 指向自己。

### 陷阱 5: entry point

`e_entry` 要是 `_start` 的 VA，不是 offset。

### 陷阱 6: HI/LO pair 的 label 關聯

`R_RISCV_PCREL_LO12_I` 的 symbol 是 label（指向 HI20 指令）。你要維護一個 map 從 label 地址 → 對應 HI20 relocation 的 target/addend。

## README 建議結構

```markdown
# mini-riscv-ld

A minimal static linker for RV64 ELF, from scratch in C++.

## Goal

Link .o files into a runnable spike/qemu executable,
supporting R_RISCV_CALL, PCREL_HI20/LO12, and basic relaxation.

## Features

- [x] ELF64 RISC-V parser
- [x] Symbol resolution (global, weak, multiple-def errors)
- [x] R_RISCV_CALL, R_RISCV_PCREL_HI20/LO12_I/LO12_S, R_RISCV_64
- [x] call → jal relaxation
- [x] Hello world runs on spike + pk

## Non-features (intentional)

- No dynamic linking
- No linker script
- No archive (.a)
- No C++ specific (vtable, etc)

## Architecture

[your ASCII diagram here]

## Build & Run

```bash
make
./mini_ld -o hello.elf hello.o sys.o
spike pk hello.elf
```

## Tests

See `tests/`. Each test has `expected.elf` from GNU ld for diff.

## Lessons Learned

- ELF structure is simpler than I thought, but emit needs care
- Relaxation cascade is the hardest part
- ...

## References

- SysV ABI spec
- RISC-V ELF psABI
- LLD source (especially lld/ELF/Arch/RISCV.cpp)
- mold early commits for inspiration
```

## 評估標準

**60 分**：能 parse ELF，能 link 單個 `.o` round-trip

**75 分**：能 link 2 個 `.o` 產生 executable，能跑 spike hello world

**85 分**：處理 R_RISCV_CALL + PCREL pair + 基本 error message

**95 分**：支援 call → jal relaxation

**100 分**：能 link 自己（self-hosting linker）

60 分就是面試亮點。85 分以上面試官會追問實作細節。

## 時間預估

- **MVP (60 分)**：5-10 天
- **M1-M8 (85 分)**：2-3 週
- **M9 relaxation (95 分)**：+1-2 週
- **Self-hosting (100 分)**：+1 個月

大部分人做到 75-85 分就夠用。

## 面試展示

面試時：

1. Live demo：run `./mini_ld`、看 output 跑
2. 30 秒 architecture overview
3. 挑一個 tricky 部分深入（relocation / relaxation）
4. 對比 GNU ld 講「這裡我簡化了，因為 ...」
5. 講「下次我會做 ...」

這種節奏勝過 90% 空談技術的候選人。

## 推薦參考

**讀它們的早期 commit 或 code**：

- **mold**: 早期幾百個 commit 特別值得
- **chibild**: 日本工程師的迷你 linker（可能已改名）
- **Rui Ueyama's talks**: YouTube 有他講 linker 設計

**不要 copy-paste**。讀完理解，自己重寫。

## 完成後

```bash
# 你的 repo
git push origin main

# 履歷加一行
"Built a minimal static linker for RISC-V ELF in 3000 lines of C++,
supporting R_RISCV_CALL and call→jal linker relaxation.
Link self-hosted hello world runs on Spike + pk."
```

寄履歷給 SiFive / T-Head / Rivos 附這個 repo 連結。**這是面試的強力武器**。

## 結語

這是 `elf_linking` 的終點。你從「什麼是 ELF」走到「我寫了一個 ELF linker」。

下一門課：**`compiler_backend`**。有了 RISC-V + ELF + linker 的基礎，看 LLVM backend 會是暢通無阻的旅程。

**走到這裡，你離 SiFive offer 只剩一哩路。**

加油。
