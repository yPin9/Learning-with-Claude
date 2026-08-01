# 練習 D — 把洩漏的 code 改成 constant-time

## 任務目標

走完「發現洩漏 → 修復 → 驗證」的完整流程。我們用 dudect（統計計時檢定工具）當裁判：先讓它告訴我們有洩漏，把程式修好之後，再讓它告訴我們沒洩漏。

這是 Part 5 的總結練習。讀完本練習你會具備：

- 獨立設計 dudect harness（`prepare_inputs` + `do_one_computation`）的能力
- 理解 t 值（Welch t-test statistic）意義：|t| > 4.5 = 洩漏確認
- 能區分「讓 compiler 看不見 secret」和「真正 constant-time」的差異
- 用 `objdump -d` 確認組語沒有 secret-dependent 分支

**前置知識**：Ch 03（計時攻擊原理）、Ch 11（Cache Side-Channel）、Ch 29（dudect 使用）、Ch 31（constant-time 程式設計）。

---

## 環境說明

```
WSL2 Ubuntu 22.04
CPU: Intel i7-10700（8 核心，關閉 Hyper-Threading 測量時用 taskset -c 0）
dudect: ~/microarch_lab/dudect/
  src/dudect.h      — 主要 header-only 實作
  src/ttest.h       — Welch t-test 輔助
  src/fixture.h     — 輸入 fixture 定義
```

確認環境：

```bash
ls ~/microarch_lab/dudect/src/
# 應看到 dudect.h  fixture.h  ttest.h
gcc --version      # 需要 11.x 以上
```

所有測試統一在 `~/microarch_lab/practice-d/` 目錄下操作。

---

## Task 1：用 dudect 證明 leaky code 洩漏

### 1-1 洩漏函式

以下函式執行時間和 `secret_byte` 的值成正比——迴圈跑 `secret_byte` 次，0x00 時幾乎立即結束，0xFF 時跑 255 次。這是教科書等級的 secret-dependent iteration count 洩漏。

```c
/* leaky_loop: 跑 secret_byte 次迴圈
 * 執行時間隨 secret_byte 的值（0–255）線性增長
 * 這使計時攻擊者可以區分不同 secret 值的執行時間 */
__attribute__((noinline))
static volatile uint32_t leaky_loop(uint8_t secret_byte) {
    volatile uint32_t acc = 0;
    for (volatile uint32_t i = 0; i < secret_byte; i++) {
        acc += i;
    }
    return acc;
}
```

### 1-2 你的任務：寫 dudect harness

建立 `leaky_loop.c`，把 `leaky_loop` 包進 dudect framework。以下是框架骨架，你需要填入兩個函式：

```c
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include "dudect.h"

#define SAMPLE_SIZE 128

/* ---- 你要填的函式 ---- */

/*
 * prepare_inputs:
 *   分配 n 個輸入，每個輸入 1 byte（secret_byte）。
 *   classes[i] == 0 → secret_byte = 0x00（迴圈 0 次）
 *   classes[i] == 1 → secret_byte = 0xFF（迴圈 255 次）
 */
void prepare_inputs(uint8_t *input_data, uint8_t *classes, size_t n) {
    /* TODO */
}

/*
 * do_one_computation:
 *   用 input_data[0] 當作 secret_byte，呼叫 leaky_loop。
 *   回傳值必須防止 compiler dead-code-elimination。
 */
uint8_t do_one_computation(uint8_t *data) {
    /* TODO */
}
/* ---- end ---- */

int main(void) {
    puts("[MODE] leaky variable-loop (secret-dependent iteration count)");
    dudect_config_t config = {
        .chunk_size  = SAMPLE_SIZE,
        .number_measurements = 1 << 20,  /* 約 100 萬次 */
    };
    dudect_ctx_t ctx;
    dudect_init(&ctx, &config);

    dudect_state_t state = DUDECT_NO_LEAKAGE_EVIDENCE_YET;
    while (state == DUDECT_NO_LEAKAGE_EVIDENCE_YET) {
        state = dudect_main(&ctx);
    }
    dudect_free(&ctx);
    return (int)state;
}
```

**提示**：
- `prepare_inputs` 的 `classes[i]` 只能是 0 或 1；用 `i % 2` 做交錯分配。
- `do_one_computation` 回傳值需要讓 compiler 認為有副作用，才不會被最佳化掉；用 `volatile` 或直接回傳計算結果。
- 用 `-O1` 編譯；`-O2` 可能把 `volatile` loop 最佳化到無法區分。

