# Ch 8 — 表示式語言與 convenience variables

> **目標**：把 `print` 從「印一個變數」升級成「在 GDB 裡求值、運算、暫存、寫小腳本」。掌握 GDB 表示式語言、value history（`$1`/`$$`）、convenience variables（`$foo`）、convenience functions、以及 inferior call（在 debug 中呼叫函式）。這章是邁向 Part 4/5 自動化的橋樑。

> **環境**：GDB 13/14，Linux x86_64，`gcc -g -O0`。

## 為什麼 `print` 是一門語言

多數人把 `print x` 當「顯示 x」。但 GDB 的表示式求值器其實懂**整個 C 表示式語法**（C++ 模式還懂更多）：算術、比較、轉型、解指標、陣列索引、取址、`sizeof`、甚至呼叫函式。你可以把 GDB 當一個「能直接讀寫 inferior 記憶體的 C REPL」。

學會把求值結果**存起來重用**（value history、convenience variable），是後面寫條件斷點（Ch 12）、命令腳本（Ch 20）、Python 自動化（Ch 22）的基本功。

## GDB 表示式：一個能讀 inferior 的計算機

```c
// expr_demo.c — gcc -g -O0
#include <stdlib.h>
struct Node { int val; struct Node *next; };
struct Node* mklist(void) {
    struct Node *a = malloc(sizeof *a), *b = malloc(sizeof *b);
    a->val = 11; a->next = b;
    b->val = 22; b->next = NULL;
    return a;
}
int main(void){ struct Node *head = mklist(); return head->val; }
```

```
(gdb) break main
(gdb) run
(gdb) next                          # 讓 head 賦值
(gdb) print head->val               # 解指標 + 取欄位
$1 = 11
(gdb) print head->next->val         # 串接
$2 = 22
(gdb) print head->val + head->next->val   # 算術
$3 = 33
(gdb) print head->val > 10          # 比較（回傳 0/1）
$4 = 1
(gdb) print sizeof(struct Node)     # sizeof
$5 = 16
(gdb) print (char)head->val         # 轉型
$6 = 11 '\v'
(gdb) print &head->next             # 取址
$7 = (struct Node **) 0x...
```

GDB 在 inferior 的記憶體上求值——`head->next->val` 真的去讀那串指標鏈。這就是 debug 的核心動作之一。

## value history：每個結果都被記住

每次 `print` 的結果存進 value history，編號 `$1`、`$2`…，可以後續引用：

```
(gdb) print head
$8 = (struct Node *) 0x5555...2a0
(gdb) print $8->val                 # 用 $8 引用剛剛印的指標
$9 = 11
(gdb) print $                       # $ = 最近一個結果（= $9）
$10 = 11
(gdb) print $$                      # $$ = 倒數第二個（= $9）
(gdb) print $$2                     # $$2 = 倒數第三個
```

`$` / `$$` / `$$n` 讓你不用記編號就能引用最近的結果。實戰中常這樣串：

```
(gdb) print head            # $11 = 某指標
(gdb) print *$              # 印它指向的內容
(gdb) print $.next          # 再取 next
(gdb) print *$              # 再解...一路爬鏈
```

## convenience variables：你自己的暫存變數

`$名字`（你自訂的名字，不是數字）是 **convenience variable**——存在 GDB 裡、不影響 inferior 的暫存變數。它們不需要宣告，直接賦值：

```
(gdb) set $node = head              # 存一個指標
(gdb) print $node->val
$12 = 11
(gdb) set $node = $node->next       # 往前走一格
(gdb) print $node->val
$13 = 22
(gdb) set $count = 0                # 當計數器
(gdb) set $count = $count + 1
```

convenience variable 的威力在於**走訪資料結構**：手動爬 linked list、tree 時，用一個 `$cur` 變數當游標，配合 `set $cur = $cur->next` 一格格走。這也是 Ch 20 用命令語言寫迴圈走訪整個 list 的基礎。

> `set $foo = ...` vs `set var x = ...`：`$foo` 是 GDB 的 convenience variable（不碰 inferior）；`set var x`（或 `set x`）是改 **inferior 裡的真實變數**。別搞混——前者是你的便條紙，後者是動程式的記憶體。

## 內建的 convenience variables

GDB 預設提供一些有用的 `$` 變數：

