# Ch 11 — 記憶體模型 stack/heap/static/text

> **目標**：把一個 C 程式的記憶體佈局（text/data/bss/heap/stack）一次講清楚，搞懂 malloc/free、記憶體洩漏、dangling pointer、double free、stack vs heap 的取捨。這串起前面 static（Ch 2）、指標（Ch 4），也是 OS 記憶體（Ch 25）的前置。

> **環境**：C，Linux/類 Unix 行程記憶體佈局。前置：Ch 1-2、Ch 4。

## 為什麼考這個

「這個變數放在記憶體哪一段」「malloc 的記憶體在哪」「stack 和 heap 差在哪」是 C/OS 面試的共同題。韌體記憶體有限，更要清楚每塊記憶體的用途與成本。搞懂佈局，static 保值（Ch 2）、區域變數垃圾值（Ch 1）、指標生命週期都串起來。

## 先建立直覺：一個程式的記憶體分五段

```
   高位址
   ┌─────────────────┐
   │      stack      │  區域變數、函式參數、返回位址 → 向下成長 ↓
   │        ↓        │
   │                 │
   │        ↑        │
   │      heap       │  malloc 配的記憶體 → 向上成長 ↑
   ├─────────────────┤
   │   bss (未初始化) │  未初始化/初值0的全域、static → 載入時清零
   ├─────────────────┤
   │   data (已初始化)│  初值非0的全域、static → 值存在執行檔
   ├─────────────────┤
   │   text (程式碼)  │  機器碼、字串字面值 → 通常唯讀
   └─────────────────┘
   低位址
```

五段各管什麼、誰住哪——這張圖能回答一大半記憶體題。

## 五段詳解

| 段 | 放什麼 | 生命週期 | 特性 |
|---|---|---|---|
| **text（程式碼）** | 機器碼、字串字面值（`"hello"`） | 整個程式 | 唯讀（防改 code） |
| **data** | 初值**非 0** 的全域/static 變數 | 整個程式 | 值存在執行檔裡 |
| **bss** | 初值 **0** 或未初始化的全域/static | 整個程式 | 執行檔不存內容、載入時清零（省檔案大小） |
| **heap** | `malloc`/`calloc` 配的記憶體 | 手動（malloc~free） | 你自己管，忘了 free 就 leak |
| **stack** | 區域變數、參數、返回位址 | 進出函式自動 | 自動配置/釋放，有大小上限 |

對照前面章節：

- **static/全域變數**（Ch 1-2）→ data（初值非0）或 bss（初值0）→ 所以保值、預設歸零。
- **區域變數（auto）**（Ch 1）→ stack → 函式返回就消失、預設垃圾值。
- **malloc**（下面）→ heap → 要手動 free。
- **字串字面值** `"hello"` → text（唯讀）→ 改它是 UB（下面 Q）。

## stack vs heap：核心對比

| | stack | heap |
|---|---|---|
| 配置/釋放 | 自動（進出函式） | 手動（malloc/free） |
| 速度 | 快（移動 stack pointer） | 慢（要找空閒塊、管理） |
| 大小 | 有限（通常幾 MB，會 overflow） | 大（受系統記憶體限制） |
| 生命週期 | 函式範圍 | 你決定（malloc~free） |
| 碎片 | 無 | 會碎片化（Ch 25） |
| 管理風險 | stack overflow（遞迴太深） | leak / dangling / double free |

判斷用哪個：**小、生命週期在函式內 → stack；大、或要跨函式存活、或大小執行期才知道 → heap。**

## malloc / free 與三大記憶體錯誤

```c
int *p = malloc(10 * sizeof(int));   // 配 heap 記憶體
if (p == NULL) { /* 配置失敗要檢查！ */ }
// ...用 p...
free(p);                              // 釋放
p = NULL;                             // 好習慣：free 後設 NULL（防 dangling）
```

三大經典錯誤（面試必考）：

### 1. memory leak（記憶體洩漏）

```c
void f(void) {
    int *p = malloc(100);
    return;                  // 忘了 free(p)！p 是區域變數，函式返回後沒人記得這塊 heap
}                            // → 那塊 heap 永遠回收不了 = leak
```

