# Ch 1 — ELF 三層結構：header / section / segment

> 目標：把 ELF 這個 binary 格式看透。讀完你能閉眼畫出 ELF 的三層結構、能解釋 section header 跟 program header 各自給誰看、能從 file 的 byte offset 直接定位到任一欄位。

## 為什麼叫 ELF

**ELF = Executable and Linkable Format**。1993 年 AT&T 為 System V Unix 設計，後來幾乎所有 Unix-like 系統都用。Linux、FreeBSD、Solaris、甚至早期 BeOS 都用 ELF；Windows 用 PE、macOS 用 Mach-O 是少數例外。

ELF 的精神：**同一個格式，支援四種角色**：

1. **Relocatable file** (`.o`)：compiler/assembler 的輸出。還沒定 address。
2. **Executable file**：linker 的輸出、kernel exec 的輸入。
3. **Shared object file** (`.so`)：可被動態連結的 library。也可以當 executable 跑（PIE）。
4. **Core dump file**：程式死掉時 kernel 生的快照。

**一個 format 做這麼多事**，所以很複雜 —— 每個欄位都要想「在哪個角色下用到」。

## 三層結構總覽

```
┌──────────────────────────────────────────────────┐
│ ELF Header (固定 64 byte, ELF64)                  │ ← 第一層：告訴你後面怎麼讀
├──────────────────────────────────────────────────┤
│ Program Header Table (segment 描述)               │ ← 第二層：runtime view
│   (可選，executable / shared object 才有)         │
├──────────────────────────────────────────────────┤
│                                                   │
│             Sections 或 Segments 的實體資料        │
│                                                   │
│   .text, .data, .rodata, .bss, ...               │
│                                                   │
├──────────────────────────────────────────────────┤
│ Section Header Table (section 描述)               │ ← 第三層：linker view
│   (可選，但 `.o` 跟大多 executable 都有)          │
└──────────────────────────────────────────────────┘
```

三層的關鍵：

- **ELF Header**：永遠在 offset 0，固定大小。它告訴你 program header / section header 在哪。
- **Program Header Table**：通常緊跟 ELF header。描述**要 load 到 memory 哪些東西**。
- **Section Header Table**：通常在檔案尾端。描述**每個 section 的屬性**（name、type、size、flags...）。

記住：**資料本體在中間，兩個 metadata 表一頭一尾**。

## ELF Header：所有東西的起點

`/usr/include/elf.h` 裡定義（ELF64）：

```c
typedef struct {
    unsigned char  e_ident[16];   // magic + class + data + version...
    Elf64_Half     e_type;        // ET_REL / ET_EXEC / ET_DYN / ET_CORE
    Elf64_Half     e_machine;     // EM_X86_64 / EM_RISCV / EM_AARCH64 / ...
    Elf64_Word     e_version;
    Elf64_Addr     e_entry;       // entry point (VA)
    Elf64_Off      e_phoff;       // program header table 在檔案的 offset
    Elf64_Off      e_shoff;       // section header table 在檔案的 offset
    Elf64_Word     e_flags;       // arch-specific flags
    Elf64_Half     e_ehsize;      // ELF header 本身大小 (64)
    Elf64_Half     e_phentsize;   // 每個 program header entry 大小 (56)
    Elf64_Half     e_phnum;       // program header entry 數
    Elf64_Half     e_shentsize;   // 每個 section header entry 大小 (64)
    Elf64_Half     e_shnum;       // section header entry 數
    Elf64_Half     e_shstrndx;    // section name string table 的 index
} Elf64_Ehdr;
```

64 byte，排列緊湊。

### e_ident：16 byte 的識別區

```
offset   content                meaning
0-3      7F 45 4C 46           magic "\x7fELF"
4        1 / 2                 CLASS: 1=32-bit, 2=64-bit
5        1 / 2                 DATA: 1=little-endian, 2=big-endian
6        1                     EI_VERSION = 1 (EV_CURRENT)
7        00                    OSABI (0=System V)
8        0                     ABI version
9-15     00...                 padding
```

`readelf -h` 印的 "Magic:" 那行就是這 16 byte。

