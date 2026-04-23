# Ch 18 — DWARF debug info

> 目標：理解 DWARF 是什麼、ELF 裡的 `.debug_*` 段各自放什麼、DIE 樹狀結構、line number program 怎麼算、location expression 怎麼描述「變數在哪」。能用 `readelf --debug-dump` 與 `dwarfdump` 看懂 debug info。

## DWARF 的角色

Ch 1 的直覺版：DWARF 是 compiler 留給 debugger 的地圖。這章我們看地圖上到底畫了什麼。

DWARF 回答這些問題：

1. **位址 0x1149 是哪一行 source？** — `.debug_line`
2. **`main` 這個名字對應什麼位址、什麼型別？** — `.debug_info`
3. **局部變數 `x` 在哪？** — `.debug_info` 裡的 location expression
4. **這個 struct 的佈局是什麼？** — `.debug_info` 的 type DIE
5. **crash 時怎麼 unwind stack？** — `.debug_frame` / `.eh_frame`

DWARF 的設計哲學：**盡量不預設語言 / 平台**。它的觀念建構在「compilation unit」、「DIE」、「location expression」這些抽象上，然後 C / C++ / Rust / Go 各自用這些 primitive 表達自己的型別系統。

## ELF 裡的 DWARF 段

```bash
readelf -WS your_binary | grep debug
```

常見段：

| 段 | 內容 |
|---|---|
| `.debug_info` | DIE 樹主體（函式、變數、型別、scope） |
| `.debug_abbrev` | DIE 的 abbreviation 表（壓縮用） |
| `.debug_line` | 位址 ↔ source line 對應的 bytecode program |
| `.debug_str` | 字串池（名字、檔名） |
| `.debug_line_str` | `.debug_line` 用的字串池（DWARF 5） |
| `.debug_aranges` | 位址範圍索引（加速查表） |
| `.debug_ranges` / `.debug_rnglists` | 非連續位址範圍的 list |
| `.debug_loc` / `.debug_loclists` | Location list（變數位置隨 PC 變時用） |
| `.debug_frame` / `.eh_frame` | Call frame info（stack unwinding 用） |
| `.debug_pubnames` / `.debug_pubtypes` | 全域 symbol/type index（現代不太用） |
| `.debug_macro` | 預處理 macro 定義資訊 |
| `.debug_types` / DWARF 5 `.debug_info` | 型別單獨 section（optional） |

ELF header 不會解釋這些段，它們只是 compiler 丟進去的 blob。linker 不動、loader 不 map 到記憶體。GDB 用 `open()` + `mmap()` 自己讀 binary。

## DIE（Debugging Information Entry）

DWARF 的核心 data structure 是 **DIE**（DWARF Information Entry）— 一個「描述某個東西」的節點，有 tag、attributes、可以有 children。

看 hello.c（Ch 0）的 DWARF：

```bash
readelf --debug-dump=info hello
```

會印出類似（節錄）：

```
<0><0xb>: Abbrev Number: 1 (DW_TAG_compile_unit)
   DW_AT_producer            : GNU C17 11.4.0 -mtune=generic -O0 -g
   DW_AT_language            : 29 (C11)
   DW_AT_name                : hello.c
   DW_AT_comp_dir            : /tmp
   DW_AT_low_pc              : 0x1149
   DW_AT_high_pc             : 0x74
   DW_AT_stmt_list           : 0x0

<1><0x29>: Abbrev Number: 2 (DW_TAG_subprogram)
   DW_AT_external            : 1
   DW_AT_name                : main
   DW_AT_decl_file           : 1
   DW_AT_decl_line           : 8
   DW_AT_prototyped          : 1
   DW_AT_type                : <0x65>
   DW_AT_low_pc              : 0x1174
   DW_AT_high_pc             : 0x49
   DW_AT_frame_base          : 1 byte block: 9c   (DW_OP_call_frame_cfa)

<2><0x4d>: Abbrev Number: 3 (DW_TAG_variable)
   DW_AT_name                : sum
   DW_AT_decl_file           : 1
   DW_AT_decl_line           : 11
   DW_AT_type                : <0x65>
   DW_AT_location            : 2 byte block: 91 6c   (DW_OP_fbreg -20)

<1><0x65>: Abbrev Number: 5 (DW_TAG_base_type)
   DW_AT_byte_size           : 4
   DW_AT_encoding            : 5 (signed)
   DW_AT_name                : int
```

