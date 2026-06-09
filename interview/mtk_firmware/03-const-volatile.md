# Ch 3 — const 與 volatile

> **目標**：把 `const` 的五層宣告和 `volatile` 的三大場景講到能秒答。**volatile 是韌體面試的招牌題**（硬體暫存器、ISR、多執行緒），幾乎必考，還會延伸到 `const volatile` 和 `square()` double-read 陷阱。

> **環境**：C，`gcc -Wall`。前置：Ch 1-2。

## 為什麼考這個

`const` 考你「指標和它指向的東西，誰不能改」——一個 `const int *p` 到底哪裡 const，能瞬間分辨高下。`volatile` 更是韌體職位的核心——它直接關係到「讀硬體暫存器、ISR 共享變數」這些韌體天天做的事。被問 volatile 答不清楚，等於告訴面試官你沒寫過真正的韌體。

## const：五層遞進

`const` 的難點全在「const 修飾的是指標本身、還是指標指向的東西」。用**右左法則**（從變數名往右讀、再往左）拆解：

```c
int x = 5, y = 10;

int *p;                  // p：可改指向、可改 *p（普通指標）
const int *p;            // p 指向 const int：*p 不能改，p 可改指向
int const *p;            // 同上！const 在 int 左右都一樣（修飾指向的 int）
int *const p = &x;       // const 指標：p 不能改指向，*p 可改
const int *const p = &x; // 都不能改：*p 不能改、p 不能改指向
```

判讀技巧：**看 `const` 緊鄰 `*` 的哪一邊**：

```
   const 在 * 左邊（const int *p / int const *p）→ 指向的「值」是 const（不能改 *p）
   const 在 * 右邊（int *const p）              → 「指標」是 const（不能改 p 指向哪）
```

五層由淺到深（面試常要你逐一說明）：

```c
const int a = 5;          // 1. a 是唯讀整數
const int *p;             // 2. p 指向唯讀整數（*p 不可改）
int *const p = &a;        // 3. p 是唯讀指標（p 不可改指向）
const int *const p = &a;  // 4. p 唯讀指標、指向唯讀整數（都不可改）
const int **pp;           // 5. pp 指向「指向唯讀 int 的指標」
```

驗證（故意違規看編譯器罵）：

```c
const int *p = &x;
*p = 99;        // 錯：assignment of read-only location *p
p = &y;         // OK：p 本身可改

int *const q = &x;
q = &y;         // 錯：assignment of read-only variable q
*q = 99;        // OK：*q 可改
```

## volatile：告訴編譯器「別自作聰明」

`volatile` 的本質：**告訴編譯器「這個變數的值可能在程式控制之外被改變，每次都要從記憶體重新讀，不准用暫存器裡的舊副本、不准最佳化掉讀寫」。**

先建立直覺——沒有 volatile 的災難：

```c
   unsigned char *status = (unsigned char *)0x1234;   // 硬體狀態暫存器
   while (*status == 0) {  }   // 等硬體把 status 設成非 0

   編譯器最佳化的想法：「*status 在迴圈裡沒被改過，我讀一次放暫存器就好，
   不用每次都讀記憶體」→ 變成 if (*status == 0) while(1);  → 永遠卡死！

   因為編譯器不知道「硬體會在背後改 *status」。
   volatile 就是告訴它：「每次都給我從記憶體重讀」。
```

### volatile 的三大場景（必背）

面試問「volatile 用在哪」，講這三個：

```
   1. 記憶體映射的硬體暫存器（memory-mapped I/O）
      硬體會在背後改它的值，要每次重讀（Ch 13）

   2. ISR（中斷服務常式）與主程式共享的全域變數
      ISR 在背後改它，主程式的迴圈要每次重讀（Ch 14）

   3. 多執行緒共享的變數（在沒有更強同步機制時）
      別的執行緒會改它（但注意：volatile ≠ 執行緒安全，見踩雷）
```

