# Ch 29 — 面試手寫題 20 道

> 目標：在白板或電腦上快速寫出正確、乾淨的 C 實作，涵蓋指標操作、字串、資料結構、位元操作。

---

## 字串操作

### Q1：實作 strlen

```c
size_t my_strlen(const char *s) {
    const char *p = s;
    while (*p) p++;
    return (size_t)(p - s);
}
```

面試注意：`s` 不能是 NULL（UB），若 interviewer 問就說「呼叫方確保非 NULL，或加 `if (!s) return 0;`」。

---

### Q2：實作 strcpy

```c
char *my_strcpy(char *dst, const char *src) {
    char *d = dst;
    while ((*d++ = *src++)) ;   // 賦值 + 測試，很多人愛考
    return dst;
}
```

常見追問：「如果 dst 和 src overlap？」→ 行為未定義（用 memmove）。

---

### Q3：實作 strrev（原地反轉字串）

```c
char *strrev(char *s) {
    char *l = s, *r = s + strlen(s) - 1;
    while (l < r) {
        char t = *l;
        *l++ = *r;
        *r-- = t;
    }
    return s;
}
```

---

### Q4：實作 atoi（字串轉整數）

```c
int my_atoi(const char *s) {
    while (*s == ' ') s++;   // 跳過空白

    int sign = 1;
    if (*s == '-') { sign = -1; s++; }
    else if (*s == '+') { s++; }

    int result = 0;
    while (*s >= '0' && *s <= '9') {
        result = result * 10 + (*s - '0');
        s++;
        // 完整版本要檢查溢位
    }
    return sign * result;
}
```

---

### Q5：判斷字串是否為回文

```c
int is_palindrome(const char *s) {
    int l = 0, r = (int)strlen(s) - 1;
    while (l < r)
        if (s[l++] != s[r--]) return 0;
    return 1;
}
```

---

## 鏈表

### Q6：反轉單向鏈表

```c
typedef struct Node { int val; struct Node *next; } Node;

Node *reverse_list(Node *head) {
    Node *prev = NULL, *cur = head, *next;
    while (cur) {
        next     = cur->next;
        cur->next = prev;
        prev     = cur;
        cur      = next;
    }
    return prev;   // 新的 head
}
```

---

### Q7：偵測鏈表環（Floyd's algorithm）

```c
int has_cycle(Node *head) {
    Node *slow = head, *fast = head;
    while (fast && fast->next) {
        slow = slow->next;
        fast = fast->next->next;
        if (slow == fast) return 1;
    }
    return 0;
}
```

---

### Q8：找鏈表的中間節點

```c
Node *find_middle(Node *head) {
    Node *slow = head, *fast = head;
    while (fast && fast->next) {
        slow = slow->next;
        fast = fast->next->next;
    }
    return slow;   // 偶數長度返回後半段的第一個
}
```

---

### Q9：合并兩個有序鏈表

```c
Node *merge_sorted(Node *a, Node *b) {
    Node dummy = {0, NULL}, *tail = &dummy;
    while (a && b) {
        if (a->val <= b->val) { tail->next = a; a = a->next; }
        else                  { tail->next = b; b = b->next; }
        tail = tail->next;
    }
    tail->next = a ? a : b;
    return dummy.next;
}
```

---

### Q10：實作 Stack（使用陣列）

```c
#define STACK_MAX 256
typedef struct {
    int  data[STACK_MAX];
    int  top;
} Stack;

void stack_init(Stack *s)          { s->top = -1; }
int  stack_empty(const Stack *s)   { return s->top == -1; }
int  stack_full(const Stack *s)    { return s->top == STACK_MAX - 1; }
void stack_push(Stack *s, int v)   { if (!stack_full(s))  s->data[++s->top] = v; }
int  stack_pop(Stack *s)           { return stack_empty(s) ? -1 : s->data[s->top--]; }
int  stack_peek(const Stack *s)    { return stack_empty(s) ? -1 : s->data[s->top]; }
```

---

## 位元操作

### Q11：計算整數有多少個 1（popcount）

```c
int popcount(unsigned int x) {
    int count = 0;
    while (x) {
        x &= x - 1;   // 清除最低位的 1
        count++;
    }
    return count;
}
// 生產代碼用：__builtin_popcount(x)
```

---

### Q12：判斷是否為 2 的冪次

```c
int is_power_of_2(unsigned int n) {
    return n != 0 && (n & (n - 1)) == 0;
}
```

---

