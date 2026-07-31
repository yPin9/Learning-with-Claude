# Ch 6 — ltrace 與動態連結

> **目標**：理解 ltrace——它看 library 函式呼叫（malloc/strcpy/fopen），補上 strace（只看 syscall）看不到的那一層。但更重要的是理解它**怎麼做到**——透過動態連結的 PLT/GOT 機制。理解動態連結（程式怎麼找到 library 函式），你就懂 ltrace 怎麼攔截、也懂 Ch 20 的 LD_PRELOAD 怎麼運作。這章把「library 層的觀察」和「動態連結機制」一起講透。

> **環境**：Linux x86-64，C。ltrace（Ch 0 已裝）。

## 為什麼需要 ltrace？

Ch 5 的 strace 看 syscall——但很多程式行為**不是 syscall**。`malloc(100)` 不是 syscall（它是 glibc 的函式，底層可能批次跟 kernel 要記憶體）、`strcpy`/`strlen`/`printf` 的格式化都是 library 函式。strace 看不到這些（Ch 1 驗證過）。**ltrace** 補上這一層——它攔截 library 函式呼叫。

但 ltrace 比 strace 更有教育意義的地方是「**它怎麼做到**」——strace 用 ptrace 在 syscall 邊界攔截（Ch 3-4），但 library 函式呼叫不經過 kernel，ltrace 怎麼攔截？答案是**動態連結的 PLT/GOT 機制**。理解這個，你不只懂 ltrace，還懂「程式怎麼找到 library 函式」「LD_PRELOAD 怎麼覆蓋函式」（Ch 20）。這章把 library 觀察和動態連結一起講——這是理解很多進階技巧的基礎。

## 先建立直覺:strace 和 ltrace 的分工

```
strace vs ltrace（觀察不同層）：

  你的程式
    │ 呼叫 library 函式（malloc, strcpy, fopen, printf...）
    ▼
  ┌─────────────────────────┐
  │  C library (glibc)      │ ← ltrace 攔截「這一層的呼叫」
  │  malloc/strcpy/fopen... │
  └───────────┬─────────────┘
    │ library 內部可能呼叫 syscall（不一定）
    ▼
  ┌─────────────────────────┐
  │  syscall（brk/openat/   │ ← strace 攔截「這一層」
  │  write/...）            │
  └─────────────────────────┘
        │
  → ltrace 看「程式 → library」的呼叫
    strace 看「library → kernel」的呼叫（syscall）
    例：fopen（ltrace 看到）底層 openat（strace 看到）
        malloc（ltrace 看到）底層可能 brk（strace 看到），也可能不（重用）
```

關鍵心智：strace 看「library → kernel」（syscall），ltrace 看「程式 → library」（函式呼叫）。`fopen`（ltrace 看到）底層是 `openat`（strace 看到）；`malloc`（ltrace 看到）底層可能是 `brk`（strace 看到）也可能不是（glibc 重用已有記憶體，沒跟 kernel 要）。兩者觀察不同層，互補。

> ltrace 補上 strace（Ch 5）看不到的 library 層。如果對「為什麼 strace 看不到 malloc」不熟，回看 [Ch 1](./01-observation-tools-overview.md) 的分層觀察。

## ltrace 的基本用法

```bash
# 基本：看 library 函式呼叫
cd ~/obslab
cat > libdemo.c <<'EOF'
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main() {
    char *buf = malloc(50);
    strcpy(buf, "hello");
    int len = strlen(buf);
    printf("len=%d\n", len);
    free(buf);
    return 0;
}
EOF
gcc -o libdemo libdemo.c

ltrace ./libdemo
# malloc(50)          = 0x55...        ← 看到 malloc！
# strcpy(0x55..., "hello") = 0x55...   ← 看到 strcpy！
# strlen("hello")     = 5
# printf("len=%d\n", 5) = 7
# free(0x55...)       = <void>
# → strace 看不到這些（它們不是 syscall），ltrace 看得到

# === 選項（和 strace 類似）===
ltrace -f ./prog                 # 追蹤子 process
ltrace -e malloc+free ./prog     # 只看 malloc/free（過濾）
ltrace -c ./prog                 # 統計（各函式呼叫次數/時間）
ltrace -S ./prog                 # 同時顯示 syscall（library + syscall 一起看！）
ltrace -p <PID>                  # attach

# ltrace -S 是強大組合：同時看 library 和 syscall
ltrace -S ./libdemo 2>&1 | head
# malloc(50) = 0x55...
# SYS_brk(...) = ...               ← malloc 底層的 syscall（如果有）
# → 看到 library 呼叫「和」它底層的 syscall，完整的層次
```

