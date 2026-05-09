# Ch 17 — 強度削減（Strength Reduction）

> 目標：理解強度削減的核心思想，掌握整數乘除法轉位移的規則，以及迴圈感應變數的削減。

## 什麼是強度削減

**強度削減（Strength Reduction）**：把代價高的運算替換成等價但更快的運算。

「強度」指的是計算成本：乘法比加法慢，除法比乘法慢，取模比除法慢。

```
昂貴                   替換成                    快速
x * 2           →      x << 1                  （移位 vs 乘法）
x * 8           →      x << 3
x / 2           →      x >> 1（無符號）
x % 8           →      x & 7（無符號，2 的冪）
x * 15          →      (x << 4) - x            （移位 + 減）
```

## 乘以 2 的冪

規則最簡單，效果也最顯著：

```
x * (2^n) = x << n

x * 1  = x           （移 0 位）
x * 2  = x << 1
x * 4  = x << 2
x * 8  = x << 3
x * 16 = x << 4
...

無符號除法：
x / (2^n) = x >> n   （logical right shift）

有符號除法（向零舍入）：
x / (2^n) 需要修正負數的舍入：
  t = x >> 31         （算術右移，得到 0 或 -1）
  t = t >>> (32-n)    （logical right shift，得到 0 或 2^n-1）
  (x + t) >> n        （加上修正值再移位）
```

為什麼有符號除法需要修正？因為 `-7 >> 2` 在算術右移下是 `-2`，但 `-7 / 4` 的結果是 `-1`（向零舍入）。

## 乘以任意常數：乘以互質數

乘以非 2 的冪的常數，也可以用移位和加減組合：

```
x * 3  = x + (x << 1)       = x + x*2
x * 5  = x + (x << 2)       = x + x*4
x * 7  = (x << 3) - x       = x*8 - x
x * 10 = (x << 1) + (x << 3) = x*2 + x*8
x * 15 = (x << 4) - x       = x*16 - x
```

何時值得？如果移位+加減的指令數 ≤ 某個閾值（通常是 3–4 條）。現代 CPU 的乘法指令已很快（1–3 個周期），但在某些嵌入式目標上乘法很慢，這個替換更有意義。

## 整數除法轉乘以倒數

```c
unsigned x / 7;
// 不是 2 的冪，不能直接移位
```

乘以倒數的技巧：計算 `magic number` M 和 shift count S，使得：

```
x / d = (x * M) >> S
（用高位乘法實作）
```

例如 `/ 7`：M = 0x24924925，S = 35

```
x / 7 = (unsigned __int128)(x) * 0x24924925 >> 35
```

這把除法（低速）換成了乘法+移位（更快），但代價是需要知道分母是編譯期常數。

LLVM 在 InstCombine 中自動做這個替換（`DivisionByConstantInfo`）。

## 迴圈感應變數削減

這是強度削減最重要的應用：在迴圈中，把乘法替換成加法。

```c
for (int i = 0; i < n; i++) {
    a[i * stride] = 0;   // 每次迭代都要做乘法 i * stride
}
```

迴圈中 `i` 是感應變數（每次迭代遞增 1），`i * stride` 也是感應變數（每次遞增 `stride`）。

可以引入一個新感應變數：

```c
for (int i = 0, j = 0; i < n; i++, j += stride) {
    a[j] = 0;   // 只需要加法
}
```

把乘法 `i * stride`（每次迭代 1 次）替換成加法 `j += stride`（每次迭代 1 次，但加法更快）。

這就是**感應變數強度削減（Induction Variable Strength Reduction）**，LLVM 的 SCEV（Ch 23）和 `IndVarSimplify` pass 會做這件事。

## 取模轉 AND

對於 `x % (2^n)`（無符號）：

```
x % 1 = 0
x % 2 = x & 1
x % 4 = x & 3
x % 8 = x & 7
x % (2^n) = x & ((2^n) - 1)
```

這把取模（代價高）換成了按位 AND（單周期）。

有符號取模同樣需要修正負數：

```c
((x % d) + d) % d  // 保證結果為正（Python-style 取模）
```

## LLVM 中的強度削減

InstCombine 自動處理乘以常數 2 的冪、除以常數（DivRem）、取模等。

```bash
# 觀察 InstCombine 的強度削減效果
cat > /tmp/sr_test.c << 'EOF'
unsigned f(unsigned x) {
    return x * 8 + x / 4 + x % 16;
}
EOF

clang -O0 -S -emit-llvm /tmp/sr_test.c -o /tmp/sr_O0.ll
opt -S -passes="instcombine" /tmp/sr_O0.ll -o /tmp/sr_opt.ll
cat /tmp/sr_opt.ll  # 應該看到移位和 AND，不再有乘除取模
```

## 自我檢核

- [ ] `x * 2^n = x << n`；無符號 `x / 2^n = x >> n`（邏輯右移）
- [ ] 有符號除以 2 的冪需要修正負數舍入
- [ ] 乘以任意常數 = 若干移位和加減的組合
- [ ] 除以編譯期常數 = 乘以 magic number + 移位
- [ ] 感應變數強度削減：迴圈中 `i * c` → 新感應變數，每次加 c

→ [Ch 18 InstCombine：LLVM 的 Peephole 引擎](./18-instcombine.md)
