# Ch 3 — Symbol Table 與 String Table

> 目標：理解 `.symtab` / `.dynsym` / `.strtab` / `.dynstr` 這四個表怎麼組織、怎麼互指、怎麼用 `readelf -s` 跟 `nm` 讀。這章結束你看到 `undefined reference to 'foo'` 能立刻判斷「是 static linking 找不到、還是 dynamic 階段沒 resolve」。

## 為什麼要有 symbol table

每個 `.o` 裡的 code 會引用**名字**：

```asm
call printf           # 這個名字 printf 對應哪個地址？
sw   t0, x(gp)        # x 是哪個全域變數？
```

但 `.o` 產生時 compiler 不知道 `printf` / `x` 最終會在哪個 virtual address。**它產生一個「symbol reference」**，linker 後來才填值。

反過來，某個 `.o` 定義了函式 `foo`：

```asm
    .global foo
foo:
    ...
```

compiler 產生一個「symbol definition」。linker 收到後要把「定義」跟「引用」配對。

**symbol table 是裝這些定義與引用的容器**。

## 四個相關的表

```
.symtab   ← static linking 用的完整 symbol 表 (可能很大)
.strtab   ← .symtab 參照的 string 表 (存 symbol name)

.dynsym   ← dynamic linking 用的 symbol 表 (較精簡)
.dynstr   ← .dynsym 參照的 string 表
```

這兩組為什麼要分開：

- `.symtab` / `.strtab` 是 **static linking 產物**，含所有 local / internal 符號。`strip` 可以砍掉 —— runtime 不需要。
- `.dynsym` / `.dynstr` 是 **runtime 需要**的。dynamic linker 要用它們做 symbol resolve。**不能砍，砍了 binary 跑不動**。

通常 `.dynsym ⊂ .symtab`（dynamic 是 static 的子集），但兩個表各自存，不共用 entry。

## Elf64_Sym 結構

```c
typedef struct {
    Elf64_Word     st_name;       // 在對應 strtab 的 offset (index into .strtab)
    unsigned char  st_info;        // binding + type (4+4 bit packed)
    unsigned char  st_other;       // visibility
    Elf64_Half     st_shndx;       // section index (這個 symbol 定義在哪個 section)
    Elf64_Addr     st_value;       // symbol 的 value (通常是 address)
    Elf64_Xword    st_size;        // symbol 大小（如 function 的 byte 數）
} Elf64_Sym;
```

24 byte 一筆。每個 symbol 一筆。

### st_info：binding + type

高 4 bit 是 binding：

```
STB_LOCAL  0    file-scope (static 變數、inline helper...)
STB_GLOBAL 1    全域可見
STB_WEAK   2    weak symbol (優先順位低)
```

低 4 bit 是 type：

```
STT_NOTYPE  0    沒特定類型
STT_OBJECT  1    變數
STT_FUNC    2    函式
STT_SECTION 3    section 本身的代表 symbol
STT_FILE    4    file name（debugging）
STT_COMMON  5    uninitialized，等 linker 配位
STT_TLS     6    Thread-Local Storage 變數
```

兩個合起來就是「這個 symbol 是什麼、誰看得到」。

### st_shndx：定義在哪個 section

- 正常 section index（1, 2, 3, ...）：symbol 定義在那個 section 裡
- **`SHN_UNDEF` (0)**：**undefined** — 只有 reference、沒 definition
- **`SHN_COMMON`**：common block（uninitialized global）
- **`SHN_ABS`**：絕對值，跟 section 無關

**`SHN_UNDEF` 就是 "U" 狀態**。`nm` 印出來那個 U 來自這裡。

### st_value

對 defined symbol：是它在對應 section 的 offset（`.o`）或 virtual address（executable）。
對 undefined symbol：通常 0。
對 function：是該 function 的入口。
對 variable：是該 variable 的地址。

### st_size

Symbol 佔幾個 byte。function 是長度、variable 是大小。Debug / profiler 靠這個切 function。

## String Table：存 symbol 名字

string table 是個**很簡單**的結構：

```
offset 0:  '\0'                         ← 第 0 byte 永遠是空字串
offset 1:  'f' 'o' 'o' '\0'
offset 5:  'b' 'a' 'r' '\0'
offset 9:  'p' 'r' 'i' 'n' 't' 'f' '\0'
...
```

所有字串尾隨 null、從 offset 0 開始編號。Symbol 的 `st_name` 是「進 string table 的 offset」。

**為什麼這樣存**：

1. 允許字串共用。`bar` 跟 `foobar` 可以 overlap（`foobar` 的後三字就是 `bar`）。
2. 讀快：隨機 access 只要 `strtab + offset`。
3. 結構超簡單，parser 好寫。