### 1-3 期望輸出

在 WSL2 i7-10700 上實際跑到的輸出（`taskset -c 0 timeout 30 ./leaky_loop`）：

```
[MODE] leaky variable-loop (secret-dependent iteration count)
meas:    0.00 M, not enough measurements (9511 still to go).
meas:    0.00 M, not enough measurements (8979 still to go).
meas:    0.00 M, not enough measurements (8447 still to go).
meas:    0.00 M, not enough measurements (7915 still to go).
meas:    0.00 M, not enough measurements (7383 still to go).
meas:    0.00 M, not enough measurements (6851 still to go).
meas:    0.00 M, not enough measurements (6319 still to go).
meas:    0.00 M, not enough measurements (5787 still to go).
meas:    0.00 M, not enough measurements (5255 still to go).
meas:    0.00 M, not enough measurements (4723 still to go).
meas:    0.00 M, not enough measurements (4191 still to go).
meas:    0.00 M, not enough measurements (3659 still to go).
meas:    0.00 M, not enough measurements (3127 still to go).
meas:    0.00 M, not enough measurements (2595 still to go).
meas:    0.00 M, not enough measurements (2063 still to go).
meas:    0.00 M, not enough measurements (1531 still to go).
meas:    0.00 M, not enough measurements (999 still to go).
meas:    0.00 M, not enough measurements (467 still to go).
meas:    0.01 M, max t: +164.47, max tau: 1.62e+00, (5/tau)^2: 9.49e+00. Probably not constant time.
```

**解讀**：

| 數字 | 意義 |
|------|------|
| `meas: 0.01 M` | 僅約 10,000 次測量 |
| `max t: +164.47` | Welch t-statistic；閾值是 4.5 |
| `max tau: 1.62e+00` | 正規化效果量 |
| `(5/tau)^2: 9.49` | 若此值 < 1e6，表示需要極少測量量就能確認洩漏 |

t = +164.47 是 4.5 閾值的 36 倍。只需 10,000 次測量，dudect 就斷定「統計上兩個 class 的執行時間分佈可被區分」——這就是洩漏。

---

## Task 2：把洩漏點改成 constant-time

你現在知道 `leaky_loop` 有洩漏。動手修。

### 2-1 三個你可能考慮的思路

**思路 A（錯誤）：把 secret 預先 copy 到 volatile 變數**

```c
// 你以為這樣 compiler 就看不見 secret 了
volatile uint8_t hidden = secret_byte;
for (volatile uint32_t i = 0; i < hidden; i++) { ... }
```

為什麼不夠：`volatile` 只告訴 compiler「每次讀取必須真的 load」，但迴圈次數仍然由執行時期的值決定——hardware 的分支預測器和計時差異照舊存在。這是混淆 compiler-level 可見性和 microarchitecture-level 可區分性的典型錯誤。

**思路 B（錯誤）：用 `__builtin_expect`**

```c
for (volatile uint32_t i = 0; i < secret_byte; i++) {
    if (__builtin_expect(i < secret_byte, 1)) acc += i;
}
```

為什麼沒用：`__builtin_expect` 影響的是 compiler 的 branch layout（哪個路徑放前面），不影響執行路徑本身。secret-dependent 的迴圈終止條件仍存在。

**思路 C（正確）：固定迴圈次數 + bitmask**

永遠跑 255 次，用算術 mask 決定是否累加。沒有 secret-dependent 分支，沒有 secret-dependent 迴圈長度。

### 2-2 你的任務

在 `ct_fixed.c` 裡寫出 constant-time 版本，讓以下性質成立：

1. 無論 `secret_byte` 是 0x00 還是 0xFF，迴圈必須跑**相同次數**
2. 累加是否發生，透過算術 mask 控制，不走條件跳躍（`jcc`）
3. 保留和 `leaky_loop` 相同的語義（`acc` 最後的值和原版一樣）

寫完之後先不要看解答，自己跑 dudect 看 t 值有沒有降下來。

<details>
<summary>參考修法（確認自己的版本正確後再展開）</summary>