### Q13：找第一個缺少的正整數（用 bit manipulation）

```c
// 輸入：1-N 範圍內有一個數字缺失，找出它
int find_missing(int *arr, int n) {
    int expected_sum = n * (n + 1) / 2;
    int actual_sum = 0;
    for (int i = 0; i < n - 1; i++)
        actual_sum += arr[i];
    return expected_sum - actual_sum;
}
```

---

### Q14：兩個數 XOR 找出唯一不重複的數

```c
// 陣列中所有數出現兩次，只有一個出現一次：
int single_number(int *arr, int n) {
    int result = 0;
    for (int i = 0; i < n; i++) result ^= arr[i];
    return result;   // 相同的 XOR 消掉，剩下唯一的那個
}
```

---

### Q15：旋轉整數的 bits

```c
uint32_t rotate_left(uint32_t x, int n) {
    return (x << n) | (x >> (32 - n));
}
// 注意：若 n == 0，(x >> 32) 是 UB（移位量 >= 型別寬度）
// 安全版本：
uint32_t rotate_left_safe(uint32_t x, int n) {
    n &= 31;   // n 只取低 5 bits（0-31 範圍）
    return n ? (x << n) | (x >> (32 - n)) : x;
}
```

---

## 記憶體與指標

### Q16：實作 memcpy

```c
void *my_memcpy(void *dst, const void *src, size_t n) {
    char       *d = (char *)dst;
    const char *s = (const char *)src;
    while (n--) *d++ = *s++;
    return dst;
    // 不處理 overlap（用 memmove 處理 overlap）
}
```

---

### Q17：實作 memmove（支援 overlap）

```c
void *my_memmove(void *dst, const void *src, size_t n) {
    char       *d = (char *)dst;
    const char *s = (const char *)src;
    if (d < s || d >= s + n) {
        while (n--) *d++ = *s++;          // 向前複製
    } else {
        d += n; s += n;
        while (n--) *--d = *--s;          // 向後複製（避免覆蓋未複製的源）
    }
    return dst;
}
```

---

### Q18：二分搜尋

```c
int binary_search(const int *arr, int n, int target) {
    int lo = 0, hi = n - 1;
    while (lo <= hi) {
        int mid = lo + (hi - lo) / 2;   // 用 (hi-lo)/2 而非 (lo+hi)/2，防止溢位
        if      (arr[mid] == target) return mid;
        else if (arr[mid] < target)  lo = mid + 1;
        else                          hi = mid - 1;
    }
    return -1;
}
```

---

### Q19：用兩個 stack 實作 queue

```c
typedef struct {
    Stack in, out;
} Queue;

void enqueue(Queue *q, int v) { stack_push(&q->in, v); }

int dequeue(Queue *q) {
    if (stack_empty(&q->out)) {
        while (!stack_empty(&q->in))
            stack_push(&q->out, stack_pop(&q->in));
    }
    return stack_pop(&q->out);
}
```

---

### Q20：實作 hash table（鏈式）

```c
#define HASH_BUCKETS 64

typedef struct HNode { char *key; int val; struct HNode *next; } HNode;
typedef struct { HNode *buckets[HASH_BUCKETS]; } HashMap;

static size_t hash(const char *key) {
    size_t h = 5381;
    while (*key) h = (h << 5) + h + (unsigned char)*key++;
    return h % HASH_BUCKETS;
}

void hm_put(HashMap *m, const char *key, int val) {
    size_t h = hash(key);
    for (HNode *n = m->buckets[h]; n; n = n->next) {
        if (strcmp(n->key, key) == 0) { n->val = val; return; }
    }
    HNode *node = malloc(sizeof(HNode));
    node->key   = strdup(key);
    node->val   = val;
    node->next  = m->buckets[h];
    m->buckets[h] = node;
}

int hm_get(const HashMap *m, const char *key, int *out) {
    size_t h = hash(key);
    for (HNode *n = m->buckets[h]; n; n = n->next)
        if (strcmp(n->key, key) == 0) { *out = n->val; return 1; }
    return 0;
}
```

---

## 自我檢核

- [ ] Q6（反轉鏈表）能流暢寫出，不看答案
- [ ] Q7（Floyd's cycle detection）能解釋為什麼快慢指針一定會相遇
- [ ] Q17（memmove）能解釋為什麼要分向前/向後複製
- [ ] Q20（hash table）能解釋 load factor 和 rehash 的關係

→ [Ch 30 C 語言系統設計題](./30-system-design-c.md)
