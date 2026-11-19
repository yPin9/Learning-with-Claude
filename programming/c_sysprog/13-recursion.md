# Ch 13 — 遞迴與尾端遞迴最佳化

> **目標**：用實測的 stack 用量與 `gcc -O2` 的組語，看清楚遞迴到底花掉多少 stack、什麼情況下編譯器會把它變成迴圈、什麼情況不會，以及 stack overflow 在核心與 sanitizer 眼中長什麼樣。

> **對應原文**：[你所不知道的 C 語言：函式呼叫篇](https://hackmd.io/@sysprog/c-function) 的「遞迴呼叫」「針對尾端遞迴的編譯器最佳化」「案例分析」「從遞迴觀察函式呼叫」等節

> **環境**：gcc 11.4、clang 14、gdb 12.1，WSL2 Ubuntu 22.04 x86-64；`ulimit -s` 為 8192（KiB）。tail call 的 `musttail` 另以 Docker 的 `gcc:14`（14.4.0）與 `gcc:15`（15.3.0）驗證。

## 為什麼需要這個？

「遞迴很優雅但很慢、又會爆 stack」是流傳很廣的說法。這句話對一半：

- 對的一半：每一層遞迴都是一次真正的函式呼叫，要 push 返回位址、建立 frame，深度受限於 stack 大小。
- 錯的一半：編譯器很擅長把某一類遞迴改寫成迴圈，`-O2` 下遞迴版本可以和迴圈版本產生**同樣的**機械碼。

要判斷自己的遞迴屬於哪一半，就得知道兩件事：一層遞迴到底吃多少 bytes（[Ch 11](./11-process-layout-and-abi.md) 的 frame 圖），以及編譯器什麼時候願意把 `call` 換成 `jmp`。這章把這兩件事全部量出來，不靠猜。

歷史上，遞迴進入主流程式語言並不平順：FORTRAN 早期版本不允許遞迴（子程式的返回位址和區域變數放在固定位置，重入就會互相覆蓋）；ALGOL 60 明確支援遞迴，也因此需要 stack 這種資料結構，這是現代 call stack 的源頭之一。C 的設計從一開始就假設 stack 存在，所以遞迴是標準特徵。

## 先建立直覺

遞迴就是 **「呼叫自己」造成的一條 stack 上的鏈**，每層都在等下一層的答案：

```
 sum(3)  ───等待──▶  sum(2)  ───等待──▶  sum(1)  ───等待──▶  sum(0)
   │                   │                   │                   │
   │                   │                   │                   └─ 回傳 0
   │                   │                   └─ 回傳 1 + 0 = 1
   │                   └─ 回傳 2 + 1 = 3
   └─ 回傳 3 + 3 = 6

 stack：[sum(3)][sum(2)][sum(1)][sum(0)]   ← 最深時有 4 個 frame 同時存在
```

關鍵：**每一層「回來之後還有事要做」（那個 `n + ...`）**，所以它的 frame 不能丟。如果回來之後什麼事都不用做（直接把答案傳上去），那這一層的 frame 就沒有存在的必要，這就是**尾端遞迴**（tail recursion）的核心想法。

> 如果你對 frame 的結構（返回位址、saved rbp）不熟，先回看 [Ch 11 — 用 gdb 逐步看一次呼叫](./11-process-layout-and-abi.md)。

## 遞迴的 stack 用量：用數字說話

### 一層遞迴吃多少 bytes？

`depth.c` 每層把深度寫進全域變數，崩潰後在 gdb 讀出最後的深度；用 `FRAME_PAD` 控制區域陣列大小來改變 frame：

```c
__attribute__((noinline)) long recurse(long n)
{
    volatile char pad[FRAME_PAD + 1];
    pad[0] = (char)n;
    depth = n;
    last_addr = &pad[0];
    return recurse(n + 1) + pad[0];     /* 返回後還要 + pad[0]：不是 tail call */
}
```

編譯加 `-fstack-usage`（輸出每個函式的 frame 大小到 `.su` 檔）、關掉 canary 與 CET 以排除干擾：

```
$ gcc -O0 -DFRAME_PAD=0   -fno-stack-protector -fcf-protection=none -fstack-usage -c depth.c
depth.c:12:32:recurse	48	static
（FRAME_PAD=48 → 96；FRAME_PAD=240 → 288）
```

`gcc -O0` 的組語是 `push rbp; mov rbp,rsp; sub rsp,32 ...`：32 bytes 區域空間 + 8 bytes saved rbp + 8 bytes 返回位址 = 48 bytes。在 gdb 裡跑到崩潰：

```
FRAME_PAD=0:   crash at depth=174683      ← 8 MiB / 48  = 174762（差 79 層）
FRAME_PAD=48:  crash at depth=87341       ← 8 MiB / 96  =  87381
FRAME_PAD=240: crash at depth=29112       ← 8 MiB / 288 =  29127
```

實測深度與 `stack 大小 ÷ frame 大小` 的預測誤差不到 0.05%，剩下的零頭是 `main` 之前就佔用的 stack（`_start`、`argv`、環境變數、auxiliary vector，約數 KB）。**這個公式夠準：遞迴最大深度 ≈ `ulimit -s` ÷ 每層 frame 大小。**

`ulimit -s` 改變上限，崩潰深度等比例變動（`stackovf.c`，每層 frame 約 112 bytes）：

```
ulimit -s 1024  → 深度 9258
ulimit -s 8192  → 深度 74853
ulimit -s 16384 → 深度 149691
```

### 原文的謎題：`p(i, N)`

原文有個用短路求值寫的遞迴：

```c
int p(int i, int N)
{
    return (i < N && printf("%d\n", i) && !p(i + 1, N))
           || printf("%d\n", i);
}
```

它印出 `i → N → i` 的數列，N 只出現一次。實測（`p 0 5`）：

```
0 1 2 3 4 5 4 3 2 1 0
```

拆解方法：`&&` 與 `||` 是**短路**（左邊決定結果就不求值右邊，且有 sequence point，N1570 6.5.13p4、6.5.14p4）。`i < N` 成立時先 `printf` 印 `i`（`printf` 回傳印出的字元數，必然非 0，所以條件繼續成立），再遞迴；遞迴回來後，`!p(...)` 把 `p` 的非 0 回傳值反轉成 0，讓 `&&` 整體為假，於是輪到 `||` 右邊的 `printf` 再印一次 `i`。當 `i == N`，`i < N` 為假，直接走 `||` 右邊，印出 `N`。

用這個程式量 stack 上限（`main` 呼叫 `p(0, INT_MAX)`，`setvbuf` 設不緩衝以讓崩潰前最後一行準確）：

```
-O0: frame 32 bytes（-fstack-usage），崩潰前最後印出的 i = 261602   （8 MiB / 32 = 262144）
-O2: 最後 i = 261675        （-O2 把 push 了三個 callee-saved 暫存器，frame 同樣是 32 bytes）
-O3: 最後 i = 348841        （frame 縮到約 24 bytes：8 MiB / 348841 ≈ 24.05）
```

原文以 `-O0` 推算每層 32 bytes（8 返回位址 + 8 saved rbp + 8 區域副本 + 8 對齊填充），上限 262144 層，**和實測吻合**。注意 `INT_MAX` 的 `i + 1` 溢位問題在這裡不會發生：在 `i == INT_MAX` 之前 stack 早就爆了，而且 `i < N` 為假時根本不會算 `i + 1`。

## 尾端遞迴：編譯器的魔法

### 定義

函式的**最後一個動作**是呼叫（通常是自己），並且**直接回傳該呼叫的結果，不做任何事後處理**。這樣的呼叫叫 **tail call**。

`tail.c`：

```c
/* 1. 非 tail：返回後還要 n + ... */
unsigned long sum_rec(unsigned long n) { if (n == 0) return 0; return n + sum_rec(n - 1); }

/* 2. tail：累加器 acc 帶著走，遞迴呼叫是最後動作 */
unsigned long sum_tail(unsigned long n, unsigned long acc)
{
    if (n == 0) return acc;
    return sum_tail(n - 1, acc + n);
}
```

先看 `-O0` 的 `sum_tail`：

```asm
sum_tail:
    pushq %rbp ; movq %rsp,%rbp ; subq $16,%rsp
    movq  %rdi,-8(%rbp) ; movq %rsi,-16(%rbp)
    cmpq  $0,-8(%rbp) ; jne .L5
    movq  -16(%rbp),%rax ; jmp .L6
.L5:
    ...                          # 算 n-1、acc+n，放進 rdi、rsi
    call  sum_tail               # 真的 call：frame 一層一層疊
.L6:
    leave ; ret
```

`-O0` 不做任何改寫，每層都是真的 `call`。換成 `-O2`：

```asm
sum_tail:                        # gcc 11.4 -O2
    movq    %rsi, %rax           # acc -> rax
    testq   %rdi, %rdi
    je      .L14
.L11:                            # 迴圈！沒有 call
    addq    %rdi, %rax
    subq    $1, %rdi
    jne     .L11
.L14:
    ret
```

遞迴變成了 5 條指令的迴圈，沒有 stack 操作。**這個轉換叫 tail call elimination**：既然這一層回來後什麼都不用做，就可以重用同一個 frame：把新參數放進 `rdi`、`rsi`，然後 `jmp` 回函式開頭，而不是 `call`。

### 連非 tail 的 `sum_rec` 也被消除了

更有趣的是 `sum_rec`（返回後還要 `n + ...`）在 `-O2` 也變成同樣的迴圈：

```asm
sum_rec:                         # gcc 11.4 -O2
    xorl    %eax, %eax           # 累加器從 0 開始
    testq   %rdi, %rdi
    je      .L1
.L2:
    addq    %rdi, %rax
    subq    $1, %rdi
    jne     .L2
.L1:
    ret
```

這是 gcc 的「累加器轉換」：加法滿足結合律與交換律，`n + f(n-1)` 可以改寫成帶累加器的 tail call 形式再消除。**所以「是不是 tail call」不是看原始碼長相，而是看編譯器能不能證明改寫是等價的**。乘法（階乘 `n * f(n-1)`）同理；`fib(n-1) + fib(n-2)` 有兩個遞迴呼叫，無法這樣轉。

### 實測：同一個函式在不同最佳化層級的存活率

`tail.c` 的 `main` 同時呼叫 `sum_rec` 與 `sum_tail`，n = 10,000,000（遠超 8 MiB / 48 的深度）：

```
gcc -O0                       → exit 139（SIGSEGV）
gcc -O1                       → exit 139
gcc -O2                       → 正常：sum_rec(10000000) = 50000005000000
gcc -O2 -fno-optimize-sibling-calls → exit 139（只關掉這個選項，其他 -O2 最佳化保留）
```

結論：

- gcc 的 tail call 消除由 `-foptimize-sibling-calls` 控制，在 **`-O2` 起**才開啟（`-O1` 不開）。
- 原文說「C 要開編譯器最佳化才會做」，更精確的說法是**要開到 `-O2`（gcc）**，而且是否套用由編譯器自行決定。**絕對不要讓程式的正確性依賴它**：同一支程式用 `-O0` 編譯（為了除錯）就崩潰了。

### 遞迴 vs 迴圈：組語一樣

原文提到 clang 對 `gcd` 遞迴與迴圈版本產生相同的 inner loop。gcc 11.4 `-O2` 同樣如此：

```asm
gcd_rec:                          gcd_itr:                         # 兩個的核心迴圈完全一致
    ...                               ...
.L27: movl %edx, %r8d             .L30: movl %edx, %r8d
.L19: xorl %edx, %edx                   xorl %edx, %edx
      divl %r8d                         divl %r8d
      movl %r8d, %eax                   movl %r8d, %eax
      testl %edx, %edx                  testl %edx, %edx
      jne  .L27                         jne  .L30
```

（`gcd_rec` 開頭多一段等價的進入檢查，核心迴圈相同。）這個例子說明**遞迴的成本不是遞迴本身，是「無法被編譯器消除的呼叫」**。對照原文的 Fibonacci 比較，我們實測（`fib.c`，`-O2`）：

```
fib_rec(40)       = 102334155   0.25 s    （約 3.3 億次呼叫，指數 O(2^n)）
fib_itr  x1e6 次 (n = 80..89)   0.046 s   （線性 O(n)）
fib_tail x1e6 次 (n = 80..89)   0.025 s   （tail call 消除後也是迴圈）
fib_fd   x1e6 次 (n = 80..89)   0.016 s   （fast doubling，O(log n)）
```

（四者在這個環境的絕對時間受 gcc 對小迴圈的展開與向量化影響，不要解讀成「tail 比 itr 快」，重點是數量級：指數版本的 `fib(40)` 要 0.25 秒，其他三個的 90 層只要幾十奈秒。）`fib_fd` 的遞迴深度只有 `log2(n)`，是**分治遞迴**最典型的好處：深度小，stack 不是問題。

### 哪些 tail call 不能消除？

`tail3.c` 設計了四種情況，用 `gcc -O2` 看組語中的遞迴點：

```
is_even → is_odd（互相遞迴，皆 noinline）   : jmp is_odd / jmp is_even    ✔ 互相 tail call 都變成 jmp
via_ptr（透過 volatile 函式指標呼叫自己）   : jmp *%rdx                    ✔ indirect tail call 也是 jmp
not_tail（返回後 +1）                       : 沒有 call（被累加器轉換消除）
blocked_by_escape（把 buf 位址傳給下一層）  : call blocked_by_escape       ✘ 無法消除
```

實際用 n = 5,000,000 跑：前三種都正常，`blocked_by_escape` 崩潰（exit 139）。它的程式：

```c
unsigned long blocked_by_escape(unsigned long n, const unsigned char *parent)
{
    unsigned char buf[16] = {0};
    buf[0] = parent ? (unsigned char)(parent[0] + 1) : 1;
    if (n == 0) return buf[0];
    return blocked_by_escape(n - 1, buf);   /* 看起來是 tail call，但 callee 要讀 caller 的 buf */
}
```

**把自己 frame 裡物件的位址傳給被呼叫者**，被呼叫者執行期間要讀它，這個 frame 就不能被重用，tail call 消除不成立。其他常見的阻礙：

- 呼叫之後還有要執行的程式碼（例如 `free`、`unlock`、再包一層運算而編譯器無法改寫）
- 回傳型別需要轉換、或被呼叫函式的 stack 參數比自己多（需要更多 stack 空間，frame 無法原地重用）
- Ubuntu 預設的 `-fstack-protector-strong` 會在 `ret` 前檢查 canary，含陣列的函式如果要保護，通常需要在 return 前做檢查，這會讓「呼叫完還有事做」；我在上面的實驗中 `blocked_by_escape` 有 `buf[16]`，是否因為 canary 也被擋我沒有單獨隔離驗證，結論以「位址逃逸」為主

### 強制 tail call：`musttail`

既然「靠編譯器心情」不可靠，clang 提供 `__attribute__((musttail))`：標在 `return f(...)` 上，要求一定做成 tail call，**做不到就編譯錯誤**。`musttail.c`：

```c
unsigned long sum_tail(unsigned long n, unsigned long acc)
{
    if (n == 0) return acc;
    __attribute__((musttail)) return sum_tail(n - 1, acc + n);
}
```

```
$ clang -O0 musttail.c -o mt_clang && ./mt_clang 50000000
1250000025000000                          ← -O0 也不會爆 stack
$ clang -O0 nomt.c -o nomt_clang && ./nomt_clang 50000000    # 同程式，去掉屬性
Segmentation fault (core dumped)
```

簽章不相容時編譯器直接擋下：

```
mt_bad.c:3:31: error: cannot perform a tail call to function 'f' because its signature is incompatible with the calling function
mt_bad.c:1:1: note: target function has different number of parameters (expected 2 but has 1)
```

**原文說 GCC 自 14 版起亦支援 `musttail`；實測不是**：

```
$ docker run gcc:14 ... gcc -O0 musttail.c      # GCC 14.4.0
musttail.c:7:31: error: expected identifier or '(' before 'return'
$ docker run gcc:15 ... gcc -O0 musttail.c && ./mt 50000000   # GCC 15.3.0
1250000025000000
```

也就是 **gcc 14.4 仍然不認得這個屬性，gcc 15.3 才支援**（gcc 11.4 同樣報錯）。clang 14 就有（`musttail` 自 clang 13 起提供，就我所知）。所以要寫跨編譯器的程式，要用 `#if __has_attribute(musttail)` 保護，像 rv32emu 的 `MUST_TAIL` 巨集（見下一節）。

### 為什麼 musttail 值得用：解譯器的 threaded dispatch

解譯器核心是個 `dispatch` 迴圈：取指令 → 查表 → 呼叫對應的 handler。若每個 handler 結尾 tail call 下一個 handler：

```c
#define MUST_TAIL __attribute__((musttail))
const char *op_add(PARAMS) { /* 做 add */ MUST_TAIL return next->impl(ARGS); }
```

好處（Josh Haberman 在 protobuf 解析器的實驗）：每個 handler 是獨立函式，編譯器可以把熱的值一路留在暫存器，不必 spill 到 stack；快路徑與慢路徑分開，各自獨立配置暫存器。限制是：這些函式裡**所有**其他呼叫都要是 inline 或 tail call，否則任何一個普通 `call` 就迫使編譯器建立完整 frame 並把暫存器存到 stack，效能崩潰。RISC-V 模擬器 rv32emu 用這個模式，每道指令是一個 handler，用 `MUST_TAIL return next->impl(...)` 串起來，在 `-O0` 也不會爆 stack。

## 遞迴案例分析

### Hanoi：用遞迴描述的問題

把 n 個盤子從 A 搬到 C，借 B：先搬上面 n-1 個到 B，搬最大的，再搬 n-1 個到 C。`hanoi.c`：

```
n = 3：
  盤 1: A -> C
  盤 2: A -> B
  盤 1: C -> B
  盤 3: A -> C
  盤 1: B -> A
  盤 2: B -> C
  盤 1: A -> C
共 7 步
n = 10：1023 步 (2^n - 1 = 1023)
n = 20：1048575 步 (2^n - 1 = 1048575)
n = 30：1073741823 步 (2^n - 1 = 1073741823)
```

步數是 `T(n) = 2T(n-1) + 1 = 2^n - 1`，**時間是指數**，但**stack 深度只有 n**（每次遞迴 n 減 1）。這個區分很重要：時間複雜度與空間（stack）複雜度是兩回事。

### 字串反轉：指數時間的遞迴與線性的改良

原文要求以遞迴 in-place 反轉字串。原文的第一個版本呼叫三次遞迴（`reverse(s+1)`、`reverse(s+2)`、`reverse(s+1)`），原文推導 `T(n) = 2T(n-1) + T(n-2) + O(1)`。我們實測呼叫次數與時間（`rev.c`，`-O1`），並對照原文的改良版本 B（遞迴到尾端、回溯時交換）與我加的雙指標版本 C：

```
n=10  A:       4059 次呼叫 0.0000s   B:  11 次呼叫
n=14  A:     137903 次呼叫 0.0002s   B:  15 次呼叫
n=18  A:    4684659 次呼叫 0.0070s   B:  19 次呼叫
n=22  A:  159140519 次呼叫 0.2239s   B:  23 次呼叫
n=26  A: 5406093003 次呼叫 7.7419s   B:  27 次呼叫
```

n 每加 4，A 的呼叫數乘以約 34 = (1+√2)^4（1+√2 ≈ 2.414），和遞迴式的解吻合；B 與字串長度成線性。三個版本對 `"abcde"` 都得到 `edcba`。版本 C：

```c
static void rev_range(char *l, char *r)
{
    if (l >= r) return;
    swap(l, r);
    rev_range(l + 1, r - 1);        /* tail call：-O2 會變成迴圈 */
}
```

最後一個動作是遞迴呼叫，是 tail call。這裡的 `swap` 我用了暫存變數，沒有用原文的 XOR 版本：XOR swap 在兩個參數指向**同一個位址**時會把值清成 0（`*a ^= *b` 此時等於 `*a ^= *a`），遞迴反轉的邊界條件（`l == r`）剛好是這個情況，所以要在呼叫前就 `l >= r` 擋掉，或乾脆用暫存變數。

### 遞迴走訪目錄

原文的 `list_dir` 有兩個已知問題：沒有略過 `.` 與 `..`（然後遞迴進入，對 `.` 會無限遞迴），而且只印目錄不印檔案。修正版（`walk.c`）：

```c
static void list_dir(const char *dir_name, int depth)
{
    DIR *d = opendir(dir_name);
    if (!d) { perror(dir_name); return; }
    struct dirent *entry;
    while ((entry = readdir(d)) != NULL) {
        const char *name = entry->d_name;
        if (!strcmp(name, ".") || !strcmp(name, "..")) continue;
        char path[PATH_MAX];
        int n = snprintf(path, sizeof path, "%s/%s", dir_name, name);
        if (n < 0 || (size_t)n >= sizeof path) { fprintf(stderr, "path too long\n"); continue; }
        printf("%*s%s%s\n", depth * 2, "", name, entry->d_type == DT_DIR ? "/" : "");
        if (entry->d_type == DT_DIR) list_dir(path, depth + 1);
    }
    closedir(d);
}
```

對 `tree/`（`a/b/y.txt`、`a/x.txt`、`c/z.txt`、`top.txt`）執行並排序：

```
    y.txt
  b/
  x.txt
  z.txt
a/
c/
top.txt
```

（`readdir` 的順序由檔案系統決定，這裡用 `sort` 比較，7 個條目與 `find tree` 一致；加 `-fsanitize=address,undefined` 同樣通過。）幾個值得注意的點：

- `snprintf` 回傳的是「若空間足夠本來要寫的長度」，所以 `n >= sizeof path` 才是截斷檢查，原文版本只在 `path_length >= PATH_MAX` 時 `return`，會讓整個目錄的後續項目被跳過。
- `d_type` 在某些檔案系統回傳 `DT_UNKNOWN`（`man 3 readdir` 有說明），保險做法是 `lstat` 再看 `S_ISDIR`。**用 `stat` 會跟隨符號連結，可能造成無限遞迴**（連結指向上層），要用 `lstat`。
- 遞迴深度由目錄深度決定，通常很淺，但 `PATH_MAX`（4096）的 `path[]` 區域陣列每層佔 4 KiB，目錄 1000 層深就吃掉 4 MiB stack。這是遞迴 + 大型區域陣列的典型問題。

## Stack overflow：核心與 sanitizer 眼中的樣子

stack 用盡時，核心的 stack 區域因 page fault 無法再向下擴展（超過 `RLIMIT_STACK`，或碰到下方的 guard 區域），行程收到 SIGSEGV。在 gdb 裡看到的是：

```
Program received signal SIGSEGV, Segmentation fault.
```

`stackovf.c` 示範如何在 stack 已爆的情況下**仍然執行一段處理程式**：用 `sigaltstack` 準備備用 stack，註冊 handler 時加 `SA_ONSTACK`：

```c
static char alt[64 * 1024];
stack_t ss = { .ss_sp = alt, .ss_size = sizeof alt, .ss_flags = 0 };
sigaltstack(&ss, NULL);
struct sigaction sa;  memset(&sa, 0, sizeof sa);
sa.sa_sigaction = on_segv;  sa.sa_flags = SA_SIGINFO | SA_ONSTACK;
sigemptyset(&sa.sa_mask);
sigaction(SIGSEGV, &sa, NULL);
```

```
SIGSEGV：fault address = 0x7ffe1f6fbfa8，遞迴深度 = 74853
```

少了 `SA_ONSTACK`，handler 會試圖跑在已經爆掉的 stack 上，直接再次 SIGSEGV 而且沒有任何輸出。（handler 中我用了 `snprintf` 方便觀察；它不是 async-signal-safe，正式程式要改用 `write` 加手寫整數轉字串。）

AddressSanitizer 對這件事給出比 SIGSEGV 清楚得多的診斷（`gcc -O0 -g -fsanitize=address depth.c`）：

```
==23466==ERROR: AddressSanitizer: stack-overflow on address 0x7ffeee046fb8 (pc 0x58f9d7c9123c bp 0x7ffeee047040 sp 0x7ffeee046fb0 T0)
    #0 0x58f9d7c9123c in recurse /home/ypp/c_sysprog_lab/p2b/depth.c:13
    #1 0x58f9d7c9131f in recurse /home/ypp/c_sysprog_lab/p2b/depth.c:18
    #2 0x58f9d7c9131f in recurse /home/ypp/c_sysprog_lab/p2b/depth.c:18
    ...
```

指出是 stack-overflow，並印出呼叫鏈的頭幾層。（注意 ASan 會增加每層 frame 的大小，因為有 redzone，所以崩潰深度比無 ASan 淺。）

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 一般遞迴 | 對應問題的數學定義，易讀 | 深度受 stack 限制；每層有 call 成本 | 深度為 O(log n) 的分治、樹的走訪 |
| 迴圈 | 沒有 stack 增長 | 複雜問題（回溯、樹）要自己維護 stack | 線性、狀態簡單的問題 |
| tail recursion（靠 `-O2`） | 保持遞迴的寫法，編譯器轉迴圈 | 不保證、`-O0` 會爆、除錯版與發行版行為不同 | 寫給編譯器的提示，不要依賴 |
| `musttail`（clang；gcc 15+） | 保證 tail call，編譯期檢查 | 非標準；簽章必須相容；可攜性差 | 解譯器 dispatch、效能關鍵的狀態機 |
| 顯式 stack + 迴圈 | stack 放 heap，大小可控 | 程式碼變長 | 深度不可預測的輸入（惡意或未信任資料） |

**為什麼 C 標準不保證 tail call 消除？** Scheme 的規格明確要求（所以 Scheme 程式可以只用遞迴寫迴圈）。C 不要求，部分原因是 C 的區域變數位址可以外洩（上面的 `blocked_by_escape`），要在語言層級保證 tail call，就得精確規範什麼情況下 frame 可以被重用，這會綁死編譯器與 ABI 實作。

## 踩雷集錦

1. **以為「遞迴一定慢」**：`-O2` 的 `gcd_rec` 與 `gcd_itr` 核心迴圈完全相同；慢的是指數時間的演算法（`fib_rec(40)` 3 億次呼叫），不是遞迴本身。
2. **以為 `-O0` 能跑的遞迴 `-O2` 也一定能跑，或反過來**：tail call 消除讓 `-O2` 能跑 10,000,000 層，`-O0`/`-O1` 同一支程式 SIGSEGV。除錯版與發行版的差異可能是 stack overflow 的來源，也可能掩蓋它。
3. **忽略每層 frame 的大小**：frame 48 bytes 時深度約 17 萬，帶一個 240 bytes 的區域陣列就剩 2.9 萬層。遞迴函式裡不要放大的區域陣列，改用 heap 或傳進來的緩衝區。
4. **用 XOR swap 又沒擋 `a == b`**：值會被清成 0。遞迴雙指標演算法的中間格是常見觸發點。
5. **把輸入決定的深度交給遞迴**：解析 JSON、走訪使用者給定的目錄樹，深度由輸入決定。攻擊者可以構造深度巢狀輸入讓程式爆 stack（拒絕服務）。這類程式要設深度上限，或改用顯式 stack。

## 進階：再往深一層

**`-fstack-usage`、`-Wstack-usage=N`、`-Wframe-larger-than=N`**：編譯期就能知道每個函式的 frame 大小，遞迴函式再乘上最大深度即可估計 stack 需求。嵌入式與核心程式常用 `-Wframe-larger-than=1024` 之類的選項強制控制；Linux 核心的 stack 只有 16 KiB（x86-64），遞迴在核心裡幾乎被禁止。

**tail call 與 `-fno-omit-frame-pointer`、除錯**：tail call 消除後 `bt` 看不到被消除的呼叫者（它的 frame 被重用了），除錯時 backtrace 會「缺層」，這是正常現象；要保留可以加 `-fno-optimize-sibling-calls`。

**Continuation 與 trampoline**：沒有 tail call 保證的語言（以及 C 的可攜寫法）可用 trampoline：函式不直接遞迴，而是回傳「下一步要做的事」給外層迴圈。這是 Eli Bendersky 文章探討的主題，也是解譯器實作的常用手法（[Ch 38 編譯器實作](./38-compiler-construction.md) 會再遇到）。

**mutual recursion 與 jump table**：`is_even`/`is_odd` 的 `jmp` 展示了狀態機 → 函式 → tail call 的對應關係：每個狀態是一個函式，狀態轉換是 tail call。狀態機寫成互相 tail call 的函式，是遞迴下降 parser 與解譯器常見的手法。

## 動手練習

1. 在你的機器上用 `ulimit -s` 配合 `depth.c`，驗證「最大深度 ≈ stack 大小 ÷ frame 大小」，並試試 `ulimit -s unlimited` 會發生什麼（提示：看 `/proc/self/maps` 的 stack 與 mmap 區如何改變配置）。
2. 把 `tail3.c` 的 `via_ptr` 的 `volatile` 拿掉，看 `-O2` 的組語，說明編譯器做了什麼。
3. 把 `blocked_by_escape` 改成不傳 `buf` 位址、但在返回前讀 `buf[0]`，看是否仍然是 tail call。
4. 用遞迴寫 `binary_search`，確認 `-O2` 下是否變成迴圈；再把它改成需要在返回後做事的版本，比較。
5. 用 `walk.c` 掃 `/usr`，加上 `ulimit -s 64` 看看目錄夠深時是否崩潰；然後改成顯式 stack 版本。

## 本章重點整理

- 遞迴最大深度 ≈ `ulimit -s` ÷ 每層 frame 大小，實測誤差不到 0.05%；frame 大小可由 `-fstack-usage` 在編譯期得知。
- gcc 在 `-O2` 起開啟 tail call 消除（`-foptimize-sibling-calls`），連 `n + f(n-1)` 這種可用結合律改寫的遞迴也會變迴圈；位址逃逸則會讓它失敗。
- `musttail` 能保證 tail call：clang（13+）與 gcc 15+ 支援，gcc 14.4 實測不支援。
- 遞迴的成本是「不能被消除的呼叫」，不是遞迴本身；指數時間的演算法才是慢的主因。

## 自我檢核

- [ ] 不看筆記，能解釋為什麼 `sum_rec`（有 `n + ...`）在 `-O2` 也能被轉成迴圈？
- [ ] 給你一個遞迴函式，能不能估算出在 8 MiB stack 下的最大深度？需要知道什麼資訊？
- [ ] 如果有人說「我的程式在 debug 版崩潰、release 版正常」，你會懷疑和遞迴有什麼關係？
- [ ] 想想你現在的專案，哪個函式的遞迴深度由外部輸入決定？該如何防禦？
- [ ] 能說出至少兩種會讓 tail call 消除失敗的情況。

## 延伸閱讀

### 論文

- **Lambda: The Ultimate GOTO** — Guy Steele，MIT AI Memo 443（1977；可在 MIT DSpace 搜尋標題取得 PDF，這裡不附未驗證的直接連結）
  - **核心貢獻**：論證「procedure call 在尾端位置等價於 goto」，是 tail call 消除的理論基礎。
  - **讀哪裡**：前半的論證與範例；後半涉及 Lisp 編譯器細節，可略。
  - **前提**：對 Lisp/Scheme 的語法有基本概念。

### 部落格 / 技術文章

- **[On Recursion, Continuations and Trampolines](https://eli.thegreenplace.net/2017/on-recursion-continuations-and-trampolines/)** — Eli Bendersky（2017）
  - **這篇說什麼**：沒有 tail call 保證的語言怎麼用 continuation passing 與 trampoline 避免 stack 增長；程式範例為 Python，概念可直接搬到 C。
  - **為什麼值得讀**：把「為什麼要消除 stack frame」講得很清楚，延伸本章的 tail call 討論。
- **[Parsing Protobuf at 2+GB/s: How I Learned To Love Tail Calls in C](https://blog.reverberate.org/2021/04/21/musttail-efficient-interpreters.html)** — Josh Haberman（2021）
  - **這篇說什麼**：用 `musttail` 重寫 protobuf 解析器，並展示 switch、computed goto、tail call 三種 dispatch 的組語與效能差異。
  - **讀哪裡**：「The Tail Call Pattern」之後各節；前提是讀過本章與 [Ch 31 goto 與流程控制](./31-goto-and-control-flow.md) 的 computed goto。

### 官方文件 / Spec

- **[GCC 文件：Optimize Options，`-foptimize-sibling-calls`](https://gcc.gnu.org/onlinedocs/gcc/Optimize-Options.html)**
  - **讀哪裡**：搜尋 `-foptimize-sibling-calls` 與 `-fstack-usage`（在 Developer Options）；本章的實驗開關都在這裡。
- **[Clang 文件：`musttail` 屬性](https://clang.llvm.org/docs/AttributeReference.html#musttail)**
  - **讀哪裡**：整節；列出簽章相容、不得有 cleanup 等限制條件。

### 書籍

- **《Structure and Interpretation of Computer Programs》** — Abelson & Sussman（MIT Press，1996，第 2 版）
  - **這本書的定位**：1.2 節「Procedures and the Processes They Generate」區分遞迴 *procedure* 與遞迴 *process*，並解釋為何尾端遞迴的 process 是迭代的；與本章的 `sum_tail` 對照最清楚。範例用 Scheme，不需要熟悉就能讀懂這一節。

遞迴把「呼叫」的成本與 stack 的大小放到了台面上；下一章看這個機制為什麼是資安攻防的主戰場：stack 上相鄰的返回位址與區域變數，讓一次越界寫入就能改變控制流。

→ [Ch 14 函式呼叫與資安：buffer overflow、canary 與 ROP](./14-function-call-security.md)
