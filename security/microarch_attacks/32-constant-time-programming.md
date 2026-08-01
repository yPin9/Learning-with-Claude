# Ch 32 — Constant-time 程式設計

> **目標**：讀完能判斷一段 code 是否 constant-time（常數時間）、能把洩漏點修成無分支版本、能用 dudect 在真實硬體上統計驗證。

---

## 為什麼要 constant-time

Ch 11 演示過 AES T-table cache 攻擊：加密函式用 `table[key_byte ^ pt_byte]` 查值，攻擊者觀察 L1 cache miss 率就能還原密鑰。Ch 25 演示過 Hertzbleed：就算把 cache 存取全部填平，Intel Alder Lake 的頻率縮放仍能把 AES key 的 Hamming weight 洩漏出去。

這兩章說明同一件事：**密碼學實作必須讓所有執行特性都與 secret 無關**。「執行特性」包括三條：

1. **執行時間** — 任何 secret 值跑起來都要花同樣的時間（CPU cycle 數相同，不是 wall-clock 近似相同）。
2. **記憶體存取樣式 (memory access pattern)** — 讀取哪些 cache line、讀取順序，不能因 secret 而異。
3. **分支路徑 (branch pattern)** — 走哪條 taken/not-taken，不能因 secret 而異。

違反任何一條，就有 timing side-channel。這三條合稱 **constant-time 三原則**。

Constant-time 程式設計不是效能玄學，是一個可以機械化驗證的工程要求。本章先讓你認識洩漏模式，再給修法工具箱，最後用 dudect 跑真實數據確認。

---

## 常見洩漏模式

以下四類是密碼學 code 最常見的洩漏源，每一類都附能直接觸發 timing leak 的 C 片段。

### 1. Secret-dependent 分支

```c
// 危險：分支路徑因 secret 而異
uint8_t ct_compare_WRONG(const uint8_t *a, const uint8_t *b, size_t len) {
    for (size_t i = 0; i < len; i++) {
        if (a[i] != b[i])   // secret-dependent branch
            return 0;
    }
    return 1;
}
```

CPU 的 branch predictor 在這裡吃 secret，不匹配的位置越早、函式越快回來，攻擊者只需要量執行時間就能二分搜尋出正確 MAC。這是 early-exit timing attack 的教科書案例，HMAC 比較幾乎都死在這裡。

### 2. Secret-index 查表

```c
// 危險：查表索引來自 secret
uint32_t aes_encrypt_WRONG(uint8_t key_byte, uint8_t pt_byte) {
    return Te0[key_byte ^ pt_byte];   // cache miss pattern 洩漏 key_byte
}
```

即使不是真接 `table[secret]`，只要索引與 secret 有函數關係，cache miss 就會洩漏。AES 的 T-table 實作（Ch 11 的主角）全部屬於這類。

### 3. 提早 return（naive strcmp / memcmp）

```c
// 危險：標準庫 memcmp 幾乎都是 early-exit 實作
int check_token_WRONG(const char *input, const char *expected) {
    return memcmp(input, expected, TOKEN_LEN) == 0;
}
```

`<string.h>` 的 `memcmp` 規格書只說回傳值的意義，沒說不能 early-exit。實際上 glibc 和 musl 的 `memcmp` 都在第一個差異位元組就回傳，是完美的 oracle。

### 4. 變長迴圈（跑 secret 次）

```c
// 危險：迴圈長度直接洩漏 secret 的大小
int is_prime_WRONG(uint64_t secret_n) {
    for (uint64_t i = 2; i < secret_n; i++) {  // 跑 secret_n-2 次
        if (secret_n % i == 0) return 0;
    }
    return 1;
}
```

迴圈次數決定執行時間，只需統計數千次呼叫就能準確推算 `secret_n` 的範圍。

---

## 修法工具箱

### 工具一：無分支選擇（ct_select / bitmask）

把 `if (cond) a else b` 改成算術選擇：

