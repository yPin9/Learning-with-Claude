# Ch 1 — 一支 C 程式的一生：從原始碼到行程

> **目標**：把 `gcc hello.c` 背後的七個站（前置處理、編譯、組譯、連結、載入、`_start`、`main` 與結束）逐站拆開，每一站都用指令親眼看到中間產物；同時把這張地圖當作整門課的路線圖，知道每一站在後面哪一章深入。

> **對應原文**：[你所不知道的 C 語言（系列目錄）](https://hackmd.io/@sysprog/c-programming) 的編譯器、連結器、動態連結器、執行階段程式庫各篇導讀，以及 [開發工具和規格標準篇](https://hackmd.io/@sysprog/c-standards) 的「讀規格書可大幅省去臆測」（`main` 的定義）等節

> **環境**：WSL2 Ubuntu 22.04.3 LTS（x86_64）、gcc 11.4.0、GNU binutils 2.38、glibc 2.35、gdb 12.1、strace。

## 為什麼需要這個？

大多數人對 C 程式的認識是：「寫好 `hello.c`，`gcc hello.c`，`./a.out`」。中間發生了什麼是一個黑盒子。黑盒子平常不礙事，直到你遇到這些問題：

- `undefined reference to 'sqrt'`——明明 `#include <math.h>` 了啊？
- 執行檔明明在那裡，卻說 `No such file or directory`
- 全域變數 `int table[1000000];` 為什麼不會讓執行檔變大？
- `main` 是第一個執行的函式嗎？`main` 回傳之後去了哪？

每個問題都對應黑盒子裡的一個站。jserv 的系列把這些站拆成編譯器兩篇、連結器篇、動態連結器篇、執行階段程式庫篇共五篇講座，本課對應 Part 6（Ch 36–41）。這一章先走一遍全景，讓你在讀到任何一章時，都知道自己站在地圖的哪裡。

## 先建立直覺

把一支 C 程式的一生想成一本書的出版：

```
 作者手稿      編輯整理        翻譯           排版成頁        裝訂成書        上架         讀者翻開
 hello.c  →  hello.i   →    hello.s   →    hello.o    →    hello     →  行程映像  →  main() 執行
          前置處理器      編譯器本體       組譯器          連結器         載入器
          cpp (cc1 -E)    cc1              as              ld (collect2)  kernel + ld.so
          展開 #include   C → 組合語言     組合語言 →      合併 .o 與      execve 建立
          展開巨集                         機械碼          程式庫、        定址空間
                                                          填好位址
```

這個比喻在一個地方會誤導你：書裝訂好就固定了，但 Linux 上預設的執行檔是**動態連結**的——`printf` 的程式碼不在 `hello` 裡，要等到執行時才由動態連結器（`ld.so`）從 `libc.so.6` 接上。好比書裡有幾頁寫著「此處請見圖書館另一本書第 N 頁」，上架時才幫你夾進去。

規格書也有對應的描述：N1570 5.1.1.2 把翻譯分成 8 個「翻譯階段」（translation phases）。第 1–4 階段大致是前置處理器的工作（處理換行接續、切 token、執行前置處理指令），第 5–7 階段是編譯，第 8 階段是連結（"All external object and function references are resolved"）。**規格書不規定要分成幾個程式來做**，gcc 把它拆成 `cc1`、`as`、`ld` 三支程式，是實作選擇。

## 本章的實驗對象

```c
#include <stdio.h>

#define GREETING "hello, world"

int counter = 42;          /* 有初值的全域變數 → .data */
static int zeroed;         /* 沒有初值 → .bss */

int main(void)
{
    zeroed = counter + 1;
    printf("%s %d\n", GREETING, zeroed);
    return 0;
}
```

刻意放了一個巨集、一個有初值的全域變數、一個沒初值的 `static` 變數、一個外部函式呼叫，每一個都會在某一站留下痕跡。

## 第 1 站：前置處理（preprocess）

```
$ gcc -E hello.c -o hello.i
$ wc -l hello.i
751 hello.i
$ tail -12 hello.i


# 5 "hello.c"
int counter = 42;
static int zeroed;

int main(void)
{
    zeroed = counter + 1;
    printf("%s %d\n", "hello, world", zeroed);
    return 0;
}
```

十三行的原始碼變成 751 行。多出來的幾乎全是 `<stdio.h>`（以及它再 include 的檔案）被原封不動貼進來。觀察三件事：

1. `GREETING` 不見了，換成 `"hello, world"`：巨集是**文字替換**，編譯器本體從來沒看過 `GREETING` 這個名字
2. 註解不見了：在翻譯階段 3 每個註解被換成一個空白
3. `# 5 "hello.c"` 這種 **linemarker**：告訴後面的編譯器「接下來這行是 hello.c 的第 5 行」，錯誤訊息的行號才對得上

`printf` 的宣告就在那 700 多行裡：

```
$ grep -n "printf (" hello.i | head -3
413:extern int fprintf (FILE *__restrict __stream,
419:extern int printf (const char *__restrict __format, ...);
421:extern int sprintf (char *__restrict __s,
```

注意：這只是**宣告**（告訴編譯器 `printf` 長什麼樣子），不是**定義**（`printf` 的程式碼）。`#include` 不會把函式庫的程式碼帶進來，這是「include 了 `<math.h>` 還是 `undefined reference to 'sqrt'`」的答案：標頭檔只給宣告，定義在 `libm`，要在連結時 `-lm`。實測 gcc 11：`printf("%f\n", sqrt(2.0))` 不加 `-lm` 也能連結並印出 `1.414214`，因為常數引數在編譯期就算掉了；改成 `sqrt((double) argc + 1)`，連 `-O2` 都得到 `undefined reference to 'sqrt'`（gcc 雖然會用 `sqrtsd` 指令，但仍保留呼叫 `sqrt` 的慢速路徑處理負數與 `errno`），加 `-lm` 才通過。「有時要 `-lm`、有時不用」的困惑就是這樣來的。

> 深入：[Ch 27](./27-preprocessor-basics.md)、[Ch 28](./28-preprocessor-advanced.md) 前置處理器。

## 第 2 站：編譯（compile）

```
$ gcc -S -O0 -fno-asynchronous-unwind-tables hello.c -o hello.s
```

節錄 `hello.s` 的重點（AT&T 語法，gcc 預設）：

```
	.globl	counter
	.data
	.align 4
	.type	counter, @object
	.size	counter, 4
counter:
	.long	42
	.local	zeroed
	.comm	zeroed,4,4
	.section	.rodata
.LC0:
	.string	"hello, world"
.LC1:
	.string	"%s %d\n"
	.text
	.globl	main
	.type	main, @function
main:
	endbr64
	pushq	%rbp
	movq	%rsp, %rbp
	movl	counter(%rip), %eax
	addl	$1, %eax
	movl	%eax, zeroed(%rip)
	movl	zeroed(%rip), %eax
	movl	%eax, %edx
	leaq	.LC0(%rip), %rax
	movq	%rax, %rsi
	leaq	.LC1(%rip), %rax
	movq	%rax, %rdi
	movl	$0, %eax
	call	printf@PLT
	movl	$0, %eax
	popq	%rbp
	ret
```

C 的概念在這裡第一次被翻譯成機器的概念：

| C 的東西 | 組合語言裡的樣子 |
|---|---|
| `int counter = 42;` | `.data` 區段裡一個 4 bytes 的 `.long 42` |
| `static int zeroed;` | `.local` + `.comm`：只要求保留 4 bytes，不存內容 |
| 字串常值 | `.rodata`（read-only data）裡的 `.string` |
| `static` | `.local`：符號不對外公開；`counter` 則是 `.globl` |
| 呼叫 `printf` 的三個參數 | 依 x86-64 System V ABI 放進 `%rdi`、`%rsi`、`%edx` |
| `movl $0, %eax` 在 call 之前 | ABI 規定：呼叫可變參數函式時 `%al` 放用到的向量暫存器數，這裡是 0 |
| `return 0;` | 回傳值放在 `%eax` |

`printf@PLT` 的 PLT（Procedure Linkage Table）是動態連結的跳板，第 5 站再說。

> 深入：[Ch 11](./11-process-layout-and-abi.md)（ABI 與 stack frame）、[Ch 36](./36-gnu-toolchain.md)（GNU toolchain）、[Ch 37](./37-compiler-optimization.md)（最佳化）、[Ch 38](./38-compiler-construction.md)（自己寫編譯器）。

## 第 3 站：組譯（assemble）

```
$ gcc -c hello.s -o hello.o
$ file hello.o
hello.o: ELF 64-bit LSB relocatable, x86-64, version 1 (SYSV), not stripped
$ nm hello.o
0000000000000000 D counter
0000000000000000 T main
                 U printf
0000000000000000 b zeroed
```

`hello.o` 是 **ELF**（Executable and Linkable Format）格式的**可重定位目的檔**（relocatable object file）。`nm` 列出符號表，第二欄是種類：

- `T`：在 text（程式碼）區段，大寫代表全域可見
- `D`：在 data 區段，全域
- `b`：在 bss 區段，**小寫代表 local**——正是 `static` 的效果
- `U`：undefined，這個檔案用到但沒有定義，等連結器去找

所有位址都是 `0`：每個符號只知道自己在**所屬區段內的偏移**，最終位址還沒決定。看機械碼就更清楚：

```
$ objdump -d -r -M intel hello.o
0000000000000000 <main>:
   0:	f3 0f 1e fa          	endbr64
   4:	55                   	push   rbp
   5:	48 89 e5             	mov    rbp,rsp
   8:	8b 05 00 00 00 00    	mov    eax,DWORD PTR [rip+0x0]        # e <main+0xe>
			a: R_X86_64_PC32	counter-0x4
   ...
  38:	e8 00 00 00 00       	call   3d <main+0x3d>
			39: R_X86_64_PLT32	printf-0x4
   ...
```

`8b 05 00 00 00 00` 後面四個 `00` 是**空格子**，組譯器還不知道 `counter` 在哪。底下那行 `R_X86_64_PC32 counter-0x4` 是一筆**重定位項目**（relocation entry）：「連結時，請把 `counter` 相對於這裡的距離減 4，填進 offset 0xa 的 4 bytes」。`call` 那四個 `00` 同理，等著填 `printf`。為什麼要 `-4`？因為 `rip` 相對定址是以「下一道指令的位址」為基準，而那四 bytes 本身到指令結尾還有 4 bytes。

> 深入：[Ch 39](./39-static-linking.md)（符號解析與重定位）。

## 第 4 站：連結（link）

`gcc -v` 會顯示 gcc 驅動程式實際呼叫了什麼。把連結那行（`collect2`，gcc 包在 `ld` 外面的一層）拆開，只留重點：

```
$ gcc -v hello.o -o hello 2>&1 | grep collect2 | tr ' ' '\n' | grep -E 'crt|\.o$|-l|dynamic-linker|ld-linux'
...
-dynamic-linker
/lib64/ld-linux-x86-64.so.2
/usr/lib/gcc/x86_64-linux-gnu/11/../../../x86_64-linux-gnu/Scrt1.o
/usr/lib/gcc/x86_64-linux-gnu/11/../../../x86_64-linux-gnu/crti.o
/usr/lib/gcc/x86_64-linux-gnu/11/crtbeginS.o
...
hello.o
-lgcc
-lgcc_s
-lc
-lgcc
-lgcc_s
/usr/lib/gcc/x86_64-linux-gnu/11/crtendS.o
/usr/lib/gcc/x86_64-linux-gnu/11/../../../x86_64-linux-gnu/crtn.o
```

你只給了 `hello.o`，連結器實際收到的卻是一長串：

```
  Scrt1.o   ← 提供 _start（真正的程式進入點）；S 代表給 PIE 用的版本
  crti.o    ← .init/.fini 區段的開頭
  crtbeginS.o
  hello.o   ← 你的程式
  -lc       ← glibc（printf 在這裡）
  -lgcc     ← 編譯器輔助程式庫（例如 32 位元平台上的 64 位元除法）
  crtendS.o
  crtn.o    ← .init/.fini 區段的結尾
```

這些 `crt*.o`（C runtime）就是「`main` 不是第一個執行的函式」的物證。連結器做兩件事：**符號解析**（每個 `U` 都要找到唯一的定義）和**重定位**（決定每個區段的位址，把第 3 站的空格子填好）。

```
$ file hello
hello: ELF 64-bit LSB pie executable, x86-64, version 1 (SYSV), dynamically linked, interpreter /lib64/ld-linux-x86-64.so.2, ...
$ nm hello | grep -E " main| _start|counter|zeroed|printf|__libc_start"
                 U __libc_start_main@GLIBC_2.34
0000000000001060 T _start
0000000000004010 D counter
0000000000001149 T main
                 U printf@GLIBC_2.2.5
0000000000004018 b zeroed
$ readelf -h hello | grep -E "Type|Entry"
  Type:                              DYN (Position-Independent Executable file)
  Entry point address:               0x1060
```

現在每個符號都有位址了，但 `printf` 還是 `U`，只是多了版本標籤 `@GLIBC_2.2.5`：它要到執行期才由動態連結器解析。**進入點（Entry point）是 `0x1060`，也就是 `_start`，不是 `main`**。

位址從 `0x1000` 附近開始很小，是因為這是 **PIE**（Position-Independent Executable）：Ubuntu 的 gcc 預設 `-pie`，載入時整支程式會被搬到一個隨機的基底位址（ASLR），這些數字只是相對於基底的偏移。

`size` 指令顯示各區段大小：

```
$ size hello
   text	   data	    bss	    dec	    hex	filename
   1422	    604	     12	   2038	    7f6	hello
```

`.bss` 在檔案裡**不佔空間**，只記錄「載入時需要多少 bytes 的零」。這是「`int table[1000000];` 不會讓執行檔變大」的答案。N1570 6.7.9p10 規定沒有明確初始化的 static 儲存期物件要初始化為零（算術型別）或 null pointer（指標型別），`.bss` 是實作這條規定最省空間的方法。

靜態連結做個對比：

```
$ gcc -static -O0 hello.c -o hello_static
$ file hello_static
hello_static: ELF 64-bit LSB executable, x86-64, version 1 (GNU/Linux), statically linked, ...
$ ls -l hello hello_static    （只留大小欄）
17320 hello
900344 hello_static
```

17 KB 對 900 KB：靜態版把用到的 glibc 程式碼整個複製進來了。

> 深入：[Ch 39](./39-static-linking.md)（靜態連結）、[Ch 40](./40-dynamic-linking.md)（動態連結、PLT/GOT）。

## 第 5 站：載入（load）

在 shell 裡打 `./hello`，shell 會 `fork` 出子行程，子行程呼叫 `execve("./hello", ...)`。之後發生的事，用 `strace` 看系統呼叫（節錄並截斷過長的行）：

```
$ strace ./hello 2>&1 >/dev/null
execve("./hello", ["./hello"], 0x7ffd54b46848 /* 23 vars */) = 0
brk(NULL)                               = 0x633cb3225000
...
access("/etc/ld.so.preload", R_OK)      = -1 ENOENT (No such file or directory)
openat(AT_FDCWD, "/etc/ld.so.cache", O_RDONLY|O_CLOEXEC) = 3
...
openat(AT_FDCWD, "/lib/x86_64-linux-gnu/libc.so.6", O_RDONLY|O_CLOEXEC) = 3
read(3, "\177ELF\2\1\1\3\0\0\0\0\0\0\0\0\3\0>\0\1\0\0\0P\237\2\0\0\0\0\0"..., 832) = 832
...
mmap(NULL, 2264656, PROT_READ, MAP_PRIVATE|MAP_DENYWRITE, 3, 0) = 0x7cb7c5200000
mmap(0x7cb7c5228000, 1658880, PROT_READ|PROT_EXEC, MAP_PRIVATE|MAP_FIXED|MAP_DENYWRITE, 3, ...
...
mprotect(0x7cb7c5416000, 16384, PROT_READ) = 0
...
write(1, "hello, world 43\n", 16)       = 16
exit_group(0)                           = ?
+++ exited with 0 +++
```

（位址每次執行不同。）時間軸是這樣：

```
 execve("./hello")
   │  核心讀 ELF header，看到 PT_INTERP = /lib64/ld-linux-x86-64.so.2
   │  核心把 hello 與 ld.so 都映射進記憶體，建立 stack（argc、argv、envp、auxv）
   │  跳到 ld.so 的進入點（不是 hello 的！）
   ▼
 ld.so（動態連結器）
   │  查 /etc/ld.so.preload、/etc/ld.so.cache
   │  openat + mmap 載入 libc.so.6
   │  做重定位、mprotect 把 GOT 等設成唯讀（RELRO）
   │  跳到 hello 的 _start
   ▼
 _start → __libc_start_main → main → printf → write(1, ...)
   ▼
 exit → exit_group(0)
```

第一行 `execve` 之後到 `write` 之前的三十多個系統呼叫，**全部是 `ld.so` 和 glibc 初始化在做事**，你的 `main` 只貢獻了一個 `write`。靜態連結版省掉了 `ld.so` 的工作：

```
$ strace ./hello_static 2>&1 >/dev/null | wc -l
20
$ strace ./hello 2>&1 >/dev/null | wc -l
41
```

`PT_INTERP` 可以用 `readelf -l hello | grep -A1 INTERP` 看到。這也是「執行檔明明存在卻 `No such file or directory`」的典型原因：核心找不到 `PT_INTERP` 指定的那個動態連結器（例如在 Alpine 上執行為 glibc 編的程式），錯誤碼 `ENOENT` 說的是 interpreter 不存在，不是你的檔案。

> 深入：[Ch 15](./15-memory-hierarchy.md)（虛擬記憶體）、[Ch 40](./40-dynamic-linking.md)（`ld.so`）、[Ch 41](./41-c-runtime.md)（從 `execve` 到 `main`）。

## 第 6 站：`_start`

```
$ objdump -d -M intel hello | sed -n '/<_start>:/,/hlt/p'
0000000000001060 <_start>:
    1060:	f3 0f 1e fa          	endbr64
    1064:	31 ed                	xor    ebp,ebp
    1066:	49 89 d1             	mov    r9,rdx
    1069:	5e                   	pop    rsi
    106a:	48 89 e2             	mov    rdx,rsp
    106d:	48 83 e4 f0          	and    rsp,0xfffffffffffffff0
    1071:	50                   	push   rax
    1072:	54                   	push   rsp
    1073:	45 31 c0             	xor    r8d,r8d
    1076:	31 c9                	xor    ecx,ecx
    1078:	48 8d 3d ca 00 00 00 	lea    rdi,[rip+0xca]        # 1149 <main>
    107f:	ff 15 53 2f 00 00    	call   QWORD PTR [rip+0x2f53]        # 3fd8 <__libc_start_main@GLIBC_2.34>
    1085:	f4                   	hlt
```

十幾道指令，逐行翻譯：

| 指令 | 意義 |
|---|---|
| `xor ebp,ebp` | 把 frame pointer 清零，標記「這是呼叫鏈的最外層」，除錯器回溯到這裡就停 |
| `mov r9,rdx` | 核心/ld.so 放在 `rdx` 的是 `rtld_fini`（ld.so 的清理函式），當第 6 個參數傳下去 |
| `pop rsi` | stack 頂端是 `argc`，彈出來當第 2 個參數 |
| `mov rdx,rsp` | 現在 stack 頂端是 `argv` 陣列，它的位址當第 3 個參數 |
| `and rsp,-16` | 把 stack 對齊到 16 bytes，ABI 要求呼叫函式前必須如此 |
| `xor r8d,r8d` / `xor ecx,ecx` | 第 4、5 個參數（init、fini）傳 0：glibc 2.34 起改由 libc 內部處理 |
| `lea rdi,[main]` | **`main` 的位址**當第 1 個參數 |
| `call __libc_start_main` | 把控制權交給 glibc |
| `hlt` | 不該執行到這裡；`__libc_start_main` 不會返回 |

用 GDB 驗證「誰先執行」：

```
$ gdb -q -batch -x g.gdb ./hello     （g.gdb：starti、break _start、break main、continue…）
Program stopped.
0x00007ffff7fe32b0 in _start () from /lib64/ld-linux-x86-64.so.2
...
Breakpoint 1, 0x0000555555555060 in _start ()
_start in section .text of /home/ypp/c_sysprog_lab/p0/life/hello

Breakpoint 2, main () at hello.c:10
10	    zeroed = counter + 1;
```

`starti` 停在**第一道使用者空間指令**，它在 `ld-linux-x86-64.so.2` 的 `_start`，連 `hello` 自己的 `_start` 都還沒輪到。在 `main` 裡印完整呼叫鏈（GDB 預設不顯示 `main` 之上的框架，要開 `set backtrace past-main on`）：

```
#0  main () at hello.c:10
#1  0x00007ffff7c29d90 in __libc_start_call_main (main=main@entry=0x555555555149 <main>, argc=argc@entry=1, argv=argv@entry=0x7fffffffe248) at ../sysdeps/nptl/libc_start_call_main.h:58
#2  0x00007ffff7c29e40 in __libc_start_main_impl (main=0x555555555149 <main>, argc=1, argv=0x7fffffffe248, init=<optimized out>, fini=<optimized out>, rtld_fini=<optimized out>, stack_end=0x7fffffffe238) at ../csu/libc-start.c:392
#3  0x0000555555555085 in _start ()
```

`_start → __libc_start_main_impl → __libc_start_call_main → main`。GDB 底下位址是 `0x5555...` 開頭且每次相同，因為 GDB 預設關閉 ASLR（`set disable-randomization on`）。

> 深入：[Ch 5](./05-gdb-in-practice.md)（GDB）、[Ch 41](./41-c-runtime.md)（`_start` 與 glibc 啟動程式碼）。

## 第 7 站：`main` 與結束

N1570 5.1.2.2.1 規定 hosted 環境的 `main` 有兩種標準形式（`int main(void)` 與 `int main(int argc, char *argv[])`），5.1.2.2.3 規定：`main` 返回等同於以回傳值呼叫 `exit`，而且 C99 起執行到 `main` 結尾的 `}` 等同 `return 0;`。`exit` 會依註冊的**相反順序**呼叫 `atexit` 函式、清空並關閉所有 stdio 串流（7.22.4.4p3–4），最後把狀態交還宿主環境。

```c
#include <stdio.h>
#include <stdlib.h>

static void cleanup(void) { puts("atexit: cleanup()"); }

int main(int argc, char *argv[])
{
    atexit(cleanup);
    printf("main: argc = %d\n", argc);
    if (argc > 1)
        return atoi(argv[1]);
    return 0;
}
```

```
$ ./bye; echo "status=$?"
main: argc = 1
atexit: cleanup()
status=0
$ ./bye 3; echo "status=$?"
main: argc = 2
atexit: cleanup()
status=3
$ ./bye 300; echo "status=$?"
main: argc = 2
atexit: cleanup()
status=44
```

`main` 回傳之後還有程式在跑（`cleanup`），回傳 300 卻得到 44：Linux 的 `exit_group` 系統呼叫只把狀態的**低 8 位元**交給父行程（`waitpid` 的 `WEXITSTATUS`），300 mod 256 = 44。C 標準只保證 `0`、`EXIT_SUCCESS`、`EXIT_FAILURE` 的意義，其他值怎麼傳給宿主環境是實作定義。

> 深入：[Ch 35](./35-stream-io-and-signals.md)（stdio 緩衝與 `exit` 的清空）、[Ch 41](./41-c-runtime.md)。

## 整門課的路線圖

把七站和本課章節對起來：

```
 hello.c ──[1 前置處理]──[2 編譯]──[3 組譯]──[4 連結]──[5 載入]──[6 _start]──[7 main/exit]
             │             │          │         │          │          │            │
           Ch 27-28     Ch 36-38    Ch 36     Ch 39-40   Ch 15,40    Ch 41       Ch 35,41
           巨集          最佳化/                符號/       虛擬記憶體/                stdio/
                         自製編譯器              重定位      ld.so                    signal

 程式「執行中」的世界：
   型別與物件表示法   Ch 2, 7, 16, 21-26
   指標、陣列、函式   Ch 3, 7-10
   stack 與呼叫慣例   Ch 11-14
   heap 與配置器      Ch 15, 17, practice-b
   資料結構          Ch 18-20
   語言機制與手法     Ch 27-35
```

Part 0 的接下來兩章（型別、指標）是讀懂「執行中的世界」的最低限度；Part 1 的 [Ch 4](./04-reading-the-standard.md) 教你查規格書，[Ch 5](./05-gdb-in-practice.md) 把本章用到的 GDB 技巧系統化。

## 對比與取捨

| 面向 | 動態連結（預設） | 靜態連結（`-static`） |
|---|---|---|
| 執行檔大小 | 小（本例 17 KB） | 大（本例 900 KB） |
| 啟動成本 | `ld.so` 要載入、重定位（本例 41 行 strace） | 少（20 行） |
| 更新 libc 安全修補 | 換掉 `.so` 即可 | 每支程式要重新連結 |
| 可攜性 | 依賴目標系統有相容的 libc 與 `ld.so` | 單檔可搬（但 glibc 的 `getaddrinfo` 等功能仍會動態載入 NSS 模組，glibc 官方不建議完全靜態） |
| `LD_PRELOAD` 攔截 | 可以 | 不行 |

## 踩雷集錦

1. **「`main` 是程式第一個執行的函式」**：不是。核心先跳到 `ld.so` 的 `_start`，再到程式的 `_start`，再經 `__libc_start_main` 才到 `main`。C++ 全域物件建構子、`__attribute__((constructor))` 函式也都在 `main` 之前跑。
2. **「`#include <math.h>` 就能用 `sqrt`」**：標頭檔只提供宣告。函式的定義在程式庫裡，連結時要找得到（`-lm`）。而且 `-l` 的**位置**有影響：`gcc -lm x.c` 在某些設定下會失敗，因為連結器由左到右處理，看到 `-lm` 時還沒有任何未定義符號需要它。慣例是 `-l` 放最後。Ch 39 會解釋搜尋順序。
3. **「`static` 是讓變數放在 static 區」**：`static` 在檔案範圍有兩個效果混在一起——儲存期（本來就是 static 儲存期）不變，真正改變的是**連結性**（linkage）變成 internal，`nm` 裡從大寫變小寫。在函式內，`static` 才是改變儲存期。Ch 40 談 linkage。
4. **「`void main()` 也能跑，所以沒差」**：N1570 5.1.2.2.1 只保證 `int` 回傳的兩種形式，其他形式是實作定義。`void main` 在 gcc 下能編譯，但結束狀態是未指定的。
5. **「`nm` 看到位址 `0x1149`，`main` 就在記憶體 0x1149」**：PIE 執行檔的位址是相對於載入基底的偏移，實際位址要加上 ASLR 選的基底（GDB 下是 `0x555555554000`，所以 `main` 在 `0x555555555149`）。

## 進階：再往深一層

- **`gcc -save-temps -v hello.c`**：一次保留 `.i`、`.s`、`.o`，並印出每個子程式的完整命令列（`cc1`、`as`、`collect2`）。試著**手動**依序執行那三行，不透過 `gcc` 驅動程式完成編譯。
- **不用 libc 的程式**：寫一個自己定義 `_start`、用 `syscall` 指令呼叫 `write` 和 `exit` 的程式，以 `gcc -nostdlib -static` 連結。它沒有 `main`，也沒有 `printf`，可以讓你看清 crt 和 libc 各自負責什麼。Ch 41 會完整示範。
- **`LD_DEBUG=all ./hello`**：讓 `ld.so` 印出它做的每一個決策（搜尋哪些路徑、繫結哪些符號）。輸出很長，先試 `LD_DEBUG=libs`。
- **讀 glibc 原始碼**：`csu/libc-start.c` 裡的 `__libc_start_main`，就是 GDB backtrace 裡 `libc-start.c:392` 指的那個檔案。glibc 2.34 把 `__libc_start_main` 移到 `libc.so.6` 內並加上版本 `GLIBC_2.34`，這解釋了 `nm` 輸出裡的 `@GLIBC_2.34`。

## 動手練習

1. 對 `hello.c` 加上 `-O2` 重做第 2 站。`printf("%s %d\n", ...)` 還在嗎？再把程式改成 `printf("hello\n");`，看 gcc 是否把它換成了 `puts`。
2. 刪掉 `#include <stdio.h>` 重新編譯，記下 gcc 11 給的警告；它還能連結成功嗎？為什麼？（提示：連結器只看符號名稱 `printf`。）
3. 把 `static int zeroed;` 改成 `static int zeroed = 0;`，再看 `nm` 與 `size`，它還在 `.bss` 嗎？改成 `= 1` 呢？
4. 用 `strace -f sh -c ./hello` 找出 `fork`（在 Linux 上通常顯示為 `clone`）和 `execve` 的位置。

## 本章重點整理

- 一支 C 程式經過前置處理、編譯、組譯、連結四步變成 ELF 執行檔，每一步都能用 `gcc -E/-S/-c`、`nm`、`objdump`、`readelf` 觀察
- 目的檔裡的位址是空格子加重定位項目；連結器負責符號解析與重定位；`printf` 這類動態符號要到執行期才由 `ld.so` 解析
- 執行時核心先把控制權交給 `ld.so`，再到程式的 `_start`，經 `__libc_start_main` 才到 `main`；`main` 返回後 `exit` 還會跑 `atexit` 與清空 stdio
- 規格書只規定 8 個翻譯階段與 `main` 的語意，不規定工具怎麼切；這些站的劃分是 gcc/glibc/Linux 的實作

## 自我檢核

- [ ] 合上書，畫出從 `hello.c` 到 `main` 執行的七站圖，每站寫出用什麼指令觀察
- [ ] 能向別人解釋 `undefined reference to 'sqrt'` 是哪一站出的錯、為什麼 include 標頭檔不能解決
- [ ] 能說出 `hello.o` 裡 `call` 指令後面那四個 `00` 是什麼、誰負責把它填上
- [ ] 能解釋為什麼 `int big[1000000];` 不讓執行檔變大，而 `int big[1000000] = {1};` 會
- [ ] 能說出程式回傳 300 時 shell 看到 44 的原因，以及 C 標準在這件事上保證了什麼、沒保證什麼

## 延伸閱讀

### 書籍

- **《Computer Systems: A Programmer's Perspective》（CSAPP，3rd ed.）** — Bryant & O'Hallaron（Pearson，2015）
  - **這本書的定位**：本章是 CSAPP 第 1 章「A Tour of Computer Systems」與第 7 章「Linking」的濃縮版
  - **讀哪幾章**：1.1–1.4（hello 程式的一生，正是本章的原型）；7.1–7.7（目的檔、符號、重定位）可以留到 Ch 39 再讀
  - **前提知識**：無；書雖是 2015 年版，ELF 與連結的基本機制至今未變，但書中範例未使用 PIE，位址會和本章不同
- **《Linkers and Loaders》** — John R. Levine（Morgan Kaufmann，1999）
  - **這本書的定位**：連結器的專書。年代久遠（沒有 PIE、RELRO），但符號解析、重定位的概念章節仍是最清楚的
  - **讀哪幾章**：第 1 章（全景）現在就能讀；第 7 章（重定位）留到 Ch 39

### 部落格 / 技術文章

- **[How statically linked programs run on Linux](https://eli.thegreenplace.net/2012/08/13/how-statically-linked-programs-run-on-linux)** — Eli Bendersky（2012）
  - **這篇說什麼**：從 `execve` 到 `main` 的完整追蹤，逐步讀 glibc 的 `_start` 與 `__libc_start_main`
  - **為什麼值得讀**：用靜態連結避開 `ld.so`，讓第 6、7 站最乾淨地呈現；本章的 `_start` 逐行表格可與它對照
  - **注意**：寫於 glibc 2.34 之前，`__libc_csu_init` 等函式已不存在（本章 `_start` 傳 0 給 init/fini 就是這個改變）
- **[A Whirlwind Tutorial on Creating Really Teensy ELF Executables for Linux](https://www.muppetlabs.com/~breadbox/software/tiny/teensy.html)** — Brian Raiter
  - **這篇說什麼**：把一個 ELF 執行檔一路縮到 45 bytes，過程中解釋每個 header 欄位的作用
  - **讀哪裡**：整篇；讀完會知道 ELF 裡哪些東西是核心真正需要的
  - **前提知識**：本章的第 3–5 站

### 官方文件 / Spec

- **[ISO/IEC 9899 草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：5.1.1.2「Translation phases」（8 個翻譯階段）、5.1.2.2.1「Program startup」、5.1.2.2.3「Program termination」、7.22.4.4「The exit function」
- **[System V ABI AMD64 Architecture Processor Supplement](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：3.4.1「Initial Stack and Register State」——`_start` 進入時 stack 上 `argc`、`argv`、`envp`、auxv 的排列，就是 `pop rsi` / `mov rdx,rsp` 的依據
  - **注意**：其餘章節留到 Ch 11

地圖有了，接下來兩章回到「程式執行中的世界」：先搞清楚記憶體裡的資料長什麼樣子——型別與物件。

→ [Ch 2 型別與物件：整數型別、sizeof、陣列與 struct](./02-types-and-objects.md)
