# Ch 38 — DWARF 除錯資訊剖析

> **目標**：看進 debug info 的真身——DWARF。理解 DIE（debugging information entry）樹、`.debug_info`/`.debug_line`/`.debug_abbrev` 等 section、line number program（行號↔位址）、location expression（變數在哪）。學完你會懂 GDB 怎麼把位址翻譯成「`hello.c:10` 的變數 `x` 在 `rbp-4`」——這是 Ch 41 自寫 debugger 的理論基礎。

> **環境**：GDB 13/14，gcc 12+（DWARF 5），`readelf` / `objdump` / `llvm-dwarfdump`，Linux x86_64。

## 為什麼要看進 DWARF

整門課你一直在用 debug info（Ch 0 的「地圖」）：`break main` 找位址、`print x` 找變數、`bt` 走 stack——全靠它。但你一直把它當黑盒。這章打開黑盒，看 DWARF 到底長什麼樣、怎麼編碼這些資訊。

理解 DWARF 的回報：

- 懂為什麼最佳化讓變數 `<optimized out>`（location expression 的範圍限制，Ch 32）
- 懂 GDB 怎麼做 source-level step（line program，Ch 5）
- 懂 backtrace 怎麼 unwind（CFI，Ch 10）
- **能自己寫 debugger**（Ch 41）——你得自己讀 DWARF

DWARF 是除錯的「資料層」，這章是 Part 8 內部原理的基石。

## 先建立直覺：一棵描述程式的樹

DWARF 用一棵樹描述你的程式——每個東西（編譯單元、函式、變數、型別）是樹上一個節點，叫 **DIE**（Debugging Information Entry）。

```
   DW_TAG_compile_unit  (hello.c)
   ├── DW_TAG_subprogram (main)          函式
   │   ├── DW_AT_name = "main"
   │   ├── DW_AT_low_pc = 0x1149         起始位址
   │   ├── DW_TAG_variable (x)           區域變數
   │   │   ├── DW_AT_name = "x"
   │   │   ├── DW_AT_type → int          型別（指向另一個 DIE）
   │   │   └── DW_AT_location = rbp-4    它在哪
   │   └── DW_TAG_variable (y) ...
   ├── DW_TAG_base_type (int)            型別
   └── DW_TAG_structure_type (Point) ... struct 型別
       ├── DW_TAG_member (x) ...
       └── DW_TAG_member (y) ...
```

每個 DIE 有：

- **tag**（`DW_TAG_*`）：它是什麼（函式、變數、型別…）
- **attributes**（`DW_AT_*`）：它的屬性（名字、型別、位址、location…）
- **children**：巢狀的 DIE（函式裡的變數、struct 的成員）

GDB 的 `ptype`（Ch 9）走的就是這棵 type DIE 樹；`info functions` 列的是 subprogram DIE；`print x` 查的是 variable DIE 的 location。**你學過的每個符號操作，底層都是在查這棵樹。**

## 看真實的 DWARF

```c
// dwarf_demo.c — gcc -g -O0 dwarf_demo.c -o dwarf_demo
struct Point { int x, y; };
int add(int a, int b) {
    int sum = a + b;
    return sum;
}
int main(void) {
    struct Point p = {3, 7};
    int r = add(p.x, p.y);
    return r;
}
```

看 `.debug_info`（DIE 樹）：

```bash
readelf --debug-dump=info dwarf_demo
# 或更好讀：
llvm-dwarfdump dwarf_demo | less
```

```
0x0000000b: DW_TAG_compile_unit
              DW_AT_producer ("GNU C17 ...")
              DW_AT_name     ("dwarf_demo.c")
              DW_AT_low_pc   (0x1149)
0x0000002a:   DW_TAG_subprogram
                DW_AT_name   ("add")
                DW_AT_low_pc (0x1149)
                DW_AT_high_pc(0x18)
0x00000043:     DW_TAG_formal_parameter
                  DW_AT_name     ("a")
                  DW_AT_type     (0x... "int")
                  DW_AT_location (DW_OP_fbreg -20)    # a 在 frame base - 20
0x00000051:     DW_TAG_variable
                  DW_AT_name     ("sum")
                  DW_AT_location (DW_OP_fbreg -28)
...
```