```c
// 正確的 constant-time select
// mask 必須是全 0 (cond=false) 或全 1 (cond=true)
static inline uint64_t ct_mask(int cond) {
    // 把 cond 的 LSB 擴展到全 64 位
    // 注意：不能用 -cond，因為有些 ABI 下 int 的負數表示是 IB
    uint64_t m = (uint64_t)(uint32_t)cond;
    m = (-m);          // 0 -> 0x0000...0000, 1 -> 0xFFFF...FFFF
    return m;
}

static inline uint64_t ct_select(int cond, uint64_t a, uint64_t b) {
    uint64_t mask = ct_mask(cond);
    return (mask & a) | (~mask & b);
}
```

用法：

```c
// 安全版本：兩條路都走，結果用 mask 選
uint64_t safe_result = ct_select(secret > threshold, branch_true, branch_false);
```

注意事項：`ct_select` 的兩個參數 `a`、`b` 必須是已計算好的值，不是惰性求值。如果 `a` 或 `b` 本身含有 secret-dependent 操作，必須先把它們都算出來再選。

### 工具二：掃全表（AES / OAEP 修法）

不用 `table[secret]`，改成掃所有 entry 加 mask：

```c
// 安全版本：每次都掃全表，用 mask 只取一個值
uint32_t ct_table_lookup(const uint32_t *table, size_t table_size,
                          uint8_t secret_index) {
    uint32_t result = 0;
    for (size_t i = 0; i < table_size; i++) {
        // 當 i == secret_index 時 mask 全 1，否則全 0
        uint32_t mask = (uint32_t)ct_mask(i == secret_index);
        result |= (table[i] & mask);
    }
    return result;
}
```

這是 AES bitsliced 實作、或 RSA blind padding 的標準手法。代價是把 O(1) 查表變成 O(n) 掃表，AES S-box (256 entry) 每個 byte 要多 256 次讀取。現代 AES-NI 直接在硬體走，不需要這招，但沒有 AES-NI 的環境仍然需要。

### 工具三：固定迴圈 + mask

把 `for (i < secret)` 改成 `for (i < MAX)` 加掩碼控制副作用：

```c
// 危險版本
void process_secret_len(uint8_t *buf, uint8_t secret_len) {
    for (int i = 0; i < secret_len; i++)
        buf[i] ^= 0xFF;
}

// 安全版本：固定跑 MAX_LEN 次，超出 secret_len 的操作被 mask 掩掉
void process_secret_len_ct(uint8_t *buf, uint8_t secret_len, int max_len) {
    for (int i = 0; i < max_len; i++) {
        uint8_t active = (uint8_t)ct_mask(i < secret_len);
        // 只有 active=0xFF 時才真正改 buf[i]
        buf[i] = (buf[i] & ~active) | ((buf[i] ^ 0xFF) & active);
    }
}
```

### 工具四：constant-time memcmp

```c
// 生產可用的 ct_memcmp
// 回傳 0 = 相等, 非零 = 不等（但不洩漏差異位置）
int ct_memcmp(const void *a, const void *b, size_t len) {
    const uint8_t *pa = (const uint8_t *)a;
    const uint8_t *pb = (const uint8_t *)b;
    uint8_t diff = 0;
    for (size_t i = 0; i < len; i++)
        diff |= pa[i] ^ pb[i];   // 累積所有差異，不提早結束
    return diff;   // 0 表示完全相等
}
```

關鍵是 `diff |= ...`：即使找到第一個差異也不跳出，繼續跑完所有 byte，執行時間只與 `len` 有關，與內容無關。

---

## 真跑 dudect 驗證

這是本章最重要的段落。知道怎麼寫還不夠，要用工具**量化確認**你的修法有效。

### dudect 統計原理

Dudect（"Dude, is my code constant time?"）的核心是 **Welch t-test（韋爾奇 t 檢定）**：

