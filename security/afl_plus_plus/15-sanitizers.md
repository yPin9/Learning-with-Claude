# Ch 15 — Sanitizer 整合：AFL + ASan 為什麼不衝突

> 目標：說明 ASan 的 shadow memory 機制為什麼和 AFL 的 bitmap 不打架；解釋開 ASan fuzzing 慢 2–3x 的來源；講 MSan 需要全 stdlib rebuild 的原因；給出 ASAN_OPTIONS 中幾個 fuzzing 必調的 knob。

## 為什麼 fuzzer 要 sanitizer

Ch 4 講 coverage — fuzzer 靠 bitmap 知道「走到哪」。但「走到哪」不等於「有 bug」。

考慮這個經典 off-by-one：

```c
char buf[16];
strcpy(buf, input);   // input 長度沒檢查
```

如果 input 是 24 byte，`strcpy` 寫出 buffer 8 byte — 一個明確的 heap/stack buffer overflow。但：

- **程式可能不 crash**：overflow 的 byte 如果剛好在 stack padding 或沒被 return address 到的 region，程式看起來正常跑完。fuzzer 沒看到 SIGSEGV。
- **之後某個遙遠的地方 crash**：overflow 破壞了後續會用到的 memory，但要等到 random 時機才崩，很難歸因。

**Fuzzer 沒 sanitizer 的話，bug 會從指縫溜走**。加了 sanitizer 後：

- 每次 memory access 都被檢查。
- 有 bug 當場 abort()，fuzzer 立刻收到 crash。
- 報告會指出 "heap-buffer-overflow at address X, allocated at trace Y, read at trace Z"，triage 成本大降。

## ASan（AddressSanitizer）機制速寫

ASan 的核心 idea：**shadow memory**。每 8 byte 的真實 memory 對應 1 byte 的 shadow，記錄這 8 byte 的可用性：

```
shadow byte 含義:
   0    → 8 byte 全可用
   1    → 只有前 1 byte 可用（rest 是 redzone）
   2    → 前 2 byte 可用
   ...
   7    → 前 7 byte 可用
   -1   → freed
   -2   → redzone
   -6   → stack left redzone
```

每個 malloc 的 buffer 前後都插 redzone（紅區，標 shadow 為 -2），讓 overflow 一下就踩到。free 掉的 memory shadow 標 -1（quarantine），踩到就是 use-after-free。

Shadow memory 位置由一個固定的 mapping 決定：

```
Linux x86_64 下：
  shadow_addr = (real_addr >> 3) + 0x7fff8000
```

也就是 real memory `[0x0, 0xFFFF_FFFF_FFFF]` 對應 shadow `[0x7FFF_8000_0000, 0xXXXX]`。

每個 load/store 會被 compile-time 插入：

```c
// 原本：
*p = x;

// 插樁後：
u8 *shadow = (u8*)(((u64)p >> 3) + 0x7fff8000);
if (*shadow != 0 && shadow_check_slow(p, *shadow)) abort();
*p = x;
```

## AFL bitmap vs ASan shadow：為什麼不衝突

AFL bitmap 在 shared memory（某個 shmid 分配到 `0x7Fxxxx` 之類）。ASan shadow 在 `0x7FFF_8000_0000` 之類。兩者佔不同 virtual address range，完全不交集。

共存條件：
- AFL instrumentation 寫的是 `__afl_area_ptr[edge]++`，地址由 fuzzer 的 shmem 決定。
- ASan 寫的是 `shadow[p >> 3]`，地址由 ASan 的 mapping 決定。
- 兩者**各管各的 memory**，互不干擾。

AFL++ 的 compiler wrapper 對 `-fsanitize=address` 有特殊處理：

```bash
AFL_USE_ASAN=1 afl-clang-fast -o target target.c
# 等於：
# afl-clang-fast -fsanitize=address -o target target.c
# 但加上 AFL_USE_ASAN 會調整幾個 flag：禁某些 ASan 優化、確保和 forkserver 相容
```

## Overhead 從哪來

開 ASan 後 fuzzer 通常慢 2–3x。分析：

### 1. 每個 memory access 多兩個指令

load/store 都插入 shadow check。x86_64 下大致：

```asm
# 原本：
mov (%rdi), %rax

# 加 shadow check：
mov %rdi, %rcx
shr $3, %rcx
add $0x7fff8000, %rcx
cmpb $0, (%rcx)        # shadow check
jne  slow_path
mov (%rdi), %rax
```

每 load/store 多約 4–5 個指令。memory-heavy 程式成本疊加明顯。

### 2. Malloc/free 攔截

每個 malloc 實際分配 `size + redzone * 2`，還要更新 shadow。free 要把記憶體放進 quarantine 而不是真釋放。allocator 成本 2–3x。

### 3. 一次性的 shadow 初始化

process 啟動時要 mmap 整塊 shadow region。幾 MB RAM 就沒了。對 forkserver 這是一次性成本（parent 先 map，child 繼承）。

### 4. Stack redzone

function 開頭每個 local variable 間塞 redzone（8 byte）。stack frame 變大、stack allocation 慢一點。

實測對 parser target：**ASan 後約 2.5x–3x 慢**。對計算密集 target 可能到 5x。

## 選擇開哪個 sanitizer

### ASan（AddressSanitizer）

