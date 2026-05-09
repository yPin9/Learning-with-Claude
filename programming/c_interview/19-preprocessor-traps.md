# Ch 19 — 前處理器陷阱

> 目標：掌握 `#define` 的所有常見陷阱，以及何時應該用 `inline`、`const`、`enum` 替代。

## #define 的本質：文字替換

前處理器做的是純文字替換，**在編譯前完成**，完全不理解 C 語法：

```c
#define DOUBLE(x) x + x     // 純文字替換！

int a = DOUBLE(3) * 2;
// 展開成：int a = 3 + 3 * 2;
// 不是  (3 + 3) * 2 = 12
// 是    3 + (3 * 2) = 9   因為 * 優先順序高於 +
```

**修正**：括號保護每個引數和整個表達式：

```c
#define DOUBLE(x) ((x) + (x))     // 每個 x 加括號
// 但這還有問題 ↓
```

---

## Side Effect 問題

```c
#define MAX(a, b) ((a) > (b) ? (a) : (b))

int x = 5;
int m = MAX(x++, 3);
// 展開成：((x++) > (3) ? (x++) : (3))
// x++ 可能被求值兩次！（若 x > 3，x 會遞增兩次）
// 結果：m = 6（取了第二次 x++），x = 7（遞增了兩次）
```

傳入有副作用的表達式（函式呼叫、`++`）給 macro 就是陷阱。

**修正一：用 inline 函式**（C99 以上推薦）：

```c
static inline int max(int a, int b) { return a > b ? a : b; }
// 型別安全、無副作用問題、大多數情況下編譯器會 inline
```

**修正二：GCC extension（Statement Expression）**：

```c
#define MAX(a, b) ({         \
    typeof(a) _a = (a);      \
    typeof(b) _b = (b);      \
    _a > _b ? _a : _b;       \
})
// 每個引數只求值一次，但不可移植（GNU 擴充）
```

---

## 常見 #define 陷阱整理

```c
// 陷阱 1：運算子優先序
#define SQ(x)   x * x           // SQ(1+2) → 1+2*1+2 = 5，不是 9
#define SQ(x)   ((x) * (x))     // 修正

// 陷阱 2：分號吞噬
#define SWAP(a, b) { int t = a; a = b; b = t; }
if (cond)
    SWAP(x, y);   // 展開後：if (cond) { int t = x; x = y; y = t; };
else              // 這個 else 對不到任何 if！
    foo();
// 修正：使用 do { ... } while(0)

#define SWAP(a, b) do { int t = (a); (a) = (b); (b) = t; } while(0)

// 陷阱 3：遞迴展開問題
#define BUFSIZE 256
#define BUFSIZE BUFSIZE + 1    // 展開後：BUFSIZE + 1 → 256 + 1 + 1？
                                // 實際上 C 不支援遞迴展開，BUFSIZE 變成自己
// 不要重定義 macro
```

---

## do-while(0) 慣用法

multi-statement macro 必須用 `do { ... } while(0)`：

```c
// 錯誤：
#define LOG_AND_RETURN(x) fprintf(stderr, "err\n"); return x;
if (cond) LOG_AND_RETURN(-1);
// 展開：if (cond) fprintf(stderr, "err\n"); return -1;
//       return -1 永遠執行！

// 正確：
#define LOG_AND_RETURN(x) do { \
    fprintf(stderr, "err\n");  \
    return (x);                \
} while(0)
// 展開後是完整的 if (cond) do { ... } while(0); ← 無論是否有 else 都正確
```

---

## X Macro（一個進階但實用的技巧）

當你有一個枚舉，並需要對應的字串名稱：

```c
// 定義一個「資料來源」macro
#define COLORS(X) \
    X(RED,   0xFF0000) \
    X(GREEN, 0x00FF00) \
    X(BLUE,  0x0000FF)

// 用 X macro 展開出 enum：
typedef enum {
#define X(name, value) name = value,
    COLORS(X)
#undef X
} Color;

// 用 X macro 展開出名稱陣列：
static const char *color_names[] = {
#define X(name, value) #name,
    COLORS(X)
#undef X
};

// 用 X macro 展開出 switch case：
const char *color_to_str(Color c) {
    switch (c) {
#define X(name, value) case name: return #name;
        COLORS(X)
#undef X
    }
    return "UNKNOWN";
}
```

新增顏色只需修改 `COLORS` 的一行，enum、字串陣列、switch case 全部自動跟著更新。

---

## 什麼時候用什麼

| 需求 | 推薦 | 理由 |
|------|------|------|
| 常數 | `const int N = 10;` 或 `enum { N = 10 };` | 有型別、有 scope |
| 型別無關的最大值 | `static inline` + 多載 / C11 `_Generic` | 無副作用 |
| 編譯期常數（陣列大小）| `enum { BUF = 256 };` | 比 `const` 更保證編譯期 |
| 條件編譯 | `#ifdef` | macro 的正當用途 |
| 字串化 / Token Paste | `#` 和 `##` 運算子 | macro 的正當用途 |

---

## 自我檢核

- [ ] 能說出三種 `#define MAX(a,b)` 的陷阱（優先序、副作用、無法 debug）
- [ ] 知道 multi-statement macro 必須用 `do { ... } while(0)`
- [ ] 知道用 `static inline` 替代大多數帶引數的 macro
- [ ] 能解釋 X Macro 的展開機制

→ [Ch 20 連結器與符號](./20-linker-symbols.md)