```
(gdb) print $pc            # program counter（= $rip on x86-64）
(gdb) print $sp            # stack pointer
(gdb) print $rax           # 任何暫存器都能當變數用（Ch 11）
(gdb) print $_exitcode     # 程式上次 exit 的 code
(gdb) print $_siginfo      # 上次 signal 的詳細資訊
(gdb) print $_             # x 指令最後檢視的位址
(gdb) print $__            # 該位址的值
```

`$_exitcode` 在腳本裡判斷程式怎麼結束很有用；`$pc`/`$sp` 是組語級 debug 的命脈（Ch 11）。

## convenience functions：內建的工具函式

GDB 提供一些 `$函式()` 形式的便利函式（部分由 Python 實作，Ch 28 可自訂）：

```
(gdb) print $_strlen(msg)           # 字串長度
(gdb) print $_regex("foobar", "o+") # regex 比對
(gdb) print $_memeq(p, q, 8)        # 記憶體比較
(gdb) print $_caller_is("main")     # 呼叫者是不是 main（條件斷點神器，Ch 12）
(gdb) print $_streq(s, "hello")     # 字串相等
(gdb) help function                 # 列出所有 convenience functions
```

`$_caller_is()` / `$_caller_matches()` 在條件斷點裡極實用：「只在被 X 呼叫時才停」。

## inferior call：在 debug 中呼叫函式

GDB 最神奇的能力之一：**直接呼叫 inferior 裡的函式**，它會真的在被 debug 的程式裡執行那個函式。

```
(gdb) print strlen(msg)             # 真的呼叫 libc 的 strlen
$14 = 10
(gdb) print mklist()                # 呼叫你自己的函式
$15 = (struct Node *) 0x5555...4e0
(gdb) call printf("val=%d\n", head->val)   # call 等同 print，但丟棄無回傳值的結果
val=11
```

底層（Ch 41 會實作）：GDB 把參數依 ABI 放進暫存器/stack、把 `$pc` 設到函式入口、設一個回返斷點、讓 inferior 跑、函式 return 後撈回傳值、再把 inferior 狀態還原。等於「借用 inferior 的身體執行一段程式碼」。

用途：

- 呼叫 `malloc_stats()`、自訂的 `dump_state()` debug 函式
- 測試「如果用這參數呼叫會回傳什麼」
- 觸發 pretty-print 邏輯

> 認識論誠實 + 危險警告：inferior call **有副作用**——它真的執行程式碼，可能改全域狀態、配記憶體、甚至自己崩潰。在多執行緒或訊號處理中呼叫尤其危險（可能 deadlock）。它也要求 inferior 處於可執行狀態（core dump 不能 call，Ch 33）。`print` 一個會呼叫函式的表示式時，記得它不是純讀。

## 把它們串起來：手動走訪 linked list

```
(gdb) set $cur = head
(gdb) while $cur != 0                # GDB 也有 while！（Ch 20 細講）
 >print $cur->val
 >set $cur = $cur->next
 >end
$16 = 11
$17 = 22
```

不到五行，你在 GDB 裡寫了一個走訪整個 list 的迴圈。這就是「把 print 當語言」的威力，也是 Ch 20 命令語言、Ch 22 Python 的縮影。練習 B 會大量用這招。

## 踩雷集錦

1. **`$foo` 與 `$N` 與真實變數混淆**：`$1`(history)、`$foo`(convenience)、`x`(inferior 真實變數) 是三種東西。改 inferior 變數用 `set var x=`，不要用 `$x`。
2. **convenience variable 不存在時是 void**：`print $undefined_var` 回傳 `void`，不是錯誤。第一次 `set $foo` 才賦予型別。
3. **inferior call 改了程式狀態**：`print my_init()` 之後程式狀態變了，後續 debug 看到的不是「自然」狀態。知道自己在做什麼。
4. **inferior call 崩潰連累 GDB session**：被呼叫的函式若 segfault，GDB 會停在那、把你彈出原本的 context。`set unwindonsignal on` 讓它在這種情況自動還原。
5. **`call` vs `print`**：兩者都能呼叫函式；差別只在 `call` 對 `void` 回傳不顯示 `$N = void`。功能上幾乎一樣。
6. **value history 在 inferior 重啟後失效**：`$1` 存的是上次 run 的指標，重新 `run` 後那位址無意義（甚至 ASLR 換了）。

