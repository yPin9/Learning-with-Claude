# Ch 2 — 指標深度剖析：運算、const、restrict

> 目標：能解讀任意複雜指標宣告，正確使用 const 的四種位置，理解 restrict 對編譯器的語意承諾。

## 指標的本質

指標是存放記憶體位址的整數。在 64-bit 系統上，任何型別的指標都是 8 bytes——`sizeof(int*)` == `sizeof(char*)` == `sizeof(void*)` == 8。

```c
int   x = 42;
int  *p  = &x;       // p 存放 x 的位址
int **pp = &p;       // pp 存放 p 的位址

*p   = 99;           // dereference：透過 p 修改 x，x 變 99
**pp = 0;            // 兩次 dereference：x 變 0
```

---

## 指標算術

指標加減的**單位**是所指型別的 `sizeof`，不是 bytes：

```c
int arr[5] = {10, 20, 30, 40, 50};
int *p = arr;   // p → &arr[0]

p++;            // p 移動 sizeof(int)=4 bytes → arr[1]
p += 2;         // 再移動 2*4=8 bytes → arr[3]

printf("%d\n", *p);         // 40
printf("%d\n", *(arr+1));   // 20（arr[1] 等同 *(arr+1)）

// 指標相減：回傳 ptrdiff_t（元素個數）
ptrdiff_t diff = (arr+4) - p;   // 4 - 3 = 1
```

**越界就是 UB**，即使不 dereference，越界的指標算術本身也是 UB（除了指向「最後一個元素之後」這個特例）：

```c
int *bad = arr + 10;  // UB：超出陣列範圍的指標值
```

---

## const 的四種位置

`const` 修飾它**右邊**最近的東西。從右往左讀宣告：

```c
int       *p1;         // p1 是指向 int 的指標（都可改）
const int *p2;         // p2 是指向 const int 的指標（*p2 不可改）
int const *p3;         // 等同 p2
int *const p4 = &x;   // p4 是 const 指標指向 int（p4 不可改）
const int *const p5 = &x; // 都不可改
```

記法：`const` 在 `*` 左邊 → 指向的值不可改；`const` 在 `*` 右邊 → 指標本身不可改。

**面試最常考的形式**：

```c
// 函式不修改傳入的字串：
void print(const char *s);    // s 是指向 const char 的指標

// 函式不修改陣列，但可以讓指標移動：
void scan(const int *arr, int n);

// 函式連指標都不能改（少見，通常沒意義）：
void fixed(int * const p);
```

`const` 只是編譯器約束，不是硬體保護，用 cast 可以繞過（但那是 UB）：

```c
const int ci = 42;
((int *)&ci)[0] = 99;  // UB！ci 可能在唯讀記憶體
```

---

## 複雜指標宣告：從右往左讀

規則：從變數名出發，**先往右讀（遇右括號停），再往左讀**。

```c
int  *f(void);          // f：函式，返回 int*
int  (*fp)(void);       // fp：指標，指向 void→int 的函式
int  (*arr)[5];         // arr：指標，指向含 5 個 int 的陣列
int  *arr2[5];          // arr2：含 5 個 int* 的陣列
```

最惡的考題：

```c
void (*signal(int, void(*)(int)))(int);
// signal 是函式，接受 (int, void(*)(int))，返回 void(*)(int)
// 即：signal 的返回值也是一個函式指標
```

遇到這種，先用 `typedef` 拆解：

```c
typedef void (*SigHandler)(int);
SigHandler signal(int signum, SigHandler handler);
```

---

## void 指標

`void *` 可以指向任何型別，但**不能 dereference，不能做指標算術**：

```c
void *vp = malloc(100);  // malloc 返回 void*
int  *ip = vp;           // C：隱式轉換合法；C++：必須顯式 cast
*(int *)vp = 42;         // 先 cast 才能 dereference

// 不合法：
// *vp = 42;             // 編譯錯誤
// vp++;                 // C 標準不允許（gcc extension 允許但不可移植）
```

---

## restrict（C99）

`restrict` 是你對編譯器的承諾：**這個指標是在其生命週期內存取該記憶體物件的唯一方式**——不存在 aliasing。

```c
// 標準 memcpy 宣告：
void *memcpy(void * restrict dst, const void * restrict src, size_t n);
// 你保證 dst 和 src 不重疊；否則是 memmove 的工作

// 實際效果：
void add(float * restrict a, const float * restrict b, int n) {
    for (int i = 0; i < n; i++) a[i] += b[i];
    // 沒有 restrict：編譯器必須每次從記憶體重讀 b[i]（因為 a 可能 alias b）
    // 有 restrict：b[i] 可以暫存暫存器，允許向量化
}
```

錯誤使用 `restrict`（謊稱不 alias）→ UB：

```c
float arr[10];
add(arr, arr, 10);  // dst 和 src 是同一塊記憶體，違反 restrict 承諾
```

---

## 常見面試題

**Q1：`int *p = NULL; printf("%zu\n", sizeof(*p));` 會 crash 嗎？**

不會。`sizeof` 在編譯期求值，完全不 dereference，輸出 `4`。

**Q2：`++*p` 和 `*p++` 差在哪？**

```c
int x = 5, *p = &x;
++*p;   // (*p)++：x 變 6，p 不動
*p++;   // *(p++)：取 *p 後 p 遞增，等同 *(p++)
        // 後置 ++ 優先級高於 *
```

**Q3：`int *p; p = p + 1;` 合法嗎？**

`p` 未初始化，值是不確定的（indeterminate）。對不確定值的指標做算術是 UB，即使不 dereference。

---

## 自我檢核

- [ ] 能說明指標加 1 移動多少 bytes（依所指型別）
- [ ] 能解讀 `const int *p`、`int * const p`、`const int * const p`
- [ ] 能用從右往左法解讀 `int (*fp)(int, char*)`
- [ ] 能解釋 `restrict` 的語意和用途
- [ ] 知道 `sizeof(NULL pointer dereference)` 不 crash

→ [Ch 3 陣列與指標的真正關係](./03-arrays-vs-pointers.md)
