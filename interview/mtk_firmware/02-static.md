# Ch 2 — static 全解

> **目標**：把 `static` 的兩種用途、三個場景一次講透，做到面試現場秒答。這是 C 面試最高頻的關鍵字之一，幾乎必考。

> **環境**：C（C99/C11），`gcc -Wall`。前置：[Ch 1 儲存類別與作用域](./01-storage-classes-scope.md)。

## 為什麼考這個

`static` 是 MTK（和幾乎所有 C 面試）最愛問的關鍵字——因為它一個字、兩種完全不同的效果，能測出你是不是真懂「儲存期」和「連結」（Ch 1）。被問「static 有什麼用」答不完整，立刻露餡。

## 先建立直覺：static = 「靜態」的兩個意思

`static` 的中文「靜態」其實對應兩件事，看它修飾誰：

```
   static 修飾「區域變數」  →  延長壽命：靜態儲存期（活到程式結束、保值）
   static 修飾「全域變數/函式」→ 限制可見：internal linkage（只給本檔用）

   表面兩種效果，底層是同一件事：
   「靜態儲存期 + 限制連結」——只是修飾不同對象時，哪個效果顯著不同。
```

口訣：**對區域變數，static 管「活多久」；對全域/函式，static 管「誰看得到」。**

## 用途一：函式內 static 區域變數（保值）

```c
#include <stdio.h>
int next_id(void) {
    static int id = 0;     // 只初始化一次，跨呼叫保值
    return ++id;
}
int main(void) {
    printf("%d %d %d\n", next_id(), next_id(), next_id());
    // 注意：printf 參數求值順序未定義（Ch 10），這裡只為示意
    return 0;
}
```

關鍵性質：

- **初始化一次**：`static int id = 0;` 只在程式啟動時做一次，不是每次進函式都重設。
- **跨呼叫保值**：`id` 的值在函式返回後仍保留（它在 data 段，不在 stack）。
- **作用域仍限函式內**：只有 `next_id` 看得到 `id`，但它活著。

用途：函式內需要記住狀態（計數器、快取、初始化旗標）、又不想用全域變數污染命名空間。

## 用途二：static 全域變數 / 函式（藏起來）

```c
/* mymodule.c */
static int internal_state = 0;       // 只有本檔看得到
static void helper(void) { ... }     // 只有本檔能呼叫

int public_api(void) { helper(); return internal_state; }   // 對外的介面
```

關鍵性質：

- **internal linkage**：`internal_state` 和 `helper` 只在 `mymodule.c` 可見，別的檔案 `extern` 不到、也不能呼叫。
- **封裝**：這是 C 做「模組私有」的方式——把不想曝露的實作細節 `static` 起來，只留 `public_api` 對外。類似 OOP 的 private。

用途：模組封裝、避免命名衝突（兩個檔案都有 `helper` 不會撞）、減少全域命名空間污染。

## 三個場景速記

面試被問「static 有什麼用」，講這三點就完整：

```
   1. 函式內 static 區域變數：保值、初始化一次（計數器/狀態）
   2. static 全域變數：限本檔可見（模組私有資料）
   3. static 函式：限本檔可呼叫（模組私有函式）
```

2 和 3 本質相同（都是 internal linkage），1 是另一件事（static lifetime）。

## 考古題詳解

### Q1：static int 的初始值？沒寫初值會怎樣？

```c
static int a;        // 沒給初值
static int b = 0;
```

<details>
<summary>詳解</summary>

兩者都是 **0**。靜態儲存期變數（static / 全域）若未明確初始化，C 標準保證初始化為 0（指標為 NULL、浮點為 0.0）。`a` 和 `b` 效果相同，都在 bss 段（載入時清零）。

對比：區域 `int a;`（非 static）是垃圾值（Ch 1 Q2）。

**考點**：static 預設歸零。
</details>

### Q2：static 區域變數的初始化能用變數嗎？

```c
void f(int n) {
    static int x = n;    // 合法嗎？
}
```

<details>
<summary>詳解</summary>

**不合法（在 C 裡編譯錯誤）。** static 變數的初值必須是**編譯期常數**（constant expression），不能用執行期才知道的變數 `n`。因為 static 變數在程式載入時就要決定初值（放進 data 段），那時 `n` 還不存在。