1. 把輸入分成兩個 class：class 0 用固定 secret（e.g. `0x00`），class 1 用隨機 secret（e.g. `0xFF` 或任意值）。
2. 各跑數十萬次，分別量 CPU timestamp（`RDTSC`）。
3. 計算兩組執行時間分佈的 t 統計量：

   ```
   t = (mean1 - mean0) / sqrt(var1/n1 + var0/n0)
   ```

4. 判斷標準：**|t| > 4.5** 認為洩漏（對應 p < 0.00001，幾乎不可能是雜訊）。

Dudect 用 t-test 而不是直接比 mean，是因為 mean 的差異可能被方差掩蓋；t-test 同時考慮兩組的散布。

### 實驗設計

以下在 WSL2、Intel i7-10700 上跑兩個版本的「byte array 掃描」，讓 secret 控制迴圈長度（leaky）或只控制 mask（constant-time）：

**Leaky 版本**（secret-dependent 迴圈長度）：

```c
// leaky.c — 跑 secret 次
void do_operation_leaky(uint8_t *buf, uint8_t secret) {
    for (int i = 0; i < secret; i++)
        buf[i] ^= 0xAB;
}
```

**Constant-time 版本**（固定 255 次 + bitmask）：

```c
// ct.c — 永遠跑 255 次，只有 mask 因 secret 而異
void do_operation_ct(uint8_t *buf, uint8_t secret) {
    for (int i = 0; i < 255; i++) {
        uint8_t mask = (uint8_t)ct_mask(i < secret);
        buf[i] = (buf[i] & ~mask) | ((buf[i] ^ 0xAB) & mask);
    }
}
```

### 真實輸出

**Leaky 版（secret-dependent 迴圈長度）**：

```
[MODE] leaky variable-loop (secret-dependent iteration count)
meas:    0.01 M, max t: +164.47, max tau: 1.62e+00, (5/tau)^2: 9.49e+00. Probably not constant time.
```

t = +164.47 遠超過閾值 4.5，而且只跑了 1 萬次（0.01 M）就確認。`(5/tau)^2 = 9.49`，意思是再跑 9.5 倍樣本就能讓 tau（標準化效應量）達到 5，說明 effect size 極大——攻擊者不需要很多 oracle query。

**Constant-time 版（固定 255 次迴圈 + bitmask）**：

```
[MODE] constant-time fixed-loop
meas:    0.07 M, max t:   +1.47, max tau: 5.59e-03, (5/tau)^2: 8.01e+05. For the moment, maybe constant time.
meas:    0.07 M, max t:   +1.43, max tau: 5.44e-03, (5/tau)^2: 8.45e+05. For the moment, maybe constant time.
```

t ≈ 1.4–1.5，遠低於 4.5，300 輪以上未偵測到洩漏。`(5/tau)^2 ≈ 8×10^5`，意思是要讓這個差異達到顯著，需要 80 萬倍的樣本——在統計意義上等同於「沒有洩漏」。

dudect 的輸出不說「confirmed constant time」，只說「for the moment, maybe constant time」——這是誠實的統計學：你只能排除你觀察到的洩漏，不能排除更微小的洩漏。但工程上 t < 4.5 且樣本夠大，已足夠信任。

### 跑 dudect 的步驟

```bash
git clone https://github.com/oreparaz/dudect
cd dudect/examples
# 修改 example.c 換上你的函式
make
./example
```

關鍵細節：
- 用 `RDTSC` 或 `clock_gettime(CLOCK_MONOTONIC)` 量時，RDTSC 在 x86 更精準。
- 跑之前關掉 CPU frequency scaling（`sudo cpupower frequency-set -g performance`），否則雜訊大、需要更多樣本。
- 跑的過程中不要跑其他重 CPU 工作。

---

## 驗證工具全覽

