# Ch 39 — Constant-Time Programming：和 Compiler 及 CPU 作戰

> **目標**：能寫出 constant-time 的 comparison、conditional swap、table lookup（C 語言），理解為什麼這比想像中更難。

---

## 為什麼需要這個？

Ch 38 告訴你：side-channel attack 的核心是「execution time / cache pattern 取決於 secret data」。防禦的核心原則也對稱：

> **讓所有可觀察的行為不取決於 secret data。**

這就是 constant-time programming——不是說程式花固定時間（那叫 fixed-time），而是說 **程式的執行路徑和記憶體存取模式不隨 secret 變化**。

聽起來容易。實際上你要對抗三個敵人：

1. **語言語意**：C 的 `if (secret)` 會編譯成 branch，branch 有不同路徑
2. **Compiler**：你寫的 branchless code，compiler 可能「幫你」優化成 branch version
3. **CPU microarchitecture**：即使 machine code 沒有 branch，CPU 的 speculative execution 可能引入 timing difference

---

## 先建立直覺

```
Naive comparison（不安全）：

bool compare(const uint8_t *a, const uint8_t *b, size_t n) {
    for (size_t i = 0; i < n; i++) {
        if (a[i] != b[i])        ← 第一個不同的 byte 就返回
            return false;         ← 匹配 0 byte: 快
    }                             ← 匹配 n-1 byte: 慢
    return true;                  ← 匹配 n byte: 最慢
}

              時間
   全不同 ────┤
              │■
              │
   差一個 ────┤
              │        ■
              │
   全相同 ────┤
              │              ■
              └──────────────→

攻擊者測量時間 → 推斷匹配了多少 byte → 逐 byte 爆破


Constant-time comparison（安全）：

bool ct_compare(const uint8_t *a, const uint8_t *b, size_t n) {
    uint32_t diff = 0;
    for (size_t i = 0; i < n; i++)
        diff |= a[i] ^ b[i];     ← 每個 byte 都檢查，不提前返回
    return diff == 0;
}

              時間
   全不同 ────┤
              │■
              │
   差一個 ────┤
              │■                  ← 時間相同！
              │
   全相同 ────┤
              │■                  ← 時間相同！
              └──────────────→
```

---

## 核心概念：三條鐵律

### 鐵律 1：No secret-dependent branch

```c
// ✗ 不安全：branch 取決於 secret
if (key_bit == 1) {
    result = result * base % n;   // multiply
}

// ✓ 安全：用 bit mask 代替 branch
uint64_t mask = -(uint64_t)(key_bit);  // key_bit=1 → mask=0xFFFF...
                                        // key_bit=0 → mask=0x0000...
uint64_t temp = result * base % n;
result = (temp & mask) | (result & ~mask);
// key_bit=1 → result = temp（做了 multiply）
// key_bit=0 → result = result（沒變）
// 兩條路徑執行的指令數相同
```

`-(uint64_t)(key_bit)` 的原理：在二補數表示中，`-0 = 0x0000...0000`，`-1 = 0xFFFF...FFFF`。這給你一個全 0 或全 1 的 mask，用來做 branchless select。

### 鐵律 2：No secret-dependent memory access

```c
// ✗ 不安全：T-table lookup，index 取決於 secret
uint32_t val = T_table[secret_byte];
// secret_byte 不同 → 存取不同的 cache line → cache side-channel

// ✓ 安全：access ALL entries，用 mask 選出正確的
uint32_t val = 0;
for (int i = 0; i < 256; i++) {
    uint32_t mask = ct_eq(i, secret_byte);  // i == secret_byte → 0xFFFFFFFF
                                             // i != secret_byte → 0x00000000
    val |= T_table[i] & mask;
}
// 每次都存取所有 256 個 entry → cache pattern 固定
// 代價：慢 256 倍
```

### 鐵律 3：No secret-dependent loop count

```c
// ✗ 不安全：loop 次數取決於 secret（例如 big integer 的有效位數）
for (int i = 0; i < num_significant_bits(secret_key); i++) {
    // ...
}

// ✓ 安全：永遠 loop 固定次數（key 的最大可能長度）
for (int i = 0; i < MAX_KEY_BITS; i++) {
    // ...
}
```

