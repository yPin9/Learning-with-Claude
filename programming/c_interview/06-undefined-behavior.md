# Ch 6 — 未定義行為（UB）全圖

> 目標：知道 C 標準中最常見的 UB 類型，理解為什麼 UB 比 crash 更危險，以及如何用工具偵測。

## 什麼是未定義行為

C 標準說：遇到 UB，「任何事都可能發生」。不是「可能 crash」——而是：
- 可能正常跑（你永遠不知道是偶然）
- 可能給錯誤答案
- 可能被編譯器利用 UB 的假設做出讓你完全意外的優化
- 可能製造安全漏洞（緩衝區溢位的根源）

**UB 比 crash 更危險，因為它通常靜靜地騙你。**

---

## 為什麼編譯器「利用」UB

編譯器可以假設：**你的程式不含 UB**。

```c
int foo(int x) {
    if (x + 1 > x) return 1;   // 有號整數溢位是 UB
    return 0;
}
```

編譯器推理：「有號整數加法不可能溢位（因為那是 UB，你保證不做）。所以 `x+1 > x` 永遠為真。」

```bash
gcc -O2 foo.c
objdump -d a.out | grep foo -A5
# → 直接 return 1，整個條件被優化掉
```

```c
// 更可怕的例子：
int arr[4];
for (int i = 0; i <= 4; i++) {
    arr[i] = 0;   // i==4 時越界：UB
}
// 編譯器：「迴圈不含 UB，所以 i 不可能到 4 之後，
//          所以 i <= 4 等同於 i < 5，所以迴圈跑 5 次」
// 實際行為：可能刪除後面的範圍檢查
```

---

## 最常見的 UB 類型

### 1. 有號整數溢位

```c
int a = INT_MAX;
int b = a + 1;    // UB：有號整數溢位
// 無號整數溢位是 well-defined（模 2^N）
unsigned u = UINT_MAX;
u = u + 1;        // Well-defined：0
```

面試考點：`INT_MIN / -1` 也是 UB（結果是 `INT_MAX + 1`，溢位）。

### 2. 陣列越界

```c
int arr[5];
arr[5] = 0;    // UB：越界（合法索引 0–4）
arr[-1] = 0;   // UB

// 「一過末端」指標合法，但不可 dereference：
int *p = arr + 5;   // 合法（只是指標）
*p = 0;             // UB（dereference 越界指標）
```

### 3. Null pointer dereference

```c
int *p = NULL;
*p = 42;   // UB，通常 SIGSEGV
```

### 4. Use-after-free / Use-after-scope

```c
int *p = malloc(4);
free(p);
*p = 42;   // UB：use-after-free

// use-after-scope（懸空指標）：
int *bad_ptr(void) {
    int x = 5;
    return &x;   // UB：x 是 stack 上的區域變數，函式返回後就不存在
}
```

### 5. 未初始化變數

```c
int x;
printf("%d\n", x);   // UB：不確定值（indeterminate value）

// 編譯器可以假設 x 有任意值，甚至「x 不可能等於 5」
if (x == 5) { ... }  // 可能被整個刪掉
```

### 6. 資料競爭（Data Race）

```c
// 兩個執行緒同時讀寫同一變數（無同步）：UB
// 不只是「可能讀到舊值」，而是整個程式行為未定義
```

### 7. Strict Aliasing 違反

```c
float f = 1.0f;
int  *ip = (int *)&f;
*ip = 0x3F800000;   // UB：透過不相容型別指標存取（見 Ch 7）
```

### 8. 修改字串常數

```c
char *p = "hello";
p[0] = 'H';   // UB：字串常數在唯讀記憶體
```

### 9. 除以零

```c
int x = 5 / 0;     // UB（有號整數）
int y = 5 % 0;     // UB
// 浮點數除以零是 well-defined（±Inf 或 NaN）
```

---

## UB 的標準三分類

| 類型 | 意義 | 例子 |
|------|------|------|
| **Undefined Behavior** | 任何事都可能發生 | 越界、溢位、UB |
| **Unspecified Behavior** | 可能有多種結果，都合法 | `f(a(), b())` 的求值順序 |
| **Implementation-defined** | 由實作決定，但必須文件化 | `char` 是否有號、`int` 大小 |

---

## 偵測 UB 的工具

```bash
# UBSan：執行期偵測
gcc -fsanitize=undefined -g foo.c && ./a.out
# → foo.c:5:11: runtime error: signed integer overflow: 2147483647 + 1 cannot be represented

# ASan：執行期偵測記憶體問題
gcc -fsanitize=address -g foo.c && ./a.out
# → ERROR: AddressSanitizer: stack-buffer-overflow

# 一起用：
gcc -fsanitize=address,undefined -g foo.c

# 靜態分析（clang）：
clang --analyze foo.c
```

---

## 動手練習：這些哪些是 UB？

```c
// (1)
int x = 0;
x = x++;

// (2)
int arr[3] = {1,2,3};
int *p = arr + 3;  // 指向「結尾後一個」
printf("%p\n", (void*)p);

// (3)
int *p = arr + 3;
printf("%d\n", *p);  // dereference 結尾後

// (4)
unsigned int u = 0;
u -= 1;

// (5)
printf("%d %d\n", printf("A"), printf("B"));
```

答：(1) UB（序列點問題，見 Ch 10），(2) 合法（指向末端後一個合法），(3) UB（dereference），(4) Well-defined（UINT_MAX），(5) Unspecified（求值順序未定義）。

---

## 自我檢核

- [ ] 能說出至少 5 種 UB 類型
- [ ] 理解為什麼編譯器優化可以把 UB「放大」成意外行為
- [ ] 知道有號整數溢位和無號整數溢位的不同（後者 well-defined）
- [ ] 能用 `-fsanitize=undefined,address` 在執行期抓 UB

→ [Ch 7 型別轉換與 Type Punning：嚴格別名規則](./07-type-punning-aliasing.md)
