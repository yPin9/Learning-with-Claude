# Ch 11 — 行程記憶體布局、x86-64 SysV ABI 與 stack frame

> **目標**：能在腦中畫出一個執行中 C 程式的位址空間，並用 `gcc -S` 與 gdb 逐行看懂一次函式呼叫（參數怎麼傳、返回位址放哪、frame 怎麼建立與拆除、哪些暫存器誰負責保存）。

> **對應原文**：[你所不知道的 C 語言：函式呼叫篇](https://hackmd.io/@sysprog/c-function) 的「從 function prototype 談起」「Process 和 C 程式的關聯」「回傳值」「Stack」等節

> **環境**：gcc 11.4、gdb 12.1，WSL2 Ubuntu 22.04 x86-64（kernel 6.18）。除非特別說明，輸出都是在這個環境實際跑出來的；位址每次執行不同的地方會註明。AT&T 與 Intel 語法兩種都會出現：`gcc -S` 預設輸出 AT&T，`gdb` 的 `set disassembly-flavor intel` 切成 Intel。

## 為什麼需要這個？

寫了一陣子 C 之後，你會遇到一些「看起來和語法無關」的問題：

- 為什麼遞迴太深程式會被殺掉？
- 為什麼 `printf("%f", 1)` 印出垃圾，卻不報錯？
- 為什麼組合語言、Rust、Go 寫的函式能被 C 呼叫（反過來也行）？

答案都在同一個地方：**函式呼叫是一份「雙方事先約好」的協定**。這份協定規定參數放在哪個暫存器、返回值放哪、誰負責保存哪些暫存器、stack 上的東西怎麼排。這份協定叫 **ABI**（Application Binary Interface，應用程式二進位介面）。

在沒有這份協定的年代，這件事靠「編譯器自己說了算」。早期 C 編譯器（1972–1973 年 dmr 的版本）甚至不要求函式先宣告再使用：看到 `name(` 就當成「回傳 `int`、參數不知道」的函式去呼叫。後來標準化把這種寬鬆收緊，才有了 function prototype。下一節我們先親手看看「不告訴編譯器參數型別」會出什麼事。

## 先建立直覺

把 call stack 想成**一疊便條紙**，每張紙是一次函式呼叫：

```
   呼叫 funcB 時                      funcB 返回後
 ┌────────────────┐                ┌────────────────┐
 │ funcB 的便條紙 │ ← 最上面       │                │
 ├────────────────┤                ├────────────────┤
 │ funcA 的便條紙 │                │ funcA 的便條紙 │ ← 又變最上面
 ├────────────────┤                ├────────────────┤
 │ main  的便條紙 │                │ main  的便條紙 │
 └────────────────┘                └────────────────┘
```

每張便條紙上寫著：「做完之後要回到哪裡」（返回位址）、「這次呼叫的區域變數」、「我借了呼叫者哪些暫存器要還」。函式返回就是撕掉最上面那張。

這個比喻在一個地方會誤導你：真實的 stack 上，被撕掉的紙**不會被擦掉**，只是 `rsp` 移走了，內容還留在記憶體裡，下一次呼叫會直接蓋上去。這一點在 Ch 14 談安全時很重要。

> 如果你對指標與位址還不熟，先回看 [Ch 3 — 指標入門重建](./03-pointer-basics.md)。GDB 的基本操作見 [Ch 5 — GDB 實戰](./05-gdb-in-practice.md)。

## 從 function prototype 談起

### 沒有 prototype 會怎樣

舊式（K&R）C 的規則：函式在被呼叫前若沒有宣告，編譯器隱含宣告為 `extern int name();`，也就是「回傳 `int`、**對參數沒有任何假設**」。C99 移除了這個隱含宣告規則，C23 更進一步移除舊式函式定義，並讓 `int f()` 等同 `int f(void)`。

先看編譯器怎麼處理「有 prototype 但呼叫錯誤」（`proto1.c`，原文的 `factorial()` 少一個引數）：

```
$ gcc -Wall proto1.c -o proto1
proto1.c:6:20: error: too few arguments to function ‘factorial’
    6 |     printf("%d\n", factorial());
      |                    ^~~~~~~~~
proto1.c:3:5: note: declared here
    3 | int factorial(int n);
```

這是 constraint violation（N1570 6.5.2.2p2：有 prototype 時，引數個數必須與參數個數一致），編譯器必須診斷，gcc 直接報錯。

再看**完全沒宣告**就呼叫（`proto2.c`，`twice` 與 `half` 定義在 `main` 之後）：

```
$ gcc -std=gnu17 proto2.c -o proto2
proto2.c:4:20: warning: implicit declaration of function ‘twice’ [-Wimplicit-function-declaration]
proto2.c:5:20: warning: implicit declaration of function ‘half’ [-Wimplicit-function-declaration]
proto2.c:5:14: warning: format ‘%f’ expects argument of type ‘double’, but argument 2 has type ‘int’ [-Wformat=]
proto2.c:10:8: error: conflicting types for ‘half’; have ‘double(double)’
proto2.c:5:20: note: previous implicit declaration of ‘half’ with type ‘int()’
```

gcc 11 對 `twice` 只給警告，但 `half` 回傳 `double`，和隱含宣告的 `int()` 衝突，變成錯誤。加上 `-std=c99 -pedantic-errors`，連 `twice` 也是 error。**原文說 GCC 14 起這類行為才視為錯誤；實測 gcc 11.4 預設只是警告**（clang 14 也是警告）。要在舊編譯器上得到同樣的嚴格度，自己加 `-Werror=implicit-function-declaration`。

### 型別不一致的呼叫：編譯器不查，連結器也不查

如果宣告與定義**分屬不同檔案**，連 conflicting types 都不會報。`kr_main.c`：

```c
#include <stdio.h>

int half();      /* 舊式宣告：不帶參數列，編譯器對參數「沒有任何假設」 */

int main(void) {
    printf("half(5) = %d\n", half(5));
    return 0;
}
```

`kr_half.c`：`double half(double x) { return x / 2; }`

```
$ gcc -Wall -Wextra -std=gnu17 kr_main.c kr_half.c -o kr
$ ./kr
half(5) = 0
```

沒有任何警告。為什麼是 0？呼叫端以為 `half` 收 `int`，把 `5` 放進 `edi`；被呼叫端以為自己收 `double`，去讀 `xmm0`（那裡剛好是 0.0）。返回值同理：被呼叫端把 `double` 放在 `xmm0`，呼叫端卻去讀 `eax`。**兩邊各自遵守 ABI，但對「型別」的理解不同，結果是靜默的垃圾**。規格書把這種情況列為未定義行為（N1570 6.5.2.2p9：被呼叫函式的定義型別與呼叫表達式的型別不相容）。

這就是 prototype 的價值：讓編譯器能在**呼叫端**就知道要用哪些暫存器、做哪些型別轉換。C9X Rationale 還點出另一個好處：`const char *` 這類修飾能讓最佳化器知道函式不會修改指標指向的內容。

> 呼叫端與被呼叫端必須對 ABI 的理解一致，這件事由 prototype（編譯期）和 ABI 文件（連結期、跨語言）共同保證。接下來進入 ABI 本身。

## 行程記憶體布局

核心的 loader 把 ELF 檔的各個 section 載入、重組成 process 的 segment。我們不要只看示意圖，直接問核心：`maps.c` 印出各種物件的位址，再印出 `/proc/self/maps`。

```c
int g_init = 10;          /* .data */
int g_zero;               /* .bss  */
const int g_ro = 7;       /* .rodata */
char *msg = "hello";      /* msg 本身在 .data，"hello" 在 .rodata */
/* main 內：local（stack）、malloc(100)（heap）、printf（libc） */
```

```
main      (text)   0x5565f4a9a239
g_ro      (rodata) 0x5565f4a9b004
"hello"   (rodata) 0x5565f4a9b008
g_init    (data)   0x5565f4a9d010
g_zero    (bss)    0x5565f4a9d02c
malloc(100)(heap)  0x55661c8f12a0
local     (stack)  0x7fff8ef3e72c
printf    (libc)   0x724167c60680
```

（位址每次執行不同，原因見後面的 ASLR。）對照 `/proc/self/maps`（節錄並縮短路徑）：

```
5565f4a99000-5565f4a9a000 r--p  .../maps          ← ELF header 等唯讀資料
5565f4a9a000-5565f4a9b000 r-xp  .../maps          ← .text（可執行）：main 在 ...a239
5565f4a9b000-5565f4a9c000 r--p  .../maps          ← .rodata："hello"、g_ro 在這頁
5565f4a9c000-5565f4a9d000 r--p  .../maps          ← .init_array、GOT 等（RELRO 後唯讀，見 Ch 14）
5565f4a9d000-5565f4a9e000 rw-p  .../maps          ← .data 與 .bss：g_init、g_zero
55661c8f1000-55661c912000 rw-p  [heap]            ← malloc 拿到的空間
724167c28000-724167dbd000 r-xp  .../libc.so.6     ← libc 的 .text：printf 在這
7fff8ef20000-7fff8ef41000 rw-p  [stack]           ← local 在這
```

欄位依序是：位址範圍、權限（`r` 讀 `w` 寫 `x` 執行，`p` 表示 private）、檔案偏移、裝置、inode、路徑。重點觀察：

1. **權限已經把 code 與 data 分開**：`.text` 是 `r-x`、`.data` 是 `rw-`。沒有任何一頁同時可寫又可執行（這是 NX/W^X 的基礎，Ch 14）。
2. **`"hello"` 與 `g_ro` 在唯讀頁**：寫入它們會 SIGSEGV。`char *msg = "hello"` 中，`msg` 這個指標變數在 `.data`（可寫），它**指向**的字串在 `.rodata`。
3. **`.bss` 不佔磁碟空間**：`size maps` 顯示 `text 2845 / data 680 / bss 16`，bss 在 ELF 中只記錄大小，載入時由核心配置並清零，這也是全域變數預設為 0 的原因。
4. **heap 在低位址、stack 在高位址**，中間隔著巨大的空洞，mmap 的共享函式庫（libc、ld.so）落在空洞裡。
5. 這個 `maps` 是 **PIE**（Position Independent Executable，位置獨立執行檔）：主程式本身被載入到 `0x55...` 這種隨機位址，而不是傳統的 `0x400000`。

```
 高位址  0x7fff...  ┌─────────────────┐
                    │     [stack]     │  向低位址成長（每次 call 往下）
                    ├─────────────────┤
                    │       ↓         │
                    │   （未對映）     │
                    │       ↑         │
                    ├─────────────────┤
                    │ libc / ld.so    │  mmap 區（共享函式庫）
                    ├─────────────────┤
                    │   （未對映）     │
                    ├─────────────────┤
                    │     [heap]      │  向高位址成長（brk / mmap）
                    ├─────────────────┤
                    │ .data / .bss    │  rw-
                    │ .rodata         │  r--
                    │ .text           │  r-x
 低位址  0x55...    └─────────────────┘
```

### ASLR：每次位址都不同

跑兩次 `./maps`，`local` 的位址是 `0x7fff25468ccc` 與 `0x7fff87f6e98c`，完全不同。這是 ASLR（Address Space Layout Randomization，位址空間配置隨機化）：核心每次 `execve` 時隨機化 stack、heap、mmap 區（以及 PIE 主程式）的基底。系統設定在 `/proc/sys/kernel/randomize_va_space`，本機值為 `2`（完整隨機化，包含 heap）。

**gdb 預設會關掉 ASLR**，所以你在 gdb 裡看到的位址（如 `0x7fffffffe...`）每次都一樣，但和直接執行時不同。不用 gdb 也能關：`setarch x86_64 -R ./maps`，實測兩次都得到 `local = 0x7fffffffe06c`、`main = 0x555555555239`。

> 關閉 ASLR 只為了讓教學輸出可重現。ASLR 作為防禦的角色在 [Ch 14](./14-function-call-security.md)。

## x86-64 System V ABI：函式呼叫的協定

本課使用 **System V AMD64 ABI**（Linux、macOS、BSD 在 x86-64 上通用；Windows 的 x64 ABI 不同，見後面的對比）。核心規則（psABI §3.2）：

| 項目 | 規則 |
|---|---|
| 整數／指標參數 | 依序 `rdi, rsi, rdx, rcx, r8, r9` |
| 浮點參數 | `xmm0`–`xmm7` |
| 第 7 個以上整數參數 | 放 stack，由右往左 push |
| 整數返回值 | `rax`（128 bit 時 `rax:rdx`） |
| 浮點返回值 | `xmm0`（兩個 `double` 的 struct 為 `xmm0:xmm1`） |
| callee-saved（被呼叫者必須保存還原） | `rbx, rbp, r12–r15`（以及 `rsp`） |
| caller-saved（呼叫者若要保留就自己存） | `rax, rcx, rdx, rsi, rdi, r8–r11`、所有 `xmm` |
| variadic 呼叫 | `al` 放用到的 vector 暫存器個數 |
| 呼叫瞬間的 stack | `call` 執行**之前** `rsp` 必須是 16 的倍數 |

> 這張表你不必背，但要知道它存在，遇到問題時回來查。`syscall` 指令用另一套：參數是 `rdi, rsi, rdx, r10, r8, r9`（第 4 個換成 `r10`，因為 `syscall` 指令本身會用掉 `rcx`）。

### 參數：前 6 個走暫存器，其餘走 stack

`args.c` 把 8 個參數傳給 `sum8`，`gcc -O0 -S` 的結果（去掉 `.cfi` 雜訊）：

```asm
sum8:                                  # 被呼叫端
    pushq   %rbp
    movq    %rsp, %rbp
    movq    %rdi, -8(%rbp)             # 前 6 個：先從暫存器存進自己的 frame（-O0 的習慣）
    movq    %rsi, -16(%rbp)
    movq    %rdx, -24(%rbp)
    movq    %rcx, -32(%rbp)
    movq    %r8,  -40(%rbp)
    movq    %r9,  -48(%rbp)
    ...
    movq    16(%rbp), %rax             # 第 7 個參數：在 rbp+16（跳過 saved rbp 與 return address）
    ...
    movq    24(%rbp), %rax             # 第 8 個
    ...
main:                                  # 呼叫端
    subq    $16, %rsp
    pushq   $8                         # 先 push 第 8 個，再 push 第 7 個（由右往左）
    pushq   $7
    movl    $6, %r9d
    movl    $5, %r8d
    movl    $4, %ecx
    movl    $3, %edx
    movl    $2, %esi
    movl    $1, %edi
    call    sum8
    addq    $16, %rsp                  # 呼叫端負責把 stack 參數彈掉
```

被呼叫端用 `rbp+16`、`rbp+24` 取 stack 參數：`rbp+0` 是 saved rbp，`rbp+8` 是 return address，所以第一個 stack 參數在 `rbp+16`。注意 `main` 在 push 兩個參數之前先 `subq $16, %rsp`：兩次 push 共 16 bytes，加上這 16 bytes，才能讓 `call` 當下 `rsp` 對齊 16。

**為什麼是前 6 個？** 這是 ABI 設計者的取捨：x86-64 比 i386 多了 8 個通用暫存器，大部分函式參數不超過 6 個，全部走暫存器能省掉對 stack 的讀寫。i386 的 cdecl 把所有參數都 push 到 stack。

### 返回值：`ret` 管位址，`rax` 管資料

`return i;` 在組語層級是兩個獨立動作：把 `i` 放進 `eax`，再 `ret`。`ret` 只做一件事：從 stack 頂 pop 出返回位址放進 `rip`。結構返回值的規則更有趣，`ret.c` 用 `gcc -O1 -S` 觀察（節錄）：

```c
typedef struct { int a[2]; } S8;     /*  8 bytes */
typedef struct { int a[4]; } S16;    /* 16 bytes */
typedef struct { int a[8]; } S32;    /* 32 bytes */
typedef struct { double x, y; } V2;  /* 16 bytes，浮點 */
```

```asm
get8:   movabsq $8589934593, %rax                  # 0x0000000200000001：a[1]=2、a[0]=1 打包成一個 64 位元
        ret
get16:  movabsq $8589934593, %rax                  # a[0..1] -> rax
        movabsq $17179869187, %rdx                 # a[2..3] -> rdx（0x0000000400000003）
        ret
get32:  movq    %rdi, %rax                         # 超過 16 bytes：rdi 是呼叫端給的「隱藏第一參數」
        movl    $1, (%rdi)                         # 直接寫進呼叫端預留的空間
        movl    $2, 4(%rdi)
        ...
        ret                                        # rax 也回傳同一個位址
getv:   movsd   .LC0(%rip), %xmm0                  # 兩個 double 各進一個 xmm
        movsd   .LC1(%rip), %xmm1
```

規則歸納：**≤ 16 bytes 的 struct 依成員型別拆進 `rax/rdx`（整數類）或 `xmm0/xmm1`（浮點類）；超過 16 bytes 由呼叫端在自己的 stack 預留空間，把位址當作隱藏的第一個參數放 `rdi`，被呼叫者寫完後把同一個位址放進 `rax` 回傳**。所以大 struct 的傳值返回有隱藏的記憶體拷貝成本，也讓真正的第一個參數被擠到 `rsi`。

> 原文的實驗用 gcc 7.3 `-O0`，輸出是用 stack 中轉的版本；這裡改用 `-O1` 讓結果更精簡，規則相同。

### 用 gdb 逐步看一次呼叫

回到原文的小程式 `stack.c`，加 `-g -no-pie -fcf-protection=none` 編譯：

```c
int funcB(int a) { return a + 1; }
int funcA(int b) { return funcB(b); }
int main() { int a = funcA(1); return 0; }
```

`gcc -S -O0` 的完整輸出：

```asm
funcB:  pushq %rbp ; movq %rsp,%rbp ; movl %edi,-4(%rbp) ; movl -4(%rbp),%eax
        addl $1,%eax ; popq %rbp ; ret
funcA:  pushq %rbp ; movq %rsp,%rbp ; subq $8,%rsp ; movl %edi,-4(%rbp)
        movl -4(%rbp),%eax ; movl %eax,%edi ; call funcB ; leave ; ret
main:   pushq %rbp ; movq %rsp,%rbp ; subq $16,%rsp ; movl $1,%edi
        call funcA ; movl %eax,-4(%rbp) ; movl $0,%eax ; leave ; ret
```

**prologue（序言）** 是三步：`push rbp`（保存呼叫者的 frame pointer）→ `mov rbp, rsp`（新的 `rbp` 指向剛存的舊值，成為本 frame 的錨點）→ `sub rsp, N`（為區域變數預留空間）。**epilogue（尾聲）** 反過來：`leave`（等於 `mov rsp, rbp; pop rbp`）→ `ret`。`funcB` 是 leaf function，沒有 `sub rsp`，結尾用 `pop rbp` 即可。

用 gdb 在 `funcB` 的 `push rbp` 執行**之後**停下，看實際的 stack：

```
(gdb) break *funcB+4
(gdb) run
(gdb) p $rsp
$1 = (void *) 0x7fffffffe108
(gdb) p $rbp
$2 = (void *) 0x7fffffffe108
(gdb) x/10gx $rsp
0x7fffffffe108: 0x00007fffffffe120  0x000000000040112a
0x7fffffffe118: 0x00000001178bfbff  0x00007fffffffe140
0x7fffffffe128: 0x000000000040113e  0x0000000000001000
0x7fffffffe138: 0x0000000000401020  0x0000000000000001
0x7fffffffe148: 0x00007ffff7c29d90  0x0000000000000000
(gdb) bt
#0  funcB (a=32767) at stack.c:1
#1  0x000000000040112a in funcA (b=1) at stack.c:6
#2  0x000000000040113e in main () at stack.c:10
```

（`a=32767` 是因為此刻 `funcB` 還沒把 `edi` 存進 frame，gdb 讀到了垃圾。stack 位址在不同機器、環境變數大小不同時會平移。）把這 10 個 qword 對上 frame 結構：

```
 高位址
 0x7fffffffe148  0x00007ffff7c29d90  ← main 的返回位址（回到 libc 的 __libc_start_call_main）
 0x7fffffffe140  0x0000000000000001  ← main 的 saved rbp（呼叫 main 的那層留下的）
 0x7fffffffe138  0x0000000000401020  ← main 的區域變數 a 在 e13c（高 4 bytes = 0，尚未賦值）
 0x7fffffffe130  0x0000000000001000  ← main sub rsp,0x10 預留的另一半
                 ─────────────── main 的 frame 結束，rbp = 0x7fffffffe140 ───────────────
 0x7fffffffe128  0x000000000040113e  ← funcA 的返回位址（call funcA 的下一條指令，在 main 裡）
 0x7fffffffe120  0x00007fffffffe140  ← funcA 的 saved rbp（= main 的 rbp）   ← funcA 的 rbp
 0x7fffffffe118  0x00000001178bfbff  ← funcA 的區域變數：高 4 bytes (e11c) = 1 就是參數 b；低 4 bytes 未使用
                 ─────────────── funcA 的 frame 結束 ───────────────
 0x7fffffffe110  0x000000000040112a  ← funcB 的返回位址（call funcB 的下一條指令，在 funcA 裡）
 0x7fffffffe108  0x00007fffffffe120  ← funcB 的 saved rbp（= funcA 的 rbp）   ← funcB 的 rbp = rsp
 低位址
```

三個觀察：

1. **frame pointer 串成鏈**：`funcB` 的 saved rbp 指向 `funcA` 的 rbp，後者再指向 `main` 的 rbp。gdb 的 `bt` 就是沿著這條鏈（或更精確地，用 `.eh_frame` 的 CFI 資訊）往上走。
2. **小端序**：`0x00000001178bfbff` 的高 4 bytes 是 `1`，因為 x86 是 little-endian，位址較高的 4 bytes 是整個 qword 的高位。`b = 1` 放在 `rbp-4`（= `0x...e11c`）。
3. **返回位址就在 saved rbp 的正上方（高位址側）**：這個事實是 Ch 14 的核心。

`info frame` 給出同樣資訊的整理版：

```
(gdb) info frame
Stack level 0, frame at 0x7fffffffe118:
 rip = 0x40110a in funcB (stack.c:1); saved rip = 0x40112a
 called by frame at 0x7fffffffe130
 Arglist at 0x7fffffffe108, args: a=32767
 Locals at 0x7fffffffe108, Previous frame's sp is 0x7fffffffe118
 Saved registers:
  rbp at 0x7fffffffe108, rip at 0x7fffffffe110
```

「frame at 0x7fffffffe118」稱為 **CFA**（Canonical Frame Address）：呼叫者在執行 `call` **之前**的 `rsp` 值。`Previous frame's sp` 也是它。

### 一個值得注意的細節：red zone 與 stack 對齊

仔細看 `funcB`：它沒有 `sub rsp`，卻執行了 `mov DWORD PTR [rbp-0x4], edi`。此刻 `rbp == rsp`，所以這個寫入在 `rsp` **下方** 4 bytes。這是允許的，因為 ABI 規定 **`rsp` 下方 128 bytes 是 red zone**：leaf function 可以不調整 `rsp` 直接使用，訊號處理函式與核心中斷處理不會動它（x86-64 ABI §3.2.2）。

用 `saved.c` 的 `leaf_arr` 看 `-O2` 的實際效果（要加 `-fno-stack-protector`，否則 Ubuntu 預設的 `-fstack-protector-strong` 會因為陣列而插入 canary 並 `sub rsp`，見 Ch 14）：

```
$ gcc -O2 -fno-stack-protector -S saved.c            $ gcc -O2 -fno-stack-protector -mno-red-zone -S saved.c
leaf_arr:                                            leaf_arr:
    xorl  %edx, %edx                                     subq  $40, %rsp          ← 多了這行
    xorl  %eax, %eax                                     xorl  %edx, %edx
.L5:                                                     xorl  %eax, %eax
    movslq %eax, %rcx                                .L5:
    addl  $1, %eax                                       movslq %eax, %rcx
    movl  %edx, -40(%rsp,%rcx,4)   ← 寫到 rsp 下方         addl  $1, %eax
    ...                                                  movl  %edx, (%rsp,%rcx,4)
    movl  -40(%rsp,%rdi,4), %eax                         ...
    ret                                                  addq  $40, %rsp          ← 以及這行
                                                         ret
```

Linux 核心用 `-mno-red-zone` 編譯，因為核心的中斷隨時可能發生，且發生時不會跳過 red zone，會直接蓋掉 `rsp` 下方的資料。

**stack 對齊**則是另一個容易被忽略的規則：`call` 之前 `rsp` 要是 16 的倍數，所以進入函式時（`call` 已經 push 了 8 bytes 返回位址）`rsp ≡ 8 (mod 16)`，`push rbp` 之後又回到 `≡ 0`。原因是 SSE 的 `movaps` 等指令要求 16 bytes 對齊的記憶體運算元。我們故意違反它（`misalign.s`，手寫組語在 `call printf` 前多做一次 `sub $8, %rsp`）：

```
$ gcc misalign.s -o ok && ./ok
pi = 3.140000
$ gcc -Wa,--defsym,MISALIGN=1 misalign.s -o bad && ./bad
Segmentation fault (core dumped)
$ gdb -q -batch -x g2.gdb ./bad          # g2.gdb：run / x/i $pc / p/x $rsp
Program received signal SIGSEGV, Segmentation fault.
0x00007ffff7c606ab in __printf (format=0x555555556004 "pi = %f\n") at ./stdio-common/printf.c:28
=> 0x7ffff7c606ab <__printf+43>:	movaps %xmm0,0x50(%rsp)
$1 = 0x7fffffffe058
```

`printf` 的 prologue 把 `xmm0` 存進 stack，用的是要求 16 對齊的 `movaps`，位址 `0x7fffffffe058 + 0x50 = ...a8`，不是 16 的倍數，所以 SIGSEGV。這是 C 程式碼本身不會踫到的一類錯誤，但手寫組語、`longjmp` 到亂改過的 `rsp`、或攻擊者偽造 stack 時都會遇到。

> 觀察：上面 `stack.c` 的 `funcA` 在呼叫 `funcB` 時，gdb 顯示 `funcB` 的 CFA 是 `0x7fffffffe118`（≡ 8 mod 16），也就是 `-O0` 的 gcc 在這條「沒有任何函式需要對齊」的呼叫鏈上，讓 `funcA` 用 `sub rsp, 8` 而不是 `sub rsp, 16`。我沒有查到 gcc 文件明確說明這個行為，所以只當作實測觀察；ABI 條文仍然要求 `call` 前對齊，寫手寫組語時不要依賴編譯器的這種寬鬆。

## callee-saved 與 caller-saved：誰負責保存

CPU 只有十幾個暫存器，函式呼叫的時候誰可以破壞哪個，必須約定好：

- **caller-saved**（`rax, rcx, rdx, rsi, rdi, r8–r11`）：被呼叫的函式**可以隨便改**。呼叫者如果需要 call 之後還用某個值，要在 call 前自己存起來。
- **callee-saved**（`rbx, rbp, r12–r15`）：被呼叫的函式**如果要用就必須先存、返回前還原**。呼叫者可以放心把跨呼叫的值放在這些暫存器裡。

看 `saved.c` 的 `keep`，`a` 與 `b` 在 `ext()` 呼叫之後還要用：

```c
int keep(int a, int b) { int t = ext(a); return t + a + b; }
```

```asm
keep:                                  # gcc -O2
    pushq  %rbp                        # 自己要用 rbp、rbx（callee-saved），先存
    movl   %esi, %ebp                  # b -> ebp：活過 call
    pushq  %rbx
    movl   %edi, %ebx                  # a -> ebx：活過 call
    subq   $8, %rsp                    # 補 8 bytes 讓 call 時 rsp 對齊 16
    call   ext@PLT
    addq   $8, %rsp
    addl   %ebx, %eax                  # 回來之後 a、b 還在
    popq   %rbx                        # 還原 callee-saved
    addl   %ebp, %eax
    popq   %rbp
    ret
```

這段同時展示了 prologue 不一定是教科書的 `push rbp; mov rbp, rsp`。`-O2` 預設不用 frame pointer（`-fomit-frame-pointer`），`rbp` 被當成普通的 callee-saved 暫存器。

如果被呼叫者**違反**協定會怎樣？`bad_ext.s` 寫兩個函式，`ext_bad` 改了 `rbx` 卻沒還原，`ext_good` 先 push 後 pop。`clobber.c` 分別用它們呼叫（`ext(10)` 回傳 `11`，預期結果 `11 + 10 + 100 = 121`）：

```
$ gcc -O2 -Wall -Wextra clobber.c bad_ext.s -o clobber && ./clobber
keep_good(10, 100) = 121
keep_bad (10, 100) = 111
```

`keep_bad` 把 `a`（10）放在 `rbx`，被 `ext_bad` 歸零，結果少了 10。這類錯誤不會有任何編譯警告，因為 C 編譯器看不到組語檔；它也解釋了為什麼把 C 與手寫組語、JIT、不同語言 FFI 混用時，ABI 的細節必須嚴格遵守。

## 對比與取捨

| ABI | 參數 | 返回值 | callee-saved | 特色 |
|---|---|---|---|---|
| i386 cdecl（32 位元 Linux） | 全部 push 到 stack | `eax`（64 位元用 `edx:eax`） | `ebx, esi, edi, ebp` | 呼叫端清 stack；簡單，但每個參數都要碰記憶體 |
| x86-64 System V（Linux/macOS） | 6 個整數暫存器 + 8 個 xmm | `rax`/`rdx`/`xmm0`/`xmm1` | `rbx, rbp, r12–r15` | 128 bytes red zone；本課使用 |
| x64 Windows | `rcx, rdx, r8, r9`（4 個）+ xmm0–3 | `rax`/`xmm0` | 另有一組（含 `rdi, rsi`） | 呼叫端必須預留 32 bytes 的 shadow space；沒有 red zone |

Windows x64 與 System V 在 x86-64 上並存，所以同一份 C 原始碼在兩個平台上產生的呼叫序列不同；跨平台的 JIT 與 FFI 函式庫必須各寫一套。

**為什麼不用更多暫存器、為什麼不全放暫存器？** 暫存器數量是硬體給定的，且被呼叫者仍需要暫存器做自己的運算；參數太多時要 spill 到 stack。ABI 選 6 這個數字是統計權衡：它涵蓋絕大多數函式的參數個數，同時保留足夠的暫存器給函式本體。

## 踩雷集錦

1. **以為 `rbp` 一定是 frame pointer**：`-O2` 預設不使用 frame pointer，`rbp` 只是個 callee-saved 通用暫存器（見上面的 `keep`）。想用 frame pointer 回溯堆疊，要加 `-fno-omit-frame-pointer`；否則靠 `.eh_frame` 的 CFI 資料（gdb、perf 都支援，但 `perf` 預設的 `fp` 模式需要 frame pointer）。
2. **以為沒宣告函式只是警告、不影響結果**：`kr_main.c` 的例子顯示「沒警告、結果是 0」的靜默錯誤，連結器不檢查型別。永遠 `#include` 正確的標頭，並加 `-Wall -Werror=implicit-function-declaration`。
3. **以為 stack 上的資料「用完就消失」**：函式返回後，frame 的內容仍留在記憶體（只是 `rsp` 往上移）。回傳指向區域變數的指標會得到「看起來還有用」的垃圾，直到下一次呼叫蓋掉它。`-Wall` 的 `-Wreturn-local-addr` 會抓到最明顯的情況。
4. **手寫 `asm` 或 `longjmp` 之後 stack 沒對齊**：不是在你的程式碼崩潰，而是在遠處的 `printf` 的 `movaps` 崩潰，非常難除錯（`misalign.s` 示範）。
5. **struct 傳值有隱藏的成本**：超過 16 bytes 的 struct 作為參數或返回值，會經過記憶體複製和隱藏指標（`get32` 的 `rdi`）。大 struct 傳指標通常更好；但小 struct（≤16 bytes）傳值反而可以全程在暫存器裡。

## 進階：再往深一層

**ELF 的 section 與 segment**：編譯器和連結器處理 *section*（`.text`、`.data`、`.bss`、`.rodata`……），核心 loader 處理 *segment*（program header 的 `PT_LOAD` 項目）。一個 segment 可以包含多個權限相容的 section，上面 `maps` 前四段 `r--p`、`r-xp`、`r--p`、`rw-p` 對應 4 個 `PT_LOAD`（實測 `readelf -lW maps`：`R`、`R E`、`R`、`RW`）；`maps` 多出來的第二個 `r--p` 是 `RW` segment 前段被 `GNU_RELRO` 在啟動後改成唯讀的部分（見 Ch 14）。用 `readelf -lW ./maps` 能看到 program header 與 section 的對應表。細節見 [Ch 39 — 靜態連結](./39-static-linking.md)、[Ch 41 — 執行階段](./41-c-runtime.md)。

**CET 與 `endbr64`**：Ubuntu 22.04 的 gcc 11.4 預設 `-fcf-protection=full`，每個函式開頭都有 `endbr64`（指標間接分支的合法目標標記）。上面的 `gdb` 輸出之所以沒有它，是因為我們用 `-fcf-protection=none` 編譯。不加這個 flag 時，`funcB` 的反組譯是：

```
   0x0000000000401106 <+0>:	endbr64
   0x000000000040110a <+4>:	push   rbp
   ...
```

**原文說 GCC 14 起才會插入 `endbr64`；實測 gcc 11.4 在 Ubuntu 22.04 上就預設插入**（Ubuntu 的 gcc 預設啟用 `-fcf-protection`，上游 gcc 預設則不一定，這是發行版設定）。它對程式邏輯沒有影響，我們在 Ch 14 再談它的安全意義。

**stack 的上限**：stack 向低位址成長，核心依 `RLIMIT_STACK`（`ulimit -s`，預設 8192 KiB）限制其大小，超出時 page fault 無法再擴展，行程收到 SIGSEGV。這與遞迴深度的關係見 [Ch 13](./13-recursion.md)。

## 動手練習

1. 把 `stack.c` 的 `funcA` 改成接受 7 個參數並傳給一個接受 7 個 `long` 的 `funcB`，用 `gcc -S -O0` 找出第 7 個參數在 `funcB` 裡用 `16(%rbp)` 取得，並在 gdb 裡 `x/gx $rbp+16` 驗證。
2. 寫一個回傳 `struct { char c[24]; }` 的函式，從組語確認呼叫端把位址放進 `rdi`，且被呼叫端把同一個位址放 `rax`。再改成 `struct { char c[16]; }`，觀察改走暫存器。
3. 在 gdb 裡對 `stack` 下 `break *funcA`，`stepi` 逐步走到 `ret`，每一步用 `p $rsp`、`x/gx $rsp` 記錄 `rsp` 與頂端值，畫出 `rsp` 的變化曲線。
4. 用 `clang -O2 -S` 編譯 `saved.c` 的 `keep`，與 gcc 比較它使用哪些 callee-saved 暫存器。

## 本章重點整理

- 行程位址空間依權限分頁：`.text` `r-x`、`.rodata` `r--`、`.data/.bss` `rw-`、heap 與 stack 在兩端，`/proc/self/maps` 可直接觀察。
- ABI 是呼叫端與被呼叫端的協定：前 6 個整數參數走 `rdi, rsi, rdx, rcx, r8, r9`，`rax` 回傳；`call` 推入返回位址、`ret` 彈出；prologue/epilogue 對稱。
- `rbx, rbp, r12–r15` 由被呼叫者保存；`rsp` 下方 128 bytes 是 red zone；`call` 前 `rsp` 要 16 對齊，否則 `movaps` 會讓 libc 崩潰。
- 沒有 prototype 時，編譯器與連結器都不檢查型別，錯誤靜默發生。

## 自我檢核

- [ ] 不看筆記，能畫出 `funcB` 執行 `push rbp; mov rbp, rsp` 之後的 stack，標出 saved rbp、返回位址、呼叫者的區域變數嗎？
- [ ] 解釋 `ret` 與 `rax` 各自負責什麼，為什麼「返回值」與「返回位址」是兩件獨立的事？
- [ ] 面試官問「為什麼有些函式沒有 `sub rsp`」，你能同時說出 leaf function 與 red zone 兩個概念嗎？
- [ ] 說出 callee-saved 與 caller-saved 的差異，並判斷：若 `foo` 在 `bar()` 呼叫前後都要用到某個值，它應該把值放在哪類暫存器？
- [ ] 能解釋為什麼 `ASLR` 讓同一個程式每次 `local` 的位址不同，但 `gdb` 裡每次相同嗎？

## 延伸閱讀

### 官方文件 / Spec

- **[System V AMD64 psABI](https://gitlab.com/x86-psABI/x86-64-ABI)**（原文連結為舊版 PDF，這是 psABI 的官方儲存庫）
  - **讀哪裡**：§3.2.2「The Stack Frame」（red zone、對齊）、§3.2.3「Parameter Passing」（INTEGER/SSE/MEMORY 分類，決定 struct 怎麼傳）。
  - **前提**：讀得懂 x86-64 暫存器名稱；§3.2.3 的分類演算法稍繁瑣，可以先看表格再回頭。
- **[N1570 6.5.2.2「Function calls」](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：p2（引數個數限制）、p6（無 prototype 時的 default argument promotion）、p9（型別不相容的呼叫是 UB）。
  - **和本章的關聯**：`kr_main.c` 的靜默錯誤對應的就是 p9。

### 部落格 / 技術文章

- **[Stack frame layout on x86-64](https://eli.thegreenplace.net/2011/09/06/stack-frame-layout-on-x86-64)** — Eli Bendersky
  - **這篇說什麼**：用圖解釋 `rbp` 鏈、red zone、參數傳遞與 frame 內容，比原文的圖更細。
  - **為什麼值得讀**：作者以清楚的圖與實驗聞名；前提是讀過本章的 frame 圖。
- **[Anatomy of a Program in Memory](https://manybutfinite.com/post/anatomy-of-a-program-in-memory/)** — Gustavo Duarte
  - **這篇說什麼**：process 位址空間、虛擬記憶體與 `/proc/pid/maps` 的關係；偏 32 位元但概念通用。
  - **讀哪裡**：整篇；與本章「行程記憶體布局」節對照。

### 書籍

- **《Computer Systems: A Programmer's Perspective》（CSAPP，3rd ed.）** — Bryant & O'Hallaron（Pearson，2015）
  - **讀哪幾章**：第 3.7 節「Procedures」完整講 x86-64 的呼叫、stack frame、暫存器保存，是本章的教科書版；3.10 節講 buffer overflow，可與 [Ch 14](./14-function-call-security.md) 對照。

有了 stack frame 的圖像，就能讀懂一種「不遵守 call/return 對稱性」的特殊跳躍：`setjmp`/`longjmp`，以及建立在它之上的協同式多工。

→ [Ch 12 setjmp/longjmp 與協同式多工](./12-setjmp-and-coroutines.md)