| 工具 | 類型 | 原理 | 優點 | 缺點 |
|------|------|------|------|------|
| dudect | 動態統計 | Welch t-test | 真實硬體，抓所有洩漏來源 | 需大量樣本，不能保證完全無洩漏 |
| ctgrind | 動態 taint | Valgrind + shadow memory | 精確找洩漏點（哪一行） | 速度慢 100x，不考慮 μarch 效果 |
| ct-verif | 形式化 | LLVM IR + SMT solver | 數學保證，不用跑 | 只能驗證 constant-time 的 IR 層，compiler 可能破壞 |
| binsec | 形式化 binary | 符號執行 binary | 驗證最終 binary | 規模受限，分析複雜函式困難 |

**實務建議**：開發時用 ctgrind 找洩漏行，修完後用 dudect 在目標硬體上確認效果。生產密碼庫要加 ct-verif 進 CI。

### ctgrind 用法

```bash
# 把 secret 標成 taint，ctgrind 追蹤到 branch/memory
valgrind --tool=memcheck ./your_crypto_binary
# secret 輸入用 VALGRIND_MAKE_MEM_UNDEFINED() 標記
```

---

## Compiler 的背叛

這是 constant-time 程式設計最陰險的坑：**你寫的 constant-time code，compiler 可能優化回去**。

### 案例一：bitmask select 被優化成 branch

```c
// 你寫的
uint64_t ct_select(int cond, uint64_t a, uint64_t b) {
    uint64_t mask = -(uint64_t)(uint32_t)cond;
    return (mask & a) | (~mask & b);
}
```

`-O2` 下，GCC 有時把這個模式識別為「有條件選擇」，然後生成：

```asm
; GCC -O2 可能生成（非 constant-time）
test   edi, edi
je     .Lfalse
mov    rax, rsi    ; return a
ret
.Lfalse:
mov    rax, rdx    ; return b
ret
```

而不是你期望的 `cmov`。

修法：用 `__builtin_expect` 告訴 compiler 不要 speculate，或更可靠的是直接加 compiler barrier：

```c
static inline uint64_t ct_select(int cond, uint64_t a, uint64_t b) {
    uint64_t mask = -(uint64_t)(uint32_t)cond;
    uint64_t result = (mask & a) | (~mask & b);
    // 防止 compiler 重新排序或優化掉 mask 計算
    __asm__ volatile("" : "+r"(result) : "r"(mask) : );
    return result;
}
```

### 案例二：volatile 的誤解

```c
// 常見誤用：以為 volatile 能保 constant-time
volatile int secret_branch = secret;
if (secret_branch) { ... }   // 仍然是 secret-dependent branch！
```

`volatile` 只告訴 compiler「不要把這個 read 優化掉」，它**不阻止 branch**，也不阻止 CPU 投機執行。constant-time 需要的是無分支，不是 volatile。

### Rust 的 `subtle` crate

Rust 在語言層提供更好的抽象：

```rust
use subtle::{Choice, ConditionallySelectable, ConstantTimeEq};

let a: u64 = 0xDEADBEEF;
let b: u64 = 0xCAFEBABE;
let cond: Choice = secret.ct_eq(&0xFF_u8);  // Choice 只有 0 或 1
let result = u64::conditional_select(&a, &b, cond);
```

`subtle::Choice` 是不透明型別，阻止你直接 `if choice`，強制走 `conditional_select`。編譯器 hints（透過 `black_box` 語意）在 Rust 1.66+ 是 stable API，不依賴 inline asm。

### LLVM `optnone` 屬性

```c
__attribute__((optnone))
uint64_t ct_critical(uint64_t secret) {
    // 這個函式不做任何優化，包括不 inline 進其他函式
    ...
}
```

這是最暴力的方法：直接關掉整個函式的優化。代價是效能大幅下降，只適合用在少數真正關鍵的函式。

---

## 呼應 Ch 25 — Hertzbleed

Hertzbleed（2022）是對 constant-time 程式的一記耳光：研究者證明，即使 AES-GCM 的實作完全符合 constant-time 三原則（無 secret-dependent branch、無 secret-dependent memory access），在 Intel Alder Lake 上透過觀察頻率縮放，仍能把 AES-256 密鑰還原。

