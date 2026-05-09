# 練習 A — UB 偵錯題集

> 目標：把 Ch 6–10 學到的 UB 知識應用到實際程式碼，找出問題、解釋原因、寫出修正版本。

## 規則

每道題給你一段有問題的 C 程式碼。你需要：
1. 指出 **問題在哪裡**（行號）
2. 解釋 **為什麼是問題**（UB、陷阱、邏輯錯誤）
3. 寫出 **修正版本**

先自己做，再看參考解答。

---

## 題目 1：整數溢位陷阱

```c
#include <stdio.h>

int is_positive_sum(int a, int b) {
    return (a + b > 0);
}

int main(void) {
    printf("%d\n", is_positive_sum(2147483647, 1));
    return 0;
}
```

<details>
<summary>參考解答</summary>

**問題**：`a + b` 在 `a = INT_MAX, b = 1` 時溢位，有號整數溢位是 UB。

編譯器在 `-O2` 下可能假設 `a + b` 不溢位，優化掉某些分支。

**修正**：用 `long long` 或提前檢查：

```c
int is_positive_sum(int a, int b) {
    return ((long long)a + b > 0);
}
// 或：
int is_positive_sum(int a, int b) {
    if (a > 0 && b > INT_MAX - a) return 1;   // 溢位但和必為正
    return (a + b > 0);
}
```
</details>

---

## 題目 2：有號/無號混用

```c
#include <stdio.h>
#include <string.h>

void process(const char *str, int offset) {
    size_t len = strlen(str);
    if (len - offset > 0) {
        printf("有效的 offset\n");
    } else {
        printf("offset 超過字串長度\n");
    }
}

int main(void) {
    process("hello", 10);
    return 0;
}
```

<details>
<summary>參考解答</summary>

**問題**：`len - offset`。`len` 是 `size_t`（無號），`offset` 是 `int`（有號）。  
根據 usual arithmetic conversions，`offset` 被轉成 `size_t`。  
`offset = 10`，`len = 5`，`(size_t)5 - (size_t)10 = SIZE_MAX - 4`（無號環繞），結果是個巨大的正數，大於 0，印出「有效的 offset」——錯了。

**修正**：

```c
if ((ssize_t)len - offset > 0)    // 強制有號算術
// 或：
if (offset >= 0 && (size_t)offset < len)
```
</details>

---

## 題目 3：懸空指標

```c
#include <stdio.h>

int *get_value(void) {
    int x = 42;
    return &x;
}

int main(void) {
    int *p = get_value();
    printf("%d\n", *p);
    return 0;
}
```

<details>
<summary>參考解答</summary>

**問題**：`x` 是 `get_value` 的區域變數，存在 stack 上。函式返回後 `x` 的 stack frame 被釋放，`p` 是懸空指標（dangling pointer）。`*p` 是 UB——通常「剛好可以跑」（舊值還在 stack 上），但在 `-O2` 下可能被完全優化掉，或給出任意值。

**修正**：

```c
// 方案一：用 static（但不是執行緒安全的）
int *get_value(void) {
    static int x = 42;
    return &x;
}

// 方案二：傳入 output 指標
void get_value(int *out) {
    *out = 42;
}

// 方案三：malloc（呼叫者負責 free）
int *get_value(void) {
    int *p = malloc(sizeof(int));
    if (p) *p = 42;
    return p;
}
```
</details>

---

## 題目 4：strcpy 緩衝區溢位

```c
#include <stdio.h>
#include <string.h>

void greet(const char *name) {
    char buf[16];
    strcpy(buf, "Hello, ");
    strcat(buf, name);
    printf("%s\n", buf);
}

int main(void) {
    greet("AliceAliceAliceAlice");
    return 0;
}
```

<details>
<summary>參考解答</summary>

**問題**：`buf` 只有 16 bytes，`"Hello, "` 佔 8 bytes（含 `\0`），還剩 8 bytes，但 `name` 可以是任意長度。`strcat` 會越界寫入，覆蓋 stack 上的返回地址——經典的棧溢位漏洞。

**修正**：

```c
void greet(const char *name) {
    char buf[64];
    snprintf(buf, sizeof(buf), "Hello, %s", name);
    printf("%s\n", buf);
}
// 或更嚴格的大小檢查：
void greet(const char *name) {
    const char prefix[] = "Hello, ";
    size_t total = sizeof(prefix) + strlen(name);
    char *buf = malloc(total);
    if (!buf) return;
    snprintf(buf, total, "%s%s", prefix, name);
    printf("%s\n", buf);
    free(buf);
}
```
</details>