看到了——`add` 是個 subprogram DIE，`a` 是 formal_parameter，`DW_AT_location = DW_OP_fbreg -20` 說「`a` 在 frame base 減 20 的地方」。這就是 `print a` 怎麼知道去哪讀的。

## 主要的 `.debug_*` sections

DWARF 不是一塊，是 ELF 裡多個 section 分工：

| section | 內容 |
|---|---|
| `.debug_info` | DIE 樹（主體：函式、變數、型別） |
| `.debug_abbrev` | DIE 的「縮寫表」（壓縮用，定義每種 DIE 有哪些屬性） |
| `.debug_str` | 字串池（名字都放這，DIE 用 offset 引用） |
| `.debug_line` | line number program（行號 ↔ 位址） |
| `.debug_loclists` | location list（變數在不同 PC 範圍在哪——最佳化用） |
| `.debug_rnglists` | 位址範圍 |
| `.eh_frame` / `.debug_frame` | CFI（call frame info，backtrace unwinding 用） |

```bash
readelf -S dwarf_demo | grep debug    # 看有哪些 debug section
```

`.debug_abbrev` 是壓縮機制——DIE 不直接存「我有哪些屬性」，而是引用 abbrev 表的一個編號，省空間。`.debug_str` 同理把字串集中。所以 DWARF raw bytes 很難手讀，要靠 `readelf`/`llvm-dwarfdump` 解碼。

## Line number program：行號↔位址

`.debug_line` 是一個**虛擬機程式**（不是表！），執行它會「產生」行號↔位址的對應表。為什麼用程式而非表？因為表太大，用程式（一連串 advance_pc、advance_line 指令）壓縮。

```bash
readelf --debug-dump=decodedline dwarf_demo
```

```
File name   Line   Address
dwarf_demo.c   2    0x1149
dwarf_demo.c   3    0x1151
dwarf_demo.c   4    0x115b
dwarf_demo.c   8    0x1162
...
```

這張表就是 Ch 5 講的 source-level step 的依據——GDB single-step 後查這表，知道「現在 PC 對應哪一行」、「這一行涵蓋哪段位址」。Ch 41 你寫 mini debugger 的 step 功能，就要自己讀這張表。

> 為什麼是「程式」而非「表」：line table 可能有上萬筆。DWARF 把它編碼成一連串小指令（`DW_LNS_advance_pc`、`DW_LNS_advance_line`、`DW_LNS_copy`），執行這些指令才「展開」成表。`readelf --debug-dump=rawline` 看原始程式，`decodedline` 看展開後的表。

## Location expression：變數在哪

`DW_AT_location` 告訴 GDB 一個變數在哪。它不是單純「在 rbp-4」，而是一個**小型堆疊機的表達式**（location expression），能表達各種情況：

```
DW_OP_fbreg -20          # frame base + (-20)：最常見，stack 上的變數
DW_OP_reg3               # 直接在暫存器 rbx 裡（最佳化常見）
DW_OP_addr 0x4040        # 固定位址（全域變數）
DW_OP_breg6 -8           # 暫存器 rbp + (-8)
```

最佳化的關鍵：**location list**（`.debug_loclists`）——一個變數在不同 PC 範圍可能在不同地方：

```
變數 x:
  PC 0x1149..0x1160: DW_OP_reg3      （在 rbx）
  PC 0x1160..0x1180: DW_OP_fbreg -8  （被 spill 到 stack）
  PC 0x1180..0x11a0: (無)             （optimized out！）
```

這就是 Ch 32 `<optimized out>` 的真相——當前 PC 落在「無」的範圍，GDB 就說 optimized out。`readelf --debug-dump=loclists` 看這些。entry value（Ch 32）也是 location expression 的一種（`DW_OP_entry_value`）。

## CFI：backtrace 怎麼 unwind

