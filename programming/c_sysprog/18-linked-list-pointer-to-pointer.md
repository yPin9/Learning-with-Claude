# Ch 18 — 鏈結串列與指標的指標

> **目標**：能用「指標的指標」（pointer to pointer）消掉鏈結串列操作裡的開頭節點特例，看懂 Linus Torvalds 所說的 "good taste"；能在 $O(1)$ 額外空間下偵測環並找出入口（Floyd 與 Brent）；能寫出遞迴與迭代二種鏈結串列 merge sort，並說清楚各自的比較次數與穩定性。
> **對應原文**：[你所不知道的 C 語言：鏈結串列和非連續記憶體篇](https://hackmd.io/@sysprog/c-linked-list) 的「從 Linux 核心的藝術談起」「移去與新增節點」「案例探討」「環狀鏈結串列」「Merge Sort 的實作」等節

> **環境**：WSL Ubuntu 22.04 x86_64，gcc 11.4。本章所有輸出都在這個環境實跑；位址類數值每次執行不同。

## 為什麼需要這個？

陣列是一整塊連續的記憶體，第 `i` 個元素的位址可以直接算出來。鏈結串列（linked list）反過來：每個節點各自配置、散落在 heap 各處，彼此只靠節點裡的一個 `next` 指標串起來。它換來的是 $O(1)$ 的插入與刪除（只要你手上已經有位置），代價是失去隨機存取，而且每走一步都要先把目前節點載入，才知道下一個節點在哪裡——這種「下一個位址取決於上一次載入的結果」的存取模式，對 [Ch 15](./15-memory-hierarchy.md) 講的快取與預取器非常不友善。

鏈結串列本身的資料結構寫起來不到十行，真正難的是**改指標**：刪一個節點、插一個節點，都要改「前一個節點的 `next`」。偏偏第一個節點沒有前一個節點，它是被 `head` 這個變數指著的。於是幾乎每本教科書的程式碼都長這樣：

```
if (要改的是第一個節點)
    head = ...;          ← 改的是 head 變數
else
    prev->next = ...;    ← 改的是某個節點的 next 欄位
```

二條路徑做的其實是同一件事——「把某個指向 target 的指標改成指向 target 的下一個」——只是那個指標住的地方不同。這章的主角「指標的指標」就是把「那個指標住在哪裡」也變成一個變數，二條路徑因此合而為一。這不是炫技：特例越少，越不容易在邊界上寫錯，而鏈結串列的 bug 幾乎全出在邊界（空串列、只有一個節點、刪開頭、刪尾端）。

> 如果你對「指標的指標」還不熟，先回看 [Ch 9 — void *、指標的指標、function pointer 與 qualifier](./09-void-pointer-and-callbacks.md) 的指標的指標一節；對 lvalue 的概念不熟，回看 [Ch 7 — 規格書裡的指標](./07-pointers-by-the-standard.md)。

## 先建立直覺

想像一排人手牽手，每個人的右手指向下一個人；隊伍最前面有一塊告示牌（`head`）指著第一個人。現在要把某個人 T 從隊伍裡拿掉，你要做的是：找到「正在指著 T 的那隻手」，把它改成指向 T 的下一個人。

教科書的作法是盯著**人**走：記住「前一個人」，找到 T 之後，如果沒有前一個人，就去改告示牌；否則改前一個人的右手。

Linus 的作法是盯著**手**走：你手上拿的不是「某個人」，而是「某隻指向別人的手」。一開始拿的是告示牌，之後每一步換成「目前這個人的右手」。找到「指著 T 的那隻手」時，不管它是告示牌還是某人的右手，改法完全一樣。

```
head ──▶ [10|•]──▶ [20|•]──▶ [30|•]──▶ NULL
 ▲          ▲         ▲
 │          │         │
 indirect 一開始指這裡（&head）
            第一步後指這裡（&n10->next）
                      第二步後指這裡（&n20->next）

indirect 的型別是 Node **：它存的是「某個 Node * 變數的位址」。
*indirect 就是那個 Node * 變數本身，可以讀也可以寫。
```

關鍵觀念只有一句：**`head` 和每個節點的 `next` 型別都是 `Node *`，所以一個 `Node **` 可以指向其中任何一個。**

## 從 Linus 的 good taste 開始

Linus Torvalds 在 2016 年 TED 訪談〈The mind behind Linux〉約 14:20 處談程式設計的 "good taste"，舉的例子就是從單向串列移除節點。他更早在 2012 年 Slashdot 訪談就說過，看到 `if (prev) prev->next = entry->next; else list_head = entry->next;` 這種程式碼，他就認定寫的人不懂指標；懂的人會用「指向 entry 指標的指標」，以 `list_head` 的位址初始化，走訪後直接 `*pp = entry->next`。

先看可跑的完整程式，二個版本並排（以下假設串列非空且 `target` 一定在串列裡，聚焦在開頭特例）：

```c
#include <stdio.h>
#include <stdlib.h>

typedef struct node {
    int value;
    struct node *next;
} Node;

typedef struct {
    Node *head;
} List;

static void print_list(const char *tag, const List *l)
{
    printf("%-10s:", tag);
    for (const Node *p = l->head; p; p = p->next)
        printf(" %d", p->value);
    printf("\n");
}

/* 教科書版：開頭節點與中段節點走不同路徑 */
static void remove_textbook(List *list, Node *target)
{
    Node *prev = NULL, *cur = list->head;
    while (cur != target) {
        prev = cur;
        cur = cur->next;
    }
    if (!prev)
        list->head = target->next;
    else
        prev->next = target->next;
}

/* Linus 版：indirect 指向「要被改寫的那個指標」 */
static void remove_indirect(List *list, Node *target)
{
    Node **indirect = &list->head;
    while (*indirect != target)
        indirect = &(*indirect)->next;
    *indirect = target->next;
}

int main(void)
{
    Node n[5];
    List a = {&n[0]}, b;
    for (int i = 0; i < 5; i++) {
        n[i].value = (i + 1) * 10;
        n[i].next = (i < 4) ? &n[i + 1] : NULL;
    }
    print_list("original", &a);
    remove_textbook(&a, &n[0]);   /* 移除開頭 */
    remove_textbook(&a, &n[3]);   /* 移除中段 */
    print_list("textbook", &a);

    for (int i = 0; i < 5; i++)
        n[i].next = (i < 4) ? &n[i + 1] : NULL;
    b.head = &n[0];
    remove_indirect(&b, &n[0]);
    remove_indirect(&b, &n[3]);
    print_list("indirect", &b);
    remove_indirect(&b, &n[4]);   /* 移除尾端 */
    print_list("tail gone", &b);
    return 0;
}
```

```
$ gcc -Wall -Wextra -g remove.c -o remove && ./remove
original  : 10 20 30 40 50
textbook  : 20 30 50
indirect  : 20 30 50
tail gone : 20 30
```

### 逐步畫圖：indirect 怎麼走

以刪除 `30` 為例（串列 `10 → 20 → 30 → 40`）：

```
步驟 0：indirect = &list->head
  list->head ─────▶ [10|next]──▶ [20|next]──▶ [30|next]──▶ [40|next]──▶ NULL
  ▲
  indirect          *indirect == n10 ≠ target，繼續

步驟 1：indirect = &(*indirect)->next   即 &n10->next
  list->head ─────▶ [10|next]──▶ [20|next]──▶ [30|next]──▶ [40|next]──▶ NULL
                         ▲
                         indirect   *indirect == n20 ≠ target，繼續

步驟 2：indirect = &n20->next
  list->head ─────▶ [10|next]──▶ [20|next]──▶ [30|next]──▶ [40|next]──▶ NULL
                                      ▲
                                      indirect   *indirect == n30 == target，停

步驟 3：*indirect = target->next   即 n20->next = n40
  list->head ─────▶ [10|next]──▶ [20|next]──┐  [30|next]──▶ [40|next]──▶ NULL
                                            └──────────────▶ ▲
```

若 target 是 `10`，迴圈一次都不執行，`indirect` 仍是 `&list->head`，步驟 3 改寫的就是 `list->head`——同一行程式碼，自動涵蓋了開頭的情況。

### 規格書怎麼保證這能寫

`*indirect = ...` 之所以能放在指定運算子左側，是因為 unary `*` 的結果是 lvalue。C11（N1570）6.5.3.2 §4：

> The unary * operator denotes indirection. If the operand points to a function, the result is a function designator; if it points to an object, the result is an lvalue designating the object.

`indirect` 指向的物件可能是 `list->head`，也可能是某個節點的 `next` 成員，`*indirect` 都是指稱該物件的 lvalue。`&(*indirect)->next` 則先用 `->` 取得下一個節點的 `next` 成員（也是 lvalue），再以 unary `&` 取其位址；6.5.3.2 §1 要求 `&` 的運算元必須是 lvalue（或函式指示符等少數例外）。

### 對照組合語言：機器其實不在乎

一個常被忽略的事實：開最佳化後，編譯器常常自己就把二個版本變得差不多。把二個函式丟給 gcc 11.4 `-O2`：

```
$ gcc -O2 -S -fno-asynchronous-unwind-tables -masm=intel rm_asm.c -o -
remove_textbook:                         remove_indirect:
    mov  rax, QWORD PTR [rdi]               mov  rax, QWORD PTR [rdi]
    cmp  rax, rsi                           cmp  rsi, rax
    je   .L2                                je   .L12
.L3:                                     .L11:
    mov  rdx, rax                           mov  rdx, rax
    mov  rax, QWORD PTR 8[rax]              mov  rax, QWORD PTR 8[rax]
    cmp  rsi, rax                           cmp  rsi, rax
    jne  .L3                                jne  .L11
    mov  rax, QWORD PTR 8[rsi]              lea  rdi, 8[rdx]
    mov  QWORD PTR 8[rdx], rax           .L12:
    ret                                     mov  rax, QWORD PTR 8[rsi]
.L2:                                        mov  QWORD PTR [rdi], rax
    mov  rax, QWORD PTR 8[rax]              ret
    mov  QWORD PTR [rdi], rax
    ret
```

（為了並排閱讀而排版，指令內容照抄自實際輸出；`endbr64` 已略去。`8[rax]` 就是 `next` 欄位，因為 `int value` 佔 4 bytes 加上 4 bytes padding，見 [Ch 16](./16-alignment-and-padding.md)。）

二者的迴圈本體一模一樣。教科書版被編譯器拆成二個出口，indirect 版則用一道 `lea rdi, 8[rdx]` 把「要寫的位址」算出來後匯流到同一個出口。結論要說清楚：**good taste 的收益在人，不在機器**。它讓你在讀程式時少想一種情況、寫程式時少一個出錯的分支；效能上不要期待有差。Linus 自己在 TED 上也說這是 "CS 101"，重點是「換個角度看問題，讓特例變成一般情況」。

## 移除、新增與尾端指標

### 移除第一個值為 v 的節點：三種寫法

```c
/* 寫法 A：回傳新開頭，開頭要另外判斷 */
Node *list_remove(Node *head, int value)
{
    if (!head)
        return NULL;
    if (head->value == value)
        return head->next;
    Node *prev = head;
    for (Node *cur = head->next; cur; prev = cur, cur = cur->next)
        if (cur->value == value) {
            prev->next = cur->next;
            break;
        }
    return head;
}

/* 寫法 B：放一個區域變數 dummy 在開頭前面 */
Node *list_remove(Node *head, int value)
{
    Node dummy = {.next = head};
    for (Node *prev = &dummy; prev->next; prev = prev->next)
        if (prev->next->value == value) {
            prev->next = prev->next->next;
            break;
        }
    return dummy.next;
}

/* 寫法 C：指標的指標，連 dummy 都不要 */
void list_remove(Node **head, int value)
{
    for (Node **pp = head; *pp; pp = &(*pp)->next)
        if ((*pp)->value == value) {
            *pp = (*pp)->next;
            break;
        }
}
```

寫法 B 的 dummy node（也叫 sentinel，哨兵）也能消除特例，代價是多一個節點大小的區域變數，且 dummy 的 `value` 欄位是沒有意義的垃圾——如果節點很大（例如內嵌一個 4 KiB buffer），在 stack 上放一個 dummy 就不划算了。寫法 C 什麼都不多付。被移走的節點由呼叫端負責 `free`。

### 名字不能叫 `remove`：故意弄錯看看

這三個函式都刻意取名 `list_remove`。改成 `remove` 試試：

```c
#include <stdio.h>
typedef struct node { int value; struct node *next; } Node;
Node *remove(Node *head, int value)
{
    (void) value;
    return head;
}
int main(void) { return remove(NULL, 0) != NULL; }
```

```
$ gcc -Wall -Wextra clash.c -o clash
clash.c:3:7: error: conflicting types for ‘remove’; have ‘Node *(Node *, int)’ {aka ‘struct node *(struct node *, int)’}
    3 | Node *remove(Node *head, int value)
      |       ^~~~~~
In file included from clash.c:1:
/usr/include/stdio.h:152:12: note: previous declaration of ‘remove’ with type ‘int(const char *)’
  152 | extern int remove (const char *__filename) __THROW;
      |            ^~~~~~
```

`remove` 是 `<stdio.h>` 的刪檔函式（C11 7.21.4.1）。即使不含入 `<stdio.h>`，C11 7.1.3 也規定標準函式庫中具外部連結的識別字保留給實作，自己定義同名外部函式屬未定義行為。同理，macOS 與 FreeBSD 的 `<stdlib.h>` 有 BSD 的 `mergesort(3)`，後面的 merge sort 函式也要避開這個名字。

### 新增到尾端：為什麼參數是 `Node **`

```c
int append_indirect(int value, Node **head)
{
    Node *node = malloc(sizeof(*node));
    if (!node)
        return -1;
    node->value = value, node->next = NULL;

    Node **indirect = head;
    while (*indirect)
        indirect = &(*indirect)->next;
    *indirect = node;
    return 0;
}
```

串列為空時，新節點會成為開頭，`head` 變數本身必須被改寫。C 的參數傳遞只有傳值（[Ch 11](./11-process-layout-and-abi.md) 看過參數就是 callee 自己的暫存器或 stack 槽），函式拿到的 `Node *head` 只是副本，改它不影響呼叫端；所以要傳 `&head` 進來。另一條路是回傳新開頭（`head = append(v, head)`），但這樣回傳值就不能再拿來回報 `malloc` 失敗了。

這個函式每次都要走到尾端，連續加 $n$ 個節點是 $O(n^2)$。間接指標的第二個用途是**記住尾端欄位的位址**。Git 的 [`commit.c`](https://github.com/git/git/blob/master/commit.c) 裡 `commit_list_append()` 接受 `struct commit_list **next`，把新節點寫入 `*next` 後回傳新節點的 `&new->next`，呼叫端持有這個間接指標，每次新增 $O(1)$：

```c
/* 仿 Git commit_list_append：回傳新的「尾端欄位位址」 */
static Node **append_at(int value, Node **tail)
{
    Node *node = malloc(sizeof(*node));
    if (!node)
        return NULL;
    node->value = value, node->next = NULL;
    *tail = node;
    return &node->next;
}
/* 用法：Node *list = NULL, **tail = &list;
 *       for (...) tail = append_at(i, tail);            */
```

`tail` 一開始是 `&list`，空串列不需特例。實測（`-O2`，同一台機器，`clock()` 量測，數值依機器不同）：

```
n= 10000  walk-to-tail    50.5 ms   tail-pointer  0.28 ms
n= 20000  walk-to-tail   202.8 ms   tail-pointer  0.57 ms
n= 40000  walk-to-tail   813.0 ms   tail-pointer  1.47 ms
```

$n$ 加倍，走到尾端的版本時間約變 4 倍（二次方），尾端指標版約變 2 倍（線性）。Linux 核心 RCU 的回呼串列用的是同一招：[`kernel/rcu/rcu_segcblist.c`](https://github.com/torvalds/linux/blob/master/kernel/rcu/rcu_segcblist.c) 的 `rcu_cblist_enqueue()` 本體只有 `*rclp->tail = rhp; rclp->tail = &rhp->next;`。

## LeetCode 案例：同一招反覆出現

以下案例都用這個節點與輔助函式（`ll.h`，`ll_from` 由陣列建串列，`ll_print` 印出，`ll_free` 釋放），完整程式都附 `-fsanitize=address,undefined` 跑過。

```c
struct ListNode {
    int val;
    struct ListNode *next;
};
```

### LeetCode 21：合併二條已排序串列

dummy 版和指標的指標版的差別，和前面移除節點完全同構，這裡直接看最精簡的第三版：

```c
static struct ListNode *merge_node(struct ListNode *L1, struct ListNode *L2)
{
    struct ListNode *head = NULL, **ptr = &head, **node;
    for (; L1 && L2; *node = (*node)->next) {
        node = (L1->val <= L2->val) ? &L1 : &L2;
        *ptr = *node;
        ptr = &(*ptr)->next;
    }
    *ptr = L1 ? L1 : L2;
    return head;
}
```

二個間接指標各司其職：`ptr` 指向「結果串列的尾端欄位」，`node` 指向「L1 或 L2 這個變數本身」。第一輪、選中 `L1` 時：

| 運算式 | 此時等同 | 說明 |
|---|---|---|
| `ptr = &head` | | `ptr` 指向變數 `head` |
| `node = &L1` | | 比較後選取 `L1` |
| `*ptr = *node` | `head = L1` | 把選中的節點接上 |
| `ptr = &(*ptr)->next` | `ptr = &head->next` | `ptr` 移到剛接上節點的 `next` |
| `*node = (*node)->next` | `L1 = L1->next` | `L1` 前進一步 |

```
dummy       : [1, 1, 2, 3, 4, 4]
indirect    : [1, 1, 2, 3, 4, 4]
node        : [1, 1, 2, 3, 4, 4]
empty+b     : [1, 3, 4]
empty+empty : []
```

二個細節值得停下來：

1. **`<=` 不是隨便寫的**。數值相等時取左邊的 `L1`，相等元素維持原本的先後次序，稱為穩定（stable）合併。寫成 `<` 也能排序，但以它建構的 merge sort 不再穩定。
2. **為什麼不寫 `node = &(L1->val <= L2->val ? L1 : L2)`**？試試看：

```
$ gcc -Wall -Wextra -c condlv.c
condlv.c: In function ‘pick’:
condlv.c:4:30: error: lvalue required as unary ‘&’ operand
    4 |     struct ListNode **node = &(L1->val <= L2->val ? L1 : L2);
      |                              ^
```

C11 6.5.15 的註腳 110 明說 "A conditional expression does not yield an lvalue."，而 `&` 的運算元要是 lvalue。所以只能先分別取 `&L1`、`&L2`，再用條件運算挑一個。（C++ 的條件運算子在二側都是同型別 lvalue 時結果是 lvalue，這是 C 與 C++ 的差異之一。）

原文還提到用 `(uintptr_t) L1 | (uintptr_t) L2` 取代 `L1 ? L1 : L2`。這依賴 ISO C 沒保證的事：指標與整數互轉的結果由實作定義（C11 6.3.2.3 §5–6），標準不保證空指標轉成整數為 0，`uintptr_t` 本身還是選配型別（7.20.1.4）。`L1 ? L1 : L2` 意圖清楚，編譯器也常產生無分支的 `cmov`，沒有理由耍這個花招。

**合併 k 條串列（LeetCode 23）**：逐一把 `lists[i]` 併進 `lists[0]`，`lists[0]` 越來越長，最壞 $O(kN)$（$N$ 為總節點數）。改成每輪合併相隔 `interval` 的二條、`interval` 每輪加倍，共 $\lceil \log_2 k \rceil$ 輪、每輪每個節點最多被走一次，總時間 $O(N \log k)$：

```c
for (int interval = 1; interval < listsSize; interval *= 2)
    for (int i = 0; i + interval < listsSize; i += interval * 2)
        lists[i] = mergeTwoLists(lists[i], lists[i + interval]);
```

它只合併**相鄰**的串列，因此保持穩定；「頭尾配對」（`lists[i]` 配 `lists[n-1-i]`）同樣 $O(N \log k)$，但會打亂相等元素的原次序。這個「分段合併」等一下會變成迭代 merge sort 的骨架。

### LeetCode 2095：刪除中間節點，以及一個真實的 UAF

快慢指標（fast/slow pointer）：`fast` 每次走二步，間接指標 `indir` 每次走一步，`fast` 到尾端時 `*indir` 就是中間節點（從 0 起算第 $\lfloor n/2 \rfloor$ 個）。若要一併 `free` 被刪的節點，很多人會這樣寫：

```c
/* 直覺版：多一個 prev 跟在 *indir 之前 —— 有 bug */
static struct ListNode *deleteMiddle(struct ListNode *head)
{
    if (!head->next) {
        free(head);
        return NULL;
    }
    struct ListNode **indir = &head, *prev = NULL;
    for (struct ListNode *fast = head; fast && fast->next;
         fast = fast->next->next) {
        prev = *indir;
        indir = &(*indir)->next;
    }
    prev->next = (*indir)->next; /* 這行其實就是 *indir = ...  */
    free(*indir);                /* 釋放到的是「下一個」節點！ */
    return head;
}
```

輸入 `[1, 3, 4, 7, 1, 2, 6]`，開 AddressSanitizer 跑：

```
$ gcc -Wall -Wextra -g -fsanitize=address -DBUGGY delmid.c -o delmid_bug && ./delmid_bug
==3284==ERROR: AddressSanitizer: heap-use-after-free on address 0x502000000098 at pc 0x55a42d8225ee ...
READ of size 8 at 0x502000000098 thread T0
    #0 0x55a42d8225ed in ll_print /home/ypp/c_sysprog_lab/p3b/ll.h:26
    #1 0x55a42d822c8d in main /home/ypp/c_sysprog_lab/p3b/delmid.c:40
...
0x502000000098 is located 8 bytes inside of 16-byte region [0x502000000090,0x5020000000a0)
freed by thread T0 here:
    #0 0x7ab0b72b4537 in __interceptor_free ...
    #1 0x55a42d822997 in deleteMiddle /home/ypp/c_sysprog_lab/p3b/delmid.c:18
```

（位址每次執行不同。）「8 bytes inside of 16-byte region」：16 bytes 是一個 `struct ListNode`，偏移 8 正是 `next` 欄位，`ll_print` 正在沿著 `next` 走。

**為什麼**？迴圈結束時 `prev` 指向 node4、`*indir` 是 node7。但 `indir` 存的值恰好就是 `&prev->next`——`prev->next` 和 `*indir` 是**同一個物件的二個名字**（aliasing）。所以 `prev->next = (*indir)->next` 執行後，`*indir` 已經變成 node1；接下來的 `free(*indir)` 釋放的是 node1，而它還串在串列裡。node7 則洩漏。之後走訪讀到 node1，就是使用已結束生命期的物件，C11 6.2.4 §2 規定此時指標值變為 indeterminate（C23 6.2.4 §2 直接寫明是 undefined behavior）。

修正：**先記住要刪的節點，再改指標**，`prev` 也不需要了：

```c
static struct ListNode *deleteMiddle(struct ListNode *head)
{
    struct ListNode **indir = &head;
    for (struct ListNode *fast = head; fast && fast->next;
         fast = fast->next->next)
        indir = &(*indir)->next;
    struct ListNode *del = *indir;
    *indir = del->next;
    free(del);
    return head;
}
```

```
result      : [1, 3, 4, 1, 2, 6]
single      : []
two         : [1]
```

只有一個節點時迴圈不執行，`*indir` 就是 `head`，改寫後回傳 `NULL`，不需特例；ASan 的 leak 檢查也沒有回報。

```
迴圈結束時：
  indir ──▶ [4|next]──▶ [7|next]──▶ [1|next]──▶ ...
             (node4 的 next 欄位)

del = *indir;         del ──▶ [7]
*indir = del->next;   [4|next]─────────────▶ [1|next]
                                [7|next]──┘ （del 仍指著 7）
free(del);            釋放的是 7，正確
```

原文補了一個效能觀察：「先數長度、再走 $\lfloor n/2 \rfloor$ 步」與快慢指標讀 `next` 的次數一樣多，但二趟版的耗時約為 1.5 倍。原因在關鍵路徑（critical path）：快慢指標的 `fast` 與 `slow` 是二條互不相依的載入鏈，亂序執行可以讓它們重疊；二趟版的第二趟要等第一趟結束才開始。這點我們沒有在本章重測，數據與組合語言分析見原文連結的〈分析「快慢指標」〉。

### LeetCode 86：Partition List 與「指標的指標的指標」

把小於 `x` 的節點移到其餘節點之前，二組各自保持原次序。二個 dummy 的版本有一個致命的邊界：

```c
for (; head; head = head->next) {
    if (head->val < x)
        p1 = p1->next = head;
    else
        p2 = p2->next = head;
}
p2->next = NULL;      /* 拿掉這行會怎樣？ */
p1->next = more.next;
```

拿掉 `p2->next = NULL` 跑 `[1, 4, 3, 2, 5, 2]`、`x = 3`，只印前 10 個：

```
first 10    : 1 2 2 4 3 5 2 4 3 5 ...
```

node5 原本的 `next` 指向最後一個 node2，而那個 node2 已經被搬到前段——串列成環了，走訪永遠停不下來（LeetCode 會判超時）。後段的尾端一定要截斷。

指標的指標版用二個尾端欄位指標 `p1`、`p2`，而「選 `p1` 還是 `p2`」又用一層間接消掉 `if-else`：

```c
static struct ListNode *partition_3star(struct ListNode *head, int x)
{
    struct ListNode *l2 = NULL;
    struct ListNode **p1 = &head, **p2 = &l2;
    for (struct ListNode *node = head; node; node = node->next) {
        struct ListNode ***indir = node->val < x ? &p1 : &p2;
        **indir = node;               /* *p1 = node  */
        *indir = &(**indir)->next;    /* p1 = &(*p1)->next */
    }
    *p1 = l2;
    *p2 = NULL;
    return head;
}
```

```
dummy       : [1, 2, 2, 4, 3, 5]
3star       : [1, 2, 2, 4, 3, 5]
all >= x    : [5, 6, 7]
```

為什麼迴圈裡改寫 `**indir` 不會破壞 `node = node->next`？因為被寫入的永遠是**先前**節點的 `next` 欄位（或 `head`、`l2`），目前節點的 `next` 要到下一輪才會被改。全部節點都 `>= x` 時 `p1` 從未前進，`*p1 = l2` 改寫的就是 `head`。三顆星看起來嚇人，但只要逐層念：`indir` 指向 `p1`；`*indir` 就是 `p1`；`**indir` 就是 `*p1`，即前段尾端那個 `next` 欄位。實務上這一層是否值得，見仁見智——二個 `if` 分支其實更好讀，這裡的重點是練習「把要修改的位置當成資料」的思考方式。

## 環狀串列與 Floyd cycle detection

### 環狀鏈結串列

環狀鏈結串列（circular linked list）的尾端不指向 `NULL`，而是指回開頭。單向環狀串列只要存一個指向**尾端**的 `last`，開頭就是 `last->next`，二端插入都是 $O(1)$：

```c
/* last 指向尾端；last->next 是開頭 */
void push_front(struct ListNode **last, struct ListNode *node)
{
    if (!*last) {
        node->next = node;
        *last = node;
        return;
    }
    node->next = (*last)->next;
    (*last)->next = node;
}

void push_back(struct ListNode **last, struct ListNode *node)
{
    push_front(last, node);
    *last = node;     /* 環上「開頭之前」就是「尾端之後」 */
}
```

`push_back` 配上「從 `last->next` 取出」就是只用一個指標的 FIFO 佇列。代價是走訪不再以 `NULL` 結束，要記住起點，寫成 `do { ... } while (p != start)`。雙向環狀串列再加上一個不存資料的哨兵節點，每個 `prev`、`next` 永遠是有效指標，所有插入刪除都沒有特例——這正是下一章 Linux `struct list_head` 的設計。

順帶一提沒有 `NULL` 的另一個好處：對空指標做 unary `*` 是未定義行為（C11 6.5.3.2 註腳 102）；在有 MMU 的 Linux 上它通常立刻 segfault，但在沒有 MMU 的 bare metal 環境，讀位址 0 常常不會出錯，走過串列尾端的 bug 會悄悄讀到垃圾。

### 偵測環：問題的形狀

環有時是設計，有時是 bug（像剛才 partition 忘記截斷）。以 $x_i$ 表示從開頭走 $i$ 步到的節點。若串列成環，它的形狀像希臘字母 $\rho$：前 $\mu$ 個節點不在環上，環的入口是 $x_\mu$，環長 $\lambda$：

```
  x0 ─▶ x1 ─▶ x2 ─▶ x3 ─▶ x4
  (a)         (b)          │
               ▲           ▼
               x6 ◀─────── x5
                           (c)
  μ = 2（x0、x1 不在環上），λ = 5（x2..x6），入口 b = x2
```

用雜湊表記錄走過的節點可以直接找到入口，但要 $O(n)$ 額外空間。Floyd 的龜兔賽跑（tortoise and hare）只要二個指標。

### Floyd 演算法的三個階段與推導

1. **相遇**：tortoise 每次一步、hare 每次二步，第 $t$ 輪分別在 $x_t$ 與 $x_{2t}$。hare 碰到 `NULL` 表示無環。若有環，二者相遇（$x_t = x_{2t}$）的條件是：二者都已在環上（$t \ge \mu$），且距離差 $t$ 是環長的倍數（$\lambda \mid t$）。第一次相遇的 $t$ 是「不小於 $\mu$ 的最小正 $\lambda$ 倍數」，所以 $t \le \mu + \lambda$，第一階段是線性時間。上圖 $t = 5$，相遇在 $c = x_5$。
2. **找入口**：一個指標回到 $x_0$，另一個留在 $x_t$，二者每次各走一步。走了 $\mu$ 步後分別在 $x_\mu$ 與 $x_{t+\mu}$；因為 $t$ 是 $\lambda$ 的倍數，$x_{t+\mu} = x_\mu$——二者恰在入口相遇。在此之前從 $x_0$ 出發的那個還不在環上，不可能提早相遇，所以步數就是 $\mu$。
3. **量環長**：從入口繞一圈回到入口，得到 $\lambda$；回到入口前的最後一個節點就是「尾端」，`last->next = NULL` 即可把環剪開。

這個推導的漂亮之處在第 2 步：我們**不必知道** $t$ 是 $\lambda$ 的幾倍，知道它是倍數就夠了。

```c
/* 回傳環上的尾端節點；無環回傳 NULL，*mu 與 *lambda 皆為 0 */
static struct ListNode *find_cycle(struct ListNode *head, size_t *mu,
                                   size_t *lambda)
{
    struct ListNode *slow = head, *fast = head;
    *mu = *lambda = 0;
    do {
        if (!fast || !fast->next)
            return NULL;                 /* 走到尾端：無環 */
        slow = slow->next;
        fast = fast->next->next;
    } while (slow != fast);

    for (slow = head; slow != fast; slow = slow->next, fast = fast->next)
        (*mu)++;

    struct ListNode *last = slow;        /* slow 是入口 */
    for (*lambda = 1; last->next != slow; last = last->next)
        (*lambda)++;
    return last;
}
```

### Brent 的瞬移烏龜

Richard Brent 在 1980 年提出另一種走法：tortoise 停著不動，hare 一步步走；步數達到 2 的冪時，tortoise 瞬移到 hare 的位置、上限加倍。hare 再遇到 tortoise 時，期間走的步數直接就是 $\lambda$：

```c
static size_t brent_lambda(const struct ListNode *head)
{
    if (!head)
        return 0;
    const struct ListNode *tortoise = head, *hare = head->next;
    size_t power = 1, lambda = 1;
    while (hare != tortoise) {
        if (!hare)
            return 0;                    /* 無環 */
        if (power == lambda) {           /* 瞬移 */
            tortoise = hare;
            power *= 2;
            lambda = 0;
        }
        hare = hare->next;
        lambda++;
    }
    return lambda;
}
```

為什麼一定會遇到？當 `power` 增長到 $\ge \lambda$ 且 tortoise 已瞬移進環內之後，hare 在下一個區段內繞完一圈前就會回到 tortoise 所在的位置。Floyd 每輪要移動三次指標（tortoise 一次、hare 二次），Brent 每步只移動 hare 一次；Brent 的論文報告平均約比 Floyd 快 36%（這是論文的數字，本章未重測）。取得 $\lambda$ 後，讓一個指標先走 $\lambda$ 步，再與從開頭出發的指標同速前進，相遇處就是入口。

用六種形狀測試（`build` 建 $n$ 個節點，尾端接回第 `entry` 個）：

```
rho mu=2 lambda=5 : floyd mu=2 lambda=5 last=6 | brent lambda=5
full circle       : floyd mu=0 lambda=5 last=4 | brent lambda=5
self loop tail    : floyd mu=3 lambda=1 last=3 | brent lambda=1
no cycle          : floyd mu=0 lambda=0 last=-1 | brent lambda=0
single node       : floyd mu=0 lambda=0 last=-1 | brent lambda=0
empty             : floyd mu=0 lambda=0 last=-1 | brent lambda=0
```

邊界都過：整串首尾相連時 $\mu = 0$；尾端指回自己時 $\lambda = 1$；空串列 `fast` 一開始就是 `NULL`。

**這不只是面試題**。Knuth 在 TAOCP 第 2 卷用它量虛擬亂數產生器的週期；Pollard 在 1975 年把它用在整數分解（Pollard's rho，名字同樣來自 $\rho$ 形軌跡），GNU coreutils 的 `factor`（[`src/factor.c`](https://github.com/coreutils/coreutils/blob/master/src/factor.c)）用的是 Pollard-Brent 版本；Emacs 的 [`src/lisp.h`](https://github.com/emacs-mirror/emacs/blob/master/src/lisp.h) 以巨集 `FOR_EACH_TAIL` 走訪 Lisp 串列時，用改良版的 Brent 演算法偵測環狀串列並發出 `circular-list` 錯誤。另外一個歷史細節：演算法冠 Floyd 之名源自 Knuth 的說法，但 Floyd 已發表的著作中找不到它（見 Wikipedia "Cycle detection"）。

## Merge sort on list：遞迴與迭代

陣列的 merge sort 需要 $O(n)$ 暫存陣列；鏈結串列的合併只改 `next`，不搬資料。因此鏈結串列排序的首選幾乎都是 merge sort：最差 $O(n \log n)$、穩定、不需暫存陣列。quick sort 則因為無法隨機取 pivot，遇到已排序輸入容易退化成 $O(n^2)$（下一章會再看到）。

### 遞迴（top-down）

切分沿用快慢指標：`fast` 從第二個節點出發，`slow` 停在前半段最後一個節點（奇數長度時前半段多一個），剪斷後各自遞迴：

```c
static struct ListNode *mergesort_list(struct ListNode *head)
{
    if (!head || !head->next)
        return head;
    struct ListNode *slow = head;
    for (struct ListNode *fast = head->next; fast && fast->next;
         fast = fast->next->next)
        slow = slow->next;
    struct ListNode *mid = slow->next;
    slow->next = NULL;
    return mergeTwoLists(mergesort_list(head), mergesort_list(mid));
}
```

遞迴深度 $\lceil \log_2 n \rceil$，一百萬個節點也只有 20 層，不用擔心 stack（對照 [Ch 13](./13-recursion.md)）。弱點是每一層都要把子串列走一遍找中點，累計多出 $O(n \log n)$ 次指標追蹤，而指標追蹤正是鏈結串列在快取上的痛點。

### 迭代（bottom-up）：二進位計數器

想像一個二進位計數器：`pending[i]` 要嘛是空的，要嘛恰好存一條 $2^i$ 個節點的已排序串列。每拿一個新節點就像計數器加 1——遇到非空的 `pending[i]` 就合併並「進位」到 `pending[i+1]`，直到碰到空格：

```c
static struct ListNode *mergesort_iter(struct ListNode *p)
{
    struct ListNode *pending[64] = {NULL}; /* pending[i]: 2^i 個節點或空 */
    while (p) {
        struct ListNode *carry = p;
        p = p->next;
        carry->next = NULL;
        size_t i = 0;
        for (; pending[i]; i++) {
            carry = mergeTwoLists(pending[i], carry);
            pending[i] = NULL;
        }
        pending[i] = carry;
    }
    struct ListNode *result = NULL;
    for (size_t i = 0; i < 64; i++)
        if (pending[i])
            result = mergeTwoLists(pending[i], result);
    return result;
}
```

處理 5 個節點（e1..e5）時的狀態：

```
加入 e1：count=1 (001)  pending[0]=[e1]
加入 e2：count=2 (010)  [e1]+[e2] 進位 → pending[1]=[e1 e2]
加入 e3：count=3 (011)  pending[0]=[e3]  pending[1]=[e1 e2]
加入 e4：count=4 (100)  [e3]+[e4] → [e3 e4]，再與 pending[1] 合併 → pending[2]=[e1..e4]
加入 e5：count=5 (101)  pending[0]=[e5]  pending[2]=[e1..e4]
收尾：由小到大合併 → [e1..e5]
```

三個關鍵性質：

- **64 格一定夠**：已處理 $k$ 個節點時，非空的 `pending` 恰好是 $k$ 的二進位表示中為 1 的位元。用到 `pending[64]` 需要 $2^{64}$ 個節點，任何放得進 64 位元定址空間的串列都不可能，所以不需要動態配置。
- **穩定**：越高位的 `pending` 收的是越早的節點，二處合併都把較早的串列放在 `mergeTwoLists` 的第一個參數，配合 `<=` 就穩定。
- **只循序走訪輸入一次**，不用找中點。GCC libstdc++ 的 `std::list::sort`（[`bits/list.tcc`](https://github.com/gcc-mirror/gcc/blob/master/libstdc++-v3/include/bits/list.tcc)）就是這個演算法，以 `__tmp[64]` 存放待合併串列，註解說它 "largely unchanged from the SGI STL"。

### 實測：正確性、穩定性與比較次數

測試程式給每個節點一個 `seq` 欄位記錄原始次序，鍵值取 `rand() % (n/10+1)` 刻意製造大量重複，`check()` 同時驗證「已排序」與「相等鍵值的 `seq` 遞增」（即穩定）：

```
$ gcc -O2 -Wall -Wextra msort.c -o msort && ./msort 1028 && ./msort 1000000
top-down  n=1028 ok=1 cmp=8991 time=0 ms
bottom-up n=1028 ok=1 cmp=9907 time=0 ms
top-down  n=1000000 ok=1 cmp=18674351 time=323 ms
bottom-up n=1000000 ok=1 cmp=18716209 time=303 ms
```

（節點配置在連續陣列並依序串起，時間只是這台機器的單次量測。ASan/UBSan 版本以 $n = 10^5$ 跑過，無錯誤。）

注意 $n = 1028$ 這一行：bottom-up 多了約 10% 的比較。原因是 $1028 = 1024 + 4$，計數器版收尾時要把 1024 個節點的串列和 4 個節點的串列合併，極度不平衡；top-down 每層對半切，不會有這個問題。$n = 10^6$ 時差距縮小到 0.2%，而 bottom-up 因為不必找中點反而較快。下一章的 Linux `list_sort` 正是要同時拿到二者的優點：保留 bottom-up 的「不找中點、不遞迴」，又把合併的不平衡限制在 2:1 以內。

### natural merge sort（以 run 為單位）

另一個迭代思路：輸入中已經遞增的區段（run）本身就是排序好的子串列，不必切到單一節點。先掃一遍把 run 剪開放進陣列，再用上一節 LeetCode 23 的「分段合併」：$r$ 個 run 要 $\lceil \log_2 r \rceil$ 輪。已排序的輸入只有一個 run，掃一次就結束。代價是要 $O(r)$ 的陣列，最差（遞減輸入）$r = n$，而且 `malloc` 可能失敗——計數器版沒有這個問題。

## 對比與取捨

| 寫法 / 演算法 | 優點 | 缺點 | 適用情境 |
|---|---|---|---|
| 教科書 `prev` 版 | 直白，初學者好懂 | 開頭特例，容易漏邊界 | 教學、一次性程式 |
| dummy node | 消除特例，好理解 | 多一個節點大小的暫存；欄位無意義 | 節點小、團隊不熟指標的指標 |
| 指標的指標 | 消除特例，零額外空間 | 讀者要能在腦中解兩層間接 | 核心、函式庫、長期維護的程式 |
| 尾端欄位指標 `**tail` | 連續 append $O(1)$ | 刪除尾端節點後要記得更新 | 建構串列、佇列 |
| Floyd | $O(1)$ 空間，可找入口 | 每輪移動三次指標 | 偵測環 + 找入口 |
| Brent | 每步只移一個指標，直接得 $\lambda$ | 找入口要多一趟 | 只要環長、Pollard rho |
| top-down merge sort | 合併最平衡，比較次數少 | 每層找中點，多 $O(n \log n)$ 次走訪 | 已知長度（可直接走到中點） |
| bottom-up 計數器 | 不遞迴、不找中點、固定 64 格 | 長度略大於 2 的冪時收尾不平衡 | libstdc++ `std::list::sort` |

## 踩雷集錦

1. **「指標的指標比較快」**：錯。gcc `-O2` 下二個版本的迴圈指令完全相同（見上面的組合語言）。它的價值在消除特例、降低出錯機率，不在效能。
2. **以為 `prev->next` 和 `*indir` 是不同東西**：當 `indir == &prev->next` 時它們是同一個物件。先改其中一個，再用另一個讀，讀到的已經是新值——LeetCode 2095 的 UAF 就是這樣來的。原則：**要釋放的節點先存進區域變數，再改指標，最後釋放。**
3. **重排串列後忘記截斷尾端**：partition、split 之類的操作，最後一個節點的 `next` 還指著舊鄰居，不設 `NULL` 就成環，走訪變無窮迴圈。
4. **`&(cond ? a : b)`**：C 的條件運算結果不是 lvalue，不能取位址；要寫 `cond ? &a : &b`。從 C++ 搬程式碼過來特別容易踩到。
5. **函式取名 `remove`、`mergesort`**：前者與 `<stdio.h>` 衝突，後者在 BSD/macOS 的 `<stdlib.h>` 衝突；標準函式庫的外部名稱一律保留給實作。
6. **在 Floyd 第一階段只檢查 `fast`**：`fast->next->next` 需要 `fast` 與 `fast->next` 都非空，二個都要檢查，否則奇數長度的無環串列會解參考 `NULL`。
7. **merge 用 `<` 寫**：結果仍然有序，但不穩定。依多個鍵排序（先依次要鍵排、再依主要鍵排）時答案就錯了。

## 進階：再往深一層

- **hlist 的 `pprev`**：Linux 核心雜湊表用的 `struct hlist_node` 有一個 `struct hlist_node **pprev`，指向「前一個節點的 `next` 或 bucket 的 `first`」。刪除時寫 `*pprev = next` 即可，正是本章的間接指標手法被寫進資料結構本身。下一章會看到它的定義。
- **`kernel/notifier.c`**：`notifier_chain_register()` 以 `struct notifier_block **nl` 走訪，找到插入位置後直接改寫 `*nl`，是核心中最乾淨的教科書級實例，建議打開來讀。
- **Linux 的 RCU 分段回呼串列**：`include/linux/rcu_segcblist.h` 的 `struct rcu_segcblist` 用 `tails[]` 中四個間接指標，把同一條單向串列依 grace period 切成四段。每一段的「尾端欄位位址」就是下一段的起點，可以當作本章尾端指標技巧的進階練習。
- **CPython `functools.lru_cache`**：`Modules/_functoolsmodule.c` 以雜湊表加上內嵌哨兵 `root` 的雙向環狀串列實作 LRU（LeetCode 146 的標準解）。
- **面試常見追問**：「Floyd 第二階段為什麼一定在入口相遇？」——答案就是 $t$ 是 $\lambda$ 的倍數，所以 $x_{t+\mu} = x_\mu$。能把這句推出來，比背程式碼有用得多。

## 動手練習

1. 把 `remove_indirect` 改成「移除所有值為 `v` 的節點並 `free`」。提示：找到時不要前進 `pp`（因為 `*pp` 已經換成下一個節點），沒找到才前進。用 ASan 驗證沒有洩漏與 UAF。
2. 寫 `void reverse(Node **head)`，然後故意寫一個在 `next` 被改寫後才讀 `next` 的錯誤版本，觀察它怎麼斷掉串列。
3. 在 `msort.c` 裡把 `mergeTwoLists` 的 `<=` 改成 `<`，確認 `check()` 回報 `ok=0`（不穩定）。
4. 用 `find_cycle` 偵測 partition 忘記截斷所產生的環，印出 $\mu$ 與 $\lambda$，再把環剪開。對 `[1, 4, 3, 2, 5, 2]`、`x = 3` 你預期 $\mu$ 與 $\lambda$ 是多少？先手算再跑。
5. 實作 natural merge sort（以 run 為單位），比較它與計數器版在「已排序」「完全反序」「隨機」三種輸入下的比較次數。

## 本章重點整理

- `head` 與每個 `next` 型別相同，所以一個 `Node **` 可以指向任一個；以它代表「要改寫的位置」，開頭特例就消失了。
- `*pp` 是 lvalue（C11 6.5.3.2 §4），所以能被寫入；條件運算的結果不是 lvalue，不能取位址。
- 間接指標的二大用途：消除開頭特例、記住尾端欄位讓 append 變 $O(1)$。
- aliasing 會咬人：`prev->next` 與 `*indir` 可能是同一個物件，先存要釋放的節點再改指標。
- Floyd 的關鍵是相遇時 $t$ 是 $\lambda$ 的倍數；Brent 每步只動一個指標並直接得 $\lambda$。
- 鏈結串列 merge sort 不需暫存陣列；bottom-up 計數器版用 64 格就涵蓋任何串列，但長度略大於 2 的冪時收尾不平衡。

## 自我檢核

- [ ] 不看書，畫出 `remove_indirect` 刪除開頭節點與刪除尾端節點時 `indirect` 每一步指向哪裡。
- [ ] 如果面試官說「用 dummy node 不就好了，為什麼要指標的指標？」，你怎麼回答二者的取捨？
- [ ] 解釋 LeetCode 2095 直覺版為什麼 `free` 到錯的節點，不用「因為 ASan 說」當理由。
- [ ] 用自己的話推導 Floyd 第二階段的步數為什麼恰好是 $\mu$。
- [ ] 說明 bottom-up 計數器版為什麼 `pending[64]` 永遠夠用，以及 $n = 1028$ 時它比 top-down 多比較的原因。
- [ ] 說出至少一個情境，你**不會**選指標的指標寫法（例如團隊可讀性、節點有父指標的雙向結構）。

## 延伸閱讀

### 論文

- **[An improved Monte Carlo factorization algorithm](https://maths-people.anu.edu.au/~brent/pd/rpb051i.pdf)** — Richard P. Brent，BIT 20（1980）
  - **核心貢獻**：提出瞬移烏龜的環偵測法，並用它加速 Pollard's rho 分解。
  - **讀哪裡**：Section 2–3（cycle-finding 的演算法與平均步數分析）；數論部分可跳過。
  - **前提**：本章 Floyd 一節；知道期望值即可。
  - **和本章的關聯**：本章 `brent_lambda` 的出處，以及「平均約快 36%」這個數字的來源。

### 部落格 / 技術文章

- **[Linked lists, pointer tricks and good taste](https://github.com/mkirchner/linked-list-good-taste)** — Michael Kirchner
  - **這篇說什麼**：把 Linus 的 TED 範例完整展開成可編譯的程式，並延伸到插入操作；另有 [felipec 的 fork](https://github.com/felipec/linked-list-good-taste) 改成更貼近核心的寫法。
  - **讀哪裡**：README 全文，約 15 分鐘。
  - **為什麼值得讀**：圖解 `indirect` 每一步的位置，和本章「逐步畫圖」互相印證。
- **[分析「快慢指標」](https://hackmd.io/@sysprog/fast-slow-pointer)** — jserv（sysprog）
  - **這篇說什麼**：快慢指標與二趟走訪的組合語言對照，x86-64 與 Arm64 實測，以及 cache miss 計數。
  - **讀哪裡**：關鍵路徑（critical path）的分析段落。
  - **前提**：[Ch 15](./15-memory-hierarchy.md) 的快取概念；會讀簡單的 x86-64 組合語言。
- **[Merge Sort 與它的變化](https://hackmd.io/@lambert-wu/list-merge-sort)** — Lambert Wu
  - **這篇說什麼**：鏈結串列 merge sort 的多種變形與比較次數實驗。
  - **讀哪裡**：bottom-up 與 natural merge sort 的段落。
  - **和本章的關聯**：把本章的 top-down 與計數器版推廣，也為下一章的 `list_sort` 鋪路。

### 官方文件 / Spec

- **[ISO/IEC 9899:2011 草案 N1570](https://www.open-std.org/jtc1/sc22/wg14/www/docs/n1570.pdf)**
  - **讀哪裡**：6.5.3.2（`&` 與 `*`，§1 與 §4）、6.5.15（條件運算子及註腳 110）、6.2.4 §2（物件生命期）、7.1.3（保留識別字）。
  - **注意**：本章引用的都是很短的條文，直接翻到那一段讀原文，比讀任何二手解釋都準。

### 書籍

- **《The Art of Computer Programming, Vol. 2: Seminumerical Algorithms》（3rd ed.）** — Donald Knuth（Addison-Wesley，1997）
  - **這本書的定位**：Floyd 演算法目前所知最早的印刷紀錄就在這裡。
  - **讀哪幾章**：3.1 節習題 6（環偵測），配合解答閱讀；其餘是亂數理論，可選。
- **《Computer Systems: A Programmer's Perspective》（3rd ed.）** — Bryant & O'Hallaron（Pearson，2015）
  - **讀哪幾章**：第 6 章（記憶體階層），理解為什麼鏈結串列走訪在快取上吃虧。

教科書式的串列把資料放進節點；Linux 核心反過來把節點放進資料，並用 `container_of` 從節點找回資料。下一章把本章的間接指標、哨兵、bottom-up merge sort 全部串起來，看它們在 `include/linux/list.h` 與 `lib/list_sort.c` 裡長什麼樣子。

→ [Ch 19 Linux list_head、container_of、list_sort 與洗牌](./19-linux-list-head.md)