---

## 題目 5：Use-after-free

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct {
    char *name;
    int  age;
} Person;

Person *create_person(const char *name, int age) {
    Person *p = malloc(sizeof(Person));
    p->name = malloc(strlen(name) + 1);
    strcpy(p->name, name);
    p->age = age;
    return p;
}

void free_person(Person *p) {
    free(p->name);
    free(p);
}

int main(void) {
    Person *alice = create_person("Alice", 30);
    free_person(alice);
    printf("Name: %s\n", alice->name);   // Bug?
    return 0;
}
```

<details>
<summary>參考解答</summary>

**問題**：`free_person(alice)` 後，`alice` 指向已釋放的記憶體（dangling pointer），`alice->name` 指向的記憶體也已被 `free`。兩次 use-after-free，都是 UB。

在實務中可能「剛好可以跑」（heap allocator 不立刻清除釋放的記憶體），但這是安全漏洞（UAF 是重要的漏洞類型）。

**修正**：

```c
void free_person(Person **pp) {
    if (!pp || !*pp) return;
    free((*pp)->name);
    (*pp)->name = NULL;
    free(*pp);
    *pp = NULL;   // 清 NULL 防止再次 use-after-free
}

// 呼叫：
free_person(&alice);
// alice 現在是 NULL，再 dereference 會 segfault（明顯的錯誤）
```
</details>

---

## 題目 6：陣列越界 + 指標陷阱

```c
#include <stdio.h>

int main(void) {
    int a[5] = {1, 2, 3, 4, 5};
    int *p = &a[5];   // (A)
    printf("%d\n", *p);   // (B)

    for (int i = 0; i <= 5; i++)   // (C)
        printf("%d ", a[i]);
    printf("\n");
    return 0;
}
```

<details>
<summary>參考解答</summary>

**(A)**：`&a[5]` 是指向「最後一個元素之後」的指標。C 標準允許這樣的指標存在，這不是 UB。

**(B)**：`*p` dereference 越界指標，UB。即使 `p` 的值合法，解引用就不行。

**(C)**：`i <= 5` 讓迴圈多跑一次（`i=5`），`a[5]` 越界，UB。應改為 `i < 5`。

**修正**：

```c
int *end = a + 5;   // 哨兵，合法，但不 dereference
for (int *p = a; p != end; p++)
    printf("%d ", *p);
```
</details>

---

## 題目 7：Volatile 誤用

```c
#include <stdio.h>
#include <pthread.h>

volatile int counter = 0;

void *increment(void *arg) {
    for (int i = 0; i < 1000000; i++)
        counter++;   // 以為 volatile 讓這安全？
    return NULL;
}

int main(void) {
    pthread_t t1, t2;
    pthread_create(&t1, NULL, increment, NULL);
    pthread_create(&t2, NULL, increment, NULL);
    pthread_join(t1, NULL);
    pthread_join(t2, NULL);
    printf("counter = %d\n", counter);  // 期望 2000000？
    return 0;
}
```

<details>
<summary>參考解答</summary>

**問題**：`counter++` 在多執行緒下是 **data race**（C11 的 UB），因為它是非原子的讀-改-寫操作：`load` → `add` → `store`，兩個執行緒可能同時在 `load` 步驟讀到相同的值，導致計數丟失。

`volatile` 只防止編譯器把 `counter` 快取在暫存器——但它不讓 `counter++` 變成原子操作。

**修正**：使用 C11 `_Atomic` 或互斥鎖：

```c
#include <stdatomic.h>
atomic_int counter = ATOMIC_VAR_INIT(0);

// 每次 counter++ 等同 atomic_fetch_add(&counter, 1)，是原子的
```
</details>

---

## 自我檢核

- [ ] 能從程式碼快速識別有號整數溢位的風險
- [ ] 能識別有號/無號混用的比較陷阱
- [ ] 知道懸空指標的兩種來源（stack 區域變數返回、free 後繼續使用）
- [ ] 能解釋為什麼 `volatile counter++` 不是執行緒安全的

→ [Ch 11 malloc / calloc / realloc / free 內部機制](./11-malloc-internals.md)
