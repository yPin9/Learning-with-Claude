# Ch 39 — Constant-time programming

> 目標：寫 const-time 密碼學程式碼的紀律。沒有 secret-dependent branch、沒有 secret-dependent memory access、bit-twiddling 替換 if、`memcmp` 為什麼有毒（用 `CRYPTO_memcmp`）、編譯器把你 const-time code 優化掉的真實案例。

## Const-time 是什麼

```
"Constant-time" = 執行時間不依賴 secret data
```

具體：

- **時間** 不洩漏（CPU cycle 與 secret 無關）
- **memory access pattern** 不洩漏（cache state 與 secret 無關）
- **branch** 不依賴 secret（branch predictor 不洩漏）
- **power consumption** 不洩漏（更難，HW 級保護）

通用 software 達前三項；power 要 HW 配合。

## 三類禁忌

### 禁忌 1：secret-dependent branch

```c
// BAD
if (secret_byte == 0) {
    do_something();
} else {
    do_something_else();
}
// branch predictor / pipeline timing 洩漏

// GOOD
unsigned char mask = (secret_byte == 0) ? 0xFF : 0x00;  // 危險：仍 branch
// 改用 bit-tricks:
unsigned char mask = -(unsigned char)(((secret_byte | (secret_byte - 1)) >> 7) & 1);
// or use compiler intrinsics
```

更乾淨：

```c
// const-time conditional move
static inline uint8_t ct_cmov(uint8_t bit, uint8_t a, uint8_t b) {
    // bit = 0 or 1
    uint8_t mask = -bit;  // 0xFF if bit=1, 0x00 if bit=0
    return (a & mask) | (b & ~mask);
}

// 用法：result = ct_cmov(secret_bit, value_if_1, value_if_0);
```

### 禁忌 2：secret-dependent memory access

```c
// BAD: lookup table indexed by secret
output = SBOX[secret_byte];   // cache timing leak

// GOOD: scan 整 table
uint8_t result = 0;
for (int i = 0; i < 256; i++) {
    uint8_t mask = -(uint8_t)(i == secret_byte);  // 1 if match, 0 else
    result |= SBOX[i] & mask;
}
// 永遠 access 整 table，cache 不洩漏
```

代價：**256× 慢**。但對只 access 一個 byte 是必須。AES 的 T-table 怎麼處理？要嘛硬體 AES-NI，要嘛 bitslice 整個換 SIMD。

### 禁忌 3：secret-dependent branch（loop variant）

```c
// BAD: loop count 看 secret
while (key[i] != 0) i++;   // 找 null terminator
return i;
// 不同 key 不同 iteration count

// GOOD: fixed loop bound
size_t result = 0;
size_t found = 0;
for (size_t i = 0; i < KEY_MAX_LEN; i++) {
    found |= (key[i] == 0);
    result += !found;  // 但要 const-time addition
}
```

實務上 secret length 通常 fixed（KEY_SIZE = 32 byte for AES-256）— 直接 loop 32 次。

## `memcmp` 為什麼有毒

```c
// BAD: 比較 MAC
if (memcmp(received_mac, expected_mac, 32) == 0) accept();
```

`memcmp` 在第一個不同 byte 就 return。**timing 洩漏哪個位置不同**：

```
攻擊者試 MAC byte by byte：
  1. 全 0：memcmp 在 byte 0 fail（第 1 個 byte 不對）
  2. 改 byte 0 = 各種值：找一個讓 memcmp 在 byte 1 才 fail
     → byte 0 對了
  3. 重複到 byte 31
共 256 × 32 = 8192 次 query，破 256-bit MAC
```

實際比這複雜（需要 timing accuracy），但網路上 demo 過。

修復：const-time compare。

```c
int CRYPTO_memcmp(const void *a, const void *b, size_t n) {
    const uint8_t *pa = a, *pb = b;
    uint8_t diff = 0;
    for (size_t i = 0; i < n; i++) {
        diff |= pa[i] ^ pb[i];
    }
    return diff;  // 0 if equal
}
```

OpenSSL 提供 `CRYPTO_memcmp`、Python `hmac.compare_digest`、Go `subtle.ConstantTimeCompare`、Rust `subtle` crate。

**MAC verify 永遠用 const-time compare**。

## 編譯器是敵人

你寫 const-time C code，**編譯器可能優化掉**：

```c
// 寫的 const-time
uint8_t result = 0;
for (int i = 0; i < n; i++) {
    uint8_t mask = -(uint8_t)(i == target);
    result |= data[i] & mask;
}
```

聰明 compiler 看到：「`mask` 只在 `i == target` 時 0xFF，其他時候 0」 → 「**直接** if branch / lookup 比較快」 → 編譯成 secret-dependent branch！

