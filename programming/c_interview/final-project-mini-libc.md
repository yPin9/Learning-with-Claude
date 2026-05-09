# Final Project — mini-libc：從零實作 C 標準函式庫子集

> 目標：整合整套課程，從 Linux syscall 層出發，自己實作 `strlen`、`malloc`/`free`、`printf`、`qsort`、`setjmp`，最後編譯一個不依賴 glibc 的靜態 binary。

---

## 專案概覽

```
mini-libc/
├── include/
│   ├── mini_string.h      # strlen, strcpy, memcpy, memset, ...
│   ├── mini_stdlib.h      # malloc, free, calloc, realloc
│   ├── mini_stdio.h       # printf, putchar, puts
│   ├── mini_sort.h        # qsort
│   └── mini_setjmp.h      # setjmp, longjmp
├── src/
│   ├── syscall.h          # Linux syscall wrappers（write, brk, mmap）
│   ├── string.c
│   ├── malloc.c           # 簡化版 free-list allocator
│   ├── printf.c
│   ├── qsort.c
│   └── setjmp_x86_64.s   # x86-64 assembly 實作
├── test/
│   ├── test_string.c
│   ├── test_malloc.c
│   ├── test_printf.c
│   └── test_all.c
└── Makefile
```

---

## Step 1：Syscall Layer

不依賴 glibc，直接呼叫 Linux kernel：

```c
// src/syscall.h
#pragma once
#include <stddef.h>
#include <stdint.h>

// Linux x86_64 syscall numbers
#define SYS_write   1
#define SYS_brk     12
#define SYS_mmap    9
#define SYS_munmap  11
#define SYS_exit    60

static inline long syscall1(long nr, long a1) {
    long ret;
    __asm__ volatile (
        "syscall"
        : "=a"(ret)
        : "0"(nr), "D"(a1)
        : "memory", "rcx", "r11"
    );
    return ret;
}

static inline long syscall3(long nr, long a1, long a2, long a3) {
    long ret;
    __asm__ volatile (
        "syscall"
        : "=a"(ret)
        : "0"(nr), "D"(a1), "S"(a2), "d"(a3)
        : "memory", "rcx", "r11"
    );
    return ret;
}

static inline ssize_t sys_write(int fd, const void *buf, size_t n) {
    return (ssize_t)syscall3(SYS_write, fd, (long)buf, (long)n);
}

static inline void *sys_brk(void *addr) {
    return (void *)syscall1(SYS_brk, (long)addr);
}

static inline void sys_exit(int code) {
    syscall1(SYS_exit, code);
    __builtin_unreachable();
}
```

---

## Step 2：String Functions

```c
// src/string.c
#include "mini_string.h"

size_t mini_strlen(const char *s) {
    const char *p = s;
    while (*p) p++;
    return (size_t)(p - s);
}

void *mini_memcpy(void *dst, const void *src, size_t n) {
    char *d = (char *)dst;
    const char *s = (const char *)src;
    while (n--) *d++ = *s++;
    return dst;
}

void *mini_memset(void *s, int c, size_t n) {
    unsigned char *p = (unsigned char *)s;
    while (n--) *p++ = (unsigned char)c;
    return s;
}

int mini_strcmp(const char *a, const char *b) {
    while (*a && *a == *b) { a++; b++; }
    return (unsigned char)*a - (unsigned char)*b;
}

char *mini_strcpy(char *dst, const char *src) {
    char *d = dst;
    while ((*d++ = *src++)) ;
    return dst;
}
```

---

## Step 3：malloc / free