```c
/* ct_fixed: constant-time 版本
 * 永遠跑 255 次迴圈，用 bitmask 決定是否累加
 * 執行時間不相依 secret_byte 的值 */
__attribute__((noinline))
static volatile uint32_t ct_fixed(uint8_t secret_byte) {
    volatile uint32_t acc = 0;
    for (volatile uint32_t i = 0; i < 255; i++) {
        /*
         * mask 推導：
         *   i < secret_byte  → (int32_t) 比較結果為 1
         *   -(int32_t)(1)    → -1 = 0xFFFFFFFF（all bits set）
         *   i < secret_byte  → mask = 0xFFFFFFFF，acc += i
         *   i >= secret_byte → mask = 0x00000000，acc += 0（no-op）
         */
        uint32_t mask = (uint32_t)(-(int32_t)(i < (uint32_t)secret_byte));
        acc += (i & mask);
    }
    return acc;
}
```

**bitmask 算術解釋**：

C 的 `(int32_t)(i < k)` 在條件成立時為 1，不成立時為 0。
把它取負號：`-(int32_t)(1) = -1`，以 two's complement 表示為 `0xFFFFFFFF`；
`-(int32_t)(0) = 0`，表示為 `0x00000000`。

用 `& mask` 做選擇：`i & 0xFFFFFFFF == i`（累加），`i & 0x00000000 == 0`（不累加）。
全程沒有分支指令，GCC 和 Clang 都會把這段編成 `setl` + `neg` + `and`，不會產生 `jcc`。

**更難的變體：把 `memcmp` 改成 constant-time**

標準 `memcmp` 遇到第一個不同 byte 就 early exit——洩漏比較了幾個 byte：

```c
/* 有洩漏：timing 洩漏比對位置 */
int leaky_memcmp(const uint8_t *a, const uint8_t *b, size_t len) {
    for (size_t i = 0; i < len; i++) {
        if (a[i] != b[i]) return -1;
    }
    return 0;
}

/* constant-time 版：全部比完，用 OR 累積差異 */
int ct_memcmp(const uint8_t *a, const uint8_t *b, size_t len) {
    uint8_t diff = 0;
    for (size_t i = 0; i < len; i++) {
        diff |= (a[i] ^ b[i]);
    }
    /* diff == 0 表示完全相同；任何差異都會讓至少一個 bit 為 1 */
    return (int)diff;
}
```

密碼學函式庫通常用 `CRYPTO_memcmp`（OpenSSL）或 `timingsafe_bcmp`（BSD libc）；Linux kernel 5.x 以後有 `crypto_memneq`。自己寫時，注意不要讓 compiler 「看穿」你在做比較而把它最佳化掉——加 `__attribute__((noinline))` 或明確 barrier。

</details>

---

## Task 3：用 dudect 證明修好了

把 `ct_fixed` 包進 dudect harness（和 Task 1 相同的 class 設計：class 0 = 0x00，class 1 = 0xFF），編譯後執行。

### 3-1 期望輸出

在 WSL2 i7-10700 上實際跑到的輸出（`taskset -c 0 timeout 60 ./ct_fixed`）：

```
[MODE] constant-time fixed-loop
meas:    0.01 M, max t:   +0.22, max tau: 2.12e-03, (5/tau)^2: 5.58e+06. For the moment, maybe constant time.
meas:    0.01 M, max t:   +0.27, max tau: 2.47e-03, (5/tau)^2: 4.09e+06. For the moment, maybe constant time.
meas:    0.02 M, max t:   +0.53, max tau: 3.47e-03, (5/tau)^2: 2.08e+06. For the moment, maybe constant time.
meas:    0.02 M, max t:   +0.71, max tau: 3.87e-03, (5/tau)^2: 1.67e+06. For the moment, maybe constant time.
meas:    0.03 M, max t:   +0.89, max tau: 4.17e-03, (5/tau)^2: 1.43e+06. For the moment, maybe constant time.
meas:    0.03 M, max t:   +1.03, max tau: 4.57e-03, (5/tau)^2: 1.19e+06. For the moment, maybe constant time.
meas:    0.04 M, max t:   +1.18, max tau: 4.89e-03, (5/tau)^2: 1.05e+06. For the moment, maybe constant time.
meas:    0.05 M, max t:   +1.22, max tau: 4.96e-03, (5/tau)^2: 1.01e+06. For the moment, maybe constant time.
meas:    0.05 M, max t:   +1.31, max tau: 5.17e-03, (5/tau)^2: 9.33e+05. For the moment, maybe constant time.
meas:    0.06 M, max t:   +1.37, max tau: 5.34e-03, (5/tau)^2: 8.76e+05. For the moment, maybe constant time.
meas:    0.07 M, max t:   +1.47, max tau: 5.59e-03, (5/tau)^2: 8.01e+05. For the moment, maybe constant time.
meas:    0.07 M, max t:   +1.43, max tau: 5.44e-03, (5/tau)^2: 8.45e+05. For the moment, maybe constant time.
```

