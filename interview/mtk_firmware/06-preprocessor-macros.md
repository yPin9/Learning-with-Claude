# Ch 6 — 前置處理器與巨集

> **目標**：掌握 `#define` 巨集的正確寫法與陷阱（MIN/SQR 的括號與 side effect）、`#define` vs typedef、`#`/`##` 運算子、`#error`、條件編譯與 include guard。這是上機考手寫題的高頻來源。

> **環境**：C，`gcc -Wall -E`（`-E` 看前處理結果）。前置：Ch 1-5。

## 為什麼考這個

巨集是「純文字替換」，這個本質產生一堆陷阱——少一對括號、參數有副作用，結果就錯。MTK 上機考愛叫你「寫一個 MIN/SQR 巨集」，看你會不會踩這些坑。答對展現你懂前處理器的運作，不只是會用。

## 先建立直覺：巨集是「無腦文字替換」

```
   #define SQUARE(x) x*x
   程式碼：  SQUARE(2+3)
   前處理器替換（純文字貼上，不管語意）：  2+3*2+3  = 2+6+3 = 11  ← 不是 25！

   前處理器不懂 C 語法、不算數學，它只做「把 SQUARE(2+3) 的文字換成 x*x、
   再把 x 換成 2+3」的字串貼上。
```

關鍵心智模型：**巨集在編譯前做純文字替換（`gcc -E` 可看），不是函式呼叫、沒有型別、不算值。** 所有巨集陷阱都源於這個「無腦貼上」。

## 巨集的兩大陷阱

### 陷阱一：括號（運算子優先序）

```c
#define SQUARE(x) x*x          // 壞
SQUARE(2+3)  →  2+3*2+3 = 11   // 錯！

#define SQUARE(x) ((x)*(x))    // 對：每個參數和整體都包括號
SQUARE(2+3)  →  ((2+3)*(2+3)) = 25   // 對

#define DOUBLE(x) 2*x          // 壞
10/DOUBLE(5)  →  10/2*5 = 25   // 錯！(本意 10/(2*5)=1)
#define DOUBLE(x) (2*(x))      // 對
```

規則：**巨集的每個參數都包括號，整個替換結果也包括號。** 少一層都可能被外面的運算子搶走優先序。

### 陷阱二：side effect（參數被求值多次）

```c
#define MAX(a,b) ((a)>(b)?(a):(b))   // 括號對了，但...
MAX(i++, j++)  →  ((i++)>(j++)?(i++):(j++))
// i++ 或 j++ 被求值「兩次」！（比較一次、回傳一次）→ i 或 j 多加了一次
```

這是巨集**和函式的本質差異**：函式參數只求值一次（先算好再傳進去），巨集是文字替換，參數出現幾次就求值幾次。所以 `MAX(i++, j++)` 會讓 `i` 或 `j` 遞增兩次——bug。

防範：**呼叫巨集時別傳有副作用的參數**（`i++`、`f()`）。或用 C99 的 inline function 取代巨集（函式參數只求值一次，又有型別檢查）。

## 寫一個安全的 MIN/MAX 巨集（經典手寫題）

```c
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
```

要點：
1. 每個參數包括號：`(a)`、`(b)`——防優先序。
2. 整體包括號：`(...)`——防外面搶優先序。
3. 用三元運算子（巨集不能有 `if/return`，要用表達式）。

面試會追問「這巨集有什麼問題」——答 **side effect**（`MIN(i++, j++)` 會多求值），這是它和函式的差異，避不掉（除非用 inline function / GCC 的 statement expression）。

## #define vs typedef（經典陷阱題）

兩者都能「取別名」，但機制不同：`#define` 是文字替換，`typedef` 是真正的型別別名。差異在多變數宣告：

```c
#define PINT int*
typedef int* TINT;

PINT p1, p2;    // 替換成 int* p1, p2;  →  p1 是 int*，p2 是 int！（只有第一個是指標）
TINT q1, q2;    // q1, q2 都是 int*（typedef 是真型別，套用到每個變數）
```

`#define PINT int*` 純文字替換成 `int* p1, p2`，而 C 裡 `int* p1, p2` 的 `*` 只綁 p1——所以 p2 是普通 int。`typedef` 不同，它讓 `TINT` 真正等於「int 指標」這個型別，q1、q2 都是指標。

**結論：取型別別名用 typedef，不要用 #define。** 這是經典考古題（`dPS p1, p2` 的陷阱）。

## # 與 ##：字串化與貼接

```c
#define STR(x) #x              // # = 字串化（把參數變字串字面值）
STR(hello)   →  "hello"
STR(1+2)     →  "1+2"

#define CONCAT(a,b) a##b       // ## = token 貼接（把兩個 token 黏成一個）
CONCAT(foo, bar)  →  foobar
int CONCAT(var, 1) = 5;        // → int var1 = 5;
```

`#` 把參數變成字串（debug 巨集常用，如 `#define DBG(x) printf(#x " = %d\n", x)`）；`##` 把 token 黏接（產生變數名/函式名）。這兩個是進階但偶爾考。

## #error 與條件編譯

```c
#define VERSION 2

#if VERSION < 2
  #error "Version must be >= 2"   // 編譯時直接報錯停止
#endif

#ifdef DEBUG          // 若定義了 DEBUG
  printf("debug\n");
#endif

#ifndef CONFIG_H      // include guard（防重複引入）
#define CONFIG_H
  ... 標頭內容 ...
#endif
```

