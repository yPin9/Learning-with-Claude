# Ch 30 — LTO：連結時優化

> 目標：理解為什麼跨編譯單元的優化需要 LTO，掌握 Full LTO 和 ThinLTO 的架構差異，以及 LTO 對內聯和 devirtualization 的影響。

## 問題：編譯單元邊界

傳統編譯模型：

```
file1.c  ──→  clang  ──→  file1.o
file2.c  ──→  clang  ──→  file2.o
                               ↓
                            ld/lld  ──→  executable
```

每個 `.c` 文件獨立編譯，編譯器只能在每個編譯單元（`.c` 文件）內優化。

結果：

```
file1.c 呼叫 file2.c 的函式
→ file1.c 看不到被呼叫函式的定義
→ 無法內聯、無法常數傳播
→ 函式必須有 external linkage（公開符號）→ IPCP 受限
```

**LTO（Link-Time Optimization）** 解決了這個問題：在鏈接時，所有編譯單元的 IR 放在一起，再做一次全程式優化。

## Full LTO

**Full LTO** 的流程：

```
1. 編譯階段：每個 .c 生成 LLVM IR（bitcode）而不是 object file
   clang -flto file1.c -c -o file1.bc
   clang -flto file2.c -c -o file2.bc

2. 鏈接階段：lld/gold 把所有 bitcode 合成一個大 module
   全部函式都可見 → 可以做全程式 IPCP、內聯、devirtualization

3. 在合并後的 module 上跑完整優化 pipeline

4. 後端代碼生成
```

代價：步驟 2 需要把所有 IR 一次載入記憶體，對大型程式（LLVM 本身幾百萬行）記憶體佔用巨大，且無法並行。

## ThinLTO

**ThinLTO**（LLVM 3.9+）解決了 Full LTO 的擴展性問題，用「摘要（summary）+ 按需載入」替代「全部合并」：

```
1. 編譯階段：生成 bitcode + 函式摘要（function summary）
   摘要包含：函式大小、呼叫關係、attribute（readonly 等）

2. 鏈接階段（快速）：
   a. 合并所有摘要（不合并 IR，很快）
   b. 決定哪些函式需要內聯（基於摘要）
   c. 標記需要 import（從其他模組引入）的函式

3. 後端並行：每個模組獨立優化，只載入需要 import 的函式 IR
   → 可以多線程並行，擴展性好
```

ThinLTO 的效果接近 Full LTO（通常損失 < 5%），但速度快很多。

```bash
# Full LTO
clang -flto=full -O2 file1.c file2.c -o output

# ThinLTO
clang -flto=thin -O2 file1.c file2.c -o output

# 也可以用 lld
clang -flto=thin -fuse-ld=lld -O2 file1.c file2.c -o output
```

## LTO 帶來的優化機會

**1. 跨模組內聯（Cross-module Inlining）**

```c
// file2.c
static inline int helper(int x) { return x * 2; }  // 對外不可見
int g(int x) { return helper(x); }

// file1.c
extern int g(int x);
int f() { return g(21); }  // 沒有 LTO：無法看到 helper 的定義
                           // 有 LTO：g 被內聯，helper 也被內聯，直接返回 42
```

**2. Devirtualization**

C++ 虛函式呼叫在沒有 LTO 的情況下是間接呼叫，無法內聯。

LTO 讓連結器看到整個程式的繼承關係，如果能確定虛函式的實際目標（single implementation），就能把 virtual call 換成 direct call，再內聯。

```cpp
// 程式中只有一個 Derived 類繼承 Base
class Base { virtual void f(); };
class Derived : public Base { void f() override { /* ... */ } };

Base *p = new Derived();
p->f();  // 沒有 LTO：間接呼叫
         // 有 LTO + 類型分析：直接呼叫 Derived::f()，可以內聯
```

**3. Whole-Program Dead Code Elimination**

沒有從任何入口點（main, exported functions）可達的函式，是死碼，可以整個刪除。只有在看到整個程式時才能確認「沒有任何人呼叫這個函式」。

## LTO 對符號可見性的影響

LTO 允許把 external linkage 改成 internal linkage（在確認沒有動態鏈接的情況下），讓更多函式可以被積極優化。

```c
// file.c（會被鏈接成完整程式，不是 shared library）
int helper(int x) { return x * 2; }  // external，但只在本程式用
// LTO 發現沒有外部使用者 → 可以當 static 來優化
```

## 自我檢核

- [ ] LTO 的動機：跨編譯單元的優化（內聯、IPCP、devirtualization、全程式 DCE）
- [ ] Full LTO：全部 IR 合并後優化，記憶體大，無法並行
- [ ] ThinLTO：基於摘要決策，按需載入，可並行，效果接近 Full LTO
- [ ] `-flto=thin` 和 `-fuse-ld=lld` 的搭配
- [ ] Devirtualization：LTO 看到全繼承關係，把 virtual call 換成 direct call

→ [Ch 31 Undefined Behavior 在優化中的角色](./31-ub-and-optimization.md)