```c
// src/malloc.c
#include "mini_stdlib.h"
#include "syscall.h"

#define ALIGN(n) (((n) + 7) & ~(size_t)7)

typedef struct Block {
    size_t        size;
    int           free;
    struct Block *next;
} Block;

static void *heap_start = NULL;
static Block *free_list  = NULL;

static void heap_init(void) {
    // 從 OS 要 1 MB
    heap_start = sys_brk(NULL);
    void *end  = sys_brk((char *)heap_start + 1024 * 1024);
    if (end == heap_start) return;   // brk 失敗

    free_list       = (Block *)heap_start;
    free_list->size = (char *)end - (char *)heap_start;
    free_list->free = 1;
    free_list->next = NULL;
}

void *mini_malloc(size_t size) {
    if (!free_list) heap_init();
    if (!free_list) return NULL;

    size_t need = ALIGN(size) + sizeof(Block);
    for (Block *b = free_list; b; b = b->next) {
        if (!b->free || b->size < need) continue;
        if (b->size >= need + sizeof(Block) + 8) {
            Block *split = (Block *)((char *)b + need);
            split->size  = b->size - need;
            split->free  = 1;
            split->next  = b->next;
            b->size      = need;
            b->next      = split;
        }
        b->free = 0;
        return (char *)b + sizeof(Block);
    }
    return NULL;
}

void mini_free(void *ptr) {
    if (!ptr) return;
    Block *b = (Block *)((char *)ptr - sizeof(Block));
    b->free  = 1;
    while (b->next && b->next->free) {
        b->size += b->next->size;
        b->next  = b->next->next;
    }
}

void *mini_calloc(size_t nmemb, size_t size) {
    // 溢位保護：
    if (nmemb && size > (size_t)-1 / nmemb) return NULL;
    void *p = mini_malloc(nmemb * size);
    if (p) mini_memset(p, 0, nmemb * size);
    return p;
}
```

---

## Step 4：printf

```c
// src/printf.c
#include "mini_stdio.h"
#include "mini_string.h"
#include "syscall.h"
#include <stdarg.h>

static char out_buf[4096];
static int  out_pos = 0;

static void flush(void) {
    if (out_pos > 0) {
        sys_write(1, out_buf, (size_t)out_pos);
        out_pos = 0;
    }
}

static void emit(char c) {
    out_buf[out_pos++] = c;
    if (out_pos == (int)sizeof(out_buf)) flush();
}

static void emit_str(const char *s) {
    while (*s) emit(*s++);
}

static void emit_int(long long n, int base) {
    static const char digits[] = "0123456789abcdef";
    char buf[32];
    int  pos = 0;
    if (n < 0) { emit('-'); n = -n; }
    unsigned long long u = (unsigned long long)n;
    do { buf[pos++] = digits[u % (unsigned)base]; u /= (unsigned)base; } while (u);
    while (pos--) emit(buf[pos]);
}

int mini_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    for (const char *p = fmt; *p; p++) {
        if (*p != '%') { emit(*p); continue; }
        p++;
        switch (*p) {
            case 'd': emit_int(va_arg(ap, int), 10);           break;
            case 'u': emit_int((long long)(unsigned)va_arg(ap, unsigned), 10); break;
            case 'x': emit_int(va_arg(ap, unsigned), 16);      break;
            case 'l':
                p++;
                if (*p == 'd') emit_int(va_arg(ap, long), 10);
                else if (*p == 'u') emit_int((long long)va_arg(ap, unsigned long), 10);
                break;
            case 's': emit_str(va_arg(ap, const char *));      break;
            case 'c': emit((char)va_arg(ap, int));             break;
            case '%': emit('%');                                break;
        }
    }
    va_end(ap);
    flush();
    return 0;
}
```

---

## Step 5：qsort

使用 insertion sort + quicksort（introsort 的簡化版）：

```c
// src/qsort.c
#include "mini_stdlib.h"
#include <stdint.h>

static void swap_bytes(char *a, char *b, size_t size) {
    for (size_t i = 0; i < size; i++) {
        char t = a[i]; a[i] = b[i]; b[i] = t;
    }
}

static char *median3(char *a, char *b, char *c, size_t size,
                     int (*cmp)(const void *, const void *)) {
    if (cmp(a, b) < 0) {
        if (cmp(b, c) < 0) return b;
        if (cmp(a, c) < 0) return c;
        return a;
    }
    if (cmp(a, c) < 0) return a;
    if (cmp(b, c) < 0) return c;
    return b;
}

void mini_qsort(void *base, size_t nmemb, size_t size,
                int (*cmp)(const void *, const void *)) {
    if (nmemb < 2) return;
    char *arr = (char *)base;

    // 小陣列用 insertion sort
    if (nmemb < 8) {
        for (size_t i = 1; i < nmemb; i++) {
            for (size_t j = i; j > 0 && cmp(arr + (j-1)*size, arr + j*size) > 0; j--)
                swap_bytes(arr + (j-1)*size, arr + j*size, size);
        }
        return;
    }

    // Pivot：median-of-three
    char *mid   = arr + (nmemb / 2) * size;
    char *last  = arr + (nmemb - 1) * size;
    char *pivot = median3(arr, mid, last, size, cmp);
    swap_bytes(pivot, last, size);   // 把 pivot 移到最後

    // Partition
    char *store = arr;
    for (size_t i = 0; i < nmemb - 1; i++) {
        if (cmp(arr + i * size, last) < 0) {
            swap_bytes(arr + i * size, store, size);
            store += size;
        }
    }
    swap_bytes(store, last, size);

    size_t pivot_idx = (size_t)(store - arr) / size;
    mini_qsort(arr, pivot_idx, size, cmp);
    mini_qsort(store + size, nmemb - pivot_idx - 1, size, cmp);
}
```

