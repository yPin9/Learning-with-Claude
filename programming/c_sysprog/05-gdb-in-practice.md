# Ch 5 — GDB 實戰：不靠 printf 觀察程式狀態

> **目標**：能用 GDB 停住程式、檢視與修改任意記憶體、追出「誰改壞了這個變數」、分析 crash 與 core dump、把除錯步驟寫成腳本；並理解 GDB 底層靠 ptrace、int3、硬體除錯暫存器與 DWARF 運作，知道它在什麼情況下會看不到東西。

> **對應原文**：[你所不知道的 C 語言：開發工具和規格標準篇](https://hackmd.io/@sysprog/c-standards) 的「只用 printf 觀察資料，有問題嗎？」「不要急著印出位址，善用 GDB」等節

> **環境**：GNU gdb 12.1（Ubuntu 12.1-0ubuntu1~22.04.2），gcc 11.4，WSL2 Ubuntu 22.04 x86_64（CPU：AMD Ryzen 7 5700G）。本章的 GDB 輸出都是以 `gdb -q -batch -ex '...'` 實際執行取得；互動模式下的輸出內容相同，但會多出少數提示訊息（例如 `finish` 的 "Run till exit from ..."）。位址每次執行可能不同。

## 為什麼需要這個？

`printf` 大概是每個人最早記住的函式，也是最多人唯一會用的除錯工具。它的問題不是「不方便」，而是**你只看得到格式字串框住的東西**。看一個小例子：

```c
#include <stdio.h>

int main(void)
{
    size_t n = (size_t) 1 << 32;      /* 4294967296 */
    double x = 0.1 + 0.2;
    printf("n = %d\n", n);            /* 型別不符：未定義行為 */
    printf("x = %f, x == 0.3 ? %d\n", x, x == 0.3);
    printf("x = %a\n", x);
    return 0;
}
```

```
$ gcc -g -O0 fmt.c -o fmt && ./fmt
fmt.c:7:18: warning: format ‘%d’ expects argument of type ‘int’, but argument 2 has type ‘size_t’ {aka ‘long unsigned int’} [-Wformat=]
n = 0
x = 0.300000, x == 0.3 ? 0
x = 0x1.3333333333334p-2
```

兩個謊言：

1. `n = 0`：格式字串說 `%d`，`printf` 就只讀 32 位元，`2³²` 的低 32 位元剛好是 0。依 C11 7.21.6.1 §9，型別不符本身就是 UB（[Ch 4](./04-reading-the-standard.md) 教過怎麼查這種條文）。gcc 在這裡有警告，但如果格式字串是執行期組出來的，編譯器就無能為力。
2. `x = 0.300000` 卻 `x == 0.3` 為假：`%f` 預設四捨五入到小數 6 位，把真相藏起來了。C99 引入的 `%a` 以十六進位印出精確值才看得出差異。

同一個時間點，在 GDB 裡看：

```
$ gdb -q -batch -ex 'break 9' -ex run -ex 'print n' -ex 'print x' -ex 'print/x n' \
      -ex 'p x - 0.3' -ex 'x/gx &x' -ex 'x/8xb &x' ./fmt
Breakpoint 1, main () at fmt.c:9
9	    printf("x = %a\n", x);
$1 = 4294967296
$2 = 0.30000000000000004
$3 = 0x100000000
$4 = 5.5511151231257827e-17
0x7fffffffe138:	0x3fd3333333333334
0x7fffffffe138:	0x34	0x33	0x33	0x33	0x33	0x33	0xd3	0x3f
```

GDB 依**變數的實際型別**解讀記憶體（DWARF 除錯資訊告訴它 `n` 是 `size_t`），還能以任何格式、任何粒度直接看 byte。不用改程式、不用重新編譯、不用猜要印什麼。

`printf` 這個名字本身也有段歷史：1960 年代初 MIT 的 CTSS 作業系統就有一個叫 `PRINTF` 的命令，作用卻是印出檔案內容；C 的格式化輸出則源自 BCPL 的 `writef`（1967），經 B 傳入 C（這段脈絡見 [Ch 6](./06-history-of-c.md)）。五十多年後，它仍然只能顯示你事先決定要看的東西。

## 先建立直覺

把 GDB 想成兩樣東西的組合：**暫停時間的遙控器**，加上**能對準任何記憶體位址的顯微鏡**。

```
          你（下指令）
             │  break / run / next / print / x / watch ...
             ▼
      ┌─────────────┐   ptrace(2) 系統呼叫     ┌─────────────────┐
      │    gdb      │ ───────────────────────▶ │  被除錯的行程    │
      │             │  讀寫記憶體與暫存器、     │  (inferior)      │
      │  讀 DWARF   │  單步、繼續、收 signal    │                  │
      │  除錯資訊    │ ◀─────────────────────── │  停住時一動不動  │
      └─────────────┘   行程停住時通知 gdb      └─────────────────┘
             │
             ▼
   「位址 0x7fffffffe138 是 main 的區域變數 x，型別 double，
     對應 fmt.c 第 6 行」
```

三個關鍵：

- **行程停住時，整個世界凍結**。你可以慢慢看每個變數、每個暫存器、每一塊記憶體，看完再讓它走一步。
- **GDB 不懂 C，它懂的是 DWARF**。`gcc -g` 會在執行檔裡加一份地圖：哪個機械碼位址對應哪一行原始碼、哪個變數放在哪裡、是什麼型別。沒有這份地圖，GDB 只看得到位址與暫存器。
- **GDB 透過作業系統做事**。Linux 上是 `ptrace` 系統呼叫，所以權限、容器、最佳化都會影響它看得到什麼。

> 本章會用到 stack frame、暫存器、組合語言等概念，有直覺層次的理解就夠了。完整的 x86-64 呼叫慣例與 stack frame 布局在 [Ch 11 — 行程記憶體布局、x86-64 SysV ABI 與 stack frame](./11-process-layout-and-abi.md)。如果你對指標與位址還不熟，先回看 [Ch 3 — 指標入門重建](./03-pointer-basics.md)。

## 第一個除錯階段：一個算錯的平均分數

這是本章前半段的主角，一個有 bug 的小程式：

```c
#include <stdio.h>
#include <string.h>

struct student {
    char name[8];
    int score;
};

static void set_name(struct student *s, const char *name)
{
    strcpy(s->name, name);          /* 沒檢查長度 */
}

static int total(const struct student *s, int n)
{
    int sum = 0;
    for (int i = 0; i < n; i++)
        sum += s[i].score;
    return sum;
}

int main(void)
{
    struct student class[3] = {
        {"Amy", 90}, {"Bob", 85}, {"Cat", 70},
    };
    set_name(&class[1], "Bartholomew");
    int t = total(class, 3);
    printf("total = %d, average = %d\n", t, t / 3);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g -O0 scores.c -o scores && ./scores
total = 7824909, average = 2608303
```

三個分數加起來應該是 245，結果是七百多萬，而且 `-Wall -Wextra` 沒有任何警告。先別急著猜，用 GDB 一步一步看：

```
$ gdb -q -batch -ex 'break main' -ex 'run' -ex 'next' -ex 'next' \
      -ex 'print class' -ex 'print class[1]' -ex 'print/x class[1].score' \
      -ex 'ptype class' -ex 'ptype struct student' -ex 'print sizeof(struct student)' \
      -ex 'step' -ex 'bt' -ex 'info frame' -ex 'finish' -ex 'next' -ex 'print t' ./scores
Breakpoint 1 at 0x1212: file scores.c, line 23.

Breakpoint 1, main () at scores.c:23
23	{
24	    struct student class[3] = {
27	    set_name(&class[1], "Bartholomew");
$1 = {{name = "Amy\000\000\000\000", score = 90}, {name = "Bob\000\000\000\000", score = 85}, {name = "Cat\000\000\000\000", score = 70}}
$2 = {name = "Bob\000\000\000\000", score = 85}
$3 = 0x55
type = struct student {
    char name[8];
    int score;
} [3]
type = struct student {
    char name[8];
    int score;
}
$4 = 12
set_name (s=0x7fffffffe11c, name=0x555555556004 "Bartholomew") at scores.c:11
11	    strcpy(s->name, name);          /* 沒檢查長度 */
#0  set_name (s=0x7fffffffe11c, name=0x555555556004 "Bartholomew") at scores.c:11
#1  0x0000555555555268 in main () at scores.c:27
Stack level 0, frame at 0x7fffffffe100:
 rip = 0x55555555519d in set_name (scores.c:11); saved rip = 0x555555555268
 called by frame at 0x7fffffffe150
 source language c.
 Arglist at 0x7fffffffe0f0, args: s=0x7fffffffe11c, name=0x555555556004 "Bartholomew"
 Locals at 0x7fffffffe0f0, Previous frame's sp is 0x7fffffffe100
 Saved registers:
  rbp at 0x7fffffffe0f0, rip at 0x7fffffffe0f8
main () at scores.c:28
28	    int t = total(class, 3);
29	    printf("total = %d, average = %d\n", t, t / 3);
$5 = 7824909
```

逐一解讀：

| 指令 | 縮寫 | 做了什麼 | 從上面輸出學到什麼 |
|---|---|---|---|
| `break main` | `b` | 在 `main` 開頭設中斷點 | 位址 `0x1212` 是 PIE 執行檔的**相對偏移**，程式還沒載入（見「底層機制」） |
| `run` | `r` | 啟動程式，跑到第一個中斷點 | 停在第 23 行 `{`（函式序言） |
| `next` | `n` | 執行一行，**不進入**函式呼叫 | 初始化陣列跨了兩行 |
| `print` | `p` | 依型別印出運算式的值 | 整個 struct 陣列一次印完，`name` 的 padding `\000` 也看得到 |
| `print/x` | `p/x` | 以十六進位印 | 85 = `0x55` |
| `ptype` | | 印出型別定義 | `class` 是 `struct student [3]` |
| `step` | `s` | 執行一行，**會進入**函式 | 進入 `set_name`，參數值自動印出 |
| `bt` | `backtrace` | 列出呼叫堆疊 | `#0` 是目前函式，`#1` 是呼叫者及返回位址 |
| `info frame` | `i f` | 目前 stack frame 的細節 | 返回位址（saved rip）存在 `0x7fffffffe0f8` |
| `finish` | `fin` | 跑到目前函式返回為止 | 回到 `main` 第 28 行 |

`print sizeof(struct student)` 得到 12：8 byte 的 `name` 加 4 byte 的 `score`。`print` 可以算任何 C 運算式，包括 `sizeof`、指標運算、甚至函式呼叫。到這裡我們知道 `set_name` 之前資料都正常，問題出在它之後。

### `x`：直接看記憶體

`print` 依型別解讀；`x`（examine）則讓你指定**怎麼解讀**：`x/<個數><格式><單位> <位址>`。

| 格式 | 意義 | 單位 | 意義 |
|---|---|---|---|
| `x` | 十六進位 | `b` | byte（1） |
| `d` / `u` | 有號 / 無號十進位 | `h` | halfword（2） |
| `c` | 字元 | `w` | word（4） |
| `s` | C 字串 | `g` | giant（8） |
| `i` | 機械指令 | | |

在 `set_name` 前後各看一次 `class[1]` 的 12 個 byte：

```
$ gdb -q -batch -ex 'break 27' -ex 'run' -ex 'print &class[1]' -ex 'x/12xb &class[1]' \
      -ex 'x/s class[1].name' -ex 'x/3dw &class[0].score' -ex 'next' \
      -ex 'x/12xb &class[1]' -ex 'x/12cb &class[1]' -ex 'print class[1]' ./scores
Breakpoint 1, main () at scores.c:27
27	    set_name(&class[1], "Bartholomew");
$1 = (struct student *) 0x7fffffffe11c
0x7fffffffe11c:	0x42	0x6f	0x62	0x00	0x00	0x00	0x00	0x00
0x7fffffffe124:	0x55	0x00	0x00	0x00
0x7fffffffe11c:	"Bob"
0x7fffffffe118:	90	6451010	0
28	    int t = total(class, 3);
0x7fffffffe11c:	0x42	0x61	0x72	0x74	0x68	0x6f	0x6c	0x6f
0x7fffffffe124:	0x6d	0x65	0x77	0x00
0x7fffffffe11c:	66 'B'	97 'a'	114 'r'	116 't'	104 'h'	111 'o'	108 'l'	111 'o'
0x7fffffffe124:	109 'm'	101 'e'	119 'w'	0 '\000'
$2 = {name = "Bartholo", score = 7824749}
```

畫成圖：

```
 class[1] 的 12 bytes（0x7fffffffe11c 起）
 
 set_name 前：
 ┌──┬──┬──┬──┬──┬──┬──┬──┐┌──┬──┬──┬──┐
 │B │o │b │\0│\0│\0│\0│\0││55│00│00│00│   score = 0x00000055 = 85
 └──┴──┴──┴──┴──┴──┴──┴──┘└──┴──┴──┴──┘
  ◄────── name[8] ───────►◄── score ──►
 
 set_name("Bartholomew") 後：12 個字元 + '\0' = 12 bytes
 ┌──┬──┬──┬──┬──┬──┬──┬──┐┌──┬──┬──┬──┐
 │B │a │r │t │h │o │l │o ││m │e │w │\0│   score = 0x0077656d = 7824749
 └──┴──┴──┴──┴──┴──┴──┴──┘└──┴──┴──┴──┘
                           ▲ strcpy 寫過頭，蓋掉 score
```

`"Bartholomew"` 是 11 個字元，加上結尾 `'\0'` 共 12 byte，`name` 只有 8 byte，多出的 4 byte `"mew\0"` 正好蓋在 `score` 上。x86-64 是 little-endian，`6d 65 77 00` 讀成 `int` 是 `0x0077656d` = 7824749；90 + 7824749 + 70 = 7824909，和程式輸出完全吻合。注意 `print class[1]` 印出 `name = "Bartholo"`：GDB 知道 `name` 只有 8 byte，只印 8 個字元，不會跟著 `strcpy` 一起越界。

這裡藏了一個 `x` 的經典陷阱：`x/3dw &class[0].score` 印出 `90 6451010 0`，不是三個學生的分數。`x` 只會從起始位址開始**連續**讀 3 個 4-byte word，不懂 struct 的間距（stride 是 12 不是 4）。第二個 word 其實是 `class[1].name` 的前 4 byte `"Bob\0"` = `0x00626f42` = 6451010。要印每個學生的分數，用 `print class[0].score` 或 GDB 的 `print class[0]@3`。

### `watch`：誰改了我的變數？

上面是我們「猜到」問題在 `set_name`，才在它前後觀察。真實專案裡你通常不知道兇手在哪——可能是十層呼叫之外的某個函式寫壞了記憶體。這時用 **watchpoint**：讓 GDB 在某個記憶體位置**被寫入**時停下來。

```
$ gdb -q -batch -ex 'break 27' -ex 'run' -ex 'watch class[1].score' \
      -ex 'continue' -ex 'bt' -ex 'continue' ./scores
Breakpoint 1, main () at scores.c:27
27	    set_name(&class[1], "Bartholomew");
Hardware watchpoint 2: class[1].score

Hardware watchpoint 2: class[1].score

Old value = 85
New value = 7824749
__strcpy_avx2 () at ../sysdeps/x86_64/multiarch/strcpy-avx2.S:622
622	../sysdeps/x86_64/multiarch/strcpy-avx2.S: No such file or directory.
#0  __strcpy_avx2 () at ../sysdeps/x86_64/multiarch/strcpy-avx2.S:622
#1  0x00005555555551b0 in set_name (s=0x7fffffffe11c, name=0x555555556004 "Bartholomew") at scores.c:11
#2  0x0000555555555268 in main () at scores.c:27

Watchpoint 2 deleted because the program has left the block in
which its expression is valid.
```

GDB 停在 glibc 的 `__strcpy_avx2` 裡面——寫入 `score` 的那一道指令就在這裡；"No such file or directory" 只是說本機沒有 glibc 的原始碼，不影響除錯。`bt` 往上看一層，兇手是 `scores.c:11` 的 `set_name`。整個過程我們**不需要事先懷疑任何函式**。

最後一段訊息也值得注意：`class` 是 `main` 的區域變數，`main` 返回後它就不存在了，GDB 自動刪除這個 watchpoint。這反映了 C 的 storage duration（[Ch 7](./07-pointers-by-the-standard.md) 會從規格書角度定義）——GDB 比很多人更清楚區域變數的生命週期。

相關指令：`rwatch`（被讀取時停）、`awatch`（讀或寫都停）、`watch -l <expr>`（盯住運算式當下算出的**位址**，不隨作用域失效）。

## 底層機制：GDB 是怎麼做到的？

### ptrace：一切的基礎

Linux 上 GDB 對行程的所有操作都經過 `ptrace(2)` 系統呼叫：

```
 gdb                                   kernel                     inferior
  │ fork()                               │                           │
  │──────────────────────────────────────┼──────────────────────────▶│ 子行程
  │                                      │       ptrace(TRACEME)     │
  │                                      │◀──────────────────────────│
  │                                      │       execve("./scores")  │
  │                                      │◀──────────────────────────│
  │  waitpid() 收到 SIGTRAP：子行程停在第一道指令前                   │
  │◀─────────────────────────────────────│                           │
  │  ptrace(PEEKDATA/POKEDATA, addr)  讀寫記憶體                     │
  │  ptrace(GETREGS/SETREGS)          讀寫暫存器                     │
  │  ptrace(CONT) / ptrace(SINGLESTEP) 繼續 / 單步                   │
  │─────────────────────────────────────▶│──────────────────────────▶│
```

被追蹤的行程每收到一個 signal（包括中斷點觸發的 `SIGTRAP`、crash 時的 `SIGSEGV`）都會先停下來，由 kernel 通知 GDB，GDB 決定要不要把 signal 轉交給程式。

`attach` 到一個已在執行的行程也是用 ptrace，但 Ubuntu 預設的 Yama 安全模組會限制它：

```
$ cat /proc/sys/kernel/yama/ptrace_scope
1
```

`1` 表示只能 attach 到自己的子孫行程。所以 `gdb -p <pid>` 去 attach 一個不相干的行程會失敗，需要 `sudo` 或調整這個值（需要 root，本章未實測 attach）。

### 軟體中斷點：把一個 byte 換成 `int3`

`break main` 做的事，是把那個位址的第一個 byte 暫時換成 `0xCC`——x86 的 `int3` 指令，專門用來觸發除錯例外：

```
 原本的機械碼                       插入中斷點後
 0x555555555212: f3 0f 1e fa ...    0x555555555212: cc 0f 1e fa ...
                 (endbr64)                          ▲ int3
 
 CPU 執行到 0xCC → 除錯例外 → kernel 送 SIGTRAP → 行程停住 → gdb 接手
 繼續執行時：gdb 把原本的 f3 寫回、rip 退回一格、單步一道指令、
            再把 0xCC 放回去，然後 ptrace(CONT)
```

GDB 在你讀記憶體時會把原本的 byte 顯示給你，所以你在 `x/i` 裡看不到 `0xCC`。這也解釋了兩件事：

- **中斷點位址在 `run` 之前只是偏移**。現代 Ubuntu 的 gcc 預設產生 PIE（位置無關執行檔），載入位址要等執行時才知道：

  ```
  $ gdb -q -batch -ex 'info address main' -ex 'start' -ex 'info address main' ./scores
  Symbol "main" is a function at address 0x1206.
  ...
  Symbol "main" is a function at address 0x555555555206.
  ```

  GDB 預設關閉 ASLR（`set disable-randomization on`），所以每次在 GDB 裡跑，PIE 都載入在 `0x555555554000`，位址可重現；直接在 shell 執行時則每次不同。
- **軟體中斷點的數量沒有上限**，因為它只是改記憶體。

### 硬體 watchpoint：CPU 的除錯暫存器

watchpoint 不能用 `int3` 實作——你不知道哪一道指令會寫那個位址。x86 提供 **除錯暫存器** DR0–DR3 存放要監看的位址，DR7 設定監看條件（寫入、讀寫、執行）與長度（1、2、4、8 byte）。CPU 每次存取記憶體時由硬體比對，命中就觸發例外，**沒有任何效能代價**。上面的輸出寫著 "Hardware watchpoint 2"，就是用了這個機制。

代價是**只有 4 個**。監看的東西超過硬體能力時（太多、太大），GDB 會退回**軟體 watchpoint**：每執行一道指令就單步停下來檢查值有沒有變，速度可能慢上數百倍以上（確切倍數取決於程式，這裡只是量級的說法）。

### DWARF：位址與原始碼之間的地圖

`-g` 讓 gcc 在 ELF 執行檔裡加入一組 `.debug_*` section：

```
$ readelf -S --wide scores | grep debug      # 節錄 section 名稱
.debug_aranges .debug_info .debug_abbrev .debug_line .debug_str .debug_line_str
$ readelf -S --wide scores_nog | grep -c debug
0
$ objdump --dwarf=decodedline scores | grep -E "scores.c +1[0-9] "
scores.c                                      10              0x1189               x
scores.c                                      11              0x119d               x
scores.c                                      11              0x11a1               x
scores.c                                      12              0x11b0               x
scores.c                                      15              0x11b3               x
scores.c                                      16              0x11c2               x
```

`.debug_line` 是「行號 ↔ 位址」對照表（`break 27`、`next` 靠它）；`.debug_info` 描述每個變數的型別與位置（`print class` 靠它）。拿掉 `-g` 會怎樣？

```
$ gcc -O0 scores.c -o scores_nog
$ gdb -q -batch -ex 'break total' -ex 'run' -ex 'info locals' -ex 'list' ./scores_nog
Breakpoint 1 at 0x11bb

Breakpoint 1, 0x00005555555551bb in total ()
No symbol table info available.
1	../sysdeps/x86/abi-note.c: No such file or directory.
```

函式名稱還在（來自 ELF 的符號表 `.symtab`），但沒有行號、沒有區域變數、沒有型別。GDB 退化成只能看組合語言與暫存器的工具。

## 條件斷點、display 與修改變數

### 條件斷點

迴圈跑一百萬次、只有第 999,999 次出錯時，你不會想按一百萬次 `continue`。條件斷點只在條件成立時停：

```
$ gdb -q -batch -ex 'break 18 if i == 1' -ex 'run' -ex 'info locals' \
      -ex 'display sum' -ex 'display s[i]' -ex 'next' -ex 'next' \
      -ex 'info display' -ex 'info breakpoints' ./scores
Breakpoint 1, total (s=0x7fffffffe110, n=3) at scores.c:18
18	        sum += s[i].score;
i = 1
sum = 90
17	    for (int i = 0; i < n; i++)
1: sum = 7824839
2: s[i] = {name = "Bartholo", score = 7824749}
18	        sum += s[i].score;
1: sum = 7824839
2: s[i] = {name = "Cat\000\000\000\000", score = 70}
Auto-display expressions now in effect:
Num Enb Expression
1:   y  sum
2:   y  s[i]
Num     Type           Disp Enb Address            What
1       breakpoint     keep y   0x00005555555551d2 in total at scores.c:18
	stop only if i == 1
	breakpoint already hit 1 time
```

- `break 18 if i == 1`：條件可以是任何 C 運算式，例如 `if p == 0`、`if strcmp(name, "Bob") == 0`。對已存在的中斷點加條件用 `condition <編號> <運算式>`。
- `info locals` 印出目前函式所有區域變數。
- `display`：每次程式停下來都自動印出。追一個變數在迴圈中怎麼變化時，比每次手打 `print` 方便得多；`undisplay <編號>` 取消。
- `info breakpoints` 顯示命中次數，`ignore <編號> <N>` 可以跳過前 N 次命中。

注意條件斷點的實作方式：GDB 仍然在那個位址放 `int3`，**每次命中都停下來**算條件，不成立再自動繼續。熱迴圈裡的條件斷點因此會讓程式明顯變慢。

### 改變數值、看函式回傳值

GDB 不只能看，還能改。假設我們想確認「如果 `score` 沒被蓋掉，總分會不會對」，不必改程式重編：

```
$ gdb -q -batch -ex 'break total' -ex 'run' -ex 'set var s[1].score = 85' \
      -ex 'print s[1]' -ex 'finish' -ex 'continue' ./scores
Breakpoint 1, total (s=0x7fffffffe110, n=3) at scores.c:16
16	    int sum = 0;
$1 = {name = "Bartholo", score = 85}
0x0000555555555279 in main () at scores.c:28
28	    int t = total(class, 3);
Value returned is $2 = 245
total = 245, average = 81
[Inferior 1 (process 8334) exited normally]
```

`finish` 跑完 `total` 後印出 "Value returned is $2 = 245"：函式回傳值放在 `rax`，GDB 依回傳型別幫你讀出來。驗證了假設——`total` 本身沒問題，問題完全出在被蓋掉的資料。注意 `s` 是 `const struct student *`，C 語言不允許透過它寫入，但 GDB 直接改記憶體，不受 C 型別系統約束。

### `start`、`tbreak`、`until`

```
$ gdb -q -batch -ex 'start' -ex 'info args' -ex 'tbreak total' -ex 'continue' \
      -ex 'until 19' -ex 'info locals' ./scores
Temporary breakpoint 1, main () at scores.c:23
23	{
No arguments.
Temporary breakpoint 2, total (s=0x7fffffffe110, n=3) at scores.c:16
16	    int sum = 0;
total (s=0x7fffffffe110, n=3) at scores.c:19
19	    return sum;
sum = 7824909
```

- `start` = 在 `main` 設臨時中斷點再 `run`。
- `tbreak`：命中一次就自動刪除的中斷點。
- `until 19`：一路跑到第 19 行（或目前函式返回），**跳過整個迴圈**，不用一圈一圈 `next`。

## Crash 與 core dump 分析

第二個程式：鏈結串列找第一個大於 `limit` 的節點，忘了檢查走到串列結尾的情況。

```c
#include <stdio.h>
#include <stdlib.h>

struct node {
    int key;
    struct node *next;
};

static struct node *push(struct node *head, int key)
{
    struct node *n = malloc(sizeof *n);
    n->key = key;
    n->next = head;
    return n;
}

/* 找第一個 key 大於 limit 的節點，回傳它的 key */
static int first_greater(const struct node *p, int limit)
{
    while (p->key <= limit)        /* bug：沒檢查 p 是否為 NULL */
        p = p->next;
    return p->key;
}

int main(int argc, char *argv[])
{
    int limit = argc > 1 ? atoi(argv[1]) : 2;
    struct node *head = NULL;
    for (int k = 1; k <= 3; k++)
        head = push(head, k);
    printf("first > %d: %d\n", limit, first_greater(head, limit));
    return 0;
}
```

```
$ gcc -Wall -Wextra -g -O0 list.c -o list
$ ./list
first > 2: 3
$ ./list 5; echo "exit=$?"
Segmentation fault      (core dumped)
exit=139
```

結束碼 139 = 128 + 11，11 是 `SIGSEGV` 的編號：shell 用「128 + signal 編號」表示行程被 signal 殺死。

### 在 GDB 裡重現 crash

```
$ gdb -q -batch -ex 'run 5' -ex 'bt' -ex 'print p' -ex 'frame 1' -ex 'info locals' \
      -ex 'print *head' -ex 'print *head->next' -ex 'print *head->next->next' \
      -ex 'generate-core-file core.list' ./list

Program received signal SIGSEGV, Segmentation fault.
0x00005555555551e6 in first_greater (p=0x0, limit=5) at list.c:20
20	    while (p->key <= limit)        /* bug：沒檢查 p 是否為 NULL */
#0  0x00005555555551e6 in first_greater (p=0x0, limit=5) at list.c:20
#1  0x000055555555526c in main (argc=2, argv=0x7fffffffe248) at list.c:31
$1 = (const struct node *) 0x0
#1  0x000055555555526c in main (argc=2, argv=0x7fffffffe248) at list.c:31
31	    printf("first > %d: %d\n", limit, first_greater(head, limit));
limit = 5
head = 0x5555555592e0
$2 = {key = 3, next = 0x5555555592c0}
$3 = {key = 2, next = 0x5555555592a0}
$4 = {key = 1, next = 0x0}
warning: target file /proc/8804/cmdline contained unexpected null characters
Saved corefile core.list
```

`bt` 第一行就給出答案：`first_greater (p=0x0, ...)`，`p` 是 NULL。`frame 1` 切換到呼叫者 `main` 的 frame，可以看它的區域變數；順著 `head->next` 走一遍，串列是 3 → 2 → 1 → NULL，沒有任何節點大於 5，迴圈走到盡頭解參考 NULL。最後的 `warning` 是 GDB 讀 `/proc/<pid>/cmdline` 時對引數間 `\0` 分隔字元的提示，不影響結果。

### Core dump：事後驗屍

上面最後一個指令 `generate-core-file` 把**當下整個行程的記憶體與暫存器**存成一個 core 檔。core dump 的價值在於：crash 發生在客戶的機器、CI 伺服器或半夜的正式環境時，你不在現場，但可以事後把現場搬回來看。

```
$ gdb -q -batch -ex 'bt' -ex 'info registers rip rax' -ex 'x/i $pc' \
      -ex 'print p' -ex 'frame 1' -ex 'print limit' ./list core.list
[New LWP 8804]
Core was generated by `/home/ypp/c_sysprog_lab/p1/list 5'.
Program terminated with signal SIGSEGV, Segmentation fault.
#0  0x00005555555551e6 in first_greater (p=0x0, limit=5) at list.c:20
20	    while (p->key <= limit)        /* bug：沒檢查 p 是否為 NULL */
#0  0x00005555555551e6 in first_greater (p=0x0, limit=5) at list.c:20
#1  0x000055555555526c in main (argc=2, argv=0x7fffffffe248) at list.c:31
rip            0x5555555551e6      0x5555555551e6 <first_greater+33>
rax            0x0                 0
=> 0x5555555551e6 <first_greater+33>:	mov    (%rax),%eax
$1 = (const struct node *) 0x0
#1  0x000055555555526c in main (argc=2, argv=0x7fffffffe248) at list.c:31
31	    printf("first > %d: %d\n", limit, first_greater(head, limit));
$2 = 5
```

程式已經不存在了，但 `bt`、`print`、`frame` 都照樣能用（只是不能 `next` 或 `continue`）。`x/i $pc` 印出 crash 的那道指令：`mov (%rax),%eax`，意思是「從 `rax` 指向的位址讀 4 byte 到 `eax`」，而 `rax` 是 0。這就是 `p->key` 的機械碼：`key` 是 struct 的第一個成員，偏移 0，所以直接讀 `*p`。

### 讓系統自動產生 core dump

上面用 GDB 的 `generate-core-file` 手動產生 core，是因為本章的環境不方便自動產生。Linux 決定 core 檔去向的是 `/proc/sys/kernel/core_pattern`，而本機（WSL2）是：

```
$ cat /proc/sys/kernel/core_pattern
|/wsl-capture-crash %t %E %p %s
$ ulimit -c
0
```

開頭的 `|` 表示 kernel 把 core 內容**以管線交給一支程式**，WSL 用它收集當機資訊，所以 shell 雖然印出 "(core dumped)"，目前目錄裡卻找不到 core 檔。一般 Ubuntu 上這支程式通常是 apport 或 systemd-coredump（後者可以用 `coredumpctl gdb` 直接開啟最近的 core）。要讓 core 檔直接寫在目前目錄，典型的設定如下（**需要 root，本段未實測**）：

```
$ ulimit -c unlimited                              # 只影響目前 shell
$ sudo sysctl -w kernel.core_pattern=core.%e.%p    # %e 程式名、%p PID
$ ./list 5
Segmentation fault (core dumped)
$ gdb ./list core.list.<pid>
```

core 檔必須搭配**同一份**有 `-g` 的執行檔才讀得出符號；正式環境常見的做法是發布 strip 過的執行檔，另外保存除錯資訊（`objcopy --only-keep-debug`），驗屍時再配對。

### 對照組合語言：`disassemble /s`

```
$ gdb -q -batch -ex 'set disassembly-flavor intel' -ex 'disassemble /s first_greater' ./list
Dump of assembler code for function first_greater:
list.c:
19	{
   0x00000000000011c5 <+0>:	endbr64 
   0x00000000000011c9 <+4>:	push   rbp
   0x00000000000011ca <+5>:	mov    rbp,rsp
   0x00000000000011cd <+8>:	mov    QWORD PTR [rbp-0x8],rdi
   0x00000000000011d1 <+12>:	mov    DWORD PTR [rbp-0xc],esi

20	    while (p->key <= limit)        /* bug：沒檢查 p 是否為 NULL */
   0x00000000000011d4 <+15>:	jmp    0x11e2 <first_greater+29>

21	        p = p->next;
   0x00000000000011d6 <+17>:	mov    rax,QWORD PTR [rbp-0x8]
   0x00000000000011da <+21>:	mov    rax,QWORD PTR [rax+0x8]
   0x00000000000011de <+25>:	mov    QWORD PTR [rbp-0x8],rax

20	    while (p->key <= limit)        /* bug：沒檢查 p 是否為 NULL */
   0x00000000000011e2 <+29>:	mov    rax,QWORD PTR [rbp-0x8]
   0x00000000000011e6 <+33>:	mov    eax,DWORD PTR [rax]
   0x00000000000011e8 <+35>:	cmp    DWORD PTR [rbp-0xc],eax
   0x00000000000011eb <+38>:	jge    0x11d6 <first_greater+17>

22	    return p->key;
   0x00000000000011ed <+40>:	mov    rax,QWORD PTR [rbp-0x8]
   0x00000000000011f1 <+44>:	mov    eax,DWORD PTR [rax]

23	}
   0x00000000000011f3 <+46>:	pop    rbp
   0x00000000000011f4 <+47>:	ret    
End of assembler dump.
```

`/s` 把原始碼行穿插在指令之間。幾個值得注意的細節：`p` 存在 `[rbp-0x8]`、`limit` 在 `[rbp-0xc]`（`-O0` 下每個變數都放在 stack 上）；`p->next` 是 `[rax+0x8]`——`key` 佔 4 byte，但 `next` 是 8 byte 指標，要對齊到 8，所以中間有 4 byte padding（[Ch 16](./16-alignment-and-padding.md)）；crash 的 `<+33>` 正是 `mov eax,DWORD PTR [rax]`。`set disassembly-flavor intel` 切換成 Intel 語法（目的運算元在前），預設是 AT&T 語法（前一節 `x/i` 的 `mov (%rax),%eax`）。

## 把除錯寫成腳本

重複的除錯步驟可以寫成 GDB 腳本。下面的腳本在每次 `push` 時印出參數但**不停下來**，並在 `first_greater` 的迴圈裡用 `dprintf` 印出走訪的節點：

```gdb
# trace.gdb：每次進入 push 就印出參數，不停下來
set pagination off
break push
commands
  silent
  printf "push(head=%p, key=%d)\n", head, key
  continue
end

# 在 first_greater 每一輪迴圈印出目前節點
dprintf list.c:21,"  visit key=%d\n", p->key

run 5
bt
```

```
$ gdb -q -batch -x trace.gdb ./list
Breakpoint 1 at 0x119c: file list.c, line 11.
Dprintf 2 at 0x11d6: file list.c, line 21.
push(head=(nil), key=1)
push(head=0x5555555592a0, key=2)
push(head=0x5555555592c0, key=3)
  visit key=3
  visit key=2
  visit key=1

Program received signal SIGSEGV, Segmentation fault.
0x00005555555551e6 in first_greater (p=0x0, limit=5) at list.c:20
20	    while (p->key <= limit)        /* bug：沒檢查 p 是否為 NULL */
#0  0x00005555555551e6 in first_greater (p=0x0, limit=5) at list.c:20
#1  0x000055555555526c in main (argc=2, argv=0x7fffffffe248) at list.c:31
```

- `commands ... end`：中斷點命中時自動執行的指令序列；`silent` 不印預設的 "Breakpoint 1, ..." 訊息，最後 `continue` 讓程式繼續跑。
- `dprintf`：「動態 printf」，等於「中斷點 + printf + continue」的簡寫。**這才是本章標題的意思**：需要 printf 式的追蹤時，不用改原始碼、不用重新編譯，還能隨時加減。
- `-batch -x script`：非互動執行腳本，適合放進 CI 或重現 bug 的 shell script。單行的則用多個 `-ex`。
- `~/.gdbinit` 與專案目錄的 `.gdbinit` 會在啟動時自動載入；後者基於安全考量預設不執行，需要在 `~/.gdbinit` 加 `add-auto-load-safe-path`。

## 反向執行：讓時間倒流

有時你停在 crash 點，想知道「上一步發生了什麼」。GDB 內建 process record，可以記錄執行過程並**倒著走**。先看失敗的嘗試：

```
$ gdb -q -batch -ex 'break 27' -ex 'run' -ex 'record full' -ex 'next' ./scores
Breakpoint 1, main () at scores.c:27
27	    set_name(&class[1], "Bartholomew");
Process record does not support instruction 0xc5 at address 0x7ffff7d9ed3a.
Process record: failed to record execution log.

Program stopped.
__strcpy_avx2 () at ../sysdeps/x86_64/multiarch/strcpy-avx2.S:66
```

`0xc5` 是 AVX 指令的 VEX 前綴。glibc 執行時偵測到 CPU 支援 AVX2，選用了 `__strcpy_avx2`，而 GDB 12 的 record 不認得這些指令。解法之一是用 glibc 的 tunable 讓它在這次執行中改用非 AVX 版本：

```
$ gdb -q -batch -ex 'set environment GLIBC_TUNABLES=glibc.cpu.hwcaps=-AVX2,-AVX,-EVEX' \
      -ex 'break 27' -ex 'run' -ex 'record full' -ex 'next' -ex 'print class[1].score' \
      -ex 'reverse-next' -ex 'print class[1].score' ./scores
Breakpoint 1, main () at scores.c:27
27	    set_name(&class[1], "Bartholomew");
28	    int t = total(class, 3);
$1 = 7824749

No more reverse-execution history.
main () at scores.c:27
27	    set_name(&class[1], "Bartholomew");
$2 = 85
```

`reverse-next` 倒退一行，`score` 從 7824749 回到 85——記憶體真的被還原了。也可以只錄純 C 的區段（不呼叫 libc），例如在 `total` 裡 `record full` 後 `next` 幾圈再 `reverse-next`，本機實測同樣可行。

GDB 的 record 每道指令都要記錄，速度慢、記憶體耗用大，適合短區段。長時間、難以重現的 bug 用 [rr](https://rr-project.org/)（Mozilla 開發的 Record and Replay Framework）：錄一次、無限次重播，重播時可以 `reverse-continue` 直接退回 watchpoint 被觸發的那一刻。rr 依賴 CPU 的效能計數器，在部分虛擬化環境（包括某些 WSL2 設定）不能用；本章**未實測 rr**。

## TUI：在終端機裡看原始碼

`gdb -tui ./scores`，或在 GDB 中輸入 `tui enable`（也可按 `Ctrl-x a` 切換），會把終端機分成原始碼視窗與指令視窗。TUI 是全螢幕介面，無法以 `-batch` 擷取輸出，下圖是**示意**，不是實際擷取的畫面：

```
┌─scores.c──────────────────────────────────────────────────┐
│   14  static int total(const struct student *s, int n)    │
│   15  {                                                    │
│   16      int sum = 0;                                     │
│B+>17      for (int i = 0; i < n; i++)                      │
│   18          sum += s[i].score;                           │
│   19      return sum;                                      │
│   20  }                                                    │
└────────────────────────────────────────────────────────────┘
(gdb) next
(gdb) print sum
$1 = 0
```

`B+` 表示該行有中斷點且已啟用，`>` 是目前執行位置。常用操作（GDB 12.1 的 `help layout` 列出的版面有 `src`、`asm`、`split`、`regs`）：

| 操作 | 作用 |
|---|---|
| `layout src` / `layout asm` / `layout split` | 原始碼 / 組合語言 / 兩者並列 |
| `layout regs` | 加上暫存器視窗，單步時變動的暫存器會高亮 |
| `focus cmd` / `focus src` | 方向鍵要捲動哪個視窗 |
| `Ctrl-l` | 程式輸出把畫面弄亂時重繪 |
| `Ctrl-x a` | 離開／進入 TUI |

## GDB 不是唯一的工具：和 sanitizer、FORTIFY 比較

回頭看 `scores.c` 的 bug，很自然會問：用 AddressSanitizer 不是更快嗎？

```
$ gcc -g -fsanitize=address,undefined scores.c -o scores_asan && ./scores_asan; echo "exit=$?"
total = 7824909, average = 2608303
exit=0
```

**ASan 沒抓到。** ASan 在每個 object 的前後放「紅區」（redzone），越界寫到紅區才會被發現；這個 bug 是從 `name` 寫進同一個 struct 的 `score`，寫入的位址仍在 `class` 陣列這個 object 內部，ASan 看不出問題。反倒是 Ubuntu gcc 在 `-O2` 預設開啟的 `_FORTIFY_SOURCE` 抓到了：

```
$ gcc -O2 -Wall scores.c -o scores_f
.../bits/string_fortified.h:79:10: warning: ‘__builtin___strcpy_chk’ writing 12 bytes into a region of size 8 [-Wstringop-overflow=]
$ ./scores_f
*** buffer overflow detected ***: terminated
Aborted                 (core dumped)
```

`-O2` 讓編譯器把 `strcpy` 內聯進 `main`，知道目的地 `name` 只有 8 byte，於是在編譯時警告，執行時改呼叫會檢查長度的 `__strcpy_chk` 並中止程式。

| 工具 | 擅長 | 盲點 | 成本 |
|---|---|---|---|
| `printf` | 快速確認一個假設；追長時間的趨勢 | 只看到格式字串框住的；格式不符本身是 UB；要改碼重編 | 低，但每次改都要重編 |
| GDB | 任意時間點看任意記憶體；watchpoint 找兇手；core 事後分析 | 你得知道往哪看；最佳化後變數可能 `<optimized out>` | 學習成本 |
| ASan / UBSan | 自動抓越界、UAF、UB，報告直接指到原始碼行 | object 內部越界（如上例）；只抓**這次執行**走到的路徑 | 約 2 倍執行時間（ASan 文件的典型值） |
| `_FORTIFY_SOURCE` | 零成本抓已知長度的緩衝區溢位 | 只涵蓋特定函式、只在編譯器知道物件大小時有效 | 幾乎為零 |
| rr | 難以重現的 bug、多執行緒競爭 | 需要硬體效能計數器支援 | 錄製時約 1.2–2 倍（依官方說法，未實測） |

實務上的順序通常是：**sanitizer 先跑一遍**（成本最低、涵蓋面廣）→ 有 crash 或可疑輸出時**用 GDB 精確定位** → 難以重現時**用 rr 錄下來**。[Ch 0](./00-environment-setup.md) 已經設定好 sanitizer 的編譯選項，[Ch 34](./34-undefined-behavior.md) 會比較 UBSan 能抓與抓不到的 UB。

## 對比與取捨：常用指令速查

| 情境 | 指令 |
|---|---|
| 啟動 | `run [args]`、`start`、`gdb --args ./prog a b` |
| 中斷點 | `break 檔案:行`、`break 函式`、`tbreak`、`break ... if 條件`、`info b`、`delete N`、`disable N` |
| 執行控制 | `next`（不進函式）、`step`（進函式）、`finish`、`until N`、`continue`、`stepi`/`nexti`（一道指令） |
| 看資料 | `print`、`print/x`、`print arr@N`、`ptype`、`info locals`、`info args`、`display` |
| 看記憶體 | `x/Nfu addr`（例：`x/16xb`、`x/4gx`、`x/s`、`x/10i $pc`） |
| 堆疊 | `bt`、`bt full`、`frame N`、`up`、`down`、`info frame` |
| 監看 | `watch`、`rwatch`、`awatch`、`watch -l` |
| 修改 | `set var x = 1`、`print func(arg)`（呼叫程式中的函式） |
| 組合語言 | `disassemble /s`、`info registers`、`x/i $pc`、`set disassembly-flavor intel` |
| 事後分析 | `gdb prog core`、`generate-core-file` |

## 踩雷集錦

1. **「我加了 `-g` 但變數顯示 `<optimized out>`」**：`-g` 只是附上地圖，不會關閉最佳化。以 `-O2` 編譯 `list.c`：

   ```
   $ gcc -g -O2 list.c -o list_o2
   $ gdb -q -batch -ex "break first_greater" -ex "run 2" -ex "bt" -ex "info args" ./list_o2
   Breakpoint 1, first_greater (limit=<optimized out>, p=0x5555555592e0) at list.c:20
   #0  first_greater (limit=<optimized out>, p=0x5555555592e0) at list.c:20
   #1  main (argc=<optimized out>, argv=<optimized out>) at list.c:31
   limit = <optimized out>
   p = 0x5555555592e0
   ```

   `limit` 被最佳化掉了（值可能只存在暫存器裡、隨即被覆蓋）；`#1` 沒有返回位址，因為 `first_greater` 被**內聯**進 `main`，已經不是真正的函式呼叫。除錯時用 `-O0 -g`，或折衷的 `-Og`。
2. **「`x/3dw &arr[0].field` 會印出每個元素的 field」**：`x` 只會連續讀記憶體，不懂 struct 的間距。本章的例子印出 `90 6451010 0`，第二個值其實是下一個元素 `name` 的前 4 byte。
3. **「watchpoint 會一直跟著變數」**：監看區域變數時，函式返回後 watchpoint 會被自動刪除（"left the block in which its expression is valid"）。要盯住某個位址本身，用 `watch -l` 或 `watch *(int *) 0x...`。
4. **「ASan 會抓所有越界」**：同一個 object 內部的越界（struct 成員之間、陣列內的 struct 之間）它看不到，本章的 `scores.c` 就是實例。
5. **「GDB 裡跑正常、外面跑會 crash」（或相反）**：GDB 預設關閉 ASLR、環境變數也略有不同（例如 `LINES`、`COLUMNS`），stack 的位址因此和直接執行不同。依賴未初始化變數或 stack 內容的 bug 在兩種情況下行為可能不一樣；用 `set disable-randomization off` 讓 GDB 內的環境更接近真實執行。

## 進階：再往深一層

- **Python API 與 pretty printer**：GDB 內嵌 Python，可以寫自訂指令與 pretty printer。glibc、libstdc++ 都附帶 pretty printer；替自己的資料結構（例如 [Ch 19](./19-linux-list-head.md) 的 `list_head`）寫一個，`print` 就能直接印出整條串列。
- **`info proc mappings`**：列出行程的記憶體映射，對照 [Ch 11](./11-process-layout-and-abi.md) 的行程布局：

  ```
  $ gdb -q -batch -ex 'start' -ex 'info proc mappings' ./scores    # 節錄
            Start Addr           End Addr       Size     Offset  Perms  objfile
        0x555555554000     0x555555555000     0x1000        0x0  r--p   .../scores
        0x555555555000     0x555555556000     0x1000     0x1000  r-xp   .../scores
        0x555555558000     0x555555559000     0x1000     0x3000  rw-p   .../scores
        0x7ffff7c00000     0x7ffff7c28000    0x28000        0x0  r--p   /usr/lib/x86_64-linux-gnu/libc.so.6
  ```

- **遠端除錯**：`gdbserver :1234 ./prog` 在目標機器上執行，主機端 `target remote ip:1234` 連線。嵌入式開發與 QEMU 模擬（`qemu -s -S`）都用這個協定，連 Linux 核心也能這樣單步除錯。
- **反除錯**：程式可以呼叫 `ptrace(PTRACE_TRACEME)` 偵測自己是否被追蹤——一個行程同時只能被一個 tracer 追蹤。這是原文延伸閱讀中 "Linux basic anti-debug" 的核心原理，逆向工程時常遇到。
- **面試題**：「軟體中斷點和硬體中斷點差在哪？」——前者改寫指令為 `int3`、數量無限但不能用在唯讀記憶體（如 ROM），後者用除錯暫存器、x86 上只有 4 個但不改程式碼。

## 動手練習

1. 把 `scores.c` 的 `"Bartholomew"` 改成 `"Bartholomew Simpson"`，預測哪些欄位會被蓋掉，再用 `x/36xb class` 驗證；然後對 `class[2].score` 下 watchpoint，確認是誰改了它。
2. 在 `list.c` 的 `first_greater` 加上 NULL 檢查（找不到時回傳 -1），寫一個 GDB 腳本：對 `first_greater` 設中斷點、用 `commands` 印出 `limit` 與 `finish` 後的回傳值，以 `-batch -x` 對 `./list 0`、`./list 2`、`./list 5` 各跑一次。
3. 寫一個會在第 1000 次迴圈時把陣列寫出界的程式，比較三種找法花的時間：(a) 加 `printf`；(b) 條件斷點 `break ... if i == 999`；(c) 對越界會蓋到的變數下 watchpoint。
4. 用 `-O0` 與 `-O2` 各編一次 `list.c`，比較 `disassemble /s first_greater` 的結果，數一數 `-O2` 少了哪些記憶體存取。

## 本章重點整理

- `printf` 只看得到你事先框定的資料，格式不符本身就是 UB；GDB 依 DWARF 型別資訊解讀記憶體，可以在任何時間點看任何東西。
- GDB 靠 ptrace 控制行程、靠 `int3` 實作軟體中斷點、靠 DR0–DR3 實作硬體 watchpoint、靠 `-g` 產生的 DWARF 把位址對回原始碼。
- watchpoint 回答「誰改了這個值」，core dump 讓你事後還原現場，`commands`/`dprintf`/腳本讓除錯可重複。
- 最佳化會讓變數 `<optimized out>`、函式被內聯；sanitizer 與 GDB 互補，各有盲點。

## 自我檢核

- [ ] 不看筆記，能畫出「gdb ↔ ptrace ↔ inferior」的關係，並解釋 `break main` 在記憶體裡實際改了什麼嗎？
- [ ] 為什麼硬體 watchpoint 幾乎沒有效能代價，而軟體 watchpoint 很慢？什麼情況下 GDB 會退回軟體 watchpoint？
- [ ] 同事說「程式在客戶那邊 crash，但我這邊跑不出來」，你會請他提供哪些東西、用哪些 GDB 指令分析？
- [ ] 能解釋本章 `scores.c` 的 bug 為什麼 ASan 抓不到、`_FORTIFY_SOURCE` 卻抓得到嗎？
- [ ] 想一個你最近用 `printf` 除錯的問題，如果改用 watchpoint 或條件斷點，會快多少？

## 延伸閱讀

### 官方文件 / Spec

- **[Debugging with GDB](https://sourceware.org/gdb/current/onlinedocs/gdb/)** — GDB 官方手冊
  - **讀哪裡**：〈Breakpoints, Watchpoints, and Catchpoints〉（含 Setting Watchpoints、Breakpoint Command Lists 兩小節）、〈Examining Memory〉、〈Running Programs Backward〉、〈GDB Text User Interface〉四章節
  - **能學到什麼**：本章每個指令的完整選項；例如 `watch` 的 `-location` 與 `mask` 參數
  - **前提**：讀完本章，知道要查哪個指令
- **[ptrace(2) man page](https://man7.org/linux/man-pages/man2/ptrace.2.html)** — Linux man-pages
  - **讀哪裡**：DESCRIPTION 的前半（`PTRACE_TRACEME`、`PEEKDATA`、`CONT`、`SINGLESTEP`）與 "Ptrace access mode checking"
  - **能學到什麼**：GDB 底層呼叫的確切語意，以及 Yama `ptrace_scope` 的限制
  - **前提**：知道 fork/exec 與 signal 的基本概念
- **[DWARF Debugging Information Format, Version 5](https://dwarfstd.org/dwarf5std.html)**
  - **讀哪裡**：先讀同站的 [Introduction to the DWARF Debugging Format](https://dwarfstd.org/doc/Debugging%20using%20DWARF-2012.pdf)（Michael Eager，約 10 頁的入門）；規格本文看第 6.2 節 Line Number Information
  - **能學到什麼**：`.debug_line` 如何用狀態機壓縮行號表，`.debug_info` 如何描述型別
  - **前提**：本章「DWARF」一節；選讀

### 部落格 / 技術文章

- **[How debuggers work](https://eli.thegreenplace.net/2011/01/23/how-debuggers-work-part-1)** 系列（Part 1–3）— Eli Bendersky（2011）
  - **這篇說什麼**：用 C 從零寫一個迷你除錯器：ptrace 啟動行程、插入 `int3`、讀 DWARF
  - **讀哪裡**：Part 1（ptrace）與 Part 2（breakpoints）是本章「底層機制」的完整實作版
  - **為什麼值得讀**：讀完你會確實知道 GDB 每個指令背後的系統呼叫；程式碼可直接在 Linux 上編譯
- **[rr: lightweight recording & deterministic debugging](https://rr-project.org/)** — Mozilla rr 專案
  - **這篇說什麼**：rr 的使用方式與設計；首頁連結的 USENIX ATC 2017 論文〈Engineering Record and Replay for Deployability〉解釋它如何用效能計數器做到低成本錄製
  - **讀哪裡**：首頁的使用範例；論文的第 2–3 節
  - **為什麼值得讀**：本章「反向執行」的進階版，是處理難以重現 bug 的主力工具

### 書籍

- **《The Art of Debugging with GDB, DDD, and Eclipse》** — Norman Matloff & Peter Jay Salzman（No Starch Press，2008）
  - **這本書的定位**：以 GDB 為主的除錯方法論，強調「確認原則」（逐一確認你以為成立的事情）
  - **讀哪幾章**：第 1–4 章；書較舊，GDB 新功能（dprintf、record、Python API）沒有涵蓋，但除錯思路至今適用
- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** — Bryant & O'Hallaron（2015）
  - **這本書的定位**：第 3 章（機械碼）在 3.10.2 節介紹 GDB，並以它觀察機械碼層級的程式狀態
  - **讀哪幾章**：3.7（程序呼叫）與 3.10.2，搭配本章的 `info frame` 與 `disassemble /s` 練習

有了查規格與觀察程式的工具，最後一塊脈絡是歷史：C 那些奇怪的設計——陣列會變指標、宣告要由內往外讀、`&` 的優先順序比 `==` 低——為什麼會長成這樣？

→ [Ch 6 C 的起源與演化：從 BCPL、B 到 PDP-11](./06-history-of-c.md)
