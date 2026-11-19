# Ch 39 — 靜態連結：符號解析、重定位與程式庫

> **目標**：看懂編譯器交給連結器的東西（符號表、重定位項目、section），能用 `nm`、`readelf`、`objdump` 親手驗算一筆 `R_X86_64_PC32` 重定位，說清楚 strong/weak/common 符號的規則、靜態程式庫為什麼「順序寫錯就連不起來」，並認識 section 收集、嵌入二進位檔、`--gc-sections`/LTO 與 `DT_RELR` 這些連結器層級的手法。

> **對應原文**：[你所不知道的 C 語言：連結器和執行檔資訊篇](https://hackmd.io/@sysprog/c-linker-loader) 的「宣告、定義與目的檔」「連結器的工作」「重複的符號」「靜態程式庫」「C++ 帶來的變化」「嵌入二進位檔案」「以 section 收集分散的資料」「連結器與軟體最佳化」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、clang 14、GNU ld (binutils) 2.38、lld 14、glibc 2.35。C23 `#embed` 與 `DT_RELR` 的載入另以 Docker `gcc:15` 映像（gcc 15.3、binutils 2.44、glibc 2.41）驗證。原文的實驗環境是 Ubuntu 24.04（gcc 14、binutils 2.42、glibc 2.39），兩邊行為不同的地方會標出來。

## 為什麼需要這個？

一個人寫的小程式可以全部塞進一個 `.c` 檔；但只要專案大一點，就會拆成幾十、幾千個檔案，還要用別人寫好的 `printf`、`malloc`。問題來了：`main.c` 呼叫 `parse()`，可是 `parse()` 的機械碼在 `parser.c` 編出來的檔案裡，`main.c` 編譯的當下根本不知道它會被放在哪個位址。

最早的做法是**全部一起重新組譯**：把所有原始碼串成一份大檔再翻譯，位址當然就都知道了。代價是改一行就要重編整個系統，而且廠商無法只發佈編好的函式庫。1950 年代的「link editor」/「loader」就是為了解決這件事：讓每個檔案**分開編譯**，把「這裡要填誰的位址」的問題留到最後，由一個專門的程式統一處理。UNIX 第一版（1971）的 `ld(I)` 手冊稱它為 link editor，第七版（1979）改稱 loader——兩個名字對應它最初的兩項職責：把各部分接起來（link），以及安排它們在記憶體中的位置以便載入（load）。現代系統把這兩件事拆開了：`ld` 在建置時期產生執行檔，載入則交給核心的 `execve` 和動態連結器 `ld.so`（下兩章的主題）。

很多 C 程式設計師把連結器當黑盒子，直到碰上這些訊息才回頭找它：

- `undefined reference to 'foo'`——明明有寫 `foo` 啊？
- `multiple definition of 'counter'`——升級 gcc 10 之後突然冒出來
- 程式庫順序換一下就連不起來
- 嵌入式專案的 linker script 裡一堆 `KEEP(*(.init_hook))` 看不懂

Linux 核心、AOSP、各種韌體專案處處依賴連結器：自訂 linker script、以 section 收集初始化函式、可載入核心模組。這一章把黑盒子打開。

## 先建立直覺

把每個 `.o` 想成一塊**拼圖**：它有自己畫好的圖案（機械碼與資料），邊緣有**凸起**（我定義了 `fn_c`，誰要都可以拿）和**凹槽**（我需要 `fn_a`，但我沒有）。連結器做兩件事：

```
  c_parts.o                          sample.o
 ┌──────────────────┐              ┌──────────────────┐
 │ 凸：fn_c          │<─────────────│ 凹：fn_c          │
 │ 凸：x_global_init │              │ 凸：fn_a          │
 │ 凹：fn_a          │─────────────>│ 凸：z_global      │
 │ 凹：z_global      │─────────────>│ 凸：main          │
 │ (static：不外露)  │              │                  │
 └──────────────────┘              └──────────────────┘
            │   ① 符號解析：每個凹槽找到唯一的凸起
            │   ② 重定位：決定每塊拼圖放哪，把位址填進凹槽
            ▼
     ┌──────────────────────────────────┐
     │ sample（執行檔）：所有位址都確定了  │
     └──────────────────────────────────┘
```

① **符號解析（symbol resolution）**回答「這個參照指的是誰」；② **重定位（relocation）**回答「把位址填到哪裡、怎麼算」。這一章的前半就是把這兩步各自拆到位元組層級。

> 如果你對編譯流程四個階段（前置處理、編譯、組譯、連結）還不熟，先回看 [Ch 36 — GNU toolchain 與編譯流程](./36-gnu-toolchain.md)；`gcc -v` 會顯示 driver 在連結時偷偷加進來的 `Scrt1.o`、`crti.o` 等啟動程式碼，這些物件在 [Ch 41](./41-c-runtime.md) 會逐一拆開。

## 宣告、定義與目的檔

連結器處理的是**有名字的實體**，所以先分清兩個詞：

- **定義（definition）**：把名稱連到實體。變數的定義讓編譯器保留空間（可能還填初值），函式的定義讓編譯器產生機械碼。
- **宣告（declaration）**：只告訴編譯器名稱的型別與屬性。只有宣告的名稱，定義必須在程式別處。定義本身也是一種宣告。

C 標準用兩個獨立的概念描述變數：**儲存期（storage duration，N1570 6.2.4）**決定它活多久，**linkage（6.2.2）**決定名稱在哪些範圍可見。對連結器而言重要的是：檔案範圍的物件與函式若沒有 `static`，就是 **external linkage**（其他檔案可以用這個名字找到它）；加上 `static` 則是 **internal linkage**（只有本檔案看得到）。區域變數沒有 linkage，它活在執行期的堆疊上，跟連結器無關。

```c
/* c_parts.c */
/* 未初始化的全域變數 (定義) */
int x_global_uninit;
/* 已初始化的全域變數 (定義) */
int x_global_init = 1;
/* 只能在本檔案以名稱存取的全域變數 */
static int y_global_uninit;
static int y_global_init = 2;
/* 宣告：定義位於程式其他地方 */
extern int z_global;
int fn_a(int x, int y);

static int fn_b(int x)
{
    return x + 1;
}

int fn_c(int x_local)
{
    int y_local_uninit;
    int y_local_init = 3;

    x_global_uninit = fn_a(x_local, x_global_init);
    y_local_uninit = fn_a(x_local, y_local_init);
    y_local_uninit += fn_b(z_global);
    return y_global_uninit + y_local_uninit + y_global_init;
}
```

編譯器把它翻成**可重定位目的檔（relocatable object file）**。程式碼參照了 `fn_a` 和 `z_global`，但它們的定義不在這個轉譯單元，編譯器於是在參照處留下**空白**，並記下「這個空白要填誰」，等連結器日後兌現。

### 以 nm 解剖目的檔

```console
$ gcc -c c_parts.c
$ nm c_parts.o
                 U fn_a
0000000000000000 t fn_b
0000000000000013 T fn_c
0000000000000000 D x_global_init
0000000000000000 B x_global_uninit
0000000000000004 d y_global_init
0000000000000004 b y_global_uninit
                 U z_global
```

第二欄的字母是符號類別：

| 字母 | 意思 | 本例 |
|---|---|---|
| `U` | undefined，就是那個「空白」 | `fn_a`、`z_global` |
| `T`/`t` | 在 `.text`（程式碼） | `fn_c`、`fn_b` |
| `D`/`d` | 在 `.data`（有初值的資料） | `x_global_init`、`y_global_init` |
| `B`/`b` | 在 `.bss`（以零填滿，不佔檔案空間） | `x_global_uninit`、`y_global_uninit` |
| `R`/`r` | 在 `.rodata`（唯讀資料，例如 `const` 全域變數） | — |
| `C` | common symbol（下面會講） | — |
| `W`/`w` | weak symbol（已定義／未定義） | — |

**大寫代表全域（`STB_GLOBAL`），小寫代表區域（`STB_LOCAL`）。** `static` 的三個東西都是小寫，連結器**不會**拿它們去滿足別的檔案的參照。區域變數 `y_local_init`、`y_local_uninit` 根本不在表上。注意第一欄的「位址」在 `.o` 裡只是各 section 內的偏移（`fn_c` 在 `.text` 偏移 `0x13`），真正的位址要等連結後才知道。

## 連結器的工作：符號解析與重定位

### 符號解析

配上第二個檔案：

```c
/* sample.c */
int z_global = 11;
/* 與 c_parts.c 同名，但二者皆為 static，互不衝突 */
static int y_global_init = 2;
extern int x_global_init;
int fn_c(int x_local);

int fn_a(int x, int y)
{
    return x + y;
}

int main(void)
{
    return fn_c(y_global_init) == 19 ? 0 : 1;
}
```

`c_parts.o` 缺的 `fn_a`、`z_global` 由 `sample.o` 提供，`sample.o` 缺的 `fn_c` 由 `c_parts.o` 提供。`x_global_init` 在 `sample.c` 只是宣告，沒有任何程式碼用到它，所以**不產生**參照。

```console
$ gcc -c sample.c && gcc c_parts.o sample.o -o sample && ./sample; echo $?
0
$ nm -n sample | grep -v ' _' | grep -E ' [tTdDbB] '
0000000000001070 t deregister_tm_clones
00000000000010a0 t register_tm_clones
0000000000001120 t frame_dummy
0000000000001129 t fn_b
000000000000113c T fn_c
00000000000011a1 T fn_a
00000000000011b9 T main
0000000000004010 D x_global_init
0000000000004014 d y_global_init
0000000000004018 D z_global
000000000000401c d y_global_init
0000000000004020 b completed.0
0000000000004024 B x_global_uninit
0000000000004028 b y_global_uninit
```

三件事值得看：`U` 全部消失了；**同類 section 被排在一起**（兩個檔案的 `.text` 相鄰，接著 `.data`，最後 `.bss`）；兩個 `y_global_init` 各有獨立的儲存空間（`0x4014` 與 `0x401c`），internal linkage 的名稱不會打架。`deregister_tm_clones`、`frame_dummy`、`completed.0` 來自 gcc 的 `crtbeginS.o`，是 C runtime 的一部分。

### 重定位：在位元組層級驗算一次

編譯器在每個空白處留下一筆**重定位項目（relocation entry）**，記錄三件事：**位置（offset）**、**計算方式（type）**、**目標符號 + addend**。

```console
$ readelf -rW c_parts.o
Relocation section '.rela.text' at offset 0x300 contains 7 entries:
    Offset             Info             Type               Symbol's Value  Symbol's Name + Addend
000000000000002b  0000000900000002 R_X86_64_PC32          0000000000000000 x_global_init - 4
0000000000000037  0000000b00000004 R_X86_64_PLT32         0000000000000000 fn_a - 4
000000000000003d  0000000800000002 R_X86_64_PC32          0000000000000000 x_global_uninit - 4
000000000000004c  0000000b00000004 R_X86_64_PLT32         0000000000000000 fn_a - 4
0000000000000055  0000000c00000002 R_X86_64_PC32          0000000000000000 z_global - 4
0000000000000065  0000000400000002 R_X86_64_PC32          0000000000000000 .bss + 0
0000000000000070  0000000300000002 R_X86_64_PC32          0000000000000000 .data + 0
```

用 `objdump -dr` 可以看到這些項目就掛在指令下面：

```console
$ objdump -dr --no-show-raw-insn c_parts.o
...
  29:	mov    0x0(%rip),%edx        # 2f <fn_c+0x1c>
			2b: R_X86_64_PC32	x_global_init-0x4
...
  36:	call   3b <fn_c+0x28>
			37: R_X86_64_PLT32	fn_a-0x4
...
  5b:	call   0 <fn_b>
  63:	mov    0x0(%rip),%edx        # 69 <fn_c+0x56>
			65: R_X86_64_PC32	.bss
```

目的檔裡位移欄位全是 `0x0`——那就是空白。連結之後再看同一段：

```console
$ objdump -d --no-show-raw-insn sample | sed -n '/<fn_c>:/,/^$/p'
000000000000113c <fn_c>:
...
    1152:	mov    0x2eb8(%rip),%edx        # 4010 <x_global_init>
...
    115f:	call   11a1 <fn_a>
```

`0x2eb8` 怎麼來的？x86-64 psABI 定義 `R_X86_64_PC32` 的計算是 **S + A − P**：

- **S**（symbol）= `x_global_init` 的最終位址 = `0x4010`
- **A**（addend）= `-4`
- **P**（place）= 這個 4 位元組欄位本身的位址。`fn_c` 在 `.o` 裡位於 `0x13`、在執行檔裡位於 `0x113c`，欄位在 `.o` 的偏移 `0x2b`，所以 P = `0x113c + (0x2b − 0x13)` = `0x1154`

```
S + A − P = 0x4010 − 4 − 0x1154 = 0x2eb8   ✔ 和反組譯一致
```

為什麼要減 4？因為 `%rip` 相對定址是以**下一道指令的位址**為基準，而 32 位元位移欄位正好在指令的最後 4 個位元組：`P + 4` 就是下一道指令。addend 的 `−4` 就是補償這件事。

其他幾點：

- `R_X86_64_PLT32` 計算方式一樣，但目標是函式的 PLT 項目。若 `fn_a` 最後在共享程式庫裡，呼叫就經過 PLT（[Ch 40](./40-dynamic-linking.md)）；若就在執行檔內（本例），連結器直接填函式本身的位移——所以連結後是 `call 11a1 <fn_a>`，沒有 `@plt`。
- 呼叫 `static` 的 `fn_b`（偏移 `0x5b`）**沒有任何重定位**：目標在同一個 section，相對距離在組譯時就確定了，組譯器直接填好。
- `static` 變數不以自己的名字出現，而是寫成「section + 偏移」：`y_global_uninit` 在 `.bss` 偏移 4，加上 addend −4，剛好顯示成 `.bss + 0`。區域符號沒有被別人參照的必要，用 section 表示就夠了。

ARM64 指令固定 4 位元組，塞不下 32 位元位移，同一段程式會變成一對 `adrp`（取頁位址，`R_AARCH64_ADR_PREL_PG_HI21`）＋`add`（補頁內低 12 位，`R_AARCH64_ADD_ABS_LO12_NC`），重定位筆數也因此較多。原文有完整輸出；本課的實驗環境只有 x86-64，ARM64 部分**未實測**。

## 重複的符號：strong、weak 與 common

找不到定義是錯；定義太多呢？C 標準要求具 external linkage 的識別字若在運算式中被使用，整個程式**恰好一個**外部定義（N1570 6.9 §5）。但 C 另有一個歷史包袱：**tentative definition**（6.9.2）——檔案範圍內沒有初值、且沒有 storage-class 或只有 `static` 的物件宣告，若同一轉譯單元沒有其他定義，就視為初值 0 的定義。`int x_global_uninit;` 就是。

### common model：Fortran 留下來的寬鬆規則

Fortran 的 `COMMON` 區塊讓每個參照它的檔案都「持有」一份全域資料，由連結器合併，大小不同取最大者。UNIX 的 C 編譯器長期沿用這套 **common model**：external linkage 的 tentative definition 被輸出成 **common symbol**（`nm` 顯示 `C`），多個檔案的同名 common symbol 會被合併成一份，**不算錯**。

這規則會吞掉真正的 bug。兩個檔案都定義了 `counter` 和 `ratio`，而且 `ratio` 型別還不一樣：

```c
/* dup1.c */
#include <stdio.h>
int counter;      /* tentative definition */
double ratio;     /* 型別與 dup2.c 不同 */
void bump(void);
int main(void)
{
    bump();
    printf("counter=%d sizeof(ratio)=%zu\n", counter, sizeof ratio);
    return 0;
}
```

```c
/* dup2.c */
int counter;
int ratio;
void bump(void)
{
    counter++;
}
```

gcc 10 起預設 `-fno-common`（clang 11 跟進），tentative definition 一律放進 `.bss` 成為普通定義，連結器就抓得到：

```console
$ gcc dup1.c dup2.c -o dup
/usr/bin/ld: /tmp/ccfajAjg.o:(.bss+0x0): multiple definition of `counter'; /tmp/ccexQ986.o:(.bss+0x0): first defined here
/usr/bin/ld: /tmp/ccfajAjg.o:(.bss+0x4): multiple definition of `ratio'; /tmp/ccexQ986.o:(.bss+0x8): first defined here
collect2: error: ld returned 1 exit status
$ gcc -fcommon dup1.c dup2.c -o dup && ./dup
counter=1 sizeof(ratio)=8
$ gcc -fcommon -Wl,--warn-common dup1.c dup2.c -o dup
/usr/bin/ld: /tmp/ccaUv8aU.o and /tmp/ccUIBLGL.o: warning: multiple common of `counter'
/usr/bin/ld: /tmp/ccaUv8aU.o: warning: common of `ratio' overridden by larger common from /tmp/ccUIBLGL.o
```

`-fcommon` 下連結默默成功，`dup2.c` 以 `int` 看待的 `ratio` 其實是 8 位元組 `double` 的前半——一個要到執行期才會咬人的型別錯誤。`--warn-common` 把合併過程說出來。只有維護依賴舊行為的程式碼時才該用 `-fcommon`；加上它之後 `c_parts.o` 的 `x_global_uninit` 也會從 `B` 變回 `C`：

```console
$ gcc -fcommon -c c_parts.c -o cp_common.o && nm cp_common.o | grep x_global_uninit
0000000000000004 C x_global_uninit
```

（`C` 的「位址」欄這時代表**對齊需求**，不是偏移。）

正確的寫法是：標頭檔放 `extern int counter;`，**恰好一個** `.c` 檔放 `int counter;`。

### weak symbol：可以被覆寫的預設實作

GNU 工具鏈的 `__attribute__((weak))` 定義在沒有其他定義時生效，遇到同名的一般（**strong**）定義時讓位，不報重複：

```c
/* weak_lib.c */
#include <stdio.h>
__attribute__((weak)) void log_hook(const char *msg)
{
    printf("default: %s\n", msg);
}
void work(void)
{
    log_hook("done");
}
```

```c
/* weak_main.c */
void work(void);
int main(void)
{
    work();
    return 0;
}
```

```c
/* weak_override.c */
#include <stdio.h>
void log_hook(const char *msg)
{
    printf("custom: %s\n", msg);
}
```

```console
$ gcc weak_main.c weak_lib.c -o w1 && ./w1
default: done
$ gcc weak_main.c weak_lib.c weak_override.c -o w2 && ./w2
custom: done
$ gcc -c weak_lib.c && nm weak_lib.o | grep log_hook
0000000000000000 W log_hook
```

規則整理：

| 組合 | 結果 |
|---|---|
| 一個 strong + 任意個 weak | 用 strong |
| 只有 weak | 用其中一個（實務上是第一個遇到的） |
| 兩個 strong | `multiple definition` 錯誤 |
| 只有 weak 的**未定義**參照、沒有任何定義 | 不報錯，位址為 0（NULL） |

最後一條讓「選配功能」成為可能：

```c
#include <stdio.h>
extern void optional_feature(void) __attribute__((weak));
int main(void)
{
    if (optional_feature)
        optional_feature();
    else
        puts("optional_feature not linked");
    return 0;
}
```

```console
$ gcc -Wall -Wextra weakref.c -o weakref && ./weakref && nm weakref | grep optional
optional_feature not linked
                 w optional_feature
```

兩個 strong 就不行了：

```console
$ gcc weak_main.c weak_lib.c weak_override.c strong2.c -o w4
/usr/bin/ld: /tmp/ccNbqzW9.o: in function `log_hook':
strong2.c:(.text+0x0): multiple definition of `log_hook'; /tmp/ccVzjjLk.o:weak_override.c:(.text+0x0): first defined here
collect2: error: ld returned 1 exit status
```

Cortex-M 韌體的向量表是 weak 的經典應用：CMSIS 啟動檔把所有中斷處理常式宣告成 `weak` 並 `alias` 到同一個 `Default_Handler`，應用程式只要定義用到的那幾個，連結器就自動以 strong 版本取代。

## 載入執行檔：section 與 segment

目的檔以 **section** 組織內容（`.text`、`.data`、`.bss`、`.rodata`、`.symtab`……），這是給連結器看的；連結器把同類 section 合併後，再依存取權限分組為 **segment**，由 **program header** 描述，這是給核心 `execve` 和 `ld.so` 看的。**載入器只看 segment，不看 section。**

```console
$ readelf -lW sample
Program Headers:
  Type           Offset   VirtAddr           PhysAddr           FileSiz  MemSiz   Flg Align
  PHDR           0x000040 0x0000000000000040 0x0000000000000040 0x0002d8 0x0002d8 R   0x8
  INTERP         0x000318 0x0000000000000318 0x0000000000000318 0x00001c 0x00001c R   0x1
      [Requesting program interpreter: /lib64/ld-linux-x86-64.so.2]
  LOAD           0x000000 0x0000000000000000 0x0000000000000000 0x0005f0 0x0005f0 R   0x1000
  LOAD           0x001000 0x0000000000001000 0x0000000000001000 0x0001e9 0x0001e9 R E 0x1000
  LOAD           0x002000 0x0000000000002000 0x0000000000002000 0x00013c 0x00013c R   0x1000
  LOAD           0x002df0 0x0000000000003df0 0x0000000000003df0 0x000230 0x000240 RW  0x1000
  DYNAMIC        0x002e00 0x0000000000003e00 0x0000000000003e00 0x0001c0 0x0001c0 RW  0x8
  ...
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
  GNU_RELRO      0x002df0 0x0000000000003df0 0x0000000000003df0 0x000210 0x000210 R   0x1

 Section to Segment mapping:
  Segment Sections...
   02     .interp .note.gnu.property ... .dynsym .dynstr .gnu.version .gnu.version_r .rela.dyn
   03     .init .plt .plt.got .text .fini
   04     .rodata .eh_frame_hdr .eh_frame
   05     .init_array .fini_array .dynamic .got .data .bss
```

```
      c_parts.o / sample.o                       sample (執行檔)
   ┌────────────┐                     ┌─────────────────────────────┐
   │ .text      │───────────┐         │ LOAD R    ELF 標頭 .dynsym…  │
   │ .data      │─────┐     └────────>│ LOAD R E  .init .plt .text  │
   │ .bss       │──┐  │               │ LOAD R    .rodata .eh_frame │
   └────────────┘  │  └──────────────>│ LOAD RW   .data             │
                   └─(只記大小)──────>│           .bss (MemSiz 差額) │
                                      └─────────────────────────────┘
```

幾個細節：

- 可寫入 LOAD 的 `FileSiz`=`0x230`、`MemSiz`=`0x240`，差的 16 位元組就是 `.bss`：有初值的變數必須把初值存進檔案，沒初值的只記大小，由核心載入時以零填滿。`size sample` 的 `bss` 欄同樣是 16。
- 為什麼有**四個** LOAD？binutils 2.31 起 x86-64 Linux 預設 `-z separate-code`，把唯讀資料和程式碼分到不同 segment，避免 `.rodata` 裡的位元組被當成可執行的 ROP gadget（[Ch 14](./14-function-call-security.md)）。
- `GNU_RELRO` 標示「動態連結器重定位完畢後改成唯讀」的範圍，搭配 `-z now` 就是 full RELRO，[Ch 40](./40-dynamic-linking.md) 會實際示範改寫 GOT 時它怎麼擋下攻擊。
- `GNU_STACK` 的旗標決定整個行程的堆疊能不能執行，下面嵌入二進位檔時會看到它出事。

> 行程的完整位址空間配置（text、data、bss、heap、mmap 區、stack）見 [Ch 11 — 行程記憶體布局、x86-64 SysV ABI 與 stack frame](./11-process-layout-and-abi.md)。

## 靜態程式庫與搜尋順序陷阱

每個程式都要 `printf`，與其每次連結都列出同一批 `.o`，不如收進**靜態程式庫（static library）**：用 `ar` 把多個目的檔包成 `.a`，開頭附一張符號索引（以前由 `ranlib` 建立，現在 `ar s` 一併完成）。`-lfred` 會在搜尋路徑找 `libfred.so` 或 `libfred.a`。

**連結器的演算法**（GNU ld）是單趟、由左至右：

```
維護一張「尚未解析的符號」清單 U
for 命令列上每個輸入（由左至右）:
    若是 .o   → 整個納入，把它的定義從 U 移除，把它的未定義參照加進 U
    若是 .a   → repeat:
                   找出能解析 U 中某個符號的成員 .o，整個納入（同上更新 U）
                until 這個 .a 再也抽不出成員
                → 移往下一個輸入，這個 .a 不會再被回頭搜尋
最後 U 不為空 → undefined reference
```

兩個關鍵：**抽取單位是整個成員 `.o`，不是單一函式**；**處理過的程式庫不會回頭**。

用原文的例子實驗。以下 bash 函式產生一批互相參照的檔案（每個定義的函式都去呼叫該檔參照的函式，只為製造參照）：

```bash
# gen 檔名 "定義的函式" "參照的函式"
gen() {
    {
        for r in $3; do echo "void $r(void);"; done
        for d in $2; do
            printf 'void %s(void) {' "$d"
            for r in $3; do printf ' %s();' "$r"; done
            echo ' }'
        done
    } > "$1.c"
}
gen a "a1 a2 a3" "b2 x12"
printf '#include <stdio.h>\nint main(void)\n{\n    puts("prog: ok");\n    return 0;\n}\n' >> a.c
gen b "b1 b2" "a3 y22"
gen b_y32 "b1 b2" "a3 y22 y32"
gen x1 "x11 x12 x13" "x23 y12"
gen x2 "x21 x22 x23" "y11"
gen x3 "x31 x32" ""
gen y1 "y11 y12" "y21"
gen y2 "y21 y22" ""
gen y3 "y31 y32" "x31"
for f in *.c; do gcc -c "$f"; done
ar rcs libx.a x1.o x2.o x3.o
ar rcs liby.a y1.o y2.o y3.o
```

`gen x1 ...` 產生的檔案長這樣：

```c
void x23(void);
void y12(void);
void x11(void) { x23(); y12(); }
void x12(void) { x23(); y12(); }
void x13(void) { x23(); y12(); }
```

`--print-map` 的連結地圖記錄了每個成員被抽取的原因：

```console
$ gcc a.o b.o -L. -lx -ly -o prog -Wl,--print-map | sed -n '/^Archive member/,/^Merging/{/^Merging/!p}'
Archive member included to satisfy reference by file (symbol)

./libx.a(x1.o)                a.o (x12)
./libx.a(x2.o)                ./libx.a(x1.o) (x23)
./liby.a(y1.o)                ./libx.a(x2.o) (y11)
./liby.a(y2.o)                ./liby.a(y1.o) (y21)
$ ./prog
prog: ok
```

讀法：處理完 `a.o`、`b.o`，`U = {x12, y22}`；`libx.a` 抽 `x1.o`（為了 `x12`），卻帶進 `x23`、`y12`；再抽 `x2.o`（為了 `x23`），帶進 `y11`；`libx.a` 抽完了。到 `liby.a` 抽 `y1.o`、`y2.o`，全部解析。`x3.o`、`y3.o` **根本沒進執行檔**——這正是靜態程式庫省空間的方式。

現在故意弄壞。把 `b.o` 換成多參照 `y32` 的 `b_y32.o`：`liby.a` 階段會額外抽 `y3.o`，帶來對 `x31` 的參照，但 `libx.a` 已經處理過了：

```console
$ gcc a.o b_y32.o -L. -lx -ly -o prog
/usr/bin/ld: ./liby.a(y3.o): in function `y31':
y3.c:(.text+0x9): undefined reference to `x31'
/usr/bin/ld: ./liby.a(y3.o): in function `y32':
y3.c:(.text+0x19): undefined reference to `x31'
collect2: error: ld returned 1 exit status
$ gcc a.o b_y32.o -L. -lx -ly -lx -o prog && echo OK
OK
$ gcc a.o b_y32.o -L. -Wl,--start-group -lx -ly -Wl,--end-group -o prog && echo OK
OK
```

`x31` 明明就在 `libx.a` 裡！兩種修法：重複列出（`-lx -ly -lx`），或用 `--start-group`/`--end-group` 包起來讓連結器在群組內反覆搜尋直到沒有新符號（代價是連結時間）。

再兩個常見的弄壞方式：

```console
$ gcc a.o b.o -L. -ly -lx -o prog2          # 程式庫順序反了
/usr/bin/ld: ./libx.a(x1.o): in function `x11':
x1.c:(.text+0xe): undefined reference to `y12'
...
$ gcc -L. -lx -ly a.o b.o -o prog3          # 程式庫寫在 .o 前面
/usr/bin/ld: a.o: in function `a1':
a.c:(.text+0xe): undefined reference to `x12'
...
```

第二個是新手最常犯的：掃到 `-lx` 時 `U` 還是空的，什麼都不抽；等 `a.o` 帶進 `x12` 時，`libx.a` 早就過去了。**經驗法則：程式庫放在用到它的東西後面；被依賴者越底層越靠右。**

lld 的語意不同：它記住所有程式庫提供的符號，不論順序都能解析：

```console
$ gcc -fuse-ld=lld a.o b_y32.o -L. -lx -ly -o prog_lld && echo LLD OK
LLD OK
```

方便，但依賴這種寬容的建置腳本換回 GNU ld 就會壞。真正的解法是消除程式庫之間的循環依賴。

### 共享程式庫：把最後一段連結延到執行時期

靜態程式庫讓每個執行檔各帶一份 `printf`；程式庫修了 bug，每個執行檔都得重連。**共享程式庫（shared library，`.so`）**把最後一段連結延到執行期：連結器只在執行檔裡記下需要的程式庫（`DT_NEEDED`）與符號，執行前由 `ld.so` 載入並解析。把 `liby.a` 的三個成員做成 `liby.so`，未定義符號只剩跨程式庫的 `x31`：

```console
$ for f in y1 y2 y3; do gcc -fPIC -c $f.c -o $f.pic.o; done
$ gcc -shared -o liby.so y1.pic.o y2.pic.o y3.pic.o
$ nm -D --undefined-only liby.so
                 w _ITM_deregisterTMCloneTable
                 w _ITM_registerTMCloneTable
                 w __cxa_finalize
                 w __gmon_start__
                 U x31
$ gcc a.o b.o -L. -lx -ly -o prog4
/usr/bin/ld: ./liby.so: undefined reference to `x31'
collect2: error: ld returned 1 exit status
```

兩個差異：同一目錄同時有 `.so` 與 `.a` 時 `-ly` **優先選 `.so`**（除非 `-static` 或 `-l:liby.a`）；粒度不同——用到 `.so` 的任何一個符號，**整個**程式庫都會映射進行程（但唯讀頁面可以被多個行程共用）。建立執行檔時 GNU ld 預設檢查共享程式庫的未定義符號（`--no-allow-shlib-undefined`），所以上面報錯；建立 `.so` 本身時則預設允許未定義符號，[Ch 40](./40-dynamic-linking.md) 會看到這怎麼讓錯誤延到執行期。

## C++ 帶來的變化

這是 C 的課，但 C 程式庫天天被 C++ 呼叫，C++ 的三個特性直接改變了連結器的工作。

### 名稱修飾與 extern "C"

C++ 允許**多載（overloading）**：

```cpp
int max(int x, int y) { return x > y ? x : y; }
float max(float x, float y) { return x > y ? x : y; }
double max(double x, double y) { return x > y ? x : y; }
```

連結器只認名字，編譯器只好把參數型別編進符號名，稱為**名稱修飾（name mangling）**。gcc/clang 在 Linux 上遵循 Itanium C++ ABI：

```console
$ g++ -c overload.cpp && nm overload.o
000000000000004b T _Z3maxdd
0000000000000020 T _Z3maxff
0000000000000000 T _Z3maxii
$ nm -C overload.o
000000000000004b T max(double, double)
0000000000000020 T max(float, float)
0000000000000000 T max(int, int)
```

`_Z` 開頭、`3max` 是「長度 + 名字」，後面 `i`/`f`/`d` 是參數型別。C 編譯器**不修飾**，所以 C++ 直接呼叫 C 函式會失敗：

```c
/* findmax.c */
int findmax(int x, int y)
{
    return x > y ? x : y;
}
```

```cpp
/* test1a.cpp */
#include <cstdio>
#ifdef USE_HEADER
#include "findmax.h"
#else
int findmax(int x, int y); /* 少了 extern "C" */
#endif
int main()
{
    std::printf("%d\n", findmax(3, 7));
    return 0;
}
```

```console
$ gcc -c findmax.c && g++ test1a.cpp findmax.o -o t1
/usr/bin/ld: /tmp/ccoXcfGm.o: in function `main':
test1a.cpp:(.text+0x13): undefined reference to `findmax(int, int)'
collect2: error: ld returned 1 exit status
$ nm findmax.o | grep findmax; g++ -c test1a.cpp && nm test1a.o | grep findmax
0000000000000000 T findmax
                 U _Z7findmaxii
```

錯誤訊息裡帶參數型別的 `findmax(int, int)` 就是線索：C++ 端找 `_Z7findmaxii`，C 端提供的是 `findmax`。解法是 C 標頭檔的標準寫法：

```c
/* findmax.h */
#ifndef FINDMAX_H
#define FINDMAX_H
#ifdef __cplusplus
extern "C" {
#endif
int findmax(int x, int y);
#ifdef __cplusplus
}
#endif
#endif
```

```console
$ g++ -DUSE_HEADER test1a.cpp findmax.o -o t1 && ./t1
7
```

**你寫的每個 C 程式庫標頭檔都該包這層。** 這也是為什麼各種語言的 FFI 都以 C ABI 為交流語言——[Ch 40](./40-dynamic-linking.md) 開頭會再談。

### 靜態物件的初始化與 .init_array

C 的靜態物件只能用常數運算式初始化（N1570 6.7.9 §4），載入時初值就已在 `.data` 裡。C++ 全域物件卻可能要執行建構子——誰在 `main` 之前呼叫它？

```cpp
#include <cstdio>
struct Fred {
    int x, y;
    Fred() : x(1), y(2) { std::puts("Fred()"); }
    Fred(int z) : x(z), y(3) { std::puts("Fred(int)"); }
};
Fred theFred;
Fred theOtherFred(55);
int main()
{
    std::puts("main");
    return theOtherFred.x == 55 ? 0 : 1;
}
```

```console
$ g++ -c global_obj.cpp && nm global_obj.o | grep -v ' U '
000000000000006f t _GLOBAL__sub_I_theFred
0000000000000028 t _Z41__static_initialization_and_destruction_0ii
0000000000000000 W _ZN4FredC1Ei
0000000000000000 W _ZN4FredC1Ev
0000000000000000 W _ZN4FredC2Ei
0000000000000000 W _ZN4FredC2Ev
0000000000000000 n _ZN4FredC5Ei
0000000000000000 n _ZN4FredC5Ev
0000000000000000 T main
0000000000000000 B theFred
0000000000000008 B theOtherFred
$ readelf -rW global_obj.o | sed -n '/init_array/,/^$/p'
Relocation section '.rela.init_array' at offset 0x5f0 contains 1 entry:
    Offset             Info             Type               Symbol's Value  Symbol's Name + Addend
0000000000000000  0000000200000001 R_X86_64_64            0000000000000000 .text + 6f
$ g++ global_obj.o -o gobj && ./gobj
Fred()
Fred(int)
main
```

編譯器為每個轉譯單元產生一個初始化函式 `_GLOBAL__sub_I_theFred`，並把它的位址放進 `.init_array` section（重定位指向 `.text + 0x6f`，正是該函式）。連結器把所有目的檔的 `.init_array` 串成一張函式指標陣列，C runtime 在 `main` 之前逐一呼叫——C 的 `__attribute__((constructor))` 也走同一條路，[Ch 41](./41-c-runtime.md) 會追到 glibc 裡是誰在走訪這張表。不同轉譯單元之間的初始化順序**未規定**，實際取決於連結順序，這就是 C++ 的 *static initialization order fiasco*。

### 樣板與 COMDAT

標為 `W` 的建構子、以及樣板具現化，都是「每個用到它的轉譯單元各產生一份」：

```cpp
/* max_template.h */
template <class T>
T max(T x, T y)
{
    return x > y ? x : y;
}
```

```cpp
/* use1.cpp */
#include "max_template.h"
int f1(int a, int b) { return max(a, b); }
double g1(double a, double b) { return max<double>(a, b); }
```

```cpp
/* use2.cpp */
#include "max_template.h"
int f1(int, int);
double g1(double, double);
int main() { return max(f1(1, 2), 2) + (int) g1(1.5, 0.5) == 3 ? 0 : 1; }
```

```console
$ g++ -c use1.cpp use2.cpp && nm -C use1.o use2.o | grep max
0000000000000000 W double max<double>(double, double)
0000000000000000 W int max<int>(int, int)
0000000000000000 W int max<int>(int, int)
$ readelf -gW use1.o
COMDAT group section [    1] `.group' [_Z3maxIiET_S0_S0_] contains 1 sections:
   [Index]    Name
   [    7]   .text._Z3maxIiET_S0_S0_

COMDAT group section [    2] `.group' [_Z3maxIdET_S0_S0_] contains 1 sections:
   [Index]    Name
   [    8]   .text._Z3maxIdET_S0_S0_
$ g++ use1.o use2.o -o tmpl && nm -C tmpl | grep ' max'
00000000000011a5 W double max<double>(double, double)
0000000000001185 W int max<int>(int, int)
```

每個具現化放在獨立 section，以 **COMDAT group** 標記（簽章是修飾後的名字）；連結器對簽章相同的 group **只保留一份、丟棄其餘**。`max<int>` 在兩個 `.o` 裡都有，執行檔裡只剩一份。這是「每處都產生、連結時合併」的策略：簡單可靠，代價是目的檔變大、編譯變慢；C++11 的 `extern template` 讓你指定只在一個轉譯單元具現化。

## 嵌入二進位檔案

想把一張圖、一份設定檔、一段韌體直接編進執行檔？先做個測試檔：

```console
$ printf 'Hello from an embedded blob\n' > blob
$ wc -c blob
28 blob
```

**方法一：`ld -b binary`**，把任意檔案當成輸入：

```console
$ ld -r -b binary -o blob.o blob
$ objdump -t blob.o
SYMBOL TABLE:
0000000000000000 l    d  .data	0000000000000000 .data
000000000000001c g       .data	0000000000000000 _binary_blob_end
0000000000000000 g       .data	0000000000000000 _binary_blob_start
000000000000001c g       *ABS*	0000000000000000 _binary_blob_size
```

連結器依檔名產生三個符號（`.` 和 `/` 這類不合 C 識別字的字元換成 `_`）。`_binary_blob_size` 是**絕對符號**，它的「位址」就是長度 28（`0x1c`）；但在 PIE 中取絕對符號的位址不可靠，長度應該用兩個邊界相減。邊界符號要宣告成**陣列**：

```c
#include <stdio.h>

extern const unsigned char _binary_blob_start[];
extern const unsigned char _binary_blob_end[];

int main(void)
{
    size_t len = (size_t) (_binary_blob_end - _binary_blob_start);
    printf("Data: %p..%p (%zu bytes)\n", (const void *) _binary_blob_start,
           (const void *) _binary_blob_end, len);
    fwrite(_binary_blob_start, 1, len, stdout);
    return 0;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror test.c blob.o -o test
$ ./test
Data: 0x5a454144a010..0x5a454144a02c (28 bytes)
Hello from an embedded blob
$ readelf -lW test | grep GNU_STACK
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RWE 0x10
```

（位址每次執行不同。）程式跑得好好的，**但整個行程的堆疊變成可執行（RWE）了**。`ld -b binary` 產生的目的檔沒有 `.note.GNU-stack` section，x86-64 的 GNU ld 把「缺少這個 section」解讀為「需要可執行堆疊」——這等於把 NX 保護（[Ch 14](./14-function-call-security.md)）整個關掉。在本課環境的 binutils 2.38 上，這件事**完全沒有警告**；binutils 2.39 起才會印出 `missing .note.GNU-stack section implies executable stack`（原文在 2.42 上看到這個警告）。另外資料位於可寫的 `.data`，也不理想。用 `objcopy` 一次修好兩件事：

```console
$ objcopy -I binary -O elf64-x86-64 \
    --rename-section .data=.rodata,alloc,load,readonly,data,contents \
    --add-section .note.GNU-stack=/dev/null blob blob_ro.o
$ gcc -std=c11 -Wall -Wextra -Werror test.c blob_ro.o -o test_ro && ./test_ro | head -1
Data: 0x615a81f0601e..0x615a81f0603a (28 bytes)
$ readelf -lW test_ro | grep GNU_STACK
  GNU_STACK      0x000000 0x0000000000000000 0x0000000000000000 0x000000 0x000000 RW  0x10
```

故意宣告錯看看——宣告成**指標變數**：

```c
extern const unsigned char *_binary_blob_start; /* 錯：宣告成指標 */
int main(void)
{
    printf("%p\n", (const void *) _binary_blob_start);
    return 0;
}
```

```console
$ ./wrong
0x7266206f6c6c6548
```

`0x7266206f6c6c6548` 拆成 little-endian 位元組是 `48 65 6c 6c 6f 20 66 72`＝`"Hello fr"`：程式把資料開頭 8 個位元組**當成指標**讀出來了。符號代表的是「資料本身的位址」，不是「存放位址的變數」——這和 [Ch 8](./08-arrays-functions-pointers.md) 講的「陣列不是指標」是同一回事，只是錯在連結層。嚴格說，兩個各自宣告的陣列相減在 ISO C 中仍是未定義行為（6.5.6 §9 要求指向同一陣列），這裡依賴的是連結器保證兩個符號界定同一塊連續資料。

**方法二：組譯器的 `.incbin`**，符號名、對齊、section 都自己決定：

```asm
    .section .rodata
    .global blob_start, blob_end
    .balign 16
blob_start:
    .incbin "blob"
blob_end:
    .section .note.GNU-stack, "", %progbits
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror test_incbin.c incbin.S -o ti && ./ti
28 bytes
```

**方法三：C23 `#embed`**，前置處理器直接把檔案展開成整數清單，`sizeof` 就是長度，不需要邊界符號：

```c
#include <stdio.h>

static const unsigned char blob[] = {
#embed "blob"
};

int main(void)
{
    printf("%zu bytes\n", sizeof blob);
    fwrite(blob, 1, sizeof blob, stdout);
    return 0;
}
```

```console
$ gcc -std=c2x test_embed.c -o te_old          # gcc 11
test_embed.c:4:2: error: invalid preprocessing directive #embed
$ docker run --rm -v "$PWD":/w -w /w gcc:15 sh -c 'gcc -std=c23 -Wall -Wextra -Werror test_embed.c -o te && ./te'
28 bytes
Hello from an embedded blob
```

`#embed` 要 gcc 15 或 clang 19 以上。不依賴任何擴充的傳統做法是 `xxd -i blob` 產生 C 陣列原始碼。

## 以 section 收集分散的資料（__start_ / __stop_）

問題：一個系統有幾十個模組，每個都要在開機時初始化。傳統做法是維護一張中央表格 `init_table[] = {clock_init, uart_init, ...}`，每加一個模組就要改這張表——容易漏、容易衝突。

[F9 microkernel](https://github.com/f9micro/f9-kernel) 的 init hooks 和 Linux 核心的 `module_init`/initcall 用的是另一招：**讓每個檔案把自己的描述結構丟進同一個具名 section，由連結器串起來**。F9 的巨集：

```c
#define INIT_HOOK(_hook, _level) \
    const init_struct _init_struct_##_hook \
        __attribute__((section(".init_hook"))) = { \
            .level = _level, \
            .hook = _hook, \
            .hook_name = #_hook, \
    };
```

它的 linker script 在前後放邊界符號，`KEEP` 防止 `--gc-sections` 把沒人參照的 section 當死碼丟掉：

```
        init_hook_start = .;
        KEEP(*(.init_hook))
        init_hook_end = .;
```

一般 Linux 程式不必寫 linker script：**section 名稱若是合法的 C 識別字，GNU ld、gold、lld 都會自動定義 `__start_SECNAME` 與 `__stop_SECNAME`**。可在使用者空間跑的版本：

```c
/* init_hook.h */
#ifndef INIT_HOOK_H
#define INIT_HOOK_H

struct init_hook {
    unsigned int level;
    void (*hook)(void);
    const char *name;
};

#define INIT_HOOK(fn, lvl) \
    static const struct init_hook init_hook_##fn \
        __attribute__((section("init_hook"), used, retain, aligned(8))) = \
            {.level = (lvl), .hook = fn, .name = #fn}

/* GNU ld、gold 與 lld 為名稱是合法 C 識別字的 section 自動定義這二個符號 */
extern const struct init_hook __start_init_hook[], __stop_init_hook[];

#endif
```

```c
/* board.c */
#include <stdio.h>
#include "init_hook.h"

static void clock_setup(void)
{
    puts("clock_setup");
}
INIT_HOOK(clock_setup, 1);

static void uart_setup(void)
{
    puts("uart_setup");
}
INIT_HOOK(uart_setup, 2);
```

```c
/* net.c */
#include <stdio.h>
#include "init_hook.h"

static void net_setup(void)
{
    puts("net_setup");
}
INIT_HOOK(net_setup, 3);

static void early_console(void)
{
    puts("early_console");
}
INIT_HOOK(early_console, 1);
```

```c
/* main.c */
#include <stdio.h>
#include "init_hook.h"

static unsigned int run_init_hooks(unsigned int last, unsigned int level)
{
    for (const struct init_hook *p = __start_init_hook; p != __stop_init_hook;
         p++) {
        if (p->level > last && p->level <= level) {
            printf("[level %u] ", p->level);
            p->hook();
        }
    }
    return level;
}

int main(void)
{
    printf("%zu hooks\n", (size_t) (__stop_init_hook - __start_init_hook));
    unsigned int last = 0;
    for (unsigned int level = 1; level <= 3; level++)
        last = run_init_hooks(last, level);
    return 0;
}
```

```console
$ gcc -std=c11 -Wall -Wextra -Werror -O2 main.c board.c net.c -o hooks && ./hooks
4 hooks
[level 1] clock_setup
[level 1] early_console
[level 2] uart_setup
[level 3] net_setup
$ nm hooks | grep -E '_init_hook$'
0000000000004010 D __start_init_hook
0000000000004070 D __stop_init_hook
```

`0x4070 − 0x4010 = 0x60 = 96 = 4 × 24`：四個 24 位元組的結構緊密排列。`main.c` 從頭到尾沒提到 `clock_setup` 或 `net_setup`，新增模組只要多連一個 `.o`。

每個屬性都有它的理由，逐一拿掉看會怎樣：

- **`section("init_hook")`**：名字不以 `.` 開頭才是合法 C 識別字，連結器才會產生 `__start_`/`__stop_`。
- **`used`**：變數是 `static` 又沒人參照，`-O2` 下編譯器會直接刪掉它。
- **`aligned(8)`**：拿掉試試（以 `-DHOOK_ALIGN=unused` 替換）：

  ```console
  $ ./hooks_na
  5 hooks
  [level 2] uart_setup
  [level 3] net_setup
  $ readelf -SW hooks_na | grep init_hook
    [26] init_hook         PROGBITS        0000000000004010 003010 000078 00 WAo  0   0 16
  ```

  gcc 11 為了效能把每個 24 位元組項目對齊到 16，section 也隨之要求 16 位元組對齊，連結器串接時在項目之間補空隙（0x60 變成 0x78）。以 `sizeof`（24）為步距走訪就會錯位：算出 5 個、讀到錯的項目、`clock_setup` 和 `early_console` 消失了。**這種 bug 不會 crash，只會靜悄悄地漏跑初始化**，在韌體裡是最難查的那種。
- **`retain`**（gcc 11 / clang 13 加入）：讓 section 帶 `SHF_GNU_RETAIN` 旗標（上面 `readelf` 的 `o` 就是它，binutils 2.38 在一般 OSABI 下把它顯示為 OS 專屬旗標），`--gc-sections` 不會移除它。GNU ld 預設把被 `__start_`/`__stop_` 參照的 section 視為存活，但 lld 13 起預設相反（`-z start-stop-gc`）。拿掉 `retain` 後用 lld 加 `--gc-sections`：

  ```console
  $ gcc -O2 -fuse-ld=lld -ffunction-sections -Wl,--gc-sections main2.c board2.c net2.c -o hooks_lld2
  ld.lld: error: undefined symbol: __start_init_hook
  >>> referenced by main2.c
  >>>               /tmp/ccoChDAS.o:(main)
  ...
  >>> the encapsulation symbol needs to be retained under --gc-sections properly; consider -z nostart-stop-gc (see https://lld.llvm.org/ELF/start-stop-gc)
  ```

  同樣的程式用 GNU ld 連結則照樣得到 4 個 hook。

同一等級內 `clock_setup` 先於 `early_console`，只因 `board.c` 在命令列排在 `net.c` 前面；同一檔案內的順序由編譯器決定（原文指出 gcc 反向輸出、clang 依宣告順序）。**需要確定順序就用等級區分，不要依賴連結順序**——Linux 的 initcall 分成 `core_initcall`、`subsys_initcall`、`device_initcall`、`late_initcall` 等多個等級，正是同一個道理。

> `##` 接合識別字、`#` 字串化的巨集手法見 [Ch 27 — 前置處理器 I](./27-preprocessor-basics.md)；Linux 核心的 initcall 機制在 [Ch 30 — Linux 核心與開源專案的物件模型](./30-object-models-in-the-wild.md) 有對照。

## 連結器與軟體最佳化

連結器是建置流程中**第一個看見所有目的檔**的工具。[Ch 37](./37-compiler-optimization.md) 的最佳化大多局限在單一轉譯單元，跨檔案的事只能等到連結時期。

### 移除死碼：--gc-sections 與 LTO

```c
/* util.c */
#include <string.h>

/* 只有 used_helper 會被呼叫 */
int used_helper(int x)
{
    return x * 3 + 1;
}

int unused_helper(const char *s)
{
    char buf[256];
    strncpy(buf, s, sizeof buf - 1);
    buf[sizeof buf - 1] = '\0';
    return (int) strlen(buf) + buf[0];
}

static const char unused_table[4096] = {1, 2, 3};
const char *unused_lookup(int i)
{
    return &unused_table[i & 4095];
}
```

```c
/* app.c */
#include <stdio.h>

int used_helper(int x);

int main(void)
{
    printf("%d\n", used_helper(14));
    return 0;
}
```

連結器以 **section** 為單位丟東西，但編譯器預設把整個檔案的函式放在同一個 `.text`——只要有一個函式被用到，整個 section 都得留。`-ffunction-sections -fdata-sections` 讓每個函式、每個變數各自一個 section，`--gc-sections` 再從進入點出發標記可達的 section，丟掉其餘：

```console
$ gcc -O2 app.c util.c -o a0 && size a0
   text	   data	    bss	    dec	    hex	filename
   6084	    624	      8	   6716	   1a3c	a0
$ gcc -O2 -ffunction-sections -fdata-sections -c app.c util.c
$ gcc app.o util.o -Wl,--gc-sections -Wl,--print-gc-sections -o a1 2>&1 | grep util
/usr/bin/ld: removing unused section '.text.unused_helper' in file 'util.o'
/usr/bin/ld: removing unused section '.text.unused_lookup' in file 'util.o'
/usr/bin/ld: removing unused section '.rodata.unused_table' in file 'util.o'
$ size a1
   text	   data	    bss	    dec	    hex	filename
   1452	    592	      8	   2052	    804	a1
$ gcc -O2 -flto app.c util.c -o a2 && size a2 && nm a2 | grep -E 'helper|lookup'; ./a2
   text	   data	    bss	    dec	    hex	filename
   1412	    600	      8	   2020	    7e4	a2
43
$ objdump -d --no-show-raw-insn a2 | sed -n '/<main>:/,/^$/p'
0000000000001060 <main>:
    1060:	endbr64
    1064:	sub    $0x8,%rsp
    1068:	mov    $0x2b,%edx
    ...
    107b:	call   1050 <__printf_chk@plt>
```

text 從 6084 降到 1452（大頭是 4 KiB 的表格）；`-flto` 降到 1412，而且 `nm` 找不到 `used_helper` 了——`main` 裡直接是 `mov $0x2b,%edx`（`0x2b` = 43 = 14×3+1），**跨檔案的行內展開加常數摺疊**，這是單獨編譯 `app.c` 時做不到的。

**LTO（link-time optimization）**的原理：編譯器把中間表示（gcc 的 GIMPLE、LLVM 的 IR）存進目的檔，連結器透過外掛（gcc 的 `liblto_plugin.so`，`gcc -v` 的 `collect2` 命令列開頭那個 `-plugin`）把整個程式交回編譯器最佳化。它的反面是：**只被 linker script 或硬體參照的物件，也會被當成死碼**。所以上一節的屬性有分工：`used` 擋編譯器（含 LTO 階段）刪除，`KEEP`/`retain` 擋連結器的 `--gc-sections`，缺一不可。

Linux 核心 v4.9 起有 `CONFIG_LD_DEAD_CODE_DATA_ELIMINATION`，難處正在於核心大量用 linker script 收集的 section（initcall、例外表）沒有程式碼直接參照，必須逐一 `KEEP`。Nicolas Pitre 在 LWN 的系列文章量測過：以 STM32 組態，LTO 讓核心縮小約 22%，但修改一個檔案後的重建時間由 3.7 秒變成 58.3 秒——LTO 每次都要重新最佳化整個程式。核心目前唯一的 LTO 選項是 v5.12 加入的 Clang LTO（含可平行化的 ThinLTO）。

### 縮減重定位資訊：DT_RELR

PIE 和共享程式庫的載入位址到執行期才決定，資料裡每一個「指向映像內部」的指標（函式指標表、C++ vtable、字串指標陣列）都需要一筆 `R_X86_64_RELATIVE` 動態重定位，由 `ld.so` 加上載入基底。x86-64 的 RELA 格式每筆 24 位元組（offset、info、addend 各 8）。Jan Hubička 2013 年的投影片指出 Firefox 的 `libxul` 有 20% 是重定位資訊。

```c
#include <stdio.h>

static void f(void)
{
    puts("ok");
}

/* 4096 個函式指標：PIE 中每一項都需要一筆 R_*_RELATIVE */
#define P4 f, f, f, f
#define P16 P4, P4, P4, P4
#define P256 P16, P16, P16, P16, P16, P16, P16, P16, \
             P16, P16, P16, P16, P16, P16, P16, P16
#define P4096 P256, P256, P256, P256, P256, P256, P256, P256, \
              P256, P256, P256, P256, P256, P256, P256, P256
void (*const table[4096])(void) = {P4096};

int main(void)
{
    table[4095]();
    return 0;
}
```

```console
$ gcc -O2 relr.c -o r0 && ./r0 && readelf -SW r0 | grep -E 'rela.dyn|data.rel.ro'
ok
  [10] .rela.dyn         RELA            0000000000000550 000550 0180c0 18   A  6   0  8
  [23] .data.rel.ro      PROGBITS        000000000001bdc0 01adc0 008000 00  WA  0   0 32
$ readelf -rW r0 | head -4
Relocation section '.rela.dyn' at offset 0x550 contains 4104 entries:
    Offset             Info             Type               Symbol's Value  Symbol's Name + Addend
000000000001bdb0  0000000000000008 R_X86_64_RELATIVE                         19160
```

`0x180c0` = 98496 位元組 = 4104 筆 × 24，執行檔 147168 位元組。注意 `const` 的 `table` 仍被放在可寫的 `.data.rel.ro`：它要先被重定位，之後才由 RELRO 改成唯讀。

`DT_RELR` 用**位元圖**壓縮相鄰的相對重定位：一個 8 位元組的位址項目，後面跟若干位元圖項目，每個位元圖的 63 個位元表示其後 63 個字組是否需要重定位。binutils 2.38 已經能產生：

```console
$ gcc -O2 -Wl,-z,pack-relative-relocs relr.c -o r1
$ readelf -SW r1 | grep -E 'rela.dyn|relr'
  [10] .rela.dyn         RELA            0000000000000570 000570 000078 18   A  6   0  8
  [12] .relr.dyn         RELR            0000000000000600 000600 000220 08   A  0   0  8
$ ./r1; echo "exit=$?"
./r1: /lib/x86_64-linux-gnu/libc.so.6: version `GLIBC_ABI_DT_RELR' not found (required by ./r1)
exit=1
```

重定位資訊從 98496 位元組縮成 120 + 544 位元組，執行檔縮成 48936 位元組——**但跑不起來**。glibc 2.36 才支援載入 `DT_RELR`，本課環境的 glibc 2.35 不認得。這個失敗是**設計好的**：GNU ld 讓執行檔依賴一個版本符號 `GLIBC_ABI_DT_RELR`，舊 glibc 找不到就拒絕載入，而不是默默不做重定位然後亂跳。對照組是 lld 14 的舊選項：

```console
$ gcc -O2 -fuse-ld=lld -Wl,--pack-dyn-relocs=relr relr.c -o r2
$ ./r2; echo exit=$?
Segmentation fault (core dumped)
exit=139
```

lld 早在第 7 版就能產生 RELR，但第 15 版才加入 `GLIBC_ABI_DT_RELR` 依賴。lld 14 產生的執行檔沒有這道保險，glibc 2.35 忽略看不懂的 `DT_RELR`，4096 個函式指標都沒被重定位，呼叫 `table[4095]` 就跳進未重定位的位址。**這就是「失敗要大聲」的價值。** 換到支援的環境（Docker `gcc:15`，glibc 2.41；該映像的 gcc 預設不是 PIE，需加 `-fPIE -pie`）：

```console
$ gcc -O2 -fPIE -pie -Wl,-z,pack-relative-relocs relr.c -o r1d && ./r1d
ok
```

延伸脈絡：Mozilla 的 Mike Hommey 在 2010 年做了 elfhack（連結後自行壓縮相對重定位，`libxul` 的重定位資訊由 7.5 MB 縮到 0.3 MB），這個需求最後在 ELF 格式層級以 `DT_RELR` 解決；Linux 核心自 v5.4 起也以 `CONFIG_RELR` 讓 arm64 核心映像用同一格式存放自身的重定位。

### 連結器的速度（簡述）

GNU ld（`ld.bfd`）建在通用的 BFD 程式庫上，支援大量格式，代價是速度。Ian Lance Taylor 2008 年寫了只處理 ELF 的 gold，但後來缺乏維護，binutils 2.44（2025）宣告淘汰。LLVM 的 lld 依 Peter Smith（FOSDEM 2019）的量測一般比 gold 快 2–3 倍、比 `ld.bfd` 快 5–10 倍（講者的數字，本課未重測）。lld 原作者 Rui Ueyama 另起的 mold 追求更徹底的平行化。換連結器之前要注意：本章的程式庫順序語意、`__start_`/`__stop_` 的 GC 預設，lld 和 GNU ld 都不一樣。

## 對比與取捨

| 方案 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 靜態程式庫 `.a` | 只抽需要的成員；執行檔自給自足、部署簡單 | 每個執行檔各帶一份；修 bug 要重連；順序陷阱 | 嵌入式、單一執行檔部署、容器內的小工具 |
| 共享程式庫 `.so` | 記憶體與磁碟共用；換程式庫不必重連 | 執行期才發現缺符號；載入成本；版本地獄 | 一般桌面/伺服器系統程式庫 |
| `-fcommon` | 相容舊程式碼 | 吞掉重複定義與型別不符 | 只在遷移舊專案時暫用 |
| weak symbol | 可覆寫的預設實作、選配功能 | 覆寫版本若在 `.a` 中不會被抽取；行為依賴工具鏈 | 中斷向量表、hook、測試替身 |
| linker script 收集 vs `__start_`/`__stop_` | 前者可控制位址與順序；後者不必寫 script | 後者受 lld GC 預設影響，需要 `retain` | 核心/韌體 vs 使用者空間外掛註冊 |
| `--gc-sections` vs LTO | 前者便宜、增量建置快 | LTO 能跨檔內嵌但重建慢；兩者都可能誤刪只被 script 參照的東西 | 韌體縮小體積；發行版建置 |

## 踩雷集錦

1. **「undefined reference 就是我沒寫那個函式」**：常見原因其實有四種——真的沒定義；程式庫順序錯（寫在 `.o` 前面或依賴順序反了）；C++ 呼叫 C 卻少了 `extern "C"`（看錯誤訊息裡有沒有參數型別）；函式是 `static` 或 hidden。先 `nm` 看提供端的符號名與大小寫，再看命令列順序。
2. **「升級 gcc 後出現 multiple definition，加 `-fcommon` 就好」**：那是 gcc 10 在幫你抓真正的 bug。正確修法是標頭檔放 `extern`、恰好一個 `.c` 放定義。
3. **「覆寫 weak 函式的版本放進 `.a` 就會生效」**：不會。weak 定義已滿足參照，`U` 清單裡沒有那個符號，連結器沒有理由去抽 `.a` 的成員。實測：把 `weak_override.o` 包成 `libover.a` 再連，輸出仍是 `default: done`。
4. **「extern 的邊界符號宣告成指標也一樣」**：宣告成 `extern char *_binary_blob_start` 會把資料內容當指標讀（本章的 `0x7266206f6c6c6548`）。連結器符號代表位址本身，必須宣告成陣列。
5. **「`ld -b binary` 只是嵌資料，沒有副作用」**：在 x86-64 上它會讓整個行程的堆疊變成可執行，而 binutils 2.39 以前連警告都沒有。用 `objcopy --add-section .note.GNU-stack=/dev/null`，並用 `readelf -lW | grep GNU_STACK` 檢查。
6. **「section 收集的項目一定緊密排列」**：編譯器可能提高對齊，連結器在項目間補洞。一律明確寫 `aligned(sizeof 的對齊)`，或乾脆收集**指標**而不是結構（Linux 的 initcall 收集的就是函式指標或相對偏移）。

## 進階：再往深一層

**連結器腳本長什麼樣？** `ld --verbose` 會印出預設腳本，你會在裡面找到 `PROVIDE_HIDDEN (__init_array_start = .);`、`.preinit_array`、`.bss` 的排法——[Ch 41](./41-c-runtime.md) 的精簡 libc 正是靠這些符號找到建構子陣列。嵌入式專案的腳本另外會用 `MEMORY` 區塊與 `AT>` 把 `.data` 的 VMA 放 RAM、LMA 放 flash（同樣在 Ch 41 的裸機段落）。

**符號版本（symbol versioning）**：`readelf -rW r0` 裡的 `puts@GLIBC_2.2.5` 就是版本。glibc 用它讓同一個 `.so` 同時提供新舊行為的同名函式，舊執行檔繼續綁到舊版本。`GLIBC_ABI_DT_RELR` 也是借用這套機制做「能力宣告」。

**面試題型**：「`static` 函式和非 `static` 函式在目的檔裡差在哪？」（`t` vs `T`、能否被其他檔解析、編譯器能否放心內嵌刪除）；「為什麼 `-lm` 要放在最後？」（單趟掃描）；「COMDAT 解決什麼問題？」（inline 函式與樣板的多份定義）。

> 與既有課重疊：[c_interview Ch 20 — 連結器與符號](../c_interview/20-linker-symbols.md) 以面試角度整理同一批概念，可當複習卡。

## 動手練習

1. 把本章 `c_parts.o` 中 `z_global` 那筆重定位（偏移 `0x55`）照「S + A − P」手算一次，再用 `objdump -d sample` 驗證填進去的位移。
2. 在 `libx.a`/`liby.a` 實驗中，用 `--print-map` 解釋 `a.o b_y32.o -lx -ly -lx` 成功時第二個 `-lx` 抽了哪個成員、為了誰。
3. 把 `init_hook` 改成收集 `void (*)(void)` 函式指標（而不是結構），拿掉 `aligned(8)`，觀察還會不會錯位，並解釋原因。
4. 把 `weak_override.o` 包成 `libover.a`，想辦法讓它在不改原始碼的情況下仍能生效（提示：`-Wl,--whole-archive`，或 `-u` 選項強迫連結器先產生一個未定義參照）。
5. 用 `ld --verbose | grep -n -A3 init_array` 找出 `__init_array_start` 是在哪裡定義的。

## 本章重點整理

- 目的檔 = 機械碼與資料 + 符號表 + 重定位項目；`nm` 的大小寫分辨全域與區域，`U` 是待兌現的空白。
- 連結器兩步：符號解析（每個參照找到唯一定義）與重定位（`R_X86_64_PC32` = S + A − P，−4 補償 `%rip` 指向下一道指令）。
- strong 只能有一個；weak 可被 strong 覆寫；common model 會合併同名 tentative definition——gcc 10 起預設 `-fno-common`。
- GNU ld 單趟由左至右掃描，`.a` 只抽能解析當下未定義符號的成員、處理過不回頭；lld 不受順序影響。
- section 給連結器看、segment 給載入器看；`.bss` 只記大小；缺 `.note.GNU-stack` 會讓堆疊可執行。
- `__start_`/`__stop_`、`used`/`retain`/`KEEP`、`aligned` 共同構成「分散註冊、連結時收集」的模式。
- `--gc-sections` 以 section 為單位刪死碼，LTO 能跨檔內嵌；`DT_RELR` 壓縮相對重定位，舊 glibc 以版本符號拒載而非默默出錯。

## 自我檢核

- [ ] 不看筆記，能畫出 `R_X86_64_PC32` 的 S、A、P 分別指什麼，並說明 addend 為何常是 −4？
- [ ] 同事說「我把 `-lfoo` 移到命令列最前面就壞了」，你能用連結器演算法向他解釋，並給出兩種修法嗎？
- [ ] 能說出 strong/weak/common 三者遇到同名時各自的處理規則，以及 gcc 10 為何改預設？
- [ ] 能解釋為什麼 `ld -b binary` 會讓 NX 失效，以及怎麼檢查一個執行檔有沒有這個問題？
- [ ] 如果要在你的專案做「每個外掛自己註冊」的機制，你會用哪些屬性、各自防止什麼？在什麼情況下你**不會**用這個手法？
- [ ] 能說明 COMDAT 和 weak symbol 都在解決「多份定義」，但兩者的層級差在哪？

## 延伸閱讀

### 論文

- **[The missing link: explaining ELF static linking, semantically](https://www.cl.cam.ac.uk/~pes20/rems/papers/oopsla-elf-linking-2016.pdf)** — Kell, Mulligan, Sewell，OOPSLA（2016）
  - **核心貢獻**：以形式語意描述 GNU ld 的 ELF 靜態連結行為，並系統整理 C 程式實際依賴的連結器功能（weak、section 收集、linker script、COMDAT…）。
  - **讀哪裡**：Section 2「Linking in practice」列舉了大量本章出現的手法及其在真實程式碼中的使用頻率；形式化部分（後半）可以跳過。
  - **和本章的關聯**：本章每一節幾乎都能在 Section 2 找到對應的真實案例；讀完會明白「最小的 C 程式對連結器也很複雜」這句話。前提：讀完本章即可。

### 部落格 / 技術文章

- **[Beginner's Guide to Linkers](https://www.lurklurk.org/linkers/linkers.html)** — David Drysdale
  - **這篇說什麼**：原文主要參考資料之一，從 C 的宣告/定義一路講到靜態、動態程式庫與 C++ 名稱修飾、靜態初始化。
  - **讀哪裡**：整篇，特別是「What The Linker Does: Part 1/2」與「Static Libraries」。
  - **為什麼值得讀**：本章的 `c_parts.c`、程式庫依賴例子都源自這篇的結構，用英文再讀一次會鞏固印象。
- **[Linkers 系列（20 篇）](https://www.airs.com/blog/archives/38)** — Ian Lance Taylor（gold 作者）
  - **這篇說什麼**：連結器作者親自解釋重定位、符號解析、section/segment、COMDAT、符號版本、連結器速度。
  - **讀哪裡**：Part 1–7（基礎與重定位）、Part 9（符號版本）、Part 20（COMDAT 等雜項）。
  - **為什麼值得讀**：從實作者角度解釋「為什麼這樣設計」，是本章的深化版。
- **[MaskRay 的部落格](https://maskray.me/blog/)** — Fangrui Song（lld 維護者）
  - **這篇說什麼**：「Metadata sections, COMDAT and SHF_LINK_ORDER」「Relative relocations and RELR」等文章把本章的 section 收集、RELR 講到工具鏈實作細節，並對照 GNU ld 與 lld 的差異。
  - **讀哪裡**：搜尋站內的 RELR、start-stop-gc、COMDAT 相關文章。
  - **為什麼值得讀**：本章 lld `start-stop-gc` 錯誤與 RELR 版本依賴的來龍去脈都在這裡。前提：熟悉本章術語。

### 官方文件 / Spec

- **[GNU ld 手冊](https://sourceware.org/binutils/docs/ld/)**
  - **讀哪裡**：「Options」中 `--start-group`、`--gc-sections`、`--wrap`、`-z` 系列；「Scripts」一章的 `KEEP`、`PROVIDE`、`AT>`。
  - **注意**：linker script 一章很長，先讀「Simple Example」與「SECTIONS Command」。
- **[ELF x86-64 psABI](https://gitlab.com/x86-psABIs/x86-64-ABI)**
  - **讀哪裡**：「Relocation Types」表格，`R_X86_64_PC32`、`PLT32`、`GOTPCREL`、`RELATIVE` 的計算式都在這張表。

### 書籍

- **《Computer Systems: A Programmer's Perspective》（CSAPP，3rd ed.）** — Bryant & O'Hallaron（Pearson，2015）
  - **這本書的定位**：本章的教科書版本，例子較少但推導完整。
  - **讀哪幾章**：第 7 章 7.1–7.9（符號解析、重定位、靜態程式庫）；7.6.3 的程式庫掃描演算法與本章相同。
- **《Linkers and Loaders》** — John R. Levine（Morgan Kaufmann，1999；[作者線上版](https://www.iecc.com/linker/)）
  - **這本書的定位**：連結器的專書。書很老（1999），ELF 細節有些過時，但概念完全適用。
  - **讀哪幾章**：第 6 章（Symbol management）、第 7 章（Relocation）、第 11 章（C++ 相關）。

靜態連結把所有東西在建置時期接好；下一章把「最後一段連結」延到執行期，看 `ld.so` 怎麼在每次啟動時重做一遍，以及這個延遲怎麼變成 `LD_PRELOAD` 這種強大（也危險）的工具。

→ [Ch 40 動態連結：PLT/GOT、LD_PRELOAD 與 dlopen](./40-dynamic-linking.md)