> **`ltrace -S`（同時顯示 library 呼叫和 syscall）是強大組合——一次看到完整的兩層**。ltrace 基本用法和 strace 類似（`-f` 追子 process、`-e` 過濾、`-c` 統計、`-p` attach），但它看的是 **library 函式**。最強的是 **`-S`**——同時顯示 library 呼叫**和**它們底層的 syscall，讓你看到完整的兩層（如 `malloc(50)` 然後 `SYS_brk(...)`——library 呼叫和它觸發的 syscall）。這對理解「library 函式底層做什麼」很有用——你看到 `fopen` 觸發 `openat`、`malloc` 觸發 `brk`/`mmap`（或不觸發，因為重用）。ltrace 特別擅長 debug：**記憶體相關**（看 malloc/free 配對——雖然 valgrind 更專業，但 ltrace 能快速看 malloc/free 的呼叫）、**字串處理**（看 strcpy/strlen/strcmp 的參數和結果）、**理解程式邏輯**（看它呼叫哪些 library 函式，推斷它在做什麼）。注意 ltrace 對「靜態連結」的程式無效（沒有動態 library 可攔截，下節解釋為什麼）。`ltrace -c`（統計）給「程式呼叫哪些 library 函式最多」的總覽。記住 strace 和 ltrace 的分工——syscall 問題用 strace、library 函式問題用 ltrace、要完整看用 `ltrace -S`。

## 動態連結:程式怎麼找到 library 函式

理解 ltrace 怎麼攔截，要先懂動態連結——這也是理解 LD_PRELOAD（Ch 20）的基礎：

```
動態連結（dynamic linking）：程式怎麼用 library 函式

  你的程式呼叫 printf，但 printf 的程式碼在 glibc（libc.so）裡
  程式編譯時「不知道」printf 在記憶體哪個位址（library 還沒載入）
        │
  解法：延遲綁定（lazy binding）+ PLT/GOT
        │
  PLT（Procedure Linkage Table，程序連結表）：
    每個外部函式（printf）有一個 PLT 條目（一小段跳轉碼）
    程式呼叫 printf → 實際跳到 printf@plt
        │
  GOT（Global Offset Table，全域偏移表）：
    存「函式的真實位址」
    第一次呼叫：PLT 觸發「解析」→ 動態連結器找到 printf 真實位址 → 寫進 GOT
    之後呼叫：PLT 直接從 GOT 拿真實位址跳過去（快）
        │
  → 程式呼叫外部函式 = 跳到 PLT → 查 GOT → 跳到真實位址
    這個「間接跳轉」是 ltrace 和 LD_PRELOAD 能介入的地方
```

```bash
# 看一個程式依賴哪些動態 library
ldd ./libdemo
# linux-vdso.so.1
# libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6   ← 依賴 glibc
# /lib64/ld-linux-x86-64.so.2                     ← 動態連結器

# 看 PLT/GOT（Ch 11 的 readelf 預習）
objdump -d ./libdemo | grep -A2 '@plt'
# printf@plt, malloc@plt...     ← 每個外部函式的 PLT 條目

# 靜態連結對比（library 直接編進程式，沒有動態連結）
gcc -static -o libdemo_static libdemo.c
ldd ./libdemo_static
# not a dynamic executable      ← 靜態連結，沒有動態 library
ltrace ./libdemo_static
# → ltrace 看不到 library 呼叫！（沒有 PLT/GOT 可攔截）
```

> **動態連結的 PLT/GOT「間接跳轉」是 ltrace 攔截和 LD_PRELOAD 覆蓋的關鍵——理解它打開很多進階技巧**。程式呼叫 `printf`，但 printf 的程式碼在 glibc（編譯時不知道它在記憶體哪）。解法是**延遲綁定 + PLT/GOT**：每個外部函式有個 **PLT 條目**（一小段跳轉碼），程式呼叫 printf 實際跳到 `printf@plt`；**GOT** 存函式的真實位址——第一次呼叫時動態連結器解析出 printf 的真實位址寫進 GOT，之後直接從 GOT 拿位址跳過去。**這個「間接跳轉」（透過 PLT 查 GOT）是 ltrace 和 LD_PRELOAD 能介入的地方**——ltrace 修改 PLT/GOT 或設斷點來攔截呼叫、LD_PRELOAD（Ch 20）讓動態連結器先載入你的 library，使 GOT 指向你的函式（覆蓋原本的）。這解釋了一個關鍵限制：**ltrace 對「靜態連結」的程式無效**——`gcc -static` 把 library 直接編進程式，沒有 PLT/GOT（函式呼叫是直接的，不經過間接跳轉），ltrace 沒有攔截點。`ldd`（看依賴哪些動態 library）、`objdump -d | grep @plt`（看 PLT 條目）讓你看到動態連結的結構。理解 PLT/GOT，你不只懂 ltrace 怎麼運作，還懂了 Ch 20 的 LD_PRELOAD（覆蓋 library 函式）、動態連結的安全問題、為什麼有些攻擊針對 GOT。這是系統程式的核心機制。

