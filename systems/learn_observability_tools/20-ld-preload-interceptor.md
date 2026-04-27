# Ch 20 — 動手：LD_PRELOAD interceptor

> 目標：用 LD_PRELOAD 寫 wrapper 攔截任何 lib function。學會看 / 改 / log / fail-injection，建一個能用的 debug 工具。

## LD_PRELOAD 是什麼

dynamic linker 載入 shared library 時，**LD_PRELOAD 列出的 .so 比所有正常 lib 都先載**。它的 symbol 會 override 後面 lib 的同名 symbol。

```bash
LD_PRELOAD=./mywrap.so ./myprog
```

`mywrap.so` 裡定義了 `malloc`，那 `myprog` 的 `malloc` 呼叫會打進 wrap.so，不是 libc.so。

## 跟 ptrace / strace 比

| 特性 | LD_PRELOAD | ptrace | bpftrace uprobe |
|---|---|---|---|
| 攔什麼 | dynamic lib function | syscall / instruction | function entry/exit |
| Overhead | 極低 | 高 | 低 |
| 需要 root | 否 | 看 ptrace_scope | 是 |
| 改行為 | ✅（wrapper 隨意寫） | ✅（POKEREGS） | ❌（只觀察） |
| 對 static binary | ❌ | ✅ | ✅（uprobe） |
| 對 setuid binary | ❌（kernel 防） | ❌（kernel 防） | ✅（用 root） |
| 寫 wrapper 難度 | C code，中等 | C code，較難 | 一行腳本 |

LD_PRELOAD 的甜蜜點：**動態 lib 程式 + 想改行為 + 不要 root + 寫 C 不痛苦**。

## 最小範例

```c
// mywrap.c — 攔截 malloc 印 log
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>

void *malloc(size_t size) {
    static void *(*real)(size_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "malloc");
    void *p = real(size);
    fprintf(stderr, "[wrap] malloc(%zu) = %p\n", size, p);
    return p;
}
```

```bash
gcc -shared -fPIC mywrap.c -ldl -o mywrap.so
LD_PRELOAD=./mywrap.so /bin/ls /tmp
# [wrap] malloc(...) = 0x...
# [wrap] malloc(...) = 0x...
# ... ls 的輸出 ...
```

## 解析

### `dlsym(RTLD_NEXT, "malloc")`

「**找下一個叫 malloc 的 symbol**」。LD_PRELOAD 把我們的 malloc 放最前，下一個就是真 libc malloc。

`RTLD_NEXT` 是「跳過 caller 的 lib，找下一個」。

### static cache

```c
static void *(*real)(size_t) = NULL;
if (!real) real = dlsym(...);
```

dlsym 不便宜，cache 起來。

### `_GNU_SOURCE`

`RTLD_NEXT` 是 GNU extension，要 define。

## 攔多個 function

```c
// trace.c
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <unistd.h>
#include <fcntl.h>
#include <dlfcn.h>

#define WRAP(ret, name, ...)                                    \
    typedef ret (*name##_t)(__VA_ARGS__);                       \
    static name##_t real_##name = NULL;                         \
    static void load_##name(void) {                             \
        if (!real_##name) real_##name = dlsym(RTLD_NEXT, #name);\
    }

WRAP(int, open, const char *, int)
int open(const char *path, int flags, ...) {
    load_open();
    int fd = real_open(path, flags);
    fprintf(stderr, "[wrap] open(\"%s\", %x) = %d\n", path, flags, fd);
    return fd;
}

WRAP(ssize_t, read, int, void *, size_t)
ssize_t read(int fd, void *buf, size_t count) {
    load_read();
    ssize_t n = real_read(fd, buf, count);
    fprintf(stderr, "[wrap] read(%d, ..., %zu) = %zd\n", fd, count, n);
    return n;
}

WRAP(int, close, int)
int close(int fd) {
    load_close();
    int r = real_close(fd);
    fprintf(stderr, "[wrap] close(%d) = %d\n", fd, r);
    return r;
}
```

```bash
gcc -shared -fPIC trace.c -ldl -o trace.so
LD_PRELOAD=./trace.so /bin/cat /etc/hostname
# [wrap] open("/etc/hostname", 0) = 3
# [wrap] read(3, ..., 4096) = 9
# myhost
# [wrap] read(3, ..., 4096) = 0
# [wrap] close(3) = 0
```

簡易版 strace。對動態 link 程式有效。

## fault injection

modify behavior，不只觀察：

```c
// fault.c — 1/100 機率讓 malloc 失敗
#define _GNU_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <dlfcn.h>
#include <errno.h>

void *malloc(size_t size) {
    static void *(*real)(size_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "malloc");

    if (rand() % 100 == 0) {
        fprintf(stderr, "[fault] injecting malloc failure (size=%zu)\n", size);
        errno = ENOMEM;
        return NULL;
    }

    return real(size);
}
```

```bash
gcc -shared -fPIC fault.c -ldl -o fault.so
LD_PRELOAD=./fault.so ./myprog
```

myprog 偶爾遇到 malloc 失敗，**測試 error path**。如果 myprog 沒檢查 NULL 就會 segfault — 找到 bug。

這是「chaos engineering」基本款。fault injection lib 例如 `libfaketime`、`libeatmydata` 都這原理。

## 攔 read 改內容