攻擊原理（Ch 25 詳細推導）：DVFS（Dynamic Voltage and Frequency Scaling）讓 CPU 根據功耗動態調頻。不同 Hamming weight 的資料在 ALU 裡造成不同的開關活動 (switching activity)，進而影響功耗，進而影響頻率，進而影響 wall-clock 時間。攻擊者遠端量 `clock_gettime` 就能還原 Hamming weight，進而恢復密鑰。

這告訴我們：**constant-time 是必要條件，不是充分條件**。防禦 Hertzbleed 需要：
- 把密碼學計算 offload 到沒有 DVFS 的硬體（固定頻率）。
- 加入 randomization（blinding）讓每次運算的功耗特徵不同。
- 或者完全不在通用 CPU 上跑（硬體 crypto engine）。

本章教的工具（dudect、ctgrind）驗證的是 timing 層的 constant-time，它們看不到頻率側信道。Hertzbleed 需要更上層的防禦。

---

## 對比與取捨

| 技術 | 效能代價 | 可驗證性 | 對 compiler 依賴 | 適用場景 |
|------|----------|----------|-------------------|----------|
| 無分支選擇（bitmask / `ct_select`） | 低（1–2 指令） | 高（dudect/ctgrind 可驗） | 中（需 compiler barrier） | 替換所有 secret-dependent if |
| 掃全表 | 中–高（O(n) vs O(1)） | 高 | 低（記憶體存取型態確定） | AES S-box、RSA padding |
| 固定迴圈 + mask | 中（跑到 MAX） | 高 | 低 | 可變長度字串/buffer 處理 |
| `ct_memcmp` | 低 | 高 | 無（純邏輯） | 任何 MAC/hash 比較 |
| Rust `subtle` | 低（無 overhead） | 高（型別系統保證） | 低（black_box 保護） | Rust 密碼學 crate |
| `optnone` 屬性 | 極高（關優化） | 中（禁優化就無優化問題） | 無 | 只用在最關鍵、極小的函式 |

Early-exit `memcmp` vs `ct_memcmp` 的效能差距：在 64-byte token 比較上，兩者在現代 CPU 上幾乎沒有差別（都在 10 ns 以內）。掃全表（256 entry S-box）比直接查表約慢 10–20 倍，但只在沒有 AES-NI 的環境才需要。

---

## 踩雷集錦

**1. `volatile` 不等於 constant-time**

最常見的誤解。開發者以為加了 `volatile` 就防止 compiler 優化掉 secret 相關計算，但 `volatile` 只影響讀寫的可見性，不阻止分支生成。`volatile uint8_t x = secret; if (x) { ... }` 仍然是 secret-dependent branch，而且比沒有 `volatile` 更難讓 compiler 幫你優化成 `cmov`。

**2. 換掉 `memcmp` 但忘記 `strlen`**

```c
// 看起來安全，其實不是
int verify_token(const char *input, const char *expected) {
    size_t len = strlen(input);   // strlen 是 early-exit！
    return ct_memcmp(input, expected, len) == 0;
}
```

`strlen` 遇到 `\0` 就停，洩漏了 token 長度資訊。正確做法是用固定長度（常數），或者用 `strnlen` 加固定 MAX。密碼學 token 應該用固定長度的 binary，不用 null-terminated string。

**3. `if (ct_select(...))` — 把 constant-time 包進 branch**

```c
// ct_select 本身正確，但外面的 if 毀了一切
uint64_t val = ct_select(cond, a, b);
if (val == a) {           // 這個 if 因 val 而異，val 因 cond 而異，間接洩漏 cond
    do_something();
}
```

`ct_select` 的回傳值如果再被用於 branch condition，洩漏就繞回來了。必須把 `do_something()` 也改成 constant-time 版本（或者確認它的 output 不洩漏 cond）。

**4. `-O0` constant-time，`-O2` 被 branch-hoist 破壞**