**前 5 byte 永遠不變**：`7F 45 4C 46 02`（ELF64）或 `7F 45 4C 46 01`（ELF32）。第 6 byte 決定 big/little endian。

### e_type：ELF 的角色

```
ET_NONE   0    無類型
ET_REL    1    Relocatable（.o, .a 裡的每個 member）
ET_EXEC   2    Executable（傳統 position-dependent 可執行檔）
ET_DYN    3    Shared object（.so） OR PIE executable
ET_CORE   4    Core dump
```

**現代 Linux binary 多半是 `ET_DYN`**（PIE），不是 `ET_EXEC`。這是為了 ASLR。Ch 11 講。

### e_machine：ISA 標識

```
EM_X86_64  62    x86-64
EM_AARCH64 183   ARM AArch64
EM_RISCV   243   RISC-V (32 跟 64 共用這個)
EM_386     3     i386
EM_ARM     40    ARM 32-bit
```

RISC-V 用 `e_flags` 的 low 3 bit + RVC bit 區分 ilp32 / lp64 / 是否 compressed。

### 重要欄位：e_phoff / e_shoff / e_shstrndx

- `e_phoff`：program header 表在哪。通常是 `0x40`（緊跟 ELF header）。
- `e_shoff`：section header 表在哪。通常在檔案尾端。
- `e_shstrndx`：哪個 section 是存 section 名稱的 string table（通常叫 `.shstrtab`）。

```bash
riscv64-linux-gnu-readelf -h hello | grep -E "Start of (program|section)"
```

## 第二層：Section Header Table

每個 section 有一個 header，描述它：

```c
typedef struct {
    Elf64_Word   sh_name;        // 在 shstrtab 裡的 offset (index)
    Elf64_Word   sh_type;        // 分類: PROGBITS / SYMTAB / STRTAB / ...
    Elf64_Xword  sh_flags;       // 屬性: ALLOC / WRITE / EXECINSTR / ...
    Elf64_Addr   sh_addr;        // virtual address (load 後)
    Elf64_Off    sh_offset;      // 在檔案的 offset
    Elf64_Xword  sh_size;        // 大小
    Elf64_Word   sh_link;        // 指向其他 section 的 link
    Elf64_Word   sh_info;        // 額外資訊（不同 type 有不同意義）
    Elf64_Xword  sh_addralign;   // alignment
    Elf64_Xword  sh_entsize;     // 若是 table (symtab/strtab)，每個 entry 大小
} Elf64_Shdr;
```

### sh_type 的分類

```
SHT_NULL      0    無效
SHT_PROGBITS  1    程式資料（.text, .data, .rodata）
SHT_SYMTAB    2    符號表（.symtab，static linking 用）
SHT_STRTAB    3    字串表（.strtab, .shstrtab）
SHT_RELA      4    帶 addend 的 relocation 表
SHT_HASH      5    symbol hash 表
SHT_DYNAMIC   6    dynamic linking 資訊（.dynamic）
SHT_NOTE      7    .note.* 類
SHT_NOBITS    8    佔地址但不佔檔案（.bss）
SHT_REL       9    不帶 addend 的 relocation 表（RISC-V 基本不用）
SHT_DYNSYM    11   dynamic symbol table (.dynsym)
...
```

**`SHT_NOBITS` 是 `.bss` 的關鍵**：uninitialized data 不需要存檔案裡（全 0），只留 size 資訊、loader 自動填零。這讓 ELF 檔可以比實際 memory footprint 小。

### sh_flags 的常用組合

```
SHF_WRITE      0x1    可寫
SHF_ALLOC      0x2    runtime 要 load 到 memory（ALLOC = allocated）
SHF_EXECINSTR  0x4    可執行
SHF_MERGE      0x10   可以 merge 相同內容（rodata 優化）
SHF_STRINGS    0x20   內容是 C string
SHF_TLS        0x400  thread-local storage
```

典型組合：

```
.text:   ALLOC | EXECINSTR          → "AX"
.data:   ALLOC | WRITE              → "WA"
.rodata: ALLOC | MERGE | STRINGS    → "AMS"
.bss:    ALLOC | WRITE              → "WA" (但是 NOBITS)
```