## 進階：再往深一層

- **convenience variable 可存任意型別的 value**：包括整個 struct、陣列、甚至型別（`set $t = (struct Node *)0`）。在腳本裡當資料容器。
- **`$_gdb_setting()` / `$_gdb_maint_setting()`**（GDB 12+）：在表示式裡讀 GDB 自己的設定值，寫可移植腳本時有用。
- **type 在表示式裡**：`print (struct Node *)$rax` 把暫存器值轉型成結構指標來看——逆向時把未知記憶體「套結構」的核心技巧。
- **`$_as_string()`、`$_cimag()` 等更多 convenience function**：`help function` 看全清單，不同版本會增加。
- **Python 橋接**：convenience variable 可以在 Python 裡讀寫（`gdb.convenience_variable("foo")`，Ch 23），convenience function 可用 Python 自訂（Ch 28）。這章是 Part 5 的伏筆。

## 動手練習

1. 對 `expr_demo.c`，用 `$` / `$$` 串接，從 `head` 一路 `print *$` / `print $.next` 爬完整個 list，不打變數名。
2. 用 convenience variable `$cur` 當游標，`set $cur=$cur->next` 手動走訪 list。
3. 用本章的 `while $cur != 0` 迴圈印出整個 list 的 val。
4. `print strlen("hello")` 與 `call printf("hi\n")`，體會 inferior call。再寫一個會改全域變數的函式，inferior call 它，然後 `print 那個全域變數` 確認被改了。
5. 在某個函式內 `print $_caller_is("main")`，理解它怎麼判斷呼叫者（Ch 12 條件斷點會用）。

## 本章重點整理

- GDB 表示式求值器懂完整 C 語法：算術、解指標、轉型、`sizeof`、取址、呼叫函式——是個能讀 inferior 的 C REPL。
- value history：`$N`、`$`、`$$`、`$$n` 引用之前的結果。
- convenience variable：`$自訂名`，GDB 內的暫存變數（不碰 inferior），走訪資料結構的游標。
- inferior call：`print f(x)` 真的在 inferior 裡執行函式——強大但有副作用、有風險。
- `set $foo`（convenience）vs `set var x`（inferior 真實變數）別搞混。

## 自我檢核

- [ ] `$1`、`$foo`、inferior 裡的 `x`——三者各是什麼？改 inferior 變數該用哪個指令？
- [ ] `$` 和 `$$` 差在哪？怎麼用它們串接爬指標鏈？
- [ ] inferior call 是什麼？為什麼說它「有副作用、有風險」？
- [ ] convenience variable 怎麼當游標走訪 linked list？
- [ ] `print my_func()` 和 `print my_var` 在「是否改變程式狀態」上有何根本差別？

## 延伸閱讀

### 官方文件

- **[GDB Manual: Expressions](https://sourceware.org/gdb/current/onlinedocs/gdb/Expressions.html)** 與 **[Value History](https://sourceware.org/gdb/current/onlinedocs/gdb/Value-History.html)** 與 **[Convenience Variables](https://sourceware.org/gdb/current/onlinedocs/gdb/Convenience-Vars.html)**
  - **讀哪裡**：三節都不長，連著讀。
  - **和本章的關聯**：本章三大主題的權威定義；內建 convenience variable 清單在 Convenience Vars 節。

- **[GDB Manual: Calling Program Functions](https://sourceware.org/gdb/current/onlinedocs/gdb/Calling.html)**
  - **讀哪裡**：整節 + `set unwindonsignal`、`set unwind-on-terminal-signal`。
  - **和本章的關聯**：inferior call 的風險控制設定都在這。

### 部落格 / 文章

- **[GDB convenience functions for debugging](https://developers.redhat.com/blog/2018/03/21/compiler-and-architecture-detection-in-gnu-make)** 類的 Red Hat 系列
  - **這篇說什麼**：convenience function / 表示式在真實 debug 的用法。
  - **為什麼值得讀**：把抽象的 `$_caller_is` 等放進實際情境。

下一章把「型別」這條線講透：GDB 怎麼理解 struct/union/enum/typedef，以及怎麼用 `ptype`/`whatis` 探查未知型別。

→ [Ch 9 型別系統](./09-type-system.md)
