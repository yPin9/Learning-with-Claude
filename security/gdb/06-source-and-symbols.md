# Ch 6 — 原始碼與符號

> **目標**：搞懂 GDB 怎麼把位址翻譯成「函式名 / 檔案:行 / 變數名」，以及怎麼找到並顯示原始碼。釐清 symbol table（`.symtab`）與 debug info（DWARF）的分工、`list` 家族、symbol 查詢指令、找不到原始碼時怎麼辦。

> **環境**：GDB 13/14，Linux x86_64，gcc / clang。

## 為什麼要分清「符號」與「除錯資訊」

Ch 0 埋過這個伏筆：函式名 `add` 沒有 `-g` 也看得到，但行號和區域變數要 `-g` 才有。這背後是**兩套不同的資訊**，住在 ELF 的不同地方：

```
   ELF 執行檔
   ├── .text          機器碼
   ├── .symtab        symbol table：名字 ↔ 位址（函式、全域變數）
   │                  ← 連結用的、strip 會拿掉、但 -g 與否都在
   ├── .debug_info    DWARF：型別、區域變數、參數、lexical scope
   ├── .debug_line    DWARF：位址 ↔ 原始碼行號
   ├── .debug_str     DWARF：字串池
   └── ...
        ↑ 這些是 -g 才產生的「除錯資訊」
```

- **symbol table**（`.symtab` / `.dynsym`）：給 linker 和基本工具用的「名字 ↔ 位址」表。函式、全域變數的位址在這。`strip` 會移除 `.symtab`（但 `.dynsym`——動態符號——通常保留，否則動態連結會壞）。
- **debug info**（`.debug_*`，DWARF）：給 debugger 用的豐富資訊——每行原始碼對到哪段位址、每個區域變數放在哪、struct 長怎樣。`-g` 才有。

理解這個分工，你才懂為什麼 strip 過的 binary「函式名還在但變數沒了」（`.dynsym` 留著、`.debug_*` 沒了），以及 debuginfod 在補的到底是哪一塊（debug info）。

## 查符號：GDB 知道哪些東西？

```
(gdb) info functions               # 所有函式（含位址）
(gdb) info functions ^parse_       # 用 regex 篩
(gdb) info variables               # 所有全域/static 變數
(gdb) info line add                # add 對應的原始碼行與位址範圍
(gdb) info address add             # add 這個符號在哪（位址或 register/offset）
(gdb) info symbol 0x555555555149   # 反查：這個位址是什麼符號 + 偏移
(gdb) whatis add                   # add 的型別（函式簽章）
(gdb) ptype struct Node            # 型別的完整定義（Ch 9 深入）
```

`info symbol <位址>` 是逆向時的好朋友——拿到一個裸位址，反查它落在哪個函式 + 偏移：

```
(gdb) info symbol 0x555555555160
add + 23 in section .text
```

## `list`：看原始碼

```
(gdb) list                  # 列出當前位置附近 10 行
(gdb) list add              # 列出 add 函式附近
(gdb) list hello.c:20       # 列出 hello.c 第 20 行附近
(gdb) list 10,30            # 列出第 10 到 30 行
(gdb) list -                # 往回列（接續上次往前）
(gdb) list *0x555555555160  # 列出某位址對應的原始碼行
```

調整一次列幾行：`set listsize 30`。

> 重點：`list` 顯示的原始碼是 GDB **去檔案系統讀** `.c` 檔來顯示的——DWARF 裡只存了「行號↔位址」，**不存原始碼本身**。所以如果 `.c` 檔被搬走/刪掉/在另一台機器，`list` 會說 `No such file or directory`，即使 debug info 完好。下一節解決這個。

## 找不到原始碼：路徑問題

最常見的痛點：在 A 機器編譯（原始碼在 `/home/builder/proj/`），搬到 B 機器 debug（原始碼在 `/data/src/proj/`）。DWARF 裡記的是編譯時的絕對路徑 `/home/builder/proj/foo.c`，B 機器上不存在。

解法：

