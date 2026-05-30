# Ch 7 — 看資料：print / display / x

> **目標**：把 GDB 最高頻的三個檢視指令 `print`、`display`、`x` 徹底吃透——format specifier、指標解參、陣列切片、結構印法、自動顯示、記憶體 dump。學完你能用一行指令把任何記憶體內容印成你要的樣子。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼這章是日常戰力的核心

如果說 breakpoint 決定你「停在哪」，那 print/x 決定你「看到什麼」。你 90% 的 debug 時間在做一件事：停下來，看某個東西的值對不對。把這三個指令的 format 與用法練到肌肉記憶，你的 debug 速度會質變。

`print` 與 `x` 容易混淆，先一句話分清：**`print` 看「值」（依型別解釋），`x` 看「記憶體」（依你指定的格式 dump byte）。**

## 範例程式

```c
// inspect_demo.c — gcc -g -O0 inspect_demo.c -o inspect_demo
#include <stdio.h>
#include <string.h>

struct Point { int x, y; };

int   global_arr[5] = {10, 20, 30, 40, 50};
char  msg[] = "Hello, GDB";
struct Point p = {3, 7};

int main(void) {
    struct Point *pp = &p;
    int *heap = malloc(4 * sizeof(int));
    for (int i = 0; i < 4; i++) heap[i] = i * 100;
    printf("%s\n", msg);          // ← break 在這
    return 0;
}
```

```
$ gcc -g -O0 inspect_demo.c -o inspect_demo
$ gdb -q ./inspect_demo
(gdb) break 17
(gdb) run
```

## `print`：依型別印值

```
(gdb) print global_arr
$1 = {10, 20, 30, 40, 50}          # 陣列：GDB 知道型別，整個印出來
(gdb) print p
$2 = {x = 3, y = 7}                 # struct：欄位名都有（DWARF 的功勞）
(gdb) print *pp
$3 = {x = 3, y = 7}                 # 解指標
(gdb) print pp->x
$4 = 3
(gdb) print msg
$5 = "Hello, GDB"                   # char 陣列當字串
(gdb) print sizeof(struct Point)
$6 = 8
```

`print`（簡寫 `p`）讀符號的型別，把記憶體 byte **依型別解釋**成人看得懂的值。struct 有欄位名、陣列有大括號、char[] 當字串——全靠 Ch 6 的 DWARF。

每個結果存進 value history：`$1`、`$2`…，可重用（Ch 8）。

## format specifier：`print/x` 換個格式看

加 `/格式字母` 改變顯示方式：

```
(gdb) print/x global_arr           # 十六進位
$7 = {0xa, 0x14, 0x1e, 0x28, 0x32}
(gdb) print/t 30                   # 二進位 (t = two)
$8 = 11110
(gdb) print/c 65                   # 當字元
$9 = 65 'A'
(gdb) print/d 0xff                 # 十進位
$10 = 255
(gdb) print/a main                 # 當位址（顯示符號+偏移）
$11 = 0x1149 <main>
(gdb) print/f $rax                 # 當浮點數解讀那串 bit
(gdb) print/s msg                  # 當字串
```

常用 format 字母：

| 字母 | 意思 | 字母 | 意思 |
|---|---|---|---|
| `x` | 十六進位 | `c` | 字元 |
| `d` | 有號十進位 | `s` | 字串 |
| `u` | 無號十進位 | `a` | 位址（含符號） |
| `t` | 二進位 | `f` | 浮點 |
| `o` | 八進位 | `z` | 補零十六進位 |

> 小技巧：format 有記憶性。`print/x` 之後，再 `print` 同型別的東西仍會用 hex，直到你換回 `/d`。

## `x`：檢視原始記憶體

`x`（examine）不管型別，直接把某位址開始的記憶體 dump 出來。語法：`x/NFU 位址`——**N** 個單位、**F** 格式、**U** 單位大小。

```
(gdb) x/4xw heap          # 從 heap 開始，4 個 word(w)，hex(x)
0x5555...2a0:  0x00000000  0x00000064  0x000000c8  0x0000012c
(gdb) x/8xb msg           # 8 個 byte(b)，hex
0x...: 0x48 0x65 0x6c 0x6c 0x6f 0x2c 0x20 0x47
(gdb) x/s msg             # 當字串印
0x...: "Hello, GDB"
(gdb) x/5i main           # 5 條指令(i)，反組譯！
   0x1149 <main>:      push   %rbp
   0x114a <main+1>:    mov    %rsp,%rbp
   ...
(gdb) x/2dw &p            # p 的兩個 int，十進位
0x...: 3   7
```