malloc 了沒 free，那塊 heap 沒人能再存取也不會還給系統。長時間執行（韌體常 7×24）leak 會累積到記憶體耗盡。**每個 malloc 都要有對應的 free。**

### 2. dangling pointer（懸空指標）

```c
int *p = malloc(100);
free(p);                     // 釋放了
*p = 5;                      // dangling！p 還指著已釋放的記憶體 → UB
```

free 後 `p` 仍指著那塊（已還給系統的）記憶體，再用它 = 存取無效記憶體（UB，可能 crash 或讀到垃圾）。**free 後把指標設 NULL**（再用 NULL 至少會明確 crash 而非詭異行為）。

### 3. double free（重複釋放）

```c
free(p);
free(p);                     // double free！UB，可能 crash 或 heap 損壞
```

同一塊 free 兩次 = heap 管理結構損壞（UB，可能被利用成漏洞）。free 後設 NULL 也能防（`free(NULL)` 是安全的 no-op）。

## 字串字面值在 text 段（唯讀陷阱）

```c
char *p = "hello";    // p 指向 text 段的唯讀字串字面值
p[0] = 'H';           // UB！試圖改唯讀記憶體 → 多半 crash（segfault）

char arr[] = "hello"; // arr 是 stack 上的可改陣列（複製了字串）
arr[0] = 'H';         // OK，改的是 stack 上的副本
```

**關鍵差異**：
- `char *p = "hello"`：`p` 指向 text 段的字串字面值（唯讀），不能改 `p[0]`。
- `char arr[] = "hello"`：`arr` 是 stack 上的陣列，字串被**複製**進來，可改。

這是經典考古題——同樣寫 "hello"，指標版唯讀、陣列版可改。

## 考古題詳解

### Q1：以下變數各放記憶體哪一段？

```c
int g1 = 5;            // ?
int g2;                // ?
static int s = 3;      // ?
char *str = "hi";      // str 在? "hi" 在?
void f(void) {
    int local;         // ?
    int *p = malloc(8);// p 在? malloc 的記憶體在?
}
```

<details>
<summary>詳解</summary>

- `g1 = 5`（初值非0全域）→ **data**
- `g2`（初值0全域）→ **bss**
- `static int s = 3` → **data**（初值非0；若 `static int s;` 則 bss）
- `str`（區域指標）→ **stack**；`"hi"`（字串字面值）→ **text**（唯讀）
- `local`（區域變數）→ **stack**
- `p`（區域指標變數本身）→ **stack**；`malloc(8)` 配的記憶體 → **heap**

**考點**：記憶體五段分類，最高頻。注意「指標變數本身」（stack）vs「它指向的東西」（heap/text）。
</details>

### Q2：這段有什麼問題？

```c
char *get_string(void) {
    char buf[100];
    strcpy(buf, "hello");
    return buf;          // ?
}
```

<details>
<summary>詳解</summary>

**回傳了 stack 上區域變數的位址（dangling）！** `buf` 在 stack，函式返回後它的 stack frame 被回收，`buf` 的記憶體失效。回傳的指標指向無效記憶體，呼叫者用它 = UB。

修法：
- 用 `static char buf[100]`（放 data/bss，函式返回仍在——但非執行緒安全/可重入問題，Ch 15）。
- 用 `malloc`（heap，呼叫者負責 free）。
- 由呼叫者傳入緩衝區（`get_string(char *out)`）。

**考點**：回傳區域變數位址 = dangling，經典 bug。
</details>

### Q3：`char *p = "hello"; p[0] = 'H';` 會怎樣？

<details>
<summary>詳解</summary>

**UB，多半 segfault（crash）。** `"hello"` 是 text 段的唯讀字串字面值，試圖修改 = 寫唯讀記憶體。

對比 `char arr[] = "hello"; arr[0] = 'H';` → OK（arr 在 stack，是複製的副本）。

**考點**：字串字面值唯讀（text 段），`char *` vs `char []` 的差異。
</details>

### Q4：stack 和 heap 主要差在哪？什麼時候用哪個？

<details>
<summary>詳解</summary>

| | stack | heap |
|---|---|---|
| 管理 | 自動 | 手動 malloc/free |
| 速度 | 快 | 慢 |
| 大小 | 有限（會 overflow） | 大 |
| 生命週期 | 函式內 | 你決定 |