`.eh_frame`（或 `.debug_frame`）存 **CFI**（Call Frame Information）——描述「在每個 PC，怎麼從當前 frame 找到 caller 的 frame」（Ch 10 的 unwinding）。

```bash
readelf --debug-dump=frames dwarf_demo
# 或
objdump --dwarf=frames dwarf_demo
```

CFI 對每個 PC 範圍記錄：CFA（canonical frame address，caller 的 SP）怎麼算、return address 在哪、各暫存器的舊值在哪。GDB 的 backtrace 就是執行這些規則一層層往上爬。`-fomit-frame-pointer`（Ch 10）下沒有 rbp 鏈，全靠 CFI——所以 CFI 不全（最佳化）backtrace 就壞。

## 一個完整的「GDB 怎麼做 print x」

把 DWARF 串起來，回答 Ch 7 的 `print x` 底層：

```
1. 當前 PC = 0x1155（GDB 從 ptrace GETREGS 拿）
2. 查 .debug_info：哪個 subprogram DIE 的 low_pc..high_pc 含 0x1155？→ add
3. 在 add 的 children 找 name="x" 的 variable DIE
4. 讀它的 DW_AT_location
5. location 是 location list？查當前 PC 0x1155 落在哪段 → DW_OP_fbreg -28
6. 算 frame base（從 DW_AT_frame_base，通常是 CFA 或 rbp）
7. 位址 = frame_base - 28
8. ptrace 讀那個位址的記憶體（4 bytes，因為 type DIE 說 x 是 int）
9. 依 type DIE 把 4 bytes 解釋成 int 顯示
```

每一個你打過的 `print`，背後都是這套 DWARF 查詢。Ch 41 你會親手實作其中關鍵步驟。

## 踩雷集錦

1. **手讀 DWARF raw bytes**：別。`.debug_abbrev` + `.debug_str` 的引用機制讓 raw bytes 無法直讀。用 `readelf --debug-dump` 或 `llvm-dwarfdump`。
2. **以為 line table 是表**：它是程式（虛擬機 opcode），要執行才展開成表。`rawline` vs `decodedline`。
3. **DWARF 版本差異**：DWARF 4 和 5 的 section 名與編碼不同（`.debug_loc` → `.debug_loclists`、`.debug_ranges` → `.debug_rnglists`）。gcc 12+ 預設 5。工具要支援對應版本。
4. **`<optimized out>` 怪 DWARF 不全**：其實 DWARF 誠實記錄了「這段 PC 變數不存在」——是最佳化真的把它消除了，不是 DWARF 漏記。
5. **CFI 在 `.eh_frame` 而非 `.debug_frame`**：`.eh_frame` 是 exception handling 用的（即使無 `-g` 也有，C++ 需要），`.debug_frame` 是 debug 專用。GDB 兩個都讀，`.eh_frame` 更常見。
6. **split DWARF（`.dwo`）**：`-gsplit-dwarf` 把 DWARF 拆到 `.dwo` 檔，`readelf` 主檔看不到完整資訊。

## 進階：再往深一層

- **`llvm-dwarfdump --verify`**：驗證 DWARF 正確性——debug 「為什麼 GDB 對這個 binary 行為怪」時用。
- **DWARF 表達式的圖靈完備性**：location expression 是個堆疊機，能做算術、條件——理論上很強大，實務上多數變數是簡單的 `fbreg`/`reg`。
- **`.debug_names` / `.gdb_index`**：加速符號查詢的索引（Ch 6/42），是額外的 section。
- **DWARF 5 的改進**：`.debug_line` 的 directory/file 編碼改進、`.debug_loclists`/`.debug_rnglists` 取代舊的、`.debug_addr` 位址池。
- **自己解析 DWARF**：用 `libdw`（elfutils）、`pyelftools`（Python）、`gimli`（Rust）讀 DWARF——Ch 41 mini debugger 會用其中一個。
- **DWARF 與其他格式**：Windows 用 PDB（不同格式同概念）、Go 早期用自己的。DWARF 是 Unix/Linux/嵌入式的標準。
- **debug fission / type units**：大型 C++ 的 DWARF 去重與拆分機制。

## 動手練習