翻譯：

```
compile_unit "hello.c" @ 0x1149–(0x1149+0x74)
├── subprogram "main" @ 0x1174–(0x1174+0x49), returns type at 0x65
│   └── variable "sum" at DW_OP_fbreg -20, type at 0x65
└── base_type "int" (4 bytes, signed)
```

**這就是 DIE 樹**。每個 DIE 有：

- **tag**：`DW_TAG_compile_unit`、`DW_TAG_subprogram`、`DW_TAG_variable`、`DW_TAG_base_type`、等等
- **attributes**：`DW_AT_name`、`DW_AT_type`、`DW_AT_location`、等等
- 某些 DIE 可以有 children（巢狀關係）

## 常見 DIE tag

| Tag | 意義 |
|---|---|
| `DW_TAG_compile_unit` | 一個 .c 檔（或 Rust crate、Go package） |
| `DW_TAG_subprogram` | 函式 |
| `DW_TAG_variable` | 變數（global / local） |
| `DW_TAG_formal_parameter` | 函式參數 |
| `DW_TAG_base_type` | `int`、`char`、`double` 等基本型別 |
| `DW_TAG_pointer_type` | 指標 |
| `DW_TAG_structure_type` | struct |
| `DW_TAG_member` | struct 的 field |
| `DW_TAG_array_type` | 陣列 |
| `DW_TAG_typedef` | typedef |
| `DW_TAG_lexical_block` | `{ ... }` 範圍 |
| `DW_TAG_inlined_subroutine` | 被 inline 的函式 |

## 常見 attribute

| Attribute | 意義 |
|---|---|
| `DW_AT_name` | 名字 |
| `DW_AT_type` | 型別（指向另一個 DIE） |
| `DW_AT_low_pc` / `DW_AT_high_pc` | 涵蓋的位址範圍 |
| `DW_AT_decl_file` / `DW_AT_decl_line` | 宣告位置 |
| `DW_AT_location` | 變數在哪（DWARF expression） |
| `DW_AT_byte_size` | 大小 |
| `DW_AT_encoding` | base_type 的 encoding（signed / unsigned / float / ...） |
| `DW_AT_frame_base` | frame pointer 的 expression |

## DWARF location expression — 變數在哪

`DW_AT_location` 的值是一段 mini bytecode（叫 **DWARF expression**），描述怎麼算出變數位址（或值）。

看例子：

```
DW_AT_location : 2 byte block: 91 6c
```

`91` 是 `DW_OP_fbreg`、`6c` 是 signed LEB128 編碼的 `-20`。整段意思：「frame base 加上 -20 就是這個變數的位址」。所以變數在 `rbp - 20`。

常見 op：

| Op | 意義 |
|---|---|
| `DW_OP_addr` | 絕對位址（後跟 8 byte 位址） |
| `DW_OP_reg0` ~ `DW_OP_reg31` | 變數就在某個暫存器 |
| `DW_OP_fbreg <offset>` | frame base + offset |
| `DW_OP_breg0` ~ `DW_OP_breg31 <offset>` | register + offset |
| `DW_OP_deref` | 堆疊頂當位址再 deref |
| `DW_OP_plus_uconst <N>` | 堆疊頂加 N |
| `DW_OP_piece <N>` | 複合變數（一部分在這、一部分在那） |

這是個 **stack-based VM**。GDB 的 interpreter 執行這段 bytecode，最後 stack 上留下的就是變數位置（或值）。

### 為什麼要這麼複雜

因為 optimization 讓變數位置會變：

- 前 10 條指令：`x` 在 `rax`
- 接下來：`x` 被存回 stack，位置 `rbp - 20`
- 再後來：`x` 被搬到 `rbx`

