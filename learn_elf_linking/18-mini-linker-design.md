# Ch 18 — Mini linker 的結構設計

> 目標：為 final project 鋪路。說明寫一個最小 static linker 的資料結構、演算法、常見設計抉擇。不會逐行教 code，給你架構圖。

## 要做到什麼

Final project 目標：**能把兩個 RISC-V `.o` 連成一個 executable**，支援：

- ELF64 RISC-V parse + produce
- 基本 relocation：`R_RISCV_CALL`、`R_RISCV_PCREL_HI20` / `LO12`
- Linker relaxation：`call` 縮 `jal`
- 輸出 executable 能在 spike + pk 或 qemu-user 跑

不求做完整的 GNU ld。目標：**跑一個 hello world**。

## 五個階段

你的 mini linker 大致這樣：

```
1. 讀 input (.o 檔)         → parse
2. 合併 sections            → merge + layout
3. 建立 output symbol table  → resolve
4. 執行 relaxation          → shrink
5. 填 relocation            → relocate
6. 寫出 output ELF          → emit
```

## 資料結構

需要的 struct（假設 C++ / Rust 風）：

```cpp
// 代表一個 input .o
struct InputFile {
    std::string name;
    Elf64_Ehdr header;
    std::vector<InputSection> sections;
    std::vector<Symbol> symbols;
    std::vector<Relocation> relocs;
    StringTable strtab;
};

// 代表一個 input section（.text of a.o 等）
struct InputSection {
    std::string name;
    uint64_t size;
    std::vector<uint8_t> content;
    uint32_t flags;         // ALLOC / WRITE / EXECINSTR
    InputFile *file;
    // 合併後的歸屬
    OutputSection *output;
    uint64_t offset_in_output;
};

// 合併後的 output section (.text, .data, ...)
struct OutputSection {
    std::string name;
    uint64_t vaddr;          // VA
    uint64_t size;
    uint32_t flags;
    std::vector<InputSection *> inputs;
};

// 符號
struct Symbol {
    std::string name;
    uint8_t bind;            // LOCAL / GLOBAL / WEAK
    uint8_t type;             // FUNC / OBJECT / ...
    InputSection *section;    // 定義所在 section，或 nullptr = undefined
    uint64_t value;           // offset_in_section (before merge) or vaddr (after)
    uint64_t size;
};

// Relocation
struct Relocation {
    uint64_t offset;          // offset in input section
    InputSection *section;    // 所屬 section
    uint32_t type;             // R_RISCV_...
    Symbol *symbol;            // 指向的 symbol
    int64_t addend;
};

// 全域 Linker
struct Linker {
    std::vector<InputFile> inputs;
    std::vector<OutputSection> outputs;
    std::map<std::string, Symbol *> global_symbols;  // 符號表
    uint64_t image_base = 0x10000;
};
```

## 階段 1：Parse ELF

讀 binary，填 `InputFile`。步驟：

```python
def parse(path):
    data = read_file(path)
    header = Elf64_Ehdr(data[:64])
    assert header.magic == b'\x7fELF'
    assert header.e_machine == EM_RISCV

    # 讀 section headers
    sections = []
    for i in range(header.e_shnum):
        off = header.e_shoff + i * header.e_shentsize
        shdr = Elf64_Shdr(data[off:off+64])
        section = InputSection(
            name=read_str(shstrtab, shdr.sh_name),
            content=data[shdr.sh_offset:shdr.sh_offset+shdr.sh_size],
            flags=shdr.sh_flags,
            # ...
        )
        sections.append(section)

    # 讀 symbol table
    symtab = find_section(sections, ".symtab")
    strtab = find_section(sections, ".strtab")
    for i in range(symtab.size // 24):
        sym = Elf64_Sym(symtab.content[i*24:(i+1)*24])
        # ...

    # 讀 relocations
    for sec in sections:
        if sec.name.startswith(".rela."):
            # each entry is 24 byte
            for i in range(sec.size // 24):
                rela = Elf64_Rela(...)
                # ...
```

## 階段 2：合併 sections + layout

把相同 name 的 input section 合併：

```python
def merge():
    for inp in inputs:
        for sec in inp.sections:
            if not (sec.flags & SHF_ALLOC):
                continue  # skip .debug_* etc
            out = find_or_create_output(sec.name)
            sec.offset_in_output = out.size
            out.size += sec.size
            out.inputs.append(sec)

    # 分配 VA
    vaddr = image_base
    for out in outputs:
        out.vaddr = align_up(vaddr, 0x1000)
        vaddr = out.vaddr + out.size

    # 計算每個 input section 的最終 VA
    for out in outputs:
        for sec in out.inputs:
            sec.vaddr = out.vaddr + sec.offset_in_output
```

