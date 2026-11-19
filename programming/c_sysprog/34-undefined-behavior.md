# Ch 34 — 未定義行為：分類、編譯器推論與偵測

> **目標**：能精確區分 undefined、unspecified、implementation-defined、locale-specific 四種行為並引出條款；理解編譯器為什麼可以、以及怎麼利用未定義行為（UB）做推論，能預測並實測同一段程式在 `-O0` 與 `-O2` 的差異；會用 UBSan、ASan、TSan 與 `-fwrapv`、`-fno-strict-aliasing` 一類選項偵測或控制 UB；並知道這個主題在 Linux 核心與 WG14 中的爭議與走向。

> **對應原文**：[你所不知道的 C 語言：未定義/未指定行為篇](https://hackmd.io/@sysprog/c-undefined-behavior) 的「從 C 語言試題談起」「四種行為」「為何 C 語言保留未定義行為」「編譯器只保證可觀察行為」「常見的未定義行為」「偵測與控制未定義行為」「開發社群的爭議與修正」「標準的下一步」等節

> **環境**：WSL2 Ubuntu 22.04 x86_64，gcc 11.4、clang 14、glibc 2.35。反組譯用 `gcc/clang -S -masm=intel`，gcc 加 `-fcf-protection=none` 省去 Ubuntu 預設插入的 `endbr64`。**未定義行為的結果本來就不保證可重現**：本章所有輸出只說明「這兩個編譯器版本在這台機器上」的實際表現，換版本就可能不同——這正是重點。原文以 GCC 13/14、Clang 18/20 實測，結果與本章不同處會標出。

## 為什麼需要這個？

先做一題經典考題：

```c
#include <stdio.h>

static int func(int *x) { return (*x)++; }

int main(void)
{
    int i = 10;
    i = i++ + ++i;                                    /* unsequenced：UB */
    printf("i = %d\n", i);

    int n = 0;
    printf("%d %d %d\n", func(&n), func(&n), func(&n)); /* 只是 unspecified */
    return 0;
}
```

```
$ gcc seq.c -o s && ./s                  # 不加 -Wall：沒有任何警告
i = 22
2 1 0
$ clang seq.c -o s && ./s
seq.c:8:10: warning: multiple unsequenced modifications to 'i' [-Wunsequenced]
i = 22
0 1 2
$ gcc -Wall seq.c
seq.c:8:7: warning: operation on ‘i’ may be undefined [-Wsequence-point]
```

第一行兩個編譯器都印 22，但「答案是 22」這句話本身不成立：C11 6.5 §2 規定，對同一純量物件的副作用若與另一個副作用（或使用其值的計算）**沒有先後順序**（unsequenced），行為未定義。`i++`、`++i` 與指定運算三者都修改 `i`，彼此 unsequenced，所以整個運算式是 UB，22 只是巧合。

第二行看起來一樣危險，其實性質完全不同：gcc 印 `2 1 0`、clang 印 `0 1 2`，`-O0`、`-O2` 都一樣，而且**兩者都正確**。C11 6.5.2.2 §10 規定函式呼叫之間是 *indeterminately sequenced*——先後未定，但不會交錯；三次呼叫各自修改 `n`，不會同時發生。所以這只是**引數求值順序未指定**，不是 UB。

這一題濃縮了本章的核心：同樣是「標準沒規定結果」，有的只是「幾種合法結果選一個」，有的是「標準完全不要求任何事」，後者會讓編譯器做出你想不到的推論。分不清楚，就看不懂為什麼 Linux 核心要用 `-fno-strict-aliasing -fno-delete-null-pointer-checks -fno-strict-overflow` 編譯，也看不懂為什麼一個溢位檢查會被編譯器「刪掉」而造成 CVE。

> 有號溢位的數學背景見 [Ch 22](./22-integer-overflow.md)，effective type 與 strict aliasing 的條款見 [Ch 7](./07-pointers-by-the-standard.md)，編譯器最佳化的整體架構見 [Ch 37](./37-compiler-optimization.md)。

## 先建立直覺

把 C 標準想成一份**合約**，編譯器是承包商：

```
             程式設計者                                編譯器
    「我保證我的程式不會做 X、Y、Z」  ──合約──►  「那我保證產生的程式在可觀察行為上
     （X、Y、Z = 所有的 UB）                     和抽象機器一致」

     你違約（觸發 UB）之後：                       承包商不再受任何約束——
                                                  不只是「那一行算錯」，
                                                  而是「整個執行」都沒有保證
```

關鍵在於承包商**可以利用你的承諾**：既然你保證有號整數不溢位，`a + 100 > a` 就必然為真，檢查可以刪掉；既然你保證不解參考 NULL，`tun->sk` 之後 `tun` 就不是 NULL，後面的 `if (!tun)` 可以刪掉。編譯器不是在「懲罰」錯誤的程式，它只是在最佳化「正確的程式」，而你的程式剛好不在那個集合裡。

```
   四種「標準沒完全規定」的行為，依嚴重度排列：

   locale-specific ──► implementation-defined ──► unspecified ──► undefined
   依地區慣例，         實作選一種，               實作選一種，      沒有任何要求
   實作要寫文件         而且要寫文件               不必寫文件，       （也沒有「合法結果」
                                                  每次可以不同        這個概念）
   islower('é')        char 有號嗎？              引數求值順序       有號溢位、NULL 解參考
                       負數右移怎麼補？                              越界、data race
```

## 四種行為：條款與例子

C11 3.4.1–3.4.4（C23 3.5.1–3.5.4）定義這四個術語：

| 術語 | C11 條款 | 定義要點 | 例子 |
|---|---|---|---|
| implementation-defined | 3.4.1 | 未指定行為中，**實作必須寫文件說明怎麼選** | `char` 是否有號、有號整數右移、超出範圍的整數轉成有號型別 |
| locale-specific | 3.4.2 | 依國家、文化、語言慣例，實作寫文件 | `islower` 對 26 個拉丁小寫字母以外的字元、小數點符號 |
| undefined | 3.4.3 | 使用不可攜或錯誤的構造、錯誤的資料，**標準不施加任何要求** | 有號溢位、移位量 ≥ 寬度、除以零、NULL 解參考、越界、data race |
| unspecified | 3.4.4 | 標準給出兩種以上可能，不要求每次選哪一個 | 函式引數求值順序、`f() + g()` 誰先呼叫 |

未指定與實作定義的差別在**文件**：兩者都有幾個合法結果，實作定義要求實作公開選擇並保持一致（gcc 的選擇寫在 [Integers implementation](https://gcc.gnu.org/onlinedocs/gcc/Integers-implementation.html)），未指定則連每次一致都不必。C99 Rationale 第 3 節的說法很精準：未指定行為「給實作者一些自由，但不及於拒絕轉譯，因為所有可能的行為都是正確的」。

未定義行為完全不同。3.4.3 的 NOTE 列出可能的後果：從「完全忽略、結果不可預測」，到「以文件化的方式表現環境特性」，到「中止轉譯或執行」。[C FAQ 11.33](https://c-faq.com/ansi/undef.html) 說得更直白："Anything at all can happen... it may fortuitously do exactly what the programmer intended." 最後這種最危險：程式「剛好」正常，換個編譯器版本才露餡。

**locale-specific 的一個真實事件**：程式在不同地區設定下走不同路徑，某些路徑只在特定設定下才被執行。2018 年 Patrick Wardle 揭露，Apple 在地區設為中國時隱藏台灣國旗 emoji 的程式碼，在某些沒有地區設定的組合下 `CFLocaleGetValue()` 回傳 NULL，隨即被解參考，收到台灣國旗 emoji 的訊息類 app 當機（[CVE-2018-4290](https://www.cve.org/CVERecord?id=CVE-2018-4290)，iOS 11.4.1 修正）。嚴格說這不是標準意義的 locale-specific behavior，而是「只在特定地區設定下才走到的一般 UB（NULL 解參考）」——它說明了測試覆蓋率要包含 locale。

C23 對 3.5.3 新增 Note 3，後面「可觀察行為」一節會用到：

> Any other behavior during execution of a program is only affected as a direct consequence of the concrete behavior that occurs when encountering the erroneous or non-portable program construct or data. In particular, all observable behavior (5.1.2.4) appears as specified in this document when it happens before an operation with undefined behavior in the execution of the program.

也就是說，UB **不能回溯**抹除它之前已經發生的可觀察行為。這是說明性的註記，但它明白排除了「編譯器可以把 UB 提前到可觀察行為之前」的解讀。

## 為什麼 C 要保留未定義行為

### 理由一：硬體差異

C 為了在差異極大的硬體上產生有效率的程式碼而生。有號溢位在某些機器上回繞、在某些機器上 trap；除以零可能 trap 也可能得到某個值（本機 x86-64 的 `idiv` 會 trap，arm64 的 `sdiv` 得 0，[Ch 35](./35-stream-io-and-signals.md) 實測）。標準若規定其中一種，其他平台就得在每次運算前後插入檢查。C99 Rationale 第 3 節：

> Undefined behavior gives the implementor license not to catch certain program errors that are difficult to diagnose. It also identifies areas of possible conforming language extension: the implementor may augment the language by providing a definition of the officially undefined behavior.

後半句很重要：gcc 的 `-fwrapv` 規定有號溢位回繞，就是這種「合法的擴充」。

### 理由二：最佳化的前提

既然程式保證不觸發 UB，編譯器就能據此推論：

```c
void bar(void);

int func(unsigned char x)
{
    int value = 2147483600; /* 假設 int 是 32 位元 */
    value += x;
    if (value < 2147483600)
        bar();
    return value;
}
```

```
gcc -O2:                       gcc -O2 -fwrapv:                 clang -O2:
func:                          func:                            func:
	movzx eax, dil                 push   r12                       lea eax, [rdi + 2147483600]
	add   eax, 2147483600          movzx  r12d, dil                 ret
	ret                            add    r12d, 2147483600
                                   cmp    r12d, 2147483599
                                   jle    .L5          ; 呼叫 bar
                                   ...
```

`x` 不可能是負數，有號溢位是 UB，所以 `value >= 2147483600` 恆成立，`bar()` 的呼叫整個消失。加 `-fwrapv` 後溢位有定義（`x > 47` 時真的會回繞成負數），比較和呼叫就回來了。

更有說服力的例子是 Chandler Carruth 在 CppCon 2016 用的 bzip2 `mainGtU`：比較區塊中兩個位置的位元組，索引一路遞增。把索引從 `uint32_t` 改成 `int32_t`，其他什麼都不改，用 clang 14 `-O2` 編三步的簡化版：

```
gt_u (uint32_t 索引)：                    gt_s (int32_t 索引)：
	mov  eax, edi                            movsxd rdi, edi          ; 開頭擴充一次
	mov  ecx, esi                            movsxd rcx, esi
	mov  cl, byte ptr [rdx + rcx]            mov  al, byte ptr [rdx + rcx]
	cmp  byte ptr [rdx + rax], cl            cmp  byte ptr [rdx + rdi], al
	jne  .LBB0_3                             jne  .LBB1_3
	lea  eax, [rdi + 1]      ; 32 位元加一   mov  al, byte ptr [rcx + rdx + 1]   ; 位移直接寫進定址
	lea  ecx, [rsi + 1]      ; 並回繞清高位  cmp  byte ptr [rdi + rdx + 1], al
	mov  cl, byte ptr [rdx + rcx]            jne  .LBB1_3
	cmp  byte ptr [rdx + rax], cl            mov  al, byte ptr [rdi + rdx + 2]
	jne  .LBB0_3                             ...
	add  edi, 2 ...
```

為什麼差這麼多？`block[i1]` 等同 `*(block + i1)`，x86-64 上位址是 64 位元，32 位元索引每次使用都得擴充成 64 位元。**無號版本**：`i1++` 必須模 $2^{32}$ 回繞（`0xFFFFFFFF + 1 == 0`，位址是 `block + 0` 而不是 `block + 0x100000000`），所以每一步都要用 32 位元 `lea` 算出新索引（寫入 32 位元暫存器會清除高 32 位元，一道指令完成「加一、回繞、零擴充」）。**有號版本**：溢位是 UB，編譯器可以假設 `i1 + 1` 的 64 位元值等於「擴充後再加一」，於是開頭擴充一次，之後的偏移量直接寫進定址模式的位移欄位 `[rdi + rdx + 1]`。這項最佳化是**允許而非要求**：原文實測 GCC 14 並未利用它。

### 不是每個語言都需要這份自由

Java 語言規格明言不包含 "array accesses without index checking"，連 data race 也保證 "cannot cause incorrect behavior such as returning the wrong length for an array"（JLS §17.4.5）。代價是每次陣列存取都有邊界檢查（JIT 能消除一部分）。C 選擇把這份成本交給程式設計者。

## 編譯器只保證可觀察行為（as-if rule）

推論的邊界是**可觀察行為**。C11 5.1.2.3 §6 只要求三件事符合抽象機器的語意：`volatile` 物件的存取、程式終止時寫入檔案的資料、互動裝置的輸入輸出。其他讀寫只要最終結果相同，編譯器都可以改寫或刪除，這就是 **as-if rule**。

這條界線**不涉及 UB**，卻同樣會刪掉你在意的程式碼：

```c
#include <string.h>

void use(char *pw);

void login(void)
{
    char pw[64];
    use(pw);                  /* 讀入並驗證密碼 */
    memset(pw, 0, sizeof pw); /* 之後沒人再讀 pw */
}

void login2(void)
{
    char pw[64];
    use(pw);
    explicit_bzero(pw, sizeof pw);
}
```

```
$ gcc -O2 -S dse.c
login:
	subq	$88, %rsp
	...
	call	use@PLT
	movq	72(%rsp), %rax        ; 堆疊保護檢查
	subq	%fs:40, %rax
	jne	.L5
	addq	$88, %rsp
	ret                         ; memset 不見了
login2:
	...
	call	use@PLT
	movl	$64, %edx
	movl	$64, %esi
	movq	%rbp, %rdi
	call	__explicit_bzero_chk@PLT   ; 保留（Ubuntu 的 _FORTIFY_SOURCE 換成檢查版）
```

`pw` 在 `login` 返回後生命週期結束，`memset` 之後沒有讀取，這次寫入沒有可觀察效果，dead store elimination 把它刪了，密碼留在已釋放的堆疊上。清除敏感資料要用 glibc/BSD 的 `explicit_bzero`，或 C23 的 `memset_explicit`（依原文，glibc 2.43 起提供）。**安全需求若無法用語言語意表達，最佳化器就看不見它。**

### 可觀察行為與 UB 的交界：Uecker 的三個除法

Martin Uecker（WG14 成員）用這個例子說明 C23 Note 3 要處理的問題：

```c
#include <stdbool.h>

volatile int x;

int foo(int a, int b, bool store_to_x)
{
    if (!store_to_x)
        return a / b;
    x = b;            /* volatile 寫入：可觀察行為 */
    return a / b;
}
```

`store_to_x` 為真、`b` 為 0 時，正確順序是：先寫 `x`（可觀察），再除以零（UB）。依 Note 3，寫入必須出現。實測：

```
gcc 11 -O2:                          clang 14 -O2:
foo:                                 foo:
	mov  eax, edi                        mov  eax, edi
	mov  r8d, edx                        test edx, edx
	cdq                                  je   .LBB0_2
	idiv esi        ; 除法先做！         mov  dword ptr [rip + x], esi   ; 先寫
	test r8b, r8b                        cdq
	je   .L1                             idiv esi                        ; 再除
	mov  DWORD PTR x[rip], esi           ret
	ret
```

gcc 把可能 trap 的 `idiv` **提到條件判斷與 volatile 寫入之前**：`b == 0` 時行程在寫 `x` 之前就收到 SIGFPE，`x` 永遠不會被寫。clang 保留了正確順序。這就是 GCC [PR104800](https://gcc.gnu.org/bugzilla/show_bug.cgi?id=104800)，Uecker 在 2022 年回報；依原文整理，Paul E. McKenney 主張「可能 trap 的運算不得跨越 volatile 存取移動，否則會破壞裝置驅動程式」，GCC 維護者則一度認為標準並不要求修正，討論至 2026 年仍在進行。這個例子說明：**「UB 能往回影響多遠」到今天都還是活的爭議**，而且答案取決於編譯器。

## 常見的未定義行為，逐一實測

以下每個例子都用 gcc 11 與 clang 14、`-O0` 與 `-O2` 各跑一次。必要時用 `__attribute__((noinline))` 避免編譯器在呼叫端直接算出結果。

### 1. 有號整數溢位（C11 6.5 §5）

```c
#include <stdio.h>

__attribute__((noinline)) int foo(int a)
{
    if (a + 100 > a)
        printf("%d GT %d\n", a + 100, a);
    else
        printf("%d LT %d\n", a + 100, a);
    return 0;
}

int main(void)
{
    foo(100);
    foo(0x7fffffff);
    return 0;
}
```

| `foo(0x7fffffff)` | `-O0` | `-O2` | `-O2 -fwrapv` |
|---|---|---|---|
| gcc 11 | `-2147483549 GT 2147483647` | `GT` | `LT` |
| clang 14 | `-2147483549 LT 2147483647` | `GT` | `LT` |

gcc **連 `-O0` 都印 GT**：GCC 前端在化簡運算式時就把 `a + 100 > a` 變成恆真，跟最佳化等級無關（依原文，GCC 8 起如此）。clang `-O0` 照字面做加法比較，才印 LT。印出來的 `-2147483549` 和 `GT` 自相矛盾，正是 UB 的樣貌。這就是 GCC [PR30475](https://gcc.gnu.org/bugzilla/show_bug.cgi?id=30475)：有人回報 `assert(a + 100 > a)` 被刪除，以 INVALID 結案，回覆是 "signed type overflow is undefined by the C standard, use unsigned int for the addition or use -fwrapv."。

UBSan 在執行時抓得到：

```
$ gcc -O0 -fsanitize=undefined ovf.c -o ou && ./ou
ovf.c:5:17: runtime error: signed integer overflow: 2147483647 + 99 cannot be represented in type 'int'
ovf.c:8:9: runtime error: signed integer overflow: 2147483647 + 100 cannot be represented in type 'int'
```

注意第一則是 `+ 99`：gcc 已經把 `a + 100 > a` 改寫成 `a + 99 >= a` 之類的形式，UBSan 檢查的是改寫後的運算。溢位檢查要在運算**之前**做，或用 `__builtin_add_overflow` / C23 `ckd_add`（[Ch 22](./22-integer-overflow.md)）。

### 2. 移位量 ≥ 型別寬度（C11 6.5.7 §3）

```c
#include <stdio.h>

int foo(int x, int y)
{
    x >>= (sizeof(int) << y);   /* y = 3：位移 32 位元 */
    return x;
}

int main(void)
{
    printf("%d\n", foo(1000, 3));
    return 0;
}
```

連跑三次：

| | `-O0` | `-O2` |
|---|---|---|
| gcc 11 | 1000 1000 1000 | 0 0 0 |
| clang 14 | 1000 1000 1000 | -42930968 -1816034248 -579786584 |

`-O0` 都是 1000：x86-64 的 `sar` 只取移位量的低 5 位元，32 被當成 0。gcc `-O2` 把 `foo` 內嵌後在編譯時期算出 0。clang `-O2` **每次執行結果都不同**：它把結果視為沒有意義的值（LLVM 的 `poison`），直接用暫存器裡殘留的內容。如果給 `foo` 加 `noinline`，兩個編譯器 `-O2` 都印 1000（實測）——UB 的結果甚至取決於內嵌決策。UBSan 回報 `shift exponent 32 is too large for 32-bit type 'int'`。另一條常被忽略的：C11 6.5.7 §4 規定有號左移若結果無法表示也是 UB，所以 `1 << 31` 在 32 位元 `int` 上就是 UB，要寫 `1u << 31`。

### 3. 除以零（C11 6.5.5 §5）

```c
#include <stdio.h>

int testdiv(int i, int k)
{
    if (k == 0)
        printf("found divide by zero\n");
    return i / k;
}

int main(void)
{
    int i = testdiv(1, 0);
    return i;
}
```

stdout 導向檔案時：

| | 結果 | 檔案內容 |
|---|---|---|
| gcc 11 `-O0`、clang 14 `-O0` | SIGFPE（exit 136） | 空 |
| gcc 11 `-O2` | SIGILL（exit 132） | 空 |
| clang 14 `-O2` | 正常結束，exit 21 | `found divide by zero` |

看 `-O2` 的 `main`：

```
gcc:                             clang:
main:                            main:
	...                              lea rdi, [rip + .Lstr]
	call  puts@PLT                   jmp puts@PLT          ; TAILCALL
	ud2          ; 刻意的非法指令
```

`testdiv` 被內嵌後 `k == 0` 是編譯時期已知的事實。gcc 在 `puts` 之後放 `ud2`（「這裡不可能到達」的陷阱指令），clang 則**整個刪掉除法**，`main` 以尾呼叫 `puts` 結束，exit status 是 `puts` 的回傳值 21（glibc 傳回寫出的位元組數）。三種結果都發生在呼叫 `printf` 之後，但訊息能否看到還取決於緩衝：導向檔案時 stdout 全緩衝，行程被 signal 殺掉時訊息跟著緩衝區消失，只有「正常結束」的 clang `-O2` 版本把它寫出來。這也說明 C23 Note 3 保護不到它：導向檔案的輸出要到程式終止才算可觀察，而終止發生在除法之後。真正的修正是 `k == 0` 時回傳錯誤，不讓除法執行。`INT_MIN / -1`（商無法表示）同樣是 UB（C11 6.5.5 §6），x86-64 上也觸發 SIGFPE。

### 4. NULL 解參考與檢查被刪除（C11 6.5.3.2 §4）

Linux 2.6.30 的 tun 驅動程式（LWN〈[Fun with NULL pointers](https://lwn.net/Articles/342330/)〉）：

```c
static unsigned int tun_chr_poll(struct file *file, poll_table *wait)
{
    struct tun_file *tfile = file->private_data;
    struct tun_struct *tun = __tun_get(tfile);
    struct sock *sk = tun->sk;          /* 先解參考 */
    unsigned int mask = 0;

    if (!tun)                           /* 才檢查 */
        return POLLERR;
```

`tun` 為 NULL 時核心 oops，看起來最壞是阻斷服務。但 gcc 預設的 `-fdelete-null-pointer-checks` 推論：`tun` 已被解參考，所以不是 NULL，`if (!tun)` 可以刪。再加上當時可以把記憶體 `mmap` 到位址 0（SELinux 政策、SVR4 `personality()` 兩個缺口），攻擊者在位址 0 放偽造的 `tun_struct`，被刪除的檢查讓核心繼續使用它——Brad Spengler 寫出提權攻擊，即 [CVE-2009-1897](https://www.cve.org/CVERecord?id=CVE-2009-1897)。Linux v2.6.31 修正驅動程式，並加上 `-fno-delete-null-pointer-checks`，頂層 Makefile 至今保留。去掉核心細節：

```c
struct sock;
struct tun { struct sock *sk; };

unsigned poll_tun(struct tun *tun)
{
    struct sock *sk = tun->sk; /* 先解參考 */
    if (!tun)                  /* 再檢查 */
        return 1;
    return sk != 0;
}
```

```
gcc 11 -O2:                    gcc 11 -O2 -fno-delete-null-pointer-checks:   clang 14 -O2:
poll_tun:                      poll_tun:                                     poll_tun:
	xor eax, eax                   test rdi, rdi                                 test rdi, rdi
	cmp QWORD PTR [rdi], 0         je   .L3                                      je   .LBB0_1
	setne al                       xor  eax, eax                                 xor  eax, eax
	ret                            cmp  QWORD PTR [rdi], 0                       ...
```

gcc 11 刪掉檢查，clang 14 保留（原文實測 Clang 18 也會刪，這是版本差異）。修正方式只有一個：**先檢查、再使用**；UB 發生之後的程式碼無法「補救」它。

Chris Lattner 在〈What Every C Programmer Should Know About Undefined Behavior #2/3〉用一個更小的函式說明**最佳化的順序決定結果**：

```c
void contains_null_check(int *P)
{
    int dead = *P;
    if (P == 0)
        return;
    *P = 4;
}
```

```
gcc 11 -O2:                          clang 14 -O2:
contains_null_check:                 contains_null_check:
	mov DWORD PTR [rdi], 4               test rdi, rdi
	ret                                  je   .LBB0_2
                                         mov  dword ptr [rdi], 4
```

先跑「多餘 NULL 檢查消除」：`*P` 已解參考，檢查被刪（gcc 的結果）；先跑「死碼消除」：`dead` 沒用，讀取被刪，檢查失去依據而保留（clang 的結果）。Lattner 的結論："both ... are perfectly valid optimized forms ... according to the standard"。

### 5. 指標算術回繞（C11 6.5.6 §8）

指標加減的結果必須落在同一陣列物件之內或最後一個元素之後。所以 `p + n < p`（「加上去之後變小了，代表回繞」）這個檢查依賴 UB：

```c
int wraps(char *p, unsigned long n) { return p + n < p; }
```

```
gcc 11 -O2 / clang 14 -O2（兩者相同）：
wraps:
	shr rsi, 63        ; 只看 n 的最高位元
	mov rax, rsi
	ret
```

編譯器推論：指標加法不回繞，所以 `p + n < p` 只可能在 `n` 被當成負數（最高位元為 1）時成立，比較被換成「`n >> 63`」。CERT 在 2008 年的 [VU#162289](https://www.kb.cert.org/vuls/id/162289) 就是針對 GCC 4.2 起刪除 `buf + len < buf` 檢查發出警告。正確寫法是改成整數比較：`len > (size_t)(end - buf)`，或先轉 `uintptr_t` 再做整數運算。（原文另有 `-m32` 的 32 位元實驗；本機沒有安裝 `gcc-multilib`，`gcc -m32` 連結失敗，**該段未實測**。）

### 6. strict aliasing：以不同型別存取同一物件（C11 6.5 §7）

```c
#include <stdio.h>

__attribute__((noinline)) int set(int *i, float *f)
{
    *i = 1;
    *f = 0.0f;      /* 編譯器假設 float* 不會指到 int 物件 */
    return *i;
}

int main(void)
{
    int x;
    printf("%d\n", set(&x, (float *) &x));
    return 0;
}
```

| | `-O0` | `-O2` | `-O2 -fno-strict-aliasing` |
|---|---|---|---|
| gcc 11 | 0 | **1** | 0 |
| clang 14 | 0 | **1** | 0 |

6.5 §7 規定物件只能透過相容型別、對應的有號/無號型別、字元型別等左值存取；以 `float` 寫 `int` 物件是 UB。編譯器因此假設 `int *` 與 `float *` 不指向同一物件，`*f = 0.0f` 不會改變 `*i`，`return *i` 直接回傳常數 1。需要重新解讀物件表示法時，可攜作法是 `memcpy`（[Ch 33](./33-tricks-pointers-and-performance.md) 的 `read_float`）。

同型別的指標無法用 aliasing 規則區分，`restrict`（C11 6.7.3.1）讓程式設計者**承諾**沒有重疊：

```c
#include <stdio.h>

__attribute__((noinline))
void foo(int *a, int *b, int *RESTRICT y, int z)
{
    while (*y < z) {
        int x = *a + *b;
        *y += x;
    }
}

int main(void)
{
    int n = 1;
    foo(&n, &n, &n, 5566); /* 違反 restrict 的承諾 */
    printf("%d\n", n);
    return 0;
}
```

| | `-O0` | `-O2` 無 restrict | `-O2` 有 restrict |
|---|---|---|---|
| gcc 11 / clang 14 | 6561 | 6561 | **5567** |

依原本的語意，每次迭代 `x` 是當前 `n` 的兩倍，`n` 依序為 1、3、9、27……最後 6561。加 `restrict` 後編譯器把 `*a + *b` 提到迴圈外只算一次（固定為 2），`n` 依序為 1、3、5……結果 5567。`restrict` 是承諾，違背它不會有任何編譯錯誤。

### 7. 讀取未初始化的變數（C11 6.3.2.1 §2）

```c
#include <stdio.h>

int main(void)
{
    int x;
    if (x == 0)
        puts("zero");
    if (x != 0)
        puts("nonzero");
    return 0;
}
```

| | `-O0` | `-O2` |
|---|---|---|
| gcc 11 | `nonzero` | `zero` |
| clang 14 | `nonzero` | **什麼都不印** |

`x == 0` 與 `x != 0` 照理恰有一個成立，clang `-O2` 卻兩個都不成立：它產生的 `main` 只有 `xorl %eax, %eax; retq`，兩次比較的結果都被當成可以任意選的值。（原文的 Clang 18 更極端，`main` 連 `ret` 都沒有，執行直接落入其後的位元組。）規則是：C11 6.3.2.1 §2 規定，**從未取過位址**（可以宣告成 `register`）的自動變數若未初始化，使用其值就是 UB；取過位址的則是 indeterminate value，可能是 trap representation（C23 稱 non-value representation）。編譯器**不必讓同一個未定義的值在兩次讀取中保持一致**。`-Wall` 兩者都有 `-Wuninitialized` 警告；較隱蔽的情況靠 gcc 的 `-Wmaybe-uninitialized`（依賴最佳化）或 clang 的 MemorySanitizer。[Ch 32](./32-tricks-syntax-and-resources.md) 的 cleanup + `goto` 實驗是同一類問題：殘值在一台機器上是 0（看似正常），在另一台是堆疊位址（當場中止）。

### 8. 越界存取：推論會連鎖擴散（C11 6.5.6 §8）

SPEC CPU2006 的 464.h264ref 有這個迴圈：

```c
#include <stdio.h>

int d[16];

int SATD(void)
{
    int satd = 0, dd, k;
    for (dd = d[k = 0]; k < 16; dd = d[++k])   /* 最後一輪先讀 d[16] 才檢查 k */
        satd += (dd < 0 ? -dd : dd);
    return satd;
}

int main(void)
{
    printf("%d\n", SATD());
    return 0;
}
```

最後一次迭代 `++k` 使 `k == 16`，條件還沒檢查，`d[16]` 已先被讀取。GCC 4.8 據此推論：「`d[k]` 被讀時 `k` 必定 < 16，所以 `k < 16` 永遠成立」，產生**無窮迴圈**（GCC [PR53073](https://gcc.gnu.org/bugzilla/show_bug.cgi?id=53073)，以 INVALID 結案："if you ever enter this loop, you'll invoke undefined behavior and anything can happen"）。GCC 4.8 同時加入 `-fno-aggressive-loop-optimizations`。gcc 11 的表現：

```
$ gcc -O2 satd.c -o sa && ./sa
satd.c:8:36: warning: iteration 15 invokes undefined behavior [-Waggressive-loop-optimizations]
satd.c:8:27: note: within this loop
0
```

現在的 gcc 會警告（需要迴圈分析，`-O0` 不會出現），產生的迴圈正常結束。同一段程式在不同版本上一個無窮迴圈一個正常，正是 UB 的特徵。另一個 GCC 案例 [PR33498](https://gcc.gnu.org/bugzilla/show_bug.cgi?id=33498) 在 gcc 11 上仍然重現：

```c
#include <stdio.h>

__attribute__((noinline)) void table_init(int *value)
{
    int i;
    int val = 0x03020100;
    for (i = 0; i < 256 / 4; i++) {
        value[i] = val;
        val += 0x04040404;    /* 第 32 次迭代溢位 */
    }
}

int main(void)
{
    static int t[64 + 1024];
    table_init(t);
    printf("%08x\n", t[63]);
    return 0;
}
```

```
$ gcc -O2 table.c
table.c:9:13: warning: iteration 31 invokes undefined behavior [-Waggressive-loop-optimizations]
```

| gcc 11 | 結果 |
|---|---|
| `-O1` | `fffefdfc`（正常） |
| `-O2` | **SIGSEGV**：迴圈不結束，一路寫出陣列 |
| `-O2 -fwrapv` | `fffefdfc` |
| clang 14 `-O2` | `fffefdfc` |

`val` 在第 32 次迭代溢位。gcc 的歸納變數最佳化（ivopts）把迴圈結束條件從 `i < 64` 換成對 `val` 的比較，而依「`val` 不會溢位」的假設，那個比較永遠不成立。Richard Biener 當年的回覆很能代表這類爭論："Technically the testcase invokes undefined behavior... Practically from a QOI point of view the undefinedness should not propagate to the loop exit test"；回報者 Eric Dumazet 的回應是 "The integer overflow is not a excuse for a compiler to generate an infinite loop."。

### 9. 推論讓「從未呼叫」的函式被執行

Krister Walfridsson 的例子（原本是 `rm -rf /`，這裡改成 `echo`）：

```c
#include <stdlib.h>

typedef int (*Function)(void);
static Function Do;

static int EraseAll(void) { return system("echo rm -rf /"); }
void NeverCalled(void) { Do = EraseAll; }

int main(void) { return Do(); }
```

| | 結果 |
|---|---|
| gcc 11 `-O0`、`-O2`；clang 14 `-O0` | SIGSEGV（呼叫 NULL） |
| clang 14 `-O2` | 印出 `rm -rf /`，exit 0 |

`Do` 是 `static`，位址沒有外流，整個程式只有 `NeverCalled` 會寫它，所以 `Do` 只可能是 NULL 或 `EraseAll`。呼叫 NULL 是 UB，clang 推論 `Do` 必定是 `EraseAll`，直接改成呼叫 `EraseAll`。

### 10. 無窮迴圈：C 與 C++ 規則不同

John Regehr 的〈C Compilers Disprove Fermat's Last Theorem〉：搜尋一個立方等於 3 的正整數（不存在）。

```cpp
static bool search(int lim)
{
    for (int a = 1;; a = a % lim + 1)
        if (a * a * a == 3)
            return true;
}
int main() { if (search(1000)) std::puts("Impossible!"); }
```

```
$ g++ -O2 -std=c++17 fermat.cpp && timeout 2 ./a.out; echo "exit=$?"
Impossible!
exit=0
$ clang++ -O2 -std=c++17 fermat.cpp && ...       # 同樣立即印出 Impossible!
$ gcc -O2 -std=c11 fermat.c && timeout 2 ./a.out; echo "exit=$?"      # C 版本
exit=124
$ clang -O2 -std=c11 fermat.c ...                                     # 同樣跑到 timeout
exit=124
```

C++ 的 forward progress 規則允許編譯器假設「沒有 I/O、volatile、同步的迴圈終將結束」，唯一的出口是 `return true`，所以 `search` 被化簡成恆真。C 的規則比較窄：C11 6.8.5 §6 只允許假設「控制運算式**不是常數運算式**」的迴圈會結束，而 `for` 省略的第二個運算式依 6.8.5.3 §2 "is replaced by a nonzero constant"，所以這個迴圈不在其列，必須一直跑。LLVM 長年把所有無副作用迴圈都當成會結束（對 C、Rust 是錯的），這個 2006 年開的 bug 直到 LLVM 12 引入 `mustprogress` 屬性才解決——clang 14 的 C 版本能正確地一直跑，正是這項修正的結果。

### 11. 資料競爭（C11 5.1.2.4 §25）

多執行緒中，對同一非 atomic 物件的兩次存取若至少一次是寫入、且沒有 happens-before 關係排序，就是 data race，"Any such data race results in undefined behavior."。兩個實驗：

```c
#include <pthread.h>
#include <stdio.h>

static long counter;                 /* 非 atomic、沒有鎖 */

static void *work(void *arg)
{
    (void) arg;
    for (int i = 0; i < 1000000; i++)
        counter++;                   /* 讀－改－寫，二個執行緒同時做 */
    return NULL;
}

int main(void)
{
    pthread_t t1, t2;
    pthread_create(&t1, NULL, work, NULL);
    pthread_create(&t2, NULL, work, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("counter = %ld (expected 2000000)\n", counter);
    return 0;
}
```

```
$ gcc -O0 -pthread race.c -o r0 && ./r0 && ./r0
counter = 1045321 (expected 2000000)
counter = 1171537 (expected 2000000)
$ gcc -O2 -pthread race.c -o r2 && ./r2
counter = 2000000 (expected 2000000)
```

`-O0` 遺失一半的遞增（每次結果不同）；`-O2` 「剛好正確」——編譯器把一百萬次遞增合併成一次 `counter += 1000000`，兩個執行緒的窗口變小而已。**`-O2` 的正確結果不是修好了，是把 bug 藏起來了。** 第二個實驗更有代表性：

```c
#include <pthread.h>
#include <stdio.h>
#include <unistd.h>

static int ready;                    /* 應該用 atomic_int */

static void *waiter(void *arg)
{
    (void) arg;
    while (!ready)                   /* 編譯器可把 ready 的讀取提出迴圈 */
        ;
    puts("waiter saw ready");
    return NULL;
}

int main(void)
{
    pthread_t t;
    pthread_create(&t, NULL, waiter, NULL);
    sleep(1);
    ready = 1;
    pthread_join(t, NULL);
    return 0;
}
```

```
$ gcc -O0 -pthread flagspin.c && timeout 3 ./a.out; echo "exit=$?"
waiter saw ready
exit=0
$ gcc -O2 -pthread flagspin.c && timeout 3 ./a.out; echo "exit=$?"
exit=124
$ gcc -O2 -S flagspin.c
waiter:
	movl	ready(%rip), %eax     ; 只讀一次
	testl	%eax, %eax
	jne	.L5
.L4:
	jmp	.L4                     ; 永遠跳回自己
```

因為 data race 是 UB，編譯器可以假設 `ready` 在迴圈中不被別人改，讀一次就好。`-O2` 產生字面意義上的 `jmp .L4` 自我迴圈。正確作法是 C11 `atomic_int` 或 mutex（[Ch 20](./20-lock-free-linked-list.md)）；`volatile` 只能防止讀取被提出迴圈，不提供跨執行緒的順序保證。Linux 核心用 `READ_ONCE()`/`WRITE_ONCE()` 表達這類共享存取，LWN〈[Who's afraid of a big bad optimizing compiler?](https://lwn.net/Articles/793253/)〉整理了所有合法的編譯器轉換。signal handler 的限制更嚴（C11 7.14.1.1 §5，[Ch 35](./35-stream-io-and-signals.md)）。

## 偵測與控制

### 執行時期偵測：三個 sanitizer

| 工具 | 旗標 | 抓什麼 | 代價（概略） |
|---|---|---|---|
| UBSan | `-fsanitize=undefined` | 有號溢位、移位、未對齊、除以零、`__builtin_unreachable` 到達等 | 小，可用於測試環境 |
| ASan | `-fsanitize=address` | 越界、UAF、double free、stack-use-after-return | 約 2 倍時間、更多記憶體 |
| TSan | `-fsanitize=thread` | data race | 5–15 倍時間（依 TSan 文件的量級） |

ASan 抓越界寫入：

```c
#include <stdio.h>
#include <stdlib.h>

int main(int argc, char **argv)
{
    (void) argv;
    int *a = malloc(8 * sizeof *a);
    for (int i = 0; i <= 8 + argc - 1; i++)   /* argc == 1：寫到 a[8] */
        a[i] = i;
    printf("a[7] = %d\n", a[7]);
    free(a);
    return 0;
}
```

```
$ gcc -O2 oob.c -o ob && ./ob; echo "exit=$?"     # 沒有 ASan：安靜地「正常」
a[7] = 7
exit=0
$ gcc -O0 -g -fsanitize=address oob.c -o oba && ./oba
==21959==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x503000000060 ...
WRITE of size 4 at 0x503000000060 thread T0
    #0 0x5bd5d4a372be in main /home/ypp/c_sysprog_lab/p5b/c34/oob.c:9
0x503000000060 is located 0 bytes to the right of 32-byte region [0x503000000040,0x503000000060)
```

TSan 在這台機器上有個環境陷阱：

```
$ gcc -O1 -g -fsanitize=thread -pthread race.c -o rt && ./rt
FATAL: ThreadSanitizer: unexpected memory mapping 0x5ce211759000-0x5ce21175a000
$ setarch -R ./rt                     # 關閉 ASLR 後執行
==================
WARNING: ThreadSanitizer: data race (pid=22350)
  Read of size 8 at 0x555555558018 by thread T2:
    #0 work /home/ypp/c_sysprog_lab/p5b/c34/race.c:9 (rt+0x1263)
  Previous write of size 8 at 0x555555558018 by thread T1:
    #0 work /home/ypp/c_sysprog_lab/p5b/c34/race.c:9 (rt+0x128d)
  Location is global 'counter' of size 8 at 0x555555558018 (rt+0x000000004018)
...
SUMMARY: ThreadSanitizer: data race /home/ypp/c_sysprog_lab/p5b/c34/race.c:9 in work
```

gcc 11 / clang 14 附帶的 TSan 執行時期，和較新核心預設較大的 mmap 隨機化位元數（`vm.mmap_rnd_bits`）不相容，會在啟動時直接 FATAL。用 `setarch -R` 關閉該行程的 ASLR，或調低 `vm.mmap_rnd_bits`（需要 root）即可；新版 sanitizer 已改善這點。sanitizer 只能抓**測試輸入真的觸發**的 UB，它們是動態工具，不是證明。

### UBSan 與「先被最佳化掉」的爭論

Check Point Research 的 OptOut（2020）認為 UBSan 幫助不大，因為化簡發生在 UBSan 介入之前。用 libtiff CVE-2019-14973 的簡化版實測：

```c
#include <stdint.h>
#include <stdio.h>

typedef int64_t tmsize_t; /* 名字像 size_t，其實是有號 */

__attribute__((noinline)) int fits(tmsize_t nmemb, tmsize_t elem_size)
{
    tmsize_t bytes = nmemb * elem_size;
    return nmemb && elem_size && bytes / elem_size == nmemb; /* 溢位檢查 */
}

int main(int argc, char **argv)
{
    (void) argv;
    tmsize_t n = (tmsize_t) 1 << (61 + argc); /* 2^62 */
    printf("%d\n", fits(n, 4));
    return 0;
}
```

| | `-O0` | `-O2` | 加 `-fsanitize=undefined` |
|---|---|---|---|
| gcc 11 / clang 14 | 0 | **1**（檢查被化簡掉） | 0，且回報 `signed integer overflow: 4611686018427387904 * 4 cannot be represented in type 'long int'` |

`-O2` 下編譯器假設乘法不溢位，所以 `bytes / elem_size == nmemb` 恆真，檢查只剩 `nmemb && elem_size`——溢位的輸入「通過」了檢查。但加上 UBSan 後，插樁的檢查作用在乘法本身，只要測試輸入觸發溢位就會被抓到，`-O2` 也不再化簡（和原文在 GCC 13/14 的觀察一致）。所以 UBSan 的限制不是「被最佳化掉」，而是「測試輸入必須觸發」。

### 改變語意的編譯選項

| 選項 | 效果 | 誰在用 |
|---|---|---|
| `-fwrapv` | 有號加減乘回繞（二補數） | 部分專案；Linux 用涵蓋更廣的 `-fno-strict-overflow` |
| `-fno-strict-overflow` | gcc 中隱含 `-fwrapv` 與 `-fwrapv-pointer` | Linux 核心 |
| `-fno-strict-aliasing` | 停用依型別的 aliasing 推論 | Linux 核心、許多網路程式 |
| `-fno-delete-null-pointer-checks` | 停用「解參考過就不是 NULL」的推論 | Linux 核心（gcc 與 clang 7+） |
| `-fno-aggressive-loop-optimizations` | 停用依 UB 推迴圈次數 | 舊程式碼救急 |
| `-ffast-math` | **反方向**：讓編譯器假設沒有 NaN/Inf 等 | 數值程式 |

最後一列要特別小心：

```
$ cat fastmath.c
int bad(double x) { return __builtin_isnan(x); }
$ gcc -O2 -ffast-math -S fastmath.c       # clang 14 相同
bad:
	xorl	%eax, %eax          ; isnan 恆為 0
	ret
```

`-ffast-math` 包含 `-ffinite-math-only`，你的 NaN 檢查被化簡成常數 0（glibc 的 `isnan` 就展開成這個內建函式）。這些選項**改變的是程式的意義**，不只是速度。而且它們只為特定運算定義語意，不讓其他非法存取變有效：停用 NULL 檢查的刪除，不代表解參考 NULL 合法。

### 靜態分析：STACK 與「越來越多的檢查被刪」

Xi Wang 等人在 SOSP 2013 的〈Towards Optimization-Safe Systems〉把「因 UB 而被編譯器意外刪除的程式碼」稱為 optimization-unstable code，他們的工具 STACK 在 Linux、PostgreSQL、Kerberos 等找出 160 個經確認並修正的錯誤，估計 Debian Wheezy 約 40% 含 C/C++ 的套件有這類程式碼。論文的 Figure 4 用六個檢查測 12 個編譯器；用同樣六個檢查測本機，記錄**從哪個最佳化等級起檢查被化簡成常數**：

```c
#include <stdlib.h>
int c1(char *p) { return p + 100 < p; }
int c2(int *p) { int v = *p; if (!p) return -1; return v; }
int c3(int x) { return x + 100 < x; }
int c4(int x) { if (x < 0) return 2; return x + 100 < 0; }
int c5(int x) { return !(1 << x); }
int c6(int x) { return abs(x) < 0; }
```

| 檢查 | gcc-4.8.1 (2013，論文) | clang-3.3 (2013，論文) | gcc 11 | clang 14 |
|---|---|---|---|---|
| `p + 100 < p` | `-O2` | `-O1` | **`-O0`** | `-O1` |
| `*p; if (!p)` | `-O2` | 不刪除 | `-O1` | `-O1` |
| `x + 100 < x` | `-O2` | `-O1` | **`-O0`** | `-O1` |
| `x+ + 100 < 0`（`x` 已知非負） | `-O2` | 不刪除 | `-O1` | `-O1` |
| `!(1 << x)` | 不刪除 | `-O1` | 不刪除 | `-O1` |
| `abs(x) < 0` | `-O2` | 不刪除 | **`-O0`** | `-O1` |

gcc 11 在 `-O0` 產生的 `c1`、`c3`、`c6` 已經是 `mov eax, 0`（實測組語），和原文 GCC 13/14 的結果一致。十幾年過去，**被刪除的檢查只多不少**，而且「降低最佳化等級就沒事」並不可靠。

STACK 在 PostgreSQL 找到的除法是另一種形態——「事後檢查」：

```c
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

__attribute__((noinline)) int64_t int8div(int64_t arg1, int64_t arg2)
{
    if (arg2 == 0) {
        puts("division by zero");
        exit(1);
    }
    int64_t result = arg1 / arg2;
    if (arg2 == -1 && arg1 < 0 && result <= 0) { /* 事後才檢查溢位 */
        puts("integer out of range");
        exit(1);
    }
    return result;
}

int main(int argc, char **argv)
{
    (void) argv;
    printf("%lld\n", (long long) int8div(INT64_MIN, -argc)); /* argc == 1 */
    return 0;
}
```

四種組合（gcc/clang × `-O0`/`-O2`）全部在 `idiv` 收到 SIGFPE（exit 136），事後檢查根本輪不到；`-O2` 的組語裡 `int8div` 只剩一個 `puts` 呼叫（事後檢查那支被刪了），因為在「除法未溢位」的前提下商必為正，`result <= 0` 不可能成立。開發者以為商會像 Java 一樣回繞成 $-2^{63}$；正確作法是在除法**之前**比較 `arg1 == INT64_MIN && arg2 == -1`。論文指出在 x86-64 上一個 SQL 查詢 `SELECT ((-9223372036854775808)::int8) / (-1);` 就能讓伺服器當機。

### 函式庫的 nonnull 承諾

推論的依據不只來自語言。glibc 以 `__nonnull` 宣告 `memcpy`、`memmove` 的指標參數，C11 7.24.1 §2 也確實要求長度為零時指標仍須有效：

```c
#include <stdio.h>
#include <string.h>

__attribute__((noinline)) int copy(int *dest, const int *src, size_t nbytes)
{
    memmove(dest, src, nbytes);
    return dest != NULL;
}

int main(void)
{
    printf("%d\n", copy(NULL, NULL, 0));
    return 0;
}
```

| | `-O0` | `-O2` | `-O2 -fno-delete-null-pointer-checks` |
|---|---|---|---|
| gcc 11 | 0 | **1** | 0 |
| clang 14 | 0 | 0 | 0 |

gcc 把 `dest != NULL` 化簡成恆真。ISC BIND 在 2014 年就因為 `qsort(x, 0, ...)` 傳入 NULL 後 `if (x != NULL)` 被 GCC 4.9 刪掉而出錯（GCC [PR61236](https://gcc.gnu.org/bugzilla/show_bug.cgi?id=61236)）。依原文，WG14 的 N3322 讓 C2y 中這類長度為零的呼叫有定義，GCC 15 新增 `nonnull_if_nonzero` 屬性回應，但 glibc 標頭目前仍用 `__nonnull`。

## 開發社群的爭議

### Linux 核心的立場

Linux 核心以 `-fno-strict-aliasing`、`-fno-delete-null-pointer-checks`、`-fno-strict-overflow` 編譯，三者分別對應本章的 aliasing、NULL、溢位推論。Linus Torvalds 的立場一貫（依原文引用的 LKML 郵件）：2009 年寫 "Type-based aliasing is _stupid_."；2018 年說核心 "explicitly use '-fno-delete-null-pointer-checks' because we do *not* want NULL pointer check removal even in the case of a bug"，並主張 "It's ok to do optimizations that are based on 'hardware does the exact same thing', but not on 'the standard says this is undefined so we can remove it'."。2024 年 Kees Cook 提議以 sanitizer 偵測整數回繞時，Linus 以雜湊函式為例強調無號回繞 "is not only well-defined, it's *common*, and *EXPECTED*"。核心的作法是**用編譯選項把部分 UB 變成有定義的方言**，代價是只能用支援這些選項的編譯器建置。

### 編譯器開發者的立場

Chris Lattner 的〈What Every C Programmer Should Know About Undefined Behavior #3/3〉解釋為什麼「依 UB 刪除程式碼時發出警告」難以普遍做到：警告的數量會多到沒用、難以只在使用者在意時才發出，也說明不了一連串最佳化如何組合出結果。前面看到的 `-Waggressive-loop-optimizations` 只涵蓋迴圈分析看得到的情形。

### 利用 UB 到底值多少效能

這個問題直到最近才有大規模量測。Popescu 與 Lopes 在 PLDI 2025 的〈Exploiting Undefined Behavior in C/C++ Programs for Optimization: A Study on the Performance Impact〉在 LLVM 16 上把利用 UB 的 18 個面向逐一停用，在 Intel、AMD、Arm 伺服器上跑 24 個程式的 129 項測試，結論是 "for the benchmarks and UB categories that we evaluated, the end-to-end performance gains are minimal"，而且退化多半能用改進最佳化演算法或 LTO 補回（依原文整理，只有 simdjson 與 sqlite 分別慢 4% 與 3% 補不回）。這和 Carruth 的 bzip2 例子不矛盾：**個別熱點可能明顯受益，整體程式則比一般認為的少**。結果限於所測的 UB 類別、程式與平台，不能直接推廣成「取消所有 UB 沒有成本」。

### 編譯器內部：poison 與 freeze

有號運算的 `nsw`（no signed wrap）旗標就是 C 有號溢位 UB 在 LLVM IR 中的表達：溢位時結果是 `poison`，本章 `a + 100 > a`、有號索引、移位結果每次不同，都由此而來。Juneyoung Lee、John Regehr、Nuno Lopes 等人在 PLDI 2017〈Taming Undefined Behavior in LLVM〉指出 `undef` 與 `poison` 兩種未定義值的語意互相矛盾，導致 loop unswitching 與 GVN 一類教科書最佳化長期有 bug，解法是新增 `freeze` 指令（LLVM 10 起），把可能是 poison 的值固定成某個任意但確定的值。

## 標準的下一步

- **C23 `unreachable()`**（`<stddef.h>`，C23 7.21.1）：讓程式設計者**刻意**引入 UB，告訴編譯器「這裡不會到達」，取代 GCC/Clang 的 `__builtin_unreachable()`。前面 gcc 在除以零後插入的 `ud2`，就是 `__builtin_unreachable` 在 `-O2` 下的典型產物。
- **C23 3.5.3 Note 3**：UB 不回溯影響之前的可觀察行為（源自 Uecker 的 N3128〈Taming the Demons〉）。gcc 11 在 Uecker 例子上的行為顯示，編譯器實作還沒全部跟上。
- **C2y 清點 UB**：依原文引用的 Uecker 狀態報告 N3957，截至 2026 年 8 月已有 45 項 UB 經表決在 C2y 中移除，方式包括改為 constraint（編譯器必須診斷）、改為實作定義或直接給予定義，例如 N3244〈Slay Some Earthly Demons I〉、N3322（`memcpy(NULL, NULL, 0)`）。這些多半是容易診斷或實作早已一致的情形；有號溢位、越界、aliasing 這類支撐最佳化的 UB 仍維持原狀。（數字與狀態取自原文，本章未逐份查核 WG14 文件。）
- **erroneous behavior**：這個詞來自 C++26（P2795），把「讀取未初始化的自動變數」從 UB 改成「錯誤但有界的行為」——值是某個實作選定的確定值，實作可以診斷但不得做 UB 式的推論。就我所知，WG14 對 C 是否引入類似分類有過討論，但截至撰寫時**沒有把握它已被 C 標準採納**，請以 WG14 最新文件為準。

## 對比與取捨

| 面對 UB 的策略 | 優點 | 缺點 | 適用 |
|---|---|---|---|
| 寫不觸發 UB 的程式（檢查在前、`memcpy`、`ckd_*`） | 可攜、讓編譯器充分最佳化 | 需要紀律與知識 | 所有新程式碼 |
| 用選項定義方言（`-fwrapv`、`-fno-strict-aliasing`） | 舊程式碼立即變安全 | 綁定編譯器、失去部分最佳化 | 大型既有專案（Linux） |
| 動態偵測（UBSan/ASan/TSan） | 精確指出位置 | 只抓測試輸入觸發的 | CI、fuzzing |
| 靜態分析（STACK、`-fanalyzer`、Coverity） | 不需要觸發輸入 | 誤報、漏報 | 稽核 |
| 降低最佳化等級 | — | **不可靠**（gcc `-O0` 也刪檢查） | 不建議 |

## 踩雷集錦

1. **「UB 就是那一行算出奇怪的值」**：UB 讓整個執行失去保證，推論會擴散——迴圈結束條件被改、從未呼叫的函式被呼叫、`x == 0` 與 `x != 0` 都不成立。
2. **「`-O0` 下正常就沒問題」**：gcc 在 `-O0` 就把 `a + 100 > a` 化簡為真、把 `p + 100 < p` 化簡為假。最佳化等級不是 UB 的開關。
3. **「事後檢查溢位」**：`if (a + b < a)`、`result <= 0`、`bytes / size == n` 這類檢查依賴溢位已經發生，而溢位一發生就是 UB。檢查要在運算之前，或用 `__builtin_*_overflow`。
4. **「先用再檢查」**：`p->x` 之後再 `if (!p)`，檢查會被刪（CVE-2009-1897）。順序永遠是先檢查。
5. **「`-O2` 結果正確代表 race 修好了」**：`counter++` 在 `-O2` 被合併成一次加法而「剛好正確」；`flagspin` 在 `-O2` 變成無窮迴圈。用 atomic 與 TSan。
6. **「`volatile` 可以拿來做執行緒同步」**：它防止讀取被提出迴圈，但不提供順序保證，也不讓 data race 變合法。
7. **「`restrict` 只是提示」**：它是承諾，違背時結果就是錯的（6561 變 5567），沒有任何診斷。

## 進階：再往深一層

- **讀 LLVM LangRef 的 `poison` 與 `freeze`**：理解為什麼 clang `-O2` 的移位結果每次不同、為什麼未初始化的 `x` 兩次比較都不成立。
- **用 Csmith 找編譯器 bug**：Csmith（PLDI 2011）隨機產生**不含 UB** 的程式，交給不同編譯器比對輸出。它的前提很有教育意義——含 UB 的程式輸出不同不算編譯器 bug，引數求值順序（unspecified）也必須避開。
- **gcc 的 `-fanalyzer`**（GCC 10+）：靜態分析 NULL 解參考、double free 等，可以拿本章的例子試試它抓得到哪些。
- **面試題**：「`int i = 0; i = i++;` 的結果？」好的回答不是給數字，而是說出 6.5 §2、unsequenced、UB 與 unspecified 的差別，並提到 `-Wsequence-point`/`-Wunsequenced`。

```c
/* 進階練習：寫出不依賴 UB 的有號加法溢位檢查（不用 builtin） */
#include <limits.h>
#include <stdbool.h>
static bool add_overflows(int a, int b)
{
    return (b > 0 && a > INT_MAX - b) || (b < 0 && a < INT_MIN - b);
}
```

（這段的正確性請自己用邊界值與 UBSan 驗證：`INT_MAX - b` 在 `b > 0` 時不會溢位，`INT_MIN - b` 在 `b < 0` 時不會溢位。）

## 動手練習

1. 把移位例子的 `foo` 加上 `__attribute__((noinline))`，重跑四種組合，解釋為什麼結果變了。
2. 對 `fits.c` 寫出不依賴 UB 的版本（用 `__builtin_mul_overflow` 或先比較 `nmemb > INT64_MAX / elem_size`），確認 `-O2` 下印 0。
3. 把 `flagspin.c` 的 `ready` 改成 `_Atomic int`，用 `-O2` 編，看組語的迴圈變成什麼樣子；再改回 `int` 並用 `setarch -R` 加 TSan 執行。
4. 在 `seq.c` 加一行 `a[i] = i++;`，看 gcc `-Wall` 與 clang 各給什麼警告。

## 本章重點整理

- 四種行為依嚴重度：locale-specific、implementation-defined（要寫文件）、unspecified（幾種合法結果選一）、undefined（沒有任何要求）。
- UB 的存在理由是硬體差異與最佳化前提；編譯器假設 UB 不發生並據此推論，推論會沿控制流程擴散。
- as-if rule 只保證可觀察行為（volatile、終止時的檔案、互動 I/O），所以沒人讀的 `memset` 會被刪；C23 Note 3 說 UB 不能回溯抹除之前的可觀察行為，但 gcc 11 仍會把 `idiv` 提到 volatile 寫入前。
- 同一段 UB 程式在 gcc/clang、`-O0`/`-O2`、內嵌與否之間結果都可能不同；`-O0` 並不安全。
- UBSan/ASan/TSan 抓「測試觸發的」UB；`-fwrapv` 等選項把 UB 變成方言；最好的策略是寫不觸發 UB 的程式（檢查在前、用 `memcpy`、用 `ckd_*`、用 atomic）。

## 自我檢核

- [ ] 不看筆記，能說出 unspecified 與 undefined 的差別，並各舉一個本章的例子
- [ ] 能解釋為什麼 `func(&n), func(&n), func(&n)` 不是 UB 但 `i++ + ++i` 是
- [ ] 如果同事說「我在 `-O0` 測過溢位檢查沒問題」，你能用哪個實驗說服他？
- [ ] 能畫出 CVE-2009-1897 的因果鏈：先解參考 → 檢查被刪 → 位址 0 可對映 → 提權
- [ ] 能解釋 Fermat 迴圈為什麼在 C++ 被化簡、在 C 不行（6.8.5 §6 的「非常數控制運算式」）
- [ ] 知道 TSan 在新核心上 FATAL 時該怎麼辦，以及 sanitizer 為什麼不能證明程式沒有 UB
- [ ] 想想你寫過的程式裡有沒有「事後檢查溢位」或「先用再檢查」的寫法，能怎麼改寫

## 延伸閱讀

### 論文

- **[Towards Optimization-Safe Systems: Analyzing the Impact of Undefined Behavior](https://people.csail.mit.edu/nickolai/papers/wang-stack.pdf)** — Xi Wang, Nickolai Zeldovich, M. Frans Kaashoek, Armando Solar-Lezama，SOSP（2013）
  - **核心貢獻**：定義 optimization-unstable code，用 STACK 在真實系統找出 160 個被確認的 bug
  - **讀哪裡**：Section 2（案例：`buf + len < buf`、tun 驅動）與 Figure 4（各編譯器刪除檢查的等級）；Section 3 的形式化可先跳過
  - **和本章的關聯**：本章「STACK 六個檢查」表格就是重測這篇的 Figure 4
- **[Taming Undefined Behavior in LLVM](https://users.cs.utah.edu/~regehr/papers/undef-pldi17.pdf)** — Juneyoung Lee et al.，PLDI（2017）
  - **核心貢獻**：釐清 `undef`/`poison` 的矛盾，提出 `freeze`
  - **讀哪裡**：Section 2–3 的動機例子；需要一點編譯器 IR 背景（[Ch 37](./37-compiler-optimization.md)）
- **[Exploiting Undefined Behavior in C/C++ Programs for Optimization: A Study on the Performance Impact](https://web.ist.utl.pt/nuno.lopes/pubs/ub-pldi25.pdf)** — Lucian Popescu, Nuno P. Lopes，PLDI（2025）
  - **核心貢獻**：第一個大規模量測「停用各類 UB 推論」的效能影響
  - **讀哪裡**：結論與 Table（各 UB 類別的影響）；方法論一節說明怎麼新增停用選項

### 部落格 / 技術文章

- **[What Every C Programmer Should Know About Undefined Behavior](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know.html)**（Part [2](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know_14.html)、[3](https://blog.llvm.org/2011/05/what-every-c-programmer-should-know_21.html)）— Chris Lattner（LLVM Blog，2011）
  - **這篇說什麼**：LLVM 作者從編譯器角度解釋 UB 為什麼存在、最佳化順序如何改變結果、為什麼難以警告
  - **讀哪裡**：三篇都讀，Part 2 的 `contains_null_check` 就是本章的例子
- **[Undefined Behavior in 2017](https://blog.regehr.org/archives/1520)** — John Regehr
  - **這篇說什麼**：按 UB 類別整理現有的偵測工具與缺口，是 sanitizer 生態的地圖
  - **前提**：本章「偵測與控制」一節
- **[Who's afraid of a big bad optimizing compiler?](https://lwn.net/Articles/793253/)** — Paul E. McKenney 等人（LWN，2019）
  - **這篇說什麼**：編譯器對 data race 程式可以做的所有合法轉換，以及核心為什麼用 `READ_ONCE`/`WRITE_ONCE`
  - **和本章的關聯**：`flagspin` 實驗的完整理論版本

### 官方文件 / Spec

- **[N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**：3.4.1–3.4.4（四種行為）、5.1.2.3 §6（可觀察行為）、6.5 §2、§5、§7（unsequenced、溢位、aliasing）、6.8.5 §6（迴圈終止）、附錄 J.2（UB 總表，約兩百項）
- **[Clang UndefinedBehaviorSanitizer 文件](https://clang.llvm.org/docs/UndefinedBehaviorSanitizer.html)**：每個 `-fsanitize=` 子選項抓什麼、成本多少

### 書籍

- **《Effective C》（2nd ed.）** — Robert C. Seacord（No Starch Press；第 2 版涵蓋 C23，出版年請以出版社頁面為準）
  - **這本書的定位**：CERT C 作者寫的現代 C，處處標出 UB 與安全寫法，適合本章之後當日常參考
  - **讀哪幾章**：整數與運算式相關章節（章名依版本，以目錄中 "Arithmetic Types"、"Expressions and Operators" 為準）

> 和 `programming/c_interview/` 的關係：UB 的面試速查見 [../c_interview/06-undefined-behavior.md](../c_interview/06-undefined-behavior.md)，aliasing 見 [../c_interview/07-type-punning-aliasing.md](../c_interview/07-type-punning-aliasing.md)，sequence point 見 [../c_interview/10-volatile-sequence-point.md](../c_interview/10-volatile-sequence-point.md)；動手抓 UB 的練習見 [../c_interview/practice-a-ub-debug.md](../c_interview/practice-a-ub-debug.md)。

本章多次看到 SIGFPE、SIGSEGV 與「緩衝區裡的訊息隨 signal 消失」。下一章從 `getchar` 為什麼回傳 `int` 講起，把串流、EOF、緩衝與 signal 的關係講清楚。

→ [Ch 35 Stream I/O、EOF 與 signal](./35-stream-io-and-signals.md)
