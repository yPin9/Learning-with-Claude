# Ch 9 — 整數系統：有號/無號/提升/轉換

> 目標：掌握 C 整數算術的完整規則：整數提升、usual arithmetic conversions、有號/無號混用的陷阱。

## 整數型別大小保證

C 標準的保證比你想的少：

| 標準保證 | 說明 |
|----------|------|
| `char` >= 8 bits | 可以是有號或無號（implementation-defined） |
| `short` >= 16 bits | |
| `int` >= 16 bits | 現代系統通常 32 bits |
| `long` >= 32 bits | LP64（Linux 64-bit）是 64 bits；LLP64（Windows 64-bit）是 32 bits |
| `long long` >= 64 bits | C99 保證 |

要可移植的固定大小型別，用 `<stdint.h>`：

```c
int8_t   s8;    // 有號 8-bit
uint32_t u32;   // 無號 32-bit
int64_t  s64;   // 有號 64-bit
intptr_t ip;    // 大小足以存指標
ptrdiff_t pd;   // 指標差的型別
size_t   sz;    // sizeof 的回傳型別（無號）
```

---

## 整數提升（Integer Promotion）

**規則**：在表達式求值前，所有比 `int` 小的整數型別（`char`、`short`、`unsigned char` 等）會先被提升為 `int`（或 `unsigned int` 如果 `int` 裝不下）。

```c
char a = 200, b = 100;
char c = a + b;   // a 和 b 提升為 int，int 相加得 300，截斷為 char
// char 有號：300 截斷 = 44
// char 無號：300 % 256 = 44
printf("%d\n", c);   // 44
```

**面試陷阱**：

```c
unsigned char uc = 250;
printf("%d\n", uc - 300);   // uc 提升為 int (250)，250 - 300 = -50（int 算術）
// 不是無號算術！結果是 -50，不是 250-300+256=206
```

---

## Usual Arithmetic Conversions

當兩個不同型別的整數做二元運算時，C 標準規定如何統一型別：

1. 先做整數提升（都至少是 `int`）
2. 若兩邊型別相同，完成
3. 若一邊無號、一邊有號：
   - 若無號型別的等級（rank）>= 有號型別：有號轉換成無號
   - 否則看 `int` 能否表示所有無號值：能就轉成 `int`，不能就轉成 `unsigned`

簡化記法：**有號 + 無號混用，通常轉成無號**。

```c
int s = -1;
unsigned int u = 1;
if (s < u)        // s 被轉成 unsigned int：-1 → UINT_MAX（巨大的正數）
    puts("s < u");
else
    puts("s >= u");   // 這行才印出！-1 變成了比 1 大的無號數
```

這是 C 最惡名昭彰的陷阱之一。

```c
// 常見的正確寫法：強制有號比較
if (s < (int)u) ...
if ((long long)s < (long long)u) ...
```

---

## 有號整數溢位（UB）vs 無號整數環繞（Well-defined）

```c
int    x = INT_MAX;
x = x + 1;   // UB：有號整數溢位

unsigned u = UINT_MAX;
u = u + 1;   // Well-defined：0（環繞，模 2^32）
```

編譯器可以根據「有號整數不溢位」做優化：

```c
for (int i = 0; i <= INT_MAX; i++) { ... }
// 編譯器優化後可能是無限迴圈（i 永遠不溢位，所以條件永遠真）
```

---

## 位元移位的陷阱

```c
int x = -1;
x >> 1;   // 右移有號數：實作定義（算術移位 vs 邏輯移位）
x << 1;   // UB if x < 0（C99 之前；C99 起有號左移負數是 UB）

unsigned u = 1u;
u << 31;  // Well-defined：0x80000000
u << 32;  // UB：移位量 >= 型別寬度
```

安全的位元操作：**總是用 `unsigned` 型別**。

---

## `char` 有號性的陷阱

`char` 是否有號是 implementation-defined：

```c
char c = 0xFF;   // 有號 char：-1；無號 char：255
printf("%d\n", c);   // 可能是 -1 或 255，取決於平台

// 危險的比較（用於解析二進位資料時）：
if (c >= 0x80) ...   // 有號 char 的 0xFF 是 -1，不滿足 >= 0x80
```

安全做法：明確使用 `unsigned char` 或 `uint8_t` 處理位元組資料。

---

## size_t 和有號整數的混用

```c
size_t len = strlen(s);   // size_t 是無號型別
if (len - 1 >= 0) ...    // 永遠為真！size_t(0) - 1 = SIZE_MAX（環繞）
                           // 即使 len == 0，len - 1 是 SIZE_MAX（巨大的數）
```

正確：

```c
if (len > 0 && len - 1 >= ...) ...
// 或：
if ((ssize_t)len - 1 >= 0) ...
```

---

## 面試常考題

**Q：以下輸出是多少？**

```c
#include <stdio.h>
int main(void) {
    unsigned int a = 5;
    int b = -3;
    if (a + b > 10)
        printf("yes\n");
    else
        printf("no\n");
}
```

答：`b` 轉成 `unsigned int`，`-3` → `UINT_MAX - 2`（約 42 億），加上 5 後 > 10，輸出 `yes`。

---

## 自我檢核

- [ ] 能說出整數提升的觸發條件（比 int 小的型別）
- [ ] 知道有號 + 無號混用的轉換規則（通常轉無號）
- [ ] 知道有號整數溢位是 UB，無號環繞是 well-defined
- [ ] 知道 `size_t - 1` 在 `size_t == 0` 時的陷阱

→ [Ch 10 volatile、Sequence Point、記憶體順序](./10-volatile-sequence-point.md)
