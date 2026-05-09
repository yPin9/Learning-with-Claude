# Ch 17 — 可變引數與 printf 內部實作

> 目標：掌握 `va_list` 機制，能自己實作 printf-like 函式，並理解 format string 漏洞的根源。

## va_list 基本語法

```c
#include <stdarg.h>

// 宣告可變引數函式：至少要有一個固定引數
int sum(int count, ...) {
    va_list ap;
    va_start(ap, count);    // 初始化，告訴它固定引數的最後一個是 count

    int total = 0;
    for (int i = 0; i < count; i++)
        total += va_arg(ap, int);    // 每次取一個 int

    va_end(ap);              // 必須呼叫，釋放 va_list 資源
    return total;
}

// 呼叫：
sum(3, 10, 20, 30);   // 回傳 60
sum(0);               // 0，count = 0 不取任何引數
```

`va_arg` 的型別必須和實際傳入的型別一致。**傳入 `char` 或 `short` 時，整數提升會讓它變成 `int`；傳入 `float` 時它變成 `double`**——用 `va_arg(ap, double)` 不是 `float`。

---

## 轉發可變引數：vprintf 家族

```c
#include <stdarg.h>
#include <stdio.h>

// 自訂 log 函式，需要轉發給 vprintf：
void log_info(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fprintf(stderr, "[INFO] ");
    vfprintf(stderr, fmt, ap);   // vprintf 接受 va_list，不是 ...
    fprintf(stderr, "\n");
    va_end(ap);
}

// vprintf 家族：
// vprintf(fmt, ap)          → stdout
// vfprintf(fp, fmt, ap)     → FILE*
// vsprintf(buf, fmt, ap)    → char buffer（危險：無邊界）
// vsnprintf(buf, n, fmt, ap)→ char buffer（安全：有邊界）
```

---

## 自製簡化 printf

理解 printf 的工作原理，自己實作一個支援 `%d`, `%s`, `%c`, `%x` 的版本：

```c
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static void print_int(int n) {
    if (n < 0) { putchar('-'); n = -n; }
    if (n >= 10) print_int(n / 10);
    putchar('0' + n % 10);
}

static void print_hex(unsigned int n) {
    static const char hex[] = "0123456789abcdef";
    if (n >= 16) print_hex(n / 16);
    putchar(hex[n % 16]);
}

void my_printf(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);

    for (const char *p = fmt; *p; p++) {
        if (*p != '%') {
            putchar(*p);
            continue;
        }
        p++;   // 跳過 %
        switch (*p) {
            case 'd': print_int(va_arg(ap, int));            break;
            case 'u': print_int((int)va_arg(ap, unsigned));  break;
            case 'x': print_hex(va_arg(ap, unsigned));       break;
            case 's': fputs(va_arg(ap, const char *), stdout); break;
            case 'c': putchar(va_arg(ap, int));              break;
            case '%': putchar('%');                           break;
            default:  putchar('%'); putchar(*p);             break;
        }
    }

    va_end(ap);
}

// 測試：
my_printf("Hello %s, you are %d years old, id=0x%x\n", "Alice", 30, 0xdeadbeef);
// Hello Alice, you are 30 years old, id=0xdeadbeef
```

---

## printf 的型別安全問題

`printf` 完全信任 format string，**不會檢查你實際傳的引數型別**：

```c
int x = 65536;
printf("%s\n", x);    // UB：把 65536 當 char* 解引用 → 幾乎必然 segfault
printf("%d\n");       // UB：va_arg 讀了不存在的引數 → garbage 或 crash

// 型別寬度不匹配：
long long big = 1LL << 40;
printf("%d\n", big);   // UB（64-bit 值用 %d 讀 32 bits）
printf("%lld\n", big); // 正確：long long 要用 %lld
```

現代編譯器（`-Wall`）可以偵測 format string 和引數的不匹配——前提是 format string 是字串常數，不是變數。

---

## Format String 漏洞

```c
// 安全的：
printf("%s", user_input);

// 危險的：把使用者輸入直接當 format string！
printf(user_input);   // format string vulnerability
```

若攻擊者輸入 `"%x %x %x %x"`：
- `printf` 對每個 `%x` 呼叫 `va_arg`，讀 stack 上「應該是引數」的位置
- 但根本沒有傳引數，所以讀出 stack 上的任意資料
- 進一步可以用 `%n`（寫入已輸出字元數到指標）**任意寫入記憶體**

這個漏洞在 2000 年代初期很常見，現在仍偶爾出現在嵌入式系統。

---

## __attribute__((format))

自訂 printf-like 函式時，可以告訴編譯器去檢查 format：

```c
__attribute__((format(printf, 1, 2)))
void my_log(const char *fmt, ...);

// 現在這樣會在編譯時警告：
my_log("%s", 42);   // warning: format '%s' expects 'char *', argument is 'int'
```

`format(printf, 1, 2)`：第 1 個引數是 format string，第 2 個引數開始是可變引數。

---

## va_copy：複製 va_list

```c
int my_vsnprintf(char *buf, size_t size, const char *fmt, va_list ap) {
    va_list ap2;
    va_copy(ap2, ap);       // 複製一份，因為 vsnprintf 會消耗 ap
    int len = vsnprintf(NULL, 0, fmt, ap);  // 第一次：計算所需長度
    // ... 用 ap2 做第二次 ...
    va_end(ap2);
    return len;
}
```

`va_list` 是有狀態的游標，不能直接複製（`va_list ap2 = ap;` 行為未定義）。要複製必須用 `va_copy`。

---

## 自我檢核

- [ ] 能說出 `va_start`、`va_arg`、`va_end` 的作用
- [ ] 知道傳入 `char`/`float` 給可變引數函式時會有整數/浮點提升
- [ ] 知道 format string vulnerability 的根源（`printf(user_input)` 而非 `printf("%s", user_input)`）
- [ ] 知道自訂 printf-like 函式應加 `__attribute__((format(printf, m, n)))`

→ [Ch 18 編譯器優化與你的程式碼](./18-compiler-optimization.md)