## ltrace 怎麼攔截（連到 PLT/GOT）

```
ltrace 攔截 library 呼叫的機制（連到動態連結）：

  ltrace 用 ptrace（和 strace 一樣，Ch 3）+ 利用 PLT/GOT：
        │
  方法（簡化）：
    1. ltrace 找出程式的 PLT 條目（每個外部函式一個）
    2. 在 PLT 條目設「斷點」（用 ptrace，像 debugger）
    3. 程式呼叫 library 函式 → 跳到 PLT → 觸發斷點 → ltrace 暫停它
    4. ltrace 讀參數（暫存器/堆疊）、記錄、放它繼續
        │
  → ltrace = ptrace（暫停/讀取，Ch 3）+ 在 PLT 設斷點
    所以它需要動態連結（有 PLT 才能設斷點攔截）
    靜態連結沒 PLT → ltrace 無效
        │
  對比 strace：在 syscall 邊界攔截（PTRACE_SYSCALL）
       ltrace：在 library 函式（PLT）攔截（斷點）
```

> **ltrace 用「在 PLT 設斷點」攔截 library 呼叫——這就是為什麼它需要動態連結、對靜態程式無效**。ltrace 和 strace 都用 ptrace（Ch 3 的暫停/讀取機制），但**攔截點不同**：strace 在 **syscall 邊界**攔截（`PTRACE_SYSCALL`，Ch 3-4），ltrace 在 **library 函式（PLT）** 攔截。ltrace 的機制（簡化）：找出程式的 PLT 條目（每個外部函式一個）、在 PLT 條目設**斷點**（用 ptrace，像 debugger 設斷點）、程式呼叫 library 函式跳到 PLT 觸發斷點、ltrace 暫停讀參數記錄再放行。所以 ltrace **需要動態連結**——有 PLT 才有攔截點。這完整解釋了：(1) 為什麼 ltrace 對靜態連結程式無效（沒 PLT）；(2) ltrace 和 strace 的關係（同樣用 ptrace，不同攔截點）；(3) 為什麼 ltrace 的開銷可能比 strace 大（library 函式呼叫通常比 syscall 頻繁，斷點觸發更多）。理解這個，你把 Ch 3-4 的 ptrace 知識和動態連結連起來了——觀察工具都用 ptrace，差別在「在哪裡攔截」（syscall 邊界 vs PLT vs 指令層）。這也預告了 Ch 19（ptrace 注入，改 tracee 的執行）和 Ch 20（LD_PRELOAD，另一種攔截 library 的方式——不用斷點，而是讓 GOT 指向你的函式）。

## 故意弄壞:用 ltrace 抓記憶體 bug

```bash
# 用 ltrace 看 malloc/free 的配對（找 leak 或 double-free 的線索）
cd ~/obslab
cat > memleak.c <<'EOF'
#include <stdlib.h>
#include <string.h>
int main() {
    char *a = malloc(100);          // 配對 1
    char *b = malloc(200);          // 配對 2
    strcpy(a, "data");
    free(a);                        // free 配對 1
    // 忘了 free(b)！→ leak
    return 0;
}
EOF
gcc -o memleak memleak.c

# 用 ltrace 看 malloc/free（過濾）
ltrace -e malloc+free ./memleak
# malloc(100) = 0x5555...a         ← 配對 1 分配
# malloc(200) = 0x5555...b         ← 配對 2 分配
# free(0x5555...a) = <void>        ← 配對 1 釋放
# +++ exited (status 0) +++
# → 看到 malloc 兩次、free 一次！
#   0x...b 分配了但沒 free → leak（少了一個 free）
#   ltrace 讓你「數 malloc 和 free 的配對」

# 對比：valgrind 是更專業的 leak 偵測（Ch 15）
valgrind --leak-check=full ./memleak 2>&1 | grep -A2 'definitely lost'
# definitely lost: 200 bytes in 1 blocks   ← valgrind 直接報 leak
```

