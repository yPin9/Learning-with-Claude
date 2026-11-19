# Ch 36 — GNU toolchain 與編譯流程

> **目標**：能說清楚 `gcc hello.c` 背後依序啟動了哪些程式（cc1、as、collect2、ld）、每一步的輸入輸出是什麼；會用 `-v`、`-save-temps`、`strace` 親眼確認；會用 binutils（objdump、readelf、nm、size、strip、addr2line）檢查目的檔與執行檔；看得懂 target triple，能把同一份 C 交叉編譯到 AArch64、RISC-V 並用 QEMU 執行；能對照 x86-64 與 AArch64 的機械碼，說出引數、全域變數、常數、區域變數各用什麼定址方式存取。
> **對應原文**：[你所不知道的 C 語言：編譯器和最佳化原理篇](https://hackmd.io/@sysprog/c-compiler-optimization) 的「理解編譯器最佳化有時是為了逃出困境」「From Source to Binary」「GNU Toolchain」「Procedures」「Accessing operands」「Target Triple」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4.0、GNU binutils 2.38、clang 14.0.0；交叉編譯器 `aarch64-linux-gnu-gcc` 11.4.0、`riscv64-linux-gnu-gcc`；`qemu-user` 6.2。本章所有輸出都在這個環境實際跑出來；不同的 gcc 版本與發行版預設旗標（例如 Ubuntu 預設開 PIE、stack protector、CET）會讓組合語言長得不一樣。

## 為什麼需要這個？

先看一個「程式碼沒錯，開了最佳化卻當掉」的例子。主執行緒等另一個執行緒把旗標 `ready` 設成 1：

```c
/* spin.c */
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

int ready = 0;              /* 刻意不用 volatile / atomic */

static void *producer(void *arg)
{
    (void) arg;
    sleep(1);
    ready = 1;              /* 另一個執行緒寫入 */
    return NULL;
}

int main(void)
{
    pthread_t t;
    pthread_create(&t, NULL, producer, NULL);
    while (!ready)          /* 等待旗標 */
        ;
    puts("got it");
    pthread_join(t, NULL);
    return 0;
}
```

```
$ gcc -O0 -pthread -o spin-O0 spin.c && timeout 3 ./spin-O0; echo "rc=$?"
got it
rc=0
$ gcc -O2 -pthread -o spin-O2 spin.c && timeout 3 ./spin-O2; echo "rc=$?"
rc=124
```

`-O2` 版本卡死，被 `timeout` 在 3 秒後砍掉（124 是 `timeout` 的逾時退出碼）。原始碼看不出任何問題，答案在機械碼裡：

```
$ objdump -d --no-show-raw-insn spin-O2 | sed -n '/<main>:/,/^$/p'
    ...
    1106:	call   10b0 <pthread_create@plt>
    110b:	mov    0x2f03(%rip),%eax        # 4014 <ready>
    1111:	test   %eax,%eax
    1113:	jne    1120 <main+0x40>
    1115:	jmp    1115 <main+0x35>
```

`ready` 只讀了**一次**；若當下是 0，就跳到 `1115: jmp 1115`——一條跳回自己的指令，永遠不再讀記憶體。gcc 有權這麼做：C11 的記憶體模型（N1570 5.1.2.4 §25）規定，兩個執行緒對同一物件的存取若至少一方是寫入、又沒有同步，就是 data race，屬於未定義行為。編譯器因此可以假設「迴圈裡沒有人改 `ready`」，把讀取提到迴圈外。把 `ready` 換成 `atomic_int` 之後，迴圈每一圈都重新讀：

```
$ gcc -O2 -pthread -o spin-fix spin-fix.c && timeout 3 ./spin-fix; echo "rc=$?"
got it
rc=0
    1110:	mov    0x2efe(%rip),%eax        # 4014 <ready>
    1116:	test   %eax,%eax
    1118:	je     1110 <main+0x30>
```

這就是原文標題「理解編譯器最佳化有時是為了逃出困境」的意思。LWN 的〈Who's afraid of a big bad optimizing compiler?〉整理了 Linux 核心開發者踩過的同類問題：load/store 被合併、被撕裂（tearing）、被重新讀取、被憑空產生。不懂工具鏈的人只會說「開 `-O2` 有 bug，關掉就好」；懂的人會打開 `objdump`，五分鐘內指出問題出在哪一行、違反了規格書哪一條。

本章先把工具鏈本身弄清楚：`gcc` 這個指令其實不是編譯器，它是**編譯器驅動程式（compiler driver）**，負責依序叫起真正做事的程式。

> 原子操作的細節見 [Ch 20 — non-blocking 鏈結串列：CAS 與 C11 atomics](./20-lock-free-linked-list.md)；未定義行為如何讓編譯器「合法地」刪改程式碼，見 [Ch 34 — 未定義行為](./34-undefined-behavior.md)。

## 先建立直覺：一條生產線，四個工站

把 `gcc` 想成工廠的調度員。它自己不做零件，只看你給的檔案副檔名與旗標，決定要叫哪幾個工站、以什麼順序把半成品往下傳：

```
 hello.c ──► [cc1 -E] ──► hello.i ──► [cc1] ──► hello.s ──► [as] ──► hello.o ─┐
            前置處理        展開後的C     編譯       組合語言    組譯     目的檔    │
                                                                              ▼
   Scrt1.o crti.o crtbeginS.o ─────────────────────────────────► [collect2 → ld] ──► hello
   libc.so libgcc.a crtendS.o crtn.o ──────────────────────────►    連結            執行檔
```

| 工站 | 程式 | 輸入 → 輸出 | 用 gcc 停在這一步 |
|---|---|---|---|
| 前置處理（preprocessing） | `cc1 -E`（或獨立的 `cpp`） | `.c` → `.i` | `gcc -E` |
| 編譯（compilation） | `cc1` | `.i` → `.s` | `gcc -S` |
| 組譯（assembly） | `as`（GNU assembler） | `.s` → `.o` | `gcc -c` |
| 連結（linking） | `collect2` → `ld` | `.o` + 程式庫 + crt 檔 → 執行檔 | （完整跑完） |

規格書對應的是 N1570 5.1.1.2 的八個**轉譯階段（translation phases）**：phase 1–4 是前置處理（三連符、行接續、切成 preprocessing token、展開巨集與 `#include`），phase 5–7 是字元集轉換、字串串接與真正的語法語意分析，phase 8 是把各個轉譯單元與程式庫結合——規格書只說「要結合」，**不關心**由幾個程式、用什麼檔案格式完成。cc1/as/ld 的分工是 GNU 工具鏈的實作選擇，不是 C 語言的要求；後面 Ch 38 會看到 shecc 把全部工站塞進同一個執行檔，連 `as` 和 `ld` 都不需要。

> 這條流程的鳥瞰在 [Ch 1 — 一支 C 程式的一生](./01-life-of-a-c-program.md) 已經走過一次；本章把每一站拆開來看。

## From Source to Binary：親眼看每一站

### `gcc -v`：調度員的工作日誌

```c
/* hello.c */
#include <stdio.h>
#define GREETING "hello, toolchain"
int main(void)
{
    printf("%s\n", GREETING);
    return 0;
}
```

`-v` 讓 gcc 印出它實際執行的每一條命令。以下是刪掉搜尋路徑等雜訊後的關鍵行（每行很長，以 `...` 截斷）：

```
$ gcc -v -o hello hello.c
Target: x86_64-linux-gnu
Configured with: ../src/configure -v ... --enable-default-pie ... --enable-cet ...
gcc version 11.4.0 (Ubuntu 11.4.0-1ubuntu1~22.04.3)
 /usr/lib/gcc/x86_64-linux-gnu/11/cc1 -quiet -v -imultiarch x86_64-linux-gnu hello.c ... -fasynchronous-unwind-tables -fstack-protector-strong -Wformat -Wformat-security -fstack-clash-protection -fcf-protection -o /tmp/ccqgfGdq.s
#include <...> search starts here:
 /usr/lib/gcc/x86_64-linux-gnu/11/include
 /usr/local/include
 /usr/include/x86_64-linux-gnu
 /usr/include
 as -v --64 -o /tmp/ccGmwaMY.o /tmp/ccqgfGdq.s
 /usr/lib/gcc/x86_64-linux-gnu/11/collect2 -plugin .../liblto_plugin.so ... --build-id --eh-frame-hdr -m elf_x86_64 --hash-style=gnu --as-needed -dynamic-linker /lib64/ld-linux-x86-64.so.2 -pie -z now -z relro -o hello .../Scrt1.o .../crti.o .../crtbeginS.o -L... /tmp/ccGmwaMY.o -lgcc --push-state --as-needed -lgcc_s --pop-state -lc -lgcc ... .../crtendS.o .../crtn.o
```

逐行讀出四個事實：

1. **沒有獨立的 cpp 步驟**。現代 gcc 把前置處理器整合進 `cc1`，同一個行程讀 `hello.c`、展開巨集、直接產生組合語言。`#include <...>` 的搜尋順序也印在這裡——標頭找錯版本時，先看這一段。
2. **發行版偷偷加了旗標**。你沒下的 `-fstack-protector-strong`（[Ch 14](./14-function-call-security.md) 的 canary）、`-fcf-protection`（產生 `endbr64`）、`-fstack-clash-protection` 都是 Ubuntu 的 gcc 預設值，`Configured with` 裡的 `--enable-default-pie`、`--enable-cet` 是源頭。同一份程式在 Arch 或自己編的 gcc 上組合語言不同，原因常在這裡。
3. **組譯器就是 `as`**，輸入是暫存目錄的 `.s`，輸出 `.o`。
4. **連結的命令列遠比你想的長**：除了你的 `.o`，還有 `Scrt1.o`（含 `_start`）、`crti.o`/`crtn.o`（`.init` 的開頭與結尾）、`crtbeginS.o`/`crtendS.o`（gcc 自己的建構式/解構式支援）、`-lc`、`-lgcc`、`-lgcc_s`，以及 `-pie -z now -z relro` 等安全強化旗標。這些 crt 檔做什麼，[Ch 41 — 執行階段：從 execve 經 _start 到 main](./41-c-runtime.md) 會逐一追；原文用 `int main() { return 1; }` 問「誰把 1 交給作業系統」，答案就在 `Scrt1.o` 與 libc 的 `__libc_start_main`。

### `strace`：確認真的有這幾個行程

`-v` 是 gcc 自己說的；`strace -f -e trace=execve` 是作業系統看到的：

```
$ strace -f -e trace=execve -o tr.txt gcc -O1 -o hello hello.c
$ grep -o 'execve("[^"]*"' tr.txt
execve("/usr/bin/gcc"
execve("/usr/lib/gcc/x86_64-linux-gnu/11/cc1"
execve("/home/ypp/.cargo/bin/as"
execve("/home/ypp/.local/bin/as"
execve("/usr/local/sbin/as"
execve("/usr/local/bin/as"
execve("/usr/sbin/as"
execve("/usr/bin/as"
execve("/usr/lib/gcc/x86_64-linux-gnu/11/collect2"
execve("/usr/bin/ld"
```

（路徑中的家目錄因人而異。）兩個值得注意的細節：

- `as` 被試了 6 次。gcc 照 `PATH` 順序逐一 `execve`，前 5 個不存在（失敗的 `execve` 也會被 strace 記下），第 6 個 `/usr/bin/as` 才成功。這代表**你在 `PATH` 前面放一個叫 `as` 的東西，gcc 就會用它**——交叉編譯環境混亂時，「用錯組譯器」就是這樣發生的。`gcc -print-prog-name=as` 可以問 gcc 它打算用哪個。
- `collect2` 再 `execve` 了 `ld`。依 GCC Internals 手冊〈Collect2〉一節，collect2 的本職是在缺乏原生機制的系統上收集「啟動時要呼叫的初始化函式」；在 GNU/Linux 這類有 `.init_array` 的 ELF 平台，我們觀察到的行為是它幾乎直接把參數轉交給 `ld`（並處理 LTO plugin）。所以「gcc 也能連結」的真相是：gcc 呼叫 collect2，collect2 呼叫 ld，真正的連結器始終是 `ld`。

### `-save-temps`：留下每一站的半成品

```
$ gcc -save-temps -O1 -o hello hello.c
$ ls -l hello*
-rwxr-xr-x 1 ypp ypp 15960 Nov 20 00:29 hello
-rw-r--r-- 1 ypp ypp   118 Nov 20 00:29 hello.c
-rw-r--r-- 1 ypp ypp 32050 Nov 20 00:29 hello.i
-rw-r--r-- 1 ypp ypp  1504 Nov 20 00:29 hello.o
-rw-r--r-- 1 ypp ypp   630 Nov 20 00:29 hello.s
$ wc -l hello.i
1080 hello.i
```

118 位元組的原始碼，前置處理後變成 1080 行、32 KB——全是 `<stdio.h>` 一路 include 進來的宣告。看 `hello.i` 的結尾：

```
# 3 "hello.c"
int main(void)
{
    printf("%s\n", "hello, toolchain");
    return 0;
}
```

`GREETING` 已經被替換成字串，`# 3 "hello.c"` 是**行號標記（linemarker）**，讓 cc1 報錯時還能指回原始檔的第 3 行。加上 `-save-temps` 後 `-v` 會顯示 gcc 改成兩次呼叫 cc1：先 `cc1 -E ... hello.c` 產生 `.i`，再 `cc1 -fpreprocessed hello.i` 編譯——整合式前置處理器在需要留檔時會被拆開。

`hello.s` 的主體：

```
	.section	.rodata.str1.1,"aMS",@progbits,1
.LC0:
	.string	"hello, toolchain"
	.text
	.globl	main
	.type	main, @function
main:
	endbr64
	subq	$8, %rsp
	leaq	.LC0(%rip), %rdi
	call	puts@PLT
	movl	$0, %eax
	addq	$8, %rsp
	ret
	...
	.section	.note.GNU-stack,"",@progbits
```

三件事：

- **`printf("%s\n", s)` 被換成了 `puts(s)`**。gcc 認得 `printf` 是標準函式（builtin），格式字串只有 `%s\n` 時，語意等同 `puts`，而 `puts` 不必在執行期解析格式字串。加 `-fno-builtin` 可以關掉這種替換（Ch 37 會實測）。
- `.globl`、`.type`、`.section` 這些以 `.` 開頭的是**組譯器指示詞（directive）**，不產生指令，只告訴 `as` 符號可見性、型別、放在哪個 section。原文用 ARM 的 GAS 範例（`.type main, %function`）說明同一件事；x86 上寫 `@function`，因為 `%` 在 AT&T 語法裡是暫存器前綴。
- `.note.GNU-stack` 告訴連結器「這個目的檔不需要可執行的 stack」。手寫組語漏掉它，ld 會把整個執行檔標成 executable stack——Ch 38 的 Brainfuck 編譯器輸出組語時會回來談。

### 手動走一遍：把 gcc 拿掉

拆開來自己跑，最能看出每一站缺了什麼會怎樣：

```
$ cpp hello.c > h.i
$ /usr/lib/gcc/x86_64-linux-gnu/11/cc1 -quiet -O1 h.i -o h.s
$ as h.s -o h.o
$ ld h.o -o h
ld: warning: cannot find entry symbol _start; defaulting to 0000000000401000
ld: h.o: in function `main':
h.i:(.text+0xc): undefined reference to `puts'
$ echo $?
1
```

`ld` 抱怨兩件事：找不到進入點 `_start`（它在 crt1.o 裡，不在你的程式裡），以及 `puts` 未定義（它在 libc 裡）。補上這兩樣：

```
$ ld -o h2 -dynamic-linker /lib64/ld-linux-x86-64.so.2 \
     /usr/lib/x86_64-linux-gnu/crt1.o /usr/lib/x86_64-linux-gnu/crti.o \
     h.o -lc /usr/lib/x86_64-linux-gnu/crtn.o
$ ./h2
hello, toolchain
```

這次用 `crt1.o`（非 PIE 版）而不是 gcc 用的 `Scrt1.o`，也沒加 `-pie`，所以 `h2` 是傳統的固定位址執行檔。`-dynamic-linker` 寫進 ELF 的 `PT_INTERP`，告訴核心「載入這個程式前先載入 `/lib64/ld-linux-x86-64.so.2`」；漏掉它，程式會因找不到解譯器而無法啟動。從這裡可以體會為什麼大家都讓 gcc 去叫 ld：這串參數因發行版、架構、PIE 與否而不同，只有 gcc 的 spec 檔知道正確組合（`gcc -dumpspecs` 可以看到）。

## GNU binutils：檢查目的檔的工具箱

GNU toolchain 的三大件是 gcc（編譯器集合）、binutils（`as`、`ld`、`objdump`、`readelf`、`nm`、`size`、`strip`、`addr2line`、`ar`……）與 gdb。binutils 的檢查工具底下多半共用 **BFD**（Binary File Descriptor）程式庫，能讀 ELF、COFF、PE 等格式；`readelf` 是例外，它刻意不依賴 BFD，直接解析 ELF，因此在 BFD 不認得的檔案上也能用。

拿一個各種符號都有的檔案來實驗：

```c
/* syms.c */
#include <stdio.h>
int g_init = 42;            /* .data */
int g_zero;                 /* .bss */
static int s_hidden = 7;    /* .data，local 符號 */
const int k_ro = 99;        /* .rodata */
static void helper(void) { puts("helper"); }
void api(void) { helper(); printf("%d %d\n", g_init + g_zero, s_hidden + k_ro); }
```

### `nm`：符號表

```
$ gcc -O0 -c syms.c && nm syms.o
000000000000001a T api
0000000000000000 D g_init
0000000000000000 B g_zero
0000000000000000 t helper
0000000000000000 R k_ro
                 U printf
                 U puts
0000000000000004 d s_hidden
```

第二欄的字母：`T`/`t` 在 `.text`、`D`/`d` 在 `.data`、`B` 在 `.bss`、`R` 在 `.rodata`、`U` 是未定義（要靠連結器去別處找）。**大寫是全域（external linkage），小寫是區域**——`static` 的 `helper` 與 `s_hidden` 都是小寫，別的 `.o` 看不到它們。這就是 Ch 39 符號解析的原料。位址欄在 `.o` 裡都是相對各 section 開頭的偏移，連結後才變成真正的位址。

### `objdump -h` / `readelf -S`：section

```
$ objdump -h syms.o
Idx Name          Size      VMA               LMA               File off  Algn
  0 .text         0000005b  0000000000000000  0000000000000000  00000040  2**0
                  CONTENTS, ALLOC, LOAD, RELOC, READONLY, CODE
  1 .data         00000008  0000000000000000  0000000000000000  0000009c  2**2
                  CONTENTS, ALLOC, LOAD, DATA
  2 .bss          00000004  0000000000000000  0000000000000000  000000a4  2**2
                  ALLOC
  3 .rodata       00000012  0000000000000000  0000000000000000  000000a4  2**2
                  CONTENTS, ALLOC, LOAD, READONLY, DATA
  ...
```

注意 `.bss` 沒有 `CONTENTS`：它只記「執行時要 4 個位元組、全部清零」，檔案裡不佔空間，所以 `.bss` 的檔案偏移（`a4`）和下一個 section `.rodata` 相同。`.data` 是 8 位元組，因為 `g_init` 和 `s_hidden` 各 4 位元組；`.text` 帶 `RELOC` 旗標，代表它有待連結器修補的重定位項目（`.rela.text`）。

### `size`：三大區塊的總量

```
$ size syms.o hello
   text	   data	    bss	    dec	    hex	filename
    229	      8	      4	    241	     f1	syms.o
   1371	    600	      8	   1979	    7bb	hello
```

`size` 的 `text` 欄把**所有唯讀且配置記憶體的 section**加總（`.text`、`.rodata`、`.eh_frame` 等），不是只有 `.text`：syms.o 的 `.text` 是 0x5b = 91 位元組，`text` 欄卻是 229。嵌入式開發常用 `size` 看韌體佔多少 Flash（text + data）與 RAM（data + bss）。

### `readelf -h`：ELF 檔頭

```
$ readelf -h hello
ELF Header:
  Magic:   7f 45 4c 46 02 01 01 00 00 00 00 00 00 00 00 00
  Class:                             ELF64
  Data:                              2's complement, little endian
  ...
  Type:                              DYN (Position-Independent Executable file)
  Machine:                           Advanced Micro Devices X86-64
  Entry point address:               0x1060
  ...
  Number of program headers:         13
  Number of section headers:         38
```

`7f 45 4c 46` 就是 `\x7f` `E` `L` `F`；後面的 `02 01 01` 是 64 位元、little endian、ELF 版本 1。PIE 執行檔的型別是 `DYN`（和共享程式庫一樣），進入點 `0x1060` 是相對載入位址的偏移，核心載入時才加上隨機基底。原文提到核心要「驗證執行檔」：核心的 ELF 載入器（`fs/binfmt_elf.c`）就是先檢查這個 magic、class、machine，對不上就交給下一個 binfmt 處理器或回傳 `ENOEXEC`。

### `strip` 與 `addr2line`：除錯資訊的去留

```
$ gcc -O1 -g -o hello hello.c
$ cp hello hello.stripped && strip hello.stripped
$ ls -l hello hello.stripped
17616 hello
14472 hello.stripped
$ readelf -S hello | grep -o '\.debug_[a-z_]*' | tr '\n' ' '
.debug_aranges .debug_info .debug_abbrev .debug_line .debug_str .debug_line_str .debug_loclists
$ nm hello.stripped
nm: hello.stripped: no symbols
$ nm -D hello.stripped
                 w _ITM_deregisterTMCloneTable
                 w _ITM_registerTMCloneTable
                 w __cxa_finalize@GLIBC_2.2.5
                 w __gmon_start__
                 U __libc_start_main@GLIBC_2.34
                 U puts@GLIBC_2.2.5
$ ./hello.stripped
hello, toolchain
```

`strip` 刪掉 `.symtab`、`.strtab` 與全部 `.debug_*`，section 數從 37 個降到 28 個，程式照跑——因為**載入與執行用到的只有 program header 和動態符號表（`.dynsym`）**，`nm -D` 還看得到 `puts`，動態連結器要靠它找 libc。section 與 segment 的差別是 Ch 39 的主題。

沒 strip 的版本可以從位址反查原始碼：

```
$ objdump -d hello | grep -A8 '<main>:'
0000000000001149 <main>:
    1149:	f3 0f 1e fa          	endbr64
    114d:	48 83 ec 08          	sub    $0x8,%rsp
    1151:	48 8d 3d ac 0e 00 00 	lea    0xeac(%rip),%rdi        # 2004 <_IO_stdin_used+0x4>
    1158:	e8 f3 fe ff ff       	call   1050 <puts@plt>
    115d:	b8 00 00 00 00       	mov    $0x0,%eax
    1162:	48 83 c4 08          	add    $0x8,%rsp
    1166:	c3                   	ret
$ addr2line -e hello 0x1149 -f
main
/home/ypp/c_sysprog_lab/p6a/ch36/hello.c:4
```

當線上程式只留下 crash 位址時，`addr2line` 配合保留了除錯資訊的同版本執行檔就能找到出事的那一行。實務做法是發佈 strip 過的執行檔，另外保存未 strip 版（或用 `objcopy --only-keep-debug` 分離出的除錯檔）。

## 機械碼對照：位元組怎麼對應到指令

上面 `main` 的原始位元組值得逐一解讀，因為它示範了 x86-64 兩種最重要的 PC 相對定址：

- `e8 f3 fe ff ff` 是 `call rel32`。`e8` 是 opcode，後面 4 位元組是 little endian 的有號位移 `0xfffffef3` = −0x10d。目標 = **下一條指令的位址** + 位移 = 0x115d − 0x10d = 0x1050，正是 `puts@plt`。相對位移讓這段機械碼不管被載入到哪個位址都不必修改，這是 PIE 能運作的基礎。
- `48 8d 3d ac 0e 00 00` 是 `lea 0xeac(%rip),%rdi`。`48` 是 REX.W 前綴（64 位元運算元）、`8d` 是 `lea`、`3d` 是 ModRM（選中 `rdi` 與 RIP 相對模式），後面是 32 位元位移 0xeac。有效位址 = 0x1158 + 0xeac = 0x2004，字串常數 `"hello, toolchain"` 在 `.rodata` 的位置。

`rel32` 的範圍是 ±2 GiB，這個限制在 Ch 38 寫 JIT 時會直接撞上：`mmap` 拿到的記憶體與 libc 的距離可能超過 2 GiB，無法用 `call rel32` 呼叫 `putchar`。

## Procedures：引數怎麼傳？

原文以 ARM 的 AAPCS 說明呼叫慣例：前 4 個 word 大小的引數放 R0–R3，其餘從 stack 傳，所以「參數少於 4 個的函式比較有效率」。這條規則隨架構而變，我們直接在 x86-64 和 AArch64 上量。先釐清用語：**引數（argument）**是呼叫端傳進去的運算式，**參數（parameter）**是被呼叫端（callee）接收的變數；`argc` 是 argument count，因為它從呼叫者（核心與 crt）的角度命名。

```c
/* args.c */
int add4(int a, int b, int c, int d) { return a + b + c + d; }
int add6(int a, int b, int c, int d, int e, int f) { return a + b + c + d + e + f; }
int add8(int a, int b, int c, int d, int e, int f, int g, int h)
{ return a + b + c + d + e + f + g + h; }
int add9(int a, int b, int c, int d, int e, int f, int g, int h, int i)
{ return a + b + c + d + e + f + g + h + i; }
```

x86-64（System V ABI，`gcc -O2 -c args.c`，省略 `endbr64` 與對齊用的 `nop`）：

```
<add6>:
  add    %esi,%edi
  add    %edx,%edi
  add    %ecx,%edi
  add    %r8d,%edi
  lea    (%rdi,%r9,1),%eax
  ret
<add8>:
  add    %esi,%edi
  add    %edx,%edi
  add    %ecx,%edi
  add    %r8d,%edi
  lea    (%rdi,%r9,1),%eax
  add    0x8(%rsp),%eax          ← 第 7 個引數：從 stack 讀
  add    0x10(%rsp),%eax         ← 第 8 個引數
  ret
```

AArch64（AAPCS64，`aarch64-linux-gnu-gcc -O2 -c args.c`）：

```
<add8>:
  add	w0, w0, w1
  add	w0, w0, w2
  add	w0, w0, w3
  add	w0, w0, w4
  add	w0, w0, w5
  add	w0, w0, w6
  add	w0, w0, w7
  ret
<add9>:
  add	w0, w0, w1
  ldr	w1, [sp]                 ← 第 9 個引數才從 stack 讀
  add	w0, w0, w2
  ...
  add	w0, w0, w1
  ret
```

| 架構 / ABI | 整數引數暫存器 | 第一個走 stack 的引數 | 回傳值 |
|---|---|---|---|
| 32 位元 ARM（AAPCS） | r0–r3 | 第 5 個 | r0（64 位元用 r0–r1） |
| x86-64 System V | rdi, rsi, rdx, rcx, r8, r9 | 第 7 個 | rax |
| AArch64 AAPCS64 | x0–x7 | 第 9 個 | x0 |
| x86-64 Windows | rcx, rdx, r8, r9 | 第 5 個（且呼叫端要預留 32 位元組 shadow space） | rax |

32 位元 ARM 這一列沒有在本環境實測（Ubuntu 上沒裝 `arm-linux-gnueabihf-gcc`），依據是 Arm AAPCS 規格書的引數傳遞規則與原文的實驗；Windows 一列取自 Microsoft 的 x64 calling convention 文件，本課只在 Linux 上操作。「4 個以內比較快」的原文結論在 x86-64 要改成 6 個、在 AArch64 要改成 8 個。暫存器傳不下時，引數要經過記憶體（通常仍在 L1 cache 裡），真正的成本通常是幾個額外的 load/store，而不是原文早年 ARM 情境下的「存取外部記憶體」那麼嚴重——這點會因核心與快取狀態而異，本章沒有量測。

呼叫端看得更清楚。把 `add9` 放在另一個檔案，讓 gcc 無法 inline：

```c
/* caller.c */
int add9(int, int, int, int, int, int, int, int, int);
int call9(void) { return add9(1, 2, 3, 4, 5, 6, 7, 8, 9); }
```

```
x86-64:                                   AArch64:
  sub    $0x10,%rsp                         sub	sp, sp, #0x20
  mov    $0x6,%r9d                          mov	w0, #0x9
  mov    $0x5,%r8d                          mov	w7, #0x8
  mov    $0x4,%ecx                          mov	w6, #0x7
  push   $0x9          ← 由右往左推        ...
  mov    $0x3,%edx                          stp	x29, x30, [sp, #16]
  mov    $0x2,%esi                          str	w0, [sp]   ← 只有第 9 個進 stack
  mov    $0x1,%edi                          mov	w1, #0x2
  push   $0x8                               mov	w0, #0x1
  push   $0x7                               bl	0 <add9>
  call   33 <call9+0x33>                       34: R_AARCH64_CALL26	add9
     2f: R_X86_64_PLT32	add9-0x4            ldp	x29, x30, [sp, #16]
  add    $0x28,%rsp                         add	sp, sp, #0x20
  ret                                       ret
```

x86-64 把 9、8、7 依序 `push`，結果 7 在最低位址（`0x8(%rsp)`，`call` 推入的回傳位址在 `0x0(%rsp)`），這就是原文說的「依反過來的順序丟入堆疊」。前面那條 `sub $0x10,%rsp` 是為了對齊：ABI 要求 `call` 之前 `rsp` 是 16 的倍數，三個 push 共 24 位元組，加上 16 才湊成 40，再加上進入函式時已偏移的 8 位元組就對齊了。AArch64 沒有 `call` 指令，`bl`（branch with link）把回傳位址放進 x30（link register），所以非葉函式要自己 `stp x29, x30` 把它存起來。`R_X86_64_PLT32`、`R_AARCH64_CALL26` 是留給連結器的重定位項目——這兩行就是 Ch 39 的入口。

> stack frame 的完整布局、callee-saved 暫存器與紅區（red zone），在 [Ch 11 — 行程記憶體布局、x86-64 SysV ABI 與 stack frame](./11-process-layout-and-abi.md)。

## Accessing operands：五種運算元的定址方式

原文列出 ARM 上存取運算元的五種情況：引數在暫存器、引數在 stack、常數、區域變數、全域變數。我們把它們寫進同一個檔案，對照兩種架構：

```c
/* operands.c */
int counter = 5;                 /* 全域變數：.data */
extern int table[];              /* 外部陣列 */
long scale(long x) { return x * 1000003; }   /* 大常數 */
int use_global(int x) { return counter + x; }
int use_array(int i) { return table[i]; }
const char *msg(void) { return "operand"; }  /* 字串常數：.rodata */
```

```
x86-64 (gcc -O2)                               AArch64 (aarch64-linux-gnu-gcc -O2)
<scale>:                                       <scale>:
  imul $0xf4243,%rdi,%rax                        mov  x1, #0x4243
                                                 movk x1, #0xf, lsl #16
                                                 mul  x0, x0, x1
<use_global>:                                  <use_global>:
  mov  0x0(%rip),%eax                            adrp x1, 0 <scale>
     16: R_X86_64_PC32 counter-0x4                  10: R_AARCH64_ADR_PREL_PG_HI21 .data
  add  %edi,%eax                                 ldr  w1, [x1]
                                                    14: R_AARCH64_LDST32_ABS_LO12_NC .data
                                                 add  w0, w0, w1
<use_array>:                                   <use_array>:
  movslq %edi,%rdi                               adrp x1, 0 <table>
  lea  0x0(%rip),%rax                               20: R_AARCH64_ADR_GOT_PAGE table
     2a: R_X86_64_PC32 table-0x4                 ldr  x1, [x1]
  mov  (%rax,%rdi,4),%eax                           24: R_AARCH64_LD64_GOT_LO12_NC table
                                                 ldr  w0, [x1, w0, sxtw #2]
<msg>:                                         <msg>:
  lea  0x0(%rip),%rax                            adrp x0, 0 <scale>
     47: R_X86_64_PC32 .LC0-0x4                     30: R_AARCH64_ADR_PREL_PG_HI21 .rodata.str1.8
                                                 add  x0, x0, #0x0
                                                    34: R_AARCH64_ADD_ABS_LO12_NC .rodata.str1.8
```

逐項解讀：

| 運算元 | x86-64 | AArch64 | 原因 |
|---|---|---|---|
| 小常數 | 直接編進指令（imm32） | `mov` 的 16 位元立即值 | 指令裡放得下 |
| 大常數 1000003 = 0xf4243 | 仍是 imm32 | `mov` + `movk` 拼兩段 16 位元 | AArch64 指令固定 32 位元，放不下 20 位元的常數 |
| 全域變數 | `disp(%rip)` 一條指令 | `adrp`（算出 4 KiB 頁的位址）+ `ldr`（頁內偏移） | x86-64 有 32 位元位移；AArch64 拆成高 21 位與低 12 位 |
| 外部陣列 `table[i]` | RIP 相對取基底 + `(%rax,%rdi,4)` 比例定址 | 經 **GOT** 取位址，再 `[x1, w0, sxtw #2]` | 見下文 |
| 字串常數 | `lea disp(%rip)` | `adrp` + `add` | 與全域變數同理，只是取位址不取值 |

兩個架構的設計哲學在這張表裡一覽無遺：x86-64 是可變長度指令（1–15 位元組），能把 32 位元位移與立即值塞進一條指令；AArch64 每條指令固定 4 位元組，大數字要分段組。原文提到 ARM 32 位元以「PC 相對定址」存取常數（把常數放在程式碼旁的 literal pool），AArch64 則多用 `adrp` 配對指令，思路相同：**位置無關**——不寫死絕對位址，而是相對目前的 PC。

`use_array` 有一個值得留意的差異：同樣是 `extern int table[]`，x86-64 的 gcc 用 `R_X86_64_PC32` 直接相對定址，AArch64 的 gcc 卻經過 GOT（Global Offset Table）間接取位址。就我們的觀察與 gcc 的行為，x86-64 上 gcc 在 PIE 模式預設假設外部資料最後會落在執行檔自身（必要時由連結器以 copy relocation 補救），AArch64 的 gcc 11 則保守地走 GOT。這屬於編譯器與 ABI 的實作選擇，[Ch 40 — 動態連結](./40-dynamic-linking.md) 會解釋 GOT 與 copy relocation 的取捨。

最後是區域變數。加上 `volatile` 逼 gcc 把陣列真的放在 stack：

```c
int use_local(int n)
{
    volatile int buf[4];
    buf[n & 3] = n;
    return buf[0];
}
```

```
$ gcc -O2 -c operands.c && objdump -d --no-show-raw-insn operands.o | sed -n '/<use_local>/,$p'
  sub    $0x28,%rsp
  mov    %fs:0x28,%rax           ← stack canary（Ubuntu 預設 -fstack-protector-strong）
  mov    %rax,0x18(%rsp)
  mov    %edi,%eax
  and    $0x3,%eax
  mov    %edi,(%rsp,%rax,4)      ← buf[n & 3]：stack pointer 相對 + 比例定址
  mov    (%rsp),%eax             ← buf[0]
  mov    0x18(%rsp),%rdx
  sub    %fs:0x28,%rdx
  jne    86 <use_local+0x36>
  add    $0x28,%rsp
  ret
  call   __stack_chk_fail
```

區域變數一律以 `rsp`（或 `rbp`）為基底加偏移存取。夾在中間的 `%fs:0x28` 讀寫就是 [Ch 14](./14-function-call-security.md) 講的 canary——你沒要求，但發行版的 gcc 幫你加了，這又是一個「`gcc -v` 先看一眼」的理由。

## Target triple 與交叉編譯

### 讀懂那串前綴

交叉編譯器的名字長得像 `aarch64-linux-gnu-gcc`、`arm-none-eabi-gcc`。前綴叫 **target triple**（雖然常常不只三段），GNU 的慣例大致是：

```
<arch>[<sub>][-<vendor>]-<os>[-<abi/libc>]
  │             │           │        │
  │             │           │        └─ gnu = glibc；gnueabihf = glibc + ARM EABI + 硬體浮點
  │             │           └─ linux；none 或省略 = 沒有作業系統（bare metal）
  │             └─ pc、unknown、apple、buildroot……常被省略
  └─ x86_64、aarch64、arm、armeb（big endian）、riscv64……
```

在本機問各個編譯器自己的 triple：

```
$ gcc -dumpmachine
x86_64-linux-gnu
$ aarch64-linux-gnu-gcc -dumpmachine
aarch64-linux-gnu
$ riscv64-linux-gnu-gcc -dumpmachine
riscv64-linux-gnu
$ clang -print-target-triple
x86_64-pc-linux-gnu
$ clang --target=aarch64-linux-gnu -print-target-triple
aarch64-unknown-linux-gnu
```

同一台機器，Debian 系 gcc 說 `x86_64-linux-gnu`（省略 vendor），clang 說 `x86_64-pc-linux-gnu`（LLVM 的 triple 固定是 arch-vendor-os-environment 四欄，缺的補 `unknown`）。原文整理的幾個例子：

| Triple | 拆解 | 用途 |
|---|---|---|
| `arm-none-eabi` | ARM / 無 vendor / 無 OS / EABI | MCU 韌體（bare metal），沒有 Linux 系統呼叫 |
| `arm-linux-gnueabihf` | ARM / Linux / glibc + EABI + hard float | 樹莓派 32 位元系統 |
| `armeb-linux-gnueabihf` | 同上但 big endian | 少數網通設備 |
| `arm-linux-androideabi` | ARM / Linux / Android 的 ABI | Android 的 libc 是 Bionic，ABI 細節和 glibc 不同 |
| `arm-buildroot-linux-uclibcgnueabi` | vendor = buildroot，libc = uClibc | 嵌入式 Linux |

`hf`（hard float）與 `softfp`、`soft` 的差別在浮點引數走浮點暫存器還是整數暫存器；兩種 ABI 編出的目的檔**不能混著連結**，這是早期 ARM Linux 發行版分成 armel 與 armhf 兩套的原因。

**clang 和 gcc 在交叉編譯上的設計差異**：gcc 的一個執行檔只服務一個目標，換目標就換一整套 `xxx-gcc`；clang 一個執行檔內建所有後端，用 `--target=` 切換，但仍需要目標架構的標頭與程式庫（sysroot）才能連結。

### 實際交叉編譯並執行

```
$ aarch64-linux-gnu-gcc --version | head -1
aarch64-linux-gnu-gcc (Ubuntu 11.4.0-1ubuntu1~22.04.3) 11.4.0
$ aarch64-linux-gnu-gcc -O1 -o hello-a64 hello.c && file hello-a64
hello-a64: ELF 64-bit LSB pie executable, ARM aarch64, version 1 (SYSV), dynamically linked, interpreter /lib/ld-linux-aarch64.so.1, ... for GNU/Linux 3.7.0, not stripped
$ ./hello-a64; echo "rc=$?"
qemu-aarch64: Could not open '/lib/ld-linux-aarch64.so.1': No such file or directory
rc=255
```

先失敗一次很有教育意義。x86-64 CPU 當然不能執行 AArch64 指令；之所以出現 `qemu-aarch64` 的訊息，是因為安裝 `qemu-user-binfmt` 時，它向核心的 **binfmt_misc** 註冊了「看到 AArch64 ELF 就交給 qemu-aarch64」。QEMU 開始模擬後，照著 `PT_INTERP` 去找 AArch64 版的動態連結器，而 x86-64 主機的 `/lib` 裡沒有。用 `-L` 指定 AArch64 的 sysroot 就能跑：

```
$ qemu-aarch64 -L /usr/aarch64-linux-gnu ./hello-a64; echo "rc=$?"
hello, toolchain
rc=0
```

或者靜態連結，讓執行檔不需要動態連結器：

```
$ aarch64-linux-gnu-gcc -O1 -static -o hello-a64s hello.c && file hello-a64s
hello-a64s: ELF 64-bit LSB executable, ARM aarch64, version 1 (GNU/Linux), statically linked, ...
$ qemu-aarch64 ./hello-a64s
hello, toolchain
```

clang 不用另裝編譯器，只要系統上有 AArch64 的 sysroot（這裡借用 `gcc-aarch64-linux-gnu` 套件裝好的那份）：

```
$ clang --target=aarch64-linux-gnu -O1 -c hello.c -o hc-a64.o && file hc-a64.o
hc-a64.o: ELF 64-bit LSB relocatable, ARM aarch64, version 1 (SYSV), not stripped
```

三種架構的 `main` 放在一起看（`-O1`）：

```
AArch64:                          RISC-V 64（riscv64-linux-gnu-gcc -static）:
stp  x29, x30, [sp, #-16]!        addi  sp,sp,-16
mov  x29, sp                      sd    ra,8(sp)
adrp x0, ...                      auipc a0,0x41
add  x0, x0, #0x790               addi  a0,a0,1610
bl   630 <puts@plt>               jal   ra,156d8 <_IO_puts>
mov  w0, #0x0                     li    a0,0
ldp  x29, x30, [sp], #16          ld    ra,8(sp)
ret                               addi  sp,sp,16
                                  ...
```

AArch64 的 `adrp/add` 與 RISC-V 的 `auipc/addi` 是同一招：高位元相對 PC 算頁位址、低位元補偏移；兩者都用 link register（x30 / ra）存回傳位址，非葉函式都要先把它存進 stack。RISC-V 靜態版直接呼叫 `_IO_puts`（glibc 內部 `puts` 的別名），因為靜態連結時不經 PLT。

## 對比與取捨

| 選擇 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 讓 gcc 驅動整條流程 | 自動帶入正確的 crt、程式庫、安全旗標 | 細節藏起來，出事時不知道從何查 | 日常開發 |
| 手動呼叫 cc1/as/ld | 完全掌控每一步 | 參數隨平台變，容易漏 | 學習、寫 bootloader 或核心等不用 libc 的程式 |
| gcc 交叉編譯器（一目標一套） | 與目標 sysroot 綁好，開箱即用 | 每個目標都要裝一套 | 嵌入式產品線（Buildroot、Yocto 都這樣做） |
| clang `--target` | 一個執行檔吃所有架構 | 仍需自備 sysroot，連結器要另外選 | 多平台 CI、Android NDK |
| 動態連結 + qemu `-L` | 執行檔小，貼近目標系統 | 需要目標 sysroot | 開發期測試 |
| `-static` + qemu | 不依賴 sysroot | 執行檔大，glibc 對靜態連結有限制（如 NSS） | 快速驗證、容器內測試 |

## 踩雷集錦

1. **「gcc 就是編譯器」**：gcc 是驅動程式，真正的編譯器是 `cc1`，組譯器是 `as`，連結器是 `ld`。碰到「undefined reference」時要知道那是 **ld** 的錯誤訊息，改 `#include` 不會有用——缺的是 `-l` 程式庫或某個 `.o`。
2. **「我沒加旗標，所以編譯器什麼都沒加」**：Ubuntu 的 gcc 預設 PIE、`-fstack-protector-strong`、`-fcf-protection`、`-z now -z relro`，`-O2` 時還有 `_FORTIFY_SOURCE`（`printf` 會變成 `__printf_chk`，Ch 37 會看到）。比對別人貼的組合語言前，先用 `gcc -v` 或 `gcc -Q --help=common` 確認雙方旗標一致。
3. **`PATH` 決定用哪個 `as` / `ld`**：strace 顯示 gcc 依 `PATH` 逐一嘗試。裝了多套工具鏈（例如 Rust、conda、廠商 SDK）的機器，可能用到不相容的 `as`。用 `gcc -print-prog-name=as`、`gcc -print-prog-name=ld` 確認。
4. **交叉編譯出的執行檔「在這台機器上能跑」不代表編對了**：binfmt_misc 讓 QEMU 默默介入，看起來像原生執行。效能數字因此完全不能當真（QEMU user mode 是動態二進位翻譯），而且 `uname -m` 在被模擬的程式裡會回報 `aarch64`，容易誤導。
5. **把 `size` 的 text 當成 `.text` 大小**：它包含 `.rodata`、`.eh_frame` 等所有唯讀 section。要看單一 section 用 `objdump -h` 或 `readelf -S`。
6. **strip 之後 `nm` 看不到符號就以為程式壞了**：執行時用到的是 `.dynsym`；`.symtab` 只給連結器與除錯器。反過來說，strip 不是保密措施——`.dynsym` 與字串常數仍在，`objdump -d` 照樣反組譯。

## 進階：再往深一層

- **gcc 的 spec 檔**：`gcc -dumpspecs` 印出驅動程式的「腳本」，裡面以一種迷你語言描述「看到 `-static` 就換 crt1.o」「看到 `-pie` 就用 Scrt1.o」。理解它，就能用 `-specs=` 為特殊平台客製驅動行為（newlib 的 `nano.specs` 就是這樣做的）。
- **`gcc -###`**：和 `-v` 類似，但只印命令不執行，適合寫建置腳本時抓參數。
- **編譯器內部的三層表示**：cc1 內部從 GENERIC（AST）轉 GIMPLE（三位址碼、SSA），再轉 RTL（貼近機器的 S-expression）。下一章用 `-fdump-tree-*` 與 `-fdump-rtl-*` 實際把它們印出來。
- **Canadian cross**：在機器 A 上建置一個「在機器 B 上執行、產生機器 C 程式碼」的編譯器。GNU 的 `--build`、`--host`、`--target` 三個 configure 參數就是這三台機器；`gcc -v` 輸出裡的 `--build=x86_64-linux-gnu --host=x86_64-linux-gnu --target=x86_64-linux-gnu` 表示這是最單純的原生編譯器。
- **自己的 sysroot**：Buildroot 或 crosstool-NG 能從原始碼建出一整套交叉工具鏈（binutils + gcc + libc + 標頭），是理解「工具鏈到底由哪些零件組成」的最佳實作練習。

## 動手練習

1. 在 `hello.c` 加一行 `#include "nonexist.h"`，分別用 `gcc -E`、`gcc -S`、`gcc -c` 編譯，觀察錯誤在哪一步出現、錯誤訊息出自哪個程式。再把 `printf` 拼錯成 `pritnf`，同樣三步，看錯誤（或警告）分別在哪一站才浮現。
2. 用 `-save-temps` 編譯一個用了 `assert` 與 `offsetof` 的程式，在 `.i` 裡找出這兩個巨集展開成什麼。
3. 寫一個 10 個 `long` 參數的函式，分別用 `gcc -O2` 與 `aarch64-linux-gnu-gcc -O2` 編譯，用 objdump 找出哪幾個引數從 stack 讀、偏移各是多少，畫出 x86-64 呼叫前那一刻的 stack 圖。
4. 把 `spin.c` 的 `ready` 改成 `volatile int`，`-O2` 下會不會卡死？用 objdump 確認。再想想：`volatile` 修好了這個例子，為什麼它仍不是正確的執行緒同步工具？（提示：[Ch 20](./20-lock-free-linked-list.md) 與下一章的 volatile 一節。）
5. 用 `riscv64-linux-gnu-gcc -O2 -c args.c` 編譯，找出 RISC-V 從第幾個整數引數開始改走 stack，補進「Procedures」那張表。

## 本章重點整理

- `gcc` 是編譯器驅動程式：依序叫起 `cc1`（前置處理 + 編譯）、`as`（組譯）、`collect2` → `ld`（連結），用 `-v`、`-save-temps`、`strace -f -e trace=execve` 可以親眼確認。
- 執行檔不只是你的程式碼：還有 `Scrt1.o`/`crti.o`/`crtbeginS.o` 等啟動碼、libc、libgcc，以及發行版預設的安全強化。
- binutils 分工：`nm` 看符號、`objdump -h`/`readelf -S` 看 section、`readelf -h` 看 ELF 檔頭、`size` 看三大區、`strip` 去除符號與除錯資訊、`addr2line` 從位址反查原始碼。
- x86-64 以可變長度指令塞進 32 位元位移與立即值；AArch64 以固定長度指令配對 `adrp`+`ldr`/`add`、`mov`+`movk`。兩者都用 PC 相對定址達成位置無關。
- 引數暫存器的數量是 ABI 決定的：ARM 4 個、x86-64 SysV 6 個、AArch64 8 個；超過的從 stack 傳。
- target triple 描述「架構-廠商-作業系統-ABI/libc」；ABI 不同（如 `hf` 與 `soft`）的目的檔不能混著連結。

## 自我檢核

- [ ] 不看筆記，畫出 `gcc hello.c` 從 `.c` 到執行檔經過的每個程式、每個中間檔，並說出 `ld` 的命令列上除了你的 `.o` 還有哪些東西、各自用途。
- [ ] 同事說「`-O2` 讓我的程式卡死，一定是 gcc 的 bug」，你會依序下哪些指令去查？如果查到的是 `spin.c` 那種情況，你會怎麼向他解釋「這不是 gcc 的 bug」？
- [ ] 給你 `e8 f3 fe ff ff` 這 5 個位元組與它所在的位址，你能算出呼叫目標嗎？為什麼位移要相對「下一條」指令？
- [ ] 能解釋為什麼 `nm` 對 `static` 函式印小寫字母，以及這和「為什麼 local function 該宣告成 static」有什麼關係嗎？
- [ ] 能說出 `aarch64-linux-gnu-gcc` 編出的程式在 x86-64 上「直接執行」時，核心、binfmt_misc、QEMU、動態連結器各做了什麼，以及它為什麼一開始失敗？
- [ ] 不看表格，說出 AArch64 載入一個全域變數為什麼需要兩條指令，而 x86-64 只要一條。

## 延伸閱讀

### 官方文件 / Spec

- **[GCC Manual：Overall Options](https://gcc.gnu.org/onlinedocs/gcc/Overall-Options.html) 與 [Developer Options](https://gcc.gnu.org/onlinedocs/gcc/Developer-Options.html)**
  - **讀哪裡**：Overall Options 的 `-E`/`-S`/`-c`/`-v`/`-###`；Developer Options 的 `-save-temps`、`-dumpmachine`、`-print-prog-name`、`-dumpspecs`
  - **能學到什麼**：本章用到的每一個「讓 gcc 停下來或吐出內部狀態」的旗標的正式定義
  - **前提**：會在 shell 下編譯 C 程式即可
- **[GCC Internals：Collect2](https://gcc.gnu.org/onlinedocs/gccint/Collect2.html)**
  - **讀哪裡**：整節（很短）
  - **能學到什麼**：collect2 存在的歷史原因——在沒有原生建構式機制的平台上收集初始化函式；對照本章 strace 看到的「collect2 再呼叫 ld」
  - **前提**：知道什麼是 constructor（`__attribute__((constructor))`、C++ 全域物件）
- **[GNU Binutils 文件](https://sourceware.org/binutils/docs/binutils/)**
  - **讀哪裡**：`objdump`、`readelf`、`nm`（看符號類型字母的完整表）、`size`、`strip`、`addr2line` 各節
  - **能學到什麼**：本章只用到各工具的一小部分，例如 `objdump -S`（交錯原始碼與組語）、`readelf --debug-dump`、`nm -C`（C++ 名稱還原）都值得試
  - **前提**：無
- **[System V AMD64 ABI](https://gitlab.com/x86-psABIs/x86-64-ABI) 與 [Arm AAPCS64](https://github.com/ARM-software/abi-aa/blob/main/aapcs64/aapcs64.rst)**
  - **讀哪裡**：x86-64 ABI 第 3.2 節（Function Calling Sequence）；AAPCS64 的「Parameter Passing」一節
  - **能學到什麼**：本章表格的權威來源，包括結構體怎麼拆進暫存器、浮點引數走哪些暫存器等本章沒展開的規則
  - **前提**：看得懂基本組合語言

### 部落格 / 技術文章

- **[Who's afraid of a big bad optimizing compiler?](https://lwn.net/Articles/793253/)** — Jade Alglave, Will Deacon, Boqun Feng, David Howells, Daniel Lustig, Luc Maranget, Paul E. McKenney, Andrea Parri, Nicholas Piggin, Alan Stern, Akira Yokosawa, Peter Zijlstra（LWN，2019）
  - **這篇說什麼**：列舉編譯器對「普通」load/store 能做的各種轉換（load tearing、store fusing、invented loads……），以及 Linux 核心用 `READ_ONCE()`/`WRITE_ONCE()` 對抗的方法
  - **讀哪裡**：從「Load tearing」開始的各個小節，每節都有短範例
  - **為什麼值得讀**：作者群是 Linux 核心記憶體模型的維護者；本章 `spin.c` 就是文中「invented / hoisted loads」的最小重現
- **[Position Independent Code (PIC) in shared libraries on x64](https://eli.thegreenplace.net/2011/11/11/position-independent-code-pic-in-shared-libraries-on-x64)** — Eli Bendersky（2011）
  - **這篇說什麼**：RIP 相對定址如何讓 x86-64 的位置無關程式碼比 32 位元 x86 便宜很多
  - **讀哪裡**：整篇；先讀同作者談 32 位元 PIC 的前一篇更容易理解對比
  - **為什麼值得讀**：補足本章「機械碼對照」只點到為止的 RIP 相對定址與 GOT

### 書籍

- **《Computer Systems: A Programmer's Perspective》3rd ed.** — Bryant & O'Hallaron（Pearson，2015）
  - **這本書的定位**：第 3 章（機器層級表示）與第 7 章（連結）是本章與 Ch 39–41 的教科書版本
  - **讀哪幾章**：3.4（存取資訊與定址模式）、3.7（程序）、7.1–7.4（編譯器驅動程式、目的檔、符號）
- **《Linkers and Loaders》** — John R. Levine（Morgan Kaufmann，1999）
  - **這本書的定位**：年代較早（ELF 前的格式著墨較多），但第 1–3 章對「工具鏈各站做什麼」的說明至今適用
  - **讀哪幾章**：第 1 章（Linking and Loading）、第 3 章（Object Files）

### 相關課程

- [`../../compilers/elf_linking/01-elf-three-layers.md`](../../compilers/elf_linking/01-elf-three-layers.md)：本 repo 的 ELF 與連結專門課，把本章 `readelf` 看到的結構展開成完整一章
- [`../c_interview/16-calling-convention-abi.md`](../c_interview/16-calling-convention-abi.md)：呼叫慣例的面試向整理

知道了工具鏈的每一站在做什麼，下一章走進最神秘的那一站——`cc1` 內部，看編譯器如何把程式轉成 SSA，再一步步把它改寫得更快、更小。

→ [Ch 37 編譯器最佳化原理：SSA 與常見最佳化](./37-compiler-optimization.md)
