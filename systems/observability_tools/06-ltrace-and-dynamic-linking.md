# Ch 6 — ltrace 與動態連結

> 目標：搞清楚 ltrace 看的是什麼、它跟 strace 的本質差別、為什麼這層觀察跟動態連結機制有關。

## strace vs ltrace 一句話

- **strace** 看「user → kernel」邊界 — syscall
- **ltrace** 看「user → 動態 lib」邊界 — function call into `.so`

```
   你的程式
      │
      │  function call  ← ltrace 看這
      ▼
   libc / 其他 .so
      │
      │  syscall        ← strace 看這
      ▼
   kernel
```

不重疊。一個 lib call 可能造成 0、1、多個 syscall。

## 一個對照例

```c
// hello.c
#include <stdio.h>
#include <stdlib.h>

int main(void) {
    char *s = malloc(100);
    sprintf(s, "hello");
    puts(s);
    free(s);
    return 0;
}
```

```bash
gcc hello.c -o hello

ltrace ./hello
# malloc(100)                     = 0x55b...
# sprintf("hello", "hello")       = 5
# puts("hello")                   = 6
# free(0x55b...)                  = <void>
# +++ exited (status 0) +++

strace ./hello 2>&1 | tail -10
# write(1, "hello\n", 6)          = 6
# exit_group(0)                   = ?
```

ltrace 印 4 個 lib call、strace 印 1 個 syscall（write）。為什麼？

- `malloc(100)` → glibc 從自己 heap 切一塊出來，**0 個 syscall**（heap 還沒滿）
- `sprintf` → 純 string 操作，**0 個 syscall**
- `puts` → 寫 stdio buffer，因為換行 flush → **1 個 write syscall**
- `free` → 還給 heap pool，**0 個 syscall**

兩個工具看的是不同層的故事。

## ltrace 怎麼運作

跟 strace 不一樣。strace 用 ptrace 攔 syscall。ltrace 攔 lib call 有兩種機制：

### 機制 1：PLT/GOT hook（傳統）

動態連結用 PLT (Procedure Linkage Table) 跟 GOT (Global Offset Table) 解析 lib function。當你 call `malloc`：

```
你的 code
  │
  ▼
call malloc@plt          ← jump to PLT entry
  │
  ▼
PLT entry
  │
  ▼
jmp [malloc@got]         ← lookup GOT, get real address
  │
  ▼
real malloc in libc
```

ltrace 用 ptrace 在 PLT entry 設 breakpoint（INT 3 instruction），每次 call 經過 PLT 就停一次。

問題：**只能攔有走 PLT 的 call**。靜態連結的 binary、用 `dlsym` 動態取得的 function、JIT 跳過去的 — 都攔不到。

### 機制 2：LD_PRELOAD wrapper

ltrace 也支援 `-L` 用 LD_PRELOAD 注入 wrapper：

```bash
ltrace -L libwrapper.so ./prog
```

但通常用第一種。

## 基本用法

```bash
ltrace ./prog                    # 跑命令
ltrace -p PID                    # attach
ltrace -f ./prog                 # follow fork
ltrace -e malloc+free ./prog     # 只看這幾個
ltrace -e '!printf' ./prog       # 除了 printf
ltrace -c ./prog                 # summary
ltrace -S ./prog                 # 同時看 syscall（合 strace + ltrace）
ltrace -o trace.log ./prog
ltrace -s 80 ./prog              # 字串最大長度
ltrace -A 5 -L libc.so.6 ./prog  # 跨 lib 顯示更多 frame
```

`-S` 特別有用：

```bash
ltrace -S ./hello
# malloc(100)                     = 0x55b...
# SYS_brk(NULL)                   = 0x55b...
# SYS_brk(0x55b...+0x21000)       = 0x55b...
# sprintf(...)                    = 5
# puts(...)                       = <void>
# SYS_write(1, "hello\n", 6)      = 6
# free(0x55b...)                  = <void>
# +++ exited (status 0) +++
```

lib + syscall 對照看，知道哪個 lib call 觸發哪個 syscall。

## 一個踩雷：ltrace 對現代 binary 常常壞掉

`ltrace` 開發很慢，新版 glibc / 新 architecture 經常出 bug。常見：

```
$ ltrace ./prog
ltrace: ./prog: ELF e_shstrndx points beyond the end
```

或 trace 結果不完整、segfault。**ltrace 在 2025 年的可靠度遠不如 strace**。

替代品：

- **`bpftrace` 加 uprobe**：攔指定 lib function，比 ltrace 穩定且能看 production
- **`perf trace`** 對少量 function 也能用 uprobe
- **自己用 LD_PRELOAD wrapper**（Ch 20）：完全控制

ltrace 仍能用，但遇到問題不要懷疑自己。

## 動態連結速成

理解 ltrace 要懂動態連結。基本概念：

**靜態 link**：把所有 lib code 包進 binary，一個 `.exe` 自給自足。