### 多個 string table

一個 ELF 通常有三個 string table：

- **`.shstrtab`**：存 section 名字。`e_shstrndx` 指向這個。
- **`.strtab`**：存 `.symtab` 的 symbol 名字。
- **`.dynstr`**：存 `.dynsym` 的 symbol 名字。

每個都獨立。

## 用 readelf 看 symbol table

```bash
riscv64-linux-gnu-readelf -s hello
```

可能看到兩塊輸出（`.symtab` 跟 `.dynsym`）：

```
Symbol table '.dynsym' contains 7 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND
     1: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND puts@GLIBC_2.17
     2: 0000000000000000     0 FUNC    GLOBAL DEFAULT  UND __libc_start_main@GLIBC_2.27
     ...

Symbol table '.symtab' contains 63 entries:
   Num:    Value          Size Type    Bind   Vis      Ndx Name
     0: 0000000000000000     0 NOTYPE  LOCAL  DEFAULT  UND
     1: 0000000000000000     0 FILE    LOCAL  DEFAULT  ABS hello.c
     2: 0000000000000000     0 FILE    LOCAL  DEFAULT  ABS
     ...
    49: 00000000000006e8    48 FUNC    GLOBAL DEFAULT    14 main
    50: 000000000000001fa8  0 OBJECT  GLOBAL DEFAULT   24 _DYNAMIC
    ...
```

每欄：

- **Num**：entry 編號
- **Value**：地址或 offset
- **Size**：byte 數
- **Type**：FUNC / OBJECT / NOTYPE / FILE / ...
- **Bind**：LOCAL / GLOBAL / WEAK
- **Vis**：visibility (DEFAULT / HIDDEN / PROTECTED) — Ch 14 講
- **Ndx**：section index（UND = undefined、ABS = 絕對值、數字 = 該 section）
- **Name**：symbol name

## `nm` 的字母對應 Elf64_Sym

| nm 字母 | 意義 | 對應 |
|---------|------|------|
| T | 在 .text (code) | TYPE=FUNC, shndx=.text |
| t | 同上但 LOCAL | TYPE=FUNC, bind=LOCAL |
| D | 在 .data | TYPE=OBJECT, shndx=.data |
| d | 同上 LOCAL | |
| B | 在 .bss (uninit data) | |
| R | 在 .rodata | |
| U | Undefined | shndx=UNDEF |
| W | Weak (未 resolved) | bind=WEAK |
| w | Weak (沒 default value) | |
| V / v | Weak object | |
| N | Debug symbol | |
| A | 絕對值 | shndx=ABS |

`nm` 就是把 symbol table 用單字母縮寫展示。

## 怎麼看 undefined reference

Link error 典型：

```
undefined reference to `my_function'
```

這個訊息對應 symbol table 裡：**某個 `.o` 有 `my_function` 為 `SHN_UNDEF`，但所有 `.o` / library 裡都找不到 `SHT_PROGBITS` 定義它**。

診斷：

```bash
# 找出哪個 .o 引用它
for f in *.o; do
    nm $f | grep "U my_function" && echo "  ← in $f"
done

# 找出哪個 library 定義它
nm -D /usr/lib/libfoo.so | grep "T my_function"
```

這是 linker debug 的基本功。

## Weak symbol

`weak` binding 的意思：**如果沒人定義，也不算錯；有人定義，覆蓋我**。

```c
__attribute__((weak)) void optional_hook(void) { /* default */ }
```

某些應用用它做 plugin 介面：default 用 weak 版、plugin 實作覆蓋掉。

linker 合併時：

- 碰到兩個 strong definition → 重複定義錯誤
- 碰到 strong + weak → 用 strong
- 碰到兩個 weak → 用第一個碰到的

這有個「**first-definition-wins for weak**」的直覺，偶爾會有 subtle bug（下面有例子）。

## Symbol resolution 的順序

Static linking 時：

1. linker 按**命令列順序**處理 `.o` 與 `.a`
2. 每個 `.o` 內的所有 symbol 加進 global symbol table
3. `.a`（archive）是 lazy —— 只有它的 `.o` 提供「目前的 undefined symbol」才納入
4. 碰到衝突（多個 strong definition）→ error
5. 所有 `.o` 處理完，還有 undefined 且沒 library 解決 → `undefined reference` error

**`.a` 的 lazy 是大陷阱**。典型 bug：

```bash
gcc -lfoo main.c        # 錯順序！libfoo 先處理，此時還沒看到 main.c 的 undefined
gcc main.c -lfoo        # 對！main.c 先放 undefined，libfoo 去解決
```

大部分學 C 的人第一次看到 `gcc foo.c -lm` 沒搞懂為什麼 `-lm` 要在後面，就是這個。

## Common symbol（歷史遺毒）

`SHN_COMMON` 是個歷史特例：

```c
// a.c
int x;                // 沒 init 的 global