`.debug_loc` / `.debug_loclists` 存的是 **location list** — 一連串 `(pc range, expression)` pair，描述「在這段 PC 範圍內，用這個 expression 找 x」。GDB 查變數時要先用當前 PC 找對的 expression。

**這就是 `<optimized out>` 的來源**：某個 PC 範圍 compiler 沒記錄變數位置（或變數已經 dead），location list 查不到，GDB 只好印 `<optimized out>`。

## Line number program — 位址 ↔ 行號

`.debug_line` 是一個 bytecode program。它跑完會吐一張表：

```
address | file | line | column | is_stmt | ...
0x1149    1      8      0        1
0x1158    1      9      5        1
0x1165    1      10     5        1
...
```

**為什麼不用簡單的 table？** 因為 line table 會很大（每條指令一筆）。DWARF 用 bytecode 壓縮 — 寫一個小程式 emit 那張表，通常比 table 本身小一個數量級。

看工具：

```bash
readelf --debug-dump=decodedline hello
```

會吐出 decoded 的表：

```
File name                            Line Number    Starting Address ...
hello.c                              8              0x1149
hello.c                              9              0x1158
hello.c                              10             0x1165
...
```

GDB 收到 `break main` 時：

1. 查 `DW_TAG_subprogram` 找 main → low_pc = 0x1149
2. 找 0x1149 對應的 file:line → `hello.c:8`
3. 下斷點

收到 SIGTRAP 在 0x1165：

1. 查 line table → `hello.c:10`
2. 告訴你「停在 hello.c:10」

## 其他 section 的角色

- **`.debug_abbrev`**：abbrev 表讓 `.debug_info` 可以用緊湊 encoding（一個 DIE 不需每次都把 tag+attribute 名稱整串寫出，用 abbrev number 引用預先定義的 pattern）。
- **`.debug_str`**：所有字串集中放這。DIE 的 `DW_AT_name` 不存字串本體，存 offset 進 `.debug_str`。
- **`.debug_aranges`**：「哪個 compilation unit 涵蓋哪段位址」的快速索引。大 binary 有成千上萬個 CU，要快速定位必用。
- **`.debug_frame` / `.eh_frame`**：Ch 21 講 unwinding 時會深入。

## DWARF 版本

- **DWARF 2**（1993）：奠基。
- **DWARF 3**（2005）：加 C++ 支援、更多 tag。
- **DWARF 4**（2010）：壓縮、`.debug_types` 分離。
- **DWARF 5**（2017）：大整理 — `.debug_line_str`、`.debug_loclists`、`.debug_rnglists`、`.debug_macro`、split DWARF（分出 `.dwo` 檔）。

現代 gcc 預設 DWARF 5（有些發行版還用 DWARF 4）。

### Split DWARF：`-gsplit-dwarf`

大型 C++ 專案的 DWARF 可能比 code 本身還大幾倍。`split DWARF` 把 `.debug_*` 的大部分拆到獨立的 `.dwo` 檔，不進最終 binary。連結器用 `.debug_info` 裡的 reference 連到 `.dwo`。

效果：binary 小很多、連結快很多。代價：debug 時 gdb 要同時找得到 `.dwo` 檔。

## 工具

| 工具 | 用途 |
|---|---|
| `readelf --debug-dump=<section>` | 原始 DWARF dump，可讀性還行 |
| `dwarfdump` | 更詳細（libdwarf / elfutils 提供） |
| `objdump -W` / `--dwarf` | 類似 readelf |
| `addr2line` | 快速「位址→行號」查詢 |
| `llvm-dwarfdump` | LLVM 出的，輸出漂亮 |

範例：

```bash
addr2line -e hello 0x1149
# /tmp/hello.c:8

readelf --debug-dump=aranges hello
readelf --debug-dump=line hello
llvm-dwarfdump hello > dwarf-dump.txt
```

## 實務上幾個你該記住的事

