# Ch 7 — B 擴充：bit manipulation 全解

> 目標：看懂 `Zba` / `Zbb` / `Zbc` / `Zbs` 四個子集各做什麼、為什麼 SiFive 面試特別愛問 bitmanip（因為 code density + perf 雙贏）、以及 compiler 什麼時候會自動用它。

## 為什麼 RISC-V 需要 bit manipulation 擴充

base ISA 的弱點：**沒有 leading-zero count、沒有 popcount、沒有 rotate、沒有 bit-field extract**。寫 hash、crypto、bit-packed 資料結構都要靠一連串 `and + shift + or`。ARM 跟 x86 早就有這些（`lzcnt`、`popcnt`、`ror`），RISC-V 直到 B 擴充才補上。

B 擴充 2021 年 ratify（Zba/Zbb/Zbc/Zbs），**2023 年進 RVA23 profile 的必備清單**。新一代 CPU（SiFive P670、XuanTie C910v2、T-Head 系列）都支援。

## B 擴充的四個子集

```
Zba     Address generation   (shifted-add for pointers / arrays)
Zbb     Basic bit manip      (常用：clz, popcount, rotate, max, min)
Zbc     Carry-less multiply  (crypto, CRC)
Zbs     Single-bit           (bset, bclr, binv, bext)
```

**`Zbb` 是最「日常」的**，compiler 在不需要開特殊 flag 的情況下就會自動用。`Zba` 提供陣列定址優化，對 C/C++ 很有用。`Zbs` 處理 bitset。`Zbc` 是 crypto 專用，除非你寫密碼學否則碰不到。

### 「B 擴充」有個歷史問題

有些舊文件把 `B` 寫成一個單一 `-march=rv64gcb`。**2021 年後 spec 改了**，B 被拆成四個 Z 子集。現在應該用：

```
-march=rv64gc_zba_zbb_zbc_zbs
```

或者 2025 年後的 shortcut：

```
-march=rv64gc_b       # 有些 compiler 支援，等於上面
```

但遇到舊文件說 `rv64gcb` 要當心它具體指哪個子集 — 通常是 Zbb。

## Zbb：日常好用的 bit 操作

這是最常用的子集：

### 計數與找位
```
clz      rd, rs        # count leading zeros
ctz      rd, rs        # count trailing zeros
cpop     rd, rs        # population count (popcount)
clzw     rd, rs        # 32-bit 版 (RV64)
ctzw     rd, rs
cpopw    rd, rs
```

`clz` / `ctz` 對「找 MSB / LSB 位置」是一步到位。沒 Zbb 時要寫 10 行。

### Rotate

```
rol      rd, rs1, rs2      # rotate left
ror      rd, rs1, rs2      # rotate right
rori     rd, rs1, shamt    # rotate right immediate
rolw / rorw / roriw        # 32-bit 版
```

`rol` / `ror` 處理 hash 函式、crypto primitives 常見。舊版 RISC-V 要用 `(x << n) | (x >> (XLEN - n))` 組兩條加一次 or。

### Min / Max

```
min      rd, rs1, rs2      # signed
minu     rd, rs1, rs2      # unsigned
max      rd, rs1, rs2      # signed
maxu     rd, rs1, rs2      # unsigned
```

這就是 Ch 6 提到的「比 Zicond 更直接的 cmov 替代品」— 對 `a > b ? a : b` 一條搞定。compiler 優先用這個。

### Byte / sign 操作

```
sext.b   rd, rs        # sign-extend byte to XLEN
sext.h   rd, rs        # sign-extend half
zext.h   rd, rs        # zero-extend half
zext.b   rd, rs        # pseudo, 實際用 andi
rev8     rd, rs        # byte-reverse (endian swap)
orc.b    rd, rs        # byte-wise or-combine (每個 byte 內 OR 後判斷非零)
```

`rev8` 對 byte-swap（網路字節序轉換）超有用。`orc.b` 看起來冷門，其實是 strlen 優化的關鍵（一次檢查 8 byte 有沒有 0）。

### 邏輯反向

```
andn     rd, rs1, rs2      # rd = rs1 & ~rs2
orn      rd, rs1, rs2      # rd = rs1 | ~rs2
xnor     rd, rs1, rs2      # rd = ~(rs1 ^ rs2)
```

這些在 bitmask 操作很常見。省一次 `not`。

## Zba：陣列定址的捷徑

處理 C 的 `a[i]` 這種模式。

### 核心指令

