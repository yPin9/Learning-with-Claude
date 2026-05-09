# Ch 26 — Branchless 程式設計

> 目標：理解為什麼分支有代價，掌握用算術、位元操作、CMOV 消除分支的技巧。

## Branch Predictor 與 Misprediction Penalty

現代 CPU 採用深度流水線（pipeline）。在分支指令解碼時，CPU 還不知道會跳哪裡——它會**猜測**（branch prediction），預先執行猜測路徑的指令。

若猜錯（misprediction）：需要清空流水線（flush pipeline），代價約 15–20 cycles（Skylake）。

```c
// 資料相關的分支，CPU 很難預測：
for (int i = 0; i < N; i++) {
    if (arr[i] > threshold)   // arr 是隨機資料 → 50% 猜錯率
        sum += arr[i];
}
// ~6 ns/element（含 misprediction penalty）

// 排序後：CPU 能預測（前半段全不滿足，後半段全滿足）
// ~2 ns/element
```

---

## Branchless 技巧一：算術代替分支

```c
// 有分支的 abs：
int abs_branchy(int x) {
    return x < 0 ? -x : x;
}

// 無分支的 abs（位元操作）：
int abs_branchless(int x) {
    int mask = x >> 31;        // 若 x < 0：mask = 0xFFFFFFFF（-1）
                                // 若 x >= 0：mask = 0
    return (x + mask) ^ mask;  // x < 0 時：(x + (-1)) ^ (-1) = -(x) - 1 + 1 = -x... 
                                // 等價：(x ^ mask) - mask（求補碼的公式）
}
// 編譯器通常已經做這個優化，你不一定需要手動寫
```

**更直接的：三元運算子通常被編譯成 CMOV（條件移動指令），本身就是 branchless**：

```c
int max_val = (a > b) ? a : b;   // 編譯器通常用 CMOV，無分支
```

---

## Branchless 技巧二：條件加法

```c
// 有分支：
int sum = 0;
for (int i = 0; i < N; i++)
    if (arr[i] > 0) sum += arr[i];

// Branchless 版本：
for (int i = 0; i < N; i++) {
    int mask = -(arr[i] > 0);   // arr[i] > 0 的結果是 0 或 1
                                  // -(0) = 0, -(1) = 0xFFFFFFFF（-1）
    sum += arr[i] & mask;         // 若 arr[i] <= 0：& 0 = 0（不加）
                                  // 若 arr[i] > 0：& (-1) = arr[i]（加）
}
```

這個技巧在對所有輸入都有效（不 skip），有時反而比分支慢（看 CPU 的 branch predictor 好不好）。

---

## Branchless 技巧三：位元操作

```c
// 交換兩個整數不用 temp：
x ^= y;
y ^= x;
x ^= y;
// 注意：x == y 時會讓 x = 0（自 XOR）！只適合確定不同地址的兩個值

// 判斷是否為 2 的冪次：
int is_power_of_2(unsigned x) {
    return x != 0 && (x & (x - 1)) == 0;
    // x 是 2 的冪次 → 只有一個 bit 是 1
    // x - 1 → 那個 bit 變 0，低位全變 1
    // x & (x-1) → 全 0
}

// 計算最低位的 1 的位置（ctz）：
int lowest_bit_pos = __builtin_ctz(x);   // GCC builtin：count trailing zeros

// 計算 popcount（set bit 數量）：
int bits = __builtin_popcount(x);   // GCC builtin，使用 POPCNT 指令
```

---

## Branchless 技巧四：查表（LUT）

```c
// 有分支的小寫轉大寫：
char to_upper_branchy(char c) {
    if (c >= 'a' && c <= 'z') return c - 32;
    return c;
}

// 查表（完全無分支，只有 memory load）：
static const uint8_t to_upper_table[256] = {
    [0 ... 255] = 0,   // GCC designated initializer（要另外初始化）
};
// 正確初始化版本（通常在 init function）：
void init_upper_table(void) {
    for (int i = 0; i < 256; i++) to_upper_table[i] = (uint8_t)i;
    for (int c = 'a'; c <= 'z'; c++) to_upper_table[c] = c - 32;
}

char to_upper(char c) {
    return (char)to_upper_table[(uint8_t)c];
}
```

查表對短的 lookup（256 bytes）很有效，因為整個表可以進入 cache。

---

## 何時用 Branchless

不是每個分支都值得消除。用 branchless 的時機：

1. **分支難以預測**（資料依賴、隨機輸入）
2. **迴圈裡的熱點**（per-element 操作）
3. **SIMD 友善**（branchless 更容易向量化）

不值得的情況：
- Branch predictor 準確率高（>95%）的分支（如 error handling、loop exit）
- 可讀性代價太高
- Branchless 版本引入了更多 memory access

---

## 量測分支代價

```bash
perf stat -e branches,branch-misses ./prog
# 查看 branch-miss rate

# Google Benchmark 比較：
static void BM_Branchy(benchmark::State &s) { ... }
static void BM_Branchless(benchmark::State &s) { ... }
```

---

## 自我檢核

- [ ] 能說出 branch misprediction 的 cycle 代價（~15-20 cycles）
- [ ] 知道三元運算子通常被編譯成 CMOV（條件移動，branchless）
- [ ] 能用 `-(condition)` 產生全 0 或全 1 的 mask
- [ ] 知道 `x & (x-1)` 的含義（清除最低位的 1）
- [ ] 知道 branchless 不是永遠更快（predictor 準確時分支反而快）

→ [Ch 27 Lock-Free 資料結構](./27-lock-free.md)