**動態 link**：binary 只記「我需要 libc.so.6 的 malloc」，執行時 dynamic linker (`ld.so`) 把 libc 載入、解析符號。

```bash
ldd /bin/ls
# linux-vdso.so.1
# libselinux.so.1 => /lib/x86_64-linux-gnu/libselinux.so.1
# libc.so.6 => /lib/x86_64-linux-gnu/libc.so.6
# /lib64/ld-linux-x86-64.so.2 (interpreter)
```

每個 `.so` 都會被 mmap 進 process address space。`/proc/PID/maps` 看得到。

## PLT / GOT 詳細

延伸 Ch 11 會深入，這裡知道概念：

- **PLT**：trampoline 區，每個 import function 一個 entry，**內容是固定 jump code**
- **GOT**：data 區，每個 PLT entry 對應一個 GOT slot，**runtime 才填入 real address**

第一次 call 某個 function 時：

```
your code → PLT entry → jmp [GOT[N]]
               GOT[N] → resolver
                          │
                          ▼
                       dl_runtime_resolve
                          │
                          ▼
                       找出 real malloc address
                          │
                          ▼
                       GOT[N] = real address
                          │
                          ▼
                       jmp real malloc
```

第二次 call：

```
your code → PLT entry → jmp [GOT[N]]
               GOT[N] → real malloc 直接跑
```

這叫 **lazy binding**，第一次慢、之後快。`LD_BIND_NOW=1` 環境變數可以強制啟動時全部解析（debug 時方便、production 啟動慢）。

ltrace 把 PLT entry 改成 INT 3，所以每次 call 都觸發 SIGTRAP，ltrace 接到後印參數、replay 原指令、放行。

## ltrace vs LD_PRELOAD interceptor

LD_PRELOAD 攔 lib call 的另一種辦法（Ch 20 詳細）：

```c
// my_malloc.c
#include <stdio.h>
#include <dlfcn.h>

void *malloc(size_t sz) {
    static void *(*real)(size_t) = NULL;
    if (!real) real = dlsym(RTLD_NEXT, "malloc");
    void *p = real(sz);
    fprintf(stderr, "malloc(%zu) = %p\n", sz, p);
    return p;
}
```

```bash
gcc -shared -fPIC my_malloc.c -ldl -o my_malloc.so
LD_PRELOAD=./my_malloc.so ./prog
```

優點：不用 ptrace、overhead 極低、能改行為（不只觀察）。
缺點：要寫 C code、每個想攔的 function 都要寫 wrapper。

## 動手練習

**1. ltrace 一個簡單程式**

```c
#include <string.h>
#include <stdlib.h>
int main() {
    char *s = strdup("hello world");
    char *t = strstr(s, "world");
    printf("%s\n", t);
    free(s);
}
```

```bash
gcc t.c -o t
ltrace ./t
# strdup("hello world") = 0x...
# strstr("hello world", "world") = 0x...
# printf("%s\n", "world") = 6
# puts(...)      ← 注意：printf 內部其實 call puts
# free(0x...) = <void>
```

注意 printf 跑出來 ltrace 可能印 `puts` 而不是 printf — 因為 glibc 把 `printf("%s\n")` 優化成 `puts`。

**2. ltrace -S 看對應 syscall**

```bash
ltrace -S ./t
```

每個 lib call 旁邊看到 SYS_brk（malloc 撐大 heap）、SYS_write（puts）。

**3. 故意用 dlsym**

```c
#include <dlfcn.h>
int main() {
    void *h = dlopen("libm.so.6", RTLD_LAZY);
    double (*fn)(double) = dlsym(h, "sqrt");
    printf("%f\n", fn(2.0));
}
```

```bash
gcc dlsym.c -ldl -o dl
ltrace ./dl
```

ltrace 看不到 `sqrt(2.0)` —— 因為它不走 PLT。你只看到 dlsym。**這就是 ltrace 的限制**。

**4. 故意 static link**

```bash
gcc -static t.c -o t-static
ltrace ./t-static
# 啥都看不到
```

靜態連結 = 沒有動態 lib boundary，ltrace 無能為力。strace 還能看 syscall。

**5. 用 LD_PRELOAD 攔 malloc**

照上面 my_malloc.c 寫一個，LD_PRELOAD 跑你自己的程式，看 stderr 有 malloc 紀錄。

## 自我檢核

- [ ] 知道 ltrace 跟 strace 看的層不一樣
- [ ] 講得出為什麼 ltrace 對 static binary 不管用
- [ ] 知道 PLT / GOT 跟 lazy binding 大致怎麼運作
- [ ] 知道 ltrace 在 2025 不太穩，替代是 bpftrace / uprobe
- [ ] 用過 `-S` 看 lib call → syscall 對照
- [ ] 知道 LD_PRELOAD 是攔 lib call 的另一種方法

下一個是 Part 2 的整合練習：用 strace 抓真實的 bug。

→ [練習 A：用 strace 抓真實 bug](./practice-a-strace-bug-hunt.md)