用 stack：小資料、生命週期在函式內、大小編譯期已知。
用 heap：大資料、要跨函式存活、大小執行期才知道（如 `malloc(n*sizeof(int))`，n 執行期決定）。

**考點**：stack/heap 取捨，必考。
</details>

### Q5：malloc 失敗回傳什麼？要怎麼處理？

<details>
<summary>詳解</summary>

回傳 **NULL**。每次 malloc 後**必須檢查**：

```c
int *p = malloc(n * sizeof(int));
if (p == NULL) { /* 處理配置失敗（記憶體不足）*/ return -1; }
```

不檢查就用，若 malloc 失敗（回 NULL）→ 解參照 NULL → crash。韌體記憶體少，配置失敗更常見，必檢查。

**考點**：malloc 失敗處理（NULL 檢查），韌體尤其重要。
</details>

## 踩雷集錦

1. **回傳區域變數（stack）的位址**：函式返回後失效（dangling）。要用 static/malloc/呼叫者傳入。
2. **改字串字面值 `char *p="x"; p[0]=...`**：text 唯讀，UB/crash。要用 `char arr[]`。
3. **malloc 沒檢查 NULL**：失敗回 NULL，不檢查直接用會 crash。
4. **忘了 free（leak）/ free 後再用（dangling）/ free 兩次（double free）**：三大 heap 錯誤。每個 malloc 配一個 free，free 後設 NULL。
5. **搞混「指標變數」和「指向的記憶體」**：`int *p = malloc()`——`p` 在 stack，配的記憶體在 heap。free 的是後者。
6. **以為 free 會把指標設 NULL**：不會。free 只還記憶體，指標還指著舊位址（dangling）。要手動設 NULL。

## 速記

- 五段：**text**（碼+字串字面值,唯讀）、**data**（初值非0全域/static）、**bss**（初值0,載入清零）、**heap**（malloc,手動）、**stack**（區域變數,自動,有限）。
- static/全域 → data/bss（保值、歸零）；區域 → stack（消失、垃圾）；malloc → heap。
- **stack**：快、小、自動、函式內；**heap**：慢、大、手動、你決定生命週期。
- 三大 heap 錯誤：**leak**（沒free）、**dangling**（free後用）、**double free**（free兩次）。free 後設 NULL。
- malloc 失敗回 **NULL**，必檢查；回傳 stack 區域變數位址 = dangling。
- `char *p="x"`（text唯讀,不可改）vs `char arr[]="x"`（stack副本,可改）。

## 自我檢核

- [ ] 不看圖，能畫出五段記憶體佈局、說出各放什麼嗎？
- [ ] static 變數、區域變數、malloc 的記憶體各在哪段？為什麼 static 保值、區域消失？
- [ ] 回傳函式內 `char buf[100]` 的位址有什麼問題？怎麼修？
- [ ] `char *p="x"` 和 `char arr[]="x"` 改 `[0]` 各會怎樣？
- [ ] leak / dangling / double free 各是什麼？怎麼防？

## 延伸閱讀

### 書籍

- **《Computer Systems: A Programmer's Perspective (CSAPP)》** — §9.9 Dynamic Memory Allocation
  - **讀哪裡**：9.9（malloc 實作、碎片、常見錯誤）、7.x（記憶體段/連結）。
  - **和本章的關聯**：記憶體佈局與 malloc 的權威，連結 Ch 25 OS 記憶體。

- **《Expert C Programming》** — 談 memory layout / segment 的章
  - **為什麼值得讀**：把 text/data/bss/stack/heap 講得很實際。

### 文章

- **[Anatomy of a Program in Memory](https://manybutfinite.com/post/anatomy-of-a-program-in-memory/)** — Gustavo Duarte
  - **這篇說什麼**：行程記憶體佈局的圖解（含 virtual memory）。
  - **讀哪裡**：整篇；本章五段圖的詳細視覺版。
  - **為什麼值得讀**：把記憶體佈局畫得最清楚的一篇。

記憶體佈局懂了，Part 1 最後一章是上機考的手寫常客——字串與標準函式，自己實作 strcpy/memcpy。

→ [Ch 12 字串與標準函式](./12-strings-stdlib.md)