- 抓什麼：**buffer overflow、use-after-free、stack overflow、global overflow、double-free**。
- 成本：2–3x 慢，記憶體 ~3x。
- **預設首選**。多數 C/C++ memory bug 都會被抓到。

### UBSan（UndefinedBehaviorSanitizer）

- 抓什麼：signed integer overflow、divide by zero、shift 超出範圍、null deref、misaligned load...
- 成本：極低（< 10%）。
- **幾乎無腦開**。對 C 的 undefined behavior 特別有效。

### MSan（MemorySanitizer）

- 抓什麼：**uninitialized memory read**。
- 成本：2–3x 慢。
- 限制：需要**所有 library 也用 MSan 編譯**（包括 libc）— 否則外部 lib 的 memory 會被誤認為「未初始化」。這是建置地獄。
- 通常只在能完全控制 build 的專案使用。

### TSan（ThreadSanitizer）

- 抓什麼：資料競爭。
- 成本：5–15x 慢，且對 fuzzing 的 dev/null style target 意義不大（單 thread 居多）。
- Fuzzing **幾乎不用**。

### LSan（LeakSanitizer）

- 抓什麼：memory leak。
- 成本：低，ASan 附贈。
- Fuzzing 通常關掉（persistent mode 下 leak 是 feature 不是 bug），`ASAN_OPTIONS=detect_leaks=0`。

## ASAN_OPTIONS 必調的 knob

ASan runtime 的行為用環境變數調。Fuzzing 情境下幾個建議：

| Option | 推薦值 | 理由 |
|---|---|---|
| `abort_on_error` | `1` | 出問題立刻 abort 而不是 exit(1)，fuzzer 會看到 SIGABRT |
| `symbolize` | `0` | 關掉 symbol 解析，crash 時省時間（triage 時再開） |
| `detect_leaks` | `0` | fuzzing 場景下 leak noise 太多 |
| `handle_segv` | `0` | 讓原始 SIGSEGV 透到 fuzzer，而不是被 ASan 吞掉 |
| `allocator_may_return_null` | `1` | OOM 時 malloc 回 NULL 而非 abort（target 可能測試此 path） |
| `detect_odr_violation` | `0` | C++ 的 one-definition-rule check，fuzzing 不需要 |

典型 invocation：

```bash
ASAN_OPTIONS="abort_on_error=1:symbolize=0:detect_leaks=0:handle_segv=0:\
allocator_may_return_null=1:detect_odr_violation=0" \
  afl-fuzz -i seeds/ -o out/ -- ./target @@
```

或在 fuzzer 外部設好 env。

## AFL_HARDEN：編譯期強化

不開 ASan 的輕量替代：`AFL_HARDEN=1`，在 compile 時加入：

- `-fstack-protector-all`：stack canary
- `-D_FORTIFY_SOURCE=2`：libc 的 buffer check
- 其他幾個 hardening flag

成本：<5%，抓的 bug 面沒有 ASan 廣但夠用一部分。適合大型 target 在 ASan 太慢時作為折中。

## Persistent mode + ASan 的雷

ASan 預設會在 exit 時做 final leak check 和 summary 輸出。在 persistent mode 下每輪不 exit，所以這些不觸發，正常。

但 heap quarantine 會**持續累積** — 所有 free 的 memory 都放進 quarantine 不真釋放，直到 quarantine 滿。長時間 persistent + ASan 會 OOM。

解法：`ASAN_OPTIONS=quarantine_size_mb=10`，限制 quarantine 大小。

## 多個 sanitizer 同開

ASan 和 MSan 不能同開（兩者都要佔用大 shadow region）。ASan + UBSan 可以。TSan 和其他通常獨立使用。

常見組合：

| 用途 | 編譯 flag |
|---|---|
| 通用 fuzzing | `-fsanitize=address,undefined` |
| 深入 uninit 探勘 | `-fsanitize=memory`（另外編一份） |
| 輕量大量並行 | `AFL_HARDEN=1`（不用 sanitizer） |

實務上常**同時跑多個 fuzzer 實例，每個開不同 sanitizer 組合**（Ch 16 的 parallel fuzzing）。

## 常見誤解

- **「開 ASan 就一定能找到所有 memory bug」**：不。ASan 只在記憶體被 access 時檢查。如果 bug 是 memory 寫壞了但沒人讀，ASan 不會 flag。
- **「ASan 的 redzone 8 byte 夠大」**：不完全。大 overflow（比如一口氣 overflow 16 byte）可能跳過 redzone 落進下一個 valid allocation 的 region，只會被當 value corruption 而非 overflow。
- **「MSan 和 ASan 可以一起開」**：不能。shadow region 衝突。

## 自我檢核

- [ ] 能解釋 ASan shadow memory 的 `(addr >> 3) + offset` mapping
- [ ] 知道 AFL bitmap 和 ASan shadow 為什麼可以共存
- [ ] 能列出 ASAN_OPTIONS 的 fuzzing 關鍵 knob（abort_on_error、symbolize、detect_leaks）
- [ ] 知道 MSan 為什麼 build 難、何時該用
- [ ] 記得 persistent mode + ASan 要設 `quarantine_size_mb`

下一章進 parallel fuzzing — 多個 fuzzer 實例怎麼分工合作。

→ [Ch 16 Parallel fuzzing：master / secondary 分工](./16-parallel-fuzzing.md)
