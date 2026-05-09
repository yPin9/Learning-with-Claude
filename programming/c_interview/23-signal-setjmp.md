# Ch 23 — signal 與 setjmp / longjmp

> 目標：理解 POSIX signal 的非同步語意、async-signal-safe 的限制，以及 setjmp/longjmp 作為非本地跳轉機制的正確與錯誤用法。

## Signal 基礎

Signal 是 OS 向程序發送的非同步通知。程式可以：
1. 忽略（`SIG_IGN`）
2. 使用預設處理（通常是終止）
3. 安裝自訂 handler

```c
#include <signal.h>
#include <stdio.h>

volatile sig_atomic_t g_interrupted = 0;

void sigint_handler(int signo) {
    g_interrupted = 1;   // 只做最簡單的事
}

int main(void) {
    struct sigaction sa = {
        .sa_handler = sigint_handler,
        .sa_flags   = 0,
    };
    sigemptyset(&sa.sa_mask);
    sigaction(SIGINT, &sa, NULL);   // 用 sigaction，不要用 signal()（行為不可移植）

    while (!g_interrupted)
        do_work();

    printf("Interrupted\n");
    return 0;
}
```

**為什麼用 `volatile sig_atomic_t`**：
- `volatile`：防止編譯器把讀取優化掉（與 Ch 10 的 ISR 原因相同）
- `sig_atomic_t`：C 標準保證讀寫是原子的（對這個型別的單次讀/寫不會被中斷）

---

## async-signal-safe：signal handler 的嚴格限制

signal handler 可以在**任意時刻**打斷程式，包括打斷 malloc、printf 等函式的執行中間。這就是限制的來源：

```c
// 不安全：
void bad_handler(int signo) {
    printf("Caught!\n");   // printf 使用 FILE* lock，若主程式在 printf 裡被中斷
                            // → handler 再呼叫 printf → deadlock 或 heap corruption
    malloc(100);            // malloc 有 lock，同樣危險
    free(ptr);              // free 也一樣
}

// 安全：只呼叫 async-signal-safe 函式
void good_handler(int signo) {
    // 安全：write()（系統呼叫，非 libc 帶 lock 的版本）
    write(STDERR_FILENO, "caught\n", 7);

    // 安全：設定 volatile sig_atomic_t 旗標
    g_interrupted = 1;

    // 安全：_exit()（不做 atexit cleanup，不 flush stdio）
    // _exit(1);
}
```

async-signal-safe 函式列表：POSIX 定義了約 180 個（`write`、`read`、`_exit`、`kill` 等），大多數 libc 函式**不在列表上**。

---

## Common Signals

| Signal | 預設動作 | 觸發場景 |
|--------|----------|----------|
| SIGINT | 終止 | Ctrl+C |
| SIGTERM | 終止 | kill PID（可攔截）|
| SIGKILL | 終止 | kill -9 PID（不可攔截）|
| SIGSEGV | 終止+core dump | null deref / OOB |
| SIGFPE | 終止+core dump | 除以零（整數）|
| SIGCHLD | 忽略 | 子程序結束 |
| SIGALRM | 終止 | alarm() 計時到 |
| SIGUSR1/2 | 終止 | 使用者自訂 |

---

## setjmp / longjmp

非本地跳轉（non-local jump）：`setjmp` 儲存 call stack 狀態，`longjmp` 直接跳回去，中間的 stack frames 全部「消失」。

```c
#include <setjmp.h>
#include <stdio.h>

jmp_buf env;

void deep_function(int level) {
    if (level > 3)
        longjmp(env, 42);   // 跳回 setjmp 的位置，傳值 42
    deep_function(level + 1);
}

int main(void) {
    int val = setjmp(env);   // 第一次呼叫回傳 0
    if (val == 0) {
        printf("calling deep_function\n");
        deep_function(0);
        printf("this line never executes\n");
    } else {
        printf("longjmp returned: %d\n", val);  // 輸出：longjmp returned: 42
    }
    return 0;
}
```

---

## longjmp 的陷阱

```c
// 陷阱 1：自動變數的值可能不恢復
int main(void) {
    int counter = 0;       // 若 counter 在暫存器裡，longjmp 後值不確定
    volatile int vcnt = 0; // volatile 變數保證 longjmp 後保留最新值
    int val = setjmp(env);
    if (val == 0) {
        counter++;
        vcnt++;
        longjmp(env, 1);
    }
    // counter 可能是 0 或 1（取決於優化）
    // vcnt 保證是 1
}

// 陷阱 2：longjmp 跳過了 cleanup
void foo(void) {
    FILE *f = fopen("a.txt", "r");
    if (some_condition)
        longjmp(env, 1);   // f 沒有被 fclose！ resource leak
    fclose(f);
}
// C++ 的 RAII 解決這個問題，C 沒有 → 必須非常小心
```

---

## signal handler 裡的 longjmp

```c
jmp_buf sigenv;

void crash_handler(int signo) {
    // 危險但常見（Google Crashpad、Lua 解釋器使用）：
    longjmp(sigenv, signo);
    // 理論上：從 signal handler longjmp 出去的行為是 undefined（POSIX）
    // 實際上：siglongjmp 是 async-signal-safe，應用更廣
}

int main(void) {
    signal(SIGSEGV, crash_handler);
    int sig = sigsetjmp(sigenv, 1);   // sigsetjmp：額外保存/恢復 signal mask
    if (sig == 0) {
        // 受保護的代碼區域
        int *p = NULL;
        *p = 1;   // SIGSEGV → crash_handler → siglongjmp
    } else {
        printf("Caught signal %d, continuing\n", sig);
    }
}
```

---

## setjmp 的實際用途

1. **C 語言的例外處理**（Lua、SQLite 用這個模式）
2. **測試框架**的錯誤回復（例如讓 segfault 的測試不 crash 整個 suite）
3. **協程/fiber 的 context switching**（更常用 ucontext.h 或平台原生）

---

## 自我檢核

- [ ] 知道為什麼 signal handler 不能用 printf（非 async-signal-safe）
- [ ] 知道 `volatile sig_atomic_t` 的兩個修飾詞各自的作用
- [ ] 能解釋 `setjmp` 回傳 0 vs 非零的語意
- [ ] 知道 longjmp 跳過 stack frames 可能造成 resource leak

→ [Ch 24 Cache 友善程式設計](./24-cache-friendly.md)