簡化：按 section name alphabetical 排序。正式 linker 會照 linker script 排。

## 階段 3：Symbol resolution

```python
def resolve():
    for inp in inputs:
        for sym in inp.symbols:
            if sym.binding == STB_LOCAL:
                continue  # local 不進全域表
            if sym.section is None:  # undefined
                continue
            # 定義性 symbol
            if sym.name in global_symbols:
                existing = global_symbols[sym.name]
                # 簡化：multiple def → error
                if existing.binding != STB_WEAK and sym.binding != STB_WEAK:
                    error(f"multiple def of {sym.name}")
                # weak 處理略
            else:
                global_symbols[sym.name] = sym

    # 處理 undefined: 查 global_symbols
    for inp in inputs:
        for sym in inp.symbols:
            if sym.section is None and sym.binding != STB_LOCAL:
                if sym.name in global_symbols:
                    sym.resolved_to = global_symbols[sym.name]
                else:
                    error(f"undefined ref to {sym.name}")
```

**簡化版假設**：所有輸入是 `.o`，沒 `.a`。Final project 可以先不處理 archive。

## 階段 4：Relaxation（可選進階）

實作 `call` → `jal` 的 relax：

```python
def relax():
    for sec in all_sections:
        for reloc in sec.relocs:
            if reloc.type == R_RISCV_CALL:
                target_vaddr = resolve_symbol_vaddr(reloc.symbol)
                call_site_vaddr = sec.vaddr + reloc.offset
                offset = target_vaddr - call_site_vaddr
                if -1_MiB <= offset < 1_MiB:
                    # 可以 relax
                    mark_relax(reloc)
                    # 標記後 4 byte 要砍
```

Relax 後要重算地址 cascade 更新（見 Ch 6）。

**建議**：first pass 做 pure linker，不支援 relax。Ch 6 的內容留給進階。

## 階段 5：Relocation

對每個 reloc entry 按 type 計算並填值：

```python
def relocate():
    for sec in all_sections:
        for r in sec.relocs:
            P = sec.vaddr + r.offset
            S = resolve_symbol_vaddr(r.symbol)
            A = r.addend
            write_pos = sec.content[r.offset: r.offset + 4]

            if r.type == R_RISCV_CALL:
                # auipc + jalr pair, 8 byte
                delta = S + A - P
                hi = (delta + 0x800) >> 12
                lo = delta & 0xfff
                # patch auipc bit[31:12] = hi
                patch_u_type(sec.content, r.offset, hi)
                # patch jalr bit[31:20] = lo
                patch_i_type(sec.content, r.offset + 4, lo)

            elif r.type == R_RISCV_PCREL_HI20:
                delta = S + A - P
                hi = (delta + 0x800) >> 12
                patch_u_type(sec.content, r.offset, hi)

            elif r.type == R_RISCV_PCREL_LO12_I:
                # 找對應的 HI20 (symbol 是 label，指向 HI20 指令的位置)
                hi_reloc = find_pcrel_hi(r.symbol)
                delta = hi_reloc.target_vaddr + hi_reloc.addend - hi_reloc.site_vaddr
                lo = delta & 0xfff
                patch_i_type(sec.content, r.offset, lo)
            ...
```

**陷阱**：`PCREL_LO12_I` 的 symbol 是 auipc 的 label，要找回 HI20 才能算對。Ch 5 / Ch 6 有解釋。

## 階段 6：Emit ELF

寫一個 ELF64 executable：

```python
def emit(output_path):
    # 決定 program headers：R-X 一個、RW- 一個
    phdrs = []
    if code_sections:
        phdrs.append({
            type=PT_LOAD,
            flags=PF_R | PF_X,
            vaddr=first_code_vaddr,
            filesz=code_size,
            memsz=code_size,
            align=0x1000,
        })
    if data_sections:
        # ... 類似

    # 組 ELF header
    ehdr = Elf64_Ehdr(
        e_ident=b'\x7fELF\x02\x01\x01\x00' + b'\0' * 8,
        e_type=ET_EXEC,
        e_machine=EM_RISCV,
        e_entry=resolve_symbol_vaddr(global_symbols['_start']),
        e_phoff=64,
        e_shoff=...,
        e_phnum=len(phdrs),
        e_shnum=...,
        ...
    )

    # 寫檔：ehdr | phdrs | sections content | shdrs
    with open(output_path, 'wb') as f:
        f.write(ehdr)
        for p in phdrs: f.write(p)
        # ... 對齊處理
        for sec in outputs: f.write(sec.content)
        # ... 寫 shdrs
```

