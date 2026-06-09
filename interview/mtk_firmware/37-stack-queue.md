# Ch 37 — stack / queue

> **目標**：搞懂 stack（堆疊，LIFO）和 queue（佇列，FIFO）的操作、實作（array vs linked list）、circular queue、以及常見應用。這兩個是基礎結構，面試會問實作與應用。

> **環境**：C，`gcc -Wall`。前置：Ch 36（array/linked list）、Ch 11（stack 記憶體）。

## 為什麼考這個

stack 和 queue 是最基礎的兩個抽象資料結構——很多東西建在它們上面（函式呼叫用 stack、BFS 用 queue、buffer 用 queue）。面試會問「實作一個 stack/queue」「LIFO/FIFO 差別」「circular queue 怎麼做」。也連結韌體的 ring buffer（練習 B）。

## 先建立直覺：疊盤子 vs 排隊

```
   stack（堆疊）= 疊盤子：後放的先拿（LIFO，Last In First Out）
   push（放上去）、pop（從頂拿）—— 都在「同一端」

   queue（佇列）= 排隊：先到的先服務（FIFO，First In First Out）
   enqueue（隊尾加入）、dequeue（隊首取出）—— 「兩端」操作
```

核心：**stack = LIFO（一端進出）；queue = FIFO（一端進、另一端出）。** 這個「進出順序」決定它們的用途。

## stack（LIFO）

操作：`push`（推入頂）、`pop`（彈出頂）、`peek/top`（看頂不取）、`isEmpty`。全部 O(1)。

array 實作：

```c
#define MAX 100
typedef struct {
    int data[MAX];
    int top;            // 頂的索引，-1 表示空
} Stack;

void init(Stack *s) { s->top = -1; }
int isEmpty(Stack *s) { return s->top == -1; }
int isFull(Stack *s) { return s->top == MAX - 1; }

void push(Stack *s, int x) {
    if (isFull(s)) return;       // 滿了（邊界檢查！）
    s->data[++s->top] = x;       // top 先 +1 再放
}
int pop(Stack *s) {
    if (isEmpty(s)) return -1;   // 空（邊界檢查！）
    return s->data[s->top--];    // 取再 top -1
}
```

stack 的應用（常考）：
- **函式呼叫**（call stack，Ch 11）——返回位址、區域變數，後呼叫的先返回（LIFO）。
- **括號匹配**（檢查 `()[]{}` 配對）。
- **運算式求值**（中綴轉後綴、後綴求值）。
- **DFS**（深度優先，Ch 41）、回溯（backtracking）、undo 功能。

## queue（FIFO）

操作：`enqueue`（隊尾加）、`dequeue`（隊首取）、`front`（看隊首）、`isEmpty`。O(1)。

queue 的應用：
- **BFS**（廣度優先，Ch 41）。
- **buffer / 排程**（先到先處理，如印表佇列、Ch 21 FCFS）。
- **生產者-消費者的 buffer**（Ch 24）、韌體 ring buffer（練習 B）。

## circular queue（環形佇列，必考）

用 array 實作 queue 有個問題：dequeue 後前面的空間「浪費」了（front 往後移，前面空出來但用不到）。**circular queue（環形佇列）** 解決——讓 array「首尾相接」循環使用：

```c
#define SIZE 5
typedef struct {
    int data[SIZE];
    int front, rear;    // front=隊首、rear=隊尾
    int count;          // 元素數（用來區分滿/空）
} CircularQueue;

void init(CircularQueue *q) { q->front = 0; q->rear = 0; q->count = 0; }
int isEmpty(CircularQueue *q) { return q->count == 0; }
int isFull(CircularQueue *q) { return q->count == SIZE; }

void enqueue(CircularQueue *q, int x) {
    if (isFull(q)) return;
    q->data[q->rear] = x;
    q->rear = (q->rear + 1) % SIZE;   // 環繞！到尾就回 0
    q->count++;
}
int dequeue(CircularQueue *q) {
    if (isEmpty(q)) return -1;
    int x = q->data[q->front];
    q->front = (q->front + 1) % SIZE; // 環繞
    q->count--;
    return x;
}
```

關鍵：**`% SIZE` 讓索引環繞**（到尾回到頭），重複利用空間。

**滿/空的判斷問題**（經典考點）：當 `front == rear` 時，是滿還是空？兩種解法：
1. **用 count**（上面）：額外記元素數，count==0 空、count==SIZE 滿。最清楚。
2. **犧牲一格**：規定「rear 的下一格 == front」為滿（`(rear+1)%SIZE == front`），`front==rear` 為空。這樣少用一格，但不用額外 count。

circular queue 就是韌體 ring buffer 的本質（練習 B Q7 的 ISR↔主程式溝通）。

## stack vs queue 對比

| | stack | queue |
|---|---|---|
| 順序 | LIFO（後進先出）| FIFO（先進先出）|
| 操作端 | 同一端（top）| 兩端（front/rear）|
| 主要操作 | push/pop | enqueue/dequeue |
| 應用 | 函式呼叫、DFS、括號匹配、undo | BFS、buffer、排程、ring buffer |

## 考古題詳解

### Q1：stack 和 queue 差在哪？各舉應用

<details>
<summary>詳解</summary>

