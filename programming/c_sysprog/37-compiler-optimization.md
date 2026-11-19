# Ch 37 — 編譯器最佳化原理：SSA 與常見最佳化

> **目標**：知道編譯器內部「前端 → IR → 最佳化 → 後端」的分工，以及為什麼幾乎所有現代編譯器都選擇 SSA 形式的 IR；能用 `clang -emit-llvm`、`opt -mem2reg` 與 gcc 的 `-fdump-tree-*`／`-fdump-rtl-*` 親眼看到 phi 節點、GIMPLE 與 RTL；能從組合語言認出 constant folding、DCE、CSE、inlining、LICM、strength reduction、vectorization 留下的痕跡；理解 `-O0` 到 `-O3`、`-Os` 實際差在哪；最重要的是，知道最佳化在什麼地方**被擋住**（aliasing、volatile）、又在什麼地方**被放行到出乎意料**（未定義行為）。
> **對應原文**：[你所不知道的 C 語言：編譯器和最佳化原理篇](https://hackmd.io/@sysprog/c-compiler-optimization) 的「編譯器無所不在」「From Source to Binary」Page 31–78（IR、SSA、RTL、peephole、LTO）、「編譯器原理」「JIT 實作案例」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4.0、clang 14.0.0（`opt-14`）。最佳化行為隨編譯器版本變動很大：本章的組合語言只保證在這兩個版本上重現，換版本時「有沒有做某個最佳化」可能不同，請以自己的輸出為準。

## 為什麼需要這個？

先做一個實驗。下面這個函式把同一個指標讀了 20 次：

```c
void foo(int *i1, int *i2, int *res)
{
    for (int i = 0; i < 10; i++)
        *res += *i1 + *i2;
}
```

你大概會想：「`*i1 + *i2` 每圈都一樣，編譯器應該會把它算一次就好。」gcc `-O2` 的結果：

```
<foo>:
  mov    (%rdx),%r8d          ← *res 讀一次，放暫存器
  mov    $0xa,%ecx
  mov    (%rsi),%eax          ← 迴圈開始：每圈重讀 *i2
  add    (%rdi),%eax          ←            每圈重讀 *i1
  add    %eax,%r8d
  mov    %r8d,(%rdx)          ←            每圈寫回 *res
  sub    $0x1,%ecx
  jne    10 <foo+0x10>
  ret
```

它沒有提出去，而且每圈都把 `*res` 寫回記憶體。不是 gcc 笨：呼叫端可能寫 `foo(&v, &v, &v)`，三個指標指向同一個變數，那麼寫 `*res` 就會改到 `*i1` 和 `*i2`，下一圈必須重讀。編譯器不知道呼叫端怎麼傳，只能保守。這就是原文 Page 36 說的「pointer aliasing 會讓編譯器最佳化無用武之地」。

反過來，有時編譯器激進到讓人錯愕：

```c
int gt_plus1(int x) { int y = x + 1; return y > x; }
```

```
$ gcc -O0 -o ub2 ub2.c && ./ub2
gt_plus1(INT_MAX) = 0
$ gcc -O2 -o ub2 ub2.c && ./ub2
gt_plus1(INT_MAX) = 1
```

同一份程式，`-O0` 和 `-O2` 答案不同。`INT_MAX + 1` 是有號溢位，屬於未定義行為（N1570 6.5 §5），`-O2` 的 gcc 據此推論「`x + 1 > x` 恆真」，整個函式變成 `return 1`。

這兩個例子是同一枚硬幣的兩面：最佳化器在「規格書保證的範圍內」盡可能改寫程式。想預測它會做什麼、不會做什麼，就得知道它內部怎麼看程式——這正是本章的主題。

> 未定義行為的分類與更多案例在 [Ch 34 — 未定義行為](./34-undefined-behavior.md)；本章從編譯器內部的角度回頭解釋它為什麼會這樣。

## 先建立直覺：編譯器是一條三段式管線

```
  C 原始碼     Rust     Fortran          ←── 前端（每種語言一個）
     │          │          │                 詞法、語法、語意分析
     ▼          ▼          ▼
  ┌─────────────────────────────┐
  │   中間表示 IR（與語言、       │       ←── 中端（共用）
  │   機器都無關）＋ 最佳化 pass   │           SSA 形式上的各種改寫
  └─────────────────────────────┘
     │          │          │
     ▼          ▼          ▼
  x86-64     AArch64    RISC-V           ←── 後端（每種機器一個）
                                             指令選擇、暫存器配置、排程
```

原文 Page 31 的圖說的是一個組合數學問題：M 種語言、N 種機器，若每種語言直接翻成每種機器碼，要寫 M×N 個翻譯器；中間插一層 IR，只要寫 M 個前端加 N 個後端。更重要的是，**最佳化只要在 IR 上寫一次**，所有語言、所有機器都受惠。GCC 與 LLVM 都是這個架構。

IR 的設計直接決定最佳化好不好寫。早期編譯器在語法樹或三位址碼（three-address code，每條指令最多一個運算子、三個運算元，如 `t1 = a + b`）上做最佳化，但一個變數被多處賦值時，要回答「這裡用到的 `x` 是哪一次賦值的結果？」需要昂貴的資料流分析（reaching definitions）。SSA 把這個問題在 IR 的結構上就解決掉了。

## IR 與 SSA：每個名字只被賦值一次

### 什麼是 SSA

**靜態單一賦值（Static Single Assignment, SSA）**：在程式文字中，每個變數**只出現在一個賦值的左邊**。原本被多次賦值的變數拆成多個版本：

```
原始碼                    SSA 形式
x = 1;                    x1 = 1
x = x + 2;                x2 = x1 + 2
y = x * 3;                y1 = x2 * 3
```

直線程式碼很好拆，麻煩在合流處：

```
if (a > b)                if (a > b) goto L1 else goto L2
    r = a;                L1: r1 = a;  goto L3
else                      L2: r2 = b;  goto L3
    r = b;                L3: r3 = φ(r1, r2)   ← 從 L1 來就是 r1，從 L2 來就是 r2
return r + 1;                 return r3 + 1
```

**φ 函數（phi function）**放在基本區塊開頭，依「控制流程從哪個前驅區塊進來」選擇對應的值。它不是真的指令，而是一種記帳方式；最後產生機械碼時，φ 會被翻成各前驅區塊尾端的暫存器複製（或者乾脆讓兩邊用同一個暫存器）。

這裡用到兩個術語：**基本區塊（basic block）**是一段只能從頭進、從尾出、中間沒有跳躍也沒有跳躍目標的指令序列；**控制流程圖（control flow graph, CFG）**以基本區塊為節點、可能的跳躍為邊。

為什麼這樣做有價值？因為每個名字只有一個定義點，「`r3` 從哪來」「`x2` 被誰用到」都變成在圖上直接查邊，不必做全域分析。常數傳遞、無用碼消除、值編號等最佳化在 SSA 上都能寫成近乎線性時間的稀疏演算法。SSA 由 IBM 的 Rosen、Wegman、Zadeck 等人在 1980 年代末提出，Cytron 等人 1991 年發表了以支配邊界（dominance frontier）有效率地放置 φ 的演算法，此後成為主流；GCC 在 4.0 版（2005）引入 Tree SSA 中端，LLVM 則從一開始就以 SSA 為核心。

> SSA 的建構演算法（支配樹、支配邊界、φ 插入）在本 repo 的 [`../../compilers/ssa_optimizations/`](../../compilers/ssa_optimizations/README.md) 有完整一門課；本章只求能讀懂、能觀察。

### 用 clang 看 phi 怎麼長出來

```c
/* sel.c */
int select_max(int a, int b)
{
    int r;
    if (a > b)
        r = a;
    else
        r = b;
    return r + 1;
}
```

clang 的前端**不直接**產生 SSA。它先把每個區域變數放在 stack 上（`alloca`），讀寫都經過記憶體——這樣前端最好寫，不用操心 φ。`-Xclang -disable-O0-optnone` 讓 `-O0` 的 IR 之後還能交給 `opt` 處理：

```
$ clang -S -emit-llvm -O0 -Xclang -disable-O0-optnone sel.c -o sel-O0.ll
define dso_local i32 @select_max(i32 noundef %0, i32 noundef %1) #0 {
  %3 = alloca i32, align 4
  %4 = alloca i32, align 4
  %5 = alloca i32, align 4             ← r 的 stack 槽
  store i32 %0, i32* %3, align 4
  store i32 %1, i32* %4, align 4
  %6 = load i32, i32* %3, align 4
  %7 = load i32, i32* %4, align 4
  %8 = icmp sgt i32 %6, %7
  br i1 %8, label %9, label %11

9:                                                ; preds = %2
  %10 = load i32, i32* %3, align 4
  store i32 %10, i32* %5, align 4      ← r = a
  br label %13

11:                                               ; preds = %2
  %12 = load i32, i32* %4, align 4
  store i32 %12, i32* %5, align 4      ← r = b
  br label %13

13:                                               ; preds = %11, %9
  %14 = load i32, i32* %5, align 4
  %15 = add nsw i32 %14, 1
  ret i32 %15
}
```

注意：`%3`、`%10` 這些 LLVM 暫存器本身已經是 SSA（每個只定義一次），但變數 `r` 藏在記憶體 `%5` 裡，被 store 了兩次。接著跑 **mem2reg** pass，把「位址沒有外流」的 `alloca` 提升成 SSA 值：

```
$ opt-14 -S -mem2reg sel-O0.ll -o sel-m2r.ll
define dso_local i32 @select_max(i32 noundef %0, i32 noundef %1) #0 {
  %3 = icmp sgt i32 %0, %1
  br i1 %3, label %4, label %5

4:                                                ; preds = %2
  br label %6

5:                                                ; preds = %2
  br label %6

6:                                                ; preds = %5, %4
  %.0 = phi i32 [ %0, %4 ], [ %1, %5 ]    ← 從 %4 進來是 a，從 %5 進來是 b
  %7 = add nsw i32 %.0, 1
  ret i32 %7
}
```

所有 load/store 消失，`r` 變成 φ 節點 `%.0`。`-O2` 再進一步，發現兩個分支都是空的，用 `select`（條件選擇，x86 上通常變 `cmov`）取代整個分支：

```
$ clang -S -emit-llvm -O2 sel.c -o -
define dso_local i32 @select_max(i32 noundef %0, i32 noundef %1) local_unnamed_addr #0 {
  %3 = icmp sgt i32 %0, %1
  %4 = select i1 %3, i32 %0, i32 %1
  %5 = add nsw i32 %4, 1
  ret i32 %5
}
```

`add nsw` 的 `nsw` 是「no signed wrap」：clang 依 C 的規則把有號加法標記為「保證不溢位」，後續 pass 可以據此推理。開頭那個 `gt_plus1` 被摺成 `return 1`，在 LLVM 裡就是靠這個旗標。

「先全部放記憶體、再用 mem2reg 提升」這個策略，Ch 38 的 shecc 也一模一樣：區域變數先記成 load/store，函式解析完再把位址沒外流的變數升為 SSA 值。

### 用 gcc 看 GIMPLE 與 PHI

gcc 的中端 IR 叫 **GIMPLE**：三位址碼、每個運算式最多兩個運算元。`-fdump-tree-<pass>` 把某個 pass 之後的 GIMPLE 寫到檔案。

```c
/* sum.c */
int sum_to(int n)
{
    int s = 0;
    for (int i = 1; i <= n; i++)
        s += i;
    return s;
}
```

```
$ gcc -O1 -c -fdump-tree-ssa -fdump-tree-optimized sum.c
$ ls sum.c.*
sum.c.023t.ssa
sum.c.244t.optimized
$ cat sum.c.023t.ssa
  <bb 2> :
  s_3 = 0;
  i_4 = 1;
  goto <bb 4>; [INV]

  <bb 3> :
  s_8 = s_1 + i_2;
  i_9 = i_2 + 1;

  <bb 4> :
  # s_1 = PHI <s_3(2), s_8(3)>
  # i_2 = PHI <i_4(2), i_9(3)>
  if (i_2 <= n_5(D))
    goto <bb 3>; [INV]
  else
    goto <bb 5>; [INV]

  <bb 5> :
  _6 = s_1;
  return _6;
```

檔名裡的 `023t`、`244t` 是 pass 的序號——`ssa` 是第 23 個 tree pass，`optimized` 是第 244 個，中間還有兩百多個。讀法：

- `s_3`、`s_8`、`s_1` 是變數 `s` 的三個 SSA 版本，底線後的數字是版本號。
- `# s_1 = PHI <s_3(2), s_8(3)>`：在迴圈標頭 `bb 4`，從 `bb 2`（進入迴圈）來時 `s_1 = s_3 = 0`，從 `bb 3`（迴圈主體，回邊）來時 `s_1 = s_8`。這是迴圈在 SSA 裡的標準長相。
- `n_5(D)` 的 `(D)` 表示 default definition：函式進入時的參數值，沒有任何賦值。

原文 Page 40 列出 SSA 上的一連串處理：拆基本區塊、化簡 CFG、轉 SSA、常數傳遞、DCE、值域傳遞……對照 `optimized` 的輸出可以看到其中幾項的結果：

```
  <bb 2> [local count: 118111600]:
  if (n_3(D) > 0)
    goto <bb 3>; [89.00%]
  else
    goto <bb 5>; [11.00%]

  <bb 3> [local count: 105119324]:
  _1 = (unsigned int) n_3(D);
  _7 = _1 + 1;
  _8 = (int) _7;

  <bb 4> [local count: 955630225]:
  # s_9 = PHI <s_5(4), 0(3)>
  # i_11 = PHI <i_6(4), 1(3)>
  s_5 = s_9 + i_11;
  i_6 = i_11 + 1;
  if (i_6 != _8)
    goto <bb 4>; [89.00%]
  else
    goto <bb 5>; [11.00%]
```

`s_3 = 0` 這種常數已經直接傳進 PHI（`0(3)`）；迴圈被轉成「先檢查 `n > 0`，再以 `do-while` 形式執行」（loop inversion，省掉每圈開頭的一次比較）；`i <= n` 變成 `i != n + 1`；方括號裡的百分比是**分支機率估計**，後端會據此安排程式碼布局。

gcc 11 沒有更進一步，但 clang 14 認出這是等差級數，直接套公式：

```
$ clang -O2 -S -o - sum.c
sum_to:
	testl	%edi, %edi
	jle	.LBB0_1
	leal	-1(%rdi), %eax
	leal	-2(%rdi), %ecx
	imulq	%rax, %rcx
	shrq	%rcx
	leal	(%rcx,%rdi,2), %eax
	addl	$-1, %eax
	retq
.LBB0_1:
	xorl	%eax, %eax
	retq
```

算的是 (n−1)(n−2)/2 + 2n − 1，展開正好等於 n(n+1)/2——迴圈消失了。這種「求出歸納變數的閉合形式、刪掉迴圈」叫 final value replacement，LLVM 在 IndVarSimplify 中利用 scalar evolution（SCEV）分析完成。為什麼拆成 (n−1)(n−2) 這種怪形式？就我們的理解，是為了讓中間乘積在 64 位元內不溢位、同時避開 n(n+1) 在邊界的特殊情況，細節屬 LLVM 的實作選擇。同一個最佳化 gcc 做不做，取決於版本與型態，**別把一個編譯器的行為當成所有編譯器的行為**。

### GCC 的後端：RTL

GIMPLE 最佳化完，gcc 轉成 **RTL（Register Transfer Language）**，一種 LISP 風格的 S-expression，已經貼近機器指令，但暫存器仍是無限多個的虛擬暫存器：

```
$ echo 'int add1(int x) { return x + 1; }' > rtl.c
$ gcc -O2 -c -fdump-rtl-expand rtl.c
(insn 2 4 3 2 (set (reg/v:SI 83 [ x ])
        (reg:SI 5 di [ x ])) "rtl.c":1:17 -1
     (nil))
(insn 6 3 7 2 (parallel [
            (set (reg:SI 84)
                (plus:SI (reg/v:SI 83 [ x ])
                    (const_int 1 [0x1])))
            (clobber (reg:CC 17 flags))
        ]) "rtl.c":1:28 -1
     (nil))
(insn 11 7 12 2 (set (reg/i:SI 0 ax)
        (reg:SI 82 [ <retval> ])) "rtl.c":1:33 -1
     (nil))
```

`SI` 是 single integer（32 位元）；`reg:SI 5 di` 是實體暫存器 edi，`reg:SI 84` 是虛擬暫存器；`(clobber (reg:CC 17 flags))` 誠實記下「x86 的 `add` 會改寫旗標暫存器」——這種機器相關的副作用在 GIMPLE 層看不到。之後的暫存器配置把虛擬暫存器對到實體暫存器，指令排程（instruction scheduling）重排指令以減少管線停頓，最後的 peephole 階段在小窗口內套用機器相關的改寫規則。

```
gcc 內部：  C ─► GENERIC (AST) ─► GIMPLE ─► GIMPLE SSA ─► (200+ tree passes) ─► RTL ─► 組合語言
clang/LLVM：C ─► Clang AST ─► LLVM IR (alloca) ─► mem2reg ─► SSA 上的 pass ─► SelectionDAG/MIR ─► 組合語言
```

想看全部階段：`gcc -O2 -fdump-tree-all -fdump-rtl-all -c misc.c` 在本機產生 176 個 dump 檔。

## 常見最佳化：從組合語言認出它們

把幾種經典最佳化各寫成一個小函式，用 `gcc -O2 -c opts.c` 編譯（以下 objdump 輸出省略 `endbr64` 與 `nop`）：

```c
/* opts.c */
#include <stdio.h>
int ga, gb;

int cf(void)                      /* constant folding / propagation */
{
    int x = 3;
    int y = 4 + x;
    return x + y;
}

int dce(int a)                    /* dead code elimination */
{
    int unused = a * 37;
    if (0)
        puts("never");
    return a;
}

void cse(int x, int y)            /* common subexpression elimination */
{
    ga = (x + y) * 2;
    gb = (x + y) * 3;
}

static int add(int x, int y) { return x + y; }
int sub(int x, int y) { return add(x, -y); }   /* inlining */

void licm(int *a, int n, int k, int m)          /* loop invariant code motion */
{
    for (int i = 0; i < n; i++)
        a[i] = i * (k * m);
}

unsigned div10(unsigned x) { return x / 10; }  /* strength reduction：除法變乘法 */
int mul8(int x) { return x * 8; }
int sdiv8(int x) { return x / 8; }
```

### Constant folding 與 constant propagation

```
-O0:                                     -O2:
<cf>:                                    <cf>:
  push   %rbp                              mov    $0xa,%eax
  mov    %rsp,%rbp                         ret
  movl   $0x3,-0x8(%rbp)      ← x = 3
  mov    -0x8(%rbp),%eax
  add    $0x4,%eax            ← y = 4 + x
  mov    %eax,-0x4(%rbp)
  mov    -0x8(%rbp),%edx
  mov    -0x4(%rbp),%eax
  add    %edx,%eax            ← x + y
  pop    %rbp
  ret
```

**常數傳遞（constant propagation）**把「`x` 的值是 3」傳到每個用到 `x` 的地方；**常數摺疊（constant folding）**把 `4 + 3`、`3 + 7` 在編譯期算掉。兩者交替進行直到不動，結果是 `return 10`。SSA 讓傳遞變得直接：`x1 = 3` 只有一個定義，所有 `x1` 的使用都能換成 3。GCC 與 LLVM 用的是 SCCP（sparse conditional constant propagation），它同時追蹤「哪些分支可能被執行」，因此能傳遞穿越 `if (常數)` 的分支。

### Dead code elimination

`dce` 編成兩條指令：

```
<dce>:
  mov    %edi,%eax
  ret
```

`unused` 的結果沒人用、`if (0)` 的分支不可達，全刪。**無用碼消除（DCE）**在 SSA 上的做法是：從有副作用的指令（store、呼叫、return）出發，反向標記所有被用到的定義，沒被標記的就刪掉。`puts` 的字串常數也一起消失了。

### Common subexpression elimination

```
<cse>:
  add    %esi,%edi               ← t = x + y，只算一次
  lea    (%rdi,%rdi,1),%eax      ← t * 2
  mov    %eax,ga(%rip)
  add    %edi,%eax               ← t * 2 + t = t * 3
  mov    %eax,gb(%rip)
  ret
```

（重定位已代入符號名以利閱讀。）`x + y` 只算一次，這是**共同子運算式消除（CSE）**。更妙的是 `t * 3` 被改寫成「已經算好的 `t * 2` 再加 `t`」——乘法被換成了加法，是下面 strength reduction 的味道。編譯器常把多種最佳化交織在一起，不必強求每段組語只對應一種最佳化。

### Function inlining

```
<sub>:
  mov    %edi,%eax
  sub    %esi,%eax
  ret
```

`add(x, -y)` 被展開到 `sub` 裡，`x + (-y)` 再化簡成 `x - y`。`add` 是 `static`、只被呼叫一次、又已全部 inline，所以 `nm opts.o` 裡根本沒有 `add` 這個符號。**inlining 的價值不只是省掉 call/ret**：展開後，呼叫端的已知資訊（例如常數引數）傳進函式本體，又能觸發一輪常數傳遞與 DCE。代價是程式碼變大、可能傷 I-cache，所以編譯器用成本模型決定要不要展開。

跨檔案的函式，單獨編譯時看不到本體，無法 inline。原文 Page 73 提到的解法是 **LTO（Link Time Optimization）**：

```c
/* lib.c */  int square(int x) { return x * x; }
/* app.c */  int square(int x);
             int main(void) { return square(7) - 49; }
```

```
$ gcc -O2 -c lib.c app.c && gcc -O2 lib.o app.o -o nolto
<main>:
  sub    $0x8,%rsp
  mov    $0x7,%edi
  call   1150 <square>
  add    $0x8,%rsp
  sub    $0x31,%eax
  ret
$ gcc -O2 -flto -c lib.c app.c && gcc -O2 -flto lib.o app.o -o lto
<main>:
  xor    %eax,%eax
  ret
```

`-flto` 讓 `.o` 裡存的是 GIMPLE 而不是（只有）機械碼，連結時 gcc 把所有檔案的 IR 合在一起重新最佳化：`square(7)` 被 inline、摺成 49、`49 - 49` 摺成 0。原文引用 GCC 5 的釋出說明：Firefox 做 LTO 時，相同函式合併（ICF）統一了約 31000 個函式，約一半的虛擬函式呼叫被推測性地去虛擬化。

### Loop invariant code motion

```
-O0（節錄迴圈主體）:                    -O2:
  mov    -0x20(%rbp),%eax   ← k            <licm>:
  imul   -0x24(%rbp),%eax   ← k*m 每圈算     test   %esi,%esi
  mov    -0x4(%rbp),%edx                     jle    75
  movslq %edx,%rdx                           imul   %ecx,%edx      ← k*m 提到迴圈外
  lea    0x0(,%rdx,4),%rcx                   lea    -0x1(%rsi),%eax
  mov    -0x18(%rbp),%rdx                    lea    0x4(%rdi,%rax,4),%rcx  ← 結束位址
  add    %rcx,%rdx          ← &a[i]          xor    %eax,%eax
  imul   -0x4(%rbp),%eax    ← i*(k*m)      68:
  mov    %eax,(%rdx)                         mov    %eax,(%rdi)    ← *p = val
  addl   $0x1,-0x4(%rbp)                     add    $0x4,%rdi      ← p++
  ...                                        add    %edx,%eax      ← val += k*m
                                             cmp    %rcx,%rdi
                                             jne    68
                                             ret
```

`-O2` 版的迴圈裡**一條乘法都沒有**，因為三種最佳化疊在一起：

1. **LICM（迴圈不變量外提）**：`k * m` 每圈都一樣，提到迴圈前算一次。
2. **Strength reduction（強度折減）**：`i * c` 隨 `i` 每次加 1 而增加 `c`，所以改成維護一個累加值 `val += c`，乘法變加法。
3. **歸納變數替換**：`&a[i]` 改成指標 `p` 每圈加 4，迴圈結束條件從 `i < n` 改成 `p != &a[n]`，`i` 本身被刪掉。

LICM 能提出去的前提是「它真的不變」。開頭的 `foo` 正是反例：`*i1 + *i2` 看似不變，但 `*res` 的寫入可能改到它們。

### Strength reduction：除法與乘法

```
<div10>:                          <mul8>:                         <sdiv8>:
  mov    %edi,%eax                  lea    0x0(,%rdi,8),%eax        test   %edi,%edi
  mov    $0xcccccccd,%edx           ret                             lea    0x7(%rdi),%eax
  imul   %rdx,%rax                                                  cmovns %edi,%eax
  shr    $0x23,%rax                                                 sar    $0x3,%eax
  ret                                                               ret
```

- `x / 10` 沒有 `div` 指令，變成乘以 0xcccccccd 再右移 35 位（0x23）。原理：0xcccccccd ≈ 2³⁵ / 10（2³⁵ / 10 = 3435973836.8，上取整為 3435973837 = 0xcccccccd），所以 `x * 0xcccccccd >> 35` ≈ `x / 10`，並且可以證明對所有 32 位元無號 `x` 都精確。整數除法指令的延遲通常是乘法的數倍以上（確切數字依微架構而異，可查 Agner Fog 的指令延遲表），所以值得用一次乘法加一次移位取代。
- `x * 8` 變成 `lea (,%rdi,8)`：用定址單元算位移，不佔 ALU 的移位指令。
- 有號 `x / 8` **不能**只寫 `sar $3`：C 的除法向零截斷（N1570 6.5.5 §6），−1 / 8 必須得 0，但算術右移 −1 >> 3 得 −1（向負無窮取整）。所以 gcc 先判斷正負，負數時加上 7 再移位。原文 Page 67 的 integer mod 最佳化也是同一個道理——`x % y` 在 `y` 為 2 的冪時要為負數補正。

### Vectorization

**向量化（vectorization）**把一次處理一個元素的迴圈，改成用 SIMD 指令一次處理多個：

```c
/* vadd.c */
void vadd(float *restrict a, const float *restrict b,
          const float *restrict c, int n)
{
    for (int i = 0; i < n; i++)
        a[i] = b[i] + c[i];
}
```

```
$ gcc -O3 -c -fopt-info-vec-optimized vadd.c
vadd.c:4:23: optimized: loop vectorized using 16 byte vectors
vadd.c:4:23: optimized: loop vectorized using 8 byte vectors
$ gcc -O2 -c -fopt-info-vec-optimized vadd.c
（沒有輸出：gcc 11 的 -O2 不向量化）
$ objdump -d --no-show-raw-insn vadd.o | grep -E 'addps|addss|movups|movss'
  20:	movups (%rsi,%rax,1),%xmm0
  24:	movups (%rdx,%rax,1),%xmm2
  28:	addps  %xmm2,%xmm0          ← 一次加 4 個 float（16 位元組）
  2b:	movups %xmm0,(%rdi,%rax,1)
  5b:	addps  %xmm1,%xmm0          ← 剩餘元素用 8 位元組向量（2 個 float）
  74:	movss  (%rsi,%rax,4),%xmm0
  79:	addss  (%rdx,%rax,4),%xmm0  ← 最後零頭一次一個（epilogue）
  7e:	movss  %xmm0,(%rdi,%rax,4)
$ gcc -O3 -mavx2 -c vadd.c -o vadd-avx.o && objdump -d vadd-avx.o | grep vaddps | head -1
  2d:	vaddps (%rdx,%rax,1),%ymm1,%ymm0   ← AVX2：一次 8 個 float
```

`ps` 是 packed single（4 個並排的 float），`ss` 是 scalar single。預設的 x86-64 目標只保證 SSE2，所以最多 16 位元組；加 `-mavx2` 才敢用 256 位元的 ymm 暫存器——代價是執行檔在沒有 AVX2 的 CPU 上會因非法指令當掉。

拿掉 `restrict` 會怎樣？

```
$ gcc -O3 -c -fopt-info-vec-all vadd-alias.c 2>&1 | grep -iE 'alias|version'
vadd-alias.c:4:23: optimized:  loop versioned for vectorization because of possible aliasing
```

gcc 仍然向量化，但產生兩個版本的迴圈，開頭插入執行期檢查：若 `a` 與 `b`、`c` 的距離夠遠（不重疊）就跑向量版，否則跑純量版。這叫 **loop versioning**，代價是多出的檢查與程式碼體積。`restrict`（N1570 6.7.3.1）是程式設計師對編譯器的承諾：「透過這個指標存取的物件，不會經由其他指標存取」，編譯器因此可以省掉檢查。

版本差異要特別注意：GCC 12 起 `-O2` 預設開啟成本模型很保守的向量化（`-fvect-cost-model=very-cheap`），clang 則早在 `-O2` 就開啟 loop vectorizer。本章在 gcc 11 上看到的「`-O2` 不向量化」**不適用於新版 gcc**。

> SIMD 指令與向量化友善的寫法見 [`../c_interview/25-simd-vectorization.md`](../c_interview/25-simd-vectorization.md)。

### 其他：原文列出的分支與存取模式最佳化

同樣用 `gcc -O2` 驗證原文 Page 67 的幾個例子：

```c
static int s_unused = 1;          /* 沒人用的 static */
int g_unused = 2;                 /* 沒人用的外部連結變數 */
int inc_if_neg(int a, int b) { if (b < 0) a++; return a; }
int narrow(unsigned short s) { return (s >> 20) + (s > 0x10000) + (s == -1); }
int mod16(int x) { return x % 16; }
void hello(void) { printf("hi\n"); }
```

```
<inc_if_neg>:                      <narrow>:              <mod16>:
  mov    %edi,%eax                   xor    %eax,%eax       mov    %edi,%edx
  cmp    $0x80000000,%esi            ret                    sar    $0x1f,%edx     ← 負數得 -1，否則 0
  sbb    $0xffffffff,%eax                                   shr    $0x1c,%edx     ← 負數得 15，否則 0
  ret                                                       lea    (%rdi,%rdx,1),%eax
                                                            and    $0xf,%eax
$ nm misc.o | grep unused                                   sub    %edx,%eax
0000000000000000 D g_unused                                 ret
```

- **無分支化**：原文把 `if (b < 0) a++;` 改寫成 `a -= b >> 31;`（依賴有號右移是算術位移，這在 C 是 implementation-defined，N1570 6.5.7 §5）。gcc 用了另一招：`cmp $0x80000000, %esi` 以無號比較設定進位旗標 CF（`b` 非負時 CF=1），`sbb $-1, %eax` 算 `a - (-1) - CF = a + 1 - CF`，`b` 為負時 CF=0、結果 `a + 1`。完全沒有跳躍，也不依賴 implementation-defined 行為。
- **Narrowing / 值域分析**：`unsigned short` 的值域是 0–65535，右移 20 位必為 0、不可能大於 0x10000、提升成 `int` 後不可能等於 −1，整個函式摺成 `return 0`。
- **取模**：`x % 16` 用位元運算加上負數補正，避開 `idiv`。
- **未使用的變數**：`s_unused` 消失，`g_unused` 留著（`D`）。原文 Page 69：編譯器看不到其他轉譯單元會不會用 `g_unused`，只能保留；`static` 等於告訴編譯器「只有這個檔案看得到」，它才敢刪。**這是 local 函式與變數應該宣告成 `static` 的實際理由之一**。
- **`printf` → `puts`**：`hello` 最後是 `jmp puts`（tail call，連 `call`/`ret` 都省了）。加 `-fno-builtin` 後變成 `jmp __printf_chk`——不是 `printf`，因為 Ubuntu 的 gcc 在 `-O2` 預設定義 `_FORTIFY_SOURCE`，glibc 的標頭把 `printf` 導向會做額外檢查的 `__printf_chk`。

### Peephole：最後一道以管窺天

原文花了一段講 peephole optimization 的典故：Coventry 的 Godiva 夫人裸身騎馬遊街，偷看的裁縫 Tom 成了「peeping Tom」，門上的窺孔叫 peephole。McKeeman 在 1965 年的 CACM 論文用這個詞描述「透過一個只看得到幾條指令的小窗口，反覆套用局部改寫規則」：刪掉多餘的 load/store、把 `x * 1`、`x + 0` 刪掉、把 `x * 2` 換成移位、利用特殊指令（例如上面的 `sbb` 技巧）。單看每條規則收益很小，但它在最後階段反覆套用，累積效果可觀。原文提醒一個前提：改寫的指令序列中間不能有標籤（別處可能跳進來），也就是必須在同一個基本區塊內。

## `-O0`／`-O1`／`-O2`／`-O3`／`-Os` 實際差在哪

先看各等級開了多少最佳化旗標：

```
$ for o in O0 O1 O2 O3 Os; do echo "-$o: $(gcc -Q --help=optimizers -$o | grep -c '\[enabled\]') enabled"; done
-O0: 58 enabled
-O1: 97 enabled
-O2: 140 enabled
-O3: 155 enabled
-Os: 135 enabled
```

`-O0` 也有 58 個「enabled」，但要小心解讀：其中一部分是語意旗標（`-fmath-errno`、`-fsigned-zeros`、`-ftrapping-math` 規定浮點運算要保留哪些行為），一部分是安全強化（`-fstack-protector-strong`），還有 `-fdce`、`-ftree-loop-im` 這類名字像最佳化的旗標——GCC 手冊〈Optimize Options〉說明，在不開最佳化時大多數最佳化 pass 根本不會執行，即使旗標顯示為 enabled。`-O3` 比 `-O2` 多出的 15 個（本機 `diff` 結果）包括 `-ftree-loop-vectorize`、`-ftree-slp-vectorize`、`-funswitch-loops`、`-fpeel-loops`、`-fipa-cp-clone`、`-ftree-loop-distribution` 等，幾乎全是**以程式碼體積換速度**的迴圈轉換。原文 Page 67 的 loop unswitching（把迴圈內不變的 `if` 提到外面、複製出兩個迴圈）就是 `-funswitch-loops`。

用一個平方和核心量測（`sum_sq` 加上 `noinline`，每輪改一個元素避免整個迴圈被當成不變量）：

```c
__attribute__((noinline))
unsigned sum_sq(const unsigned *a, int n)
{
    unsigned s = 0;
    for (int i = 0; i < n; i++)
        s += a[i] * a[i];
    return s;
}
```

N = 4096、重複 200000 次，`taskset -c 3` 綁核、各跑 3 次取最佳值（完整程式見本章「動手練習」1）：

| 等級 | `sum_sq` 大小 | 時間（best of 3） | 備註 |
|---|---:|---:|---|
| `-O0` | 98 B | 1149.9 ms | 每個變數每次都讀寫 stack |
| `-O1` | 50 B | 247.9 ms | 變數進暫存器，快 4.6 倍 |
| `-O2` | 55 B | 242.6 ms | 與 `-O1` 差異在誤差內 |
| `-O3` | 223 B | 103.3 ms | 向量化（`movdqu`/`pmuludq`/`pshufd`/`paddd`），體積變 4 倍 |
| `-Os` | 31 B | 255.0 ms | 最小，速度接近 `-O1` |

量測環境是 WSL2 上的 AMD Ryzen 7 5700G，背景有其他負載，數字只代表**這個核心、這台機器**；四個等級的計算結果（`acc`）完全相同。可以讀出的結論：

1. `-O0` → `-O1` 是最大的一步，主要來自暫存器配置（不再每次存取 stack）。除錯時用 `-O0`、出貨時至少 `-O1`。
2. `-O2` 相對 `-O1` 在這種簡單迴圈上沒有差別；它的收益在有大量函式呼叫、分支、別名分析機會的真實程式上。
3. `-O3` 的向量化讓這個核心快 2.3 倍，代價是 4 倍的程式碼。預設目標只有 SSE2，而 SSE2 沒有 32 位元的逐元素乘法（`pmulld` 要 SSE4.1），所以 gcc 用 `pmuludq` 加 `pshufd` 拼出四個 32 位元乘積——這也是向量版不只快 4 倍的原因之一（另一個原因是記憶體頻寬，本章沒有分開量測）。在 I-cache 吃緊的大型程式（瀏覽器、資料庫）裡，`-O3` 不一定比 `-O2` 快，有時更慢；Linux 核心預設以 `-O2` 建置，改用 `-O3` 的提案在社群裡一直有爭議。
4. `-Os` 適合 Flash 有限的嵌入式系統，它基本上是 `-O2` 去掉會讓程式變大的最佳化。

## 最佳化的限制：三道牆

### 第一道牆：aliasing

回到開頭的 `foo`。加上 `restrict` 承諾三個指標互不重疊：

```c
void foo_r(int *restrict i1, int *restrict i2, int *restrict res)
{
    for (int i = 0; i < 10; i++)
        *res += *i1 + *i2;
}
```

```
<foo_r>:
  mov    (%rsi),%eax
  add    (%rdi),%eax              ← t = *i1 + *i2，只讀一次
  lea    (%rax,%rax,8),%ecx       ← t * 9
  add    %ecx,%eax                ← t * 10
  add    %eax,(%rdx)              ← *res += t * 10，只寫一次
  ret
```

迴圈完全消失，`10 * t` 還用 `lea` 拆成 `t*9 + t`。但如果呼叫端違背承諾：

```c
int v = 1, w = 1;
foo(&v, &v, &v);
foo_r(&w, &w, &w);     /* 違反 restrict 的承諾：UB */
printf("foo: %d  foo_r: %d\n", v, w);
```

```
$ gcc -O2 alias.c alias_main.c -o alias && ./alias
foo: 59049  foo_r: 21
$ gcc -O0 alias.c alias_main.c -o alias0 && ./alias0
foo: 59049  foo_r: 59049
```

`foo` 的正確答案是 3¹⁰ = 59049（每圈 `v = v + v + v`）。`foo_r` 在 `-O0` 碰巧也是 59049，`-O2` 卻是 1 + 10 × 2 = 21。**`restrict` 是單向的承諾，編譯器不檢查**；說謊的代價是靜默的錯誤結果。

另一種 aliasing 限制來自型別。N1570 6.5 §7（俗稱 strict aliasing rule）規定，物件只能透過相容型別、對應的有號/無號型別、字元型別等左值存取。於是編譯器可以假設 `int *` 和 `float *` **不會指向同一個物件**：

```c
__attribute__((noinline))
int f(int *ip, float *fp)
{
    *ip = 1;
    *fp = 0.0f;          /* 若 fp 與 ip 指向同一塊，這裡把它改成 0 */
    return *ip;          /* 編譯器：int 和 float 不會 alias，直接回 1 */
}
/* main 裡：int x; printf("%d\n", f(&x, (float *) &x)); */
```

```
$ gcc -O0 -o strict strict.c && ./strict
0
$ gcc -O2 -o strict strict.c && ./strict
1
$ gcc -O2 -fno-strict-aliasing -o strict strict.c && ./strict
0
f:
	movl	$1, (%rdi)
	movl	$1, %eax            ← 不重讀 *ip，直接回 1
	movl	$0x00000000, (%rsi)
	ret
```

`-O2` 的 gcc 甚至把 `*fp = 0` 排到 `return` 值設定之後。Linux 核心以 `-fno-strict-aliasing` 編譯，正是因為核心裡大量用不同型別的指標看同一塊記憶體；代價是放棄這類最佳化。正確的 type punning 寫法（`memcpy`、union）在 [`../c_interview/07-type-punning-aliasing.md`](../c_interview/07-type-punning-aliasing.md)。

### 第二道牆：volatile

```c
int plain;
volatile int mmio;
void w_plain(void) { plain = 1; plain = 2; }
void w_vol(void)   { mmio = 1; mmio = 2; }
int r_plain(void)  { return plain + plain; }
int r_vol(void)    { return mmio + mmio; }
```

```
<w_plain>:                         <w_vol>:
  movl   $0x2,plain(%rip)            movl   $0x1,mmio(%rip)
  ret                                movl   $0x2,mmio(%rip)
                                     ret
<r_plain>:                         <r_vol>:
  mov    plain(%rip),%eax            mov    mmio(%rip),%eax
  add    %eax,%eax                   mov    mmio(%rip),%edx
  ret                                add    %edx,%eax
                                     ret
```

普通變數的第一次寫入被刪掉（dead store elimination），兩次讀合併成一次；`volatile` 的存取一次不少、順序不變。N1570 5.1.2.3 §6 規定實作只需保證**可觀察行為（observable behavior）**與抽象機器一致，而可觀察行為只有三項：volatile 物件的存取、程式結束時寫入檔案的資料、互動裝置的輸入輸出。除此之外的任何計算，編譯器想怎麼改都行。volatile 就是讓一個存取「變成可觀察」的開關，適用於記憶體映射 I/O（MMIO）暫存器、被 signal handler 修改的 `volatile sig_atomic_t`、`setjmp` 後要保值的區域變數。

**volatile 不是執行緒同步工具**：它不保證原子性、不產生記憶體屏障，也不限制**非** volatile 存取在它前後的重排。Ch 36 `spin.c` 若改用 `volatile int` 能「碰巧」跑對，但正確的工具是 C11 atomics。

> volatile 的面試向整理見 [`../c_interview/10-volatile-sequence-point.md`](../c_interview/10-volatile-sequence-point.md)。

### 第三道牆（反向的牆）：未定義行為

前兩道牆擋住最佳化；UB 則是把門打開。回到開頭的 `gt_plus1`，有一個更意外的細節：

```c
int gt_plus1(int x) { return x + 1 > x; }      /* ub.c：寫在同一個運算式裡 */
```

```
$ gcc -O0 -o ub ub.c && ./ub
gt_plus1(INT_MAX) = 1
$ gcc -O0 -S -o - ub.c
gt_plus1:
	pushq	%rbp
	movq	%rsp, %rbp
	movl	%edi, -4(%rbp)
	movl	$1, %eax          ← -O0 也直接回 1
	popq	%rbp
	ret
$ gcc -O2 -fwrapv -o ub ub.c && ./ub
gt_plus1(INT_MAX) = 0
```

**`-O0` 也回傳 1**。gcc 前端的 `fold-const` 在建立運算式樹時就把 `x + 1 > x` 摺成真，不管最佳化等級。拆成兩個敘述的 `ub2.c`（`int y = x + 1; return y > x;`）在 `-O0` 才回 0。更陰險的是 UBSan：

```
$ gcc -O0 -fsanitize=undefined -o ubs0 ub.c && ./ubs0
gt_plus1(INT_MAX) = 1                     ← 沒有任何報告！
$ gcc -O0 -fsanitize=undefined -o ub2s ub2.c && ./ub2s
ub2.c:3:53: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'
gt_plus1(INT_MAX) = 0
```

運算在插入檢查之前就被摺掉了，sanitizer 無從檢查。教訓：**`-O0` 不等於「照字面翻譯」，sanitizer 也不是萬能**。`-fwrapv` 讓 gcc 把有號溢位定義為二補數環繞，代價是失去這類推理（例如迴圈計數器不會溢位的假設，會影響向量化）。

為什麼編譯器要利用 UB？因為「有號整數不會溢位」讓它能證明 `for (int i = 0; i < n; i++)` 恰好執行 n 次、`a[i]` 的位址單調遞增——這些事實是向量化與強度折減的前提。Wang 等人在 SOSP 2013 的論文量化了編譯器利用 UB 刪掉安全檢查的真實案例；[Ch 22](./22-integer-overflow.md) 與 [Ch 34](./34-undefined-behavior.md) 有完整討論。

## JIT 實作案例概述

到目前為止的最佳化都在**執行前**（ahead-of-time, AOT）完成。**及時編譯（just-in-time compilation, JIT）**則在程式執行期間才產生機械碼。原文提醒 just-in-time 譯「及時」而非「即時」：real-time（即時）系統的正確性包含截止時間，JIT 不提供這種保證，編譯本身就花時間。

JIT 的優勢是能用執行期才知道的資訊最佳化：哪些分支實際上從不走、某個虛擬呼叫的接收者實際上總是同一型別、某個參數實際上總是常數。HotSpot JVM、V8、LuaJIT 這類系統通常分層：先直譯（啟動快），對熱點程式碼再用越來越激進的最佳化重新編譯；假設失效時（例如出現新型別）退回直譯器，稱為 deoptimization。Android 手機就同時內建了多個這類執行引擎，原文因此說「編譯器無所不在」。

原文列出的教學用 JIT 專案：

| 專案 | 語言 | 特色 |
|---|---|---|
| [jit-construct](https://github.com/jserv/jit-construct) | Brainfuck | 直譯器 → 編譯器 → 以 DynASM 產生機械碼的 JIT，三步對照 |
| [rubi](https://github.com/embedded2015/rubi) | 類 Ruby | 小型 JIT 編譯器，直接產生 x86 機械碼 |
| [AMaCC](https://github.com/jserv/amacc) | C 子集 | 從 c4 衍生的小型 C 編譯器，可產生 Arm ELF 或在執行期 JIT |

JIT 的核心機制只有兩步：把機械碼位元組寫進一塊記憶體，再把那塊記憶體改成可執行並跳過去。下一章會親手用 `mmap` 與 `mprotect` 寫一個 Brainfuck JIT，並量測它比直譯器快多少。

## 對比與取捨

| 等級 / 手段 | 執行速度 | 程式碼大小 | 編譯時間 | 除錯體驗 | 適用 |
|---|---|---|---|---|---|
| `-O0` | 最慢 | 中 | 最快 | 最好（變數都在 stack） | 開發除錯 |
| `-Og` | 接近 `-O1` | 中 | 快 | 好（保留除錯可見性） | 需要一定速度的除錯 |
| `-O1`／`-O2` | 快 | 小～中 | 中 | 變數可能 `<optimized out>` | 一般出貨（多數發行版用 `-O2`） |
| `-O3` | 迴圈密集時最快 | 大 | 慢 | 差 | 數值運算、熱點迴圈 |
| `-Os`／`-Oz` | 中 | 最小 | 中 | 差 | 嵌入式、Flash 有限 |
| `-flto` | 跨檔案 inline 帶來提升 | 視情況 | 連結時間大增 | 差 | 大型程式；收益依程式而異，需自行量測 |
| `restrict` | 解開 aliasing 限制 | — | — | — | 確定不重疊的熱點函式；說謊即 UB |

## 踩雷集錦

1. **「`-O0` 就是逐字翻譯」**：gcc 前端在 `-O0` 仍會做常數摺疊與部分化簡（`x + 1 > x` 直接變 1）。想看「真正逐字」的對應，只能接受它是近似。
2. **「編譯器會幫我把不變的運算提出迴圈」**：前提是編譯器能**證明**它不變。只要迴圈裡有透過指標的寫入、或呼叫了看不到本體的函式，透過指標的讀取就可能被重新讀取。熱點迴圈裡把 `*p` 先讀進區域變數，往往比期待編譯器更可靠。
3. **「加 `restrict`／改 `-fno-strict-aliasing` 只影響速度」**：`restrict` 寫錯是 UB，結果靜默出錯（`foo_r` 回 21）；反過來，程式依賴 type punning 卻沒加 `-fno-strict-aliasing`，`-O2` 會「莫名其妙」算錯（`strict.c` 回 1）。
4. **「volatile 可以拿來做執行緒同步」**：它只保證該存取本身不被刪除或合併，不保證原子性與記憶體順序。跨執行緒用 `<stdatomic.h>`。
5. **「`-O3` 一定比 `-O2` 快」**：本章的迴圈快了 2.3 倍，但體積大 4 倍。大型程式的 I-cache 壓力可能抵銷甚至反轉收益，要量測才算數。
6. **「UBSan 開了就能抓到所有溢位」**：在插入檢查前就被摺掉的運算式，UBSan 看不到（`ub.c`）。動態工具只檢查「實際執行、且仍存在」的程式碼。
7. **「這個最佳化 gcc 沒做，所以編譯器做不到」**：clang 14 把 `sum_to` 變成公式，gcc 11 沒有；gcc 12 起 `-O2` 會向量化，gcc 11 不會。結論要附上編譯器與版本。

## 進階：再往深一層

- **Missed optimizations**：原文推薦的 [gergo-/missed-optimizations](https://github.com/gergo-/missed-optimizations) 收集了 gcc/clang 該做沒做的案例。對照 [Compiler Explorer](https://godbolt.org/) 換版本看哪些已被修正，是理解最佳化器邊界的好練習。
- **Pass 順序（phase ordering）**：每個 pass 單獨看都正確，效果卻取決於前面的 pass 把 IR 整理到什麼程度。下一章 shecc 的 LICM 就有一個真實案例；GCC 與 LLVM 以多次重跑相同 pass 緩解，代價是編譯時間。
- **最佳化的正確性驗證**：[Alive2](https://alive2.llvm.org/) 能以 SMT solver 自動檢查一個 LLVM IR 轉換是否保持語意，在 LLVM 的最佳化器中找出過許多錯誤（Lopes 等人，PLDI 2021）。本 repo [`../../compilers/ssa_optimizations/32-testing-alive2.md`](../../compilers/ssa_optimizations/32-testing-alive2.md) 有專章。
- **Profile-guided optimization（PGO）**：`-fprofile-generate` 收集執行剖析、`-fprofile-use` 重新編譯，讓編譯器知道真實的分支機率與熱點。這相當於把 JIT 的優勢搬回 AOT。
- **觀察 LLVM 的每一個 pass**：`clang -O2 -mllvm -print-after-all` 會在每個 pass 後印出 IR，適合追「某個最佳化到底是哪個 pass 做的」。

## 動手練習

1. 完整的量測程式：把 `sum_sq` 包進 `main`（`static unsigned a[4096]` 以 `rand()` 初始化，迴圈 200000 次、每次 `a[r % 4096] ^= 1` 後累加 `sum_sq(a, 4096)`，用 `clock_gettime(CLOCK_MONOTONIC)` 計時），在你的機器上重做五個等級的表格，並用 `objdump -d` 找出 `-O3` 版用了哪些 SIMD 指令。
2. 把 `licm` 改成 `a[i] = i * (*kp)`（`kp` 是指標參數），觀察 `-O2` 還能不能把乘法提出迴圈；再把參數改成 `const int *restrict kp` 看看。解釋差別。
3. 對 `sel.c` 依序執行 `opt-14 -S -mem2reg`、`-instcombine`、`-simplifycfg`，每步之後看 IR 的變化，找出是哪個 pass 把分支變成 `select`。
4. 把 `div10` 改成除以 7，看 gcc 用了什麼魔術數字與幾次移位；推導這個數字為什麼對所有 32 位元無號整數都正確（提示：Hacker's Delight 第 10 章）。
5. 寫一個 `for (int i = 0; i <= n; i++)` 和一個 `for (unsigned i = 0; i <= n; i++)` 的迴圈，比較 `-O3` 下兩者是否都能向量化，用 `-fopt-info-vec-missed` 看 gcc 給的理由，並解釋有號溢位 UB 在其中扮演的角色。

## 本章重點整理

- 編譯器分前端、中端（IR + 最佳化）、後端；IR 讓最佳化只寫一次就能服務所有語言與機器。
- SSA 讓每個值只有一個定義點，合流處用 φ 選值；clang 先用 `alloca` 再以 mem2reg 升成 SSA，gcc 以 GIMPLE SSA（`# x_1 = PHI <...>`）表示，最後轉 RTL 交給後端。
- 常見最佳化在組語留下可辨識的痕跡：常數直接出現、無用運算消失、共同運算只算一次、小函式被展開、迴圈內的乘法變加法、除法變乘法加移位、SIMD 指令。
- 最佳化的邊界由規格書劃定：aliasing 讓編譯器保守、`restrict` 與 strict aliasing 讓它激進；volatile 讓存取變成可觀察行為；UB 讓編譯器可以假設「不會發生」。
- 最佳化等級是取捨：`-O0`→`-O1` 收益最大，`-O3` 用體積換迴圈速度，`-Os` 用速度換體積；結論必須附上編譯器版本與量測條件。

## 自我檢核

- [ ] 不看筆記，畫出 `if (c) x = 1; else x = 2; return x;` 的 CFG 與 SSA 形式，標出 φ 節點，並說明 φ 最後怎麼變成機械碼。
- [ ] 同事問：「`foo` 裡 `*i1 + *i2` 明明不變，為什麼 gcc 不提出迴圈？」你能用一個具體的呼叫方式說服他，並給出兩種讓編譯器能最佳化的改法嗎？
- [ ] 看到一段迴圈裡沒有乘法、只有 `add` 與指標遞增的組合語言，你能推回原始 C 大概長什麼樣、是哪幾種最佳化疊出來的嗎？
- [ ] 能解釋為什麼 `static int s_unused` 會被刪而 `int g_unused` 不會，以及這和 LTO 有什麼關係嗎？
- [ ] 如果有人說「我的程式在 `-O0` 正確、`-O2` 錯誤，所以是 gcc 的 bug」，你會優先懷疑哪三類原因？各用什麼工具或旗標驗證？
- [ ] 能說出 volatile 保證了什麼、**沒有**保證什麼，並舉出一個 volatile 正確用途與一個錯誤用途嗎？

## 延伸閱讀

### 論文

- **[Efficiently Computing Static Single Assignment Form and the Control Dependence Graph](https://dl.acm.org/doi/10.1145/115372.115320)** — Cytron, Ferrante, Rosen, Wegman, Zadeck，ACM TOPLAS（1991）
  - **核心貢獻**：以支配邊界（dominance frontier）決定 φ 的插入位置，讓 SSA 的建構在實務上可行，是今天 GCC 與 LLVM SSA 建構的理論基礎
  - **讀哪裡**：Section 1–2（動機與定義）、Section 5（φ 插入）；支配邊界的演算法細節可先跳過
  - **和本章的關聯**：本章觀察到的 PHI 節點，就是這篇演算法放進去的
- **[LLVM: A Compilation Framework for Lifelong Program Analysis & Transformation](https://llvm.org/pubs/2004-01-30-CGO-LLVM.html)** — Chris Lattner, Vikram Adve，CGO（2004）
  - **核心貢獻**：提出以型別化 SSA IR 為核心、可在編譯期、連結期、執行期持續最佳化的框架
  - **讀哪裡**：Section 2（LLVM IR 的設計）；後面的實驗數據可略讀
  - **和本章的關聯**：解釋了本章 `clang -emit-llvm` 看到的 IR 為什麼長這樣，以及 LTO 為什麼在 LLVM 裡很自然
- **[Towards Optimization-Safe Systems: Analyzing the Impact of Undefined Behavior](https://dl.acm.org/doi/10.1145/2517349.2522728)** — Xi Wang, Nickolai Zeldovich, M. Frans Kaashoek, Armando Solar-Lezama，SOSP（2013）
  - **核心貢獻**：系統化整理編譯器利用 UB 刪除程式碼的模式，並以工具 STACK 在真實專案中找出被刪掉的安全檢查
  - **讀哪裡**：Section 2（UB 與最佳化的案例）
  - **和本章的關聯**：本章「第三道牆」的大規模實證版

### 部落格 / 技術文章

- **[What Every C Programmer Should Know About Undefined Behavior](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know.html)** — Chris Lattner（LLVM Blog，2011，三篇系列）
  - **這篇說什麼**：從編譯器作者角度解釋 UB 讓哪些最佳化成為可能（有號溢位、strict aliasing、空指標），以及為什麼編譯器很難對 UB 發出警告
  - **讀哪裡**：第 1 篇的「Advantages of Undefined Behavior in C」與第 2 篇
  - **為什麼值得讀**：作者是 LLVM 的創造者；`nsw` 旗標的設計動機在這裡講得最清楚
- **[GCC Internals：Tree SSA](https://gcc.gnu.org/onlinedocs/gccint/Tree-SSA.html) 與 [RTL](https://gcc.gnu.org/onlinedocs/gccint/RTL.html)**
  - **這篇說什麼**：GIMPLE、SSA 形式、PHI 節點與 RTL 運算式的官方說明
  - **讀哪裡**：Tree SSA 的「SSA」小節（原文引用的 "program variables are assigned in exactly one location" 出自這裡）；RTL 的「RTL Objects」與「Machine Modes」
  - **為什麼值得讀**：讀懂 `-fdump-tree-*`、`-fdump-rtl-*` 輸出的權威參考
- **[Missed optimizations in C compilers](https://github.com/gergo-/missed-optimizations)** — Gergö Barany
  - **這篇說什麼**：一系列 gcc/clang 沒做到的最佳化實例，各附最小重現程式
  - **讀哪裡**：README 中任選幾則，到 Compiler Explorer 用新版編譯器重試
  - **為什麼值得讀**：作者是編譯器測試研究者；看「做不到什麼」比看「做得到什麼」更能理解最佳化器的推理方式

### 書籍

- **《Engineering a Compiler》3rd ed.** — Keith Cooper, Linda Torczon（Morgan Kaufmann，2022）
  - **這本書的定位**：比 Dragon Book 更偏實作與最佳化的現代教科書
  - **讀哪幾章**：中間表示（IR）一章、資料流分析與 SSA 建構一章、以及區域／全域／迴圈最佳化各章（各版章號略有不同，以目錄為準）
- **《SSA-based Compiler Design》** — Fabrice Rastello, Florent Bouchez Tichadou 編（Springer，2022；原文引用的「Static Single Assignment Book」是它的早期線上草稿）
  - **這本書的定位**：SSA 的百科全書，多位研究者各寫一章
  - **讀哪幾章**：Part I 前三章（SSA 的基本性質與建構）；之後依需要查閱
- **《Hacker's Delight》2nd ed.** — Henry S. Warren（Addison-Wesley，2012）
  - **這本書的定位**：位元技巧與整數算術最佳化的經典
  - **讀哪幾章**：第 10 章（除以常數），本章 `div10` 魔術數字的完整推導

### 相關課程

- [`../../compilers/ssa_optimizations/`](../../compilers/ssa_optimizations/README.md)：SSA 建構、SCCP、GVN、LICM、SCEV、向量化的專門課
- [`../c_interview/18-compiler-optimization.md`](../c_interview/18-compiler-optimization.md)：最佳化相關的面試題整理

看懂了工業編譯器做什麼，下一章反過來動手：從八個指令的 Brainfuck 寫起直譯器、編譯器與 JIT，再讀 c4 與 shecc，看一個能編譯自己的 C 編譯器最少需要什麼。

→ [Ch 38 編譯器實作：Brainfuck、c4 與 shecc](./38-compiler-construction.md)