---

## Step 6：setjmp（x86-64 Assembly）

```asm
# src/setjmp_x86_64.s
# int setjmp(jmp_buf env)
# jmp_buf 佈局：rbx(0), rbp(8), r12(16), r13(24), r14(32), r15(40), rsp(48), rip(56)

.global mini_setjmp
mini_setjmp:
    mov  %rbx, 0(%rdi)
    mov  %rbp, 8(%rdi)
    mov  %r12, 16(%rdi)
    mov  %r13, 24(%rdi)
    mov  %r14, 32(%rdi)
    mov  %r15, 40(%rdi)
    mov  %rsp, 48(%rdi)       # 當前 rsp
    mov  (%rsp), %rax         # 返回地址（rip）
    mov  %rax, 56(%rdi)
    xor  %eax, %eax           # 返回 0
    ret

.global mini_longjmp
mini_longjmp:
    # rdi = env, esi = val
    mov  0(%rdi),  %rbx
    mov  8(%rdi),  %rbp
    mov  16(%rdi), %r12
    mov  24(%rdi), %r13
    mov  32(%rdi), %r14
    mov  40(%rdi), %r15
    mov  48(%rdi), %rsp
    mov  56(%rdi), %rax
    mov  %rax, (%rsp)         # 恢復返回地址
    mov  %esi, %eax           # 返回值（longjmp 的 val）
    test %eax, %eax
    jnz  .done
    inc  %eax                 # val == 0 時，setjmp 應回傳 1
.done:
    ret
```

---

## Makefile

```makefile
CC      = gcc
CFLAGS  = -Wall -Wextra -std=c11 -g -nostdlib -nostdinc -fno-builtin
ASFLAGS = -g

OBJS = build/string.o build/malloc.o build/printf.o build/qsort.o build/setjmp_x86_64.o

.PHONY: all test clean

all: build/mini-libc.a

build/%.o: src/%.c | build
	$(CC) $(CFLAGS) -Iinclude -c $< -o $@

build/setjmp_x86_64.o: src/setjmp_x86_64.s | build
	$(CC) $(ASFLAGS) -c $< -o $@

build/mini-libc.a: $(OBJS)
	ar rcs $@ $^

build:
	mkdir -p build

test: build/mini-libc.a
	$(CC) $(CFLAGS) -Iinclude test/test_all.c build/mini-libc.a \
	  -e main -o build/test_all   # -e main 讓 linker 知道 entry point
	./build/test_all

clean:
	rm -rf build
```

---

## 驗收清單

- [ ] `mini_strlen("hello") == 5`
- [ ] `mini_malloc` / `mini_free` 通過 double-free 保護測試
- [ ] `mini_calloc` 回傳清零記憶體
- [ ] `mini_printf("%d %s\n", 42, "hello")` 輸出正確
- [ ] `mini_qsort` 正確排序 100 個隨機整數
- [ ] `mini_setjmp` / `mini_longjmp` 能在巢狀函式間跳轉
- [ ] `gcc -nostdlib` 編譯成功，`ldd ./test_all` 顯示 not a dynamic executable
- [ ] Valgrind 無 memory error（`valgrind --error-exitcode=1 ./test_all`）

---

恭喜！完成 mini-libc 的工程師，對 C 的底層理解已經超越大多數面試候選人。

接下來可以考慮：
- 把 malloc 升級成 free-list + arena 混合（Ch 13）
- 在 mini-libc 基礎上實作一個 mini-printf 的 format string fuzzer（Ch 17 + ASan）
- 把 setjmp 用來實作一個簡單的 C 協程（Ch 23）
