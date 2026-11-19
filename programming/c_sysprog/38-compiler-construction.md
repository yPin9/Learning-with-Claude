# Ch 38 — 編譯器實作：Brainfuck、c4 與 shecc

> **目標**：親手寫出同一門語言的四種執行方式——逐字元直譯、IR 直譯、翻譯成 C 交給 gcc（AOT）、用 `mmap`/`mprotect` 在執行期產生 x86-64 機械碼（JIT）——並量測差距從何而來；讀懂 c4 如何用四個函式把 C 子集編譯成虛擬機器指令再直譯自己；理解 shecc 這類能編譯自身的編譯器的管線（前置處理 → SSA IR → 最佳化 → 暫存器配置 → ELF），以及自我編譯的不動點、Ken Thompson 的「Reflections on Trusting Trust」與 diverse double-compiling 到底證明了什麼；最後知道「編譯器本身也會錯」，以及 Csmith 怎麼找出這些錯誤。
> **對應原文**：[你所不知道的 C 語言：編譯器原理和案例分析篇](https://hackmd.io/@sysprog/c-compiler-construction) 的「從 Brainfuck 認識直譯、編譯與 JIT」「c4：直譯自身的 C 子集」「編譯器的規模」「編譯器與軟體可靠度」「shecc 的編譯管線」「自我編譯與信任」「VIR：SSA 與 block parameter」「程式碼產生、ABI 與 ELF」「效能」等節

> **環境**：WSL Ubuntu 22.04 x86_64（AMD Ryzen 7 5700G），gcc 11.4.0、clang 14.0.0、`aarch64-linux-gnu-gcc` 11.4.0 + `qemu-aarch64` 6.2。c4 取自 [rswier/c4](https://github.com/rswier/c4) 2019-03-14 的 `2feb8c0`；Brainfuck 測試程式取自 [jserv/jit-construct](https://github.com/jserv/jit-construct) 的 `progs/`；shecc 取自 [sysprog21/shecc](https://github.com/sysprog21/shecc) 2026-11-06 的 `0f0f4f8`。shecc 演進很快，原文用的是 2026-10 的版本，部分 IR 輸出與本章不同，會在文中標出。

## 為什麼需要這個？

前兩章從外面看工業編譯器：Ch 36 看它由哪些工站組成，Ch 37 看它怎麼改寫程式。但 GCC 有上千萬行（下文會看到數字），讀它的原始碼不是學編譯器的好起點。要真正理解「直譯和編譯差在哪」「JIT 為什麼快」「編譯器怎麼編譯自己」，最好的方法是自己寫一個最小的，再讀幾個刻意寫得很小的。

這一章的路線是：

```
Brainfuck（8 個指令）        c4（C 子集，~500 行）          shecc（C 子集，數萬行）
 ───────────────────         ────────────────────          ─────────────────────
 直譯 / AOT / JIT 三種後端      邊剖析邊產生 VM 指令             SSA IR + 最佳化 pass
 量測每一層抽象的代價          直譯自己（自我直譯）             自我編譯到不動點，直接寫 ELF
```

三者共用同一副骨架：**前端**把原始碼轉成某種中間表示，**後端**決定何時、以什麼形式執行它。差別只在中間表示有多豐富、後端走多遠。

## 先建立直覺：直譯、編譯、JIT 是「翻譯的時機」不同

想像你拿到一份外文食譜：

- **直譯器（interpreter）**：每做一步就翻一句，下次再做同一道菜又重翻一次。啟動最快，執行最慢。
- **事先編譯（ahead-of-time, AOT）**：做菜前先把整份食譜翻好寫下來，之後照中文版做。翻譯要花時間，但做菜時不再翻。
- **及時編譯（just-in-time, JIT）**：開始做菜時才翻譯（可能只翻常做的那幾道），翻好的直接用。翻譯時間算在做菜時間裡，但能依「今天手邊有什麼材料」調整翻法。

原文特別提醒：just-in-time 譯為「及時」，不是「即時」。即時（real-time）系統的正確性包含時間，晚到就算錯；JIT 沒有這種保證，編譯本身就花時間。本章 `bf -j` 讀進程式後一次編譯完再執行——編譯發生在 `bf` 行程的執行期，所以仍算 JIT。

```
                      ┌──► run_naive()  逐字元直譯，跳躍時掃描原始碼找配對括號
 Brainfuck 原始碼 ────┤
                      └──► parse() ──► 線性 IR（struct op 陣列）
                                          │
                                          ├──► run_ir()   直譯 IR（跳躍目標已算好）
                                          ├──► emit_c()   輸出 C，交給 gcc（AOT）
                                          └──► run_jit()  編碼成 x86-64 機械碼，mmap 後呼叫（JIT）
```

## Brainfuck：只有八個指令的機器

Urban Müller 在 1993 年發表 Brainfuck。機器有一條初值全為 0 的位元組陣列（tape）和一個指向其中一格的資料指標 `p`；程式由八個字元組成，其他字元都是註解：

| 指令 | 對應的 C | 意義 |
|---|---|---|
| `>` `<` | `++p;` `--p;` | 指標右移 / 左移一格 |
| `+` `-` | `++*p;` `--*p;` | 目前格加一 / 減一 |
| `.` `,` | `putchar(*p);` `*p = getchar();` | 輸出 / 輸入一個位元組 |
| `[` `]` | `while (*p) {` `}` | 目前格為 0 就跳過迴圈 / 不為 0 就跳回開頭 |

它是圖靈完備的，原文因此建議對照計算理論裡的 Turing machine。語言本身沒有規定格的寬度、陣列長度、越界與 EOF 的行為——這就是原文說的「灰色地帶」。本章的實作選擇：格是 `uint8_t`、30000 格（Müller 原始編譯器的大小）、不檢查越界。`uint8_t` 讓溢位行為明確：`++*p` 先依整數提升（N1570 6.3.1.1 §2）轉成 `int` 加 1，存回時依 6.3.1.3 §2 取模 256，255 加 1 得 0，沒有有號溢位的 UB。EOF 方面，`getchar` 回傳 `int` 正是為了容納 `EOF`（一個負的 `int`，N1570 7.21.1 §3），glibc 定義為 −1，存進 `uint8_t` 變成 255。

範例程式 `A.b` 印出 `A` 和換行：

```
++++++++[->++++++++<]>+.[-]++++++++++.
```

`++++++++` 把第 0 格設成 8；`[->++++++++<]` 每圈第 0 格減 1、第 1 格加 8，共 8 圈，第 1 格變成 64；`>+.` 移到第 1 格加 1 得 65（`'A'`）並輸出；`[-]` 把它清成 0，再加 10 輸出換行。

## 一份前端，四種執行方式：bf.c

以下是完整程式（206 行）。結構仿照原文 jserv 整理的 `bf.c`，但精簡掉迴圈乘法改寫（留給練習 E），保留收縮與 `[-]` 清零兩項最佳化：

```c
/* bf.c：Brainfuck 的直譯器、C 翻譯器與 x86-64 JIT（共用同一份 IR） */
#define _DEFAULT_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/mman.h>

#define TAPE 30000                        /* Müller 原始實作的格數 */
enum kind { ADD, MOVE, OUT, IN, JZ, JNZ, CLEAR };
struct op { enum kind kind; int arg; };   /* ADD: 增量 mod 256；MOVE: 位移；JZ/JNZ: 配對索引 */

static struct op ops[1 << 16];
static int nops;

static void die(const char *m) { fprintf(stderr, "bf: %s\n", m); exit(1); }

static void emit(enum kind k, int arg, int opt)
{
    if (opt && nops > 0 && ops[nops - 1].kind == k && (k == ADD || k == MOVE)) {
        ops[nops - 1].arg += arg;              /* 收縮：+++ 變成 ADD 3 */
        if (k == ADD) ops[nops - 1].arg &= 255;
        if (ops[nops - 1].arg == 0) nops--;    /* +- 互相抵銷 */
        return;
    }
    if (nops == (int) (sizeof ops / sizeof ops[0])) die("program too long");
    ops[nops++] = (struct op) {k, arg};
}

static void parse(const char *s, int opt)
{
    int stack[1024], depth = 0;
    for (; *s; s++) {
        switch (*s) {
        case '+': emit(ADD, 1, opt); break;
        case '-': emit(ADD, 255, opt); break;
        case '>': emit(MOVE, 1, opt); break;
        case '<': emit(MOVE, -1, opt); break;
        case '.': emit(OUT, 0, 0); break;
        case ',': emit(IN, 0, 0); break;
        case '[':
            if (depth == 1024) die("nesting too deep");
            stack[depth++] = nops;
            emit(JZ, 0, 0);
            break;
        case ']': {
            if (depth == 0) die("unmatched ]");
            int start = stack[--depth];
            if (opt && nops == start + 2 && ops[start + 1].kind == ADD &&
                (ops[start + 1].arg & 1)) {      /* [-]、[+]：奇數增量必定歸零 */
                nops = start;
                emit(CLEAR, 0, 0);
                break;
            }
            ops[start].arg = nops;
            emit(JNZ, start, 0);
            break;
        }
        }
    }
    if (depth) die("unmatched [");
}

static void run_naive(const char *src)        /* 逐字元直譯，跳躍時掃描原始碼 */
{
    static uint8_t tape[TAPE];
    uint8_t *p = tape;
    for (const char *c = src; *c; c++) {
        int d = 1;
        switch (*c) {
        case '>': p++; break;
        case '<': p--; break;
        case '+': ++*p; break;
        case '-': --*p; break;
        case '.': putchar(*p); break;
        case ',': *p = (uint8_t) getchar(); break;
        case '[': if (!*p) while (d) { c++; d += (*c == '[') - (*c == ']'); } break;
        case ']': if (*p) while (d) { c--; d += (*c == ']') - (*c == '['); } break;
        }
    }
}

static void run_ir(void)                      /* 直譯 IR：跳躍目標已算好 */
{
    static uint8_t tape[TAPE];
    uint8_t *p = tape;
    for (int pc = 0; pc < nops; pc++) {
        struct op o = ops[pc];
        switch (o.kind) {
        case ADD: *p += o.arg; break;
        case MOVE: p += o.arg; break;
        case OUT: putchar(*p); break;
        case IN: *p = (uint8_t) getchar(); break;
        case JZ: if (!*p) pc = o.arg; break;
        case JNZ: if (*p) pc = o.arg; break;
        case CLEAR: *p = 0; break;
        }
    }
}

static void emit_c(void)                      /* AOT：翻成 C，交給 gcc */
{
    printf("#include <stdio.h>\nstatic unsigned char tape[%d];\n"
           "int main(void)\n{\n    unsigned char *p = tape;\n", TAPE);
    int d = 1;
    for (int i = 0; i < nops; i++) {
        struct op o = ops[i];
        if (o.kind == JNZ) d--;
        printf("%*s", 4 * d, "");
        switch (o.kind) {
        case ADD: printf("*p += %d;\n", o.arg); break;
        case MOVE: printf("p += %d;\n", o.arg); break;
        case OUT: puts("putchar(*p);"); break;
        case IN: puts("*p = getchar();"); break;
        case JZ: puts("while (*p) {"); d++; break;
        case JNZ: puts("}"); break;
        case CLEAR: puts("*p = 0;"); break;
        }
    }
    puts("    return 0;\n}");
}

static uint8_t code[1 << 22];
static size_t len;
static void put(const void *b, size_t n)
{
    if (len + n > sizeof code) die("code buffer full");
    memcpy(code + len, b, n);
    len += n;
}
#define PUT(...) put((const uint8_t[]) {__VA_ARGS__}, sizeof((const uint8_t[]) {__VA_ARGS__}))
static void put32(int32_t v) { put(&v, 4); }           /* x86-64 為 little-endian */
static void put_call(uintptr_t fn)
{
    uint64_t a = fn;
    PUT(0x48, 0xb8); put(&a, 8);                      /* mov rax, imm64 */
    PUT(0xff, 0xd0);                                  /* call rax */
}

static void run_jit(void)                     /* JIT：把 IR 編碼成 x86-64 機械碼 */
{
#if defined(__x86_64__)
    static int32_t after_jz[1 << 16];               /* 每個 JZ 的 rel32 之後的位置 */
    PUT(0x53);                                      /* push rbx */
    PUT(0x48, 0x89, 0xfb);                          /* mov rbx, rdi */
    for (int i = 0; i < nops; i++) {
        struct op o = ops[i];
        switch (o.kind) {
        case ADD: PUT(0x80, 0x03, (uint8_t) o.arg); break;          /* add byte [rbx], imm8 */
        case MOVE: PUT(0x48, 0x81, 0xc3); put32(o.arg); break;      /* add rbx, imm32 */
        case OUT: PUT(0x0f, 0xb6, 0x3b);                             /* movzx edi, byte [rbx] */
                  put_call((uintptr_t) putchar); break;
        case IN: put_call((uintptr_t) getchar); PUT(0x88, 0x03); break; /* mov [rbx], al */
        case CLEAR: PUT(0xc6, 0x03, 0x00); break;                    /* mov byte [rbx], 0 */
        case JZ:                                                     /* cmp byte [rbx],0; je ? */
            PUT(0x80, 0x3b, 0x00, 0x0f, 0x84); put32(0);
            after_jz[i] = (int32_t) len;
            break;
        case JNZ: {                                                  /* cmp byte [rbx],0; jne body */
            int32_t body = after_jz[o.arg];
            PUT(0x80, 0x3b, 0x00, 0x0f, 0x85); put32(body - (int32_t) (len + 4));
            int32_t fwd = (int32_t) len - body;                      /* 回填 JZ 的目的地 */
            memcpy(code + body - 4, &fwd, 4);
            break;
        }
        }
    }
    PUT(0x5b, 0xc3);                                /* pop rbx; ret */

    void *mem = mmap(NULL, len, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) die("mmap");
    memcpy(mem, code, len);
    if (mprotect(mem, len, PROT_READ | PROT_EXEC) != 0) die("mprotect");  /* W^X */
    static uint8_t tape[TAPE];
    ((void (*)(uint8_t *)) mem)(tape);
    munmap(mem, len);
#else
    die("JIT supports x86-64 only");
#endif
}

static char *slurp(const char *path)
{
    static char buf[1 << 20];
    FILE *f = fopen(path, "rb");
    if (!f) die("cannot open input");
    size_t n = fread(buf, 1, sizeof buf - 1, f);
    fclose(f);
    buf[n] = '\0';
    return buf;
}

int main(int argc, char **argv)
{
    int opt = argc == 4 && strcmp(argv[1], "-O") == 0;
    if (argc != 3 + opt) die("usage: bf [-O] -n|-i|-c|-j file");
    const char *mode = argv[1 + opt];
    char *src = slurp(argv[2 + opt]);
    parse(src, opt);
    if (!strcmp(mode, "-n")) run_naive(src);
    else if (!strcmp(mode, "-i")) run_ir();
    else if (!strcmp(mode, "-c")) emit_c();
    else if (!strcmp(mode, "-j")) run_jit();
    else die("unknown mode");
    return 0;
}
```

### 前端：parse() 與收縮

`parse()` 掃一遍原始碼，產生 `struct op` 陣列。`[` 先把自己的索引推進 stack、`]` 彈出配對的 `[`，互相記下對方的索引——這樣直譯時跳躍是 O(1)，不必再掃描原始碼。

加 `-O` 時多做兩件事：

- **收縮（contraction）**：`emit()` 發現要產生的 op 和上一個同種，就把增量合併。`++++++++` 變成一個 `ADD 8`，`+-` 互相抵銷時整個刪除。這是 Ch 37 的常數摺疊：在編譯期把 8 次加 1 算成加 8。
- **清零迴圈**：`[-]` 的主體只有一個 ADD；若增量是奇數，因為奇數與 256 互質，重複加它必定會在某一圈得到 0，所以整個迴圈等同 `*p = 0`。

看 `-O -c` 的輸出就能看到 IR 的樣子：

```
$ ./bf -O -c A.b
#include <stdio.h>
static unsigned char tape[30000];
int main(void)
{
    unsigned char *p = tape;
    *p += 8;
    while (*p) {
        *p += 255;
        p += 1;
        *p += 8;
        p += -1;
    }
    p += 1;
    *p += 1;
    putchar(*p);
    *p = 0;
    *p += 10;
    putchar(*p);
    return 0;
}
```

`-` 被表示成 `+= 255`（模 256 下等價），`[-]` 變成 `*p = 0`。還看得到改進空間：`*p = 0; *p += 10;` 可以合成 `*p = 10`（常數傳遞），中間的 `[->++++++++<]` 其實等於 `p[1] += p[0] * 8; p[0] = 0;`（迴圈改寫成乘法）。原文的 `bf.c` 有後者，稱為 `-O2` 的 `fold_loop()`；本章留到 [練習 E](./practice-e-malloc-tracer-and-bf-jit.md)。

### 四個後端

```
$ gcc -std=c11 -Wall -Wextra -O2 -o bf bf.c
$ for m in -n -i -j; do ./bf $m A.b; ./bf -O $m A.b; done
A
A
A
A
A
A
```

- **`run_naive()`**：讀一個字元執行一個。遇到要跳躍的 `[` 或 `]`，以巢狀深度 `d` 往前或往後掃描原始碼找配對括號。
- **`run_ir()`**：直譯 IR，`JZ`/`JNZ` 只是改寫 `pc`。
- **`emit_c()`**：把 IR 一對一翻成 C，交給 gcc。這是「編譯到另一個高階語言」，很多語言的早期實作（例如 C++ 的 Cfront、Nim）都這樣起步，好處是直接繼承 gcc 的最佳化與所有目標平台。原文的版本改為輸出 x86-64 組合語言交給 `as`；手寫組語時要記得加 `.section .note.GNU-stack,"",@progbits`——本機 binutils 2.38 實測，漏掉它時 ld 不警告，但執行檔的 `GNU_STACK` 會變成 `RWE`（可執行 stack）：

```
$ gcc -o nostack nostack.s && readelf -lW nostack | grep GNU_STACK
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RWE 0x10
（加上那一行之後）
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
```

- **`run_jit()`**：把每個 op 編碼成固定的 x86-64 指令位元組，寫進 `mmap` 的記憶體再以函式指標呼叫。

### JIT 逐位元組解剖

把 JIT 產生的位元組存檔，用 `objdump -b binary` 反組譯（在 `mmap` 前插一行 `fwrite(code, 1, len, f)` 的修改版）：

```
$ ./bf_dump -O -j A.b && objdump -D -b binary -m i386:x86-64 jit.bin
   0:	53                   	push   %rbx
   1:	48 89 fb             	mov    %rdi,%rbx
   4:	80 03 08             	addb   $0x8,(%rbx)
   7:	80 3b 00             	cmpb   $0x0,(%rbx)
   a:	0f 84 1d 00 00 00    	je     0x2d
  10:	80 03 ff             	addb   $0xff,(%rbx)
  13:	48 81 c3 01 00 00 00 	add    $0x1,%rbx
  1a:	80 03 08             	addb   $0x8,(%rbx)
  1d:	48 81 c3 ff ff ff ff 	add    $0xffffffffffffffff,%rbx
  24:	80 3b 00             	cmpb   $0x0,(%rbx)
  27:	0f 85 e3 ff ff ff    	jne    0x10
  2d:	48 81 c3 01 00 00 00 	add    $0x1,%rbx
  34:	80 03 01             	addb   $0x1,(%rbx)
  37:	0f b6 3b             	movzbl (%rbx),%edi
  3a:	48 b8 d0 28 28 a8 37 	movabs $0x7237a82828d0,%rax
  41:	72 00 00
  44:	ff d0                	call   *%rax
  46:	c6 03 00             	movb   $0x0,(%rbx)
  ...
  5b:	5b                   	pop    %rbx
  5c:	c3                   	ret
```

（`movabs` 的位址是 `putchar` 在本次執行的位置，每次執行不同。）幾個設計決策：

- **資料指標放 `rbx`**：x86-64 SysV ABI 規定 `rbx` 是 callee-saved，呼叫 `putchar` 之後保證不變；所以進入時要 `push rbx`、離開前 `pop rbx`。
- **stack 對齊**：進入 JIT 函式時 `rsp` 差 8 位元組才是 16 的倍數（`call` 推了回傳位址），`push rbx` 恰好補齊，之後呼叫 `putchar` 時滿足 ABI 的對齊要求。這是 [Ch 11](./11-process-layout-and-abi.md) 講過的規則在實戰中的樣子。
- **前向跳躍要回填（backpatching）**：產生 `je` 時還不知道 `]` 在哪，先填 0 並記下位置，遇到配對的 `]` 再把相對位移寫回去。位移相對「下一條指令」：`0x10 + 0x1d = 0x2d`、`0x2d + 0xffffffe3 = 0x10`（−0x1d）。組譯器處理前向標籤也是這樣做。
- **呼叫 libc 用 `mov rax, imm64; call rax`**：`mmap` 拿到的位址和 libc 的距離可能超過 `call rel32` 的 ±2 GiB（Ch 36「機械碼對照」一節），只好把完整 64 位元位址放進暫存器。
- **`MOVE` 一律用 32 位元立即值**（`48 81 c3 imm32`），即使位移只有 1。`as` 會為小位移選 `48 83 c3 imm8` 的短編碼；我們的 JIT 為了簡單沒這麼做，代價是程式碼變大。

### W^X 與「物件指標轉函式指標」

最小的 JIT 只有兩步：寫位元組、改成可執行、呼叫。用一個「兩數相加」的骨架看清楚每一步，順便驗證跨架構的細節：

```c
/* jit_add.c：最小的 JIT——把「兩數相加」的機械碼寫進記憶體再呼叫 */
#define _DEFAULT_SOURCE
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/mman.h>
#include <unistd.h>

typedef int (*binop_t)(int, int);

#if defined(__x86_64__)
static const uint8_t code[] = {0x8d, 0x04, 0x37,  /* lea eax, [rdi + rsi] */
                               0xc3};             /* ret */
#elif defined(__aarch64__)
static const uint32_t code[] = {0x0b010000,       /* add w0, w0, w1 */
                                0xd65f03c0};      /* ret */
#else
#error "only x86-64 and AArch64"
#endif

int main(int argc, char **argv)
{
    int skip_mprotect = argc > 1 && strcmp(argv[1], "--no-exec") == 0;
    size_t size = (size_t) sysconf(_SC_PAGESIZE);
    void *mem = mmap(NULL, size, PROT_READ | PROT_WRITE,
                     MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
    if (mem == MAP_FAILED) { perror("mmap"); return 1; }
    memcpy(mem, code, sizeof code);
    __builtin___clear_cache((char *) mem, (char *) mem + sizeof code);
    if (!skip_mprotect && mprotect(mem, size, PROT_READ | PROT_EXEC) != 0) {
        perror("mprotect");
        return 1;
    }
    binop_t add = (binop_t) mem;
    printf("add(40, 2) = %d\n", add(40, 2));
    munmap(mem, size);
    return 0;
}
```

```
$ gcc -std=c11 -Wall -Wextra -O2 -o jit_add jit_add.c && ./jit_add
add(40, 2) = 42
$ ./jit_add --no-exec; echo "rc=$?"
Segmentation fault (core dumped)
rc=139
$ aarch64-linux-gnu-gcc -std=c11 -Wall -Wextra -O2 -static -o jit_add_a64 jit_add.c && qemu-aarch64 ./jit_add_a64
add(40, 2) = 42
```

指令編碼用 `as` 組譯後的 objdump 核對過：x86-64 的 `lea (%rdi,%rsi,1),%eax` 是 `8d 04 37`，AArch64 的 `add w0, w0, w1` 是 `0b010000`。三個關鍵細節：

1. **W^X（write xor execute）**：記憶體不能同時可寫又可執行。先以 `PROT_READ | PROT_WRITE` 寫入，再 `mprotect` 成 `PROT_READ | PROT_EXEC`。跳過 `mprotect` 時（`--no-exec`），CPU 的 NX 位元讓「從資料頁取指令」觸發 page fault，核心送出 SIGSEGV（退出碼 139 = 128 + 11）。有些系統會直接拒絕同時要求寫與執行的 `mmap`（例如預設強制 W^X 的 OpenBSD、禁止 `execmem` 的 SELinux 策略；Apple Silicon 上的 macOS 則要求 JIT 改用特別的 `MAP_JIT` 旗標）——這些平台本章都沒有實測；jit-construct 早期的 `jit0-x64.c` 一次要 `PROT_WRITE | PROT_EXEC`，在這類系統上就跑不起來。
2. **指令快取一致性**：x86-64 的硬體自動讓指令快取與資料快取一致，`__builtin___clear_cache` 在 x86-64 上不產生任何指令。AArch64 不保證這點，寫進去的指令要先清回 point of unification、讓 I-cache 失效。從靜態連結的 AArch64 執行檔可以看到它最後跳到 libgcc 的 `__aarch64_sync_cache_range`：

   ```
   $ aarch64-linux-gnu-objdump -d jit_add_a64 | grep -A1 '<__clear_cache>:'
   0000000000400820 <__clear_cache>:
     400820:	14000004 	b	400830 <__aarch64_sync_cache_range>
   ```

   本章的 AArch64 結果是在 QEMU 上跑的，QEMU user mode 的翻譯快取不完全模擬真實硬體的 I-cache 行為，**漏掉 `__builtin___clear_cache` 在 QEMU 上可能仍然「看起來正常」，在真實 AArch64 硬體上才出錯**——這一點本章沒有在實體機上驗證。
3. **物件指標轉函式指標不是標準 C**：N1570 6.3.2.3 §8 只定義函式指標之間的轉換，沒有定義 `void *` 轉函式指標；附錄 J.5.7 把它列為常見擴充。加 `-Wpedantic` 會看到：

   ```
   $ gcc -std=c11 -Wall -Wextra -Wpedantic -O2 -o bf bf.c
   bf.c: In function ‘run_jit’:
   bf.c:175:6: warning: ISO C forbids conversion of object pointer to function pointer type [-Wpedantic]
     175 |     ((void (*)(uint8_t *)) mem)(tape);
         |      ^
   ```

   POSIX 只在 `dlsym` 明文要求「回傳的 `void *` 轉成函式指標後可以呼叫」。JIT 能運作，依賴的是 Linux 上 gcc/clang 的實作與 ABI。`bf.c` 把 `putchar` 的位址轉成 `uintptr_t`（函式指標轉整數是 implementation-defined，6.3.2.3 §6），避開了另一個方向的同類警告。原文另外記錄了 Clang 18 的 `-fsanitize=function` 會讓 JIT 程式碼 SEGV（它在間接呼叫前讀取目標前方的型別簽章），需要 `-fno-sanitize=function`；本機的 clang 14 沒有實測這點。

### 量測：每一層抽象的代價

以 Erik Bosman 的 `mandelbrot.b`（11451 個指令字元）量測。所有方式的輸出先以 `md5sum` 確認逐位元組相同（`hello.b`、`hanoi.b`、`sierpinski.b`、`mandelbrot.b` 四個程式，五種執行方式）。`taskset -c 5` 綁在同一個核心，直譯器各跑 1–3 次，其餘跑 3 次取最佳值：

| 執行方式 | 未最佳化 IR（11451 ops） | `-O` 收縮 + 清零（3867 ops） |
|---|---:|---:|
| 逐字元直譯 `-n` | 32.9–35.7 s（3 次） | — |
| IR 直譯 `-i` | 36.7–38.0 s（3 次） | 10.3 s |
| JIT `-j` | 2.36 s | 0.773 s |
| 翻成 C，`gcc -O0` | 9.95 s | 2.09 s |
| 翻成 C，`gcc -O2` | 0.576 s | 0.561 s |

另外，`gcc -O2` 編譯 `-O` 版的 C 檔要 904 ms，`gcc -O0` 要 281 ms。環境是 WSL2，背景負載未控制（1 分鐘平均負載約 1–1.6），所以只看數量級與比例。能讀出的事情：

1. **只預先算好跳躍目標，幾乎沒有幫助，在這台機器上甚至更慢**（`-i` 36.7 s 對 `-n` 32.9 s）。`mandelbrot.b` 的迴圈主體都很短，`run_naive` 掃描配對括號的成本不高；而 `run_ir` 每執行一個 op 都要讀出 8 位元組的結構、經 `switch` 分派，開銷和逐字元直譯同等級。我們沒有用 `perf` 拆分各因素的比重（WSL2 上 `perf` 不易使用），這只是與數據相符的推論。原文在 Threadripper 上量到的是「快 20%」，方向不同，提醒我們**直譯器的效能對微架構和編譯器版本很敏感**。
2. **收縮是最划算的最佳化**：op 數減少 66%，IR 直譯快 3.6 倍，JIT 快 3.1 倍。直譯器的成本和「執行的 op 數」幾乎成正比，減少 op 數就是一切。
3. **JIT 比 IR 直譯快 13–16 倍**：同樣的 op 序列，JIT 消除了「讀 op、分派」這層，每個跳躍也各自有獨立的分支指令可供 CPU 預測。
4. **gcc `-O2` 贏過我們的 JIT**：0.56 s 對 0.77 s。gcc 把 `p` 放在暫存器、把 `p += 1; *p += 8;` 合成一條帶偏移的指令、做了暫存器配置與指令選擇——全是我們的 JIT 沒做的。而且 `gcc -O2` 對**未收縮**的 C 檔也只要 0.576 s：gcc 的常數摺疊自動完成了我們的收縮。
5. **但 gcc 要先花將近 1 秒編譯**。若把編譯時間算進去（0.9 + 0.56 s），我們的 JIT（剖析 + 編碼 + 執行共 0.77 s）反而勝出。這就是 JIT 存在的理由：**對只跑一次的程式，翻譯成本和執行成本同樣重要**。HotSpot、V8 先直譯、對熱點再編譯，就是在權衡這筆帳。

## c4：直譯自身的 C 子集

Robert Swierczek 的 c4 標榜「C in four functions」：`next()` 是詞法分析器、`expr()` 剖析運算式、`stmt()` 剖析敘述、`main()` 讀入原始碼、處理宣告、最後直譯產生的指令。整個檔案 528 行（`wc -l`；原文以 cloc 扣除空行與註解計為 487 行），支援的 C 子集只有 `char`、`int`、指標、`enum`、`if`、`while`、`return`、`sizeof` 與運算式——但足以寫出 c4 自己。

### 虛擬機器：累加器 + stack

c4 的目標不是真實 CPU，而是一台虛擬機器（VM）：

```
 暫存器：  pc（下一條指令）  sp（stack 頂）  bp（frame 基底）  a（累加器）

 二元運算 x + y 的指令序列：
   <算 x，結果在 a>     a = x
   PSH                  把 a 推上 stack           stack: [... x]
   <算 y，結果在 a>     a = y
   ADD                  a = *sp++ + a             a = x + y，stack 彈出 x
```

一個運算元在累加器 `a`、另一個從 stack 彈出——這是介於純 stack machine（JVM）與暫存器機之間的設計，產生指令的程式碼非常短。指令集分成五群：

| 類別 | 指令 | 說明 |
|---|---|---|
| 載入 | `LEA n`、`IMM v` | `a = bp + n`（區域變數位址）、`a = v`（常數或全域位址） |
| 控制 | `JMP`、`JSR`、`BZ`、`BNZ` | 跳躍、呼叫、為零跳、非零跳 |
| frame | `ENT n`、`ADJ n`、`LEV` | 建立 frame 並保留 n 格區域變數、呼叫後彈掉 n 個引數、返回 |
| 記憶體 | `LI`、`LC`、`SI`、`SC`、`PSH` | 載入 int/char、儲存 int/char、推入 stack |
| 運算 | `OR`…`MOD` | 算術、位元、比較 |
| 系統 | `OPEN`、`READ`、`CLOS`、`PRTF`、`MALC`、`FREE`、`MSET`、`MCMP`、`EXIT` | 轉呼叫 C 程式庫 |

注意最後一群：c4 不實作 I/O，而是讓 VM 直接呼叫宿主的 `printf`、`malloc`。這正是 c4 能自我直譯的關鍵——它需要的程式庫功能，就是這 9 個。

### 實跑

```
$ git clone https://github.com/rswier/c4 && cd c4
$ gcc -w -o c4 c4.c
$ ./c4 -s hello.c
1: #include <stdio.h>
2:
3: int main()
4: {
5:   printf("hello, world\n");
    ENT  0
    IMM  542896144
    PSH
    PRTF
    ADJ  1
6:   return 0;
    IMM  0
    LEV
7: }
    LEV
$ ./c4 hello.c
hello, world
exit(0) cycle = 9
```

`-s` 在每行原始碼之後列出它產生的指令：`IMM` 載入字串位址、`PSH` 推上 stack 當引數、`PRTF` 呼叫 `printf`、`ADJ 1` 彈掉一個引數。`IMM` 後的數字是字串位址的**低 32 位元**，每次執行不同（c4 用 `malloc` 配置資料區，受 ASLR 影響）——它用 `%d` 印一個 `long long`，格式與引數型別不符本身就是 UB（N1570 7.21.6.1 §9），在 x86-64 上碰巧印出低 32 位元。`cycle = 9` 是 VM 執行的指令數。

`-w` 是必要的。c4 用 `#define int long long` 讓所有 `int` 變成 64 位元以支援 64 位元指標，副作用很多：

```
$ gcc -Wall -o c4w c4.c 2>&1 | grep -o '\[-W[a-z=-]*\]' | sort | uniq -c
     49 [-Wformat=]
      2 [-Wmain]
      3 [-Wparentheses]
$ clang -w -o c4c c4.c
c4.c:333:5: error: first parameter of 'main' (argument count) must be of type 'int'
int main(int argc, char **argv)
    ^
```

巨集也把 `main` 變成 `long long main(long long argc, ...)`。N1570 5.1.2.2.1 §1 只容許 `int main(void)`、`int main(int, char *[])` 或實作定義的形式；gcc 只警告（`-Wmain`），clang 14 直接拒絕。一個 500 行的教學程式，就能同時示範「巨集替換關鍵字」「格式字串 UB」「不同編譯器對同一規格條款的嚴格度不同」三件事。

用一個有函式呼叫的程式看 frame 指令：

```c
int fib(int n)
{
  if (n < 2) return n;
  return fib(n - 1) + fib(n - 2);
}

int main()
{
  printf("fib(20) = %d\n", fib(20));
  return 0;
}
```

```
$ ./c4 -s fib.c
3:   if (n < 2) return n;
    ENT  0
    LEA  2
    LI
    PSH
    IMM  2
    LT
    BZ   0
    LEA  2
    LI
    LEV
4:   return fib(n - 1) + fib(n - 2);
    LEA  2
    LI
    PSH
    IMM  1
    SUB
    PSH
    JSR  -1113284584
    ADJ  1
    ...
$ ./c4 fib.c
fib(20) = 6765
exit(0) cycle = 394044
```

- 參數 `n` 在 `bp + 2`：`bp + 0` 是舊的 bp、`bp + 1` 是回傳位址（`JSR` 推入）、`bp + 2` 起是引數。`LEA 2; LI` 就是「取 `n` 的位址、載入值」。
- `BZ 0` 的目標顯示為 0，不是錯誤：`-s` 在讀到行尾時就印出該行的指令，而 `if` 的跳躍目標要等 `stmt()` 處理完整個 then 分支、回頭**回填**時才寫入——和 `bf.c` JIT 的 backpatching 同一招。
- `JSR` 的運算元同樣是位址的低 32 位元。

### 一次讀懂四個函式的關鍵段落

**`next()`：詞法分析與符號表合一**。識別字一邊讀一邊算雜湊（`tk = tk * 147 + *p++`），再線性搜尋符號表 `sym`；找到就回傳它的 token 種類，沒找到就新增一筆。因為 c4 不支援 struct（它自己也不能用），符號表是一個 `int` 陣列，每個識別字佔 `Idsz` 格，以 `enum { Tk, Hash, Name, Class, Type, Val, HClass, HType, HVal, Idsz }` 當欄位偏移——用列舉值模擬 struct 成員。`#` 開頭的行整行略過，所以 `#include` 被當成註解。

**`expr(lev)`：優先序爬升（precedence climbing）**。token 的列舉值刻意依運算子優先序由低到高排列：

```c
enum {
  Num = 128, Fun, Sys, Glo, Loc, Id,
  Char, Else, Enum, If, Int, Return, Sizeof, While,
  Assign, Cond, Lor, Lan, Or, Xor, And, Eq, Ne, Lt, Gt, Le, Ge, Shl, Shr, Add, Sub, Mul, Div, Mod, Inc, Dec, Brak
};
```

`expr()` 先處理一元部分（常數、變數、`*`、`&`、`(`……），然後進入：

```c
  while (tk >= lev) { // "precedence climbing" or "Top Down Operator Precedence" method
    t = ty;
    ...
    else if (tk == Add) {
      next(); *++e = PSH; expr(Mul);
      if ((ty = t) > PTR) { *++e = PSH; *++e = IMM; *++e = sizeof(int); *++e = MUL;  }
      *++e = ADD;
    }
```

`while (tk >= lev)` 一次比較就決定要不要繼續吸收運算子：看到 `+` 時以 `expr(Mul)` 剖析右運算元，表示「右邊只吃優先序 ≥ `*` 的運算子」，`a + b * c` 因此正確結合成 `a + (b * c)`。C 標準本身沒有優先序表，優先序由文法層次表達；N1570 6.5 的註腳 85 寫明各子節的順序就是優先序由高到低。加法那段還順便做了**指標算術**：左運算元是 `int *` 時（`ty > PTR`），右運算元先乘以 `sizeof(int)`——[Ch 3](./03-pointer-basics.md) 的指標算術規則，在編譯器裡就是這四條指令。

**`stmt()`：敘述與回填**。`while` 的翻譯：

```c
  else if (tk == While) {
    next();
    a = e + 1;                       // 記下迴圈開頭
    ...
    expr(Assign);                    // 條件
    ...
    *++e = BZ; b = ++e;              // 條件為假時跳出，目標先留空
    stmt();                          // 迴圈主體
    *++e = JMP; *++e = (int)a;       // 跳回開頭
    *b = (int)(e + 1);               // 回填 BZ 的目標
  }
```

和 `bf.c` 的 `[`/`]` 處理一模一樣：前向跳躍留空、事後回填。c4 不建 AST，邊剖析邊輸出指令（single-pass），所以只能這樣做。

**`main()` 的 VM 迴圈**：一長串 `if (i == LEA) ... else if (i == IMM) ...`，每條指令一行：

```c
    if      (i == LEA) a = (int)(bp + *pc++);                             // load local address
    else if (i == IMM) a = *pc++;                                         // load global address or immediate
    else if (i == JSR) { *--sp = (int)(pc + 1); pc = (int *)*pc; }        // jump to subroutine
    else if (i == ENT) { *--sp = (int)bp; bp = sp; sp = sp - *pc++; }     // enter subroutine
    else if (i == LEV) { sp = bp; bp = (int *)*sp++; pc = (int *)*sp++; } // leave subroutine
    ...
    else if (i == ADD) a = *sp++ +  a;
    ...
    else if (i == PRTF) { t = sp + pc[1]; a = printf((char *)t[-1], t[-2], t[-3], t[-4], t[-5], t[-6]); }
```

`ENT`/`LEV` 就是 x86 的 `push rbp; mov rbp, rsp; sub rsp, n` 與 `leave; ret`，只是寫成 C。`PRTF` 不管格式字串要幾個引數，一律傳 6 個——多傳的引數本身是合法的（N1570 7.21.6.1 §2：格式用完後剩下的引數會被求值但忽略），真正的問題是前面提過的型別不符：VM 的值都是 `long long`，格式卻是 `%d`。

不支援的語法會在剖析時報錯：

```
$ cat for.c
int main()
{
  int i;
  for (i = 0; i < 3; i++) printf("%d", i);
  return 0;
}
$ ./c4 for.c; echo "rc=$?"
4: bad expression
rc=255
```

`for` 不是 c4 的關鍵字，被當成識別字；`for (` 看起來像函式呼叫，`expr()` 開始剖析引數，碰到括號內的第一個 `;` 時無法構成運算式而報錯。訊息只說「bad expression」，沒告訴你「不支援 for」——錯誤訊息不精確是單趟、無 AST 設計的典型代價。

### 自我直譯：每疊一層，指令數暴增

```
$ ./c4 c4.c hello.c
hello, world
exit(0) cycle = 9
exit(0) cycle = 26015
$ time ./c4 c4.c c4.c hello.c
hello, world
exit(0) cycle = 9
exit(0) cycle = 26015
exit(0) cycle = 10060183

real	0m0.052s
```

```
 gcc 編出的 c4（原生機械碼）               印出 cycle = 10,060,183
   └─ 直譯 c4.c 的 VM 指令                  印出 cycle = 26,015
        └─ 直譯 c4.c 的 VM 指令             印出 cycle = 9
             └─ 直譯 hello.c 的 VM 指令     印出 hello, world
```

每一行 `exit(...)` 由不同層的 c4 印出，最內層先印。外層的數字包含被直譯的那個 c4「編譯下一層原始碼」的工作，不只是重複內層的指令，但趨勢很清楚：每疊一層直譯，外層的指令數增加兩到三個數量級。這和 `bf -i` 比 `bf -j` 慢十幾倍是同一件事——直譯的每一步都要付分派成本，疊起來就是乘法。

c4 不做任何最佳化，產生的指令和原始碼一一對應。要產生真實處理器的機械碼並最佳化，就需要 shecc 這種事先編譯器。

## 編譯器的規模

原文以 cloc 1.98 統計 GCC 整個原始碼套件（扣除空行與註解，含測試、執行期程式庫與所有語言前端）：

| 版本 | 釋出日期 | 程式碼行數 | 最大宗語言 |
|---|---|---:|---|
| GCC 2.8.0 | 1998-01-07 | 527,723 | C |
| GCC 8.2.0 | 2018-07-26 | 7,933,248 | C |
| GCC 16.1.0 | 2026-04-30 | 13,754,065 | C++ |

（以上為原文的量測，本章未重做。）GCC 4.8 起改以 C++ 撰寫 GCC 本身，到 16.1.0 時 C++ 成為最大宗。本章各程式的規模（`wc -l`，含空行與註解，與 cloc 口徑不同）：

| 程式 | 行數 | 能做什麼 |
|---|---:|---|
| `bf.c` | 206 | Brainfuck 直譯 + 翻 C + x86-64 JIT |
| c4 | 528 | C 子集 → VM 指令，直譯，可自我直譯 |
| shecc（`src/` + `lib/`，`0f0f4f8`） | 60,159 | C 子集 → SSA → 四種架構的 ELF，可自我編譯 |

原文記錄 shecc 在 2020 年 8 月匯入時只有 Arm 後端、約三千行；到 2026 年已有四個後端與 SSA 最佳化器，仍不到 GCC 2.8.0 的十分之一。從 200 行到 1300 萬行之間的差距，是語言完整度、目標平台數、最佳化深度、診斷品質與數十年相容性的累積。

## 編譯器與軟體可靠度

編譯器本身的錯誤會直接進入每一個用它編出的產品。原文引述美國國家研究委員會 2010 年的報告：由軟體實現的戰機系統功能比例，從 1960 年 F-4 的 8%、1982 年 F-16 的 45%，增加到 2000 年 F-22 的 80%；GAO 2012 年的報告稱 F-35 需要開發與整合的程式碼超過 2400 萬行。這些程式碼全都要經過編譯器。

Yang、Chen、Eide、Regehr 在 PLDI 2011 的〈Finding and Understanding Bugs in C Compilers〉用 **Csmith** 隨機產生**不含未定義行為**的 C 程式，交給多個編譯器（或同一編譯器的不同最佳化等級）編譯執行，比較輸出——這叫**差異測試（differential testing）**。結果：三年間回報了超過 325 個先前未知的錯誤，每一個受測的編譯器都會當機，也都會對合法輸入**靜默地產生錯誤的程式碼**。

為什麼 Csmith 必須避開 UB？因為對含 UB 的程式，兩個編譯器輸出不同是合法的（Ch 37 的 `gt_plus1` 在 `-O0` 與 `-O2` 答案不同就是例子），比對失去意義。Csmith 產生程式時會插入保護（例如以安全的包裝函式做有號運算），確保程式在抽象機器上有唯一定義的結果。

論文中最有意思的對照是 **CompCert**——以 Coq 證明輔助工具開發、證明了「編譯後的程式與原始碼語意相同」的 C 編譯器。Csmith 在 CompCert 裡也找到錯誤，但全部位在**未經證明的部分**（前端與一處組譯器相關的語意描述），經過證明的中段一個錯誤也沒有。論文寫道，截至 2011 年初，開發中的 CompCert 是唯一一個找不到 wrong-code 錯誤的受測編譯器——「This is not for lack of trying: we have devoted about six CPU-years to the task.」

```
 Csmith ──► 隨機、無 UB 的 C 程式 ──┬──► gcc -O0 ──► 執行 ──► 檢查碼 A
                                    ├──► gcc -O2 ──► 執行 ──► 檢查碼 B   ──► A, B, C 不一致？
                                    └──► clang -O2 ─► 執行 ──► 檢查碼 C        → 至少一個編譯器錯了
```

## shecc 的編譯管線

[shecc](https://github.com/sysprog21/shecc) 只接受 C 的子集（沒有浮點型別），但足以編譯自身。它把 Ch 36 的所有工站塞進**一個執行檔**，從 C 原始碼直接產生 Armv7-A、AArch64、RV32IM 或 x86-64 的 Linux ELF，不呼叫 `as` 也不呼叫 `ld`。依 `docs/vir.md` 與目前的 `src/` 目錄結構整理：

```
 C 原始碼 + 內建精簡 libc（lib/c.c）
        │
        ▼
 src/frontend/   前置處理（preprocessor.c）、詞法（lexer.c）、
                 語法分析（parser/）——不建完整 AST，讀 token 直接產生 VIR
        │  每個函式解析完立刻往下送
        ▼
 src/optimizer/  VIR：SSA 形式的 CFG，block parameter 取代 φ
                 inlining、SCCP、CFG 化簡、CSE、GVN、LICM、強度折減、DCE……
                 每個函式最佳化後做完整的圖形驗證，壓縮存入 arena
        │
        ▼
 lower.c         四個後端共用：呼叫慣例、暫存器配置、spill、peephole
        │
        ▼
 src/codegen/    arm / arm64 / riscv / x64：指令選擇與編碼
        │
        ▼
 elf.c           直接寫出 ELF（靜態，或 --dynlink 連結系統 glibc）
```

和 c4 一樣不建 AST，和 clang 一樣先把區域變數記成 load/store、函式解析完再把位址沒外流的變數升為 SSA 值（Ch 37 的 mem2reg）。「每個函式最佳化完就壓縮存起來」讓整個編譯單元不必同時保有所有函式的圖，記憶體用量可控。

### 建置與自我編譯

```
$ git clone https://github.com/sysprog21/shecc && cd shecc
$ make config ARCH=x64
Configurations: (x64, static linking)
$ make            （輸出中 shecc 編譯自身時的 [Warning] 省略）
$ ls -l out/
-rwxr-xr-x 1 ypp ypp 3303696 Nov 20 00:50 shecc
-rwxr-xr-x 1 ypp ypp  712712 Nov 20 00:50 shecc-stage1.elf
-rwxrwxr-x 1 ypp ypp  712712 Nov 20 00:50 shecc-stage2.elf
...
$ cmp out/shecc-stage1.elf out/shecc-stage2.elf && echo identical
identical
$ file out/shecc-stage2.elf
out/shecc-stage2.elf: ELF 64-bit LSB executable, x86-64, version 1 (SYSV), statically linked, no section header
```

建置分三階段：

```
 shecc 原始碼 ──[gcc]──► stage 0：out/shecc（gcc 編出的 x86-64 執行檔，3.3 MB）
 shecc 原始碼 ──[stage 0]──► stage 1：shecc-stage1.elf（shecc 編出的 shecc）
 shecc 原始碼 ──[stage 1]──► stage 2：shecc-stage2.elf（shecc 編出的 shecc 編出的 shecc）
                                         cmp stage1 stage2 → 逐位元組相同
```

stage 1 與 stage 2 都是 shecc 的輸出；兩者相同，代表編譯器達到**不動點（fixed point）**：「gcc 編出的 shecc」與「shecc 編出的 shecc」對同一份原始碼產生完全相同的結果。這是一個很強的一致性檢查——若 shecc 的程式碼產生有錯，stage 1（由錯誤的 shecc 編出）的行為很可能和 stage 0 不同，stage 2 就會和 stage 1 不同。原文〈編譯器和最佳化原理篇〉談的 self-hosting 指的就是這件事：C# 團隊用 C# 重寫 C# 編譯器（Roslyn），也是追求這種「吃自己的狗食」的里程碑。

`file` 說 `no section header`：Ch 36 講過，載入時用到的只有 program header，section 是給連結器與除錯器的。shecc 自己寫 ELF，就乾脆不寫 section header。

### 看 VIR：block parameter 取代 φ

shecc 的 inliner 會把「定義在呼叫者之前」的小函式展開並刪除，所以觀察用的函式要**宣告在前、定義在後**（`docs/vir.md` 說明這是測試保留呼叫的方法）：

```c
/* sel.c */
int select_and_store(int condition, int a, int b, int *out);

int main(void)
{
    int x;
    int r = select_and_store(1, 41, 7, &x);
    return r - x;
}

int select_and_store(int condition, int a, int b, int *out)
{
    int selected;
    if (condition)
        selected = a;
    else
        selected = b;
    *out = selected;
    return selected + 1;
}
```

```
$ ./out/shecc --dump-ir -o sel sel.c
function select_and_store
b0(%d0:i32, %d2:i32, %d4:i32, %d6:ptr):
  jump b1()
b1:
  %d10 = const.i32 0
  %d11 = eq.i32 %d0, %d10
  branch %d11, b3(), b2()
b2:
  jump b4(%d2)
b3:
  jump b4(%d4)
b4(%d22:i32):
  store %d6, %d22
  %d25 = const.i32 1
  %d27 = add.i32 %d25, %d22
  return %d27
function main
b0:
  ...
  %d4 = call.i32 @select_and_store(%d0, %d1, %d2, %d3) sig(i32, i32, i32, ptr)->i32
  ...
$ ./sel; echo "rc=$?"
rc=1
```

（最早的版本把 `select_and_store` 定義在 `main` 之前；在 `0f0f4f8` 上它會被 inline 進 `main` 並從輸出消失，`main` 摺成 `return 1`。這是版本差異的一個具體例子。）

`b4(%d22:i32)` 是帶參數的基本區塊：`b2` 跳過來時傳 `%d2`（`a`），`b3` 跳過來時傳 `%d4`（`b`）。這和 Ch 37 的 φ 完全等價——`%d22 = φ(%d2 from b2, %d4 from b3)`——只是把「從哪條邊來就用哪個值」寫在**跳躍指令上**，而不是寫在合流區塊開頭。MLIR、Cranelift、Swift SIL 都採用這種設計。好處是增刪或改接一條邊時，引數跟著邊走，不必另外維護 φ 運算元與前驅區塊的對應。

對照 `--no-opt`：

```
$ ./out/shecc --no-opt --dump-ir -o sel0 sel.c
function select_and_store
b0(%d0:i32, %d2:i32, %d4:i32, %d6:ptr):
  %d1 = stackaddr 0, 4, align 4
  %d3 = stackaddr 1, 4, align 4
  %d5 = stackaddr 2, 4, align 4
  %d7 = stackaddr 3, 8, align 8
  %d16 = stackaddr 4, 4, align 4
  jump b1()
b1:
  %d10 = const.i32 0
  %d11 = eq.i32 %d0, %d10
  %d12 = const.i1 0
  %d13 = eq.i32 %d11, %d12
  branch %d13, b2(), b3()
b2:
  jump b4(%d2)
b3:
  jump b4(%d4)
b4(%d22:i32):
  store %d6, %d22
  %d24 = load.i32 %d6
  %d25 = const.i32 1
  %d27 = add.i32 %d25, %d22
  return %d27
```

兩個版本都已經沒有 `selected` 的 load/store——提升成 SSA 值是建構時的必要步驟，不算可選最佳化。最佳化額外做了三件事：刪除沒人用的 stack 位置（`stackaddr`）、把 `(condition == 0) == 0` 化簡成一次比較並對調分支目標、刪除 `store` 之後沒人用的 `load`。

### 迴圈：強度折減做了，LICM 卻沒做

```c
/* loop.c（同樣：宣告在前、定義在後） */
int scale_sum(int *a, int n, int k)
{
    int s = 0;
    for (int i = 0; i < n; i++)
        s += a[i] * (k * 4);
    return s;
}
/* main：int v[4] = {1, 2, 3, 4}; return scale_sum(v, 4, 1); */
```

```
$ ./out/shecc --dump-ir -o loop loop.c
function scale_sum
b0(%d0:ptr, %d2:i32, %d4:i32):
  %d6 = const.i32 0
  jump b1()
b1:
  %d8 = const.i32 0
  jump b2(%d8, %d6, %d0)
b2(%d11:i32, %d44:i32, %d53:ptr):
  %d14 = slt.i32 %d11, %d2
  branch %d14, b5(), b3()
b3:
  return %d44
b4:
  %d20 = const.i32 1
  %d23 = add.i32 %d20, %d11
  %d52 = const.i64 4
  %d54 = ptradd %d53, %d52
  jump b2(%d23, %d41, %d54)
b5:
  %d33 = load.i32 %d53
  %d49 = const.i32 2
  %d50 = shl.i32 %d4, %d49
  %d38 = mul.i32 %d33, %d50
  %d41 = add.i32 %d38, %d44
  jump b4()
$ ./loop; echo "rc=$?"
rc=40
```

迴圈標頭 `b2` 的三個參數是 `i`、`s` 與指向 `a[i]` 的指標 `%d53`。對照 `--no-opt` 版本（每圈 `sext` 把 `i` 延伸成 64 位元、乘以 4、加到 `a` 上算 `&a[i]`），最佳化後：

- **強度折減**：指標本身成為歸納變數，每圈 `ptradd 4`，`load` 直接讀它——和 Ch 37 gcc 的 `licm` 函式一樣。
- **條件化簡**：`--no-opt` 的 `i < n` 經過 `zext`、兩次 `eq` 才得到分支條件，最佳化後只剩一道 `slt`。
- **`k * 4` 被正規化成 `k << 2`**（`shl`），但**仍在迴圈主體 `b5` 裡**，每圈重算——LICM 沒有把它提出去。

原文用 2026-10 的版本詳細追了這個現象：LICM 只搬移有運算元的值，而常數 `4`（現在是移位量 `2`）由前端在使用處產生、位於迴圈內又不會被搬出，依賴它的運算也就無法外提；把常數換成第四個參數 `j`（`k * j`）時就能外提。原文還記錄了另一層原因：SSA 建構時迴圈不變的 `k` 先成了標頭的參數、由回邊原樣傳回，LICM 把它誤認為迴圈內定義的值；一個把「參數化簡」提到 LICM 之前的 commit 修掉了這一層。我們在 `0f0f4f8` 上看到的是：運算從 `mul` 變成 `shl`，但仍留在迴圈內，與原文對常數運算元的描述一致。

這是典型的 **pass 順序（phase ordering）**問題：每個 pass 單獨看都正確，效果卻取決於前面的 pass 把圖整理到什麼程度。

### 自我編譯與信任：Thompson 與 DDC

不動點證明了什麼？只證明 shecc 對自身原始碼的行為一致。它**不能**證明 shecc 沒有後門。

Ken Thompson 在 1983 年圖靈獎演說〈Reflections on Trusting Trust〉（CACM 1984）描述了一個三步驟的思想實驗：

```
 第 1 步：在 C 編譯器的原始碼裡加入：「如果正在編譯 login.c，就插入一個萬用密碼後門」
 第 2 步：再加入：「如果正在編譯 C 編譯器自己，就把第 1 步和第 2 步的程式碼插進去」
 第 3 步：用這個編譯器編譯出執行檔，然後從原始碼刪掉這兩段

 結果：原始碼乾乾淨淨，但用這個執行檔編出的每一代編譯器都帶著後門，
       每一代編出的 login 也都帶著後門。審查原始碼找不到任何痕跡。
```

演說的結論是：「You can't trust code that you did not totally create yourself.」若 stage 0 的 gcc 已被植入這種後門，shecc 的不動點照樣成立，後門照樣一代傳一代。

David A. Wheeler 的 **diverse double-compiling（DDC）**（ACSAC 2005，後為其博士論文）提供了檢驗方法：用**另一個獨立的編譯器**編譯同一份原始碼，再讓產物編譯原始碼一次；若結果與待查的執行檔逐位元組相同，二者就對應同一份原始碼——除非兩個獨立的編譯器被植入了**相互配合**的後門。shecc 的 stage 0 本來就由別的編譯器產生，換用不同的編譯器與最佳化等級即可直接套用：

```bash
#!/bin/bash
# 在 shecc 目錄執行：以不同編譯器建置 stage 0，比較各自產生的 stage 1
set -euo pipefail
mkdir -p ddc
for cc in "gcc -O2" "gcc -O0" "clang -O2"; do
    tag=$(echo "$cc" | tr -d ' -')
    $cc -w -o ddc/stage0-$tag src/main.c
    ddc/stage0-$tag -o ddc/stage1-$tag.elf src/main.c > /dev/null
done
sha256sum ddc/* out/shecc-stage1.elf out/shecc-stage2.elf
for f in ddc/stage1-*.elf out/shecc-stage2.elf; do
    cmp out/shecc-stage1.elf "$f"
done
echo "all identical"
```

本機結果（SHA-256 取前 12 個十六進位字）：

```
69282ffbbe60 ddc/stage0-clangO2        （1,047,936 位元組）
5e3b2aa5a72e ddc/stage0-gccO0          （826,264 位元組）
26fb64665117 ddc/stage0-gccO2          （770,656 位元組）
721ac31b4a67 ddc/stage1-clangO2.elf
721ac31b4a67 ddc/stage1-gccO0.elf
721ac31b4a67 ddc/stage1-gccO2.elf
721ac31b4a67 out/shecc-stage1.elf
721ac31b4a67 out/shecc-stage2.elf
all identical
```

三個 stage 0 的機械碼各不相同（大小差了快 30 萬位元組），產生的 stage 1 卻逐位元組相同，也與建置流程中的 stage 1、stage 2 相同。DDC 的結論仍有前提，原文也特別強調：用來對照的編譯器必須**真正獨立**且可信——同一個 gcc 換最佳化等級並不構成獨立的信任來源，gcc 與 clang 才勉強算兩個；兩個 stage 0 也要對 shecc 原始碼的語意有相同的理解。

**可重現建置的陷阱：時間戳記**。shecc 的 `config` 檔記錄建置時間：

```
$ grep TIME config
#define SHECC_TRANSLATION_TIME "16:50:14"
```

shecc 把它編進自身，作為所編譯程式中 `__DATE__`、`__TIME__` 的值。同一台機器、同一次 `make config` 之內，各個 stage 共用這份 `config`，所以上面的比對成立；但原文發現，在兩台機器上（x86-64 交叉建置 vs. arm64 原生建置）比對 stage 1 時，差異就出在這個時間戳。N1570 6.10.8.1 §1 規定 `__DATE__`/`__TIME__` 是「轉譯」的日期時間，shecc 給的卻是它自己的建置時間。gcc 與 clang 會讀環境變數 `SOURCE_DATE_EPOCH` 取代目前時間；原文設定它之後，兩台機器的 stage 1 雜湊一致。跨機器的這部分本章**未實測**。可重現建置（reproducible build）要求固定所有輸入，時間戳記是最常被遺漏的一項。

### 程式碼產生、ABI 與 ELF

lowering 階段以 SSA 值的實際使用建立保守的存活區間，指派暫存器與 spill 位置；block parameter 在每條邊上解析成一次平行複製（parallel copy），來源與目的已在同一個位置時不產生指令。四個後端分別遵循 32 位元 Arm 的 AAPCS、AArch64 的 AAPCS64、RISC-V 的 RV32 慣例與 x86-64 的 System V ABI——正是 Ch 36「Procedures」那張表的各列。`config` 裡可以看到這些參數直接寫成巨集：

```
#define ELF_MACHINE 0x3e /* AMD x86-64 architecture */
#define PTR_SIZE 8 /* 64-bit pointer size */
#define MAX_ARGS_IN_REG 6 /* System V: rdi rsi rdx rcx r8 r9 */
#define DYN_LINKER "/lib64/ld-linux-x86-64.so.2"
```

以 `tests/hello.c`（兩個 `printf`）比較執行檔大小（x86-64，單位位元組）：

| 產生方式 | 大小 |
|---|---:|
| shecc，靜態（內建精簡 libc） | 8,200 |
| shecc，`--dynlink` | 1,008 |
| gcc 11.4 `-O1 -static`（靜態連結 glibc） | 900,384 |
| gcc 11.4 `-O1`（動態） | 16,008 |

靜態版 gcc 比 shecc 大一百倍以上，因為它帶進了 glibc 的 stdio、locale、啟動碼等大量程式碼；shecc 的 `lib/c.c` 只實作自己需要的部分。原文另外量了 arm64（shecc 靜態版 65,544 位元組，因為可執行與可寫入 segment 以 64 KiB 隔開以相容 4/16/64 KiB 分頁的核心），本章沒有 arm64 實體機，未重做。

`--dynlink` 版本的 program header：

```
$ ./out/shecc --dynlink -o hd tests/hello.c
$ readelf -lW hd | grep -E 'LOAD|INTERP|DYNAMIC|RELRO|STACK'
  LOAD           0x000000 0x0000000000010000 0x0000000000010000 0x000238 0x000238 R E 0x1000
  LOAD           0x000238 0x0000000000011238 0x0000000000011238 0x0001b8 0x0001c0 RW  0x1000
  INTERP         0x000238 0x0000000000011238 0x0000000000011238 0x00001c 0x00001c R   0x1
  DYNAMIC        0x000318 0x0000000000011318 0x0000000000011318 0x0000d0 0x0000d0 RW  0x8
$ readelf -dW hd | grep -E 'NEEDED|PLTGOT|JMPREL'
 0x0000000000000017 (JMPREL)             0x101b0
 0x0000000000000003 (PLTGOT)             0x11258
 0x0000000000000001 (NEEDED)             Shared library: [libc.so.6]
$ readelf -lW hg | grep -E 'RELRO|STACK'      （gcc 動態連結版，對照）
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
  GNU_RELRO      0x002db0 0x0000000000003db0 0x0000000000003db0 0x000250 0x000250 R   0x1
```

兩個 LOAD segment（程式碼 `R E`、資料 `RW`）、一個 INTERP（指向動態連結器）、一個 DYNAMIC（`NEEDED libc.so.6`、PLT/GOT 的位置）——這是一個能動態連結的 ELF 的最小骨架，比 gcc 產出的 13 個 program header 精簡得多。少掉的兩個值得注意：

- **沒有 `GNU_RELRO`**：GOT 在一般的可寫 segment，執行期可被改寫——攻擊者若有任意寫入原語，就能改 GOT 劫持控制流程。這是 shecc 文件列出的已知限制；RELRO 的用途在 [Ch 40 — 動態連結](./40-dynamic-linking.md)。
- **沒有 `GNU_STACK`**：這個 header 平常由連結器依 `.note.GNU-stack` 寫出（上面 `bf -c` 那節），shecc 自己寫 ELF，沒有產生它。依原文的說明，Linux v5.8 之前，x86-64 與 arm64 對沒有這個 header 的執行檔設定 `READ_IMPLIES_EXEC`（所有可讀映射都可執行）；v5.8 起 64 位元行程不再如此。本章未在舊核心上驗證這個差異。

### 效能：編譯速度

量測 shecc stage 1 與 gcc 編譯 shecc 自身（`src/main.c`，含連結）的時間，`taskset -c 6`、各 5 次取中位數：

| 編譯器 | 時間 |
|---|---:|
| shecc（stage 1） | 706 ms |
| gcc 11.4 `-O0` | 1,801 ms |
| gcc 11.4 `-O1` | 4,730 ms |

shecc 比 gcc `-O1` 快 6.7 倍，也比不做最佳化的 gcc `-O0` 快 2.6 倍。這是兩種**完整編譯流程**的比較，沒有分階段計時：gcc 要依序啟動 `cc1`、`as`、`collect2`、`ld` 等行程（Ch 36 的 strace），剖析 glibc 的標頭，即使 `-O0` 也要經過 GENERIC、GIMPLE、RTL 數層表示；shecc 在單一行程內讀入精簡 libc、直接寫出 ELF。差距是這些因素的總和，個別比重未量測。

產生的程式碼品質方面，原文以 shecc 附的 `tests/native-throughput.py`（七個合成工作負載）量得 shecc 達到 gcc `-O1` 的 95% 以上；這組工作負載由 shecc 專案設計、用來檢驗 VIR 最佳化是否生效，不包含 gcc `-O2` 的向量化與跨函式 inline，數字不代表一般程式的效能。本章未重做這項量測。

## 對比與取捨

| 方式 | 啟動成本 | 執行速度 | 實作難度 | 可攜性 | 代表 |
|---|---|---|---|---|---|
| 逐字元 / AST 直譯 | 無 | 最慢 | 最低 | 只要能編譯直譯器 | 早期 BASIC、shell |
| IR / bytecode 直譯 | 剖析一次 | 慢（受分派成本限制） | 低 | 高 | c4、CPython、Lua |
| 翻成 C 再編譯 | 呼叫外部編譯器（秒級） | 快（繼承 gcc 最佳化） | 低 | 高（凡有 C 編譯器處） | Cfront、Nim、`bf -c` |
| 直接產生組語 / ELF | 低 | 取決於自己的最佳化 | 高（每個架構一個後端） | 每個架構要重寫 | shecc、tcc |
| JIT | 中（執行期編譯） | 快，可利用執行期資訊 | 最高（W^X、I-cache、ABI、去最佳化） | 每個架構要重寫 | HotSpot、V8、LuaJIT、`bf -j` |

## 踩雷集錦

1. **「預先算好跳躍目標，直譯器就會快很多」**：本章實測 `-i` 反而比 `-n` 慢約 10%。直譯器的成本大頭在每個指令的「讀取 + 分派」，減少指令數（收縮）的效果遠大於減少跳躍成本。
2. **「JIT 一定比 AOT 快」**：我們的 JIT 輸給 `gcc -O2`（0.77 s 對 0.56 s），因為它沒有暫存器配置與指令選擇。JIT 的優勢在「不用另外跑一次編譯器」以及「能用執行期資訊」，不是天生較快。
3. **`mmap(PROT_READ | PROT_WRITE | PROT_EXEC)` 一次搞定**：違反 W^X，在強制此規則的系統上直接失敗，而且同時可寫可執行的頁面是攻擊者最愛的落腳處。先寫入、再 `mprotect` 改成可執行。
4. **在 x86-64 上能跑，就以為 AArch64 也沒問題**：AArch64 需要 `__builtin___clear_cache` 同步指令快取；QEMU 可能掩蓋這個錯誤，要在真實硬體上驗證。
5. **「stage 1 == stage 2，所以編譯器可信」**：不動點只證明自我一致。Thompson 式後門在每一代都會重現；要用 DDC，而且對照的編譯器必須真正獨立。
6. **差異測試時沒排除 UB**：兩個編譯器對含 UB 的程式輸出不同是合法的，比對會產生大量假警報。Csmith 刻意產生無 UB 的程式就是為此。
7. **用 `-w` 壓掉警告後就當沒事**：c4 的 49 個 `-Wformat` 警告是真實的 UB（`%d` 印 `long long`），只是在 x86-64 上碰巧無害。

## 進階：再往深一層

- **把 bf.c 推到 gcc 的水準**：迴圈改寫成乘法（`[->+++<]` → `p[1] += p[0] * 3`）、把指標位移併進後續存取的偏移（`addb $8, 1(%rbx)`）、常用的格留在暫存器、`[>>>>]` 這類掃描迴圈改用 `memchr` 式的掃描 op。原文對每一項都有量測。
- **DynASM**：手寫位元組容易出錯。LuaJIT 的 DynASM 讓你在 C 裡以 `|` 開頭的行寫組合語言，建置時轉成 C，執行期由 `dasm_*` 函式編碼並處理標籤回填；jit-construct 與 Josh Haberman 的文章都用它實作 Brainfuck JIT。
- **讀 c4 的衍生作品**：[write-a-C-interpreter](https://github.com/lotabout/write-a-C-interpreter) 以中文逐步講解如何寫出 c4；jserv 的 [AMaCC](https://github.com/jserv/amacc) 在 c4 的基礎上加入 AST 與 Arm 機械碼產生。
- **CompCert 與形式化驗證**：Xavier Leroy 的〈Formal Verification of a Realistic Compiler〉說明如何以 Coq 證明「編譯不改變語意」，是 Csmith 結果的另一半故事。
- **Bootstrappable builds**：GNU Mes、stage0 等專案試圖從幾百位元組的手寫機械碼一路自舉出完整的 gcc，徹底切斷 Thompson 式的信任鏈。

## 動手練習

1. 在 `bf.c` 加上 `-O2`：在 `]` 檢查剛結束的迴圈，若主體只有 `ADD`/`MOVE`、指標淨位移為 0、目前格每圈淨減 1，就改寫成一串 `MUL off, k` 加一個 `CLEAR`。思考：目前格為 0 時原迴圈不執行，但改寫後的 `MUL` 仍會讀寫目標格——若目標格在陣列左邊界之外怎麼辦？（原文的解法是陣列兩端多配置 margin 格。）量測 `mandelbrot.b` 的改善。
2. 把 JIT 的 `MOVE` 改成：位移在 −128 到 127 之間時用 `48 83 c3 imm8` 的短編碼。用 `objdump -b binary` 確認編碼正確，並比較程式碼大小與執行時間。
3. 用 `./c4 -d hello.c`（debug 模式）印出每一條被執行的 VM 指令，對照 `-s` 的輸出，畫出 `printf` 呼叫前後 stack 與 `bp` 的變化。
4. 在 shecc 上把 `loop.c` 的 `k * 4` 改成 `k * j`（`j` 為第四個參數），用 `--dump-ir` 確認乘法是否被提到迴圈外；再試 `--dot` 輸出 CFG，用 Graphviz 畫出來。
5. 在 shecc 目錄執行 `make config ARCH=arm64 && make`，用 `qemu-aarch64` 執行 `out/shecc-stage2.elf` 編譯一個測試程式，確認交叉建置的 stage 1 與 stage 2 也達到不動點。

## 本章重點整理

- 直譯器、AOT 編譯器、JIT 共用「前端 → IR → 後端」的骨架，差別在翻譯的時機與 IR 的豐富程度。
- 直譯器的效能受「每個指令的分派成本 × 執行的指令數」支配；收縮這類減少指令數的最佳化最划算。JIT 消除分派成本，但沒有暫存器配置與指令選擇時仍輸給成熟的 AOT 編譯器。
- JIT 的三個平台細節：W^X（先寫再 `mprotect`）、指令快取同步（AArch64 需要 `__builtin___clear_cache`）、物件指標轉函式指標不是標準 C。
- c4 以累加器 + stack 的 VM、優先序爬升與回填，在約 500 行內實現能自我直譯的 C 子集編譯器。
- shecc 以 SSA（block parameter）IR、多個最佳化 pass 與共用 lowering，在單一執行檔內完成到 ELF 的全部工作；stage 1 == stage 2 證明自我一致，DDC 以獨立編譯器交叉驗證，但兩者都有前提。
- 編譯器本身會錯；Csmith 以無 UB 的隨機程式做差異測試，找出了所有受測編譯器的錯誤，只有 CompCert 經過證明的部分倖免。

## 自我檢核

- [ ] 不看程式碼，說出 `bf.c` 的 JIT 如何處理 `[` 的前向跳躍；如果改成「遇到 `]` 時從頭掃描找配對的 `[`」，會有什麼問題？
- [ ] 有人說「我們的 JIT 比 `gcc -O2` 慢，所以 JIT 沒用」，你能用本章的數字反駁並說明 JIT 適用的情境嗎？
- [ ] 能解釋 c4 的 `expr(Mul)` 為什麼能讓 `a + b * c` 正確結合，以及 `while (tk >= lev)` 中的 `lev` 扮演什麼角色嗎？
- [ ] 不看筆記，畫出 shecc 的三階段建置，並說明「stage 1 與 stage 2 相同」證明了什麼、**沒有**證明什麼。
- [ ] 如果你負責一個安全關鍵專案的工具鏈，會怎麼組合 DDC、可重現建置與差異測試？每一項防的是哪一種風險？
- [ ] 能說出 block parameter 和 φ 函數在語意上的對應關係，以及 block parameter 在改接 CFG 邊時的實務好處嗎？

## 延伸閱讀

### 論文

- **[Reflections on Trusting Trust](https://www.cs.cmu.edu/~rdriley/487/papers/Thompson_1984_ReflectionsonTrustingTrust.pdf)** — Ken Thompson，CACM 27(8)（1984）
  - **核心貢獻**：以三個階段的程式說明編譯器可以把後門傳給自己的下一代，且原始碼不留痕跡
  - **讀哪裡**：全文只有三頁，Stage I–III 與最後的 Moral
  - **和本章的關聯**：解釋了為什麼 shecc 的不動點不等於可信，以及 DDC 想解決什麼
- **[Countering Trusting Trust through Diverse Double-Compiling](https://dwheeler.com/trusting-trust/)** — David A. Wheeler，ACSAC（2005）
  - **核心貢獻**：以一個獨立的編譯器重新編譯兩次，檢驗執行檔是否對應宣稱的原始碼；給出成立的前提條件
  - **讀哪裡**：ACSAC 論文的 Section 3–4（方法與前提）；後來的博士論文有形式化證明，可選讀
  - **和本章的關聯**：本章 DDC 腳本的理論依據
  - **前提**：讀過 Thompson 的演說
- **[Finding and Understanding Bugs in C Compilers](https://users.cs.utah.edu/~regehr/papers/pldi11-preprint.pdf)** — Xuejun Yang, Yang Chen, Eric Eide, John Regehr，PLDI（2011）
  - **核心貢獻**：Csmith 的設計（如何產生無 UB 的隨機 C 程式）與三年間在 gcc、LLVM 等編譯器找到的 325+ 個錯誤
  - **讀哪裡**：Section 2（Csmith 如何避開 UB）與 Section 3（結果，含 CompCert 的部分）
  - **和本章的關聯**：本章「編譯器與軟體可靠度」一節的原始資料；也呼應 Ch 34 的 UB
- **[Formal Verification of a Realistic Compiler](https://xavierleroy.org/publi/compcert-CACM.pdf)** — Xavier Leroy，CACM（2009）
  - **核心貢獻**：CompCert 的整體架構與證明策略——每個 pass 證明語意保持，組合起來得到整個編譯器的正確性
  - **讀哪裡**：Section 1–3；證明的技術細節可略讀
  - **和本章的關聯**：Csmith 找不到 CompCert 中段錯誤的原因

### 部落格 / 技術文章

- **[Interpreter, Compiler, JIT](https://nickdesaulniers.github.io/blog/2015/05/25/interpreter-compiler-jit/)** — Nick Desaulniers（2015）
  - **這篇說什麼**：以 Brainfuck 依序寫直譯器、輸出組語的編譯器與 JIT，是 jserv 投影片與本章 `bf.c` 的源頭
  - **讀哪裡**：整篇；JIT 那段對照本章的「逐位元組解剖」
  - **為什麼值得讀**：作者是 LLVM/Linux 核心的編譯器工程師，篇幅短、程式碼完整
- **[Adventures in JIT compilation](https://eli.thegreenplace.net/2017/adventures-in-jit-compilation-part-1-an-interpreter/)** — Eli Bendersky（2017，四篇系列）
  - **這篇說什麼**：同樣以 Brainfuck 為例，從直譯器一路做到手寫 x86-64 JIT 與 LLVM JIT，每一步都有量測
  - **讀哪裡**：Part 1（直譯器最佳化）與 Part 2（x86-64 JIT）
  - **為什麼值得讀**：本章「量測」一節的延伸；他對直譯器分派成本的分析比本章深入
- **[How to JIT - an introduction](https://eli.thegreenplace.net/2013/11/05/how-to-jit-an-introduction)** — Eli Bendersky（2013）
  - **這篇說什麼**：JIT 的最小核心（`mmap`、寫入、改權限、呼叫）以及為什麼要分兩步改權限
  - **讀哪裡**：整篇
  - **為什麼值得讀**：本章 `jit_add.c` 的完整解說版

### 官方文件 / 原始碼

- **[shecc 的 `docs/vir.md`](https://github.com/sysprog21/shecc/blob/master/docs/vir.md)**
  - **讀哪裡**：最佳化排程（pass 的順序清單）、inlining 的條件、`--dump-ir`/`--no-opt` 的說明
  - **注意**：shecc 演進很快，文件與程式碼以你 clone 的版本為準
- **[c4.c](https://github.com/rswier/c4/blob/master/c4.c)** — Robert Swierczek
  - **讀哪裡**：依序讀 `next()`、`expr()` 的 `while (tk >= lev)` 迴圈、`stmt()`、`main()` 末尾的 VM 迴圈；全檔 528 行，一個下午讀得完
  - **前提**：熟悉指標與 `#define` 的副作用（[Ch 27](./27-preprocessor-basics.md)）

### 書籍

- **《Crafting Interpreters》** — Robert Nystrom（Genever Benning，2021；[線上免費版](https://craftinginterpreters.com/)）
  - **這本書的定位**：從 AST 直譯器寫到 bytecode VM 的完整教程，第二部分用 C 實作
  - **讀哪幾章**：Part III 的「Chunks of Bytecode」「A Virtual Machine」「Compiling Expressions」（Pratt parser，對照 c4 的優先序爬升）

### 相關課程

- [`../../compilers/compiler_frontend/`](../../compilers/compiler_frontend/README.md)：用 flex/bison 正規地寫前端，與 c4 的手寫單趟剖析對照
- [`../../compilers/elf_linking/`](../../compilers/elf_linking/README.md)：shecc 自己寫出的 ELF 結構，在這門課有完整拆解

shecc 能不用 `ld` 直接寫出執行檔，是因為它只有一個轉譯單元、不需要合併其他目的檔。真實世界的程式由成百上千個 `.o` 與程式庫組成，把它們拼起來的工作——符號解析與重定位——是下一章的主題。

→ [Ch 39 靜態連結：符號解析、重定位與程式庫](./39-static-linking.md)