在 `-O0` 下跑 dudect 通過，以為完成。換到 `-O2`（或 release build）後，compiler 把你精心設計的 bitmask 識別為條件選擇，生成 `je/jne` 而非 `cmov`。一定要在**目標 optimization level** 下跑驗證，不能在 debug build 下通過就收工。

---

## 進階：再往深一層

### `__builtin_constant_p` 與 Clang VCC

GCC 和 Clang 的 `__builtin_constant_p(x)` 在 compile time 回傳 `x` 是否為常數。某些密碼庫用它來做「如果 secret 在 compile time 確定就走快路；否則走 constant-time 路」——但這個用法本身就是洩漏來源：它造成 two-path 執行，攻擊者可以設計 oracle 使 secret 不是 compile-time constant，觸發慢路，再量差異。VCC（Verified Constant-time Compiler）試圖在 Clang 層保證 constant-time 屬性到 assembly，是學術研究方向。

### Rust `subtle::Choice` 型別

`Choice` 是 `u8` 的 newtype，只接受 0 或 1，且禁止用 `==` 或 `if` 取值。所有 `ConditionallySelectable` 型別（`u8`、`u32`、`[u8; N]` 等）實作 `conditional_select`，保證生成 constant-time 程式碼。`ConstantTimeEq` trait 讓 `ct_eq` 回傳 `Choice` 而非 `bool`，從型別層強制正確性。

### Secret-tracking 硬體：STT 與 DOLMA

STT（Speculative Taint Tracking）和 DOLMA 是硬體層的 constant-time 解決方案：CPU 在微架構層追蹤哪些值來自 secret，如果 secret 的 taint 流到 branch predictor 或 cache index，硬體自動阻斷（stall），不讓投機執行發生。這是 Spectre（Ch 20）的硬體修法，也是 constant-time 程式設計的硬體版本。目前是研究原型，尚未進入主流 CPU。

### DOIT bit（呼應 Ch 34）

Intel 的 DOIT（Data Operand Independent Timing）bit 是 `IA32_UARCH_MISC_CTL` MSR 的 bit 0。設定後，某些指令（如整數 divide、某些 FP 操作）強制走固定時間路徑，不管輸入值。AESNI 指令本身已經是 constant-time，DOIT bit 主要影響 multiply/divide。Ch 34 會詳細介紹硬體層的 timing 防禦機制。

---

## 動手練習

**練習一：找洩漏點並分類**

以下函式用於驗證 TOTP（Time-based OTP）token：

```c
int verify_totp(const char *user_input, const char *expected_otp) {
    if (strlen(user_input) != 6) return 0;
    for (int i = 0; i < 6; i++) {
        if (user_input[i] < '0' || user_input[i] > '9') return 0;
        if (user_input[i] != expected_otp[i]) return 0;
    }
    return 1;
}
```

找出所有洩漏點，說明每個屬於哪一類（secret-dependent branch / early-exit / 變長迴圈 / secret-index 查表），並說明攻擊者能用哪種測量方式利用它。

**練習二：把 leaky AES key comparison 改成 constant-time 並 dudect 驗證**

給定以下 key 比較函式：

```c
// 洩漏版本
int compare_aes_key(const uint8_t *input_key, const uint8_t *stored_key) {
    return memcmp(input_key, stored_key, 16) == 0;
}
```

步驟：
1. 用 ctgrind 確認洩漏點在哪一行。
2. 改寫成 `ct_memcmp` 版本。
3. 把兩個版本分別接進 dudect harness，class 0 用 `stored_key = {0x00,...}` + `input_key = {0x00,...}`，class 1 用 `stored_key = {0x00,...}` + `input_key = {0xFF,...}`，各跑到樣本數 100K（0.1 M）。
4. 報告兩個版本的 max t 值，確認修法後 t < 4.5。

**練習三：寫 `ct_select` 並確認組語**

```c
// 實作這個函式
uint64_t ct_select(uint64_t cond, uint64_t a, uint64_t b);
// cond 只保證 LSB 有意義（0 or 1）
```