t 值在 1.4–1.5 範圍內徘徊，70,000 次測量後未超過 4.5，持續收到「For the moment, maybe constant time」。這是我們要的結果。

**注意**：dudect 不能「證明 constant-time」，只能說「在這個樣本量內找不到統計證據」。|t| < 4.5 是工程上的充分條件，不是數學上的充分條件。

### 3-2 前後對照

| 版本 | max t（達到） | 所需測量量 | 結論 |
|------|-------------|-----------|------|
| `leaky_loop` | +164.47 | ~10,000 次 | 洩漏確認，Probably not constant time |
| `ct_fixed` | +1.47 | 70,000+ 次 | 未偵測到洩漏，maybe constant time |

差距是 164.47 vs 1.47——兩個數量級。而且 `leaky_loop` 只需要 1 萬次就爆表，`ct_fixed` 跑了 7 萬次還安全。

---

## Task 4（選做）：Secret-Index 查表 — AES S-box 場景

### 4-1 洩漏函式

```c
/* 256-byte S-box，模擬 AES SubBytes */
static const uint8_t sbox[256] = {
    0x63, 0x7c, 0x77, 0x7b, 0xf2, 0x6b, 0x6f, 0xc5,
    0x30, 0x01, 0x67, 0x2b, 0xfe, 0xd7, 0xab, 0x76,
    /* ... 其餘 240 bytes 省略，使用真實 AES S-box ... */
};

uint8_t sbox_lookup(uint8_t secret_key_byte, uint8_t plaintext_byte) {
    return sbox[secret_key_byte ^ plaintext_byte];  /* 洩漏：secret-index cache access */
}
```

### 4-2 洩漏原因

`secret_key_byte ^ plaintext_byte` 作為 index，決定存取 `sbox` 的哪個位置。
256 bytes 的 sbox 佔 4 條 cache line（64 bytes/line × 4 = 256 bytes）。
不同 `secret_key_byte` 的值會讓不同 cache line 被存取——timing 差異可量測。

這和 Ch 11 討論的 AES T-table 攻擊直接相關：攻擊者準備大量已知 plaintext，對不同的 `secret_key_byte` 值收集 timing，推斷出哪個 cache line 被存取最頻繁，縮小 key space。

### 4-3 dudect harness 設計的難點

這個場景比 Task 1 複雜：

1. **需要控制 cache 狀態**：每次測量前要把 sbox 從 cache 裡清掉（`clflush` 或對 sbox 做一次 dummy scan），否則 cache hit/miss 的差異被 cache warm-up 掩蓋。
2. **class 設計**：
   - class 0：`secret_key_byte = 0x00`（存取 sbox[0..31] 那個 cache line 範圍）
   - class 1：`secret_key_byte = 0x80`（存取 sbox[128..159] 那個 cache line 範圍）
   - `plaintext_byte` 兩個 class 都用固定值（如 0x00）
3. **WSL2 的雜訊**：VMM 層的 context switch 讓計時雜訊大，可能需要跑更多輪。

### 4-4 修復思路

AES 的 constant-time 實作有兩條路：

- **bitsliced AES**：把 8-bit 操作拆成 bitwise 邏輯，完全不用查表。效能差但 cache-免疫。參考 BearSSL 的 `aes_ct64` 實作。
- **AES-NI**：用 `AESENC`/`AESDEC` 指令，hardware 保證 constant-time。`#include <wmmintrin.h>` + `__m128i` 即可。實際生產環境的正確選擇。

---

## Task 5（選做）：確認 compiler 沒有插入條件跳躍

### 5-1 任務

用 `gcc -S` 或 `objdump -d` 確認 `ct_fixed` 的 bitmask 在 `-O2` 下被編成 `cmov`（conditional move）而非 `jcc`（conditional jump）。

```bash
# 編譯產生組語
gcc -O2 -S -o ct_fixed.s ct_fixed.c

# 搜尋條件跳躍指令
grep -E 'j[a-z]+\s' ct_fixed.s
# 理想情況：ct_fixed 函式內看不到 jcc

# 搜尋 cmov 指令族（setl/setb/cmovl 等都算 data-conditional 操作）
grep -E 'cmov|setl|setb|setle|setbe|neg|and' ct_fixed.s
```

### 5-2 期望觀察