```
(gdb) directory /data/src/proj            # 加一個原始碼搜尋路徑
(gdb) set substitute-path /home/builder /data/src   # 路徑前綴替換（更精準）
(gdb) show directories
```

`set substitute-path 舊前綴 新前綴` 是最乾淨的做法：把 DWARF 裡的 `/home/builder/...` 自動換成 `/data/src/...`。CI build 出來的 binary 拿回本機 debug 必備。

## `addr2line`：GDB 之外的快速翻譯

不開 GDB 也能做位址→行號的翻譯，崩潰 log 分析常用：

```bash
addr2line -e ./myprog -f -C 0x1149
# add
# /home/user/hello.c:4
```

`-f` 顯示函式名、`-C` demangle C++ 名稱。當你手上只有一串崩潰位址（例如 backtrace log），`addr2line` 比開 GDB 快。原理和 GDB 的符號翻譯完全一樣——都讀 DWARF line table。

## demangle：C++ 的名字會被「絞」

C++ 為了支援 overload，編譯器會把 `void Foo::bar(int)` 編碼成 `_ZN3Foo3barEi` 這種 mangled name。GDB 預設會自動 demangle 顯示，但你會在某些場合看到原始 mangled 名：

```
(gdb) set print asm-demangle on      # 組語裡的符號也 demangle
(gdb) p (int)$rax                    # 偶爾要手動處理
```

命令列工具：`echo _ZN3Foo3barEi | c++filt` → `Foo::bar(int)`。Ch 29 會深入 C++ 符號。

## 載入額外符號

GDB 通常自動載入主程式與共享庫的符號，但有時要手動：

```
(gdb) symbol-file ./myprog.debug          # 載入 separate debug 檔
(gdb) add-symbol-file plugin.so 0x7ffff7a00000   # 手動指定載入位址（無自動資訊時）
(gdb) info sharedlibrary                   # 看已載入的共享庫與符號狀態
(gdb) sharedlibrary                        # 重新載入共享庫符號
```

`info sharedlibrary` 的 `Syms Read` 欄位告訴你哪些 `.so` 的符號讀到了（`Yes`）、哪些沒有（`No`，通常缺 debug 套件——這時 debuginfod 救援）。

## 一個完整的「找回原始碼」流程

CI 編出的 binary，符號齊但原始碼路徑對不上：

```
$ gdb -q ./myprog-from-ci
(gdb) break crash_func
(gdb) run
Breakpoint 1, crash_func () at /build/ci/src/parser.c:88
88      in /build/ci/src/parser.c          ← 找不到原始碼！只顯示路徑
(gdb) set substitute-path /build/ci/src /home/me/proj/src
(gdb) list
88          char *p = lookup(key);          ← 現在看得到了
```

## 踩雷集錦

1. **「No such file or directory」≠ 沒符號**：debug info 可能完好，只是原始碼檔案 GDB 找不到。用 `directory` / `set substitute-path`，別急著重編。
2. **strip 後 `info functions` 空空如也**：`.symtab` 被拿掉了。但 `.dynsym`（動態符號）可能還有，`info functions` 偶爾還能看到匯出函式。逆向時用 `info symbol <位址>` 反查仍有限。
3. **C++ 名字看起來像亂碼**：那是 mangled name，`set print demangle on`（預設開）或 `c++filt` 還原。
4. **共享庫的函式沒符號**：`info sharedlibrary` 看 `Syms Read` 是 No。裝對應 `-dbgsym` 套件或開 debuginfod。
5. **`list` 顯示的原始碼跟實際跑的對不上**：你 debug 的 binary 和當前 `.c` 不是同一份（改了 code 沒重編）。GDB 會用當前檔案內容配舊的行號，看起來錯位。重編。
6. **`info line` 與 `info address` 搞混**：`info line` 給「行↔位址範圍」，`info address` 給「符號的位置（可能是 register、stack offset、絕對位址）」。

## 進階：再往深一層