## 第三層：Program Header Table

Program header 只給 **loader** 看。它描述「runtime 要 load 什麼到 memory」：

```c
typedef struct {
    Elf64_Word   p_type;         // PT_LOAD / PT_DYNAMIC / PT_INTERP / ...
    Elf64_Word   p_flags;        // R / W / X
    Elf64_Off    p_offset;       // 在檔案的 offset
    Elf64_Addr   p_vaddr;        // 要 load 到的 virtual address
    Elf64_Addr   p_paddr;        // physical address (embedded only, userspace 忽略)
    Elf64_Xword  p_filesz;       // 檔案裡佔多少 byte
    Elf64_Xword  p_memsz;        // memory 裡佔多少 byte (> filesz 的部分是 .bss)
    Elf64_Xword  p_align;        // 對齊
} Elf64_Phdr;
```

### p_type 的核心類型

```
PT_NULL      0    無效
PT_LOAD      1    要 load 到 memory（主角）
PT_DYNAMIC   2    指向 .dynamic section，動態連結用
PT_INTERP    3    指定 dynamic linker 路徑（/lib/ld-linux-*.so）
PT_NOTE      4    .note.*
PT_PHDR      6    指向 program header table 自己
PT_TLS       7    TLS template
PT_GNU_STACK 6474e551  stack 的 RWX 設定（記憶體不可執行 NX）
PT_GNU_RELRO 6474e552  read-only after relocation (security)
PT_GNU_EH_FRAME 6474e550 unwind info
```

**多數 `PT_LOAD` 就是你要關心的**。典型的 executable 有 2–4 個 PT_LOAD：

```
PT_LOAD (R-X)   → code 段（.text, .rodata, ...）
PT_LOAD (RW-)   → data 段（.data, .bss, .got, ...）
PT_LOAD (R--)   → 只讀 data（GNU_RELRO 相關）
```

`NX`（No eXecute）很重要：讓 data 段不可執行、code 段不可寫 —— 防 code injection 攻擊。

### filesz < memsz 的意義

```
PT_LOAD 1 filesz=0x168  memsz=0x260
```

memsz > filesz 差的部分是 `.bss`。Loader load 時：

1. 把檔案的 `p_offset..+filesz` 複製到 `p_vaddr..+filesz`
2. 把 `p_vaddr+filesz..+memsz` 填零

這樣 `.bss` 不佔檔案空間、只佔記憶體。**compile 一個 `int big[1000000];` 的程式，binary 大小不會增加 4 MB**。

## 檔案的實際 byte 佈局

一個典型的 executable，按 byte offset 從低到高：

```
0x0000  ELF Header                          (64 byte)
0x0040  Program Header Table                (e.g., 9 entries × 56 = 504 byte)
0x0238  .interp                              /lib/ld-linux-riscv64-lp64d.so.1
0x0254  .note.gnu.build-id
0x0278  .hash
        .gnu.hash
        .dynsym
        .dynstr
        .gnu.version
        .gnu.version_r
        .rela.dyn
        .rela.plt
        .init
0x05e0  .plt                                 (code)
0x0620  .text                                (code, main 在這)
0x0850  .fini
0x0858  .rodata                              (string constants)
--- page boundary (0x1000 aligned) ---
0x1000  .init_array
        .fini_array
        .dynamic
        .got
0x1060  .data
0x1068  .bss                                 (SHT_NOBITS, 不佔檔案)
0x3860  Section Header Table                 (29 entries × 64 = 1856 byte)
0x3F80  .shstrtab                            (section 名字表)
```

**Page boundary 重要**：因為不同權限的 PT_LOAD 要分頁對齊。code 段 R-X 結束後要對齊 0x1000 才能開始 data 段 RW-。這就是為什麼每個 binary 中間有一塊「空」—— pad 到下一個 page。

## 用 readelf 驗證

```bash
riscv64-linux-gnu-readelf -h hello       # ELF header
riscv64-linux-gnu-readelf -S hello       # section headers
riscv64-linux-gnu-readelf -l hello       # program headers
riscv64-linux-gnu-readelf -x 1 hello     # dump section #1 (hex)
```