要求：
1. 不使用任何 branch（不能有 `if`、`? :`、`switch`）。
2. 用 `gcc -O2 -S -o ct_select.s ct_select.c` 生成組語，確認裡面有 `cmov` 而非 `je/jne`。
3. 如果生成了 branch，加 compiler barrier（`__asm__ volatile`）後再試。
4. 用 `objdump -d` 和 `grep -E 'je|jne|jg|jl|jge|jle'` 確認最終 binary 裡沒有條件跳躍。

---

## 本章重點整理

- Constant-time 三原則：執行時間、記憶體存取樣式、分支路徑都不能相依 secret。
- 四類常見洩漏：secret-dependent 分支、secret-index 查表、提早 return（memcmp）、變長迴圈。
- 修法工具箱：`ct_select`（bitmask 選擇）、掃全表、固定迴圈 + mask、`ct_memcmp`。
- Dudect 用 Welch t-test 量兩組的執行時間分佈，|t| > 4.5 確認洩漏；t < 4.5 且樣本充足代表工程上可接受。
- Leaky 版 t = 164.47，0.01 M 樣本就確認；CT 版 t ≈ 1.4，0.07 M 樣本未偵測到洩漏。
- Compiler 可能把 bitmask select 優化回 branch：必須在 release build（-O2）下驗證，必要時加 compiler barrier。
- `volatile` 不保證 constant-time，只防止 access 被優化掉。
- 即使真正 constant-time，Hertzbleed（DVFS 頻率側信道）仍可能洩漏 Hamming weight——constant-time 是必要非充分條件。

---

## 自我檢核

1. 列出 constant-time 三原則，並各舉一個違反的 C 程式碼範例。
2. `ct_select(cond, a, b)` 的實作為什麼要用 `-(uint64_t)(uint32_t)cond` 而不是直接 `-cond`？
3. Dudect 的 t 值是什麼？閾值 4.5 的意義是什麼？`(5/tau)^2` 這個欄位代表什麼？
4. 為什麼說 `volatile` 不保證 constant-time？舉一個加了 `volatile` 但仍然洩漏的例子。
5. Hertzbleed 打的是哪個層面的 constant-time？它讓哪一條原則失效？需要什麼層面的防禦？
6. 解釋掃全表（ct_table_lookup）為什麼比直接查表慢，以及在什麼情況下可以不用這個技巧。

---

## 延伸閱讀

- **Reparaz et al. "Dude, is my code constant time?" DATE 2017**
  dudect 的原始論文，完整推導 Welch t-test 應用在 constant-time 驗證的統計基礎。直接讀第 2–4 節，學 class 分配策略和樣本量估算。與本課的關聯：本章所有 dudect 輸出都來自論文的方法論。

- **Bernstein "Cache-timing attacks on AES" 2005**
  CT AES 的原始動機文件，從實際攻擊推導出 constant-time 需求，推薦讀第 3–5 節。與本課的關聯：呼應 Ch 11 的 T-table 攻擊，是為什麼要用 AES-NI 或 bitsliced AES 的根本原因。

- **Pornin et al. BearSSL ct.h**
  `https://bearssl.org/gitweb/?p=BearSSL;a=blob;f=src/symcipher/aes_ct.c`
  生產級 constant-time 工具函式庫，涵蓋 AES bitsliced、ct_memcmp、ct_select 的完整參考實作，程式碼有詳細注釋解釋每個 mask 操作的意圖。與本課的關聯：本章 `ct_select` 的設計直接參考 BearSSL 的 `br_aes_ct_bitslice_encrypt` 風格。

- **Ch 25 — Hertzbleed（本課程）**
  讀完本章後回頭重讀 Ch 25，會對頻率側信道有更深的理解：Ch 25 演示攻擊，本章演示防禦，兩章合起來是 constant-time 攻防的完整圖景。

---

→ [下一章：偵測（HPC-based）](33-detection-hpc.md)