> **ltrace 能「數 malloc/free 配對」快速看 leak 的線索，但 valgrind（Ch 15）是更專業的記憶體偵測**。用 `ltrace -e malloc+free` 看記憶體配對——這個例子分配兩次（malloc 100、malloc 200）但只 free 一次，ltrace 顯示 `malloc` 兩次、`free` 一次，立刻看出「少了一個 free」（`0x...b` 分配了沒釋放 = leak）。這是 ltrace 的實用 debug——快速看記憶體函式的呼叫模式（malloc/free 配對、strcpy 的緩衝、fopen/fclose 配對）。但要注意：**ltrace 不是專業的記憶體偵測工具**——它只是顯示呼叫，要你自己對照（數配對）；對複雜的 leak（在迴圈裡、條件分支、跨函式）很難用 ltrace 追。**valgrind memcheck（Ch 15）才是專業的**——它追蹤每個 malloc 的記憶體塊，程式結束時直接報「definitely lost: 200 bytes」（精確指出哪個 leak、多少、在哪分配的）。所以 ltrace 適合「快速看一眼記憶體呼叫模式」，深入的記憶體 debug 用 valgrind/ASan（Ch 15/18）。這呼應 Ch 1 的「選對工具」——同樣是記憶體問題，ltrace 給快速線索、valgrind 給專業診斷。理解每個工具的「甜蜜點」（ltrace 看呼叫模式、valgrind 追記憶體塊）讓你選對工具。ltrace 在「快速理解程式呼叫哪些 library 函式、參數是什麼」這個用途上最有價值。

## 動手練習

1. 基本 ltrace：對 libdemo.c 用 ltrace，看 malloc/strcpy/printf 的呼叫，對比 strace（看不到這些）

2. ltrace -S：用 `-S` 同時看 library 和 syscall，理解 fopen→openat、malloc→brk 的層次

3. 靜態連結：`gcc -static` 編譯，ltrace 它（看不到 library 呼叫），理解為什麼（沒 PLT）

4. 看動態連結：`ldd` 看依賴、`objdump -d | grep @plt` 看 PLT 條目，理解 PLT/GOT

5. 跑「故意弄壞」：用 `ltrace -e malloc+free` 數記憶體配對找 leak，對比 valgrind

## 本章重點整理

- ltrace 看 library 函式呼叫（malloc/strcpy/fopen），補上 strace（只看 syscall）看不到的層；`-S` 同時看兩層
- 動態連結用 PLT/GOT：程式呼叫外部函式 → 跳 PLT → 查 GOT 拿真實位址 → 跳過去（延遲綁定）
- ltrace 用 ptrace + 在 PLT 設斷點攔截 library 呼叫；所以需要動態連結，對靜態程式（無 PLT）無效
- PLT/GOT 的間接跳轉是 ltrace 攔截、LD_PRELOAD 覆蓋（Ch 20）的關鍵——理解它打開很多進階技巧
- ltrace 適合快速看 library 呼叫模式（malloc/free 配對）；深入記憶體 debug 用 valgrind（Ch 15）

## 自我檢核

- [ ] 能說出 ltrace 和 strace 的分工（library 層 vs syscall 層）
- [ ] 理解動態連結的 PLT/GOT 機制（程式怎麼找到 library 函式）
- [ ] 知道 ltrace 怎麼攔截（PLT 斷點），為什麼對靜態程式無效
- [ ] 會用 ltrace 看 library 呼叫、用 -S 同時看 syscall
- [ ] 知道 ltrace 和 valgrind 在記憶體 debug 的分工

## 延伸閱讀

### 文章

- **[PLT and GOT 詳解](https://systemoverlord.com/2017/03/19/got-and-plt-for-pwning.html)** — System Overlord
  - **這篇說什麼**：PLT/GOT 怎麼運作，以及它的安全意義
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章動態連結那節的權威深入版，也連到 Ch 20

- **[How dynamic linking works](https://www.technovelty.org/linux/plt-and-got-the-key-to-code-sharing-and-dynamic-libraries.html)** — Ian Wienand
  - **這篇說什麼**：動態連結、PLT/GOT、延遲綁定的完整解釋
  - **為什麼值得讀**：把動態連結講得最清楚

### 官方文件

- **[ltrace(1) man page](https://man7.org/linux/man-pages/man1/ltrace.1.html)** — ltrace
  - **讀哪裡**：選項（-e/-S/-c）
  - **為什麼值得讀**：ltrace 選項的權威

### 書籍

- **《Linkers and Loaders》— John Levine**
  - **讀哪幾章**：動態連結那幾章
  - **這本書的定位**：連結器/載入器的權威，把動態連結講到底

Part 2（strace 與 ltrace）的章節到此完成。接下來是練習 A——用 strace 抓一系列真實的 bug，把 strace/ltrace 的知識用在實戰偵探。

→ [練習 A：用 strace 抓 bug](./practice-a-strace-bug-hunt.md)