共同點：**值會在「當前這段程式碼的控制流之外」被改變**——硬體、中斷、別的執行緒。編譯器看不到這些，所以要 volatile 提醒它。

範例（ISR 共享變數）：

```c
volatile int flag = 0;        // ISR 會設它

void isr(void) { flag = 1; }  // 中斷發生時被呼叫

int main(void) {
    while (!flag) { /* 等中斷 */ }   // 有 volatile：每次重讀 flag → 中斷後跳出
    // 若 flag 沒 volatile：編譯器可能把它快取在暫存器 → 永遠卡死
    return 0;
}
```

## const volatile：兩者並存（最常被質疑的組合）

很多人以為 `const volatile` 矛盾——「const 不能改、volatile 會變，怎麼共存？」**完全不矛盾**：

```c
const volatile unsigned int *reg = (unsigned int *)0x4000;
```

意思是：
- **const**：「**我（程式）**不能改它」（不能 `*reg = x`，這是唯讀暫存器）。
- **volatile**：「但它的值會被**別人（硬體）**改，所以每次都要重讀」。

典型場景：**唯讀的硬體狀態暫存器**——你只能讀（const）、但它的值硬體會更新（volatile）。例如一個只能讀的計時器計數值、感測器讀數暫存器。

口訣：**const = 我不改它；volatile = 它會被別人改。兩者管的「改」是不同主體，所以能並存。**

## square() double-read 陷阱（經典韌體題）

這是 volatile 最有名的考古題：

```c
int square(volatile int *ptr) {
    return *ptr * *ptr;     // 想算 (*ptr)²
}
```

**問題**：這函式**不一定**回傳 `(*ptr)²`。因為 `ptr` 是 volatile，`*ptr * *ptr` 會**讀兩次** `*ptr`（編譯器不准把兩次讀合併成一次——volatile 的語意）。如果這兩次讀之間，硬體/ISR 改了 `*ptr`，兩次讀到不同值，結果就不是平方。

正確寫法（讀一次存下來）：

```c
int square(volatile int *ptr) {
    int a = *ptr;     // 只讀一次
    return a * a;     // 用本地副本平方
}
```

**考點**：volatile 變數「每次存取都是真的存取」，所以一個表達式裡多次用它 = 多次讀 = 可能讀到不同值。要保證用同一個值，先讀進本地變數。

## 考古題詳解

### Q1：以下哪些寫法 `*p` 不能改？哪些 `p` 不能改指向？

```c
const int *p1;       int *const p2 = &x;
int const *p3;       const int *const p4 = &x;
```

<details>
<summary>詳解</summary>

| | `*p` 可改？ | `p` 指向可改？ |
|---|---|---|
| `const int *p1` | ✗ | ✓ |
| `int *const p2` | ✓ | ✗ |
| `int const *p3` | ✗（同 p1）| ✓ |
| `const int *const p4` | ✗ | ✗ |

關鍵：`p1` 和 `p3` 一樣（`const int` = `int const`，都修飾指向的值）。`p2` 的 const 在 `*` 右邊（修飾指標本身）。
</details>

### Q2：為什麼 ISR 改的全域變數，主程式要宣告 volatile？

<details>
<summary>詳解</summary>

因為主程式的迴圈（如 `while(!flag)`）裡，編譯器看不到「ISR 會在背後改 flag」——它可能把 flag 讀進暫存器一次後就不再讀記憶體（最佳化），導致 ISR 改了 flag、主程式卻看不到、永遠卡死。

`volatile` 強制每次都從記憶體重讀 flag，確保看得到 ISR 的修改。

**考點**：volatile 三大場景之一（ISR 共享變數），最高頻。
</details>

### Q3：`const volatile` 有意義嗎？舉例。

<details>
<summary>詳解</summary>

**有意義。** 例：唯讀的硬體狀態暫存器——程式只能讀不能寫（const），但硬體會更新它的值（volatile，要每次重讀）。

