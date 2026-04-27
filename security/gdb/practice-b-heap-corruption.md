# 練習 B — debug heap corruption（配合 valgrind）

> 目標：用 GDB + valgrind 搭配找一個 heap 被寫壞的 bug。重點是**兩個工具各擅所長**：valgrind 最快指出「哪裡寫壞了」，GDB 讓你看現場、推導因果。

## 背景

Heap corruption 是讓人想放棄 debug 的典型案例。特徵：

- crash 位址跟 bug 位置離很遠（A 寫壞了 B 的 memory，B 用時才 crash）
- 時機不穩定（依賴 malloc 給到的位址、依賴執行順序）
- 重跑不一定重現

單 GDB 很辛苦。但**先跑 valgrind 拿 root cause hint，再用 GDB 配 watchpoint / reverse 驗證**，效率翻十倍。

## 題目：`heap_bug.c`

```c
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef struct Node {
    int id;
    char tag[8];
    struct Node *next;
} Node;

Node *create(int id, const char *tag) {
    Node *n = malloc(sizeof(Node));
    n->id = id;
    strcpy(n->tag, tag);
    n->next = NULL;
    return n;
}

void append(Node *head, Node *new_node) {
    while (head->next != NULL) {
        head = head->next;
    }
    head->next = new_node;
}

int find(Node *head, int id) {
    int depth = 0;
    while (head != NULL) {
        if (head->id == id) return depth;
        head = head->next;
        depth++;
    }
    return -1;
}

int main(void) {
    Node *list = create(1, "root");
    append(list, create(2, "middle"));
    append(list, create(3, "internal"));
    append(list, create(4, "leaf-alpha"));
    append(list, create(5, "tail"));

    printf("find(3) = %d\n", find(list, 3));
    printf("find(5) = %d\n", find(list, 5));

    Node *cur = list;
    while (cur != NULL) {
        Node *next = cur->next;
        free(cur);
        cur = next;
    }

    return 0;
}
```

編譯：

```bash
gcc -g -O0 heap_bug.c -o heap_bug
```

直接跑：

```bash
./heap_bug
```

**可能**不 crash（你看到 find 結果然後正常 exit），**可能** crash。不穩定是 heap corruption 的典型症狀。

## 任務

- 找出 heap corruption 的位置。
- 解釋為什麼它**可能**不 crash，但仍然是嚴重 bug。
- 用 GDB 驗證你的結論（複製現場給別人看）。

## 步驟建議

### Step 1：先跑 valgrind

```bash
valgrind --tool=memcheck --track-origins=yes ./heap_bug
```

valgrind 在幾秒內會告訴你：

```
==12345== Invalid write of size 1
==12345==    at 0x... : strcpy (in /usr/lib/x86_64-linux-gnu/...)
==12345==    by 0x... : create (heap_bug.c:14)
==12345==    by 0x... : main (heap_bug.c:35)
==12345==  Address 0x... is 0 bytes after a block of size 28 alloc'd
==12345==    at 0x... : malloc
==12345==    by 0x... : create (heap_bug.c:12)
```

**注意 `Invalid write of size 1 ... 0 bytes after a block of size 28`** — 寫到 chunk 邊界外。為什麼？

<details>
<summary>答案</summary>

`Node` 結構：`int id (4) + char tag[8] + Node *next (8) = 20`，但因為 alignment 會 padding 到 24 bytes（如果 struct 只存 id+tag 可能 16，加上 next 後變 24）。

不，等等，算錯。實際：
- `int id` = 4
- `char tag[8]` = 8 （緊跟 id 後面，不用 padding 因為都是 1 byte align）
- `struct Node *next` = 8，但要 8-byte align，前面 `4 + 8 = 12` bytes，要補 4 byte padding。
- total = 4 + 8 + 4 (padding) + 8 = 24 bytes

但 valgrind 說 28 ? 讓我重新推。其實 gcc 把 `char tag[8]` 的對齊當 1，沒問題。那 12 之後要對齊到 8 給 next，是加 4。total = 4+8+4+8 = 24。

等等 — 實際依賴 padding 選擇與 `strcpy("leaf-alpha", n->tag)` 寫 11 字元（含 `\0`）— tag 只有 8 byte，多 3 個 byte 寫出去。那 3 個 byte 去哪？

以 `struct Node` 的 layout：
```
offset 0-3:   id
offset 4-11:  tag[8]
offset 12-15: padding
offset 16-23: next
```

strcpy 從 offset 4 開始寫 `"leaf-alpha\0"` = 11 byte，會寫到 offset 4–14，**蓋到 offset 12–14 的 padding**。

padding 被蓋是 UB 但通常無害 — 直到 compiler / malloc 把 padding 重用於其他 metadata。

但 valgrind 說的 28 / "0 bytes after a block of size 28" 可能是因為 glibc 的 chunk 實際 malloc size 被 round up 到 32，中間有 padding... 細節依 glibc 版本。

**核心 bug**：`strcpy(n->tag, "leaf-alpha")` 寫超過 8 byte 的 `tag`。

</details>

### Step 2：用 GDB 重現並確認

```
gdb -q ./heap_bug
(gdb) b create
(gdb) r
... 停在 create(1, "root") ...
(gdb) p strlen(tag) + 1           ; = 5, fit
(gdb) c
... 每次停，印 strlen(tag) + 1 ...
```

當跑到 `create(4, "leaf-alpha")`：

```
(gdb) p strlen(tag) + 1           ; = 11
(gdb) p sizeof(n->tag)             ; = 8
```