---

## 底層機制：Constant-Time 的 Primitive 工具箱

### 範例一：C 的 constant-time byte comparison

```c
#include <stdint.h>
#include <stddef.h>

/* 不能用 memcmp（short-circuit）。OpenSSL 有 CRYPTO_memcmp，
   但自己寫 crypto library 時需要自己實作。 */
volatile int ct_compare(const uint8_t *a, const uint8_t *b, size_t n) {
    volatile uint32_t diff = 0;
    for (size_t i = 0; i < n; i++)
        diff |= a[i] ^ b[i];
    return (int)diff;  /* 0 = 相等，非零 = 不同 */
}

/* ct_eq：返回 0xFFFFFFFF（相等）或 0x00000000（不等）*/
static inline uint32_t ct_eq(uint32_t a, uint32_t b) {
    uint32_t diff = a ^ b;
    diff |= -diff;    /* 非零值的 MSB 被設成 1 */
    diff >>= 31;      /* 取 MSB：0 或 1 */
    return diff - 1;  /* 0→0xFFFFFFFF, 1→0x00000000 */
}

/* ct_select：mask=全1 返回 a，mask=全0 返回 b */
static inline uint32_t ct_select(uint32_t mask, uint32_t a, uint32_t b) {
    return (a & mask) | (b & ~mask);
}
```

驗證 `ct_eq` 的邏輯：

```
ct_eq(5, 5)：
  diff = 5 ^ 5 = 0
  diff | -diff = 0 | 0 = 0
  diff >> 31 = 0
  return 0 - 1 = 0xFFFFFFFF ✓（相等）

ct_eq(5, 3)：
  diff = 5 ^ 3 = 6
  -diff = ...11111010 (二補數)
  diff | -diff = ...11111110 → MSB = 1
  diff >> 31 = 1
  return 1 - 1 = 0 ✓（不等）
```

### 範例二：Constant-time conditional swap

```c
/* 用途：Montgomery ladder（Ch 22 ECC）、sorting network */
void ct_cswap(uint32_t *a, uint32_t *b, uint32_t condition) {
    uint32_t mask = -(condition != 0);  /* 全1 或 全0 */
    uint32_t temp = (*a ^ *b) & mask;   /* mask=全1 → a^b; mask=全0 → 0 */
    *a ^= temp;  /* mask=全1: a ^= (a^b) = b; mask=全0: a ^= 0 = a */
    *b ^= temp;  /* mask=全1: b ^= (a^b) = a; mask=全0: b ^= 0 = b */
}
```

---

## 進一步用法：Compiler 是你的敵人

### 問題：Compiler 會「優化掉」你的 constant-time code

```c
/* 你寫的 constant-time comparison */
int ct_compare_v1(const uint8_t *a, const uint8_t *b, size_t n) {
    uint32_t diff = 0;
    for (size_t i = 0; i < n; i++)
        diff |= a[i] ^ b[i];
    return diff == 0;
}
```

GCC `-O2` 可能觀察到 `diff |= x` 只要一次 x != 0 結果就確定，優化成 early-exit branch——不再 constant-time。2017 年 OpenSSL 就因為 Clang 做了這種優化而緊急修復。

### 防禦 1：volatile

```c
int ct_compare_v2(const uint8_t *a, const uint8_t *b, size_t n) {
    volatile uint32_t diff = 0;  /* compiler 不能優化掉對它的讀寫 */
    for (size_t i = 0; i < n; i++)
        diff |= a[i] ^ b[i];
    return diff == 0;
}
```

`volatile` 阻止 compiler eliminate loop 迭代。缺點：可能放棄其他合法優化（如 loop unrolling）。

### 防禦 2：Compiler barrier（inline assembly）