單位大小 **U**：`b`(byte=1) / `h`(half=2) / `w`(word=4) / `g`(giant=8)。
格式 **F**：和 print 的 format 字母一樣，外加 `i`（指令）和 `s`（字串）。

`x/i` 反組譯、`x/s` 印字串、`x/Nxw` dump 記憶體——這三個組合你會用一輩子。

> `x` 和 `print` 的關鍵差別：`x heap` 把 `heap` 這個**指標的值**當位址去 dump 那裡的記憶體；`print heap` 印指標本身的值（一個位址數字）。想看指標**指向的內容**用 `x`，想看指標**自己**用 `print`。

## `print` 進階：陣列切片與人工陣列

```
(gdb) print global_arr[1]@3        # 從 [1] 開始連印 3 個（@ 運算子）
$12 = {20, 30, 40}
(gdb) print *heap@4                # 把 heap 指向的記憶體當 4 元素陣列印
$13 = {0, 100, 200, 300}
```

`@` 是「人工陣列」運算子——`表達式@N` 表示「從這裡連續取 N 個同型別元素」。對 `malloc` 出來的記憶體（GDB 不知道長度）特別有用：`print *heap@4` 把裸指標當陣列看。練習 B 會大量用到。

## 控制 print 的呈現

```
(gdb) set print pretty on          # struct 換行縮排，可讀性大增
(gdb) set print array on           # 陣列每元素一行
(gdb) set print array-indexes on   # 顯示陣列索引 [0]=.. [1]=..
(gdb) set print elements 200       # 最多印幾個元素（預設 200，大陣列會被截斷）
(gdb) set print repeats 10         # 重複元素摺疊成 "<repeats N times>"
(gdb) set print null-stop on       # char 陣列遇 \0 就停
```

`set print pretty on` 強烈建議放進 `~/.gdbinit`（Ch 19）。`set print elements 0` 解除元素數量上限（印超大陣列時）。

```
# pretty on 之後
(gdb) print p
$14 = {
  x = 3,
  y = 7
}
```

## `display`：每次停下來自動印

`display` = 「每次 inferior 停下來，自動 print 這個」。debug 迴圈、追一個變數的變化時超好用：

```
(gdb) display i                    # 每次停都印 i
(gdb) display/x $rax               # 每次停都用 hex 印 rax
(gdb) display/i $pc                # 每步都顯示當前指令（組語級 debug 標配）
(gdb) info display                 # 看目前有哪些自動顯示
(gdb) undisplay 1                  # 取消編號 1 的
```

設好 `display i` 後，你按 `next`、`step`，每次都會自動看到 `1: i = 5`，不用一直手打 `print i`。

## 一個完整的檢視流程

```
(gdb) break 17
(gdb) run
(gdb) print pp                     # 看指標值：0x555...0
(gdb) print *pp                    # 看它指向的 struct：{x=3, y=7}
(gdb) x/2dw pp                     # 用記憶體層級驗證：3  7
(gdb) print *heap@4                # malloc 的記憶體當陣列：{0,100,200,300}
(gdb) x/4xw heap                   # 同上但看 raw hex
(gdb) display heap[0]              # 之後每步自動盯 heap[0]
```

「`print` 看值、`x` 看記憶體、`display` 自動盯」——三件武器各司其職。

## 踩雷集錦

1. **`x` 和 `print` 對指標的混淆**：`print arr`（陣列）印全部；`print ptr`（指標）只印一個位址數字，要看內容得 `print *ptr@N` 或 `x/Nxw ptr`。
2. **大陣列被截斷**：`print` 預設只印 200 個元素，後面 `...`。`set print elements 0` 解除，或 `set print elements 1000`。
3. **`x/s` 印出亂碼**：你給的位址不是字串開頭，或那段不是以 `\0` 結尾的文字。確認位址對不對。
4. **format 黏住了**：`print/x` 之後忘了它會記憶，下次 `print` 還是 hex。明確指定 `/d` 切回。
5. **`malloc` 記憶體 print 不出陣列**：GDB 不知道 `malloc` 配了多大，`print *heap` 只印一個元素。要 `print *heap@N` 告訴它長度。
6. **`set print pretty on` 沒生效**：你打成 `set pretty on`（舊語法在某些版本不通）。完整是 `set print pretty on`。
7. **`x` 單位字母順序**：`x/4xw` 和 `x/4wx` 都行（N 一定在前，F/U 順序可換），但 N 必須最前面。

