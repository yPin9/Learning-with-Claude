# Ch 31 — goto 與流程控制：清理鏈、跳躍表與 computed goto

> **目標**：能判斷什麼時候 `goto` 讓程式更清楚、什麼時候該避免，並說出 C11/C23 與 MISRA C 對跳躍的確切限制；能寫出並審查 Linux 風格的資源清理鏈，知道 `cleanup` 屬性如何改寫它；能從組合語言讀懂 `switch` 的跳躍表，實測 computed goto 直譯器分派的效能；能解釋 Clifford's device、Duff's device 與 protothreads 式 coroutine 的運作原理。

> **對應原文**：[你所不知道的 C 語言：goto 和流程控制篇](https://hackmd.io/@sysprog/c-control-flow) 的「從 Considered Harmful 談起」「標準的限制與工程上的建議」「Linux 核心中的 goto」「其他專案的案例」「switch 背後的跳躍表」「computed goto 與直譯器分派」「進階題材：case 標籤的延伸用法」等節

> **環境**：WSL2 Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G，Zen 3），gcc 11.4、clang 14、zlib 1.2.11、OpenSSL 3.0.2。WSL 中沒有可用的 `perf`，效能以 `taskset` 固定處理器核、取 5 次最佳的牆上時間量測。Ubuntu 的 gcc 預設啟用 `-fcf-protection`，組合語言中的 `endbr64` 與 `notrack jmp` 來自這個選項。條款先寫 C11（N1570），括號內附 C23（N3220）：C23 新增 6.8.1 General，跳躍敘述由 6.8.6 移到 6.8.7。

## 為什麼需要這個？

`goto` 是 C 最受爭議的敘述，同時也是 Linux 核心最常見的錯誤處理手法。這兩件事不矛盾：爭議的關鍵從來不是 `goto` 這個關鍵字本身，而是兩個工程問題：

1. **控制流程是否容易推理**：讀者能不能從原始碼的位置判斷「此刻程式處在什麼狀態」？
2. **每一條離開函式的路徑，是否都履行了資源管理的責任**：鎖有沒有放、記憶體有沒有還、錯誤碼有沒有設？

C 沒有解構子、沒有 `finally`、沒有例外。當一個函式依序取得五項資源，任何一步都可能失敗時，「每條失敗路徑都要以相反順序歸還已取得的資源」這件事，必須由某種控制結構表達。巢狀 `if` 會把正常路徑推到螢幕右邊；旗標變數讓狀態散落各處；每個失敗點各自寫一份釋放程式碼，會讓 n 項資源產生 n(n-1)/2 個釋放呼叫。`goto` 清理鏈是這個問題在 C 裡最被廣泛採用的答案。

原文給了一條貫穿全章的判斷準則，值得背下來：

> 多條路徑必須執行相同的善後工作時，`goto` 能讓責任集中、順序明確。每一次跳躍都應能回答三個問題：此刻持有哪些資源，目標標籤假設哪些前提，離開後必須恢復哪些狀態。

這章同時處理 C 裡其他「跳到某處」的機制：`switch` 背後的跳躍表、GNU 的 computed goto、把 `case` 標籤塞進迴圈中段的 Duff's device 與 coroutine。它們共用同一個底層概念：**控制流程就是「下一步跳到哪個位址」**，高階的結構化敘述只是對這件事的約束。

## 先建立直覺

把一個函式想成一棟有很多出口的建築。進門後你陸續拿了鑰匙、借了雨傘、開了置物櫃。從任何出口離開前，都要把拿過的東西依相反順序還回去：

```
           ┌─────────┐   ok   ┌─────────┐   ok   ┌─────────┐   ok
  入口 ───►│ 拿鑰匙 A │──────►│ 借雨傘 B │──────►│ 開櫃子 C │──────► 成功離開
           └────┬────┘        └────┬────┘        └────┬────┘        （A、B、C 交給呼叫端）
                │fail              │fail              │fail
                ▼                  ▼                  ▼
            直接離開          ┌─────────┐ ◄──────┌─────────┐
           （沒拿東西）        │ 還鑰匙 A │  落入   │ 還雨傘 B │
                              └────┬────┘        └─────────┘
                                   ▼                 ▲ undo_b:
                              失敗離開               │
                              undo_a:            （標籤 = 「從這裡開始收拾」）
```

清理鏈的精髓：**標籤依取得順序的反向由上而下排列，失敗時跳到對應位置，然後一路落入（fall through）下方的標籤**。標籤的排列本身就是一份「取得進度表」。

`switch` 是另一種直覺：一個**多路的 goto**。編譯器常把它變成一張位址表，用 case 值當索引查表、間接跳躍過去。理解了這張表，computed goto 和 Duff's device 就只是「手動操作這張表」與「把表的入口放進迴圈中間」。

## 從 "Considered Harmful" 談起

Edsger Dijkstra 1968 年投書 CACM 的〈Go To Statement Considered Harmful〉論證：程式的正確性要靠讀者在腦中追蹤執行進度，循序、選擇與迴圈都能以「原始碼中的位置」加上「迴圈計數」描述進度，任意的 `goto` 則讓這組座標失去意義。標題不是 Dijkstra 訂的：他投稿的題目是〈A Case against the GO TO Statement〉（EWD215），依他在 EWD1308 的回憶，編輯 Niklaus Wirth 為了加快刊出把它改成讀者來函並換上這個標題。此後 "considered harmful" 成了電腦科學界的標題慣用句型。

歷史脈絡很重要。1968 年的 FORTRAN 與早期 BASIC 缺乏區塊結構，`goto` 是組織控制流程的主要工具，濫用的結果是 spaghetti code。1966 年 Böhm 與 Jacopini 已證明任何流程圖都能以循序、選擇、迴圈三種結構表達（代價是可能要引入額外的布林狀態），結構化程式設計遂有理論支撐。

但爭論沒有就此結束：

- **Knuth（1974）**〈Structured Programming with go to Statements〉持平地指出：跳出多層迴圈、錯誤處理、某些效能關鍵的迴圈，用 `goto` 反而更清楚。爭點應該是程式結構，不是單一敘述。
- **Rubin（1987）**〈"GOTO Considered Harmful" Considered Harmful〉稱 Dijkstra 的論證 "academic and unconvincing"，以「找出 N×N 矩陣中第一個全為零的列」為例主張 `goto` 版本更簡短。Dijkstra 在 EWD1009 回應：Rubin 的程式本身就有錯（一個版本在 N = 0 時出錯，另一個找不到位於最後一列的全零列），並用兩次 bounded linear search 寫出不需要 `goto` 的版本。

我們把這個例子兩種寫法都做出來，順便測試 Dijkstra 點出的兩個邊界：

```c
#include <stdio.h>

#define N 3

/* goto version: leave two loops at once */
static int first_zero_row_goto(int n, int m[][N])
{
    int i, j;
    for (i = 0; i < n; i++) {
        for (j = 0; j < N; j++)
            if (m[i][j] != 0)
                goto next_row;
        return i;              /* inner loop finished: the whole row is zero */
next_row:;
    }
    return -1;
}

/* structured version: bounded linear search applied twice (Dijkstra, EWD1009) */
static int first_zero_row_search(int n, int m[][N])
{
    int i = 0;
    while (i < n) {
        int j = 0;
        while (j < N && m[i][j] == 0)
            j++;
        if (j == N)
            return i;
        i++;
    }
    return -1;
}

int main(void)
{
    int a[][N] = { {1, 0, 0}, {0, 2, 0}, {0, 0, 0}, {0, 0, 0} };
    int b[][N] = { {1, 1, 1}, {0, 0, 0} };       /* zero row is the last row */
    int c[][N] = { {1, 2, 3} };
    printf("%d %d\n", first_zero_row_goto(4, a), first_zero_row_search(4, a));
    printf("%d %d\n", first_zero_row_goto(2, b), first_zero_row_search(2, b));
    printf("%d %d\n", first_zero_row_goto(1, c), first_zero_row_search(1, c));
    printf("%d %d\n", first_zero_row_goto(0, c), first_zero_row_search(0, c));
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra zerorow.c -o zerorow && ./zerorow
2 2
1 1
-1 -1
-1 -1
```

兩個版本在「全零列在最後」與「n = 0」這兩個邊界都正確。`next_row:;` 後面那個分號不是筆誤：C11 的標籤必須附屬於一個敘述，而 `}` 不是敘述，所以要補一個空敘述（後面「C 標準的限制」會解釋 C23 怎麼改）。這場爭論的教訓是：**論證 `goto` 的價值之前，程式本身得先正確**。

實證研究支持「實務上的 `goto` 大多是節制的」：Nagappan 等人在 ESEC/FSE 2015 蒐集 GitHub 上 11,627 個專案的 2,150,387 個 C 檔案，11.47% 含有 `goto`；人工檢視抽樣的 384 個函式，80.21% 用 `goto` 處理錯誤、40.36% 用它在函式尾端清理資源（一個函式可兼有多種用途）。

## 標準的限制與工程上的建議

跳躍的規則分兩層：C 標準的**限制條款**（constraint，違反時編譯器必須給出診斷）與**工程上的建議**（違反時程式仍合法，但容易出錯）。

### C 標準的限制

N1570 6.8.6（C23 6.8.7.1）列出四種跳躍敘述：`goto identifier;`、`continue;`、`break;`、`return expression_opt;`。`goto` 的規則分散在幾處：

| 規則 | 條款 | 白話 |
|---|---|---|
| 標籤具有**函式作用域** | 6.2.1 §3 | 整個函式內都看得到，同函式不得重名；`goto` 不能離開函式（跨函式要 `setjmp`/`longjmp`，見 [Ch 12](./12-setjmp-and-coroutines.md)） |
| 可前跳、可後跳、可跳出區塊、可跳入區塊 | 6.8.6.1 §1（C23 6.8.7.2 §1） | 限制很少 |
| **不得**從外面跳入 VLA 等 variably modified 型別的作用域 | 同上 | 唯一的硬限制 |
| 跳過 automatic 變數的初始化**不違反限制** | 6.2.4 §6、6.8 §3 | 但被跳過的變數是未定值 |
| C11：標籤後必須是敘述 | 6.8.1 | 不能放在宣告前或 `}` 前 |
| C23：標籤可在宣告前、可在 `}` 前 | N3220 6.8.2 §4、6.8.3 §2 | "A label shall be translated as if it were followed by a null statement" |

跳入 VLA 作用域的實測：

```c
void f(int n)
{
    goto inside;          /* jumps into the scope of a VLA: constraint violation */
    {
        double a[n];
inside:
        a[0] = 1.0;
        (void) a;
    }
}
```

```
$ gcc -c vlajump.c
vlajump.c:3:5: error: jump into scope of identifier with variably modified type
vlajump.c:6:1: note: label ‘inside’ defined here
vlajump.c:5:16: note: ‘a’ declared here
$ clang -c vlajump.c
vlajump.c:3:5: error: cannot jump from this goto statement to its label
vlajump.c:5:16: note: jump bypasses initialization of variable length array
```

理由很實際：VLA 的大小在執行到宣告時才計算、空間在那時才配置，跳過宣告就沒有空間可用。

標籤位置的規則在 C11 與 C23 之間改變了，編譯器的反應也不一致：

```c
int main(int argc, char *argv[])
{
    (void) argv;
    if (argc > 2)
        goto out;
    if (argc > 1)
        goto done;
out:
    int status = argc;          /* label before a declaration */
    printf("status = %d\n", status);
done:
}                               /* label right before } */
```

```
$ gcc -std=c11 -c label.c                # gcc 11：不加 -pedantic 時沒有任何訊息
$ gcc -std=c11 -pedantic -c label.c
label.c:10:5: warning: a label can only be part of a statement and a declaration is not a statement [-Wpedantic]
label.c:12:1: warning: label at end of compound statement [-Wpedantic]
$ gcc -std=c2x -pedantic -c label.c      # 不再警告
$ clang -std=c2x -pedantic -c label.c    # clang 14：連 C2x 模式都是錯誤
label.c:10:5: error: expected expression
label.c:11:29: error: use of undeclared identifier 'status'
label.c:13:1: error: expected statement
```

clang 14 還沒實作這項 C23 變更；原文指出 Clang 18 與 20 在 `-std=c11` 下以 `-Wc23-extensions` 警告、在 C23 模式下接受。可攜的寫法仍是在標籤後補一個空敘述 `;`，或把宣告包進大括號。

### 工程上的建議：不要跳過初始化

Jens Gustedt 的《Modern C》把 `goto` 歸納成三條 takeaway，前兩條是語言規則（標籤在整個函式可見、只能跳到同函式的標籤），第三條 "goto should not jump over variable initializations" 是建議。下面這支程式在 C 中**可以編譯**：

```c
#include <stdio.h>

int main(int argc, char *argv[])
{
    (void) argv;
    if (argc > 1)
        goto print;
    int x = 42;
print:
    printf("x = %d\n", x);
    return 0;
}
```

```
$ gcc -O0 -Wall -Wextra skip.c -o s0        # 沒有任何警告
$ ./s0 ; ./s0 a ; ./s0 a
x = 42
x = 23846
x = 25707
$ gcc -O2 -Wall -Wextra skip.c -o s2 && ./s2 a
x = 42
```

帶引數時 `goto` 跳過 `x` 的初始化，`-O0` 印出堆疊殘值（每次執行不同），`-O2` 卻印出 42：最佳化器把 `x` 當常數傳播了。**同一份程式，最佳化等級不同、行為不同**，這是未定義行為的標誌（依 6.2.4 §6，跳過宣告後 `x` 是未定值；附錄 J.2 把使用未定值的 automatic 物件列為未定義行為）。診斷也不一致：

```
$ gcc -Wjump-misses-init -c skip.c          # gcc 要另外指定，-Wall -Wextra 不含
skip.c:7:9: warning: jump skips variable initialization [-Wjump-misses-init]
$ clang -Wall skip.c                        # clang 從另一個角度抓到
skip.c:6:9: warning: variable 'x' is used uninitialized whenever 'if' condition is true [-Wsometimes-uninitialized]
$ g++ -x c++ -c skip.c                      # C++ 直接拒絕
skip.c:9:1: error: jump to label ‘print’
skip.c:8:9: note:   crosses initialization of ‘int x’
$ clang++ -x c++ -c skip.c
skip.c:7:9: error: cannot jump from this goto statement to its label
skip.c:8:9: note: jump bypasses variable initialization
```

**C 和 C++ 在這裡的規則不同**：C++ 把跳過帶初始化的宣告列為錯誤，C 只說值未定。從 C++ 背景來的人容易以為 C 編譯器也會擋。`switch` 也能跳過初始化：N1570 6.8.4.2 §7 的 EXAMPLE 在 `switch` 主體開頭寫 `int i = 4; f(i);`，控制直接跳到某個 `case`，`i` 存在卻從未初始化。這在後面的 coroutine 巨集裡會再出現。

未初始化讀取的完整討論見 [Ch 34 — 未定義行為](./34-undefined-behavior.md)。

### 工程規範：MISRA C

MISRA C 原為汽車產業制定，現廣泛用於航太、鐵路、醫療等安全攸關系統。它對 `goto` 的態度**隨版本改變**，引用時必須標明版本與規則等級，不能一句「MISRA 禁止 goto」帶過：

| 版本 | `goto` | `continue` |
|---|---|---|
| MISRA-C:2004 | Rule 14.4 "shall not be used"（Required） | Rule 14.5 禁止（Required） |
| MISRA C:2012 / 2023 / 2025 | Rule 15.1 "should not be used"（**Advisory**）；Rule 15.2 只能跳往**同函式中其後宣告**的標籤（Required）；Rule 15.3 標籤必須在 `goto` 所在區塊或其外層區塊（Required） | 不再限制 |

依 MISRA C:2012，清理鏈（往後跳、跳到外層）符合 15.2 與 15.3；N1570 6.8.6.1 的 EXAMPLE 1（跳進內層區塊的 `first_time` 標籤）雖然往後跳，卻違反 15.3。`switch` 的標籤另由 Rule 16.2（Required）約束：`case` 只能出現在 `switch` 主體的最外層，後面的 Clifford's device、Duff's device、coroutine 巨集**全部違反**。

為什麼要這樣限制？只准往後跳、不准跳入區塊的 `goto` 不會產生多入口的迴圈，在其他控制結構皆為單一入口時，控制流程圖仍是**可歸約的**（reducible），資料流分析與最壞情況執行時間（WCET）估算這類靜態分析可以照常進行。換言之，限制 `goto` 的收益主要由靜態分析工具兌現；專案若根本不做靜態分析，機械地用旗標變數改寫錯誤處理只會換來更難讀的程式碼。

## Linux 核心中的 goto

依原文對 Linux v7.2 的統計（`git grep -c -E '\bgoto\s+[A-Za-z_]\w*\s*;'`），`.c` 與 `.h` 檔案中含 `goto label;` 的行共 206,428 行、分布在 17,424 個檔案（v4.0 時為 122,314 行）。最常見的目標標籤是 `out`（42,183 處）、`err`、`error`、`done`、`fail`；其次是重試：`goto retry` 1,045 處、`goto again` 846 處。

### 資源清理鏈

Linux 的 [coding style 文件](https://www.kernel.org/doc/html/latest/process/coding-style.html#centralized-exiting-of-functions) 第 7 節 "Centralized exiting of functions" 明文採用此手法，並劃出範圍："The goto statement comes in handy when a function exits from multiple locations and some common work such as cleanup has to be done. If there is no cleanup needed then just return directly." 標籤名稱應說明該處做什麼（`out_free_buffer:`），而非 `err1:`、`err2:` 這類增刪出口時要重新編號的名字。

下面的使用者空間程式抽出這個結構，並和不用 `goto` 的寫法對照。注意**所有權**：失敗時函式必須歸還已取得的資源；成功時三項資源保留給呼叫端，所以成功路徑直接 `return`，不經過清理標籤：

```c
#include <stdbool.h>
#include <stdio.h>

static int fail_at; /* 0: all succeed; k: step k fails */
static bool step(int k, const char *name)
{
    printf("  do%s\n", name);
    return fail_at != k;
}
static bool doA(void) { return step(1, "A"); }
static bool doB(void) { return step(2, "B"); }
static bool doC(void) { return step(3, "C"); }
static void undoA(void) { puts("  undoA"); }
static void undoB(void) { puts("  undoB"); }

int foo(void)
{
    if (!doA())
        return -1; /* nothing acquired yet: no cleanup needed */
    if (!doB())
        goto undo_a;
    if (!doC())
        goto undo_b;

    /* success: A, B and C remain acquired; the caller now owns them */
    return 0;

undo_b:
    undoB();
undo_a:
    undoA();
    return -1;
}

int foo_nogoto(void)
{
    if (!doA())
        return -1;
    if (!doB()) {
        undoA();
        return -1;
    }
    if (!doC()) {
        undoB();
        undoA();
        return -1;
    }
    return 0;
}

int main(void)
{
    for (fail_at = 0; fail_at <= 3; fail_at++) {
        printf("fail_at = %d\n", fail_at);
        int r1 = foo();
        int r2 = foo_nogoto();
        printf("  -> %d %d\n", r1, r2);
    }
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra unwind.c -o unwind && ./unwind
fail_at = 0
  doA
  doB
  doC
  doA
  doB
  doC
  -> 0 0
fail_at = 1
  doA
  doA
  -> -1 -1
fail_at = 2
  doA
  doB
  undoA
  doA
  doB
  undoA
  -> -1 -1
fail_at = 3
  doA
  doB
  doC
  undoB
  undoA
  doA
  doB
  doC
  undoB
  undoA
  -> -1 -1
```

`main` 讓每一步依序失敗一次，正是風格文件最後一句的建議："Ideally you should simulate errors to test all exit paths." 兩個版本行為相同；差別在擴充性：n 項資源時，`foo_nogoto` 第 k 項失敗要重複寫 k-1 個釋放呼叫，總共 Σ(k-1) = n(n-1)/2 個；清理鏈只對前 n-1 項各寫一次。這是**原始碼維護量**的差異，不代表執行速度的差異。

清理鏈不是 RAII。C++ 的 RAII 把釋放綁定到物件生命週期，離開作用域時由解構子依建構的相反順序自動執行；`goto` 清理鏈仍仰賴程式設計者明確安排每一項釋放，只是把安排集中在一處，**手工寫出 RAII 會自動產生的釋放順序**。

### 標籤是一組狀態前提

集中出口不自動保證安全。風格文件以 "one err bugs" 為例：

```c
err:
    kfree(foo->bar);
    kfree(foo);
    return ret;
```

若某條路徑因 `foo` 配置失敗而抵達 `err`，`foo->bar` 就是解參考空指標。修正方式是依取得進度拆成兩個標籤：

```c
err_free_bar:
    kfree(foo->bar);
err_free_foo:
    kfree(foo);
    return ret;
```

**每個清理標籤都對應一組狀態前提**：它後續每個釋放動作所需的指標，都已具有有效值或是該釋放函式允許的 `NULL`。審查時對每個 `goto` 檢查：跳躍當下是否滿足目標標籤的前提？

### 規模最大的實例：`copy_process()`

建立行程的 `kernel/fork.c:copy_process()` 在 Linux v7.2 有 22 個標籤與 30 個 `goto`（原文統計），中段依序複製排程、效能計數、稽核、安全模組、semaphore undo、檔案描述子表、檔案系統資訊、signal、定址空間、namespace、I/O context 與暫存器狀態：

```c
    retval = copy_files(clone_flags, p, args->no_files);
    if (retval)
        goto bad_fork_cleanup_semundo;
    ...
    retval = copy_mm(clone_flags, p);
    if (retval)
        goto bad_fork_cleanup_signal;
    retval = copy_namespaces(clone_flags, p);
    if (retval)
        goto bad_fork_cleanup_mm;
    retval = copy_io(clone_flags, p);
    if (retval)
        goto bad_fork_cleanup_namespaces;
```

尾端是完全鏡像的階梯：

```c
bad_fork_cleanup_io:
    if (p->io_context)
        exit_io_context(p);
bad_fork_cleanup_namespaces:
    exit_nsproxy_namespaces(p);
bad_fork_cleanup_mm:
    if (p->mm) {
        mm_clear_owner(p->mm, p);
        mmput(p->mm);
    }
bad_fork_cleanup_signal:
    if (!(clone_flags & CLONE_THREAD))
        free_signal_struct(p->signal);
    ...
```

標籤依「失敗時要從哪一步開始收拾」命名：`copy_mm()` 失敗跳到 `bad_fork_cleanup_signal`，因為最後一項成功取得的是 signal 狀態。部分步驟以旗標決定是否真的配置（`CLONE_THREAD` 共用 signal 狀態），對應的清理就在標籤內以同一個旗標判斷。中段 13 個步驟若照 `foo_nogoto` 的方式展開，失敗路徑上的釋放呼叫將達 13 × 12 / 2 = 78 個。SEI CERT C 的 MEM12-C 以 Linux v2.6.29 的 `copy_process()` 作為合規範例（當時 17 個標籤）。

原文在真實核心上用 bpftrace 觀察過這條鏈：一般使用者以 `CLONE_NEWPID` 呼叫 `clone` 時（需要 `CAP_SYS_ADMIN`），`copy_namespaces()` 回傳 `-EPERM`，接著依序看到 `mmput`、`__cleanup_sighand`、`exit_fs`、`exit_files`、`exit_sem`、`security_task_free`、`perf_event_free_task`、`exit_creds`，與 `bad_fork_cleanup_mm` 以下的標籤完全對應。

> **本段未實測**：需要 root 權限執行 bpftrace 並以 kprobe 掛載核心函式，WSL2 核心也未必開啟對應選項。在自己的 Linux VM 上可依原文的 `unwind.bt` 腳本重現。

### 清理鏈的兩類典型缺陷

清理鏈把正確性押在兩件事上：**跳到正確的標籤**，以及**跳之前設好傳回值**。後者的錯誤多到在核心中有固定的修正標題，原文統計截至 v7.2 標題或說明含 "missing error code" 的提交有 169 個。典型的一行修正（v4.13 "tracing: Missing error code in tracer_alloc_buffers()"）：

```diff
     if (ret < 0)
         goto out_free_cpumask;
     /* Used for event triggers */
+    ret = -ENOMEM;
     temp_buffer = ring_buffer_alloc(PAGE_SIZE, RB_FL_OVERWRITE);
     if (!temp_buffer)
         goto out_rm_hp_state;
```

前一個檢查通過後 `ret` 是 0，`ring_buffer_alloc()` 失敗時跳到清理標籤，函式卻回報成功。靜態分析工具 Smatch 對這類路徑發出 `warn: missing error code 'ret'`。對策：在每個 `goto` 之前就近設定錯誤碼（如 `copy_process()` 的 `retval = copy_files(...)` 緊接檢查）、標籤名稱描述它要做的釋放、用工具檢查、並像 `fail_at` 或核心的 fault injection 框架那樣讓每一條錯誤路徑至少實際執行一次。

### 其他用途：重試迴圈與 `asm goto`

**重試**：x86 page fault 處理函式 `do_user_addr_fault()` 先在 RCU 保護下只鎖單一 VMA（v6.4 的 per-VMA lock），找不到就 `goto lock_mmap` 改走取得 `mmap_lock` 的慢路徑；慢路徑的 `handle_mm_fault()` 可能為了等 I/O 釋放 lock 並回傳 `VM_FAULT_RETRY`，此時 `goto retry`。page cache 的 `find_get_entry()` 有兩個向後跳躍的入口：`retry` 用於走訪狀態仍有效、直接再查一次；`reset` 用於狀態已不可信、要先 `xas_reset()`。兩個標籤以名稱區分兩種重試的前提，改寫成 `for (;;)` 時就只能從程式碼位置推斷。

**`asm goto`**（GCC 4.5 起的擴充）讓內嵌組合語言的分支目標是 C 標籤。Linux 的 static key（`arch_static_branch()`）在原地放一道 NOP，並在 `__jump_table` 區段記錄這個位置；啟用 key 時核心把 NOP **改寫**成跳到 `l_yes` 標籤的 `jmp`。結果是功能關閉時每個呼叫點只經過一道 NOP，連旗標都不用讀；tracepoint 與許多除錯開關靠它做到「關閉時幾乎不付代價」。GCC 11 起允許 `asm goto` 帶輸出運算元，讓 `unsafe_get_user()` 在 page fault 時直接跳到錯誤標籤，正常路徑上沒有任何錯誤檢查指令。

> **本段未實測**：static key 與 `unsafe_get_user` 只存在於核心建置環境。

## 從 goto 到 cleanup.h

Linux v6.5 由 Peter Zijlstra 引入 `include/linux/cleanup.h`，用 GCC 與 Clang 的 `__attribute__((cleanup(func)))` 把釋放動作綁定到變數的作用域：`__free(kfree)` 宣告的指標離開作用域時自動 `kfree()`，`guard(mutex)(&lock)` 在區塊結束時自動釋放 lock，錯誤路徑因此可以直接 `return`。說明文件〈Scope-based Cleanup Helpers〉開頭就說："The “goto error” pattern is notorious for introducing subtle resource leaks."

`cleanup` 屬性的語意（GCC 手冊「Common Variable Attributes」）：變數離開作用域時，以**指向該變數的指標**為引數呼叫指定函式；同一作用域的多個 cleanup 變數依**宣告的相反順序**執行。下面是一個縮小版：

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* a tiny version of linux/cleanup.h */
static inline void free_ptr(void *p) { void *q = *(void **) p; if (q) printf("  auto free(%p)\n", q); free(q); }
#define __free_heap __attribute__((cleanup(free_ptr)))

typedef struct { const char *name; int locked; } Lock;
static void lock_acquire(Lock *l) { l->locked = 1; printf("  lock %s\n", l->name); }
static void lock_release(Lock **lp) { (*lp)->locked = 0; printf("  unlock %s\n", (*lp)->name); }
#define guard(l) Lock *__guard_##l __attribute__((cleanup(lock_release))) = (lock_acquire(&(l)), &(l))

static Lock table_lock = { "table", 0 };

static char *dup_upper(const char *s, int fail_at)
{
    guard(table_lock);                               /* released on every return */
    char *buf __free_heap = malloc(strlen(s) + 1);   /* freed on every return ... */
    if (!buf)
        return NULL;
    if (fail_at == 1)
        return NULL;                                 /* early return: no goto needed */
    for (size_t i = 0; s[i]; i++)
        buf[i] = (char) (s[i] >= 'a' && s[i] <= 'z' ? s[i] - 32 : s[i]);
    buf[strlen(s)] = '\0';
    if (fail_at == 2)
        return NULL;
    char *ret = buf;                                 /* ... unless ownership moves out */
    buf = NULL;                                      /* = no_free_ptr(buf) */
    return ret;
}

int main(void)
{
    for (int f = 0; f <= 2; f++) {
        printf("fail_at=%d\n", f);
        char *r = dup_upper("goto", f);
        printf("  -> %s, lock still held: %d\n", r ? r : "(null)", table_lock.locked);
        free(r);
    }
    return 0;
}
```

```
$ gcc -std=gnu11 -Wall -Wextra -fsanitize=address cleanup.c -o cleanup && ./cleanup
fail_at=0
  lock table
  unlock table
  -> GOTO, lock still held: 0
fail_at=1
  lock table
  auto free(0x602000000010)
  unlock table
  -> (null), lock still held: 0
fail_at=2
  lock table
  auto free(0x602000000010)
  unlock table
  -> (null), lock still held: 0
```

（位址每次執行不同；ASan 無洩漏報告。）三個重點：

- 每個提早返回都自動釋放 `buf` 與 lock，不需要任何標籤。
- 順序是 `buf` 先釋放、lock 後釋放，因為 `buf` 較晚宣告。**釋放順序由宣告順序決定**：`goto` 階梯以標籤排列記錄順序，cleanup.h 以宣告排列記錄。核心文件的反例是先宣告 `obj __free(remove_free) = NULL`、後取得 lock，結果 `remove_free()` 在 lock 已釋放後才執行；所以文件建議宣告與指定寫在一起，不要集中在函式開頭。
- 成功時要把資源交給呼叫端，就先把變數設成 `NULL`，核心提供 `no_free_ptr(p)` 與 `return_ptr(p)` 做這件事，對應清理鏈中 `foo` 成功路徑「不經過標籤」的所有權契約。

### 不要混用 `goto` 與 cleanup 變數

核心文件要求同一函式內不可混用。理由之一：`goto` 可以跳過 cleanup 變數的初始化，兩個編譯器的反應截然不同：

```c
static void free_ptr(void *p) { printf("  cleanup sees %p\n", *(void **) p); }
int main(int argc, char *argv[])
{
    (void) argv;
    if (argc > 1)
        goto out;                 /* jumps over the initialization below */
    char *buf __attribute__((cleanup(free_ptr))) = malloc(16);
    puts("  using buf");
out:
    return 0;
}
```

```
$ gcc -Wall -Wextra cleanup_goto.c -o cg       # 沒有警告
$ ./cg
  using buf
  cleanup sees 0x6020034942a0
$ ./cg skip
  cleanup sees 0x7ffef7d31d19                  ← 對未初始化的堆疊殘值呼叫清理函式
$ clang -Wall cleanup_goto.c
cleanup_goto.c:8:9: error: cannot jump from this goto statement to its label
cleanup_goto.c:9:11: note: jump bypasses initialization of variable with __attribute__((cleanup))
```

gcc 11 照常編譯，並在 `out:` 離開作用域時對一個從未初始化的指標呼叫清理函式；如果清理函式是 `free`，這就是 free 一個任意位址。clang 拒絕編譯。原文也記錄了一個反向修正：v7.0 的 "firmware: cs_dsp: Don't use __free() in cs_dsp_load()..." 把混用 `goto` 與 `__free()` 的函式改回在標籤呼叫 `kfree()`，提交說明指出 `__free()` 引入的清理缺陷難以察覺，因為出錯的程式碼依一般 C 的慣例讀來完全正確。依原文統計，cleanup.h 推出後 `guard()` 由 v6.5 的 0 處增加到 v7.2 的 8,422 處，但同期 `goto label;` 仍持續增加，兩種寫法會長期並存。`cleanup` 屬性與 WG14 的 `defer` 提案在 [Ch 32](./32-tricks-syntax-and-resources.md) 有更完整的討論。

## 其他專案的案例

每個標籤都有固定的**入口狀態**與**出口責任**。以下用兩個能實測的案例加上三個原始碼摘錄，展示「善後」不只是釋放記憶體。

### OpenSSL：暫存資源與輸出所有權

OpenSSL Wiki 的 EVP 簽章範例依序建立 digest context、初始化簽章、輸入訊息、查詢長度、配置緩衝區、產生簽章，任一步失敗都跳到 `err`。它涉及**兩種生命週期**：digest context 是暫存資源，成功失敗都要釋放；簽章緩衝區是輸出，成功時交給呼叫端，失敗時才回收。加上 `fail_at` 讓每步依序失敗：

```c
#include <openssl/evp.h>
#include <stdio.h>
#include <string.h>

static int fail_at; /* 0: no injected failure; k: force step k to fail */
#define STEP(k, expr) (fail_at != (k) && (expr))

/* Returns 1 and hands *sig (free with OPENSSL_free) to the caller on success. */
static int sign_msg(EVP_PKEY *key, const char *msg, unsigned char **sig,
                    size_t *slen)
{
    EVP_MD_CTX *mdctx = NULL;
    int ret = 0;

    *sig = NULL;
    if (!STEP(1, (mdctx = EVP_MD_CTX_new()) != NULL))
        goto err;
    if (!STEP(2, EVP_DigestSignInit(mdctx, NULL, EVP_sha256(), NULL, key) == 1))
        goto err;
    if (!STEP(3, EVP_DigestSignUpdate(mdctx, msg, strlen(msg)) == 1))
        goto err;
    if (!STEP(4, EVP_DigestSignFinal(mdctx, NULL, slen) == 1))
        goto err;
    if (!STEP(5, (*sig = OPENSSL_malloc(*slen)) != NULL))
        goto err;
    if (!STEP(6, EVP_DigestSignFinal(mdctx, *sig, slen) == 1))
        goto err;
    ret = 1; /* success: *sig now belongs to the caller */

err:
    if (!ret) { /* failure: reclaim the output buffer */
        OPENSSL_free(*sig);
        *sig = NULL;
    }
    EVP_MD_CTX_free(mdctx); /* temporary: released on every path */
    return ret;
}

int main(void)
{
    EVP_PKEY *ec = EVP_PKEY_Q_keygen(NULL, NULL, "EC", "P-256");
    EVP_PKEY *x25519 = EVP_PKEY_Q_keygen(NULL, NULL, "X25519");
    if (!ec || !x25519) {
        EVP_PKEY_free(ec);
        EVP_PKEY_free(x25519);
        return 1;
    }

    for (fail_at = 0; fail_at <= 6; fail_at++) {
        unsigned char *sig;
        size_t slen = 0;
        int ok = sign_msg(ec, "hello", &sig, &slen);
        printf("fail_at = %d: ok = %d, sig %s\n", fail_at, ok,
               sig ? "owned by caller" : "NULL");
        OPENSSL_free(sig);
    }

    unsigned char *sig;
    size_t slen = 0;
    fail_at = 0;
    printf("X25519 key: ok = %d\n", sign_msg(x25519, "hello", &sig, &slen));

    EVP_PKEY_free(ec);
    EVP_PKEY_free(x25519);
    return 0;
}
```

```
$ gcc -std=c11 -O1 -g -fsanitize=address,undefined -Wall -Wextra sign.c -o sign -lcrypto && ./sign
fail_at = 0: ok = 1, sig owned by caller
fail_at = 1: ok = 0, sig NULL
fail_at = 2: ok = 0, sig NULL
fail_at = 3: ok = 0, sig NULL
fail_at = 4: ok = 0, sig NULL
fail_at = 5: ok = 0, sig NULL
fail_at = 6: ok = 0, sig NULL
X25519 key: ok = 0
```

沒有任何 ASan/UBSan 報告。最後一行是自然發生的失敗：X25519 是金鑰交換用的金鑰，不能簽章。`STEP(k, expr)` 依賴 `&&` 的短路求值（N1570 6.5.13 §4）：`fail_at == k` 時對應的 OpenSSL 呼叫根本不執行。`*sig` 與 `mdctx` 都在第一個 `goto` 之前初始化為 `NULL`，`err` 才能對任何進度安全呼叫 `OPENSSL_free()` 與 `EVP_MD_CTX_free()`（兩者都接受 `NULL`）。把 `err` 中的 `OPENSSL_free(*sig)` 拿掉（`-DLEAK`）：

```
$ gcc -std=c11 -O1 -g -fsanitize=address -DLEAK sign.c -o signleak -lcrypto && ./signleak
Direct leak of 72 byte(s) in 1 object(s) allocated from:
SUMMARY: AddressSanitizer: 72 byte(s) leaked in 1 allocation(s).
```

72 位元組是 P-256 ECDSA 簽章以 DER 編碼的最大長度，正是 `fail_at = 6` 時已配置卻遺失的緩衝區。

### zlib：離開前保存進度

zlib 的 `inflate()` 可以分多次呼叫：呼叫端每次只給一段輸入與一塊輸出緩衝區，其中一者用盡就返回，下次從中斷處繼續。它的主體是 `for (;;) switch (state->mode)` 狀態機，進入時以 `LOAD()` 把 stream 的位置複製到區域變數，**所有需要暫停的地方都 `goto inf_leave`**：

```c
#define PULLBYTE() \
    do { \
        if (have == 0) goto inf_leave; \
        have--; \
        hold += (unsigned long)(*next++) << bits; \
        bits += 8; \
    } while (0)
...
  inf_leave:
    RESTORE();
    ...
```

這裡的善後不是釋放資源，而是**提交進度**：`RESTORE()` 把區域變數寫回 stream。暫停點深藏在 `switch` 與內層迴圈中，`break` 只能結束最內層（6.8.6.3 §2），直接 `return` 又會漏掉狀態同步。下面的程式逼 `inflate()` 在任意位置中斷：每次只給 1 位元組輸入、至多 16 位元組輸出：

```c
#include <stdio.h>
#include <string.h>
#include <zlib.h>

int main(void)
{
    unsigned char text[4096], packed[4096], out[4096];
    for (size_t i = 0; i < sizeof(text); i++)
        text[i] = "goto considered harmful? "[i % 25];
    uLongf plen = sizeof(packed);
    if (compress2(packed, &plen, text, sizeof(text), 9) != Z_OK)
        return 1;

    z_stream s;
    memset(&s, 0, sizeof(s));
    if (inflateInit(&s) != Z_OK)
        return 1;

    /* Hand inflate() one input byte and at most 16 output bytes per call. */
    int calls = 0, ret;
    do {
        s.next_in = packed + s.total_in;
        s.avail_in = s.total_in < plen ? 1 : 0;
        s.next_out = out + s.total_out;
        s.avail_out = sizeof(out) - s.total_out < 16 ? sizeof(out) - s.total_out : 16;
        ret = inflate(&s, Z_NO_FLUSH);
        calls++;
    } while (ret == Z_OK || ret == Z_BUF_ERROR);
    inflateEnd(&s);
    printf("zlib %s: %lu -> %lu bytes in %d calls, %s, output %s\n",
           zlibVersion(), (unsigned long) plen, (unsigned long) s.total_out,
           calls, ret == Z_STREAM_END ? "Z_STREAM_END" : "error",
           s.total_out == sizeof(text) && !memcmp(out, text, sizeof(text))
               ? "matches" : "differs");
    return ret != Z_STREAM_END;
}
```

```
$ gcc -std=c11 -Wall -Wextra inflate1.c -o inflate1 -lz && ./inflate1
zlib 1.2.11: 59 -> 4096 bytes in 299 calls, Z_STREAM_END, output matches
```

（clang 14 結果相同；原文以 zlib 1.3 得到同樣的 299 次。）59 位元組的壓縮資料分 299 次才解完，光是 4096 位元組輸出就至少要 256 次（4096 / 16）。這 299 次中斷都經過 `inf_leave`，由同一段程式碼保存進度。中斷點可能落在 Huffman 碼中間、長度與距離之間或區塊標頭內；若每個暫停點各自保存狀態，任何一處遺漏都會讓下一次從錯誤狀態繼續。`inflate()` 以 `state->mode` 記錄續行位置、以 `switch` 跳回中斷處，正是後面要講的 stackless coroutine。

### SQLite、Git、Bison：原始碼摘錄

- **SQLite `btreeBeginTrans()`**：開頭 `sqlite3BtreeEnter(p)` 取得 mutex，之後四處 `goto trans_begun`，其中**一處是成功捷徑**（已在合適交易中），三處是失敗。`trans_begun` 同時接收成功與失敗：成功時補讀 schema 版本、開 savepoint，一律 `sqlite3BtreeLeave(p)`。把成功捷徑改成 `return SQLITE_OK`，就會持有 mutex 離開函式。
- **Git `pick_one_utf8_char()`**：每個 UTF-8 長度分支檢查續接位元組、overlong、surrogate、超出 U+10FFFF，任一不符就 `goto invalid`；`invalid:` 位在最後一個 `else` 區塊**內**，其他分支照樣跳得進去，示範標籤的函式作用域。所有失敗共用 `*start = NULL; return 0;` 這份契約。
- **GNU Bison 的 `yacc.c` 範本**：`#define YYACCEPT goto yyacceptlab`、`#define YYABORT goto yyabortlab`。這些巨集出現在使用者的語意動作中，而動作位在 `switch (yyn)` 之內，`break` 只能離開 `switch`、`return` 會跳過 `yyreturnlab` 對 lookahead 與堆疊語意值的 `yydestruct`；`goto` 一步越過 `switch` 與剖析迴圈，保證三種終止都經過同一段釋放。

| 案例 | 標籤 | 抵達時成立的狀態 | 離開前完成的責任 |
|---|---|---|---|
| SQLite | `trans_begun` | 持有 mutex，`rc` 記錄成敗 | 成功時補做 schema 版本與 savepoint，一律釋放 mutex |
| zlib | `inf_leave` | 進度只存在區域變數中 | 寫回 stream、更新視窗與計數 |
| Git | `invalid` | 輸入不是合法 UTF-8 | 以 `*start = NULL` 回報失敗 |
| Bison | `yyreturnlab` | `yyresult` 已設定 | 釋放 lookahead 與堆疊上的語意值 |
| OpenSSL | `err` | `ret` 表示成敗，未配置的指標為 `NULL` | 一律釋放 context，失敗時回收輸出 |
| Linux `copy_process` | `bad_fork_cleanup_*` | 已取得到該步為止的資源 | 由此往下依序歸還 |

同一標籤可以依 `rc`/`ret` 決定責任（SQLite、OpenSSL）；但若某些路徑抵達時無法滿足前提，就要依進度拆成多個標籤（Linux 的 one-err bug 修正、CPython `TaskObj_finalize()` 以 `done` 與 `finally` 區分是否已保存例外）。

## switch 背後的跳躍表

`switch` 也是跳往標籤的敘述。N1570 6.8.4.2 §5 只規定控制運算式做整數提升、各 `case` 常數轉成同型別後比較、相符就跳到該標籤，**不規定怎麼找出目標**。case 值密集且分支夠多時，編譯器常用跳躍表（jump table）：不論分支數多少，選定目標只需一次範圍檢查加一次間接跳躍。CS:APP 3/e Section 3.6.8 的例子：

```c
void switch_eg(long x, long n, long *dest)
{
    long val = x;
    switch (n) {
    case 100:
        val *= 13;
        break;
    case 102:
        val += 10;
        /* Fall through */
    case 103:
        val += 11;
        break;
    case 104:
    case 106:
        val *= val;
        break;
    default:
        val = 0;
    }
    *dest = val;
}
```

缺 101 與 105；104 與 106 共用；102 沒有 `break`，落入 103。gcc 11 `-O2` 的輸出：

```
switch_eg:
	endbr64
	subq	$100, %rsi              # index = n - 100
	cmpq	$6, %rsi
	ja	.L8                     # 無號比較：index > 6 → default
	leaq	.L4(%rip), %rcx         # rcx = 表格位址
	movslq	(%rcx,%rsi,4), %rax     # rax = 表格[index]（32 位元相對位移，符號延伸）
	addq	%rcx, %rax              # 位移 + 表格位址 = 目標
	notrack jmp	*%rax
	.section	.rodata
.L4:
	.long	.L7-.L4                 # 100
	.long	.L8-.L4                 # 101 → default
	.long	.L6-.L4                 # 102
	.long	.L5-.L4                 # 103
	.long	.L3-.L4                 # 104
	.long	.L8-.L4                 # 105 → default
	.long	.L3-.L4                 # 106
.L3:
	imulq	%rdi, %rdi
	movq	%rdi, (%rdx)
	ret
.L6:
	addq	$10, %rdi
.L5:                                    # 102 落入 103：兩段緊鄰，不需要任何跳躍
	addq	$11, %rdi
	movq	%rdi, (%rdx)
	ret
.L7:
	leaq	(%rdi,%rdi,2), %rax     # 3x
	leaq	(%rdi,%rax,4), %rdi     # x + 4*3x = 13x
	movq	%rdi, (%rdx)
	ret
.L8:
	xorl	%edi, %edi
	movq	%rdi, (%rdx)
	ret
```

（這份輸出與原文以 GCC 14 得到的結果逐行相同。）逐項解讀：

- **一次比較排除兩側**：`n - 100` 以無號數看待，`n < 100` 時回繞成極大值，`ja`（無號大於）一次就排除小於 100 與大於 106 兩種情況。這個「無號減法平移到原點」的技巧見 [Ch 32](./32-tricks-syntax-and-resources.md)。
- **表格存相對位移而非絕對位址**：Ubuntu 預設產生 PIE，程式碼的載入位址執行時才決定；存 32 位元相對位移不需要載入時重定位，表格也縮小一半，代價是多一道 `addq`。CS:APP 書中（非 PIE）是 64 位元絕對位址。
- **`notrack`**：告訴 Intel CET 的間接分支追蹤，這次跳躍的目標不必以 `endbr64` 開頭，因為目標來自編譯器產生的唯讀表格。
- **tail duplication**：書中各分支跳到共用的結尾執行 `*dest = val; ret`，gcc 把這段結尾**複製**到每個分支，省一次跳躍。

clang 14 同樣用範圍檢查加相對位移的表格（`.LJTI0_0`），但各分支共用結尾，也沒有 `endbr64`/`notrack`（Ubuntu 的 clang 未預設 `-fcf-protection`）。

case 值稀疏時，跳躍表會大得不划算，編譯器改用比較樹：

```c
int sparse(int n)
{
    switch (n) {
    case 1:      return 10;
    case 1000:   return 20;
    case 50000:  return 30;
    case 900000: return 40;
    default:     return 0;
    }
}
```

```
sparse:
	cmpl	$50000, %edi            # 先和中間值比，二分
	je	.L4
	jg	.L3
	movl	$10, %eax
	cmpl	$1, %edi
	je	.L1
	xorl	%eax, %eax
	movl	$20, %edx
	cmpl	$1000, %edi
	cmove	%edx, %eax              # 小分支直接用條件搬移，連跳躍都省了
	ret
.L3:
	xorl	%eax, %eax
	movl	$40, %edx
	cmpl	$900000, %edi
	cmove	%edx, %eax
	ret
.L4:
	movl	$30, %eax
.L1:
	ret
```

所以「`switch` 一定比 `if-else` 快」是錯誤的簡化：編譯器會依 case 的分布選擇跳躍表、比較樹、位元測試或查值表，`if-else` 鏈在最佳化後也可能變成同樣的東西。

### 用 GNU labels as values 把跳躍表寫回 C

CS:APP Figure 3.22 以 GCC 的 labels as values 擴充把機械碼「反寫」成 C：`&&label` 取得標籤位址（型別 `void *`），`goto *ptr` 跳到該位址，稱為 **computed goto**。

```c
void switch_eg_impl(long x, long n, long *dest)
{
    /* table of code pointers */
    static void *jt[7] = {
        &&loc_A, &&loc_def, &&loc_B, &&loc_C,
        &&loc_D, &&loc_def, &&loc_D,
    };
    unsigned long index = (unsigned long) n - 100;
    long val;

    if (index > 6)
        goto loc_def;
    /* multiway branch */
    goto *jt[index];

loc_A: /* case 100 */
    val = x * 13;
    goto done;
loc_B: /* case 102 */
    x = x + 10;
    /* Fall through */
loc_C: /* case 103 */
    val = x + 11;
    goto done;
loc_D: /* case 104, case 106 */
    val = x * x;
    goto done;
loc_def: /* default case */
    val = 0;
done:
    *dest = val;
}
```

和 `switch_eg` 放在同一程式，以 `x = 7`、`n` 從 90 到 115 逐一比對：

```
n=99 -> 0
n=100 -> 91
n=101 -> 0
n=102 -> 28
n=103 -> 18
n=104 -> 49
n=105 -> 0
n=106 -> 49
n=107 -> 0
all 26 values match
```

gcc `-O0`/`-O2` 與 clang `-O2` 都是 `all 26 values match`。注意這裡先轉 `unsigned long` 再減：CS:APP 原文寫 `n - 100` 以 `long` 相減，`n` 接近 `LONG_MIN` 時會溢位，是未定義行為（6.5 §5）。這是 GNU 擴充，`-pedantic` 下：

```
jt_check.c:17:5: warning: ISO C forbids ‘goto *expr;’ [-Wpedantic]
jt_check.c:8:9: warning: taking the address of a label is non-standard [-Wpedantic]
```

這段 C 只是**示意**：編譯器不會先產生它再編譯，`switch` 也不一定用跳躍表。

## computed goto 與直譯器分派

位元組碼直譯器的主迴圈每執行一道虛擬機器指令，就要依 opcode 跳到對應的處理程式，這一步稱為**分派**（dispatch），是直譯器最頻繁的動作。可攜寫法是迴圈內放 `switch`；支援 labels as values 時，則讓每個處理程式結尾自行 `goto *labels[op]`。GCC 手冊對這個擴充有兩項提醒：標籤位址陣列 "does not check whether the subscript is in bounds"；標籤位址不可用來跳到其他函式，否則 "totally unpredictable things happen"。

以下的小型暫存器機器用 `-DUSE_GOTO` 切換兩種分派，指令本體以 `CASE` 與 `NEXT` 巨集共用（程式取自原文）：

```c
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

enum { OP_ADD, OP_SUB, OP_MUL, OP_XOR, OP_SHR, OP_SHL, OP_DEC, OP_JNZ, OP_HALT };

typedef struct {
    uint8_t op, a, b, c; /* r[a] = r[b] OP r[c]; JNZ: if (r[a]) pc = b */
} insn_t;

__attribute__((noinline)) static uint64_t run(const insn_t *code, uint64_t *r)
{
    const insn_t *pc = code;
#ifdef USE_GOTO
    static const void *const labels[] = {
        &&L_ADD, &&L_SUB, &&L_MUL, &&L_XOR, &&L_SHR,
        &&L_SHL, &&L_DEC, &&L_JNZ, &&L_HALT,
    };
#define DISPATCH() goto *labels[pc->op]
#define CASE(x) L_##x:
#define NEXT() do { pc++; DISPATCH(); } while (0)
    DISPATCH();
#else
#define CASE(x) case OP_##x:
#ifdef BROKEN_NEXT
#define NEXT() do { pc++; continue; } while (0)   /* continue is captured! */
#else
#define NEXT() { pc++; continue; } /* do-while(0) would capture continue */
#endif
    for (;;) {
        switch (pc->op) {
#endif
    CASE(ADD) r[pc->a] = r[pc->b] + r[pc->c]; NEXT();
    CASE(SUB) r[pc->a] = r[pc->b] - r[pc->c]; NEXT();
    CASE(MUL) r[pc->a] = r[pc->b] * r[pc->c]; NEXT();
    CASE(XOR) r[pc->a] = r[pc->b] ^ r[pc->c]; NEXT();
    CASE(SHR) r[pc->a] = r[pc->b] >> (r[pc->c] & 63); NEXT();
    CASE(SHL) r[pc->a] = r[pc->b] << (r[pc->c] & 63); NEXT();
    CASE(DEC) r[pc->a]--; NEXT();
    CASE(JNZ)
        if (r[pc->a]) {
            pc = code + pc->b;
#ifdef USE_GOTO
            DISPATCH();
#else
            continue;
#endif
        }
        NEXT();
    CASE(HALT) return r[1];
#ifndef USE_GOTO
#ifdef NO_BOUNDS_CHECK
        default: __builtin_unreachable();
#endif
        }
    }
#endif
}

int main(int argc, char **argv)
{
    /* r0: loop counter, r1: hash, r2..r5: constants and temporaries */
    static const insn_t prog[] = {
        { OP_MUL, 1, 1, 2 },  /* 0: h *= K          */
        { OP_SHR, 3, 1, 4 },  /*    t = h >> 29     */
        { OP_XOR, 1, 1, 3 },  /*    h ^= t          */
        { OP_SHL, 3, 1, 5 },  /*    t = h << 17     */
        { OP_ADD, 1, 1, 3 },  /*    h += t          */
        { OP_SUB, 1, 1, 0 },  /*    h -= i          */
        { OP_DEC, 0, 0, 0 },  /*    i--             */
        { OP_JNZ, 0, 0, 0 },  /*    if (i) goto 0   */
        { OP_HALT, 0, 0, 0 },
    };
    uint64_t r[8] = { 0 };
    r[0] = 100000000;
    if (argc > 1) {
        char *end;
        r[0] = strtoull(argv[1], &end, 0);
        if (*argv[1] == '\0' || *end != '\0' || r[0] == 0) {
            fprintf(stderr, "usage: %s [iterations > 0]\n", argv[0]);
            return 1;
        }
    }
    r[1] = 1;
    r[2] = 0x9E3779B97F4A7C15u;
    r[4] = 29;
    r[5] = 17;
    printf("%016llx\n", (unsigned long long) run(prog, r));
    return 0;
}
```

`0x9E3779B97F4A7C15` 是 2⁶⁴ 除以黃金比例取整的常數，常用於雜湊混合；29 與 17 是位移量。這段位元組碼只是一個「每圈 8 道指令」的穩定負載，數值本身不重要，輸出用來確認三種組態算出同樣的結果。

### 先看一個 bug：`do { } while (0)` 捕獲 `continue`

`switch` 版本的 `NEXT()` 刻意寫成單純的大括號。Ch 27 推薦的 `do { ... } while (0)` 在這裡會出錯：N1570 6.8.6.2 §2 把 `continue` 定義為跳到**最內層**迴圈主體的結尾，而 `do { } while (0)` 本身就是最內層迴圈：

```
$ gcc -std=gnu11 -O2 -Wall -Wextra -DBROKEN_NEXT vm.c -o vm_broken     # 0 個警告
$ ./vm_broken 1000
0000000000000000
$ ./vm_gcc_switch 1000
9d342abd4cb5f8c0
```

`continue` 跳到偽迴圈結尾、`while (0)` 為偽、偽迴圈結束，控制**循序落入下一個 `case`**，而不是回到 `for (;;)` 重新分派。程式照常編譯、沒有警告（clang 14 也一樣），結果卻是錯的。

### 量測

以 `-std=gnu11 -O2` 編譯三種組態，引數 `300000000`（3 億圈 × 8 道指令 = 24 億次分派），`taskset -c 4` 固定核心，各跑 5 次取最佳。間接跳躍數由 `objdump -d` 計算 `run()` 中的 `jmp *` 指令：

| 組態 | 間接跳躍數 | `run()` 指令數 | 最佳時間 | 每次分派 |
|---|---|---|---|---|
| gcc `switch` | 1 | 85 | 3.636 s | 1.52 ns |
| gcc `switch` + `__builtin_unreachable()` | 1 | 83 | 3.651 s | 1.52 ns |
| gcc computed goto | 10 | 105 | 1.802 s | 0.75 ns |
| clang `switch` | 1 | 61 | 1.889 s | 0.79 ns |
| clang `switch` + `__builtin_unreachable()` | 10 | 107 | 1.916 s | 0.80 ns |
| clang computed goto | 10 | 94 | 2.042 s | 0.85 ns |

（AMD Ryzen 7 5700G，WSL2 回報 3.79 GHz；六個組態輸出同為 `245063cd0c5593e3`。）

這張表要小心解讀：

1. **gcc 的 `switch` 版本慢一倍，但 clang 的 `switch` 版本和 computed goto 一樣快**。所以「單一分派點」本身不是罪魁禍首。看 gcc 的機械碼，每次分派的路徑是：處理程式結尾 `jmp` 到一段共用的「載入下一個 opcode」程式碼，再 `jmp` 回範圍檢查，再查表、間接跳躍。也就是每道虛擬指令多了**兩次無條件跳躍**；clang 的版本只多一次。我們推測多出的 taken branch 與更長的相依鏈是主因，但沒有 `perf` 無法確認分支預測失誤數，這點是推論。我們也試了 `-falign-functions=64`、`-falign-loops=64`（排除原文提到的程式碼佈局偶然），gcc `switch` 都維持在 3.8 s 左右，差距穩定存在。
2. **`__builtin_unreachable()` 對 clang 改變了程式形狀**：宣告 `default` 不可達後，clang 對分派做 tail duplication，複製到每個處理程式結尾（間接跳躍從 1 變 10），產生的控制流程和 computed goto 相同；gcc 始終保留單一分派點。
3. **和原文的結果不同**：原文在 Zen+（Threadripper 2990WX）上用 GCC 14 量到 computed goto 只快約 14%，而且所有組態的分支預測失誤都低於每 2,000 次分派一次。我們在 Zen 3 上用 gcc 11 量到快一倍。這正說明原文的結論：**computed goto 的效益取決於處理器、編譯器版本、程式碼佈局與 opcode 序列**，必須在目標平台上以相同輸入驗證。

### 為什麼傳統上認為 computed goto 比較快

Ertl 與 Gregg 在 JILP 2003 的〈The Structure and Performance of Efficient Interpreters〉提出的經典解釋是**分支預測**：早期的間接分支預測器以分支指令的位址查 BTB，只記得「這道跳躍上次跳到哪裡」。`switch` 只有一道間接跳躍，相鄰 opcode 經常不同時就頻繁猜錯；computed goto 有多道，每一道只要預測「某道特定指令之後的下一道」，對有規律的位元組碼更容易猜中。

但 Rohou、Swamy 與 Seznec 在 CGO 2015 的〈Branch Prediction and the Performance of Interpreters: Don't Trust Folklore〉實測 Nehalem、Sandy Bridge、Haswell 三代處理器：分派造成的預測失誤由 Nehalem 的每千道指令 12–20 次降到 Haswell 的 0.5–2 次，結論是 "the accuracy of indirect branch prediction is no longer critical for interpreters"。現代預測器（ITTAGE 類）會參考分支歷史，單一分派點也能預測得很好。剩下的差異多半來自**指令數與跳躍數**，這和我們的觀察一致。

### 省掉範圍檢查不是免費的

即使所有 opcode 都有 `case`，編譯器仍須處理超出範圍的值（gcc 的版本是 `ja` 跳回自己，形成無窮迴圈）。computed goto 版與 `NO_BOUNDS_CHECK` 版都**假設位元組碼已事先驗證**：遇到超出範圍的 opcode，前者越界索引 `labels` 並跳到讀出的任意位址，後者抵達 `__builtin_unreachable()`，兩者都是未定義行為。C23 7.21.1 把同一語意標準化為 `<stddef.h>` 的 `unreachable()`。接收外部輸入的直譯器必須在載入時驗證，或者像 Linux eBPF 直譯器那樣，讓表格涵蓋**所有可能的 opcode 值**：

```c
    static const void * const jumptable[256] __annotate_jump_table = {
        [0 ... 255] = &&default_label,
        /* Now overwrite non-defaults ... */
        BPF_INSN_MAP(BPF_INSN_2_LBL, BPF_INSN_3_LBL),
        ...
    };
    ...
select_insn:
    goto *jumptable[insn->code];
```

`insn->code` 是 `u8`，256 項的表格保證不會越界，未定義的 opcode 跳到 `default_label`。`[0 ... 255]` 是 GNU 的範圍指定擴充，後面的指定覆寫個別項目（N1570 6.7.9 §19 的「後列覆寫先列」），正是 Ch 30 `FOO_DEFAULTS` 的同一招；核心因此在 `kernel/bpf/Makefile` 對 `core.c` 加 `-Wno-override-init`。CPython 用 `USE_COMPUTED_GOTOS` 切換兩種分派（Ch 28 的 `TARGET` 巨集）；Python 3.14 的尾端呼叫直譯器最初宣稱快 9%–15%，後來發現收益主要來自避開 LLVM 19 對 computed goto 版本的效能退化，官方下修為 3%–5%：**比較分派方式時，基準的編譯器版本同樣要固定**。

## 進階題材：case 標籤的延伸用法

N1570 6.8.1 §2 只要求 `case` 與 `default` 出現在 `switch` 敘述**中**，6.8.4.2 §4 也寫 "on or in the switch body"：標籤可以放進 `if`、迴圈或巢狀區塊。以下三個手法都建立在這條規則上，它們見於既有程式碼，新程式碼應先考慮更直接的寫法。

### Clifford's device：`if (0)` 中的 case 標籤

```c
#include <stdio.h>
#ifndef DEBUG
#define DEBUG 1
#endif
#define DBG(...) \
    if (DEBUG) { __VA_ARGS__; }

int main(int argc, char *argv[])
{
    char *num;
    switch (argc - 1) {
             case  0: num =  "zero";
        DBG( case  1: num =   "one"; )
        DBG( case  2: num =   "two"; )
        DBG( case  3: num = "three"; )
        DBG( default: num =  "many"; )
        while (--argc)
            printf("%s ", argv[argc]);
        printf("\nArgument count: %s\n", num);
        break;
    }
    return 0;
}
```

```
$ gcc -Wall -Wextra clifford.c -o cl1 && gcc -Wall -Wextra -DDEBUG=0 clifford.c -o cl0   # 兩者都沒有警告
== DEBUG=1
1
Argument count: many
2 1
Argument count: many
4 3 2 1
Argument count: many
== DEBUG=0
1
Argument count: one
2 1
Argument count: two
3 2 1
Argument count: three
4 3 2 1
Argument count: many
```

一個看似「除錯開關」的巨集，從 1 改成 0 卻**改變了程式語意**。`DEBUG` 為 1 時展開成 `if (1) { ... }`，從任一 `case` 進入後，後面每個 `if (1)` 都為真，`num` 被逐一覆寫成 `"many"`。`DEBUG` 為 0 時，`switch` **直接跳進** `if (0)` 的主體，條件只在循序執行到 `if` 時才求值；從某個 `case` 進入後，後面的 `if (0)` 都為偽而被跳過，`num` 保留進入點設的值。gcc 11 與 clang 14 加上 `-Wall -Wextra` 對兩種設定都沒有警告。

```c
if (0) {
label:
    ...
}
```

這個結構稱為 Clifford's device：正常流程略過區塊，只有經由 `goto` 或 `switch` 跳到標籤時才執行，執行完回到 `if (0)` 之後。OpenSSL 1.0.0/1.0.1 的 `X509_verify_cert()` 用它讓 16 處 `goto end` 先補做一步參數處理，再落入所有路徑共用的釋放；1.1.0 重寫後不再使用。

### Duff's device

Tom Duff 1983 年在 Lucasfilm 把逐一複製的迴圈展開 8 次，再用 `switch` 依餘數**跳進展開後迴圈主體的中段**，處理不足 8 個的部分：

```c
#include <stdio.h>

void dsend(int count)
{
    if (count <= 0)
        return;
    int n = (count + 7) / 8;
    switch (count % 8) {
    case 0:
        do {
            puts("case 0");
        case 7:
            puts("case 7");
        case 6:
            puts("case 6");
        case 5:
            puts("case 5");
        case 4:
            puts("case 4");
        case 3:
            puts("case 3");
        case 2:
            puts("case 2");
        case 1:
            puts("case 1");
        } while (--n > 0);
    }
}

int main(void)
{
    dsend(10);
    return 0;
}
```

```
$ ./duff | tr "\n" ","
case 2,case 1,case 0,case 7,case 6,case 5,case 4,case 3,case 2,case 1,
```

`dsend(10)`：`n = 2`、`count % 8 = 2`，`switch` 跳到 `case 2`，第一圈印 2 行，第二圈從 `case 0` 印 8 行，共 10 行。從中段進入後迴圈照常重複，N1570 6.8.5 §4 寫明 "The repetition occurs regardless of whether the loop body is entered from the iteration statement or by a jump"。迴圈主體因此有 8 個入口，構成**不可歸約**的控制流程圖。

```
$ gcc -Wall -Wextra duff.c        # 7 個 "this statement may fall through" 警告
$ clang -Wall -Wextra duff.c      # 0 個：clang 的 -Wextra 不含 -Wimplicit-fallthrough
$ clang -Wimplicit-fallthrough duff.c
duff.c:12:9: warning: unannotated fall-through between switch labels [-Wimplicit-fallthrough]
```

C23 的 `[[fallthrough]];`（N3220 6.7.13.6）在語言層級表達「刻意落入」：在 `case 1` 以外的七個 `puts` 之後加上它即可消除警告；6.7.13.6 要求它之後的下一個區塊項目是 `case`/`default` 標籤，在迴圈內時還必須屬於同一次迴圈主體的執行，Duff's device 的七處落入都符合。

> **本段未實測**：`[[fallthrough]]` 版本原計畫以 Docker `gcc:15` 驗證，撰寫時 Docker 引擎無法連線。依原文，GCC 13/14 與 Clang 18/20 在 C23 模式下以 `-Wall -Wextra -Werror` 編譯皆通過。

Tom Duff 本人說 "if I were writing assembly language code, I'd just jump into the middle of the unwound loop"，這是組合語言慣用法在 C 的直譯。在今日的處理器與編譯器上多半沒有益處：多入口迴圈妨礙編譯器辨識與向量化迴圈，`-O2`/`-O3` 對單純迴圈的自動展開與 SIMD 化通常更好。[Ch 33](./33-tricks-pointers-and-performance.md) 會實測比較。

### 以 switch 實作 coroutine

coroutine 一詞由 Melvin Conway 在 1963 年提出。副程式每次呼叫都從頭開始，返回後區域狀態消失；coroutine 能在中途讓出（yield），下次被呼叫時從讓出處繼續。實作只需兩項能力：**保存目前位置**，以及**之後跳回該處**。

PuTTY 作者 Simon Tatham 的〈Coroutines in C〉利用 Duff's device 的同一規則：讓出時把 `__LINE__` 存進 `static` 變數並 `return`，同時在原地放一個 `case __LINE__:` 標籤；下次呼叫時，函式開頭的 `switch` 直接跳回該標籤：

```c
#define _DEFAULT_SOURCE /* usleep() under -std=c11 */
#include <stdio.h>
#include <unistd.h>

#define THREAD_INTERVAL 500
#define cr_start()           \
    static int cr_state = 0; \
    switch (cr_state) {      \
    case 0:

#define cr_yield                 \
    {                            \
        cr_state = __LINE__;     \
        usleep(THREAD_INTERVAL); \
        return;                  \
    case __LINE__:;              \
    }

#define cr_end() \
    }            \
    cr_state = 0;

static int condition = 1;

static void user_thread_1(void)
{
    cr_start();
    for (;;) {
        /* do something */
        printf("Run %s\n", __func__);
        cr_yield;
    }
    cr_end();
}

static void user_thread_2(void)
{
    cr_start();
    for (;;) {
        if (condition) {
            /* do something conditional */
            printf("Run %s - (1)\n", __func__);
            cr_yield;
        }
        /* do something */
        printf("Run %s - (2)\n", __func__);
        condition = !condition;
        cr_yield;
    }
    cr_end();
}

int main(void)
{
    for (;;) {
        user_thread_1();
        user_thread_2();
    }
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra coro.c -o coro && stdbuf -oL ./coro | head -14
Run user_thread_1
Run user_thread_2 - (1)
Run user_thread_1
Run user_thread_2 - (2)
Run user_thread_1
Run user_thread_2 - (2)
Run user_thread_1
Run user_thread_2 - (1)
Run user_thread_1
Run user_thread_2 - (2)
Run user_thread_1
Run user_thread_2 - (2)
Run user_thread_1
Run user_thread_2 - (1)
```

（stdout 接到管線時是全緩衝，`stdbuf -oL` 改成行緩衝；`head` 讀滿 14 行後關閉管線，程式因 `SIGPIPE` 結束。）用 `gcc -E -P` 看 `user_thread_2` 展開後的樣子，一切就清楚了：

```c
static void user_thread_2(void)
{
    static int cr_state = 0; switch (cr_state) { case 0:;
    for (;;) {
        if (condition) {
            printf("Run %s - (1)\n", __func__);
            { cr_state = 43; usleep(500); return; case 43:; };
        }
        printf("Run %s - (2)\n", __func__);
        condition = !condition;
        { cr_state = 48; usleep(500); return; case 48:; };
    }
    } cr_state = 0;;
```

`case 43:` 在 `if` 裡面、`case 48:` 在 `for` 裡面，`switch (cr_state)` 直接跳進去。函式變成一台狀態機，`cr_state` 只會是 0、43、48；狀態值由 `__LINE__` 自動產生，讓出點可以寫在迴圈與 `if` 之中，程式碼仍保有循序的外觀。`main` 的迴圈扮演排程器，這是**合作式多工**（cooperative multitasking）。

這組巨集只保存「續行位置」、不保存堆疊，屬於 **stackless coroutine**，限制也由此而來：

- **區域變數在讓出後消失**：跨越讓出點的狀態必須是 `static` 或放在呼叫端傳入的結構中（zlib 把所有跨呼叫變數放在 `z_stream`）；而 `static` 讓同一函式只能有一個實體。
- **只有 coroutine 函式本身能讓出**：它呼叫的其他函式無法代為讓出，因為讓出靠 `return`。
- **兩個 `cr_yield` 不能寫在同一行**（重複的 `case` 標籤），也不能寫在內層另一個 `switch` 裡（`case` 屬於最內層的 `switch`）。
- **恢復時跳過讓出點之前的 automatic 變數初始化**，和前面 6.8.4.2 §7 的 EXAMPLE 相同；若讓出點位於 VLA 作用域內則無法編譯。

Tatham 直言 PuTTY 的 SSH 協定程式碼用了這個手法，"As far as I know, this is the worst piece of C hackery ever seen in serious production code"。PuTTY 的 `sshcr.h` 把狀態存在呼叫端結構的成員（`crLine` 指向它），所以同一函式可以同時服務多條連線：

```c
#define crBegin(v)      do { int *crLine = &v; switch(v) { case 0:
#define crFinish(z)     } *crLine = 0; return (z); } while (0)
#define crReturn(z)     \
        do {\
            *crLine = __LINE__; return (z); case __LINE__:;\
        } while (0)
```

`case __LINE__:` 放在 `do { } while (0)` 裡，`switch` 照樣跳得進去，又是一次 Duff's device。Adam Dunkels 的 **Protothreads** 是為記憶體極少的嵌入式系統設計的同類函式庫，它把「保存位置」與「跳回位置」抽象為 local continuation：預設的 `lc-switch.h` 用上述的 `switch` + `__LINE__`（檔案中警告 LC_SET 不能在另一個 `switch` 內使用）；另一個實作 `lc-addrlabels.h` 改用 labels as values，以 `&&label` 保存位置、`goto *` 跳回，解除了 `switch` 巢狀的限制。

要保留 automatic 區域變數、讓任意深度的呼叫都能讓出，就得替每個 coroutine 保存獨立的堆疊與暫存器，即 **stackful coroutine**（`ucontext` 或平台相關的組合語言）。`setjmp`/`longjmp` 本身不建立獨立堆疊，見 [Ch 12](./12-setjmp-and-coroutines.md)。

## 對比與取捨

| 需求 | 手法 | 優點 | 缺點 |
|---|---|---|---|
| 多步取得資源、失敗時反向歸還 | `goto` 清理鏈 | 每個釋放只寫一次；標籤即進度表；可攜 | 標籤前提靠人審查；錯誤碼容易漏設 |
| 同上 | `__attribute__((cleanup))` | 提早返回自動釋放；新增資源不必改出口 | GCC/Clang 擴充；釋放時機從原始碼中消失；不可與 `goto` 混用 |
| 同上 | 巢狀 `if` | 不用 `goto` | 正常路徑右移；深度隨資源數增加 |
| 跳出多層迴圈 | `goto` | 直接 | 需要讀者確認標籤位置 |
| 同上 | 旗標變數 / 抽成函式 `return` | 結構化 | 旗標散落；抽函式要傳很多參數 |
| 多路分支 | `switch` | 可攜；編譯器自選最佳實作 | 只能比整數 |
| 直譯器分派 | computed goto | 省範圍檢查與回跳；常見更快 | GNU 擴充；越界即 UB；效益依平台而定 |
| 可暫停的函式 | `switch` + `__LINE__` coroutine | 零額外記憶體；純 C | 區域變數不保留；不能巢狀 `switch`；難除錯 |
| 同上 | stackful（ucontext/組語） | 區域變數與呼叫深度不受限 | 每個 coroutine 一個堆疊；不可攜 |

## 踩雷集錦

1. **以為 C 編譯器會擋住跳過初始化**：C 允許，值未定；gcc 要 `-Wjump-misses-init` 才警告，而且 `-O0` 與 `-O2` 行為不同。C++ 才把它列為錯誤。
2. **跳到清理標籤前沒有設錯誤碼**：`ret` 還是上一個成功呼叫留下的 0，函式失敗卻回報成功。在每個 `goto` 前就近設定，或讓「取得」與「檢查」緊鄰（`ret = f(); if (ret) goto out;`）。
3. **一個標籤收所有失敗**：不同進度抵達同一標籤，釋放了尚未取得的資源（one-err bug）。依進度拆標籤，或確保所有指標先初始化為 `NULL` 且釋放函式接受 `NULL`。
4. **`goto` 跳過 cleanup 變數**：clang 拒絕編譯，gcc 11 照常編譯並對堆疊殘值呼叫清理函式。同一函式不要混用。
5. **巨集裡的 `continue`/`break` 被 `do { } while (0)` 捕獲**：直譯器默默算錯（`run()` 回傳 0），沒有任何警告。巨集中需要 `continue` 時改用單純的大括號，或像 eBPF 那樣用 statement expression 加 `goto`。
6. **以為 computed goto 一定比 `switch` 快**：我們在 clang 14 上量到 `switch` 反而略快；原文在 Zen+ 上差 14%；我們的 gcc 11 差一倍。效能宣稱必須附上編譯器、版本、處理器與輸入。
7. **把「除錯開關」巨集包在 `switch` 的 `case` 外面**：Clifford's device 讓 `DEBUG` 從 1 改 0 時改變語意，編譯器沒有警告。

## 進階：再往深一層

- **可歸約性與編譯器**：多入口迴圈（Duff's device、跳入迴圈的 `goto`）讓控制流程圖不可歸約，許多迴圈最佳化（辨識迴圈、向量化、LICM）會放棄。編譯器中間表示本身就以 basic block 與跳躍表達控制流程，[Ch 37](./37-compiler-optimization.md) 會從 SSA 的角度看「結構化」對最佳化的意義。
- **threaded code 的家族**：computed goto 對應 direct threading 的一種形式；更進一步有 subroutine threading、context threading，以及 JIT。Ertl 與 Gregg 的論文是這個領域的入口。
- **`defer` 提案**：WG14 正在討論把 `defer` 以技術規格（TS）形式加入 C，讓「離開作用域時執行」成為標準語法而非擴充；目前狀態請以 WG14 文件為準（[Ch 32](./32-tricks-syntax-and-resources.md) 有更多討論）。
- **面試題**：「C 裡怎麼處理錯誤時的資源清理？」完整答案：清理鏈與標籤命名、標籤前提、錯誤碼設定、`cleanup` 屬性與它的陷阱、以及用 fault injection 測試所有路徑。

## 動手練習

1. 把 `unwind.c` 擴充到 5 項資源，再寫一個用旗標變數（不用 `goto`）的版本，比較兩者的行數與每增加一項資源要改幾處。
2. 在 `sign.c` 中故意把第 3 步的 `goto err` 改成直接 `return 0`，用 ASan 觀察洩漏的是什麼；再故意移除 `int ret = 0;` 的初始化，看 `-Wall`、`-Wmaybe-uninitialized`、clang 的 `-Wsometimes-uninitialized` 各自的反應。
3. 在 `vm.c` 的 `prog` 加入一道 opcode 為 9 的非法指令，分別以三種組態執行，觀察：`switch` 版本（無窮迴圈？）、`NO_BOUNDS_CHECK` 版本、computed goto 版本各發生什麼事；再用 eBPF 的做法把 `labels` 擴成 256 項並加 `default_label`。
4. 把 `coro.c` 改成 PuTTY 風格：狀態存在呼叫端傳入的結構中，同時跑兩個 `user_thread_2` 實體，確認它們各自獨立。

## 本章重點整理

- `goto` 的爭議在可推理性與資源責任，不在關鍵字本身；Linux 核心以「只有需要共同善後時才集中出口」為原則，標籤命名描述動作。
- C 只禁止跳入 VLA 作用域；跳過初始化合法但值未定；C23 允許標籤在宣告與 `}` 前。MISRA C:2012 起 `goto` 降為 Advisory，但限定只能往後跳、不能跳入區塊。
- 清理鏈的每個標籤是一組狀態前提；兩類典型缺陷是跳錯標籤與漏設錯誤碼。`cleanup` 屬性改以宣告順序記錄釋放順序，但不可與 `goto` 混用。
- `switch` 由編譯器自選跳躍表、比較樹或條件搬移；跳躍表的範圍檢查利用無號回繞，表格存相對位移以支援 PIE。
- computed goto 把分派複製到每個處理程式，傳統解釋是分支預測，現代處理器上主要差在指令與跳躍數；效益高度依賴平台，必須實測。省略範圍檢查的前提是位元組碼已驗證。
- `case` 標籤可放進 `if` 與迴圈內，衍生出 Clifford's device、Duff's device 與 stackless coroutine，都違反 MISRA Rule 16.2，新程式碼應優先考慮直接的寫法。

## 自我檢核

- [ ] 不看筆記，能說出 C 標準對 `goto` 唯一的硬限制，以及 C 與 C++ 在「跳過初始化」上的差異
- [ ] 能寫出一個 3 項資源的清理鏈，並對每個 `goto` 說出目標標籤的狀態前提
- [ ] 如果面試官問「MISRA 禁止 goto 嗎」，能依版本與規則等級精確回答
- [ ] 能解釋 `cleanup` 屬性的釋放順序由什麼決定，以及為什麼核心要求不與 `goto` 混用
- [ ] 能逐行解讀 `switch_eg` 的組合語言，說明 `ja` 為什麼能一次排除兩側、表格為什麼存相對位移
- [ ] 能解釋 `do { } while (0)` 為什麼會讓直譯器的 `NEXT()` 出錯
- [ ] 能說明為什麼「computed goto 比 switch 快」需要附條件，並舉出本章與原文兩組不同的量測結果
- [ ] 能說出 stackless coroutine 的四個限制，並說明 zlib `inflate()` 與它的關係

## 延伸閱讀

### 論文

- **[Go To Statement Considered Harmful](https://homepages.cwi.nl/~storm/teaching/reader/Dijkstra68.pdf)** — Edsger W. Dijkstra，CACM（1968）
  - **核心貢獻**：論證程式正確性依賴讀者追蹤執行進度的能力，任意跳躍破壞了描述進度的座標
  - **讀哪裡**：全文只有一頁半，直接讀
  - **和本章的關聯**：所有後續爭論的起點；讀完再讀 Knuth 那篇

- **[Structured Programming with go to Statements](https://pic.plover.com/knuth-GOTO.pdf)** — Donald E. Knuth，ACM Computing Surveys（1974）
  - **核心貢獻**：系統性分析哪些情況 `goto` 讓程式更清楚（錯誤處理、跳出多層迴圈、效能關鍵迴圈），把爭點從關鍵字拉回程式結構
  - **讀哪裡**：前三節（Introduction、Elimination of go to statements、Introduction of go to statements）；後面的效能範例偏長可跳讀
  - **前提知識**：本章前半

- **[The Structure and Performance of Efficient Interpreters](https://www.jilp.org/vol5/v5paper12.pdf)** — M. Anton Ertl、David Gregg，JILP（2003）
  - **核心貢獻**：以分支預測解釋 threaded code（computed goto）比 `switch` 分派快的原因，並量測多種直譯器
  - **讀哪裡**：Section 2（dispatch techniques）與 Section 3（branch prediction）

- **[Branch Prediction and the Performance of Interpreters: Don't Trust Folklore](https://inria.hal.science/hal-01100647/document)** — Erven Rohou、Bharath Narasimha Swamy、André Seznec，CGO（2015）
  - **核心貢獻**：在三代 Intel 處理器上實測，顯示現代間接分支預測器讓分派的預測失誤大幅下降，推翻「switch 分派必然慢」的民間說法
  - **和本章的關聯**：解釋為什麼我們的 clang `switch` 版本並不慢，以及為什麼效能結論必須附平台

- **[An Empirical Study of Goto in C Code from GitHub Repositories](https://cs.uwaterloo.ca/~m2nagapp/publications/pdfs/An-Empirical-Study-of-Goto-in-C-Code-from-GitHub-Repositories.pdf)** — Nagappan et al.，ESEC/FSE（2015）
  - **讀哪裡**：RQ1（goto 的用途分類）的表格；了解實務上的 `goto` 長什麼樣

### 官方文件 / 規範

- **[Linux kernel coding style](https://www.kernel.org/doc/html/latest/process/coding-style.html)** 第 7 節 "Centralized exiting of functions" 與 **[Scope-based Cleanup Helpers](https://docs.kernel.org/core-api/cleanup.html)**
  - **讀哪裡**：前者一頁，包含 one-err bug 範例；後者的「Comments」段落討論宣告順序與不可混用 `goto` 的理由
  - **為什麼值得讀**：核心開發者寫給核心開發者的規範，直接對應本章兩種清理寫法

- **[GCC 手冊：Labels as Values](https://gcc.gnu.org/onlinedocs/gcc/Labels-as-Values.html)**
  - **讀哪裡**：整頁（短），特別是對越界與跨函式跳躍的警告

### 部落格 / 技術文章

- **[Computed goto for efficient dispatch tables](https://eli.thegreenplace.net/2012/07/12/computed-goto-for-efficient-dispatch-tables)** — Eli Bendersky（2012）
  - **這篇說什麼**：從零寫一個 `switch` 版與 computed goto 版直譯器並比較，附反組譯分析
  - **為什麼值得讀**：本章直譯器實驗的前身；作者量到快 25%，可和本章與原文的數字對照，體會平台差異

- **[Coroutines in C](https://www.chiark.greenend.org.uk/~sgtatham/coroutines.html)** — Simon Tatham
  - **讀哪裡**：整篇，從 Duff's device 一步步推導出 `crBegin`/`crReturn`
  - **前提知識**：本章 Duff's device 小節

### 書籍

- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** — Bryant & O'Hallaron（Pearson，2015）
  - **讀哪幾章**：Section 3.6.8 "Switch Statements"（本章 `switch_eg` 的來源，含跳躍表的完整解說）
  - **注意**：書中是非 PIE 的 64 位元絕對位址表格，和本章的相對位移版本對照閱讀

> 和既有課的關係：`setjmp`/`longjmp` 與 signal 的面試題整理見 [../c_interview/23-signal-setjmp.md](../c_interview/23-signal-setjmp.md)。

`goto`、`switch` 與 `case` 標籤的花招到這裡告一段落。下一章收集更多「語法層面」的 C 技巧：designated initializer、compound literal、`cleanup` 屬性與 `defer` 提案、以及資源管理的各種慣用法。

→ [Ch 32 技巧 I：語法與資源管理](./32-tricks-syntax-and-resources.md)