```c
/*
 * compiler barrier：告訴 compiler「在這裡假設所有記憶體都可能被修改」
 * compiler 必須在 barrier 之前完成所有 pending 的寫入
 * 並在 barrier 之後重新載入所有變數
 *
 * 注意：這只是 compiler barrier，不是 CPU memory barrier
 * CPU 仍然可能 reorder 指令（但對 single-thread 沒影響）
 */
#define CT_BARRIER(x) __asm__ __volatile__("" : "+r"(x) : : "memory")

int ct_compare_v3(const uint8_t *a, const uint8_t *b, size_t n) {
    uint32_t diff = 0;
    for (size_t i = 0; i < n; i++) {
        diff |= a[i] ^ b[i];
        CT_BARRIER(diff);  /* 阻止 compiler 分析 diff 的值 */
    }
    return ct_is_zero(diff);
}

/*
 * ct_is_zero：constant-time 判斷是否為零
 * 不能直接用 diff == 0（compiler 可能翻譯成 branch）
 */
static inline uint32_t ct_is_zero(uint32_t x) {
    CT_BARRIER(x);
    /* 利用 unsigned underflow：0 - 1 = 0xFFFFFFFF（MSB = 1）*/
    /* 非零值 - 1：MSB 可能是 0 或 1，但 x | (0 - x) 的 MSB 一定是 1 */
    x |= -x;
    x >>= 31;   /* 0 → 0, 非零 → 1 */
    return x ^ 1; /* 0 → 1, 1 → 0 */
}
```

`"+r"(x)` 告訴 GCC：「x 在 inline assembly 裡被讀和寫了，但我不告訴你怎麼改的。」compiler 因此無法追蹤 `diff` 的 value range——所有基於 value range 的優化（包括 early-exit）都被阻斷。

### 防禦 3：用 verified constant-time 的 library

最可靠的做法：不自己寫，用已被驗證的 library。

| Library | 語言 | constant-time 保證 |
|---|---|---|
| OpenSSL `CRYPTO_memcmp` | C | 手寫 + review |
| libsodium `sodium_memcmp` | C | 整個 library 設計為 ct |
| BoringSSL | C | Google 維護，有 ct-verif 驗證 |
| ring (Rust) | Rust | 底層用 assembly 確保 ct |
| BearSSL | C | Thomas Pornin，ct 是設計原則 |

---

## 底層機制：AES Bitsliced 實作

T-table 因為 secret-dependent memory access 而不安全。Bitslicing 的解法：**不用 lookup table，用 boolean circuit 在 register 裡算 S-box**。

把 128 個 AES state 的同一 bit 位置打包成一個 128-bit register → 128 個 S-box 同時用 AND/XOR/NOT 計算 → 全在 register 裡 → 0 次 memory access → 沒有 cache side-channel。

代價：需要同時 batch 128 個 block → 適合 CTR mode，不適合 CBC。BearSSL 的 AES 就是 bitsliced 實作。

---

## 對比與取捨

| 防禦方法 | 適用場景 | 效能代價 | 可靠性 | 備註 |
|---|---|---|---|---|
| `volatile` | 簡單變數 | 低（阻止部分優化） | 中（不保證所有 compiler） | 最簡單的第一道防線 |
| compiler barrier | loop 內部 | 極低 | 高（GCC/Clang） | 需要 inline asm，不可移植 |
| assembly 實作 | 關鍵路徑 | 無（手寫最佳化） | 最高 | 維護成本高，per-architecture |
| bitslicing | AES 等 S-box | 中（需要 batch） | 高 | 只在 CTR/counter mode 有效率 |
| AES-NI | AES | 零（硬體加速） | 最高 | 需要 CPU 支援 |
| ct-verif 驗證 | 整個 library | 編譯期 | 高 | 形式化驗證 constant-time |

---

## 踩雷集錦

### 雷 1：「Python 不需要擔心 constant-time」

**錯**。Python 的 `int` 是 arbitrary-precision，底層用 variable-length digit array，運算時間和 digit 數量成正比。`pow(a, 1, n)` 和 `pow(a, n-1, n)` 的時間差異巨大——洩漏指數大小。Python 的 `hmac.compare_digest()` 是 constant-time 的（CPython 用 C 實作）。永遠用它做密碼學比較，不要用 `==`。

