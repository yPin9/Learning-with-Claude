# Ch 28 — 經典 C 陷阱題 40 道

> 目標：快速檢視整個課程的知識點，透過「你知道輸出是什麼？」來確認理解深度。

每題先自己想，再看答案。

---

## Part A：整數算術（Q1–Q10）

**Q1**：輸出是什麼？

```c
printf("%d\n", 2147483647 + 1);
```

**A1**：UB（有號整數溢位）。-O2 下通常是 -2147483648，但不保證。

---

**Q2**：

```c
unsigned int a = 5;
int b = -3;
printf("%d\n", a + b > 10 ? 1 : 0);
```

**A2**：輸出 `1`。`b` 轉成 `unsigned int`：`-3 → UINT_MAX-2 ≈ 42億`，加 5 後 > 10。

---

**Q3**：

```c
size_t len = 0;
printf("%zu\n", len - 1);
```

**A3**：`SIZE_MAX`（無號環繞，約 18446744073709551615）。

---

**Q4**：

```c
unsigned char c = 250;
int result = c - 300;
printf("%d\n", result);
```

**A4**：`-50`。`c` 被整數提升為 `int`（250），然後 250 - 300 = -50 在 int 算術裡計算。

---

**Q5**：

```c
int i = 0;
printf("%d %d\n", i++, i++);
```

**A5**：未指定（unspecified）。可能是 `0 1` 或 `1 0`，取決於引數求值順序。不是 UB（求值結果本身是 well-defined），但順序不保證。

---

**Q6**：

```c
int i = 5;
int x = i++ + ++i;
printf("%d\n", x);
```

**A6**：UB。`i` 在沒有 sequence point 的同一表達式裡被多次修改。

---

**Q7**：

```c
int x = -1;
printf("%d\n", x >> 1);
```

**A7**：實作定義（implementation-defined）。大多數系統是算術右移：結果是 `-1`（保持符號位）。但 C 標準允許邏輯右移（結果是 `2147483647`）。

---

**Q8**：

```c
unsigned u = 1u;
printf("%u\n", u << 31);
printf("%u\n", u << 32);
```

**A8**：`2147483648`（0x80000000），然後 UB（移位量 >= 型別寬度）。

---

**Q9**：

```c
char c = '\xff';
printf("%d\n", c);
```

**A9**：實作定義。有號 `char`：`-1`；無號 `char`：`255`。

---

**Q10**：

```c
for (int i = INT_MAX - 1; i <= INT_MAX; i++)
    printf("%d\n", i);
```

**A10**：無限迴圈。`i = INT_MAX` 後 `i++` 溢位是 UB，編譯器可以假設溢位不發生，讓迴圈條件永遠成立。

---

## Part B：指標與陣列（Q11–Q20）

**Q11**：

```c
int a[5] = {1,2,3,4,5};
int *p = a + 5;
printf("%p\n", (void *)p);   // 合法嗎？
printf("%d\n", *p);          // 合法嗎？
```

**A11**：`p = a+5` 合法（past-the-end pointer，允許存在）；`*p` 不合法（dereference past-the-end 是 UB）。

---

**Q12**：

```c
int a[3][4];
printf("%zu\n", sizeof(a[0]));
printf("%zu\n", sizeof(a));
```

**A12**：`16`（一行 4 個 int），`48`（整個陣列）。

---

**Q13**：

```c
int a[5] = {0};
int *p = &a[5];
int *q = a + 5;
printf("%d\n", p == q);
```

**A13**：`1`（相等）。兩者都是 past-the-end pointer，指向相同地址。

---

**Q14**：

```c
void foo(int arr[10]) {
    printf("%zu\n", sizeof(arr));
}
```

**A14**：指標大小（8，64-bit 系統）。函式參數的陣列 decay 成指標，`sizeof` 拿到的是指標大小，不是陣列大小。

---

**Q15**：

```c
int a[5] = {1,2,3,4,5};
printf("%d\n", *(&a + 1) - 1);
```

**A15**：`5`（最後一個元素）。`&a` 是 `int(*)[5]`，`&a + 1` 跳過整個陣列，`*(&a + 1)` 是 `int*`，指向 `a[5]`（past-the-end），減 1 得 `&a[4]`，解引用得 5。

---

**Q16–Q20 略（跳到後面更難的）**

---

## Part C：字串（Q21–Q25）

**Q21**：

```c
char s[3] = "abc";
printf("%zu\n", strlen(s));
```

**A21**：UB。`s` 沒有 `'\0'`（`char s[3]` 只夠放 `'a','b','c'`），`strlen` 會讀越界。

---

**Q22**：

```c
char *p = "hello";
p[0] = 'H';
```

**A22**：UB。字串常數在 text segment（唯讀），修改它通常導致 segfault。

---

**Q23**：

```c
char dst[5];
strncpy(dst, "hello world", sizeof(dst));
printf("%s\n", dst);
```