這部分繁瑣但直接。**ELF64_Phdr 跟 Elf64_Shdr 的結構請查 Ch 1 或 `elf.h`**。

## 選擇語言

寫 mini linker 的建議語言：

- **C / C++**：跟 production linker 生態一致，可讀 GNU ld / LLD 當參考
- **Rust**：現代化、error handling 好、byte manipulation 安全
- **Python**：快速原型，效率差但 OK 小範圍
- **Go**：中間路線

我的推薦：**C++ 或 Rust**，跟 final project 定位一致（完成後能放 GitHub 當 portfolio）。

## 測試策略

Mini linker 的 bug 超難 debug。測試計畫：

1. **最小 case**：只有一個 `.o`，link 出一樣的 executable。驗證 parse + emit。
2. **兩個 .o call 彼此**：驗證 cross-file relocation。
3. **含 global variable**：驗證 `.data` 處理。
4. **含 external libc**：link fail，驗證 undefined error 訊息正確。
5. **跑 hello world in spike**：`./mylinker hello.o | spike pk -`

每個 case 都跟 GNU ld 的輸出 diff：

```bash
# Reference
riscv64-unknown-elf-ld -o hello.ref hello.o

# Mine
./mylinker hello.o -o hello.mine

# Diff 兩個 ELF
diff <(readelf -a hello.ref) <(readelf -a hello.mine)
```

目標：跑起來一樣。ELF 結構不必完全 bit-identical（linker 有些自由度），但邏輯要一致。

## 簡化假設

為了做得完，刪掉：

- Archive (`.a`) 支援
- Dynamic linking (PLT / GOT / `.dynamic`)
- C++ 功能（vtable、name mangling）
- Linker script（硬寫 code 裡）
- TLS
- DWARF merge
- Compressed relocation
- Endianness 切換

這些留給 production linker。

## 增量擴展路線

MVP 做到後，一步一步加：

1. Ch 6 的 relaxation（`call` 縮 `jal`）
2. linker script 簡易解析（只支援 SECTIONS + ENTRY）
3. 更多 relocation type（`R_RISCV_ADD*` / `SUB*`）
4. `.a` archive 支援
5. dynamic linking

每完成一個 milestone 是可以 showcase 的。**最終一份 5000 行 C++ linker 能 link 自己，是強大 portfolio**。

## 參考實作

可以 fork / 對照的開源 mini linker：

- **chibild**（日本工程師寫的 mini linker，C++）
- **tinyld**（玩具級）
- 你最好的參考：**mold 的早期 commit**（看他從零開始怎麼實作）

## 建議章節安排

寫這個 project，我的 README 建議結構：

```
# mini-riscv-linker

Minimal static linker for RV64 ELF, supporting R_RISCV_CALL + relaxation.

## Goal
Link two .o files into an executable that can run on spike + pk.

## Features
- ELF64 RISC-V parse / emit
- Symbol resolution
- R_RISCV_CALL / R_RISCV_PCREL_HI20 / LO12
- Linker relaxation (call → jal)
- ~3000 lines of C++

## Architecture
[diagram]

## Tests
...

## What I learned
...
```

## 動手練習

1. 把本章的 pseudocode 轉成你選擇語言的實際 code stub。
2. 手寫一個 `parse_elf()`，用 GNU ld link 出來的 `hello` 測試 round-trip（parse + emit 應該得到相同 binary）。
3. 手算一個 `R_RISCV_PCREL_HI20 + LO12_I` 的配對範例（Ch 5 那個公式），用 Python 驗證。
4. 寫幾個 test case：1 `.o` / 2 `.o` cross-ref / missing symbol / multi def。
5. 讀 mold 的 commit 歷史，找到「First working linker」的 commit，看它當時多少 code、什麼 feature。

## 自我檢核

- [ ] 我能列 linker 的 6 個階段與各自的資料結構
- [ ] 我知道怎麼 parse ELF64 header / section / symbol
- [ ] 我能寫 `R_RISCV_CALL` 的 relocation 計算
- [ ] 我能規劃 mini linker 的 MVP 跟擴展路線
- [ ] 我能用 GNU ld 輸出當 reference 跑 diff 測試

下一步：去做 final project。前 18 章的知識你已經有了，剩下的是把它們變 muscle memory。

→ [Final Project：靜態 linker 支援 R_RISCV_CALL + relaxation](./final-project-mini-linker.md)