- `#error`：編譯期主動報錯（檢查設定不對就停，韌體常用來擋錯誤的編譯設定）。MTK 考古題問過「`#error` 用途」。
- `#if/#ifdef/#ifndef`：條件編譯（依設定決定編哪段，韌體做不同硬體版本的常用手段）。
- **include guard**（`#ifndef X / #define X / #endif`）：防止標頭被重複 include 導致重複定義。每個 `.h` 都該有。

## 考古題詳解

### Q1：`SQUARE(2+3)` 印出什麼？（`#define SQUARE(x) x*x`）

<details>
<summary>詳解</summary>

**11**（不是 25）。文字替換成 `2+3*2+3`，按優先序 = `2 + 6 + 3` = 11。

修正：`#define SQUARE(x) ((x)*(x))` → `((2+3)*(2+3))` = 25。

**考點**：巨集括號（優先序）陷阱，必考。
</details>

### Q2：寫一個 MIN 巨集，並說出它的問題

<details>
<summary>詳解</summary>

```c
#define MIN(a, b) ((a) < (b) ? (a) : (b))
```

問題：**side effect**——`MIN(i++, j++)` 會把較小者（被選中的那個）求值兩次（比較一次、回傳一次），導致它多遞增一次。這是巨集（文字替換、多次求值）vs 函式（參數求值一次）的本質差異，無法在巨集內完全避免。

**考點**：MTK/Nigel Jones 經典題——寫 MIN + 指出 side effect。
</details>

### Q3：`#define dPS struct s *` 之後 `dPS p1, p2;` 中 p1、p2 各是什麼？

<details>
<summary>詳解</summary>

替換成 `struct s * p1, p2;`——**p1 是 `struct s *`（指標），p2 是 `struct s`（結構實體，不是指標）！** 因為 `*` 只綁 p1。

正解用 typedef：`typedef struct s * tPS; tPS p1, p2;` → p1、p2 都是指標。

**考點**：#define vs typedef 的經典差異，必考。
</details>

### Q4：`#define` 用 `\` 換行、巨集寫多行？

```c
#define SWAP(a, b) do { \
    int tmp = (a);      \
    (a) = (b);          \
    (b) = tmp;          \
} while(0)
```

<details>
<summary>詳解</summary>

多行巨集用 `\` 接續，並用 `do { ... } while(0)` 包起來。

**為什麼 do-while(0)**：讓多行巨集能像單一語句一樣用（含分號、能放進 `if` 後不加大括號的地方）。若直接用 `{ }`，`if(x) SWAP(a,b); else ...` 會因為 `};` 變成 `if(x){...}; else` → 語法錯。`do{}while(0)` 後面接分號剛好成一個完整語句。

**考點**：多行巨集的 do-while(0) 慣用法，進階加分。
</details>

### Q5：include guard 是什麼？為什麼需要？

<details>
<summary>詳解</summary>

```c
#ifndef MYHEADER_H
#define MYHEADER_H
/* 標頭內容 */
#endif
```

防止同一個 `.h` 被重複 `#include`（直接或間接）導致**重複定義**（型別、變數重複宣告 → 編譯錯）。第一次 include 時定義 `MYHEADER_H`，之後再 include，`#ifndef` 為假就跳過整段。

（現代也可用 `#pragma once`，較簡潔但非標準。）

**考點**：include guard 機制與必要性。
</details>

## 踩雷集錦

1. **巨集參數沒包括號**：`SQUARE(2+3)` = 11。每個參數 + 整體都包括號。
2. **傳有副作用的參數給巨集**：`MAX(i++,j++)` 多求值。別傳 `i++`/`f()`。
3. **用 #define 取型別別名**：`#define PINT int*` 多變數宣告出錯。用 typedef。
4. **多行巨集沒用 do-while(0)**：在 if-else 裡會語法錯。
5. **標頭沒 include guard**：重複 include → 重複定義錯誤。
6. **以為巨集是函式**：巨集無型別檢查、無作用域、文字替換、多次求值——本質不同。能用 inline function 就用（C99）。

## 速記

- 巨集 = **編譯前純文字替換**（`gcc -E` 可看），不是函式。
- 兩大陷阱：**括號**（每參數+整體包，防優先序）、**side effect**（參數出現幾次求值幾次，別傳 `i++`）。
- `#define` vs typedef：取型別別名用 **typedef**（`#define PINT int*` 多變數宣告出錯）。
- `#` 字串化、`##` token 貼接；`#error` 編譯期報錯；`#ifdef` 條件編譯。
- 多行巨集用 `\` + `do{...}while(0)`；標頭用 include guard。

## 自我檢核

- [ ] `#define SQUARE(x) x*x`，`SQUARE(2+3)` 是多少？怎麼修？
- [ ] 寫一個 MIN 巨集，並說出它無法避免的問題是什麼？
- [ ] `#define PINT int*` 後 `PINT a, b;`，a、b 各是什麼？為什麼該用 typedef？
- [ ] 多行巨集為什麼用 `do{}while(0)`？
- [ ] include guard 在防什麼？怎麼寫？

## 延伸閱讀

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q1（一年秒數 #define）、Q2（MIN 巨集 + side effect）、Q3（#error）、Q15（typedef vs #define）。
  - **和本章的關聯**：本章巨集考點的主要源頭，全是 MTK/嵌入式經典題。

### 書籍

- **《C Programming Language (K&R)》** — Ch 4.11 The C Preprocessor
  - **讀哪幾章**：4.11（macro、#include、conditional inclusion）。
  - **和本章的關聯**：前處理器的權威定義。

巨集搞定，下一章是韌體與上機考都最愛的——位元運算，set/clear/toggle bit、判 2 次方、XOR swap 一次練齊。

→ [Ch 7 位元運算](./07-bit-manipulation.md)
