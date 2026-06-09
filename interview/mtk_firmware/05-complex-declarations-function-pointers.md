# Ch 5 — 複雜宣告與函式指標

> **目標**：用「右左法則」破解 C 的複雜宣告（指標陣列 vs 陣列指標、函式指標、函式指標陣列），並掌握函式指標的實用寫法。這是 MTK「八種宣告」考古題的標準考點。

> **環境**：C，`gcc -Wall`。前置：Ch 4（指標）。

## 為什麼考這個

C 的宣告語法是出了名的繞——`int *a[10]` 和 `int (*a)[10]` 差一個括號、意思天差地遠。能不能正確讀出 `void (*signal(int, void(*)(int)))(int)` 這種東西，分得出高下。MTK 直接考「寫出八種宣告」「這個宣告是什麼」。學會右左法則，這類題全破。

## 先建立直覺：右左法則（Right-Left Rule）

讀 C 宣告的口訣：**從變數名開始，先往右讀、碰到 `)` 或讀完就往左、遇到 `(` 就反向**。把 `*` 讀成「指向」、`[]` 讀成「陣列」、`()` 讀成「函式」。

```
   步驟：
   1. 找到變數名
   2. 往右看：[] = 陣列、() = 函式
   3. 往左看：* = 指標
   4. 遇到括號 () 改變優先順序，先處理括號內
```

關鍵：**`[]` 和 `()` 優先於 `*`**（綁變數名比較緊），所以要用括號才能讓 `*` 先綁。

## 核心對比：差一個括號

```c
int *a[10];      // a 是「陣列」，裝 10 個 int*  →  指標陣列（array of pointers）
int (*a)[10];    // a 是「指標」，指向 int[10]   →  陣列指標（pointer to array）
```

用右左法則拆 `int *a[10]`：
1. `a` 是變數名
2. 往右：`[10]` → a 是「10 個元素的陣列」
3. 往左：`*` → 元素是「指標」
4. 最左：`int` → 指向 int
→ **a 是「裝 10 個 int 指標的陣列」**（指標陣列）

拆 `int (*a)[10]`：
1. `a` 是變數名
2. 括號 `(*a)` 先綁：往左 `*` → a 是「指標」
3. 出括號往右：`[10]` → 指向「10 個元素的陣列」
4. `int` → int 陣列
→ **a 是「指向 int[10] 的指標」**（陣列指標）

差別在記憶體：

```
   int *a[10]（指標陣列）：          int (*a)[10]（陣列指標）：
   ┌──┬──┬──┬─...─┬──┐              a → ┌──┬──┬──┬─...─┬──┐
   │p0│p1│p2│     │p9│                  │ 一整塊 10 個 int │
   └──┴──┴──┴─────┴──┘                  └──┴──┴──┴────────┘
   10 個指標，各指別處               1 個指標，指向一塊 10 int
   sizeof = 10*8 = 80               sizeof = 8（只是個指標）
```

## 函式指標

函式名本身就是「指向函式的指標」（類似陣列名退化）。函式指標讓你「把函式當參數傳、存進陣列、動態選擇要呼叫誰」。

```c
int add(int a, int b) { return a + b; }

int (*fp)(int, int);     // fp 是「指向『收兩個 int、回傳 int 的函式』的指標」
fp = add;                // 或 fp = &add（兩者等價）
int r = fp(3, 4);        // = 7，呼叫（或寫 (*fp)(3,4)）
```

右左法則拆 `int (*fp)(int, int)`：
1. `fp` 變數名
2. 括號 `(*fp)`：`*` → fp 是指標
3. 出括號往右：`(int, int)` → 指向「函式」
4. `int` → 函式回傳 int
→ **fp 是指向「收 (int,int) 回傳 int 的函式」的指標**

**為什麼要括號**：`int *fp(int, int)` 沒括號 = `fp` 是「回傳 int* 的函式」（`()` 優先於 `*`），完全不同！括號讓 `*` 先綁 fp。

## 函式指標陣列（分支優化的考古題）

把多個函式指標放陣列，用索引取代 if/switch——MTK 考古題的「用函式指標陣列取代 if-switch」：

```c
int add(int a,int b){return a+b;}
int sub(int a,int b){return a-b;}
int mul(int a,int b){return a*b;}

int (*ops[3])(int, int) = {add, sub, mul};   // 函式指標陣列

int r = ops[0](3, 4);   // 呼叫 add(3,4) = 7
int s = ops[2](3, 4);   // 呼叫 mul(3,4) = 12
```

拆 `int (*ops[3])(int,int)`：
1. `ops` 變數名
2. 括號內：往右 `[3]` → ops 是 3 元素陣列；往左 `*` → 元素是指標
3. 出括號往右：`(int,int)` → 指向函式
4. `int` → 回傳 int
→ **ops 是「裝 3 個『指向收(int,int)回傳int的函式』指標的陣列」**

用途：state machine、dispatch table、callback——韌體常用（中斷向量表本質就是函式指標陣列！Ch 14）。

## typedef 拯救複雜宣告

複雜宣告難讀難寫，`typedef` 給它取個名字：

```c
typedef int (*Operation)(int, int);   // Operation = 「收(int,int)回傳int的函式指標」型別

Operation ops[3] = {add, sub, mul};   // 清楚多了！
Operation fp = add;
```

面試寫複雜的東西（如函式指標陣列、回傳函式指標的函式），先 typedef 會清楚很多，也展現你懂。

## 考古題詳解

### Q1：說出以下八種宣告各是什麼

```c
int a;            int *b;           int c[10];        int *d[10];
int (*e)[10];     int (*f)(int);    int (*g[10])(int); int *(*h)(int);
```