```
sh1add   rd, rs1, rs2    # rd = (rs1 << 1) + rs2     (for short arrays, *2)
sh2add   rd, rs1, rs2    # rd = (rs1 << 2) + rs2     (*4, for int arrays)
sh3add   rd, rs1, rs2    # rd = (rs1 << 3) + rs2     (*8, for long/ptr arrays)
```

看例子：`int a[N]; return a[i];`

無 Zba：
```asm
slli  t0, a0, 2          # i * 4
add   t0, t0, a1         # &a[i]
lw    a0, 0(t0)
```

有 Zba：
```asm
sh2add t0, a0, a1        # t0 = (i << 2) + &a[0]
lw     a0, 0(t0)
```

省一條。對 hot loop 很值。

### 有 RV64 專屬的 `add.uw` / `sh*add.uw`

這些處理「把一個 unsigned 32-bit 值擴展成 64-bit 再加/shift-add」。主要服務 hash / array-indexing 的 32-bit index。寫 kernel / compiler 後端會看到。

### Zba 對 pointer-arithmetic 影響

C 的 `p + i` 當 `i` 是 `size_t`、`p` 指向 `long`（8 byte）時：

```
sh3add p_new, i, p
```

一條指令。這是**現代 RISC-V 編譯 HPC code 的標配**。

## Zbs：單 bit 操作

處理 `flags |= (1 << n)` / `flags &= ~(1 << n)` / `if (flags & (1 << n))` 這類模式。

```
bset     rd, rs1, rs2        # rd = rs1 | (1 << rs2)
bclr     rd, rs1, rs2        # rd = rs1 & ~(1 << rs2)
binv     rd, rs1, rs2        # rd = rs1 ^ (1 << rs2)
bext     rd, rs1, rs2        # rd = (rs1 >> rs2) & 1

bseti / bclri / binvi / bexti        # immediate 版 (shamt)
```

**`bext` 是很好用**：一條指令從任意 bit 位置抽出 1 bit。

例子：`return (x >> 5) & 1;`

無 Zbs：
```asm
srli t0, a0, 5
andi a0, t0, 1
```

有 Zbs：
```asm
bexti a0, a0, 5
```

## Zbc：carry-less multiply

給 crypto / CRC 用。一般應用程式基本碰不到，除非你寫 AES-GCM、GHASH 或 CRC32。

```
clmul    rd, rs1, rs2    # carry-less multiply, low half
clmulh   rd, rs1, rs2    # carry-less multiply, high half
clmulr   rd, rs1, rs2    # carry-less multiply, reversed
```

**carry-less** 的意思是加法不進位 — 等於在 GF(2) 上做多項式乘法。這是 CRC 的數學基礎。

C 的 intrinsic 叫 `__riscv_clmul_*`。之後 `compiler_backend` 會看到怎麼寫一個 pass 把某些 pattern map 到 `clmul`。

## Compiler 什麼時候自動用 B 擴充

GCC 13 / LLVM 16+ 開始積極：

- **Zbb**：任何 `__builtin_clz` / `__builtin_popcount` / `__builtin_ctz` / `std::max` / `std::min` / byte-swap 都會用。
- **Zba**：`a[i]` 自動用 `sh*add`。
- **Zbs**：bit-set / clear 的 pattern 被 InstCombine pass 改寫成 `bseti` 等。
- **Zbc**：除非你手寫 intrinsic 或用 `__builtin_clmul`，compiler **不會** auto-vectorize 出來。

但：**如果 `-march` 沒寫，compiler 當作沒有**。RISC-V 沒有 runtime feature detection（至少沒廣泛部署），所以你想要 Zbb，就得 `-march=rv64gc_zbb`。發 binary 到多種硬體是個難題（Ch 19 會講 profile）。

## 與 ARM / x86 比較

B 擴充大致對應：

| 操作 | RISC-V B | ARMv8 | x86-64 |
|------|----------|-------|--------|
| popcount | `cpop` (Zbb) | `cnt`   | `popcnt` (SSE4.2) |
| clz | `clz` (Zbb) | `clz` | `lzcnt` (BMI1) |
| ctz | `ctz` (Zbb) | `rbit` + `clz` | `tzcnt` (BMI1) |
| rotate | `rol` / `ror` (Zbb) | `ror` | `rol` / `ror` |
| byte swap | `rev8` (Zbb) | `rev` | `bswap` |
| clmul | `clmul` (Zbc) | `pmull`  | `pclmulqdq` |
| bit extract | `bext` (Zbs) | `ubfx` | `bextr` (BMI1) |
| shift-add | `sh*add` (Zba) | 整合到 LDR | LEA |