1. **`<optimized out>` 不是 bug**，是 optimizer 沒留變數 / compiler 沒 emit 完整 location。-O0 幾乎不會遇到，-O2 很常見。
2. **function 被 inline**：會變 `DW_TAG_inlined_subroutine`，有自己的 low_pc/high_pc 與 call site 資訊。Ch 21 會談。
3. **decl_file / decl_line** 是宣告位置，不是 code 位址。macro 展開或 template 可能讓這跟 `.debug_line` 的 pc→file:line 不同。
4. **Debug info 可以放到 `.gnu_debuglink`** 指向外部檔案。就是 strip 後「symbol 在別的 .debug 檔」的機制。
5. **Compressed DWARF**：`.debug_info` 可以 zlib 壓縮，ELF section 名會變 `.zdebug_info`。gdb 能解。

## 最小 DWARF parser 概念

如果你要自己寫 debugger，最少要能讀：

1. **.debug_abbrev**：記住每個 CU 的 abbrev table。
2. **.debug_info**：依 abbrev 解 DIE 樹。
3. **.debug_str**：解 `DW_FORM_strp` 的 string reference。
4. **.debug_line**：跑 line number program，拿到 pc → file:line 的 map。

建議用 `libdwarf`（elfutils）或 `libdw` 這類 library，不要自己刻（規格夠複雜，DWARF 5 文件幾百頁）。Final project 會用 `libdwarf` 組一個 minidbg。

## 常見坑

1. **`-g0` / `-g1`**：`-g` 預設是 `-g2`，`-g1` 只有部分資訊、`-g0` 等於沒加。
2. **`-g3`**：加上 macro info。`.debug_macro` 會出現。
3. **編譯時 `-fdebug-prefix-map=/build=/local`** 可以改變 DWARF 裡的路徑（讓 gdb 能找到 source）。
4. **DWARF 5 對老 gdb**：GDB 8.0 以上才好支援 DWARF 5。搭配舊 gdb + 新 compiler 會看到怪行為。
5. **stripped binary 裡還有 `.note.gnu.build-id`**：這是對照 `/usr/lib/debug/.build-id/XX/YYYY...` 找 debug info 的 key。
6. **Template 的 DIE 會爆炸**：C++ template 每個實例化都是一個 DIE。大專案的 `.debug_info` 可以 GB 級。

## 動手練習

1. 用 `readelf --debug-dump=info hello | less`，瀏覽整個 DIE 樹。標記出 main、local 變數、int 型別的 DIE。
2. 用 `readelf --debug-dump=decodedline hello`，確認「`main` 的第一行」對應哪個位址。
3. 用 `addr2line -e hello <addr>` 試幾個位址，與 `.debug_line` 比對一致。
4. 用 `-g3` 編譯並 inspect `.debug_macro`。
5. 手工解 `DW_OP_fbreg -20`（查 DWARF 規格，確認 encoding）：`91` = `DW_OP_fbreg`，`6c` 是 SLEB128。手解 `6c` = -20？（提示：SLEB128 的 0x6c 是 sign-extended 成 -20）。
6. `-O2` 重編，再看 DWARF。觀察 `DW_TAG_inlined_subroutine`、location list、可能多出的 range entries。

## 延伸閱讀

- DWARF 5 spec：<https://dwarfstd.org/doc/DWARF5.pdf>（免費 PDF，認真讀前兩章就夠用）
- Eli Bendersky's blog 有「How debuggers work」、「DWARF line number program」兩系列，入門絕佳
- libdwarf 文件：<https://www.prevanders.net/libdwarf.pdf>

## 自我檢核

- [ ] 我能說出 `.debug_info` / `.debug_line` / `.debug_abbrev` / `.debug_str` 各自的角色
- [ ] 我能看懂 `readelf --debug-dump=info` 的輸出，認出 DIE tag 跟 attribute
- [ ] 我知道 `DW_AT_location` 是 DWARF expression bytecode
- [ ] 我知道 `<optimized out>` 的來源是 location list 查不到
- [ ] 我會用 `addr2line` 做快速位址反查
- [ ] 我知道 split DWARF (`-gsplit-dwarf`) 是什麼、為什麼要

下一章回到動作層：breakpoint 的**實作**。software 斷點怎麼寫、hardware 斷點用哪些暫存器、watchpoint 跟 breakpoint 有什麼不同。

→ [Ch 19 Breakpoint 的實作](./19-breakpoint-implementation.md)
