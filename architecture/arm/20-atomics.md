# Ch 20 — 原子操作：LDXR/STXR 與 LSE atomics

> 目標：搞懂 ARM 兩套原子原語 — 古典 LL/SC（LDXR/STXR）與 ARMv8.1+ LSE。寫 spinlock、queue、lock-free 結構必備。

## ARM 為什麼選 LL/SC

x86 的 atomic 走 `LOCK` prefix + RMW 指令（`lock cmpxchg`、`lock xadd`）— 硬體鎖總線或 cache line。

ARM 走 **LL/SC（Load-Linked / Store-Conditional）**：

```
LDXR  Load eXclusive：load + 標記「我在監控這個位址」
STXR  Store eXclusive：寫，但只在「監控」未被打斷時成功
```

整段是 optimistic：

```
loop:
  LDXR  讀
  ...   修改
  STXR  寫
  if STXR failed → loop
```

**好處**：不鎖總線，多核 contention 下擴展性好。
**壞處**：要寫 retry loop。在 high contention 下重試浪費 cycle。

## LL/SC 範例：atomic increment

```asm
; atomic_inc(&x)
1:
    ldxr  w0, [x_addr]      ; load-exclusive
    add   w0, w0, #1
    stxr  w1, w0, [x_addr]  ; store-exclusive，w1 = 0 (success) or 1 (fail)
    cbnz  w1, 1b            ; 失敗就重試
```

對 64-bit：

```asm
1:
    ldxr  x0, [x_addr]
    add   x0, x0, #1
    stxr  w1, x0, [x_addr]
    cbnz  w1, 1b
```

**注意 STXR 的回傳值是 32-bit `w1`**（成功是 0），不是 64-bit。

## LL/SC 為什麼會 fail

STXR 失敗的條件：

1. 從 LDXR 到 STXR 之間，**有任何核**寫了同個 cache line
2. 中斷、context switch（kernel 可能也搶）
3. 同一個 thread 在 LDXR 後又做了一次 LDXR（exclusive monitor 被打亂）

第 3 條值得注意：**LDXR 後不要再 LDXR 別的位址**，否則 monitor state 被洗掉。

## CAS：compare-and-swap

LL/SC 寫 CAS：

```asm
; atomic_cas(&val, expected, desired)
;   if (*val == expected) { *val = desired; return true; }
;   else return false;

cas_loop:
    ldxr   w_tmp, [x_val]
    cmp    w_tmp, w_expected
    b.ne   cas_fail
    stxr   w_status, w_desired, [x_val]
    cbnz   w_status, cas_loop      ; 重試
    mov    w0, #1                  ; success
    b      cas_done
cas_fail:
    clrex                          ; 取消 monitor
    mov    w0, #0
cas_done:
```

`CLREX` 取消 exclusive monitor — 跳出 loop 不寫之前要清，否則 monitor state 殘留會搞下次。

## LSE：ARMv8.1 的單指令 atomic

ARMv8.1-A 加了 **Large System Extensions**，把常用 atomic 編成單一指令：

```asm
cas    w_old, w_new, [x_addr]      ; compare-and-swap
casa   ...                          ; CAS with acquire
casl   ...                          ; CAS with release
casal  ...                          ; CAS with acquire+release

ldadd  w_val, w_old, [x_addr]      ; *addr += val, return old
ldclr  w_val, w_old, [x_addr]      ; *addr &= ~val, return old
ldset  ...                          ; |=
ldeor  ...                          ; ^=
ldsmax / ldumax / ldsmin / ldumin   ; max/min variants

swp    w_new, w_old, [x_addr]      ; exchange
```

LSE 指令**內部硬體保證原子**，不需要 retry loop、不需要 monitor，**對 high contention 性能巨大**。AWS Graviton 2/3、Apple M 系列、所有現代 ARM server 都實作 LSE。

## LSE vs LL/SC 性能

high contention atomic counter：

| Cores | LL/SC（LDXR/STXR） | LSE（LDADD） |
|---|---|---|
| 4 | 性能 1.0× | 1.5–2× |
| 16 | 0.4× | 1.2× |
| 64 | 0.1× | 0.8× |

LL/SC 在多核高 contention 下因為 retry 多、cache line ping-pong 嚴重，**核越多越差**。LSE 因為單指令內部處理，cache line 只 ping-pong 一次。

GCC 預設不啟用 LSE，要 `-march=armv8.1-a` 或更新版才會用。

## 編譯器生成

```c
__atomic_fetch_add(&x, 1, __ATOMIC_RELAXED);
```