**x86 的 LEA（Load Effective Address）是經典的 shift-add 融合**，RISC-V 的 Zba 是對應。看成「LEA on RISC-V」沒錯。

## 一個小 benchmark：memset 的差距

手寫一個把 array 每個 bit 數成 popcount 的迴圈：

```c
int sum_popcount(unsigned *a, int n) {
    int s = 0;
    for (int i = 0; i < n; i++) s += __builtin_popcount(a[i]);
    return s;
}
```

| `-march`           | 內迴圈指令數 | 相對 baseline 速度 |
|---------------------|-------------|--------------------|
| `rv64gc`           | ~14 (軟體 popcount) | 1.0x |
| `rv64gc_zbb`       | 4 (`cpop` + sh)     | ~3.5x |
| `rv64gc_zbb_zba`   | 4 (少一條加法)      | ~3.6x |

SiFive 面試真的會問「popcount 的 Zbb 版跟無 Zbb 版差幾 cycle」。準備時手寫一次、用 `llvm-mca` 量一次。

## 常見誤會

1. **「Zbb 只是把多條合併成一條」**：不完全。`cpop` 在硬體上通常是 **單 cycle latency**（不是走多層 adder）。效能差距不只來自指令數。
2. **「有 Zba 就不用寫 `p[i]`」**：Zba 是 compiler 用，你不是直接寫它。C 層寫得自然，compiler 會自己用 `sh3add`。
3. **「B 擴充可以替代 V 擴充」**：不。B 是 scalar bitmanip（一次處理 1 個 XLEN），V 是 vector（一次處理幾十個）。兩者互補。
4. **「`rev8` = byte swap 整個 word」**：對 RV64 是 8 byte 的 reverse；對 RV32 是 4 byte 的 reverse。**不同 XLEN 下行為不同**，要看 spec 看清楚。
5. **「我可以手寫 Zbs bseti 比 compiler 快」**：通常不會。`bseti x, x, 3` 跟 `ori x, x, 8` 在很多硬體 latency 一樣。compiler 已經挑過了。

## 面試常考：手寫 popcount without Zbb

「如果沒有 `cpop` 指令，你怎麼寫 popcount？」標準答案是 Hacker's Delight 的 SWAR：

```c
unsigned popcount(unsigned x) {
    x = (x & 0x55555555) + ((x >> 1) & 0x55555555);
    x = (x & 0x33333333) + ((x >> 2) & 0x33333333);
    x = (x & 0x0F0F0F0F) + ((x >> 4) & 0x0F0F0F0F);
    return (x * 0x01010101) >> 24;
}
```

大約 12–15 條 RISC-V 指令。理解這個算法代表你懂「為什麼 `cpop` 值得一條指令」。

## 動手練習

1. 寫 `int clz(int x) { return __builtin_clz(x); }`，無 Zbb 跟有 Zbb 各編一次，看差異。
2. 寫一個 endian swap：`return __builtin_bswap64(x);`。對照 `rev8` 與無 Zbb 版的 shift/and/or 組合。
3. 手寫一個「檢查字串是否有 null byte」的 `strlen` 版本，用 Zbb 的 `orc.b`。這是 glibc `strlen` 在 RISC-V 的實作核心。
4. 用 `__builtin_clmul(a, b)`（有的編譯器沒 intrinsic，可以 inline asm）寫 CRC32 的一部分，對照 Intel 的 pclmulqdq 實作。
5. 編一個不用 B 擴充的 bitset 操作：`flags |= (1ULL << pos);`，跟開 Zbs 的 `bseti`。觀察 `pos` 是常數 vs 變數時各用哪個 variant（`bseti` vs `bset`）。

## 自我檢核

- [ ] 我能說出 Zba / Zbb / Zbc / Zbs 四個子集的分工
- [ ] 我能寫出 SWAR popcount 演算法並解釋它跟 `cpop` 的差距
- [ ] 我能解釋 `sh3add` 為什麼對 pointer arithmetic 重要
- [ ] 我知道 Zbs 的 `bext` 跟 `andi + srli` 的差異
- [ ] 我能讀 `-march=rv64gc_zba_zbb_zbs` 這類字串並拆字

下一章進 V 擴充 — RISC-V 最複雜、最有爭議、也最有潛力的擴充。理解它要重新 reset 你對「向量指令」的既有印象（因為它不是 SSE / AVX / NEON 的直譯）。

→ [Ch 8 V 擴充：vector、vtype、LMUL 心法](./08-vector-extension.md)