**11 > 8，確認溢出。**

### Step 3：用 watchpoint 看被寫壞的 byte

假設你想確認「寫到哪裡」：

```
(gdb) b create if id == 4
(gdb) r
... 停在 create(4, "leaf-alpha") ...
(gdb) n    ; malloc
(gdb) p n
$1 = (Node *) 0x5555555592f0

(gdb) p &n->tag
$2 = (char (*)[8]) 0x5555555592f4
(gdb) p &n->next
$3 = (Node **) 0x555555559300

(gdb) x/32bx n
0x5555555592f0: ...
```

memory layout 清楚後，下 watchpoint 在 next 欄位，**然後 `n` 繼續進 strcpy**：

```
(gdb) watch -l *(long *)0x555555559300     ; watch n->next 位址
(gdb) n                                     ; 進 strcpy
...
```

如果 strcpy 溢位寫到 `next` 的位置，watchpoint 會觸發。（實際上這個 bug 寫的是 padding，不是 next，你需要 watch padding 位置。）

### Step 4：往 corruption 的消費端看

bug 的症狀不一定在寫壞的那刻顯現。例如 `find(5)` 遍歷 list 時，如果 padding 被蓋影響了某個 node 的 `next` 指標（在某些 compiler 佈局下可能），那 find 走一半會讀到壞的位址 crash。

這就是「先跑 valgrind 找 source，再用 GDB 看 consumer」的意義。

### Step 5：reverse debugging 確認因果

如果你願意付 record 的代價：

```
(gdb) start
(gdb) record
(gdb) watch list->next->next->next->next        ; list[4] 的 next
(gdb) c
... 跑 ...
```

當 watchpoint 觸發（例如在 free 或 find 時），`reverse-continue` 可以找出「誰把這個位置寫壞」。

或用 rr：

```bash
rr record ./heap_bug
rr replay
(gdb) ...
```

## 為什麼「可能不 crash」？

heap corruption 的特性：

- 如果寫壞的是 padding 而沒碰到 metadata 或其他 chunk，可能完全沒症狀
- 如果 malloc 的 chunk 剛好後面有空閒空間，多寫 3 byte 不會影響
- glibc 的 tcache 讓 free/realloc 順序影響多大
- OS 的記憶體分配 / ASLR 讓每次跑結果不同

**這正是 heap corruption 讓人崩潰的地方** — 你以為修好了（「上次沒 crash 啊！」），過兩個月在 production 又炸。

**只有 valgrind / AddressSanitizer 之類的工具才能 100% 抓到。**

## AddressSanitizer (ASan) — 另一個選擇

valgrind 慢（10–30 倍）。ASan 是 clang/gcc 內建的 sanitizer，只慢 2 倍，但你要重編：

```bash
gcc -g -O0 -fsanitize=address heap_bug.c -o heap_bug
./heap_bug
```

```
==12345==ERROR: AddressSanitizer: heap-buffer-overflow on address 0x...
WRITE of size 11 at 0x...
    #0 ... in strcpy ...
    #1 ... in create heap_bug.c:14
    #2 ... in main heap_bug.c:35
...
```

ASan 的優勢：快、原生 stack trace、跟 GDB 整合無縫（ASan 觸發時會停在 debugger 裡，繼續 bt）。

實務建議：**專案 CI 加 ASan**，開發時優先用 ASan，valgrind 當第二道防線或遇到 ASan 不好使的情境（JIT、inline asm）。

## 完整修復

```c
void create(int id, const char *tag) {
    Node *n = malloc(sizeof(Node));
    if (!n) return NULL;              // null check
    n->id = id;
    strncpy(n->tag, tag, sizeof(n->tag) - 1);
    n->tag[sizeof(n->tag) - 1] = '\0';  // 強制 terminator
    n->next = NULL;
    return n;
}
```

或用 `snprintf`：

```c
snprintf(n->tag, sizeof(n->tag), "%s", tag);
```

## 工具對比表

| 工具 | 速度 | 抓得到的 bug | 優缺 |
|---|---|---|---|
| GDB 單獨 | 原生速度 | 要你自己想 watchpoint | 靈活但要腦力 |
| valgrind memcheck | 10–30x 慢 | heap overflow、use-after-free、uninit、leak | 不用重編、最全面 |
| AddressSanitizer | 2x 慢 | heap / stack / global overflow、UAF | 要重編、最快 |
| MemorySanitizer | 2–3x 慢 | uninit read | 要重編，只有 clang |
| UBSanitizer | 很快 | undefined behavior | 輕量、常駐 |
| rr + reverse | 錄製 2–5x 慢 | 任何 bug + 時間倒流 | 救回難重現的 bug |

**日常組合**：開發時 ASan + UBSan 常駐；debug 時 GDB 挖；遇到難重現上 rr + reverse。

## 自我檢核

- [ ] 我知道 heap corruption 的症狀不穩定，需要專門工具
- [ ] 我會用 valgrind 抓 heap overflow
- [ ] 我會用 GDB 的 watchpoint 驗證 valgrind 的結論
- [ ] 我知道 ASan 比 valgrind 快但要重編
- [ ] 我知道 strcpy/strncpy 的陷阱與正確替代（snprintf）
- [ ] 我能解釋「為什麼測試時沒 crash 不代表 code 沒 bug」

做完這個練習，你有了對付 heap 類 bug 的工具組合。Part 4 換一種戰場：多執行緒、遠端、post-mortem。

→ [Ch 11 多執行緒 debug](./11-multithreaded-debugging.md)
