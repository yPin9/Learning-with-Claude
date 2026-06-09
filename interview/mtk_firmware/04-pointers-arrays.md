# Ch 4 — 指標基礎與陣列

> **目標**：把指標運算、陣列與指標的關係、指標的指標講清楚，破解一堆「印出什麼」的考古題。指標是 C 面試的硬核，也是上機考最常出錯的地方。

> **環境**：C，`gcc -Wall`，假設 64-bit（指標 8 bytes）。

## 為什麼考這個

指標題是 C 面試的試金石——能不能正確算出 `*(p+1)`、分辨 `arr` 和 `&arr`、看懂指標運算，直接反映你對 C 記憶體模型的掌握。上機考的「印出什麼」題一半在考指標。

## 先建立直覺：指標就是「住址」

```
   變數 x = 42，住在記憶體位址 0x1000
   指標 p = &x  →  p 的「值」是 0x1000（x 的住址）
   *p           →  去 0x1000 把東西拿出來 = 42（解參照 dereference）

   記憶體：
   位址     內容
   0x1000   42        ← x
   0x2000   0x1000    ← p（存的是 x 的位址）
```

`&` = 取址（拿住址），`*` = 解參照（按住址去拿東西）。指標的型別決定「解參照時拿幾個 byte、運算時跳多遠」。

## 指標運算：跳的是「元素」不是「byte」

最關鍵、最常錯的一點：**指標 +1 跳的是「一個元素的大小」，不是 1 個 byte。**

```c
int *p = (int *)0x1000;
p + 1;     // = 0x1004（跳 sizeof(int) = 4 bytes），不是 0x1001！

char *c = (char *)0x1000;
c + 1;     // = 0x1001（跳 sizeof(char) = 1）

double *d = (double *)0x1000;
d + 1;     // = 0x1008（跳 8）
```

公式：`p + n` 的位址 = `p + n * sizeof(*p)`。所以指標的型別超重要——同樣 `+1`，不同型別跳不同距離。

## 陣列與指標：相關但不相同

陣列名在多數場合會「退化（decay）」成指向首元素的指標，但**陣列 ≠ 指標**。

```c
int arr[5] = {10, 20, 30, 40, 50};

arr        // 退化成 &arr[0]，型別 int*（指向首元素）
arr[2]     // = *(arr + 2) = 30
*(arr+2)   // 同上 = 30
2[arr]     // 也是 30！因為 arr[2] == *(arr+2) == *(2+arr) == 2[arr]（交換律）
```

`arr[i]` 只是 `*(arr+i)` 的語法糖——這也是為什麼 `2[arr]` 合法（雖然詭異，面試愛考）。

陣列 vs 指標的關鍵差異：

| | 陣列 `int arr[5]` | 指標 `int *p` |
|---|---|---|
| `sizeof` | 20（5×4，整個陣列）| 8（指標本身大小）|
| `&arr` 型別 | `int (*)[5]`（指向整個陣列）| `int **` |
| 可否重新賦值 | 不行（`arr = ...` 錯）| 可（`p = ...`）|
| 本質 | 一塊連續記憶體 | 一個存位址的變數 |

```c
int arr[5];
printf("%zu\n", sizeof(arr));   // 20（整個陣列）
int *p = arr;
printf("%zu\n", sizeof(p));     // 8（只是個指標）— 陣列退化後丟失了大小資訊
```

## arr vs &arr：值一樣，型別不同

```c
int arr[5];
arr        // int*，指向 arr[0]，值 = 首元素位址
&arr       // int(*)[5]，指向「整個陣列」，值 = 同一個位址（但型別不同！）

arr + 1    // 跳 1 個 int = +4 bytes
&arr + 1   // 跳 1 個「整個陣列」= +20 bytes！
```

**值相同（都是陣列起始位址），但型別不同，所以 +1 跳的距離不同。** 這是經典陷阱題。

## 考古題詳解

### Q1：印出什麼？

```c
#include <stdio.h>
int main(void) {
    int arr[5] = {1, 2, 3, 4, 5};
    int *p = arr;
    printf("%d %d %d\n", *p, *(p+2), *(arr+4));
    return 0;
}
```

<details>
<summary>詳解</summary>

```
1 3 5
```
- `*p` = arr[0] = 1
- `*(p+2)` = arr[2] = 3（跳 2 個 int）
- `*(arr+4)` = arr[4] = 5

**考點**：基本指標運算 + 陣列退化。
</details>

### Q2：以下兩個 sizeof 各是多少？（64-bit）

```c
int arr[10];
int *p = arr;
sizeof(arr);   // ?
sizeof(p);     // ?
```

<details>
<summary>詳解</summary>

`sizeof(arr)` = **40**（10 × 4，整個陣列的大小）。
`sizeof(p)` = **8**（指標本身，64-bit 系統）。

陷阱：陣列退化成指標後就丟失大小資訊。所以**不能用 sizeof 算「傳進函式的陣列」長度**——函式參數的 `int arr[]` 其實是 `int *`，sizeof 得到指標大小（8），不是陣列大小。這是 C 新手最痛的坑。

**考點**：陣列 vs 指標的 sizeof 差異 + 退化。
</details>

### Q3：印出什麼？（arr vs &arr）