（註：C++ 允許 static 區域變數用執行期值初始化，且保證執行緒安全的「首次進入才初始化」。但**C 不行**。面試考 C 要答不合法。）

**考點**：C 的 static 初值必須是編譯期常數。
</details>

### Q3：兩個檔案都有 `static void helper()`，會衝突嗎？

```c
/* a.c */  static void helper(void) { /* A 版 */ }
/* b.c */  static void helper(void) { /* B 版 */ }
```

<details>
<summary>詳解</summary>

**不衝突，編譯連結都成功。** 因為兩個 `helper` 都是 `static`（internal linkage），各自只在自己的檔案可見，linker 不會把它們當同一個符號——所以不會「重複定義」。

對比：如果**沒有** `static`（external linkage），兩個同名全域函式會 linker error（multiple definition）。

**考點**：static 函式避免跨檔命名衝突。
</details>

### Q4：static 變數放在記憶體哪一段？

<details>
<summary>詳解</summary>

放在 **data 段（初值非 0）或 bss 段（初值 0 或未初始化）**，不是 stack、不是 heap。這就是它「跨呼叫保值」的原因——它不隨函式呼叫在 stack 上生滅。

對比區域變數（auto）在 stack、malloc 在 heap。Ch 11 會把記憶體佈局整個講一遍。

**考點**：static 變數的儲存位置（data/bss），連結到「為什麼能保值」。
</details>

### Q5：const static / static const 有差嗎？

<details>
<summary>詳解</summary>

**沒差**，順序不影響語意。兩者都是「靜態儲存期 + 唯讀」。`const` 管「不能改」、`static` 管「儲存期/連結」，兩個正交的屬性，順序隨意。

`static const int x = 5;` = `const static int x = 5;` = 一個本檔可見、唯讀、靜態儲存期的整數。

**考點**：const 與 static 是正交屬性。
</details>

## 踩雷集錦

1. **答「static 就是讓變數變全域」**：不完整且誤導。static 區域變數是「保值但作用域仍限函式」；static 全域反而是「**限制**可見性」（不是變全域，是藏起來）。
2. **以為 static 區域變數每次進函式重新初始化**：只初始化一次（程式啟動）。
3. **C 裡用變數初始化 static**：不合法（必須編譯期常數）。別把 C++ 行為帶進來。
4. **以為 static 全域「變成全域」**：相反，它變成「只有本檔的全域」（internal linkage），別檔看不到。
5. **忘了 static 函式的用途**：不只變數，static 修飾函式 = 限本檔呼叫（模組私有）。面試要連函式一起講。

## 速記

- **static 區域變數**：靜態儲存期（保值、初始化一次、放 data/bss）、作用域仍限函式內。
- **static 全域變數/函式**：internal linkage（只給本檔，避免命名衝突、做模組封裝）。
- 預設初值 0；初值須編譯期常數（C）；不能取址限制無（static 有位址，跟 register 不同）。
- 「static 有什麼用」三點：函式內保值、全域變數本檔私有、函式本檔私有。

## 自我檢核

- [ ] 面試官問「static 關鍵字有哪些用途」，你能完整講出三個場景嗎？
- [ ] static 區域變數初始化幾次？放記憶體哪一段？為什麼能保值？
- [ ] 為什麼說 static 全域是「限制」可見性而非「變成」全域？
- [ ] C 裡 `static int x = n;`（n 是變數）合法嗎？為什麼？

## 延伸閱讀

### 書籍

- **《C Programming Language (K&R)》** — 4.6 Static Variables
  - **讀哪裡**：4.6 即可，短而精。
  - **和本章的關聯**：static 的權威說明。

- **《Expert C Programming》** — 談 linkage 與 segment 的章節
  - **為什麼值得讀**：把 static 的 internal linkage 與記憶體段一起講透。

### 文章

- **[韌體工程師的0x10個問題 — HackMD](https://hackmd.io/@Chienyu/S1loEqCuo)**
  - **讀哪裡**：Q6（static 關鍵字作用）。
  - **和本章的關聯**：經典嵌入式面試對 static 的標準考法（本章 Q1-Q5 的源頭之一）。

static 搞定，下一章是另一對高頻關鍵字——const 與 volatile，尤其 volatile 是韌體面試的招牌題。

→ [Ch 3 const 與 volatile](./03-const-volatile.md)
