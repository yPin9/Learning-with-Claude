# Ch 1 — 記憶體分區：text / data / BSS / heap / stack

> 目標：能從一段 C 程式，指出每個變數在哪個記憶體區段，以及各區段的生命週期和存取權限。

## 程式的記憶體佈局

```
高位址
┌─────────────────┐
│   kernel space  │  使用者程式不能直接存取
├─────────────────┤
│      stack      │  往低位址成長 ↓
│    (grows ↓)    │  區域變數、函式參數、返回地址
├ ─ ─ ─ ─ ─ ─ ─ ┤
│    （未使用）    │
├ ─ ─ ─ ─ ─ ─ ─ ┤
│      heap       │  往高位址成長 ↑
│    (grows ↑)    │  malloc/calloc 分配
├─────────────────┤
│   BSS segment   │  未初始化的全域/靜態變數（執行期清零）
├─────────────────┤
│   data segment  │  已初始化的全域/靜態變數（可讀寫）
├─────────────────┤
│   text segment  │  機器碼 + 字串常數（唯讀 + 可執行）
└─────────────────┘
低位址（0x400000...）
```

---

## 各區段詳解

### Text Segment（程式碼區）

存放機器碼和字串常數（如 `"hello"`）。**唯讀 + 可執行**。

```c
const char *p = "hello";
p[0] = 'H';   // UB：通常直接 SIGSEGV，"hello" 在唯讀的 text segment
```

### Data Segment（初始化資料區）

存放已初始化的全域和靜態變數，可讀寫，程式執行期間持續存在。

```c
int  g = 42;           // data segment
static int sg = 100;   // data segment

void foo(void) {
    static int ls = 7; // data segment（靜態存儲期，不在 stack！）
}
```

### BSS Segment

存放未初始化（或初始化為 0）的全域/靜態變數。BSS 在可執行檔裡只記錄「這塊多大」，**不存實際零值資料**，所以 1 MB 的 BSS 不讓 .o 檔案變大。

```c
int  g_zero;              // BSS（OS 保證清零）
char big_buf[1024*1024];  // BSS（不佔 binary 空間）
```

### Heap（堆）

`malloc` / `calloc` / `realloc` 的領地，生命週期由程式員控制。

```c
int *p = malloc(100 * sizeof(int));
// 使用 p...
free(p);   // 必須手動釋放，否則 memory leak
p = NULL;  // 好習慣：避免 dangling pointer
```

### Stack（堆疊）

函式呼叫時自動建立，返回時自動銷毀。存放區域變數、函式參數、返回地址。

```c
void foo(int x) {       // x 在 stack
    int local = 1;      // local 在 stack
    int *p = malloc(4); // p（指標）在 stack，*p 在 heap
}   // foo 返回：x、local、p 全部消失；*p 仍在 heap（但無人管）
```

Stack 大小有限（Linux 預設 8 MB），用完會 SIGSEGV：

```c
void bad(void) {
    int huge[2*1024*1024];  // 8 MB，爆 stack
}
// 解法：static int huge[...] 或 malloc
```

---

## 面試常考：判斷每個變數的位置

```c
int g1 = 5;               // (1)
int g2;                   // (2)
static int g3 = 3;        // (3)
const char *msg = "hi";   // (4) msg 本身？"hi" 字串？

void foo(int param) {     // (5)
    int local = 1;        // (6)
    static int s = 2;     // (7)
    int *p = malloc(8);   // (8) p 本身？*p？
    const char *s2 = "world"; // (9) s2？"world"？
}
```

| 變數 | 位置 | 理由 |
|------|------|------|
| g1 | data | 已初始化全域 |
| g2 | BSS | 未初始化全域 |
| g3 | data | static + 已初始化 |
| msg（指標） | data | 已初始化全域指標 |
| "hi"（字串） | text | 字串常數，唯讀 |
| param | stack | 函式參數 |
| local | stack | 區域變數 |
| s | data | static + 已初始化，靜態存儲期 |
| p（指標） | stack | 區域指標變數 |
| \*p（8 bytes） | heap | malloc 分配 |
| s2（指標） | stack | 區域指標 |
| "world"（字串） | text | 字串常數，唯讀 |

---

## 用工具驗證

```bash
gcc -o demo demo.c

# 各段大小（bytes）
size demo
#   text    data     bss
#   1234     312    8200

# 各符號所在段（T=text, D=data, B=bss）
nm demo | grep -E " [TDBtdb] "
# 0000004010a0 T main
# 000000404030 D g1
# 000000404040 B g2
```

---

## 動手練習

```c
#include <stdio.h>
#include <stdlib.h>

int a = 1;     // 猜：data
int b;         // 猜：BSS
static int c = 2; // 猜：data

int main(void) {
    int d = 3;
    static int e = 4;
    int *f = malloc(sizeof(int));

    printf("&a=%p  &b=%p  &c=%p\n", (void*)&a, (void*)&b, (void*)&c);
    printf("&d=%p  &e=%p   f=%p\n", (void*)&d, (void*)&e, (void*)f);
    free(f);
    return 0;
}
```

觀察：a/b/c/e 的地址是否在相近範圍（data/BSS），d 和 stack pointer 是否接近，f（heap）是否在完全不同的範圍。

## 自我檢核

- [ ] 能說出五個區段的名稱、存的是什麼、生命週期
- [ ] 能判斷任意 C 宣告的變數在哪個區段
- [ ] 知道 BSS 不佔可執行檔空間的原因
- [ ] 知道 `static` 在函式內的含義（data/BSS，不是 stack）
- [ ] 能解釋 stack overflow 原理

→ [Ch 2 指標深度剖析](./02-pointers-deep.md)
