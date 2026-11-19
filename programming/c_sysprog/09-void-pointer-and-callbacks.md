# Ch 9 — void *、指標的指標、function pointer 與 qualifier

> **目標**：掌握 `void *` 的轉換規則與它不能做的事（取值、算術、與函式指標互轉），分清「轉型合法」與「存取合法」；用指標的指標改寫呼叫端的變數；理解 function designator 的轉換規則，寫出正確的 `qsort`／`bsearch` 比較器與帶 `user_data` 的 callback；讀懂 `const`、`volatile`、`restrict` 加在指標上的意義與組合語言效果；並和 Go 的指標做對比。

> **對應原文**：[你所不知道的 C 語言：指標篇](https://hackmd.io/@sysprog/c-pointer) 的「`void *` 之謎」「沒有『雙指標』只有『指標的指標』」「Function Pointer」「針對指標的修飾 (qualifier)」「Go 語言也有指標」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4、gdb 12.1、Go 1.22.12（linux/amd64）。輸出中的位址每次執行不同。

## 為什麼需要這個？

[Ch 7](./07-pointers-by-the-standard.md) 說指標 = 位址 + 所指型別，[Ch 8](./08-arrays-functions-pointers.md) 說陣列與函式在運算式中會變成指標。這一章處理指標的三種「變形」，它們解決的是同一個問題：**怎麼寫出不知道資料型別、甚至不知道要做什麼動作的通用程式碼**。

- `memcpy` 要能複製任何 object，它的參數不能是 `int *` 或 `char *`，C89 為此引入 `void *`。
- 函式要修改呼叫端的指標變數（例如 `strtol` 回報解析停在哪裡），C 只有 call-by-value，所以需要「指標的指標」。
- `qsort` 要能排序任何型別，但它不知道怎麼比大小，所以「比較」這個動作必須由呼叫端用函式指標傳進來。

這三樣東西一起出現在 `qsort` 的原型裡：

```c
void qsort(void *base, size_t nmemb, size_t size,
           int (*compar)(const void *, const void *));
```

讀完這一章，你應該能逐個 token 解釋這一行，並且寫出一個不會溢位、不會轉錯型別的比較器。

## 先建立直覺

```
int *p          ──►  [ 位址 ] + 「那裡是 int，步幅 4」        能 *p、能 p+1
void *v         ──►  [ 位址 ] + 「不知道那裡是什麼」          不能 *v、不能 v+1
int **pp        ──►  [ 位址 ] + 「那裡是一個 int *」          *pp 是指標、**pp 是 int
int (*fp)(int)  ──►  [ 指令位址 ] + 「那裡是一段接 int、回傳 int 的程式碼」 能 fp(3)
```

`void *` 像一張只寫了門牌、沒寫房子坪數的紙條：你可以轉交給別人，但自己沒辦法進去量。要用，必須先「告訴編譯器那裡是什麼」，也就是轉回具體型別。

「指標的指標」不是「兩個指標」，而是**一條兩段的參照鏈**。原文用了一個很好記的比喻：「雙馬尾」是左右對稱、彼此獨立的兩束；「馬尾的馬尾」是從一個關聯到另一個。中文「雙指標」聽起來像前者，行為卻是後者，所以本課一律說「指標的指標」（英文就是 pointer to pointer）。

## `void *` 之謎

### 為什麼要有 void

最早的 C 沒有 `void`。沒標回傳型別的函式一律當成回傳 `int`，「通用指標」也只能用 `char *` 湊合。這帶來兩個問題：編譯器無從檢查函式原型與實際使用是否一致；`char *` 當通用指標時，任何地方都能直接對它解參考或做算術，誤用不會被發現。C89 正式確立 `void`，其中 `void *` 成為「指向任意 object 的通用指標」。

N1570 6.2.5 §19 說 `void` 是「an incomplete object type that cannot be completed」，所以 `void *` 是指向 incomplete type 的指標（[Ch 7](./07-pointers-by-the-standard.md) 講過 incomplete type 的限制）。兩條關鍵規則：

- **6.3.2.3 §1**：`void *` 可以和「任何 object type 的指標」互相轉換，轉回去後與原指標比較相等。這個轉換在指定時是**隱式**的，所以 `int *p = malloc(...)` 不用轉型（C++ 要）。
- **6.2.5 §28**：`void *` 和字元型別的指標有相同的表示法與對齊需求。這讓 `void *` 和 `char *` 在底層可以互換。

所以 `memcpy` 的原型是：

```c
void *memcpy(void *dest, const void *src, size_t n);
```

它複製的是任意 object 的位元組，不限字串。

### void * 不能做的事

`void` 沒有大小，所以凡是需要大小的操作都不行：

```c
#include <stdio.h>
int main(void)
{
    int arr[4] = {10, 20, 30, 40};
    void *p = arr;
    void *q = p + 1;                     /* ISO C：違反限制式；GNU C：當成 +1 byte */
    char *c = (char *) p + sizeof(int);  /* 可攜寫法：先轉成 char * */
    printf("p=%p q=%p c=%p\n", p, q, (void *) c);
    printf("*(int *)c = %d\n", *(int *) c);
    return 0;
}
```

```
$ gcc -Wall -Wextra voidarith.c -o voidarith && ./voidarith
p=0x7ffdd76c9390 q=0x7ffdd76c9391 c=0x7ffdd76c9394
*(int *)c = 20
$ gcc -std=c11 -pedantic -Wall -c voidarith.c
voidarith.c:6:17: warning: pointer of type ‘void *’ used in arithmetic [-Wpointer-arith]
$ gcc -std=c11 -pedantic-errors -c voidarith.c
voidarith.c:6:17: error: pointer of type ‘void *’ used in arithmetic [-Wpointer-arith]
```

預設模式下 gcc 不吭聲，而且 `q` 只前進 1 byte：這是 GNU C 擴充，把 `sizeof(void)` 當成 1。`-Wall -Wextra` 都不會警告，要 `-pedantic` 或 `-Wpointer-arith` 才看得到。

這個擴充在實務上很重要：**Linux 核心以 GNU C（`-std=gnu11`）編譯，不是 ISO C**，所以核心程式碼裡直接寫 `ptr + 1`（`ptr` 是 `void *`）是合法且常見的。你寫要跨編譯器的函式庫時，就得寫 `(char *) p + n`。

取值則連 GNU 擴充都救不了：

```c
int f(void *p) { return *p; }
```

```
$ gcc -c voidderef.c
voidderef.c:1:25: warning: dereferencing ‘void *’ pointer
voidderef.c:1:25: error: void value not ignored as it ought to be
```

`*p` 的型別是 `void`，一個「沒有值」的運算式，不能拿來回傳。

### 對齊：轉型不等於能讀

把 `void *` 轉回具體型別再讀，還要滿足那個型別的**對齊需求**。從網路封包或檔案裡讀一個 16-bit 欄位，位址很可能是奇數：

```c
#include <stdio.h>
#include <stdint.h>
#include <string.h>

static uint16_t read_le16_cast(const void *ptr)  { return *(const uint16_t *) ptr; }
static uint16_t read_le16_bytes(const void *ptr)
{
    const uint8_t *b = ptr;
    return (uint16_t) (b[0] | ((unsigned) b[1] << 8));
}
static uint16_t read_le16_memcpy(const void *ptr)
{
    uint16_t v;
    memcpy(&v, ptr, sizeof v);          /* 編譯器會把它變成一道 load */
    return v;
}

int main(void)
{
    uint8_t pkt[] = { 0xff, 0x34, 0x12, 0x00 };   /* 從 pkt+1 開始是 0x1234 */
    const void *p = pkt + 1;                       /* 奇數位址：沒有對齊 2 */
    printf("bytes : 0x%04x\n", read_le16_bytes(p));
    printf("memcpy: 0x%04x\n", read_le16_memcpy(p));
    printf("cast  : 0x%04x\n", read_le16_cast(p));
    return 0;
}
```

```
$ gcc -O2 -Wall -Wextra unaligned.c -o unaligned && ./unaligned
bytes : 0x1234
memcpy: 0x1234
cast  : 0x1234
$ gcc -O0 -g -fsanitize=alignment unaligned.c -o unaligned_ub && ./unaligned_ub
unaligned.c:5:59: runtime error: load of misaligned address 0x7ffd6f282dd5 for type 'const uint16_t', which requires 2 byte alignment
0x7ffd6f282dd5: note: pointer points here
 00 00 00 ff 34 12 00  00 e6 39 bb 29 31 3d 1b  01 00 00 00 00 00 00 00  90 9d 62 10 b6 76 00 00  7f
             ^
```

三種寫法在 x86-64 上都印出正確答案，因為 x86 的一般 load 指令容許非對齊存取。但 `read_le16_cast` 在規格上是 UB（N1570 6.3.2.3 §7：轉換成對齊不正確的指標，行為未定義），UBSan 的 `-fsanitize=alignment` 會抓出來。原文提到 ARMv5 以前的 Arm，非對齊的 32-bit load 會得到旋轉過的錯誤值或觸發例外；ARMv8-A 對一般記憶體容許非對齊，但 atomic 指令與 device memory 仍要求對齊。

可攜寫法有兩種：逐位元組組合（同時處理了位元組序），或 `memcpy` 到一個對齊好的區域變數。後者常被誤以為「多一次函式呼叫比較慢」，看 `-O2` 的結果：

```c
uint16_t rd_memcpy(const void *p) { uint16_t v; memcpy(&v, p, sizeof v); return v; }
uint16_t rd_bytes(const void *p) { const uint8_t *b = p; return (uint16_t)(b[0] | ((unsigned) b[1] << 8)); }
```

```
$ gcc -O2 -S -o - rd.c
rd_memcpy:
	movzwl	(%rdi), %eax
	ret
rd_bytes:
	movzwl	(%rdi), %eax
	ret
```

兩者都被編譯成同一道 `movzwl`。編譯器知道 x86 可以非對齊 load，而且知道 x86 是 little-endian，於是 `b[0] | b[1] << 8` 也被辨認成一次 16-bit 讀取。在要求對齊的架構上，同樣的原始碼會被編譯成逐位元組 load，**原始碼保持可攜，最佳化交給編譯器**。Linux 核心為此提供 `get_unaligned()`／`put_unaligned()` 系列巨集。

原文還指出一個常見寫錯的地方：`*(uint8_t *) (ptr + 1)` 是先對 `void *` 做 `+1` 再轉型（ISO C 違規），正確的是 `*((uint8_t *) ptr + 1)`，先轉型再位移。括號的位置決定了步幅的單位。

> 對齊需求從哪裡來、struct 為什麼有 padding，見 [Ch 16](./16-alignment-and-padding.md)；位元組序與位元操作見 [Ch 23](./23-bitwise-basics.md)。

### `void *` 真的萬能嗎：函式指標不在其中

6.3.2.3 §1 說的是「pointer to **any object type**」。函式不是 object，所以 `void *` 和函式指標之間的轉換**不在規格保證內**。函式指標之間可以互轉（6.3.2.3 §8），但用轉錯的型別去呼叫是 UB：

> A pointer to a function of one type may be converted to a pointer to a function of another type and back again; the result shall compare equal to the original pointer. If a converted pointer is used to call a function whose type is not compatible with the referenced type, the behavior is undefined.

```
$ gcc -std=c11 -pedantic -c fp2.c        # 內容：printf("%p\n", (void *) test);
fp2.c:3:32: warning: ISO C forbids conversion of function pointer to object pointer type [-Wpedantic]
```

為什麼規格要這樣限制？因為有些架構的程式碼與資料位址空間不同、寬度不同（哈佛架構的微控制器、早期 x86 的 segmented memory model），函式指標可能比資料指標寬。POSIX 則另外要求 `dlsym()` 回傳的 `void *` 必須能轉成函式指標，所以在 POSIX 平台上這個轉換實務上可行，但那是 POSIX 的保證，不是 ISO C 的。

### 轉型合法 ≠ 存取合法：`void *` 與 `void **`

`void *` 能和任何 object 指標互轉，很多人就以為 `void **` 也能和任何「指標的指標」互轉。gcc 會告訴你不行：

```c
void demo(void)
{
    int *ip = 0; void *vp; const void *cvp; int **ipp = &ip; void **vpp;
    vp = ip;          /* OK */
    ip = vp;          /* OK（C 允許；C++ 不允許） */
    cvp = ip;         /* OK：加上 const */
    vp = cvp;         /* 丟掉 const */
    vpp = ipp;        /* int ** 與 void ** 不相容 */
    (void) vpp;
}
```

```
$ gcc -Wall -c quals.c
quals.c:7:8: warning: assignment discards ‘const’ qualifier from pointer target type [-Wdiscarded-qualifiers]
quals.c:8:9: warning: assignment to ‘void **’ from incompatible pointer type ‘int **’ [-Wincompatible-pointer-types]
```

理由在 6.7.6.1 §2：兩個指標型別相容，條件是 qualifier 相同**且所指型別相容**。`int **` 指向 `int *`，`void **` 指向 `void *`，而 `int *` 和 `void *` 不相容。`void *` 能和各型別互轉，是規格對它開的**特例**，不是因為相容；這個特例不會遞迴套用到 `void **`。

更深的問題在**存取**。原文分析了一個常見寫法：

```c
int *intptr = NULL;
void *dvoidptr = &intptr;                       /* 轉型合法 */
*(void **) dvoidptr = malloc(sizeof(*intptr));  /* 以 void * 型別的 lvalue 寫入 intptr */
```

前兩行都合法（`int **` 轉 `void *` 再轉 `void **`，每一步都是 object 指標互轉）。問題在第三行：`*(void **) dvoidptr` 是一個型別為 `void *` 的 lvalue，它去改寫 `intptr`，而 `intptr` 的 effective type 是 `int *`。N1570 6.5 §7 列出可以存取一個 object 的 lvalue 型別（相容型別、對應的 signed/unsigned、包含它的 aggregate/union、字元型別），`void *` 不在 `int *` 的清單裡，所以這是 UB（strict aliasing 違規）。在 `int *` 與 `void *` 表示法相同的平台上，它通常「能用」，但編譯器有權據此做錯誤的最佳化。

精確的說法是：**`void *` 與 `void **` 可以互相轉型；把轉型後的指標拿去存取「型別不相容的物件」才是 UB。** 寫「通用的釋放並清空」函式時，正確做法是讓它接 `void *` 回傳值，或用巨集：`#define FREE_AND_NULL(p) (free(p), (p) = NULL)`。

> effective type 與 strict aliasing 的完整說明，見 [c_interview 的 type punning 章](../c_interview/07-type-punning-aliasing.md) 與 [Ch 34](./34-undefined-behavior.md)。

## 沒有「雙指標」只有「指標的指標」

C 只有 call-by-value：呼叫函式時，每個引數的**值**被複製到被呼叫端的參數 object 裡。傳指標也一樣，複製的是位址值。

```c
#include <stdio.h>
int B = 2;
void func1(int *p)  { p = &B; }       /* 只改到副本 */
void func2(int **p) { *p = &B; }      /* 透過指標的指標改寫呼叫端的指標 */
int main(void)
{
    int A = 1;
    int *ptrA = &A;
    func1(ptrA);
    printf("after func1: *ptrA = %d\n", *ptrA);
    func2(&ptrA);
    printf("after func2: *ptrA = %d\n", *ptrA);
    return 0;
}
```

```
$ gcc -Wall -Wextra pp.c -o pp && ./pp
pp.c:3:17: warning: parameter ‘p’ set but not used [-Wunused-but-set-parameter]
after func1: *ptrA = 1
after func2: *ptrA = 2
```

gcc 的警告已經點出問題：`func1` 裡的 `p` 被設了值卻從沒用到，因為它只是一份副本。畫成圖：

```
func1(ptrA)：複製「ptrA 的值」

  main                       func1
  ptrA [ &A ]──┐             p [ &A ] ──► 改成 [ &B ]
               ▼                           （main 的 ptrA 不受影響）
            A [ 1 ]

func2(&ptrA)：複製「ptrA 的位址」

  main                       func2
  ptrA [ &A ] ◄──────────────p [ &ptrA ]
     │                        *p = &B  → 寫進 ptrA 本身
     ▼ 變成 [ &B ]
  A [ 1 ]   B [ 2 ] ◄── ptrA 現在指這裡
```

規則可以一般化：**想讓函式修改呼叫端的 T，就傳 T 的位址，也就是 `T *`。** T 本身是 `int *`，所以參數是 `int **`。標準函式庫到處是這個模式：

- `long strtol(const char *nptr, char **endptr, int base)`：透過 `endptr` 回報解析停在哪個字元。
- `int pthread_join(pthread_t, void **retval)`：把執行緒的回傳值（一個 `void *`）寫回呼叫端。
- `int posix_memalign(void **memptr, size_t alignment, size_t size)`：配置的位址從參數傳回，回傳值留給錯誤碼。

指標的指標還有一個更漂亮的用法：用「指向 `next` 欄位的指標」走訪鏈結串列，讓刪除開頭節點不再需要特例。原文的開頭疑問 `for (lpp = &list; *lpp != NULL; lpp = &(*lpp)->next)` 就是這個慣用語，Linus Torvalds 拿它當「good taste」的範例。[Ch 10](./10-strings-and-null.md) 會簡短介紹，[Ch 18](./18-linked-list-pointer-to-pointer.md) 完整展開。

## Function pointer

### `(********puts)("Hello")` 為什麼能跑

原文丟出一行看似胡鬧的程式：

```c
int main() { return (********puts)("Hello"); }
```

它能編譯也能跑。答案在兩條規格互相抵消：

- **6.3.2.1 §4**：function designator（型別為函式的運算式，最常見的就是函式名稱）除了當 `sizeof`、單元 `&` 的運算元之外，會被轉換成「指向函式的指標」。
- **6.5.3.2 §4**：單元 `*` 作用在指向函式的指標上，結果是 function designator。

所以 `*puts`：`puts` 先轉成函式指標，`*` 把它變回 function designator，它馬上又被轉成函式指標……不論加幾個 `*`，最後都是同一個函式指標，交給呼叫運算子 `()`。

```c
#include <stdio.h>
#include <stdint.h>
void test(void) { }
int main(void)
{
    void (*fptr)(void) = test;
    void (*fptr2)(void) = &test;
    printf("test  = %p\n", (void *) (uintptr_t) test);
    printf("&test = %p\n", (void *) (uintptr_t) &test);
    printf("fptr  = %p, fptr2 = %p\n", (void *) (uintptr_t) fptr, (void *) (uintptr_t) fptr2);
    fptr(); (*fptr)(); (**fptr)();
    printf("sizeof fptr = %zu\n", sizeof fptr);
    return (********puts)("Hello");
}
```

```
$ gcc -Wall -Wextra -g -no-pie fp.c -o fp && ./fp; echo "exit=$?"
test  = 0x401156
&test = 0x401156
fptr  = 0x401156, fptr2 = 0x401156
sizeof fptr = 8
Hello
exit=6
```

- `fptr = test` 與 `fptr = &test` 等價：前者靠 6.3.2.1 §4 的轉換，後者是 `&` 例外直接取址。
- `fptr()`、`(*fptr)()`、`(**fptr)()` 都等價。
- 結束碼是 6：glibc 的 `puts` 回傳寫出的字元數（5 個字 + 換行）。**規格只保證成功時回傳非負值**（7.21.7.9），6 是 glibc 的實作細節。

印函式指標時，`%p` 只定義給 `void *`，而函式指標轉 `void *` 又不在 ISO C 保證內（見上一節）。上面先轉 `uintptr_t` 再轉 `void *` 的寫法在主流 POSIX 平台可行，但嚴格說也不可攜：`uintptr_t` 只保證能容納 `void *`（7.20.1.4），不保證容納函式指標。原文提到的 `printf("%x", test)` 則是明確的錯誤：`%x` 要 `unsigned int`（4 bytes），傳進去的是 8 bytes 的指標。

用 gdb 看這些運算式的型別：

```
(gdb) print test
$1 = {void (void)} 0x401156 <test>
(gdb) print &test
$2 = (void (*)(void)) 0x401156 <test>
(gdb) print *test
$3 = {void (void)} 0x401156 <test>
(gdb) print **test
$4 = {void (void)} 0x401156 <test>
(gdb) ptype fptr
type = void (*)(void)
```

`{void (void)}` 是 function designator（函式型別），`(void (*)(void))` 是函式指標，位址都是 `0x401156`。這個數字從哪來？

```
$ objdump -d --no-show-raw-insn fp | sed -n '/<test>:/,/ret/p'
0000000000401156 <test>:
  401156:	endbr64
  40115a:	push   %rbp
  40115b:	mov    %rsp,%rbp
  40115e:	nop
  40115f:	pop    %rbp
  401160:	ret
```

函式指標的值就是函式第一道指令的**虛擬位址**。`-no-pie` 讓執行檔載入固定位址，所以 gdb 和 objdump 看到的數字一致；預設的 PIE 執行檔每次載入位址都不同（ASLR，[Ch 14](./14-function-call-security.md)）。第一道 `endbr64` 是 Intel CET 的間接跳躍落點標記，Ubuntu 的 gcc 預設開 `-fcf-protection`，它就是為了「函式指標只能跳到合法的函式開頭」而存在的。

### 真實系統：Linux VFS 的 `file_operations`

函式指標的價值不在上面的文字遊戲，而在**以資料驅動的分派**。Linux 核心的虛擬檔案系統（VFS）讓每種檔案系統或裝置提供一張函式指標表（節錄，欄位隨核心版本增減）：

```c
struct file_operations {
    ssize_t (*read_iter)(struct kiocb *, struct iov_iter *);
    ssize_t (*write_iter)(struct kiocb *, struct iov_iter *);
    int     (*open)(struct inode *, struct file *);
    /* ... */
};
```

```
 read(fd, ...) ──► vfs_read() ──► file->f_op->read_iter(...)
                                       │
           ┌───────────────────────────┼─────────────────────┐
           ▼                           ▼                     ▼
  ext4 的表 .read_iter        procfs 的表 .read_iter     字元裝置驅動的表
  = ext4_file_read_iter       = ...                      = 驅動自己的函式
```

`vfs_read` 不用 `if`／`switch` 判斷檔案系統種類，它呼叫的函式取決於開檔時 `f_op` 指向哪一張表。這就是手工打造的 vtable，與 C++ 虛擬函式等價的動態分派（[Ch 29](./29-oop-in-c.md)、[Ch 30](./30-object-models-in-the-wild.md)）。

## qsort 與 bsearch：callback 的教科書範例

回到開頭的原型：

```c
void qsort(void *base, size_t nmemb, size_t size,
           int (*compar)(const void *, const void *));
```

| 參數 | 為什麼長這樣 |
|---|---|
| `void *base` | 不知道元素型別，只能拿起始位址 |
| `size_t nmemb, size_t size` | 不知道型別就不知道大小，由呼叫端告知；內部用 `(char *) base + i * size` 定位第 `i` 個元素 |
| `compar` | 不知道怎麼比大小，由呼叫端提供；回傳負、零、正 |
| `const void *` | 比較器只該讀不該寫，`const` 把這個承諾寫進型別 |

這叫 **type erasure（型別抹除）**：函式庫把型別資訊丟掉以換取通用性，再用 `size` 和 callback 把必要的資訊補回來。代價是編譯器無法檢查型別，所有轉型錯誤都只能在執行期爆出來。

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <limits.h>

static int cmp_int_bad(const void *a, const void *b)
{
    return *(const int *) a - *(const int *) b;     /* 可能溢位 */
}
static int cmp_int(const void *a, const void *b)
{
    int x = *(const int *) a, y = *(const int *) b;
    return (x > y) - (x < y);                       /* 只回傳 -1/0/1，不溢位 */
}
static int cmp_str(const void *a, const void *b)
{
    /* 元素型別是 const char *，所以 a 實際是 const char * const * */
    return strcmp(*(const char *const *) a, *(const char *const *) b);
}

struct emp { const char *name; int age; };
static int cmp_emp_age(const void *a, const void *b)
{
    const struct emp *x = a, *y = b;
    return (x->age > y->age) - (x->age < y->age);
}

static void show(const char *tag, const int *v, size_t n)
{
    printf("%-6s:", tag);
    for (size_t i = 0; i < n; i++) printf(" %d", v[i]);
    printf("\n");
}

int main(void)
{
    int v1[] = { 3, INT_MIN, 7, -1, INT_MAX, 0 };
    int v2[sizeof v1 / sizeof v1[0]];
    size_t n = sizeof v1 / sizeof v1[0];
    memcpy(v2, v1, sizeof v1);

    qsort(v1, n, sizeof v1[0], cmp_int_bad);
    show("bad", v1, n);
    qsort(v2, n, sizeof v2[0], cmp_int);
    show("good", v2, n);

    const char *words[] = { "pear", "apple", "fig", "banana" };
    qsort(words, 4, sizeof words[0], cmp_str);
    for (int i = 0; i < 4; i++) printf("%s ", words[i]);
    printf("\n");

    struct emp staff[] = { {"amy", 41}, {"bob", 29}, {"cat", 35} };
    qsort(staff, 3, sizeof staff[0], cmp_emp_age);
    for (int i = 0; i < 3; i++) printf("%s(%d) ", staff[i].name, staff[i].age);
    printf("\n");

    int key = 7;
    int *hit = bsearch(&key, v2, n, sizeof v2[0], cmp_int);
    printf("bsearch 7 -> %s (index %td)\n", hit ? "found" : "not found", hit ? hit - v2 : -1);
    key = 5;
    hit = bsearch(&key, v2, n, sizeof v2[0], cmp_int);
    printf("bsearch 5 -> %s\n", hit ? "found" : "not found");
    return 0;
}
```

```
$ gcc -Wall -Wextra -g sort.c -o sort && ./sort
bad   : -1 0 3 7 2147483647 -2147483648
good  : -2147483648 -1 0 3 7 2147483647
apple banana fig pear
bob(29) cat(35) amy(41)
bsearch 7 -> found (index 4)
bsearch 5 -> not found
$ gcc -g -fsanitize=undefined sort.c -o sort_ub && ./sort_ub
sort.c:8:29: runtime error: signed integer overflow: -2147483648 - 7 cannot be represented in type 'int'
```

三個重點：

1. **`a - b` 比較器是錯的**。`INT_MIN - 7` 溢位（signed overflow 是 UB，[Ch 22](./22-integer-overflow.md)），實際上繞成一個大正數，於是 `INT_MIN` 被排到最後。`(x > y) - (x < y)` 只產生 -1、0、1，沒有溢位空間。
2. **字串陣列的比較器多一層間接**。`words` 的元素是 `const char *`，`qsort` 傳給比較器的是「指向元素的指標」，也就是 `const char *const *`。直接把 `a` 當成 `const char *` 丟給 `strcmp`，比較的會是指標本身的位元組，排出來的順序是亂的，而且編譯器不會警告。這是 type erasure 的代價。
3. **`bsearch` 要求陣列已依同一個比較器排序**，找不到回傳 `NULL`。它的第一個參數是指向 key 的指標，比較器的第一個參數永遠是 key。

原文還提醒一個可攜性陷阱：需要額外脈絡參數的 `qsort_r`，glibc 與歷史上的 BSD／macOS 參數順序不同（FreeBSD 14.0 起改採與 glibc 相同的介面）；C11 Annex K 的 `qsort_s` 是選用的，少有實作。

## callback 與 user_data：手工打造的 closure

`qsort` 的比較器揭示了函式指標最重要的用途：**callback（回呼）**。呼叫端把函式交給函式庫，函式庫在適當時機回頭呼叫它，控制權因此反轉（inversion of control），也就是 Hollywood principle 的「Don't call us, we'll call you」。

C 沒有 closure，callback 無法自動捕捉外部狀態。慣例是在 callback 的簽章多帶一個 `void *`（常叫 `user_data`、`arg`、`ctx`），呼叫端註冊時交給函式庫，函式庫原封不動傳回給 callback：

```c
#include <stdio.h>
#include <stddef.h>

/* 函式庫端：只知道「對每個元素呼叫 visit」，不知道元素是什麼、要做什麼 */
typedef void (*visit_fn)(void *elem, void *user_data);

static void for_each(void *base, size_t n, size_t size, visit_fn visit, void *user_data)
{
    for (size_t i = 0; i < n; i++)
        visit((char *) base + i * size, user_data);   /* char * 才能逐位元組位移 */
}

/* 應用端：user_data 充當手工 closure */
struct stats { long sum; int max; };
static void accumulate(void *elem, void *user_data)
{
    int v = *(int *) elem;
    struct stats *st = user_data;
    st->sum += v;
    if (v > st->max) st->max = v;
}
static void scale(void *elem, void *user_data)
{
    *(double *) elem *= *(double *) user_data;
}

/* dispatch table：以索引取代 if/switch */
static int op_add(int a, int b) { return a + b; }
static int op_sub(int a, int b) { return a - b; }
static int op_mul(int a, int b) { return a * b; }
static int (*const ops[])(int, int) = { op_add, op_sub, op_mul };

int main(void)
{
    int v[] = { 4, 9, 2, 7 };
    struct stats st = { 0, v[0] };
    for_each(v, 4, sizeof v[0], accumulate, &st);
    printf("sum=%ld max=%d\n", st.sum, st.max);

    double d[] = { 1.5, 2.0, 4.0 };
    double factor = 10.0;
    for_each(d, 3, sizeof d[0], scale, &factor);
    printf("%.1f %.1f %.1f\n", d[0], d[1], d[2]);

    const char *name[] = { "add", "sub", "mul" };
    for (size_t i = 0; i < sizeof ops / sizeof ops[0]; i++)
        printf("%s(6, 3) = %d\n", name[i], ops[i](6, 3));
    return 0;
}
```

```
$ gcc -Wall -Wextra -g callback.c -o callback && ./callback
sum=22 max=9
15.0 20.0 40.0
add(6, 3) = 9
sub(6, 3) = 3
mul(6, 3) = 18
```

`for_each` 同時用了本章三個主題：`void *base` 抹除型別、`visit_fn` 參數化行為、`void *user_data` 攜帶狀態。`ops` 是 [Ch 8](./08-arrays-functions-pointers.md) 提過的「函式指標的陣列」，宣告成 `static int (*const ops[])(int, int)`，讀作「const 的函式指標所組成的陣列」，讓這張表可以放進唯讀區段，不會被意外改寫（被改寫的函式指標表是經典的攻擊目標，[Ch 14](./14-function-call-security.md)）。

這個「函式指標 + `void *`」的組合在開源專案裡隨處可見：

| 專案 | API | callback 與 user_data |
|---|---|---|
| C 標準函式庫 | `qsort`、`bsearch`、`atexit` | 比較器；`atexit` 沒有 user_data |
| POSIX threads | `pthread_create(..., void *(*start)(void *), void *arg)` | 執行緒進入點就是一個 callback |
| SQLite | `sqlite3_exec(db, sql, callback, arg, errmsg)` | 每一列結果呼叫一次 callback，`arg` 是 user_data |
| libcurl | `CURLOPT_WRITEFUNCTION` + `CURLOPT_WRITEDATA` | 收到資料時呼叫，`WRITEDATA` 是 user_data |
| GLib／GTK | `g_signal_connect(obj, "clicked", G_CALLBACK(fn), data)` | 事件綁定到 callback |
| Linux 核心 | workqueue 的 `work_func_t`、notifier chain、`sort()` 的比較器 | 核心多用 `container_of` 從參數找回外層 struct，取代 user_data（[Ch 19](./19-linux-list-head.md)） |

## qualifier：const、volatile、restrict

### const 的位置

`const` 修飾的是**它左邊緊鄰的東西**；左邊沒東西時，修飾右邊緊鄰的東西。用 right-left rule 讀最可靠：

| 宣告 | 讀法 | `*p = x` | `p = q` |
|---|---|---|---|
| `const char *p` | p 是 pointer to const char | 不行 | 可以 |
| `char const *p` | 同上（`const` 寫在型別後面） | 不行 | 可以 |
| `char *const p` | p 是 const pointer to char | 可以 | 不行 |
| `const char *const p` | p 是 const pointer to const char | 不行 | 不行 |

故意全部試一次：

```c
void demo(void)
{
    char buf[4] = "abc", other[4] = "xyz";
    const char *p1 = buf;          /* pointer to const char */
    char *const p2 = buf;          /* const pointer to char */
    const char *const p3 = buf;    /* 兩者皆 const */
    p1 = other;   /* OK */
    *p1 = 'A';    /* 錯 */
    p2 = other;   /* 錯 */
    *p2 = 'A';    /* OK */
    p3 = other;   /* 錯 */
    *p3 = 'A';    /* 錯 */
}
```

```
$ gcc -c constpos.c
constpos.c:8:9: error: assignment of read-only location ‘*p1’
constpos.c:9:8: error: assignment of read-only variable ‘p2’
constpos.c:11:8: error: assignment of read-only variable ‘p3’
constpos.c:12:9: error: assignment of read-only location ‘*(const char *)p3’
```

錯誤訊息的用詞剛好對應兩層：`read-only location ‘*p1’`（資料層）與 `read-only variable ‘p2’`（指標層）。原文提醒的誤稱也在這裡：**把 "pointer to const" 叫成 "const pointer" 會造成混淆**，兩者是表格第一列和第三列，完全不同。

`const char *p = buf` 也說明了一件常被誤解的事：`buf` 本身不是 const，只是**透過 `p` 不能改**。`const` 限定的是「這條存取路徑」，不是 object 本身（[Ch 7](./07-pointers-by-the-standard.md) 的 `constub.c` 示範了改寫「以 const 定義的 object」才是 UB）。

### 為什麼 `char **` 不能隱式轉成 `const char **`

`char *` 可以隱式轉成 `const char *`（加 const 很安全），但多一層就不行：

```c
void takes(const char **pp);
void demo2(void)
{
    char *s = 0;
    takes(&s);     /* char ** -> const char **：看似無害，其實不安全 */
}
```

```
$ gcc -Wall -c cpp.c
cpp.c:5:11: warning: passing argument 1 of ‘takes’ from incompatible pointer type [-Wincompatible-pointer-types]
cpp.c:1:25: note: expected ‘const char **’ but argument is of type ‘char **’
```

如果允許，下面這段就能在沒有任何轉型的情況下改寫 const 物件：

```c
const char secret[] = "ro";
char *s;
const char **pp = &s;   /* 假設允許 */
*pp = secret;           /* 合法：把 const char * 存進 *pp，也就是 s */
s[0] = 'X';             /* s 是 char *，於是改寫了 const 物件 */
```

C 允許的安全轉換是 `char **` → `char *const *`（中間那層變成 const，就無法再透過它存入任何指標）。C++ 的規則更寬：`char **` 可以隱式轉成 `const char *const *`，因為兩層都加了 const 就堵住了上面的漏洞；C 到 C23 都沒有採納這條規則，這個轉換在 C 裡仍會得到 incompatible pointer type 的警告。

### volatile：每次都要真的去讀

N1570 6.7.3 §7：volatile 物件「may be modified in ways unknown to the implementation」，所以每次存取都必須依抽象機器的規則真的執行。看同一個等待迴圈有無 volatile 的差別：

```c
int flag;
volatile int vflag;
void wait_plain(void)    { while (!flag) ; }
void wait_volatile(void) { while (!vflag) ; }
```

```
$ gcc -O2 -S -o - vol.c
wait_plain:
	movl	flag(%rip), %eax     # 只讀一次
	testl	%eax, %eax
	jne	.L5
.L4:
	jmp	.L4                  # flag 是 0 就永遠空轉，不再讀記憶體
.L5:
	ret
wait_volatile:
.L8:
	movl	vflag(%rip), %eax    # 每一圈都重新讀
	testl	%eax, %eax
	je	.L8
	ret
```

沒有 volatile，編譯器認為迴圈內沒人改 `flag`，於是讀一次就決定命運；如果 `flag` 是 0，就變成真正的無窮迴圈 `jmp .L4`。這就是嵌入式程式讀硬體暫存器、或等待中斷處理常式設旗標時必須用 volatile 的原因。指標上的寫法和 const 一樣看位置：`volatile uint32_t *reg` 是「指向 volatile 資料的指標」（暫存器內容會變），這才是 MMIO 要的；`uint32_t *volatile p` 是指標本身 volatile，很少用到。

**volatile 不是多執行緒同步工具**：它不保證原子性，也不產生記憶體屏障。多執行緒共享旗標要用 C11 `_Atomic`（[Ch 20](./20-lock-free-linked-list.md)）。Linux 核心甚至有一份文件〈Why the "volatile" type class should not be used〉專門勸開發者別用它。

### restrict：承諾沒有別名

C99 的 `restrict` 是程式設計師對編譯器的承諾：在這個指標的有效範圍內，它指向的 object 只會透過它（或由它衍生的指標）存取（N1570 6.7.3.1）。

```c
void add_plain(int *a, int *b, int *x)                               { *a += *x; *b += *x; }
void add_restrict(int *restrict a, int *restrict b, int *restrict x) { *a += *x; *b += *x; }
```

```
$ gcc -O2 -S -o - restrict.c
add_plain:
	movl	(%rdx), %eax
	addl	%eax, (%rdi)         # *a += *x
	movl	(%rdx), %eax         # 重新讀 *x：因為 a 可能就是 x
	addl	%eax, (%rsi)
	ret
add_restrict:
	movl	(%rdx), %eax
	addl	%eax, (%rdi)
	addl	%eax, (%rsi)         # 直接沿用：承諾過 a 不會是 x
	ret
```

`add_plain` 必須在寫完 `*a` 之後重讀 `*x`，因為呼叫端可能傳 `add_plain(&n, &m, &n)`。加了 `restrict`，少一次 load。在迴圈裡，這個差別決定編譯器敢不敢向量化。標準函式庫早就用上了：`memcpy` 的原型實際是 `void *memcpy(void *restrict dest, const void *restrict src, size_t n)`，這就是「`memcpy` 的來源與目的不能重疊，重疊請用 `memmove`」的型別層級表達。**違反 restrict 承諾是 UB**，編譯器不會幫你檢查。

## Go 語言也有指標

原文講了一段歷史：Ken Thompson 與 Dennis Ritchie 在 1999 年獲頒美國國家科技獎章，Thompson 隔年自貝爾實驗室退休，2006 年加入 Google，2007 年和 Rob Pike、Robert Griesemer 在 Google 內部開始設計 Go。C 的設計者之一把指標和 struct 帶進了新語言，但拿掉了 C 指標最危險的部分。對照實驗：

```go
package main

import "fmt"

type point struct{ x, y int }

func newPoint() *point {
	p := point{1, 2} // 區域變數
	return &p        // 合法：編譯器做 escape analysis，把 p 放到 heap
}

func move(p *point) { p.x += 10 } // 沒有 ->：p.x 自動解參考

func main() {
	p := newPoint()
	move(p)
	fmt.Println(*p)
	var q *point
	fmt.Println(q == nil)
	defer func() { fmt.Println("recovered:", recover()) }()
	fmt.Println(q.x) // nil 解參考：定義良好的 runtime panic
}
```

```
$ go run main.go
{11 2}
true
recovered: runtime error: invalid memory address or nil pointer dereference
$ go build -gcflags=-m main.go 2>&1 | grep "moved to heap"
./main.go:8:2: moved to heap: p
```

```go
func main() {
	a := [3]int{1, 2, 3}
	p := &a[0]
	p++ // Go 不允許指標算術
	_ = p
}
```

```
$ go run bad.go
./bad.go:6:2: invalid operation: p++ (non-numeric type *int)
```

| 面向 | C | Go |
|---|---|---|
| 回傳區域變數的位址 | 懸掛指標，UB（gcc 甚至改成回傳 0，見 [Ch 7](./07-pointers-by-the-standard.md)） | 合法；escape analysis 把變數移到 heap，GC 負責回收 |
| 指標算術 | 有，是陣列存取的基礎 | 沒有（要用 `unsafe` 套件） |
| 存取成員 | `p->x` 或 `(*p).x` | `p.x`，自動解參考 |
| 空指標解參考 | UB | 定義良好的 panic，可 `recover` |
| 通用指標 | `void *` | `any`（interface，帶型別資訊）與 `unsafe.Pointer` |
| 函式值 | 函式指標，不能捕捉狀態，靠 `void *user_data` | 一級函式值與 closure |

Go 保留了指標「傳位址以修改呼叫端、避免複製大 struct」的用途，拿掉了「位址可以任意運算」的能力，再用 GC 消滅生命週期問題。這張表也是一張 C 指標的風險清單：右欄的每一個設計，都在回應左欄的一個漏洞來源。

## 對比與取捨

| 通用化手法 | 型別安全 | 效能 | 典型例子 |
|---|---|---|---|
| `void *` + `size` + callback | 無，轉錯只能執行期發現 | 每次比較一次間接呼叫，難 inline | `qsort`、`bsearch` |
| 巨集產生特化程式碼 | 有（展開後型別正確） | 可 inline，通常最快 | Linux 核心的 `list_for_each_entry`、各種 `DEFINE_*` |
| `_Generic`（C11） | 有，編譯期選函式 | 等同直接呼叫 | `<tgmath.h>`（[Ch 27](./27-preprocessor-basics.md)） |
| C++ template | 有 | 可 inline；`std::sort` 通常比 `qsort` 快 | `std::sort` |

`qsort` 慢在比較器是透過函式指標呼叫，編譯器在 `qsort` 內部看不到比較器的內容，無法 inline。這不是說 `qsort` 不能用，而是當排序是熱點時，你知道該往哪個方向換。

## 踩雷集錦

1. **以為 `void *` 能做算術**：gcc 預設接受，只前進 1 byte，`-Wall -Wextra` 不警告。可攜程式碼請加 `-Wpointer-arith`，並寫 `(char *) p + n`。
2. **以為 `void **` 是「通用的指標的指標」**：`int **` 轉 `void **` 會被警告，透過它寫入 `int *` 物件是 strict aliasing 違規。`void *` 的特例不會遞迴。
3. **用 `a - b` 寫比較器**：遇到 `INT_MIN` 就溢位，排序結果錯誤。用 `(x > y) - (x < y)`。浮點數也別用相減，`NaN` 會讓比較不具傳遞性。
4. **字串陣列的比較器少解一層**：`qsort(words, ...)` 傳給比較器的是 `const char **` 的偽裝，直接把參數交給 `strcmp` 會比錯東西而且沒有警告。
5. **把 volatile 當成鎖或 atomic**：volatile 只保證「每次都真的存取」，不保證原子性與順序；多執行緒用 `_Atomic` 或鎖。
6. **「pointer to const」與「const pointer」混用**：面試和 code review 都會被抓。用 right-left rule 從變數名讀到底就不會錯。

## 進階：再往深一層

- **函式指標與 CFI**：被攻擊者改寫的函式指標可以把控制流程導向任意位址。Intel CET 的 IBT（上面看到的 `endbr64`）與 clang 的 `-fsanitize=cfi-icall` 都在限制「間接呼叫只能落在型別相符的函式開頭」，[Ch 14](./14-function-call-security.md) 會談。
- **`qsort` 的演算法不是規格的一部分**：規格只說「排序」，不保證穩定、不保證 O(n log n)。glibc 長期以 merge sort 為主（配置不到暫存記憶體時才退回 quicksort），近幾個版本曾嘗試改用 introsort、又因既有程式依賴舊行為而部分回退。不要依賴 `qsort` 的穩定性；你的 glibc 實際用哪個演算法，請以該版本原始碼為準。
- **restrict 的形式化定義**（6.7.3.1）很難讀，其中「based on」的定義與 [Ch 7](./07-pointers-by-the-standard.md) 進階提到的 provenance 是同一類問題。
- **面試題**：「寫一個函式，交換兩個任意型別的變數。」標準答案是 `void swap(void *a, void *b, size_t size)`，內部用 `unsigned char` 逐位元組交換或 `memcpy` 到暫存區；追問是「為什麼不能用 `*a` 交換」與「`a == b` 時會怎樣」。

## 動手練習

1. 把 `sort.c` 的 `cmp_str` 故意改成 `return strcmp(a, b);`，加 `-Wall -Wextra` 編譯，確認沒有警告，觀察排序結果，再用 gdb 在 `cmp_str` 下斷點印出 `(char *) a` 看它實際指向什麼。
2. 寫一個泛型的 `void *find_if(void *base, size_t n, size_t size, int (*pred)(const void *, void *), void *user_data)`，用它找出 `staff` 陣列中第一個年齡大於某個門檻的員工，門檻透過 `user_data` 傳入。這是 [練習 A](./practice-a-generic-container.md) 的熱身。
3. 把 `vol.c` 的 `wait_plain` 改成在迴圈內呼叫一個外部函式 `void relax(void);`，再看 `-O2` 組合語言：`flag` 是否每圈都重讀了？想想為什麼「呼叫一個看不到內容的函式」會有類似 volatile 的效果，以及這為什麼仍然不是正確的同步方式。
4. 用 `perf stat` 或 `clock_gettime` 比較 `qsort` 排序 1000 萬個 `int` 與一個你手寫的、比較直接寫在迴圈裡的 insertion sort + quicksort 混合版本（或 C++ 的 `std::sort`）。記錄結果與編譯旗標，不要只記倍數。

## 本章重點整理

- `void *` 能和任何 object 指標隱式互轉，與 `char *` 表示法相同；不能取值、ISO C 不能做算術（GNU C 可以）；不保證能和函式指標互轉。
- 轉型合法不等於存取合法：`void **` 不是通用的指標的指標，透過不相容型別的 lvalue 存取物件是 strict aliasing 違規。非對齊的指標轉型也是 UB，用 `memcpy` 或逐位元組組合，編譯器會最佳化成單一 load。
- C 只有 call-by-value；要修改呼叫端的 `T`，就傳 `T *`，所以修改指標要用指標的指標。
- function designator 會轉成函式指標，`*` 又把函式指標變回 function designator，所以 `(****f)()` 等於 `f()`。函式指標 + `void *user_data` 是 C 的 callback 與手工 closure。
- `const` 的位置決定保護指標還是資料；volatile 強迫每次存取都真的發生；restrict 承諾無別名，讓編譯器少做 load。

## 自我檢核

- [ ] 不看筆記，逐個參數解釋 `qsort` 的原型為什麼長這樣，並寫出一個對 `double` 陣列正確的比較器。
- [ ] 如果同事寫了 `void free_and_null(void **pp)` 然後呼叫 `free_and_null((void **) &int_ptr)`，你能說出它哪裡有問題、引哪一條規格，並給出替代寫法嗎？
- [ ] 能解釋為什麼 `char **` 不能隱式轉成 `const char **`，並舉出如果允許會發生什麼事嗎？
- [ ] 看到一段 `-O2` 組合語言裡的 `jmp .L4` 自我迴圈，你能想到哪一種 C 原始碼錯誤會產生它嗎？
- [ ] 在你熟悉的某個 C 函式庫裡找一個 callback API，說出它的 user_data 參數叫什麼名字、生命週期由誰負責。

## 延伸閱讀

### 官方文件 / Spec

- **[N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.2.5 §19、§28（void 與 `void *` 的表示法）、6.3.2.3 §1、§7、§8（指標轉換、對齊、函式指標）、6.5 §6–§7（effective type）、6.7.3（qualifier，§6 const、§7 volatile）、6.7.3.1（restrict 的形式定義與範例）、7.22.5.1–7.22.5.2（`bsearch`、`qsort`）。
  - **注意**：6.7.3.1 的形式定義很抽象，先讀後面的 EXAMPLE 1–4，再回頭讀定義。
- **[Why the "volatile" type class should not be used](https://www.kernel.org/doc/html/latest/process/volatile-considered-harmful.html)** — Linux 核心文件
  - **讀哪裡**：整篇（短）。
  - **能學到什麼**：核心開發者為什麼認為 volatile 在核心裡幾乎總是錯的，以及該用什麼取代（鎖、`READ_ONCE`／`WRITE_ONCE`、memory barrier）。前提是讀完本章 volatile 一節。

### 部落格 / 技術文章

- **[Everything you need to know about pointers in C](https://boredzo.org/pointers/)** — Peter Hosey
  - **這篇說什麼**：原文推薦的指標總整理，涵蓋宣告、`const`、函式指標、指標的指標。
  - **讀哪裡**：「Pointers and const」與「Function pointers」兩節。
  - **為什麼值得讀**：寫法平實、例子小，適合讀完本章後當複習。
- **[Demystifying The Restrict Keyword](https://cellperformance.beyond3d.com/articles/2006/05/demystifying-the-restrict-keyword.html)** — Mike Acton（CellPerformance，2006）
  - **這篇說什麼**：從遊戲引擎效能的角度解釋 aliasing 如何阻礙最佳化、restrict 如何解除，附組合語言對照。
  - **讀哪裡**：整篇；PowerPC／Cell 的組合語言細節可以略讀，看「多了哪些 load」即可。
  - **為什麼值得讀**：作者是遊戲引擎效能專家，這篇是 restrict 被引用最多的解說之一。前提是看得懂本章的 `restrict.c` 組合語言對照。

### 書籍

- **《Learn C The Hard Way》** — Zed Shaw（Addison-Wesley，2015）
  - **讀哪幾章**：Exercise 15（Pointers, Dreaded Pointers）、Exercise 16（Structs and Pointers to Them）、Exercise 18（Pointers to Functions）。
  - **定位**：原文列出的練習書，每個練習都要你親手打、親手改壞。適合本章之後動手補量；書中部分觀點較主觀，以規格為準。
- **《C Interfaces and Implementations》** — David Hanson（Addison-Wesley，1996）
  - **讀哪幾章**：第 1–2 章（interface 與 opaque type 的設計方法）、第 8 章（Table，callback 與 `void *` 的實戰）。
  - **定位**：用 `void *` 與函式指標設計通用資料結構的經典，和 [練習 A](./practice-a-generic-container.md) 直接相關。

指標能指向任意 object、任意函式，那「不指向任何東西」該怎麼表示？下一章處理 C 最常用、也最容易出錯的兩種指標用法：字串，以及 null pointer。

→ [Ch 10 字串、null pointer、nullptr 與記憶體安全](./10-strings-and-null.md)