- **`.gdb_index` / `.debug_names`**：大型程式（chromium 等級）載入符號極慢。GDB 可用預建的索引 section 加速符號查詢。`gdb-add-index myprog` 產生，或編譯時 `-Wl,--gdb-index`。Ch 0 提過、Ch 42 細節。
- **`maint info symtabs` / `maint print symbols`**：maintenance 指令，看 GDB 內部的符號表結構——debug 「為什麼 GDB 找不到這個符號」時的終極工具。
- **partial symtab（psymtab）**：GDB 為了加速啟動，不會一次讀完所有 DWARF，而是先讀一個「部分符號表」，需要時才展開完整的。這是它能 debug 巨型程式的關鍵。`maint info psymtabs` 觀察。
- **`set debug symtab-create` / `set debug dwarf-read`**：開啟 DWARF 讀取的除錯輸出，看 GDB 怎麼解析符號。Ch 38 會用。

## 動手練習

1. 對 `hello_g` 與 `hello_nog`（Ch 0 編的）分別 `info functions` 與 `info variables`，比較差異，理解 symtab vs DWARF。
2. `info symbol <隨便一個 .text 位址>` 反查它落在哪個函式 + 偏移。
3. 故意把 `hello.c` 改名，重開 GDB `list`，看它抱怨找不到原始碼，再用 `directory` 或 `set substitute-path` 修好。
4. 寫一個小 C++ 程式（含 overload 函式），`info functions`，觀察 mangled vs demangled 顯示，再 `c++filt` 手動還原一個。
5. `info sharedlibrary` 看你的程式連了哪些 `.so`、符號讀到沒；對沒讀到的一個開 debuginfod 再看。

## 本章重點整理

- symbol table（`.symtab`，名字↔位址，strip 會拿掉）與 debug info（DWARF `.debug_*`，行號/變數/型別，`-g` 才有）是兩套東西。
- 查符號：`info functions/variables/line/address/symbol`、`whatis`、`ptype`。
- `list` 顯示的原始碼是 GDB 去檔案系統讀的；DWARF 不存原始碼本身。
- 找不到原始碼用 `directory` / `set substitute-path`（CI build 拿回本機 debug 必備）。
- C++ 名字會 mangle；`c++filt` 與 `set print demangle` 處理。

## 自我檢核

- [ ] strip 過的 binary 為什麼「函式名可能還在但變數沒了」？
- [ ] `list` 說找不到檔案，但 `break` 還對得到行號——這代表什麼？怎麼修？
- [ ] `info symbol <位址>` 和 `info address <符號>` 各做什麼？
- [ ] 拿到一串崩潰位址但不想開 GDB，用什麼命令列工具翻譯成行號？
- [ ] debuginfod 補的是 symbol table 還是 debug info？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Examining the Symbol Table](https://sourceware.org/gdb/current/onlinedocs/gdb/Symbols.html)**
  - **讀哪裡**：`info functions/variables/line/symbol`、`maint info symtabs` 各段。
  - **和本章的關聯**：本章符號查詢指令的完整參考。

- **[GDB Manual: Specifying Source Directories](https://sourceware.org/gdb/current/onlinedocs/gdb/Source-Path.html)**
  - **讀哪裡**：`directory`、`set substitute-path` 的精確語意與優先順序。
  - **和本章的關聯**：解決「CI 編譯路徑 vs 本機路徑」的權威說明。

### 部落格 / 文章

- **[How GDB loads symbols (psymtab)](https://developers.redhat.com/articles/2022/01/10/gdb-debugging-large-programs)** — Red Hat Developers
  - **這篇說什麼**：GDB 為什麼用 partial symtab、`.gdb_index` 怎麼加速大型程式。
  - **為什麼值得讀**：當你 debug chromium / LLVM 等級的程式覺得 GDB 卡死時，這篇救你。

### 工具

- **[binutils: addr2line / nm / c++filt](https://sourceware.org/binutils/docs/binutils/)**
  - **讀哪裡**：addr2line、nm、c++filt 三個工具的 man。
  - **和本章的關聯**：GDB 之外的符號工具箱，崩潰 log 分析常用。

符號到位後，下一章進入 debug 最高頻的動作：把記憶體裡的 byte 印成你看得懂的值。

→ [Ch 7 看資料：print / display / x](./07-print-display-examine.md)
