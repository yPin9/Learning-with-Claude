# Ch 7 — 型別轉換與 Type Punning：嚴格別名規則

> 目標：理解嚴格別名規則（strict aliasing rule）為何存在，以及在 C 中做 type punning 的合法方式。

## 嚴格別名規則（C99 §6.5）

**規則**：程式只能透過「相容型別」的指標存取一個物件。

「相容型別」包含：
- 相同型別（`int*` 存取 `int`）
- 有號/無號版本（`int*` 和 `unsigned int*` 互相相容）
- `char*` 或 `unsigned char*`（萬用：可存取任何物件）
- 加上 `const`/`volatile` 修飾的版本

**違反這個規則就是 UB**。

---

## 為什麼有這條規則

讓編譯器能做更積極的優化。考慮這個函式：

```c
void add(float *result, int *a, int *b) {
    *result = *a + *b;
    // 編譯器能假設 result、a、b 互不 alias（float* 和 int* 不相容）
    // 因此可以把 *a 和 *b 的值暫存在暫存器，不用每次重讀
}
```

如果沒有 strict aliasing，編譯器必須保守地假設任何指標都可能指向同一塊記憶體。

---

## 常見的 Strict Aliasing 違反

### 錯誤方式：直接 cast 指標

```c
float f = 3.14f;
unsigned int *ip = (unsigned int *)&f;
printf("%u\n", *ip);   // UB！用 int* 存取 float 物件
```

這在 x86 上通常能跑，但不可移植，且 `-O2` 下編譯器可能產生錯誤結果。

### 另一個常見錯誤：透過不同型別解讀網路封包

```c
// 錯誤：
uint8_t buf[8];
// ... 填充 buf ...
uint32_t val = *(uint32_t *)buf;  // UB：用 uint32_t* 存取 uint8_t 物件
                                   // 還有未對齊的問題
```

---

## 合法的 Type Punning 方式

### 方式一：memcpy（**最安全，標準保證**）

```c
#include <string.h>

float f = 1.0f;
uint32_t bits;
memcpy(&bits, &f, sizeof(bits));   // 合法：逐 byte 複製
printf("0x%08X\n", bits);          // 0x3F800000
```

`memcpy` 不要擔心效能：編譯器在最佳化下通常會消除 memcpy 直接用暫存器移動。

### 方式二：union（C 標準明確允許）

```c
union FloatBits {
    float    f;
    uint32_t u;
};

union FloatBits fb = { .f = 1.0f };
printf("0x%08X\n", fb.u);   // 合法：C99/C11 允許透過 union 做 type punning
```

注意：C++ 嚴格不允許這樣做（UB in C++）。

### 方式三：`char*` 或 `unsigned char*`

`char`/`unsigned char` 指標可以存取任何型別的物件——這是 strict aliasing 的例外：

```c
float f = 1.0f;
unsigned char *p = (unsigned char *)&f;
for (size_t i = 0; i < sizeof(float); i++) {
    printf("%02x ", p[i]);   // 合法：逐 byte 讀取
}
```

---

## 型別轉換（Casts）詳解

### 整數轉換

```c
int    i = 300;
char   c = (char)i;     // 實作定義：300 超出 char 範圍（8-bit 有號）
                         // 通常 300 mod 256 = 44 或 -68（取決於 char 是否有號）
unsigned char uc = (unsigned char)i;  // Well-defined：300 % 256 = 44
```

### 指標轉整數

```c
uintptr_t addr = (uintptr_t)ptr;   // 合法：uintptr_t 大小夠放指標
int       bad  = (int)ptr;          // UB in C（指標可能比 int 大）
```

### 函式指標轉換

```c
void (*fp)(void) = (void (*)(void))some_func;
// 透過不相容的函式指標呼叫：UB
// 只有轉回原始型別再呼叫才合法
```

---

## `-fno-strict-aliasing` 旗標

若你有舊程式碼大量違反 strict aliasing，可以暫時用這個旗標關閉優化假設：

```bash
gcc -O2 -fno-strict-aliasing foo.c
```

但這是妥協方案，正確做法是用 `memcpy` 修正程式碼。

---

## 自我檢核

- [ ] 能說明嚴格別名規則的核心：只能透過相容型別指標存取物件
- [ ] 知道 `char*` 是 strict aliasing 的例外（可以存取任何物件）
- [ ] 能說出三種合法的 type punning 方式（memcpy、union、char*）
- [ ] 知道直接 cast 指標做 type punning 是 UB

→ [Ch 8 字串陷阱](./08-string-traps.md)