修復：

1. **用 `volatile`** 防優化（部分有效）
2. **用 inline asm** 強制（最可靠但 platform-specific）
3. **特殊 macro** 在 specific compiler 隱藏 intent
4. **測試**：用 `dudect`、`ctgrind` 在不同 compiler / opt level 驗證

```c
// Volatile 版（部分防優化）
volatile uint8_t mask = -(uint8_t)(i == target);
```

這個是密碼工程的 nightmare。即使最好的 const-time code 在 GCC 14 / Clang 18 / 不同 -O level 行為可能變。

## 工具：dudect

Daniel Bernstein 等的 timing leak detector：

```bash
# 對你的 function 跑 random + fixed input pair
# 統計 timing distribution，看 mean 是否一致
# t-test：< 10⁻¹⁰ p-value 才算 const-time
dudect ./my_crypto_function
```

**現代密碼 library 都跑 dudect**。OpenSSL、libsodium、ring 等。

## ctgrind

基於 valgrind 的：把 secret 標 "uninitialized"，valgrind 偵測 secret 影響 control flow / memory access → 報警。

```bash
ctgrind ./my_crypto_program
```

不需要重複 timing，**直接靜態看 data flow**。對發現 const-time bug 極有效。

Curve25519 reference C 實作就是用 ctgrind 驗。

## Rust 的 subtle crate

```rust
use subtle::{ConstantTimeEq, ConditionallySelectable};

let a: [u8; 32] = ...;
let b: [u8; 32] = ...;

// const-time equality
let eq: subtle::Choice = a.ct_eq(&b);
let is_equal: bool = eq.into();   // 仍 const-time

// const-time conditional swap
let mut x = 5u32;
let mut y = 10u32;
u32::conditional_swap(&mut x, &mut y, choice);
```

Rust trait system 強制 const-time API。比 C 寫起來安全得多。

## libsodium：default const-time

libsodium 全部 API const-time。**你不用想 const-time 細節**：

```c
crypto_secretbox_easy(...)  // 內部 const-time
crypto_box_easy(...)
crypto_sign(...)
```

設計目標就是「**安全 default + 普通工程師也不會踩雷**」。**寫密碼 application 用 libsodium**。

## 哪些 operation 必須 const-time

```
✓ 密碼比較 (MAC, password hash)
✓ AES SBOX lookup
✓ RSA modpow (secret exponent)
✓ ECC scalar multiplication
✓ Random nonce generation (no shortcut based on RNG state)
✓ HMAC verification
✓ Key derivation
✓ Signature verification (內部 hash 比較等)
```

**幾乎整個密碼 library 都要**。

## 哪些不需要

```
- Public key 驗證（pub 是 public，timing 不 secret）
- Hash function 對 public message
- TLS handshake 的非 secret part
- Plaintext 處理（一旦解密完）
```

但區分「secret」與「public」要小心 — sometimes public 的東西其實 leak secret（如 RSA modulus 給 timing 信息）。

## 特殊情境：Web crypto

JavaScript 的 `Math.random` 不安全前面提過。**`crypto.subtle` 也有踩雷**：

```js
// BAD
if (receivedMac === expectedMac) accept();   // string compare 非 const-time

// GOOD
async function constantTimeEqual(a, b) {
    if (a.length !== b.length) return false;
    let result = 0;
    for (let i = 0; i < a.length; i++) {
        result |= a.charCodeAt(i) ^ b.charCodeAt(i);
    }
    return result === 0;
}
```

JavaScript 多數 string compare 沒 const-time 保證。**寫 crypto 一定用 const-time helper**。

## 一個常見誤解

「我的密碼 server 在 internal network，沒人 timing attack」

**錯**。

- **insider threat**：在你 datacenter 的別的 service 仍能 timing attack
- **co-located VM**（cloud）：肯定可以
- **被入侵的 sidecar / container**：能 attack 主 service
- **未來分析錄製的 traffic**：歷史 timing 仍 leak

const-time 是 **defense-in-depth 的標準層級**。即使「現在沒人攻」，留下 timing leak 是定時炸彈。

## 自我檢核

- [ ] 我能寫 `ct_cmov` 沒 branch
- [ ] 我能寫 const-time `memcmp`
- [ ] 我能用 SBOX 例子說明 secret-dependent memory access 的 fix
- [ ] 我知道 `volatile` 不是萬靈丹，要 dudect 驗
- [ ] 我能列出至少 5 種必須 const-time 的 operation
- [ ] 我能說出 libsodium / Rust subtle 的 const-time 設計優勢

下一章看隨機數失敗史 — Debian、PS3、Dual_EC_DRBG 等。

→ [Ch 40 隨機數失敗史](./40-randomness-failures.md)