```c
// fakeread.c — 把 /etc/hostname 內容變成 "fakehost"
ssize_t read(int fd, void *buf, size_t count) {
    static ssize_t (*real)(int, void *, size_t) = NULL;
    static int (*real_open)(const char*, int, ...) = NULL;
    if (!real) {
        real = dlsym(RTLD_NEXT, "read");
        real_open = dlsym(RTLD_NEXT, "open");
    }

    // 先讀真的
    ssize_t n = real(fd, buf, count);

    // 如果這 fd 是 /etc/hostname...
    char path[256];
    snprintf(path, sizeof(path), "/proc/self/fd/%d", fd);
    char real_path[256];
    ssize_t pn = readlink(path, real_path, sizeof(real_path) - 1);
    if (pn > 0) {
        real_path[pn] = 0;
        if (strcmp(real_path, "/etc/hostname") == 0) {
            const char *fake = "fakehost\n";
            size_t l = strlen(fake);
            if (count >= l) {
                memcpy(buf, fake, l);
                return l;
            }
        }
    }

    return n;
}
```

跑：

```bash
gcc -shared -fPIC fakeread.c -ldl -o fake.so
LD_PRELOAD=./fake.so cat /etc/hostname
# fakehost
```

`cat` 看到「假的」hostname。

## 經典應用：libfaketime

模擬時間：

```bash
sudo apt install libfaketime
faketime '2030-01-01' date
# Tue Jan  1 00:00:00 UTC 2030
```

LD_PRELOAD 攔 `time()`、`gettimeofday()`、`clock_gettime()` 回傳假時間。**測試 time-based bug** 神器（Y2K38、cert expire、cron 邏輯）。

## 經典應用：libeatmydata

```bash
sudo apt install libeatmydata
eatmydata ./mydb-import
```

LD_PRELOAD 攔 `fsync` / `fdatasync` / `sync` 全部 no-op。**「我知道很危險，但我要快」** —— 大量 import / test 用，省 disk sync 等待。

## 一個常見踩雷：攔到的 function 在 PLT 之外

```c
// 攔 strcpy
char *strcpy(char *dest, const char *src) { ... }
```

但其他 lib 有些 strcpy 是 inline，不走 PLT —— LD_PRELOAD 攔不到。typical 在 release build 加 `-O2` 後問題明顯。

對策：用 `-fno-builtin-strcpy` 編 myprog，強制走 lib call。實務上 inline 過的就 LD_PRELOAD 攔不到，只能換工具（uprobe）。

## 一個常見踩雷：static binary

```bash
gcc -static myprog.c -o myprog
LD_PRELOAD=./trace.so ./myprog
# trace.so 完全不工作
```

static binary 沒有 dynamic linker 介入，LD_PRELOAD 失效。

## 一個常見踩雷：setuid binary

LD_PRELOAD **被 kernel 對 setuid binary 忽略**：

```bash
LD_PRELOAD=./trace.so /bin/su
# trace.so 不會載
```

如果允許，攻擊者能對 root 權限程式注入任意 code。kernel 直接 disable。

## 一個常見踩雷：signal handler 內 malloc

如果你 wrap malloc 加 fprintf，signal handler 內 call malloc → 觸發 fprintf → 又 malloc → 無限遞迴。

```c
void *malloc(size_t size) {
    if (in_signal_handler) return real(size);    // 跳過 log
    ...
}
```

這也是為什麼 signal handler 不該 call malloc / printf。

## 一個常見踩雷：constructor 順序

LD_PRELOAD 比其他 lib 早，但**比 ld.so 自己晚**。`__attribute__((constructor))` 在 lib load 時跑，可以做 init：

```c
__attribute__((constructor))
static void init(void) {
    fprintf(stderr, "[wrap] loaded\n");
}
```

## 動手練習

**1. malloc tracker**

寫一個 LD_PRELOAD，攔 malloc / free，記錄總分配 / 總釋放 / 當前未釋放數量。程式 exit 時印 summary。

**2. 攔 fopen 加 prefix**

寫一個 LD_PRELOAD，把所有 fopen 的 path 加 prefix `/tmp/sandbox`。程式以為自己在訪問 `/etc/passwd`，實際打開 `/tmp/sandbox/etc/passwd`。

**3. 用 libfaketime**

改變一個程式看到的時間：

```bash
faketime '2010-01-01' python3 -c 'from datetime import *; print(datetime.now())'
```

**4. fault injection**

寫一個 LD_PRELOAD，10% 機率讓 connect() 回 ECONNREFUSED。對你的 client 程式跑，看它怎麼處理 retry。

**5. 用 LD_PRELOAD 寫一個 strace mini**

攔 open / read / write / close / socket / connect，全部印。對比真 strace 看少了什麼（多半 syscall 細節 + signal）。

## 自我檢核

- [ ] 寫過自己的 LD_PRELOAD interceptor
- [ ] 知道 dlsym(RTLD_NEXT) 怎麼用
- [ ] 知道 LD_PRELOAD 對 static / setuid / inline 失效
- [ ] 用過 libfaketime / libeatmydata 之類預製工具
- [ ] 知道 fault injection 是 testing technique
- [ ] 知道 LD_PRELOAD 跟 ptrace / bpftrace 各自適用情境

下一章看 core dump 跟 signal trap —— 程式 crash 後怎麼還原案發現場。

→ [Ch 21 core dump 與 signal trap](./21-coredump-and-signals.md)