- **stack**：LIFO（後進先出），一端 push/pop。應用：函式呼叫（call stack）、DFS、括號匹配、運算式求值、undo。
- **queue**：FIFO（先進先出），一端 enqueue 一端 dequeue。應用：BFS、buffer、排程（FCFS）、ring buffer。

**考點**：LIFO vs FIFO + 應用，必考。
</details>

### Q2：用 array 實作 stack（push/pop）

<details>
<summary>詳解</summary>

```c
typedef struct { int data[MAX]; int top; } Stack;
void push(Stack *s, int x) {
    if (s->top >= MAX-1) return;   // 滿（邊界檢查）
    s->data[++s->top] = x;
}
int pop(Stack *s) {
    if (s->top < 0) return -1;     // 空（邊界檢查）
    return s->data[s->top--];
}
```

top 初值 -1（空）。push：`++top` 再放；pop：取再 `top--`。**邊界檢查必加**（滿/空）。

**考點**：stack 實作 + 邊界檢查。
</details>

### Q3：circular queue 怎麼判斷滿和空？

<details>
<summary>詳解</summary>

問題：`front == rear` 時不知是滿還是空。兩解法：
1. **用 count**：額外記元素數，count==0 空、count==SIZE 滿（最清楚）。
2. **犧牲一格**：`(rear+1)%SIZE == front` 為滿、`front==rear` 為空（少用一格，不用 count）。

環繞用 `% SIZE`。

**考點**：circular queue 滿/空判斷，經典。
</details>

### Q4：為什麼用 circular queue 而不是普通 array queue？

<details>
<summary>詳解</summary>

普通 array queue：dequeue 後 front 往後移，前面空出的空間**用不到**（rear 一直往後，到陣列尾就滿了，即使前面有空間）→ 浪費。

circular queue：用 `% SIZE` 讓索引環繞，dequeue 空出的前面空間能被 enqueue 重用——空間利用率 100%，不浪費。這就是韌體 ring buffer 的做法（練習 B）。

**考點**：circular queue 的必要性（空間重用）。
</details>

### Q5：怎麼用兩個 stack 實作一個 queue？

<details>
<summary>詳解</summary>

用兩個 stack（in 和 out）：
- enqueue：push 到 `in` stack。
- dequeue：若 `out` 空，把 `in` 全部 pop 出來 push 進 `out`（順序反轉兩次 = 變 FIFO），再從 `out` pop。

```
in:  push 1,2,3 → in=[1,2,3]（3在頂）
dequeue: out 空 → 把 in 倒進 out：out=[3,2,1]（1在頂）→ pop 出 1（FIFO！）
```

原理：stack 反轉兩次 = 保持原順序 = FIFO。amortized O(1)。

**考點**：兩 stack 實作 queue，經典變化題。
</details>

## 踩雷集錦

1. **stack/queue 沒邊界檢查**：push 滿的 stack（overflow）、pop 空的 stack（underflow）→ 錯誤。必檢查。
2. **circular queue 滿/空判斷錯**：`front==rear` 模糊。用 count 或犧牲一格。
3. **普通 array queue 浪費空間**：dequeue 後前面用不到。用 circular。
4. **circular queue 忘了 `% SIZE`**：索引到尾不環繞 → 越界。
5. **LIFO/FIFO 記反**：stack LIFO（疊盤子）、queue FIFO（排隊）。
6. **以為 stack/queue 操作不是 O(1)**：基本操作（push/pop/enqueue/dequeue）都 O(1)（array 或 linked list 實作）。

## 速記

- **stack（LIFO）**：一端 push/pop；應用：函式呼叫、DFS、括號匹配、undo。
- **queue（FIFO）**：一端 enqueue、另一端 dequeue；應用：BFS、buffer、排程、ring buffer。
- 操作都 **O(1)**；array/linked list 都可實作。
- **circular queue**：`% SIZE` 環繞重用空間；滿/空判斷用 **count** 或**犧牲一格**（`front==rear` 模糊）。= 韌體 ring buffer（練習 B）。
- 邊界檢查必加（滿/空）。
- 兩 stack 可實作 queue（反轉兩次 = FIFO）。

## 自我檢核

- [ ] stack 和 queue 的順序（LIFO/FIFO）差在哪？各舉兩個應用。
- [ ] 不看，能用 array 實作 stack（含邊界檢查）嗎？
- [ ] circular queue 怎麼判斷滿和空？為什麼會模糊？
- [ ] 為什麼用 circular queue 而非普通 array queue？
- [ ] 怎麼用兩個 stack 實作 queue？

## 延伸閱讀

### 書籍

- **《Introduction to Algorithms (CLRS)》** — Ch 10.1 Stacks and Queues
  - **讀哪幾章**：10.1。
  - **和本章的關聯**：stack/queue 的標準教材。

### 文章

- **[面試紀錄 & 練習（聯發科）— HackMD](https://hackmd.io/@chiangkd/interview)**
  - **讀哪裡**：stack/queue/circular queue 題。
  - **和本章的關聯**：MTK 的資料結構考點。

stack/queue 是線性結構，下一章進入樹狀——tree、BST、heap 與走訪。

→ [Ch 38 tree / BST / heap](./38-tree-bst-heap.md)
