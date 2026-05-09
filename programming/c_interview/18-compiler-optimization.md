# Ch 18 — 編譯器優化與你的程式碼

> 目標：理解編譯器在各 `-O` 等級做什麼，以及它為什麼能根據 UB 的「不存在假設」做出令人驚訝的優化。

## 優化等級概覽

```bash
gcc -O0 prog.c   # 不優化：每條 C 語句直譯成 assembly，除錯用
gcc -O1 prog.c   # 基本優化：dead code elimination、簡單 inlining
gcc -O2 prog.c   # 正式 production 等級：loop unrolling、LICM、嚴格 alias 分析
gcc -O3 prog.c   # 激進：auto-vectorization、更多 inlining（可能讓 binary 更大）
gcc -Os prog.c   # 最小化 binary 大小
gcc -Og prog.c   # 為除錯優化：比 -O0 快，但保留除錯資訊
```

---

## 常見優化技術

### Dead Code Elimination（DCE）

```c
int unused_fn(int x) { return x * 2; }   // 若從未呼叫，整個函式被刪除

int main(void) {
    int x = 5;
    int y = x * 2;   // 若 y 從未使用 → 這行被刪除
    return 0;
}
```

**陷阱**：你以為的「清零敏感資料」可能被 DCE 掉：

```c
void clear_password(char *pw, size_t n) {
    memset(pw, 0, n);   // 編譯器看到之後 pw 沒有被讀，可能把 memset 優化掉！
}
// 正確做法：memset_s（C11）、SecureZeroMemory（Windows）、或 volatile 指標
```

### Constant Folding / Constant Propagation

```c
int x = 2 + 3;          // 編譯期計算：x = 5
int y = x * 4;          // 再展開：y = 20
printf("%d\n", y);      // 直接輸出常數 20
```

整個計算在編譯期就完成，執行期只剩 `printf("%d\n", 20)`。

### Inlining

```c
static inline int square(int x) { return x * x; }

int main(void) {
    int a = square(5);   // -O1 以上：直接展開成 a = 5 * 5 = 25
}
// 函式呼叫的 overhead（push/pop、call/ret）完全消除
```

### Loop Invariant Code Motion（LICM）

```c
// 原始碼：
for (int i = 0; i < n; i++)
    arr[i] *= 2.0 * M_PI;   // 2.0 * M_PI 每次迴圈都重算！

// 優化後等效：
double k = 2.0 * M_PI;
for (int i = 0; i < n; i++)
    arr[i] *= k;
```

### Loop Unrolling

```c
// 原始碼：
for (int i = 0; i < 8; i++)
    a[i] = b[i] + c[i];

// -O3 可能展開成：
a[0] = b[0] + c[0];
a[1] = b[1] + c[1];
// ... 減少迴圈 overhead，可能觸發 SIMD
```

---

## UB 讓優化「越界」

這是 C 最重要的部分：**編譯器被允許假設 UB 永遠不會發生**，並基於此做優化。

### 有號整數溢位

```c
// 原始碼：
void loop_until_overflow(int start) {
    for (int i = start; i < start + 100; i++)
        do_something(i);
}

// 編譯器的推理：
// int 溢位是 UB → 所以 i 永遠不會溢位 → i 一定會走完 100 次
// 因此：不需要溢位的特殊處理，可以完全展開或 vectorize
```

### NULL pointer 推導

```c
void foo(int *p) {
    int x = *p;        // (1) dereference
    if (p == NULL)     // (2) NULL 檢查
        return;
    use(x);
}
// 編譯器：(1) 已經 dereference，所以 p 不是 NULL（否則是 UB）
// → (2) 的 NULL 檢查永遠為假 → 刪除整個 if block
```

這個優化導致了 Linux kernel 的 CVE-2009-1897：NULL pointer check 被 gcc 優化掉，留下安全漏洞。

### Strict Aliasing 優化

```c
void process(int *a, float *b, int n) {
    for (int i = 0; i < n; i++) {
        a[i] += 1;
        b[i] *= 2.0f;
        // 編譯器可以假設 a 和 b 不互相 alias（int* 和 float* 不相容）
        // 因此可以把迴圈向量化，用 SIMD 一次處理多個元素
    }
}
```

---

## 如何觀察優化效果

```bash
# 輸出 assembly（Intel 語法更易讀）：
gcc -O2 -S -masm=intel prog.c

# Compiler Explorer（godbolt.org 線上版）：
# 輸入 C 代碼，右側即時顯示 assembly，可切換 -O0/-O1/-O2/-O3

# 看函式是否被 inline：
gcc -O2 -fopt-info-inline prog.c 2>&1 | grep inlined
```

---

## 常用優化相關 flag

| Flag | 說明 |
|------|------|
| `-fno-strict-aliasing` | 關閉 strict aliasing 優化（舊代碼用）|
| `-fno-omit-frame-pointer` | 保留 rbp frame pointer（profiling 需要）|
| `-fstack-protector-strong` | stack canary（防止 buffer overflow）|
| `-D_FORTIFY_SOURCE=2` | 在 glibc 中啟用 buffer overflow 偵測 |
| `-march=native` | 針對當前 CPU 優化（啟用 AVX2 等 SIMD）|
| `-flto` | Link-Time Optimization（跨 .c 檔 inline）|

---

## 防止 DCE 清除敏感資料

```c
#include <string.h>

// 方案一：C11 memset_s（保證不被 DCE）
memset_s(buf, sizeof(buf), 0, sizeof(buf));

// 方案二：volatile 指標
volatile char *p = (volatile char *)buf;
for (size_t i = 0; i < sizeof(buf); i++)
    p[i] = 0;

// 方案三：OpenSSL 的 OPENSSL_cleanse
// 內部用 memset + 防止優化的 barrier
```

---

## 自我檢核

- [ ] 能說出 DCE 刪除 memset 的條件（之後無讀取），以及防止方法
- [ ] 能解釋「編譯器假設 UB 不發生 → NULL check 被刪除」的推導
- [ ] 知道 `-fno-strict-aliasing` 的用途（舊代碼但不影響 strict aliasing 假設）
- [ ] 知道用 godbolt.org 可以即時對比不同優化等級的 assembly

→ [Ch 19 前處理器陷阱](./19-preprocessor-traps.md)
