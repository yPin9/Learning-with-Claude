# Ch 41 — 執行階段：從 execve 經 _start 到 main

> **目標**：說清楚 C 為什麼需要 runtime，能在 gdb 裡停在 `_start` 親眼看到核心擺好的 `argc`/`argv`/`envp`/auxv，追出 glibc 從 `_start` 經 `__libc_start_main` 到 `main`、再從 `exit` 到 `exit_group` 的每一步，並用 `-nostdlib` 寫出一個不靠 libc、兩百多行的精簡 CRT；最後認識 libgcc/compiler-rt、musl 與裸機的 newlib/libgloss 各自補上哪一塊。

> **對應原文**：[你所不知道的 C 語言：執行階段程式庫篇](https://hackmd.io/@sysprog/c-runtime) 的「C 語言為何需要 runtime」「從 execve 到 _start」「main 的原型與參數」「glibc 的啟動與終止」「精簡 libc：CRT 的設計考量」「編譯器的支援程式庫」「沒有作業系統時：newlib 與 libgloss」等節

> **環境**：WSL Ubuntu 22.04 x86_64（核心 6.18 WSL2），gcc 11.4、clang 14、GNU ld 2.38、glibc 2.35、gdb 12.1、strace。musl 部分以 Docker `alpine:3.20`（gcc 13.2、musl）實測。原文環境為 Ubuntu 24.04、glibc 2.39，並另有 arm64 與 arm-none-eabi 的結果；本課只實測 x86-64，裸機部分**未實測**，會明確標出。

## 為什麼需要這個？

「C 程式從 `main` 開始執行」只是方便的說法。試著回答：

- `argv` 的字串是誰、在什麼時候放到記憶體裡的？
- `main` 裡 `return 3;`，shell 的 `$?` 為什麼是 3？`return 256;` 呢？
- `atexit` 登記的函式由誰呼叫？順序為什麼是反的？
- `printf("no newline")` 之後呼叫 `_exit`，那行字去哪了？

答案都在 **C 執行階段程式庫（C runtime library，CRT）**：在 `main` 之前建立 C 程式期望的環境，在 `main` 之後依標準規定的順序收尾，把結束狀態交還作業系統。

C 的設計讓 CRT 成為必要。COBOL 有 `DISPLAY`、Fortran 有 `READ`/`WRITE`/`PRINT` 敘述，**C 的關鍵字完全不涉及輸入輸出**——`printf` 只是一般函式，編譯器不必知道它存在。語言本體因此精簡到能寫作業系統核心（沒有函式庫可用的環境），代價是所有需要和執行環境互動的功能都得由函式庫提供，**包括程式的啟動與結束**。1973 年 Ritchie 用 C 改寫 UNIX 核心，常用函式逐漸整理成 `<stdio.h>`、`<stdlib.h>` 背後的函式庫，各家移植各自實作，直到 ANSI C（1989）才統一介面。

C 標準因此區分兩種實作（N1570 4 §6）：

| | hosted implementation | freestanding implementation |
|---|---|---|
| 函式庫 | 完整標準函式庫 | 只需 `<stddef.h>`、`<stdint.h>`、`<limits.h>`、`<float.h>` 等少數不依賴 OS 的標頭 |
| 進入點 | `main`（5.1.2.2.1） | 由實作定義 |
| 例子 | Linux 上的一般程式 | 作業系統核心、裸機韌體 |

在 Linux 上，「hosted 實作」其實是好幾個元件拼起來的：

```
 ┌────────────────────────── 你的 .o ──────────────────────────┐
 │  main(), 你的函式                                           │
 └─────────────────────────────────────────────────────────────┘
 啟動程式碼  Scrt1.o (_start)、crti.o/crtn.o  ← glibc
            crtbeginS.o/crtendS.o            ← gcc
 C 函式庫    libc.so.6：printf、malloc、exit ← glibc（或 musl）
 編譯器支援  libgcc.a、libgcc_s.so.1         ← gcc（clang 可用 compiler-rt）
 動態連結器  ld-linux-x86-64.so.2            ← glibc（Ch 40）
 核心        execve、初始堆疊、exit_group
```

`gcc` 這個命令本身是 **compiler driver**：它不編譯任何東西，而是依序啟動 `cc1`（編譯器本體）、`as`、`collect2`（再呼叫 `ld`），並**替你補上**上圖中的啟動程式碼與程式庫。Windows 的分工類似：MSVC 連結器依子系統選擇 `mainCRTStartup`、`WinMainCRTStartup` 等進入點，它們初始化 CRT、執行全域建構子，再呼叫你的 `main`。

## 先建立直覺

把行程啟動想成一場接力賽，`main` 只是其中一棒：

```
 execve()          核心：辨識格式、映射 segment、建初始堆疊、設好 %rsp
   │
   ├─(動態連結)──> ld.so：載入 .so、重定位、跑各 .so 的建構子（Ch 40）
   │                 │
   └─(靜態連結)──────┴──> _start（Scrt1.o / crt1.o）：組語，清 %rbp、對齊堆疊
                           │
                           ▼
                     __libc_start_main：初始化 libc、登記收尾函式
                           │  call_init：_init → .init_array（你的 constructor）
                           ▼
                     __libc_start_call_main ──> exit(main(argc, argv, environ))
                                                   │
                                                   ▼
                     exit：atexit 函式(反序) → .fini_array(destructor) → 清空 stdio
                           │
                           ▼
                     _exit → exit_group 系統呼叫 → 核心保留低 8 位元給親代的 wait()
```

這一章就是沿著這張圖，每一棒都用工具親眼看一次。

> 行程的位址空間配置與 x86-64 呼叫慣例見 [Ch 11](./11-process-layout-and-abi.md)；`ld.so` 那一棒見 [Ch 40](./40-dynamic-linking.md)；`.init_array` 怎麼被連結器串起來見 [Ch 39](./39-static-linking.md)。

## 從 execve 到 _start

### 核心如何辨識執行檔

`execve` 不建立行程（那是 `fork`/`clone`/`posix_spawn` 的事），它**在原地**以新程式取代呼叫者的位址空間：PID 不變，開啟的 fd 除了標記 close-on-exec 者都保留。核心的 `search_binary_handler`（`fs/exec.c`）依序詢問已登記的格式處理常式：

- `binfmt_elf`：ELF。`load_elf_binary` 的「驗證」只是**格式檢查**：開頭四位元組是 `\177ELF`、`e_type` 是 `ET_EXEC` 或 `ET_DYN`、`e_machine` 符合目前架構、program header 大小合理——**不檢查來源是否可信**。
- `binfmt_script`：`#!` 開頭的腳本，改以直譯器重新比對。
- `binfmt_misc`：系統管理者依檔頭或副檔名登記的其他直譯器（例如以 QEMU 執行別的架構）。

處理常式回傳 `-ENOEXEC` 表示「不是我的格式」，全部回絕時 `execve` 以 `ENOEXEC` 失敗。用一個直接呼叫 `execv` 的小程式驗證（繞過 shell 的善後）：

```c
/* ex.c：以 execv 直接執行參數指定的檔案 */
#include <stdio.h>
#include <unistd.h>

int main(int argc, char *argv[])
{
    if (argc < 2) {
        fprintf(stderr, "usage: %s file [args...]\n", argv[0]);
        return 2;
    }
    execv(argv[1], argv + 1);
    perror("execv");
    return 1;
}
```

```console
$ printf '#!/bin/sh\necho "script: $0 $1"\n' > s.sh && chmod +x s.sh
$ printf 'echo no shebang\n' > ns.sh && chmod +x ns.sh
$ printf '\177ELF garbage' > bad && chmod +x bad
$ gcc ex.c -o ex
$ ./ex ./s.sh x
script: ./s.sh x
$ ./ex ./ns.sh
execv: Exec format error
$ ./ex ./bad
execv: Exec format error
$ ./ex ./nonexist
execv: No such file or directory
$ ./ns.sh
no shebang
```

沒有 `#!` 的文字檔和標頭損壞的 ELF，核心都以 `ENOEXEC` 回絕。但**直接在 shell 執行 `./ns.sh` 卻成功**——因為 POSIX 規定 shell 遇到 `ENOEXEC` 時要把檔案當 shell 腳本、自己啟動子 shell 執行。**這是 shell 的行為，不是核心的功能**；從 C 程式 `execv` 就沒有這個善後。

ELF 若帶 `PT_INTERP`，核心另外把指定的動態連結器映射進來並先跳到它；直譯器不存在時的 `ENOENT` 陷阱見 [Ch 40](./40-dynamic-linking.md)。驗證執行檔來源另有其他機制：IMA appraisal（v3.7 起，比對雜湊或簽章）、fs-verity（v5.4 起）、IPE LSM（v6.12 起）。

### 初始堆疊：用 gdb 停在 _start 看

核心在新行程堆疊的高位址端（`create_elf_tables`，`fs/binfmt_elf.c`）由低到高擺放：

```
低位址  ← %rsp（進入 _start 時）
 ┌──────────────────────────────┐
 │ argc                          │  8 位元組
 │ argv[0] … argv[argc-1]        │  指標
 │ NULL                          │
 │ envp[0] … envp[n-1]           │  指標
 │ NULL                          │
 │ auxv：(AT_PHDR, …) (AT_PAGESZ, 4096) (AT_ENTRY, …) (AT_RANDOM, …) … │  鍵值對
 │ (AT_NULL, 0)                  │
 │ 填充、AT_RANDOM 的 16 位元組亂數 │
 │ argv 與 envp 的字串本身、AT_EXECFN 的路徑 │
 └──────────────────────────────┘
高位址（堆疊起點）
```

**auxv（auxiliary vector）**是核心傳給使用者空間的鍵值對：頁面大小 `AT_PAGESZ`、程式進入點 `AT_ENTRY`、16 位元組亂數 `AT_RANDOM`（glibc 用它產生 stack protector 的 canary，見 [Ch 14](./14-function-call-security.md)）、執行檔路徑 `AT_EXECFN`、vDSO 位置 `AT_SYSINFO_EHDR` 等。x86-64 psABI 3.4.1 節保證進入時 `%rsp` 對齊 16 位元組，`%rdx` 存放應以 `atexit` 登記的函式指標。

**在 gdb 裡驗證**。動態連結的程式用 `starti` 會停在**第一道使用者空間指令**——注意那不是程式的 `_start`，而是 `ld.so` 的：

```console
$ gcc -O0 -g ex.c -o ex_dyn
$ gdb -q -batch -ex starti -ex 'info registers rip' -ex 'info symbol $pc' \
    -ex 'break _start' -ex continue \
    -ex 'info registers rsp rdx' -ex 'info symbol $rdx' \
    -ex 'x/gd $rsp' -ex 'x/4a $rsp+8' \
    -ex 'x/s *(char **)($rsp+8)' -ex 'x/s *(char **)($rsp+16)' -ex 'x/s *(char **)($rsp+24)' \
    -ex 'x/8i $pc' --args ./ex_dyn ./s.sh hello

Program stopped.
0x00007ffff7fe32b0 in _start () from /lib64/ld-linux-x86-64.so.2
rip            0x7ffff7fe32b0      0x7ffff7fe32b0 <_start>
_start in section .text of /lib64/ld-linux-x86-64.so.2

Breakpoint 1, 0x00005555555550a0 in _start ()
rsp            0x7fffffffe220      0x7fffffffe220
rdx            0x7ffff7fc9040      140737353912384
_dl_fini in section .text of /lib64/ld-linux-x86-64.so.2
0x7fffffffe220:	3
0x7fffffffe228:	0x7fffffffe4a9	0x7fffffffe4d1
0x7fffffffe238:	0x7fffffffe4d8	0x0
0x7fffffffe4a9:	"/home/ypp/c_sysprog_lab/p6b/ch41/ex_dyn"
0x7fffffffe4d1:	"./s.sh"
0x7fffffffe4d8:	"hello"
=> 0x5555555550a0 <_start>:	endbr64
   0x5555555550a4 <_start+4>:	xor    %ebp,%ebp
   0x5555555550a6 <_start+6>:	mov    %rdx,%r9
   0x5555555550a9 <_start+9>:	pop    %rsi
   0x5555555550aa <_start+10>:	mov    %rsp,%rdx
   0x5555555550ad <_start+13>:	and    $0xfffffffffffffff0,%rsp
   0x5555555550b1 <_start+17>:	push   %rax
   0x5555555550b2 <_start+18>:	push   %rsp
```

讀出來的東西：

- `%rsp` 指向的 8 位元組是 **3**（`argc`），後面三個指標、一個 0（`argv[argc]` 必為 NULL，N1570 5.1.2.2.1 §2）。
- `argv[0]` 是 gdb 啟動時給的**絕對路徑**，不是我們打的 `./ex_dyn`——`argv[0]` 由呼叫 `execve` 的一方任意決定。
- `%rdx` = `_dl_fini`：`ld.so` 跳到 `_start` 前填入的終止函式，`_start` 會把它傳給 `__libc_start_main` 登記。
- `%rsp` = `0x...e220`，對齊 16 位元組。

靜態連結的程式沒有 `ld.so`，`starti` 直接停在程式自己的 `_start`，`%rdx` 是核心清成的 0：

```console
$ gcc -O0 -static ex.c -o ex_static
$ gdb -q -batch -ex starti -ex 'info symbol $pc' -ex 'info registers rsp rdx' --args ./ex_static a
0x0000000000401620 in _start ()
_start in section .text of /home/ypp/c_sysprog_lab/p6b/ch41/ex_static
rsp            0x7fffffffe220      0x7fffffffe220
rdx            0x0                 0
```

glibc 只在這個指標非空時才登記它。

也可以在 C 裡驗證同一個佈局：`envp` 緊接在 `argv` 的 NULL 之後，越過 `envp` 的 NULL 就是 auxv：

```c
/* stack.c：觀察 argv、envp 與 auxv 的相對位置 */
#include <elf.h>
#include <stdint.h>
#include <stdio.h>
#include <sys/auxv.h>

extern char **environ;

int main(int argc, char *argv[], char *envp[])
{
    printf("argc=%d  &argc=%p\n", argc, (void *) &argc);
    printf("argv=%p  envp=%p  argv+argc+1=%p\n", (void *) argv,
           (void *) envp, (void *) (argv + argc + 1));
    printf("environ == envp: %d\n", environ == envp);

    char **p = envp;
    while (*p)
        p++;
    Elf64_auxv_t *auxv = (Elf64_auxv_t *) (p + 1);
    for (Elf64_auxv_t *a = auxv; a->a_type != AT_NULL; a++) {
        if (a->a_type == AT_PAGESZ || a->a_type == AT_ENTRY ||
            a->a_type == AT_RANDOM || a->a_type == AT_EXECFN)
            printf("auxv[%2lu] = %#lx (getauxval: %#lx)\n",
                   (unsigned long) a->a_type, (unsigned long) a->a_un.a_val,
                   getauxval(a->a_type));
    }
    printf("AT_EXECFN string: %s\n", (char *) getauxval(AT_EXECFN));
    printf("argv[0] string at %p, envp[0] string at %p\n", (void *) argv[0],
           (void *) envp[0]);
    return 0;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror stack.c -o stack && ./stack a b
argc=3  &argc=0x7ffe6423dc6c
argv=0x7ffe6423dda8  envp=0x7ffe6423ddc8  argv+argc+1=0x7ffe6423ddc8
environ == envp: 1
auxv[ 6] = 0x1000 (getauxval: 0x1000)
auxv[ 9] = 0x5eeb3f96a080 (getauxval: 0x5eeb3f96a080)
auxv[25] = 0x7ffe6423dff9 (getauxval: 0x7ffe6423dff9)
auxv[31] = 0x7ffe6423eff0 (getauxval: 0x7ffe6423eff0)
AT_EXECFN string: ./stack
argv[0] string at 0x7ffe6423e50b, envp[0] string at 0x7ffe6423e517
```

（位址每次執行不同。）`envp` 恰為 `argv + argc + 1`（差 `0x20` = 4 個指標：3 個 argv + NULL）；auxv 的類型編號 6、9、25、31 分別是 `AT_PAGESZ`、`AT_ENTRY`、`AT_RANDOM`、`AT_EXECFN`。`&argc` 位址較低，因為 `argc` 已被複製進 `main` 的堆疊框架。`argv[0]` 字串 `"./stack"` 佔 8 位元組，`envp[0]` 字串緊接在 `"a"`、`"b"` 之後（`0x50b + 8 + 2 + 2 = 0x517`）。

glibc 還提供一個不用寫程式的方法：

```console
$ LD_SHOW_AUXV=1 ./stack | head -12
AT_SYSINFO_EHDR:      0x7daf490aa000
AT_MINSIGSTKSZ:       1776
AT_HWCAP:             178bfbff
AT_PAGESZ:            4096
AT_CLKTCK:            100
AT_PHDR:              0x5d4dd986e040
AT_PHENT:             56
AT_PHNUM:             13
AT_BASE:              0x7daf490ac000
AT_FLAGS:             0x0
AT_ENTRY:             0x5d4dd986f080
AT_UID:               1000
```

`AT_BASE` 就是 `ld.so` 被映射的位址，`AT_PHDR`/`AT_PHNUM` 讓 `ld.so` 不必重讀檔案就能找到執行檔的 program header。

## main 的原型與結束狀態

N1570 5.1.2.2.1 規定 hosted 環境的 `main` 傳回 `int`，形式為 `int main(void)` 或 `int main(int argc, char *argv[])`（或等價者、或實作定義的其他形式）。第三個參數 `envp` 是附錄 J.5.1 的常見擴充，可攜的程式應用 POSIX 的 `environ` 或 `getenv`。

**`void main` 會怎樣？** 5.1.2.2.3 說傳回型別與 `int` 不相容時，回到主機環境的結束狀態**未規定**。實測：

```c
#include <stdio.h>
void main(void)
{
    puts("void main");
}
```

```console
$ gcc -std=c11 -Wall voidmain.c -o voidmain; ./voidmain; echo "exit=$?"
voidmain.c:2:6: warning: return type of ‘main’ is not ‘int’ [-Wmain]
    2 | void main(void)
      |      ^~~~
void main
exit=10
```

結束狀態 10 從哪來？`puts` 傳回寫出的位元組數（`"void main\n"` = 10），傳回值放在 `%eax`，`main` 沒有覆寫它，`__libc_start_call_main` 就把 `%eax` 當成 `main` 的傳回值交給 `exit`。這就是「未規定」的真實樣貌：不會壞掉，只是拿到垃圾。（C++ 完全不允許 `void main`。）

5.1.2.2.3 也規定：從最初呼叫的 `main` 返回等同以傳回值呼叫 `exit`；執行到 `main` 結尾的 `}` 則傳回 0。結束狀態經 `exit_group` 交給核心，核心**只保留低 8 位元**（`kernel/exit.c` 的 `(error_code & 0xff) << 8`），親代以 `wait` + `WEXITSTATUS` 取得：

```c
int main(void)
{
    return 256;
}
```

```console
$ gcc ret256.c -o ret256 && ./ret256; echo "\$?=$?"
$?=0
```

`256 & 0xff = 0`——**回傳 256 代表成功**。所以錯誤碼別超過 255，慣例上 1–125 用於程式自己的錯誤（126/127 被 shell 用來表示無法執行/找不到，128+N 表示被訊號 N 終止）。

### argv[0] 與 multicall 程式

`argv[0]` 通常是啟動時用的名字，程式可以據此改變行為。BusyBox 把數百個工具編進同一個執行檔，再以符號連結提供 `ls`、`cp` 等名字：

```c
/* multicall.c：依 argv[0] 決定行為 */
#include <stdio.h>
#include <string.h>

static int hello(void)
{
    puts("hello applet");
    return 0;
}

static int bye(void)
{
    puts("bye applet");
    return 0;
}

int main(int argc, char *argv[])
{
    (void) argc;
    const char *name = strrchr(argv[0], '/');
    name = name ? name + 1 : argv[0];
    if (!strcmp(name, "hello"))
        return hello();
    if (!strcmp(name, "bye"))
        return bye();
    fprintf(stderr, "%s: unknown applet\n", name);
    return 1;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror multicall.c -o multicall
$ ln -sf multicall hello && ln -sf multicall bye
$ ./hello && ./bye && ./multicall; echo "exit=$?"
hello applet
bye applet
multicall: unknown applet
exit=1
$ (exec -a bye ./multicall)
bye applet
```

最後一行用 bash 的 `exec -a` 直接指定 `argv[0]`，沒有任何符號連結也騙得過它——**`argv[0]` 不可信**。需要執行檔實際位置時讀 `/proc/self/exe`；auxv 的 `AT_EXECFN` 也只是傳給 `execve` 的路徑字串（可能是相對路徑）。

## glibc 的啟動與終止

### 連結進來的啟動程式碼

`gcc -v` 顯示 driver 在你的目的檔前後加了什麼：

```console
$ gcc -v m.c -o m 2>&1 | grep collect2 | tr ' ' '\n' | grep -E 'crt|^-l'
/usr/lib/gcc/x86_64-linux-gnu/11/../../../x86_64-linux-gnu/Scrt1.o
/usr/lib/gcc/x86_64-linux-gnu/11/../../../x86_64-linux-gnu/crti.o
/usr/lib/gcc/x86_64-linux-gnu/11/crtbeginS.o
-lgcc
-lgcc_s
-lc
-lgcc
-lgcc_s
/usr/lib/gcc/x86_64-linux-gnu/11/crtendS.o
/usr/lib/gcc/x86_64-linux-gnu/11/../../../x86_64-linux-gnu/crtn.o
$ gcc -static -v m.c -o ms 2>&1 | grep collect2 | tr ' ' '\n' | grep -E 'crt|^-l'
.../crt1.o
.../crti.o
.../11/crtbeginT.o
-lgcc
-lgcc_eh
-lc
.../11/crtend.o
.../crtn.o
```

| 物件 | 來源 | 作用 |
|---|---|---|
| `Scrt1.o` / `crt1.o` / `rcrt1.o` | glibc | 提供 `_start`。`S` = 給 PIE 用；非 PIE 用 `crt1.o`；static PIE 用 `rcrt1.o` |
| `crti.o` / `crtn.o` | glibc | `.init`/`.fini` section 的開頭與結尾，夾住其他目的檔的 `.init` 片段，拼成 `_init`/`_fini`。舊機制，為相容保留 |
| `crtbeginS.o` / `crtendS.o` | gcc（`libgcc/crtstuff.c`） | 透過 `.init_array`/`.fini_array` 掛上 `frame_dummy`、`__do_global_dtors_aux`（後者呼叫 `__cxa_finalize` 跑 C++ 靜態物件解構子）；`-static` 時改用 `crtbeginT.o` |
| `-lgcc`、`-lgcc_s`/`-lgcc_eh` | gcc | 編譯器支援程式庫，本章後面會講 |

順序有講究：`crti.o` 必須最前、`crtn.o` 必須最後，其他目的檔的 `.init` 片段才會落在兩者之間組成完整函式。`-lgcc` 在 `-lc` 後面**又出現一次**，因為 libc 本身也可能用到 libgcc 的函式，而靜態程式庫只會由左至右掃一次（[Ch 39](./39-static-linking.md) 的程式庫順序陷阱）。

### 從 _start 到 main

前面 gdb 看到的 `_start` 完整反組譯：

```console
$ objdump -d --no-show-raw-insn ex_dyn | sed -n '/<_start>:/,/^$/p'
00000000000010a0 <_start>:
    10a0:	endbr64
    10a4:	xor    %ebp,%ebp                       ① 清 frame pointer：回溯到此為止
    10a6:	mov    %rdx,%r9                        ② ld.so 的 _dl_fini → 第 6 個參數 rtld_fini
    10a9:	pop    %rsi                            ③ 彈出 argc → 第 2 個參數
    10aa:	mov    %rsp,%rdx                       ④ 現在 %rsp 指向 argv → 第 3 個參數
    10ad:	and    $0xfffffffffffffff0,%rsp        ⑤ 對齊 16 位元組
    10b1:	push   %rax                            ⑥ 墊 8 位元組保持對齊
    10b2:	push   %rsp                            ⑦ stack_end → 第 7 個參數（堆疊上）
    10b3:	xor    %r8d,%r8d                       ⑧ fini = NULL
    10b6:	xor    %ecx,%ecx                       ⑨ init = NULL
    10b8:	lea    0xca(%rip),%rdi        # 1189 <main>   ⑩ main → 第 1 個參數
    10bf:	call   *0x2f13(%rip)        # 3fd8 <__libc_start_main@GLIBC_2.34>
    10c5:	hlt                                    ⑪ 不會回來；回來就讓它 crash
```

對照 x86-64 SysV 呼叫慣例（參數依序放 `%rdi`、`%rsi`、`%rdx`、`%rcx`、`%r8`、`%r9`，第 7 個起放堆疊；見 [Ch 11](./11-process-layout-and-abi.md)），這就是呼叫

```c
__libc_start_main(main, argc, argv, /*init*/ NULL, /*fini*/ NULL, rtld_fini, stack_end);
```

`init`/`fini` 兩個參數固定為 0，這是 glibc 2.34 的改變（commit「Reduce the statically linked startup code」）：以前它們傳的是靜態連結進每個執行檔的 `__libc_csu_init`/`__libc_csu_fini`，而 `__libc_csu_init` 的結尾是一段現成的 ROP gadget（「ret2csu」技巧的名字由此而來，見 [Ch 14](./14-function-call-security.md)），glibc 於是把初始化移進 `libc.so.6`。注意 `call` 的目標是 `__libc_start_main@GLIBC_2.34`——用新版 glibc 編的執行檔因此**無法在 glibc 2.34 以前的系統上執行**（符號版本，Ch 39 進階段落）。

在 `main` 與建構子設中斷點，看完整呼叫鏈（gdb 預設不顯示 `main` 之外的框架，要 `set backtrace past-main on`）：

```console
$ gdb -batch -ex 'set backtrace past-main on' -ex 'break main' -ex run -ex bt ./order 2>&1 | grep '^#'
#0  0x00005555555552a6 in main ()
#1  0x00007ffff7c29d90 in __libc_start_call_main (main=main@entry=0x55555555529e <main>, argc=argc@entry=1, argv=argv@entry=0x7fffffffe248) at ../sysdeps/nptl/libc_start_call_main.h:58
#2  0x00007ffff7c29e40 in __libc_start_main_impl (main=0x55555555529e <main>, argc=1, argv=0x7fffffffe248, init=<optimized out>, fini=<optimized out>, rtld_fini=<optimized out>, stack_end=0x7fffffffe238) at ../csu/libc-start.c:392
#3  0x0000555555555125 in _start ()
$ gdb -batch -ex 'set backtrace past-main on' -ex 'break ctor' -ex run -ex bt ./order 2>&1 | grep '^#'
#0  0x000055555555523e in ctor ()
#1  0x00007ffff7c29ebb in call_init (env=<optimized out>, argv=0x7fffffffe248, argc=1) at ../csu/libc-start.c:145
#2  __libc_start_main_impl (main=0x55555555529e <main>, argc=1, argv=0x7fffffffe248, init=<optimized out>, fini=<optimized out>, rtld_fini=<optimized out>, stack_end=0x7fffffffe238) at ../csu/libc-start.c:379
#3  0x0000555555555125 in _start ()
```

（原始碼行號來自 glibc 的除錯資訊。）`__libc_start_call_main` 先呼叫 `setjmp` 留下返回點（讓主執行緒呼叫 `pthread_exit` 時能回到這裡），再執行 `exit(main(argc, argv, __environ))`。動態連結時大部分 libc 初始化已由 `ld.so` 做完，`__libc_start_main` 主要是登記收尾函式、由 `call_init` 執行執行檔自己的 `_init` 與 `.init_array`。

### 初始化與終止的順序：constructor / atexit 實驗

```c
/* order.c：觀察初始化與終止的順序 */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

static void preinit(int argc, char **argv, char **envp)
{
    (void) argv;
    (void) envp;
    printf("preinit_array (argc=%d)\n", argc);
}
__attribute__((section(".preinit_array"), used)) static void (*preinit_p)(
    int, char **, char **) = preinit;

__attribute__((constructor(101))) static void ctor_early(void)
{
    puts("constructor(101)");
}

__attribute__((constructor)) static void ctor(void)
{
    puts("constructor");
}

__attribute__((destructor)) static void dtor(void)
{
    puts("destructor");
}

static void bye1(void)
{
    puts("atexit #1");
}

static void bye2(void)
{
    puts("atexit #2");
}

int main(int argc, char *argv[])
{
    atexit(bye1);
    atexit(bye2);
    printf("main: buffered output without newline");
    if (argc > 1 && !strcmp(argv[1], "_exit"))
        _exit(3);
    putchar('\n');
    return 3;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror order.c -o order
$ ./order; echo "exit=$?"
preinit_array (argc=1)
constructor(101)
constructor
main: buffered output without newline
atexit #2
atexit #1
destructor
exit=3
```

規則：

1. **`.preinit_array`** 只在執行檔中有效，先於所有建構子，和 `.init_array` 一樣收到 `argc, argv, envp`。它甚至在 `__libc_start_main` 之前就被 `ld.so` 執行：

   ```console
   $ gdb -batch -ex 'set backtrace past-main on' -ex 'break preinit' -ex run -ex bt ./order 2>&1 | grep '^#'
   #0  0x00005555555551f1 in preinit ()
   #1  0x00007ffff7fc95be in _dl_init (main_map=0x7ffff7ffe2e0, argc=1, argv=0x7fffffffe248, env=0x7fffffffe258) at ./elf/dl-init.c:102
   #2  0x00007ffff7fe32ea in _dl_start_user () from /lib64/ld-linux-x86-64.so.2
   #3  0x0000000000000001 in ?? ()
   #4  0x00007fffffffe4b8 in ?? ()
   #5  0x0000000000000000 in ?? ()
   ```

   注意 #3、#4：gdb 把堆疊上的 `argc`（1）和 `argv[0]` 指標**當成返回位址**，回溯出虛構的框架——`ld.so` 這一段沒有告訴 unwinder「到此為止」。精簡 libc 一節會看到怎麼用 `.cfi_undefined rip` 避免這件事。
2. **建構子優先序**：0–100 保留給實作，數字小的先執行，沒指定的排最後。用保留值 gcc 會警告：

   ```console
   $ gcc -Wall prio.c -o prio
   prio.c:2:1: warning: constructor priorities from 0 to 100 are reserved for the implementation [-Wprio-ctor-dtor]
   ```
3. **`exit` 的順序**：`atexit` 登記的函式以**相反順序**執行（N1570 7.22.4.4），接著是解構子，最後清空 stdio 緩衝區。在 glibc 裡，解構子其實和 `atexit` 函式在同一張表上——看 `dtor` 和 `bye2` 的呼叫鏈：

   ```console
   $ gdb -batch -ex 'break bye2' -ex run -ex bt ./order 2>&1 | grep '^#'
   #0  0x000055555555528c in bye2 ()
   #1  0x00007ffff7c45425 in __run_exit_handlers (status=3, listp=0x7ffff7e1a838 <__exit_funcs>, run_list_atexit=run_list_atexit@entry=true, run_dtors=run_dtors@entry=true) at ./stdlib/exit.c:113
   #2  0x00007ffff7c455a0 in __GI_exit (status=<optimized out>) at ./stdlib/exit.c:143
   ...
   $ gdb -batch -ex 'break dtor' -ex run -ex bt ./order 2>&1 | grep '^#'
   #0  0x0000555555555258 in dtor ()
   #1  0x00007ffff7fc924e in _dl_fini () at ./elf/dl-fini.c:142
   #2  0x00007ffff7c45425 in __run_exit_handlers (status=3, listp=0x7ffff7e1a838 <__exit_funcs>, run_list_atexit=run_list_atexit@entry=true, run_dtors=run_dtors@entry=true) at ./stdlib/exit.c:113
   ...
   ```

   兩者都由 `__run_exit_handlers` 呼叫：`_dl_fini`（`_start` 從 `%rdx` 拿到、由 `__libc_start_main` 以 `__cxa_atexit` 登記）**最早登記，所以最後執行**，它再去跑各物件的 `.fini_array`。這解釋了為什麼解構子在所有 `atexit` 函式之後——也解釋了 [Ch 40](./40-dynamic-linking.md) 的 `mcount.c` 為何要預先 `dup` stderr。

### _exit 與被丟掉的緩衝區

`_exit`（以及 C11 的 `_Exit`）直接進核心，跳過上面**所有**步驟。輸出遺失多少取決於 stdout 的緩衝模式：連到終端機是行緩衝，連到管線或檔案是全緩衝。用 `script` 模擬終端機：

```console
$ script -eqc './order _exit' /dev/null < /dev/null; echo "exit=$?"
preinit_array (argc=2)
constructor(101)
constructor
exit=3
$ ./order _exit | cat; echo "exit=${PIPESTATUS[0]}"
exit=3
```

終端機上，以換行結尾的訊息早已寫出，只有 `main` 中沒換行的那行留在緩衝區而遺失，`atexit` 和解構子也沒跑。輸出到管線時**一行都沒有**——所有訊息都還在緩衝區裡。

實務意涵：`fork` 之後的子行程若要結束，應呼叫 `_exit` 而非 `exit`，否則繼承來的緩衝區會被寫出兩次；`fork` 之後、`exec` 之前若用 stdio 輸出要先 `fflush`（`execve` 成功時同樣不經過 `exit`）。stdio 緩衝的細節見 [Ch 35 — Stream I/O、EOF 與 signal](./35-stream-io-and-signals.md)。

### 靜態連結時 glibc 在 main 之前做了什麼

```console
$ gcc -O2 -static hello.c -o hello_static
$ strace ./hello_static 2>&1 >/dev/null
execve("./hello_static", ["./hello_static"], 0x7fffaaa3cea0 /* 23 vars */) = 0
arch_prctl(0x3001 /* ARCH_??? */, 0x7ffe4e1f4fa0) = -1 EINVAL (Invalid argument)
brk(NULL)                               = 0x811000
brk(0x811dc0)                           = 0x811dc0
arch_prctl(ARCH_SET_FS, 0x8113c0)       = 0
set_tid_address(0x811690)               = 13596
set_robust_list(0x8116a0, 24)           = 0
rseq(0x811d60, 0x20, 0, 0x53053053)     = 0
uname({sysname="Linux", nodename="DESKTOP-JK2JTGG", ...}) = 0
prlimit64(0, RLIMIT_STACK, NULL, {rlim_cur=8192*1024, rlim_max=RLIM64_INFINITY}) = 0
readlink("/proc/self/exe", "/home/ypp/c_sysprog_lab/p6b/ch41"..., 4096) = 45
getrandom("\xdb\x73\xf0\x06\xe9\x3a\x24\x6e", 8, GRND_NONBLOCK) = 8
brk(0x832dc0)                           = 0x832dc0
brk(0x833000)                           = 0x833000
mprotect(0x4c1000, 16384, PROT_READ)    = 0
newfstatat(1, "", {st_mode=S_IFCHR|0666, st_rdev=makedev(0x1, 0x3), ...}, AT_EMPTY_PATH) = 0
ioctl(1, TCGETS, 0x7ffe4e1f4c00)        = -1 ENOTTY (Inappropriate ioctl for device)
write(1, "hello\n", 6)                  = 6
exit_group(0)                           = ?
+++ exited with 0 +++
```

`newfstatat` 以前都發生在 `main` 之前，來源（依原文對 glibc 2.39 的追蹤，本課的 2.35 大致相同）：

| 系統呼叫 | 誰發出 | 目的 |
|---|---|---|
| `brk` ×2、`arch_prctl(ARCH_SET_FS)` | `__libc_setup_tls` | 配置 thread-local storage 並設定 `%fs`。`errno`、stack canary（`%fs:0x28`）都在這裡，這一步之前的程式碼不能用它們 |
| `set_tid_address`、`set_robust_list`、`rseq` | `__tls_init_tp` | 把主執行緒登記給核心（`pthread_join`、robust mutex 的基礎）、註冊 restartable sequences |
| `prlimit64(RLIMIT_STACK)` | `__pthread_early_init` | 決定新執行緒的預設堆疊大小 |
| `readlink("/proc/self/exe")` | `_dl_non_dynamic_init` → `_dl_get_origin` | 為靜態程式中 `dlopen` 的 `$ORIGIN` 記錄執行檔目錄；這裡第一次呼叫 `malloc`（Ch 40 的 `--wrap` 計數） |
| `getrandom` + `brk` | `malloc` 初始化 | `tcache_key_initialize` 取得偵測 double free 用的亂數，再擴充 heap |
| `mprotect` | `_dl_protect_relro` | 把 RELRO 區設為唯讀 |
| `newfstatat`、`ioctl(TCGETS)` | 第一次輸出時的 `_IO_file_doallocate` | 判斷 stdout 是不是終端機，決定行緩衝或全緩衝，再配置緩衝區 |

本課的 glibc 2.35 比原文多兩個：開頭的 `arch_prctl(0x3001)` 是 `ARCH_CET_STATUS`（查詢 Intel CET 狀態，此核心不支援所以 `EINVAL`）；`uname` 就我所知是舊版 glibc 靜態程式檢查核心版本留下的（後續版本已移除這項檢查），這點未在原始碼中逐行確認。順帶一提，靜態 hello world 的 text 有 781509 位元組；動態連結版只有 1362。

## 精簡 libc：用 -nostdlib 寫一個 CRT

glibc 為了執行緒、locale、動態連結和眾多架構，啟動流程很複雜。下面這個解說用的精簡 libc 只保留 CRT 的骨架：進入點、系統呼叫、`environ`、建構子/解構子、`atexit`/`exit`，外加全緩衝的 `puts`、`getenv` 和幾個字串/記憶體函式。原文的版本同時支援 x86-64 與 arm64，這裡為了篇幅只保留 x86-64。

### 進入點：為什麼必須是組合語言

核心跳到 `_start` 時，`%rsp` 指向 `argc`，**沒有返回位址**——這不是一個正常的 C 函式呼叫，C 編譯器產生的函式序言（假設堆疊上有返回位址、`%rsp+8` 對齊）在這裡都不成立：

```asm
/* start.S: 行程的進入點，核心把控制權交給這裡 (x86-64 Linux) */
    .text
    .globl _start
    .type _start, @function
_start:
    .cfi_startproc
    .cfi_undefined rip      /* 告訴 unwinder：沒有上一層框架 */
    xor %ebp, %ebp          /* 以 frame pointer 回溯時也到此為止 */
    mov %rsp, %rdi          /* 第一個參數：指向 argc 的初始堆疊指標 */
    and $-16, %rsp          /* call 之前 %rsp 須對齊 16 位元組 */
    call __mini_start
    hlt                     /* 不會回到這裡 */
    .cfi_endproc
    .size _start, . - _start
    .section .note.GNU-stack, "", @progbits
```

（檔名大寫 `.S` 會先經過前置處理器；最後一行避免 [Ch 39](./39-static-linking.md) 那個「可執行堆疊」陷阱。）反例：舊版 newlib 的 i386 Linux `crt0.c` 以 C 寫 `_start(int args)`，再用 `&args - 1` 推算 `argc` 的位置——這依賴 i386 參數全放堆疊、編譯器不調整框架，換成暫存器傳參的架構或開最佳化就不成立；newlib 4.3.0 移除了整個 i386 Linux 支援。

### 函式庫本體

```c
/* mini.h: 解說用的精簡 libc 介面 */
#ifndef MINI_H
#define MINI_H

#include <stddef.h> /* freestanding 環境也提供的標頭 */

typedef long ssize_t;

extern char **environ;
extern int errno;

ssize_t write(int fd, const void *buf, size_t n);
_Noreturn void _exit(int status);
_Noreturn void exit(int status);
int atexit(void (*fn)(void));
char *getenv(const char *name);
int puts(const char *s);

size_t strlen(const char *s);
int strcmp(const char *a, const char *b);
int strncmp(const char *a, const char *b, size_t n);
void *memcpy(void *dst, const void *src, size_t n);
void *memset(void *dst, int c, size_t n);
void *memmove(void *dst, const void *src, size_t n);
int memcmp(const void *a, const void *b, size_t n);

#endif
```

```c
/* mini.c：解說用的精簡 libc，只支援 x86-64 Linux */
#include "mini.h"

#include <stdint.h> /* uintptr_t，同樣是 freestanding 標頭 */

char **environ;
int errno; /* 真正的 libc 以 thread-local storage 實作 */

/* 系統呼叫：編號放 %rax，參數依序放 %rdi、%rsi、%rdx；
 * syscall 指令會覆寫 %rcx 與 %r11，必須列入 clobber */
static long syscall3(long nr, long a, long b, long c)
{
    long ret;
    __asm__ volatile("syscall"
                     : "=a"(ret)
                     : "a"(nr), "D"(a), "S"(b), "d"(c)
                     : "rcx", "r11", "memory");
    return ret;
}

enum { SYS_write = 1, SYS_exit_group = 231 }; /* x86-64 的系統呼叫編號 */
enum { EINTR = 4 };

ssize_t write(int fd, const void *buf, size_t n)
{
    long ret = syscall3(SYS_write, fd, (long) buf, (long) n);
    if (ret < 0 && ret > -4096) { /* 核心以 -errno 回報錯誤 */
        errno = (int) -ret;
        return -1;
    }
    return ret;
}

_Noreturn void _exit(int status)
{
    for (;;)
        syscall3(SYS_exit_group, status, 0, 0);
}

/* GCC 在 -O2 會把逐位元組的迴圈辨識為 memset/memcpy 並改寫成對它們的呼叫，
 * 在 memset 自身之內就成了無窮遞迴，因此對這幾個函式關閉該最佳化 */
#if defined(__GNUC__) && !defined(__clang__)
#define NO_LOOP_IDIOM __attribute__((optimize("no-tree-loop-distribute-patterns")))
#else
#define NO_LOOP_IDIOM
#endif

size_t strlen(const char *s)
{
    const char *p = s;
    while (*p)
        p++;
    return (size_t) (p - s);
}

int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b)
        a++, b++;
    return (unsigned char) *a - (unsigned char) *b;
}

int strncmp(const char *a, const char *b, size_t n)
{
    for (; n; n--, a++, b++) {
        if (*a != *b || !*a)
            return (unsigned char) *a - (unsigned char) *b;
    }
    return 0;
}

NO_LOOP_IDIOM void *memcpy(void *dst, const void *src, size_t n)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    while (n--)
        *d++ = *s++;
    return dst;
}

NO_LOOP_IDIOM void *memset(void *dst, int c, size_t n)
{
    unsigned char *d = dst;
    while (n--)
        *d++ = (unsigned char) c;
    return dst;
}

NO_LOOP_IDIOM void *memmove(void *dst, const void *src, size_t n)
{
    unsigned char *d = dst;
    const unsigned char *s = src;
    if ((uintptr_t) d < (uintptr_t) s) { /* 不同物件的指標不能直接比大小 */
        while (n--)
            *d++ = *s++;
    } else {
        while (n--)
            d[n] = s[n];
    }
    return dst;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const unsigned char *p = a, *q = b;
    for (; n; n--, p++, q++) {
        if (*p != *q)
            return *p - *q;
    }
    return 0;
}

char *getenv(const char *name)
{
    size_t len = strlen(name);
    if (len == 0)
        return NULL;
    for (size_t i = 0; i < len; i++)
        if (name[i] == '=')
            return NULL; /* 名稱不得含有 '=' */
    for (char **e = environ; *e; e++) {
        if (!strncmp(*e, name, len) && (*e)[len] == '=')
            return *e + len + 1;
    }
    return NULL;
}

/* write 可能只寫出一部分，重複呼叫直到寫完 */
static int write_all(int fd, const char *s, size_t n)
{
    while (n > 0) {
        ssize_t w = write(fd, s, n);
        if (w < 0 && errno == EINTR)
            continue;
        if (w <= 0)
            return -1;
        s += w;
        n -= (size_t) w;
    }
    return 0;
}

/* stdout 一律全緩衝：填滿或 exit 時才寫出，_exit 則連同緩衝區一起丟棄 */
static char out_buf[256];
static size_t out_len;

static int flush_stdout(void)
{
    int ret = write_all(1, out_buf, out_len);
    out_len = 0;
    return ret;
}

static int put_bytes(const char *s, size_t n)
{
    while (n > 0) {
        if (out_len == sizeof out_buf && flush_stdout() < 0)
            return -1;
        size_t k = sizeof out_buf - out_len;
        if (k > n)
            k = n;
        memcpy(out_buf + out_len, s, k);
        out_len += k;
        s += k;
        n -= k;
    }
    return 0;
}

int puts(const char *s)
{
    if (put_bytes(s, strlen(s)) < 0 || put_bytes("\n", 1) < 0)
        return -1;
    return 0;
}

/* atexit：C 標準要求至少能登記 32 個函式，以相反順序呼叫 */
static void (*atexit_fns[32])(void);
static int atexit_count;

int atexit(void (*fn)(void))
{
    if (atexit_count == 32)
        return -1;
    atexit_fns[atexit_count++] = fn;
    return 0;
}

/* 由連結器的預設腳本定義，界定各陣列的範圍 */
typedef void (*init_fn)(int, char **, char **);
typedef void (*fini_fn)(void);
extern init_fn __preinit_array_start[], __preinit_array_end[];
extern init_fn __init_array_start[], __init_array_end[];
extern fini_fn __fini_array_start[], __fini_array_end[];

_Noreturn void exit(int status)
{
    while (atexit_count > 0)
        atexit_fns[--atexit_count]();
    for (fini_fn *f = __fini_array_end; f != __fini_array_start;)
        (*--f)();
    flush_stdout(); /* 最後才清空緩衝區，解構子的輸出也包含在內 */
    _exit(status);
}

int main(int argc, char *argv[], char *envp[]);

/* 由 start.S 呼叫：sp 指向核心建立的初始堆疊 */
_Noreturn void __mini_start(long *sp)
{
    int argc = (int) sp[0];
    char **argv = (char **) (sp + 1);
    char **envp = argv + argc + 1;
    environ = envp;

    for (init_fn *f = __preinit_array_start; f != __preinit_array_end; f++)
        (*f)(argc, argv, envp);
    for (init_fn *f = __init_array_start; f != __init_array_end; f++)
        (*f)(argc, argv, envp);
    exit(main(argc, argv, envp));
}
```

`__mini_start` 拿到的就是核心建立的初始堆疊：`sp[0]` 是 `argc`，`sp + 1` 是 `argv`，`envp = argv + argc + 1`——和前面 `stack.c`、gdb 的觀察完全一致。`__init_array_start` 等符號不必自己定義：GNU ld 的預設 linker script（`ld --verbose` 可看）以 `PROVIDE_HIDDEN` 提供，範圍內就是各目的檔 `.init_array` 串接而成的函式指標陣列（[Ch 39](./39-static-linking.md)）。`init_array` 的項目以 `(argc, argv, envp)` 呼叫，即使建構子宣告為 `void (void)`——ISO C 中這是未定義行為，在 x86-64 呼叫慣例下無害（多餘的引數暫存器被忽略），glibc 也這樣做。

### 測試程式

```c
/* app.c: 以精簡 libc 建置的測試程式 */
#include "mini.h"

static void bye(void)
{
    puts("atexit: bye");
}

__attribute__((constructor)) static void setup(void)
{
    puts("constructor");
}

__attribute__((destructor)) static void teardown(void)
{
    puts("destructor");
}

int main(int argc, char *argv[], char *envp[])
{
    (void) envp;
    atexit(bye);
    if (argc > 1 && !strcmp(argv[1], "_exit"))
        _exit(4); /* 不經過 exit：atexit、解構子與緩衝區都被略過 */
    for (int i = 0; i < argc; i++)
        puts(argv[i]);
    const char *user = getenv("USER");
    puts(user ? user : "(no USER)");

    char line[64];
    size_t n = (size_t) argc * 8; /* 長度在執行時期才知道 */
    memset(line, '-', n < sizeof line ? n : sizeof line - 1);
    line[n < sizeof line ? n : sizeof line - 1] = '\0';
    puts(line);
    return argc;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O2 -fno-stack-protector -c start.S mini.c app.c
$ gcc -static -nostdlib start.o mini.o app.o -o app
$ USER=student ./app x y; echo "exit=$?"
constructor
./app
x
y
student
------------------------
atexit: bye
destructor
exit=3
$ USER=student ./app _exit; echo "exit=$?"
exit=4
$ size app
   text	   data	    bss	    dec	    hex	filename
   2494	     16	    576	   3086	    c0e	app
$ strace -o trace.txt ./app >/dev/null; sed 's/(.*//' trace.txt | LC_ALL=C sort | uniq -c
      1 +++ exited with 1 +++
      1 execve
      1 exit_group
      1 write
```

- `-nostdlib` 讓 driver **不加**任何啟動程式碼與程式庫；`-static` 產生不需要 `ld.so` 的執行檔。
- `./app _exit` 什麼都沒印：建構子與 `main` 的輸出都還在緩衝區，`_exit` 連同緩衝區一起丟棄——和 `order.c` 輸出到管線時一樣。
- text 只有 2494 位元組（glibc 靜態 hello world 是 781509）；整個行程只有 `write` 和 `exit_group` **兩個系統呼叫**，所有輸出在 `exit` 中一次寫出。
- 換成 clang 編 `mini.c`/`app.c` 也能跑。

### 設計考量：故意弄壞看看

**1. 拿掉 `-fno-stack-protector`**：

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O2 -c app.c -o app_sp.o && gcc -static -nostdlib start.o mini.o app_sp.o -o app_sp
/usr/bin/ld: app_sp.o: in function `main':
app.c:(.text.startup+0x148): undefined reference to `__stack_chk_fail'
collect2: error: ld returned 1 exit status
```

Ubuntu 的 gcc 預設 `-fstack-protector-strong`，`char line[64]` 觸發 canary 檢查。就算補上 `__stack_chk_fail`，canary 從 `%fs:0x28` 讀取，而我們從沒做過 `arch_prctl(ARCH_SET_FS)`——前面 glibc 靜態程式的 strace 裡那一步正是在建立它。真正的 libc 以 auxv 的 `AT_RANDOM` 初始化 canary。

**2. 拿掉 `NO_LOOP_IDIOM`**：

```console
$ gcc -std=c11 -O2 -fno-stack-protector -c mini_bad.c && objdump -dr --no-show-raw-insn mini_bad.o | sed -n '/<memset>:/,/^$/p'
0000000000000250 <memset>:
 250:	endbr64
 254:	test   %rdx,%rdx
 257:	je     270 <memset+0x20>
 259:	sub    $0x8,%rsp
 25d:	movzbl %sil,%esi
 261:	call   266 <memset+0x16>
			262: R_X86_64_PLT32	memset-0x4
 266:	add    $0x8,%rsp
 26a:	ret
```

gcc 的最佳化器把 `memset` 自己的逐位元組迴圈辨識成「這是 memset」，改寫成……呼叫 `memset`——**無窮遞迴**（[Ch 37](./37-compiler-optimization.md) 的 loop idiom recognition）。gcc 以 `-fno-tree-loop-distribute-patterns` 關閉這項轉換，我們用 `optimize` 屬性只對這幾個函式關閉；clang 不會在名為 `memset` 的函式中產生對自身的呼叫。反方向也成立：即使你沒寫 `memcpy`，結構複製、大陣列初始化也可能被編譯成 `memcpy`/`memset` 呼叫，GCC 手冊明說 **freestanding 環境也必須提供 `memcpy`、`memmove`、`memset`、`memcmp`**。

**3. 編譯器替你換函式**：把 `strcmp(argv[1], "_exit")` 改成 `strncmp(argv[1], "_exit", 6)`（長度涵蓋了結尾的 `'\0'`）：

```console
$ nm app_n.o | grep -E ' U '
                 U _exit
                 U atexit
                 U getenv
                 U puts
                 U strcmp
```

目的檔裡**沒有** `strncmp`，只有 `strcmp`——gcc 依 builtin 語意判斷兩者等價，改寫了呼叫。我們的 mini libc 剛好有 `strcmp`，所以連得起來；如果你的 libc 只提供 `strncmp`，就會出現「我明明沒呼叫 strcmp」的連結錯誤。不想要這種假設就用 `-fno-builtin` 或 `-ffreestanding`。

**4. 拿掉 `.cfi_undefined rip`**，用 gdb 回溯：

```console
$ gdb -q -batch -ex 'set backtrace past-entry on' -ex 'break __mini_start' -ex run -ex bt ./app_g 2>&1 | grep '^#'
#0  __mini_start (sp=0x7fffffffe1b0) at mini.c:211
#1  0x000000000040100e in _start () at start.S:11
$ gdb -q -batch -ex 'set backtrace past-entry on' -ex 'break __mini_start' -ex run -ex bt ./app_nocfi 2>&1 | grep '^#'
#0  __mini_start (sp=0x7fffffffe1a0) at mini.c:211
#1  0x000000000040100e in _start () at start_nocfi.S:10
#2  0x0000000000000001 in ?? ()
#3  0x00007fffffffe42c in ?? ()
#4  0x0000000000000000 in ?? ()
```

回溯工具有兩類：以 frame pointer 回溯的（如 `perf --call-graph fp`）沿 `%rbp` 鏈前進，`xor %ebp,%ebp` 讓它停下；gdb 與 libgcc 的 unwinder 讀 `.eh_frame` 的 CFI，`.cfi_undefined rip` 宣告「返回位址不存在」。沒有 CFI 時，gdb 把堆疊上的 `argc`（1）和 `argv[0]` 當成返回位址，多出三層虛構框架——和前面 `preinit` 的回溯同一個現象。glibc 的 `start.S` 註解寫著 "Clearing frame pointer is insufficient, use CFI."

其他省略的部分（真正的 libc 都有）：`FILE` 串流與行緩衝、`malloc`、locale、訊號、執行緒與 TLS、vDSO（glibc 從 auxv 的 `AT_SYSINFO_EHDR` 找到核心映射的 vDSO，讓 `clock_gettime` 不必進核心）、動態連結、static PIE 的自我重定位（`rcrt1.o` + `_dl_relocate_static_pie`）。glibc 靜態執行檔多出的十幾個系統呼叫，大多對應這些功能的初始化。

> 想把這個精簡 libc 擴充成可用的版本（加上 `malloc`、`printf`），見既有課 [c_interview Final Project — mini libc](../c_interview/final-project-mini-libc.md)。

## 精簡 libc 的真實例子：musl 的 CRT

musl 的啟動程式碼和上面的精簡 libc 結構幾乎一樣。在 Alpine 容器裡實測：

```console
$ docker run --rm -v "$PWD":/w -w /w alpine:3.20 sh -c '
    apk add -q gcc musl-dev binutils
    gcc -v hello.c -o hm 2>&1 | grep collect2 | tr " " "\n" | grep -E "crt|^-l"
    gcc -O2 -static hello.c -o hello_musl_static && size hello_musl_static
    objdump -d --no-show-raw-insn hello_musl_static | sed -n "/<_start>:/,/^\$/p;/<_start_c>:/,/^\$/p"
    readelf -l hm | grep interpreter'
.../Scrt1.o
.../crti.o
.../13.2.1/crtbeginS.o
-lssp_nonshared
-lgcc
-lgcc_s
-lc
-lgcc
-lgcc_s
.../13.2.1/crtendS.o
.../crtn.o
   text	   data	    bss	    dec	    hex	filename
   3886	    344	   1744	   5974	   1756	hello_musl_static
0000000000401047 <_start>:
  401047:	xor    %rbp,%rbp
  40104a:	mov    %rsp,%rdi
  40104d:	lea    -0x401054(%rip),%rsi        # 0 <_init-0x401000>
  401054:	and    $0xfffffffffffffff0,%rsp
  401058:	call   40105d <_start_c>

000000000040105d <_start_c>:
  40105d:	mov    (%rdi),%esi
  401060:	lea    0x8(%rdi),%rdx
  ...
  40107b:	jmp    40135f <__libc_start_main>
      [Requesting program interpreter: /lib/ld-musl-x86_64.so.1]
```

（為了排版把容器內的命令拆成多行。）musl 的 `_start`（`arch/x86_64/crt_arch.h`）和我們的 `start.S` 幾乎逐行相同：清 `%rbp`、把 `%rsp` 交給 C 寫的 `_start_c`、對齊、`call`。`_start_c`（`crt/crt1.c`）取出 `argc = *sp`、`argv = sp + 1`，跳到 `__libc_start_main`；後者的 `__init_libc` 設定 `environ` 與 auxv、建立 TLS、以 `AT_RANDOM` 初始化 stack protector，接著執行 `_init` 與 `.init_array`，最後 `exit(main(argc, argv, envp))`。**靜態 hello world 的 text 只有 3886 位元組**，對比 glibc 的 781509——差異幾乎全在 glibc 為 locale、`dlopen`、執行緒等預先連結進來的程式碼。musl 不使用 `%rdx` 傳來的 `rtld_fini`。

| | glibc | musl | 本章精簡 libc |
|---|---|---|---|
| `_start` | 組語，呼叫 `__libc_start_main`（7 個參數） | 組語 + `_start_c` | 組語，呼叫 `__mini_start` |
| TLS / canary | 有，`AT_RANDOM` | 有，`AT_RANDOM` | 無（需 `-fno-stack-protector`） |
| 靜態 hello text | 781509 B | 3886 B | （`app` 2494 B） |
| 動態連結器 | `/lib64/ld-linux-x86-64.so.2` | `/lib/ld-musl-x86_64.so.1`（libc 本身兼任） | 無 |

## 編譯器的支援程式庫：libgcc 與 compiler-rt

C 的運算子看似都直接對應到處理器指令，其實取決於目標架構。硬體沒有對應指令的運算，編譯器改為呼叫**支援程式庫**中的函式：gcc 的是 libgcc，clang 可改用 LLVM 的 compiler-rt（提供同名函式）。

```c
/* helpers.c：哪些運算會變成 libgcc 的函式呼叫 */
unsigned __int128 div128(unsigned __int128 a, unsigned __int128 b)
{
    return a / b; /* 128 位元除法 */
}

long double add_ld(long double a, long double b)
{
    return a + b; /* long double 加法 */
}

int popcount(unsigned long x)
{
    return __builtin_popcountl(x); /* 計算 1 的個數 */
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O2 -c helpers.c -o h.o
$ objdump -dr h.o | grep -oE 'R_[A-Z0-9_]+\s+__[a-z0-9]+' | sort -u
R_X86_64_PLT32	__popcountdi2
R_X86_64_PLT32	__udivti3
$ gcc -std=c11 -O2 -mpopcnt -c helpers.c -o h2.o; objdump -dr h2.o | grep -oE 'R_[A-Z0-9_]+\s+__[a-z0-9]+' | sort -u
R_X86_64_PLT32	__udivti3
$ clang -std=c11 -O2 -c helpers.c -o h3.o; objdump -dr h3.o | grep -oE 'R_[A-Z0-9_]+\s+__[a-z0-9]+' | sort -u
R_X86_64_PLT32	__udivti3
$ objdump -d --no-show-raw-insn h.o | sed -n '/<add_ld>:/,/^$/p'
0000000000000020 <add_ld>:
  20:	endbr64
  24:	fldt   0x8(%rsp)
  28:	fldt   0x18(%rsp)
  2c:	faddp  %st,%st(1)
  2e:	ret
```

- **128 位元除法**：x86-64 沒有 128÷128 的指令，gcc 與 clang 都呼叫 `__udivti3`。
- **`long double` 加法**：x86-64 的 `long double` 是 x87 的 80 位元延伸精度，有硬體指令（`fldt`/`faddp`）；原文指出 arm64 Linux 的 `long double` 是 IEEE 128 位元格式，沒有硬體支援，會呼叫 `__addtf3`（本課未實測 arm64）。同一行 C 在兩種架構上的成本截然不同（[Ch 25](./25-ieee754.md)）。
- **`__builtin_popcountl`**：x86-64 基準指令集沒有 `popcnt`，gcc 呼叫 `__popcountdi2`；加 `-mpopcnt`（或 `-march=x86-64-v2`）才用指令；clang 在同樣條件下以位元運算行內展開（[Ch 24](./24-bitwise-tricks.md)）。
- 32 位元平台上連 64 位元除法都要求救：

  ```console
  $ echo 'unsigned long long div64(unsigned long long a, unsigned long long b) { return a / b; }' | gcc -m32 -O2 -c -x c - -o d32.o
  $ objdump -dr d32.o | grep -oE 'R_386_[A-Z0-9_]+\s+__[a-z0-9]+' | sort -u
  R_386_PLT32	__udivdi3
  ```

名稱裡的 `ti`、`di` 是 gcc 的**機器模式（machine mode）**：DImode = 64 位元整數、TImode = 128 位元整數、TFmode = 128 位元浮點。

**為什麼這些函式由編譯器自備，不放進 C 函式庫？** 一、哪些運算會變成函式呼叫、函式叫什麼，是編譯器產生程式碼的慣例，隨編譯器版本與 `-march` 變動，由編譯器維護最自然；二、同一個編譯器要搭配 glibc、musl、newlib，甚至不搭配任何 libc（核心、韌體、本章的精簡 libc 照樣要 64 位元除法）；三、`/` 是語言本身的運算子，freestanding 實作也必須支援。

### -nostdlib 連 libgcc 也一起拿掉了

GCC 手冊：`-nostdlib` 略過的標準程式庫**也包括 `libgcc.a`**，並寫道 "In most cases, you need libgcc.a even when you want to avoid other standard libraries."

```c
/* big.c：128 位元除法需要 libgcc */
#include "mini.h"

int main(int argc, char *argv[], char *envp[])
{
    (void) argv;
    (void) envp;
    unsigned __int128 n = (unsigned __int128) 1 << 100;
    unsigned __int128 q = n / (unsigned) argc; /* 除數在執行時期才知道 */
    return (int) (q >> 96);                    /* 2^100 >> 96 = 16 */
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O2 -fno-stack-protector -c start.S mini.c big.c
$ gcc -static -nostdlib start.o mini.o big.o -o big
/usr/bin/ld: big.o: in function `main':
big.c:(.text.startup+0x19): undefined reference to `__udivti3'
collect2: error: ld returned 1 exit status
$ gcc -static -nostdlib start.o mini.o big.o -lgcc -o big && ./big; echo "exit=$?"
exit=16
$ gcc -static -nostdlib -lgcc start.o mini.o big.o -o big2 2>&1 | grep undefined
big.c:(.text.startup+0x19): undefined reference to `__udivti3'
```

最後一個：`-lgcc` 寫在目的檔**前面**就沒用——掃過 `libgcc.a` 時還沒有任何未定義的 `__udivti3`（Ch 39 的單趟掃描）。

Linux 核心以 `ld` 直接連結 `vmlinux`，不經 gcc driver，libgcc 不會自動加入。依原文對 v6.8 的整理，多數架構不連 libgcc：arm 在 `arch/arm/lib/` 自備 `__aeabi_uidiv` 等函式，32 位元架構需要 64 位元除法時要明確呼叫 `include/linux/math64.h` 的 `div_u64` 一類函式——這就是為什麼在 32 位元核心裡直接寫 `u64 / u32` 會連結失敗。

libgcc 還有共享版 `libgcc_s.so.1`，提供 `_Unwind_RaiseException` 等 **stack unwinder**：C++ 例外、`-fexceptions` 下的 `__attribute__((cleanup))`、`pthread_cancel` 都靠它沿 `.eh_frame` 回溯堆疊（`gcc -v` 裡的 `-lgcc_s` 被 `--as-needed` 包住，沒用到就不記錄相依）。C 的 `setjmp`/`longjmp` 則**不需要** unwinder——它們是 libc 的函式，直接存取暫存器（[Ch 12](./12-setjmp-and-coroutines.md)）：

```console
$ nm -D /usr/lib/x86_64-linux-gnu/libgcc_s.so.1 | grep -E '_Unwind_RaiseException|__udivti3'
0000000000016c30 T _Unwind_RaiseException@@GCC_3.0
0000000000007400 T __udivti3@@GCC_3.0
$ nm -D /lib/x86_64-linux-gnu/libc.so.6 | grep -wE 'setjmp|longjmp|_setjmp'
0000000000042170 T _setjmp@@GLIBC_2.2.5
0000000000042180 W longjmp@@GLIBC_2.2.5
0000000000042160 T setjmp@@GLIBC_2.2.5
```

## 沒有作業系統時：newlib 與 libgloss

> **本節未實測**：需要 `gcc-arm-none-eabi`、`libnewlib-arm-none-eabi` 與 `qemu-arm`，本課環境未安裝。以下輸出取自原文（Ubuntu 24.04、arm-none-eabi-gcc 13.2.1、newlib 4.4.0、qemu-arm 8.2.2），標為「原文輸出」。想自己驗證：`sudo apt install gcc-arm-none-eabi libnewlib-arm-none-eabi qemu-user`。

libgcc 補足處理器缺少的指令；**作業系統缺少的服務**則由另一層補足。Linux 上 glibc 以系統呼叫完成 `write`、`brk`；嵌入式系統往往沒有 OS，同一份 C 函式庫卻要在模擬器、接上除錯器的開發板、各家 ROM monitor 上跑。Cygnus 開發的 **newlib** 把需要外界協助的部分縮減為十來個函式——`_write`、`_read`、`_sbrk`、`_exit`、`_fstat`、`_isatty` 等（底線開頭，不佔用使用者命名空間）——由 **libgloss**（"GNU Low-level OS support"）依執行環境各自實作。

```c
/* hello.c：以 newlib 建置的裸機程式 */
#include <stdio.h>

int main(void)
{
    printf("hello, %s\n", "newlib");
    return 0;
}
```

原文輸出（節錄）：

```console
$ arm-none-eabi-gcc -mcpu=cortex-a9 -O2 hello.c -o h0 2>&1 | grep -o "undefined reference to .*" | sort -u
undefined reference to `_close'
undefined reference to `_exit'
undefined reference to `_fstat'
...
undefined reference to `_sbrk'
undefined reference to `_write'
$ arm-none-eabi-gcc -mcpu=cortex-a9 -O2 --specs=nosys.specs hello.c -o h1 2>&1 | grep -o "warning: .*" | sort -u
warning: _close is not implemented and will always fail
...
$ qemu-arm ./h1; echo "exit=$?"
qemu: uncaught target signal 11 (Segmentation fault) - core dumped
exit=139
$ arm-none-eabi-gcc -mcpu=cortex-a9 -O2 --specs=rdimon.specs hello.c -o h2
$ qemu-arm ./h2; echo "exit=$?"
hello, newlib
exit=0
```

三次建置對應三種情況：

- **不指定 spec**：工具鏈不知道程式要跑在哪塊板子上，不連任何 libgloss，newlib 用到的十個 stub 全部未定義。
- **`nosys.specs`**（`-lnosys`）：stub 只把 `errno` 設為 `ENOSYS` 並傳回 −1；能連結，輸出卻無處可去。在 `qemu-arm` 中甚至到不了 `main`：standalone 版 `crt0.S` 把堆疊指標設為寫死的 0x80000（假想開發板的 RAM 頂端），在 qemu 的使用者行程中未映射，清 `.bss` 時第一個 `push` 就 SIGSEGV。
- **`rdimon.specs`**（`-lrdimon`）：用 **semihosting**——程式執行約定的陷入指令（A32 `SVC 0x123456`、Thumb `SVC 0xAB`、M-profile `BKPT 0xAB`、A64 `HLT 0xF000`），由除錯器或模擬器攔下，在主機上代為完成 I/O。`qemu-arm` 實作了這組請求，訊息就出現在主機終端機。

```
 應用程式 printf("hello")
       │
 newlib libc.a：stdio 緩衝、格式化（與環境無關）
       │
 _write、_sbrk、_exit … 十來個 stub
       ├── libnosys   ：傳回 ENOSYS
       ├── librdimon  ：semihosting 陷入指令 → 除錯器/QEMU 在主機上做 I/O
       └── 開發板 BSP  ：驅動 UART 等周邊
```

這和本章的精簡 libc 是同一個分工：`mini.c` 的 `syscall3`、`write`、`_exit` 就相當於 Linux 版的 libgloss。

### 裸機與 Linux 的差異

| 工作 | Linux (glibc) | 裸機（newlib + libgloss / picolibc） |
|---|---|---|
| 載入程式 | `execve` 映射 segment，`.bss` 由核心的零頁面填滿 | 映像燒在 flash；crt0 把 `.data` 初值從 flash 複製到 RAM，再把 `.bss` 清零 |
| 堆疊指標 | 核心交出對齊的 `sp` | crt0 依 linker script 設定；Cortex-M 由硬體在重置時從向量表第 0 個字組載入 |
| `argc`/`argv`/`envp`/auxv | 核心放在初始堆疊 | 通常沒有；semihosting 版以 `GetCmdLine` 向除錯器索取命令列 |
| 記憶體配置 | `brk`/`mmap`，越界存取未映射頁由 MMU 攔下 | `_sbrk` 從 `end` 往上長；堆疊往下長進 heap 時沒有分頁錯誤可攔 |
| 結束狀態 | `exit_group` 交給親代 | 沒有親代：無窮迴圈、重置，或 semihosting 回報 |
| stack protector | canary 取自 `AT_RANDOM` | newlib 不假設有亂數來源，預設用固定的 terminator canary；有硬體 RNG 時由應用程式覆寫 `__stack_chk_guard` |

第一列的複製與清零，源自 linker 替每個 section 記錄的兩個位址：執行時的 **VMA**（virtual memory address）與存放映像的 **LMA**（load memory address）。微控制器的 linker script 用 `> ram AT> flash` 把 `.data` 的 VMA 放 RAM、LMA 放 flash，再以 `ADDR`/`LOADADDR` 匯出成符號，crt0 才知道從哪複製到哪：

```
/* board.ld 節錄：flash 在 0x08000000、RAM 在 0x20000000 的微控制器 */
    .data : { *(.data*) } > ram AT> flash
    __data_start = ADDR(.data);
    __data_source = LOADADDR(.data);
    .bss : { *(.bss*) *(COMMON) } > ram
    __stack = ORIGIN(ram) + LENGTH(ram);
```

上電時 RAM 內容未定，crt0 完成這兩步之後，N1570 保證的「靜態儲存期物件在程式開始前完成初始化」（6.2.4 §3、5.1.2 §1）才成立——5.1.2 明言這對 freestanding 與 hosted 一體適用，**裸機的 crt0 不能省略這一步**。其他裸機特有的考量：newlib-nano（`--specs=nano.specs`）以精簡的 `printf`/`malloc` 把原文範例的 text 由 34532 降到 12904 位元組（代價是預設不支援浮點 `printf`）；中斷處理常式與 `main` 共用同一份函式庫，在中斷裡呼叫 `printf`/`malloc` 類似在 signal handler 裡呼叫非 async-signal-safe 函式。這些在 [Ch 42](./42-embedded-quiz-part1.md) 的嵌入式考題會再遇到。

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| glibc（動態） | 功能完整、相容性最好、與系統一同更新 | 啟動流程複雜；執行檔綁定 glibc 版本（`__libc_start_main@GLIBC_2.34`） | 一般 Linux 發行版 |
| glibc（`-static`） | 不依賴系統的 `.so` | 體積大（hello 78 萬位元組）；`dlopen`、NSS 等功能受限 | 救援工具、相容性要求 |
| musl（`-static`） | 小（hello 不到 4 KB text）、程式碼易讀、靜態連結友善 | 與 glibc 行為細節不同（locale、DNS、malloc 效能） | 容器、單一執行檔分發 |
| `-nostdlib` 自製 CRT | 完全掌控、極小 | 一切自己來；要記得 libgcc、`memcpy` 族、stack protector | 教學、shellcode 研究、極端受限環境 |
| newlib + libgloss / picolibc | 為無 OS 環境設計，I/O 可替換 | 要自己提供 stub 或 BSP | 微控制器、裸機 |

## 踩雷集錦

1. **「程式從 main 開始」**：之前還有 `ld.so`、`_start`、`__libc_start_main`、`.preinit_array`、建構子；之後還有 `atexit`、解構子、stdio 清空。在建構子裡用到尚未初始化的全域狀態，或在解構子裡用到已關閉的 fd（Ch 40 的 `dup`），都源於忘了這一點。
2. **「`return 256` 是錯誤碼」**：核心只保留低 8 位元，256 變成 0 = 成功。錯誤碼保持在 1–125。
3. **「`void main` 只是不標準，結果一樣」**：結束狀態是 `%eax` 裡剩下的垃圾（本章實測為 10）。在 CI 腳本裡這會讓成功的程式被判為失敗，或反過來。
4. **「`_exit` 和 `exit` 差不多」**：`_exit` 不清 stdio 緩衝、不跑 `atexit` 與解構子。導向檔案或管線時（全緩衝），輸出會整個消失。`fork` 後的子行程反而**應該**用 `_exit`。
5. **「`-nostdlib` 只是不要 libc」**：它也拿掉了 libgcc、所有 crt 物件。要 `-lgcc`（寫在目的檔之後），要自己提供 `memcpy`/`memset`/`memmove`/`memcmp`，要處理 stack protector。
6. **「自己寫的 `memset` 一定不會遞迴」**：gcc `-O2` 會把它的迴圈改寫成 `memset` 呼叫。用 `-fno-tree-loop-distribute-patterns` 或 `-ffreestanding`。
7. **「`argv[0]` 就是執行檔路徑」**：呼叫者可任意指定（`exec -a`）。安全相關的判斷用 `/proc/self/exe`。

## 進階：再往深一層

**`AT_SECURE` 與 secure-execution**：setuid 程式啟動時核心設 `AT_SECURE=1`，glibc 據此忽略 `LD_PRELOAD`、`LD_LIBRARY_PATH` 等危險環境變數（Ch 40）。`LD_SHOW_AUXV=1` 能看到它的值。

**vDSO**：`AT_SYSINFO_EHDR` 指向核心映射進每個行程的一小段 ELF 共享物件，`clock_gettime`、`gettimeofday` 在使用者空間就能完成。`ldd` 輸出裡的 `linux-vdso.so.1` 就是它。

**static PIE**：`gcc -static-pie` 產生載入位址隨機的靜態執行檔，`_start`（`rcrt1.o`）在碰任何需要重定位的指標之前，必須先自行套用 `R_X86_64_RELATIVE`——這是自己當自己的 `ld.so`。讀 glibc 的 `_dl_relocate_static_pie` 是理解 Ch 39 重定位與本章 CRT 的好練習。

**面試題型**：「`main` 之前發生了什麼？」（從 `execve` 講到 `.init_array`）；「`atexit` 為什麼是反序？」（和建構順序相反才能安全拆除依賴，類似 C++ 解構順序）；「`fork` 後子行程為什麼用 `_exit`？」。

## 動手練習

1. 在 gdb 裡停在 `_start`，用 `x/40gx $rsp` 找出 auxv 的 `AT_RANDOM`（類型 25），再 `x/16xb` 它指向的位址，和 `LD_SHOW_AUXV=1` 的輸出對照。
2. 在 `order.c` 加一個 `at_quick_exit` 與 `quick_exit(5)` 分支，觀察哪些收尾會執行、stdout 緩衝有沒有被清空（glibc 不清）。
3. 給精簡 libc 加上 `strace` 看得到的 `read` 與 `getchar`，寫一個 `cat` 程式，用 `strace -c` 比較它和 `/bin/cat` 的系統呼叫數。
4. 把 `start.S` 的 `and $-16, %rsp` 刪掉，再把它改成 `sub $8, %rsp`（故意弄歪），兩種版本都在 `main` 裡用 `-O2` 編一段可能使用 SSE 對齊存取的程式（例如區域 `long double` 陣列或大 struct 複製）。先預測哪一版會出事、為什麼刪掉那行通常沒事（提示：psABI 對進入時 `%rsp` 的保證），再說明 glibc 與 musl 為何仍保留這行。
5. 用 `-static-pie` 編 `stack.c`，連續跑兩次比較 `AT_ENTRY`，再和 `-static` 的版本比較。

## 本章重點整理

- C 的語言本體不含 I/O 與啟動邏輯，hosted 環境的這些工作由 CRT（crt 物件 + libc + libgcc + ld.so）完成；freestanding 環境進入點由實作定義。
- `execve` 依格式處理常式載入程式；核心在初始堆疊擺 `argc`/`argv`/NULL/`envp`/NULL/auxv，`%rsp` 指向 `argc`，`%rdx` 是 `ld.so` 給的 `_dl_fini`（靜態連結為 0）。
- glibc 的 `_start` 只做最少的事，把 7 個參數交給 `__libc_start_main`；初始化順序 preinit → 建構子（依優先序）→ `main`；結束順序 `atexit`（反序）→ 解構子（經 `_dl_fini`）→ 清 stdio → `exit_group`，核心只留低 8 位元。
- `_exit` 跳過所有收尾，全緩衝的輸出會整個遺失。
- 自製 CRT 的必修課：組語進入點、對齊堆疊、CFI 終止回溯、系統呼叫 clobber、自備 `memcpy` 族、防 loop idiom 遞迴、stack protector、記得 `-lgcc`。
- libgcc/compiler-rt 補硬體缺的運算（`__udivti3`、`__popcountdi2`），newlib/libgloss 補 OS 缺的服務（`_write`、`_sbrk`）。

## 自我檢核

- [ ] 不看筆記，從 `execve` 開始說到 `main` 的第一行，至少講出五個步驟和負責的元件？
- [ ] 能畫出進入 `_start` 時的堆疊佈局，並說明 `envp` 為什麼等於 `argv + argc + 1`？
- [ ] 能解釋為什麼解構子在所有 `atexit` 函式之後執行（提示：誰最早登記）？
- [ ] 同事說他的程式「輸出到檔案就少了最後幾行」，你會先懷疑什麼？
- [ ] 如果要在一個沒有 libc 的環境（例如寫 shellcode 測試程式或 bootloader）寫 C，你會遇到哪四類連結錯誤，各怎麼解？
- [ ] 能說明為什麼 128 位元除法的函式放在 libgcc 而不是 libc？

## 延伸閱讀

### 部落格 / 技術文章

- **[How programs get run: ELF binaries](https://lwn.net/Articles/631631/)** — David Drysdale（LWN，2015）
  - **這篇說什麼**：沿著 `load_elf_binary()` 逐段解說核心怎麼讀 program header、映射 segment、處理 `PT_INTERP`、建立 auxv。
  - **讀哪裡**：整篇；前一篇〈How programs get run〉講 `execve` 與 `binfmt` 框架，可先讀。
  - **為什麼值得讀**：本章「核心如何辨識執行檔」與「初始堆疊」兩節的核心原始碼導讀。前提：本章內容。
- **[Linux x86 Program Start Up, or How the heck do we get to main()?](http://dbp-consulting.com/tutorials/debugging/linuxProgramStartup.html)** — Patrick Horgan
  - **這篇說什麼**：用一張大圖畫出 `_start` → `__libc_start_main` → `__libc_csu_init` → `_init` → `main` 的流程。
  - **讀哪裡**：整篇。**注意**：這篇寫於 glibc 2.34 之前，`__libc_csu_init` 已被移除（本章 `_start` 反組譯中 `init` 參數為 0 的原因），讀時對照本章的現代版本。
  - **為什麼值得讀**：最經典的一張啟動流程圖，看懂新舊差異本身就是好練習。
- **[A Whirlwind Tutorial on Creating Really Teensy ELF Executables for Linux](https://www.muppetlabs.com/~breadbox/software/tiny/teensy.html)** — Brian Raiter
  - **這篇說什麼**：從 C 的 hello 一路縮到 45 位元組的 ELF，過程中拿掉 libc、crt、section header。
  - **讀哪裡**：整篇；前半與本章精簡 libc 重疊，後半是 ELF 格式的極限把玩。
  - **為什麼值得讀**：把「CRT 到底提供了什麼」推到極致的思想實驗。

### 官方文件 / Spec

- **[System V ABI, AMD64 Architecture Processor Supplement](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：3.4「Process Initialization」——初始堆疊佈局、auxv 類型、進入時暫存器（`%rdx`、`%rsp` 對齊）的規定都在這裡。
- **[GCC Internals: The GCC low-level runtime library](https://gcc.gnu.org/onlinedocs/gccint/Libgcc.html)**
  - **讀哪裡**：「Integer library routines」與「Soft float library routines」列出 `__udivti3`、`__addtf3` 等所有函式與機器模式命名規則。
- **[GCC 手冊：Link Options](https://gcc.gnu.org/onlinedocs/gcc/Link-Options.html)**
  - **讀哪裡**：`-nostartfiles`、`-nodefaultlibs`、`-nostdlib`、`-static-pie` 條目，說明每個選項拿掉哪些東西。

### 書籍

- **《Advanced Programming in the UNIX Environment》（3rd ed.）** — W. Richard Stevens & Stephen A. Rago（2013）
  - **這本書的定位**：UNIX 程式設計的標準參考書；年代雖久，第 7 章的內容至今適用。
  - **讀哪幾章**：第 7 章 Process Environment（`main`、`exit`/`_exit`、`atexit`、環境變數、記憶體佈局）；第 8 章 8.3（`fork` 與 stdio 緩衝的交互作用）。
- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** — Bryant & O'Hallaron（2015）
  - **讀哪幾章**：7.9「Loading Executable Object Files」、8.4「Process Control」中 `execve` 與初始堆疊的圖。

從原始碼到行程的整條路走完了：編譯（Ch 36–38）、靜態連結（Ch 39）、動態連結（Ch 40）、執行階段（本章）。練習 E 把這些拼起來：用 `LD_PRELOAD` 寫一個真正的 `malloc` 追蹤器，再在執行期把 Brainfuck 編成機械碼直接跑。

→ [練習 E：LD_PRELOAD malloc 追蹤器與 Brainfuck JIT](./practice-e-malloc-tracer-and-bf-jit.md)