// b.c
int x;                // 另一個 .c 也這麼寫
```

在老 C 標準下（pre-C99 / GCC `-fcommon` 模式），兩個都會變成 `SHN_COMMON` 而不是錯誤。linker 看到兩個 common 會合併成一個（取大的 size）。

**這是危險的 silent override**。GCC 10+ 預設 `-fno-common` 把這些變成 strong definition → 多定義會錯誤。想要老行為用 `-fcommon`。

如果你 debug 大型 legacy codebase 看到 `nm` 印 `C`，就是這個。

## Local vs Global：誰看得見誰

- **LOCAL** (STB_LOCAL)：只在定義它的 `.o` 內看得到。其他 `.o` 不知道它存在。
- **GLOBAL** (STB_GLOBAL)：linker 可見，可以被任何 `.o` 引用。

C 的 `static` keyword 產生 LOCAL：

```c
static int helper(void) { return 42; }   // LOCAL
```

這不是優化，是**語意**。即使兩個 `.c` 都定義 `static int x`，各自獨立、不衝突。

Assembly 裡靠 `.local` / `.global`：

```asm
    .local local_var
    .global public_func
```

## 特殊 symbol

一些 linker 產生的 symbol（「內建」但不在任何 `.c` 裡）：

```
_start                  program 真正的 entry point
_init / _fini           legacy 初始化/結束 (現在用 init_array)
__init_array_start
__init_array_end        constructor 陣列的邊界
_DYNAMIC                .dynamic section 的起點
_GLOBAL_OFFSET_TABLE_   GOT 的起點
__bss_start / _end      bss section 邊界 (可用來清 bss)
_edata                  data section 結束
environ                 環境變數指標（glibc）
```

這些都是 linker script 定義的（`PROVIDE`、`__start_<section>` 等）。Ch 8 會細講 linker script 語法。

## 動手練習

1. 對一個有 10+ .c 的小 project，用 `nm -A *.o` 找出所有 Undefined symbol 並確認它們都有對應 Defined。
2. 寫一個故意重複定義的例子（兩個 .c 都 `int x = 1;`），觀察新版 GCC 的錯誤訊息，再用 `-fcommon` 重試。
3. 寫一個使用 weak symbol 的例子：a.c 定義 `__attribute__((weak)) int (*hook)(void) = NULL;`，b.c 定義 `int my_hook(void) { return 42; }` 並 `hook = my_hook`。觀察 `nm` 輸出。
4. 用 `strip` 砍掉 `.symtab`，前後對比 `readelf -s` 與 `nm`。確認 `.dynsym` 還在、程式還能跑。
5. 寫一個呼叫 `pthread_create` 的 program，`gcc main.c -o main` 看會不會 error。加 `-lpthread` 再試。從 symbol 角度解釋差異。

## 常見誤會

1. **「symbol 就是 C 的變數名」**：不完全。C++ symbol 會被 mangle（`_Z3fooii`）；linker 看到的是 mangle 後的字串。
2. **「nm 的 U 是錯誤」**：不是。`.o` 裡本來就該有 U（引用外部符號）。executable 裡的 U 是 dynamic linker 要 resolve 的。
3. **「static 變數不會進 symbol table」**：會，只是 LOCAL。strip 後才會消失。
4. **「`SHT_NOBITS` 的 symbol 沒 address」**：有。`.bss` 裡的 symbol 有 virtual address、只是檔案不存 byte。
5. **「library 連結順序不重要」**：非常重要。`-l` 必須在引用它的 `.o` 之後。

## 自我檢核

- [ ] 我能解釋 `.symtab` 跟 `.dynsym` 的差別以及為什麼要分兩個
- [ ] 我能看 `readelf -s` 的欄位知道 symbol 的 binding / type / visibility
- [ ] 我能把 `nm` 的字母對回 Elf64_Sym 結構
- [ ] 我能解釋 undefined reference / multiple definition 錯誤的根源
- [ ] 我知道 weak symbol 的行為與 link-order 的重要性

下一章進正式的 static linking 流程 —— symbol resolution 後怎麼 relocate、怎麼 layout、怎麼產生 executable。

→ [Ch 4 靜態連結流程：resolution → relocation → layout](./04-static-linking-flow.md)