**A23**：UB。`strncpy` 複製 5 個字元但不補 `'\0'`，`printf` 讀越界。

---

**Q24**：

```c
char *a = "hello";
char *b = "hello";
printf("%d\n", a == b);
```

**A24**：可能是 `1`（string interning：編譯器可能讓兩個字串常數共用同一地址），也可能是 `0`。不要比較字串指標，要用 `strcmp`。

---

**Q25**：

```c
char buf[8] = "hello";
printf("%zu %zu\n", strlen(buf), sizeof(buf));
```

**A25**：`5 8`。`strlen` 數到 `'\0'`，`sizeof` 返回宣告的陣列大小。

---

## Part D：記憶體（Q26–Q30）

**Q26**：

```c
int *p = malloc(sizeof(int));
*p = 42;
int **pp = &p;
free(p);
printf("%p\n", (void *)*pp);
```

**A26**：`*pp` 是 `p` 的值，即原本的地址（dangling pointer）。`free` 不清零 `p`，所以 `*pp` 仍然是那個地址（但已失效）。

---

**Q27**：

```c
void *p = malloc(0);
printf("%p\n", p);
free(p);
```

**A27**：`p` 可能是非 NULL（glibc 回傳有效指標）或 NULL，實作定義。`free(p)` 在兩種情況下都合法。

---

**Q28**：

```c
int *a = malloc(10 * sizeof(int));
int *b = realloc(a, 5 * sizeof(int));
printf("%p %p\n", (void *)a, (void *)b);
```

**A28**：縮小通常是原地操作，`a == b` 可能相等。但若縮小導致搬遷（罕見），`a != b` 且 `a` 已被 free。所以此後不應再使用 `a`。

---

**Q29**：

```c
struct { int x; char y; int z; } s;
printf("%zu\n", sizeof(s));
```

**A29**：`12`（非 `9`）。`z` 需要 4-byte 對齊，`y` 後有 3 bytes padding。

---

**Q30**：

```c
int arr[5] = {1,2,3,4,5};
int *p = arr;
p += 2;
printf("%d\n", *(p - 1));
printf("%d\n", p[-1]);   // 合法嗎？
```

**A30**：兩行都輸出 `2`，都合法。`p[-1]` 等價 `*(p - 1)`。

---

## Part E：並行與 volatile（Q31–Q35）

**Q31**：

```c
volatile int counter = 0;
// 兩個執行緒各執行 counter++ 一百萬次
// 最終 counter 是多少？
```

**A31**：不保證是 2000000。`counter++` 是 load-add-store 三步，兩執行緒同時做有 data race（C11 UB），`volatile` 不提供原子性。

---

**Q32**：

```c
int x = 0;
// Thread 1: x = 1;
// Thread 2: if (x == 1) printf("yes\n");
```

**A32**：Data race（UB）。需要用 `_Atomic` 或 mutex，否則 Thread 2 可能永遠看不到 `x = 1`（CPU cache 或 reorder）。

---

**Q33–Q35 略**

---

## Part F：UB 綜合（Q36–Q40）

**Q36**：

```c
int *foo(void) {
    int arr[10] = {0};
    return arr;
}
```

**A36**：回傳 stack 上的局部陣列地址。函式返回後 stack frame 被回收，`arr` 是 dangling pointer。UB。

---

**Q37**：

```c
union { int i; float f; } u;
u.f = 1.0f;
printf("%d\n", u.i);
```

**A37**：合法（C 標準允許用 union 做 type punning）。輸出 `1065353216`（0x3F800000，1.0f 的 IEEE 754 表示）。

---

**Q38**：

```c
int a = 1, b = 0;
int c = a || b++;
printf("%d %d\n", c, b);
```

**A38**：`1 0`。`||` 有 sequence point，左邊為真時不求值右邊（short-circuit），`b++` 沒有執行。

---

**Q39**：

```c
int arr[] = {1, 2, 3};
printf("%zu\n", sizeof(arr) / sizeof(arr[0]));
```

**A39**：`3`。`sizeof(arr) = 12`，`sizeof(arr[0]) = 4`，`12 / 4 = 3`。這是 C 計算陣列元素數的標準寫法。

---

**Q40**：

```c
typedef struct Node { struct Node *next; } Node;
Node *head = NULL;
// free 整個鏈表：
while (head) {
    free(head);            // Bug？
    head = head->next;     // UAF：head 已被 free，存取 head->next 是 UB
}
```

**A40**：Bug。`free(head)` 後再存取 `head->next` 是 use-after-free。正確寫法：先存 next，再 free。

```c
while (head) {
    Node *next = head->next;  // 先存
    free(head);
    head = next;
}
```

---

## 自我檢核

- [ ] Q1–Q10 整數陷阱全部答對
- [ ] Q11–Q20 指標陷阱至少答對 7 題
- [ ] 知道 Q40 的鏈表釋放 bug（必考）

→ [Ch 29 面試手寫題 20 道](./29-impl-interview-questions.md)