**多用 `-x`** 直接看 raw bytes。例如：

```bash
riscv64-linux-gnu-readelf -x .interp hello
# Hex dump of section '.interp':
#   0x00000238 2f6c6962 2f6c642d 6c696e75 782d7269 /lib/ld-linux-ri
#   0x00000248 7363763 642d6c70 36346400          scv64-lp64d.
```

這就是 kernel exec 時會讀的 interpreter 路徑字串。

## `.o` 跟 executable 的差別

`.o` 檔（relocatable）通常**沒有 program header**：

```bash
riscv64-linux-gnu-readelf -h hello.o
# Type: REL (Relocatable file)
# ...
# Number of program headers: 0
```

因為 `.o` 還沒決定要 load 到哪、沒概念叫 "segment"。只有 section。

反過來：**stripped executable 可以沒有 section header**，但通常還是會留（對 debugger 友善）。

## 三個問題幫你檢驗理解

### Q1：相同檔案裡，可以有兩個 `.text` section 嗎？

可以。例如 `-ffunction-sections` 會讓每個 function 各自一個 section，叫 `.text.main`、`.text.foo` 等。linker 可以合併也可以分開。

### Q2：一個 section 可以同時 in 多個 segment 嗎？

可以。例如 `.text` 通常在 `PT_LOAD` 也在 `PT_PHDR` 的掃描範圍中（如果 phdr 放在 PT_LOAD 內）。

### Q3：沒有 section header table 的 ELF 能跑嗎？

能。Loader 只看 program header。Section header 是給 linker / debugger 看的。極端 stripped binary 可以砍掉。

## 常見坑

1. **把 section 跟 segment 當一回事**：兩者有 overlap 但不一樣。Ch 2 專章處理。
2. **ELF32 跟 ELF64 結構不同**：`Elf32_Ehdr` 跟 `Elf64_Ehdr` 欄位大小不同。寫 parser 時必須先讀 class byte 再挑結構。
3. **以為 `.bss` 佔檔案**：不佔。`SHT_NOBITS` 的意義就是「不在檔案」。
4. **搞反 endian**：RISC-V 預設 little-endian，但 spec 允許 big-endian 實作。解析前先查 e_ident[5]。
5. **用 `objdump -s` 以為是 readelf**：`objdump -s` 印所有 section 的 hex dump。要看 header 用 `readelf`。

## 動手練習

1. 用 `readelf -h hello` 找出 `e_shoff`，然後用 `dd if=hello bs=1 skip=$e_shoff count=64 | xxd` 肉眼確認第一個 section header（應該是 `SHT_NULL` 全 0）。
2. 用 `readelf -S hello` 找 `.text` 的 `Offset`，然後 `dd` + `xxd` 看 raw bytes，對照 `objdump -d hello` 找 `_start` 的機器碼確認 match。
3. 故意寫一個 C 程式 `int big[1000000];`，build 後比較 binary size 跟 `.bss` section size。驗證 bss 不佔檔案。
4. 寫小 Python script，只用 `struct.unpack` 解 ELF header 所有欄位，印出人類可讀格式。不用 pyelftools。
5. 用 `readelf -l` 找出 RELRO segment 的 vaddr 跟 size，跟 `.got` section 對照看它覆蓋哪幾個 section。

## 自我檢核

- [ ] 我能閉眼畫出 ELF 三層結構：header、program headers、sections、section headers
- [ ] 我能列 `e_type` 的 4 種主要類型以及什麼 ELF 會是哪種
- [ ] 我能解釋 `sh_flags` 裡 ALLOC / WRITE / EXECINSTR 的組合與 `.text` / `.data` / `.rodata` / `.bss` 的對應
- [ ] 我知道 `p_filesz` 跟 `p_memsz` 差距的意義
- [ ] 我能用 `readelf` 三個 flag 把 ELF 拆到欄位層級

下一章專門處理「Section 跟 Segment 差在哪」這個最容易混的題目。看完你會徹底分清兩個 view。

→ [Ch 2 Section vs Segment：為什麼要分兩套](./02-section-vs-segment.md)