`-O2` 下 GCC 對 `(uint32_t)(-(int32_t)(i < k))` 這個 pattern 通常會產生：

```asm
; 類似這樣的序列（x86-64）
    cmpl   %edi, %eax        ; i < secret_byte ?
    setl   %cl               ; cl = (i < secret_byte) ? 1 : 0
    movzbl %cl, %ecx
    negl   %ecx              ; ecx = -(cl) → 0xFFFFFFFF 或 0
    andl   %eax, %ecx        ; ecx = i & mask
    addl   %ecx, ...         ; acc += masked_i
```

沒有 `jl` / `jge` / `jne`——這就是 data-conditional 而非 branch-conditional 操作。

### 5-3 反面案例

如果你的 constant-time 實作用了 `if`：

```c
if (i < (uint32_t)secret_byte) acc += i;  // 這樣寫
```

`-O1` 大概會出現 `jge`，`-O2` 有時會最佳化成 `cmov` 但不保證。用 bitmask 算術是唯一可靠的方法。

---

## 完整參考解答

<details>
<summary>leaky_loop.c（完整可編譯）</summary>

```c
/* leaky_loop.c — dudect harness for leaky secret-dependent loop
 * 編譯：gcc -O1 -I$(HOME)/microarch_lab/dudect/src -lm -o leaky_loop leaky_loop.c
 * 執行：taskset -c 0 timeout 30 ./leaky_loop
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define DUDECT_IMPLEMENTATION
#include "dudect.h"

#define SAMPLE_SIZE 128

__attribute__((noinline))
static volatile uint32_t leaky_loop(uint8_t secret_byte) {
    volatile uint32_t acc = 0;
    for (volatile uint32_t i = 0; i < secret_byte; i++) {
        acc += i;
    }
    return acc;
}

void prepare_inputs(uint8_t *input_data, uint8_t *classes, size_t n) {
    for (size_t i = 0; i < n; i++) {
        classes[i] = (uint8_t)(i % 2);          /* 0 和 1 交錯 */
        input_data[i * SAMPLE_SIZE] = (classes[i] == 0) ? 0x00 : 0xFF;
    }
}

uint8_t do_one_computation(uint8_t *data) {
    volatile uint32_t result = leaky_loop(data[0]);
    return (uint8_t)(result & 0xFF);             /* 防止 dead-code-elimination */
}

int main(void) {
    puts("[MODE] leaky variable-loop (secret-dependent iteration count)");
    dudect_config_t config = {
        .chunk_size          = SAMPLE_SIZE,
        .number_measurements = 1 << 20,
    };
    dudect_ctx_t ctx;
    dudect_init(&ctx, &config);

    dudect_state_t state = DUDECT_NO_LEAKAGE_EVIDENCE_YET;
    while (state == DUDECT_NO_LEAKAGE_EVIDENCE_YET) {
        state = dudect_main(&ctx);
    }
    dudect_free(&ctx);
    return (int)state;
}
```

</details>

<details>
<summary>ct_fixed.c（完整可編譯）</summary>

```c
/* ct_fixed.c — dudect harness for constant-time fixed loop
 * 編譯：gcc -O1 -I$(HOME)/microarch_lab/dudect/src -lm -o ct_fixed ct_fixed.c
 * 執行：taskset -c 0 timeout 60 ./ct_fixed
 */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>

#define DUDECT_IMPLEMENTATION
#include "dudect.h"

#define SAMPLE_SIZE 128

__attribute__((noinline))
static volatile uint32_t ct_fixed(uint8_t secret_byte) {
    volatile uint32_t acc = 0;
    for (volatile uint32_t i = 0; i < 255; i++) {
        uint32_t mask = (uint32_t)(-(int32_t)(i < (uint32_t)secret_byte));
        acc += (i & mask);
    }
    return acc;
}

void prepare_inputs(uint8_t *input_data, uint8_t *classes, size_t n) {
    for (size_t i = 0; i < n; i++) {
        classes[i] = (uint8_t)(i % 2);
        input_data[i * SAMPLE_SIZE] = (classes[i] == 0) ? 0x00 : 0xFF;
    }
}

uint8_t do_one_computation(uint8_t *data) {
    volatile uint32_t result = ct_fixed(data[0]);
    return (uint8_t)(result & 0xFF);
}

int main(void) {
    puts("[MODE] constant-time fixed-loop");
    dudect_config_t config = {
        .chunk_size          = SAMPLE_SIZE,
        .number_measurements = 1 << 20,
    };
    dudect_ctx_t ctx;
    dudect_init(&ctx, &config);

    dudect_state_t state = DUDECT_NO_LEAKAGE_EVIDENCE_YET;
    while (state == DUDECT_NO_LEAKAGE_EVIDENCE_YET) {
        state = dudect_main(&ctx);
    }
    dudect_free(&ctx);
    return (int)state;
}
```