```c
const volatile uint32_t *timer = (uint32_t *)0x40000000;  // 唯讀計時器
uint32_t now = *timer;   // OK，讀（每次重讀，因 volatile）
*timer = 0;              // 錯，不能寫（const）
```

**考點**：const（我不改）與 volatile（別人會改）是不同主體的「改」，可並存。
</details>

### Q4：volatile 能保證執行緒安全嗎？

<details>
<summary>詳解</summary>

**不能。** volatile 只保證「每次存取都真的讀寫記憶體、不被最佳化掉、不重排（對 volatile 之間）」——它**不提供原子性、不提供記憶體屏障（對非 volatile 的重排）**。

多執行緒下 `volatile int x; x++;` 仍有 race condition（`x++` 是 read-modify-write 三步，非原子）。真正的執行緒安全要用 mutex / atomic（Ch 22、Ch 35）。

**考點**：volatile ≠ thread-safe。這是進階陷阱，答對加分。
</details>

## 踩雷集錦

1. **const int *p 以為 p 不能改**：錯，是 `*p` 不能改、`p` 可改指向。const 在 `*` 左邊修飾「值」。
2. **以為 const volatile 矛盾**：不矛盾，「我不改」（const）vs「別人會改」（volatile）。
3. **square 直接 `*ptr * *ptr`**：volatile 讀兩次可能不同值。要先讀進本地變數。
4. **以為 volatile = 執行緒安全**：不是，volatile 不給原子性/屏障。多執行緒要 mutex/atomic。
5. **韌體裡忘了給硬體暫存器/ISR 變數加 volatile**：編譯器最佳化掉重讀 → 卡死或讀到舊值。這是真實韌體 bug，也是面試陷阱。
6. **以為 volatile 會讓變數變慢/變快**：它只是禁止特定最佳化（暫存器快取、合併讀寫），不是效能開關。

## 速記

- **const**：`*` 左邊 const → 值唯讀（`*p` 不可改）；`*` 右邊 const → 指標唯讀（`p` 不可改指向）。
- **volatile**：每次存取都真的讀寫記憶體（禁最佳化掉/合併）。三場景：硬體暫存器、ISR 共享變數、多執行緒共享。
- **const volatile**：我不改（const）+ 別人會改（volatile），唯讀硬體暫存器的典型。
- **square 陷阱**：volatile 多次用 = 多次讀，先存本地變數。
- **volatile ≠ thread-safe**：不給原子性，多執行緒要 mutex/atomic。

## 自我檢核

- [ ] 不查表，能分辨 `const int *p` / `int *const p` / `const int *const p` 各自誰不能改嗎？
- [ ] 面試官問「volatile 用在哪三種場景」，你能秒答嗎？
- [ ] 為什麼 ISR 改的全域變數主程式要 volatile？不加會怎樣？
- [ ] `const volatile` 矛盾嗎？舉一個真實例子。
- [ ] volatile 保證執行緒安全嗎？為什麼？
- [ ] `int square(volatile int *p){return *p * *p;}` 有什麼問題？

## 延伸閱讀

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q7（const 五層）、Q8（volatile 含義 + square 陷阱 + const volatile）。
  - **和本章的關聯**：本章 const/volatile 考點的經典源頭（Nigel Jones 原題）。

- **[Introduction to the volatile keyword](https://barrgroup.com/embedded-systems/how-to/c-volatile-keyword)** — Barr Group
  - **這篇說什麼**：volatile 在嵌入式的三大場景與 square 陷阱，作者是嵌入式 C 權威。
  - **讀哪裡**：整篇；本章 volatile 的英文權威版。

### 書籍

- **《C Programming Language (K&R)》** — const/volatile type qualifiers（附錄 A）
  - **讀哪裡**：type qualifier 那節。
  - **和本章的關聯**：const/volatile 的語言定義。

const/volatile 是觀念題核心，下一章回到 C 的硬核——指標與陣列，一堆「印出什麼」考題的根。

→ [Ch 4 指標基礎與陣列](./04-pointers-arrays.md)