`-march=armv8-a`（沒 LSE）：

```asm
1: ldxr w0, [x_x]
   add  w0, w0, #1
   stxr w1, w0, [x_x]
   cbnz w1, 1b
```

`-march=armv8.1-a` 或 `-moutline-atomics`：

```asm
mov  w1, #1
ldadd w1, w0, [x_x]
```

巨大差別。**生產 server code 一定要編 ARMv8.1+**，否則性能損失明顯。

## 屏障變種

LSE 與 LL/SC 都支援 acquire / release / acq+rel 變種：

```asm
ldadd       — relaxed
ldadda      — acquire
ldaddl      — release
ldaddal     — acquire+release（sequential consistent）

ldxr        — relaxed
ldaxr       — acquire
stxr        — relaxed
stlxr       — release
```

C++ atomic 的 `memory_order` 直接對應這些變種。

## 寫一個 ARM spinlock

LL/SC 版（教科書）：

```c
void spin_lock(volatile int *lock) {
    int tmp;
    asm volatile(
        "1:  ldaxr  %w0, [%1]     \n"
        "    cbnz   %w0, 1b       \n"   // 已上鎖，等
        "    mov    %w0, #1       \n"
        "    stxr   %w0, %w0, [%1]\n"
        "    cbnz   %w0, 1b       \n"   // 寫失敗，retry
        : "=&r"(tmp) : "r"(lock) : "memory"
    );
}

void spin_unlock(volatile int *lock) {
    asm volatile("stlr wzr, [%0]" :: "r"(lock) : "memory");
}
```

LSE 版：

```c
void spin_lock(volatile int *lock) {
    while (1) {
        int expected = 0;
        if (__atomic_compare_exchange_n(lock, &expected, 1, false,
                                         __ATOMIC_ACQUIRE, __ATOMIC_RELAXED))
            break;
        // contention 下 spin (可加 yield / WFE)
    }
}
```

`STLR` (store-release) for unlock，`LDAXR` 或 `CASA` for lock。

## WFE 在 spinlock 中省電

短 spin 時 CPU 滿載 polling，浪費電。idiom：

```asm
spin:
    ldaxr  w0, [x_lock]
    cbnz   w0, wait
    ; 嘗試獲取鎖 ...

wait:
    wfe              ; 進入低功耗，等 event
    b      spin
```

當 unlock 端做 `STLR` 時，硬體自動 broadcast event 喚醒所有 WFE 等待者。**比死轉省電 + 對 thermal 友善**。Linux kernel 的 `arm64_cpu_relax()` 用這個。

## 多核 atomicity 的限制：alignment

ARM 普通 LDR / STR **不保證 atomic** — 64-bit 寫到非 64-bit aligned 位址在某些 implementation 可能撕裂。

**LDXR / STXR / CAS / LDADD 等 atomic 指令對齊要求**：

- 32-bit atomic：4-byte 對齊
- 64-bit atomic：8-byte 對齊
- 128-bit atomic（LDXP/STXP）：16-byte 對齊

不對齊會觸發 alignment fault。

## 跨 cache line atomic？

ARM 的 atomic 指令 **不保證跨 cache line atomic**，事實上 LDXR 跨 cache line 行為 **未定義**（implementation defined）。

實務上：alignment 對齊就一定不跨 cache line（cache line ≥ 64 byte，atomic ops ≤ 16 byte）。

## 一個常見誤解

「LDXR / STXR 是不是要連續執行？中間能放別的指令嗎？」

**可以，但越少越好**。中間放越多指令，被打斷（IRQ / context switch）的機會越大。建議 LDXR 與 STXR 之間 < 10 條指令。

ARM 規範**不保證**任意長 RMW 必能成功 — 寫太長 retry loop 永遠 fail 是合法行為。實務上 < 100 條一般 OK，但越短越好。

## 自我檢核

- [ ] 我能寫出 LDXR/STXR atomic increment 的 retry loop
- [ ] 我能解釋 STXR 為什麼回傳 32-bit status
- [ ] 我能比較 LL/SC 與 LSE 的性能在 high contention 下的差距
- [ ] 我能用 LSE 一條指令做 fetch_add
- [ ] 我能寫一個 spinlock 用 LDAXR + STLR
- [ ] 我能解釋 WFE 在 spinlock 中的用途

下一章看 TrustZone 與 EL3 — ARM 的 secure world 架構，OP-TEE、ATF、iOS Secure Enclave 的機制基礎。

→ [Ch 21 TrustZone 與 EL3](./21-trustzone-el3.md)