</details>

<details>
<summary>Makefile</summary>

```makefile
DUDECT_SRC = $(HOME)/microarch_lab/dudect/src
CFLAGS     = -O1 -I$(DUDECT_SRC) -lm

all: leaky_loop ct_fixed

leaky_loop: leaky_loop.c
	gcc $(CFLAGS) -o $@ $<

ct_fixed: ct_fixed.c
	gcc $(CFLAGS) -o $@ $<

test-leaky:
	taskset -c 0 timeout 30 ./leaky_loop

test-ct:
	taskset -c 0 timeout 60 ./ct_fixed

asm-check:
	gcc -O2 -S -o ct_fixed_O2.s ct_fixed.c
	@echo "=== conditional jumps in ct_fixed ==="
	@grep -E 'j[a-z]+\s' ct_fixed_O2.s || echo "(none — good)"
	@echo "=== data-conditional ops in ct_fixed ==="
	@grep -E 'cmov|setl|setb|neg|and' ct_fixed_O2.s | head -20

clean:
	rm -f leaky_loop ct_fixed ct_fixed_O2.s
```

**使用方式**：

```bash
cd ~/microarch_lab/practice-d/
make                  # 編譯兩個 binary
make test-leaky       # 跑 leaky 版，應該快速出現 Probably not constant time
make test-ct          # 跑 ct 版，應該持續看到 maybe constant time
make asm-check        # Task 5：確認組語
```

</details>

---

## 常見錯誤排除

**問題 1：`-O2` 把 `volatile` loop 最佳化掉，兩個 class 的 t 值都很低**

`-O2` 某些情況下會把 `volatile uint32_t i` 的迴圈展開或向量化，導致原本的 secret-dependent iteration count 被消除，洩漏消失——但這是「compiler 意外修好了」，不是正確的 constant-time 修法。改用 `-O1` 或加 `__attribute__((noinline))` 確保洩漏可被觀察。

**問題 2：dudect 初始一直顯示「not enough measurements」**

正常現象。dudect 需要 warm-up 期（通常 10,000–20,000 次）才開始計算 t 值。看到「not enough measurements」不是錯誤，等它跑完 warm-up 即可。

**問題 3：WSL2 上 ct_fixed 的 t 值偶爾跳到 3.x，感覺快超過 4.5**

WSL2 的計時雜訊比裸機大（VMM 的 context switch、Hyper-V 的 vCPU 排程）。|t| 在 2.0–3.5 範圍內的波動可接受；重要的是長時間（70,000 次以上）不超過 4.5。若頻繁跳到 4.0 以上，考慮：
- 確認有用 `taskset -c 0` 釘在單一 CPU 核心
- 關閉 WSL2 背景服務（`wsl --shutdown` 後重開）
- 接受「For the moment, maybe constant time」作為工程上足夠的結論

**問題 4：`ct_fixed.c` 裡的 mask 計算是否 portable？**

`-(int32_t)(condition)` 依賴 two's complement，這在 C23 之前是實作定義行為（implementation-defined），C23 正式保證 two's complement。GCC/Clang 在 x86-64 上實際上一直這樣實作。生產環境可改用 `UINT32_C(0) - (uint32_t)(condition)` 以明確在 unsigned 算術下操作——unsigned overflow 在 C 標準裡是 well-defined（模 2^32）。

---

## 練習回顧

本練習的核心訊息：

1. **dudect 是發現洩漏的工具，不是證明安全的工具**——t > 4.5 可以確認洩漏，t < 4.5 只代表「目前沒找到」。
2. **secret-dependent control flow 的修法只有一種**：把控制流轉成資料流（data-conditional），用算術 mask 取代 `if`/迴圈條件。
3. **`volatile` 不等於 constant-time**——它影響 compiler 最佳化，不影響 microarchitecture 的 timing 行為。
4. **用組語驗收**——`-O2` 的 `cmov` 輸出才是最後的確認依據，統計工具只是輔助。

→ [下一章：串起來——一條真實 end-to-end 洩漏鏈](35-end-to-end-leak-chain.md)