### 雷 2：Compiler 在 -O2 把你的 constant-time 邏輯優化掉

真實案例：2018 年有人發現 GCC 8.1 把以下 constant-time code 優化成了 conditional branch：

```c
uint32_t ct_mask(uint32_t bit) {
    return -(bit & 1);  // 你期望：branchless neg
}

// GCC 8.1 -O2 生成：
//   test edi, 1
//   je .L2           ← branch！
//   mov eax, -1
//   ret
// .L2:
//   xor eax, eax
//   ret
```

修復方法：加 compiler barrier。

```c
uint32_t ct_mask(uint32_t bit) {
    bit &= 1;
    __asm__ __volatile__("" : "+r"(bit));
    return -bit;
}
```

### 雷 3：comparison 後的 branch 也會洩漏

```c
// 你的 constant-time comparison 是對的：
uint32_t diff = ct_compare(mac_received, mac_computed, 32);

// 但接下來你寫了：
if (diff != 0) {       // ← 這個 branch 取決於 secret（diff 和 MAC 相關）
    return FAILURE;     //    洩漏 MAC 是否正確
}
return SUCCESS;

// 改成：
return ct_select(ct_is_zero(diff), SUCCESS, FAILURE);
```

### 雷 4：division 和 modulo 在某些架構上不是 constant-time

ARM Cortex-M3/M4 的 `UDIV` 指令的執行時間取決於 operand 的值（前導零越多越快）。如果 operand 是 secret，division 就是 timing leak。

x86 的 `DIV` 指令在大多數微架構上也不是 constant-time。

### 雷 5：variable-length encoding 是 timing leak 的溫床

```c
// RSA 的 PKCS#1 v1.5 padding：
// 0x00 || 0x02 || [random non-zero bytes] || 0x00 || [message]
// 移除 padding 時需要找到第二個 0x00 → loop 次數取決於 padding 長度
// → 取決於 message 長度 → timing leak
// 這就是 Bleichenbacher attack 的入口
```

---

## 進階

### ct-verif：形式化驗證 constant-time

2016 年 Almeida 等人提出 ct-verif：把程式 P 複製成 P₁、P₂，給不同 secret input、相同 public input，用 SMT solver 驗證兩者的 observable behavior（branch taken、memory address）是否完全相同。成立 → constant-time；不成立 → solver 給出反例。

### Spectre 對 constant-time 的挑戰

即使 machine code 沒有 secret-dependent branch，Spectre v1 讓 CPU 推測性地執行越界 load，cache 痕跡在 rollback 後仍在。防禦：Retpoline、index masking、LFENCE、Clang `-mspeculative-load-hardening`。

### Constant-time 在不同語言中的現實

| 語言 | constant-time 可行性 | 備註 |
|---|---|---|
| C | **可行**（需要和 compiler 鬥爭） | 用 volatile / asm barrier |
| Rust | **可行**（`subtle` crate） | `subtle::ConstantTimeEq` trait |
| Go | **部分可行** | `crypto/subtle.ConstantTimeCompare` |
| Python | **極難** | 底層是 C，但 Python 層無法控制 branch |
| Java | **極難** | JIT compiler 會優化；GC 引入 timing noise |
| JavaScript | **不可能** | JIT + GC + event loop = 無法保證任何 timing |

結論：**密碼學的 constant-time 部分用 C 或 Rust 寫**。高層語言只負責 API 調用，不負責 primitive 實作。

---

## 動手練習

1. **驗證 compiler 是否保留 constant-time**：把範例一的 `ct_compare` 用 `gcc -O0 -S` 和 `gcc -O2 -S` 分別編譯，比較 assembly output。在 `-O2` 的 output 裡找是否有 conditional jump（`je`、`jne`）。

2. **加入 compiler barrier**：如果練習 1 發現 `-O2` 引入了 branch，加入 `CT_BARRIER` 後重新編譯，確認 branch 消失。