<details>
<summary>詳解</summary>

| 宣告 | 是什麼 |
|---|---|
| `int a` | 整數 |
| `int *b` | 指向 int 的指標 |
| `int c[10]` | 10 個 int 的陣列 |
| `int *d[10]` | **指標陣列**：10 個 int* 的陣列 |
| `int (*e)[10]` | **陣列指標**：指向 int[10] 的指標 |
| `int (*f)(int)` | **函式指標**：指向「收 int 回傳 int 的函式」 |
| `int (*g[10])(int)` | **函式指標陣列**：10 個「函式指標」的陣列 |
| `int *(*h)(int)` | 指向「收 int 回傳 **int***  的函式」的指標 |

關鍵分辨：`*` 在括號裡（綁變數名）= 指標優先；`[]`/`()` 在外 = 陣列/函式。

**考點**：MTK 經典「八種宣告」題（來源見延伸閱讀）。
</details>

### Q2：`int *a[10]` 和 `int (*a)[10]` 差在哪？

<details>
<summary>詳解</summary>

- `int *a[10]`：**指標陣列**——10 個獨立的 int 指標，`sizeof` = 80（10×8）。
- `int (*a)[10]`：**陣列指標**——1 個指標，指向一塊 int[10]，`sizeof` = 8。

差一個括號，意思完全不同。`[]` 優先於 `*`，所以 `int *a[10]` 先讀成「a 是陣列」；加括號 `(*a)` 讓 `*` 先綁。

**考點**：差一括號的經典對比，必考。
</details>

### Q3：`fp = add` 和 `fp = &add` 一樣嗎？呼叫 `fp(3,4)` 和 `(*fp)(3,4)` 呢？

<details>
<summary>詳解</summary>

**都一樣。** 函式名 `add` 會自動轉成函式指標（類似陣列退化），所以 `fp = add` 和 `fp = &add` 等價。呼叫時 `fp(3,4)` 和 `(*fp)(3,4)` 也等價（C 允許兩種寫法）。這是 C 的語法糖。

**考點**：函式名/函式指標的等價寫法。
</details>

### Q4：用函式指標陣列把這段 switch 改寫

```c
int compute(int op, int a, int b) {
    switch(op) {
        case 0: return a+b;
        case 1: return a-b;
        case 2: return a*b;
    }
    return 0;
}
```

<details>
<summary>詳解</summary>

```c
int add(int a,int b){return a+b;}
int sub(int a,int b){return a-b;}
int mul(int a,int b){return a*b;}

int compute(int op, int a, int b) {
    int (*ops[])(int,int) = {add, sub, mul};
    if (op < 0 || op >= 3) return 0;     // 邊界檢查不可少！
    return ops[op](a, b);
}
```

好處：O(1) dispatch（不用逐個 case 比）、易擴充（加函式就加陣列元素）。**邊界檢查必加**——op 越界會存取陣列外（未定義/crash）。

**考點**：函式指標陣列取代分支，MTK 考古題。注意邊界檢查（面試官會追問）。
</details>

## 踩雷集錦

1. **`int *a[10]` 當成陣列指標**：它是指標陣列（10 個指標）。陣列指標要括號 `int (*a)[10]`。
2. **函式指標忘了括號**：`int *fp(int)` 是「回傳 int* 的函式」，不是函式指標。函式指標要 `int (*fp)(int)`。
3. **右左法則沒先處理括號**：括號改變優先序，要先拆括號內。
4. **函式指標陣列沒邊界檢查**：索引越界 = 存取陣列外 = 災難。dispatch 前必檢查。
5. **不用 typedef 硬寫複雜宣告**：易錯難讀。複雜的先 typedef。

## 速記

- **右左法則**：從變數名，右看 `[]`(陣列)/`()`(函式)、左看 `*`(指標)，括號先處理。
- `int *a[10]` = 指標陣列（10 個指標）；`int (*a)[10]` = 陣列指標（1 個指標指向 int[10]）。差一括號。
- 函式指標 `int (*fp)(int,int)`；`fp=add` == `fp=&add`；`fp(x)` == `(*fp)(x)`。
- 函式指標陣列 `int (*ops[3])(int,int)` → dispatch table（取代 switch，韌體中斷向量表的本質）。
- 複雜宣告用 `typedef` 取名。

## 自我檢核

- [ ] 不查，能用右左法則讀出 `int (*g[10])(int)` 是什麼嗎？
- [ ] `int *a[10]` 和 `int (*a)[10]` 差在哪？sizeof 各多少？
- [ ] 函式指標為什麼一定要括號 `(*fp)`？沒括號變成什麼？
- [ ] 怎麼用函式指標陣列取代 switch？要注意什麼（邊界）？
- [ ] `fp = add` 和 `fp = &add` 一樣嗎？

## 延伸閱讀

### 書籍

- **《Expert C Programming》** — "Unscrambling Declarations in C"（右左法則章）
  - **讀哪幾章**：講 C 宣告解析的那章。
  - **和本章的關聯**：右左法則的權威來源，把 C 宣告語法的歷史與規則講透。

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q5（複雜指標宣告 / 八種宣告）。
  - **和本章的關聯**：本章八種宣告題的源頭。

- **[cdecl.org](https://cdecl.org/)**（工具）
  - **這是什麼**：輸入 C 宣告自動翻成白話（與反向）。
  - **怎麼用**：練習時拿來驗證你右左法則讀對了沒。

複雜宣告破解後，下一章是上機考最愛的手寫題來源——前置處理器與巨集，MIN/SQR side effect 是經典陷阱。

→ [Ch 6 前置處理器與巨集](./06-preprocessor-macros.md)