1. 對 `dwarf_demo.c`，`llvm-dwarfdump` 或 `readelf --debug-dump=info` 看 DIE 樹，找出 `add` 的 subprogram DIE 與 `a`/`sum` 的 location。
2. `readelf --debug-dump=decodedline` 看 line table，對照原始碼行號與位址；在 GDB 用 `info line 3` 驗證一致。
3. `readelf -S | grep debug` 看有哪些 `.debug_*` section。
4. `-O2` 重編，看 `a`/`sum` 的 location 變成 location list（`readelf --debug-dump=loclists`），找出「在某 PC 範圍 optimized out」的證據。
5. `readelf --debug-dump=frames` 看 CFI，理解 backtrace 的 unwind 規則來源。
6. 對照本章「GDB 怎麼做 print x」的 9 步，在真實 binary 上手動走一遍（用 readelf 查每一步的資訊）。

## 本章重點整理

- DWARF 用 DIE 樹描述程式：每個 DIE 有 tag（是什麼）、attributes（屬性）、children；GDB 的符號操作都是查這棵樹。
- 分散在多個 section：`.debug_info`（DIE）、`.debug_abbrev`（縮寫）、`.debug_str`（字串）、`.debug_line`（行號程式）、`.debug_loclists`（變數位置）、`.eh_frame`（CFI）。
- line program 是虛擬機程式（非表），執行後展開成行號↔位址表——source-level step 的依據。
- location expression 是堆疊機表達式（`DW_OP_fbreg`/`reg`/`addr`）；location list 讓變數在不同 PC 在不同地方——`<optimized out>` 的真相。
- CFI（`.eh_frame`）描述怎麼 unwind 到 caller——backtrace 的依據。

## 自我檢核

- [ ] DWARF 用什麼資料結構描述程式？`ptype`/`print`/`bt` 各對應查什麼？
- [ ] line number 資訊為什麼存成「程式」而非「表」？
- [ ] `<optimized out>` 在 DWARF 層是怎麼表達的？
- [ ] backtrace unwind 靠哪個 section？跟 `-fomit-frame-pointer` 什麼關係？
- [ ] 為什麼 DWARF raw bytes 不能直接讀？用什麼工具？

## 延伸閱讀

### 規格

- **[DWARF Debugging Information Format v5](https://dwarfstd.org/doc/DWARF5.pdf)**
  - **讀哪裡**：§1–2（概論、DIE）、§6.2（line number program）、§2.6（location descriptions）、§6.4（CFI）。
  - **和本章的關聯**：本章每個概念的最終權威；Ch 41 寫 debugger 時要查。
  - **注意**：很厚，當 reference 查需要的章節，別從頭讀。

### 部落格 / 文章

- **[How debuggers work: Part 3 (Debugging information)](https://eli.thegreenplace.net/2011/02/07/how-debuggers-work-part-3-debugging-information)** — Eli Bendersky
  - **這篇說什麼**：用 pyelftools 實際解析 DWARF，找變數位置、行號。
  - **讀哪裡**：整篇；本章理論的可跑 code 版，Ch 41 的直接前置。
  - **為什麼值得讀**：把抽象的 DIE/location 變成你能跑的 Python。

- **[Introduction to the DWARF Debugging Format](https://dwarfstd.org/doc/Debugging-using-DWARF-2012.pdf)** — Michael Eager
  - **這篇說什麼**：DWARF 的友善導論（比 spec 好讀）。
  - **為什麼值得讀**：先讀這個再碰 spec，省很多力。

### 工具

- **[pyelftools](https://github.com/eliben/pyelftools)** / **[llvm-dwarfdump]**
  - **和本章的關聯**：解析 DWARF 的實用工具；Ch 41 mini debugger 用 pyelftools 讀 DWARF。

下一章看 breakpoint 與 single-step 的底層實作——INT3 怎麼 patch、硬體斷點怎麼用 debug register、single-step 怎麼做、displaced stepping 是什麼。

→ [Ch 39 Breakpoint / single-step 底層實作](./39-breakpoint-singlestep-internals.md)
