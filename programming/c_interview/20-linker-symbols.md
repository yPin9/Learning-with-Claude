# Ch 20 — 連結器與符號

> 目標：理解編譯、連結流程，掌握 `extern`、`static`、`weak` 符號的行為，能解釋 "undefined reference" 和 "multiple definition" 錯誤的根因。

## 編譯 → 連結流程

```
source.c  →  [compiler]  →  source.o  ─┐
other.c   →  [compiler]  →  other.o   ─┤→ [linker] → executable
libfoo.a  ─────────────────────────────┘
```

1. **編譯**：每個 `.c` 獨立編譯，輸出目的檔（`.o`）。不跨檔案知識。
2. **連結**：把所有 `.o` 和 `.a` / `.so` 合并，解析符號引用。
3. 每個 `.o` 有個符號表，記錄「定義了哪些符號」和「引用了哪些外部符號」。

```bash
# 查看符號表：
nm prog.o
# T = text section（已定義的函式）
# U = undefined（引用但未定義，等連結器解析）
# D = data section（已初始化全域變數）
# B = BSS（未初始化全域變數）
# W = weak symbol
```

---

## extern：宣告而非定義

```c
// header.h：
extern int global_count;     // 宣告：「這個變數在某個 .c 裡定義」
extern void process(int n);  // 函式宣告通常省略 extern（函式預設就是 external linkage）

// main.c：
#include "header.h"
// 可以使用 global_count 和 process

// data.c：
int global_count = 0;   // 定義：只能在一個 .c 裡

// 常見錯誤：在 header.h 裡直接定義
// int global_count = 0;   // 若多個 .c 包含此 header → multiple definition 錯誤
```

---

## static：限制 linkage scope

`static` 在函式/變數宣告上（不是 block 內）的意思是 **internal linkage**——只在本 `.c` 檔可見：

```c
// file1.c：
static int counter = 0;       // 只有 file1.c 能看到
static void helper(void) {}   // 只有 file1.c 能呼叫

// file2.c：
static int counter = 0;       // OK：和 file1.c 的 counter 完全不同！
static void helper(void) {}   // OK：不衝突
```

**常見用法**：把 helper 函式宣告為 `static`，避免和其他 `.c` 的同名函式衝突，也減少符號表污染。

---

## Weak Symbol

weak symbol 允許被 strong symbol 覆蓋。若無 strong symbol，用 weak 的預設值：

```c
// 預設實作（weak）：
__attribute__((weak)) void on_event(int id) {
    printf("default handler: event %d\n", id);
}

// 使用者可以在自己的 .c 定義 strong 版本：
void on_event(int id) {
    printf("custom handler: event %d\n", id);
}
// 連結後，custom 版本覆蓋 weak 版本
```

這是嵌入式 / 框架庫的常見模式（HAL callback、FreeRTOS hook）。

---

## 連結順序的重要性

靜態庫（`.a`）的連結是**一遍掃描**：連結器按順序掃描，只從 `.a` 裡取出**當時有未解析引用**的 `.o`。

```bash
# 錯誤：libfoo 在 main.o 之前
gcc main.o -lfoo            # 若 main.o 引用 foo_func，而 libfoo 在左邊，可能找不到

# 正確：庫放在引用它的目的檔之後
gcc main.o -lfoo
# 連結器先處理 main.o，記錄未解析的 foo_func，再從 libfoo.a 找
```

循環依賴（A 需要 B，B 需要 A）：

```bash
gcc main.o -la -lb -la     # 列兩次解決循環依賴
# 或用：
gcc main.o -Wl,--start-group -la -lb -Wl,--end-group
```

---

## 常見連結錯誤

### "undefined reference to 'foo'"

```
prog.o: In function 'main':
main.c:5: undefined reference to 'foo'
collect2: error: ld returned 1 exit status
```

原因：
- `foo` 沒有定義（只有宣告）
- 忘記加 `-lfoo` 或 `-lm`
- 庫的連結順序錯誤
- `foo` 定義在 `.cpp` 裡（C++ name mangling），沒有用 `extern "C"`

### "multiple definition of 'bar'"

```
/usr/bin/ld: other.o:other.c:1: multiple definition of 'bar'
/usr/bin/ld: main.o:main.c:1: first defined here
```

原因：
- 在 `.h` 裡直接定義（不是宣告）全域變數或非 inline 函式，然後多個 `.c` include 這個 header

---

## 符號可見性（Visibility）

```c
// 控制 .so 的哪些符號對外可見：
__attribute__((visibility("default")))  void public_api(void);   // 預設：對外可見
__attribute__((visibility("hidden")))   void internal_fn(void);  // 對外不可見
```

```bash
# 讓所有符號預設 hidden，只顯式標記 public 的：
gcc -fvisibility=hidden -o libfoo.so ...
```

減少暴露的符號數量可以加快動態連結、防止 ABI 意外耦合。

---

## 動態連結 vs 靜態連結

```bash
# 靜態連結：把庫代碼嵌入 binary
gcc -static main.o -lfoo -o prog   # binary 較大，無動態依賴

# 動態連結：執行時載入 .so
gcc main.o -lfoo -o prog           # binary 較小，需要 libfoo.so 在系統上
ldd ./prog                          # 查看動態依賴
```

---

## 自我檢核

- [ ] 能說出 `extern int x;` 和 `int x;` 的區別（宣告 vs 定義）
- [ ] 知道 `static` 修飾 file-scope 變數/函式的含義（internal linkage）
- [ ] 知道 weak symbol 的用途（提供可被覆蓋的預設實作）
- [ ] 知道靜態庫連結順序為什麼重要（一遍掃描）

→ [Ch 21 嵌入式 C 模式](./21-embedded-c.md)