```c
#include <stdio.h>
int main(void) {
    int arr[5] = {1,2,3,4,5};
    printf("%ld\n", (long)(arr+1) - (long)arr);     // 差幾 byte
    printf("%ld\n", (long)(&arr+1) - (long)&arr);   // 差幾 byte
    return 0;
}
```

<details>
<summary>詳解</summary>

```
4
20
```
- `arr+1` 跳 1 個 int = 4 bytes。
- `&arr+1` 跳 1 個「整個 int[5] 陣列」= 20 bytes。

`arr` 和 `&arr` 的值相同（陣列起始位址），但型別不同（`int*` vs `int(*)[5]`），所以 +1 跳的距離不同。

**考點**：arr vs &arr 的型別差異，最經典的指標陷阱。
</details>

### Q4：指標 +1 跳幾個 byte？

```c
char *cp;  int *ip;  double *dp;  int **pp;
```

<details>
<summary>詳解</summary>

- `cp + 1`：+1 byte（sizeof char = 1）
- `ip + 1`：+4 bytes（sizeof int = 4）
- `dp + 1`：+8 bytes（sizeof double = 8）
- `pp + 1`：+8 bytes（pp 是「指標的指標」，+1 跳一個指標 = 8 bytes on 64-bit）

公式：`p+1` 跳 `sizeof(*p)`。

**考點**：指標運算跳的是元素大小。
</details>

### Q5：印出什麼？（指標遞增與運算子優先序）

```c
#include <stdio.h>
int main(void) {
    int arr[] = {10, 20, 30};
    int *p = arr;
    printf("%d\n", *p++);    // ?
    printf("%d\n", *p);      // ?
    return 0;
}
```

<details>
<summary>詳解</summary>

```
10
20
```
`*p++`：`++` 優先於 `*`，但是**後置**遞增——先用 `p` 的當前值解參照（`*p` = arr[0] = 10），**再**把 `p` 遞增（指向 arr[1]）。所以第一行印 10，第二行 `*p` 印 20。

對比：
- `(*p)++`：解參照後對「值」遞增（arr[0] 變 11，回傳 10）
- `*++p`：先遞增 p（指向 arr[1]），再解參照（= 20）

**考點**：`*p++` vs `(*p)++` vs `*++p`，運算子優先序 + 前後置（Ch 10 深入）。
</details>

## 踩雷集錦

1. **指標 +1 以為跳 1 byte**：跳 `sizeof(*p)`。int* 跳 4、double* 跳 8。
2. **用 sizeof 算函式參數陣列長度**：函式裡 `int arr[]` 是指標，sizeof 得指標大小（8），不是陣列。要另外傳長度。
3. **混淆 arr 和 &arr**：值同、型別異，+1 跳不同距離。
4. **`*p++` 搞錯**：後置遞增先用後加，`*p++` 是「用 *p 再 p++」。
5. **NULL 指標解參照 / 野指標**：`int *p; *p = 5;`（未初始化）= 未定義行為，可能 crash。
6. **指標相減的單位**：兩個同型別指標相減得到「相差幾個元素」（不是 byte）。`&arr[3]-&arr[0]` = 3。

## 速記

- `&` 取址、`*` 解參照；指標型別決定「拿幾 byte、跳多遠」。
- 指標 `p+n` 跳 `n * sizeof(*p)`，不是 n byte。
- `arr[i]` == `*(arr+i)` == `i[arr]`。
- 陣列 ≠ 指標：sizeof(陣列)=整個大小、sizeof(指標)=8；陣列名退化成指標後丟失大小。
- `arr`（int*）vs `&arr`（int(*)[5]）：值同型別異，+1 跳 4 vs 20。
- `*p++`（用 *p 再 p++）≠ `(*p)++`（值++）≠ `*++p`（先 p++ 再解）。

## 自我檢核

- [ ] `int *p; p+1` 跳幾個 byte？`char *c; c+1` 呢？為什麼？
- [ ] `sizeof(arr)` 和 `sizeof(p)`（p=arr）為什麼不同？傳進函式後呢？
- [ ] `arr` 和 `&arr` 差在哪？`arr+1` 和 `&arr+1` 跳的距離一樣嗎？
- [ ] `*p++`、`(*p)++`、`*++p` 各做什麼？
- [ ] 為什麼不能用 sizeof 算函式收到的陣列長度？

## 延伸閱讀

### 書籍

- **《C Programming Language (K&R)》** — Ch 5 Pointers and Arrays
  - **讀哪幾章**：5.1–5.5（指標與陣列、指標運算）。
  - **和本章的關聯**：指標的權威教材，本章內容的完整版。

- **《Expert C Programming》** — "Arrays and Pointers are NOT the same" 章
  - **為什麼值得讀**：把「陣列 vs 指標」的差異講到最透，連退化、宣告 vs 定義一起。

### 文章

- **[C/C++ 常見試題 — Pointer (Medium, Yu-Pu Wu)](https://medium.com/@earth875/c-c-%E5%B8%B8%E8%A6%8B%E8%A9%A6%E9%A1%8C-961619b14f88)**
  - **讀哪裡**：指標運算與陣列題。
  - **和本章的關聯**：MTK 風格指標考題的補充題庫。

指標基礎有了，下一章是它的進階魔王——複雜宣告與函式指標，右左法則破解「八種宣告」。

→ [Ch 5 複雜宣告與函式指標](./05-complex-declarations-function-pointers.md)