## 進階：再往深一層

- **`print` 可以求值與呼叫函式**：`print foo(3)`、`print strlen(msg)`——GDB 在 inferior 裡真的呼叫該函式（inferior call，Ch 8、Ch 21）。有副作用，小心。
- **`p` 表達式支援轉型**：`print (struct Point *)0x555...0`——把裸位址轉成型別來看，逆向時把未知記憶體「套上」結構。
- **pretty-printer**：對 `std::vector`、`std::map` 這種，原生 `print` 印出一坨內部指標很難看。Python pretty-printer（Ch 26、Ch 30）能讓 `print myvec` 直接顯示 `{1, 2, 3}`。這是 Final Project 的核心能力之一。
- **`x` 的 `$_` 與 `$__`**：`x` 執行後，`$_` 是最後檢視的位址、`$__` 是該處的值，可串接（Ch 8）。
- **`set print pretty` vs pretty-printer**：前者只是 struct 換行排版，後者是用程式自訂顯示邏輯，兩者不同層次。

## 動手練習

1. 對 `inspect_demo.c`，用 `print`、`print/x`、`x/4xw`、`x/8xb` 各看一次 `global_arr`，比較顯示差異。
2. 用 `print *heap@4` 把 malloc 的記憶體當陣列印；再用 `x/4dw heap` 用記憶體層級驗證一致。
3. `display/i $pc` 然後連按 `stepi`，體會組語級 debug 時每步自動顯示指令。
4. `set print pretty on` 前後各 `print p`，看排版差異；把這行加進 `~/.gdbinit`。
5. 對 `msg` 用 `x/s`、`x/11c`、`x/11xb` 三種看法，理解同一段記憶體的不同詮釋。
6. 把 `global_arr` 改成 1000 元素，`print` 看它被截斷，再 `set print elements 0` 看全部。

## 本章重點整理

- `print` 依型別解釋值（struct 有欄位名、陣列有大括號）；`x` 把記憶體當 byte 依你指定格式 dump。
- format：`/x /d /u /t /o /c /s /a /f /z`；`x` 多 `i`(指令) 與單位 `b/h/w/g`。
- `表達式@N` 人工陣列，把裸指標當陣列；對 malloc 記憶體必備。
- `display` 每次停自動印；`display/i $pc` 是組語 debug 標配。
- `set print pretty/elements/array-indexes` 控制呈現，建議寫進 `.gdbinit`。

## 自我檢核

- [ ] `print ptr` 和 `x ptr` 差在哪？想看指標指向的內容用哪個？
- [ ] 怎麼把一個 `malloc(N*sizeof(int))` 的記憶體當 N 元素陣列印出來？
- [ ] `x/8xb`、`x/4xw`、`x/5i`、`x/s` 各看什麼？
- [ ] 想 debug 迴圈時每步自動看 `i`，用什麼指令？
- [ ] `print` 大陣列只印出 200 個就 `...`，怎麼解除？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Examining Data](https://sourceware.org/gdb/current/onlinedocs/gdb/Data.html)**
  - **讀哪裡**：Output Formats、Examining Memory（`x`）、Artificial Arrays（`@`）、Auto Display（`display`）。
  - **和本章的關聯**：本章所有指令的完整參考；format 字母與單位的權威清單。

- **[GDB Manual: Print Settings](https://sourceware.org/gdb/current/onlinedocs/gdb/Print-Settings.html)**
  - **讀哪裡**：`set print pretty/elements/array/repeats/null-stop` 全部。
  - **和本章的關聯**：把顯示調到最舒服的所有開關。

### 部落格 / 文章

- **[GDB's x command cheat sheet](https://visualgdb.com/gdbreference/commands/x)** — VisualGDB reference
  - **這篇說什麼**：`x` 命令的 format/unit 組合速查。
  - **為什麼值得讀**：`x` 的字母組合容易忘，當速查表。

下一章把 `print` 升級成一門「語言」：GDB 的表示式求值、convenience variable、value history，讓你在 GDB 裡寫小程式。

→ [Ch 8 表示式語言與 convenience variables](./08-expressions-and-convenience-vars.md)