3. **測量 constant-time vs naive 的時間差**：寫一個 C 程式，分別用 naive comparison 和 ct_compare 比較兩個 32-byte buffer。用 `clock_gettime(CLOCK_MONOTONIC)` 測量 10,000 次的時間分布。畫 histogram，觀察 naive 版本的 bimodal 分布（匹配 / 不匹配兩個 peak）vs ct_compare 的 unimodal 分布。

4. **用 Python `hmac.compare_digest` 重寫**：把範例一的 timing attack PoC 中的 `vulnerable_compare` 替換成 `hmac.compare_digest`，驗證 timing attack 失敗。

5. **閱讀 BearSSL 的 constant-time 指南**：https://www.bearssl.org/constanttime.html 。列出 Thomas Pornin 提到的至少三個你在本章沒看到的 constant-time technique。

---

## 重點整理

```
Constant-time programming 的三條鐵律：
  1. No secret-dependent branch    → 用 bit mask 代替 if
  2. No secret-dependent memory access → access all entries 或用 bitslicing
  3. No secret-dependent loop count → 固定迭代次數

工具箱：
  ct_eq(a, b)      → 全 1 mask（相等）或全 0 mask（不等）
  ct_select(m,a,b) → branchless select
  ct_cswap(a,b,c)  → branchless conditional swap
  ct_is_zero(x)    → branchless zero check

Compiler 防禦：
  volatile          → 阻止 compiler 消除變數的讀寫
  compiler barrier  → __asm__ __volatile__("" : "+r"(x) : : "memory")
  assembly 實作     → 繞過 compiler 的所有優化

AES 的 constant-time 方案：
  AES-NI（硬體指令） → 最快最安全，但需要 CPU 支援
  Bitslicing        → 純軟體，batch 處理，CTR mode 適用
  T-table           → 不安全（secret-dependent memory access）

Python 注意事項：
  永遠用 hmac.compare_digest()，不要用 ==
  Python 的 int 是 variable-length → 運算時間洩漏大小
```

---

## 自我檢核

- [ ] 能寫出 constant-time 的 byte comparison（C 語言），不看參考
- [ ] 能解釋 `ct_eq` 的 bit manipulation 為什麼能產生全 0 或全 1 mask
- [ ] 能實作 constant-time conditional swap（用 XOR trick）
- [ ] 能解釋為什麼 compiler 是 constant-time code 的敵人（舉出具體的優化）
- [ ] 知道 `volatile` 和 compiler barrier 的區別和適用場景
- [ ] 能解釋 bitslicing 如何避免 T-table 的 cache side-channel
- [ ] 知道 Python 中 `==` 和 `hmac.compare_digest()` 在密碼學上的差異
- [ ] 能說出至少一個 constant-time 的形式化驗證工具

---

## 延伸閱讀

- **BearSSL Constant-Time Crypto Guide（Thomas Pornin）**
  - **讀哪裡**：全文（不長，約 20 分鐘）
  - **學什麼**：最實用的 constant-time programming 指南——涵蓋 integer comparison、conditional swap、table lookup、big integer arithmetic 的 constant-time 寫法
  - **關聯**：本章所有 primitive 的權威參考

- **"ct-verif: Verifying Constant-Time Implementations by Abstract Interpretation"（Almeida et al., 2016）**
  - **讀哪裡**：Section 3（self-composition 方法）
  - **學什麼**：如何用 SMT solver 自動驗證 constant-time——不再依賴人工 review
  - **關聯**：本章進階段落的理論基礎

- **"Spectre Attacks: Exploiting Speculative Execution"（Kocher et al., 2019）**
  - **讀哪裡**：Section 4.1（bounds check bypass）
  - **學什麼**：即使 machine code 沒有 secret-dependent branch，CPU 推測執行也能造成 cache side-channel
  - **關聯**：constant-time 在 speculative execution 時代的挑戰

- **`subtle` crate documentation（Rust）**
  - **讀哪裡**：`ConstantTimeEq` trait 和 `Choice` type 的 API 文件
  - **學什麼**：Rust 如何在 type system 層級強制 constant-time——比 C 的 volatile/barrier 更 systematic

---

→ [Ch 40 — 隨機數失敗史](./40-rng-failures.md)
