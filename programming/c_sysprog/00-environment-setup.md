# Ch 0 — 環境建置：gcc、clang、gdb 與 sanitizer

> **目標**：架好一套「會幫你抓錯」的 C 開發環境：知道每個編譯旗標在做什麼、會用 sanitizer 讓記憶體錯誤當場現形、會看編譯器產生的組合語言，並能用 Docker 試跑 C23。

> **對應原文**：[你所不知道的 C 語言（系列目錄）](https://hackmd.io/@sysprog/c-programming) 的「講座說明」與 [開發工具和規格標準篇](https://hackmd.io/@sysprog/c-standards) 的「不要急著印出位址，善用 GDB」等節

> **環境**：WSL2 Ubuntu 22.04.3 LTS（x86_64）、gcc 11.4.0、clang 14.0.0、gdb 12.1、GNU Make 4.3、binutils 2.38、glibc 2.35；C23 段落用 Docker 映像 `gcc:14`（實測 gcc 14.4.0）與 `gcc:15`（15.3.0）。

## 為什麼需要這個？

很多人學 C 的環境是「一個 IDE、按下綠色三角形」。程式能跑，就以為是對的。問題是 C 有一大類錯誤**不會讓程式當場壞掉**：陣列多寫一格、用了沒初始化的變數、有號整數溢位。這些錯誤在你的機器上可能印出看似合理的數字，換個編譯器、換個最佳化等級、換台機器才爆炸。

jserv 在系列講座開頭就強調「捨棄以臆測為主的學習方式，探究 C 程式的實際行為」。要探究實際行為，靠的是工具：

- **編譯器警告**：在編譯期就指出可疑寫法
- **sanitizer**：在執行期把「沒當場壞掉」的錯誤變成「當場報錯並指出行號」
- **組合語言輸出**：看編譯器到底把你的 C 變成了什麼
- **除錯器（GDB）**：停下程式，直接看記憶體

這一章把這四樣工具裝好、各跑一次，後面 43 章都會反覆用到。

## 先建立直覺

把編譯器想成一個很懂規則、但預設很安靜的同事。你不問，他就不說；你問他（開警告），他會指出問題；你請他在程式裡埋感測器（sanitizer），程式跑到出錯的那一行就會響。

```
              你寫的 hello.c
                    │
   ┌────────────────┼──────────────────────────────┐
   │ 編譯期檢查     │ -Wall -Wextra               │ ← 警告：可疑寫法
   │                ▼                              │
   │        gcc / clang 編譯                       │
   │                │ -g      → 附除錯資訊          │ ← 給 GDB 用
   │                │ -O0/-O2 → 最佳化等級          │
   │                │ -fsanitize=... → 埋檢查碼     │
   └────────────────┼──────────────────────────────┘
                    ▼
               ./hello 執行
   ┌────────────────┼──────────────────────────────┐
   │ 執行期觀察     │ ASan/UBSan 報錯              │ ← 執行到錯誤當場停
   │                │ gdb ./hello                  │ ← 停下來看記憶體
   └───────────────────────────────────────────────┘
```

## 安裝工具

在 Windows 上，本課使用 WSL2 裡的 Ubuntu 22.04。macOS 使用者請注意：macOS 的 `gcc` 指令預設其實是 Apple clang，GDB 在 Apple Silicon 上無法使用（改用 lldb），本課不涵蓋 macOS 差異，建議另開一台 Linux VM 或用 Docker。

```bash
# Windows PowerShell（系統管理員），只需一次
wsl --install -d Ubuntu-22.04

# 進入 Ubuntu 後
sudo apt update
sudo apt install build-essential gdb clang manpages-dev manpages-posix-dev strace
```

各套件的角色：

| 套件 | 內容 | 本課用在哪 |
|---|---|---|
| `build-essential` | gcc、g++、make、libc6-dev（標頭檔與 crt 物件） | 全課 |
| `gdb` | GNU Debugger | Ch 3、Ch 5 起大量使用 |
| `clang` | LLVM 的 C 前端，錯誤訊息與 sanitizer 實作和 gcc 不同 | 交叉驗證 |
| `manpages-dev` | 第 2 節（系統呼叫）、第 3 節（函式庫）man page | `man 3 printf` |
| `manpages-posix-dev` | POSIX 規格版的 man page（`man 3p`） | 查可攜性 |
| `strace` | 追蹤系統呼叫 | Ch 1、Ch 35、Ch 41 |

> 上面的 `apt install` 需要 root，本章作者的環境已預先裝好；以下是裝好後實測的版本確認。

```
$ gcc --version | head -1
gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
$ clang --version | head -1
Ubuntu clang version 14.0.0-1ubuntu1.1
$ gdb --version | head -1
GNU gdb (Ubuntu 12.1-0ubuntu1~22.04.2) 12.1
$ man -w 3 printf
/usr/share/man/man3/printf.3.gz
```

`man` 是你最近的第一手資料。`man 3 printf` 看 C 函式庫的 `printf`，`man 2 write` 看系統呼叫 `write`。數字是 man page 的「節」：1 是指令、2 是系統呼叫、3 是函式庫函式。少了 `manpages-dev`，`man 3 printf` 只會找到第 1 節的 shell 指令 `printf`，這是新手常見的困惑。

## 核心旗標：先看一次「沒開警告」的代價

下面這支程式有兩個錯誤。先別往下看答案，自己找找看：

```c
#include <stdio.h>

int main(void)
{
    int n;
    unsigned u = 1;
    int i = -1;
    if (i < u)
        printf("-1 < 1\n");
    else
        printf("-1 >= 1 ?!\n");
    printf("%d\n", n);
    return 0;
}
```

不加任何旗標編譯，安安靜靜：

```
$ gcc warn.c -o warn && ./warn
-1 >= 1 ?!
24638
```

`-1 >= 1`，而且 `n` 印出一個莫名其妙的數字（每次執行可能不同）。加上警告旗標再編一次：

```
$ gcc -Wall -Wextra warn.c -o warn
warn.c: In function ‘main’:
warn.c:8:11: warning: comparison of integer expressions of different signedness: ‘int’ and ‘unsigned int’ [-Wsign-compare]
    8 |     if (i < u)
      |           ^
warn.c:12:5: warning: ‘n’ is used uninitialized [-Wuninitialized]
   12 |     printf("%d\n", n);
      |     ^~~~~~~~~~~~~~~~~
```

兩個錯誤都被點名。第一個是 C 的「一般算術轉換」（usual arithmetic conversions，N1570 6.3.1.8）：`int` 和 `unsigned int` 比較時，`int` 會先被轉成 `unsigned int`，`-1` 變成 `4294967295`，所以比 `1` 大。[Ch 2](./02-types-and-objects.md) 會把這條規則講清楚。第二個是讀取未初始化的自動變數，值是不確定的（indeterminate）。

> 注意：`-Wsign-compare` 在 C 裡要 `-Wextra` 才會開（`-Wall` 只在 C++ 開它）；`-Wuninitialized` 的偵測能力和最佳化等級有關，同一份 code 在 `-O2` 下 gcc 會做更多資料流分析，有時抓得更多、有時因為變數被最佳化掉而不報。**警告不是證明**，沒有警告不代表沒有錯。

### 常用旗標一覽

| 旗標 | 作用 | 為什麼要開 |
|---|---|---|
| `-Wall` | 開一組「幾乎沒有誤報」的警告 | 名字騙人：它**不是**全部警告 |
| `-Wextra` | 再加一組（`-Wsign-compare`、未使用參數…） | 抓有號/無號混用 |
| `-Wpedantic` | 依 `-std=` 指定的標準，對非標準擴充發警告 | 確認你寫的是標準 C，不是 GNU C |
| `-std=c11` | 指定語言標準 | gcc 11 預設是 `gnu17`（C17 + GNU 擴充） |
| `-g` | 產生 DWARF 除錯資訊 | GDB 才能對應到原始碼行號與變數名 |
| `-O0` | 不最佳化（預設） | 每個變數都真的放在記憶體，除錯時看得到 |
| `-Og` | 只做不妨礙除錯的最佳化 | 除錯與效能的折衷 |
| `-O2` | 一般發佈用的最佳化等級 | 看「真實」的組合語言、量效能 |
| `-fsanitize=address` | AddressSanitizer（ASan） | 抓越界、use-after-free、double free |
| `-fsanitize=undefined` | UndefinedBehaviorSanitizer（UBSan） | 抓有號溢位、非法位移、null 解參考… |
| `-fno-omit-frame-pointer` | 保留 frame pointer | 讓 sanitizer 的 stack trace 更完整 |

預設標準可以這樣查：

```
$ echo | gcc -dM -E -x c - | grep STDC_VERSION
#define __STDC_VERSION__ 201710L
$ echo | clang -dM -E -x c - | grep STDC_VERSION
#define __STDC_VERSION__ 201710L
```

`-dM -E` 的意思是「只跑前置處理器，並把所有預先定義的巨集倒出來」。`201710L` 就是 C17，對照表見 [Ch 4](./04-reading-the-standard.md)。

## 底層機制：sanitizer 怎麼「看見」越界？

先看一支 off-by-one 的程式：

```c
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
    int *a = malloc(4 * sizeof(int));
    for (int i = 0; i <= 4; i++)   /* off-by-one: i == 4 越界 */
        a[i] = i * 10;
    printf("a[3] = %d\n", a[3]);
    free(a);
    return 0;
}
```

一般編譯，什麼事都沒有：

```
$ gcc -Wall -Wextra -g oob.c -o oob && ./oob
a[3] = 30
exit=0
```

這正是最危險的情況：寫壞了 heap 上別人的資料，但程式照常結束。加上 sanitizer：

```
$ gcc -Wall -Wextra -g -fsanitize=address,undefined oob.c -o oob_asan && ./oob_asan
=================================================================
==770==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x502000000020 at pc 0x60c88ff5f3c1 bp 0x7ffe4d878650 sp 0x7ffe4d878640
WRITE of size 4 at 0x502000000020 thread T0
    #0 0x60c88ff5f3c0 in main /home/ypp/c_sysprog_lab/p0/oob.c:8
    #1 0x7acef2a29d8f in __libc_start_call_main ../sysdeps/nptl/libc_start_call_main.h:58
    #2 0x7acef2a29e3f in __libc_start_main_impl ../csu/libc-start.c:392
    #3 0x60c88ff5f204 in _start (/home/ypp/c_sysprog_lab/p0/oob_asan+0x1204)

0x502000000020 is located 0 bytes to the right of 16-byte region [0x502000000010,0x502000000020)
allocated by thread T0 here:
    #0 0x7acef36b4887 in __interceptor_malloc ../../../../src/libsanitizer/asan/asan_malloc_linux.cpp:145
    #1 0x60c88ff5f2e1 in main /home/ypp/c_sysprog_lab/p0/oob.c:6
    ...
SUMMARY: AddressSanitizer: heap-buffer-overflow /home/ypp/c_sysprog_lab/p0/oob.c:8 in main
Shadow bytes around the buggy address:
  ...
=>0x0a047fff8000: fa fa 00 00[fa]fa fa fa fa fa fa fa fa fa fa fa
  ...
Shadow byte legend (one shadow byte represents 8 application bytes):
  Addressable:           00
  Partially addressable: 01 02 03 04 05 06 07
  Heap left redzone:       fa
  Freed heap region:       fd
  ...
==770==ABORTING
exit=1
```

（位址每次執行不同；`...` 是省略的重複行。）讀這份報告的順序：

1. **第一行**：錯誤種類 `heap-buffer-overflow`
2. **`WRITE of size 4`** + `#0 ... oob.c:8`：在第 8 行寫了 4 bytes（一個 `int`）
3. **`0 bytes to the right of 16-byte region`**：剛好超出一塊 16 bytes 的配置，也就是 `malloc(4 * sizeof(int))`
4. **`allocated by ... oob.c:6`**：那塊記憶體是第 6 行配置的

ASan 的原理叫 **shadow memory**（影子記憶體）：

```
應用程式記憶體（每 8 bytes）          shadow memory（每格 1 byte）
┌────────┬────────┬────────┬────────┐   ┌────┬────┬────┬────┐
│redzone │redzone │ a[0-1] │ a[2-3] │   │ fa │ fa │ 00 │ 00 │
├────────┼────────┼────────┼────────┤   ├────┼────┼────┼────┤
│redzone │redzone │  ...   │        │   │[fa]│ fa │ ...│    │
└────────┴────────┴────────┴────────┘   └────┴────┴────┴────┘
   a[4] 落在這裡 ─┘                          ↑ 00 = 可存取, fa = heap redzone
```

- 編譯器在**每一次記憶體存取前**插入檢查碼：把位址右移 3 位（除以 8）加上一個固定偏移，查 shadow byte
- `malloc` 被 ASan 換掉（報告裡的 `__interceptor_malloc`），每塊配置前後多放一段 **redzone**（紅區），在 shadow 裡標成 `fa`
- 寫到 redzone，shadow 不是 `00`，檢查碼就呼叫報錯函式

所以 ASan 有代價：論文中量測的平均減速約 73%、記憶體用量約 3.4 倍（見延伸閱讀的 ASan 論文；這是 2012 年 SPEC CPU2006 的數字，實際依程式而定）。它是**測試期**工具，不是上線用的防護。

UBSan 管的是另一類錯誤：語言層面的未定義行為（undefined behavior，規格書沒有定義結果的操作，[Ch 34](./34-undefined-behavior.md) 專章討論）。例如有號整數溢位：

```c
#include <limits.h>
#include <stdio.h>

int main(int argc, char *argv[])
{
    (void) argv;
    int x = INT_MAX - 1 + argc;   /* argc == 1 時 x == INT_MAX */
    int y = x + 1;                /* 有號溢位：未定義行為 */
    printf("%d\n", y);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g -fsanitize=undefined ovf.c -o ovf && ./ovf | cat
-2147483648
ovf.c:8:9: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented in type 'int'
exit=0
```

為什麼用 `argc` 湊出 `INT_MAX`？如果寫成 `int x = INT_MAX;`，編譯器在編譯期就能算出 `x + 1`，可能直接給警告或把整段算掉，示範不出執行期的行為。用執行期才知道的 `argc` 擋住常數摺疊（constant folding）。

兩個觀察：UBSan 預設**報錯後繼續執行**（exit=0），要它停下來加 `-fno-sanitize-recover=undefined`；輸出順序看起來顛倒，是因為 stdout 接到管線（`| cat`）時是全緩衝，`printf` 的內容等到程式結束才寫出，而 UBSan 的訊息走無緩衝的 stderr。在終端機直接執行，你會先看到 `runtime error` 那行。

## 看組合語言：`-S`、`objdump` 與 Compiler Explorer

```c
int sum(int n)
{
    int s = 0;
    for (int i = 1; i <= n; i++)
        s += i;
    return s;
}
```

用 `-S` 叫 gcc 停在組合語言（`-masm=intel` 用 Intel 語法，`-fno-asynchronous-unwind-tables` 去掉 `.cfi` 雜訊，再用 grep 濾掉組譯指示）：

```
$ gcc -O0 -S -fno-asynchronous-unwind-tables -masm=intel sum.c -o -
sum:
	endbr64
	push	rbp
	mov	rbp, rsp
	mov	DWORD PTR -20[rbp], edi      ; n 存到 stack
	mov	DWORD PTR -8[rbp], 0         ; s = 0
	mov	DWORD PTR -4[rbp], 1         ; i = 1
	jmp	.L2
.L3:
	mov	eax, DWORD PTR -4[rbp]
	add	DWORD PTR -8[rbp], eax       ; s += i
	add	DWORD PTR -4[rbp], 1         ; i++
.L2:
	mov	eax, DWORD PTR -4[rbp]
	cmp	eax, DWORD PTR -20[rbp]      ; i <= n ?
	jle	.L3
	mov	eax, DWORD PTR -8[rbp]       ; return s
	pop	rbp
	ret
```

（`;` 後的註解是本書加的。）`-O0` 下每個變數都住在 stack 上，每次使用都從記憶體讀、寫回記憶體——這就是為什麼 `-O0` 好除錯。換成 `-O2`：

```
sum:
	endbr64
	test	edi, edi
	jle	.L4
	add	edi, 1
	mov	eax, 1
	xor	r8d, r8d
.L3:
	add	r8d, eax
	add	eax, 1
	cmp	eax, edi
	jne	.L3
	mov	eax, r8d
	ret
.L4:
	xor	r8d, r8d
	mov	eax, r8d
	ret
```

變數全進了暫存器，迴圈只剩四道指令。再看 clang 14 `-O2`：

```
sum:
	test	edi, edi
	jle	.LBB0_1
	lea	eax, [rdi - 1]
	lea	ecx, [rdi - 2]
	imul	rcx, rax
	shr	rcx
	lea	eax, [rcx + 2*rdi]
	add	eax, -1
	ret
.LBB0_1:
	xor	eax, eax
	ret
```

迴圈不見了。clang 認出這是等差級數，換成閉合公式：`(n-1)(n-2)/2 + 2n - 1`，展開正好是 `n(n+1)/2`。這種轉換合法的前提之一，是有號溢位屬未定義行為，編譯器可以假設 `s += i` 永遠不溢位。這是 [Ch 37](./37-compiler-optimization.md)（最佳化）與 [Ch 34](./34-undefined-behavior.md)（UB）的伏筆。

`endbr64` 是什麼？那是 Intel CET（Control-flow Enforcement Technology）的間接跳躍落點標記，Ubuntu 的 gcc 預設開 `-fcf-protection`，所以每個函式開頭都有。在不支援 CET 的 CPU 上它等同 `nop`。[Ch 14](./14-function-call-security.md) 會談它防的是什麼。

已經編好的執行檔，用 `objdump -d -M intel ./prog` 反組譯。不想在本機折騰時，[Compiler Explorer](https://godbolt.org/)（godbolt.org，Matt Godbolt 開發）可以在瀏覽器裡同時對照多個編譯器、多個版本的輸出，滑鼠移到 C 程式碼的某行，對應的組合語言會亮起來。jserv 原文裡的 godbolt 範例就是這樣用的。

## 一份 Makefile 範本

本課的實驗多是「一個 `.c` 一個執行檔」，下面這份 Makefile 夠用了：

```makefile
# 通用小專案 Makefile：每個 .c 編成同名執行檔
CC      = gcc
CFLAGS  := -std=c11 -Wall -Wextra -Wpedantic -g -O0
SANFLAGS:= -fsanitize=address,undefined -fno-omit-frame-pointer

SRCS := $(wildcard *.c)
BINS := $(SRCS:.c=)

.PHONY: all asan clean
all: $(BINS)

%: %.c
	$(CC) $(CFLAGS) $< -o $@

asan: CFLAGS += $(SANFLAGS)
asan: clean all

clean:
	rm -f $(BINS)
```

（recipe 那一行開頭必須是 Tab，不能是空白。）實測：

```
$ make
gcc -std=c11 -Wall -Wextra -Wpedantic -g -O0 hello.c -o hello
gcc -std=c11 -Wall -Wextra -Wpedantic -g -O0 oob.c -o oob
$ make asan
rm -f hello oob
gcc -std=c11 -Wall -Wextra -Wpedantic -g -O0 -fsanitize=address,undefined -fno-omit-frame-pointer hello.c -o hello
gcc -std=c11 -Wall -Wextra -Wpedantic -g -O0 -fsanitize=address,undefined -fno-omit-frame-pointer oob.c -o oob
$ make CC=clang clean all
rm -f hello oob
clang -std=c11 -Wall -Wextra -Wpedantic -g -O0 hello.c -o hello
clang -std=c11 -Wall -Wextra -Wpedantic -g -O0 oob.c -o oob
```

幾個設計決策：

- `asan: CFLAGS += ...` 是 **target-specific variable**：只在建 `asan` 這個目標（以及它的前置條件）時生效，所以 `all` 底下的每個執行檔都吃到 sanitizer 旗標
- `%: %.c` 是 pattern rule；`$<` 是第一個前置條件（`.c` 檔），`$@` 是目標
- `.PHONY` 告訴 make 這些名字不是檔案，否則目錄裡剛好有個叫 `clean` 的檔案時，`make clean` 會什麼都不做
- `make CC=clang` 在命令列給的變數會覆蓋 Makefile 內的設定

## C23：用 Docker 跑新版編譯器

gcc 11 不支援 C23 的大部分新特徵。不想升級系統編譯器時，用 Docker Hub 上的官方 `gcc` 映像。

> 路徑掛載的坑：作者環境裡，在 WSL 內執行 `docker run -v "$PWD":/src`，容器看不到檔案（`cc1: fatal error: c23.c: No such file or directory`），連 `/mnt/c/...` 底下的目錄也一樣——這通常代表 Docker Desktop 的 Settings → Resources → WSL integration 沒有對這個 distro 啟用，WSL 裡的 `docker` 連到的不是能看到 WSL 檔案系統的那個引擎。本節的實測因此改從 **Windows 端的 Git Bash** 執行，掛載 Windows 目錄。如果你的 WSL integration 設定正確，WSL 內用 `-v "$PWD":/src` 即可。

範例程式：

```c
#include <stdio.h>
#include <stdbit.h>

int main(void)
{
    constexpr int N = 0b1010'0000;   /* C23：constexpr、二進位字面值、數字分隔符 */
    auto x = N;                      /* C23：auto 型別推導 */
    int *p = nullptr;                /* C23：nullptr */
    bool ok = (p == nullptr);        /* C23：bool 成為關鍵字 */
    printf("__STDC_VERSION__ = %ldL\n", __STDC_VERSION__);
    printf("N = %d, x = %d, ok = %d\n", N, x, ok);
    printf("stdc_count_ones(N) = %u\n", stdc_count_ones((unsigned) N));
    return 0;
}
```

```
# Windows 端 Git Bash：pwd -W 給出 C:/... 形式的路徑；MSYS_NO_PATHCONV=1 阻止 Git Bash 改寫 /src
$ MSYS_NO_PATHCONV=1 docker run --rm -v "$(pwd -W)":/src -w /src gcc:14 \
      sh -c 'gcc -std=c23 -Wall -Wextra c23.c -o c23 && ./c23'
__STDC_VERSION__ = 202000L
N = 160, x = 160, ok = 1
stdc_count_ones(N) = 2
```

`-v "$PWD":/src` 把目前目錄掛進容器，`-w /src` 設工作目錄，`--rm` 跑完刪容器。三個實測觀察：

1. **gcc 14 的 `-std=c23` 回報 `202000L`**，不是標準規定的 `202311L`。gcc 14 還把 C23 支援視為未完成，用了暫定值。換 `gcc:15`（實測 15.3.0），**不加任何 `-std`** 就得到 `202311L`：gcc 15 把預設標準改成 `gnu23`。同一份程式在不同 gcc 版本預設語意不同，這就是本書一再強調「釘版本」的原因。
2. **`stdc_count_ones(N)` 原本沒加 `(unsigned)` 時編不過**：`error: argument 1 in call to function '__builtin_stdc_count_ones' has signed type`。C23 的 `<stdbit.h>` 泛型函式只接受無號整數。
3. 用 `-std=c17` 編同一份檔案，第一個錯誤就是 `'constexpr' undeclared`：舊標準下它只是普通識別字。

## 對比與取捨

| 工具 | 抓什麼 | 代價 | 什麼時候用 |
|---|---|---|---|
| `-Wall -Wextra` | 編譯期看得出的可疑寫法 | 幾乎零 | **永遠開** |
| ASan | 越界、UAF、double free、stack 越界 | 執行變慢、記憶體數倍 | 測試、除錯、fuzzing |
| UBSan | 有號溢位、非法位移、未對齊、null 解參考 | 較 ASan 輕 | 同上，可和 ASan 一起開 |
| Valgrind memcheck | 越界、洩漏、未初始化讀取 | 減速一到兩個數量級，**不用重編** | 拿不到原始碼或不能重編時 |
| GDB | 任何你想停下來看的狀態 | 需要人力 | 理解行為、追 crash |

ASan 和 Valgrind 的根本差異：ASan 在**編譯期**插檢查碼，Valgrind 在**執行期**把整支程式放進虛擬 CPU 動態翻譯。所以 ASan 快很多，但只能看到有被插樁的程式碼；Valgrind 慢，但連沒原始碼的程式庫也能檢查。另外，ASan 不能和 Valgrind 同時用，也不能和 `-fsanitize=thread`（TSan）同時開。

## 踩雷集錦

1. **「`-Wall` 就是全部警告」**：不是。`-Wsign-compare`、`-Wunused-parameter` 在 C 裡要 `-Wextra`；`-Wconversion` 連 `-Wextra` 都不包含。真的想要全部，clang 有 `-Weverything`（會很吵），gcc 沒有對應旗標。
2. **「用 `CC ?= gcc` 讓使用者可以用環境變數覆蓋」**：本章第一版 Makefile 就這樣寫，結果 `make` 印出的是 `cc ...` 而不是 `gcc ...`。原因：GNU make 內建 `CC` 的預設值是 `cc`，`?=` 只在變數**未定義**時才賦值，而 `CC` 永遠有定義。要用環境變數覆蓋，得寫 `CC = gcc` 再搭配 `make -e`，或直接在命令列 `make CC=clang`。
3. **「`-O0` 下沒問題，`-O2` 出錯，是編譯器的 bug」**：九成以上是你的程式有未定義行為，`-O2` 的最佳化讓它現形。先開 `-fsanitize=undefined` 跑一次再怪編譯器。
4. **「sanitizer 沒報錯，所以沒有記憶體錯誤」**：sanitizer 只檢查**實際執行到**的路徑。測試沒覆蓋的分支，ASan 看不到。而且 ASan 只能抓「越界到 redzone」的存取；跳得夠遠、直接落在另一塊合法配置裡的越界，它看不出來。
5. **「`-g` 會讓程式變慢」**：`-g` 只是多寫一段 DWARF 除錯資訊到執行檔（檔案變大），**不改變產生的機械碼**。慢的是 `-O0`，不是 `-g`。發佈版可以用 `-O2 -g` 再 `strip` 或分離除錯符號。

## 進階：再往深一層

- **把 sanitizer 設成預設**：在 `~/.bashrc` 設 `export ASAN_OPTIONS=detect_leaks=1:abort_on_error=1`、`export UBSAN_OPTIONS=print_stacktrace=1`。各選項的完整列表在 [google/sanitizers wiki](https://github.com/google/sanitizers/wiki/AddressSanitizerFlags)。
- **`-fanalyzer`**：gcc 10 起內建靜態分析器，能在編譯期抓一部分 double free、leak、null 解參考，不必執行程式。對照 clang 的 `scan-build`。
- **`-save-temps`**：一次保留所有中間產物（`.i`、`.s`、`.o`），[Ch 1](./01-life-of-a-c-program.md) 會逐一拆開。
- **`gcc -v`**：顯示 gcc 實際呼叫了哪些子程式（`cc1`、`as`、`collect2`/`ld`）與標頭檔搜尋路徑。gcc 本身只是一個驅動程式（driver），Ch 1 與 [Ch 36](./36-gnu-toolchain.md) 會看到它在背後做了什麼。
- **為什麼 `-O2` 不是 `-O3`**：`-O3` 開更積極的向量化與內聯，程式碼膨脹，不一定更快。Linux 核心長期以 `-O2` 編譯（也提供 `-Os` 選項）。效能結論要量，不要猜。

## 動手練習

1. 把 `oob.c` 的 `<= 4` 改成 `< 4`，確認 ASan 不再報錯；再改成 `<= 5`，ASan 報的 offset 有什麼變化？
2. 在 `warn.c` 裡加一行 `int unused;`，分別用 `-Wall`、`-Wall -Wextra` 編，觀察哪個旗標開始報 `-Wunused-variable`。
3. 到 godbolt.org 貼上 `sum`，分別選 x86-64 gcc 11 和 clang 14 加 `-O2`，確認你看到和本章一樣的輸出；再把參數型別改成 `unsigned n`，看 clang 的閉合公式還在不在，想想為什麼。
4. 在 Makefile 加一個 `release` 目標，用 `-O2 -DNDEBUG`。

## 本章重點整理

- 警告、sanitizer、組合語言、GDB 是本課四件基本工具；預設全部要「會開、會讀」
- ASan 靠 shadow memory 與 redzone，在每次記憶體存取前插檢查；UBSan 檢查語言層的未定義行為
- `-O0` 讓變數住在記憶體、好除錯；`-O2` 才是編譯器真正的樣子
- 編譯器的預設語言標準會隨版本改變（gcc 11 是 `gnu17`、gcc 15 是 `gnu23`），所以要用 `-std=` 釘死

## 自我檢核

- [ ] 不看本章，能說出 `-Wall`、`-Wextra`、`-g`、`-O0`、`-fsanitize=address` 各自做什麼，以及哪一個會改變機械碼
- [ ] 能向同事解釋 ASan 為什麼能發現「沒有當場 crash」的越界寫入，以及它為什麼抓不到所有越界
- [ ] 拿到一份 ASan 報告，知道先看哪三行
- [ ] 能說明為什麼 clang 能把 `sum` 的迴圈消掉，以及這和「有號溢位是 UB」的關係
- [ ] 說得出 ASan 和 Valgrind 各自適合的情境

## 延伸閱讀

### 論文

- **[AddressSanitizer: A Fast Address Sanity Checker](https://www.usenix.org/conference/atc12/technical-sessions/presentation/serebryany)** — Serebryany, Bruening, Potapenko, Vyukov，USENIX ATC（2012）
  - **核心貢獻**：用 1:8 的 shadow memory 對映與 redzone，在編譯期插樁，以約 73% 的平均減速做到 heap/stack/global 越界與 UAF 偵測
  - **讀哪裡**：Section 3（shadow memory 的位址換算與插樁程式碼）是本章「底層機制」的原始出處；Section 4 的效能數字可略讀
  - **前提知識**：知道 heap、stack 是什麼即可；看完 Ch 3 後讀更順

### 官方文件

- **[GCC Manual：Warning Options](https://gcc.gnu.org/onlinedocs/gcc/Warning-Options.html) 與 [Instrumentation Options](https://gcc.gnu.org/onlinedocs/gcc/Instrumentation-Options.html)**
  - **讀哪裡**：Warning Options 裡 `-Wall` 與 `-Wextra` 各自列出它開了哪些子警告，是踩雷第 1 條的依據；Instrumentation Options 的 `-fsanitize=` 段落列出 UBSan 的所有子檢查
  - **注意**：線上版是最新 gcc 的文件，和 gcc 11 可能有出入；用 `man gcc` 看本機版本最準
- **[Clang：UndefinedBehaviorSanitizer](https://clang.llvm.org/docs/UndefinedBehaviorSanitizer.html)**
  - **讀哪裡**：「Available checks」表格，一條條對照 C 的哪種 UB 有檢查、哪種沒有
  - **為什麼值得讀**：比 gcc 文件寫得更完整，而 gcc 的 UBSan 與 clang 共用執行期程式庫，大部分選項通用
- **[GNU make Manual](https://www.gnu.org/software/make/manual/make.html)**
  - **讀哪裡**：6.2「The Two Flavors of Variables」（`=` 與 `:=` 的差異）、6.11「Target-specific Variable Values」、10.5「Pattern Rules」、10.3「Variables Used by Implicit Rules」（`CC` 預設為 `cc` 的出處）

### 部落格 / 技術文章

- **[Compiler Explorer](https://godbolt.org/)** 與 Matt Godbolt 的演講 **[What Has My Compiler Done for Me Lately?](https://www.youtube.com/watch?v=bSkpMdDe4g4)**（CppCon 2017）
  - **這篇說什麼**：工具作者本人示範如何讀編譯器輸出，大量「編譯器比你想的聰明」的例子（包含本章的等差級數）
  - **前提知識**：看得懂 `mov`、`add`、`ret` 即可，不懂的指令他會講

本章架好了工具，下一章用這些工具把一支 hello world 從原始碼一路拆到行程結束，順便看清楚整門課要走的路。

→ [Ch 1 一支 C 程式的一生：從原始碼到行程](./01-life-of-a-c-program.md)
